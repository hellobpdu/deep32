"""Fail-closed installed-release verification and frozen receipt reader.

The owning adapter supplies an independently qualified verifier capability.
No GnuPG keyring discovery, temporary directory, cryptography implementation,
installer, shell verifier, or network fallback is present here.
"""

from dataclasses import dataclass, field
import hashlib
import re
from types import MappingProxyType

from .errors import DiagError, bounded
from .runner import Budget, CommandOutput, CommandRequest, strict_json

PIN = 'B205B406548627AABF5250D8A078C5F25D025DD6'
RELEASE = '0.1.0-alpha.3'
SHA = re.compile(r'[0-9a-f]{64}\Z')
ALIAS = re.compile(r'[a-z][a-z0-9-]{0,63}\Z')
PAIRS = frozenset({('workstation', 'openbsd'), ('forge', 'linux'), ('forge', 'windows'),
                   ('headend', 'linux'), ('headend', 'windows'), ('headend', 'macos')})
_VERIFIED = object()


def _keys(value, required):
    if type(value) is not dict or set(value) != set(required):
        raise DiagError('invalid-receipt')


@bounded('bad-signature')
def signature_status(output, pin=PIN):
    """Existing verifier's pinned-primary/binary/exactly-one-signature policy."""
    if (type(output) is not CommandOutput or type(output.returncode) is not int or output.returncode != 0
            or not isinstance(output.stdout, bytes) or len(output.stdout) > 65536):
        raise DiagError('bad-signature')
    try:
        lines = output.stdout.decode('ascii').splitlines()
        records = [line[9:].split() for line in lines if line.startswith('[GNUPG:] ')]
        good = [row for row in records if row and row[0] == 'GOODSIG']
        valid = [row for row in records if row and row[0] == 'VALIDSIG']
        bad = {'BADSIG', 'ERRSIG', 'EXPSIG', 'EXPKEYSIG', 'REVKEYSIG', 'NO_PUBKEY',
               'FAILURE', 'ERROR', 'NODATA', 'KEYEXPIRED', 'SIGEXPIRED'}
        if len(good) != 1 or len(valid) != 1 or any(row and row[0] in bad for row in records):
            raise ValueError()
        row = valid[0]
        if len(row) != 11 or row[9] != '00' or row[10].upper() != pin or not re.fullmatch(r'[0-9A-Fa-f]{40}', row[1]):
            raise ValueError()
    except (UnicodeError, ValueError, IndexError):
        raise DiagError('bad-signature') from None
    return pin


@dataclass(frozen=True, repr=False)
class VerifierQualification:
    """Compiled adapter input, never loaded from a diagnostic receipt.

    The release owner supplies the measured binary/keyring hashes and exact
    pre-existing read-only home after denied-write/native process-tree testing.
    Constructing this value in a fake proves the contract, not native approval.
    """
    executable: str = field(repr=False)
    home: str = field(repr=False)
    binary_sha256: str
    keyring_sha256: str
    qualification_id: str
    no_persistent_writes: bool = False
    bounded_process_tree: bool = False

    def __repr__(self):
        return 'VerifierQualification(<installed capability>)'


@dataclass(frozen=True, repr=False)
class VerifiedRelease:
    manifest: bytes = field(repr=False)
    manifest_sha256: str
    fingerprint: str
    qualification_id: str
    _seal: object = field(default=None, init=False, repr=False, compare=False)

    def __repr__(self):
        return 'VerifiedRelease(<authenticated public metadata>)'


