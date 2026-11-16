"""Update visible desktop/help names and output expectations (release specification section 15).

Inputs are measured after the earlier rename layers. Existing generators write
text desktop files from these inputs. Internal names and legacy recognition stay.
"""
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
MANIFEST_SHA256 = '36e777a2a579c4b207ce1ebfa30c6ca5f15266a29fbf20af4403241f90d0e465'  # sha256 or public signing fingerprint


def apply(tree_dir):
    root = Path(tree_dir)
    encoded = (BASE / 'extra-visible-names.json').read_bytes()
    if hashlib.sha256(encoded).hexdigest() != MANIFEST_SHA256:
        raise ValueError('1013: name manifest differs')
    content = json.loads(encoded)['content']
    prepared = []
    # All files and exact measured counts are checked before the first write.
    for rel, edits in sorted(content.items()):
        if rel.startswith('/') or '..' in Path(rel).parts or '\\' in rel:
            raise ValueError('1013: unsafe path')
        path = root / rel
        for part in [path, *path.parents]:
            if part == root:
                break
            if part.is_symlink():
                raise ValueError('1013: linked path: ' + rel)
        if not path.is_file():
            raise FileNotFoundError('1013: target missing: ' + rel)
        data = path.read_bytes()
        for before, after, expected in edits:
            old, new = before.encode('utf-8'), after.encode('utf-8')
            got = data.count(old)
            if not expected or got != expected or old == new:
                raise ValueError('1013: %s: expected %d occurrences, found %d' % (rel, expected, got))
            data = data.replace(old, new)
        prepared.append((rel, path, data))
    for rel, path, data in prepared:
        path.write_bytes(data)
    return [rel for rel, _, _ in prepared]
