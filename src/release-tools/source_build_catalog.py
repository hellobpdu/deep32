#!/usr/bin/env python3
"""Bind the exact contents of detached source/build carriers to signed C2 members."""
import gzip
import hashlib
import os
from pathlib import Path
import re
import tarfile

import capabilities
import contract_validate as contracts
import release_assemble as whole


ASSET = 'SOURCE-BUILD-CATALOG.json'
DIRECTORY = 'source-build'
FORMAT = 'deep32-source-build-catalog/1'
MAX_BYTES = 5 * 1024 * 1024
MAX_CARRIER_BYTES = 512 * 1024**3
HASH = re.compile(r'[0-9a-f]{64}\Z')
BLOCK_BYTES = 1024 * 1024


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _object(value, fields, context):
    _require(type(value) is dict and set(value) == fields, 'Invalid ' + context + ' fields.')
    return value


def _list(value, context):
    _require(type(value) is list and bool(value), 'Missing ' + context + '.')
    return value


def _sha(value):
    return type(value) is str and HASH.fullmatch(value) is not None


def _entry(name):
    # Entries may have upstream dotfiles. They are never extracted or joined to a local path.
    _require(type(name) is str and 0 < len(name) <= 512 and name.isprintable()
             and '\\' not in name and ':' not in name
             and all(part not in ('', '.', '..') for part in name.split('/')),
             'Unsafe archive member name.')
    return name


def _expected(raw, capability_raw):
    capability = capabilities.validate_manifest(contracts.strict_loads(capability_raw, max_bytes=8 * 1024 * 1024))
    doc = _object(contracts.strict_loads(raw, max_bytes=MAX_BYTES),
                  {'format', 'release', 'capability_sha256', 'carriers', 'companions'},
                  'source/build catalog')
    _require(doc['format'] == FORMAT and contracts.canonical(doc) == raw,
             'Source/build catalog must use the canonical format.')
    _require(doc['release'] == capability['release']
             and doc['capability_sha256'] == hashlib.sha256(capability_raw).hexdigest(),
             'Source/build catalog does not bind the exact capability bytes and release.')
    carriers = {}
    for row in _list(doc['carriers'], 'source/build carriers'):
        _object(row, {'sha256', 'size'}, 'source/build carrier')
        digest = row['sha256']
        size = row['size']
        _require(_sha(digest) and digest not in carriers
                 and type(size) is int and 0 < size <= MAX_CARRIER_BYTES,
                 'Invalid or repeated source/build carrier.')
        carriers[digest] = (size, {})
    bundles = {b['id']: {m['path']: m for m in b['members']}
               for b in capability['bundles'] if b['kind'] == 'corresponding-source-build'}
    for row in _list(doc['companions'], 'source/build companions'):
        _object(row, {'id', 'members'}, 'source/build companion delivery')
        name = row['id']
        _require(type(name) is str and name in bundles, 'Unexpected or repeated source/build companion.')
        members = bundles.pop(name)
        for binding in _list(row['members'], 'source/build member bindings'):
            _object(binding, {'path', 'carrier_sha256', 'entry'}, 'source/build member binding')
            logical = binding['path']
            digest = binding['carrier_sha256']
            _require(type(logical) is str and logical in members,
                     'Unexpected or repeated logical source/build member.')
            _require(_sha(digest) and digest in carriers, 'Undeclared source/build carrier.')
            entry = _entry(binding['entry'])
            member = members.pop(logical)
            expected = carriers[digest][1]
            _require(expected.setdefault(entry, member) == member,
                     'A physical source file has conflicting logical identities.')
        _require(not members, 'The source/build member map is incomplete.')
    _require(not bundles and all(members for _, members in carriers.values()),
             'Source/build catalog must cover every companion and every declared carrier.')
    return carriers


