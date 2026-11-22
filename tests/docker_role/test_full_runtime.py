"""Offline full-role topology, signed-import adapter and real local C8 fixtures."""
import base64
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / 'src/gpu-pack'
FULL = KIT / 'containers/full'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


runtime = module('test_full_config', FULL / 'runtime.py')
launch = module('test_full_launch', FULL / 'launch.py')
enrol = module('test_full_enrol', FULL / 'local_enrol.py')
build = module('test_full_build', KIT / 'build/full_images.py')
host = module('test_full_host', KIT / 'forms/host-requirements.py')
IMAGES = {name: 'sha256:' + hashlib.sha256(name.encode()).hexdigest() for name in runtime.IMAGE_KEYS}
BRIDGE = {'schema': 'deep32.model-bridge.v1', 'GPU_EXT_EMBED_MODEL_ID': 'fixture/embed',
          'GPU_EXT_RERANK_MODEL_ID': 'fixture/reranker', 'GPU_EXT_EMBED_REVISION': 'a' * 40,
          'GPU_EXT_RERANK_REVISION': 'b' * 40}
CONFIG = {'models': {'tei': {'revision': 'a' * 40}},
          'runtime': {'reranker_dtype': 'float16'},
          'tailscale_dns_name': 'fixture.fixture.ts.net.', 'full': {'model_bridge': BRIDGE, 'minimum_data_free_bytes': 12345}}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-full-',
            dir=os.environ.get('D32_DOCKER_TEST_ROOT', str(Path(__file__).resolve().parent)))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        if os.name == 'nt':
            host.windows_private(self.root, create=True)
        self.no_socket = contextlib.ExitStack()
        self.no_socket.enter_context(patch.object(socket, 'socket', side_effect=AssertionError('real sockets forbidden')))
        self.no_socket.enter_context(patch.object(socket, 'create_connection', side_effect=AssertionError('real connections forbidden')))
        self.addCleanup(self.no_socket.close)


