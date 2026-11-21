"""Fixed local reason classes. Peer text is never used as a reason."""

class NetError(Exception):
    def __init__(self, reason='internal', exit_code=1, **fields):
        self.reason, self.exit_code, self.fields = reason, exit_code, fields
        super().__init__(reason)

class StateError(NetError):
    def __init__(self, reason='storage'):
        super().__init__(reason, 6)

class Exists(NetError):
    def __init__(self):
        super().__init__('exists', 7)

class Locked(NetError):
    def __init__(self, remaining):
        super().__init__('locked', 3, locked_seconds=remaining)

class ProtocolError(Exception):
    def __init__(self, code=1):
        self.code = code
        super().__init__('protocol')

class LimitError(ProtocolError):
    def __init__(self):
        super().__init__(5)

class ConnectionLost(Exception):
    pass
