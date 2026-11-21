"""Switch, check and restore generations under C3, C4, C5 and C6.

The deep32-import-report contract supplies states and errors. Public functions:
read_pointer, move_pointer, already_current, run_switch, run_check, rollback,
reconcile_switch, meaning_search and finish.
"""

import copy
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys

from imp_paths import ENGINE_VERSION, hooks, iso, test_mode
from imp_fs import ImportFailure, crash, ignore_signals, log, remove_tree, sync


def _fault(value):
    return value in hooks().get('faults', ())


def read_pointer(path):
    """Read either an atomic relative symlink or the test linkfile backend."""
    path = Path(path)
    try:
        if path.is_symlink():
            value = os.readlink(path)
        elif test_mode() and hooks().get('D32_TEST_POINTERS') == 'linkfile':
            value = path.read_text(encoding='ascii')
            if not value.endswith('\n') or value.count('\n') != 1:
                return None
            value = value[:-1]
        else:
            return None
    except (OSError, UnicodeError):
        return None
    if (not value or value.startswith('/') or '\\' in value or
            any(part in ('', '.', '..') for part in value.split('/'))):
        return None
    return value


def move_pointer(path, value, fault=True):
    """Replace a pointer in one rename; null restores an absent old pointer."""
    path = Path(path)
    if fault and _fault('pointer:' + path.name):
        raise OSError('pointer-fault')
    if value is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    if (value.startswith('/') or '\\' in value or
            any(part in ('', '.', '..') for part in value.split('/'))):
        raise ValueError('unsafe-pointer')
    temporary = path.with_name(path.name + '.new')
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
    if test_mode() and hooks().get('D32_TEST_POINTERS') == 'linkfile':
        with temporary.open('xb') as stream:
            stream.write((value + '\n').encode('ascii'))
            stream.flush()
            os.fsync(stream.fileno())
    else:
        if os.name == 'nt':
            os.symlink(value, temporary, target_is_directory=True)
        else:
            os.symlink(value, temporary)
    os.replace(temporary, path)
    if hasattr(os, 'O_DIRECTORY'):
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _records(ctx):
    return getattr(ctx, 'desired', None) or ctx.journal.data.get('desired', [])


def _unit(record):
    return record['units'][0]['id']


def _targets(record):
    return ({value.split('/')[0] for value in (record or {}).get('pointers', {}).values()} |
            set((record or {}).get('targets', [])))


def _present(ctx, record):
    if not record:
        return False
    return (all(read_pointer(ctx.p.cr / pointer) == value and
               (ctx.p.cr / value).is_dir()
                for pointer, value in record.get('pointers', {}).items()) and
            all((ctx.p.cr / target).is_dir() and not (ctx.p.cr / target).is_symlink()
                for target in record.get('targets', [])))


def already_current(ctx):
    active = ctx.registry.active
    return bool(active and active.get('manifest_sha256') ==
                ctx.report.data['source']['manifest_sha256'] and _present(ctx, active))


def _commit_registry(ctx):
    switch = ctx.journal.data['switch']
    old = switch['saved_registry']['active']
    active = {'manifest_sha256': ctx.journal.data['manifest_sha256'],
              'import_id': ctx.import_id,
              'disc_version': ctx.report.data['source']['disc_version'],
              'pointers': {row['pointer']: row['value'] for row in _records(ctx) if row['pointer'] is not None}}
    targets = sorted(row['target'] for row in _records(ctx) if row['pointer'] is None)
    if targets:
        active['targets'] = targets
    if switch['pointers'] or targets != (old or {}).get('targets', []):
        ctx.registry.previous = copy.deepcopy(old)
    ctx.registry.active = active
    ctx.registry.save()


def run_switch(ctx):
    pointers = []
    for row in _records(ctx):
        if row['pointer'] is None:
            continue
        old = read_pointer(ctx.p.cr / row['pointer'])
        if old != row['value']:
            pointers.append({'pointer': row['pointer'], 'old': old,
                             'new': row['value'], 'moved': False, 'moved_back': False})
    switch = {'phase': 'begun',
              'saved_registry': {'active': copy.deepcopy(ctx.registry.active),
                                 'previous': copy.deepcopy(ctx.registry.previous)},
              'pointers': pointers}
    ignore_signals(True)
    try:
        ctx.journal.data['switch'] = switch
        ctx.journal.save()
        crash('CP-switch-begun')
        stage = ctx.report.stage('switch')
        stage['total_items'], stage['done_items'] = len(pointers), 0
        _forward(ctx)
        _commit_registry(ctx)
    finally:
        ignore_signals(False)


