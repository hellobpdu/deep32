"""Read disc 2 once, in two parts: the knowledge base first, put in use on its own, then the Library.

Public: run_files, run_space, run_kb, run_library, run_switch and run_check (the Workstation's stages), kb_members,
library_members, add_unit_times, kb_in_use, undo_kb_switch, keep_way_back, Status and check_strings (its console
status lines), check_files (the Forge's check-only files stage) and initialise_progress.

The knowledge base part reads only what search needs: the knowledge base's files and the corpus blocks' block.json
files, against which kb_bundle checks it (kb_members). Its files stage checks free space for that part, then reads
each of those members once; its kb stage installs the knowledge base from the checked copies, moves the kb pointer
alone (the Library in use keeps its pointers), restarts the engine on it and checks it. From then on search and Ask
use the new knowledge base, and the console says so. The Library stage then does the same for every other member:
free space for the Library, each member read once and checked, the packs and the Cisco titles index installed from
the checked copies; the switch and check stages put the Library in use in one step and check it. A failure in the
Library part leaves the new knowledge base in use and the Library in use before it (if any) as it was.

Every byte goes through SHA-256 as it is read. A member that will be installed is written as it streams into the
stage folder's disc-copy folder, laid out as on the disc (a Library part as numbered files of STAGE_CHUNK bytes), and
its size and SHA-256 are compared with the signed manifest when it ends. The first refusal or read error stops the
import and removes the partly written copy. Nothing of a part is unpacked or moved into a generation folder before
every member of that part has been checked, and installing reads only the checked copies: the knowledge base and the
Cisco titles index are moved into their generation folders, and each Library stream is unpacked from its chunk files,
which PartReader checks again and which are removed as they are read. A resumed import uses a copy an earlier attempt
staged only after hashing it again and finding the signed size and SHA-256.
"""

import sys
sys.dont_write_bytecode = True

from contextlib import closing, contextmanager, suppress
import copy
import errno
import hashlib
import os
from pathlib import Path
import sqlite3
import stat

import imp_fs
import imp_journal
import imp_switch
import imp_units
from imp_c6 import C6Workstation
from imp_fs import (ImportFailure, check_signal, crash, fault_matches, free_bytes, ignore_signals, log, open_regular,
                    remove_tree, signal_point, sync)
from imp_manifest import member_unit
from imp_paths import hooks, iso


CHUNK = 1048576
# A staged Library part is kept as files of this size, so unpacking frees each one as soon as it has been read.
STAGE_CHUNK = 67108864
# The folder in the stage folder that holds the checked copies; no generation target can have this name.
COPY = 'disc-copy'
# How many checked bytes may pass before the journal records how far the files stage got.
SAVE_BYTES = 268435456
# The unit kinds each part installs.
KB_KINDS = ('kb',)
LIBRARY_KINDS = ('library', 'cisco')
# Each unit's times in the import report: reading and checking its files, then installing it.
UNIT_TIMES = ('copy_started', 'copy_finished', 'install_started', 'install_finished')
# How much the Library part lowers its CPU priority, so search, Ask and pairing stay quick while it runs.
NICE = 10


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


def _record(ctx, code, path, unit, args=None, stage='files'):
    error = ImportFailure(code, stage=stage, unit=unit, path=path, args=args or {})
    ctx.report.add_error(code, stage, unit, path, args or {})
    error.recorded = True
    return error


def check_files(ctx):
    """The Forge's files stage: check every member on the disc; its install stages read the disc again."""
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
    _unlisted(ctx)
    ctx.journal.data['stream_members'] = {name: counter.count for name, counter in stream_counts.items() if counter.valid}
    ctx.journal.save()
    ctx.report.write()


def _unlisted(ctx):
    """Record, without reading them, the files on the disc that the signed manifest does not name."""
    root = ctx.disc.root
    named = {x['path'] for x in ctx.manifest['members']} | {'MANIFEST.json', 'MANIFEST.json.asc'}
    unlisted = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        files.sort()
        for name in files:
            relative = os.path.relpath(os.path.join(folder, name), root).replace('\\', '/')
            if relative not in named:
                unlisted.append(relative)
    ctx.journal.data['unlisted'] = {'count': len(unlisted), 'first': sorted(unlisted)[:20]}


# ------------------------------------------------------------------------------------------- the Workstation's pass

def kb_members(m):
    """The members search needs, in manifest order: the knowledge base's files and the corpus blocks' block.json
    files (kb_bundle checks the knowledge base against them). The engine reads only the knowledge base (C3)."""
    blocks = {block['path'] for block in m['blocks']}
    prefix = m['kb']['path'] + '/'
    return [item for item in m['members'] if item['path'].startswith(prefix) or item['path'] in blocks]


def library_members(m):
    """Every other member, in manifest order: the Library parts, the Cisco titles index, the corpus archives, code
    and the root files. On the Workstation only the Library parts and the index are installed; the rest is checked."""
    first = {item['path'] for item in kb_members(m)}
    return [item for item in m['members'] if item['path'] not in first]


def add_unit_times(ctx):
    """Give each unit of the report its copy and install times, empty until they happen."""
    for row in ctx.report.data['units']:
        for key in UNIT_TIMES:
            row.setdefault(key, None)


def _time(ctx, unit, key):
    row = ctx.report.unit(unit)
    if row is not None and key in row:
        row[key] = iso()
        log(ctx.p, ctx.import_id, 'unit', unit=unit, time=key)


def _remove(path):
    path = Path(path)
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        remove_tree(path)


def _create(path):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    return os.fdopen(os.open(path, flags, 0o600), 'wb')


