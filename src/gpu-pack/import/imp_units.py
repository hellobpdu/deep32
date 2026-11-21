"""Stage C3 bundles, C5 streams and Forge C4 blocks beside active content.

Public: run_space, run_kb, run_library, run_blocks, reconcile_placing and
verify_desired. Pointer moves belong to the separate switch transaction.
"""

import sys
sys.dont_write_bytecode = True

import errno
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tarfile

from imp_fs import (ImportFailure, atomic_write, check_signal, copy_hashed,
                    crash, free_bytes, remove_tree, set_owner_mode,
                    set_tree_owner_mode, signal_point, sync)
from imp_manifest import safe_member, safe_version
from imp_paths import ENGINE_VERSION, hooks


CHUNK = 1048576


def _safe_arg(value):
    if (isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9._:/+-]{1,128}', value) and
            not re.search(r'[0-9a-fA-F]{32,}', value)):
        return value
    return 'unknown'


def _target_basename(value):
    """Generation targets are one hidden basename, on POSIX and Windows."""
    return (isinstance(value, str) and
            re.fullmatch(r'\.[A-Za-z0-9][A-Za-z0-9._+-]{0,253}', value) is not None)


def _block_version(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z._+-]{0,63}', value) is not None


def _validate_target(ctx, desired, stage=None):
    """Refuse untrusted target strings before any generation path is joined."""
    kind = desired.get('unit_kind')
    target = desired.get('target')
    if kind == 'block':
        block = desired.get('block', {})
        valid = (block.get('name') in ('rfc', 'cisco', 'powershell', 'system') and
                 _block_version(block.get('version')) and
                 target == '.block-' + block['name'] + '-' + block['version'])
    elif kind == 'kb':
        valid = (isinstance(desired.get('generation'), str) and
                 re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,39}', desired['generation']) is not None and
                 target == '.kb-' + desired['generation'])
    elif kind == 'cisco':
        valid = isinstance(target, str) and re.fullmatch(r'\.cisco-[0-9a-f]{16}', target) is not None
    elif kind == 'library':
        valid = isinstance(target, str) and re.fullmatch(r'\.[a-z0-9][a-z0-9._-]{0,199}', target) is not None
    else:
        raise ImportFailure('IMPORT_INTERNAL', stage=stage)
    if not valid or not _target_basename(target):
        raise _failure(ctx, desired, 'IMPORT_KB_FORM' if kind == 'kb' else 'IMPORT_PACK_FORM', stage=stage)


def _stage(ctx, stage):
    return next(row for row in ctx.report.data['stages'] if row['id'] == stage)


def _rows(ctx, desired):
    ids = {x['id'] for x in desired['units']}
    return [row for row in ctx.report.data['units'] if row['id'] in ids]


def _record(ctx, desired):
    return next((row for row in ctx.registry.data['generations'] if row['target'] == desired['target']), None)


def _active_record(ctx, desired):
    active = ctx.registry.data.get('active') or {}
    if desired['pointer'] is None:
        value = desired['target'] if desired['target'] in active.get('targets', []) else None
    else:
        value = active.get('pointers', {}).get(desired['pointer'])
    if not value:
        return None
    return next((row for row in ctx.registry.data['generations'] if row['target'] == value.split('/')[0]), None)


def _current(ctx, desired):
    row = _active_record(ctx, desired)
    return row is not None and row['content_key'] == desired['content_key']


def _disc_path(ctx, desired):
    kind = desired['unit_kind']
    if kind == 'kb':
        return ctx.manifest['kb']['path']
    if kind == 'library':
        return desired['streams'][0]['parts'][0]['path']
    if kind == 'cisco':
        return 'library/cisco-public.sqlite'
    return desired['block']['path']


def _failure(ctx, desired, code, stage=None, path=None, args=None):
    stage = stage or {'kb': 'kb', 'library': 'library', 'cisco': 'library', 'block': 'blocks'}[desired['unit_kind']]
    if path is None and code in ('IMPORT_WRITE', 'IMPORT_PACK_FORM', 'IMPORT_PACK_BINDING'):
        path = _disc_path(ctx, desired)
    return ImportFailure(code, stage=stage, unit=desired['units'][0]['id'], path=path, args=args or {})


def _generation_refs(record):
    return ({value.split('/')[0] for value in (record or {}).get('pointers', {}).values()} |
            set((record or {}).get('targets', [])))


def _registry_add(ctx, desired):
    _validate_target(ctx, desired)
    _owners(ctx, ctx.p.cr / desired['target'], desired['unit_kind'], desired['pointer'])
    fields = ('target', 'unit_kind', 'content_key', 'generation', 'fingerprint', 'units')
    record = {key: desired[key] for key in fields}
    record['bytes'] = desired.get('installed_bytes', desired['bytes'])
    record['import_id'] = ctx.journal.data['import_id']
    ctx.registry.add(record)


def _journal_units(ctx, desired, state):
    for row in _rows(ctx, desired):
        ctx.journal.set_unit(row['id'], state=state, target=desired['target'],
                             content_key=desired['content_key'], bytes=row['bytes'])


