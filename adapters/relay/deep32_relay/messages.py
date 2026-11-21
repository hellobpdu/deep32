"""Strict JSON and schemas for the shipped research-v1 peer."""
import json
import re
from .wire import ProtocolError, Error, REQUEST_MAX, RESPONSE_MAX

TOOLS = ("rag_status", "search_rag", "search_many", "get_section", "get_outline",
         "lookup_id", "lookup_rfc", "find_command", "grep_corpus", "list_documents", "verify_quote")
STATUS = ("OFF", "LISTENING", "PAIRING", "CONNECTED", "DISCONNECTED", "CODEX_READY",
          "CODEX_SIGNED_OUT", "BUSY", "IDLE", "ERROR")
ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z", re.ASCII)
HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class _Number:
    """C J_NUM retains its lexeme, including numbers beyond machine ranges."""
    def __init__(self, lexeme):
        self.lexeme = lexeme


def _parse_integer(lexeme):
    # Avoid Python's large-integer digit conversion limit for legal J_NUM.
    if len(lexeme.lstrip("-")) <= 19:
        value = int(lexeme)
        if -(2**63) <= value < 2**63:
            return value
    return _Number(lexeme)


def identifier(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError()
        result[key] = value
    return result


def _tree(value, depth=0):
    if depth > 32:
        raise ProtocolError()
    if isinstance(value, str):
        # Python's decoder retains unpaired surrogate escapes; C refuses them.
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ProtocolError() from None
    elif isinstance(value, dict):
        for k, v in value.items():
            _tree(k, depth + 1)
            _tree(v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            _tree(v, depth + 1)


def loads(payload, limit=RESPONSE_MAX):
    if not isinstance(payload, (bytes, bytearray)) or len(payload) > limit:
        raise ProtocolError(Error.LIMIT)
    try:
        text = bytes(payload).decode("utf-8")
        if text.startswith("\ufeff"):
            raise ProtocolError()
        value = json.loads(text, object_pairs_hook=_object,
                           parse_int=_parse_integer, parse_float=_Number,
                           parse_constant=lambda _: (_ for _ in ()).throw(ProtocolError()))
        _tree(value)
        return value
    except (UnicodeError, ValueError, RecursionError):
        raise ProtocolError() from None


def dumps(value, limit=RESPONSE_MAX):
    try:
        _tree(value)
        payload = _encode(value).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ProtocolError() from None
    if len(payload) > limit:
        raise ProtocolError(Error.LIMIT)
    return payload


def _encode(value):
    if isinstance(value, _Number):
        # These tokens come only from the strict JSON decoder.
        if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", value.lexeme, re.ASCII):
            raise ProtocolError()
        return value.lexeme
    if type(value) is dict:
        if any(not isinstance(k, str) for k in value):
            raise ProtocolError()
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + _encode(v) for k, v in value.items()) + "}"
    if type(value) is list:
        return "[" + ",".join(_encode(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def keys(value, required, optional=()):
    if type(value) is not dict or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ProtocolError()


def integer(value, low=0, high=0x7fffffffffffffff):
    return type(value) is int and low <= value <= high


def text(value, maximum, nonempty=False, nul=True):
    if not isinstance(value, str) or (nonempty and not value) or (not nul and "\0" in value):
        return False
    try:
        return len(value.encode("utf-8")) <= maximum
    except UnicodeError:
        return False


def token(value, maximum):
    return isinstance(value, str) and 1 <= len(value) <= maximum and all(33 <= ord(c) <= 126 for c in value)


def validate_hello(value, role="relay"):
    keys(value, ("proto", "id", "version", "versions", "role", "features", "nonce", "sessions"),
         ("codex_version", "signed_in", "profiles"))
    if (value["proto"] != "D32U/1" or not identifier(value["id"])
            or not integer(value["version"], 1, 1) or type(value["versions"]) is not list
            or len(value["versions"]) != 1 or not integer(value["versions"][0], 1, 1)
            or value["role"] not in (("relay", "hub") if role == "relay" else ("node",))
            or value["features"] != ["research-v1"] or type(value["features"]) is not list
            or not isinstance(value["nonce"], str) or not HEX.fullmatch(value["nonce"])
            or value["sessions"] != [] or type(value["sessions"]) is not list):
        raise ProtocolError()
    if "codex_version" in value and value["codex_version"] != "0.160.0":
        raise ProtocolError()
    if "signed_in" in value and type(value["signed_in"]) is not bool:
        raise ProtocolError()
    if "profiles" in value:
        profiles = value["profiles"]
        if type(profiles) is not list or len(profiles) > 1:
            raise ProtocolError()
        for profile in profiles:
            keys(profile, ("name", "models", "efforts"))
            if profile["name"] != "research":
                raise ProtocolError()
            for name in ("models", "efforts"):
                if type(profile[name]) is not list or any(not text(v, 64, True, False) for v in profile[name]):
                    raise ProtocolError()
    return value


def hello(local_id, nonce, profiles=()):
    value = {"proto": "D32U/1", "id": local_id, "version": 1, "versions": [1], "role": "relay",
             "features": ["research-v1"], "nonce": nonce.hex(), "sessions": []}
    if profiles:
        value["profiles"] = list(profiles)
    return validate_hello(value)


def validate_ack(value):
    keys(value, ("version", "features", "paired"))
    if not integer(value["version"], 1, 1) or value["features"] != ["research-v1"] or type(value["paired"]) is not bool:
        raise ProtocolError()
    return value


def validate_open(value, channel):
    keys(value, () if channel == 4 else ("session",))
    if channel != 4 and not identifier(value["session"]):
        raise ProtocolError()
    return value


def validate_request(value):
    if type(value) is not dict:
        raise ProtocolError()
    op = value.get("op")
    if op == "session.open":
        keys(value, ("op", "profile", "kind", "skill", "model", "effort", "caller_class"))
        if (value["profile"] != "research" or value["kind"] != "research" or value["skill"] != "deep32"
                or value["caller_class"] not in ("research", "research-relay")
                or not text(value["model"], 64, True, False) or not text(value["effort"], 64, True, False)):
            raise ProtocolError()
    elif op == "turn.start":
        keys(value, ("op", "text"))
        if not text(value["text"], REQUEST_MAX, True, False):
            raise ProtocolError()
    elif op in ("turn.interrupt", "session.close"):
        keys(value, ("op",))
    else:
        raise ProtocolError()
    dumps(value, REQUEST_MAX)
    return value


def validate_event(value, session=None):
    if type(value) is not dict or not identifier(value.get("session")) or (session is not None and value["session"] != session):
        raise ProtocolError()
    op = value.get("op")
    if op == "session.opened":
        keys(value, ("op", "session"))
    elif op in ("agent.delta", "agent.final"):
        keys(value, ("op", "session", "text"))
        if not text(value["text"], RESPONSE_MAX):
            raise ProtocolError()
    elif op == "turn.completed":
        keys(value, ("op", "session", "status", "usage"))
        keys(value["usage"], ("input_tokens", "output_tokens"))
        if value["status"] not in ("completed", "interrupted", "failed") or any(not integer(v) for v in value["usage"].values()):
            raise ProtocolError()
    elif op in ("error", "status"):
        keys(value, ("op", "session", "code"))
        if (op == "error" and not integer(value["code"], 1, 11)) or (op == "status" and value["code"] not in STATUS):
            raise ProtocolError()
    else:
        raise ProtocolError()
    dumps(value)
    return value


def validate_mcp_request(value):
    keys(value, ("body", "mcp_session_id"), ("protocol_version",))
    if value["body"] is not None and type(value["body"]) is not dict:
        raise ProtocolError()
    if value["mcp_session_id"] is not None and not token(value["mcp_session_id"], 128):
        raise ProtocolError()
    if "protocol_version" in value and not token(value["protocol_version"], 64):
        raise ProtocolError()
    body = value["body"]
    if body is not None:
        method = body.get("method")
        if (body.get("jsonrpc") != "2.0" or method not in ("initialize", "notifications/initialized", "tools/list", "tools/call")
                or ((method == "notifications/initialized") == ("id" in body))):
            raise ProtocolError(Error.CHANNEL_FORBIDDEN)
        if method == "tools/call" and (type(body.get("params")) is not dict or body["params"].get("name") not in TOOLS):
            raise ProtocolError(Error.CHANNEL_FORBIDDEN)
    dumps(value, REQUEST_MAX)
    return value


def validate_mcp_response(value):
    keys(value, ("body", "mcp_session_id", "http_status"))
    if value["body"] is not None and type(value["body"]) is not dict:
        raise ProtocolError()
    if value["mcp_session_id"] is not None and not token(value["mcp_session_id"], 128):
        raise ProtocolError()
    if not integer(value["http_status"], 100, 599):
        raise ProtocolError()
    return value
