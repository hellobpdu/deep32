"""Protected, read-only observations of one explicitly selected connector.

The validators below extract the accepted connect/C6/pairing/publication rules.
No installer, PrivateStore, Status or publication CLI is imported or constructed.
Native Windows seams only query security descriptors and decrypt CurrentUser
DPAPI; their qualification on installed hosts is a separate release gate.
"""
from dataclasses import dataclass, field
import ctypes
import functools
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
import re
import shlex
import stat
import tomllib
from urllib.parse import urlsplit

MAX_FILE = 4 * 1024 * 1024
_CODES = frozenset(("profile-selection", "profile-state", "profile-config",
    "profile-launcher", "unsafe-file", "file-unavailable", "credential-permissions",
    "credential-unavailable", "credential-invalid", "c6-invalid", "peer-invalid",
    "publication-invalid", "publication-uncertain", "snapshot-changed"))


class ProfileError(Exception):
    """A fixed, public reason; never retain an OS/decryption/parser exception."""
    def __init__(self, code):
        self.code = code if code in _CODES else "profile-state"
        super().__init__(self.code)


def _public(function):
    """Discard private exception objects before raising a public fixed error."""
    @functools.wraps(function)
    def guarded(*args, **kwargs):
        code = None
        try:
            return function(*args, **kwargs)
        except Exception as error:
            code = error.code if isinstance(error, ProfileError) else "profile-state"
        # Outside the handler: __context__ cannot retain a private OS/parser error.
        raise ProfileError(code) from None
    return guarded


class Credential:
    """Memory-only header capability; no bearer appears in repr or str."""
    __slots__ = ("__value",)

    def __init__(self, raw):
        if type(raw) is not bytes or re.fullmatch(rb"[0-9a-f]{64}\n", raw) is None:
            raise ProfileError("credential-invalid")
        self.__value = raw[:-1].decode("ascii")

    def authorization(self):
        return "Bearer " + self.__value

    def __repr__(self):
        return "<Credential redacted>"

    __str__ = __repr__

    def __reduce__(self):
        raise ProfileError("credential-unavailable")


@dataclass(frozen=True)
class WindowsACL:
    current_user: str = field(repr=False)
    sddl: str = field(repr=False)


@dataclass(frozen=True)
class SelectedProfile:
    agent: str
    credential: Credential = field(repr=False)
    url: str = field(repr=False)
    executable: str = field(repr=False)
    state_path: Path = field(repr=False)
    peer_id: str = field(repr=False)
    server_name: str = field(repr=False)
    variable: str = field(repr=False)
    launcher_origin_confirmed: bool = False


@dataclass(frozen=True, repr=False)
class _FileMarker:
    path: Path
    data: bytes
    identity: tuple
    maximum: int
    check: object
    access: object

    def __repr__(self):
        return "_FileMarker(<private>)"


@dataclass(frozen=True, repr=False)
class _DirectoryMarker:
    path: Path
    identity: tuple
    access: WindowsACL | None
    getter: object

    def __repr__(self):
        return "_DirectoryMarker(<private>)"


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            getattr(info, "st_uid", None), getattr(info, "st_gid", None),
            info.st_size, info.st_mtime_ns,
            getattr(info, "st_birthtime_ns", None) if os.name == "nt" else info.st_ctime_ns)


def _json(raw, code):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise ProfileError(code) from None


def _safe(path):
    """Check the exact selected path and its ancestors, never search a home."""
    try:
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts:
            raise ProfileError("unsafe-file")
        for part in (path, *path.parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ProfileError("unsafe-file")
        return path
    except (OSError, ValueError, TypeError):
        raise ProfileError("file-unavailable") from None


def _read(path, limit=MAX_FILE, *, check=None, markers=None):
    path = _safe(path)
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            info = os.fstat(stream.fileno())
            current = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_size > limit or getattr(info, "st_file_attributes", 0) & 0x400 or
                    (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)):
                raise ProfileError("unsafe-file")
            access = check(path, info, stream.fileno()) if check is not None else None
            raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise ProfileError("unsafe-file")
            _safe(path)
            final = path.lstat()
            if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
                    final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns):
                raise ProfileError("unsafe-file")
            if _identity(info) != _identity(final):
                raise ProfileError("unsafe-file")
            if markers is not None:
                markers.append(_FileMarker(path, raw, _identity(final), limit, check, access))
            return raw
    except ProfileError:
        raise
    except Exception:
        raise ProfileError("file-unavailable") from None


