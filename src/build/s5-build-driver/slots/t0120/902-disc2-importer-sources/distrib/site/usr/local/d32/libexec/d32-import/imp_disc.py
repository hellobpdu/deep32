"""Find the C2 content medium without trusting any manifest field.

Public: Disc, run_disc, unmount and clear_stale_mount. Manifest and signature
bytes are read once and retained until the signature stage accepts them.
"""

import sys
sys.dont_write_bytecode = True

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat

from imp_fs import ImportFailure, atomic_write, crash, signal_point


@dataclass
class Disc:
    root: Path
    device: str
    manifest_bytes: bytes = b''
    signature_bytes: bytes = b''
    mounted: bool = False
    label: str | None = None
    signature_error: str | None = None


def _option(ctx, name, default=None):
    opts = ctx.options
    return opts.get(name, default) if isinstance(opts, dict) else getattr(opts, name, default)


def _regular(path):
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _read_once(path, limit):
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, 'rb') as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError('not regular')
        return handle.read(limit + 1)


def clear_stale_mount(ctx):
    """The mounted marker, rather than an unfinished journal, owns a mount."""
    marker = ctx.p.journal / 'mounted'
    if marker.exists():
        try:
            ctx.command('umount', [str(ctx.p.mount)])
        finally:
            marker.unlink(missing_ok=True)


def unmount(ctx):
    disc = getattr(ctx, 'disc', None)
    marker = ctx.p.journal / 'mounted'
    if (disc is not None and disc.mounted) or marker.exists():
        try:
            ctx.command('umount', [str(ctx.p.mount)])
        finally:
            marker.unlink(missing_ok=True)
            if disc is not None:
                disc.mounted = False


def run_disc(ctx):
    """Mount a named device read-only or inspect a Forge folder."""
    clear_stale_mount(ctx)
    folder = _option(ctx, 'folder')
    device = 'folder' if folder else _option(ctx, 'device')
    if not device:
        device = (ctx.journal.data or {}).get('device')
    root = Path(folder) if folder else ctx.p.mount
    disc = Disc(root=root, device=device)
    ctx.disc = disc
    if not folder:
        if ctx.p.node == 'workstation':
            result = ctx.command('disklabel', [device])
            if result.returncode:
                raise ImportFailure('IMPORT_NO_DISC', stage='disc', args={'device': device})
            text = result.stdout.decode('ascii', 'replace') if isinstance(result.stdout, bytes) else result.stdout
            parts = [m.group(1) for line in text.splitlines()
                     if (m := re.match(r'^  ([a-p]): ', line)) and
                     'swap' not in line and 'unused' not in line]
            if 'c' in parts:
                part = 'c'
            elif len(parts) == 1:
                part = parts[0]
            else:
                raise ImportFailure('IMPORT_NO_DISC', stage='disc', args={'device': device})
            label = re.search(r'^\s*label:\s*(.*?)\s*$', text, re.MULTILINE)
            disc.label = label.group(1) if label else None
            args = ['-t', 'cd9660', '-o', 'ro', '/dev/' + device + part, str(root)]
        else:
            args = ['-t', 'iso9660', '-o', 'ro', '/dev/' + device, str(root)]
        root.mkdir(parents=True, exist_ok=True)
        result = ctx.command('mount', args)
        if result.returncode:
            raise ImportFailure('IMPORT_MOUNT', stage='disc', args={'device': device})
        disc.mounted = True
        atomic_write(ctx.p.journal / 'mounted', (device + '\n').encode('ascii'), 0o600,
                     group='wheel' if ctx.p.node == 'workstation' else 'root')
        crash('CP-disc-mounted')
        signal_point('CP-disc-mounted')
    for name in ('MANIFEST.json', 'MANIFEST.json.asc', 'kb', 'corpus', 'library'):
        try:
            mode = os.lstat(root / name).st_mode
            good = stat.S_ISREG(mode) if name.startswith('MANIFEST.') else stat.S_ISDIR(mode)
        except OSError:
            good = False
        if not good:
            raise ImportFailure('IMPORT_NOT_DISC2', stage='disc', args={'missing': name})
    try:
        disc.manifest_bytes = _read_once(root / 'MANIFEST.json', 67108864)
        disc.signature_bytes = _read_once(root / 'MANIFEST.json.asc', 1048576)
    except OSError as exc:
        raise ImportFailure('IMPORT_READ', stage='disc') from exc
    if len(disc.manifest_bytes) > 67108864:
        disc.signature_error = 'IMPORT_MANIFEST'
    elif len(disc.signature_bytes) > 1048576:
        disc.signature_error = 'IMPORT_SIGNATURE'
    ctx.manifest_sha256 = hashlib.sha256(disc.manifest_bytes).hexdigest()
    return disc
