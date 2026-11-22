#!/usr/bin/env python3
"""Exercise installed pairing callers using synthetic state and local stand-ins.

Usage: python -I -B tests/pairing/test_callers.py --tmp SCRATCH
No service, network, real credential store or build is started.
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RUNTIME = REPO / 'src/workstation/distrib/site/usr/local/d32'
FIRSTBOOT = RUNTIME / 'sbin/d32-firstboot'
TWIN = REPO / 'src/build/s5-build-driver/slots/t0113/901-install-settings-and-first-boot-sources/new/distrib/site/usr/local/d32/sbin/d32-firstboot'
BASH = Path('C:/Program Files/Git/bin/bash.exe') if os.name == 'nt' else Path('/bin/bash')
LIBEXEC_LAUNCHERS = ('d32-c6-words', 'd32-content-get', 'd32-content-import', 'd32-forge-enrol', 'd32-gateway-enrol',
                    'd32-import-again', 'd32-import-report', 'd32-pair', 'd32-setup', 'd32-uijson',
                    'deep32-pair', 'pairing-helper')


def shell_path(path):
    path = Path(path).resolve()
    if os.name == 'nt':
        return '/' + path.drive[0].lower() + '/' + path.as_posix().split(':/', 1)[1]
    return str(path)


def function(source, name):
    match = re.search(r'(?ms)^' + re.escape(name) + r'\(\) \{.*?^\}$', source)
    if match is None:
        raise AssertionError('required caller function is absent: ' + name)
    return match.group(0) + '\n'


class CallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-callers-', dir=os.environ['TMP'])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.serial = 0

    def shell(self, body, *args, env=None):
        self.assertTrue(BASH.is_file(), 'the explicit local Bash interpreter is unavailable')
        self.serial += 1
        script = self.root / ('synthetic-script-' + str(self.serial) + '.sh')
        script.write_text(body, encoding='ascii', newline='\n')
        return subprocess.run([str(BASH), '--noprofile', '--norc', shell_path(script), *map(str, args)],
                              env=dict(os.environ, **(env or {})), capture_output=True,
                              text=True, timeout=30, check=False)

    def executable(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!/bin/sh\n' + text, encoding='ascii', newline='\n')
        path.chmod(0o755)

    def wrapper(self, name, args=(), running=False, fail_enrol=False, fail_stop=False, uid=0):
        events = []
        api = types.ModuleType('deep32net.enrol')
        files = types.ModuleType('deep32net.files')

        def enrol(state, peer=None):
            events.append(('enrol', Path(state).as_posix(), peer))
            if fail_enrol:
                raise RuntimeError('synthetic refusal')
            return {}

        api.enrol_gateway = enrol
        api.enrol_forge = enrol
        api.enrol_engine = lambda root: events.append(('engine', Path(root).as_posix()))
        files.atomic_write = lambda path, value: events.append(('write', str(path), bytes(value)))

        def command(argv, **kwargs):
            self.assertEqual(argv[0], '/usr/sbin/rcctl')
            action = argv[1]
            events.append(('service', action, argv[2]))
            if action == 'stop' and fail_stop:
                raise subprocess.CalledProcessError(1, argv)
            return subprocess.CompletedProcess(argv, int(not running) if action == 'check' else 0)

        output = io.StringIO()
        with patch.dict(sys.modules, {'deep32net.enrol': api, 'deep32net.files': files}), \
                patch.object(os, 'geteuid', return_value=uid, create=True), \
                patch.object(subprocess, 'run', side_effect=command), \
                patch.object(sys, 'argv', [name, *args]), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with self.assertRaises(SystemExit) as caught:
                runpy.run_path(str(RUNTIME / 'libexec' / name), run_name='__main__')
        self.assertFalse('PRIVATE KEY' in output.getvalue())
        return caught.exception.code, events, output.getvalue()

    def test_installed_launcher_imports_its_sibling_package(self):
        self.assertTrue((RUNTIME / 'deep32net/__init__.py').is_file())
        for name in ('deep32-pair', 'pairing-helper'):
            launcher = RUNTIME / 'libexec' / name
            result = subprocess.run([sys.executable, '-I', '-B', str(launcher), '--version'],
                                    capture_output=True, text=True, timeout=10, check=False)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), name + ' 0.1.0')
            self.assertEqual(result.stderr, '')

    def check_site_modes(self, replacement=None):
        self.serial += 1
        stage = self.root / ('synthetic-install-' + str(self.serial))
        shutil.copytree(RUNTIME, stage / 'usr/local/d32', ignore=shutil.ignore_patterns('__pycache__'))
        command = stage / 'usr/local/bin/deep32'
        command.parent.mkdir(parents=True)
        shutil.copy2(REPO / 'src/workstation/distrib/site/usr/local/bin/deep32', command)
        libexec = stage / 'usr/local/d32/libexec'
        overlay = REPO / 'src/workstation/ship/site/usr/local/d32/libexec'
        if overlay.is_dir():
            shutil.copytree(overlay, libexec, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
        decoys = {'synthetic-module.py': '"""Synthetic importer module, never a launcher."""\n',
                  'synthetic-shebang.py': '#!/bin/sh\nexit 0\n',
                  'synthetic-nested/deep32-pair': '#!/bin/sh\nexit 0\n',
                  'synthetic-nested/data.tsv': 'synthetic\tdata\n'}
        for name, value in decoys.items():
            destination = libexec / name
            destination.parent.mkdir(exist_ok=True)
            destination.write_text(value, encoding='ascii', newline='\n')
        (stage / 'etc/rc.d').mkdir(parents=True)
        (stage / 'etc/rc.d/d32synthetic').write_text('synthetic\n', encoding='ascii')
        (stage / 'install.site').write_text('synthetic\n', encoding='ascii')
        source = (REPO / 'src/workstation/ship/tools/mksite-ship.sh').read_text(encoding='ascii')
        start = source.index('find $st -type d -exec chmod')
        end = source.index('chmod 0600 $st/etc/pf.conf', start)
        block = source[start:end] if replacement is None else replacement
        tools = self.root / ('synthetic-mode-tools-' + str(self.serial))
        audit = self.root / ('synthetic-mode-audit-' + str(self.serial) + '.tsv')
        # NTFS/MSYS can synthesise execute bits from a shebang. Record each
        # successful numeric operation while still delegating to real chmod.
        self.executable(tools / 'chmod', '''set -eu
mode=$1; shift
case "$mode" in 0[0-7][0-7][0-7]) ;; *) exit 90;; esac
/usr/bin/chmod "$mode" "$@"
for target do printf '%s\t%s\n' "$mode" "$target" >>"$D32_MODE_AUDIT"; done
''')
        body = 'set -e\nst="$1"\nPATH="$D32_MODE_TOOLS:/usr/bin:/bin"; export PATH\n' + block
        for name in LIBEXEC_LAUNCHERS:
            body += 'test -x "$st/usr/local/d32/libexec/' + name + '"\n'
        body += 'test -x "$st/usr/local/d32/sbin/d32-pairdesk"\n'
        body += 'test -x "$st/usr/local/bin/deep32"\n'
        result = self.shell(body, shell_path(stage), env={'D32_MODE_TOOLS': shell_path(tools),
                                                        'D32_MODE_AUDIT': shell_path(audit)})
        self.assertEqual(result.returncode, 0, 'actual site mode recipe failed on a correct tree')
        changes = [line.split('\t', 1) for line in audit.read_text(encoding='ascii').splitlines()]
        prefix = shell_path(stage)
        modes = {}
        for mode, target in changes:
            self.assertTrue(target == prefix or target.startswith(prefix + '/'), 'mode operation left the staged tree')
            modes[target[len(prefix):].lstrip('/')] = int(mode, 8)
        files = {path.relative_to(libexec).as_posix(): path for path in libexec.rglob('*') if path.is_file()}
        applied = {name: modes['usr/local/d32/libexec/' + name] for name in files}
        self.assertEqual({name for name, mode in applied.items() if mode & 0o111}, set(LIBEXEC_LAUNCHERS),
                         'libexec executable set differs from the intended launchers')
        self.assertEqual(applied, {name: 0o555 if name in LIBEXEC_LAUNCHERS else 0o644 for name in files})
        self.assertTrue(all(modes[path.relative_to(stage).as_posix()] == 0o755
                            for path in stage.rglob('*') if path.is_dir()))
        self.assertEqual(modes['install.site'], 0o555)
        self.assertEqual(modes['etc/rc.d/d32synthetic'], 0o555)
        self.assertEqual(modes['usr/local/bin/deep32'], 0o555)
        for path in (stage / 'usr/local/d32/sbin').iterdir():
            if path.is_file():
                self.assertEqual(modes[path.relative_to(stage).as_posix()], 0o555)
        if replacement is None:
            type(self).libexec_mode_audit = {'files': len(files), 'launchers': len(LIBEXEC_LAUNCHERS),
                'non_launchers': len(files) - len(LIBEXEC_LAUNCHERS), 'synthetic_decoys': len(decoys),
                'numeric_mode_changes': len(changes), 'libexec_modes': {name: oct(applied[name]) for name in sorted(applied)}}

    def test_site_mode_recipe_has_exact_libexec_launcher_modes(self):
        self.check_site_modes()

    def test_site_mode_check_rejects_blanket_modes_and_missing_launcher(self):
        source = (REPO / 'src/workstation/ship/tools/mksite-ship.sh').read_text(encoding='ascii')
        start = source.index('find $st -type d -exec chmod')
        end = source.index('chmod 0600 $st/etc/pf.conf', start)
        block = source[start:end]
        mutations = {'blanket': block + 'find $st/usr/local/d32/libexec -type f -exec chmod 0555 {} +\n',
                     'missing-launcher': block.replace('d32-gateway-enrol', '', 1),
                     'missing-command-launcher': block.replace('chmod 0555 "$st/usr/local/bin/deep32"\n', '', 1)}
        for name, changed in mutations.items():
            self.assertNotEqual(changed, block, 'mode mutation did not change the recipe')
            with self.subTest(mutation=name), self.assertRaises(AssertionError):
                self.check_site_modes(changed)

    def test_gateway_firstboot_does_not_start_or_stop_services(self):
        code, events, _ = self.wrapper('d32-gateway-enrol', ['--firstboot'], running=True)
        self.assertEqual(code, 0)
        # The engine token moves first (only at first boot), then the gateway's own credentials.
        self.assertEqual(events, [('engine', '/'), ('enrol', '/var/db/d32/net', None)])

    def test_gateway_refresh_restarts_running_service_after_enrol(self):
        code, events, _ = self.wrapper('d32-gateway-enrol', running=True)
        self.assertEqual(code, 0)
        self.assertEqual([event[:2] for event in events],
                         [('service', 'check'), ('service', 'stop'), ('enrol', '/var/db/d32/net'),
                          ('service', 'start')])
        code, events, _ = self.wrapper('d32-gateway-enrol', running=False)
        self.assertEqual(code, 0)
        self.assertEqual([event[:2] for event in events],
                         [('service', 'check'), ('enrol', '/var/db/d32/net')])

    def test_gateway_new_peer_starts_after_enrol_and_failure_keeps_it_stopped(self):
        code, events, _ = self.wrapper('d32-gateway-enrol', ['headend-synthetic'], running=False)
        self.assertEqual(code, 0)
        self.assertEqual([event[:2] for event in events],
                         [('service', 'check'), ('enrol', '/var/db/d32/net'), ('service', 'start')])
        self.assertEqual(events[1][2], 'headend-synthetic')
        code, events, message = self.wrapper('d32-gateway-enrol', ['headend-synthetic'],
                                             running=True, fail_enrol=True)
        self.assertEqual(code, 6)
        self.assertFalse(any(event[:2] == ('service', 'start') for event in events))
        self.assertEqual(message, 'd32-gateway-enrol: enrolment failed\n')

    def test_enrol_wrapper_requires_root_and_stopping_before_write(self):
        code, events, _ = self.wrapper('d32-gateway-enrol', uid=1000)
        self.assertEqual((code, events), (6, []))
        code, events, _ = self.wrapper('d32-gateway-enrol', ['headend-synthetic'],
                                       running=True, fail_stop=True)
        self.assertEqual(code, 6)
        self.assertFalse(any(event[0] == 'enrol' for event in events))
        code, events, _ = self.wrapper('d32-forge-enrol', ['forge-synthetic'], running=False)
        self.assertEqual(code, 0)
        self.assertEqual([event[:2] for event in events],
                         [('write', '/etc/d32/state/gpu_ext'), ('service', 'stop'),
                          ('service', 'check'), ('enrol', '/var/db/d32/net')])
        code, events, _ = self.wrapper('d32-forge-enrol', ['forge-synthetic'], running=True)
        self.assertEqual(code, 6)
        self.assertFalse(any(event[0] == 'enrol' for event in events))

    def test_mandatory_pairdesk_hooks_fail_without_success_message(self):
        source = (RUNTIME / 'sbin/d32-pairdesk').read_text(encoding='ascii')
        node = self.root / 'synthetic-desk'
        (node / 'tmp').mkdir(parents=True)
        helper = node / 'synthetic-pair'
        self.executable(helper, 'case "$1" in new-code) echo KLMNP23456;; *) echo "{}";; esac\n')
        common = '''
set +e
R="$1"; PAIR="$R/synthetic-pair"; STATE="$R/net"; UIJSON=cat; WINDOW=300; REL=synthetic; CR=''
header() { :; }; have_pair() { return 0; }; wait_return() { :; }
setting() { echo loopback; }; ask() { ANSWER=KLMNP23456; }
d32ui_code() { D32UI_CODE=KLMNP23456; return 0; }
d32ui_text() { D32UI_T=synthetic; }; d32ui_say() { echo "$1"; }
fields_from() { F_ok=yes; F_peer_id=forge-synthetic; }
why() { D32UI_T=storage; }; d32ui_c6_ok() { return 0; }
d32ui_group() { D32UI_T=synthetic; }; d32ui_spell() { D32UI_T=synthetic; }
d32ui_mmss() { D32UI_T=synthetic; }; d32ui_fmt() { D32UI_T=synthetic; }
clear() { :; }
'''
        common += function(source, 'G_stop') + function(source, 'join') + function(source, 'show')
        for action, hook, success in (('join forge', 'd32-forge-enrol', 'pair.ok.forge'),
                                       ('show', 'd32-gateway-enrol', 'pair.ok.headend')):
            for exists in (False, True):
                with self.subTest(action=action, hook_exists=exists):
                    target = node / 'usr/local/d32/libexec' / hook
                    if exists:
                        self.executable(target, 'exit 6\n')
                    else:
                        target.unlink(missing_ok=True)
                    result = self.shell(common + '\n' + action + '\n', shell_path(node))
                    self.assertEqual(result.returncode, 0)
                    self.assertTrue('pair.fail' in result.stdout, 'failed mandatory hook was not reported')
                    self.assertFalse(success in result.stdout, 'success preceded mandatory enrolment')

    def test_firstboot_source_twin_and_existing_credentials_are_preserved(self):
        self.assertEqual(hashlib.sha256(FIRSTBOOT.read_bytes()).digest(),
                         hashlib.sha256(TWIN.read_bytes()).digest())
        source = FIRSTBOOT.read_text(encoding='ascii')
        token, second = '5c' * 32, '6d' * 32
        kept = ('legacy', 'file', 'files')
        for mode in ('legacy', 'file', 'mismatch', 'damaged', 'files'):
            with self.subTest(mode=mode):
                node = self.root / ('synthetic-firstboot-' + mode)
                state = node / 'etc/d32/state'
                state.mkdir(parents=True)
                core = node / 'etc/d32/core.env'
                edge = node / 'etc/d32/edge.env'
                core_value = 'damaged' if mode == 'damaged' else token
                edge_value = second if mode == 'mismatch' else core_value
                # files: the engine token already moved into its two protected files by an earlier enrolment
                core_line = ('RAG_CORE_API_TOKEN_FILE=/etc/d32/private/engine-api.token' if mode == 'files'
                             else 'RAG_CORE_API_TOKEN=' + core_value)
                edge_line = ('RAG_CORE_API_TOKEN_FILE=/etc/d32/private/engine-client.token' if mode == 'files'
                             else 'RAG_CORE_API_TOKEN=' + edge_value)
                core.write_text(core_line + '\n', encoding='ascii', newline='\n')
                mcp = ('RAG_MCP_TOKENS_FILE=/etc/d32/private/mcp-tokens' if mode in ('file', 'files')
                       else 'RAG_MCP_TOKENS=' + ('' if mode == 'damaged' else second))
                edge.write_text(edge_line + '\n' + mcp + '\n', encoding='ascii', newline='\n')
                if mode != 'damaged':
                    (state / 'mcp.token').write_text(second + '\n', encoding='ascii', newline='\n')
                before = (core.read_bytes(), edge.read_bytes())
                self.executable(node / 'usr/local/d32/libexec/d32-gateway-enrol',
                                'printf "%s\\n" "$*" >>"$D32_ROOT/synthetic-enrol-calls"\n')
                body = '''
D32_ROOT="$1"; export D32_ROOT
c6_set() { echo "c6 $*"; }; _d32f_fail() { echo safe-failure; return 1; }
openssl() { echo attempted >"$D32_ROOT/synthetic-reset-attempt"; return 1; }
'''
                body += function(source, '_d32f_token') + function(source, '_d32f_keys') + '\n_d32f_keys\n'
                result = self.shell(body, shell_path(node))
                self.assertEqual(result.returncode, 0 if mode in kept else 1)
                self.assertFalse((node / 'synthetic-reset-attempt').exists(), 'existing credential state was regenerated')
                self.assertTrue(before == (core.read_bytes(), edge.read_bytes()), 'existing credentials changed')
                calls = node / 'synthetic-enrol-calls'
                if mode in kept:
                    self.assertIn('c6 keys done msg=tokens kept', result.stdout)
                    self.assertEqual(calls.read_text(encoding='ascii'), '--firstboot\n')
                else:
                    self.assertFalse(calls.exists())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tmp', required=True, type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    scratch = args.tmp.resolve()
    if scratch == Path(scratch.anchor) or scratch == REPO or REPO in scratch.parents:
        parser.error('--tmp must be a separate scratch directory outside the repository')
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ.update(TMP=str(scratch), TEMP=str(scratch), TMPDIR=str(scratch))
    tempfile.tempdir = str(scratch)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CallerTests))
    if args.report:
        args.report.write_text(json.dumps({'tests_run': result.testsRun, 'tests_failed': len(result.failures),
                                          'tests_errors': len(result.errors),
                                          'libexec_mode_audit': getattr(CallerTests, 'libexec_mode_audit', None)},
                                         indent=2) + '\n', encoding='ascii')
    return int(not result.wasSuccessful())


if __name__ == '__main__':
    raise SystemExit(main())