def _directory(path):
    path = _safe(path)
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise ProfileError("unsafe-file")
    return path


def _members(path, limit):
    # Consume only limit+1 observations; an oversized directory is not sorted
    # or traversed in full before refusing it.
    # Path.iterdir on supported Python releases may itself materialise entries.
    # scandir is the native streaming seam and is closed on the first refusal.
    with os.scandir(path) as entries:
        members = [path / entry.name for entry in islice(entries, limit + 1)]
    if len(members) > limit:
        raise ProfileError("unsafe-file")
    return sorted(members)


def _endpoint(value):
    # Same endpoint contract as connect.endpoint: no credentials/query/redirect.
    try:
        if type(value) is not str or not 1 <= len(value) <= 512 or any(ord(c) <= 32 or ord(c) >= 127 for c in value):
            raise ValueError()
        part = urlsplit(value)
        port = part.port
        if (part.scheme != "https" or not part.hostname or part.username or part.password or
                part.path != "/mcp" or part.query or part.fragment or
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", part.hostname) is None or
                port is not None and not 1 <= port <= 65535):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise ProfileError("profile-state") from None


@_public
def validate_windows_acl(value, *, protected=False):
    """Narrow getter-only SDDL validation; no ACL normalisation or repair."""
    try:
        if not isinstance(value, WindowsACL) or re.fullmatch(r"S-1-[0-9-]{1,160}", value.current_user) is None:
            raise ValueError()
        prefix, marker, dacl = value.sddl.partition("D:")
        if not marker or prefix != "O:" + value.current_user:
            raise ValueError()
        aces = re.findall(r"\(([^)]*)\)", dacl)
        flags = re.sub(r"\([^)]*\)", "", dacl)
        if not aces or flags not in ("P", "PAI", "AI", "") or protected and "P" not in flags:
            raise ValueError()
        trustees = set()
        for ace in aces:
            kind, inheritance, rights, object_id, inherited_id, trustee = ace.split(";")
            if trustee == "OW":
                trustee = value.current_user
            if (kind != "A" or rights != "FA" or object_id or inherited_id or
                    trustee not in (value.current_user, "SY", "BA") or
                    re.fullmatch(r"(?:OI|CI|NP|IO|ID)*", inheritance) is None or "IO" in inheritance):
                raise ValueError()
            trustees.add(trustee)
        if value.current_user not in trustees:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ProfileError("credential-permissions") from None


@_public
def native_windows_acl(path, fd=None):
    """Query an existing handle/path and the current SID. No setter is loaded."""
    if os.name != "nt":
        raise ProfileError("credential-unavailable")
    from ctypes import wintypes
    import msvcrt
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [pointer]
    adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(wintypes.LPWSTR)]
    adv.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(pointer), pointer, ctypes.POINTER(pointer), pointer, ctypes.POINTER(pointer)]
    adv.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(pointer), pointer, ctypes.POINTER(pointer), pointer, ctypes.POINTER(pointer)]
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [pointer, wintypes.DWORD,
        wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD)]
    handle = wintypes.HANDLE()
    sid_text = wintypes.LPWSTR()
    descriptor = pointer()
    text = wintypes.LPWSTR()
    try:
        if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(handle)):
            raise ValueError()
        size = wintypes.DWORD()
        adv.GetTokenInformation(handle, 1, None, 0, ctypes.byref(size))
        if not 0 < size.value <= 65536:
            raise ValueError()
        token = ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(handle, 1, token, size, ctypes.byref(size)):
            raise ValueError()
        sid = ctypes.cast(token, ctypes.POINTER(pointer))[0]
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise ValueError()
        owner, dacl = pointer(), pointer()
        args = (1, 5, ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor))
        result = (adv.GetSecurityInfo(wintypes.HANDLE(msvcrt.get_osfhandle(fd)), *args) if fd is not None
                  else adv.GetNamedSecurityInfoW(str(path), *args))
        if result != 0 or not dacl:
            raise ValueError()
        if not adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, 5, ctypes.byref(text), None):
            raise ValueError()
        return WindowsACL(sid_text.value, text.value)
    except Exception:
        raise ProfileError("credential-permissions") from None
    finally:
        for value in (sid_text, text, descriptor):
            if value:
                kernel.LocalFree(value)
        if handle:
            kernel.CloseHandle(handle)