def _open_member(root, path):
    """Open a disc member read-only, without following a link; None unless it is a regular file below folders."""
    parent = root
    for component in path.split('/')[:-1]:
        parent = parent / component
        if not stat.S_ISDIR(os.lstat(parent).st_mode):
            return None
    local = root.joinpath(*path.split('/'))
    if not stat.S_ISREG(os.lstat(local).st_mode):
        return None
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    return os.fdopen(os.open(local, flags), 'rb')


def _chunks(item):
    """The chunk files a staged Library part is kept as: (path below the copy folder, size), in order."""
    count = (item['size'] + STAGE_CHUNK - 1) // STAGE_CHUNK
    return [('%s.c%04d' % (item['path'], index + 1), min(STAGE_CHUNK, item['size'] - index * STAGE_CHUNK))
            for index in range(count)]


class _Stream:
    """One Library pack's joined stream, hashed and its ustar records counted as its parts go by."""

    def __init__(self):
        self.digest, self.counter, self.whole = hashlib.sha256(), _HeaderCounter(), True

    def update(self, chunk):
        self.digest.update(chunk)
        self.counter.update(chunk)

    def fork(self):
        other = _Stream()
        other.digest, other.counter, other.whole = self.digest.copy(), copy.deepcopy(self.counter), self.whole
        return other