def _state(ctx, desired, state):
    for row in _rows(ctx, desired):
        row['state'] = state
        row['parts_checked'] = row['parts']
    ctx.report.write()


def _bytes(ctx, stage, count):
    ctx.report.progress(count, stage=stage)


def _keep(ctx, desired, stage):
    _bytes(ctx, stage, desired['bytes'])
    ctx.report.data['resume']['bytes_kept'] += desired['bytes']
    done = ctx.report.data['resume']['units_done']
    for unit in desired['units']:
        if unit['id'] not in done:
            done.append(unit['id'])


def _setup_stage(ctx, stage, desired):
    row = _stage(ctx, stage)
    total = sum(x['bytes'] for x in desired if not _current(ctx, x))
    count = sum(len(x['units']) for x in desired)
    row.update(done_bytes=0, total_bytes=total, done_items=0, total_items=count)
    ctx.report.write()


def _unit_done(ctx, stage, desired):
    _stage(ctx, stage)['done_items'] += len(desired['units'])
    ctx.report.write()


def _mkdir(ctx, path, desired, stage):
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _failure(ctx, desired, 'IMPORT_WRITE', stage) from exc


def _copy(ctx, item, destination, desired, stage):
    _mkdir(ctx, destination.parent, desired, stage)
    return copy_hashed(ctx.disc.root.joinpath(*item['path'].split('/')), destination,
                       item['size'], item['sha256'], lambda n: _bytes(ctx, stage, n),
                       stage=stage, unit=desired['units'][0]['id'], disc_path=item['path'],
                       content_root=ctx.p.cr)


def _owners(ctx, folder, kind, pointer=None):
    if ctx.p.node == 'forge':
        set_tree_owner_mode(folder, 'root', 'root', 0o555, 0o444)
    else:
        group = '_d32core' if kind == 'kb' else '_kiwix-serve' if pointer == 'encyclopedia' else '_d32users'
        set_tree_owner_mode(folder, 'root', group, 0o550, 0o440)


def _stage_dir(ctx):
    name = ctx.journal.data['stage_dir']
    if not isinstance(name, str) or re.fullmatch(r'\.import-stage-[0-9a-f]{8}', name) is None:
        raise ImportFailure('IMPORT_INTERNAL', stage=getattr(ctx, 'current_stage', None))
    folder = getattr(ctx, 'stage_dir', None)
    if folder is None:
        folder = ctx.p.cr / name
        ctx.stage_dir = folder
    folder = Path(folder)
    if folder != ctx.p.cr / name or folder.is_symlink():
        raise ImportFailure('IMPORT_INTERNAL', stage=getattr(ctx, 'current_stage', None))
    folder.mkdir(parents=True, exist_ok=True)
    set_owner_mode(folder, 'root', 'root' if ctx.p.node == 'forge' else 'wheel', 0o700)
    return folder


def run_space(ctx):
    """Clean only importer-owned generations, then reserve the exact margin."""
    cr = ctx.p.cr
    if not cr.exists():
        if ctx.p.node == 'workstation':
            raise ImportFailure('IMPORT_WRITE', stage='space')
        cr.mkdir(parents=True, exist_ok=True)
        set_owner_mode(cr, 'root', 'root', 0o755)
    if not stat.S_ISDIR(os.lstat(cr).st_mode):
        raise ImportFailure('IMPORT_WRITE', stage='space')
    this_stage = ctx.journal.data['stage_dir']
    for entry in sorted(cr.iterdir(), key=lambda p: p.name):
        if (re.fullmatch(r'\.import-stage-[0-9a-f]{8}', entry.name) and entry.name != this_stage and
                stat.S_ISDIR(os.lstat(entry).st_mode)):
            remove_tree(entry)
    retained = _generation_refs(ctx.registry.data.get('active')) | _generation_refs(ctx.registry.data.get('previous'))
    retained |= {row.get('target') for row in ctx.journal.data['units'].values() if row.get('target')}
    for entry in list(ctx.registry.data['generations']):
        if not _target_basename(entry.get('target')):
            ctx.journal.note('unsafe registry target')
            raise ImportFailure('IMPORT_INTERNAL', stage='space')
        if entry['target'] not in retained:
            path = cr / entry['target']
            if path.exists():
                remove_tree(path)
            ctx.registry.remove(entry['target'])
            from imp_index import library_removed
            library_removed(ctx, entry)
    available = free_bytes(cr)
    needed = 0
    for desired in ctx.desired:
        if _current(ctx, desired):
            continue
        states = [ctx.journal.data['units'].get(x['id'], {}).get('state') for x in desired['units']]
        if states and all(x in ('placed', 'kept') for x in states) and (cr / desired['target']).is_dir():
            continue
        needed += desired['bytes']
    needed = (needed * 105 + 99) // 100
    ctx.report.data['progress'].update(free_bytes_before=available, free_bytes_now=available, needed_bytes=needed)
    ctx.report.write()
    crash('CP-space-cleaned')
    signal_point('CP-space-cleaned')
    if available < needed:
        raise ImportFailure('IMPORT_SPACE', stage='space', args={'needed_bytes': needed, 'free_bytes': available})
    _stage_dir(ctx)


