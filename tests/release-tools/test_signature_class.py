"""Offline C2-059 signature-class checks; synthetic status transport, no signing."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / 'src/release-tools'
SPEC = importlib.util.spec_from_file_location('signature_class_manifest', TOOLS / 'deep32_get_manifest.py')
manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manifest)
PIN = 'A' * 40
SCRATCH = None
BASH = Path('C:/Program Files/Git/bin/bash.exe')
POWERSHELL = Path('C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe')


def msys(value):
    value = str(value).replace('\\', '/')
    return '/' + value[0].lower() + value[2:] if len(value) > 2 and value[1:3] == ':/' else value


def status(signature_class, primary=True):
    return ('[GNUPG:] GOODSIG SYNTHETIC synthetic test\n[GNUPG:] VALIDSIG ' + PIN +
            ' 2000-01-01 0 0 4 0 1 10 ' + signature_class + (' ' + PIN if primary else '') + '\n')


class SignatureClass(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='signature-', dir=SCRATCH)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.key_status = self.root / 'synthetic-public-listing'
        self.key_status.write_text('pub:\nfpr:::::::::' + PIN + ':\n', encoding='ascii')
        self.status = self.root / 'synthetic-gpg-status'

    def test_python_binary_signature_with_primary_or_subkey_status(self):
        for primary in (False, True):
            with self.subTest(primary=primary):
                self.status.write_text(status('00', primary), encoding='ascii')
                manifest.validsig(self.status, self.key_status)
        self.status.write_text(status('00').replace('VALIDSIG ' + PIN, 'VALIDSIG ' + 'B' * 40), encoding='ascii')
        manifest.validsig(self.status, self.key_status)

    def test_python_text_and_other_classes_refused_for_unchanged_bytes(self):
        payload = self.root / 'synthetic-SHA256SUMS'
        payload.write_bytes(b'SYNTHETIC EXACT BYTES\n')
        before = payload.read_bytes()
        for signature_class in ('01', '02', '0', '000', 'ff'):
            for primary in (False, True):
                with self.subTest(signature_class=signature_class, primary=primary):
                    self.status.write_text(status(signature_class, primary), encoding='ascii')
                    with self.assertRaisesRegex(manifest.Refusal, 'class 00'):
                        manifest.validsig(self.status, self.key_status)
        self.assertEqual(payload.read_bytes(), before)

    def environment(self):
        environment = os.environ.copy()
        temp = self.root / 'temp'
        temp.mkdir(exist_ok=True)
        public_home = self.root / 'synthetic-public-home'
        public_home.mkdir(exist_ok=True)
        environment.update(TEMP=str(temp), TMP=str(temp), TMPDIR=msys(temp),
                           GNUPGHOME=str(public_home), D32_TEST_STATUS=str(self.status), D32_TEST_PIN=PIN)
        return environment

    def run_process(self, command, environment):
        return subprocess.run([str(value) for value in command], env=environment, cwd=self.root,
                              capture_output=True, text=True, timeout=45)

    def legacy_fixture(self):
        release = self.root / 'synthetic-release'
        release.mkdir()
        checksums = {}
        children = []
        for role, kind in (('disc1', 'workstation-test'), ('disc2', 'content'), ('disc3', 'gpu-pack')):
            prefix = 'deep32-0.1.0-alpha.1-' + role + '-' + kind
            image = prefix + ('.img' if role == 'disc1' else '.iso')
            readme = prefix + '-README.md'
            for name in (image, readme):
                (release / name).write_bytes(('SYNTHETIC ' + name + '\n').encode('ascii'))
                checksums[name] = hashlib.sha256((release / name).read_bytes()).hexdigest()
            child = prefix + '-SHA256SUMS'
            (release / child).write_bytes(''.join(checksums[n] + '  ' + n + '\n' for n in sorted((image, readme))).encode('ascii'))
            checksums[child] = hashlib.sha256((release / child).read_bytes()).hexdigest()
            (release / (child + '.sig')).write_bytes(b'SYNTHETIC SIGNATURE\n')
            children.append(child)
        (release / 'SHA256SUMS').write_bytes(''.join(checksums[n] + '  ' + n + '\n' for n in sorted(checksums)).encode('ascii'))
        (release / 'SHA256SUMS.sig').write_bytes(b'SYNTHETIC SIGNATURE\n')
        return release, children

    def test_legacy_bash_and_powershell_refuse_text_root_and_each_child(self):
        release, children = self.legacy_fixture()
        before = {p.name: p.read_bytes() for p in release.iterdir()}
        shim = self.root / 'shim'
        shim.mkdir()
        (shim / 'gpg').write_text('''#!/bin/sh
class=00
for arg do last=$arg; done
if [ "${last##*/}" = "$D32_TEXT_CHECKSUM" ]; then class=01; fi
printf '[GNUPG:] VALIDSIG %s 2000-01-01 0 0 4 0 1 10 %s %s\\n' "$D32_TEST_PIN" "$class" "$D32_TEST_PIN"
''', encoding='ascii', newline='\n')
        (shim / 'gpg').chmod(0o700)
        (shim / 'gpg.cmd').write_text('''@echo off
set "class=00"
if "%~nx7"=="%D32_TEXT_CHECKSUM%" set "class=01"
echo [GNUPG:] VALIDSIG %D32_TEST_PIN% 2000-01-01 0 0 4 0 1 10 %class% %D32_TEST_PIN%
''', encoding='ascii')
        environment = self.environment()
        environment.update(PATH=str(shim) + os.pathsep + environment.get('PATH', ''), D32_TEST_SHIM=msys(shim),
                           D32_TEST_GPG_CMD=str(shim / 'gpg.cmd'), D32_TEST_LEGACY=str(TOOLS / 'verify-release.ps1'),
                           D32_TEST_RELEASE=str(release))
        harness = self.root / 'synthetic-legacy-harness.ps1'
        harness.write_text('''$ErrorActionPreference='Stop'
if ((Get-Command gpg -CommandType Application | Select-Object -First 1).Source -cne $env:D32_TEST_GPG_CMD) {
    throw 'Synthetic GPG command was not selected'
}
& $env:D32_TEST_LEGACY -ReleaseDirectory $env:D32_TEST_RELEASE -Fingerprint $env:D32_TEST_PIN -LegacySplit
exit $LASTEXITCODE
''', encoding='ascii')
        commands = {
            'bash': [BASH, '--noprofile', '--norc', '-c',
                     'export PATH="$D32_TEST_SHIM:$PATH"; test "$(command -v gpg)" = "$D32_TEST_SHIM/gpg" || exit 97; source "$1" "${@:2}"',
                     '--', msys(TOOLS / 'verify-release.sh'),
                     '--legacy-split', msys(release), PIN],
            'powershell': [POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                           harness]}
        for shell, command in commands.items():
            for checksum in ('', 'SHA256SUMS', *children):
                with self.subTest(shell=shell, text_checksum=checksum):
                    environment['D32_TEXT_CHECKSUM'] = checksum
                    result = self.run_process(command, environment)
                    self.assertEqual(result.returncode == 0, not checksum, result.stdout + result.stderr)
                    if checksum:
                        self.assertIn('class 00', result.stdout + result.stderr)
                    self.assertEqual(before, {p.name: p.read_bytes() for p in release.iterdir()})

    def installer_fixture(self):
        folder = self.root / 'synthetic-manifest'
        folder.mkdir()
        (folder / 'release-key.gpg').write_bytes(b'SYNTHETIC PUBLIC KEY STAND-IN\n')
        (folder / 'release-key.fingerprint').write_text(PIN + '\n', encoding='ascii')
        value = {'format': 'deep32-release-manifest', 'format_version': 1,
                 'signer': {'type': 'openpgp', 'fingerprint': PIN},
                 'release': {'version': 'synthetic', 'tag': 'synthetic', 'created': '2000-01-01', 'complete': True},
                 'discs': [{'role': 'system', 'file': 'synthetic.img', 'size': 1, 'sha256': '0' * 64,
                            'parts': [{'name': 'synthetic.img.part001', 'size': 1, 'sha256': '0' * 64}]}]}
        (folder / 'MANIFEST.json').write_bytes((json.dumps(value) + '\n').encode('ascii'))
        (folder / 'MANIFEST.json.asc').write_bytes(b'SYNTHETIC SIGNATURE\n')
        return folder

    def test_install_bash_and_powershell_refuse_text_over_identical_manifest(self):
        folder = self.installer_fixture()
        before = {p.name: p.read_bytes() for p in folder.iterdir()}
        fake = self.root / 'synthetic-gpgv'
        fake.write_text('#!/bin/sh\ncat "$D32_TEST_STATUS"\n', encoding='ascii', newline='\n')
        fake.chmod(0o700)
        # Replace only the subprocess transport in a private script copy. The
        # shipping status parser and every subsequent manifest check run intact.
        source = (ROOT / 'install/verify-release.ps1').read_text(encoding='utf-8-sig')
        begin = source.index('function Invoke-Gpgv(')
        end = source.index('\nfunction Get-Sha256(', begin)
        replacement = '''function Invoke-Gpgv([string[]]$GArgs) {
    return @{Code=0;Out=[IO.File]::ReadAllText($env:D32_TEST_STATUS);Err=''}
}
'''
        instrumented = self.root / 'synthetic-transport-verify.ps1'
        instrumented.write_text(source[:begin] + replacement + source[end:], encoding='utf-8')
        environment = self.environment()
        commands = {
            'bash': [BASH, '--noprofile', '--norc', msys(ROOT / 'install/verify-release.sh'),
                     '--key-dir', msys(folder), '--gpgv', msys(fake), '--signature-only', msys(folder)],
            'powershell': [POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                           instrumented, '-PartsDir', folder, '-KeyDir', folder, '-Gpgv', fake, '-SignatureOnly']}
        for shell, command in commands.items():
            for signature_class in ('00', '01'):
                with self.subTest(shell=shell, signature_class=signature_class):
                    self.status.write_text(status(signature_class), encoding='ascii')
                    result = self.run_process(command, environment)
                    self.assertEqual(result.returncode, 0 if signature_class == '00' else 4, result.stdout + result.stderr)
                    if signature_class == '01':
                        self.assertIn('not 00', result.stdout + result.stderr)
                    self.assertEqual(before, {p.name: p.read_bytes() for p in folder.iterdir()})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scratch-root', required=True, type=Path)
    args, rest = parser.parse_known_args()
    SCRATCH = args.scratch_root
    SCRATCH.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0], *rest])