def _forward(ctx):
    switch = ctx.journal.data['switch']
    for index, entry in enumerate(switch['pointers']):
        try:
            if read_pointer(ctx.p.cr / entry['pointer']) != entry['new']:
                move_pointer(ctx.p.cr / entry['pointer'], entry['new'])
            entry['moved'] = True
            ctx.journal.save()
            log(ctx.p, ctx.import_id, 'pointer', pointer=entry['pointer'], state='new')
            if hasattr(ctx, 'report'):
                ctx.report.stage('switch')['done_items'] = index + 1
                ctx.report.write()
            if index == 0:
                crash('CP-switch-mid')
        except OSError as exc:
            for previous in reversed(switch['pointers'][:index]):
                move_pointer(ctx.p.cr / previous['pointer'], previous['old'], fault=False)
                previous['moved_back'] = True
                ctx.journal.save()
            failed = next((row for row in _records(ctx)
                           if row['pointer'] == entry['pointer']), None)
            ctx.journal.note('pointer-failed:' + entry['pointer'] + ':' + type(exc).__name__)
            raise ImportFailure('IMPORT_SWITCH', stage='switch',
                                unit=_unit(failed) if failed else None) from exc
    switch['phase'] = 'done'
    ctx.journal.save()
    crash('CP-switch-done')


def _services(ctx, restoring=False):
    if ctx.p.node != 'workstation':
        return None
    switch = ctx.journal.data.get('switch') or {}
    moved = {row['pointer'] for row in switch.get('pointers', [])}
    if not moved:
        return None
    try:
        lines = ctx.p.services_file.read_text(encoding='ascii').splitlines()
    except (OSError, UnicodeError):
        lines = []
    words = []
    for line in lines:
        match = re.match(r'^\s*pkg_scripts\s*=\s*(.*?)\s*$', line)
        if match:
            words = match.group(1).split('#', 1)[0].strip().strip('"\'').split()
            break
    commands = []
    if 'kb' in moved and 'd32core' in words:
        commands += [('restart', 'd32core', 'engine'), ('check', 'd32core', 'engine')]
    if 'encyclopedia' in moved and 'kiwix_serve' in words:
        row = next(row for row in _records(ctx) if row['pointer'] == 'encyclopedia')
        commands.append(('restart', 'kiwix_serve', _unit(row)))
    for action, service, what in commands:
        result = ctx.command('rcctl', [action, service])
        log(ctx.p, ctx.import_id, 'service', action=action, service=service, exit=result.returncode)
        if result.returncode and not restoring:
            return what
    return None


def _pointer_folder(ctx, row):
    if row['pointer'] is None:
        folder = ctx.p.cr / row['target']
        return folder if folder.is_dir() and not folder.is_symlink() else None
    path = ctx.p.cr / row['pointer']
    if read_pointer(path) != row['value']:
        return None
    folder = ctx.p.cr / row['value']
    if not folder.is_dir() or folder.is_symlink():
        return None
    if path.is_symlink() and os.path.realpath(path) != os.path.realpath(folder):
        return None
    return folder


def _sha(path):
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1048576), b''):
            digest.update(chunk)
        return digest.hexdigest()


def _regular(path, size=None):
    try:
        info = path.lstat()
        return stat.S_ISREG(info.st_mode) and (size is None or info.st_size == size)
    except OSError:
        return False