class _TarStream:
    """Limit decompressed reads, including extended metadata processed by tarfile."""
    def __init__(self, stream, limit):
        self.stream = stream
        self.limit = limit

    def tell(self):
        return self.stream.tell()

    def seek(self, offset, whence=os.SEEK_SET):
        _require(whence == os.SEEK_SET and 0 <= offset <= self.limit,
                 'Source/build tar exceeds its decompression bound.')
        return self.stream.seek(offset)

    def read(self, size):
        _require(0 <= size <= BLOCK_BYTES, 'Oversized source/build tar metadata entry.')
        data = self.stream.read(min(size, self.limit - self.tell() + 1))
        _require(self.tell() <= self.limit, 'Source/build tar exceeds its decompression bound.')
        return data


def _verify_tar(stream, expected):
    files = dict(expected)
    ancestors = {p for name in files for p in
                 ('/'.join(name.split('/')[:i]) for i in range(1, len(name.split('/'))))}
    paths = set(files) | ancestors
    _require(not files.keys() & ancestors and len(paths) == len({name.casefold() for name in paths}),
             'Conflicting source/build archive paths.')
    # An archive may carry ordinary directory and PAX headers as emitted by git archive.
    bound = sum(row['size'] for row in files.values()) + (len(files) + len(ancestors)) * 4096 + BLOCK_BYTES
    seen = set()
    with gzip.GzipFile(fileobj=stream, mode='rb') as compressed:
        reader = _TarStream(compressed, bound)
        with tarfile.open(fileobj=reader, mode='r:') as archive:
            for item in archive:
                name = _entry(item.name)
                _require(name.casefold() not in seen, 'Repeated or case-aliased archive member.')
                seen.add(name.casefold())
                if item.isdir():
                    _require(name in ancestors and item.size == 0, 'Unexpected archive directory.')
                    continue
                _require(item.isfile() and not item.issparse() and name in files,
                         'Unexpected, linked or special archive member.')
                expected_file = files.pop(name)
                _require(item.size == expected_file['size'], 'Source/build archive member size differs.')
                value = hashlib.sha256()
                with archive.extractfile(item) as member:
                    for chunk in iter(lambda: member.read(BLOCK_BYTES), b''):
                        value.update(chunk)
                _require(value.hexdigest() == expected_file['sha256'],
                         'Source/build archive member hash differs.')
            offset = archive.offset
        _require(not files, 'A source/build archive omits declared files.')
        # tarfile stops at the first end marker. Reject appended entries and bound all padding.
        reader.seek(offset)
        tail_size = 0
        for chunk in iter(lambda: reader.read(BLOCK_BYTES), b''):
            _require(not chunk.strip(b'\0'), 'Nonzero bytes follow the source/build tar end marker.')
            tail_size += len(chunk)
        _require(tail_size >= 1024, 'Source/build tar is missing its complete end marker.')


def verify(raw, capability_raw, directory):
    """Verify trusted descriptor bytes and exact local carriers without extracting or fetching."""
    directory = whole.plain_path(directory, directory=True)
    carriers = _expected(raw, capability_raw)
    names = {digest + '.tar.gz' for digest in carriers}
    _require({p.name for p in directory.iterdir()} == names,
             'Source/build carrier directory has missing or extra entries.')
    for digest, (size, files) in sorted(carriers.items()):
        path = whole.plain_path(directory / (digest + '.tar.gz'))
        with path.open('rb') as stream:
            before = whole._identity(os.fstat(stream.fileno()))
            _require(before == whole._identity(path.stat()) and before[2] == size,
                     'Source/build carrier size or identity differs.')
            _require(hashlib.file_digest(stream, 'sha256').hexdigest() == digest,
                     'Source/build carrier hash differs.')
            stream.seek(0)
            try:
                _verify_tar(stream, files)
            except (EOFError, gzip.BadGzipFile, tarfile.TarError) as exc:
                raise ValueError('Malformed source/build archive.') from exc
            _require(before == whole._identity(os.fstat(stream.fileno()))
                     == whole._identity(whole.plain_path(path).stat()),
                     'Source/build carrier changed during verification.')
    return tuple(directory / name for name in sorted(names))
