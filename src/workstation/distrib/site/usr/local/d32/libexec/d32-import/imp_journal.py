"""Import journal, generation registry and lock for C3, C5 and deep32-import-report.

Public: Journal, Registry, Lock, LockBusy, reconcile. JSON is atomic, ASCII,
indented and sorted. Reconciliation delegates content checks to stage owners.
"""

import copy
import json
import os
from pathlib import Path
import re

import imp_fs
import imp_paths


STAGES = ('disc', 'signature', 'files', 'space', 'kb', 'library', 'blocks',
          'switch', 'check', 'finish')


def _json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode('ascii')


def _read(path):
    try:
        with imp_fs.open_regular(path) as stream:
            return json.loads(stream.read().decode('ascii'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeError) as exc:
        raise imp_fs.ImportFailure('IMPORT_INTERNAL') from exc


def _safe_target(value):
    return isinstance(value, str) and value not in ('.', '..') and re.fullmatch(r'\.[A-Za-z0-9._+-]{1,254}', value) is not None


def _safe_pointer_name(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,254}', value) is not None


def _safe_pointer_value(value):
    if not isinstance(value, str):
        return False
    parts = value.split('/')
    return (len(parts) in (1, 2) and _safe_target(parts[0]) and
            all(part not in ('.', '..') and re.fullmatch(r'[A-Za-z0-9._+-]{1,255}', part) is not None
                for part in parts[1:]))


def _safe_component(value):
    return (isinstance(value, str) and value not in ('.', '..') and
            re.fullmatch(r'[A-Za-z0-9._+-]{1,255}', value) is not None)


def _safe_unit(value):
    return (value in ('kb', 'cisco-public', 'block-rfc', 'block-cisco', 'block-powershell', 'block-system')
            if isinstance(value, str) and not value.startswith('lib-') else
            isinstance(value, str) and re.fullmatch(r'lib-[a-z0-9-]{1,32}', value) is not None)


def _safe_relative(value):
    return (isinstance(value, str) and value and not value.startswith('/') and
            '\\' not in value and ':' not in value and
            all(part not in ('', '.', '..') for part in value.split('/')) and
            all(ord(char) >= 32 and ord(char) != 127 for char in value))


def _validate_generation_units(record):
    kind = record.get('unit_kind')
    units = record.get('units')
    if kind not in ('kb', 'library', 'cisco', 'block') or not isinstance(units, list):
        raise imp_fs.ImportFailure('IMPORT_INTERNAL')
    seen = set()
    for unit in units:
        if not isinstance(unit, dict) or not _safe_unit(unit.get('id')) or unit['id'] in seen:
            raise imp_fs.ImportFailure('IMPORT_INTERNAL')
        identifier = unit['id']
        seen.add(identifier)
        if ((kind == 'kb' and identifier != 'kb') or
                (kind == 'cisco' and identifier != 'cisco-public') or
                (kind == 'library' and not identifier.startswith('lib-')) or
                (kind == 'block' and not identifier.startswith('block-'))):
            raise imp_fs.ImportFailure('IMPORT_INTERNAL')
        generation = unit.get('generation')
        if not (identifier == 'cisco-public' and generation is None) and not _safe_component(generation):
            raise imp_fs.ImportFailure('IMPORT_INTERNAL')
        if unit.get('release') is not None and not _safe_component(unit['release']):
            raise imp_fs.ImportFailure('IMPORT_INTERNAL')


def _validate_registry(value, node):
    if (not isinstance(value, dict) or value.get('format') != 'deep32-import-installed' or
            value.get('format_version') != 1 or value.get('node') != node or
            not isinstance(value.get('generations'), list)):
        raise imp_fs.ImportFailure('IMPORT_INTERNAL')
    for generation in value['generations']:
        if not isinstance(generation, dict) or not _safe_target(generation.get('target')):
            raise imp_fs.ImportFailure('IMPORT_INTERNAL')
        _validate_generation_units(generation)
    _validate_active(value.get('active'))
    _validate_active(value.get('previous'))


def _validate_desired_records(record):
    _validate_generation_units(record)
    units = {unit['id']: unit['generation'] for unit in record['units']}
    records = record.get('records', {})
    if not isinstance(records, dict):
        raise imp_fs.ImportFailure('IMPORT_INTERNAL')
    for pack, data in records.items():
        if (not _safe_unit(pack) or not pack.startswith('lib-') or not isinstance(data, dict) or
                not _safe_component(data.get('generation')) or pack not in units or
                units[pack] != data['generation'] or
                not isinstance(data.get('INFO'), str) or not isinstance(data.get('MANIFEST.tsv'), str)):
            raise imp_fs.ImportFailure('IMPORT_INTERNAL')
    streams = record.get('streams', [])
    if not isinstance(streams, list):
        raise imp_fs.ImportFailure('IMPORT_INTERNAL')
    for stream in streams:
        if (not isinstance(stream, dict) or not _safe_unit(stream.get('pack')) or
                not stream['pack'].startswith('lib-') or stream['pack'] not in units or
                not _safe_component(stream.get('generation')) or units[stream['pack']] != stream['generation'] or
                not _safe_relative(stream.get('install_prefix'))):
            raise imp_fs.ImportFailure('IMPORT_INTERNAL')
    verification = record.get('verification', [])
    if not isinstance(verification, list):
        raise imp_fs.ImportFailure('IMPORT_INTERNAL')
    for entry in verification:
        if not isinstance(entry, dict) or not _safe_relative(entry.get('path')):
            raise imp_fs.ImportFailure('IMPORT_INTERNAL')
    if 'block' in record:
        block = record['block']
        if (not isinstance(block, dict) or block.get('name') not in ('rfc', 'cisco', 'powershell', 'system') or
                not _safe_component(block.get('version'))):
            raise imp_fs.ImportFailure('IMPORT_INTERNAL')


def _validate_active(record):
    if record is None:
        return
    if not isinstance(record, dict) or not isinstance(record.get('pointers'), dict):
        raise imp_fs.ImportFailure('IMPORT_INTERNAL')
    for pointer, value in record['pointers'].items():
        if not _safe_pointer_name(pointer) or not _safe_pointer_value(value):
            raise imp_fs.ImportFailure('IMPORT_INTERNAL')
    targets = record.get('targets', [])
    if (not isinstance(targets, list) or any(not _safe_target(target) for target in targets) or
            len(set(targets)) != len(targets)):
        raise imp_fs.ImportFailure('IMPORT_INTERNAL')


def _validate_journal(value):
    if not isinstance(value.get('stage_dir'), str) or not re.fullmatch(r'\.import-stage-[0-9a-f]{8}', value['stage_dir']):
        raise imp_fs.ImportFailure('IMPORT_INTERNAL')
    if not isinstance(value.get('units'), dict):
        raise imp_fs.ImportFailure('IMPORT_INTERNAL')
    for identifier, unit in value['units'].items():
        if not _safe_unit(identifier) or not isinstance(unit, dict) or (unit.get('target') is not None and not _safe_target(unit['target'])):
            raise imp_fs.ImportFailure('IMPORT_INTERNAL')
    desired = value.get('desired', [])
    if not isinstance(desired, list):
        raise imp_fs.ImportFailure('IMPORT_INTERNAL')
    for unit in desired:
        if (not isinstance(unit, dict) or not _safe_target(unit.get('target')) or
                not _safe_pointer_value(unit.get('value'))):
            raise imp_fs.ImportFailure('IMPORT_INTERNAL')
        pointer = unit.get('pointer')
        if not (_safe_pointer_name(pointer) or
                pointer is None and unit.get('unit_kind') == 'library' and unit['value'] == unit['target']):
            raise imp_fs.ImportFailure('IMPORT_INTERNAL')
        _validate_desired_records(unit)
    if value.get('registry_before') is not None:
        _validate_registry(value['registry_before'], value['node'])
    switch = value.get('switch')
    if switch is not None:
        if (not isinstance(switch, dict) or switch.get('phase') not in
                ('begun', 'done', 'rollback-begun', 'rolled-back') or not isinstance(switch.get('pointers'), list)):
            raise imp_fs.ImportFailure('IMPORT_INTERNAL')
        for pointer in switch['pointers']:
            if (not isinstance(pointer, dict) or not _safe_pointer_name(pointer.get('pointer')) or
                    not _safe_pointer_value(pointer.get('new')) or
                    (pointer.get('old') is not None and not _safe_pointer_value(pointer['old']))):
                raise imp_fs.ImportFailure('IMPORT_INTERNAL')
        saved = switch.get('saved_registry')
        if saved is not None:
            if not isinstance(saved, dict):
                raise imp_fs.ImportFailure('IMPORT_INTERNAL')
            _validate_active(saved.get('active'))
            _validate_active(saved.get('previous'))


def prepare(paths):
    """Create state folders without Windows owner-only directory modes."""
    group = 'wheel' if paths.node == 'workstation' else 'root'
    for folder, mode, folder_group in ((paths.state, paths.state_mode, paths.state_group),
                                       (paths.journal, 0o700, group),
                                       (paths.tmp, 0o700, group),
                                       (paths.mount, 0o700, group)):
        if folder.is_symlink():
            raise imp_fs.ImportFailure('IMPORT_WRITE')
        folder.mkdir(parents=True, exist_ok=True)
        if not folder.is_mount():
            imp_fs.set_owner_mode(folder, 'root', folder_group, mode)


class Journal:
    def __init__(self, paths, load=True):
        self.paths = paths
        self.data = None
        if load:
            self.load()

    def load(self):
        value = _read(self.paths.state_file)
        if value is not None:
            if not isinstance(value, dict) or value.get('format') != 'deep32-import-journal' or value.get('format_version') != 1 or value.get('node') != self.paths.node:
                raise imp_fs.ImportFailure('IMPORT_INTERNAL')
            _validate_journal(value)
        self.data = value
        return self

    def new(self, manifest_sha256, device, import_id, qdrant_url=None, **kw):
        self.data = {
            'format': 'deep32-import-journal', 'format_version': 1,
            'node': self.paths.node, 'manifest_sha256': manifest_sha256,
            'device': device, 'qdrant_url': qdrant_url,
            'import_id': import_id, 'first_import_id': import_id,
            'attempt': 1, 'stage_dir': '.import-stage-' + import_id.rsplit('-', 1)[-1],
            'stages': {name: {'state': 'pending', 'started': None, 'finished': None} for name in STAGES},
            'units': {}, 'switch': None, 'error': None, 'outcome': None,
            'finished_at': None, 'label': None, 'unlisted': None,
            'children': [], 'notes': [],
        }
        for key, value in kw.items():
            self.data[key] = copy.deepcopy(value)
        self.save()
        return self.data

    def resumable(self):
        if not self.data:
            return False
        outcome = self.data.get('outcome')
        return outcome is None or (outcome == 'failed' and
                                   (self.data.get('error') or {}).get('code') in imp_fs.RESUMABLE)

    def should_resume(self, manifest_sha256=None, disc_failed=False):
        if not self.resumable():
            return False
        switch = self.data.get('switch') or {}
        if switch.get('phase') in ('begun', 'done', 'rollback-begun') and self.data.get('outcome') is None:
            return True
        return disc_failed or self.data.get('manifest_sha256') in (None, manifest_sha256)

    def start_attempt(self, import_id):
        if not self.resumable():
            raise ValueError('journal is not resumable')
        previous = self.data['import_id']
        if self.data.get('outcome') == 'failed':
            self.data['switch'] = None
        self.data['import_id'] = import_id
        self.data['attempt'] += 1
        self.data['outcome'] = None
        self.data['finished_at'] = None
        # A second power cut during recovery must keep the original check fault.
        if (self.data.get('switch') or {}).get('phase') != 'rollback-begun':
            self.data['error'] = None
        self.save()
        return previous

    def save(self):
        if self.data is None:
            return
        self.data['notes'] = self.data.get('notes', [])[-200:]
        self.data['children'] = self.data.get('children', [])[-50:]
        for child in self.data['children']:
            child['tail'] = child.get('tail', [])[-20:]
        prepare(self.paths)
        imp_fs.atomic_write(self.paths.state_file, _json_bytes(self.data), 0o600,
                            group='wheel' if self.paths.node == 'workstation' else 'root')

    def set_stage(self, stage, state, started=None, finished=None):
        if stage not in STAGES:
            raise ValueError('unknown stage')
        row = self.data['stages'].setdefault(stage, {'state': 'pending', 'started': None, 'finished': None})
        row['state'] = state
        if started is not None:
            row['started'] = started
        elif state == 'running':
            row['started'] = imp_paths.iso()
            row['finished'] = None
        if finished is not None:
            row['finished'] = finished
        elif state in ('done', 'failed', 'skipped'):
            row['finished'] = imp_paths.iso()
        self.save()
        return row

    def set_unit(self, unit, **fields):
        row = self.data['units'].setdefault(unit, {'state': 'checked', 'target': None,
                                                 'content_key': None, 'bytes': 0})
        row.update(fields)
        self.save()
        return row

    def note(self, text):
        self.data.setdefault('notes', []).append(str(text))
        self.data['notes'] = self.data['notes'][-200:]
        self.save()

    def child(self, program, exit_code, output):
        if isinstance(output, bytes):
            output = output.decode('ascii', 'replace')
        if isinstance(output, str):
            output = output.splitlines()
        tail = [str(line).encode('ascii', 'backslashreplace').decode('ascii') for line in output][-20:]
        self.data.setdefault('children', []).append({'program': Path(program).name,
                                                    'exit': exit_code, 'tail': tail})
        self.data['children'] = self.data['children'][-50:]
        self.save()


class Registry:
    def __init__(self, paths, load=True):
        self.paths = paths
        self.data = {'format': 'deep32-import-installed', 'format_version': 1,
                     'node': self.paths.node, 'active': None, 'previous': None,
                     'generations': []}
        if load:
            self.load()

    def load(self):
        value = _read(self.paths.registry_file)
        if value is None:
            value = {'format': 'deep32-import-installed', 'format_version': 1,
                     'node': self.paths.node, 'active': None, 'previous': None,
                     'generations': []}
        _validate_registry(value, self.paths.node)
        self.data = value
        return self

    @property
    def active(self):
        return self.data['active']

    @active.setter
    def active(self, value):
        self.data['active'] = copy.deepcopy(value)

    @property
    def previous(self):
        return self.data['previous']

    @previous.setter
    def previous(self, value):
        self.data['previous'] = copy.deepcopy(value)

    def save(self):
        prepare(self.paths)
        self.data['generations'].sort(key=lambda row: row['target'])
        imp_fs.atomic_write(self.paths.registry_file, _json_bytes(self.data), 0o600,
                            group='wheel' if self.paths.node == 'workstation' else 'root')

    def generation(self, target):
        return next((row for row in self.data['generations'] if row['target'] == target), None)

    def owned(self, target):
        return self.generation(target) is not None

    def add(self, record):
        record = copy.deepcopy(record)
        target = record['target']
        if not _safe_target(target):
            raise ValueError('unsafe generation target')
        self.data['generations'] = [row for row in self.data['generations'] if row['target'] != target]
        self.data['generations'].append(record)
        self.save()
        return record

    def remove(self, target):
        self.data['generations'] = [row for row in self.data['generations'] if row['target'] != target]
        self.save()


class LockBusy(Exception):
    """A live process owns the importer lock; do not touch its report."""


class Lock:
    def __init__(self, paths):
        self.paths = paths
        self.acquired = False

    def acquire(self):
        self.paths.journal.mkdir(parents=True, exist_ok=True)
        try:
            self.paths.lock.mkdir()
        except FileExistsError:
            try:
                with imp_fs.open_regular(self.paths.lock / 'pid') as stream:
                    pid = int(stream.read(64).decode('ascii').strip())
            except (OSError, ValueError, UnicodeError):
                pid = None
            if imp_paths.alive(pid):
                imp_fs.log(self.paths, '-', 'busy')
                raise LockBusy()
            imp_fs.remove_tree(self.paths.lock)
            self.paths.lock.mkdir()
            imp_fs.log(self.paths, '-', 'lock-taken')
        self.acquired = True
        group = 'wheel' if self.paths.node == 'workstation' else 'root'
        imp_fs.set_owner_mode(self.paths.lock, 'root', group, 0o700)
        imp_fs.atomic_write(self.paths.lock / 'pid', str(os.getpid()).encode('ascii') + b'\n', 0o600, group=group)
        return True

    def release(self):
        if self.acquired:
            imp_fs.remove_tree(self.paths.lock)
            self.acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.release()


def reconcile(ctx):
    """Dispatch journal recovery before reading a disc; return switch recovery."""
    journal = ctx.journal
    if not journal.data:
        return False
    placing = getattr(ctx, 'adopt_placing', None)
    if placing is not None:
        for unit, row in list(journal.data.get('units', {}).items()):
            if row.get('state') == 'placing':
                placing(unit, row)
    switch = journal.data.get('switch') or {}
    required = journal.data.get('outcome') is None and switch.get('phase') in ('begun', 'done', 'rollback-begun')
    callback = getattr(ctx, 'reconcile_switch', None)
    if required and callback is not None:
        callback()
    return required
