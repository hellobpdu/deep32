"""Run the C2-C6 disc importer and deep32-import-report state machine.

Public functions: parse_args and main. All child processes use fixed command
tables. Durable journal records govern resumption, switching and rollback.
"""

import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import copy
import hashlib
import json
import os
import re
import subprocess
import time
from types import SimpleNamespace

import imp_paths
import imp_fs
from imp_fs import ImportFailure, Interrupted, log
from imp_journal import Journal, Registry, Lock, LockBusy, prepare
from imp_report import Report
from imp_c6 import Strings, C6Workstation, C6Forge
import imp_disc
import imp_manifest
import imp_files
import imp_units
import imp_switch


USAGE = {
    'workstation': 'usage: d32-content-import --device cdN | --resume | --report',
    'forge': 'usage: d32-content-import (--device srN | --folder DIR | --resume) [--qdrant-url URL] | --report'}
STDERR = {
    'root': 'd32-content-import: run as root',
    'strings': 'd32-content-import: the status texts are missing or incomplete',
    'resume': 'd32-content-import: there is no import to carry on',
    'report': 'd32-content-import: there is no import report',
    'busy': 'd32-content-import: another import is running'}
STAGES = ('disc', 'signature', 'files', 'space', 'kb', 'library', 'blocks',
          'switch', 'check', 'finish')


class UsageError(Exception):
    def __init__(self, node='workstation'):
        self.node = node


def parse_args(argv):
    """Parse the closed CLI before any root, strings, state or lock operation."""
    result = {'node': None, 'mode': None, 'device': None, 'folder': None,
              'qdrant_url': None}
    seen = set()
    index = 0
    while index < len(argv):
        key = argv[index]
        if key not in ('--node', '--device', '--folder', '--resume', '--report', '--qdrant-url') or key in seen:
            raise UsageError(result['node'] or 'workstation')
        seen.add(key)
        if key in ('--resume', '--report'):
            if result['mode'] is not None:
                raise UsageError(result['node'] or 'workstation')
            result['mode'] = key[2:]
            index += 1
            continue
        if index + 1 >= len(argv) or argv[index + 1].startswith('--'):
            raise UsageError(result['node'] or 'workstation')
        value = argv[index + 1]
        if key == '--node':
            if value not in ('workstation', 'forge'):
                raise UsageError()
            result['node'] = value
        elif key == '--qdrant-url':
            result['qdrant_url'] = value
        else:
            if result['mode'] is not None:
                raise UsageError(result['node'] or 'workstation')
            result['mode'] = key[2:]
            result[key[2:]] = value
        index += 2
    node = result['node'] or 'workstation'
    if result['node'] is None or result['mode'] is None:
        raise UsageError(node)
    if result['mode'] == 'device' and not re.fullmatch(
            r'cd[0-9]' if node == 'workstation' else r'sr[0-9]', result['device']):
        raise UsageError(node)
    if result['mode'] == 'folder':
        if node != 'forge' or not Path(result['folder']).is_absolute() or not Path(result['folder']).is_dir():
            raise UsageError(node)
    url = result['qdrant_url']
    if url is not None:
        match = re.fullmatch(r'http://(?:127\.0\.0\.1|localhost|\[::1\]):([0-9]+)', url)
        if node != 'forge' or result['mode'] == 'report' or not match or not 1 <= int(match.group(1)) <= 65535:
            raise UsageError(node)
    return result


def _stderr(key):
    sys.stderr.buffer.write((STDERR[key] + '\n').encode('ascii'))


def _usage(node):
    sys.stderr.buffer.write((USAGE[node] + '\n').encode('ascii'))


def _children(ctx):
    def run_child(argv, timeout=600):
        try:
            process = subprocess.Popen([str(x) for x in argv], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, encoding='utf-8', errors='replace')
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                try:
                    stdout, stderr = process.communicate(timeout=max(0.001, min(10, remaining)))
                    result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
                    break
                except subprocess.TimeoutExpired:
                    if remaining <= 0:
                        process.kill()
                        stdout, stderr = process.communicate()
                        result = subprocess.CompletedProcess(argv, 124, stdout, stderr)
                        break
                    if ctx.report is not None and ctx.report.data['state'] == 'running':
                        ctx.report.heartbeat(force=True)
        except OSError:
            result = subprocess.CompletedProcess(argv, 127, '', '')
        script = next((Path(str(x)).name for x in argv[1:]
                       if str(x).endswith('.py')), Path(str(argv[0])).name)
        if ctx.journal.data is not None and ctx.import_id != '-':
            ctx.journal.child(script, result.returncode, result.stdout + result.stderr)
        log(ctx.p, ctx.import_id, 'child', program=script, exit=result.returncode)
        return result
    ctx.run_child = run_child
    ctx.command = lambda name, args, timeout=600: run_child(ctx.p.command(name) + list(args), timeout)