def _engine(ctx, bundle):
    engine = hooks().get('D32_TEST_ENGINE_VERSION') or ENGINE_VERSION
    bounds = bundle.get('engine') if isinstance(bundle, dict) else None
    minimum = bounds.get('min', 'unknown') if isinstance(bounds, dict) else 'unknown'
    maximum = bounds.get('max', 'unknown') if isinstance(bounds, dict) else 'unknown'
    valid = all(isinstance(x, str) and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', x) for x in (engine, minimum, maximum))
    if valid:
        triple = lambda s: tuple(int(x) for x in s.split('.'))
        valid = triple(minimum) <= triple(engine) <= triple(maximum)
    if not valid:
        if ctx.p.node == 'workstation':
            ctx.report.data['kb']['engine_ok'] = False
        raise ImportFailure('IMPORT_KB_ENGINE', stage='kb', unit='kb', args={
            'engine': _safe_arg(engine), 'min': _safe_arg(minimum), 'max': _safe_arg(maximum)})
    return engine


def _read_json(path, code, stage, unit):
    try:
        with open(path, 'rb') as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError('object required')
        return value
    except (OSError, ValueError, UnicodeError) as exc:
        raise ImportFailure(code, stage=stage, unit=unit) from exc


def _check_kb(ctx, folder):
    bundle = _read_json(folder / 'BUNDLE.json', 'IMPORT_KB_FORM', 'kb', 'kb')
    engine = _engine(ctx, bundle)
    argv = ctx.p.command('python') + [str(Path(__file__).with_name('vendor') / 'kb_bundle.py'), 'check',
             '--dir', str(folder), '--public', '--allow-unsigned', '--engine-version', engine]
    for block in ctx.manifest['blocks']:
        argv += ['--block', str(ctx.disc.root.joinpath(*block['path'].split('/')))]
    argv += ['--json', '--report', str(ctx.p.journal / 'kb-check.json')]
    result = ctx.run_child(argv, timeout=1800)
    if result.returncode:
        code = 'IMPORT_KB_FORM' if result.returncode in (3, 4, 5, 6, 7, 8) else 'IMPORT_INTERNAL'
        raise ImportFailure(code, stage='kb', unit='kb' if code != 'IMPORT_INTERNAL' else None)
    if ctx.p.node == 'workstation':
        ctx.report.data['kb']['engine_ok'] = True
    ctx.bundle = bundle
    ctx.report.data['kb']['embedding'] = {key: bundle['embedding'][key] for key in ('model_id', 'revision', 'dimension')}
    ctx.journal.data['bundle'] = bundle
    ctx.journal.save()
    return bundle


def _check_cisco(ctx, desired, folder):
    path = folder / 'cisco-public.sqlite'
    try:
        uri = path.resolve().as_uri() + '?mode=ro&immutable=1'
        connection = sqlite3.connect(uri, uri=True)
        try:
            values = {}
            for key in ('kb_fingerprint', 'kb_generation'):
                row = connection.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
                if row is None:
                    raise ValueError('missing meta')
                values[key] = row[0]
        finally:
            connection.close()
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise _failure(ctx, desired, 'IMPORT_PACK_FORM') from exc
    _binding(ctx, desired, values['kb_generation'], values['kb_fingerprint'])


def _check_block(ctx, desired, folder):
    block = desired['block']
    if not _block_version(block['version']):
        raise _failure(ctx, desired, 'IMPORT_PACK_FORM')
    try:
        data = _read_json(folder / 'block.json', 'IMPORT_PACK_FORM', 'blocks', desired['units'][0]['id'])
    except ImportFailure as exc:
        exc.path = block['path']
        raise
    if data.get('version') != block['version']:
        raise _failure(ctx, desired, 'IMPORT_PACK_FORM')
    argv = ctx.p.command('python') + [str(Path(__file__).with_name('vendor') / 'validate_block.py'), str(folder)]
    result = ctx.run_child(argv, timeout=1800)
    if result.returncode:
        if result.returncode == 3:
            raise _failure(ctx, desired, 'IMPORT_PACK_FORM')
        raise ImportFailure('IMPORT_INTERNAL', stage='blocks')


def _hash_regular(path):
    if not stat.S_ISREG(os.lstat(path).st_mode):
        raise OSError('not regular')
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(fd, 'rb') as handle:
        count = 0
        while True:
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            count += len(chunk)
            digest.update(chunk)
    return count, digest.hexdigest()


def verify_desired(ctx, desired):
    """Verify a placed generation without following its public pointer."""
    _validate_target(ctx, desired)
    folder = ctx.p.cr / desired['target']
    kind = desired['unit_kind']
    if not stat.S_ISDIR(os.lstat(folder).st_mode):
        raise _failure(ctx, desired, 'IMPORT_KB_FORM' if kind == 'kb' else 'IMPORT_PACK_FORM')
    if kind == 'kb':
        nested = folder / ctx.manifest['kb']['generation']
        if _hash_regular(nested / 'MANIFEST.json')[1] != desired['content_key']:
            raise _failure(ctx, desired, 'IMPORT_KB_FORM')
        _check_kb(ctx, nested)
    elif kind == 'library':
        verification = desired.get('verification', [])
        for item in verification:
            size, digest = _hash_regular(folder.joinpath(*item['path'].split('/')))
            if size != item['size'] or digest != item['sha256']:
                raise _failure(ctx, desired, 'IMPORT_PACK_FORM')
        if verification:
            actual = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file() or p.is_symlink()}
            if actual != {x['path'] for x in verification}:
                raise _failure(ctx, desired, 'IMPORT_PACK_FORM')
        else:
            _verify_library_records(ctx, desired, folder)
    elif kind == 'cisco':
        if _hash_regular(folder / 'cisco-public.sqlite')[1] != desired['content_key']:
            raise _failure(ctx, desired, 'IMPORT_PACK_FORM')
        _check_cisco(ctx, desired, folder)
    else:
        nested = folder / desired['block']['name']
        if _hash_regular(nested / 'block.json')[1] != desired['content_key']:
            raise _failure(ctx, desired, 'IMPORT_PACK_FORM')
        _check_block(ctx, desired, nested)
    return True


