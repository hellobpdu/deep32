"""Read measured Python distribution metadata without extracting or running it.

The build's existing lock and source-archive allowance remain authoritative.
This adapter records declared metadata, rather than resolving dependencies or
claiming that an archive describes an installed environment.
"""
from email import policy
from email.parser import BytesParser
import hashlib
import itertools
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
from urllib.parse import unquote, urlsplit
import zipfile

from .common import build_distributions, digest, public_path, public_text, public_url, regular


MAX_MEMBERS = 20000
MAX_MEMBER_BYTES = 2 * 1024 ** 3
MAX_TOTAL_BYTES = 8 * 1024 ** 3
MAX_METADATA_BYTES = 2 * 1024 ** 2
MAX_LICENCE_BYTES = 16 * 1024 ** 2
USAGES = frozenset({'runtime', 'build-only', 'bundled', 'source'})
_TAG = re.compile(r'[A-Za-z0-9_.]+-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+\Z')
_NAME = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?\Z')
_VERSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9.+_!-]*\Z')
_DEPENDENCY_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9._-]+(?:\s*,\s*[A-Za-z0-9._-]+)*\])?\Z')
_MARKER_TOKEN = re.compile(r'''\s*(?:"[A-Za-z0-9_.,+ -]*"|'[A-Za-z0-9_.,+ -]*'|===|==|!=|<=|>=|~=|<|>|[A-Za-z_][A-Za-z0-9_]*|[()])''')
_MARKER_NAMES = frozenset({'python_version', 'python_full_version', 'os_name', 'sys_platform',
                           'platform_release', 'platform_system', 'platform_version', 'platform_machine',
                           'platform_python_implementation', 'implementation_name', 'implementation_version', 'extra'})


def _member_name(name):
    """Validate the original name, before pathlib can normalise traversal away."""
    if (not isinstance(name, str) or not name or '\\' in name or '\x00' in name
            or any(ord(char) < 32 for char in name) or ':' in name):
        raise ValueError('unsafe distribution archive member name')
    value = name[:-1] if name.endswith('/') else name
    if not value or value.startswith('/') or any(part in ('', '.', '..') for part in value.split('/')):
        raise ValueError('unsafe distribution archive member name')
    return value


def _members(entries):
    """Reject ambiguous names, file/directory conflicts and excessive archives."""
    found = {}
    total = 0
    for name, size, directory in entries:
        name = _member_name(name)
        if name in found:
            raise ValueError('duplicate distribution archive member: ' + name)
        if not isinstance(size, int) or size < 0 or size > MAX_MEMBER_BYTES:
            raise ValueError('distribution archive member exceeds size limit')
        total += size
        if len(found) >= MAX_MEMBERS or total > MAX_TOTAL_BYTES:
            raise ValueError('distribution archive exceeds inventory limits')
        found[name] = directory
    for name in found:
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.' and found.get(str(parent)) is False:
                raise ValueError('distribution archive file/directory conflict')
    return found


def _read(stream, size, limit):
    if size > limit:
        raise ValueError('distribution metadata exceeds size limit')
    data = stream.read(limit + 1)
    if len(data) != size or len(data) > limit:
        raise ValueError('distribution metadata length differs')
    return data