@_public
def native_dpapi_decrypt(raw):
    """CurrentUser CryptUnprotectData, UI forbidden; never ProtectData."""
    if os.name != "nt" or type(raw) is not bytes or not 0 < len(raw) <= 65536:
        raise ProfileError("credential-unavailable")
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source, result = Blob(len(raw), buffer), Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    try:
        if not crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
            raise ValueError()
        if result.size != 65:
            raise ValueError()
        return ctypes.string_at(result.data, result.size)
    except Exception:
        raise ProfileError("credential-unavailable") from None
    finally:
        ctypes.memset(buffer, 0, len(raw))
        if result.data:
            ctypes.memset(result.data, 0, result.size)
            kernel.LocalFree(result.data)


def validate_posix_permissions(info, current_uid, *, directory=False):
    """Pure extraction of connect.read owner/mode checks for platform fixtures."""
    if (current_uid is None or info.st_uid != current_uid or
            stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)):
        raise ProfileError("credential-permissions")


@_public
def read_credential(path, *, private_root, windows=None, acl_getter=None, decrypt=None, current_uid=None,
                    _markers=None, _directories=None):
    """Read one bound slot; reject links, broad ownership and changed files."""
    windows = os.name == "nt" if windows is None else windows
    try:
        root, path = _directory(private_root), _safe(path)
        path.relative_to(root)
        if path == root:
            raise ValueError()
        # Build components without inspecting an unrelated private/home tree.
        directories = [root]
        for component in path.parent.relative_to(root).parts:
            directories.append(directories[-1] / component)
        getter = acl_getter or native_windows_acl
        uid = current_uid if current_uid is not None else (getattr(os, "geteuid", lambda: None)())
        for directory in directories:
            info = _directory(directory).lstat()
            access = None
            if windows:
                access = getter(directory, None)
                validate_windows_acl(access, protected=directory in (root, path.parent))
            else:
                validate_posix_permissions(info, uid, directory=True)
            if _directories is not None:
                _directories.append(_DirectoryMarker(directory, _identity(info), access,
                    getter if windows else None))
        def check(selected, info, fd):
            if windows:
                access = getter(selected, fd)
                validate_windows_acl(access)
                return access
            else:
                validate_posix_permissions(info, uid)
        raw = _read(path, 65536 if windows else 65, check=check, markers=_markers)
        if windows:
            raw = (decrypt or native_dpapi_decrypt)(raw)
        return Credential(raw)
    except ProfileError:
        raise
    except Exception:
        raise ProfileError("credential-unavailable") from None


def _config(raw, state):
    try:
        if state["agent"] == "codex":
            obj = tomllib.loads(raw.decode("utf-8"))
            servers = obj.get("mcp_servers")
            expected = {"url": state["url"], "bearer_token_env_var": state["variable"],
                        "startup_timeout_sec": 30, "tool_timeout_sec": 120}
        else:
            obj = _json(raw, "profile-config")
            servers = obj.get("mcpServers")
            expected = {"type": "http", "url": state["url"],
                        "headers": {"Authorization": "Bearer ${" + state["variable"] + "}"}}
        if type(servers) is not dict or servers.get(state["server_name"]) != expected:
            raise ValueError()
        # TOML floats compare equal to integer timeout settings in Python but
        # do not preserve the owning client's exact typed configuration.
        if state["agent"] == "codex" and any(type(servers[state["server_name"]][key]) is not int
                for key in ("startup_timeout_sec", "tool_timeout_sec")):
            raise ValueError()
    except (ValueError, UnicodeError, AttributeError, KeyError):
        raise ProfileError("profile-config") from None