def _write_records(ctx, desired):
    for pack, record in desired.get('records', {}).items():
        folder = ctx.p.packs / pack / record['generation']
        folder.mkdir(parents=True, exist_ok=True)
        group = 'root' if ctx.p.node == 'forge' else 'wheel'
        set_owner_mode(ctx.p.packs, 'root', group, 0o755)
        set_owner_mode(ctx.p.packs / pack, 'root', group, 0o755)
        for name in ('MANIFEST.tsv', 'INFO'):
            atomic_write(folder / name, record[name].encode('utf-8'), 0o444, owner='root', group=group)
        set_tree_owner_mode(folder, 'root', group, 0o755, 0o444)


def reconcile_placing(ctx):
    """Adopt rename-complete generations; partial stage copies are redone."""
    old_desired = ctx.journal.data.get('desired', [])
    ctx.adopted = []
    for desired in ctx.desired:
        previous = next((x for x in old_desired if x['target'] == desired['target'] and
                         x['content_key'] == desired['content_key']), None)
        if previous:
            for key in ('records', 'verification', 'installed_bytes'):
                if key in previous:
                    desired[key] = previous[key]
        states = [ctx.journal.data['units'].get(x['id'], {}).get('state') for x in desired['units']]
        if not any(x in ('placing', 'placed', 'kept') for x in states):
            continue
        _validate_target(ctx, desired)
        folder = ctx.p.cr / desired['target']
        if not folder.exists():
            continue
        try:
            verify_desired(ctx, desired)
        except (ImportFailure, OSError, ValueError):
            refs = _generation_refs(ctx.registry.data.get('active')) | _generation_refs(ctx.registry.data.get('previous'))
            removed = False
            if desired['target'] not in refs:
                remove_tree(folder)
                removed = True
                if ctx.registry.owned(desired['target']):
                    ctx.registry.remove(desired['target'])
            _journal_units(ctx, desired, 'checked')
            if removed:
                from imp_index import library_removed
                library_removed(ctx, desired)
        else:
            _write_records(ctx, desired)
            _registry_add(ctx, desired)
            _journal_units(ctx, desired, 'placed')
            ctx.adopted.append(desired['target'])
    ctx.journal.data['desired'] = ctx.desired
    ctx.journal.save()


def _prepare(ctx, desired, stage):
    """Return a fresh stage folder, or None when current or safely reused."""
    _validate_target(ctx, desired, stage)
    if _current(ctx, desired):
        active = _active_record(ctx, desired)
        value = (active['target'] if desired['pointer'] is None else
                 ctx.registry.data['active']['pointers'][desired['pointer']])
        desired['target'], desired['value'] = active['target'], value
        _validate_target(ctx, desired, stage)
        _state(ctx, desired, 'current')
        _journal_units(ctx, desired, 'current')
        return None
    target = ctx.p.cr / desired['target']
    if target.exists() or target.is_symlink():
        record = _record(ctx, desired)
        states = [ctx.journal.data['units'].get(x['id'], {}).get('state') for x in desired['units']]
        if ((record and record['content_key'] == desired['content_key']) or
                any(x == 'placing' for x in states)):
            verify_desired(ctx, desired)
            _write_records(ctx, desired)
            _registry_add(ctx, desired)
            _journal_units(ctx, desired, 'placed')
            _state(ctx, desired, 'kept' if ctx.p.node == 'forge' and desired['unit_kind'] != 'kb' else 'installed')
            _keep(ctx, desired, stage)
            return None
        if record:
            refs = _generation_refs(ctx.registry.data.get('active')) | _generation_refs(ctx.registry.data.get('previous'))
            if desired['target'] in refs:
                ctx.journal.note('generation name reused with other content')
                raise _failure(ctx, desired, 'IMPORT_KB_FORM' if desired['unit_kind'] == 'kb' else 'IMPORT_PACK_FORM')
            remove_tree(target)
            ctx.registry.remove(desired['target'])
            from imp_index import library_removed
            library_removed(ctx, record)
        else:
            ctx.journal.note('unowned generation collision: ' + desired['target'])
            raise _failure(ctx, desired, 'IMPORT_WRITE')
    folder = _stage_dir(ctx) / desired['target']
    if folder.exists():
        remove_tree(folder)
    folder.mkdir(parents=True)
    _state(ctx, desired, 'installing')
    return folder


