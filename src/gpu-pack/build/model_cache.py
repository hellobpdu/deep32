"""Derive offline cache pointers from the immutable model pins."""
from pathlib import Path
import re


def reference_files(pins):
    """Return both resolver pointers for each shared model cache."""
    result = {}
    for role in ('tei', 'reranker'):
        model = pins['models'][role]
        identity, revision = model['id'], model['revision']
        if (not isinstance(identity, str)
                or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', identity)
                or any(part in {'.', '..'} for part in identity.split('/'))
                or not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{40}', revision)):
            raise ValueError('A public model ID and immutable revision are required.')
        cache = 'models/' + role + '/models--' + identity.replace('/', '--')
        for name in ('main', revision):
            result[cache + '/refs/' + name] = revision.encode('ascii')
    return dict(sorted(result.items()))


def write_references(payload, pins):
    """Write only into a new producer-owned payload copy."""
    payload = Path(payload)
    references = reference_files(pins)
    # Validate every destination and pinned snapshot before writing any pointer.
    for name, content in references.items():
        target = payload / name
        snapshot = target.parent.parent / 'snapshots' / content.decode('ascii')
        for path in (target, snapshot, *target.parents, *snapshot.parents):
            if path.is_symlink() or path.is_junction():
                raise ValueError('Linked model cache paths are refused.')
        if not snapshot.is_dir() or (target.exists() and not target.is_file()):
            raise ValueError('A pinned snapshot and regular cache pointer are required.')
    for name, content in references.items():
        target = payload / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o644)
    return references
