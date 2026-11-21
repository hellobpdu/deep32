"""The thirteen-type dialect implemented by asklink/src/frame.c."""
from dataclasses import dataclass
from enum import IntEnum
import struct

HEADER = struct.Struct("!IIBBH")
HEADER_SIZE = 12
MAX_PAYLOAD = 1048576
CONTROL_MAX = 16384
WINDOW = 262144
FRAGMENT = 65536
REQUEST_MAX = 65536
RESPONSE_MAX = 1048576
END_MESSAGE = 1


class Type(IntEnum):
    HELLO = 1
    HELLO_ACK = 2
    OPEN = 3
    OPEN_ACK = 4
    DATA = 5
    WINDOW_UPDATE = 6
    CLOSE = 7
    RESET = 8
    PING = 9
    PONG = 10
    ERROR = 11
    PAIR_CONFIRM = 12
    PAIR_COMMIT = 13


class Error(IntEnum):
    PROTOCOL = 1
    AUTH = 2
    CHANNEL_FORBIDDEN = 3
    FLOW_CONTROL = 4
    LIMIT = 5
    TIMEOUT = 6
    SESSION = 7
    SETTINGS_UNVERIFIED = 8
    CODEX_UNAVAILABLE = 9
    CANCELLED = 10
    RELAY_LOST = 11


# Familiar source names are provided without changing the wire enum.
T_HELLO, T_HELLO_ACK, T_OPEN, T_OPEN_ACK, T_DATA = tuple(Type)[:5]
T_WINDOW, T_CLOSE, T_RESET, T_PING, T_PONG, T_ERROR, T_PAIR_CONFIRM, T_PAIR_COMMIT = tuple(Type)[5:]
F_END_MESSAGE = END_MESSAGE


class ProtocolError(ValueError):
    def __init__(self, code=Error.PROTOCOL):
        self.code = Error(code)
        super().__init__(self.code.name)


CONTROL = frozenset((1, 2, 9, 10, 11, 12, 13))
EMPTY = frozenset((4, 7, 12, 13))
U32 = frozenset((6, 8, 11))


def check_header(length, stream, kind, channel, flags):
    # Preserve the C check order, including LIMIT before unknown type.
    if not 0 <= length <= MAX_PAYLOAD:
        raise ProtocolError(Error.LIMIT)
    if not 1 <= kind <= 13:
        raise ProtocolError()
    if (kind == Type.DATA and flags & ~END_MESSAGE) or (kind != Type.DATA and flags):
        raise ProtocolError()
    if not 0 <= stream <= 0xffffffff or not 0 <= channel <= 255 or not 0 <= flags <= 65535:
        raise ProtocolError()
    if (kind in CONTROL and (stream or channel)) or (kind not in CONTROL and (not stream or not channel)):
        raise ProtocolError()
    if kind in (1, 2, 3) and length > CONTROL_MAX:
        raise ProtocolError(Error.LIMIT)
    if ((kind in EMPTY and length != 0) or (kind in U32 and length != 4)
            or (kind in (9, 10) and length != 8) or (kind == 5 and length == 0)):
        raise ProtocolError()


@dataclass(frozen=True)
class Frame:
    type: int
    stream: int = 0
    channel: int = 0
    payload: bytes = b""
    flags: int = 0

    def encode(self):
        return encode_frame(self)


def encode_frame(frame):
    if not isinstance(frame.payload, bytes):
        raise ProtocolError()
    check_header(len(frame.payload), frame.stream, frame.type, frame.channel, frame.flags)
    return HEADER.pack(len(frame.payload), frame.stream, frame.type, frame.channel, frame.flags) + frame.payload


def decode_frame(data):
    if len(data) < HEADER_SIZE:
        raise ProtocolError()
    length, stream, kind, channel, flags = HEADER.unpack_from(data)
    check_header(length, stream, kind, channel, flags)
    if len(data) != HEADER_SIZE + length:
        raise ProtocolError()
    return Frame(kind, stream, channel, bytes(data[HEADER_SIZE:]), flags)


class FrameDecoder:
    """Incremental decoder; a header is checked before buffering its payload."""
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        frames = []
        view = memoryview(data)
        offset = 0
        while offset < len(view):
            need = HEADER_SIZE - len(self.buffer)
            if need > 0:
                count = min(need, len(view) - offset)
                self.buffer.extend(view[offset:offset + count])
                offset += count
                if len(self.buffer) < HEADER_SIZE:
                    break
            length, stream, kind, channel, flags = HEADER.unpack_from(self.buffer)
            check_header(length, stream, kind, channel, flags)
            need = HEADER_SIZE + length - len(self.buffer)
            count = min(need, len(view) - offset)
            self.buffer.extend(view[offset:offset + count])
            offset += count
            if len(self.buffer) == HEADER_SIZE + length:
                frames.append(Frame(kind, stream, channel, bytes(self.buffer[HEADER_SIZE:]), flags))
                self.buffer.clear()
        return frames


def u32(value):
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xffffffff:
        raise ProtocolError()
    return struct.pack("!I", value)


def read_u32(payload):
    if len(payload) != 4:
        raise ProtocolError()
    return struct.unpack("!I", payload)[0]
