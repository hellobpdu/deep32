"""Check every C2 member and C5 joined stream before installing a byte.

Public: run_files and initialise_progress. Refusals are recorded in member
order; only a read error stops the walk before the remaining members.
"""

import sys
sys.dont_write_bytecode = True

import errno
import hashlib
import os
import stat

from imp_fs import ImportFailure, check_signal, crash, signal_point
from imp_manifest import member_unit
from imp_paths import hooks


CHUNK = 1048576


class _HeaderCounter:
    """Count opaque ustar records while files are already being hashed."""

    def __init__(self):
        self.buffer, self.remaining, self.count = b'', 0, 0
        self.valid = True

    def update(self, chunk):
        if not self.valid:
            return
        self.buffer += chunk
        offset = 0
        available = len(self.buffer) // 512 * 512
        while offset < available:
            if self.remaining:
                skipped = min(self.remaining, (available - offset) // 512)
                self.remaining -= skipped
                offset += skipped * 512
                continue
            block = self.buffer[offset:offset + 512]
            offset += 512
            if block != b'\0' * 512:
                try:
                    size = int(block[124:136].rstrip(b'\0 ').lstrip(b' ') or b'0', 8)
                except ValueError:
                    self.valid = False
                    self.buffer = b''
                    return
                self.count += 1
                self.remaining = (size + 511) // 512
        self.buffer = self.buffer[offset:]


def _stage(ctx, name):
    return next(x for x in ctx.report.data['stages'] if x['id'] == name)


def _active_key(ctx, pointer, target=None):
    active = ctx.registry.data.get('active') or {}
    if pointer is None:
        value = target if target in active.get('targets', []) else None
    else:
        value = active.get('pointers', {}).get(pointer)
    if not value:
        return None
    target = value.split('/')[0]
    return next((x['content_key'] for x in ctx.registry.data['generations'] if x['target'] == target), None)


def initialise_progress(ctx):
    """Freeze the signed total, including only units this node must install."""
    total = ctx.manifest['counts']['bytes']
    for desired in ctx.desired:
        if _active_key(ctx, desired['pointer'], desired['target']) != desired['content_key']:
            total += desired['bytes']
    ctx.report.data['progress']['total_bytes'] = total
    if ctx.journal.data.get('stages', {}).get('files', {}).get('state') == 'done':
        ctx.report.data['progress']['done_bytes'] = ctx.manifest['counts']['bytes']
    return total


def _record(ctx, code, path, unit, args=None):
    error = ImportFailure(code, stage='files', unit=unit, path=path, args=args or {})
    ctx.report.add_error(code, 'files', unit, path, args or {})
    error.recorded = True
    return error


def run_files(ctx):
    m, root = ctx.manifest, ctx.disc.root
    initialise_progress(ctx)
    stage = _stage(ctx, 'files')
    stage.update(done_bytes=0, total_bytes=m['counts']['bytes'], done_items=0, total_items=len(m['members']))
    errors = []
    failed_units = set()
    streams = {part['path']: entry['pack'] for entry in m['library'] for part in entry['parts']}
    stream_hashes = {entry['pack']: hashlib.sha256() for entry in m['library']}
    stream_counts = {entry['pack']: _HeaderCounter() for entry in m['library']}
    stream_good = {entry['pack']: True for entry in m['library']}
    good_paths = set()
    units = {x['id']: x for x in ctx.report.data['units']}
    faults = hooks().get('faults', ())
    for index, item in enumerate(m['members'], 1):
        check_signal()
        path, expected = item['path'], item['size']
        unit = member_unit(m, path)
        row = units.get(unit)
        if row and row['state'] not in ('skipped', 'failed'):
            row['state'] = 'checking'
        local = root.joinpath(*path.split('/'))
        handle = None
        failure = None
        try:
            parent = root
            safe_parents = True
            for component in path.split('/')[:-1]:
                parent = parent / component
                if not stat.S_ISDIR(os.lstat(parent).st_mode):
                    safe_parents = False
                    break
            if not safe_parents or not stat.S_ISREG(os.lstat(local).st_mode):
                failure = _record(ctx, 'IMPORT_FILE_MISSING', path, unit)
            else:
                flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
                fd = os.open(local, flags)
                handle = os.fdopen(fd, 'rb')
                found = os.fstat(handle.fileno()).st_size
                if found != expected:
                    failure = _record(ctx, 'IMPORT_FILE_SIZE', path, unit, {'expected': expected, 'found': found})
                else:
                    digest = hashlib.sha256()
                    read_count = 0
                    while True:
                        check_signal()
                        if 'read:' + path in faults:
                            raise OSError(errno.EIO, 'test read')
                        chunk = handle.read(CHUNK)
                        if not chunk:
                            break
                        read_count += len(chunk)
                        digest.update(chunk)
                        if path in streams:
                            stream_hashes[streams[path]].update(chunk)
                            stream_counts[streams[path]].update(chunk)
                        ctx.report.progress(len(chunk), stage='files')
                    if read_count != expected:
                        failure = _record(ctx, 'IMPORT_FILE_SIZE', path, unit, {'expected': expected, 'found': read_count})
                    elif digest.hexdigest() != item['sha256']:
                        failure = _record(ctx, 'IMPORT_FILE_HASH', path, unit)
        except OSError as exc:
            if exc.errno == errno.ENOENT and handle is None and root.is_dir():
                failure = _record(ctx, 'IMPORT_FILE_MISSING', path, unit)
            else:
                failure = _record(ctx, 'IMPORT_READ', path, unit)
                failure.errors = errors + [failure]
                raise failure from exc
        finally:
            if handle:
                handle.close()
        if failure:
            errors.append(failure)
            failed_units.add(unit)
            if path in streams:
                stream_good[streams[path]] = False
        else:
            good_paths.add(path)
            if row and row['kind'] == 'library':
                row['parts_checked'] += 1
        stage['done_items'] = index
        ctx.report.heartbeat()
        if index == (len(m['members']) + 1) // 2:
            crash('CP-files-mid')
            signal_point('CP-files-mid')
    for entry in m['library']:
        unit = entry['pack']
        if stream_good[unit] and stream_hashes[unit].hexdigest() != entry['stream_sha256']:
            failure = _record(ctx, 'IMPORT_FILE_HASH', 'library/' + unit + '/', unit)
            errors.append(failure)
            failed_units.add(unit)
    for row in units.values():
        if row['state'] == 'skipped':
            continue
        if row['id'] in failed_units:
            row['state'] = 'failed'
            continue
        row['state'] = 'checked'
        row['parts_checked'] = row['parts']
        old = ctx.journal.data.get('units', {}).get(row['id'], {})
        if old.get('state') not in ('placing', 'placed', 'current', 'kept'):
            ctx.journal.set_unit(row['id'], state='checked', bytes=row['bytes'])
    if errors:
        errors[0].errors = errors
        raise errors[0]
    named = {x['path'] for x in m['members']} | {'MANIFEST.json', 'MANIFEST.json.asc'}
    unlisted = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        files.sort()
        for name in files:
            relative = os.path.relpath(os.path.join(folder, name), root).replace('\\', '/')
            if relative not in named:
                unlisted.append(relative)
    ctx.journal.data['unlisted'] = {'count': len(unlisted), 'first': sorted(unlisted)[:20]}
    ctx.journal.data['stream_members'] = {name: counter.count for name, counter in stream_counts.items() if counter.valid}
    ctx.journal.save()
    ctx.report.write()
