"""Exact hashed requirements and the one lead-reviewed source distribution."""
import re
from pathlib import Path
from urllib.parse import urlsplit

from provenance import digest

# Lead ruling 2026-11-15: jieba 0.42.1 publishes no wheel on PyPI.
# SHA256 of the permitted source archive.
SDIST_ALLOWED = {('jieba', '0.42.1'): '055ca12f62674fafed09427f176506079bc135638a14e23e25be909131928db2'}  # SHA256
SDIST_URL = ('https://files.pythonhosted.org/packages/c6/cb/'
             '18eeb235f833b726522d7ebed54f2278ce28ba9438e3135ab0278d9792a2/jieba-0.42.1.tar.gz')
SDIST_ALLOWED_BY = 'lead 2026-11-15: jieba 0.42.1 has no wheel on PyPI'
REQUIREMENT = r'[A-Za-z0-9_.-]+==[A-Za-z0-9.+_-]+(?: --hash=sha256:[0-9a-f]{64})+'


def canonical_name(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def lock_requirements(text):
    rows = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        if not re.fullmatch(REQUIREMENT, line):
            raise ValueError('lock must contain only exact hashed single-line requirements')
        name, _, remainder = line.partition('==')
        version, *options = remainder.split(' ')
        key = (canonical_name(name), version)
        if any(previous[0] == key[0] for previous in rows):
            raise ValueError('duplicate lock requirement: ' + key[0])
        rows[key] = dict(line=line, hashes={option.removeprefix('--hash=sha256:') for option in options})
    if not rows:
        raise ValueError('lock must contain exact hashed requirements')
    return rows


def sdist_row(name, version, hashes):
    key = (canonical_name(name), version)
    expected = SDIST_ALLOWED.get(key)
    if expected is None:
        raise ValueError('sdist not allowed: ' + name + ' ' + version)
    if set(hashes) != {expected}:
        raise ValueError('sdist hash differs: ' + name + ' ' + version)
    return dict(name=key[0], version=version, kind='sdist', filename=Path(urlsplit(SDIST_URL).path).name,
                url=SDIST_URL, sha256=expected, allowed_by=SDIST_ALLOWED_BY)


def check_sdist_row(row):
    allowed = sdist_row(row['name'], row['version'], {row['sha256']})
    if row.get('kind') != 'sdist' or any(row.get(field) != allowed[field] for field in ('filename', 'url', 'allowed_by')):
        raise ValueError('sdist metadata differs from the lead allowance')
    return allowed


def validate_wheelhouse(folder, requirements):
    """Require one hash-matching wheel or the allowed archive for each lock row."""
    rows = lock_requirements(requirements) if isinstance(requirements, str) else requirements
    found = set()
    for path in sorted(folder.iterdir()):
        if path.is_symlink() or not path.is_file():
            raise ValueError('unexpected wheelhouse entry: ' + path.name)
        if path.name == 'jieba-0.42.1.tar.gz':
            key = ('jieba', '0.42.1')
            if key not in rows:
                raise ValueError('sdist is absent from lock: ' + path.name)
            sdist_row(*key, rows[key]['hashes'])
        elif path.suffix == '.whl':
            parts = path.name[:-4].split('-')
            if len(parts) not in (5, 6) or any(not part for part in parts):
                raise ValueError('invalid wheel filename: ' + path.name)
            key = (canonical_name(parts[0]), parts[1])
            if key not in rows and key[0] == 'torch' and '+' in key[1]:
                key = (key[0], key[1].split('+', 1)[0])
            if key not in rows:
                raise ValueError('wheel is absent from lock: ' + path.name)
        else:
            raise ValueError('unexpected wheelhouse archive: ' + path.name)
        if digest(path) not in rows[key]['hashes']:
            raise ValueError('wheelhouse hash differs: ' + path.name)
        if key in found:
            raise ValueError('duplicate wheelhouse distribution: ' + key[0])
        found.add(key)
    if found != set(rows):
        raise ValueError('wheelhouse is missing: ' + ', '.join(name + '==' + version for name, version in sorted(set(rows) - found)))
    return len(found)
