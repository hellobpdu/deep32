"""Owned native agents; staged attestation gates every research tool call."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class EngineError(RuntimeError):
    pass


def resolve_agent_binary(executable):
    """Resolve the selected native-agent entry point for this launch only.

    Native installers may keep a stable symlink while replacing its versioned
    target. This exception is confined to executable selection: relay state,
    profiles and the resolved target still pass the no-link path checks.
    """
    import stat
    from .state import StateError, safe_path
    try:
        path = Path(executable)
        if not path.is_absolute() or any(ord(char) < 32 for char in str(path)):
            raise StateError("binary-unavailable")
        resolved = safe_path(path.resolve(strict=True))
        info = resolved.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise StateError("binary-unavailable")
        return resolved
    except (OSError, ValueError, TypeError, RuntimeError):
        raise StateError("binary-unavailable") from None


@dataclass(frozen=True)
class Qualification:
    agent: str
    platform: str
    binary_sha256: str = ""
    version: str = ""
    tool_policy: bool = False
    scratch_writes: bool = False
    credential_resolution: bool = False
    descendant_containment: bool = False
    startup_attestation: bool = False
    usage_accounting: bool = False
    native_evidence: bool = False

    @property
    def qualified(self):
        # This is an evidence description, not an operator-controlled override.
        # NativeAgent below deliberately has no launcher pending reviewed adapters.
        return (self.agent in ("claude", "codex") and self.platform in ("windows", "linux", "macos")
                and len(self.binary_sha256) == 64 and all(c in "0123456789abcdef" for c in self.binary_sha256)
                and bool(self.version) and all((self.tool_policy, self.scratch_writes,
                    self.credential_resolution, self.descendant_containment,
                    self.startup_attestation, self.usage_accounting, self.native_evidence)))


def clean_environment(scratch, *, platform):
    """Every mutable native home is inside scratch; never copy parent secrets."""
    from .state import safe_path
    root = safe_path(scratch).resolve(strict=True)
    if not root.is_dir():
        raise EngineError("scratch-unavailable")
    value = str(root)
    result = {key: value for key in ("HOME", "USERPROFILE", "TMP", "TEMP", "TMPDIR",
        "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
        "APPDATA", "LOCALAPPDATA")}
    result.update({"PYTHONUTF8": "1", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
    if platform == "windows":
        # SystemRoot is runtime plumbing, not an inherited executable search path.
        system_root = os.environ.get("SystemRoot", "")
        if not system_root or not Path(system_root).is_absolute():
            raise EngineError("runtime-unavailable")
        result["SystemRoot"] = system_root
    return result


def advertised(qualification, model, effort):
    """The models and efforts a qualified relay offers, the qualified pair first.

    The lists come only from the attestation record: its no-model startup check copied them from the agent's
    own initialize reply. A record without them (Codex, or an older record) offers its qualified pair alone.
    """
    from .selftest import valid_choices
    value = qualification or {}
    models, efforts = value.get("models"), value.get("efforts")
    if models is not None and valid_choices(value.get("agent"), model, effort, models, efforts):
        return tuple(models), tuple(efforts)
    return (model,), (effort,)


class NativeAgent:
    """A relay-owned profile and a fresh startup gate on every Ask turn."""
    ready = False
    models = ()
    efforts = ()
    reason = "native-agent-unqualified"

    def __init__(self, agent, profile, *, executable=None, version="", platform=None,
                 qualification=None, gateway=None, model=None, effort=None,
                 instruction="", tree_factory=None, proxy_factory=None):
        if agent not in ("claude", "codex"):
            raise EngineError("invalid-agent")
        self.agent, self.profile = agent, profile
        self.executable, self.version = executable, version
        from .selftest import platform_name
        from .native import ClaudeCodeLauncher, CodexLauncher
        self.platform = platform or platform_name()
        self.launcher = ClaudeCodeLauncher() if agent == "claude" else CodexLauncher()
        self.qualification = qualification
        self.gateway = gateway
        self.instruction = instruction
        self.tree_factory, self.proxy_factory = tree_factory, proxy_factory
        self.model = model or (qualification or {}).get("model")
        self.effort = effort or (qualification or {}).get("effort")
        self.ready = bool(executable and self.model and self.effort and qualification
                          and qualification.get("schema") == "deep32.relay-attestation.v1"
                          and qualification.get("state") == "qualified")
        self.models, self.efforts = advertised(qualification, self.model, self.effort) if self.ready else ((), ())
        self.reason = "none" if self.ready else "native-agent-unqualified"

    def accepts(self, model, effort):
        """A session may choose any advertised model with any advertised effort (D32U lists are independent)."""
        return self.ready and model in self.models and effort in self.efforts

    def bind_gateway(self, gateway):
        self.gateway = gateway
        return self

    def verify(self, scratch, capability):
        from .selftest import binary_digest
        from .state import safe_path
        if (not self.ready or self.gateway is None or
                binary_digest(self.executable) != self.qualification["binary_sha256"]
                or not safe_path(self.profile).is_dir()
                or self.gateway.capabilities.get(capability.session) is not capability
                or self.gateway.clock() >= capability.deadline):
            raise EngineError("native-agent-unqualified")
        return True

    def start(self, question, context, scratch, capability, *, model=None, effort=None):
        from .native import NativeSession
        from .proxy import HTTPProxy
        from .process import native_tree
        from .selftest import binary_digest
        self.verify(scratch, capability)
        # The session's choice, or the qualified pair. Only an advertised pair reaches the agent.
        model = self.model if model is None else model
        effort = self.effort if effort is None else effort
        if not self.accepts(model, effort):
            raise EngineError("native-agent-unqualified")
        def live():
            return (capability.active and self.gateway.capabilities.get(capability.session) is capability
                    and self.gateway.clock() < capability.deadline)
        startup_gate = StartupGateway(self.gateway)
        endpoint = (self.proxy_factory or HTTPProxy)(startup_gate, capability)
        tree = None
        session = None
        try:
            endpoint.start()
            plan, protocol = self.launcher.prepare(self.executable, scratch, self.profile,
                endpoint=endpoint.url, bearer=capability.bearer, model=model, effort=effort,
                platform=self.platform, instruction=self.instruction)
            tree = (self.tree_factory or native_tree)(self.platform)
            class GuardedTree:
                def start(inner, *args, **kwargs):
                    if not live():
                        raise EngineError("inactive-turn")
                    if binary_digest(args[0][0]) != self.qualification["binary_sha256"]:
                        raise EngineError("native-agent-unqualified")
                    return tree.start(*args, **kwargs)
                def cancel(inner):
                    return tree.cancel()
            session = NativeSession(plan, protocol, GuardedTree(), endpoint, live=live,
                                    on_attested=lambda: startup_gate.allow_tools())
            session.initialise(timeout=10)  # MCP over the Ask link can take a few seconds to connect
            return session.release(question, context)
        except Exception as error:
            if session is not None:
                if session.cancel() is not True:
                    # Retain exact ownership in Supervisor; its failure path
                    # retries cancellation before scratch or slots are freed.
                    session.failure = "native-startup-unverified"
                    return session
            else:
                clean = endpoint.close() is True
                if tree is not None:
                    clean = tree.cancel() is True and clean
                if not clean:
                    return FailedStartup(endpoint, tree)
            reason = "claude-sign-in-required" if str(error) == "claude-sign-in-required" else "native-startup-unverified"
            raise EngineError(reason) from None


class FailedStartup:
    """Retain a partially started endpoint/tree until cleanup is confirmed."""
    def __init__(self, endpoint, tree):
        self.endpoint, self.tree = endpoint, tree
    def poll(self):
        raise EngineError("native-startup-unverified")
    def cancel(self):
        try:
            proxy_clean = self.endpoint.close() is True
        except Exception:
            proxy_clean = False
        try:
            tree_clean = self.tree is None or self.tree.cancel() is True
        except Exception:
            tree_clean = False
        return proxy_clean and tree_clean


class StartupGateway:
    """Permit MCP discovery before attestation, never a research tool call."""
    def __init__(self, gateway):
        self.gateway = gateway
        self.verified = False
    def __getattr__(self, name):
        return getattr(self.gateway, name)
    def allow_tools(self):
        self.verified = True
    def http_request(self, cap, **request):
        from .messages import loads
        from .proxy import ProxyError
        try:
            value = loads(request.get("body", b""))
        except (ValueError, UnicodeError):
            raise ProxyError("invalid-json") from None
        if isinstance(value, dict) and value.get("method") == "tools/call" and not self.verified:
            raise ProxyError("native-startup-unverified")
        return self.gateway.http_request(cap, **request)


# Connector and daemon share one protected qualification format. These aliases
# preserve a small public API without introducing command-line receipt overrides.
from .selftest import qualify_install, qualification_status, live_selftest


def native_from_owned(owner, receipt, gateway=None):
    """Passive construction: no agent probe, configuration read or model call.

    A changed/malformed receipt cannot enable a native lane. There are currently
    no reviewed version-specific CLI protocols in the authorised source inputs;
    their concrete launcher controls remain fail closed pending qualification.
    """
    agent = receipt.get("agent")
    if agent not in ("claude", "codex"):
        raise EngineError("invalid-agent")
    from .selftest import validate_attestation, qualification_status
    qualification = None
    try:
        owner.validate(receipt.get("install_id"))
        qualification = validate_attestation(owner.read_json("private/qualification/%s.json" % agent, 16384))
        if qualification_status(owner, receipt, executable=receipt.get("agent_binary"))["state"] != "qualified":
            qualification = None
    except (OSError, ValueError):
        qualification = None
    except Exception:
        # Never expose an underlying path or protected-state parse exception.
        qualification = None
    instruction = ""
    if qualification:
        try:
            import hashlib
            relative = "packages/" + receipt["release_id"] + "/RESEARCH.md"
            raw = owner.read(relative)
            if hashlib.sha256(raw).hexdigest() != receipt["owned_files"][relative]:
                raise EngineError("binding-changed")
            instruction = raw.decode("utf-8")
        except Exception:
            qualification = None
    return NativeAgent(agent, owner.root / "profiles" / agent, executable=receipt.get("agent_binary"),
                       version=(qualification or {}).get("version", ""),
                       qualification=qualification, gateway=gateway, instruction=instruction)
