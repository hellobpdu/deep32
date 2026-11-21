"""Offline Bash/PS5.1 selected-disc and key-format integration matrix."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('release_suite', HERE / 'test_release_tools.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools-dir', type=Path, required=True)
    parser.add_argument('--scratch-root', type=Path, required=True)
    parser.add_argument('--bash', type=Path, default=Path('C:/Program Files/Git/bin/bash.exe'))
    parser.add_argument('--powershell', type=Path, default=Path('C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'))
    parser.add_argument('--gpg', type=Path, default=Path('C:/Program Files/GnuPG/bin/gpg.exe'))
    parser.add_argument('--gpgconf', type=Path, default=Path('C:/Program Files/GnuPG/bin/gpgconf.exe'))
    parser.add_argument('--keygen-gpg', type=Path, default=Path('C:/Program Files/GnuPG/bin/gpg.exe'))
    parser.add_argument('--auth-only', action='store_true')
    args = parser.parse_args()
    args.baseline_only = False
    suite = module.Suite(args)
    try:
        suite.setup()
        source = suite.assemble('synthetic-release', version=module.VERSION)
        suite.sign(source, 'synthetic')
        armor = suite.root / 'synthetic-public.asc'
        secret = suite.root / 'synthetic-SECRET-TEST-ONLY.asc'
        suite.gpg('export-armoured', ['--test-native', '--batch', '--armor', '--output', armor, '--export', suite.fingerprint])
        suite.gpg('export-synthetic-secret-negative-fixture', ['--test-native', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '', '--armor', '--output', secret, '--export-secret-keys', suite.fingerprint])
        malformed = suite.root / 'synthetic-malformed.asc'
        malformed.write_bytes(b'-----BEGIN PGP PUBLIC KEY BLOCK-----\nnot-a-key\n')
        before_key = module.digest(suite.key)
        for index, executable in enumerate(dict.fromkeys((args.keygen_gpg, args.gpg))):
            suite.py('explicit-native-gpg-' + str(index), suite.tools / 'deep32_get_manifest.py',
                     ['authenticate', source, armor, suite.fingerprint, executable])
        if args.auth_only:
            print('PASS: %d assertions; %d commands' % (len(suite.checks), len(suite.commands)), flush=True)
            return
        for kind in ('sh', 'ps1'):
            runner = suite.shell if kind == 'sh' else suite.ps
            tool = suite.tools / ('deep32-get.' + kind)
            for label, key in (('binary', suite.key), ('armoured', armor)):
                destination = suite.root / ('synthetic-' + label + '-' + kind)
                arguments = ['disc3', '--from', source, '--release', 'v0.1.0-alpha.1', '--key', key, '--fingerprint', suite.fingerprint]
                arguments += ['--gpg', args.gpg]
                runner('get-' + label + '-' + kind, tool, arguments)
                runner('get-repeat-' + label + '-' + kind, tool, arguments)
            runner('reject-wrong-pin-' + kind, tool,
                   ['disc3', '--from', source, '--release', 'v0.1.0-alpha.1', '--key', armor, '--fingerprint', '0'*40], 1)
            runner('reject-secret-key-' + kind, tool,
                   ['disc3', '--from', source, '--release', 'v0.1.0-alpha.1', '--key', secret, '--fingerprint', suite.fingerprint], 1)
            runner('reject-malformed-key-' + kind, tool,
                   ['disc3', '--from', source, '--release', 'v0.1.0-alpha.1', '--key', malformed, '--fingerprint', suite.fingerprint], 1)
            selected = suite.root / ('selected-' + kind)
            selected.mkdir()
            data = json.loads((source / 'ASSEMBLY.json').read_text())
            chosen = next(d for d in data['discs'] if d['role'] == 'disc3')
            for name in ['ASSEMBLY.json', 'SHA256SUMS', 'SHA256SUMS.sig'] + [p['name'] for p in chosen['payloads']]:
                shutil.copyfile(source / name, selected / name)
            verifier = suite.tools / ('verify-release.' + kind)
            options = [selected, suite.fingerprint, '--disc', 'disc3', '--key', armor, '--release', 'v0.1.0-alpha.1', '--legacy-split'] if kind == 'sh' else [
                '-ReleaseDirectory', selected, '-Fingerprint', suite.fingerprint, '-Disc', 'disc3', '-Key', armor, '-Release', 'v0.1.0-alpha.1', '-LegacySplit']
            runner('verify-selected-' + kind, verifier, options)
            extra = selected / 'unexpected.txt'
            extra.write_bytes(b'synthetic unsafe extra')
            runner('verify-selected-extra-refused-' + kind, verifier, options, 1)
            extra.unlink()
            part = selected / chosen['payloads'][0]['name']
            retained = part.read_bytes()
            part.unlink()
            runner('verify-selected-missing-refused-' + kind, verifier, options, 1)
            part.write_bytes(retained[:-1] + b'x')
            runner('verify-selected-corrupt-refused-' + kind, verifier, options, 1)
            part.write_bytes(retained)
            shutil.copyfile(selected / chosen['payloads'][0]['name'], selected / chosen['file'])
            runner('verify-selected-bad-joined-refused-' + kind, verifier, options, 1)
        # Both public-key formats use native GnuPG without changing the input key.
        suite.check('input-public-key-unchanged', before_key == module.digest(suite.key))
        # A separate aligned raw installer keeps conversion tests small and
        # exercises the actual public entrypoints on both shells.
        vm_input = suite.inputs / 'disc1.img'
        vm_input.write_bytes(bytes(1048576))
        module.text(suite.inputs / 'disc1.sums', module.digest(vm_input) + '  disc1.img\n')
        vm_source = suite.root / 'synthetic-vm-release'
        vm_args = suite.assemble_args(vm_source, version=module.VERSION)
        vm_args[vm_args.index('--part-size-bytes') + 1] = '1048576'
        suite.py('vm-release-assemble', suite.tools / 'release_assemble.py', vm_args)
        suite.sign(vm_source, 'vm-release')
        for kind in ('sh', 'ps1'):
            runner = suite.shell if kind == 'sh' else suite.ps
            for hypervisor in ('hyper-v', 'kvm'):
                case_root = suite.root / ('vm-' + hypervisor + '-' + kind)
                case_root.mkdir()
                destination = case_root / 'deep32-downloads'
                suite.command_cwd = case_root
                vm_command = ['disc1', '--prepare-vm', '--hypervisor', hypervisor,
                    '--from', vm_source, '--release', 'v0.1.0-alpha.1', '--key', armor, '--fingerprint', suite.fingerprint]
                runner('vm-prepare-' + hypervisor + '-' + kind, suite.tools / ('deep32-get.' + kind), vm_command)
                prepared = next(destination.glob('*-' + hypervisor))
                installer = prepared / ('installer.vhd' if hypervisor == 'hyper-v' else 'installer.img')
                suite.check('vm-bytes-' + hypervisor + '-' + kind,
                    installer.read_bytes()[:1048576] == vm_input.read_bytes() and
                    installer.stat().st_size == 1048576 + (512 if hypervisor == 'hyper-v' else 0))
                suite.check('vm-command-only-' + hypervisor + '-' + kind,
                    (prepared / 'commands.txt').is_file() and not (prepared / 'target.vhdx').exists() and not (prepared / 'target.qcow2').exists())
                runner('vm-overwrite-refused-' + hypervisor + '-' + kind, suite.tools / ('deep32-get.' + kind), vm_command, 1)
                suite.command_cwd = suite.root
        print('PASS: %d assertions; %d commands' % (len(suite.checks), len(suite.commands)), flush=True)
    finally:
        suite.cleanup()


if __name__ == '__main__':
    main()
