"""Offline corresponding-source checks using synthetic PyPI JSON and wheel bytes."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2] / 'src' / 'build' / 'disc3-pins'
SPEC = importlib.util.spec_from_file_location('source_resolver', Path(os.environ.get('D32_RESOLVER') or ROOT / 'resolve-pins.py'))
resolver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resolver)

FILENAME = 'mineru-4.0.7-py3-none-any.whl'
WHEEL_URL = 'https://files.pythonhosted.org/locked/' + FILENAME
PYPI_JSON = resolver.PYPI_JSON.format(name='mineru', version='4.0.7')
KIND = 'wheel-as-source: pure Python, PyPI has no sdist'
DOCUMENTARY = {'Source': 'https://example.invalid/source', 'Homepage': 'https://example.invalid/home'}


def wheel(members=None, tag='py3-none-any'):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        entries = {'mineru/__init__.py': b'VALUE = 1\n',
                   'mineru-4.0.7.dist-info/WHEEL': ('Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: ' + tag + '\n').encode('ascii')}
        entries.update(members or {})
        for name, data in sorted(entries.items()):
            archive.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), data)
    return stream.getvalue()


class CorrespondingSource(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='d32-source-')
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.store = self.folder / 'store'
        (self.store / 'locks').mkdir(parents=True)
        self.calls = []

    def context(self, body=None, filename=FILENAME, locked_digest=None, pypi_digest=None, sdists=None,
                recorded=None, urls=None):
        body = wheel() if body is None else body
        digest = resolver.sha256_bytes(body)
        self.lock_path = self.store / 'locks' / 'converter.lock'
        self.lock_path.write_text('mineru==4.0.7 --hash=sha256:' + (locked_digest or digest) + '\n', encoding='ascii', newline='\n')
        metadata = {'info': {'project_urls': dict(DOCUMENTARY, Issues='https://example.invalid/issues')},
                    'urls': urls if urls is not None else [{'packagetype': 'bdist_wheel', 'filename': filename,
                                                           'url': WHEEL_URL.replace(FILENAME, filename),
                                                           'digests': {'sha256': pypi_digest or digest}}]}
        metadata['urls'].extend(sdists or [])
        self.metadata = metadata

        def get_ok(url):
            self.calls.append(url)
            if url == PYPI_JSON:
                return resolver.Response(200, {}, json.dumps(metadata).encode('ascii'), url)
            expected = recorded['url'] if recorded else WHEEL_URL.replace(FILENAME, filename)
            if url == expected:
                return resolver.Response(200, {}, body, url)
            raise AssertionError('unexpected fetch: ' + url)

        records = {}
        if recorded:
            records['fetch.locks.converter.sha256'] = {'authentication': {'wheels': [recorded]}}
        self.ctx = types.SimpleNamespace(store=self.store, lead={'corresponding_source': ['mineru']},
                                         net=types.SimpleNamespace(get_ok=get_ok), records=records)
        self.doc = {'fetch': {'locks': {'converter': {'sha256': resolver.sha256_file(self.lock_path)}}, 'files': []}}
        return self.ctx, self.doc

    def resolve(self, **kwargs):
        ctx, doc = self.context(**kwargs)
        return resolver.CorrespondingSource().resolve(ctx, doc)

    def test_pure_python_wheel_accepted_with_one_source_row_and_documentary_links_only(self):
        result = self.resolve()
        self.assertEqual(len(result.file_rows), 1)
        path, url, digest, source, auth, extra = result.file_rows[0]
        self.assertEqual((path, url, digest, source), ('payload/licences/source/' + FILENAME, WHEEL_URL,
                                                     resolver.sha256_bytes(wheel()), PYPI_JSON))
        self.assertEqual(extra['kind'], KIND)
        self.assertEqual(auth['project_urls_documentary_only'], DOCUMENTARY)
        self.assertEqual(auth['wheel_tags'], ['py3-none-any'])
        self.assertEqual(auth['wheel_members'], ['mineru-4.0.7.dist-info/WHEEL', 'mineru/__init__.py'])
        self.assertEqual(auth['locked_url_basis'], 'PyPI wheel URL matched to hash-only converter lock')
        self.assertEqual(self.calls, [PYPI_JSON, WHEEL_URL])
        appended = resolver.expand_file_rows(self.doc, result)
        self.doc['fetch']['files'].extend(appended)
        for path, value in result.updates.items():
            resolver.put(self.doc, path, value)
        self.assertEqual(self.doc['fetch']['files'], [{'path': 'payload/licences/source/' + FILENAME,
                                                     'url': WHEEL_URL, 'sha256': digest, 'kind': KIND}])
        self.assertIn(('fetch', 'files', 0, 'kind'), resolver.CorrespondingSource().paths(self.doc))
        self.assertEqual(len(result.records), 3)

    def test_every_listed_compiled_suffix_refused(self):
        for suffix in ('.so', '.pyd', '.dll', '.dylib', '.a', '.o', '.exe', '.bin', '.SO'):
            with self.subTest(suffix=suffix):
                self.calls.clear()
                with self.assertRaisesRegex(resolver.Refusal, 'contains compiled members: mineru/native'):
                    self.resolve(body=wheel({'mineru/native' + suffix: b'synthetic compiled material'}))

    def test_non_py3_none_any_filename_refused_before_download(self):
        with self.assertRaisesRegex(resolver.Refusal, 'must be tagged py3-none-any'):
            self.resolve(filename='mineru-4.0.7-cp312-cp312-linux_x86_64.whl')
        self.assertEqual(self.calls, [PYPI_JSON])

    def test_internal_non_pure_tag_refused(self):
        with self.assertRaisesRegex(resolver.Refusal, 'internal wheel tags must be py3-none-any'):
            self.resolve(body=wheel(tag='cp312-cp312-linux_x86_64'))

    def test_locked_hash_differs_from_download_refused(self):
        with self.assertRaisesRegex(resolver.Refusal, 'sha256 differs from the converter lock'):
            self.resolve(locked_digest='a' * 64, pypi_digest='a' * 64)
        self.assertEqual(self.calls, [PYPI_JSON, WHEEL_URL])

    def test_pypi_wheel_hash_not_locked_refused_before_download(self):
        with self.assertRaisesRegex(resolver.Refusal, 'exactly one PyPI wheel'):
            self.resolve(pypi_digest='a' * 64)
        self.assertEqual(self.calls, [PYPI_JSON])

    def test_recorded_locked_url_used_instead_of_other_pypi_url(self):
        recorded = {'name': 'mineru', 'version': '4.0.7', 'line_version': '4.0.7',
                    'wheel': FILENAME, 'url': 'https://files.pythonhosted.org/original/' + FILENAME,
                    'sha256': resolver.sha256_bytes(wheel())}
        result = self.resolve(recorded=recorded)
        self.assertEqual(result.file_rows[0][1], recorded['url'])
        self.assertEqual(result.file_rows[0][4]['locked_url_basis'], 'converter lock evidence wheel URL')
        self.assertEqual(self.calls, [PYPI_JSON, recorded['url']])

    def test_recorded_url_cannot_override_locked_hash(self):
        recorded = {'name': 'mineru', 'version': '4.0.7', 'wheel': FILENAME, 'url': WHEEL_URL, 'sha256': 'a' * 64}
        with self.assertRaisesRegex(resolver.Refusal, 'recorded wheel URL/hash'):
            self.resolve(recorded=recorded)
        self.assertEqual(self.calls, [PYPI_JSON])

    def test_hash_only_lock_with_ambiguous_pypi_urls_refused(self):
        row = {'packagetype': 'bdist_wheel', 'filename': FILENAME, 'url': WHEEL_URL,
               'digests': {'sha256': resolver.sha256_bytes(wheel())}}
        with self.assertRaisesRegex(resolver.Refusal, 'exactly one PyPI wheel'):
            self.resolve(urls=[row, dict(row, url='https://example.invalid/duplicate/' + FILENAME)])
        self.assertEqual(self.calls, [PYPI_JSON])

    def test_invalid_zip_refused(self):
        with self.assertRaisesRegex(resolver.Refusal, 'cannot inspect corresponding-source wheel'):
            self.resolve(body=b'synthetic non-ZIP material')

    def test_sdist_present_retains_original_row_and_authentication_without_wheel_fetch(self):
        sdist = {'packagetype': 'sdist', 'filename': 'mineru-4.0.7.tar.gz',
                 'url': 'https://files.pythonhosted.org/source/mineru-4.0.7.tar.gz', 'digests': {'sha256': 'b' * 64}}
        result = self.resolve(sdists=[sdist])
        self.assertEqual(result.file_rows[0][:5], ('payload/licences/source/mineru-4.0.7.tar.gz', sdist['url'],
                                                 'b' * 64, PYPI_JSON,
                                                 {'method': 'PyPI-published sha256 over HTTPS', 'package': 'mineru==4.0.7'}))
        self.assertNotIn('kind', result.file_rows[0][5])
        self.assertEqual(self.calls, [PYPI_JSON])

    def test_multiple_sdists_still_refused_without_wheel_fetch(self):
        sdist = {'packagetype': 'sdist'}
        with self.assertRaisesRegex(resolver.Refusal, 'has 2 source distributions on PyPI'):
            self.resolve(sdists=[sdist, sdist])
        self.assertEqual(self.calls, [PYPI_JSON])

    def test_converter_revision_refusal_explains_tree_id_without_validation_change(self):
        self.ctx = types.SimpleNamespace(lead={'production_converter': {'dir': str(self.folder),
                                                                       'revision': 'invalid', 'review': 'test review'}})
        with self.assertRaises(resolver.Refusal) as caught:
            resolver.LeadConverterSource().resolve(self.ctx, {})
        self.assertEqual(str(caught.exception), 'production_converter.revision must be a 40-hex git commit id, '
                                               'or the git tree id of the reviewed export')

    def test_non_checkout_export_still_accepts_reviewed_40_hex_revision(self):
        export = self.folder / 'synthetic-reviewed-export'
        export.mkdir()
        (export / 'adapter.py').write_bytes(b'VALUE = 1\n')
        revision = 'a' * 40
        ctx = types.SimpleNamespace(lead={'production_converter': {'dir': str(export), 'revision': revision,
                                                                  'review': 'synthetic reviewed export'}},
                                    store=self.store, store_uri=lambda path: Path(path).resolve().as_uri())
        result = resolver.LeadConverterSource().resolve(ctx, {})
        self.assertEqual(result.updates[('runtime', 'production_converter_source_revision')], revision)
        self.assertEqual(result.records[0]['authentication']['method'], 'lead-reviewed export')


if __name__ == '__main__':
    unittest.main(verbosity=2)