class Topology(Fixture):
    def render(self, config=None, images=None, data='/opt/deep32', kit='/media/forge/kit'):
        return runtime.prepare_config(kit, '/media/forge/payload', data, '/media/content',
                                      images or IMAGES, CONFIG if config is None else config)

    def test_same_actual_services_and_no_pull_or_build(self):
        value = self.render()
        self.assertEqual(set(value['services']), set(runtime.SERVICES) | {'importer'})
        for row in value['services'].values():
            self.assertEqual(row['pull_policy'], 'never')
            self.assertTrue(row['image'].startswith('sha256:'))
            self.assertNotIn('build', row)
        self.assertTrue(all(row['internal'] for row in value['networks'].values()))
        self.assertEqual(value['services']['embeddings']['gpus'], 'all')
        self.assertEqual(value['services']['reranker']['gpus'], 'all')

    def test_only_authenticated_loopback_publication(self):
        value = self.render()
        ports = {name: row['ports'] for name, row in value['services'].items() if 'ports' in row}
        self.assertEqual(ports, {'door': ['127.0.0.1:8081:8081', '127.0.0.1:8082:8082'],
                                 'gateway': ['127.0.0.1:8766:8766']})
        self.assertNotIn('models', value['services']['engine']['networks'])
        env = value['services']['engine']['environment']
        self.assertEqual(env['RAG_EMBEDDING_URL'], 'http://model-bridge:9081/v1/embeddings')
        self.assertEqual(env['RAG_RERANK_URL'], 'http://model-bridge:9082/v1/rerank')
        self.assertEqual(env['RAG_ROOT'], '/var/lib/deep32/content/kb/root')
        self.assertEqual(value['services']['model-bridge']['secrets'], ['forge-token'])
        self.assertNotIn('RAG_CORE_API_TOKEN', json.dumps(value))

    def test_persistent_ro_content_and_loopback_importer(self):
        value = self.render(data='/opt/deep32 space')
        row = value['services']['importer']
        self.assertEqual(row['network_mode'], 'service:qdrant')
        disc = next(v for v in row['volumes'] if v['target'] == '/media/content')
        self.assertTrue(disc['read_only'])
        self.assertFalse(disc['bind']['create_host_path'])
        store = next(v for v in row['volumes'] if v['target'] == '/var/lib/deep32/content')
        self.assertEqual(store['source'], '/opt/deep32 space/content')
        self.assertFalse(store['read_only'])
        engine = next(v for v in value['services']['engine']['volumes'] if v['target'] == '/var/lib/deep32/content')
        self.assertTrue(engine['read_only'])
        self.assertEqual(row['profiles'], ['import'])

    def test_health_requires_actual_model_and_index_readiness(self):
        value = self.render()['services']
        self.assertEqual(value['model-bridge']['depends_on']['door']['condition'], 'service_healthy')
        self.assertEqual(value['engine']['depends_on']['index-health']['condition'], 'service_healthy')
        self.assertIn('--check', value['gateway']['healthcheck']['test'])
        self.assertEqual(value['gateway']['healthcheck']['test'][-1], 'gateway')
        self.assertEqual(value['model-bridge']['user'], '0:0')

    def test_unpinned_or_missing_images_refused(self):
        for key in IMAGES:
            images = dict(IMAGES)
            images[key] = 'upstream/latest:latest'
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.render(images=images)

    def test_bad_paths_fail_without_output(self):
        for path in ('/', 'relative', '/tmp/../app', '/tmp/$HOME', '/tmp//app', '/tmp/app\\x', '/media'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.render(data=path)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_owned_release_kit_is_allowed(self):
        value = self.render(kit='/opt/deep32/releases/ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff/kit')
        self.assertEqual(value['services']['door']['volumes'][0]['source'],
            '/opt/deep32/releases/ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff/kit/containers/full/health.py')

    def test_arbitrary_owned_kit_overlap_is_refused(self):
        with self.assertRaises(ValueError):
            self.render(kit='/opt/deep32/releases/fixture/kit')

    def test_invalid_bridge_is_rejected_before_writes(self):
        for field, val in (('GPU_EXT_EMBED_MODEL_ID', ''), ('GPU_EXT_RERANK_REVISION', ''),
                           ('GPU_EXT_READ_TIMEOUT', 'nan'), ('GPU_EXT_TOKEN', 'fixture')):
            config = copy.deepcopy(CONFIG)
            config['full']['model_bridge'][field] = val
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.render(config=config)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_windows_long_binds_use_the_same_linux_services(self):
        value = runtime.prepare_config('D:/Media/GPU/kit', 'D:/Media/GPU/payload',
            'C:/Deep32 data/install', 'E:/Signed content', IMAGES, CONFIG)
        self.assertEqual(set(value['services']), set(runtime.SERVICES) | {'importer'})
        row = value['services']['importer']
        media = next(v for v in row['volumes'] if v['target'] == '/media/content')
        self.assertEqual(media['source'], 'E:/Signed content')
        self.assertTrue(media['read_only'])
        self.assertEqual(value['secrets']['forge-token']['file'], 'C:/Deep32 data/install/private/forge-token')
        self.assertTrue(all('docker.sock' not in str(v) for v in value['services'].values()))
        for row in value['services'].values():
            self.assertNotIn('privileged', row)
            self.assertEqual(row['pull_policy'], 'never')

    def test_windows_drive_paths_reject_mixing_traversal_and_casefold_overlap(self):
        for kit, payload, data, content in (
                ('D:/kit', '/media/payload', 'C:/Deep32', 'E:/Content'),
                ('D:/kit', 'D:/payload', 'C:/Deep32/../private', 'E:/Content'),
                ('D:/kit', 'C:/deep32/Payload', 'C:/Deep32', 'E:/Content'),
                ('D:/kit', 'D:/payload', 'C:/Deep32', 'C:/DEEP32/Content'),
                ('D:/kit', 'D:/payload', 'C:/Deep32/$HOME', 'E:/Content')):
            with self.subTest(data=data, content=content), self.assertRaises(ValueError):
                runtime.prepare_config(kit, payload, data, content, IMAGES, CONFIG)


    def test_embedding_golden_probe_supported(self):
        config = copy.deepcopy(CONFIG)
        del config['full']['model_bridge']['GPU_EXT_EMBED_REVISION']
        config['full']['model_bridge'].update(GPU_EXT_EMBED_PROBE_INPUT='synthetic query', GPU_EXT_EMBED_PROBE_SHA256='c' * 64)
        self.render(config=config)

    def test_probe_always_needs_a_probe_file(self):
        image_probes = '/opt/deep32/model-bridge/gpu-probes.json'
        for fields, renders in (({'GPU_EXT_PROBE_ALWAYS': 'no'}, True),
                                ({'GPU_EXT_PROBE_ALWAYS': 'yes', 'GPU_EXT_RERANK_PROBE': image_probes}, True),
                                ({'GPU_EXT_PROBE_ALWAYS': 'yes'}, False), ({'GPU_EXT_PROBE_ALWAYS': 'maybe'}, False)):
            config = copy.deepcopy(CONFIG)
            config['full']['model_bridge'].update(fields)
            with self.subTest(fields=fields):
                if renders:
                    self.render(config=config)
                else:
                    with self.assertRaises(ValueError):
                        self.render(config=config)

    def test_reranker_dtype_matches_the_service(self):
        # reranker_api.py, the Forge form and first boot accept exactly float16 and float32
        config = copy.deepcopy(CONFIG)
        config['runtime']['reranker_dtype'] = 'float32'
        self.render(config=config)
        config['runtime']['reranker_dtype'] = 'bfloat16'
        with self.assertRaises(ValueError):
            self.render(config=config)


class ImportAdapter(Fixture):
    def test_first_import_uses_same_signed_importer(self):
        self.assertEqual(launch.import_arguments('/media/content', self.root / 'absent'),
            ['--node', 'forge', '--folder', '/media/content', '--qdrant-url', 'http://127.0.0.1:6333'])

    def test_durable_resume_uses_recorded_source(self):
        journal = self.root / 'state.json'
        journal.write_text(json.dumps({'device': 'folder', 'source_folder': '/media/content',
            'qdrant_url': 'http://127.0.0.1:6333', 'outcome': None}))
        self.assertIn('--resume', launch.import_arguments('/media/content', journal))
        state = json.loads(journal.read_text()); state['source_folder'] = '/another'
        journal.write_text(json.dumps(state))
        with self.assertRaises(ValueError):
            launch.import_arguments('/media/content', journal)

    def test_completed_import_is_rechecked_and_source_is_fixed(self):
        journal = self.root / 'state.json'
        journal.write_text(json.dumps({'outcome': 'imported'}))
        self.assertIn('--folder', launch.import_arguments('/media/content', journal))
        with self.assertRaises(ValueError):
            launch.import_arguments('/untrusted', journal)


class LocalC8(Fixture):
    def identity(self, state, kind, openssl=None):
        seed = hashlib.sha256(str(state).encode()).digest()
        der = b'\x30\x7e' + (seed * 4)[:126]
        from deep32net import files
        directory = files.private_dir(Path(state) / 'identity')
        for name, raw in (('id', (kind + '-' + seed.hex()[:12] + '\n').encode('ascii')),
                          ('cert.pem', base64.b64encode(der)), ('key.pem', b'SYNTHETIC IDENTITY KEY ONLY\n')):
            path = directory / name
            if not path.exists():
                files.atomic_write(path, raw, overwrite=False)
        return SimpleNamespace(id=kind + '-' + seed.hex()[:12], pin=hashlib.sha256(der).hexdigest(),
                               der=lambda: der, cert_path=directory / 'cert.pem', key_path=directory / 'key.pem')

    def keys(self, private):
        public = private / 'kb-public.pem'
        if not public.exists():
            der = bytes.fromhex('302a300506032b6570032100') + bytes(range(32))
            public.write_text('-----BEGIN PUBLIC KEY-----\n' + base64.b64encode(der).decode() + '\n-----END PUBLIC KEY-----\n',encoding='ascii', newline='\n')
            (private / 'kb-private.pem').write_text('SYNTHETIC TEST KEY ONLY\n',encoding='ascii', newline='\n')
            if os.name == 'posix':
                public.chmod(0o600); (private / 'kb-private.pem').chmod(0o600)
        return public

    def test_actual_shared_c8_enrolment_is_idempotent_and_secret_free(self):
        messages = []
        result = enrol.local_enrol(self.root, KIT, self.keys, self.identity, messages.append)
        before = {p.name: p.read_bytes() for p in (self.root / 'private').iterdir()}
        again = enrol.local_enrol(self.root, KIT, self.keys, self.identity, messages.append)
        after = {p.name: p.read_bytes() for p in (self.root / 'private').iterdir()}
        self.assertEqual(before, after)
        self.assertEqual(result, again)
        self.assertEqual(result['transport'], 'protected-process-channel')
        self.assertGreaterEqual(len(messages), 2)
        text = json.dumps(result) + '\n'.join(messages)
        for name in ('forge-token', 'core-token', 'mcp-token'):
            self.assertNotIn(before[name].decode().strip(), text)
        records = list((self.root / 'pairing').rglob('peers/*/*.json'))
        self.assertEqual(len(records), 4)
        forge = json.loads((self.root / 'private/local-enrolment.json').read_text())
        self.assertTrue(forge['forge_peer'].startswith('forge-'))
        if os.name == 'posix':
            self.assertEqual((self.root / 'private/forge-token').stat().st_mode & 0o777, 0o400)

    def test_committed_missing_secret_is_never_regenerated(self):
        enrol.local_enrol(self.root, KIT, self.keys, self.identity, lambda _: None)
        credential = self.root / 'private/core-token'
        if os.name == 'nt':
            credential.chmod(0o600)
        credential.unlink()
        with self.assertRaisesRegex(ValueError, 'Restore'):
            enrol.local_enrol(self.root, KIT, self.keys, self.identity, lambda _: None)
        self.assertFalse(credential.exists())

    def test_missing_committed_identity_is_never_regenerated(self):
        enrol.local_enrol(self.root, KIT, self.keys, self.identity, lambda _: None)
        key = self.root / 'pairing/engine/identity/key.pem'
        key.unlink()
        with self.assertRaisesRegex(ValueError, 'will not be regenerated'):
            enrol.local_enrol(self.root, KIT, self.keys, self.identity, lambda _: None)
        self.assertFalse(key.exists())

    def test_wrong_code_fails_shared_confirmation_and_does_not_handover(self):
        enrol._pairing(KIT)
        channel = module('test_local_channel', FULL / 'local_channel.py')
        from deep32net import files
        provider = files.private_dir(self.root / 'provider')
        consumer = files.private_dir(self.root / 'consumer')
        with self.assertRaises(Exception):
            channel.pair_local('gateway', provider, consumer, identity_factory=self.identity,
                               emit=lambda _: None, joiner_code='AAAAAAAAAA')
        self.assertEqual(list(consumer.rglob('mcp-token')), [])
        self.assertEqual(list(provider.rglob('peers/*/*.json')), [])
        locks = list(consumer.rglob('*.pairlock'))
        self.assertEqual(len(locks), 1)


class Storage(Fixture):
    def test_windows_desktop_preflight_forces_local_linux_wsl2_and_signed_driver_floor(self):
        payload = self.root / 'payload'
        payload.mkdir()
        (payload / 'synthetic').write_bytes(b'synthetic storage fixture')
        kit = self.root / 'kit'
        kit.mkdir()
        (kit / 'pins.json').write_text(json.dumps({'runtime': {
            'mineru_minimum_driver': '500.0', 'tei_minimum_driver': '501.0', 'cuda_minimum_driver': '502.0',
            'windows_cuda13_driver_build': '580.0', 'docker_minimum_version': '20.10.0',
            'compose_minimum_version': '2.30.0'}}), encoding='ascii')
        backing = self.root / 'desktop-disk'
        backing.mkdir()
        calls = []
        info = {'Runtimes': {'nvidia': {}}, 'DockerRootDir': '/var/lib/docker', 'OSType': 'linux',
                'OperatingSystem': 'Docker Desktop', 'KernelVersion': '6.6.0-microsoft-standard-WSL2'}
        driver = ['580.0']
        def run(argv, **kwargs):
            calls.append(argv)
            if argv[0] == 'tailscale.exe':
                output = json.dumps({'BackendState': 'Running', 'Self': {'HostName': 'synthetic', 'Online': True}})
            elif argv[0] == 'nvidia-smi.exe':
                output = driver[0]
            else:
                self.assertEqual(argv[:3], ['docker', '--host', host.DESKTOP_PIPE])
                output = json.dumps(info) if argv[3] == 'info' else '99.0.0'
            return SimpleNamespace(returncode=0, stdout=output)
        def check():
            return host.check('docker', payload, kit, run=run, host_platform='windows',
                docker_storage_path=backing, storage_target=self.root / 'new-data',
                disk_usage=lambda path: SimpleNamespace(free=10 * host.GIB), check_loopback_ports=False)
        self.assertEqual(check()['minimum_driver'], '580.0')
        for field, value in (('OSType', 'windows'), ('OperatingSystem', 'remote Linux'),
                             ('KernelVersion', '6.6.0-generic'), ('Runtimes', {})):
            previous = info[field]
            info[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'Docker Desktop'):
                check()
            info[field] = previous
        driver[0] = '579.99'
        with self.assertRaisesRegex(ValueError, '580.0'):
            check()
        self.assertFalse((self.root / 'new-data').exists())
        self.assertFalse(any('systemctl' in row or 'pull' in row for row in calls))

    def storage_fixture(self):
        payload = self.root / 'payload'
        (payload / 'images').mkdir(parents=True)
        (payload / 'models').mkdir()
        (payload / 'images/fixture.tar').write_bytes(b'12345')
        (payload / 'models/fixture.bin').write_bytes(b'1234567')
        host_root = self.root / 'host'
        (host_root / 'opt').mkdir(parents=True)
        docker = host_root / 'var/lib/custom-docker'
        docker.mkdir(parents=True)
        appdata = host_root / 'mnt/appdata'
        appdata.mkdir(parents=True)
        return payload, host_root, docker, appdata

    def test_actual_appdata_backing_low_space_refused_even_when_opt_is_free(self):
        payload, root, docker, appdata = self.storage_fixture()
        extra = 73
        needed = 12 + 2 * 5 + host.GIB + extra
        calls = []
        def disk(path):
            calls.append(path)
            return SimpleNamespace(free=needed - 1 if path == appdata else needed + 1000)
        with self.assertRaisesRegex(ValueError, str(needed)):
            host.check_storage(payload, root, docker, disk,
                               target=appdata / 'new/deep32', extra_bytes=extra)
        self.assertEqual(calls, [appdata])
        self.assertFalse((appdata / 'new').exists())

    def test_storage_boundary_includes_payload_archives_and_signed_data_budget(self):
        payload, root, docker, appdata = self.storage_fixture()
        extra = 83
        needed = 12 + 2 * 5 + host.GIB + extra
        for remaining in (needed - 1, needed, needed + 1):
            calls = []
            def disk(path):
                calls.append(path)
                return SimpleNamespace(free=remaining)
            with self.subTest(remaining=remaining):
                if remaining < needed:
                    with self.assertRaises(ValueError):
                        host.check_storage(payload, root, docker, disk, target=appdata, extra_bytes=extra)
                else:
                    self.assertEqual(host.check_storage(payload, root, docker, disk,
                        target=appdata, extra_bytes=extra), needed)
                    self.assertEqual(calls, [appdata, docker])

    def test_docker_backing_low_space_refused_even_when_appdata_is_free(self):
        payload, root, docker, appdata = self.storage_fixture()
        needed = 12 + 2 * 5 + host.GIB + 10
        def disk(path):
            return SimpleNamespace(free=needed - 1 if path == docker else needed + 1)
        with self.assertRaises(ValueError):
            host.check_storage(payload, root, docker, disk, target=appdata, extra_bytes=10)

    def test_legacy_opt_target_and_zero_extra_preserved(self):
        payload, root, docker, appdata = self.storage_fixture()
        calls = []
        def disk(path):
            calls.append(path)
            return SimpleNamespace(free=2 * host.GIB)
        self.assertEqual(host.check_storage(payload, root, docker, disk), 12 + 2 * 5 + host.GIB)
        self.assertEqual(calls, [root / 'opt', docker])
        self.assertNotIn(appdata, calls)

    def test_invalid_extra_budget_rejected_without_disk_probe(self):
        for extra in (-1, True, 0.5, '100', None):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                host.check_storage(self.root, self.root, self.root,
                    lambda _: self.fail('disk probe after invalid budget'), extra_bytes=extra)

    def test_host_check_passes_explicit_appdata_and_extra_to_storage(self):
        payload, root, docker, appdata = self.storage_fixture()
        kit = self.root / 'kit'
        kit.mkdir()
        pins = {'runtime': {'mineru_minimum_driver': '500.0', 'tei_minimum_driver': '500.0',
            'cuda_minimum_driver': '500.0', 'docker_minimum_version': '20.10.0',
            'compose_minimum_version': '2.30.0'}}
        (kit / 'pins.json').write_text(json.dumps(pins),encoding='ascii',newline='\n')
        commands = []
        def run(argv, **kwargs):
            commands.append(argv)
            if argv[0] == 'tailscale':
                output = json.dumps({'BackendState': 'Running',
                    'Self': {'HostName': 'synthetic-full-host', 'Online': True}})
            elif argv[0] == 'nvidia-smi':
                output = '999.0'
            elif argv[:2] == ['docker', 'info']:
                output = json.dumps({'Runtimes': {'nvidia': {}}, 'DockerRootDir': '/var/lib/custom-docker', 'OSType': 'linux'})
            else:
                output = '99.0.0'
            return SimpleNamespace(returncode=0, stdout=output)
        needed = 12 + 2 * 5 + host.GIB + 200
        calls = []
        def disk(path):
            calls.append(path)
            return SimpleNamespace(free=needed)
        result = host.check('docker', payload, kit, root=root, run=run,
            disk_usage=disk, check_loopback_ports=False,
            storage_target=appdata / 'new/deep32', extra_storage_bytes=200)
        self.assertEqual(result['free_bytes_required'], needed)
        self.assertEqual(calls, [appdata, docker])
        self.assertEqual(len(commands), 5)
        self.assertEqual(commands[0], ['tailscale', 'status', '--json'])
        self.assertFalse((appdata / 'new').exists())


class OfflineBuild(Fixture):
    def staged(self):
        images = module('test_full_image_fixture', KIT / 'tests/test_image_binding.py')
        fixture = images.Fixture(classic=True)
        stage = self.root / 'stage'
        (stage / 'payload/images').mkdir(parents=True)
        (stage / 'payload/locks').mkdir()
        wheels = stage / 'payload/wheelhouse/gateway'
        wheels.mkdir(parents=True)
        archive = fixture.write(stage / 'payload/images/builder.tar')
        binding = build.archive_binding(archive, inspected_id=fixture.config_id)
        builder = dict(binding, role='builder', path='images/builder.tar', sha256=build.digest(archive))
        lines = []
        for line in (FULL / 'requirements-gateway.in').read_text().splitlines():
            if not line or line.startswith('#'):
                continue
            name, version = line.split('==')
            wheel = wheels / (name.replace('-', '_') + '-' + version + '-py3-none-any.whl')
            wheel.write_bytes(('SYNTHETIC WHEEL STAND-IN ' + line).encode('ascii'))
            lines.append(line + ' --hash=sha256:' + build.digest(wheel))
        lock = stage / 'payload/locks/gateway.lock'
        lock.write_text('\n'.join(lines) + '\n',encoding='ascii',newline='\n')
        (stage / 'payload/locks/builder.lock').write_text(next(x for x in lines if x.startswith('uvicorn==')) + '\n',encoding='ascii',newline='\n')
        pins = {'runtime': {'builder_image_id': fixture.config_id},
                'full': {'gateway_lock_sha256': build.digest(lock), 'model_bridge': BRIDGE}}
        (stage / 'pins.json').write_text(json.dumps(pins),encoding='ascii',newline='\n')
        (stage / 'payload/images/index.json').write_text(json.dumps([builder]),encoding='ascii',newline='\n')
        return stage, fixture

    def test_sealed_linux_wheels_and_actual_source_context(self):
        stage, _ = self.staged()
        out = self.root / 'context'
        build.context(stage, KIT, out)
        self.assertEqual((out / 'engine/src/rag_core/api.py').read_bytes(), (ROOT / 'src/engine/src/rag_core/api.py').read_bytes())
        self.assertEqual((out / 'gateway/rag_edge.py').read_bytes(), (ROOT / 'src/gateway/rag_edge.py').read_bytes())
        self.assertEqual((out / 'import/d32import.py').read_bytes(), (KIT / 'import/d32import.py').read_bytes())
        self.assertEqual((out / 'model-bridge/node_layers.py').read_bytes(), (ROOT / 'src/workstation/node/shims/py/node_layers.py').read_bytes())
        # the image's probe file (GPU_EXT_PROBE_ALWAYS) is the Workstation's, byte for byte
        self.assertEqual((out / 'model-bridge/gpu-probes.json').read_bytes(),
                         (ROOT / 'src/workstation/ship/u4/etc/d32/gpu-probes/rerank.json').read_bytes())
        self.assertTrue((out / 'gateway.lock').is_file())

    def test_wrong_platform_wheel_and_shared_pin_mismatch_fail(self):
        stage, _ = self.staged()
        wheels = stage / 'payload/wheelhouse/gateway'
        wheel = next(wheels.iterdir())
        changed = wheel.with_name(wheel.name.replace('py3-none-any', 'cp314-cp314-openbsd_7_8_amd64'))
        wheel.rename(changed)
        with self.assertRaisesRegex(ValueError, 'Workstation'):
            build.checked_inputs(stage, KIT)
        changed.rename(wheel)
        (stage / 'payload/locks/builder.lock').write_text('uvicorn==0.1 --hash=sha256:' + 'a' * 64 + '\n',encoding='ascii',newline='\n')
        with self.assertRaisesRegex(ValueError, 'pins disagree'):
            build.checked_inputs(stage, KIT)

    def test_private_or_protected_source_names_refused_before_copy(self):
        source = self.root / 'synthetic-source'
        source.mkdir()
        private = source / 'synthetic-test.env'
        private.write_text('SYNTHETIC_TEST_FIXTURE=1\n',encoding='ascii',newline='\n')
        with self.assertRaisesRegex(ValueError, 'private credentials'):
            build._copy_tree(source, self.root / 'out')
        self.assertFalse((self.root / 'out').exists())
        private.unlink()
        # Only a directory name is inspected; no protected document is read.
        (source / 'eval').mkdir()
        with self.assertRaisesRegex(ValueError, 'protected evaluation'):
            build._copy_tree(source, self.root / 'out')
        self.assertFalse((self.root / 'out').exists())

    def test_record_and_merge_binds_both_actual_archives(self):
        stage, fixture = self.staged()
        output = self.root / 'build'
        (output / 'images').mkdir(parents=True)
        (output / 'context').mkdir()
        (output / 'context/public.py').write_text('"""Synthetic public code."""\n',encoding='ascii',newline='\n')
        for role in build.ROLES:
            fixture.write(output / 'images' / (role + '.tar'))
        inspected = json.dumps([{'Id': fixture.config_id, 'Os': 'linux', 'Architecture': 'amd64'}])
        with patch.object(build.subprocess, 'check_output', return_value=inspected):
            build.record(stage, KIT, output)
        build.merge(stage, KIT, output)
        rows = json.loads((stage / 'payload/images/index.json').read_text())
        self.assertEqual({r['role'] for r in rows}, {'builder', 'full', 'model_bridge'})
        pins = json.loads((stage / 'pins.json').read_text())
        self.assertEqual(pins['runtime']['full_image_id'], fixture.config_id)
        self.assertEqual(pins['runtime']['model_bridge_image_id'], fixture.config_id)
        with self.assertRaisesRegex(ValueError, 'fresh stage'):
            build.merge(stage, KIT, output)

    def test_corrupted_full_archive_is_rejected_before_merge(self):
        stage, fixture = self.staged()
        output = self.root / 'build'
        (output / 'images').mkdir(parents=True)
        (output / 'context').mkdir()
        for role in build.ROLES:
            fixture.write(output / 'images' / (role + '.tar'))
        inspected = json.dumps([{'Id': fixture.config_id, 'Os': 'linux', 'Architecture': 'amd64'}])
        with patch.object(build.subprocess, 'check_output', return_value=inspected):
            build.record(stage, KIT, output)
        archive = output / 'images/model_bridge.tar'
        archive.write_bytes(archive.read_bytes() + b'SYNTHETIC CORRUPTION')
        with self.assertRaisesRegex(ValueError, 'unchanged'):
            build.merge(stage, KIT, output)
        self.assertFalse((stage / 'payload/images/full.tar').exists())

    def test_build_recipe_is_local_only_and_imports_actual_code(self):
        text = (KIT / 'build/build-full.sh').read_text()
        self.assertIn('--network none --pull=false', text)
        self.assertNotIn('docker pull', text)
        recipe = (FULL / 'Dockerfile').read_text()
        self.assertIn('ARG BASE_IMAGE\nFROM ${BASE_IMAGE}', recipe)
        self.assertIn('--no-index', recipe)
        self.assertIn('--require-hashes', recipe)
        self.assertIn('COPY engine /opt/deep32/engine', recipe)
        self.assertIn('COPY gateway /opt/deep32/gateway', recipe)
        self.assertIn('COPY import /opt/deep32/import', recipe)
        self.assertEqual((FULL / 'requirements-gateway.in').read_bytes(),
            (ROOT / 'src/kb-tools/linux-line/compose/requirements-gateway.in').read_bytes())

    def test_missing_staged_builder_fails_before_context_write(self):
        (self.root / 'payload/images').mkdir(parents=True)
        (self.root / 'pins.json').write_text('{}')
        (self.root / 'payload/images/index.json').write_text('[]')
        out = self.root / 'context'
        with self.assertRaisesRegex(ValueError, 'builder'):
            build.context(self.root, KIT, out)
        self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
