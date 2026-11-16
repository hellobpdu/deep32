#!/usr/bin/env python3
"""ISO operations called by build-iso.sh. No downloads."""
import hashlib
import json
from pathlib import Path
import re
import sys

from provenance import digest


def patch_grub(root):
    for name in ('grub.cfg', 'loopback.cfg'):
        path = root / name
        text = path.read_text(encoding='ascii')
        # Boot seed is on the release disc; it obtains the separate C7 settings disc.
        text, count = re.subn(r'(?m)^(\s*linux\s+[^\n]*?)(\s+---[^\n]*)?$',
                              r'\1 autoinstall ds=nocloud\\;s=/cdrom/deep32/kit/autoinstall/bootstrap/\2', text)
        if not count:
            raise ValueError('no Ubuntu installer boot entry in ' + name)
        path.write_text(text, encoding='ascii')


def main():
    action, *args = sys.argv[1:]
    if action == 'check-iso':
        if digest(args[0]) != json.loads(Path(args[1]).read_text())['ubuntu']['sha256']:
            raise ValueError('ISO SHA-256 mismatch')
    elif action == 'payload-sums':
        root = Path(args[0])
        rows = json.loads((root / 'PAYLOAD.json').read_text(encoding='ascii'))
        (root / 'PAYLOAD.SHA256').write_text(''.join(row['sha256'] + '  ' + row['path'] + '\n' for row in rows), encoding='ascii', newline='\n')
    elif action == 'patch-grub':
        patch_grub(Path(args[0]))
    elif action == 'md5':
        root = Path(args[0])
        rows = {}
        for line in (root / 'boot/md5sum.txt').read_text(encoding='ascii').splitlines():
            match = re.fullmatch(r'([0-9a-f]{32}) [ *](.+)', line)
            if match:
                rows[match[2].removeprefix('./')] = match[1]
        replacements = [(p, p.relative_to(root).as_posix()) for p in (root / 'deep32').rglob('*') if p.is_file()]
        replacements += [(root / 'boot' / name, 'boot/grub/' + name) for name in ('grub.cfg', 'loopback.cfg')]
        for path, destination in replacements:
            h = hashlib.md5(usedforsecurity=False)
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    h.update(block)
            rows[destination] = h.hexdigest()
        rows.pop('md5sum.txt', None)
        (root / 'boot/md5sum.txt').write_text(''.join(f'{value}  ./{name}\n' for name, value in sorted(rows.items())), encoding='ascii')
    elif action == 'maps':
        root = Path(args[0]).resolve()
        # xorriso path_list accepts one shell-like command per line.
        def quoted(value):
            return "'" + str(value).replace("'", "'\"'\"'") + "'"
        for path in sorted((root / 'deep32').rglob('*')):
            if path.is_file() or (path.is_dir() and not any(path.iterdir())):
                print('-map', quoted(path), quoted('/' + path.relative_to(root).as_posix()))
        for name in ('grub.cfg', 'loopback.cfg'):
            print('-map', quoted(root / 'boot' / name), quoted('/boot/grub/' + name))
        print('-map', quoted(root / 'boot/md5sum.txt'), '/md5sum.txt')
    else:
        raise ValueError('unknown media operation')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        sys.exit(str(exc))
