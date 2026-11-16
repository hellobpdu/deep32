"""Restore the laptop X core font route, undoing the round4 export (release specification section 15; round5 R2)."""
import hashlib
from pathlib import Path
BASE=Path(__file__).resolve().parent
REL='lanes/u12-ask/launcher/out/files/d32/a/bin/e26'
BEFORE='791fe997e66f21142504549b46bb91885ac2f6eb9bb7f397908ec49b9b337b17'  # sha256 or public signing fingerprint
AFTER='211cd614309afb94b226b9bdad3bf47503c002c98b1d7ea03dc22fcb07ae4575'  # sha256 or public signing fingerprint
SIZE=4699

def apply(tree_dir):
    root=Path(tree_dir);dst=root/REL
    if root.is_symlink() or not root.is_dir():raise ValueError('tree root missing or not plain directory')
    for p in (dst,*dst.parents):
        if p==root:break
        if p.is_symlink():raise ValueError('linked wrapper target/parent')
        if p!=dst and p.exists() and not p.is_dir():raise ValueError('wrapper parent is not directory')
    if not dst.is_file():raise ValueError('wrapper target missing or not regular')
    if hashlib.sha256(dst.read_bytes()).hexdigest()!=BEFORE:raise ValueError('wrapper before hash differs or already restored')
    data=(BASE/'round5-wrapper.sh').read_bytes()
    if len(data)!=SIZE or hashlib.sha256(data).hexdigest()!=AFTER:raise ValueError('wrapper payload differs')
    dst.write_bytes(data)
    return [REL]
