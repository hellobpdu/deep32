"""Offline torch mirror checks using an official-index page and synthetic pip reports.

No docker or pip process is run: the pick-torch helper receives a fake report,
and the resolver's three container calls are answered in memory.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock
import urllib.parse


ROOT = Path(__file__).resolve().parents[2] / 'src' / 'build' / 'disc3-pins'
INDEX = 'https://download.pytorch.org/whl/cu130'
INDEX_PAGE = INDEX + '/torch/'
FILENAME = 'torch-2.14.0+cu130-cp312-cp312-manylinux_2_28_x86_64.whl'
ENCODED_FILENAME = urllib.parse.quote(FILENAME)
MIRROR = 'https://download-r2.pytorch.org/whl/cu130/' + ENCODED_FILENAME
DIGEST = '9c5693d3ca91743202034094ad3b3df20b5eb1373d5e0bc8a5d976be12c35302'
ENVIRONMENT = {'python': '3.12.14', 'platform': 'linux-x86_64', 'glibc': 'glibc 2.36'}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


resolver = load_module('torch_test_resolver', Path(os.environ.get('D32_RESOLVER') or ROOT / 'resolve-pins.py'))
lockgen = load_module('torch_test_lockgen', ROOT / 'lockgen.py')


def item(name, version, url, digest):
    return {'metadata': {'name': name, 'version': version},
            'download_info': {'url': url, 'archive_info': {'hashes': {'sha256': digest}}}}


def page(url=MIRROR, digest=DIGEST):
    # The absolute mirror URL, percent-encoded local version, and sha256 fragment
    # match the real official cu130 index link form recorded in T-0129.
    return ('<!DOCTYPE html><html><body><a href="' + url + '#sha256=' + digest + '">' +
            FILENAME + '</a></body></html>\n').encode('utf-8')


class FakeNet:
    def __init__(self, body):
        self.body = body
        self.requests = []

    def get_ok(self, url, **kwargs):
        self.requests.append(url)
        if url == INDEX_PAGE:
            return resolver.Response(200, {}, self.body, url)
        if url == resolver.PYPI_JSON.format(name='filelock', version='3.20.0'):
            metadata = {'urls': [{'filename': 'filelock-3.20.0-py3-none-any.whl',
                                  'digests': {'sha256': 'b' * 64}}], 'info': {}}
            return resolver.Response(200, {}, json.dumps(metadata).encode(), url)
        raise AssertionError('unexpected URL: ' + url)


class TorchMirror(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='t0129-torch-')
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.kit = self.folder / 'kit'
        direct = self.kit / 'containers' / 'reranker' / 'requirements-direct.txt'
        direct.parent.mkdir(parents=True)
        direct.write_text('torch==2.14.0+cu130\nfilelock==3.20.0\n', encoding='ascii')
        self.doc = {'runtime': {'base_image': 'python@sha256:' + 'a' * 64,
                                'python': '3.12.14', 'pymupdf_version': '1.26.5'},
                    'fetch': {'locks': {'reranker': {'index': 'https://pypi.org/simple',
                                                   'torch_index': INDEX}}}}

    def run_lock(self, url=MIRROR, report_digest=DIGEST, index_body=None, changed_torch=None, index_net=None):
        body = page() if index_body is None else index_body
        net = FakeNet(body) if index_net is None else index_net
        self.net = net
        self.calls = []
        ctx = types.SimpleNamespace(kit=self.kit, work=self.folder / 'work',
                                    store=self.folder / 'store', net=net,
                                    store_uri=lambda path: path.as_uri(),
                                    docker_pull=lambda image: self.calls.append('pull'))
        torch_item = item('torch', '2.14.0+cu130', url, report_digest)
        filelock_item = item('filelock', '3.20.0',
                             'https://files.pythonhosted.org/filelock-3.20.0-py3-none-any.whl', 'b' * 64)

        def container(unused_ctx, unused_image, unused_work, args):
            self.calls.append(args[0])
            if args[0] == 'pick-torch':
                emitted = []
                with mock.patch.object(lockgen, 'report', return_value={'install': [torch_item]}), \
                        mock.patch.object(lockgen, 'environment', return_value=ENVIRONMENT), \
                        mock.patch.object(lockgen, 'emit', side_effect=emitted.append):
                    lockgen.pick_torch(types.SimpleNamespace(index=INDEX, spec='torch==2.14.0+cu130'))
                return emitted[0]
            if args[0] == 'resolve':
                return {'report': {'install': [changed_torch or torch_item, filelock_item]}}
            if args[0] == 'verify':
                return {'files': [{'filename': FILENAME, 'sha256': report_digest},
                                  {'filename': 'filelock-3.20.0-py3-none-any.whl', 'sha256': 'b' * 64}]}
            raise AssertionError(args)

        lock = resolver.LockItem('reranker')
        with mock.patch.object(lock, 'container', side_effect=container):
            return lock.resolve(ctx, self.doc)

    def test_official_mirror_report_and_index_hash_are_accepted(self):
        result = self.run_lock()
        self.assertEqual(self.calls, ['pull', 'pick-torch', 'resolve', 'verify'])
        self.assertEqual(self.net.requests[0], INDEX_PAGE)
        self.assertFalse(any('download-r2.pytorch.org' in url for url in self.net.requests))
        lock = self.folder / 'store' / 'locks' / 'reranker.lock'
        self.assertIn('torch==2.14.0+cu130 --hash=sha256:' + DIGEST, lock.read_text())
        expected = {'index_url': INDEX_PAGE, 'wheel_url': MIRROR,
                    'index_page_sha256': hashlib.sha256(page()).hexdigest(), 'report_sha256': DIGEST}
        for record in result.records:
            self.assertEqual({key: record['authentication'][key] for key in expected}, expected)

    def test_official_index_host_still_accepted(self):
        url = INDEX + '/' + ENCODED_FILENAME
        self.run_lock(url=url, index_body=page(url))

    def test_relative_official_link_still_accepted(self):
        url = INDEX + '/' + ENCODED_FILENAME
        self.run_lock(url=url, index_body=page('/whl/cu130/' + ENCODED_FILENAME))

    def test_other_mirror_hosts_refused(self):
        for host in ('download-r3.pytorch.org', 'download-r2.pytorch.org.example.invalid',
                     'example.invalid', 'download.pytorch.org.example.invalid'):
            with self.subTest(host=host), self.assertRaisesRegex(resolver.Refusal, 'torch did not come from'):
                self.run_lock(url='https://' + host + '/whl/cu130/' + ENCODED_FILENAME)

    def test_non_https_wheel_refused(self):
        with self.assertRaisesRegex(resolver.Refusal, 'torch did not come from'):
            self.run_lock(url=MIRROR.replace('https:', 'http:'))

    def test_credentials_and_nonstandard_port_refused(self):
        for host in ('user@download-r2.pytorch.org', 'download-r2.pytorch.org:444'):
            with self.subTest(host=host), self.assertRaisesRegex(resolver.Refusal, 'torch did not come from'):
                self.run_lock(url='https://' + host + '/whl/cu130/' + ENCODED_FILENAME)

    def test_outside_index_path_refused(self):
        for prefix in ('/whl/cu129/', '/whl/cu130-elsewhere/', '/torch/',
                       '/whl/cu130/../cu129/', '/whl/cu130/%2e%2e/cu129/'):
            with self.subTest(prefix=prefix), self.assertRaisesRegex(resolver.Refusal, 'torch did not come from'):
                self.run_lock(url='https://download-r2.pytorch.org' + prefix + ENCODED_FILENAME)

    def test_index_and_report_hash_mismatch_refused(self):
        with self.assertRaisesRegex(resolver.Refusal, 'hash differs from'):
            self.run_lock(report_digest='0' * 64)

    def test_unlisted_wheel_refused(self):
        with self.assertRaisesRegex(resolver.Refusal, 'hash differs from'):
            self.run_lock(index_body=page().replace(ENCODED_FILENAME.encode(), b'other.whl'))

    def test_index_without_sha256_refused(self):
        with self.assertRaisesRegex(resolver.Refusal, 'hash differs from'):
            self.run_lock(index_body=page().replace(b'#sha256=', b'#sha512='))

    def test_changed_resolution_torch_url_refused(self):
        changed = item('torch', '2.14.0+cu130', INDEX + '/' + ENCODED_FILENAME, DIGEST)
        with self.assertRaisesRegex(resolver.Refusal, 'resolution changed the torch wheel'):
            self.run_lock(changed_torch=changed)

    def test_changed_resolution_torch_hash_refused(self):
        changed = item('torch', '2.14.0+cu130', MIRROR, '0' * 64)
        with self.assertRaisesRegex(resolver.Refusal, 'resolution changed the torch wheel'):
            self.run_lock(changed_torch=changed)

    def test_mirror_cannot_supply_the_index_page(self):
        self.doc['fetch']['locks']['reranker']['torch_index'] = INDEX.replace('download.', 'download-r2.')
        with self.assertRaisesRegex(resolver.Refusal, 'not an official HTTPS'):
            self.run_lock()
        self.assertEqual(self.net.requests, [])

    def test_index_redirect_to_mirror_refused_before_mirror_fetch(self):
        net = resolver.Net(lambda unused: None, retries=1)
        response = resolver.Response(302, {'location': MIRROR}, b'', INDEX_PAGE)
        with mock.patch.object(net, '_once', return_value=response) as once:
            with self.assertRaisesRegex(resolver.Refusal, 'redirect away from permitted host'):
                self.run_lock(index_net=net)
        once.assert_called_once_with(INDEX_PAGE, {})

    def test_other_fetches_keep_existing_redirect_policy(self):
        net = resolver.Net(lambda unused: None, retries=1)
        responses = [resolver.Response(302, {'location': MIRROR}, b'', INDEX_PAGE),
                     resolver.Response(200, {}, b'body', MIRROR)]
        with mock.patch.object(net, '_once', side_effect=responses) as once:
            self.assertEqual(net.get_ok(INDEX_PAGE).body, b'body')
        self.assertEqual(once.call_count, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