def _place(ctx, desired, folder, stage, point):
    _validate_target(ctx, desired, stage)
    _owners(ctx, folder, desired['unit_kind'], desired['pointer'])
    sync()
    ctx.journal.data['desired'] = ctx.desired
    _journal_units(ctx, desired, 'placing')
    if desired['unit_kind'] == 'kb':
        crash('CP-kb-placing')
        signal_point('CP-kb-placing')
    try:
        os.rename(folder, ctx.p.cr / desired['target'])
    except OSError as exc:
        raise _failure(ctx, desired, 'IMPORT_WRITE', stage) from exc
    crash(point)
    signal_point(point)
    _write_records(ctx, desired)
    _registry_add(ctx, desired)
    _journal_units(ctx, desired, 'placed')


def _qdrant_load(ctx, desired):
    options = ctx.options
    url = options.get('qdrant_url') if isinstance(options, dict) else getattr(options, 'qdrant_url', None)
    if not url:
        _state(ctx, desired, 'kept')
        return
    bundle = ctx.p.cr / desired['value']
    export = _read_json(bundle / 'dense' / 'EXPORT.json', 'IMPORT_INTERNAL', 'kb', None)
    collection = export.get('collection')
    if not isinstance(collection, str):
        raise ImportFailure('IMPORT_INTERNAL', stage='kb')
    argv = ctx.p.command('python') + [str(Path(__file__).with_name('vendor') / 'kb_qdrant.py'), 'load',
             '--bundle', str(bundle), '--qdrant-url', url, '--collection', collection,
             '--allow-unsigned', '--report', str(ctx.p.journal / 'qdrant-load.json'), '--json']
    result = ctx.run_child(argv, timeout=7200)
    if result.returncode == 0:
        crash('CP-qdrant-loaded')
        signal_point('CP-qdrant-loaded')
    elif result.returncode == 11:
        argv[4] = 'check'
        argv[argv.index('--report') + 1] = str(ctx.p.journal / 'qdrant-check-existing.json')
        result = ctx.run_child(argv, timeout=1800)
        if result.returncode:
            raise ImportFailure('IMPORT_INTERNAL', stage='kb')
    else:
        raise ImportFailure('IMPORT_INTERNAL', stage='kb')
    ctx.journal.data['collection'] = collection
    ctx.journal.save()
    ctx.report.data['kb']['engine_ok'] = True
    _state(ctx, desired, 'installed')


def run_kb(ctx):
    desired = next(x for x in ctx.desired if x['unit_kind'] == 'kb')
    _setup_stage(ctx, 'kb', [desired])
    members = [x for x in ctx.manifest['members'] if x['path'].startswith(ctx.manifest['kb']['path'] + '/')]
    _stage(ctx, 'kb')['total_items'] = len(members)
    folder = _prepare(ctx, desired, 'kb')
    if folder is not None:
        generation = ctx.manifest['kb']['generation']
        for index, item in enumerate(members, 1):
            check_signal()
            relative = item['path'][len('kb/' + generation + '/'):]
            _copy(ctx, item, folder / generation / relative, desired, 'kb')
            _stage(ctx, 'kb')['done_items'] = index
            if index == (len(members) + 1) // 2:
                crash('CP-kb-mid')
                signal_point('CP-kb-mid')
        sync()
        _check_kb(ctx, folder / generation)
        crash('CP-kb-staged')
        signal_point('CP-kb-staged')
        _place(ctx, desired, folder, 'kb', 'CP-kb-placed')
        _state(ctx, desired, 'installed')
    else:
        if not hasattr(ctx, 'bundle'):
            _check_kb(ctx, ctx.p.cr / desired['value'])
    if ctx.p.node == 'forge':
        _qdrant_load(ctx, desired)
    _stage(ctx, 'kb')['total_items'] = len([x for x in ctx.manifest['members'] if x['path'].startswith(ctx.manifest['kb']['path'] + '/')])
    _stage(ctx, 'kb')['done_items'] = _stage(ctx, 'kb')['total_items']
    ctx.report.write()


def _binding(ctx, desired, generation, fingerprint, unit=None):
    kb = ctx.manifest['kb']
    if generation != kb['generation'] or fingerprint != kb['fingerprint']:
        failure = _failure(ctx, desired, 'IMPORT_PACK_BINDING', args={
            'pack_fingerprint': _safe_arg(fingerprint or 'UNBOUND'), 'kb_fingerprint': kb['fingerprint']})
        if unit is not None:
            failure.unit = unit
            entry = next(x for x in desired['streams'] if x['pack'] == unit)
            failure.path = entry['parts'][0]['path']
        raise failure