class _Copy:
    """A member staged as one new file in the copy folder."""

    def __init__(self, ctx, item):
        self.ctx = ctx
        self.path = ctx.copy_root.joinpath(*item['path'].split('/'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        imp_fs._write_fault(self.path, ctx.p.cr)
        self.handle = _create(self.path)

    def write(self, data):
        imp_fs._write_fault(self.path, self.ctx.p.cr)
        self.handle.write(data)

    def finish(self):
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.handle.close()
        self.handle = None
        return []

    def discard(self):
        if self.handle is not None:
            with suppress(OSError):
                self.handle.close()
            self.handle = None
        with suppress(OSError):
            _remove(self.path)


class _Chunks:
    """A Library part staged as numbered files of STAGE_CHUNK bytes, each flushed and closed before the next."""

    def __init__(self, ctx, item):
        self.ctx, self.names = ctx, _chunks(item)
        self.records, self.made = [], []
        self.handle = self.digest = None
        self.used = 0

    def _open(self):
        path = self.ctx.copy_root.joinpath(*self.names[len(self.records)][0].split('/'))
        path.parent.mkdir(parents=True, exist_ok=True)
        imp_fs._write_fault(path, self.ctx.p.cr)
        self.handle = _create(path)
        self.made.append(path)
        self.digest, self.used = hashlib.sha256(), 0

    def _close(self):
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.handle.close()
        self.handle = None
        name = self.names[len(self.records)][0]
        self.records.append({'path': name, 'size': self.used, 'sha256': self.digest.hexdigest()})

    def write(self, data):
        view = memoryview(data)
        while len(view):
            if self.handle is None:
                self._open()
            piece = view[:self.names[len(self.records)][1] - self.used]
            imp_fs._write_fault(self.made[-1], self.ctx.p.cr)
            self.handle.write(piece)
            self.digest.update(piece)
            self.used += len(piece)
            view = view[len(piece):]
            if self.used == self.names[len(self.records)][1]:
                self._close()

    def finish(self):
        if self.handle is not None:
            self._close()
        return self.records

    def discard(self):
        if self.handle is not None:
            with suppress(OSError):
                self.handle.close()
            self.handle = None
        for path in self.made:
            with suppress(OSError):
                _remove(path)


def _from_disc(ctx, item, unit, sink_for, stream, stage='files'):
    """Read one member from the disc once: hash it, feed its stream and, with sink_for, write it to the copy folder.

    Its size and SHA-256 are compared with the signed manifest as it ends. Any refusal or error removes what was
    written for it and stops the walk. Returns the sink's chunk records, or None when nothing was staged."""
    path, expected = item['path'], item['size']
    root = ctx.disc.root
    handle = sink = None

    def record(code, args=None):
        return _record(ctx, code, path, unit, args, stage)
    try:
        try:
            handle = _open_member(root, path)
        except OSError as exc:
            if exc.errno == errno.ENOENT and root.is_dir():
                raise record('IMPORT_FILE_MISSING') from exc
            raise record('IMPORT_READ') from exc
        if handle is None:
            raise record('IMPORT_FILE_MISSING')
        found = os.fstat(handle.fileno()).st_size
        if found != expected:
            raise record('IMPORT_FILE_SIZE', {'expected': expected, 'found': found})
        if sink_for is not None:
            try:
                sink = sink_for()
            except OSError as exc:
                raise record('IMPORT_WRITE') from exc
        digest, count = hashlib.sha256(), 0
        while True:
            check_signal()
            try:
                if fault_matches('read', path):
                    raise OSError(errno.EIO, 'test read')
                chunk = handle.read(CHUNK)
            except OSError as exc:
                raise record('IMPORT_READ') from exc
            if not chunk:
                break
            count += len(chunk)
            if count > expected:
                raise record('IMPORT_FILE_SIZE', {'expected': expected, 'found': count})
            digest.update(chunk)
            if stream is not None:
                stream.update(chunk)
            if sink is not None:
                try:
                    sink.write(chunk)
                except OSError as exc:
                    raise record('IMPORT_WRITE') from exc
            ctx.report.progress(len(chunk), stage=stage)
        if count != expected:
            raise record('IMPORT_FILE_SIZE', {'expected': expected, 'found': count})
        if digest.hexdigest() != item['sha256']:
            raise record('IMPORT_FILE_HASH')
        if sink is None:
            return None
        try:
            return sink.finish()
        except OSError as exc:
            raise record('IMPORT_WRITE') from exc
    except BaseException:
        if sink is not None:
            sink.discard()
        raise
    finally:
        if handle is not None:
            handle.close()


def _hash_copy(ctx, path, size, sinks):
    """Hash a staged copy into sinks; False unless it is a regular file of exactly size bytes that reads cleanly."""
    try:
        handle = open_regular(path)
    except OSError:
        return False
    count = 0
    with handle:
        try:
            if os.fstat(handle.fileno()).st_size != size:
                return False
            while True:
                check_signal()
                chunk = handle.read(CHUNK)
                if not chunk:
                    break
                count += len(chunk)
                for sink in sinks:
                    sink.update(chunk)
                ctx.report.heartbeat()
        except OSError:
            return False
    return count == size


def _reuse(ctx, item, how, stream):
    """Use the copy an earlier attempt staged only if it hashes to the signed size and SHA-256 now.

    Returns the chunk records ([] for a single file), or None after removing whatever was there."""
    names = [(item['path'], item['size'])] if how == 'copy' else _chunks(item)
    paths = [ctx.copy_root.joinpath(*name.split('/')) for name, _ in names]
    if not any(os.path.lexists(path) for path in paths):
        return None
    whole, records = hashlib.sha256(), []
    for (name, size), path in zip(names, paths):
        digest = hashlib.sha256()
        if not _hash_copy(ctx, path, size, [digest, whole] + ([stream] if stream is not None else [])):
            break
        records.append({'path': name, 'size': size, 'sha256': digest.hexdigest()})
    else:
        if whole.hexdigest() == item['sha256']:
            return records if how == 'part' else []
    for path in paths:
        _remove(path)
    return None


def _needs_install(ctx, desired, stage):
    """True when the install stages will build this unit from the disc (imp_units._prepare's rule)."""
    imp_units._validate_target(ctx, desired, stage)
    if imp_units._current(ctx, desired):
        return False
    target = ctx.p.cr / desired['target']
    if target.exists() or target.is_symlink():
        record = imp_units._record(ctx, desired)
        states = [ctx.journal.data['units'].get(x['id'], {}).get('state') for x in desired['units']]
        return not ((record and record['content_key'] == desired['content_key']) or 'placing' in states)
    return True


def _plan(ctx, kinds, stage):
    """Map each member this part stages to 'copy' (one file) or 'part' (chunk files); the rest is only checked.

    The knowledge base part always stages the corpus blocks' block.json files: kb_bundle checks it against them."""
    m = ctx.manifest
    plan = {block['path']: 'copy' for block in m['blocks']} if 'kb' in kinds else {}
    for desired in ctx.desired:
        if desired['unit_kind'] not in kinds or not _needs_install(ctx, desired, stage):
            continue
        if desired['unit_kind'] == 'kb':
            prefix = m['kb']['path'] + '/'
            plan.update((item['path'], 'copy') for item in m['members'] if item['path'].startswith(prefix))
        elif desired['unit_kind'] == 'cisco':
            plan[m['cisco_public']['path']] = 'copy'
        elif desired['unit_kind'] == 'library':
            plan.update((part['path'], 'part') for entry in desired['streams'] for part in entry['parts'])
    return plan


def _restore_moved(ctx, folder, root, stage):
    """Put back the checked copies an interrupted install had moved into a unit folder; they are hashed again."""
    m = ctx.manifest
    for desired in ctx.desired:
        if desired['unit_kind'] == 'kb':
            source, path = folder / desired['target'] / m['kb']['generation'], m['kb']['path']
        elif desired['unit_kind'] == 'cisco':
            source, path = folder / desired['target'] / 'cisco-public.sqlite', m['cisco_public']['path']
        else:
            continue
        imp_units._validate_target(ctx, desired, stage)
        destination = root.joinpath(*path.split('/'))
        if os.path.lexists(source) and not os.path.islink(source) and not os.path.lexists(destination):
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.rename(source, destination)
            except OSError as exc:
                raise ImportFailure('IMPORT_WRITE', stage=stage) from exc


def _clean_copies(ctx, plan, root):
    """Keep in the copy folder only the copies this plan uses, and return their bytes; each is hashed before use."""
    if not os.path.lexists(root):
        return 0
    if os.path.islink(root) or not root.is_dir():
        _remove(root)
        return 0
    wanted = set()
    for item in ctx.manifest['members']:
        how = plan.get(item['path'])
        if how == 'copy':
            wanted.add(item['path'])
        elif how == 'part':
            wanted.update(name for name, _ in _chunks(item))
    present = 0
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in list(dirs):
            if (Path(base) / name).is_symlink():
                _remove(Path(base) / name)
                dirs.remove(name)
        for name in files:
            path = Path(base) / name
            info = os.lstat(path)
            if path.relative_to(root).as_posix() in wanted and stat.S_ISREG(info.st_mode):
                present += info.st_size
            else:
                _remove(path)
    return present


def _reserve(ctx, plan, stage):
    """Clean what earlier imports left behind, then refuse before copying when this part's copies cannot fit.

    The rule is the space stage's: what is staged plus 5%, the margin unpacking needs. Copies an earlier attempt of
    this import staged already take their space, so they count as used. imp_units.run_space does the cleaning; its
    own figure counts those copies again, so its refusal is replaced by this one."""
    try:
        imp_units.run_space(ctx)
    except ImportFailure as exc:
        if exc.code != 'IMPORT_SPACE':
            exc.stage = stage
            raise
    present = 0
    if os.path.lexists(ctx.p.cr / ctx.journal.data['stage_dir']):
        folder = imp_units._stage_dir(ctx)
        _restore_moved(ctx, folder, folder / COPY, stage)
        # Unit folders of an interrupted install: _prepare would make each one again, empty.
        for entry in sorted(folder.iterdir(), key=lambda p: p.name):
            if entry.name != COPY:
                _remove(entry)
        present = _clean_copies(ctx, plan, folder / COPY)
    total = sum(item['size'] for item in ctx.manifest['members'] if item['path'] in plan)
    needed = max(0, (total * 105 + 99) // 100 - present)
    available = free_bytes(ctx.p.cr)
    ctx.report.data['progress'].update(free_bytes_before=available, free_bytes_now=available, needed_bytes=needed)
    ctx.report.write()
    if available < needed:
        raise ImportFailure('IMPORT_SPACE', stage=stage, args={'needed_bytes': needed, 'free_bytes': available})
    ctx.copy_root = imp_units._stage_dir(ctx) / COPY
    ctx.staged_total = total


@contextmanager
def _checked_copies(ctx):
    """While imp_units reads the disc (kb_bundle's --block files, PartReader), its root is the checked copies."""
    root = imp_units._stage_dir(ctx) / COPY
    disc = ctx.disc
    saved = disc.root
    disc.root = root
    try:
        yield root
    finally:
        disc.root = saved


def _verified(ctx, key, count):
    """How many leading members of this list an earlier attempt of this import checked, for the same manifest.

    Each list keeps its own record: files_verified for every member (a disc already in use), kb_verified for the
    knowledge base part and library_verified for the Library part, so an index never counts another list's members."""
    value = ctx.journal.data.get(key)
    if (isinstance(value, dict) and value.get('manifest_sha256') == ctx.report.data['source']['manifest_sha256'] and
            type(value.get('members')) is int and 0 <= value['members'] <= count):
        return value['members']
    return 0


class _Walk:
    """One part's walk over its signed members, in manifest order, stopping at the first refusal."""

    def __init__(self, ctx, plan, members, key, stage):
        self.ctx, self.m, self.plan, self.first = ctx, ctx.manifest, plan, set()
        self.members, self.key, self.stage = members, key, stage
        self.units = {x['id']: x for x in ctx.report.data['units']}
        self.packs = {part['path']: entry for entry in self.m['library'] for part in entry['parts']}
        self.streams = {entry['pack']: _Stream() for entry in self.m['library']}
        self.counts = dict(ctx.journal.data.get('stream_members') or {})
        self.known = self.saved = _verified(ctx, key, len(members))
        self.unsaved = 0
        self.last = {}
        for index, item in enumerate(members, 1):
            self.last[member_unit(self.m, item['path'])] = index
        self.walked = set(self.last) - {None}

    def member(self, item):
        """Read a member from the disc once, or use the copy an earlier attempt staged when it still matches."""
        ctx, path = self.ctx, item['path']
        unit = member_unit(self.m, path)
        how = self.plan.get(path)
        entry = self.packs.get(path)
        stream = self.streams[entry['pack']] if entry else None
        if how is None:
            _from_disc(ctx, item, unit, None, stream, self.stage)
            return
        trial = stream.fork() if stream is not None else None
        records = _reuse(ctx, item, how, trial)
        if records is None:
            sink_for = (lambda: _Copy(ctx, item)) if how == 'copy' else (lambda: _Chunks(ctx, item))
            records = _from_disc(ctx, item, unit, sink_for, stream, self.stage)
        else:
            if entry is not None:
                self.streams[entry['pack']] = trial
            ctx.report.progress(item['size'], stage=self.stage)
        if how == 'part':
            ctx.staged_chunks[path] = records

    def step(self, index, item):
        ctx, path = self.ctx, item['path']
        check_signal()
        unit = member_unit(self.m, path)
        row = self.units.get(unit)
        if row and row['state'] not in ('skipped', 'failed'):
            if row['state'] != 'checking':
                _time(ctx, unit, 'copy_started')
            row['state'] = 'checking'
        entry = self.packs.get(path)
        if path not in self.first:
            if path not in self.plan and index <= self.known:
                # Checked by an earlier attempt of this import, and never used: not read again.
                ctx.report.progress(item['size'], stage=self.stage)
                if entry is not None:
                    self.streams[entry['pack']].whole = False
            else:
                self.member(item)
        if row and row['kind'] == 'library':
            row['parts_checked'] += 1
        last = entry is not None and path == entry['parts'][-1]['path']
        if last:
            stream = self.streams[entry['pack']]
            if stream.whole:
                if stream.digest.hexdigest() != entry['stream_sha256']:
                    raise _record(ctx, 'IMPORT_FILE_HASH', 'library/' + entry['pack'] + '/', entry['pack'],
                                  stage=self.stage)
                if stream.counter.valid:
                    self.counts[entry['pack']] = stream.counter.count
        if row and row['state'] == 'checking' and self.last.get(unit) == index:
            _time(ctx, unit, 'copy_finished')
        self.unsaved += item['size']
        if ((entry is None or last) and index > self.saved and
                (self.unsaved >= SAVE_BYTES or index == len(self.members))):
            # Only between streams: a stream checked by an earlier attempt is skipped whole or read whole.
            ctx.journal.data[self.key] = {'manifest_sha256': ctx.report.data['source']['manifest_sha256'],
                                          'members': index}
            ctx.journal.save()
            self.saved, self.unsaved = index, 0
        _stage(ctx, self.stage)['done_items'] = index
        ctx.report.heartbeat()
        if index == (len(self.members) + 1) // 2:
            point = 'CP-files-mid' if self.stage == 'files' else 'CP-library-copy-mid'
            crash(point)
            signal_point(point)

    def run(self):
        for index, item in enumerate(self.members, 1):
            self.step(index, item)
        for unit in sorted(self.walked):
            row = self.units.get(unit)
            if row is None or row['state'] == 'skipped':
                continue
            row['state'] = 'checked'
            row['parts_checked'] = row['parts']
            old = self.ctx.journal.data.get('units', {}).get(unit, {})
            if old.get('state') not in ('placing', 'placed', 'current', 'kept'):
                self.ctx.journal.set_unit(unit, state='checked', bytes=row['bytes'])
        self.ctx.journal.data['stream_members'] = self.counts


def _reconcile(ctx, kinds):
    """imp_units.reconcile_placing for this part's units only: the other part's are neither checked nor changed."""
    every = ctx.desired
    ctx.desired = [x for x in every if x['unit_kind'] in kinds]
    try:
        imp_units.reconcile_placing(ctx)
    finally:
        ctx.desired = every
        ctx.journal.data['desired'] = every
        ctx.journal.save()


def _prepare_walk(ctx, members, kinds, key, stage):
    """Free space for this part's copies first, then the walk; units an earlier attempt placed are adopted on the way."""
    walk = _Walk(ctx, _plan(ctx, kinds, stage), members, key, stage)
    _reserve(ctx, walk.plan, stage)
    if 'kb' in kinds:
        # The block.json files first: reconciling a placed knowledge base checks it against them.
        walk.first = {block['path'] for block in ctx.manifest['blocks']}
        for item in members:
            if item['path'] in walk.first:
                walk.member(item)
    with _checked_copies(ctx):
        _reconcile(ctx, kinds)
    plan = _plan(ctx, kinds, stage)
    if plan != walk.plan:
        walk.plan = plan
        _reserve(ctx, plan, stage)
    return walk


def run_files(ctx):
    """The Workstation's files stage: the knowledge base part's members (every member when the disc is already in
    use), after free space for their copies, each read once, checked, and staged if it will be installed."""
    m = ctx.manifest
    current = getattr(ctx, 'current', False)
    members = m['members'] if current else kb_members(m)
    initialise_progress(ctx)
    _stage(ctx, 'files').update(done_bytes=0, total_bytes=sum(item['size'] for item in members), done_items=0,
                                total_items=len(members))
    ctx.staged_chunks, ctx.staged_total = {}, 0
    if current:
        walk = _Walk(ctx, {}, members, 'files_verified', 'files')
    else:
        walk = _prepare_walk(ctx, members, KB_KINDS, 'kb_verified', 'files')
    walk.run()
    if current:
        _unlisted(ctx)
    ctx.journal.save()
    ctx.report.write()


def run_space(ctx):
    """After the copies: check that the margin unpacking needs (5% of what was staged) is still free."""
    imp_units._stage_dir(ctx)
    needed = (getattr(ctx, 'staged_total', 0) * 5 + 99) // 100
    available = free_bytes(ctx.p.cr)
    ctx.report.data['progress'].update(free_bytes_now=available, needed_bytes=needed)
    ctx.report.write()
    if available < needed:
        raise ImportFailure('IMPORT_SPACE', stage='space', args={'needed_bytes': needed, 'free_bytes': available})


def _move(ctx, desired, source, destination, stage):
    """Move a checked copy into its unit folder: one rename on the same file system, no second read."""
    try:
        info = os.lstat(source)
    except FileNotFoundError:
        info = None
    if info is None or stat.S_ISLNK(info.st_mode):
        ctx.journal.note('checked copy missing: ' + desired['target'])
        raise ImportFailure('IMPORT_INTERNAL', stage=stage)
    try:
        os.rename(source, destination)
    except OSError as exc:
        raise imp_units._failure(ctx, desired, 'IMPORT_WRITE', stage) from exc


def run_kb(ctx):
    """Install the knowledge base from its checked copies and place it, then put it in use on its own and check it.

    From then on the engine, search and Ask use it while the Library part runs."""
    desired = next(x for x in ctx.desired if x['unit_kind'] == 'kb')
    imp_units._setup_stage(ctx, 'kb', [desired])
    members = [x for x in ctx.manifest['members'] if x['path'].startswith(ctx.manifest['kb']['path'] + '/')]
    row = _stage(ctx, 'kb')
    row['total_items'] = len(members)
    _time(ctx, 'kb', 'install_started')
    with _checked_copies(ctx) as root:
        folder = imp_units._prepare(ctx, desired, 'kb')
        if folder is not None:
            generation = ctx.manifest['kb']['generation']
            _move(ctx, desired, root.joinpath(*ctx.manifest['kb']['path'].split('/')), folder / generation, 'kb')
            imp_units._bytes(ctx, 'kb', desired['bytes'])
            row['done_items'] = len(members)
            sync()
            imp_units._check_kb(ctx, folder / generation)
            crash('CP-kb-staged')
            signal_point('CP-kb-staged')
            imp_units._place(ctx, desired, folder, 'kb', 'CP-kb-placed')
            imp_units._state(ctx, desired, 'installed')
        elif not hasattr(ctx, 'bundle'):
            imp_units._check_kb(ctx, ctx.p.cr / desired['value'])
    row['done_items'] = row['total_items']
    ctx.report.write()
    _switch_kb(ctx, desired)
    _check_live_kb(ctx, desired)
    _time(ctx, 'kb', 'install_finished')
    data = ctx.journal.data
    # One journal write ends the knowledge base part. A stop before it undoes the switch (undo_kb_switch) and this
    # part runs again; after it the knowledge base stays in use, and a failure from here on fails only the Library.
    # The registry as it is now is what a failed Library part returns to.
    data['kb_live'] = {'manifest_sha256': data['manifest_sha256'],
                       'way_back': copy.deepcopy(data['switch']['saved_registry']['active'])}
    data['switch'] = None
    data['registry_before'] = copy.deepcopy(ctx.registry.data)
    data['registry_existed_before'] = ctx.p.registry_file.exists()
    ctx.journal.save()
    kb_in_use(ctx)
    crash('CP-kb-live')


def _switch_kb(ctx, row):
    """Move the kb pointer alone, as one durable transaction. The Library in use keeps its pointers. The registry
    keeps the pair in use before as the way back and marks the Library as still to come, so this disc counts as
    already imported only once its Library is in use too."""
    path = ctx.p.cr / row['pointer']
    old = imp_switch.read_pointer(path)
    pointers = [] if old == row['value'] else [{'pointer': row['pointer'], 'old': old, 'new': row['value'],
                                                'moved': False, 'moved_back': False}]
    saved = {'active': copy.deepcopy(ctx.registry.active), 'previous': copy.deepcopy(ctx.registry.previous)}
    ignore_signals(True)
    try:
        ctx.journal.data['switch'] = {'part': 'kb', 'phase': 'begun', 'saved_registry': saved, 'pointers': pointers}
        ctx.journal.save()
        crash('CP-kb-switch-begun')
        for entry in pointers:
            try:
                imp_switch.move_pointer(path, entry['new'])
            except OSError as exc:
                with suppress(OSError, ValueError):
                    imp_switch.move_pointer(path, entry['old'], fault=False)
                ctx.journal.note('pointer-failed:' + entry['pointer'] + ':' + type(exc).__name__)
                raise ImportFailure('IMPORT_SWITCH', stage='kb', unit='kb') from exc
            entry['moved'] = True
            ctx.journal.save()
            log(ctx.p, ctx.import_id, 'pointer', pointer=entry['pointer'], state='new')
        if (saved['active'] or {}).get('pointers', {}).get(row['pointer']) != row['value']:
            active = copy.deepcopy(saved['active']) or {'pointers': {}}
            active.update(manifest_sha256=ctx.journal.data['manifest_sha256'], import_id=ctx.import_id,
                          disc_version=ctx.report.data['source']['disc_version'], library_pending=True)
            active['pointers'] = dict(active['pointers'], **{row['pointer']: row['value']})
            ctx.registry.previous = saved['active']
            ctx.registry.active = active
            ctx.registry.save()
        ctx.journal.data['switch']['phase'] = 'done'
        ctx.journal.save()
        crash('CP-kb-switch-done')
    finally:
        ignore_signals(False)


def _check_live_kb(ctx, row):
    """Restart the engine on the knowledge base when it moved, check the bundle in use and the meaning search. A
    failure puts the kb pointer, the registry and the engine back (imp_switch.rollback)."""
    switch = ctx.journal.data['switch']
    what = imp_switch._services(ctx) if switch['pointers'] else None
    crash('CP-kb-check')
    signal_point('CP-kb-check')
    if what is None:
        try:
            folder = imp_switch._pointer_folder(ctx, row)
            good = (folder is not None and not imp_switch._fault('check:kb') and
                    imp_switch._kb_check(ctx, row, folder))
        except (OSError, ValueError, KeyError):
            good = False
        if not good:
            what = 'engine'
    if what:
        error = ctx.report.add_error('IMPORT_CHECK', 'kb', None, None, {'what': what})
        ctx.journal.data['error'] = copy.deepcopy(error)
        ctx.journal.save()
        if switch['pointers']:
            imp_switch.rollback(ctx)
            ctx.outcome = 'rolled-back'
        else:
            ctx.outcome = 'failed'
        raise ImportFailure('IMPORT_CHECK', stage='kb', args={'what': what}, resumable=False)
    imp_switch.meaning_search(ctx)


def kb_in_use(ctx):
    """The knowledge base of this disc is in use and checked: say so on the console."""
    ctx.kb_live = True
    log(ctx.p, ctx.import_id, 'unit', unit='kb', state='in-use')
    ctx.c6.set('running', ctx.c6.strings.get('c6.import.kb.ready'))


def undo_kb_switch(ctx):
    """After a stop during the knowledge base's switch or its check, before the disc is read: the kb pointer and the
    registry go back to what they were before it, and the engine back onto that knowledge base. The import then
    switches and checks again, and another disc never meets a half-made switch."""
    switch = ctx.journal.data['switch']
    saved = switch['saved_registry']
    ignore_signals(True)
    try:
        moved = False
        for entry in switch['pointers']:
            if imp_switch.read_pointer(ctx.p.cr / entry['pointer']) != entry['old']:
                imp_switch.move_pointer(ctx.p.cr / entry['pointer'], entry['old'], fault=False)
                moved = True
            moved = moved or entry.get('moved', False)
            log(ctx.p, ctx.import_id, 'rollback', pointer=entry['pointer'])
        ctx.registry.active, ctx.registry.previous = saved['active'], saved['previous']
        ctx.registry.save()
        if moved:
            imp_switch._services(ctx, restoring=True)
        ctx.journal.data['switch'] = None
        ctx.journal.note('knowledge base switch undone after a stop')
    finally:
        ignore_signals(False)


def keep_way_back(ctx):
    """After the Library's switch, the way back stays the pair in use before this import.

    imp_switch's commit makes the registry it saved the previous pair: here the one with this import's knowledge
    base and the old Library. The pair from before the knowledge base's switch replaces it."""
    live = ctx.journal.data.get('kb_live') or {}
    saved = ((ctx.journal.data.get('switch') or {}).get('saved_registry') or {}).get('active')
    if 'way_back' not in live or saved is None or ctx.registry.previous != saved or live['way_back'] == saved:
        return
    imp_journal._validate_active(live['way_back'])
    ctx.registry.previous = live['way_back']
    ctx.registry.save()


class _ChunkRoot:
    """The disc root PartReader sees while it unpacks one stream from its chunk files.

    PartReader asks for a chunk's path only after it has closed the chunk before it and checked its size and SHA-256,
    so every earlier chunk is removed then: unpacking needs about one chunk more than the unpacked files."""

    def __init__(self, root, names):
        self.root, self.names, self.removed = root, list(names), 0
        self.order = {name: index for index, name in enumerate(self.names)}

    def joinpath(self, *parts):
        index = self.order['/'.join(parts)]
        for name in self.names[self.removed:index]:
            _remove(self.root.joinpath(*name.split('/')))
        self.removed = max(self.removed, index)
        return self.root.joinpath(*parts)


def _unpack(ctx, root, desired, entry, folder, names, first_stream):
    """Unpack one stream from its checked chunk files with imp_units' own reader and member checks."""
    parts, owner = [], {}
    for part in entry['parts']:
        if part['path'] not in getattr(ctx, 'staged_chunks', {}):
            ctx.journal.note('checked copy missing: ' + part['path'])
            raise ImportFailure('IMPORT_INTERNAL', stage='library')
        for chunk in ctx.staged_chunks[part['path']]:
            parts.append(dict(index=len(parts) + 1, path=chunk['path'], size=chunk['size'], sha256=chunk['sha256']))
            owner[chunk['path']] = part['path']
    disc = ctx.disc
    saved = disc.root
    disc.root = _ChunkRoot(root, [chunk['path'] for chunk in parts])
    try:
        total = imp_units._extract_stream(ctx, desired, dict(entry, parts=parts), folder, names, first_stream)
    except ImportFailure as exc:
        exc.path = owner.get(exc.path, exc.path)
        raise
    finally:
        disc.root = saved
    for chunk in parts:
        _remove(root.joinpath(*chunk['path'].split('/')))
    return total


def run_library(ctx):
    """The Library part, while the knowledge base is in use: the packs' bindings first, then free space for this
    part's copies, every member that is not the knowledge base part's read once and checked, the unpacking margin,
    and the packs and the Cisco titles index installed from the checked copies. It runs at a lower CPU priority, so
    search, Ask and pairing stay quick."""
    m = ctx.manifest
    desired_list = [x for x in ctx.desired if x['unit_kind'] in LIBRARY_KINDS]
    for desired in desired_list:
        if desired['unit_kind'] == 'library':
            for entry in desired['streams']:
                if entry['has_cite_map'] or entry['kb_generation'] is not None:
                    imp_units._binding(ctx, desired, entry['kb_generation'], entry['kb_fingerprint'], unit=entry['pack'])
        else:
            entry = m['cisco_public']
            imp_units._binding(ctx, desired, entry['kb_generation'], entry['kb_fingerprint'])
    _lower_priority(ctx)
    members = library_members(m)
    ctx.library_read = sum(item['size'] for item in members)
    install = sum(x['bytes'] for x in desired_list if not imp_units._current(ctx, x))
    row = _stage(ctx, 'library')
    row.update(done_bytes=0, total_bytes=ctx.library_read + install, done_items=0, total_items=len(members))
    ctx.staged_chunks, ctx.staged_total = {}, 0
    _prepare_walk(ctx, members, LIBRARY_KINDS, 'library_verified', 'library').run()
    _unlisted(ctx)
    ctx.journal.save()
    ctx.report.write()
    crash('CP-library-copied')
    signal_point('CP-library-copied')
    needed = (ctx.staged_total * 5 + 99) // 100
    available = free_bytes(ctx.p.cr)
    ctx.report.data['progress'].update(free_bytes_now=available, needed_bytes=needed)
    ctx.report.write()
    if available < needed:
        raise ImportFailure('IMPORT_SPACE', stage='library', args={'needed_bytes': needed, 'free_bytes': available})
    first_stream = True
    first_group = True
    with _checked_copies(ctx) as root:
        for desired in desired_list:
            check_signal()
            folder = imp_units._prepare(ctx, desired, 'library')
            if folder is None:
                continue
            if desired['unit_kind'] == 'library':
                names, total = set(), 0
                desired['verification'], desired['records'] = [], {}
                for entry in desired['streams']:
                    _time(ctx, entry['pack'], 'install_started')
                    total += _unpack(ctx, root, desired, entry, folder, names, first_stream)
                    first_stream = False
                desired['installed_bytes'] = total
                point = 'CP-library-group-placed' if first_group else 'CP-unused-library-group'
                imp_units._place(ctx, desired, folder, 'library', point)
                first_group = False
            else:
                item = m['cisco_public']
                _time(ctx, 'cisco-public', 'install_started')
                _move(ctx, desired, root.joinpath(*item['path'].split('/')), folder / 'cisco-public.sqlite', 'library')
                imp_units._bytes(ctx, 'library', item['size'])
                imp_units._check_cisco(ctx, desired, folder)
                imp_units._place(ctx, desired, folder, 'library', 'CP-cisco-placed')
            imp_units._state(ctx, desired, 'installed')
            for unit in desired['units']:
                _time(ctx, unit['id'], 'install_finished')
            ctx.report.write()


def run_switch(ctx):
    """Put the Library in use in one step: imp_switch's transaction over every pointer that differs from this disc.
    The kb pointer already names this disc's knowledge base, so only the Library's pointers move and the engine is
    not restarted."""
    _restore_priority(ctx)
    imp_switch.run_switch(ctx)
    keep_way_back(ctx)


def run_check(ctx):
    """Check the Library in use, as imp_switch.run_check does for its units (the knowledge base was checked when it
    went into use). A failure moves the Library's pointers back: imp_switch.rollback restores the registry the switch
    saved, which has this disc's knowledge base, so the knowledge base stays in use."""
    stage = ctx.report.stage('check')
    rows = [x for x in ctx.desired if x['unit_kind'] in LIBRARY_KINDS]
    stage['total_items'], stage['done_items'] = len(rows), 0
    what = imp_switch._services(ctx) if ctx.journal.data.get('switch') else None
    crash('CP-check-mid')
    for row in rows:
        if what:
            break
        unit = row['units'][0]['id']
        try:
            folder = imp_switch._pointer_folder(ctx, row)
            good = folder is not None and not imp_switch._fault('check:' + unit)
            if good and row['unit_kind'] == 'library':
                good = imp_switch._library_check(ctx, row, folder)
            elif good:
                with closing(sqlite3.connect((folder / 'cisco-public.sqlite').resolve().as_uri() +
                                             '?mode=ro&immutable=1', uri=True)) as connection:
                    found = connection.execute("SELECT value FROM meta WHERE key = 'kb_fingerprint'").fetchone()
                good = good and found == (ctx.report.data['kb']['fingerprint'],)
        except (OSError, ValueError, KeyError, sqlite3.Error):
            good = False
        if not good:
            what = unit
            break
        stage['done_items'] += 1
        ctx.report.write()
    if what:
        unit = None if what == 'engine' else what
        error = ctx.report.add_error('IMPORT_CHECK', 'check', unit, None, {'what': what})
        ctx.journal.data['error'] = copy.deepcopy(error)
        ctx.journal.save()
        if ctx.journal.data.get('switch'):
            imp_switch.rollback(ctx)
        ctx.outcome = 'failed'
        raise ImportFailure('IMPORT_CHECK', stage='check', unit=unit, args={'what': what}, resumable=False)
    if ctx.report.data['kb']['meaning_search'] is None:
        imp_switch.meaning_search(ctx)


def _lower_priority(ctx):
    if getattr(ctx, 'priority', None) is not None or not hasattr(os, 'setpriority'):
        return
    try:
        value = os.getpriority(os.PRIO_PROCESS, 0)
        os.setpriority(os.PRIO_PROCESS, 0, min(19, value + NICE))
    except OSError:
        return
    ctx.priority = value


def _restore_priority(ctx):
    """Back to the importer's own priority before the services it restarts (kiwix_serve) start under it."""
    value = getattr(ctx, 'priority', None)
    if value is None:
        return
    with suppress(OSError):
        os.setpriority(os.PRIO_PROCESS, 0, value)
    ctx.priority = None


# ----------------------------------------------------------------------------------- the Workstation's console lines

STATUS = {'c6.import.kb.ready': {}, 'c6.import.failed.library': {}, 'c6.import.files.every': {},
          'c6.import.files.every.n': dict(done=1, total=1), 'c6.import.check.every': {},
          'c6.import.library.pct': dict(pct=1, files=1, count=1, done=1, total=1, packs=1, npacks=1),
          'c6.import.library.eta': dict(pct=1, files=1, count=1, done=1, total=1, packs=1, npacks=1,
                                        left='1 h 00 min')}


def check_strings(strings):
    """Refuse a status table without the Workstation's own lines, as Strings does for the shared ones."""
    try:
        for key, values in STATUS.items():
            strings.get(key, **values)
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError('c6-strings') from exc


def _duration(seconds):
    minutes = max(1, int(seconds + 59) // 60)
    return '%d min' % minutes if minutes < 60 else '%d h %02d min' % divmod(minutes, 60)


class Status(C6Workstation):
    """The Workstation's console lines: C6Workstation's, the Library part's progress (percent, files and size
    checked, packs installed, time left) on each new percent, and a failure that leaves the knowledge base in use
    says so. A disc already in use is checked whole, and its lines say that. The record stays C6's: only msg
    carries the numbers."""

    def _every(self, stage):
        return (stage == 'files' and getattr(self.ctx, 'current', False) or
                stage == 'check' and not getattr(self.ctx, 'kb_live', False))

    def stage(self, stage):
        if not self._every(stage):
            return super().stage(stage)
        self.deciles[stage] = 0
        return self.set('running', self.strings.get('c6.import.' + stage + '.every'))

    def failed(self, code):
        if getattr(self.ctx, 'kb_live', False):
            return self.set('failed', self.strings.get('c6.import.failed.library'), error=code)
        return super().failed(code)

    def progress(self, stage, done, total):
        if stage == 'files' and self._every(stage):
            decile = 10 * done // total if total else 0
            if 1 <= decile <= 9 and decile > self.deciles.get(stage, 0):
                self.deciles[stage] = decile
                self.set('running', self.strings.get('c6.import.files.every.n', done=done // 1048576,
                                                     total=max(1, (total + 1048575) // 1048576)))
            return None
        if stage != 'library' or self.ctx is None:
            return super().progress(stage, done, total)
        percent = 100 * done // total if total else 0
        if not 1 <= percent <= 99 or percent <= self.deciles.get(stage, 0):
            return None
        self.deciles[stage] = percent
        ctx = self.ctx
        row = _stage(ctx, 'library')
        read = getattr(ctx, 'library_read', 0)
        units = [u for u in ctx.report.data['units'] if u['kind'] in ('library', 'index') and u['state'] != 'skipped']
        values = dict(pct=percent, files=row['done_items'] or 0, count=row['total_items'] or 0,
                      done=min(done, read) // 1048576, total=max(1, (read + 1048575) // 1048576),
                      packs=sum(u['state'] in ('installed', 'current', 'kept') for u in units), npacks=len(units))
        rate = ctx.report.data['progress']['rate']
        if rate:
            return self.set('running', self.strings.get('c6.import.library.eta', left=_duration((total - done) / rate),
                                                         **values))
        return self.set('running', self.strings.get('c6.import.library.pct', **values))