def _launcher(raw, state_path, windows, launcher_origin=None):
    try:
        text = raw.decode("utf-8")
        if windows:
            match = re.fullmatch(r"#requires -Version 5\.1\n& ('(?:[^']|'')*') -I -B ('(?:[^']|'')*') --launch ('(?:[^']|'')*') @args\nexit \$LASTEXITCODE\n", text)
            if match is None:
                raise ValueError()
            values = [value[1:-1].replace("''", "'") for value in match.groups()]
        else:
            if not text.startswith("#!/bin/sh\nexec ") or not text.endswith(' "$@"\n'):
                raise ValueError()
            parts = shlex.split(text[len("#!/bin/sh\nexec "):-len(' "$@"\n')])
            if len(parts) != 6 or parts[1:3] != ["-I", "-B"] or parts[4] != "--launch":
                raise ValueError()
            values = [parts[0], parts[3], parts[5]]
            canonical = "#!/bin/sh\nexec " + shlex.quote(values[0]) + " -I -B " + shlex.quote(values[1]) + " --launch " + shlex.quote(values[2]) + ' "$@"\n'
            if text != canonical:
                raise ValueError()
        if (not all(Path(value).is_absolute() for value in values) or
                Path(values[1]).name != "connect.py" or values[2] != str(state_path)):
            raise ValueError()
        if launcher_origin is not None:
            if (type(launcher_origin) is not tuple or len(launcher_origin) != 2 or
                    any(type(value) is not str or not Path(value).is_absolute() for value in launcher_origin) or
                    tuple(values[:2]) != launcher_origin):
                raise ValueError()
        return launcher_origin is not None
    except (ValueError, UnicodeError, IndexError):
        raise ProfileError("profile-launcher") from None


def _unchanged(markers, directories, connection, root):
    """Ordinary-change detection only; a memory snapshot is not a lock."""
    same = True
    try:
        if _members(connection, 1) != [root]:
            same = False
        for marker in directories:
            if _identity(_directory(marker.path).lstat()) != marker.identity:
                same = False
            if marker.getter is not None and marker.getter(marker.path, None) != marker.access:
                same = False
        for marker in markers:
            current = []
            raw = _read(marker.path, marker.maximum, check=marker.check, markers=current)
            if raw != marker.data or current[0].identity != marker.identity or current[0].access != marker.access:
                same = False
    except Exception:
        same = False
    if not same:
        raise ProfileError("snapshot-changed")


@_public
def read_selected_profile(profile_dir, agent, *, windows=None, acl_getter=None, decrypt=None, current_uid=None,
                          launcher_origin=None):
    """Observe exactly one isolated profile; never run its launcher or agent."""
    windows = os.name == "nt" if windows is None else windows
    try:
        if agent not in ("codex", "claude"):
            raise ProfileError("profile-selection")
        home = _directory(profile_dir)
        connections = _directory(home / "deep32-connect")
        candidates = _members(connections, 1)
        # A profile created by isolated_profile belongs to one agent/endpoint.
        # Refuse ambiguity before reading any candidate's state or credential.
        if len(candidates) != 1 or re.fullmatch("[0-9a-f]{16}", candidates[0].name) is None:
            raise ProfileError("profile-selection")
        root = _directory(candidates[0])
        state_path = root / "state.json"
        markers, directories = [], []
        state = _json(_read(state_path, markers=markers), "profile-state")
        required = {"schema", "agent", "url", "install_id", "server_name", "variable", "profile_dir",
                    "peer_id", "credential", "executable", "version", "launcher"}
        if (type(state) is not dict or set(state) != required or
                state["schema"] != "deep32.connector.v1" or state["agent"] != agent or
                state["profile_dir"] != str(home) or
                any(type(state[key]) is not str for key in required)):
            raise ProfileError("profile-state")
        url = _endpoint(state["url"])
        ident = state["install_id"]
        if (re.fullmatch("[0-9a-f]{32}", ident) is None or
                re.fullmatch("[a-z][a-z0-9_]{2,63}", state["server_name"]) is None or
                state["variable"] != "DEEP32_ALPHA_" + ident[:8].upper() + "_MCP_TOKEN" or
                re.fullmatch("[A-Za-z0-9_-]{1,64}", state["peer_id"]) is None or
                root.name != hashlib.sha256((url + "\n" + agent).encode("ascii")).hexdigest()[:16] or
                not Path(state["executable"]).is_absolute() or
                re.fullmatch(r"[A-Za-z0-9 ._()+:/-]{1,160}", state["version"]) is None):
            raise ProfileError("profile-state")
        credential = root / "private" / "received" / "gateway" / state["peer_id"] / "mcp-token"
        launcher = root / ("launch.ps1" if windows else "launch.sh")
        if state["credential"] != str(credential) or state["launcher"] != str(launcher):
            raise ProfileError("profile-state")
        _config(_read(home / ("config.toml" if agent == "codex" else ".claude.json"), markers=markers), state)
        origin_confirmed = _launcher(_read(launcher, 16384, markers=markers), state_path, windows, launcher_origin)
        retained = read_credential(credential, private_root=root, windows=windows,
            acl_getter=acl_getter, decrypt=decrypt, current_uid=current_uid,
            _markers=markers, _directories=directories)
        _unchanged(markers, directories, connections, root)
        return SelectedProfile(agent, retained, url, state["executable"], state_path,
                               state["peer_id"], state["server_name"], state["variable"], origin_confirmed)
    except ProfileError:
        raise
    except Exception:
        raise ProfileError("profile-state") from None


