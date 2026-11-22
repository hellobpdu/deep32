"""d32upd's lock() names a disc 2 download that holds the lock, instead of "another d32upd is running (pid 1)".

d32-content-fetch holds d32upd's lock with pid 1 and an owner file while a downloaded disc 2 sits on the spare slot.
The function is taken from the real d32upd and run under Git for Windows' bash by its full path (never a bare bash,
which can start WSL), with stand-ins for die, print and kill (pid 1, init, is always alive on the Workstation).
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
D32UPD = ROOT / 'src/workstation/ship/u2/usr/local/d32/sbin/d32upd'
BASH = Path('C:/Program Files/Git/bin/bash.exe') if os.name == 'nt' else Path(shutil.which('bash') or '/bin/bash')
NEW = ('a disc 2 download is using the spare slot; import it, or remove it as root with '
       '/usr/local/d32/sbin/d32-content-fetch --cleanup')
OLD = 'another d32upd is running (pid 1)'


def shell_path(path):
    path = Path(path).resolve()
    return '/' + path.drive[0].lower() + '/' + path.as_posix().split(':/', 1)[1] if os.name == 'nt' else str(path)


def function(source, name):
    match = re.search(r'(?ms)^' + re.escape(name) + r'\(\) \{.*?^\}$', source)
    if match is None:
        raise AssertionError('required function is absent: ' + name)
    return match.group(0) + '\n'


@unittest.skipUnless(BASH.is_file(), 'bash is not available')
class Lock(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-d32upd-', dir=os.environ.get('D32_TEST_WORK'))
        self.addCleanup(temporary.cleanup)
        self.db = Path(temporary.name) / 'db'
        (self.db / '.lock').mkdir(parents=True)
        (self.db / '.lock/pid').write_text('1\n', encoding='ascii', newline='\n')
        self.script = Path(temporary.name) / 'lock.sh'
        self.script.write_bytes((
            "die() { printf '%s\\n' \"$*\"; exit 3; }\n"
            "print() { printf '%s\\n' \"$*\"; }\n"
            "kill() { return 0; }\n"
            'DB=$1\nWINDOW=$2\n'
            + function(D32UPD.read_text(encoding='ascii'), 'lock')
            + 'lock\n').encode('ascii'))

    def lock(self, window):
        self.assertNotIn(' ', shell_path(self.db))   # d32upd leaves $DB unquoted
        return subprocess.run([str(BASH), '--noprofile', '--norc', shell_path(self.script), shell_path(self.db), window],
                              capture_output=True, text=True, timeout=60)

    def test_a_download_holding_the_lock_is_named(self):
        (self.db / '.lock/owner').write_text('d32-content-fetch\n', encoding='ascii', newline='\n')
        result = self.lock('live')
        self.assertEqual((result.returncode, result.stdout.strip()), (3, NEW))

    def test_another_d32upd_keeps_its_message(self):
        result = self.lock('live')
        self.assertEqual((result.returncode, result.stdout.strip()), (3, OLD))

    def test_the_boot_window_takes_the_lock(self):
        (self.db / '.lock/owner').write_text('d32-content-fetch\n', encoding='ascii', newline='\n')
        result = self.lock('boot')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual((self.db / '.lock/pid').read_text(encoding='ascii').strip(), '1')
        self.assertFalse((self.db / '.lock/owner').exists())


if __name__ == '__main__':
    unittest.main()
