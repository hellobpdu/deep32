"""Restore the laptop period fonts and required font configuration (release specification section 15; round4 F1-F3)."""
import hashlib,json
from pathlib import Path
BASE=Path(__file__).resolve().parent
MANIFEST_SHA='dd89f7d63a8fa4c0b6823a3adef743cf87ff8bbb00a35db621c016d3fe7ac446'  # sha256 or public signing fingerprint
def apply(tree_dir):
    root=Path(tree_dir);data=(BASE/'round4-operations.json').read_bytes()
    if hashlib.sha256(data).hexdigest()!=MANIFEST_SHA:raise ValueError('round4 operations differ')
    ops=json.loads(data);prepared=[];seen=set()
    for op in ops:
        rel=op['path']
        if not rel or rel.startswith('/') or ':' in rel or '\\' in rel or any(p in ('','.','..') for p in rel.split('/')) or rel in seen:raise ValueError('unsafe or duplicate path')
        seen.add(rel);dst=root/rel
        for p in (dst,*dst.parents):
            if p==root:break
            if p.is_symlink():raise ValueError('linked target/parent: '+rel)
            if p!=dst and p.exists() and not p.is_dir():raise ValueError('parent is not directory: '+rel)
        if dst.exists() and not dst.is_file():raise ValueError('target not regular: '+rel)
        before=hashlib.sha256(dst.read_bytes()).hexdigest() if dst.is_file() else 'absent'
        if before!=op['before_sha256']:raise ValueError('before drift: '+rel)
        payload=(BASE/'round4-payload'/op['after_sha256']).read_bytes()
        if len(payload)!=op['bytes'] or hashlib.sha256(payload).hexdigest()!=op['after_sha256'] or before==op['after_sha256']:raise ValueError('payload differs or no change: '+rel)
        prepared.append((rel,dst,payload))
    if not prepared:raise ValueError('empty round4 layer')
    for rel,dst,payload in prepared:dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(payload)
    return [rel for rel,_,_ in prepared]