class ReleaseVerifier:
    """Verify fixed installed files via direct gpgv, with in-memory status only.

    `paths` and `files` must refer to the same sealed installer-owned exact
    slots. The qualified observer checks sealed path/handle identity and provides
    the OS protection against ABA/replacement while its gpgv child is reading.
    The core also rechecks snapshots, refusing an ordinary concurrent change.
    """
    @bounded('unqualified-verifier')
    def __init__(self, files, paths, *, qualification=None, observer=None, budget=None):
        self.__files, self.__paths = files, dict(paths)
        self.__qualification, self.__observer = qualification, observer
        self.__budget = budget or Budget()

    @bounded('untrusted-release')
    def verify(self):
        from pathlib import Path
        q = self.__qualification
        if (type(q) is not VerifierQualification or q.no_persistent_writes is not True
                or q.bounded_process_tree is not True or self.__observer is None
                or any(type(x) is not str for x in (q.executable, q.home, q.binary_sha256, q.keyring_sha256, q.qualification_id))
                or not SHA.fullmatch(q.binary_sha256) or not SHA.fullmatch(q.keyring_sha256)
                or not ALIAS.fullmatch(q.qualification_id)
                or not Path(q.executable).is_absolute() or not Path(q.home).is_absolute()
                or set(self.__paths) != {'manifest', 'signature', 'keyring', 'verifier'}
                or any(not isinstance(p, (str, Path)) or not Path(p).is_absolute() for p in self.__paths.values())
                or str(self.__paths['verifier']) != q.executable
                or not self.__files.matches_slots(self.__paths)):
            raise DiagError('unqualified-verifier')
        snapshots = {slot: self.__files.read_existing_regular(slot, maximum=4 * 1024 * 1024)
                     for slot in ('manifest', 'signature', 'keyring', 'verifier')}
        if (snapshots['verifier'].digest != q.binary_sha256
                or snapshots['keyring'].digest != q.keyring_sha256):
            raise DiagError('unqualified-verifier')
        request = CommandRequest((q.executable, '--homedir', q.home, '--keyring',
                                  str(self.__paths['keyring']), '--status-fd', '1',
                                  str(self.__paths['signature']), str(self.__paths['manifest'])),
                                 self.__budget.deadline(), 65536)
        failure = None
        try:
            result = self.__budget.observe(self.__observer, request)
        except DiagError as error:
            failure = 'deadline' if error.code == 'deadline' else 'bad-signature'
        if failure:
            raise DiagError(failure)
        if self.__budget.clock() > request.deadline:
            raise DiagError('deadline')
        signature_status(result)
        if not all(self.__files.unchanged(slot, snap) for slot, snap in snapshots.items()):
            raise DiagError('snapshot-changed')
        data = snapshots['manifest'].data
        manifest = strict_json(data, 4 * 1024 * 1024)
        if (type(manifest) is not dict or type(manifest.get('signer')) is not dict
                or manifest['signer'] != {'type': 'openpgp', 'fingerprint': PIN}
                or manifest.get('format') != 'deep32-release-manifest'
                or type(manifest.get('format_version')) is not int or manifest['format_version'] != 1
                or type(manifest.get('release')) is not dict
                or manifest['release'].get('version') != RELEASE):
            raise DiagError('manifest-mismatch')
        release = VerifiedRelease(data, hashlib.sha256(data).hexdigest(), PIN, q.qualification_id)
        object.__setattr__(release, '_seal', _VERIFIED)
        return release


@dataclass(frozen=True, repr=False)
class InstallReceipt:
    role: str
    platform: str
    release_version: str
    manifest_sha256: str
    inventory: object = field(repr=False)
    generation: str | None = None
    fingerprint: str | None = None
    root_sha256: str | None = None
    mirrors_sha256: str | None = None
    capability_manifest_sha256: str | None = None
    content_manifest_sha256: str | None = None
    ntp_service: str | None = None

    def __repr__(self):
        return 'InstallReceipt(<public validated metadata>)'


