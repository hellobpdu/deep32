#!/usr/bin/env python3
"""Seal the clean public source inputs used by alpha.2 production."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

FORMAT = 'deep32-alpha2-source/1'
SCOPES = ('src/engine', 'src/gateway', 'src/gpu-pack', 'src/model-bridge',
          'src/workstation/node/shims/py/node_layers.py',
          'install/release-key.gpg', 'install/release-key.fingerprint', 'src/release-tools')


def _git(checkout, *arguments):
    return subprocess.check_output(['git', '--no-optional-locks', '-C', str(checkout), *arguments], text=True)


def _relative(name):
    if (not isinstance(name, str) or not name or not name.isascii() or '\\' in name
            or PurePosixPath(name).is_absolute() or ':' in name
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or any(p in ('', '.', '..') for p in name.split('/'))):
        raise ValueError('Unsafe public source name.')
    return name


def _prohibited(name):
    parts = PurePosixPath(name).parts
    for part in parts:
        lower = part.lower()
        stem = lower.split('.', 1)[0]
        if (lower in {'.git', '.ssh', '.gnupg', '.aws', '.agents', '.codex', 'eval', 'staging'}
                or lower.startswith(('priv-', 'golden', 'smoke'))
                or re.fullmatch(r'q[0-9]{3,6}', stem)
                or lower.endswith(('.env', '.corrected.md', '.reviewed.md', '.questions.json'))):
            return True
    return False


def _cache(name):
    parts = PurePosixPath(name).parts
    return '__pycache__' in parts or name.endswith('.pyc')


def _metadata_only(name):
    """Keep test assets bound by the clean Git tree without opening their text."""
    parts = PurePosixPath(name).parts
    if any(part in ('tests', 'test', 'fixtures') for part in parts):
        return True
    if name.startswith('src/release-tools/'):
        return not (name.endswith('.py') or
                    name.startswith('src/release-tools/contracts/schemas/') and name.endswith('.json'))
    return False


def _safe_path(path):
    path = Path(path)
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ValueError('Linked public source or receipt path refused.')


def _clean(checkout):
    status = _git(checkout, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
    for record in status.split('\0'):
        if not record:
            continue
        if len(record) < 4 or record[:3] != '?? ' or not _cache(_relative(record[3:])):
            raise ValueError('The producer source checkout must be clean.')


def _collect(root, relative_name, found):
    """Inspect names and types before ever reading a candidate file's content."""
    _relative(relative_name)
    if _prohibited(relative_name):
        raise ValueError('A prohibited source name is present in producer inputs.')
    target = root / relative_name
    _safe_path(target)
    if target.is_file():
        found.add(relative_name)
        return
    if not target.is_dir():
        raise ValueError('A required public source input is missing or special.')
    with os.scandir(target) as children:
        names = sorted(entry.name for entry in children)
    for name in names:
        child_name = relative_name + '/' + name
        # Cache directories are not traversed: image context copying explicitly
        # excludes them. A tracked cache is refused separately below.
        if name == '__pycache__':
            continue
        _collect(root, child_name, found)


def _hash(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def _identity(checkout):
    revision = _git(checkout, 'rev-parse', '--verify', 'HEAD').strip()
    tree = _git(checkout, 'rev-parse', '--verify', 'HEAD^{tree}').strip()
    if any(re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', value) is None for value in (revision, tree)):
        raise ValueError('Immutable Git source identity is required.')
    return revision, tree


def _snapshot(checkout):
    checkout = Path(checkout)
    _safe_path(checkout)
    if not checkout.is_dir():
        raise ValueError('The public source checkout must be a directory.')
    _clean(checkout)
    revision, tree = _identity(checkout)
    listed = _git(checkout, 'ls-files', '-z', '--', *SCOPES)
    tracked = {_relative(name) for name in listed.split('\0') if name}
    if not tracked:
        raise ValueError('The public source inventory is empty.')
    if any(_prohibited(name) or _cache(name) for name in tracked):
        raise ValueError('Prohibited or generated tracked source input refused.')
    actual = set()
    for scope in SCOPES:
        _collect(checkout, scope, actual)
    actual = {name for name in actual if not _cache(name)}
    if actual != tracked:
        raise ValueError('Missing or untracked files can influence the producer source inputs.')
    files = []
    for name in sorted(tracked):
        if _metadata_only(name):
            continue
        path = checkout / name
        _safe_path(path)
        files.append({'path': name, 'size': path.stat().st_size, 'sha256': _hash(path)})
    _clean(checkout)
    if (revision, tree) != _identity(checkout):
        raise ValueError('Source identity changed while recording its inventory.')
    return {'format': FORMAT, 'revision': revision, 'tree': tree, 'scopes': list(SCOPES), 'files': files,
            'metadata_only_paths': sorted(name for name in tracked if _metadata_only(name))}


def _read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate source receipt field.')
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='ascii'), object_pairs_hook=unique)


def seal_source(checkout, outfile):
    checkout, outfile = map(Path, (checkout, outfile))
    _safe_path(outfile)
    if outfile.exists() or outfile.resolve().is_relative_to(checkout.resolve()):
        raise ValueError('Source receipt must be new and outside the source checkout.')
    receipt = _snapshot(checkout)
    outfile.parent.mkdir(parents=True, exist_ok=True)
    outfile.write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n', encoding='ascii', newline='\n')
    return receipt


def verify_source(checkout, sourcefile):
    sourcefile = Path(sourcefile)
    _safe_path(sourcefile)
    if not sourcefile.is_file():
        raise ValueError('A regular source receipt is required.')
    expected = _read(sourcefile)
    actual = _snapshot(Path(checkout))
    if actual != expected:
        raise ValueError('The producer source changed after sealing.')
    return actual
