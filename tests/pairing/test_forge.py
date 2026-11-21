#!/usr/bin/env python3
"""Offline Forge caller and carrier checks; all generated material is synthetic.

Usage: python -I -B tests/pairing/test_forge.py --scratch SCRATCH
No installed files, sockets, service processes, models or network are used.
"""
import argparse
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
KIT = REPO / 'src/gpu-pack'
SCRATCH = os.environ.get('D32_PAIRING_TEST_SCRATCH')
TOKEN = hashlib.sha256(b'T-0187 synthetic Forge caller credential').hexdigest()
PUBLIC = ('-----BEGIN PUBLIC KEY-----\n'
          + base64.b64encode(bytes.fromhex('302a300506032b6570032100') + bytes([17]) * 32).decode()
          + '\n-----END PUBLIC KEY-----\n')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(KIT / 'build'))
sys.path.insert(0, str(KIT / 'pairing'))
status_module = load('forge_test_status', KIT / 'firstboot/status.py')
with mock.patch.dict(sys.modules, {'pwd': types.ModuleType('pwd'), 'status': status_module}):
    firstboot = load('forge_test_firstboot', KIT / 'firstboot/firstboot.py')
thin = load('forge_test_thin', KIT / 'build/thin_forms.py')
rootfs = load('forge_test_rootfs', KIT / 'build/rootfs.py')
pack = load('forge_test_pack', KIT / 'build/pack.py')
provision = load('forge_test_provision', KIT / 'forms/provision.py')
installed = load('forge_test_installed', KIT / 'firstboot/installed-inputs.py')
status_log = load('forge_test_log', KIT / 'containers/door/status-log.py')
from deep32net import pairlock, store


class Status:
    def __init__(self):
        self.updates, self.state = [], {}

    def update(self, stage, state, **fields):
        self.state = dict(stage=stage, state=state, **fields)
        self.updates.append(self.state)

    def repeat(self):
        pass


class Sink(io.BytesIO):
    def close(self):
        self.saved = self.getvalue()


class Process:
    def __init__(self, code=0):
        self.returncode, self.stdin = code, Sink()

    def poll(self):
        return self.returncode