@dataclass(frozen=True)
class C6Observation:
    stage: str
    state: str
    version: str | None
    error_kind: str | None


_STAGES = frozenset(("install", "firstboot", "keys", "signin", "tailnet", "content", "import", "pairing", "ready"))
_ERRORS = frozenset(("forge-settings-invalid", "forge-identity-unsafe", "forge-identity-incomplete",
    "forge-identity-invalid", "forge-pairing-expired", "forge-pairing-failed", "forge-model-auth-denied",
    "forge-model-response-invalid", "forge-model-health-timeout", "forge-firstboot-running", "forge-firstboot-failed"))


@_public
def parse_c6(raw, *, dialect="workstation"):
    """Parse C6 without retaining a boot ID, name, message or short-lived code."""
    try:
        if type(raw) is not bytes or not 1 <= len(raw) <= 512 or dialect not in ("workstation", "forge"):
            raise ValueError()
        text = raw.decode("ascii")
        if text.endswith("\n"):
            text = text[:-1]
        if len(text) > 511 or any(ord(c) < 32 or ord(c) > 126 for c in text):
            raise ValueError()
        head, marker, message = text.partition(" msg=")
        if marker and (dialect == "workstation" and (not 1 <= len(message) <= 240 or "\\" in message)):
            raise ValueError()
        parts = head.split(" ")
        if (len(parts) < 5 or parts[0] != "D32S/1" or re.fullmatch("[1-9][0-9]{0,8}", parts[1]) is None or
                parts[2] not in ("forge", "workstation", "headless")):
            raise ValueError()
        obj = {}
        for part in parts[3:]:
            key, equals, value = part.partition("=")
            if not equals or not value or key in obj or key not in {
                    "boot", "stage", "state", "version", "name", "signin", "pair", "pair_with", "expires", "error"}:
                raise ValueError()
            obj[key] = value
        if (obj.get("stage") not in _STAGES or obj.get("state") not in ("running", "waiting", "done", "failed") or
                "boot" in obj and re.fullmatch("[0-9a-f]{8}", obj["boot"]) is None or
                dialect == "forge" and "boot" not in obj):
            raise ValueError()
        patterns = {"version": r"v?[0-9]{1,4}(?:\.[0-9]{1,4}){1,3}(?:-(?:alpha|beta|rc)\.[0-9]{1,4})?", "name": r"[a-z][a-z0-9-]{0,62}",
                    "signin": r"https://login\.tailscale\.com/[A-Za-z0-9/_?=.-]{1,256}",
                    "pair": r"[A-Z2-7]{10}", "pair_with": r"forge|workstation|relay|headend",
                    "expires": r"[1-9][0-9]{0,2}",
                    "error": r"[A-Z][A-Z0-9_]{1,47}" if dialect == "workstation" else r"[A-Za-z][A-Za-z0-9_-]{0,95}"}
        if any(key in obj and re.fullmatch(pattern, obj[key]) is None for key, pattern in patterns.items()):
            raise ValueError()
        if dialect == "workstation" and any(re.search(r"[0-9a-fA-F]{32}|tskey-|PRIVATE KEY|\$2[aby]\$", value)
                                                for value in (*obj.values(), message)):
            raise ValueError()
        if ("signin" in obj and (obj["stage"], obj["state"]) != ("signin", "waiting") or
                "pair" in obj and ((obj["stage"], obj["state"]) != ("pairing", "waiting") or "expires" not in obj) or
                "expires" in obj and ("pair" not in obj or int(obj["expires"]) > 300) or
                "pair_with" in obj and obj["stage"] != "pairing" or
                dialect == "workstation" and ((obj["state"] == "failed") != ("error" in obj))):
            raise ValueError()
        # Unknown but syntactically valid errors are categorised, never echoed.
        error = obj.get("error")
        error = error if error in _ERRORS else "reported-failure" if error else None
        version = obj.get("version")
        if version and re.search(r"[0-9a-fA-F]{32}|tskey-|PRIVATE", version):
            raise ValueError()
        return C6Observation(obj["stage"], obj["state"], version, error)
    except (ValueError, UnicodeError, TypeError, IndexError):
        raise ProfileError("c6-invalid") from None


