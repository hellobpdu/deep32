"""Offline getter integration: invented bytes, fake GPG and fake HTTP only."""
import argparse
import contextlib
import copy
from email.message import Message
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[2] / 'src/release-tools'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


getter = load('whole_get', TOOLS / 'deep32_get_whole.py')
trust_tests = load('trust_fakes', Path(__file__).with_name('test_whole_trust.py'))
cap_tests = load('cap_fakes', Path(__file__).with_name('test_capabilities.py'))
SCRATCH = None


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class Response:
    def __init__(self, raw, status=200, headers=None):
        self.raw = io.BytesIO(raw)
        self.status = status
        self.headers = Message()
        for key, value in headers or [('Content-Length', str(len(raw)))]:
            self.headers[key] = value
        self.closed = False

    def read(self, count):
        return self.raw.read(count)

    def close(self):
        self.closed = True


class PrivateFixtureFS(getter.transfer.FileSystem):
    def private_directory(self, value):
        return self.path(value, directory=True)


class Getter(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='get-', dir=SCRATCH)
        self.root = Path(self.temp.name)
        self.source = self.root / 'read only media'
        self.source.mkdir()
        self.cache = self.root / 'cache'
        self.cache.mkdir()
        self.dest = self.root / 'destination with spaces'
        self.dest.mkdir()
        for item in (self.cache, self.dest):
            os.chmod(item, 0o700)
        self.data = json.loads((TOOLS / 'contracts/fixtures/valid/mirrors-v2.json').read_bytes())
        self.capability = json.loads((TOOLS / 'contracts/fixtures/capabilities/manifest.json').read_bytes())
        for disc in self.data['discs'] + self.data.get('bundles', []):
            raw = ('SYNTHETIC WHOLE ' + disc.get('role', disc.get('bundle_id')) + '\n').encode('ascii')
            disc.update(size=len(raw), sha256=sha(raw), chunk_sha256=[sha(raw)])
            (self.source / disc['file']).write_bytes(raw)
        self.seal()
        self.key = self.root / 'synthetic-public.gpg'
        self.key.write_bytes(b'synthetic public fixture only\n')
        self.args = getter.parser().parse_args(['disc2', '--from', str(self.source),
            '--key', str(self.key), '--fingerprint', trust_tests.FINGERPRINT])
        self.fake = trust_tests.FakeGpg()
        self.patches = [mock.patch.object(getter.manifest, 'gpg_executable', return_value=self.key),
                        mock.patch.object(getter.manifest.subprocess, 'Popen', side_effect=self.fake.popen)]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.temp.cleanup()

    def seal(self):
        raw = (json.dumps(self.data, sort_keys=True, indent=2) + '\n').encode('ascii')
        (self.source / 'MIRRORS.json').write_bytes(raw)
        if self.data['format_version'] == 2:
            cap_raw = getter.module('contract_validate').canonical(self.capability)
            self.data['capability_manifest']['sha256'] = sha(cap_raw)
            (self.source / self.data['capability_manifest']['file']).write_bytes(cap_raw)
            by_id = {b['id']: b for b in self.capability['bundles']}
            for bundle in self.data['bundles']:
                bundle['bundle_sha256'] = sha(getter.module('contract_validate').canonical(by_id[bundle['bundle_id']]))
            raw = getter.module('contract_validate').canonical(self.data)
            (self.source / 'MIRRORS.json').write_bytes(raw)
        sums = {d['file']: d['sha256'] for d in self.data['discs'] + self.data.get('bundles', [])}
        if self.data['format_version'] == 2:
            sums[self.data['capability_manifest']['file']] = self.data['capability_manifest']['sha256']
        sums['MIRRORS.json'] = sha(raw)
        (self.source / 'SHA256SUMS').write_bytes(''.join('%s  %s\n' % (v, k) for k, v in sorted(sums.items())).encode('ascii'))
        (self.source / 'SHA256SUMS.sig').write_bytes(b'synthetic detached signature\n')

    def run_get(self, **kwargs):
        with contextlib.redirect_stderr(io.StringIO()):
            records = getter.get(self.args, **kwargs)
            return records if self.args.disc == 'disc3' else records[0]

    def online(self):
        self.args.source = None
        self.args.cache = str(self.cache)
        self.args.destination = str(self.dest)

    def metadata_open(self, url, headers, **kwargs):
        self.assertEqual(headers['Accept-Encoding'], 'identity')
        self.assertNotIn('Range', headers)
        return Response((self.source / url.rsplit('/', 1)[-1]).read_bytes())

    def test_local_in_place_no_socket_or_write(self):
        before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns, sha(p.read_bytes())) for p in self.source.iterdir()}
        with mock.patch.object(getter.transfer, 'open_http', side_effect=AssertionError('socket')), mock.patch.object(getter.transfer, 'fetch_whole', side_effect=AssertionError('payload copy')):
            result = self.run_get()
        self.assertEqual(Path(result[0]).parent, self.source)
        self.assertEqual(before, {p.name: (p.stat().st_size, p.stat().st_mtime_ns, sha(p.read_bytes())) for p in self.source.iterdir()})
        self.assertFalse(list(self.source.glob('*.download')))

    def test_local_read_only_files(self):
        for p in self.source.iterdir():
            os.chmod(p, 0o444)
        try:
            self.assertEqual(self.run_get()[1], self.data['discs'][1]['file'])
        finally:
            for p in self.source.iterdir():
                os.chmod(p, 0o600)

    def test_local_scratch_privacy_checked_before_metadata_read(self):
        with mock.patch.object(getter.transfer.FileSystem, 'private_directory', side_effect=getter.transfer.LocalError('private scratch required')), mock.patch.object(getter, 'read_checked', side_effect=AssertionError('metadata read')):
            with self.assertRaisesRegex(getter.transfer.LocalError, 'private scratch'):
                self.run_get()

    def test_bad_signature_precedes_index_parse(self):
        self.fake.bad_root = True
        with mock.patch.object(getter.manifest, 'read_mirrors', side_effect=AssertionError('index parsed')):
            with self.assertRaises(getter.manifest.Refusal):
                self.run_get()

    def test_wrong_signer_no_payload(self):
        self.fake.wrong_signer = True
        with self.assertRaises(getter.manifest.Refusal):
            self.run_get()

    def test_text_signature_over_same_bytes_refused_locally_and_before_download(self):
        before = {p.name: p.read_bytes() for p in self.source.iterdir()}
        self.fake.text_checksums = {"SHA256SUMS"}
        for online in (False, True):
            with self.subTest(online=online), \
                 mock.patch.object(getter.manifest, 'read_mirrors', side_effect=AssertionError('index parsed')), \
                 mock.patch.object(getter.transfer, 'FileSystem', PrivateFixtureFS), \
                 mock.patch.object(getter.transfer, 'fetch_whole', side_effect=AssertionError('payload fetched')):
                if online:
                    self.online()
                with self.assertRaisesRegex(getter.manifest.Refusal, 'class 00'):
                    self.run_get(opener=self.metadata_open)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.source.iterdir()})

    def test_unbound_index(self):
        (self.source / 'MIRRORS.json').write_bytes(b'{bad json')
        with self.assertRaisesRegex(getter.manifest.Refusal, 'authenticated'):
            self.run_get()

    def test_missing_each_local_file(self):
        for name in (*getter.METADATA, self.data['discs'][1]['file']):
            with self.subTest(name=name):
                path = self.source / name
                raw = path.read_bytes()
                path.unlink()
                try:
                    with self.assertRaises((getter.manifest.Refusal, getter.transfer.LocalError, OSError)):
                        self.run_get()
                finally:
                    path.write_bytes(raw)

    def test_wrong_local_size_or_hash(self):
        image = self.source / self.data['discs'][1]['file']
        for raw in (b'x', b'x' * image.stat().st_size):
            image.write_bytes(raw)
            with self.assertRaises(getter.transfer.LocalError):
                self.run_get()

    def test_wrong_release(self):
        self.args.release = 'v0.1.0-alpha.2'
        with self.assertRaisesRegex(getter.manifest.Refusal, 'release'):
            self.run_get()

    def test_no_unsigned_fallback_when_mirrors_missing(self):
        (self.source / 'MIRRORS.json').unlink()
        (self.source / 'ASSEMBLY.json').write_bytes(b'{}')
        with self.assertRaises((getter.manifest.Refusal, getter.transfer.LocalError)):
            self.run_get()

    def test_local_source_rejects_network_paths(self):
        for source in ('https://example.org/media', '//server/share', '\\\\server\\share', 'file://server/media'):
            self.args.source = source
            with self.assertRaises(getter.manifest.Refusal):
                self.run_get()

    def test_coherent_cache_reuse_and_revision_high_water(self):
        self.online()
        with mock.patch.object(getter.transfer.FileSystem, 'private_directory', PrivateFixtureFS.private_directory):
            for _ in range(2):
                with getter.metadata(self.args, opener=self.metadata_open) as (folder, data, source):
                    self.assertEqual(data['revision'], self.data['revision'])
                    self.assertIsNone(source)
                    self.assertEqual({p.name for p in folder.iterdir()}, set(getter.METADATA) | {self.data['capability_manifest']['file']})
            self.assertEqual(len(list(self.cache.glob('revision-*'))), 1)
            self.data['revision'] += 1
            self.data['published_at'] = '2026-11-18T00:00:00Z'
            self.data['discs'][0]['mirrors'].append({'id': 'extra', 'url': 'https://example.org/extra.img'})
            self.seal()
            with getter.metadata(self.args, opener=self.metadata_open):
                pass
            self.assertEqual(len(list(self.cache.glob('revision-*'))), 2)
            self.data['revision'] -= 1
            self.seal()
            with self.assertRaises(getter.manifest.Refusal):
                with getter.metadata(self.args, opener=self.metadata_open):
                    pass
            self.assertEqual(len(list(self.cache.glob('revision-*'))), 2)

    def test_failed_refresh_preserves_good_set_without_fallback(self):
        self.online()
        with mock.patch.object(getter.transfer.FileSystem, 'private_directory', PrivateFixtureFS.private_directory):
            with getter.metadata(self.args, opener=self.metadata_open):
                pass
            old = {p.relative_to(self.cache).as_posix(): p.read_bytes() for p in self.cache.rglob('*') if p.is_file()}
            with self.assertRaises(getter.manifest.Refusal):
                with getter.metadata(self.args, opener=lambda *a, **k: (_ for _ in ()).throw(OSError())):
                    self.fail('stale fallback')
            self.assertEqual(old, {p.relative_to(self.cache).as_posix(): p.read_bytes() for p in self.cache.rglob('*') if p.is_file()})

    def test_same_revision_changed_authenticated_bytes_refused(self):
        self.online()
        with mock.patch.object(getter.transfer.FileSystem, 'private_directory', PrivateFixtureFS.private_directory):
            with getter.metadata(self.args, opener=self.metadata_open):
                pass
            self.data['published_at'] = '2026-11-18T00:00:00Z'
            self.seal()
            with self.assertRaisesRegex(getter.manifest.Refusal, 'same mirror revision'):
                with getter.metadata(self.args, opener=self.metadata_open):
                    pass

    def test_revision_immutable_and_mirror_prefix(self):
        with tempfile.TemporaryDirectory(dir=self.root) as other:
            other = Path(other)
            for name in getter.METADATA:
                (other / name).write_bytes((self.source / name).read_bytes())
            for change in ('hash', 'remove', 'source', 'citation'):
                data = copy.deepcopy(self.data)
                data['revision'] += 1
                if change == 'hash':
                    data['discs'][0]['sha256'] = 'f' * 64
                elif change == 'remove':
                    data['discs'][0]['mirrors'] = []
                else:
                    data[change if change != 'source' else 'source_commit'] = 'changed'
                with self.assertRaises(getter.manifest.Refusal):
                    getter.compare_revisions(self.data, data, self.source, other)

    def test_online_uses_shared_transfer(self):
        self.online()
        report = {'path': str(self.dest / self.data['discs'][1]['file']), 'message': 'verified'}
        with mock.patch.object(getter.transfer.FileSystem, 'private_directory', PrivateFixtureFS.private_directory), mock.patch.object(getter.transfer, 'fetch_whole', return_value=report) as fetch:
            result = self.run_get(opener=self.metadata_open)
        self.assertEqual(result[0], report['path'])
        self.assertEqual(fetch.call_args.kwargs['revision'], self.data['revision'])
        self.assertEqual(fetch.call_args.kwargs['fingerprint'], trust_tests.FINGERPRINT)

    def test_online_actual_transfer_and_idempotent_final(self):
        self.online()
        payload_requests = []
        def opener(url, headers, **kwargs):
            if url.startswith('https://github.com/'):
                return self.metadata_open(url, headers, **kwargs)
            payload_requests.append(url)
            return Response((self.source / self.data['discs'][1]['file']).read_bytes())
        with mock.patch.object(getter.transfer.FileSystem, 'private_directory', PrivateFixtureFS.private_directory):
            first = self.run_get(opener=opener, filesystem=PrivateFixtureFS())
            second = self.run_get(opener=opener, filesystem=PrivateFixtureFS())
        self.assertEqual(first, second)
        self.assertEqual(len(payload_requests), 1)
        self.assertEqual(sha(Path(first[0]).read_bytes()), first[3])
        self.assertFalse(any(p.name.endswith(('.response', '.joining', '.download')) for p in self.dest.iterdir()))
        self.assertEqual(len([p for p in self.dest.iterdir() if not p.name.startswith('.')]), 1)

    def test_actual_corrupt_first_honest_final_mirror(self):
        self.online()
        disc = self.data['discs'][1]
        disc['mirrors'] = [disc['mirrors'][0], {'id': 'honest', 'url': 'https://example.org/' + disc['file']}]
        self.seal()
        payload_requests = []
        def opener(url, headers, **kwargs):
            if url.startswith('https://github.com/'):
                return self.metadata_open(url, headers, **kwargs)
            payload_requests.append((url, headers))
            raw = (self.source / disc['file']).read_bytes()
            return Response(b'x' * len(raw) if len(payload_requests) == 1 else raw)
        with mock.patch.object(getter.transfer.FileSystem, 'private_directory', PrivateFixtureFS.private_directory):
            result = self.run_get(opener=opener, filesystem=PrivateFixtureFS())
        self.assertEqual(len(payload_requests), 2)
        self.assertEqual(sha(Path(result[0]).read_bytes()), disc['sha256'])
        self.assertNotIn('Range', payload_requests[1][1])

    def test_online_disc3_has_no_universal_fallback(self):
        self.online()
        self.args.disc = 'disc3'
        with mock.patch.object(getter.transfer.FileSystem, 'private_directory', PrivateFixtureFS.private_directory), mock.patch.object(getter.transfer, 'fetch_whole', side_effect=AssertionError('universal fallback')):
            with self.assertRaises(getter.manifest.Refusal):
                self.run_get(opener=self.metadata_open)

    def test_local_alpha2_disc3_requires_exact_class(self):
        self.args.disc = 'disc3'
        with mock.patch.object(getter.transfer, 'verify_local_image', side_effect=AssertionError('unbound core')):
            with self.assertRaises(getter.manifest.Refusal):
                self.run_get()

    def test_percent_encoded_file_uri_controls_fail_before_source_io(self):
        for encoded in ('%09', '%0a', '%0d', '%1b', '%7f'):
            with self.subTest(encoded=encoded), mock.patch.object(getter.manifest, 'local_source', side_effect=AssertionError('source io')):
                with self.assertRaisesRegex(getter.manifest.Refusal, 'Control'):
                    getter.local_source('file:///synthetic/' + encoded + 'media')

    def test_whole_descriptor_above_legacy_part_cap(self):
        self.online()
        disc = self.data['discs'][1]
        disc['size'] = getter.manifest.MAX_PART + 17
        disc['chunk_sha256'] = ['f' * 64] * ((disc['size'] + 67108863) // 67108864)
        self.seal()
        report = {'path': str(self.dest / disc['file']), 'message': 'synthetic transport seam'}
        with mock.patch.object(getter.transfer.FileSystem, 'private_directory', PrivateFixtureFS.private_directory), mock.patch.object(getter.transfer, 'fetch_whole', return_value=report) as fetch:
            result = self.run_get(opener=self.metadata_open)
        self.assertEqual(result[2], getter.manifest.MAX_PART + 17)
        self.assertEqual(fetch.call_args.args[0]['size'], result[2])

    def test_capability_request_refuses_without_transport_map(self):
        for name, value in (('gpu_class', 'ada'), ('ingestion', True), ('full', True)):
            setattr(self.args, name, value)
            with self.assertRaisesRegex(getter.manifest.Refusal, 'requires disc3'):
                self.run_get()
            setattr(self.args, name, None if name == 'gpu_class' else False)

    def test_capability_exact_class_and_optional_closure(self):
        capability, *_ = cap_tests.fixture()
        for card in capability['hardware_classes']:
            self.assertEqual([r['id'] for r in getter.select_bundles(capability, card)], ['core', 'embedder-' + card])
        self.assertEqual([r['id'] for r in getter.select_bundles(capability, 'ada', ingestion=True)], ['core', 'embedder-ada', 'ingestion'])
        self.assertEqual([r['id'] for r in getter.select_bundles(capability, 'ada', full=True)], ['core', 'embedder-ada', 'ingestion', 'full'])
        for card in ('', 'cpu', 'tei-cpu', 'universal', 'unknown'):
            with self.assertRaises(getter.manifest.Refusal):
                getter.select_bundles(capability, card)

    def test_core_and_exact_class_actual_transfer_one_payload_each(self):
        self.online()
        self.args.disc, self.args.gpu_class = 'disc3', 'turing'
        payload_requests = []
        def opener(url, headers, **kwargs):
            if url.startswith('https://github.com/'):
                return self.metadata_open(url, headers, **kwargs)
            name = url.rsplit('/', 1)[-1]
            payload_requests.append(name)
            return Response((self.source / name).read_bytes())
        with mock.patch.object(getter.transfer.FileSystem, 'private_directory', PrivateFixtureFS.private_directory):
            first = self.run_get(opener=opener, filesystem=PrivateFixtureFS())
            second = self.run_get(opener=opener, filesystem=PrivateFixtureFS())
        expected = [r for key in ('core', 'embedder-turing') for r in self.data['bundles'] if r['bundle_id'] == key]
        self.assertEqual([r[1] for r in first], [r['file'] for r in expected])
        self.assertEqual(first, second)
        self.assertEqual(payload_requests, [r['file'] for r in expected])
        self.assertEqual(len([p for p in self.dest.iterdir() if not p.name.startswith('.')]), 2)
        self.assertEqual(sum(p.stat().st_size for p in self.dest.iterdir() if not p.name.startswith('.')), sum(r['size'] for r in expected))
        self.assertFalse(any(p.name.endswith(('.download', '.response', '.joining')) for p in self.dest.iterdir()))

    def test_all_classes_local_read_only_exact_pair_without_copy(self):
        self.args.disc = 'disc3'
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.source.iterdir()}
        for p in self.source.iterdir():
            os.chmod(p, 0o444)
        try:
            with mock.patch.object(getter.transfer, 'open_http', side_effect=AssertionError('socket')), mock.patch.object(getter.transfer, 'fetch_whole', side_effect=AssertionError('copy')):
                for card in self.capability['hardware_classes']:
                    self.args.gpu_class = card
                    records = self.run_get()
                    self.assertEqual(len(records), 2)
                    self.assertEqual([r[1] for r in records], [r['file'] for key in ('core', 'embedder-' + card) for r in self.data['bundles'] if r['bundle_id'] == key])
                    self.assertTrue(all(Path(r[0]).parent == self.source for r in records))
        finally:
            for p in self.source.iterdir():
                os.chmod(p, 0o600)
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.source.iterdir()})
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_missing_or_corrupt_exact_class_refuses_no_other_class(self):
        self.args.disc, self.args.gpu_class = 'disc3', 'ada'
        target = next(r for r in self.data['bundles'] if r['bundle_id'] == 'embedder-ada')
        path = self.source / target['file']
        raw = path.read_bytes()
        path.unlink()
        with self.assertRaises((getter.transfer.LocalError, OSError)):
            self.run_get()
        path.write_bytes(b'!' * len(raw))
        with self.assertRaises(getter.transfer.LocalError):
            self.run_get()

    def test_capability_tampering_before_payload(self):
        self.args.disc, self.args.gpu_class = 'disc3', 'ada'
        capability = self.source / self.data['capability_manifest']['file']
        capability.write_bytes(capability.read_bytes() + b' ')
        with mock.patch.object(getter.transfer, 'verify_local_image', side_effect=AssertionError('unbound payload')):
            with self.assertRaises((getter.manifest.Refusal, ValueError)):
                self.run_get()

    def test_only_bound_capability_can_exceed_five_mib(self):
        raw = getter.module('contract_validate').canonical(self.capability)
        binding = self.data['capability_manifest']
        for length in (getter.manifest.MAX_META + 1, getter.MAX_CAPABILITY,
                       getter.MAX_CAPABILITY + 1):
            with self.subTest(length=length):
                padded = raw + b' ' * (length - len(raw))
                binding['sha256'] = sha(padded)
                (self.source / binding['file']).write_bytes(padded)
                index_raw = getter.module('contract_validate').canonical(self.data)
                (self.source / 'MIRRORS.json').write_bytes(index_raw)
                sums = getter.manifest.read_sums(self.source / 'SHA256SUMS')
                sums.update({binding['file']: binding['sha256'], 'MIRRORS.json': sha(index_raw)})
                (self.source / 'SHA256SUMS').write_bytes(''.join('%s  %s\n' % (v, k) for k, v in sorted(sums.items())).encode('ascii'))
                if length <= getter.MAX_CAPABILITY:
                    self.assertEqual(self.run_get()[1], self.data['discs'][1]['file'])
                else:
                    with self.assertRaises(getter.manifest.Refusal):
                        self.run_get()
        with self.assertRaises(getter.manifest.Refusal):
            getter.read_checked(self.source / binding['file'])

    def test_bundle_mapping_tamper_refused_before_payload(self):
        self.args.disc, self.args.gpu_class = 'disc3', 'ada'
        self.data['bundles'][0]['bundle_sha256'] = '0' * 64
        raw = getter.module('contract_validate').canonical(self.data)
        (self.source / 'MIRRORS.json').write_bytes(raw)
        sums = getter.manifest.read_sums(self.source / 'SHA256SUMS')
        sums['MIRRORS.json'] = sha(raw)
        (self.source / 'SHA256SUMS').write_bytes(''.join('%s  %s\n' % (v, k) for k, v in sorted(sums.items())).encode('ascii'))
        with mock.patch.object(getter.transfer, 'verify_local_image', side_effect=AssertionError('unbound payload')):
            with self.assertRaises((getter.manifest.Refusal, ValueError)):
                self.run_get()

    def test_cli_rejects_ambiguous_source_and_to(self):
        with self.assertRaises(getter.manifest.Refusal):
            getter.main(['disc2', '--from', str(self.source), '--to', str(self.dest), '--fingerprint', 'A' * 40])

    def test_cli_rejects_unsafe_repo_and_release(self):
        for flag, value in (('--repo', '../repo'), ('--repo', 'https://example.org/repo'), ('--release', 'v1..2')):
            with self.assertRaises(getter.manifest.Refusal):
                getter.main(['disc2', '--from', str(self.source), '--fingerprint', 'A' * 40, flag, value])

    def test_cli_rejects_control_path_before_io(self):
        with self.assertRaisesRegex(getter.manifest.Refusal, 'Control'):
            getter.main(['disc2', '--from', 'path\tfield', '--fingerprint', 'A' * 40])