class ForgeTests(unittest.TestCase):
    def setUp(self):
        if not SCRATCH:
            self.fail('Pass --scratch or D32_PAIRING_TEST_SCRATCH for synthetic fixtures')
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-forge-', dir=SCRATCH)
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.state = self.work / 'node'
        private = self.state / 'private'
        private.mkdir(parents=True, mode=0o700)
        self.state.chmod(0o700)
        self.token, self.public = private / 'forge-token', private / 'kb-public.pem'
        self.token.write_text(TOKEN + '\n', encoding='ascii', newline='\n')
        self.public.write_text(PUBLIC, encoding='ascii', newline='\n')
        self.token.chmod(0o600)
        self.public.chmod(0o600)
        for patcher in (mock.patch.object(firstboot, 'STATE', self.state),
                        mock.patch.object(firstboot, 'KIT', KIT),
                        mock.patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')),
                        mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('process forbidden')),
                        mock.patch.object(subprocess, 'run', side_effect=AssertionError('process forbidden'))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_caller_request_is_stdin_and_requires_commit(self):
        process, status = Process(), Status()
        with mock.patch.object(firstboot, 'pairing_committed', side_effect=[False, True]), \
                mock.patch.object(subprocess, 'Popen', return_value=process) as start:
            firstboot.pair(status, 'docker', {}, self.token, self.public)
        args, options = start.call_args
        self.assertEqual(args[0], [str(Path('/opt/deep32-forge/pairing/bin/pairing-helper')), 'pair-forge'])
        self.assertEqual(options, dict(stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL))
        request = json.loads(process.stdin.saved)
        self.assertEqual(set(request), {'code', 'expires_seconds', 'attempts', 'form', 'transport',
                                        'publish', 'token_path', 'kb_public_path', 'state_dir'})
        self.assertRegex(request['code'], r'^[A-Z2-7]{10}$')
        self.assertEqual((request['expires_seconds'], request['attempts']), (300, 1))
        self.assertEqual((request['form'], request['transport'], request['publish']),
                         ('docker', 'tailnet', 'serve-https'))
        self.assertEqual(request['state_dir'], str(self.state / 'pairing'))
        self.assertEqual(request['token_path'], str(self.token))
        self.assertNotIn(TOKEN, process.stdin.saved.decode())
        self.assertEqual(status.state, dict(stage='pairing', state='done'))

    def test_success_exit_without_commit_fails_and_clears_code(self):
        status = Status()
        with mock.patch.object(firstboot, 'pairing_committed', return_value=False), \
                mock.patch.object(subprocess, 'Popen', return_value=Process()):
            with self.assertRaisesRegex(RuntimeError, 'durable commit'):
                firstboot.pair(status, 'iso', {}, self.token, self.public)
        self.assertEqual(status.state, dict(stage='pairing', state='failed'))

    def test_missing_helper_clears_code(self):
        status = Status()
        with mock.patch.object(firstboot, 'pairing_committed', return_value=False), \
                mock.patch.object(subprocess, 'Popen', side_effect=FileNotFoundError):
            with self.assertRaises(FileNotFoundError):
                firstboot.pair(status, 'iso', {}, self.token, self.public)
        self.assertEqual(status.state, dict(stage='pairing', state='failed'))

    def test_legacy_marker_does_not_skip_pairing(self):
        (self.state / 'paired').write_text('paired\n', encoding='ascii')
        self.assertFalse(firstboot.pairing_committed({}, self.token, self.public))

    def test_durable_metadata_and_claim_are_validated(self):
        state = self.state / 'pairing'
        state.mkdir(mode=0o700)
        (state / 'identity').mkdir(mode=0o700)
        row = store.record('forge', 'joined', 'workstation-123456789abc', 'workstation',
                           '2' * 64, '1' * 64, 'tailnet', 1700000000,
                           publish='serve-https', given=['forge-token', 'kb-public.pem'])
        store.save_record(state, row)
        claim = state / 'pairing/forge-joined.pairlock'
        pairlock.save(claim, pairlock.State())
        with mock.patch('deep32net.identity.ensure_identity', return_value=types.SimpleNamespace(pin='1' * 64)):
            self.assertTrue(firstboot.pairing_committed({}, self.token, self.public))
            with self.assertRaisesRegex(RuntimeError, 'conflicts'):
                firstboot.pairing_committed({'publish': 'tailnet-plain'}, self.token, self.public)
            pairlock.save(claim, pairlock.State(in_progress=1))
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                firstboot.pairing_committed({}, self.token, self.public)

    def test_health_authenticates_both_doors_without_proxy_or_redirect(self):
        requests = []
        response = mock.MagicMock()
        response.__enter__.return_value.status = 200
        opener = mock.Mock()
        opener.open.side_effect = lambda req, **kw: requests.append(req) or response
        with mock.patch.object(subprocess, 'run') as run, \
                mock.patch.object(firstboot.urllib.request, 'build_opener', return_value=opener) as build:
            firstboot.start_models(Status(), self.token)
        self.assertEqual([r.full_url for r in requests],
                         ['http://127.0.0.1:8081/health', 'http://127.0.0.1:8082/health'])
        self.assertTrue(all(r.get_header('Authorization') == 'Bearer ' + TOKEN for r in requests))
        self.assertNotIn(TOKEN, repr(run.call_args))
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsNone(build.call_args.args[1].redirect_request(None, None, 302, '', {}, 'https://example.invalid'))

    def test_general_status_log_strips_protected_fields(self):
        for field in ('pair=KLMNP23456', 'signin=https://login.tailscale.com/synthetic'):
            line = 'D32S/1 1 forge boot=12345678 stage=pairing state=waiting ' + field + ' msg=Waiting\n'
            result = status_log.log_line(line)
            self.assertNotIn(field, result)
            self.assertIn('state=waiting', result)
        self.assertIsNone(status_log.log_line('D32S/1 1 forge\nsecond line\n'))

    def test_main_warms_models_before_pairing(self):
        fake_kit = self.work / 'kit'
        first = fake_kit / 'firstboot'
        first.mkdir(parents=True)
        (first / 'installed-inputs.py').write_text(
            'def pins(): return {}\ndef verify_payload(*a, **kw): return "synthetic"\n'
            'def load_images(*a, **kw): return "synthetic"\n', encoding='ascii')
        form = self.work / 'form'
        form.write_text('docker\n', encoding='ascii')
        events = []
        path = lambda name: form if name == '/etc/deep32-forge/form' else Path(name)
        with mock.patch.object(firstboot, 'KIT', fake_kit), mock.patch.object(firstboot, 'Path', side_effect=path), \
                mock.patch.object(firstboot, 'Status', Status), mock.patch.object(firstboot.os, 'umask'), \
                mock.patch.object(firstboot, 'settings', return_value={}), \
                mock.patch.object(firstboot, 'make_identities', return_value=(self.token, self.public)), \
                mock.patch.object(firstboot, 'run'), mock.patch.object(subprocess, 'run'), \
                mock.patch.object(firstboot, 'local_account'), mock.patch.object(firstboot, 'signin', return_value='synthetic'), \
                mock.patch.object(firstboot, 'start_models', side_effect=lambda *a: events.append('authenticated-health')), \
                mock.patch.object(firstboot, 'pair', side_effect=lambda *a: events.append('pair')), \
                mock.patch.object(sys, 'argv', ['firstboot.py']):
            firstboot.main()
        self.assertEqual(events, ['authenticated-health', 'pair'])

    def test_thin_archive_contains_complete_executable_runtime(self):
        output = self.work / 'thin.tar'
        thin.docker(KIT, output, 0)
        with tarfile.open(output) as archive:
            for source in thin.pairing_files(KIT):
                entry = archive.getmember('deep32/kit/' + source.relative_to(KIT).as_posix())
                self.assertEqual(entry.mode, thin.runtime_mode(source))
                self.assertEqual(archive.extractfile(entry).read(), source.read_bytes())
        self.assertFalse(any('reference' in name for name in thin.runtime_members(KIT)))

    def test_missing_runtime_refused_before_install(self):
        kit = self.work / 'incomplete'
        (kit / 'build').mkdir(parents=True)
        (kit / 'build/thin_forms.py').write_bytes((KIT / 'build/thin_forms.py').read_bytes())
        target = self.work / 'not-written'
        with self.assertRaisesRegex(ValueError, 'pairing runtime'):
            provision.copy_kit(kit, target)
        self.assertFalse(target.exists())

    def test_pack_provision_copies_runtime_without_form_archives(self):
        stage = self.work / 'stage'
        payload = stage / 'payload'
        payload.mkdir(parents=True)
        (payload / 'synthetic.txt').write_text('synthetic payload\n', encoding='ascii')
        (stage / 'pins.json').write_text('{"release_profile":"pack","packages":[]}', encoding='ascii')
        rows = pack.members(payload)
        (stage / 'PAYLOAD.json').write_text(json.dumps(rows), encoding='ascii')
        docker, wsl = self.work / 'docker.tar', self.work / 'wsl.tar'
        for path in (docker, wsl):
            path.write_bytes(b'synthetic thin form')
        media = self.work / 'media'
        pack.prepare(stage, KIT, docker, wsl, media, 0)
        target = self.work / 'installed'
        provision.copy_kit(media, target)
        for path in thin.pairing_files(KIT):
            self.assertEqual((target / path.relative_to(KIT)).read_bytes(), path.read_bytes())
        self.assertFalse((target / 'forms/forge-docker.tar').exists())
        self.assertFalse((target / 'deep32').exists())
        provision.copy_kit(media, target)

    def test_wsl_overlay_preserves_launcher_modes(self):
        base = self.work / 'base.tar'
        inventory = b'Package: python3\nStatus: install ok installed\nVersion: 3.12.14\n\n'
        with tarfile.open(base, 'w') as archive:
            entry = tarfile.TarInfo('var/lib/dpkg/status')
            entry.size = len(inventory)
            archive.addfile(entry, io.BytesIO(inventory))
        payload = self.work / 'stage'
        evidence = payload / 'payload/evidence'
        evidence.mkdir(parents=True)
        (evidence / 'rootfs.json').write_text(json.dumps(dict(
            sha256=rootfs.digest(base), form='wsl-clean-ubuntu-24.04.5')), encoding='ascii')
        (payload / 'pins.json').write_text('{"release_profile":"pack","packages":[]}', encoding='ascii')
        output = self.work / 'wsl.tar'
        rootfs.build(base, payload, output, 0, KIT)
        with tarfile.open(output) as archive:
            for path in thin.pairing_files(KIT):
                entry = archive.getmember('opt/deep32-forge/' + path.relative_to(KIT).as_posix())
                self.assertEqual(entry.mode, thin.runtime_mode(path))
                self.assertEqual(archive.extractfile(entry).read(), path.read_bytes())

    def test_payload_cache_identity_binds_pairing_runtime(self):
        kit = self.work / 'cache-kit'
        files = thin.runtime_files(KIT)
        for source in files:
            target = kit / source.relative_to(KIT)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        manifest = self.work / 'PAYLOAD.json'
        manifest.write_text('[]', encoding='ascii')
        with mock.patch.object(installed, 'KIT', kit), mock.patch.object(installed, 'MANIFEST', manifest):
            before = installed.payload_identity()
            launcher = kit / 'pairing/bin/pairing-helper'
            launcher.write_bytes(launcher.read_bytes() + b'\n# synthetic cache change\n')
            paired = installed.payload_identity()
            self.assertNotEqual(before, paired)
            validator = kit / 'autoinstall/render-settings.py'
            validator.write_bytes(validator.read_bytes() + b'\n# synthetic validator change\n')
            validated = installed.payload_identity()
            self.assertNotEqual(paired, validated)
            lifecycle = kit / 'forms/provision.py'
            lifecycle.write_bytes(lifecycle.read_bytes() + b'\n# synthetic lifecycle change\n')
            self.assertNotEqual(validated, installed.payload_identity())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch', required=True, type=Path)
    args, rest = parser.parse_known_args()
    scratch = args.scratch.resolve()
    if scratch == Path(scratch.anchor) or scratch == REPO or REPO in scratch.parents:
        parser.error('--scratch must be outside the repository')
    scratch.mkdir(parents=True, exist_ok=True)
    SCRATCH = str(scratch)
    unittest.main(argv=[sys.argv[0], *rest])