class PartReader(io.RawIOBase):
    """A bounded joined stream with independent part and raw-ustar checks."""

    def __init__(self, ctx, entry):
        self.ctx, self.entry = ctx, entry
        self.index, self.handle = 0, None
        self.part_digest, self.part_bytes = None, 0
        self.digest, self.count = hashlib.sha256(), 0
        self.raw_buffer, self.remaining, self.zeros = b'', 0, 0
        self.trailer = False
        self.headers = []

    def readable(self):
        return True

    def _form(self):
        raise ImportFailure('IMPORT_PACK_FORM', stage='library', unit=self.entry['pack'], path=self.entry['parts'][0]['path'])

    def _raw(self, value):
        self.raw_buffer += value
        while len(self.raw_buffer) >= 512:
            block, self.raw_buffer = self.raw_buffer[:512], self.raw_buffer[512:]
            if self.remaining:
                self.remaining -= 1
                continue
            if block == b'\0' * 512:
                self.trailer = True
                self.zeros += 1
                continue
            if self.trailer or block[257:263] != b'ustar\0' or block[263:265] != b'00' or block[156:157] not in (b'0', b'\0'):
                self._form()
            try:
                header = tarfile.TarInfo.frombuf(block, 'utf-8', 'strict')
            except (tarfile.TarError, ValueError, UnicodeError):
                self._form()
            if not header.isfile() or header.pax_headers:
                self._form()
            self.headers.append(header)
            self.remaining = (header.size + 511) // 512

    def _next(self):
        if self.handle is not None:
            self.handle.close()
            part = self.entry['parts'][self.index - 1]
            if self.part_bytes != part['size'] or self.part_digest.hexdigest() != part['sha256']:
                raise ImportFailure('IMPORT_FILE_HASH', stage='library', unit=self.entry['pack'], path=part['path'])
        if self.index >= len(self.entry['parts']):
            self.handle = None
            return False
        part = self.entry['parts'][self.index]
        path = self.ctx.disc.root.joinpath(*part['path'].split('/'))
        try:
            if not stat.S_ISREG(os.lstat(path).st_mode):
                raise OSError('not regular')
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
            self.handle = os.fdopen(fd, 'rb')
        except OSError as exc:
            raise ImportFailure('IMPORT_READ', stage='library', unit=self.entry['pack'], path=part['path']) from exc
        self.index += 1
        self.part_digest, self.part_bytes = hashlib.sha256(), 0
        return True

    def read(self, size=-1):
        if size == 0:
            return b''
        if size < 0:
            chunks = []
            while True:
                chunk = self.read(CHUNK)
                if not chunk:
                    return b''.join(chunks)
                chunks.append(chunk)
        result = bytearray()
        while len(result) < size:
            check_signal()
            if self.handle is None and not self._next():
                break
            part = self.entry['parts'][self.index - 1]
            try:
                if 'read:' + part['path'] in hooks().get('faults', ()):
                    raise OSError(errno.EIO, 'test read')
                chunk = self.handle.read(size - len(result))
            except OSError as exc:
                raise ImportFailure('IMPORT_READ', stage='library', unit=self.entry['pack'], path=part['path']) from exc
            if not chunk:
                if not self._next():
                    break
                continue
            self.part_digest.update(chunk)
            self.part_bytes += len(chunk)
            self.digest.update(chunk)
            self.count += len(chunk)
            self._raw(chunk)
            _bytes(self.ctx, 'library', len(chunk))
            result.extend(chunk)
        return bytes(result)

    def finish(self):
        while self.read(CHUNK):
            pass
        if self.raw_buffer or self.remaining or self.zeros < 2:
            self._form()
        if self.count != self.entry['stream_bytes'] or self.digest.hexdigest() != self.entry['stream_sha256']:
            raise ImportFailure('IMPORT_FILE_HASH', stage='library', unit=self.entry['pack'], path='library/' + self.entry['pack'] + '/')

    def close(self):
        if self.handle:
            self.handle.close()
        super().close()


def _pack_form(entry):
    return ImportFailure('IMPORT_PACK_FORM', stage='library', unit=entry['pack'], path=entry['parts'][0]['path'])


def _tar_name(name):
    return (isinstance(name, str) and '\\' not in name and len(name.encode('utf-8')) <= 1024 and
            not name.startswith('/') and all(component not in ('', '.', '..') for component in name.split('/')) and
            all(ord(ch) >= 32 and ord(ch) != 127 for ch in name))


