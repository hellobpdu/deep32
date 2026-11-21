"""Native launch plans and bounded I/O with staged native attestation."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import queue
import threading
import time
import json

from .state import safe_path
from .proxy import MAX_RESPONSE

MAX_INPUT = 512 * 1024
MAX_DIAGNOSTIC = 65536


class NativeError(RuntimeError):
    pass


@dataclass(frozen=True, repr=False)
class LaunchPlan:
    argv: tuple
    cwd: Path
    environment: dict = field(repr=False)
    startup_input: bytes = field(default=b"", repr=False)

    def validate(self):
        if (not isinstance(self.argv, tuple) or not self.argv or not Path(self.argv[0]).is_absolute()
                or any(not isinstance(arg, str) or "\0" in arg for arg in self.argv)
                or not Path(self.cwd).is_absolute() or not safe_path(self.cwd).is_dir()
                or not isinstance(self.startup_input, bytes) or len(self.startup_input) > MAX_INPUT
                or not isinstance(self.environment, dict)
                or any(not isinstance(k, str) or not isinstance(v, str) or "\0" in k + v
                       or "=" in k for k, v in self.environment.items())):
            raise NativeError("invalid-native-plan")
        safe_path(self.argv[0])
        return self


class NativeSession:
    """Own a reviewed protocol's process, startup gate and private proxy.

    protocol.attest(bytes) returns true only for complete pre-turn evidence;
    an optional protocol.attest_turn(bytes) checks first-user init evidence;
    protocol.turn(question, context) returns bounded input bytes;
    protocol.events(bytes) returns normalised relay events. Protocols are trusted
    adapter code, never supplied by a Workstation or installation config.
    """
    def __init__(self, plan, protocol, tree, proxy, *, live, clock=time.monotonic,
                 on_attested=None):
        self.plan = plan.validate()
        self.protocol, self.tree, self.proxy = protocol, tree, proxy
        self.live, self.clock = live, clock
        self.lines = queue.Queue(maxsize=256)
        self.failure = None
        self.output_bytes = self.diagnostic_bytes = 0
        self.attested = self.released = self.completed = self.closed = False
        self.cleanup_complete = False
        self.readers = []
        self.lock = threading.RLock()
        self.process = None
        self.on_attested = on_attested or (lambda: None)

    def initialise(self, *, timeout=5):
        if self.process is not None or not 0 < timeout <= 10:
            raise NativeError("native-startup-failed")
        try:
            if not self.live():
                raise NativeError("inactive-turn")
            # The tree's start boundary is the last authority check before
            # process creation. The supervisor rechecks cancellation afterwards.
            self.process = self.tree.start(self.plan.argv, cwd=self.plan.cwd, env=self.plan.environment)
            for stream, diagnostic in ((self.process.stdout, False), (self.process.stderr, True)):
                reader = threading.Thread(target=self._read, args=(stream, diagnostic), daemon=True,
                                          name="deep32-native-pipe")
                self.readers.append(reader)
                reader.start()
            if self.plan.startup_input:
                self.process.stdin.write(self.plan.startup_input)
                self.process.stdin.flush()
            end = self.clock() + timeout
            while self.clock() < end:
                if self.failure or not self.live():
                    raise NativeError("native-startup-failed")
                try:
                    line = self.lines.get(timeout=min(0.05, max(0.001, end - self.clock())))
                except queue.Empty:
                    if self.process.poll() is not None:
                        raise NativeError("native-startup-failed")
                    continue
                if line is None:
                    raise NativeError("native-startup-failed")
                evidence = self.protocol.attest(line)
                for reply in getattr(self.protocol, "replies", lambda: ())():
                    self.process.stdin.write(reply)
                    self.process.stdin.flush()
                if evidence is None:
                    continue
                if evidence is not True:
                    raise NativeError("native-startup-unverified")
                self.attested = True
                if not getattr(self.protocol, "second_stage_required", False):
                    self.on_attested()
                return True
            raise NativeError("native-startup-timeout")
        except NativeError:
            self.cancel()
            raise
        except Exception:
            self.cancel()
            raise NativeError("native-startup-failed") from None

    def _read(self, stream, diagnostic):
        try:
            while True:
                raw = stream.readline(MAX_RESPONSE + 1)
                if not raw:
                    if not diagnostic:
                        self.lines.put_nowait(None)
                    return
                with self.lock:
                    if self.closed:
                        return
                    if diagnostic:
                        self.diagnostic_bytes += len(raw)
                        if self.diagnostic_bytes > MAX_DIAGNOSTIC:
                            raise NativeError("native-diagnostic-limit")
                        continue
                    self.output_bytes += len(raw)
                    if len(raw) > MAX_RESPONSE or self.output_bytes > 2 * MAX_RESPONSE:
                        raise NativeError("native-output-limit")
                    raw.decode("utf-8", errors="strict")
                    self.lines.put_nowait(raw)
        except Exception:
            self.failure = "native-output-failed"

    def release(self, question, context, *, timeout=5):
        if (not self.attested or self.released or not self.live() or self.failure or self.closed):
            raise NativeError("native-startup-unverified")
        try:
            if not 0 < timeout <= 10:
                raise NativeError("native-startup-failed")
            value = self.protocol.turn(question, context)
            if not isinstance(value, bytes) or len(value) > MAX_INPUT:
                raise NativeError("native-input-limit")
            # Claude only emits its exact tool inventory after this first user
            # input. The proxy remains closed to tools/call throughout stage 2.
            self.process.stdin.write(value)
            self.process.stdin.flush()
            self.released = True
            if getattr(self.protocol, "second_stage_required", False):
                end = self.clock() + timeout
                while self.clock() < end:
                    if self.failure or not self.live():
                        raise NativeError("native-startup-unverified")
                    try:
                        line = self.lines.get(timeout=min(.05, max(.001, end - self.clock())))
                    except queue.Empty:
                        if self.process.poll() is not None:
                            raise NativeError("native-startup-unverified")
                        continue
                    if line is None:
                        raise NativeError("native-startup-unverified")
                    evidence = self.protocol.attest_turn(line)
                    if evidence is None:
                        continue
                    if evidence is not True:
                        raise NativeError("native-startup-unverified")
                    self.on_attested()
                    return self
                raise NativeError("native-startup-timeout")
            return self
        except Exception:
            self.cancel()
            raise

    def poll(self):
        try:
            return self._poll()
        except Exception:
            self.cancel()
            raise

    def _poll(self):
        if self.failure:
            raise NativeError(self.failure)
        if not self.released or self.closed:
            return []
        result = []
        for _ in range(256):
            try:
                line = self.lines.get_nowait()
            except queue.Empty:
                break
            if line is None:
                if self.process.poll() not in (None, 0) or not self.completed:
                    raise NativeError("native-incomplete")
                break
            events = self.protocol.events(line)
            if not isinstance(events, (tuple, list)) or len(events) > 256:
                raise NativeError("native-output-failed")
            for event in events:
                if not isinstance(event, dict):
                    raise NativeError("native-output-failed")
                if event.get("type") == "completed":
                    if self.completed:
                        raise NativeError("native-output-failed")
                    self.completed = True
                elif self.completed:
                    raise NativeError("native-output-failed")
                result.append(event)
        return result

    def cancel(self):
        with self.lock:
            if self.cleanup_complete:
                return True
            self.closed = True
        # Revoke HTTP before killing children; revoked capabilities also make
        # requests already in flight unable to return or dispatch new work.
        try:
            proxy_clean = self.proxy.close() is True
        except Exception:
            proxy_clean = False
        try:
            clean = self.tree.cancel() is True
        except Exception:
            clean = False
        if clean:
            for reader in self.readers:
                reader.join(timeout=0.2)
            clean = all(not reader.is_alive() for reader in self.readers)
        self.cleanup_complete = clean and proxy_clean
        return self.cleanup_complete


class ClaudeCodeLauncher:
    """Captured Claude CLI spellings; init evidence remains authoritative."""
    agent = "claude"
    source_flags = (("--tools", ""), ("--strict-mcp-config",))
    required_controls = ("builtins-off", "builtins-denied", "eleven-tools",
        "single-mcp", "no-settings-layers", "isolated-profile", "no-session-persistence",
        "startup-model-cwd-tools-server", "readonly-profile", "scratch-writes-only")

    def prepare(self, executable=None, scratch=None, profile=None, *, endpoint=None,
                bearer=None, model=None, effort=None, platform=None, instruction=""):
        return _prepare(self.agent, executable, scratch, profile, endpoint, bearer,
                        model, effort, platform, instruction)


class CodexLauncher:
    """Codex app-server startup/configuration handshake, before turn/start."""
    agent = "codex"
    source_flags = ()
    required_controls = ("approval-never", "readonly-scratch-policy", "web-off", "history-off",
        "shell-off", "patch-off", "plugins-off", "hooks-off", "apps-off", "multi-agent-off",
        "memories-off", "image-view-off", "strict-single-mcp", "effective-config-attested",
        "eleven-tools", "readonly-profile", "scratch-writes-only")

    def prepare(self, executable=None, scratch=None, profile=None, *, endpoint=None,
                bearer=None, model=None, effort=None, platform=None, instruction=""):
        return _prepare(self.agent, executable, scratch, profile, endpoint, bearer,
                        model, effort, platform, instruction)


SERVER = "deep32"
TOKEN_ENV = "DEEP32_RELAY_SESSION_BEARER"
CODEX_DISABLED = ("shell_tool", "unified_exec", "apply_patch_freeform", "plugins",
                  "hooks", "apps", "multi_agent", "memories", "view_image",
                  "js_repl", "web_search_request")
CLAUDE_DENIED = ("Bash", "PowerShell", "Read", "Write", "Edit", "MultiEdit", "Glob",
                 "Grep", "NotebookEdit", "WebFetch", "WebSearch", "Task", "Agent",
                 "ToolSearch", "Skill", "Computer", "TodoWrite")
# The levels Claude Code's --effort takes (2.1.326 --help: low, medium, high, xhigh, max). A relay offers a level
# only when every model it offers lists it at initialize; "default" passes no --effort and keeps Claude Code's own.
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def _json(value):
    from .messages import dumps
    return dumps(value, MAX_INPUT) + b"\n"


def _event(line):
    from .messages import loads
    value = loads(line)
    if not isinstance(value, dict):
        raise NativeError("native-output-failed")
    return value


def _private_json(path, value):
    # Scratch is freshly allocated by OwnedRoot; never follow an existing file.
    import os
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(_json(value))


def _builtin_plugin_records_only(path):
    """Claude Code writes plugins/store/<name>_builtin-<hex>.json for its own built-in plugins on every start,
    sign-in included. That is not a customisation, and neither is the empty <record>.json.lock folder Claude Code
    leaves beside one when the relay ends it while it writes the record (seen after an Ask turn on the lab headend).
    Anything else under plugins/ (an installed plugin, a marketplace, a symlink, a lock with content) still is."""
    import re
    record = r"[A-Za-z0-9._-]{1,96}_builtin-[0-9a-f]{6,64}\.json"
    if path.is_symlink() or not path.is_dir():
        return False
    for item in path.rglob("*"):
        parts = item.relative_to(path).parts
        if item.is_symlink():
            return False
        if item.is_dir() and parts == ("store",):
            continue
        if (item.is_file() and len(parts) == 2 and parts[0] == "store" and re.fullmatch(record, parts[1])):
            continue
        if (item.is_dir() and len(parts) == 2 and parts[0] == "store" and re.fullmatch(record + r"\.lock", parts[1])
                and next(item.iterdir(), None) is None):
            continue
        return False
    return True


def _prepare(agent, executable, scratch, profile, endpoint, bearer, model, effort, platform, instruction):
    from .engine import clean_environment, resolve_agent_binary
    from .proxy import token, TOOLS
    from .selftest import platform_name
    if not executable or scratch is None or profile is None:
        raise NativeError("native-plan-incomplete")
    scratch, profile = safe_path(scratch), safe_path(profile)
    if (not scratch.is_absolute() or not scratch.is_dir() or not profile.is_absolute()
            or not profile.is_dir() or not token(bearer, 128)
            or not isinstance(endpoint, str)):
        raise NativeError("native-plan-incomplete")
    from urllib.parse import urlsplit
    try:
        url = urlsplit(endpoint)
        good_url = (url.scheme == "http" and url.hostname == "127.0.0.1" and
                    url.port is not None and 0 < url.port <= 65535 and url.path == "/mcp"
                    and url.username is None and not url.query and not url.fragment)
    except ValueError:
        good_url = False
    if not good_url or model is not None and not token(model, 128) or effort is not None and not token(effort, 32):
        raise NativeError("native-plan-incomplete")
    executable = resolve_agent_binary(executable)
    environment = clean_environment(scratch, platform=platform or platform_name())
    environment[TOKEN_ENV] = bearer
    if agent == "claude":
        for relative in ("CLAUDE.md", "skills", "plugins", "agents", "commands"):
            path = profile / relative
            if (path.exists() or path.is_symlink()) and not (relative == "plugins" and _builtin_plugin_records_only(path)):
                raise NativeError("native-profile-customised")
        environment["CLAUDE_CONFIG_DIR"] = str(profile)
        environment.update({"DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1",
                            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
        settings = scratch / "relay-settings.json"
        config = scratch / "relay-mcp.json"
        _private_json(settings, {"disableAllHooks": True, "enabledPlugins": {},
            "permissions": {"allow": ["mcp__deep32__" + name for name in sorted(TOOLS)],
                            "deny": list(CLAUDE_DENIED)}})
        _private_json(config, {"mcpServers": {SERVER: {"type": "http", "url": endpoint,
            "headers": {"Authorization": "Bearer ${" + TOKEN_ENV + "}"}}}})
        argv = [str(executable), "--print", "--verbose", "--input-format", "stream-json",
                "--output-format", "stream-json", "--include-partial-messages", "--tools", "",
                "--disallowedTools", ",".join(CLAUDE_DENIED), "--allowedTools",
                ",".join("mcp__deep32__" + name for name in sorted(TOOLS)),
                "--strict-mcp-config", "--mcp-config", str(config), "--setting-sources", "",
                "--settings", str(settings), "--restricted", "--permission-mode", "dontAsk",
                "--permission-prompts", "none", "--no-session-persistence"]
        if model:
            argv += ["--model", model]
        if effort is not None and effort != "default":
            if effort not in CLAUDE_EFFORTS:
                raise NativeError("native-plan-incomplete")
            argv += ["--effort", effort]
        protocol = ClaudeProtocol(scratch, model, instruction, effort=effort)
    else:
        # The relay-owned profile may hold independent sign-in, never user settings.
        # An operator-created config is refused rather than merged or overwritten.
        for relative in ("config.toml", "AGENTS.md", "rules", "skills", "plugins", "hooks.json"):
            if (profile / relative).exists():
                raise NativeError("native-profile-customised")
        environment["CODEX_HOME"] = str(profile)
        options = {
            "approval_policy": "never", "sandbox_mode": "read-only", "web_search": "disabled",
            "history.persistence": "none", "shell_environment_policy.inherit": "none",
            "project_doc_max_bytes": 0, "mcp_servers": {SERVER: {"url": endpoint,
                "bearer_token_env_var": TOKEN_ENV, "enabled": True,
                "enabled_tools": sorted(TOOLS)}},
        }
        if model:
            options["model"] = model
        if effort:
            options["model_reasoning_effort"] = effort
        argv = [str(executable), "--strict-config", "--no-daemon"]
        for name in CODEX_DISABLED:
            argv += ["--disable", name]
        for key, value in options.items():
            argv += ["-c", key + "=" + _toml(value)]
        argv += ["app-server"]
        protocol = CodexProtocol(scratch, model, effort, instruction, endpoint=endpoint)
    return LaunchPlan(tuple(argv), scratch, environment, protocol.startup()), protocol


def _toml(value):
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k) + "=" + _toml(v) for k, v in sorted(value.items())) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_toml(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=True)


def _tools_exact(values, *, qualified=False):
    from .proxy import TOOLS
    if not isinstance(values, list) or len(values) != len(TOOLS) or any(not isinstance(v, str) for v in values):
        return False
    expected = {"mcp__deep32__" + name for name in TOOLS} if qualified else set(TOOLS)
    return set(values) == expected


def _signed_out(value):
    # Claude Code 2.1.326 reports a sign-in the API refused (a revoked OAuth token) as API error 401 with the type
    # authentication_error on the result. Only that pair of fields selects the sign-in reason; the turn fails
    # either way.
    return value.get("api_error_status") == 401 and value.get("api_error_type") == "authentication_error"


def _supports_effort(item, level):
    levels = item.get("supportedEffortLevels")
    return item.get("supportsEffort") is True and isinstance(levels, list) and level in levels


def _claude_choices(names, listed, first):
    """The qualified model first, then Claude Code's other short aliases; "default" first, then each --effort
    level every one of those models lists. None keeps the qualified pair alone."""
    import re
    from .selftest import CHOICE_MAX, valid_choices
    models = [first] + [name for name in names if name != first and re.fullmatch(r"[a-z]{1,16}", name)]
    models = models[:CHOICE_MAX]
    efforts = ["default"] + [level for level in CLAUDE_EFFORTS
                             if all(_supports_effort(listed[name], level) for name in models)]
    if not valid_choices("claude", first, "default", models, efforts):
        return None
    return tuple(models), tuple(efforts)


def _usage(value):
    if (not isinstance(value, dict) or any(type(value.get(k)) is not int or value[k] < 0
                                          for k in ("input_tokens", "output_tokens"))):
        raise NativeError("native-usage-unavailable")
    return {k: value[k] for k in ("input_tokens", "output_tokens")}


class ClaudeProtocol:
    """R16: control handshake before the user, exact init evidence afterwards."""
    second_stage_required = True

    def __init__(self, cwd, model, instruction, *, install_check=False, effort=None):
        self.cwd, self.model, self.instruction = Path(cwd), model, instruction
        self.version = ""
        self.effort = effort or "default"
        self.choices = None
        self.partial = False
        self.install_check = install_check
        self.pending = []
        self.stage = "initialize"

    def startup(self):
        return _json({"type": "control_request", "request_id": "relay-init",
                      "request": {"subtype": "initialize"}})

    def replies(self):
        value, self.pending = self.pending, []
        return value

    def attest(self, line):
        event = _event(line)
        if event.get("type") == "system" and event.get("subtype") != "init":
            return None
        response = event.get("response")
        if (event.get("type") != "control_response" or not isinstance(response, dict)
                or response.get("subtype") != "success"
                or response.get("request_id") != ("relay-init" if self.stage == "initialize" else "relay-mcp-status")
                or not isinstance(response.get("response"), dict)):
            raise NativeError("native-startup-unverified")
        value = response["response"]
        if self.stage == "initialize":
            version = value.get("claude_code_version")
            if (not isinstance(version, str) or not version or len(version) > 128
                    or any(ord(c) < 32 or ord(c) > 126 for c in version)
                    or value.get("current_permission_mode") != "dontAsk"):
                raise NativeError("native-startup-unverified")
            # Account data is used only for this presence check and is never
            # retained in the receipt, diagnostics, status or process output.
            # Claude Code 2.1.326 still returns an account for a profile without a usable sign-in, holding only
            # apiProvider and tokenSource; a signed-in one also names the account (email, organization, plan).
            account = value.get("account")
            if not isinstance(account, dict) or not set(account) - {"apiProvider", "tokenSource"}:
                raise NativeError("claude-sign-in-required")
            models = value.get("models")
            if (not isinstance(models, list) or not models
                    or any(not isinstance(item, dict) or not isinstance(item.get("value"), str)
                           or not item["value"] or len(item["value"]) > 128
                           or any(ord(c) < 33 or ord(c) > 126 for c in item["value"]) for item in models)):
                raise NativeError("native-startup-unverified")
            names = [item["value"] for item in models]
            if len(names) != len(set(names)) or self.model is not None and self.model not in names:
                raise NativeError("native-startup-unverified")
            self.model = self.model or names[0]
            # These are Claude Code's model aliases (default, opus, ...); its init event reports the resolved model.
            self.aliases = frozenset(names)
            listed = dict(zip(names, models))
            # A chosen effort must still be one this binary lists for the chosen model, or nothing starts.
            if self.effort != "default" and (self.effort not in CLAUDE_EFFORTS
                                             or not _supports_effort(listed[self.model], self.effort)):
                raise NativeError("native-startup-unverified")
            self.choices = _claude_choices(names, listed, self.model)
            self.version = version
            self.stage = "mcp-status"
            self.pending = [_json({"type": "control_request", "request_id": "relay-mcp-status",
                                   "request": {"subtype": "mcp_status"}})]
            return None
        if self.stage != "mcp-status":
            raise NativeError("native-startup-unverified")
        servers = value.get("mcpServers")
        # Claude Code connects MCP servers in the background after initialize: the first status is often "pending".
        # Ask again (bounded) until it settles; anything other than connected is still refused below.
        if (not self.install_check and isinstance(servers, list) and len(servers) == 1
                and isinstance(servers[0], dict) and servers[0].get("name") == SERVER
                and servers[0].get("status") == "pending" and getattr(self, "status_polls", 0) < 80):
            import time
            time.sleep(0.1)
            self.status_polls = getattr(self, "status_polls", 0) + 1
            self.pending = [_json({"type": "control_request", "request_id": "relay-mcp-status",
                                   "request": {"subtype": "mcp_status"}})]
            return None
        if (not isinstance(servers, list) or len(servers) != 1
                or not isinstance(servers[0], dict) or servers[0].get("name") != SERVER
                or not isinstance(servers[0].get("status"), str) or not servers[0]["status"]
                or not self.install_check and servers[0]["status"] != "connected"):
            raise NativeError("native-startup-unverified")
        self.stage = "pre-turn"
        return True

    def _model_matches(self, reported):
        if reported == self.model:
            return True
        # A qualified Claude alias (default, opus, sonnet, ...) resolves to a concrete id at start: accept only a
        # claude-... id of that family (any for default). A concrete qualified model must still match exactly.
        import re
        alias = self.model
        if (alias not in getattr(self, "aliases", ()) or not isinstance(alias, str) or not re.fullmatch(r"[a-z]{1,16}", alias)
                or not isinstance(reported, str) or not re.fullmatch(r"claude-[a-z0-9.-]{1,96}", reported)):
            return False
        return alias == "default" or alias in reported

    def attest_turn(self, line):
        event = _event(line)
        if self.stage != "await-init":
            raise NativeError("native-startup-unverified")
        if event.get("type") == "system" and event.get("subtype") != "init":
            return None
        if event.get("type") != "system" or event.get("subtype") != "init":
            raise NativeError("native-startup-unverified")
        servers = event.get("mcp_servers")
        try:
            folder_ok = Path(event.get("cwd", "")).resolve() == self.cwd.resolve()
        except (OSError, ValueError):
            folder_ok = False
        if (not folder_ok or not _tools_exact(event.get("tools"), qualified=True)
                or not isinstance(servers, list) or len(servers) != 1
                or not isinstance(servers[0], dict) or servers[0].get("name") != SERVER
                or servers[0].get("status") != "connected"
                or not self._model_matches(event.get("model"))):
            raise NativeError("native-startup-unverified")
        self.stage = "running"
        return True

    def turn(self, question, context):
        if self.stage != "pre-turn" or self.install_check:
            raise NativeError("native-startup-unverified")
        value = _json({"type": "user", "message": {"role": "user", "content":
            _prompt(self.instruction, question, context)}, "parent_tool_use_id": None})
        self.stage = "await-init"
        return value

    def events(self, line):
        value = _event(line)
        if self.stage != "running":
            raise NativeError("native-startup-unverified")
        kind = value.get("type")
        if kind == "system":
            if value.get("subtype") == "init":
                raise NativeError("native-startup-unverified")
            return []
        if kind == "stream_event":
            event = value.get("event", {})
            delta = event.get("delta", {})
            if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                self.partial = True
                return [{"type": "delta", "text": delta["text"]}]
            return []
        if kind == "assistant":
            from .proxy import TOOLS
            content = value.get("message", {}).get("content", [])
            for block in content:
                if block.get("type") == "tool_use" and block.get("name") not in {
                        "mcp__deep32__" + name for name in TOOLS}:
                    raise NativeError("native-tool-unavailable")
            if value.get("is_api_error_message") is True and value.get("error") == "authentication_failed":
                # Claude Code's own "Failed to authenticate" line (token revoked, or expired and not refreshable)
                # comes before the failed result. It is not an answer; do not pass it on as one.
                raise NativeError("claude-sign-in-required")
            if not self.partial:
                return [{"type": "delta", "text": block["text"]} for block in content if block.get("type") == "text"]
            return []
        if kind == "result":
            if value.get("is_error") is not False or value.get("subtype") != "success":
                raise NativeError("claude-sign-in-required" if _signed_out(value) else "native-turn-failed")
            return [{"type": "final", "text": value["result"]},
                    {"type": "completed", "usage": _usage(value.get("usage"))}]
        if kind in ("user", "control_response"):
            return []
        if kind == "rate_limit_event":
            # Claude Code reports its usage window between messages. It carries no answer text; a turn that the
            # limit actually stops still ends in an error result, which fails above.
            return []
        raise NativeError("native-output-failed")


def _prompt(instruction, question, context):
    if not isinstance(question, str) or not isinstance(context, (tuple, list)):
        raise NativeError("native-input-limit")
    return instruction + "\n\n" + "\n".join(str(item) for item in context) + "\n\n" + question


class CodexProtocol:
    def __init__(self, cwd, model, effort, instruction, *, endpoint=None):
        self.cwd, self.model, self.effort = Path(cwd), model, effort or "default"
        self.instruction = instruction
        self.version = ""
        self.pending = []
        self.seen = set()
        self.thread = None
        self.usage = None
        self.endpoint = endpoint

    def startup(self):
        return _json({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "deep32_relay", "version": "1"},
            "capabilities": {"experimentalApi": True}}})

    def replies(self):
        value, self.pending = self.pending, []
        return value

    def attest(self, line):
        event = _event(line)
        if "method" in event:
            # No turn event, callback or tool request is legal before attestation.
            if event["method"] in ("thread/started", "mcpServer/startupUpdate", "mcpServer/startupComplete") and "id" not in event:
                return None
            raise NativeError("native-startup-unverified")
        ident, result = event.get("id"), event.get("result")
        if event.get("error") is not None or not isinstance(result, dict) or type(ident) is not int or ident in self.seen:
            raise NativeError("native-startup-unverified")
        self.seen.add(ident)
        if ident == 1 and self.seen == {1}:
            self.version = result.get("userAgent", "")
            self.pending = [_json({"method": "initialized", "params": {}}),
                _json({"id": 2, "method": "config/read", "params": {"includeLayers": True, "cwd": str(self.cwd)}}),
                _json({"id": 3, "method": "experimentalFeature/list", "params": {}})]
        elif ident == 2 and 1 in self.seen and 4 not in self.seen:
            self._config(result)
        elif ident == 3 and 1 in self.seen and 4 not in self.seen:
            features = result.get("data")
            if not isinstance(features, list):
                raise NativeError("native-startup-unverified")
            names = set()
            for feature in features:
                if not isinstance(feature, dict) or not isinstance(feature.get("name"), str) or type(feature.get("enabled")) is not bool or feature["name"] in names:
                    raise NativeError("native-startup-unverified")
                names.add(feature["name"])
                if feature["name"] in CODEX_DISABLED and feature["enabled"]:
                    raise NativeError("native-startup-unverified")
            if not set(CODEX_DISABLED) <= names:
                raise NativeError("native-startup-unverified")
        elif ident == 4 and self.seen == {1, 2, 3, 4}:
            thread = result.get("thread", {})
            self.thread = thread.get("id")
            selected_model = result.get("model")
            if (not isinstance(self.thread, str) or not self.thread
                    or not isinstance(selected_model, str) or not selected_model
                    or self.model is not None and selected_model != self.model
                    or Path(thread.get("cwd", "")).resolve() != self.cwd.resolve()):
                raise NativeError("native-startup-unverified")
            self.model = selected_model
            self.pending = [_json({"id": 5, "method": "mcpServerStatus/list", "params": {"threadId": self.thread}})]
        elif ident == 5 and self.seen == {1, 2, 3, 4, 5}:
            servers = result.get("data")
            if (not isinstance(servers, list) or len(servers) != 1 or servers[0].get("name") != SERVER
                    or servers[0].get("enabled", True) is not True):
                raise NativeError("native-startup-unverified")
            tools = servers[0].get("tools")
            values = list(tools) if isinstance(tools, dict) else [tool.get("name") for tool in tools] if isinstance(tools, list) else None
            if not _tools_exact(values):
                raise NativeError("native-startup-unverified")
            return True
        else:
            raise NativeError("native-startup-unverified")
        if self.seen == {1, 2, 3}:
            self.pending += [_json({"id": 4, "method": "thread/start", "params": {
                "cwd": str(self.cwd), "model": self.model, "approvalPolicy": "never",
                "sandbox": "read-only", "ephemeral": True, "baseInstructions": self.instruction}})]
        return None

    def _config(self, result):
        from .proxy import TOOLS
        config = result.get("config")
        if not isinstance(config, dict):
            raise NativeError("native-startup-unverified")
        servers = config.get("mcp_servers")
        enabled = {name: server for name, server in servers.items() if server.get("enabled", True)} if isinstance(servers, dict) else {}
        if (set(enabled) != {SERVER} or len(servers) != 1
                or not _tools_exact(enabled[SERVER].get("enabled_tools"))
                or self.endpoint is not None and (enabled[SERVER].get("url") != self.endpoint or
                    enabled[SERVER].get("bearer_token_env_var") != TOKEN_ENV)
                or config.get("approval_policy") != "never" or config.get("sandbox_mode") != "read-only"
                or config.get("web_search") != "disabled" or config.get("history", {}).get("persistence") != "none"
                or config.get("shell_environment_policy", {}).get("inherit") != "none"
                or config.get("project_doc_max_bytes") != 0
                or any(config.get("features", {}).get(name) is not False for name in CODEX_DISABLED)
                or config.get("model") is not None and (not isinstance(config["model"], str) or not config["model"])
                or self.model is not None and config["model"] != self.model):
            raise NativeError("native-startup-unverified")
        layers = result.get("layers")
        if not isinstance(layers, list):
            raise NativeError("native-startup-unverified")
        # Only CLI overrides/defaults are admitted. Managed/user/project layers
        # must not change the effective box, even inside an isolated profile.
        for layer in layers:
            source = layer.get("name", {}) if isinstance(layer, dict) else {}
            kind = source.get("type") if isinstance(source, dict) else None
            if kind not in ("commandLine", "default"):
                raise NativeError("native-startup-unverified")
        self.model = config["model"]
        self.effort = config.get("model_reasoning_effort") or "default"

    def turn(self, question, context):
        return _json({"id": 6, "method": "turn/start", "params": {"threadId": self.thread,
            "input": [{"type": "text", "text": _prompt(self.instruction, question, context)}]}})

    def events(self, line):
        value = _event(line)
        if "method" not in value:
            if value.get("id") == 6 and value.get("error") is None:
                return []
            raise NativeError("native-output-failed")
        method, params = value["method"], value.get("params", {})
        if "id" in value or params.get("threadId", self.thread) != self.thread:
            raise NativeError("native-tool-unavailable")
        if method == "item/agentMessage/delta":
            return [{"type": "delta", "text": params["delta"]}]
        if method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage", {}).get("last", {})
            self.usage = _usage({"input_tokens": usage.get("inputTokens"), "output_tokens": usage.get("outputTokens")})
        elif method == "turn/completed":
            if params.get("turn", {}).get("status") != "completed" or self.usage is None:
                raise NativeError("native-turn-failed")
            return [{"type": "completed", "usage": self.usage}]
        elif method in ("item/started", "item/completed"):
            item = params.get("item", {})
            if item.get("type") not in ("agentMessage", "reasoning", "mcpToolCall", "userMessage"):
                raise NativeError("native-tool-unavailable")
            if item.get("type") == "mcpToolCall":
                from .proxy import TOOLS
                if item.get("server") != SERVER or item.get("tool") not in TOOLS:
                    raise NativeError("native-tool-unavailable")
        elif method not in ("turn/started", "item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
            raise NativeError("native-output-failed")
        return []