def _evidence(name, data):
    public_text(name)
    return {'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def _single(message, name, required=False):
    values = message.get_all(name, [])
    if len(values) > 1 or (required and len(values) != 1):
        raise ValueError('ambiguous or missing distribution metadata field: ' + name)
    if not values:
        return None
    value = str(values[0]).strip()
    if required and not value:
        raise ValueError('empty distribution metadata field: ' + name)
    return value


def _message(data):
    try:
        # Reject an accidental locale-dependent decode or lossy replacement.
        data.decode('utf-8')
        message = BytesParser(policy=policy.default.clone(utf8=True)).parsebytes(data)
    except (UnicodeError, ValueError) as exc:
        raise ValueError('invalid distribution metadata') from exc
    if message.defects:
        raise ValueError('malformed distribution metadata')
    for value in message.values():
        if getattr(value, 'defects', ()):
            raise ValueError('malformed distribution metadata header')
    return message


def _public(value):
    if value is None:
        return None
    # Never return personal headers, descriptions, or arbitrary source URLs.
    public_text(value)
    return value


def _licence(value):
    """Keep optional freeform declarations private when not safely public.

    The exact metadata file remains checksum-bound; a header that cannot be
    represented safely is not replaced with a guessed licence expression.
    """
    if value is None:
        return 'NOASSERTION'
    try:
        return _public(value)
    except ValueError:
        return 'NOASSERTION'


def _licence_header(message, name):
    value = _single(message, name)
    # email unfolds a header before exposing its parsed string. Preserve the
    # conservative freeform rule using the original header, without emitting it.
    if any(('\n' in raw or '\r' in raw) for key, raw in message.raw_items() if key.lower() == name.lower()):
        return 'NOASSERTION'
    return _licence(value)


def _marker(value):
    """Check a bounded public marker grammar; never evaluate its expression."""
    public_text(value)
    tokens = []
    offset = 0
    while offset < len(value):
        match = _MARKER_TOKEN.match(value, offset)
        if match is None:
            raise ValueError('unsupported public dependency marker')
        tokens.append(match.group().strip())
        offset = match.end()
    position = 0

    def take():
        nonlocal position
        if position >= len(tokens):
            raise ValueError('incomplete dependency marker')
        token = tokens[position]
        position += 1
        return token

    def operand():
        token = take()
        if token not in _MARKER_NAMES and not token.startswith(('"', "'")):
            raise ValueError('unknown dependency marker variable')

    def atom():
        nonlocal position
        if position < len(tokens) and tokens[position] == '(':
            position += 1
            expression()
            if take() != ')':
                raise ValueError('unbalanced dependency marker')
        else:
            operand()
            operator = take()
            if operator == 'not':
                if take() != 'in':
                    raise ValueError('invalid dependency marker operator')
            elif operator not in {'===', '==', '!=', '<=', '>=', '~=', '<', '>', 'in'}:
                raise ValueError('invalid dependency marker operator')
            operand()

    def expression():
        nonlocal position
        atom()
        while position < len(tokens) and tokens[position] in {'and', 'or'}:
            position += 1
            atom()

    expression()
    if position != len(tokens):
        raise ValueError('unexpected dependency marker token')


def _dependency(value):
    if '@' not in value:
        return _public(value)
    name, _, remainder = value.partition('@')
    if not _DEPENDENCY_NAME.fullmatch(name.strip()):
        raise ValueError('invalid direct dependency name')
    parts = remainder.strip().split(None, 1)
    if not parts:
        raise ValueError('missing direct dependency URL')
    url = public_url(parts[0])
    if url == 'NOASSERTION':
        raise ValueError('missing direct dependency URL')
    location = urlsplit(url)
    if re.fullmatch('[0-9.]+', location.hostname):
        raise ValueError('private direct dependency host')
    path = location.path
    for _ in range(3):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    public_text(path)
    public_path(path.removeprefix('/'))
    if len(parts) > 1:
        marker = parts[1].strip()
        if not marker.startswith(';') or not marker[1:].strip():
            raise ValueError('invalid direct dependency marker')
        _marker(marker[1:].strip())
    return value


def _metadata(data):
    message = _message(data)
    name = _single(message, 'Name', True)
    version = _single(message, 'Version', True)
    if not _NAME.fullmatch(name) or not _VERSION.fullmatch(version):
        raise ValueError('invalid distribution metadata identity')
    result = {
        'name': build_distributions.canonical_name(name),
        'version': version,
        'metadata_version': _public(_single(message, 'Metadata-Version', True)),
        'requires_dist': sorted(_dependency(str(value).strip()) for value in message.get_all('Requires-Dist', [])),
        'requires_python': _public(_single(message, 'Requires-Python')),
        'licence': _licence_header(message, 'License'),
        'licence_expression': _licence_header(message, 'License-Expression'),
        'declared_licence_files': sorted(_member_name(str(value).strip())
                                         for value in message.get_all('License-File', [])),
    }
    if any(not value for value in result['requires_dist']):
        raise ValueError('empty declared distribution dependency')
    return result


def _is_licence(path):
    parts = PurePosixPath(path).parts
    basename = parts[-1].lower()
    return ('licenses' in (part.lower() for part in parts[:-1])
            or basename.startswith(('license', 'licence', 'copying', 'notice')))


def _wheel_metadata(path):
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            for item in infos:
                # ZipInfo.filename is already normalised on Windows and may
                # have been truncated at NUL. Validate the original as well.
                original = item.orig_filename
                _member_name(original)
                if original != item.filename:
                    raise ValueError('unsafe distribution archive member name')
            names = _members((item.filename, item.file_size, item.is_dir()) for item in infos)
            by_name = {_member_name(item.filename): item for item in infos}
            for item in infos:
                mode = stat.S_IFMT(item.external_attr >> 16)
                if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ValueError('distribution archive contains a link or special member')
                if ((mode == stat.S_IFDIR) != item.is_dir() and mode != 0
                        or item.flag_bits & 1
                        or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise ValueError('unsupported distribution archive member')
            metadata_paths = [name for name, directory in names.items()
                              if not directory and name.endswith('.dist-info/METADATA')]
            if len(metadata_paths) != 1 or '/' in metadata_paths[0].split('.dist-info/')[0]:
                raise ValueError('wheel needs exactly one top-level METADATA file')
            metadata_path = metadata_paths[0]
            prefix = metadata_path.rsplit('/', 1)[0]
            wheel_path = prefix + '/WHEEL'
            if wheel_path not in names or names[wheel_path]:
                raise ValueError('wheel is missing WHEEL metadata')
            if any('.dist-info/' in name and not name.startswith(prefix + '/') for name in names):
                raise ValueError('wheel has conflicting distribution metadata directories')
            evidence = []

            def read(name, limit):
                item = by_name[name]
                with archive.open(item) as stream:
                    data = _read(stream, item.file_size, limit)
                return data

            metadata_data = read(metadata_path, MAX_METADATA_BYTES)
            result = _metadata(metadata_data)
            evidence.append(_evidence(metadata_path, metadata_data))
            wheel_data = read(wheel_path, MAX_METADATA_BYTES)
            wheel = _message(wheel_data)
            wheel_version = _single(wheel, 'Wheel-Version', True)
            purelib = _single(wheel, 'Root-Is-Purelib', True)
            if not re.fullmatch(r'1\.[0-9]+', wheel_version) or purelib not in ('true', 'false'):
                raise ValueError('unsupported WHEEL metadata')
            tags = sorted(str(value).strip() for value in wheel.get_all('Tag', []))
            if not tags or any(not _TAG.fullmatch(tag) for tag in tags):
                raise ValueError('invalid WHEEL compatibility tags')
            evidence.append(_evidence(wheel_path, wheel_data))
            parts = path.name[:-4].split('-')
            if len(parts) not in (5, 6):
                raise ValueError('invalid wheel filename')
            if (build_distributions.canonical_name(parts[0]) != result['name']
                    or parts[1] != result['version']):
                raise ValueError('wheel filename and metadata identity differ')
            expected_prefix = prefix[:-len('.dist-info')].rsplit('-', 1)
            if (len(expected_prefix) != 2
                    or build_distributions.canonical_name(expected_prefix[0]) != result['name']
                    or expected_prefix[1] != result['version']):
                raise ValueError('wheel metadata directory identity differs')
            filename_tags = {'-'.join(tag) for tag in itertools.product(*(part.split('.') for part in parts[-3:]))}
            if set(tags) != filename_tags:
                raise ValueError('wheel filename and compatibility tags differ')
            licence_paths = {name for name, directory in names.items()
                             if not directory and name.startswith(prefix + '/') and _is_licence(name)}
            for declared in result['declared_licence_files']:
                candidates = [prefix + '/licenses/' + declared, prefix + '/' + declared]
                selected = [name for name in candidates if name in names and not names[name]]
                if len(selected) != 1:
                    raise ValueError('declared wheel licence file missing or ambiguous')
                licence_paths.add(selected[0])
            licence_files = [_evidence(name, read(name, MAX_LICENCE_BYTES)) for name in sorted(licence_paths)]
            result.update(wheel_version=wheel_version, root_is_purelib=purelib == 'true', tags=tags,
                          metadata_files=sorted(evidence, key=lambda row: row['path']), licence_files=licence_files)
            return result
    except (OSError, EOFError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise ValueError('cannot read wheel metadata') from exc


def _sdist_metadata(path):
    """Read PKG-INFO only; the caller has already checked the sole allowance."""
    try:
        with tarfile.open(path, mode='r:gz') as archive:
            entries = []
            members = {}
            total = 0
            # Iterate incrementally: getmembers() could allocate an unbounded list.
            for item in archive:
                if not (item.isfile() or item.isdir()) or item.sparse is not None:
                    raise ValueError('source archive contains a link or special member')
                entries.append((item.name, item.size, item.isdir()))
                if len(entries) > MAX_MEMBERS or item.size > MAX_MEMBER_BYTES:
                    raise ValueError('source archive exceeds inventory limits')
                total += item.size
                if total > MAX_TOTAL_BYTES:
                    raise ValueError('source archive exceeds inventory limits')
                name = _member_name(item.name)
                if name in members:
                    raise ValueError('duplicate source archive member')
                members[name] = item
            names = _members(entries)
            roots = {name.split('/', 1)[0] for name in names}
            if len(roots) != 1:
                raise ValueError('source archive needs one root directory')
            prefix = next(iter(roots))
            metadata_path = prefix + '/PKG-INFO'
            if metadata_path not in names or names[metadata_path]:
                raise ValueError('source archive is missing root PKG-INFO')

            def read(name, limit):
                item = members[name]
                stream = archive.extractfile(item)
                if stream is None:
                    raise ValueError('source archive metadata is not a file')
                with stream:
                    return _read(stream, item.size, limit)

            data = read(metadata_path, MAX_METADATA_BYTES)
            result = _metadata(data)
            expected_root = prefix.rsplit('-', 1)
            if (len(expected_root) != 2
                    or build_distributions.canonical_name(expected_root[0]) != result['name']
                    or expected_root[1] != result['version']):
                raise ValueError('source archive directory identity differs')
            licence_paths = {name for name, directory in names.items() if not directory and _is_licence(name)}
            for declared in result['declared_licence_files']:
                name = prefix + '/' + declared
                if name not in names or names[name]:
                    raise ValueError('declared source licence file missing')
                licence_paths.add(name)
            result.update(metadata_files=[_evidence(metadata_path, data)],
                          licence_files=[_evidence(name, read(name, MAX_LICENCE_BYTES)) for name in sorted(licence_paths)])
            return result
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise ValueError('cannot read source distribution metadata') from exc


def normalise_wheelhouse(folder, requirements, *, usage='runtime'):
    """Return one measured archive per exact lock row, in stable semantic order.

    ``requirements`` must be the build's exact hashed lock text.
    A local-version wheel accepted by the build retains its actual version and a
    separate ``lock_version``. No generated-source relationship is invented.
    """
    if usage not in USAGES:
        raise ValueError('invalid distribution usage')
    if not isinstance(requirements, str):
        raise ValueError('exact hashed lock text required')
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError('wheelhouse is not an ordinary directory')
    if getattr(folder.stat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
        raise ValueError('wheelhouse is a reparse point')
    rows = build_distributions.lock_requirements(requirements)
    # Check containment and links before the upstream validator reads any bytes.
    paths = [regular(folder, path.name) for path in sorted(folder.iterdir())]
    build_distributions.validate_wheelhouse(folder, rows)
    result = []
    for path in paths:
        selected_hash = digest(path)
        if path.suffix == '.whl':
            metadata = _wheel_metadata(path)
            key = (metadata['name'], metadata['version'])
            if key not in rows and key[0] == 'torch' and '+' in key[1]:
                key = (key[0], key[1].split('+', 1)[0])
            kind = 'wheel'
        else:
            key = ('jieba', '0.42.1')
            allowance = build_distributions.sdist_row(*key, rows[key]['hashes'])
            if path.name != allowance['filename'] or selected_hash != allowance['sha256']:
                raise ValueError('source archive differs from permitted distribution')
            metadata = _sdist_metadata(path)
            if (metadata['name'], metadata['version']) != key:
                raise ValueError('source archive metadata differs from permitted distribution')
            kind = 'sdist'
        if key not in rows or selected_hash not in rows[key]['hashes']:
            raise ValueError('selected distribution differs from lock')
        row = dict(metadata, kind=kind, usage=usage, path=path.name, size=path.stat().st_size,
                   sha256=selected_hash, allowed_hashes=sorted(rows[key]['hashes']))
        if key[1] != metadata['version']:
            row['lock_version'] = key[1]
        result.append(row)
    return sorted(result, key=lambda row: (row['name'], row['version'], row['kind'], row['path']))
