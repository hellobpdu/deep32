"""Constant failures: external messages, paths and credentials are never echoed."""

from functools import wraps

CODES = frozenset({
    'unavailable', 'unsafe-file', 'missing-file', 'changed-file', 'oversized',
    'invalid-json', 'invalid-receipt', 'untrusted-release', 'unqualified-verifier',
    'bad-signature', 'manifest-mismatch', 'inventory-mismatch', 'unsafe-command',
    'unqualified-command', 'command-failed', 'deadline', 'invalid-report',
    'invalid-observation', 'unsafe-target', 'snapshot-changed',
})


class DiagError(Exception):
    def __init__(self, code):
        self.code = code if type(code) is str and code in CODES else 'unavailable'
        super().__init__(self.code)

    def __repr__(self):
        return 'DiagError(' + repr(self.code) + ')'


def bounded(default='unavailable'):
    """Drop exception objects and context at each external-data boundary."""
    def decorate(function):
        @wraps(function)
        def guarded(*args, **kwargs):
            code = default
            try:
                return function(*args, **kwargs)
            except DiagError as error:
                code = error.code
            except Exception:
                pass
            raise DiagError(code)
        return guarded
    return decorate