def _library_check(ctx, row, folder):
    for stream in row.get('streams', []):
        path = ctx.p.packs / stream['pack'] / stream['generation'] / 'MANIFEST.tsv'
        prefix = stream['install_prefix'] + '/'
        for line in path.read_text(encoding='utf-8').splitlines():
            fields = line.split('\t')
            if len(fields) != 8:
                return False
            if not fields[0].startswith(prefix):
                continue
            relative = fields[0][len(prefix):]
            if any(part in ('', '.', '..') for part in relative.split('/')):
                return False
            member = folder.joinpath(*relative.split('/'))
            if not _regular(member, int(fields[5])):
                return False
            if relative in ('PACK.json', 'cite-map.tsv') and _sha(member) != fields[6]:
                return False
    catalogue = folder / 'catalogue.sqlite'
    if catalogue.exists():
        with closing(sqlite3.connect(catalogue.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as connection:
            connection.execute('SELECT count(*) FROM sqlite_master').fetchone()
    return True


def _kb_check(ctx, row, folder):
    engine = hooks().get('D32_TEST_ENGINE_VERSION') or ENGINE_VERSION
    vendor = Path(__file__).with_name('vendor')
    argv = [sys.executable, '-I', '-B', str(vendor / 'kb_bundle.py')]
    result = ctx.run_child(argv + ['check', '--dir', str(folder), '--public',
                                  '--allow-unsigned', '--engine-version', engine, '--json'], timeout=1800)
    if result.returncode:
        return False
    result = ctx.run_child(argv + ['info', '--dir', str(folder), '--c5'], timeout=600)
    expected = 'kb_generation=' + row['generation'] + '\nkb_fingerprint=' + row['fingerprint'] + '\n'
    if result.returncode or result.stdout != expected or result.stderr:
        return False
    ctx.bundle = json.loads((folder / 'BUNDLE.json').read_text(encoding='ascii'))
    ctx.report.data['kb']['embedding'] = {key: ctx.bundle['embedding'][key]
                                         for key in ('model_id', 'revision', 'dimension')}
    if ctx.p.node == 'forge' and ctx.options.get('qdrant_url'):
        collection = json.loads((folder / 'dense' / 'EXPORT.json').read_text(encoding='ascii'))['collection']
        result = ctx.run_child([sys.executable, '-I', '-B', str(vendor / 'kb_qdrant.py'),
                               'check', '--bundle', str(folder), '--qdrant-url', ctx.options['qdrant_url'],
                               '--collection', collection, '--allow-unsigned', '--report',
                               str(ctx.p.journal / 'qdrant-check.json'), '--json'], timeout=1800)
        if result.returncode:
            return False
        ctx.report.data['kb']['engine_ok'] = True
    elif ctx.p.node == 'workstation':
        ctx.report.data['kb']['engine_ok'] = True
    return True


def run_check(ctx):
    stage = ctx.report.stage('check')
    rows = _records(ctx)
    stage['total_items'], stage['done_items'] = len(rows), 0
    what = None if getattr(ctx, 'current', False) else _services(ctx)
    failed_unit = None if what == 'engine' else what
    crash('CP-check-mid')
    for row in rows:
        if what:
            break
        unit = _unit(row)
        label = 'engine' if unit == 'kb' else unit
        try:
            folder = _pointer_folder(ctx, row)
            good = folder is not None and not _fault('check:' + unit)
            if good and row['unit_kind'] == 'kb':
                good = _kb_check(ctx, row, folder)
            elif good and row['unit_kind'] == 'library':
                good = _library_check(ctx, row, folder)
            elif good and row['unit_kind'] == 'cisco':
                with closing(sqlite3.connect((folder / 'cisco-public.sqlite').resolve().as_uri() +
                                             '?mode=ro&immutable=1', uri=True)) as connection:
                    found = connection.execute("SELECT value FROM meta WHERE key = 'kb_fingerprint'").fetchone()
                    good = found == (ctx.report.data['kb']['fingerprint'],)
            elif good and row['unit_kind'] == 'block':
                good = _sha(folder / 'block.json') == row['content_key']
            if not good:
                what, failed_unit = label, None if unit == 'kb' else unit
                break
        except (OSError, ValueError, KeyError, sqlite3.Error):
            what, failed_unit = label, None if unit == 'kb' else unit
            break
        stage['done_items'] += 1
        ctx.report.write()
    if what:
        error = ctx.report.add_error('IMPORT_CHECK', 'check', failed_unit, None, {'what': what})
        ctx.journal.data['error'] = copy.deepcopy(error)
        ctx.journal.save()
        if not getattr(ctx, 'current', False) and ctx.journal.data.get('switch'):
            rollback(ctx)
            ctx.outcome = 'rolled-back'
        else:
            ctx.outcome = 'failed'
        raise ImportFailure('IMPORT_CHECK', stage='check', unit=failed_unit,
                            args={'what': what}, resumable=False)
    meaning_search(ctx)


def rollback(ctx):
    ignore_signals(True)
    try:
        _rollback(ctx)
    finally:
        ignore_signals(False)


def _rollback(ctx):
    switch = ctx.journal.data['switch']
    switch['phase'] = 'rollback-begun'
    ctx.journal.save()
    for index, entry in enumerate(reversed(switch['pointers'])):
        if not entry.get('moved_back'):
            try:
                move_pointer(ctx.p.cr / entry['pointer'], entry['old'], fault=False)
            except OSError as exc:
                log(ctx.p, ctx.import_id, 'rollback', pointer=entry['pointer'], error=type(exc).__name__)
                ctx.journal.note('rollback-incomplete:' + entry['pointer'])
                raise
            entry['moved_back'] = True
            ctx.journal.save()
            log(ctx.p, ctx.import_id, 'rollback', pointer=entry['pointer'])
        if index == 0:
            crash('CP-rollback-mid')
    _services(ctx, restoring=True)
    ctx.registry.active = copy.deepcopy(switch['saved_registry']['active'])
    ctx.registry.previous = copy.deepcopy(switch['saved_registry']['previous'])
    ctx.registry.save()
    switch['phase'] = 'rolled-back'
    ctx.journal.save()
    ctx.report.data['rollback']['done'] = True


def reconcile_switch(ctx):
    """Finish the durable switch or rollback before reading another disc."""
    switch = ctx.journal.data.get('switch')
    if not switch or ctx.journal.data.get('outcome') is not None:
        return False
    if switch['phase'] == 'rollback-begun':
        rollback(ctx)
        ctx.outcome = 'rolled-back'
        return True
    if switch['phase'] in ('begun', 'done'):
        ignore_signals(True)
        try:
            _forward(ctx)
            _commit_registry(ctx)
        finally:
            ignore_signals(False)
        return True
    return False


def meaning_search(ctx):
    if ctx.p.node != 'workstation':
        if ctx.report.data['kb']:
            ctx.report.data['kb']['meaning_search'] = None
        return
    wanted = (getattr(ctx, 'bundle', {}) or {}).get('embedding')
    if wanted is None:
        wanted = ctx.report.data['kb'].get('embedding')
    if not wanted:
        raise ValueError('missing-embedding')
    wanted = {key: wanted[key] for key in ('model_id', 'revision', 'dimension')}
    served = None
    try:
        lines = ctx.p.embedding_file.read_text(encoding='ascii').splitlines()
        if len(lines) == 3:
            names = ('model_id', 'revision', 'dimension')
            values = []
            for name, line in zip(names, lines):
                if not line.startswith(name + '='):
                    break
                value = line[len(name) + 1:]
                if not re.fullmatch(r'[A-Za-z0-9._:/+-]{1,128}', value):
                    break
                values.append(value)
            if len(values) == 3 and values[2].isdecimal():
                served = dict(model_id=values[0], revision=values[1], dimension=int(values[2]))
    except (OSError, UnicodeError):
        pass
    if served is None:
        ctx.journal.note('forge-embedding:missing-or-malformed')
        ctx.report.data['kb']['meaning_search'] = 'waiting-forge'
        ctx.report.add_warning('IMPORT_MEANING_WAIT', 'check')
    elif served == wanted:
        ctx.report.data['kb']['meaning_search'] = 'on'
    else:
        ctx.report.data['kb']['meaning_search'] = 'off-mismatch'
        def short(value):
            joined = str(value['model_id']) + ':' + str(value['revision'])
            return re.sub(r'[0-9a-fA-F]{32,}', lambda match: match.group(0)[:12], joined)[:128]
        ctx.journal.note('forge-embedding-mismatch:' + json.dumps(served, sort_keys=True))
        ctx.report.add_warning('IMPORT_MEANING_MISMATCH', 'check',
                               args={'served': short(served), 'wanted': short(wanted)})


def rollback_details(ctx):
    previous = ctx.registry.previous
    report = ctx.report.data
    report['rollback']['available'] = bool(previous and all(
        (ctx.p.cr / target).is_dir() for target in _targets(previous)))
    report['rollback']['kb_generation'] = None
    report['rollback']['library'] = {}
    if report['kb']:
        report['kb']['previous'] = None
    for row in ctx.registry.data.get('generations', []):
        if row['target'] not in _targets(previous):
            continue
        if row['unit_kind'] == 'kb':
            report['rollback']['kb_generation'] = row['generation']
            if report['kb']:
                report['kb']['previous'] = {'generation': row['generation'],
                                             'fingerprint': row['fingerprint']}
        elif row['unit_kind'] == 'library':
            for unit in row['units']:
                report['rollback']['library'][unit['id']] = unit['release']


def _cleanup(ctx, outcome, resumable):
    if resumable:
        return
    if ctx.stage_dir.exists():
        remove_tree(ctx.stage_dir)
    if ctx.report.data['stage'] == 'disc' and outcome == 'failed':
        return
    protected = _targets(ctx.registry.active) | _targets(ctx.registry.previous)
    ids = set(ctx.journal.data.get('import_ids', [])) | {
        ctx.journal.data.get('first_import_id'), ctx.import_id}
    index_error = None
    for row in list(ctx.registry.data.get('generations', [])):
        if row['target'] in protected:
            continue
        if outcome not in ('imported', 'rolled-back') and row.get('import_id') not in ids:
            continue
        record_paths = []
        if row['unit_kind'] == 'library':
            for unit in row.get('units', []):
                if (not isinstance(unit, dict) or not isinstance(unit.get('id'), str) or
                        re.fullmatch(r'lib-[a-z0-9-]{1,32}', unit['id']) is None or
                        not isinstance(unit.get('generation'), str) or
                        re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}', unit['generation']) is None):
                    raise ImportFailure('IMPORT_INTERNAL', stage=ctx.report.data['stage'])
                records = ctx.p.packs / unit['id'] / unit['generation']
                if ctx.p.packs.is_symlink() or not records.resolve().is_relative_to(ctx.p.packs.resolve()):
                    raise ImportFailure('IMPORT_INTERNAL', stage=ctx.report.data['stage'])
                record_paths.append(records)
        folder = ctx.p.cr / row['target']
        if folder.exists():
            remove_tree(folder)
        for records in record_paths:
            if records.exists():
                remove_tree(records)
        ctx.registry.remove(row['target'])
        ctx.registry.save()
        from imp_index import library_removed
        try:
            library_removed(ctx, row)
        except ImportFailure as exc:
            index_error = exc
    if outcome == 'failed' and ctx.journal.data.get('registry_before') is not None:
        ctx.registry.data = copy.deepcopy(ctx.journal.data['registry_before'])
        if ctx.journal.data.get('registry_existed_before'):
            ctx.registry.save()
        else:
            ctx.p.registry_file.unlink(missing_ok=True)
    if index_error is not None:
        raise index_error


def finish(ctx, outcome='imported', resumable=False):
    """Commit final machine state, keep history and release the mounted disc."""
    from imp_disc import unmount
    try:
        _cleanup(ctx, outcome, resumable)
    except ImportFailure as exc:
        if not ctx.report.data['errors']:
            raise
        ctx.report.add_error(exc.code, ctx.report.data['stage'], exc.unit, exc.path, exc.arguments)
    if outcome in ('imported', 'already-current'):
        from imp_index import regenerate
        regenerate(ctx)
    rollback_details(ctx)
    previous_targets = _targets(ctx.registry.previous)
    old_bytes = sum(row['bytes'] for row in ctx.registry.data.get('generations', [])
                    if row['target'] in previous_targets and (ctx.p.cr / row['target']).is_dir())
    if ctx.registry.previous:
        ctx.report.add_warning('IMPORT_OLD_KEPT', ctx.report.data['stage'], args={'bytes': old_bytes})
    ctx.journal.data['outcome'] = outcome
    ctx.journal.data['finished_at'] = iso()
    ctx.journal.save()
    ctx.report.data['rollback']['done'] = outcome == 'rolled-back'
    ctx.report.finish(outcome, resumable=resumable,
                      next_stage=ctx.report.data['stage'] if resumable else None)
    ctx.report.history()
    crash('CP-finish-report')
    unmount(ctx)
    if outcome in ('imported', 'already-current'):
        ctx.c6.success(ctx.report.data['source']['disc_version'], current=outcome == 'already-current')
        ctx.c6.ready()
    else:
        ctx.c6.failed(ctx.report.data['errors'][0]['code'])
    log(ctx.p, ctx.import_id, 'finish', outcome=outcome)
    return 0 if outcome in ('imported', 'already-current') else 2