@bounded('invalid-receipt')
def _validate_projected_receipt(raw, release, authoritative_inventory, *, role, platform, authoritative_content=None):
    """Private J05 projection used by the synthetic policy qualification tests.

    The inventory must independently come from authenticated pins and existing
    validated journals. An unsigned receipt never grants ownership. No paths,
    URLs, credential values, token hashes, or arbitrary extra keys are accepted.
    """
    value = strict_json(raw)
    _keys(value, ('schema', 'role', 'platform', 'release', 'inventory', 'content'))
    if (value['schema'] != 'deep32.doctor-install.v1' or (role, platform) not in PAIRS
            or value['role'] != role or value['platform'] != platform):
        raise DiagError('invalid-receipt')
    identity = value['release']
    _keys(identity, ('version', 'manifest_sha256', 'signer_fingerprint'))
    if (type(release) is not VerifiedRelease or release._seal is not _VERIFIED or identity['version'] != RELEASE
            or identity['manifest_sha256'] != release.manifest_sha256
            or identity['signer_fingerprint'] != PIN or release.fingerprint != PIN):
        raise DiagError('untrusted-release')
    inventory = value['inventory']
    _keys(inventory, ('services', 'models', 'ports', 'routes', 'filesystems', 'credential_slots'))
    for family, rows in inventory.items():
        if type(rows) is not list or len(rows) > 64:
            raise DiagError('invalid-receipt')
        aliases = set()
        for row in rows:
            if type(row) is not dict or type(row.get('alias')) is not str or not ALIAS.fullmatch(row['alias']) or row['alias'] in aliases:
                raise DiagError('invalid-receipt')
            aliases.add(row['alias'])
            if family == 'services':
                _keys(row, ('alias', 'image', 'required', 'active_job'))
                if not isinstance(row['image'], str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', row['image']) or any(type(row[x]) is not bool for x in ('required', 'active_job')):
                    raise DiagError('invalid-receipt')
            elif family == 'models':
                _keys(row, ('alias', 'id', 'revision'))
                if (not isinstance(row['id'], str) or not re.fullmatch(r'[A-Za-z0-9._-]+/[A-Za-z0-9._-]+', row['id'])
                        or len(row['id']) > 128 or any(part in ('.', '..') for part in row['id'].split('/'))
                        or not isinstance(row['revision'], str) or not re.fullmatch(r'[0-9a-f]{40}', row['revision'])):
                    raise DiagError('invalid-receipt')
            elif family == 'ports':
                _keys(row, ('alias', 'port'))
                if type(row['port']) is not int or not 1 <= row['port'] <= 65535:
                    raise DiagError('invalid-receipt')
            elif family == 'routes':
                _keys(row, ('alias', 'port', 'route'))
                if type(row['port']) is not int or not 1 <= row['port'] <= 65535 or row['route'] not in ('/mcp', '/info', '/embed', '/rerank', '/pair'):
                    raise DiagError('invalid-receipt')
            elif family == 'filesystems':
                _keys(row, ('alias', 'required_bytes', 'safe_running_bytes'))
                if any(type(row[x]) is not int or row[x] < 0 for x in ('required_bytes', 'safe_running_bytes')) or row['required_bytes'] < row['safe_running_bytes']:
                    raise DiagError('invalid-receipt')
            else:
                _keys(row, ('alias', 'slot'))
                if row['slot'] not in ('headend-bearer', 'forge-bearer', 'local-status'):
                    raise DiagError('invalid-receipt')
    # Compare canonical JSON to avoid bool/int equality and dictionary-order traps.
    import json
    if json.dumps(inventory, sort_keys=True, separators=(',', ':')) != json.dumps(authoritative_inventory, sort_keys=True, separators=(',', ':')):
        raise DiagError('inventory-mismatch')
    generation = fingerprint = None
    content = value['content']
    if json.dumps(content, sort_keys=True, separators=(',', ':')) != json.dumps(authoritative_content, sort_keys=True, separators=(',', ':')):
        raise DiagError('inventory-mismatch')
    if content is not None:
        _keys(content, ('generation', 'fingerprint'))
        generation, fingerprint = content['generation'], content['fingerprint']
        if (not isinstance(generation, str) or not re.fullmatch(r'[A-Za-z0-9._+-]{1,128}', generation)
                or not isinstance(fingerprint, str) or not SHA.fullmatch(fingerprint)):
            raise DiagError('invalid-receipt')
    # Deep immutable copy: later probe consumers cannot alter the receipt snapshot.
    immutable = MappingProxyType({family: tuple(MappingProxyType(dict(row)) for row in rows)
                                  for family, rows in inventory.items()})
    return InstallReceipt(role, platform, RELEASE, release.manifest_sha256, immutable, generation, fingerprint)


_FAMILIES = ('services', 'models', 'ports', 'routes', 'filesystems', 'credential_slots')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z')


def _identifier(value):
    return type(value) is str and _ID.fullmatch(value) is not None


def _digest(value):
    return type(value) is str and SHA.fullmatch(value) is not None


def _port(value):
    return type(value) is int and 1 <= value <= 65535


def _same(left, right):
    import json
    return json.dumps(left, sort_keys=True, separators=(',', ':')) == json.dumps(right, sort_keys=True, separators=(',', ':'))


def _frozen_inventory(value):
    """Validate the closed T-0203 vocabulary before projecting probe aliases."""
    inventory = {name: value[name] for name in _FAMILIES}
    for family, rows in inventory.items():
        limit = 16 if family in ('models', 'filesystems', 'credential_slots') else 32
        if type(rows) is not list or len(rows) > limit:
            raise DiagError('invalid-receipt')
        identities = set()
        for row in rows:
            identity_key = 'role' if family == 'models' else 'id'
            if type(row) is not dict or not _identifier(row.get(identity_key)) or row[identity_key] in identities:
                raise DiagError('invalid-receipt')
            identities.add(row[identity_key])
            if family == 'services':
                _keys(row, ('id', 'image_id', 'required', 'mode'))
                good = (type(row['image_id']) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', row['image_id'])
                        and type(row['required']) is bool and row['mode'] in ('running', 'on-demand'))
            elif family == 'models':
                _keys(row, ('role', 'model', 'revision', 'files_manifest_sha256'))
                good = (row['role'] in ('embedding', 'reranker', 'mineru-small', 'mineru-vlm')
                        and type(row['model']) is str and len(row['model']) <= 256
                        and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', row['model'])
                        and type(row['revision']) is str and re.fullmatch(r'[0-9a-f]{40}', row['revision'])
                        and _digest(row['files_manifest_sha256']))
            elif family == 'ports':
                _keys(row, ('id', 'port', 'service', 'owner'))
                good = _port(row['port']) and _identifier(row['service']) and _identifier(row['owner'])
            elif family == 'routes':
                _keys(row, ('id', 'peer_role', 'port', 'owner'))
                good = (_port(row['port']) and row['peer_role'] in ('workstation', 'forge', 'headend')
                        and _identifier(row['owner']))
            elif family == 'filesystems':
                _keys(row, ('id', 'safe_running_bytes', 'next_operation_bytes'))
                good = (all(type(row[name]) is int and row[name] >= 0
                            for name in ('safe_running_bytes', 'next_operation_bytes'))
                        and row['safe_running_bytes'] <= row['next_operation_bytes'])
            else:
                _keys(row, ('id', 'kind'))
                good = row['kind'] in ('posix-private', 'dpapi', 'acl-private')
            if not good:
                raise DiagError('invalid-receipt')
    services = {row['id'] for row in inventory['services']}
    if any(row['service'] not in services for row in inventory['ports']):
        raise DiagError('invalid-receipt')
    return inventory


@bounded('invalid-receipt')
def validate_receipt(raw, release, authoritative_inventory, *, role, platform,
                     authoritative_content=None, authoritative_binding=None):
    """Read the frozen shared receipt; independent owner inputs grant trust.

    The owning adapter supplies authenticated flat inventory, content and the
    exact trust/capability/NTP binding. Receipt claims cannot supply those inputs.
    Source execution still has no installed evidence loader. On-demand mode is
    retained, but does not manufacture authenticated active-job evidence.
    """
    value = strict_json(raw)
    _keys(value, ('schema', 'release', 'role', 'platform', 'trust',
                  'capability_manifest_sha256', *_FAMILIES, 'content', 'ntp_service'))
    if (value['schema'] != 'deep32.doctor-install.v1' or (role, platform) not in PAIRS
            or value['role'] != role or value['platform'] != platform):
        raise DiagError('invalid-receipt')
    identity = value['trust']
    _keys(identity, ('root_sha256', 'mirrors_sha256', 'signer_fingerprint'))
    if (type(release) is not VerifiedRelease or release._seal is not _VERIFIED
            or value['release'] != 'v' + RELEASE or release.fingerprint != PIN
            or identity['signer_fingerprint'] != PIN):
        raise DiagError('untrusted-release')
    if not all(_digest(identity[key]) for key in ('root_sha256', 'mirrors_sha256')):
        raise DiagError('invalid-receipt')
    capability = value['capability_manifest_sha256']
    if ((capability is not None and not _digest(capability))
            or (role == 'forge' and capability is None)
            or (value['ntp_service'] is not None and not _identifier(value['ntp_service']))):
        raise DiagError('invalid-receipt')
    inventory = _frozen_inventory(value)
    _keys(authoritative_inventory, _FAMILIES)
    if not _same(inventory, authoritative_inventory):
        raise DiagError('inventory-mismatch')
    binding = {key: value[key] for key in ('trust', 'capability_manifest_sha256', 'ntp_service')}
    if not _same(binding, authoritative_binding):
        raise DiagError('untrusted-release')
    content = value['content']
    generation = fingerprint = None
    if content is not None:
        _keys(content, ('generation', 'corpus_fingerprint', 'manifest_sha256'))
        if (role != 'workstation' or not _identifier(content['generation'])
                or not _digest(content['corpus_fingerprint']) or not _digest(content['manifest_sha256'])):
            raise DiagError('invalid-receipt')
        generation, fingerprint = content['generation'], content['corpus_fingerprint']
    if not _same(content, authoritative_content):
        raise DiagError('inventory-mismatch')
    projected = {
        'services': [dict(alias=row['id'], image=row['image_id'], required=row['required'],
                          active_job=False, mode=row['mode']) for row in inventory['services']],
        'models': [dict(alias='embeddings' if row['role'] == 'embedding' else row['role'],
                        id=row['model'], revision=row['revision'],
                        files_manifest_sha256=row['files_manifest_sha256']) for row in inventory['models']],
        'ports': [dict(row, alias=row['id']) for row in inventory['ports']],
        'routes': [dict(row, alias=row['id']) for row in inventory['routes']],
        'filesystems': [dict(alias=row['id'], required_bytes=row['next_operation_bytes'],
                             safe_running_bytes=row['safe_running_bytes']) for row in inventory['filesystems']],
        'credential_slots': [dict(row, alias=row['id']) for row in inventory['credential_slots']],
    }
    immutable = MappingProxyType({family: tuple(MappingProxyType(row) for row in rows)
                                  for family, rows in projected.items()})
    return InstallReceipt(role, platform, RELEASE, release.manifest_sha256, immutable, generation, fingerprint,
                          root_sha256=identity['root_sha256'], mirrors_sha256=identity['mirrors_sha256'],
                          capability_manifest_sha256=capability,
                          content_manifest_sha256=None if content is None else content['manifest_sha256'],
                          ntp_service=value['ntp_service'])
