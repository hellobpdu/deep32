"""Add round-2 laptop restores after accepted slot 1000 (release specification section 15; L1-L3, U1-U5)."""
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
MANIFEST_SHA = 'c037e32c9afa06bc4ce1298ab3151dc1d04dd68b0fc5f02ac19bca0e919e74ce'  # sha256 or public signing fingerprint

def apply(tree_dir):
    root = Path(tree_dir)
    encoded = (BASE / 'round2-operations.json').read_bytes()
    if hashlib.sha256(encoded).hexdigest() != MANIFEST_SHA:
        raise ValueError('laptop-look operations differ')
    ops = json.loads(encoded)
    prepared = []
    seen = set()
    # Atomic preflight: every before state and every after payload is checked first.
    for op in ops:
        rel = op['path']
        if rel in seen or rel.startswith('/') or '..' in Path(rel).parts or '\\' in rel:
            raise ValueError('unsafe or duplicate laptop-look path')
        seen.add(rel)
        dst = root / rel
        if dst.exists() and not dst.is_file():
            raise ValueError('target is not a file: ' + rel)
        for parent in dst.parents:
            if parent == root:
                break
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                raise ValueError('parent is not a plain directory: ' + rel)
        if dst.is_symlink():
            raise ValueError('target is a link: ' + rel)
        before = hashlib.sha256(dst.read_bytes()).hexdigest() if dst.is_file() else 'absent'
        if before != op['before_sha256']:
            raise ValueError(rel + ': expected ' + op['before_sha256'] + ', found ' + before)
        payload = (BASE / 'round2-payload' / op['after_sha256']).read_bytes()
        if hashlib.sha256(payload).hexdigest() != op['after_sha256'] or len(payload) != op['bytes']:
            raise ValueError('payload differs: ' + rel)
        if before == op['after_sha256']:
            raise ValueError('no change: ' + rel)
        prepared.append((rel,dst,payload))
    if not prepared:
        raise ValueError('empty laptop-look layer')
    for rel,dst,payload in prepared:
        dst.parent.mkdir(parents=True,exist_ok=True)
        dst.write_bytes(payload)
    return [rel for rel,_,_ in prepared]
