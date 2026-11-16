"""Write C6 import status with the deep32-import-report stage contract.

Public interfaces: Strings, guard_message, C6Workstation and C6Forge.
Only pinned C6 texts are accepted; progress follows the stage byte counters.
"""

import importlib.util
from pathlib import Path
import re
import subprocess
import sys

from imp_paths import test_mode
from imp_fs import log


STAGES = ('disc', 'signature', 'files', 'space', 'kb', 'library', 'blocks',
          'switch', 'check', 'finish')
REQUIRED = tuple('c6.import.' + value for value in STAGES) + tuple(
    'c6.import.' + value + '.n' for value in ('files', 'kb', 'library', 'blocks')) + (
    'c6.import.failed', 'c6.import.done', 'c6.import.current', 'c6.ready')


def guard_message(value):
    """Refuse a message that fails C6's printable and secret-shape guards."""
    if (not isinstance(value, str) or not 1 <= len(value) <= 240 or
            any(ord(ch) < 32 or ord(ch) > 126 for ch in value) or
            '\\' in value or re.search(r'[0-9a-fA-F]{32,}', value)):
        raise ValueError('c6-message')
    return value


class Strings:
    """Load the pinned id/text TSV and interpolate only its named fields."""

    def __init__(self, path=None):
        path = Path(path) if path else Path(__file__).with_name('c6-strings.tsv')
        self.rows = {}
        try:
            raw = path.read_bytes()
            if not raw.isascii() or b'\r' in raw or not raw.endswith(b'\n'):
                raise ValueError('c6-tsv')
            lines = raw.decode('ascii').splitlines()
            if not lines or lines[0] != 'id\ttext':
                raise ValueError('c6-header')
            for line in lines[1:]:
                fields = line.split('\t')
                if len(fields) != 2 or not fields[0].startswith('c6.') or fields[0] in self.rows:
                    raise ValueError('c6-row')
                self.rows[fields[0]] = fields[1]
            if any(key not in self.rows for key in REQUIRED):
                raise ValueError('c6-missing')
            for key in REQUIRED:
                guard_message(self.rows[key].format(done=0, total=1, version='1'))
        except (OSError, UnicodeError, KeyError, IndexError, ValueError) as exc:
            raise ValueError('c6-strings') from exc

    def get(self, row_id, **values):
        return guard_message(self.rows[row_id].format(**values))


class _C6:
    def __init__(self, paths, journal=None, import_id='-', strings=None):
        self.ctx = paths if hasattr(paths, 'p') else None
        self.paths = self.ctx.p if self.ctx else paths
        self.journal = journal if journal is not None else getattr(self.ctx, 'journal', None)
        self.import_id = import_id
        self.strings = strings or Strings()
        self.deciles = {}

    def _identity(self):
        return getattr(self.ctx, 'import_id', self.import_id) if self.ctx else self.import_id

    def stage(self, stage):
        self.deciles[stage] = 0
        return self.set('running', self.strings.get('c6.import.' + stage))

    def progress(self, stage, done, total):
        if stage not in ('files', 'kb', 'library', 'blocks') or not total:
            return
        decile = 10 * done // total
        previous = self.deciles.get(stage, 0)
        self.deciles[stage] = max(previous, decile)
        if 1 <= decile <= 9 and decile > previous:
            self.set('running', self.strings.get('c6.import.' + stage + '.n',
                     done=done // 1048576, total=max(1, (total + 1048575) // 1048576)))

    def failed(self, code):
        return self.set('failed', self.strings.get('c6.import.failed'), error=code)

    def success(self, version, current=False):
        return self.set('done', self.strings.get(
            'c6.import.current' if current else 'c6.import.done', version=version))


class C6Workstation(_C6):
    """Use only the shipped d32-c6 set/show command line."""

    def _run(self, args):
        argv = self.paths.command('d32-c6') + list(args)
        if self.ctx is not None and hasattr(self.ctx, 'run_child'):
            result = self.ctx.run_child(argv, timeout=600)
        else:
            try:
                result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                        text=True, timeout=600, check=False)
            except (OSError, subprocess.TimeoutExpired):
                result = subprocess.CompletedProcess(argv, 127, '', '')
            if self.journal is not None and self.journal.data is not None:
                self.journal.child('d32-c6', result.returncode, result.stdout + result.stderr)
        if result.returncode:
            log(self.paths, self._identity(), 'c6-refused', exit=result.returncode)
        return result

    def set(self, state, msg, error=None, stage='import'):
        guard_message(msg)
        args = ['set', 'workstation', stage, state]
        if state == 'failed':
            if not error:
                raise ValueError('c6-error')
            args.append('error=' + error)
        elif error is not None:
            raise ValueError('c6-error-state')
        args.append('msg=' + msg)
        return self._run(args)

    def show(self):
        return self._run(['show']).stdout

    def ready(self):
        line = self.show()
        if not re.search(r'(?:^| )stage=pairing(?: |$)', line):
            return self.set('done', self.strings.get('c6.ready'), stage='ready')


class C6Forge(_C6):
    """Continue the Forge boot/sequence through the unmodified C6 writer."""

    def __init__(self, paths, journal=None, import_id='-', strings=None):
        super().__init__(paths, journal, import_id, strings)
        source = Path(__file__).with_name('vendor') / 'status.py'
        spec = importlib.util.spec_from_file_location('d32_import_status', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.status = module.Status(path=self.paths.status_file,
                                    consoles=() if test_mode() else ('/dev/console', '/dev/ttyS0'))
        try:
            line = self.paths.status_file.read_text(encoding='ascii').strip()
        except (OSError, UnicodeError):
            line = ''
        match = re.match(r'^D32S/1 ([0-9]+) forge(?: |$)', line)
        boot = re.search(r'(?:^| )boot=([0-9a-f]{8})(?: |$)', line)
        if match and boot:
            self.status.boot, self.status.seq = boot.group(1), int(match.group(1))

    def set(self, state, msg, error=None, stage='import'):
        guard_message(msg)
        fields = {'msg': msg}
        if state == 'failed':
            if not error:
                raise ValueError('c6-error')
            fields['error'] = error
        elif error is not None:
            raise ValueError('c6-error-state')
        self.status.update(stage, state, **fields)

    def ready(self):
        return None