def _stage_functions(ctx):
    def begin(stage, total_bytes=None, total_items=None):
        imp_fs.check_signal()
        ctx.current_stage = stage
        ctx.report.begin_stage(stage, total_bytes=total_bytes, total_items=total_items)
        value = ctx.report.stage(stage)
        ctx.journal.set_stage(stage, 'running', started=value['started'])
        log(ctx.p, ctx.import_id, 'stage-start', stage=stage)
        ctx.c6.stage(stage)
        imp_fs.internal_fault(stage)
    def end(stage, state='done'):
        ctx.report.end_stage(stage, state=state)
        value = ctx.report.stage(stage)
        ctx.journal.set_stage(stage, state, started=value['started'], finished=value['finished'])
        log(ctx.p, ctx.import_id, 'stage-done', stage=stage, state=state)
    ctx.begin, ctx.end = begin, end
    progress = ctx.report.progress
    def moving(n, stage=None, items=0):
        value = progress(n, stage=stage, items=items)
        active = stage or ctx.report.data['stage']
        row = ctx.report.stage(active)
        ctx.c6.progress(active, row['done_bytes'] or 0, row['total_bytes'] or 0)
        return value
    ctx.report.progress = moving


def _history_before_identity(paths):
    try:
        raw = paths.report.read_bytes()
        previous = json.loads(raw.decode('ascii'))
        name = previous.get('import_id', '')
        if re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}', name):
            target = paths.history / (name + '.json')
            if not target.exists():
                imp_fs.atomic_write(target, raw, paths.report_mode,
                                    group=paths.state_group)
    except (OSError, ValueError, UnicodeError):
        pass


def _resume_source(options, journal):
    data = journal.data
    if options['mode'] != 'resume':
        return
    options['device'] = data.get('device')
    if options['device'] == 'folder':
        source = data.get('source_folder')
        if source is None:
            raise UsageError(options['node'])
        options['folder'] = str(Path(imp_paths.hooks()['D32_ROOT']) / source) if imp_paths.test_mode() else source
    if options['qdrant_url'] is not None and options['qdrant_url'] != data.get('qdrant_url'):
        raise UsageError(options['node'])
    options['qdrant_url'] = data.get('qdrant_url')


def _resume_engine(ctx, old):
    """Apply the stored engine endpoint to explicit and implicit resumes."""
    if ctx.p.node != 'forge':
        return
    stored = old.get('qdrant_url')
    supplied = ctx.options.get('qdrant_url')
    if supplied is not None and supplied != stored:
        raise UsageError('forge')
    ctx.options['qdrant_url'] = stored


def _identity(ctx, old, resume, disc_started):
    _history_before_identity(ctx.p)
    ctx.import_id = imp_paths.new_import_id()
    previous = old.get('import_id') if resume else None
    while previous == ctx.import_id:
        ctx.import_id = imp_paths.new_import_id()
    if resume:
        ctx.journal.data = copy.deepcopy(old)
        ctx.journal.start_attempt(ctx.import_id)
        if old.get('outcome') == 'failed':
            ctx.journal.data['switch'] = None
        ctx.journal.data.setdefault('import_ids', [old.get('first_import_id')])
        ctx.journal.data['import_ids'].append(ctx.import_id)
    else:
        if old and old.get('stage_dir') and ctx.p.cr.exists():
            abandoned = ctx.p.cr / old['stage_dir']
            if abandoned.name.startswith('.import-stage-') and abandoned.exists():
                imp_fs.remove_tree(abandoned)
        ctx.journal.new(manifest_sha256=getattr(ctx, 'manifest_sha256', None),
                        device=ctx.disc.device, import_id=ctx.import_id,
                        qdrant_url=ctx.options.get('qdrant_url'))
        ctx.journal.data['import_ids'] = [ctx.import_id]
        ctx.journal.data['registry_before'] = copy.deepcopy(ctx.registry.data)
        ctx.journal.data['registry_existed_before'] = ctx.p.registry_file.exists()
    if ctx.options.get('folder'):
        folder = ctx.options['folder']
        ctx.journal.data['source_folder'] = (os.path.relpath(folder, imp_paths.hooks()['D32_ROOT']).replace('\\', '/')
                                               if imp_paths.test_mode() else folder)
    ctx.stage_dir = ctx.p.cr / ctx.journal.data['stage_dir']
    ctx.journal.save()
    ctx.report = Report(ctx.p, ctx.import_id, ctx.disc.device,
                        attempt=ctx.journal.data['attempt'], resumed_from=previous, started=disc_started)
    if resume:
        ctx.report.add_warning('IMPORT_RESUMED', 'disc', args={'from': previous}, write=False)
    ctx.report.data['source']['manifest_sha256'] = getattr(ctx, 'manifest_sha256', None)
    ctx.report.stage('disc').update(state='running', started=disc_started)
    ctx.report.data['stage'] = 'disc'
    _stage_functions(ctx)
    ctx.report.write()
    log(ctx.p, ctx.import_id, 'start', attempt=ctx.journal.data['attempt'])


