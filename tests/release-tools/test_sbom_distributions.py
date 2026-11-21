"""Synthetic-only distribution metadata and lock-boundary checks."""
import hashlib
import io
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src' / 'release-tools'))
from sbom import distributions as subject


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='sbom-distributions-')
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def folder(self, name='wheels'):
        folder = self.root / name
        folder.mkdir()
        return folder

    @staticmethod
    def metadata(name='example_pkg', version='1.2', headers=''):
        return ('Metadata-Version: 2.4\nName: ' + name + '\nVersion: ' + version + '\n'
                + headers + '\nSynthetic public package.\n').encode('utf-8')

    def wheel(self, folder, name='example_pkg', version='1.2', *, filename=None,
              metadata=None, wheel=None, extra=(), omit=()):
        filename = filename or name + '-' + version + '-py3-none-any.whl'
        path = folder / filename
        prefix = name + '-' + version + '.dist-info/'
        contents = [(prefix + 'METADATA', metadata if metadata is not None else self.metadata(name, version)),
                    (prefix + 'WHEEL', wheel if wheel is not None else
                     b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n'),
                    (name + '/__init__.py', b'# synthetic fixture only\n')]
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED) as archive:
            for member, data in contents + list(extra):
                if isinstance(member, str) and member in omit:
                    continue
                if isinstance(member, str):
                    # Windows ZipInfo construction normalises backslashes. Set
                    # the raw name afterwards so hostile fixtures stay hostile.
                    raw = zipfile.ZipInfo('synthetic')
                    raw.filename = member
                    raw.orig_filename = member
                    member = raw
                archive.writestr(member, data)
        return path

    @staticmethod
    def lock(*paths, alternatives=()):
        lines = []
        for path in paths:
            name, version = path.name.split('-')[:2]
            hashes = sorted({sha(path), *alternatives})
            lines.append(name + '==' + version + ''.join(' --hash=sha256:' + value for value in hashes))
        return '\n'.join(lines)

    def sdist(self, folder, entries=None):
        path = folder / 'jieba-0.42.1.tar.gz'
        if entries is None:
            entries = [('jieba-0.42.1/PKG-INFO', self.metadata('jieba', '0.42.1', 'License: MIT\n')),
                       ('jieba-0.42.1/LICENSE', b'Synthetic licence fixture.\n')]
        with tarfile.open(path, 'w:gz') as archive:
            for name, data in entries:
                item = name if isinstance(name, tarfile.TarInfo) else tarfile.TarInfo(name)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
        return path

    def test_selected_actual_hash_is_separate_from_allowed_alternatives(self):
        one = self.wheel(self.folder('one'))
        two = self.wheel(self.folder('two'), extra=[('example_pkg/variant.txt', b'different bytes')])
        lock = self.lock(one, alternatives=[sha(two)])
        first = subject.normalise_wheelhouse(one.parent, lock)[0]
        second = subject.normalise_wheelhouse(two.parent, lock)[0]
        self.assertNotEqual(first['sha256'], second['sha256'])
        self.assertEqual(first['allowed_hashes'], second['allowed_hashes'])
        self.assertEqual(first['sha256'], sha(one))
        self.assertEqual(first['size'], one.stat().st_size)
        self.assertEqual(first['path'], one.name)
        self.assertEqual(first['name'], 'example-pkg')
        self.assertEqual(first['kind'], 'wheel')
        self.assertNotIn('generated_from', first)

    def test_declared_dependencies_and_licence_evidence(self):
        headers = ('Requires-Dist: zed>=2; python_version >= "3.10"\n'
                   'Requires-Dist: alpha[extra] == 1\nRequires-Python: >=3.10\n'
                   'License: MIT\nLicense-Expression: MIT\nLicense-File: COPYING\n'
                   'Author-Email: person@example.invalid\n'
                   'Home-page: https://example.invalid/project\n')
        licence = b'Synthetic public licence bytes.\n'
        path = self.wheel(self.folder(), metadata=self.metadata(headers=headers),
                          extra=[('example_pkg-1.2.dist-info/licenses/COPYING', licence)])
        row = subject.normalise_wheelhouse(path.parent, self.lock(path), usage='build-only')[0]
        self.assertEqual(row['requires_dist'], ['alpha[extra] == 1', 'zed>=2; python_version >= "3.10"'])
        self.assertEqual(row['requires_python'], '>=3.10')
        self.assertEqual(row['usage'], 'build-only')
        self.assertEqual(row['licence'], 'MIT')
        self.assertEqual(row['licence_expression'], 'MIT')
        self.assertEqual(row['licence_files'], [{
            'path': 'example_pkg-1.2.dist-info/licenses/COPYING',
            'size': len(licence), 'sha256': hashlib.sha256(licence).hexdigest()}])
        self.assertEqual(len(row['metadata_files']), 2)
        self.assertNotIn('person@', repr(row))
        self.assertNotIn('example.invalid', repr(row))

    def test_sorted_results_and_stable_usage(self):
        folder = self.folder()
        zed = self.wheel(folder, 'zed')
        alpha = self.wheel(folder, 'alpha')
        lock = self.lock(zed, alpha)
        rows = subject.normalise_wheelhouse(folder, lock, usage='bundled')
        self.assertEqual([row['name'] for row in rows], ['alpha', 'zed'])
        self.assertEqual(rows, subject.normalise_wheelhouse(folder, lock, usage='bundled'))

    def test_safe_direct_https_dependency_kept_and_private_references_refused(self):
        declared = 'dependency[extra] @ https://packages.example.invalid/dependency.whl ; python_version >= "3.10"'
        path = self.wheel(self.folder('public'), metadata=self.metadata(headers='Requires-Dist: ' + declared + '\n'))
        row = subject.normalise_wheelhouse(path.parent, self.lock(path))[0]
        self.assertEqual(row['requires_dist'], [declared])
        invalid = ['dependency @ file:///tmp/private.whl',
                   'dependency @ https://host.local/private.whl',
                   'dependency @ https://127.1/private.whl',
                   'dependency @ https://person:password@example.invalid/dependency.whl',
                   'dependency @ https://packages.example.invalid/Users/operator/dependency.whl',
                   'dependency @ https://packages.example.invalid/%2e%2e/dependency.whl',
                   'dependency @ https://packages.example.invalid/dependency.whl ; unknown_variable == "x"',
                   'not a name @ https://packages.example.invalid/dependency.whl']
        for index, declaration in enumerate(invalid):
            with self.subTest(index=index):
                path = self.wheel(self.folder(str(index)), metadata=self.metadata(headers='Requires-Dist: ' + declaration + '\n'))
                with self.assertRaises(ValueError):
                    subject.normalise_wheelhouse(path.parent, self.lock(path))

    def test_unrepresentable_optional_licence_is_unknown_with_exact_evidence(self):
        for index, declaration in enumerate(['M' * 600, 'line one\n line two', 'person@example.invalid']):
            with self.subTest(index=index):
                metadata = self.metadata(headers='License: ' + declaration + '\nLicense-Expression: ' + declaration + '\n')
                path = self.wheel(self.folder(str(index)), metadata=metadata)
                row = subject.normalise_wheelhouse(path.parent, self.lock(path))[0]
                self.assertEqual(row['licence'], 'NOASSERTION')
                self.assertEqual(row['licence_expression'], 'NOASSERTION')
                record = next(item for item in row['metadata_files'] if item['path'].endswith('/METADATA'))
                self.assertEqual(record['sha256'], hashlib.sha256(metadata).hexdigest())
                self.assertNotIn('person@', repr(row))

    def test_build_torch_local_version_rule_retains_actual_identity(self):
        path = self.wheel(self.folder(), 'torch', '2.1.0+cu121')
        lock = 'torch==2.1.0 --hash=sha256:' + sha(path)
        row = subject.normalise_wheelhouse(path.parent, lock)[0]
        self.assertEqual(row['version'], '2.1.0+cu121')
        self.assertEqual(row['lock_version'], '2.1.0')

    def test_lock_grammar_and_duplicate_normalised_names(self):
        path = self.wheel(self.folder())
        invalid = ['example_pkg>=1.2 --hash=sha256:' + sha(path),
                   self.lock(path) + '\nexample-pkg==1.2 --hash=sha256:' + sha(path),
                   self.lock(path) + ' # comment', '', {}, {'malformed': {'hashes': []}},
                   'example_pkg==1.2 --hash=sha256:' + 'f' * 63]
        for lock in invalid:
            with self.subTest(lock=lock), self.assertRaises(ValueError):
                subject.normalise_wheelhouse(path.parent, lock)

    def test_hash_mismatch_and_missing_locked_distribution(self):
        path = self.wheel(self.folder())
        for lock in ('example_pkg==1.2 --hash=sha256:' + 'f' * 64,
                     self.lock(path) + '\nmissing==1 --hash=sha256:' + 'f' * 64):
            with self.subTest(lock=lock), self.assertRaises(ValueError):
                subject.normalise_wheelhouse(path.parent, lock)

    def test_extra_archive_and_duplicate_selection_refused(self):
        folder = self.folder()
        one = self.wheel(folder)
        two = self.wheel(folder, filename='example_pkg-1.2-1-py3-none-any.whl')
        with self.assertRaisesRegex(ValueError, 'duplicate wheelhouse'):
            subject.normalise_wheelhouse(folder, self.lock(one, alternatives=[sha(two)]))
        two.unlink()
        (folder / 'unexpected.zip').write_bytes(b'synthetic')
        with self.assertRaises(ValueError):
            subject.normalise_wheelhouse(folder, self.lock(one))

    def test_filename_and_metadata_identity_must_agree(self):
        for metadata in (self.metadata('other'), self.metadata(version='9.9')):
            with self.subTest(metadata=metadata):
                folder = self.folder('case-' + hashlib.sha256(metadata).hexdigest()[:8])
                path = self.wheel(folder, metadata=metadata)
                with self.assertRaisesRegex(ValueError, 'identity differ'):
                    subject.normalise_wheelhouse(folder, self.lock(path))

    def test_missing_ambiguous_and_malformed_metadata_refused(self):
        for index, (metadata, extra, omit) in enumerate([
            (self.metadata(headers='Name: duplicate\n'), [], []),
            (b'Name: example_pkg\nVersion: 1.2\n\n', [], []),
            (b'Metadata-Version: 2.4\nName: example_pkg\nVersion: 1.2\nX: \xff\n\n', [], []),
            (None, [('other-1.dist-info/METADATA', self.metadata('other', '1'))], []),
            (None, [], ['example_pkg-1.2.dist-info/WHEEL']),
        ]):
            with self.subTest(index=index):
                path = self.wheel(self.folder(str(index)), metadata=metadata, extra=extra, omit=omit)
                with self.assertRaises(ValueError):
                    subject.normalise_wheelhouse(path.parent, self.lock(path))

    def test_compatibility_metadata_must_match_filename(self):
        wheel = b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: cp315-none-any\n\n'
        path = self.wheel(self.folder(), wheel=wheel)
        with self.assertRaisesRegex(ValueError, 'compatibility tags differ'):
            subject.normalise_wheelhouse(path.parent, self.lock(path))

    def test_unsafe_wheel_names_refused(self):
        for index, name in enumerate(['../escape', '/escape', 'a/../escape', 'a\\escape',
                                      'a//escape', 'C:/escape', './escape', 'a\x00escape']):
            with self.subTest(name=name):
                path = self.wheel(self.folder(str(index)), extra=[(name, b'synthetic')])
                with self.assertRaisesRegex(ValueError, 'unsafe'):
                    subject.normalise_wheelhouse(path.parent, self.lock(path))

    def test_duplicate_link_and_file_directory_conflicts_refused(self):
        link = zipfile.ZipInfo('link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        cases = [[('same', b'a'), ('same', b'b')], [(link, b'target')],
                 [('parent', b'a'), ('parent/child', b'b')]]
        for index, extra in enumerate(cases):
            with self.subTest(index=index):
                # zipfile's duplicate warning is expected for this hostile fixture.
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', UserWarning)
                    path = self.wheel(self.folder(str(index)), extra=extra)
                with self.assertRaises(ValueError):
                    subject.normalise_wheelhouse(path.parent, self.lock(path))

    def test_archive_member_and_metadata_limits(self):
        path = self.wheel(self.folder())
        for setting, value in [('MAX_MEMBERS', 2), ('MAX_MEMBER_BYTES', 20),
                               ('MAX_TOTAL_BYTES', 20), ('MAX_METADATA_BYTES', 20)]:
            with self.subTest(setting=setting), patch.object(subject, setting, value), self.assertRaises(ValueError):
                subject.normalise_wheelhouse(path.parent, self.lock(path))

    def test_missing_or_traversing_declared_licence_refused(self):
        for index, name in enumerate(['missing.txt', '../LICENSE']):
            with self.subTest(name=name):
                path = self.wheel(self.folder(str(index)), metadata=self.metadata(headers='License-File: ' + name + '\n'))
                with self.assertRaises(ValueError):
                    subject.normalise_wheelhouse(path.parent, self.lock(path))

    def test_corrupt_archive_and_nonfile_wheelhouse_entries(self):
        folder = self.folder()
        path = folder / 'example_pkg-1.2-py3-none-any.whl'
        path.write_bytes(b'not a zip file')
        with self.assertRaises(ValueError):
            subject.normalise_wheelhouse(folder, self.lock(path))
        (folder / 'nested').mkdir()
        with self.assertRaises(ValueError):
            subject.normalise_wheelhouse(folder, self.lock(path))

    def test_wheelhouse_link_refused_when_supported(self):
        path = self.wheel(self.folder())
        linked = self.root / 'linked'
        try:
            linked.symlink_to(path.parent, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest('host cannot create synthetic directory symlink: ' + str(exc))
        with self.assertRaises(ValueError):
            subject.normalise_wheelhouse(linked, self.lock(path))

    def test_usage_must_be_explicit_supported_category(self):
        path = self.wheel(self.folder())
        for usage in ('installed', '', None):
            with self.subTest(usage=usage), self.assertRaises(ValueError):
                subject.normalise_wheelhouse(path.parent, self.lock(path), usage=usage)

    def test_synthetic_sdist_cannot_bypass_existing_permitted_hash(self):
        path = self.sdist(self.folder())
        actual_lock = 'jieba==0.42.1 --hash=sha256:' + sha(path)
        allowance = subject.build_distributions.SDIST_ALLOWED[('jieba', '0.42.1')]
        permitted_lock = 'jieba==0.42.1 --hash=sha256:' + allowance
        for lock in (actual_lock, permitted_lock):
            with self.subTest(lock=lock), self.assertRaises(ValueError):
                subject.normalise_wheelhouse(path.parent, lock, usage='source')

    def test_synthetic_tar_parser_hashes_metadata_without_extraction(self):
        path = self.sdist(self.folder())
        # The parser is checked independently; normalisation above never accepts
        # this generated archive as the lead-permitted upstream distribution.
        with (patch.object(tarfile.TarFile, 'extract', side_effect=AssertionError('extraction forbidden')),
              patch.object(tarfile.TarFile, 'extractall', side_effect=AssertionError('extraction forbidden'))):
            row = subject._sdist_metadata(path)
        self.assertEqual(row['name'], 'jieba')
        self.assertEqual(row['version'], '0.42.1')
        self.assertEqual(row['metadata_files'][0]['path'], 'jieba-0.42.1/PKG-INFO')
        self.assertEqual(row['licence_files'][0]['path'], 'jieba-0.42.1/LICENSE')

    def test_synthetic_tar_links_traversal_and_duplicate_members_refused(self):
        link = tarfile.TarInfo('jieba-0.42.1/link')
        link.type = tarfile.SYMTYPE
        link.linkname = '../../escape'
        valid = ('jieba-0.42.1/PKG-INFO', self.metadata('jieba', '0.42.1'))
        for index, extra in enumerate([[(link, b'')], [('../escape', b'bad')], [valid],
                                      [('another/PKG-INFO', self.metadata())]]):
            with self.subTest(index=index):
                path = self.sdist(self.folder(str(index)), [valid, *extra])
                with self.assertRaises(ValueError):
                    subject._sdist_metadata(path)

    def test_zip_reader_never_extracts(self):
        path = self.wheel(self.folder())
        with (patch.object(zipfile.ZipFile, 'extract', side_effect=AssertionError('extraction forbidden')),
              patch.object(zipfile.ZipFile, 'extractall', side_effect=AssertionError('extraction forbidden'))):
            subject.normalise_wheelhouse(path.parent, self.lock(path))


if __name__ == '__main__':
    unittest.main()