class ForgeBudget(unittest.TestCase):
    def setUp(self):
        self.ids = ['core', 'embedder-ada', 'ingestion']
        self.discs = [{'bundle_id': key, 'file': key + '.iso', 'size': index + 10,
                       'sha256': str(index + 1) * 64}
                      for index, key in enumerate(self.ids)]
        self.bundles = []
        for index, key in enumerate(self.ids):
            linux = [{'domain': 'download', 'peak_bytes': 200},
                     {'domain': 'data', 'peak_bytes': 10 + index},
                     {'domain': 'docker', 'peak_bytes': 30 + index}]
            self.bundles.append({'id': key, 'storage_budgets': [
                {'backend': 'docker-desktop', 'domains': []},
                {'backend': 'docker-engine', 'domains': linux},
            ]})

    def test_exact_signed_component_selection_and_linux_sums(self):
        expected_media = [{'component': row['bundle_id'], 'size': row['size'], 'sha256': row['sha256']}
                          for row in self.discs]
        expected = {'format': 'deep32-forge-budget/1', 'media_components': expected_media,
                    'data_bytes': 33, 'docker_bytes': 93}
        self.assertEqual(getter.forge_budget_document(self.bundles, self.discs, 'ada'), expected)
        with self.assertRaises(getter.manifest.Refusal):
            getter.forge_budget_document(self.bundles, self.discs, 'hopper')
        self.bundles[-1]['storage_budgets'][1]['domains'][-1]['peak_bytes'] = True
        with self.assertRaises(getter.manifest.Refusal):
            getter.forge_budget_document(self.bundles, self.discs, 'ada')

    def test_budget_without_ingestion_sums_core_and_embedder_only(self):
        document = getter.forge_budget_document(self.bundles[:2], self.discs[:2], 'ada')
        self.assertEqual([row['component'] for row in document['media_components']], [row['bundle_id'] for row in self.discs[:2]])
        self.assertEqual((document['data_bytes'], document['docker_bytes']), (10 + 11, 30 + 31))
        for selected, discs in ((self.bundles[:1], self.discs[:1]), (self.bundles[:2], self.discs)):
            with self.subTest(bundles=len(selected), discs=len(discs)), self.assertRaises(getter.manifest.Refusal):
                getter.forge_budget_document(selected, discs, 'ada')

    def test_classic_image_store_returns_only_a_qualified_linux_engine_root(self):
        root = '/synthetic/dedicated-docker'
        classic = dict(OSType='linux', Driver='overlay2', DockerRootDir=root,
                       DriverStatus=[['Backing Filesystem', 'extfs'], ['Supports d_type', 'true']])
        self.assertEqual(getter.classic_docker_root(classic), root)
        containerd = dict(classic, Driver='overlayfs',
                          DriverStatus=[['driver-type', 'io.containerd.snapshotter.v1']])
        with self.assertRaises(getter.DockerImageStoreRefusal) as error:
            getter.classic_docker_root(containerd)
        self.assertEqual(error.exception.code, 'docker-store-containerd')
        self.assertIn('Docker Desktop', error.exception.next_step)
        cases = [dict(classic, Driver='overlayfs'), dict(classic, Driver='vfs'),
                 dict(classic, DriverStatus=None), dict(classic, DriverStatus=['driver-type']),
                 dict(classic, DriverStatus=[['driver-type', 'unknown']]),
                 dict(classic, DriverStatus=containerd['DriverStatus'] * 2),
                 dict(classic, DockerRootDir='var/lib/docker'), dict(classic, DockerRootDir='//remote/store'),
                 dict(classic, DockerRootDir='/some/../other'), dict(classic, DockerRootDir='/unsafe\nroot'),
                 dict(classic, OSType='windows'), None]
        for info in cases:
            with self.subTest(info=info), self.assertRaises(getter.DockerImageStoreRefusal) as error:
                getter.classic_docker_root(info)
            self.assertEqual(error.exception.code, 'docker-store-unknown')

    def test_signed_plan_uses_observed_local_volumes(self):
        args = argparse.Namespace(release='v0.1.0-alpha.3', disc='disc3', gpu_class='ada',
                                  source='synthetic-release-source', ingestion=True, full=False,
                                  forge_data_root='synthetic-forge-data')
        source = Path(args.source)
        index = {'format_version': 2, 'bundles': self.discs}
        info = b'{"OSType":"linux","Driver":"overlay2","DriverStatus":[],"DockerRootDir":"/synthetic/local-store"}'
        observe = mock.Mock(side_effect=[('same-device', 410), ('same-device', 400)])
        with mock.patch.object(getter, 'metadata', return_value=contextlib.nullcontext((source, index, source))) as auth, \
                mock.patch.object(getter.manifest, 'read_mirror_capabilities'), \
                mock.patch.object(getter, 'select_bundles', return_value=self.bundles):
            rows = getter.linux_forge_storage(args, info, observe=observe)
        auth.assert_called_once_with(args)
        self.assertEqual(rows, [
            {'filesystem_id': 'same-device', 'needed_bytes': 33, 'free_bytes': 410},
            {'filesystem_id': 'same-device', 'needed_bytes': 93, 'free_bytes': 400},
        ])
        self.assertEqual(observe.call_args_list, [
            mock.call(args.forge_data_root, existing=False),
            mock.call('/synthetic/local-store', existing=True),
        ])
        for bad in (b'{"OSType":"windows"}', b'x' * (1024 * 1024 + 1)):
            with self.subTest(info=bad[:24]), self.assertRaises(getter.manifest.Refusal):
                getter.linux_forge_storage(args, bad, observe=observe)

    def test_containerd_store_without_containerd_info_is_refused_first(self):
        args = argparse.Namespace(release='v0.1.0-alpha.3', disc='disc3', gpu_class='ada',
                                  source='unused-release', ingestion=True, full=False,
                                  forge_data_root='/unused-data')
        info = json.dumps(dict(OSType='linux', Driver='overlayfs', DockerRootDir='/var/lib/docker',
                               DriverStatus=[['driver-type', 'io.containerd.snapshotter.v1']])).encode('ascii')
        observe = mock.Mock()
        with mock.patch.object(getter, 'metadata') as read_release, \
                self.assertRaises(getter.DockerImageStoreRefusal) as error:
            getter.linux_forge_storage(args, info, observe=observe)
        self.assertEqual(error.exception.code, 'docker-store-unknown')
        read_release.assert_not_called()
        observe.assert_not_called()

    def test_forge_docker_root_follows_the_store(self):
        classic = dict(OSType='linux', Driver='overlay2', DockerRootDir='/synthetic/docker', DriverStatus=[])
        store = dict(classic, Driver='overlayfs', DriverStatus=[['driver-type', 'io.containerd.snapshotter.v1']])
        self.assertEqual(getter.forge_docker_root(classic), '/synthetic/docker')
        self.assertEqual(getter.forge_docker_root(dict(store, Containerd={'Address': '/run/containerd/containerd.sock'})),
                         '/var/lib/containerd')
        managed = dict(store, Containerd={'Address': '/var/run/docker/containerd/containerd.sock'})
        self.assertEqual(getter.forge_docker_root(managed), '/synthetic/docker')
        for info in (store, dict(store, Containerd={'Address': '/elsewhere/containerd.sock'}),
                     dict(store, Containerd={'Address': None}), dict(store, Containerd='x'),
                     dict(store, Driver='native', Containerd={'Address': '/run/containerd/containerd.sock'}),
                     dict(store, DockerRootDir='relative', Containerd={'Address': '/run/containerd/containerd.sock'})):
            with self.subTest(info=info), self.assertRaises(getter.DockerImageStoreRefusal) as error:
                getter.forge_docker_root(info)
            self.assertEqual(error.exception.code, 'docker-store-unknown')
        with self.assertRaises(getter.DockerImageStoreRefusal):
            getter.forge_docker_root(dict(classic, OSType='windows'))

    def test_forge_side_containerd_rule_matches_the_getter(self):
        text = (TOOLS.parents[1] / 'src/gpu-pack/forms/host-requirements.py').read_text(encoding='ascii')
        for literal in (getter.CONTAINERD_SYSTEM_SOCKET, getter.CONTAINERD_SYSTEM_ROOT, getter.CONTAINERD_MANAGED_PREFIX):
            self.assertIn("'" + literal + "'", text)

    def test_joined_cache_bytes_counts_only_a_complete_plain_copy(self):
        components = [{'component': 'core', 'size': 1, 'sha256': 'a' * 64},
                      {'component': 'embedder-ada', 'size': 2, 'sha256': 'b' * 64}]
        with tempfile.TemporaryDirectory(prefix='synthetic-joined-', dir=SCRATCH) as name:
            data_root = Path(name) / 'forge-data'
            self.assertEqual(getter.joined_cache_bytes(str(data_root), components), 0)   # no copy yet
            key = hashlib.sha256(('a' * 64 + 'b' * 64).encode('ascii')).hexdigest()
            cache = Path(name) / 'forge-data.deep32-media' / key
            (cache / 'payload').mkdir(parents=True)
            (cache / 'payload/a.bin').write_bytes(b'x' * 7)
            (cache / 'PAYLOAD.SHA256').write_bytes(b's\n')
            (cache / 'RUNTIME.json').write_bytes(b'{}\n')
            def rows(*entries):
                raw = json.dumps([{'path': path, 'size': size, 'sha256': 'c' * 64} for path, size in entries])
                (cache / 'PAYLOAD.json').write_bytes(raw.encode('ascii'))
                return len(raw)
            listed = rows(('a.bin', 7))
            self.assertEqual(getter.joined_cache_bytes(str(data_root), components), 7 + listed + 2 + 3)
            self.assertEqual(getter.joined_cache_bytes(str(data_root), None), 0)
            self.assertEqual(getter.joined_cache_bytes(str(data_root), components[:1]), 0)   # another key
            for entries in ((('/a.bin', 7),), (('../a.bin', 7),), (('a.bin', 8),), (('missing.bin', 1),)):
                with self.subTest(entries=entries):
                    rows(*entries)
                    self.assertEqual(getter.joined_cache_bytes(str(data_root), components), 0)
            rows(('a.bin', 7))
            (cache / 'payload/a.bin').unlink()
            try:
                os.symlink(cache / 'RUNTIME.json', cache / 'payload/a.bin')
            except (OSError, NotImplementedError):
                return   # this account may not create symbolic links (Windows without the privilege)
            self.assertEqual(getter.joined_cache_bytes(str(data_root), components), 0)   # a link is not a file

    def test_volume_observation_has_no_fake_root_fallback(self):
        existing = Path(__file__).resolve().parent
        missing = existing / 'synthetic-nonexistent-data-root'
        expected = getter.observed_volume(str(existing), existing=True)
        self.assertEqual(getter.observed_volume(str(missing), existing=False)[0], expected[0])
        with self.assertRaises(getter.manifest.Refusal):
            getter.observed_volume(str(missing), existing=True)
        for invalid in (None, 'relative', "//remote/share"):
            with self.subTest(path=invalid), self.assertRaises(getter.manifest.Refusal):
                getter.observed_volume(invalid, existing=True)


