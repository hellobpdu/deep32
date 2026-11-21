"""Strict, read-only input primitives shared by SBOM adapters."""
import importlib.util
import ipaddress
import json
from pathlib import Path
import re
import stat
import sys
from urllib.parse import urlsplit


def _trusted(name, filename):
    path = Path(__file__).resolve().parents[2] / 'gpu-pack' / 'build' / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_provenance = _trusted('_sbom_provenance', 'provenance.py')
digest = _provenance.digest
canonical = _provenance.canonical
_previous = sys.modules.get('provenance')
try:
    sys.modules['provenance'] = _provenance
    build_distributions = _trusted('_sbom_distributions', 'distributions.py')
finally:
    if _previous is None:
        del sys.modules['provenance']
    else:
        sys.modules['provenance'] = _previous
build_image_binding = _trusted('_sbom_image_binding', 'image_binding.py')


def keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError('missing or unknown manifest fields')


def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('non-finite JSON value')


def load_json(path):
    path = Path(path)
    ordinary(path)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('manifest exceeds size limit')
    value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_pairs,
                       parse_constant=_constant)
    # Overflowing JSON numbers, e.g. 1e999, must not escape parse_constant.
    json.dumps(value, allow_nan=False)
    return value


def public_path(value):
    if (not isinstance(value, str) or len(value) > 512 or not value
            or '\\' in value or ':' in value or value.startswith('/')
            or any(part in ('', '.', '..') or part.startswith('~') for part in value.split('/'))
            or re.fullmatch(r'(?:[A-Za-z0-9_./+@~-]|%[0-9A-Fa-f]{2})+', value) is None):
        raise ValueError('safe relative public path required')
    parts = value.lower().split('/')
    if any(part.endswith('.env') or part.startswith('priv-') or part in
           {'sealed', 'secrets', '.ssh', '.gnupg'}
           or re.fullmatch(r'q[0-9]{3,6}(?:\..*)?', part) for part in parts):
        raise ValueError('excluded private input path')
    return value


def ordinary(path, directory=False):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('links and reparse points are not inputs')
    info = path.stat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError('ordinary input file or directory required')
    return path


def regular(root, relative):
    relative = public_path(relative)
    root = ordinary(root, directory=True)
    path = ordinary(root.joinpath(*relative.split('/')))
    if not path.is_relative_to(root):
        raise ValueError('input is outside its declared root')
    return path


def sha256(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('resolved SHA-256 required')
    return value


def identifier(value):
    if not isinstance(value, str) or re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}', value) is None or 'TO-PIN' in value or 'TO-SET' in value or value.lower() in {'noassertion', 'unknown', 'pending'}:
        raise ValueError('resolved public identifier required')
    return value


def exact_version(value):
    # Exact installed versions keep their native form: Debian epochs and tildes (1:2.3-4, 1.0~rc1-1) and Python
    # epochs and local labels (1!2.0, 2.1+cpu). The frozen contract allows any printable ASCII version.
    if (not isinstance(value, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+~:!-]{0,127}', value) is None
            or 'TO-PIN' in value or 'TO-SET' in value or value.lower() in {'noassertion', 'unknown', 'pending'}):
        raise ValueError('resolved exact version required')
    return value


def public_text(value):
    if (not isinstance(value, str) or not value or len(value) > 512
            or any(ord(c) < 32 or ord(c) > 126 for c in value)
            or re.search(r'[@\\]|[A-Za-z]:/|(?:^| )/(?:home|Users|tmp)/|\b(?:token|password|secret)\s*[:=]|\b(?:\d{1,3}\.){3}\d{1,3}\b', value, re.I)
            or 'TO-PIN' in value or 'TO-SET' in value):
        raise ValueError('public metadata text required')
    return value


def public_url(value):
    if value == 'NOASSERTION':
        return value
    if not isinstance(value, str):
        raise ValueError('recorded public HTTPS provenance URL required')
    u = urlsplit(value)
    if (u.scheme != 'https' or not u.hostname or '.' not in u.hostname
            or u.username or u.password or u.query or u.fragment or u.port
            or u.hostname.endswith(('.local', '.internal', '.ts.net'))
            or '\\' in value or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise ValueError('recorded public HTTPS provenance URL required')
    try:
        ipaddress.ip_address(u.hostname)
    except ValueError:
        pass
    else:
        raise ValueError('host addresses are not public provenance names')
    return value
