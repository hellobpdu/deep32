"""Offline, exact-slot trust binding for the public installed selftest bundle.

Only an independently qualified read-only verifier may run.  Map entries select
no files: the installer adapter supplies all ten fixed paths before this module
is entered.  A signed map cannot add a reader or authorise filesystem writes.
"""

from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType

from .errors import DiagError
from .runner import CommandRequest, strict_json
from .trust import ALIAS, PIN, RELEASE, SHA, VerifierQualification, signature_status


_SEAL = object()
_SLOTS = frozenset(('manifest', 'signature', 'keyring', 'verifier', 'asset_map',
                    'questions', 'reference', 'questions_schema',
                    'reference_schema', 'result_schema'))
_ASSETS = {
    'questions': ('selftest-questions.json', 'selftest/questions.json'),
    'reference': ('selftest-reference.json', 'selftest/reference.json'),
    'questions_schema': ('selftest-questions.schema.json', 'selftest/questions.schema.json'),
    'reference_schema': ('selftest-reference.schema.json', 'selftest/reference.schema.json'),
    'result_schema': ('selftest-result.schema.json', 'selftest/result.schema.json'),
}
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}\Z')
# Exact authored schema bytes, compiled independently of the installed copies.
_SCHEMA_HASHES = {
    'questions_schema': '8a29e6aac60406b472f73223a07a2c6d89961c33021f5d2d4dc62d521487930e',
    'reference_schema': 'ba93efe5504fefc746d315100a52767d48d5daed4565e1bcac27fe5888fabdd6',
    'result_schema': 'ebe4c23deb57a4bec2ddbd48282066ae286f551f7424ac72b1207933fc52d493',
}
_LIMITS = {'manifest': 1024 * 1024, 'signature': 65536, 'keyring': 4 * 1024 * 1024,
           'verifier': 4 * 1024 * 1024, 'asset_map': 1024 * 1024,
           'questions': 1024 * 1024, 'reference': 65536,
           'questions_schema': 65536, 'reference_schema': 65536, 'result_schema': 65536}
_CODES = frozenset(('selftest-trust', 'unqualified-verifier', 'bad-signature',
                    'deadline', 'snapshot-changed', 'bundle-hash', 'bundle-schema',
                    'asset-map', 'fixture-only'))


class TrustError(ValueError):
    """Static public failure; never retain verifier, path or metadata messages."""
    def __init__(self, code='selftest-trust'):
        self.code = code if type(code) is str and code in _CODES else 'selftest-trust'
        super().__init__(self.code)