class MetadataTransport(unittest.TestCase):
    def test_windows_online_refuses_elevation_before_network_or_transfer_writes(self):
        transfer = getter.transfer
        with mock.patch.object(transfer, 'os', mock.Mock()) as platform, \
                mock.patch.object(transfer, '_windows_caller', side_effect=transfer.LocalError('unelevated only')) as caller, \
                mock.patch.object(transfer.http.client, 'HTTPSConnection', side_effect=AssertionError('network')), \
                mock.patch.object(transfer.FileSystem, 'private_directory', side_effect=AssertionError('transfer writes')):
            platform.name = 'nt'
            with self.assertRaisesRegex(transfer.LocalError, 'unelevated only'):
                transfer.open_http('https://example.org/file', {})
            caller.assert_called_once_with(require_unelevated=True)
            caller.reset_mock()
            with self.assertRaisesRegex(transfer.LocalError, 'unelevated only'):
                transfer.fetch_whole({}, 'unused', release='v0.1.0-alpha.2', revision=1,
                                     fingerprint='A' * 40, torrent_url='https://example.org/release')
            caller.assert_called_once_with(require_unelevated=True)

    def test_scoped_capability_bound_keeps_default_metadata_at_five_mib(self):
        raw = b' ' * getter.MAX_CAPABILITY
        response = Response(raw)
        self.assertEqual(getter.fetch_metadata('https://example.org/capability', opener=lambda *a, **k: response,
                                               max_bytes=getter.MAX_CAPABILITY), raw)
        for limit, length in ((getter.manifest.MAX_META, getter.manifest.MAX_META + 1),
                              (getter.MAX_CAPABILITY, getter.MAX_CAPABILITY + 1)):
            response = Response(b' ' * length)
            with self.assertRaises(getter.manifest.Refusal):
                getter.fetch_metadata('https://example.org/metadata', opener=lambda *a, **k: response, max_bytes=limit)
            self.assertTrue(response.closed)

    def test_valid_length_and_chunked(self):
        for response in (Response(b'abc'), Response(b'3\r\nabc\r\n0\r\n\r\n', headers=[('Transfer-Encoding', 'chunked')])):
            self.assertEqual(getter.fetch_metadata('https://example.org/data', opener=lambda *a, **k: response), b'abc')
            self.assertTrue(response.closed)

    def test_bad_headers_lengths_and_status(self):
        for response in (Response(b'abc', status=404), Response(b'abc', headers=[('Content-Encoding', 'gzip')]),
                Response(b'abc', headers=[('Content-Length', '2')]), Response(b'abc', headers=[('Content-Length', '4')]),
                Response(b'abc', headers=[('Content-Length', '3'), ('Content-Length', '3')]),
                Response(b'abc', headers=[('Content-Length', '3'), ('Transfer-Encoding', 'chunked')]),
                Response(b'abc', headers=[('Content-Length', str(getter.manifest.MAX_META + 1))])):
            with self.subTest(headers=response.headers):
                with self.assertRaises(getter.manifest.Refusal):
                    getter.fetch_metadata('https://example.org/data', opener=lambda *a, **k: response)
                self.assertTrue(response.closed)

    def test_bound_no_length(self):
        response = Response(b'x' * (getter.manifest.MAX_META + 1), headers=[('Connection', 'close')])
        with self.assertRaises(getter.manifest.Refusal):
            getter.fetch_metadata('https://example.org/data', opener=lambda *a, **k: response)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scratch-root', required=True)
    args, rest = parser.parse_known_args()
    SCRATCH = Path(args.scratch_root)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0], *rest])