_RECORD_KEYS = ("format", "format_version", "kind", "side", "peer_id", "peer_role", "peer_pin", "own_pin",
    "transport", "peer_address", "peer_port", "peer_host_name", "peer_dns_name", "publish", "paired_at", "received", "given")


@dataclass(frozen=True)
class PeerRecord:
    kind: str
    side: str
    peer_role: str
    transport: str
    received: tuple
    given: tuple
    peer_id: str = field(repr=False)
    peer_pin: str = field(repr=False)
    own_pin: str = field(repr=False)
    peer_address: str | None = field(repr=False)
    peer_dns_name: str | None = field(repr=False)
    peer_port: int | None = field(repr=False)
    publish: str | None = field(repr=False)


def _peer(obj):
    # Canonical store._validate rules, with duplicate-safe JSON and typed roles.
    if (type(obj) is not dict or tuple(obj) != _RECORD_KEYS or obj["format"] != "deep32-peer" or
            type(obj["format_version"]) is not int or obj["format_version"] != 1 or
            obj["kind"] not in ("relay", "forge", "gateway") or obj["side"] not in ("joined", "joiner") or
            type(obj["peer_id"]) is not str or re.fullmatch("[A-Za-z0-9_-]{1,64}", obj["peer_id"]) is None or
            any(type(obj[key]) is not str or re.fullmatch("[0-9a-f]{64}", obj[key]) is None for key in ("peer_pin", "own_pin")) or
            obj["peer_role"] not in ("workstation", "forge", "headend", "relay", "headless") or
            obj["transport"] not in ("tailnet", "loopback") or type(obj["paired_at"]) is not int):
        raise ProfileError("peer-invalid")
    for key in ("received", "given"):
        if type(obj[key]) is not list or any(value not in ("forge-token", "kb-public.pem", "mcp-token", "model-endpoints.json") for value in obj[key]):
            raise ProfileError("peer-invalid")
    for key in ("peer_address", "peer_host_name", "peer_dns_name", "publish"):
        if obj[key] is not None and (type(obj[key]) is not str or len(obj[key]) > 512):
            raise ProfileError("peer-invalid")
    if obj["peer_port"] is not None and (type(obj["peer_port"]) is not int or not 1 <= obj["peer_port"] <= 65535):
        raise ProfileError("peer-invalid")
    return PeerRecord(*(obj[key] for key in ("kind", "side", "peer_role", "transport")),
                      tuple(obj["received"]), tuple(obj["given"]), *(obj[key] for key in
                      ("peer_id", "peer_pin", "own_pin", "peer_address", "peer_dns_name", "peer_port", "publish")))