def _carry_stage(ctx, stage, old):
    saved = old.get('stages', {}).get(stage, {})
    row = ctx.report.stage(stage)
    row.update(state='done', started=saved.get('started') or ctx.report.data['started'],
               finished=saved.get('finished') or saved.get('started') or ctx.report.data['started'])
    if stage in ('files', 'kb', 'library', 'blocks'):
        row['done_bytes'] = row['total_bytes'] = 0
    ctx.journal.set_stage(stage, 'done', started=row['started'], finished=row['finished'])
    ctx.report.write()


def _skip(ctx, stage):
    ctx.report.end_stage(stage, state='skipped')
    ctx.journal.set_stage(stage, 'skipped')


def _run_stage(ctx, stage, function):
    ctx.begin(stage)
    function(ctx)
    ctx.end(stage)
    if stage in ('signature', 'files'):
        imp_fs.crash('CP-' + stage + '-done')


def _carry_units(ctx, stage, old):
    kinds = {'kb': ('kb',), 'library': ('library', 'cisco'), 'blocks': ('block',)}
    wanted = [row for row in ctx.desired if row['unit_kind'] in kinds[stage]]
    active = ctx.registry.active or {}
    amount = 0
    count = 0
    for desired in wanted:
        active_target = (desired['target'] if desired['pointer'] is None and
                         desired['target'] in active.get('targets', []) else
                         active.get('pointers', {}).get(desired['pointer'], '').split('/')[0])
        active_row = ctx.registry.generation(active_target)
        current = active_row is not None and active_row['content_key'] == desired['content_key']
        for unit in desired['units']:
            row = ctx.report.unit(unit['id'])
            if current:
                row['state'] = 'current'
            elif desired['unit_kind'] == 'block' or ctx.p.node == 'forge' and not ctx.options['qdrant_url']:
                row['state'] = 'kept'
            else:
                row['state'] = 'installed'
            row['parts_checked'] = row['parts']
            if unit['id'] not in ctx.report.data['resume']['units_done']:
                ctx.report.data['resume']['units_done'].append(unit['id'])
        if not current:
            amount += desired['bytes']
        count += len(desired['units'])
        if desired['unit_kind'] == 'kb':
            ctx.bundle = json.loads((ctx.p.cr / desired['value'] / 'BUNDLE.json').read_text(encoding='ascii'))
            ctx.report.data['kb']['embedding'] = {key: ctx.bundle['embedding'][key]
                                                 for key in ('model_id', 'revision', 'dimension')}
            if ctx.p.node == 'workstation' or ctx.options['qdrant_url']:
                ctx.report.data['kb']['engine_ok'] = True
    _carry_stage(ctx, stage, old)
    ctx.report.stage(stage).update(done_bytes=amount, total_bytes=amount,
                                   done_items=count, total_items=count)
    ctx.report.data['progress']['done_bytes'] += amount
    ctx.report.data['resume']['bytes_kept'] += amount
    ctx.report.write()


