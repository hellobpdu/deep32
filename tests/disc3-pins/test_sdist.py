"""Offline checks of the one lead-reviewed source archive; no upstream is contacted."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2] / 'src' / 'build' / 'disc3-pins'
INDEX = 'https://pypi.org/simple'
TORCH_INDEX = 'https://download.pytorch.org/whl/cu130'
DIGEST = '055ca12f62674fafed09427f176506079bc135638a14e23e25be909131928db2'
FILENAME = 'jieba-0.42.1.tar.gz'
URL = 'https://files.pythonhosted.org/packages/test/' + FILENAME
ALLOWED_BY = 'lead 2026-11-15: jieba 0.42.1 has no wheel on PyPI'
ENVIRONMENT = {'python': '3.12.14', 'platform': 'linux-x86_64', 'glibc': 'glibc 2.36'}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


resolver = load('sdist_resolver', Path(os.environ.get('D32_RESOLVER') or ROOT / 'resolve-pins.py'))
lockgen = load('sdist_lockgen', Path(os.environ.get('D32_LOCKGEN_SOURCE') or ROOT / 'lockgen.py'))


def row(name='jieba', version='0.42.1', digest=DIGEST, filename=FILENAME):
    return {'name': name, 'version': version, 'sha256': digest, 'filename': filename,
            'url': 'https://files.pythonhosted.org/packages/test/' + filename}


def item(value):
    return {'metadata': {'name': value['name'], 'version': value['version']},
            'download_info': {'url': value['url'], 'archive_info': {'hashes': {'sha256': value['sha256']}}}}


class Sdist(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='t0130-sdist-')
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.universe = self.folder / 'universe.json'
        self.calls = self.folder / 'calls.jsonl'
        self.requirements = self.folder / 'direct.txt'
        self.env = dict(os.environ, D32_LOCKGEN_PIP=str(Path(__file__).resolve().with_name('fakepip.py')),
                        D32_FAKEPIP_UNIVERSE=str(self.universe), D32_FAKEPIP_CALLS=str(self.calls))

    def universe_for(self, values):
        pypi, torch = {}, {}
        for value in values:
            target = torch if value['name'] == 'torch' else pypi
            target.setdefault(resolver.normalise_name(value['name']), []).append(value)
        self.universe.write_text(json.dumps({'indexes': {INDEX: pypi, TORCH_INDEX: torch}}), encoding='ascii', newline='\n')

    def report(self, value):
        self.universe_for([value])
        self.requirements.write_text(value['name'] + '==' + value['version'] + '\n', encoding='ascii', newline='\n')
        return subprocess.run([sys.executable, '-I', '-B', str(Path(os.environ.get('D32_LOCKGEN_SOURCE') or ROOT / 'lockgen.py')),
                               'resolve', '--requirements', str(self.requirements), '--index', INDEX],
                              env=self.env, capture_output=True, text=True)

    def call_list(self):
        return [json.loads(line) for line in self.calls.read_text(encoding='ascii').splitlines()]

    def test_allowlist_copies_equal_and_exact(self):
        expected = {('jieba', '0.42.1'): DIGEST}
        self.assertEqual(lockgen.SDIST_ALLOWED, expected)
        self.assertEqual(resolver.SDIST_ALLOWED, expected)

    def test_allowed_report_uses_standin_and_binary_exceptions(self):
        run = self.report(row())
        self.assertEqual(run.returncode, 0, run.stderr)
        emitted = json.loads(run.stdout.split('D32-JSON ', 1)[1])
        self.assertEqual(emitted['report']['install'][0]['download_info']['archive_info']['hashes']['sha256'], DIGEST)
        call = next(call for call in self.call_list() if 'install' in call)
        self.assertIn('--only-binary=:all:', call)
        self.assertEqual([arg for arg in call if arg.startswith('--no-binary=')], ['--no-binary=jieba'])

    def test_report_hash_differs_exact_message(self):
        run = self.report(row(digest='0' * 64))
        self.assertEqual(run.returncode, 1)
        self.assertEqual(run.stderr.strip(), 'sdist hash differs: jieba 0.42.1: report ' + '0' * 64 + ', allowed ' + DIGEST)

    def test_report_wrong_version_refused(self):
        value = row(version='0.42.0', filename='jieba-0.42.0.tar.gz')
        run = self.report(value)
        self.assertEqual(run.returncode, 1)
        self.assertEqual(run.stderr.strip(), 'sdist not allowed: jieba 0.42.0 (' + value['url'] + ')')

    def test_report_other_sdist_refused(self):
        value = row(name='foo', version='1.0', filename='foo-1.0.tar.gz')
        run = self.report(value)
        self.assertEqual(run.returncode, 1)
        self.assertEqual(run.stderr.strip(), 'sdist not allowed: foo 1.0 (' + value['url'] + ')')

    def test_report_name_canonicalised_and_version_compared_as_string(self):
        self.assertEqual(self.report(row(name='Jieba')).returncode, 0)
        value = row(version='0.42.1.0', filename='jieba-0.42.1.0.tar.gz')
        run = self.report(value)
        self.assertEqual(run.returncode, 1)
        self.assertEqual(run.stderr.strip(), 'sdist not allowed: jieba 0.42.1.0 (' + value['url'] + ')')

    def verify(self, allowed_digest, archive_digest=None, filename=FILENAME):
        values = [row(digest=archive_digest or allowed_digest, filename=filename)]
        for name in ('torch', 'filelock'):
            wheel = name + '-1.0-py3-none-any.whl'
            values.append(row(name=name, version='1.0', filename=wheel,
                              digest=hashlib.sha256(('fake wheel ' + wheel).encode('ascii')).hexdigest()))
        self.universe_for(values)
        lock = self.folder / 'requirements.lock'
        lock.write_text('\n'.join(value['name'] + '==' + value['version'] + ' --hash=sha256:' + value['sha256']
                                  for value in values) + '\n', encoding='ascii', newline='\n')
        args = types.SimpleNamespace(lock=lock, index=INDEX, torch_index=TORCH_INDEX)
        emitted = []
        with mock.patch.dict(os.environ, self.env), \
                mock.patch.object(lockgen, 'SDIST_ALLOWED', {('jieba', '0.42.1'): allowed_digest}), \
                mock.patch.object(lockgen, 'emit', side_effect=emitted.append):
            lockgen.verify(args)
        return emitted[0]

    def test_verify_synthetic_archive_hash_and_per_package_flags(self):
        # A synthetic archive cannot have the real upstream digest. Only this unit's table
        # is patched to its actual digest; production table parity is asserted separately.
        digest = hashlib.sha256(('fake wheel ' + FILENAME).encode('ascii')).hexdigest()
        result = self.verify(digest)
        self.assertEqual(next(value for value in result['files'] if value['filename'] == FILENAME)['sha256'], digest)
        calls = [call for call in self.call_list() if 'download' in call]
        self.assertEqual(len(calls), 3)
        for call in calls:
            name = Path(call[call.index('--requirement') + 1]).name
            self.assertIn('--require-hashes', call)
            self.assertIn('--no-deps', call)
            if name == 'jieba.lock':
                self.assertIn('--no-binary=jieba', call)
                self.assertNotIn('--only-binary=:all:', call)
                self.assertEqual(call[call.index('--index-url') + 1], INDEX)
            else:
                self.assertIn('--only-binary=:all:', call)
                self.assertFalse(any(arg.startswith('--no-binary=') for arg in call))

    def test_verify_download_hash_compared_to_lead_table(self):
        actual = hashlib.sha256(('fake wheel ' + FILENAME).encode('ascii')).hexdigest()
        with self.assertRaises(SystemExit) as caught:
            self.verify(DIGEST)
        self.assertEqual(str(caught.exception), 'sdist hash differs: jieba 0.42.1: downloaded ' + actual + ', allowed ' + DIGEST)

    def test_verify_lock_hash_compared_to_lead_table(self):
        with self.assertRaisesRegex(SystemExit, '^sdist lock hash differs: jieba 0.42.1$'):
            self.verify(DIGEST, archive_digest='0' * 64)
        self.assertFalse(self.calls.exists())

    def test_verify_unexpected_download_filename_refused(self):
        with self.assertRaisesRegex(SystemExit, '^sdist download differs: jieba 0.42.1: expected '):
            self.verify(DIGEST, filename='jieba-0.42.1.zip')

    def host_lock(self, value=None, downloaded_digest=DIGEST):
        value = value or row()
        kit = self.folder / 'kit'
        direct = kit / 'containers' / 'converter-agpl' / 'requirements-direct.txt'
        direct.parent.mkdir(parents=True, exist_ok=True)
        direct.write_text('torch==1.0\nfilelock==1.0\n', encoding='ascii', newline='\n')
        torch_url = TORCH_INDEX + '/torch-1.0-py3-none-any.whl'
        torch_digest = 'a' * 64
        torch = row(name='torch', version='1.0', digest=torch_digest, filename='torch-1.0-py3-none-any.whl')
        torch['url'] = torch_url
        filelock = row(name='filelock', version='1.0', digest='b' * 64, filename='filelock-1.0-py3-none-any.whl')
        urls = {resolver.PYPI_JSON.format(name=value['name'], version=value['version']): value,
                resolver.PYPI_JSON.format(name='filelock', version='1.0'): filelock}

        def get_ok(url, **kwargs):
            if url == TORCH_INDEX + '/torch/':
                return resolver.Response(200, {}, ('<a href="' + torch_url + '#sha256=' + torch_digest + '">torch</a>').encode('ascii'), url)
            published = urls[url]
            data = {'urls': [{'filename': published['filename'], 'digests': {'sha256': published['sha256']}}], 'info': {}}
            return resolver.Response(200, {}, json.dumps(data).encode('ascii'), url)

        ctx = types.SimpleNamespace(kit=kit, work=self.folder / 'work', store=self.folder / 'store',
                                    net=types.SimpleNamespace(get_ok=get_ok), store_uri=lambda path: path.as_uri(),
                                    docker_pull=lambda image: None)
        doc = {'runtime': {'base_image': 'python@sha256:' + 'c' * 64, 'python': '3.12.14', 'pymupdf_version': '1.26.5'},
               'fetch': {'locks': {'converter': {'index': INDEX, 'torch_index': TORCH_INDEX}}}}

        def container(unused_ctx, unused_image, unused_work, args):
            if args[0] == 'pick-torch':
                return dict(torch, env=ENVIRONMENT)
            if args[0] == 'resolve':
                # Execute the real report helper against the stand-in pip on the allowed
                # path; refusal tests also inject reports directly at the host boundary.
                if value == row():
                    run = self.report(value)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    source = json.loads(run.stdout.split('D32-JSON ', 1)[1])['report']['install']
                else:
                    source = [item(value)]
                return {'report': {'install': [item(torch), item(filelock)] + source}}
            if args[0] == 'verify':
                return {'files': [{'filename': torch['filename'], 'sha256': torch_digest},
                                  {'filename': filelock['filename'], 'sha256': filelock['sha256']},
                                  {'filename': value['filename'], 'sha256': downloaded_digest}]}
            raise AssertionError(args)

        lock = resolver.LockItem('converter')
        with mock.patch.object(lock, 'container', side_effect=container):
            return lock.resolve(ctx, doc)

    def test_allowed_report_resolves_to_sdist_lock_row(self):
        result = self.host_lock()
        rows = result.records[0]['authentication']['wheels']
        source = next(value for value in rows if value['name'] == 'jieba')
        self.assertEqual({key: source[key] for key in ('kind', 'filename', 'url', 'sha256', 'allowed_by')},
                         {'kind': 'sdist', 'filename': FILENAME, 'url': URL, 'sha256': DIGEST, 'allowed_by': ALLOWED_BY})
        for wheel in (value for value in rows if value['name'] != 'jieba'):
            self.assertIn('wheel', wheel)
            self.assertNotIn('kind', wheel)
            self.assertNotIn('filename', wheel)
        lock = self.folder / 'store' / 'locks' / 'converter.lock'
        self.assertIn('jieba==0.42.1 --hash=sha256:' + DIGEST, lock.read_text(encoding='ascii'))

    def test_host_report_hash_differs_exact_message(self):
        with self.assertRaises(resolver.Refusal) as caught:
            self.host_lock(row(digest='0' * 64), downloaded_digest='0' * 64)
        self.assertEqual(str(caught.exception), 'sdist hash differs: jieba 0.42.1: report ' + '0' * 64 + ', allowed ' + DIGEST)

    def test_host_other_sdist_refused(self):
        value = row(name='foo', version='1.0', filename='foo-1.0.tar.gz')
        with self.assertRaises(resolver.Refusal) as caught:
            self.host_lock(value)
        self.assertEqual(str(caught.exception), 'sdist not allowed: foo 1.0 (' + value['url'] + ')')

    def test_host_wrong_version_refused(self):
        value = row(version='0.42.0', filename='jieba-0.42.0.tar.gz')
        with self.assertRaises(resolver.Refusal) as caught:
            self.host_lock(value)
        self.assertEqual(str(caught.exception), 'sdist not allowed: jieba 0.42.0 (' + value['url'] + ')')

    def test_host_unexpected_filename_refused(self):
        value = row(filename='jieba-0.42.1.zip')
        with self.assertRaisesRegex(resolver.Refusal, '^sdist not allowed: '):
            self.host_lock(value)

    def test_host_download_hash_differs_refused(self):
        with self.assertRaisesRegex(resolver.Refusal, 'did not return exactly the locked wheels'):
            self.host_lock(downloaded_digest='0' * 64)


if __name__ == '__main__':
    unittest.main(verbosity=2)
