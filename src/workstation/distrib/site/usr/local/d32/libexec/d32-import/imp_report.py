"""Machine report model for C3, C5, C6 and deep32-import-report version 1.

Public: Report, STAGES, ERROR_ARGS and WARNING_ARGS. Report supplies ordered
data, stage and unit changes, validated codes, progress, heartbeat and history.
"""

# UI HAND-OFF: the Import Report window, a separate desktop program, reads report.json; this module writes data only.
# Every word a person reads comes from that window's own strings table, looked up by the codes written here.

import collections
import json
import os
from pathlib import Path
import re

import imp_fs
import imp_paths


# fetch (Download disc 2) is d32-content-fetch's: it writes the stage while it downloads the image, and the importer
# records it as done (the disc is that image) or skipped (any other disc).
STAGES = ('fetch', 'disc', 'signature', 'files', 'space', 'kb', 'library', 'blocks',
          'switch', 'check', 'finish')
ERROR_ARGS = {
    'IMPORT_NO_DISC': ('device',), 'IMPORT_NOT_DISC2': ('missing',),
    'IMPORT_MOUNT': ('device',), 'IMPORT_GPGV_MISSING': (),
    'IMPORT_SIGNATURE': (), 'IMPORT_MANIFEST': (),
    'IMPORT_WRONG_SYSTEM': ('system_version', 'wanted'),
    'IMPORT_FILE_MISSING': (), 'IMPORT_FILE_SIZE': ('expected', 'found'),
    'IMPORT_FILE_HASH': (), 'IMPORT_READ': (),
    'IMPORT_SPACE': ('needed_bytes', 'free_bytes'), 'IMPORT_WRITE': (),
    'IMPORT_KB_FORM': (), 'IMPORT_KB_ENGINE': ('engine', 'min', 'max'),
    'IMPORT_PACK_FORM': (), 'IMPORT_PACK_BINDING': ('pack_fingerprint', 'kb_fingerprint'),
    'IMPORT_SWITCH': (), 'IMPORT_CHECK': ('what',), 'IMPORT_INTERNAL': (),
    'IMPORT_FETCH': ('reason',),
}
WARNING_ARGS = {
    'IMPORT_RESUMED': ('from',), 'IMPORT_MEANING_WAIT': (),
    'IMPORT_MEANING_MISMATCH': ('served', 'wanted'), 'IMPORT_OLD_KEPT': ('bytes',),
    'IMPORT_KB_ONLY': (),
}
_ARG = re.compile(r'[A-Za-z0-9._:/+-]{1,128}\Z')
_ID = re.compile(r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\Z')
_GENERATION = re.compile(r'[A-Za-z0-9._+-]{1,64}\Z')
_LONG_HEX = re.compile(r'[0-9A-Fa-f]{32,}')
_UNIT_KEYS = ('id', 'kind', 'generation', 'bytes', 'sha256', 'parts', 'parts_checked',
              'state', 'kb_fingerprint', 'error')


def safe_generation(value):
    return value if isinstance(value, str) and _GENERATION.fullmatch(value) and not _LONG_HEX.search(value) else None


def _stage(identifier):
    return {'id': identifier, 'state': 'pending', 'started': None, 'finished': None,
            'done_bytes': None, 'total_bytes': None, 'done_items': None,
            'total_items': None, 'error': None}


def _kind(identifier):
    if identifier == 'kb':
        return 'kb'
    if identifier == 'cisco-public':
        return 'index'
    if identifier.startswith('block-'):
        return 'block'
    if re.fullmatch(r'lib-[a-z0-9-]{1,32}', identifier):
        return 'library'
    raise ValueError('unknown unit')


class Report:
    def __init__(self, paths, import_id, device, attempt=1, resumed_from=None, started=None):
        if not _ID.fullmatch(import_id):
            raise ValueError('invalid import id')
        self.paths = paths
        self._last_write = None
        self._samples = collections.deque()
        self._archived_old = False
        stamp = imp_paths.iso(started) if started is not None else imp_paths.iso()
        self.data = {
            'format': 'deep32-import-report', 'format_version': 1,
            'node': paths.node, 'import_id': import_id, 'attempt': attempt,
            'resumed_from': resumed_from, 'report_seq': 0,
            'started': stamp, 'written': stamp, 'finished': None,
            'heartbeat': stamp, 'pid': os.getpid(), 'state': 'running',
            'outcome': None, 'stage': 'disc',
            'source': {'device': device, 'disc_version': None, 'manifest_sha256': None,
                       'signature': 'unchecked', 'signer': None, 'disc_bytes': None},
            'progress': {'done_bytes': 0, 'total_bytes': 0, 'rate': None,
                         'free_bytes_before': None, 'free_bytes_now': None, 'needed_bytes': None},
            'stages': [_stage(name) for name in STAGES], 'units': [],
            'kb': {'generation': None, 'fingerprint': None, 'created': None,
                   'counts': None, 'embedding': None, 'engine_ok': None,
                   'meaning_search': None, 'previous': None},
            'rollback': {'available': False, 'kb_generation': None, 'library': {}, 'done': False},
            'resume': {'resumable': True, 'next_stage': 'disc', 'units_done': [], 'bytes_kept': 0},
            'errors': [], 'warnings': [],
        }

    def stage(self, identifier):
        return next(row for row in self.data['stages'] if row['id'] == identifier)

    def unit(self, identifier):
        return next((row for row in self.data['units'] if row['id'] == identifier), None)

    def begin_stage(self, identifier, total_bytes=None, total_items=None, *, write=True):
        if identifier not in STAGES:
            raise ValueError('unknown stage')
        row = self.stage(identifier)
        for earlier in self.data['stages'][:STAGES.index(identifier)]:
            if earlier['state'] == 'running':
                raise ValueError('another stage is running')
        row.update(state='running', started=imp_paths.iso(), finished=None,
                   done_bytes=0 if total_bytes is not None else None,
                   total_bytes=total_bytes, done_items=0 if total_items is not None else None,
                   total_items=total_items, error=None)
        self.data['stage'] = identifier
        self.data['resume']['next_stage'] = identifier
        if write:
            self.write()
        return row

    def end_stage(self, identifier, state='done', *, write=True):
        if state not in ('done', 'skipped'):
            raise ValueError('invalid stage completion')
        row = self.stage(identifier)
        row['state'] = state
        if row['started'] is None:
            row['started'] = imp_paths.iso()
        row['finished'] = imp_paths.iso()
        if state == 'skipped':
            row['done_bytes'] = row['total_bytes'] = None
            row['done_items'] = row['total_items'] = None
        if write:
            self.write()
        return row

    def fail_stage(self, identifier, error, *, write=True):
        row = self.stage(identifier)
        row['state'] = 'failed'
        if row['started'] is None:
            row['started'] = imp_paths.iso()
        row['finished'] = imp_paths.iso()
        if row['error'] is None:
            row['error'] = error['id'] if isinstance(error, dict) else error
        self.data['stage'] = identifier
        for later in self.data['stages'][STAGES.index(identifier) + 1:]:
            later.update(_stage(later['id']))
        if write:
            self.write()
        return row

    def set_unit(self, identifier, *, write=True, **fields):
        if any(key not in _UNIT_KEYS or key == 'id' for key in fields):
            raise ValueError('unknown unit field')
        row = self.unit(identifier)
        if row is None:
            row = {'id': identifier, 'kind': _kind(identifier), 'generation': None,
                   'bytes': 0, 'sha256': None, 'parts': 1, 'parts_checked': 0,
                   'state': 'pending', 'kb_fingerprint': None, 'error': None}
            self.data['units'].append(row)
            # Library units retain signed-manifest order within their kind.
            rank = {'kb': 0, 'library': 1, 'index': 2, 'block': 3}
            blocks = {'block-rfc': 0, 'block-cisco': 1, 'block-powershell': 2, 'block-system': 3}
            self.data['units'].sort(key=lambda item: (rank[item['kind']], blocks.get(item['id'], 0)))
        fields = dict(fields)
        if 'generation' in fields:
            fields['generation'] = safe_generation(fields['generation'])
        row.update(fields)
        if write:
            self.write()
        return row

    def _issue(self, warning, code, stage, unit, path, args):
        table = WARNING_ARGS if warning else ERROR_ARGS
        if code not in table or stage not in STAGES:
            raise ValueError('unknown report code or stage')
        args = {} if args is None else dict(args)
        if set(args) != set(table[code]):
            raise ValueError('incorrect report arguments')
        for value in args.values():
            if isinstance(value, bool) or not (isinstance(value, int) or
                    (isinstance(value, str) and _ARG.fullmatch(value) and not _LONG_HEX.search(value))):
                raise ValueError('invalid report argument value')
        if path is not None:
            if not isinstance(path, str) or path.startswith(('/', '\\')) or '\\' in path or '..' in path.split('/'):
                raise ValueError('unsafe report path')
        target = self.data['warnings' if warning else 'errors']
        issue = {'id': ('W' if warning else 'E') + str(len(target) + 1),
                 'code': code, 'stage': stage, 'unit': unit, 'path': path,
                 'args': {key: args[key] for key in table[code]}, 'at': imp_paths.iso()}
        target.append(issue)
        if not warning:
            if unit is not None:
                row = self.unit(unit)
                if row is None:
                    row = self.set_unit(unit, write=False)
                row['state'] = 'failed'
                if row['error'] is None:
                    row['error'] = issue['id']
            if not any(row['state'] == 'failed' for row in self.data['stages']):
                self.fail_stage(stage, issue, write=False)
        return issue

    def add_error(self, code, stage, unit=None, path=None, args=None, *, write=True):
        result = self._issue(False, code, stage, unit, path, args)
        if write:
            self.write()
        return result

    def add_warning(self, code, stage=None, unit=None, path=None, args=None, *, write=True):
        result = self._issue(True, code, stage or self.data['stage'], unit, path, args)
        if write:
            self.write()
        return result

    def bytes(self):
        return (json.dumps(self.data, indent=2) + '\n').encode('ascii')

    def _archive(self, data):
        try:
            identifier = json.loads(data.decode('ascii'))['import_id']
        except (UnicodeError, ValueError, KeyError, TypeError):
            return
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
            return
        self.paths.history.mkdir(parents=True, exist_ok=True)
        imp_fs.set_owner_mode(self.paths.history, 'root', self.paths.state_group, self.paths.state_mode)
        destination = self.paths.history / (identifier + '.json')
        if not destination.exists():
            imp_fs.atomic_write(destination, data, self.paths.report_mode, group=self.paths.state_group)
        records = sorted((item for item in self.paths.history.iterdir()
                          if item.is_file() and not item.is_symlink() and _ID.fullmatch(item.stem)
                          and item.suffix == '.json'), key=lambda item: item.name, reverse=True)
        for path in records[10:]:
            path.unlink()

    def history(self):
        self._archive(self.bytes())

    def write(self):
        if not self._archived_old:
            try:
                with imp_fs.open_regular(self.paths.report) as stream:
                    old = stream.read()
                self._archive(old)
            except FileNotFoundError:
                pass
            self._archived_old = True
        stamp = imp_paths.now()
        self.data['report_seq'] += 1
        self.data['written'] = imp_paths.iso(stamp)
        if self.data['state'] == 'running':
            self.data['heartbeat'] = self.data['written']
        try:
            self.data['progress']['free_bytes_now'] = imp_fs.free_bytes(self.paths.content_root)
        except OSError:
            pass
        self.paths.state.mkdir(parents=True, exist_ok=True)
        imp_fs.set_owner_mode(self.paths.state, 'root', self.paths.state_group, self.paths.state_mode)
        imp_fs.atomic_write(self.paths.report, self.bytes(), self.paths.report_mode, group=self.paths.state_group)
        self._last_write = stamp
        return self.data

    def heartbeat(self, force=False):
        stamp = imp_paths.now()
        if force or self._last_write is None or stamp - self._last_write >= 1:
            self.write()

    def progress(self, count, stage=None, items=0):
        if not isinstance(count, int) or count < 0 or not isinstance(items, int) or items < 0:
            raise ValueError('invalid progress increment')
        stage = stage or self.data['stage']
        row = self.stage(stage)
        self.data['progress']['done_bytes'] += count
        if row['done_bytes'] is not None:
            row['done_bytes'] += count
        if row['done_items'] is not None:
            row['done_items'] += items
        stamp = imp_paths.now()
        self._samples.append((stamp, self.data['progress']['done_bytes']))
        while len(self._samples) > 1 and self._samples[0][0] < stamp - 10:
            self._samples.popleft()
        if len(self._samples) >= 2 and stamp - self._samples[0][0] >= 1:
            first = self._samples[0]
            self.data['progress']['rate'] = int((self._samples[-1][1] - first[1]) / (stamp - first[0]))
        else:
            self.data['progress']['rate'] = None
        if self._last_write is None or stamp - self._last_write >= 1:
            self.write()

    def finish(self, outcome, resumable=False, next_stage=None):
        if outcome not in ('imported', 'already-current', 'failed', 'rolled-back'):
            raise ValueError('invalid outcome')
        self.data['state'] = 'failed' if outcome in ('failed', 'rolled-back') else 'done'
        self.data['outcome'] = outcome
        self.data['finished'] = imp_paths.iso()
        self.data['pid'] = None
        self.data['resume']['resumable'] = bool(resumable)
        self.data['resume']['next_stage'] = next_stage if resumable else None
        self.data['rollback']['done'] = outcome == 'rolled-back'
        self.write()
        self.history()
        return self.data