def _restore_report(ctx, old):
    """Restore verified identity for a switch resume without reading the disc."""
    manifest = old.get('manifest')
    if not manifest:
        manifest = json.loads((ctx.p.journal / 'manifest.json').read_text(encoding='ascii'))
    ctx.manifest = manifest
    ctx.manifest_sha256 = old['manifest_sha256']
    ctx.desired = old.get('desired', [])
    source = ctx.report.data['source']
    source.update(manifest_sha256=old['manifest_sha256'], signature='good',
                  signer=manifest['signer']['fingerprint'], disc_version=manifest['disc']['version'],
                  disc_bytes=manifest['counts']['bytes'])
    kb = manifest['kb']
    ctx.report.data['kb'].update(generation=kb['generation'], fingerprint=kb['fingerprint'], created=kb['created'],
                               counts={key: kb[key] for key in ('documents', 'chunks', 'points')})
    imp_manifest.build_units(ctx)
    for unit in ctx.report.data['units']:
        if unit['state'] != 'skipped':
            unit['state'] = 'kept' if ctx.p.node == 'forge' and unit['kind'] in ('kb', 'block') else 'installed'
            unit['parts_checked'] = unit['parts']
    bundle_row = next(row for row in ctx.desired if row['unit_kind'] == 'kb')
    ctx.bundle = json.loads((ctx.p.cr / bundle_row['value'] / 'BUNDLE.json').read_text(encoding='ascii'))
    ctx.report.data['kb']['embedding'] = {key: ctx.bundle['embedding'][key]
                                         for key in ('model_id', 'revision', 'dimension')}
    ctx.report.data['progress']['total_bytes'] = manifest['counts']['bytes'] + sum(
        unit['bytes'] for unit in ctx.report.data['units'] if unit['state'] != 'skipped')
    ctx.report.data['progress']['done_bytes'] = ctx.report.data['progress']['total_bytes']
    for stage in STAGES[:8]:
        if stage == 'library' and ctx.p.node == 'forge' or stage == 'blocks' and ctx.p.node == 'workstation':
            _skip(ctx, stage)
        else:
            _carry_stage(ctx, stage, old)
    ctx.report.write()


def _switch_recovery(ctx, old, started):
    _resume_engine(ctx, old)
    ctx.disc = imp_disc.Disc(ctx.p.mount, old['device'], mounted=(ctx.p.journal / 'mounted').exists())
    ctx.manifest_sha256 = old['manifest_sha256']
    _identity(ctx, old, True, started)
    _restore_report(ctx, old)
    log(ctx.p, ctx.import_id, 'reconcile', phase=old['switch']['phase'])
    rollback_pending = old['switch']['phase'] == 'rollback-begun'
    imp_switch.reconcile_switch(ctx)
    if rollback_pending:
        error = old.get('error')
        if not error:
            raise ImportFailure('IMPORT_INTERNAL', stage='check')
        ctx.current_stage = 'check'
        ctx.report.begin_stage('check')
        restored_error = ctx.report.add_error(error['code'], error['stage'], error['unit'], error['path'], error['args'])
        ctx.journal.data['error'] = copy.deepcopy(restored_error)
        ctx.journal.save()
        return imp_switch.finish(ctx, 'rolled-back', False)
    _run_stage(ctx, 'check', imp_switch.run_check)
    ctx.begin('finish')
    ctx.end('finish')
    return imp_switch.finish(ctx, 'imported')