@_public
def read_peer_records(state_dir, *, load_record=None):
    """No-create iterator; optional reviewed canonical load_record is injected.

    This observes records only; it does not prove identity/pairlock/credential
    agreement and must never alone produce a committed-pairing OK verdict.
    """
    try:
        root = _directory(state_dir)
        peers = root / "peers"
        if not peers.exists() and not peers.is_symlink():
            return ()
        _directory(peers)
        result = []
        directories = _members(peers, 6)
        for directory in directories:
            if directory.name not in [kind + "-" + side for kind in ("relay", "forge", "gateway") for side in ("joined", "joiner")]:
                raise ValueError()
            _directory(directory)
            items = _members(directory, 256 - len(result))
            for item in items:
                if item.suffix != ".json":
                    raise ValueError()
                obj = _json(_read(item, 65536), "peer-invalid")
                row = _peer(obj)
                if item.stem != row.peer_id or directory.name != row.kind + "-" + row.side:
                    raise ValueError()
                if load_record is not None and load_record(item) != obj:
                    raise ValueError()
                result.append(row)
        return tuple(result)
    except ProfileError:
        raise
    except Exception:
        raise ProfileError("peer-invalid") from None


@dataclass(frozen=True)
class PublicationObservation:
    role: str
    phase: str
    desired: bool
    owned_routes: int


@_public
def parse_publication(raw, *, role, validator=None):
    """Observe a journal only. Live route matching remains its owner's check.

    An injected reviewed pure validator may additionally apply canonical route
    semantics. No recovery/resume is called and uncertain intent fails closed.
    """
    try:
        if type(raw) is not bytes or len(raw) > MAX_FILE:
            raise ValueError()
        obj = _json(raw, "publication-invalid")
        if type(obj) is not dict or obj.get("phase") not in ("publishing", "ready", "removing", "off"):
            raise ValueError()
        if role == "forge":
            if (set(obj) != {"schema", "install_id", "host", "routes", "added", "pending", "removing", "phase"} or
                    obj["schema"] != "deep32.forge-publication/1" or
                    re.fullmatch("[0-9a-f]{16}", obj["install_id"]) is None or
                    type(obj["routes"]) is not list or len(obj["routes"]) != 3 or
                    type(obj["added"]) is not list or len(set(obj["added"])) != len(obj["added"]) or
                    any(type(index) is not int or index not in range(3) for index in obj["added"])):
                raise ValueError()
            for index, (row, setting) in enumerate(zip(obj["routes"], ("embeddings_https", "reranker_https", "pairing_tcp"))):
                if (type(row) is not dict or set(row) != {"setting", "port", "kind", "target"} or row["setting"] != setting or
                        type(row["port"]) is not int or not 1 <= row["port"] <= 65535 or
                        row["kind"] != ("tcp" if index == 2 else "https") or
                        re.fullmatch(("tcp" if index == 2 else "http") + r"://127\.0\.0\.1:([1-9][0-9]{0,4})", row["target"]) is None or
                        not 1 <= int(row["target"].rsplit(":", 1)[1]) <= 65535):
                    raise ValueError()
            if len({row["port"] for row in obj["routes"]}) != 3 or len({row["target"].rsplit(":", 1)[1] for row in obj["routes"]}) != 3:
                raise ValueError()
            if obj["pending"] is not None or obj["removing"] is not None:
                raise ProfileError("publication-uncertain")
            desired, count = obj["phase"] not in ("off", "removing"), len(obj["added"])
        elif role == "workstation":
            if (obj.get("format") != "deep32-publication" or type(obj.get("version")) is not int or obj["version"] != 1 or
                    type(obj.get("desired")) is not bool or obj.get("previous_front") not in ("on", "off")):
                raise ValueError()
            desired = obj["desired"]
            count = 0  # Journal presence never invents live route ownership.
        else:
            raise ValueError()
        if type(obj.get("host")) is not str or re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.ts\.net", obj["host"]) is None or len(obj["host"]) > 253:
            raise ValueError()
        if obj["phase"] in ("publishing", "removing"):
            raise ProfileError("publication-uncertain")
        if validator is not None:
            validator(obj)
        return PublicationObservation(role, obj["phase"], desired, count)
    except ProfileError:
        raise
    except Exception:
        raise ProfileError("publication-invalid") from None
