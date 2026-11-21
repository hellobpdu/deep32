#!/usr/bin/env python3
"""Offline checks of the first start's search for disc 2 (d32-firstboot), with no OpenBSD.

The search's own shell functions, taken from d32-firstboot as they stand, run with sysctl, mount, umount and
disklabel answered by stand-ins that model a machine's disks: optical drives, the root disk, the disk under an
encrypted root, and USB sticks. Every stand-in writes what it was asked to a log, so a test can say which disks were
looked at and how each one was mounted. Set D32_TEST_WORK to keep the fixtures in a chosen folder.

On Windows the shell is Git for Windows' bash by its full path, never a bare bash; elsewhere it is ksh, else bash.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / 'src/workstation/distrib/site/usr/local/d32'
FIRSTBOOT = SITE / 'sbin/d32-firstboot'
SETTINGS = SITE / 'libexec/d32-settings.sh'
WINDOWS = os.name == 'nt'
SHELL = (Path('C:/Program Files/Git/bin/bash.exe') if WINDOWS else
         Path(shutil.which('ksh') or shutil.which('bash') or '/bin/sh'))

DUID0, DUID1, DUID2 = '0123456789abcdef', '1123456789abcdef', '2123456789abcdef'
ROOT_SD0 = '/dev/sd0a on / type ffs (local)\n/dev/sd0d on /tmp type ffs (local, nodev, nosuid)\n'
LABEL_SYSTEM = 'label: SYSTEM\n  a:  1000  64  4.2BSD  2048 16384 1\n  b:  100  1064  swap\n  c:  2000  0  unused\n'
LABEL_RAID = 'label: SYSTEM\n  a:  1999  1  RAID\n  c:  2000  0  unused\n'
LABEL_ISO = 'label: DEEP32_CONTENT\n  a:  900  0  ISO9660\n  c:  900  0  ISO9660\n'
LABEL_FAT = 'label: USB DISK\n  c:  900  0  unused\n  i:  899  1  MSDOS\n'
DISC2 = ('MANIFEST.json', 'MANIFEST.json.asc', 'kb/', 'corpus/', 'library/')

STUBS = r'''
sysctl() { [ "$*" = "-n hw.disknames" ] || return 1; cat "$FIX/disknames"; }
disklabel() {
    echo "disklabel $*" >>"$LOG"
    [ -f "$FIX/label.$1" ] || { echo "disklabel: /dev/r${1}c: Device not configured" >&2; return 1; }
    cat "$FIX/label.$1"
}
mount() {
    local _a _prev= _last=
    if [ $# = 0 ]; then [ -f "$FIX/mounts" ] || return 1; cat "$FIX/mounts"; return 0; fi
    echo "mount $*" >>"$LOG"
    for _a; do _prev=$_last; _last=$_a; done
    _prev=${_prev#"$D32_ROOT/dev/"}
    [ -d "$FIX/media/$_prev" ] || return 1
    cp -R "$FIX/media/$_prev/." "$_last/"
}
umount() { echo "umount $*" >>"$LOG"; rm -rf "$1" && mkdir "$1"; }
c6_hold_stop() { :; }
c6_set() { echo "c6_set $*" >>"$LOG"; }
_d32f_waiting() { echo "waiting $*" >>"$LOG"; }
_d32f_mark() { echo "mark $*" >>"$LOG"; }
'''


def shell_path(path):
    value = Path(path).resolve().as_posix()
    return '/' + value[0].lower() + value[2:] if WINDOWS else value


def quote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


def function(source, name):
    match = re.search(r'(?ms)^' + re.escape(name) + r'\(\) \{.*?^\}$', source)
    if match is None:
        raise AssertionError('d32-firstboot has no function ' + name)
    return match.group(0) + '\n'


class FirstbootDisc2Probe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SHELL.is_file():
            raise unittest.SkipTest('no shell for the offline probe tests: ' + str(SHELL))
        source = FIRSTBOOT.read_text(encoding='ascii')
        cls.functions = ''.join(function(source, name) for name in
                                ('_d32f_disc2_devices', '_d32f_disc2_mount', '_d32f_probe'))
        cls._temp = tempfile.TemporaryDirectory(prefix='firstboot-disc2-', dir=os.environ.get('D32_TEST_WORK'))
        cls.work = Path(cls._temp.name)

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def machine(self, disknames, mounts=ROOT_SD0, labels=None, media=None):
        """A machine: hw.disknames, the mount table (None: mount fails), disklabels and what each node holds."""
        self.serial = getattr(self, 'serial', 0) + 1
        case = self.work / ('%s-%d' % (self.id().rsplit('.', 1)[-1], self.serial))
        fix = case / 'fix'
        (fix / 'media').mkdir(parents=True)
        (fix / 'disknames').write_text(disknames + '\n', encoding='ascii', newline='\n')
        if mounts is not None:
            (fix / 'mounts').write_text(mounts, encoding='ascii', newline='\n')
        for name, text in (labels or {}).items():
            (fix / ('label.' + name)).write_text(text, encoding='ascii', newline='\n')
        for node, names in (media or {}).items():
            for name in names:
                path = fix / 'media' / node / name
                if name.endswith('/'):
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'synthetic\n')
        self.case = case
        return case

    def run_probe(self, action):
        script = self.case / 'case.sh'
        lines = ['D32_ROOT=' + quote(shell_path(self.case)), 'D32_FB_DIR="$D32_ROOT/fb"', 'FIX="$D32_ROOT/fix"',
                 'LOG="$D32_ROOT/log"', ': >"$LOG"', '. ' + quote(shell_path(SETTINGS)), STUBS, self.functions,
                 {'devices': '_d32f_disc2_devices',
                  'probe': '_d32f_probe; echo "rc=$? device=$D32_FB_DEVICE"'}[action]]
        script.write_text('\n'.join(lines) + '\n', encoding='ascii', newline='\n')
        args = [str(SHELL), '--noprofile', '--norc', shell_path(script)] if WINDOWS else [str(SHELL), str(script)]
        result = subprocess.run(args, capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(result.stderr, '')
        log = (self.case / 'log').read_text(encoding='ascii').splitlines()
        return result.stdout.split(), log

    def mounts_of(self, log):
        return [line.replace(shell_path(self.case), 'ROOT') for line in log if line.startswith('mount ')]

    def test_device_list_is_optical_drives_then_sticks_without_the_system_disks(self):
        self.machine('sd0:%s,sd1:,cd0:,sd2:%s,wd0:,cd1:,sd3:,sd123:,vnd0:,rd0:' % (DUID0, DUID2),
                     mounts=ROOT_SD0 + '/dev/sd2i on /mnt type msdos (local)\n',
                     labels={'sd0': LABEL_SYSTEM, 'sd1': LABEL_ISO, 'sd2': LABEL_FAT, 'sd3': LABEL_FAT})
        devices, log = self.run_probe('devices')
        self.assertEqual(devices, ['cd0', 'cd1', 'sd1', 'sd3'])
        # The root disk and a disk with a mounted partition are left out before anything reads them.
        self.assertEqual(log, ['disklabel sd1', 'disklabel sd3'])

    def test_disk_under_an_encrypted_root_and_a_key_disk_are_never_named(self):
        self.machine('sd0:%s,sd1:%s,sd2:,sd3:%s' % (DUID0, DUID1, DUID2), mounts='/dev/sd1a on / type ffs (local)\n',
                     labels={'sd0': LABEL_RAID, 'sd1': LABEL_SYSTEM, 'sd2': LABEL_ISO, 'sd3': LABEL_RAID})
        devices, log = self.run_probe('devices')
        self.assertEqual(devices, ['sd2'])
        self.assertNotIn('disklabel sd1', log)

    def test_no_stick_is_named_when_the_root_disk_cannot_be_found(self):
        for name, mounts in (('mount fails', None), ('no root line', '/dev/sd0d on /tmp type ffs (local)\n'),
                             ('root by another name', 'root_device on / type ffs (local)\n')):
            with self.subTest(mounts=name):
                self.machine('sd0:%s,cd0:,sd1:' % DUID0, mounts=mounts, labels={'sd1': LABEL_ISO})
                devices, log = self.run_probe('devices')
                self.assertEqual(devices, ['cd0'])
                self.assertEqual(log, [])

    def test_disc_2_on_a_usb_stick_is_found_and_mounted_read_only(self):
        self.machine('sd0:%s,sd1:' % DUID0, labels={'sd0': LABEL_SYSTEM, 'sd1': LABEL_ISO}, media={'sd1c': DISC2})
        out, log = self.run_probe('probe')
        self.assertEqual(out, ['rc=0', 'device=sd1'])
        self.assertEqual(self.mounts_of(log), ['mount -t cd9660 -o ro ROOT/dev/sd1c ROOT/fb/probe'])
        self.assertIn('c6_set content done msg=disc 2 found on sd1', log)
        self.assertIn('mark content.done  sd1', log)
        self.assertFalse([line for line in log if 'sd0' in line])
        self.assertEqual(list((self.case / 'fb/probe').iterdir()), [])

    def test_foreign_sticks_are_ignored(self):
        # sd1: an ISO 9660 stick that is not disc 2 (no signature); sd2: a FAT stick, which ISO 9660 cannot mount.
        self.machine('sd0:%s,sd1:,sd2:' % DUID0, labels={'sd0': LABEL_SYSTEM, 'sd1': LABEL_ISO, 'sd2': LABEL_FAT},
                     media={'sd1c': ('MANIFEST.json', 'kb/', 'corpus/', 'library/', 'README.TXT')})
        out, log = self.run_probe('probe')
        self.assertEqual(out, ['rc=1', 'device='])
        self.assertEqual(self.mounts_of(log), ['mount -t cd9660 -o ro ROOT/dev/sd1c ROOT/fb/probe',
                                               'mount -t cd9660 -o ro ROOT/dev/sd2c ROOT/fb/probe'])
        self.assertIn('waiting content waiting msg=attach disc 2 (a disc or a USB stick), or get it over the network '
                      'in First Start', log)
        self.assertFalse([line for line in log if line.startswith(('mark', 'c6_set'))])
        self.assertEqual(list((self.case / 'fb/probe').iterdir()), [])

    def test_optical_drive_keeps_its_mount_and_two_copies_of_disc_2_are_refused(self):
        self.machine('sd0:%s,cd0:' % DUID0, labels={'sd0': LABEL_SYSTEM, 'cd0': LABEL_ISO}, media={'cd0c': DISC2})
        out, log = self.run_probe('probe')
        self.assertEqual(out, ['rc=0', 'device=cd0'])
        self.assertEqual(self.mounts_of(log), ['mount -o ro ROOT/dev/cd0c ROOT/fb/probe'])
        self.machine('sd0:%s,sd1:,cd0:' % DUID0, labels={'sd0': LABEL_SYSTEM, 'sd1': LABEL_ISO, 'cd0': LABEL_ISO},
                     media={'cd0c': DISC2, 'sd1c': DISC2})
        out, log = self.run_probe('probe')
        self.assertEqual(out, ['rc=1', 'device=sd1'])
        self.assertEqual(self.mounts_of(log), ['mount -o ro ROOT/dev/cd0c ROOT/fb/probe',
                                               'mount -t cd9660 -o ro ROOT/dev/sd1c ROOT/fb/probe'])
        self.assertIn('waiting content waiting msg=more than one disc 2 is attached: leave one', log)


if __name__ == '__main__':
    unittest.main(verbosity=2)