def _run(ctx):
    old = copy.deepcopy(ctx.journal.data or {})
    started = imp_paths.iso()
    switch = old.get('switch')
    if switch and switch.get('phase') in ('begun', 'done', 'rollback-begun') and old.get('outcome') is None:
        return _switch_recovery(ctx, old, started)
    ctx.current_stage = 'disc'
    ctx.c6.stage('disc')
    log(ctx.p, '-', 'stage-start', stage='disc')
    disc_error = None
    try:
        imp_disc.run_disc(ctx)
    except ImportFailure as exc:
        disc_error = exc
    resumable = ctx.journal.resumable()
    resume = bool(resumable and (disc_error or not old.get('manifest_sha256') or
                                old['manifest_sha256'] == ctx.manifest_sha256))
    if resume:
        _resume_engine(ctx, old)
    _identity(ctx, old, resume, started)
    if disc_error:
        raise disc_error
    ctx.end('disc')
    _run_stage(ctx, 'signature', imp_manifest.run_signature)
    if resume and old.get('stages', {}).get('files', {}).get('state') == 'done':
        _carry_stage(ctx, 'files', old)
        amount = ctx.manifest['counts']['bytes']
        ctx.report.data['progress']['done_bytes'] = amount
        ctx.report.stage('files')['done_bytes'] = ctx.report.stage('files')['total_bytes'] = amount
        ctx.report.stage('files')['done_items'] = ctx.report.stage('files')['total_items'] = len(ctx.manifest['members'])
        imp_files.initialise_progress(ctx)
        for unit in ctx.report.data['units']:
            if unit['state'] != 'skipped':
                unit['state'] = 'checked'
                unit['parts_checked'] = unit['parts']
        ctx.report.write()
    else:
        _run_stage(ctx, 'files', imp_files.run_files)
    ctx.current = imp_switch.already_current(ctx)
    if ctx.current:
        for unit in ctx.report.data['units']:
            if unit['state'] != 'skipped':
                unit['state'] = 'current'
        for stage in ('space', 'kb', 'library', 'blocks', 'switch'):
            if stage == 'library' and ctx.p.node == 'forge' or stage == 'blocks' and ctx.p.node == 'workstation':
                _skip(ctx, stage)
            else:
                ctx.report.stage(stage).update(state='done', started=imp_paths.iso(), finished=imp_paths.iso())
                if stage in ('kb', 'library', 'blocks'):
                    ctx.report.stage(stage).update(done_bytes=0, total_bytes=0, done_items=0, total_items=0)
                ctx.journal.set_stage(stage, 'done', started=ctx.report.stage(stage)['started'],
                                      finished=ctx.report.stage(stage)['finished'])
        ctx.report.write()
    else:
        if hasattr(imp_units, 'reconcile_placing'):
            imp_units.reconcile_placing(ctx)
        for stage, function in (('space', imp_units.run_space), ('kb', imp_units.run_kb),
                                ('library', imp_units.run_library), ('blocks', imp_units.run_blocks),
                                ('switch', imp_switch.run_switch)):
            if stage == 'library' and ctx.p.node == 'forge' or stage == 'blocks' and ctx.p.node == 'workstation':
                _skip(ctx, stage)
                continue
            if resume and stage in ('space', 'kb', 'library', 'blocks') and old.get('stages', {}).get(stage, {}).get('state') == 'done':
                stage_kinds = {'kb': ('kb',), 'library': ('library', 'cisco'), 'blocks': ('block',)}
                desired = [row for row in ctx.desired if row['unit_kind'] in stage_kinds.get(stage, ())]
                if stage == 'space':
                    _carry_stage(ctx, stage, old)
                    continue
                if all(all(ctx.journal.data['units'].get(unit['id'], {}).get('state') in
                           ('placed', 'current', 'kept') for unit in row['units']) for row in desired):
                    _carry_units(ctx, stage, old)
                    continue
            _run_stage(ctx, stage, function)
    _run_stage(ctx, 'check', imp_switch.run_check)
    ctx.begin('finish')
    ctx.end('finish')
    return imp_switch.finish(ctx, 'already-current' if ctx.current else 'imported')


def _failure(ctx, exc):
    stage = exc.stage or ctx.current_stage
    if ctx.report is None or ctx.journal is None or ctx.journal.data is None:
        # preserve the original failure before durable reporting exists.
        sys.stderr.buffer.write(('d32-content-import: ' + exc.code + '\n').encode('ascii'))
        try:
            imp_disc.unmount(ctx)
        except Exception:
            pass
        return 2
    args = getattr(exc, 'arguments', {})
    if not (ctx.report.data['errors'] and (getattr(exc, 'recorded', False) or
            ctx.report.data['errors'][-1]['code'] == exc.code and ctx.report.data['errors'][-1]['stage'] == stage)):
        ctx.report.add_error(exc.code, stage, exc.unit, exc.path, args)
    error = ctx.report.data['errors'][0]
    ctx.journal.data['error'] = copy.deepcopy(error)
    ctx.journal.set_stage(stage, 'failed', finished=imp_paths.iso())
    ctx.report.data['resume']['units_done'] = [unit['id'] for unit in ctx.report.data['units']
                                             if ctx.journal.data.get('units', {}).get(unit['id'], {}).get('state')
                                             in ('checked', 'placed', 'current', 'kept')]
    log(ctx.p, ctx.import_id, 'stage-failed', stage=stage, code=error['code'])
    return imp_switch.finish(ctx, getattr(ctx, 'outcome', 'failed'), exc.resumable)