def _record_checks(ctx, desired, entry, metadata, record_bytes):
    try:
        manifest_raw, info_raw = record_bytes['MANIFEST.tsv'], record_bytes['INFO']
        if manifest_raw.startswith(b'\xef\xbb\xbf') or b'\r' in manifest_raw or not manifest_raw.endswith(b'\n'):
            raise ValueError('record form')
        lines = manifest_raw.decode('utf-8').splitlines()
        # Only the pack's own record manifest is outside its coverage; a content file may also be named MANIFEST.tsv.
        record_manifest = 'var/db/d32/packs/' + entry['pack'] + '/MANIFEST.tsv'
        covered = [x for x in metadata if x['name'] != record_manifest]
        if len(lines) != len(covered):
            raise ValueError('coverage')
        for line, member in zip(lines, covered):
            fields = line.split('\t')
            if (len(fields) != 8 or fields[0] != member['name'] or fields[1] != 'f' or
                    not re.fullmatch(r'[0-7]{4}', fields[2]) or int(fields[2], 8) != member['mode'] or
                    fields[3] != member['uname'] or fields[4] != member['gname'] or
                    not fields[5].isdecimal() or int(fields[5]) != member['size'] or
                    not re.fullmatch(r'[0-9a-f]{64}', fields[6]) or fields[6] != member['sha256'] or
                    not fields[7] or any(ord(x) < 32 or ord(x) == 127 for x in fields[7])):
                raise ValueError('row')
        if b'\r' in info_raw or not info_raw.endswith(b'\n'):
            raise ValueError('info form')
        info = {}
        for line in info_raw.decode('utf-8').splitlines():
            key, value = line.split('=', 1)
            if key in info:
                raise ValueError('duplicate')
            info[key] = value
        required = {'name', 'class', 'release', 'rights', 'files', 'bytes', 'made'}
        optional = {'generation', 'kb_generation', 'kb_fingerprint'}
        content = metadata[:-2]
        map_name = entry['install_prefix'] + '/cite-map.tsv'
        has_cite_map = any(member['name'] == map_name for member in content)
        if has_cite_map != entry['has_cite_map']:
            raise ValueError('cite map inventory')
        if (not required <= set(info) or set(info) - required - optional or info['name'] != entry['pack'] or
                info['class'] != 'library' or info['release'] != entry['release'] or not info['rights'] or
                not info['files'].isdecimal() or int(info['files']) != len(content) or
                not info['bytes'].isdecimal() or int(info['bytes']) != sum(x['size'] for x in content) or
                not info['made'].isdecimal() or int(info['made']) != metadata[0]['mtime'] or
                ('generation' in info and info['generation'] != entry['generation']) or
                (('kb_generation' in info) != ('kb_fingerprint' in info))):
            raise ValueError('info values')
        generation, fingerprint = info.get('kb_generation'), info.get('kb_fingerprint')
        if has_cite_map:
            _binding(ctx, desired, generation, fingerprint, unit=entry['pack'])
        if entry['kb_generation'] is None:
            if (generation, fingerprint) not in ((None, None), ('UNBOUND', 'UNBOUND')):
                raise ValueError('unexpected binding')
        else:
            _binding(ctx, desired, generation, fingerprint, unit=entry['pack'])
            if generation != entry['kb_generation'] or fingerprint != entry['kb_fingerprint']:
                raise ValueError('entry binding')
        desired.setdefault('records', {})[entry['pack']] = dict(generation=entry['generation'],
            **{'MANIFEST.tsv': manifest_raw.decode('utf-8'), 'INFO': info_raw.decode('utf-8')})
    except (ValueError, UnicodeError, KeyError, IndexError) as exc:
        raise _pack_form(entry) from exc


