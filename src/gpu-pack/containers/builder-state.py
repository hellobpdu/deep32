"""Prepare only the builder's own state directories; never change private state."""
import os
from pathlib import Path
import stat

DIRECTORIES = ('corpus', 'rag-data', 'kb-work', 'exports', 'bundles', 'qdrant')


def prepare(root=Path('/var/lib/deep32-forge'), uid=10032, gid=10032):
    if os.geteuid() != 0:
        raise PermissionError('builder state preparation requires the Forge administrator')
    root = Path(root)
    if root.is_symlink():
        raise ValueError('builder state root must not be a symbolic link')
    root.mkdir(mode=0o711, parents=True, exist_ok=True)
    # Traverse only; no directory listing or recursive ownership changes.
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) | 0o111)
    for name in DIRECTORIES:
        path = root / name
        if path.is_symlink():
            raise ValueError('builder directory must not be a symbolic link')
        path.mkdir(mode=0o700, exist_ok=True)
        os.chown(path, uid, gid)
        os.chmod(path, 0o700)


if __name__ == '__main__':
    prepare()