def main(argv=None):
    try:
        options = parse_args(sys.argv[1:] if argv is None else argv)
    except UsageError as exc:
        _usage(exc.node)
        return 1
    paths = imp_paths.workstation() if options['node'] == 'workstation' else imp_paths.forge()
    if not imp_paths.test_mode() and (not hasattr(os, 'geteuid') or os.geteuid() != 0):
        _stderr('root')
        return 1
    if options['mode'] == 'report':
        try:
            raw = paths.report.read_bytes()
        except OSError:
            _stderr('report')
            return 2
        sys.stdout.buffer.write(raw)
        return 0
    try:
        strings = Strings()
    except ValueError:
        _stderr('strings')
        return 1
    journal = Journal(paths, load=False)
    registry = Registry(paths, load=False)
    load_error = None
    try:
        journal.load()
        registry.load()
    except Exception as exc:
        load_error = exc
        # Nothing from invalid ownership metadata may reach a cleanup routine.
        journal = Journal(paths, load=False)
        registry = Registry(paths, load=False)
    if load_error is None and options['mode'] == 'resume' and not journal.resumable():
        _stderr('resume')
        return 1
    try:
        if load_error is None:
            _resume_source(options, journal)
    except UsageError:
        _usage(options['node'])
        return 1
    ctx = SimpleNamespace(p=paths, paths=paths, options=options, journal=journal,
                          registry=registry, report=None, import_id='-', current_stage='disc',
                          desired=[], bundle=None, current=False)
    _children(ctx)
    lock = Lock(paths)
    try:
        lock.acquire()
    except LockBusy:
        _stderr('busy')
        return 3
    except (ImportFailure, OSError) as exc:
        try:
            lock.release()
        except (ImportFailure, OSError):
            pass
        failure = exc if isinstance(exc, ImportFailure) else ImportFailure('IMPORT_WRITE', stage='disc')
        return _failure(ctx, failure)
    try:
        try:
            prepare(paths)
            if paths.history.is_symlink():
                raise ImportFailure('IMPORT_WRITE')
            paths.history.mkdir(parents=True, exist_ok=True)
            if not paths.history.is_mount():
                imp_fs.set_owner_mode(paths.history, 'root', paths.state_group, paths.state_mode)
            if paths.node == 'forge':
                paths.cr.mkdir(parents=True, exist_ok=True)
                paths.status_file.parent.mkdir(parents=True, exist_ok=True)
            ctx.c6 = (C6Workstation if paths.node == 'workstation' else C6Forge)(ctx, strings=strings)
            imp_fs.install_signal_handlers()
        except ImportFailure as exc:
            return _failure(ctx, exc)
        except OSError:
            return _failure(ctx, ImportFailure('IMPORT_WRITE', stage='disc'))
        try:
            if load_error is not None:
                ctx.import_id = imp_paths.new_import_id()
                ctx.report = Report(paths, ctx.import_id, options['device'] or 'folder')
                ctx.report.begin_stage('disc')
                ctx.report.add_error('IMPORT_INTERNAL', 'disc')
                log(paths, ctx.import_id, 'error', exception=type(load_error).__name__)
                ctx.report.finish('failed', resumable=True, next_stage='disc')
                ctx.c6.failed('IMPORT_INTERNAL')
                log(paths, ctx.import_id, 'finish', outcome='failed')
                return 2
            return _run(ctx)
        except UsageError as exc:
            imp_disc.unmount(ctx)
            _usage(exc.node)
            return 1
        except Interrupted:
            if ctx.report is None:
                old = copy.deepcopy(ctx.journal.data or {})
                _identity(ctx, old, journal.resumable(), imp_paths.iso())
            ctx.report.data['resume'].update(resumable=True, next_stage=ctx.current_stage)
            ctx.report.heartbeat(force=True)
            imp_disc.unmount(ctx)
            log(ctx.p, ctx.import_id, 'stopped', stage=ctx.current_stage)
            return 4
        except ImportFailure as exc:
            return _failure(ctx, exc)
        except Exception as exc:
            if ctx.report is None:
                old = copy.deepcopy(ctx.journal.data or {})
                if not hasattr(ctx, 'disc'):
                    ctx.disc = imp_disc.Disc(paths.mount, options['device'] or 'folder')
                _identity(ctx, old, journal.resumable(), imp_paths.iso())
            ctx.journal.note(type(exc).__name__ + ':' + str(exc))
            log(ctx.p, ctx.import_id, 'error', exception=type(exc).__name__, detail=str(exc))
            return _failure(ctx, ImportFailure('IMPORT_INTERNAL', stage=ctx.current_stage))
    finally:
        lock.release()


if __name__ == '__main__':
    raise SystemExit(main())
