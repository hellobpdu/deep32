"""Restore the measured laptop look after the lead slot (release specification section 15)."""
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
MANIFEST_SHA = 'd276dbd7e5bbdfeb83e67e36b5dbbf05df0cf3c11d95820484594f6837163f83'  # sha256 or public signing fingerprint

def apply(tree_dir):
    root = Path(tree_dir)
    encoded = (BASE / 'operations.json').read_bytes()
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
        if dst.is_symlink():
            raise ValueError('target is a link: ' + rel)
        before = hashlib.sha256(dst.read_bytes()).hexdigest() if dst.is_file() else 'absent'
        if before != op['before_sha256']:
            raise ValueError(rel + ': expected ' + op['before_sha256'] + ', found ' + before)
        payload = (BASE / 'payload' / op['after_sha256']).read_bytes()
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