def _guard(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        code = 'selftest-trust'
        try:
            return function(*args, **kwargs)
        except TrustError as error:
            code = error.code
        except DiagError as error:
            code = error.code if error.code in _CODES else 'selftest-trust'
        except Exception:
            pass
        raise TrustError(code)
    return guarded


@dataclass(frozen=True, repr=False)
class TrustedBundle:
    bundle: object = field(repr=False)
    fixture_harness: bool
    manifest_sha256: str
    fingerprint: str
    _files: object = field(repr=False)
    _snapshots: object = field(repr=False)
    _seal: object = field(default=None, init=False, repr=False, compare=False)

    def __repr__(self):
        return 'TrustedBundle(<authenticated public metadata>)'

    @_guard
    def recheck(self):
        require_bundle(self, fixture_harness=self.fixture_harness)
        if not all(self._files.unchanged(slot, snap) for slot, snap in self._snapshots.items()):
            raise TrustError('snapshot-changed')


@_guard
def require_bundle(value, *, fixture_harness=False):
    """Return the contract bundle only through the unforgeable in-process seal."""
    if (type(value) is not TrustedBundle or value._seal is not _SEAL
            or type(fixture_harness) is not bool or type(value.fixture_harness) is not bool
            or value.fingerprint != PIN):
        raise TrustError()
    if value.bundle.reference['fixture_only'] is True and not (
            fixture_harness is True and value.fixture_harness is True):
        raise TrustError('fixture-only')
    return value.bundle


def _root(raw):
    # Share the release reader's strict flat-name, LF and duplicate policy.
    if (not raw or not raw.endswith(b'\n') or
            any(byte != 10 and not 32 <= byte <= 126 for byte in raw)):
        raise TrustError('bundle-hash')
    result, folded = {}, set()
    for line in raw.decode('ascii').split('\n')[:-1]:
        match = re.fullmatch(r'([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]{0,199})', line)
        if (match is None or '..' in match[2] or match[2].lower() in folded
                or match[2].lower() in ('sha256sums', 'sha256sums.sig')):
            raise TrustError('bundle-hash')
        folded.add(match[2].lower())
        result[match[2]] = match[1]
    if not 1 <= len(result) <= 1000:
        raise TrustError('bundle-hash')
    return result


def _keys(value, names):
    if type(value) is not dict or set(value) != set(names):
        raise TrustError('asset-map')


def _relative(value):
    if (type(value) is not str or len(value) > 240 or
            not re.fullmatch(r'(selftest|sbom)/[A-Za-z0-9][A-Za-z0-9._/-]*', value)
            or '\\' in value or any(part in ('', '.', '..') for part in value.split('/'))
            or str(PurePosixPath(value)) != value):
        raise TrustError('asset-map')
    return value


def _map(raw, sums):
    value = strict_json(raw)
    _keys(value, ('format', 'format_version', 'release', 'entries'))
    if (value['format'] != 'deep32-asset-map' or type(value['format_version']) is not int
            or value['format_version'] != 1 or value['release'] != 'v' + RELEASE
            or type(value['entries']) is not list or not 1 <= len(value['entries']) <= 1024):
        raise TrustError('asset-map')
    expected = {asset: path for asset, path in _ASSETS.values()}
    seen, destinations = set(), set()
    for entry in value['entries']:
        _keys(entry, ('asset', 'media_paths', 'install'))
        asset = entry['asset']
        if (type(asset) is not str or not _NAME.fullmatch(asset) or '..' in asset
                or asset.lower() in seen or asset not in sums):
            raise TrustError('asset-map')
        seen.add(asset.lower())
        for family, identity, choices in (('media_paths', 'disc', ('disc1', 'disc2', 'disc3')),
                                          ('install', 'role', ('workstation', 'forge', 'headend'))):
            rows = entry[family]
            if type(rows) is not list or len(rows) > 3:
                raise TrustError('asset-map')
            local = set()
            for row in rows:
                _keys(row, (identity, 'path'))
                path = _relative(row['path'])
                if type(row[identity]) is not str or row[identity] not in choices:
                    raise TrustError('asset-map')
                key = (family, row[identity], path.lower())
                if key in local or key in destinations:
                    raise TrustError('asset-map')
                local.add(key)
                destinations.add(key)
                if (path.startswith('selftest/') and expected.get(asset) != path
                        or asset.startswith('selftest-') and expected.get(asset) != path):
                    raise TrustError('asset-map')
        if asset in expected:
            if (entry['media_paths'] != [{'disc': 'disc1', 'path': expected[asset]}]
                    or len(entry['install']) != 2
                    or {(r['role'], r['path']) for r in entry['install']} !=
                    {('workstation', expected[asset]), ('headend', expected[asset])}):
                raise TrustError('asset-map')
        elif asset.startswith('selftest-'):
            raise TrustError('asset-map')
    if not set(expected) <= seen:
        raise TrustError('asset-map')


@_guard
def authenticate_bundle(files, paths, *, qualification, observer, budget, fixture_harness=False):
    """Verify immutable installed bytes before any profile or credential read.

    `observer` is a qualified bounded direct-gpgv capability.  As in J05, its
    platform qualification owns sealed path/handle identity during verification;
    snapshot checks also detect ordinary concurrent replacement.
    """
    q = qualification
    if (type(fixture_harness) is not bool or type(paths) is not dict or set(paths) != _SLOTS
            or any(not isinstance(path, (str, Path)) or not Path(path).is_absolute()
                   for path in paths.values()) or not files.matches_slots(paths)
            or len({str(Path(path)).lower() for path in paths.values()}) != len(_SLOTS)
            or type(q) is not VerifierQualification or q.no_persistent_writes is not True
            or q.bounded_process_tree is not True or observer is None
            or any(type(x) is not str for x in (q.executable, q.home, q.binary_sha256,
                                               q.keyring_sha256, q.qualification_id))
            or not Path(q.executable).is_absolute() or not Path(q.home).is_absolute()
            or str(paths['verifier']) != q.executable or not SHA.fullmatch(q.binary_sha256)
            or not SHA.fullmatch(q.keyring_sha256) or not ALIAS.fullmatch(q.qualification_id)):
        raise TrustError('unqualified-verifier')
    # Authenticate the root before parsing any root-directed metadata, including
    # questions, labels, schemas and asset mappings.
    snapshots = {slot: files.read_existing_regular(slot, maximum=_LIMITS[slot])
                 for slot in ('manifest', 'signature', 'keyring', 'verifier')}
    if (snapshots['verifier'].digest != q.binary_sha256
            or snapshots['keyring'].digest != q.keyring_sha256):
        raise TrustError('unqualified-verifier')
    request = CommandRequest((q.executable, '--homedir', q.home, '--keyring', str(paths['keyring']),
                              '--status-fd', '1', str(paths['signature']), str(paths['manifest'])),
                             budget.deadline(), 65536)
    output = budget.observe(observer, request)
    if budget.clock() >= request.deadline:
        raise TrustError('deadline')
    signature_status(output)
    if not all(files.unchanged(slot, snap) for slot, snap in snapshots.items()):
        raise TrustError('snapshot-changed')
    sums = _root(snapshots['manifest'].data)
    for slot in ('asset_map', *_ASSETS):
        snapshot = files.read_existing_regular(slot, maximum=_LIMITS[slot])
        name = 'ASSET-MAP.json' if slot == 'asset_map' else _ASSETS[slot][0]
        if sums.get(name) != snapshot.digest:
            raise TrustError('bundle-hash')
        snapshots[slot] = snapshot
    for slot, expected in _SCHEMA_HASHES.items():
        if snapshots[slot].digest != expected:
            raise TrustError('bundle-schema')
    _map(snapshots['asset_map'].data, sums)
    from .selftest_contract import load_bundle
    bundle = load_bundle(snapshots['questions'].data, snapshots['reference'].data,
                         fixture_harness=fixture_harness)
    if bundle.reference['release'] != RELEASE:
        raise TrustError('asset-map')
    binding = TrustedBundle(bundle, fixture_harness, snapshots['manifest'].digest, PIN,
                            files, MappingProxyType(dict(snapshots)))
    object.__setattr__(binding, '_seal', _SEAL)
    binding.recheck()
    return binding
