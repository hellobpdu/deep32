"""Synthetic sealed-input tests. All temporary files use the suite scratch root."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src' / 'release-tools'))
from sbom.common import canonical, digest, load_json, public_url
from sbom.inputs import normalise_inputs


class Inputs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sbom-input-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'inputs'
        self.root.mkdir()
        for name, content in [('disc.iso', b'synthetic disc'), ('source.py', b'pass\n'), ('LICENCE.txt', b'synthetic licence')]:
            (self.root / name).write_bytes(content)
        self.manifest_path = self.base / 'INPUT.json'
        self.m = dict(format='deep32-sbom-input/1', release='0.1.0-alpha.2', source_date_epoch=0,
                      source_revision='1' * 40, generator_sha256='2' * 64,
                      roots=[dict(id='media', files=[])],
                      components=[dict(id='app', kind='application', name='demo', version='1.0', architecture='all', usage='runtime',
                                       files=[dict(root='media', path='source.py')], dependencies=[], licence_declared='Apache-2.0',
                                       licence_evidence=[dict(root='media', path='LICENCE.txt')])],
                      subjects=[dict(id='disc1', kind='disc', file=dict(root='media', path='disc.iso'), role='workstation', aliases=['desktop'],
                                     components=['app'], component_coverage='complete', licence_coverage='complete')],
                      gaps=dict(format='deep32-sbom-gaps/1', gaps=[]), absent_roles=['ingestion'])
        self.seal()

    def seal(self):
        self.m['roots'][0]['files'] = [dict(path=p.relative_to(self.root).as_posix(), size=p.stat().st_size, sha256=digest(p)) for p in sorted(self.root.rglob('*')) if p.is_file()]

    def run_input(self):
        self.manifest_path.write_text(canonical(self.m), encoding='ascii')
        return normalise_inputs(self.manifest_path, {'media': self.root})

    def bad(self, message=None):
        with self.assertRaisesRegex(ValueError, message or '.'):
            self.run_input()

    def test_valid_read_only_and_no_operator_paths(self):
        before = {p: p.read_bytes() for p in self.root.iterdir()}
        with patch('subprocess.run', side_effect=AssertionError('no process')):
            output = canonical(self.run_input())
        self.assertNotIn(str(self.base), output)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.iterdir()})
        self.assertEqual(self.run_input()['absent_roles'], ['ingestion'])

    def test_reordered_inputs_and_relocated_root_identical(self):
        first = canonical(self.run_input())
        self.m['roots'][0]['files'].reverse()
        second = canonical(self.run_input())
        import shutil
        shutil.copytree(self.root, self.base / 'relocated')
        self.assertEqual(first, second)
        self.assertEqual(first, canonical(normalise_inputs(self.manifest_path, {'media': self.base / 'relocated'})))

    def test_unknown_licence_aliases_need_explicit_partial_gap(self):
        for alias in ('UNKNOWN', 'unknown', 'NONE'):
            with self.subTest(alias=alias):
                self.m['components'][0]['licence_declared'] = alias
                self.m['gaps']['gaps'] = []
                self.m['subjects'][0]['licence_coverage'] = 'complete'
                self.bad('licence evidence and gap record differ')
                self.m['gaps']['gaps'] = [dict(id='unknown-app', target='app', field='licence', reason='Declaration not established')]
                self.m['subjects'][0]['licence_coverage'] = 'partial'
                self.assertEqual(self.run_input()['components'][0]['licence_declared'], 'NOASSERTION')

    def test_prefreeze_runtime_requires_measured_sources_and_modes(self):
        source = self.root / 'source.py'
        member = dict(path='deep32/kit/source.py', size=source.stat().st_size, sha256=digest(source), mode=0o644)
        self.m['subjects'].append(dict(id='runtime', kind='runtime', file=dict(root='media', path='runtime.tar'),
            role='docker-kit', aliases=[], components=['app'], component_coverage='complete', licence_coverage='complete', runtime_members=[member]))
        def prefreeze():
            self.manifest_path.write_text(canonical(self.m), encoding='ascii')
            return normalise_inputs(self.manifest_path, {'media': self.root}, contents=True)
        normal = prefreeze()
        runtime = next(row for row in normal['subjects'] if row['id'] == 'runtime')
        self.assertEqual(runtime['file'], dict(root='media', path='runtime.tar'))
        member.pop('mode')
        with self.assertRaisesRegex(ValueError, 'modes and measured source'):
            prefreeze()
        member['mode'] = 0o644
        member['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'modes and measured source'):
            prefreeze()

    def test_corrupt_hash(self):
        (self.root / 'disc.iso').write_bytes(b'changed')
        self.bad('differs')

    def test_extra_file(self):
        (self.root / 'extra').write_bytes(b'opaque')
        self.bad('unexpected')

    def test_missing_file(self):
        (self.root / 'disc.iso').unlink()
        with self.assertRaises((ValueError, OSError)):
            self.run_input()

    def test_duplicate_file(self):
        self.m['roots'][0]['files'].append(self.m['roots'][0]['files'][0])
        self.bad('duplicate')

    def test_unknown_fields_and_version(self):
        for field in ['format', 'unrecognised']:
            with self.subTest(field=field):
                original = copy.deepcopy(self.m)
                self.m[field] = 'future'
                self.bad()
                self.m = original

    def test_duplicate_json_keys(self):
        self.manifest_path.write_text('{"format":1,"format":2}')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            load_json(self.manifest_path)

    def test_nonfinite_and_boolean_epoch(self):
        for text in ['{"x":NaN}', '{"x":Infinity}', '{"x":1e999}']:
            self.manifest_path.write_text(text)
            with self.assertRaises(ValueError):
                load_json(self.manifest_path)
        self.m['source_date_epoch'] = True
        self.bad()

    def test_unsafe_paths(self):
        for path in ['../disc.iso', '/disc.iso', 'C:/disc.iso', 'a\\b', 'a//b', 'secrets/token', 'priv-model/data', 'q1234.json']:
            with self.subTest(path=path):
                self.m['subjects'][0]['file']['path'] = path
                self.bad()

    def test_unknown_checksum(self):
        self.m['roots'][0]['files'][0]['sha256'] = 'sha512:' + '1' * 128
        self.bad('SHA-256')

    def test_reparse_refused(self):
        import stat
        from sbom.common import ordinary
        original = Path.lstat
        def lstat(path):
            if path == self.root / 'disc.iso':
                class Fake:
                    st_mode = stat.S_IFREG | 0o644
                    st_file_attributes = 0x400
                return Fake()
            return original(path)
        with patch.object(Path, 'lstat', lstat):
            with self.assertRaisesRegex(ValueError, 'reparse'):
                ordinary(self.root / 'disc.iso')

    def test_alias_and_optional_role_conflicts(self):
        self.m['subjects'][0]['aliases'] = ['ingestion']
        self.bad('absent')
        self.m['subjects'][0]['aliases'] = ['disc1']
        self.bad('alias')

    def test_duplicate_subject_and_component(self):
        self.m['subjects'].append(copy.deepcopy(self.m['subjects'][0]))
        self.bad('duplicate')
        self.m['subjects'].pop()
        self.m['components'].append(copy.deepcopy(self.m['components'][0]))
        self.bad('duplicate')

    def test_declared_dependencies_only(self):
        other = copy.deepcopy(self.m['components'][0])
        other.update(id='build-tool', name='builder', usage='build-only', kind='tool', dependencies=['app'])
        self.m['components'].append(other)
        self.m['subjects'][0]['components'].append('build-tool')
        rows = self.run_input()['components']
        self.assertEqual(rows[0]['dependencies'], [])
        self.assertEqual(rows[1]['usage'], 'build-only')
        self.assertEqual(rows[1]['dependencies'], ['app'])
        other['dependencies'] = ['missing']
        self.bad('unresolved')

    def test_unknown_licence_requires_gap(self):
        self.m['components'][0].pop('licence_declared')
        self.m['components'][0].pop('licence_evidence')
        self.bad('gap')
        self.m['gaps']['gaps'] = [dict(id='licence-gap', target='app', field='licence', reason='Declaration not recorded')]
        self.m['subjects'][0]['licence_coverage'] = 'partial'
        self.assertEqual(self.run_input()['components'][0]['licence_declared'], 'NOASSERTION')

    def test_mandatory_licence_source_refused(self):
        self.m['components'][0]['required_source'] = True
        self.bad('mandatory source')
        self.m['components'][0].pop('required_source')
        self.m['components'][0]['licence_evidence'] = []
        self.m['components'][0]['required_licence'] = True
        self.bad('mandatory licence')

    def test_partial_inventory_needs_sealed_approval(self):
        self.m['subjects'][0]['component_coverage'] = 'partial'
        self.m['gaps']['gaps'] = [dict(id='coverage', target='disc1', field='components', reason='Only set archive measured')]
        self.bad('approval')
        self.m['gaps']['gaps'][0]['approval'] = dict(root='media', path='LICENCE.txt')
        self.assertEqual(self.run_input()['subjects'][0]['component_coverage'], 'partial')

    def test_unattached_component(self):
        self.m['subjects'][0]['components'] = []
        self.bad('components')

    def test_missing_image_inventory(self):
        self.m['subjects'][0]['kind'] = 'container'
        self.bad('sealed image')

    def test_missing_runtime_members(self):
        self.m['subjects'][0]['kind'] = 'runtime'
        self.bad('runtime member')

    def test_model_requires_revision_and_entire_snapshot(self):
        self.m['components'][0].update(kind='model', usage='external-model')
        self.bad('revision')
        self.m['components'][0]['revision'] = 'a' * 40
        self.bad('complete dedicated')

    def test_private_metadata(self):
        self.m['components'][0]['licence_declared'] = 'owner@example.test'
        self.bad('public metadata')
        for url in ['http://example.test/x', 'https://user:pass@example.test/x', 'https://192.168.1.1/x', 'https://host.ts.net/x', 'https://example.test/x?token=x']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_url(url)

    def test_selected_wheel_hash_and_alternative_evidence(self):
        folder = self.root / 'wheels'
        folder.mkdir()
        wheel = folder / 'demo-1.0-py3-none-any.whl'
        with zipfile.ZipFile(wheel, 'w') as archive:
            archive.writestr('demo-1.0.dist-info/METADATA', 'Metadata-Version: 2.3\nName: demo\nVersion: 1.0\nLicense-Expression: MIT\n\n')
            archive.writestr('demo-1.0.dist-info/WHEEL', 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n')
        actual = digest(wheel)
        (self.root / 'selected.lock').write_text('demo==1.0 --hash=sha256:' + actual + ' --hash=sha256:' + 'f' * 64 + '\n')
        component = self.m['components'][0]
        component.update(kind='wheel', licence_declared='MIT', files=[dict(root='media', path='wheels/' + wheel.name)])
        self.m['wheelhouses'] = [dict(root='media', path='wheels', lock=dict(root='media', path='selected.lock'), usage='runtime', components=['app'])]
        self.seal()
        selected = self.run_input()['wheelhouses'][0]['distributions'][0]
        self.assertEqual(selected['sha256'], actual)
        self.assertEqual(set(selected['allowed_hashes']), {actual, 'f' * 64})
        component['licence_declared'] = 'Apache-2.0'
        self.bad('licence differs')
        component['licence_declared'] = 'MIT'
        component['files'].append(dict(root='media', path='source.py'))
        self.bad('archive and component')
        component['files'].pop()
        self.m.pop('wheelhouses')
        self.bad('selected-lock')

    def test_unresolved_required_identity(self):
        for field in ['version', 'architecture', 'name']:
            with self.subTest(field=field):
                previous = self.m['components'][0][field]
                self.m['components'][0][field] = 'NOASSERTION'
                self.bad('resolved')
                self.m['components'][0][field] = previous

    def test_wrong_enum_types_refuse_cleanly(self):
        for field in ['kind', 'usage']:
            previous = self.m['components'][0][field]
            self.m['components'][0][field] = []
            self.bad()
            self.m['components'][0][field] = previous
        self.m['subjects'][0]['kind'] = {}
        self.bad()

    def _shared_images(self):
        base = copy.deepcopy(self.m['components'][0])
        base.update(id='base', kind='base-package', name='base', files=[])
        self.m['components'] = []
        self.m['subjects'][0]['components'] = ['base']
        inventory = dict(format='deep32-sbom-image-inventory/1', config_digest='sha256:' + '3' * 64, coverage='complete', components=[base])
        (self.root / 'inventory.json').write_text(canonical(inventory))
        for number in [1, 2]:
            filename = 'image' + str(number) + '.tar'
            (self.root / filename).write_bytes(('synthetic saved image ' + str(number)).encode())
            self.m['subjects'].append(dict(id='image' + str(number), kind='container', file=dict(root='media', path=filename), role='role' + str(number), aliases=[],
                                          components=[], component_coverage='complete', licence_coverage='complete', image=dict(binding={}, inventory=dict(root='media', path='inventory.json'))))
        self.seal()
        def measured(path, binding, supplied):
            return dict(size=path.stat().st_size, sha256=digest(path), binding=dict(config_digest=supplied['config_digest']), components=supplied['components'], coverage='complete')
        return measured

    def test_shared_base_components_and_subject_order(self):
        measured = self._shared_images()
        with patch('sbom.archives.normalise_image', side_effect=measured):
            first = canonical(self.run_input())
            self.m['subjects'].reverse()
            self.assertEqual(first, canonical(self.run_input()))
            result = self.run_input()
        self.assertEqual(len(result['components']), 1)
        self.assertTrue(all('base' in row['components'] for row in result['subjects']))
        self.assertEqual(result['subjects'][1]['image']['inventory']['path'], 'inventory.json')

    def test_component_cannot_reuse_subject_identity(self):
        measured = self._shared_images()
        self.m['subjects'][0]['id'] = 'base'
        with patch('sbom.archives.normalise_image', side_effect=measured):
            self.bad('disjoint')

    def test_real_synthetic_image_inventory_end_to_end(self):
        import io
        import tarfile
        config = b'{"architecture":"amd64","os":"linux","rootfs":{"type":"layers","diff_ids":[]}}'
        checksum = hashlib.sha256(config).hexdigest()
        rows = [(checksum + '.json', config), ('manifest.json', json.dumps([dict(Config=checksum + '.json', Layers=[], RepoTags=['synthetic:test'])]).encode())]
        with tarfile.open(self.root / 'image.tar', 'w') as archive:
            for name, data in rows:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        base = copy.deepcopy(self.m['components'][0])
        base.update(id='base', kind='base-package', name='inherited-base', files=[])
        inventory = dict(format='deep32-sbom-image-inventory/1', config_digest='sha256:' + checksum, coverage='complete', components=[base])
        (self.root / 'inventory.json').write_text(canonical(inventory))
        self.m['components'] = []
        self.m['subjects'][0].update(kind='container', file=dict(root='media', path='image.tar'), components=[],
                                    image=dict(binding=dict(image_id='sha256:' + checksum, config_digest='sha256:' + checksum, index_digest=None, manifest_digest=None, archive_format='classic'),
                                               inventory=dict(root='media', path='inventory.json')))
        self.seal()
        output = self.run_input()
        self.assertEqual(output['components'][0]['name'], 'inherited-base')
        measured = output['subjects'][0]['image']
        self.assertEqual(measured['binding']['config_digest'], 'sha256:' + checksum)
        self.assertEqual(measured['sha256'], digest(self.root / 'image.tar'))
        self.assertNotEqual(measured['sha256'], checksum)

    def test_real_synthetic_runtime_end_to_end(self):
        import io
        import tarfile
        content = b'pass\n'
        with tarfile.open(self.root / 'runtime.tar', 'w') as archive:
            info = tarfile.TarInfo('deep32/kit/source.py')
            info.size = len(content)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(content))
        self.m['subjects'][0].update(kind='runtime', file=dict(root='media', path='runtime.tar'), runtime_members=[dict(path='deep32/kit/source.py', size=len(content), sha256=hashlib.sha256(content).hexdigest(), mode=0o755)])
        self.seal()
        self.assertEqual(self.run_input()['subjects'][0]['runtime']['members'][0]['mode'], 0o755)

    def test_complete_external_model_snapshot(self):
        snapshot = self.base / 'snapshot'
        snapshot.mkdir()
        (snapshot / 'model.bin').write_bytes(b'synthetic model')
        (snapshot / 'LICENCE.txt').write_bytes(b'synthetic terms')
        self.m['roots'].append(dict(id='snapshot', files=[dict(path=p.name, size=p.stat().st_size, sha256=digest(p)) for p in sorted(snapshot.iterdir())]))
        self.m['components'][0].update(kind='model', usage='external-model', revision='a' * 40,
                                      files=[dict(root='snapshot', path=p.name) for p in sorted(snapshot.iterdir())], licence_evidence=[dict(root='snapshot', path='LICENCE.txt')])
        self.manifest_path.write_text(canonical(self.m))
        output = normalise_inputs(self.manifest_path, {'media': self.root, 'snapshot': snapshot})
        self.assertEqual(output['components'][0]['usage'], 'external-model')
        self.assertEqual(len(output['components'][0]['files']), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