def _extract_stream(ctx, desired, entry, folder, names, first_stream=False):
    reader = PartReader(ctx, entry)
    metadata, record_bytes = [], {}
    prefix = entry['install_prefix'] + '/'
    record_prefix = 'var/db/d32/packs/' + entry['pack'] + '/'
    last_content = ''
    mtime = None
    midpoint = False
    member_total = ctx.journal.data.get('stream_members', {}).get(entry['pack'])
    try:
        archive = tarfile.open(fileobj=reader, mode='r|', encoding='utf-8', errors='strict')
        try:
            for member in archive:
                check_signal()
                name = member.name
                if (not member.isfile() or member.pax_headers or not _tar_name(name) or name in names or
                        member.uid != 0 or member.gid != 0 or member.uname != 'root' or
                        member.gname not in ('_d32users', 'wheel') or member.mode & 0o022):
                    raise _pack_form(entry)
                names.add(name)
                if mtime is None:
                    mtime = member.mtime
                if member.mtime != mtime:
                    raise _pack_form(entry)
                record = name in (record_prefix + 'MANIFEST.tsv', record_prefix + 'INFO')
                if not record:
                    if record_bytes or not name.startswith(prefix) or name <= last_content:
                        raise _pack_form(entry)
                    last_content = name
                    relative = name[len(prefix):]
                    if not _tar_name(relative):
                        raise _pack_form(entry)
                    destination = folder.joinpath(*relative.split('/'))
                    _mkdir(ctx, destination.parent, desired, 'library')
                source = archive.extractfile(member)
                if source is None:
                    raise _pack_form(entry)
                digest, count = hashlib.sha256(), 0
                output = None
                held = bytearray()
                try:
                    if not record:
                        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
                        fd = os.open(destination, flags, 0o600)
                        output = os.fdopen(fd, 'wb')
                    while True:
                        check_signal()
                        chunk = source.read(CHUNK)
                        if not chunk:
                            break
                        digest.update(chunk)
                        count += len(chunk)
                        if output:
                            relative_cr = destination.relative_to(ctx.p.cr).as_posix()
                            if any(relative_cr == f[6:] or relative_cr.startswith(f[6:].rstrip('/') + '/')
                                   for f in hooks().get('faults', ()) if f.startswith('write:')):
                                raise OSError(errno.EIO, 'test write')
                            output.write(chunk)
                        else:
                            held.extend(chunk)
                    if output:
                        output.flush()
                        os.fsync(output.fileno())
                except OSError as exc:
                    raise _failure(ctx, desired, 'IMPORT_WRITE') from exc
                finally:
                    source.close()
                    if output:
                        output.close()
                if count != member.size:
                    raise _pack_form(entry)
                item = dict(name=name, size=count, sha256=digest.hexdigest(), mode=member.mode,
                            uname=member.uname, gname=member.gname, mtime=member.mtime)
                metadata.append(item)
                if record:
                    record_bytes[name.rsplit('/', 1)[-1]] = bytes(held)
                else:
                    desired.setdefault('verification', []).append(dict(path=relative, size=count, sha256=digest.hexdigest()))
                halfway = (member_total + 1) // 2 if member_total else None
                if first_stream and not midpoint and ((halfway and len(metadata) >= halfway) or
                        (halfway is None and reader.count >= entry['stream_bytes'] // 2)):
                    midpoint = True
                    crash('CP-library-mid')
                    signal_point('CP-library-mid')
        finally:
            archive.close()
        reader.finish()
        if (len(metadata) < 2 or metadata[-2]['name'] != record_prefix + 'MANIFEST.tsv' or
                metadata[-1]['name'] != record_prefix + 'INFO'):
            raise _pack_form(entry)
        _record_checks(ctx, desired, entry, metadata, record_bytes)
        return sum(x['size'] for x in metadata[:-2])
    except (tarfile.TarError, UnicodeError, ValueError) as exc:
        raise _pack_form(entry) from exc
    finally:
        reader.close()


def _verify_library_records(ctx, desired, folder):
    for entry in desired['streams']:
        record_folder = ctx.p.packs / entry['pack'] / entry['generation']
        try:
            lines = (record_folder / 'MANIFEST.tsv').read_text(encoding='utf-8').splitlines()
            for line in lines:
                fields = line.split('\t')
                if len(fields) != 8:
                    raise ValueError('record')
                prefix = entry['install_prefix'] + '/'
                if fields[0].startswith(prefix):
                    relative = fields[0][len(prefix):]
                    if not _tar_name(relative):
                        raise ValueError('record path')
                    size, digest = _hash_regular(folder.joinpath(*relative.split('/')))
                    if str(size) != fields[5] or digest != fields[6]:
                        raise ValueError('record hash')
        except (OSError, UnicodeError, ValueError) as exc:
            raise _pack_form(entry) from exc


def run_library(ctx):
    desired_list = [x for x in ctx.desired if x['unit_kind'] in ('library', 'cisco')]
    if ctx.p.node != 'workstation':
        return
    for desired in desired_list:
        if desired['unit_kind'] == 'library':
            for entry in desired['streams']:
                if entry['has_cite_map'] or entry['kb_generation'] is not None:
                    _binding(ctx, desired, entry['kb_generation'], entry['kb_fingerprint'], unit=entry['pack'])
        else:
            entry = ctx.manifest['cisco_public']
            _binding(ctx, desired, entry['kb_generation'], entry['kb_fingerprint'])
    _setup_stage(ctx, 'library', desired_list)
    first_stream = True
    first_group = True
    for desired in desired_list:
        check_signal()
        folder = _prepare(ctx, desired, 'library')
        if folder is None:
            _unit_done(ctx, 'library', desired)
            continue
        if desired['unit_kind'] == 'library':
            names, total = set(), 0
            desired['verification'], desired['records'] = [], {}
            for entry in desired['streams']:
                total += _extract_stream(ctx, desired, entry, folder, names, first_stream)
                first_stream = False
            desired['installed_bytes'] = total
            point = 'CP-library-group-placed' if first_group else 'CP-unused-library-group'
            _place(ctx, desired, folder, 'library', point)
            first_group = False
        else:
            item = ctx.manifest['cisco_public']
            _copy(ctx, item, folder / 'cisco-public.sqlite', desired, 'library')
            _check_cisco(ctx, desired, folder)
            _place(ctx, desired, folder, 'library', 'CP-cisco-placed')
        _state(ctx, desired, 'installed')
        _unit_done(ctx, 'library', desired)


def run_blocks(ctx):
    if ctx.p.node != 'forge':
        return
    desired_list = [x for x in ctx.desired if x['unit_kind'] == 'block']
    for desired in desired_list:
        _validate_target(ctx, desired, 'blocks')
    _setup_stage(ctx, 'blocks', desired_list)
    first = True
    for desired in desired_list:
        check_signal()
        folder = _prepare(ctx, desired, 'blocks')
        if folder is not None:
            block = desired['block']
            for item in ctx.manifest['members']:
                if item['path'].startswith('corpus/' + block['name'] + '/'):
                    relative = item['path'][len('corpus/' + block['name'] + '/'):]
                    _copy(ctx, item, folder / block['name'] / relative, desired, 'blocks')
            if first:
                crash('CP-blocks-mid')
                signal_point('CP-blocks-mid')
                first = False
            _check_block(ctx, desired, folder / block['name'])
            _place(ctx, desired, folder, 'blocks', 'CP-unused-block-placed')
            _state(ctx, desired, 'kept')
        _unit_done(ctx, 'blocks', desired)
