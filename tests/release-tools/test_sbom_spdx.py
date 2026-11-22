"""Synthetic SPDX-profile tests; no processes, network or input discovery."""
import copy
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src' / 'release-tools'))
from sbom.common import canonical
from sbom.spdx import emit_document, stable_id, validate_document


def measured(path, letter='a', root='media'):
    return dict(root=root, path=path, size=12, sha256=letter * 64)


def component(name, **changes):
    result = dict(id=name, kind='application', name=name, version='1.0', architecture='all',
        usage='runtime', files=[measured(name + '.py')], dependencies=[],
        licence_declared='MIT', licence_evidence=[measured('LICENCE.txt')],
        source_files=[], source_url='NOASSERTION', required_source=False, required_licence=False)
    result.update(changes)
    return result


def fixture():
    return dict(format='deep32-sbom-normalised/1', release='0.1.0-alpha.3',
        source_date_epoch=0, source_revision='1' * 40, generator_sha256='2' * 64,
        roots=[dict(id='media', files=[measured('disc.iso')])],
        subjects=[dict(id='disc1', kind='disc', file=measured('disc.iso'), role='workstation',
                       aliases=['desktop'], components=['app'], component_coverage='complete',
                       licence_coverage='complete')],
        components=[component('app')], gaps=[], absent_roles=['ingestion'], generated_from=[], wheelhouses=[])


def root_package(document):
    return next(row for row in document['packages'] if row['SPDXID'] == document['documentDescribes'][0])


def package(document, name):
    return next(row for row in document['packages'] if row['name'] == name)


class Spdx(unittest.TestCase):
    def setUp(self):
        self.inputs = fixture()

    def emit(self, **kwargs):
        return emit_document(self.inputs, self.inputs['subjects'][0], **kwargs)

    def edges(self, document):
        return {(row['spdxElementId'], row['relationshipType'], row['relatedSpdxElement'])
                for row in document['relationships']}

    def test_final_header_measurement_and_profile(self):
        doc = self.emit()
        validate_document(doc)
        self.assertEqual(doc['creationInfo'], dict(creators=['Tool: deep32-sbom-1'], created='1970-01-01T00:00:00Z'))
        self.assertEqual(root_package(doc)['checksums'], [dict(algorithm='SHA256', checksumValue='a' * 64)])
        self.assertEqual(doc['dataLicense'], 'CC0-1.0')
        self.assertTrue(all(row['filesAnalyzed'] is False for row in doc['packages']))
        self.assertTrue(all('packageVerificationCode' not in row for row in doc['packages']))

    def test_contents_ignores_final_archive_and_root_inventory(self):
        first = canonical(self.emit(kind='contents'))
        self.inputs['subjects'][0]['file'].update(size=999, sha256='b' * 64)
        self.inputs['roots'][0]['files'].append(measured('unrelated.txt', 'c'))
        self.assertEqual(first, canonical(self.emit(kind='contents')))
        self.assertNotIn('checksums', root_package(self.emit(kind='contents')))

    def test_prefreeze_unmeasured_subject_matches_final_contents(self):
        first = canonical(self.emit(kind='contents'))
        self.inputs['subjects'][0]['file'] = dict(root='media', path='disc.iso')
        self.assertEqual(first, canonical(self.emit(kind='contents')))
        with self.assertRaises(ValueError):
            self.emit(kind='final')

    def test_contents_public_subject_path_participates_in_namespace(self):
        first = self.emit(kind='contents')
        self.inputs['subjects'][0]['file']['path'] = 'disc-new.iso'
        second = self.emit(kind='contents')
        self.assertNotEqual(root_package(first)['name'], root_package(second)['name'])
        self.assertNotEqual(first['documentNamespace'], second['documentNamespace'])

    def test_final_changes_when_archive_changes(self):
        first = self.emit()
        self.inputs['subjects'][0]['file']['sha256'] = 'b' * 64
        second = self.emit()
        self.assertNotEqual(first['documentNamespace'], second['documentNamespace'])
        self.assertNotEqual(first['documentDescribes'], second['documentDescribes'])

    def test_order_clock_environment_does_not_enter_output(self):
        self.inputs['components'].append(component('other'))
        self.inputs['subjects'][0]['components'].append('other')
        first = canonical(self.emit())
        self.inputs['components'].reverse()
        self.inputs['subjects'][0]['components'].reverse()
        self.inputs['roots'].reverse()
        with patch('time.time', side_effect=AssertionError('no wall clock')):
            second = canonical(self.emit())
        self.assertEqual(first, second)

    def test_stable_ids_use_full_digest_and_canonical_keys(self):
        self.assertEqual(stable_id('file', dict(a=1, b=2)), stable_id('file', dict(b=2, a=1)))
        self.assertEqual(len(stable_id('file', {})), len('SPDXRef-file-') + 64)
        self.assertNotEqual(stable_id('file', {}), stable_id('package', {}))
        with self.assertRaises(ValueError):
            stable_id('unsafe kind', {})
        with self.assertRaises(ValueError):
            stable_id('file', {'value': float('nan')})

    def test_unknown_licence_and_evidence_only_is_not_containment(self):
        self.inputs['components'][0]['licence_declared'] = 'UNKNOWN'
        doc = self.emit()
        self.assertEqual(package(doc, 'app')['licenseDeclared'], 'NOASSERTION')
        app = package(doc, 'app')['SPDXID']
        licence = next(row['SPDXID'] for row in doc['files'] if row['fileName'].endswith('LICENCE.txt'))
        self.assertNotIn((app, 'CONTAINS', licence), self.edges(doc))
        self.assertIn(licence, package(doc, 'app')['sourceInfo'])

    def test_unbacked_extracted_licence_refused(self):
        self.inputs['components'][0]['licence_declared'] = 'LicenseRef-unmeasured'
        with self.assertRaisesRegex(ValueError, 'extracted licence'):
            self.emit()

    def test_licence_expression_structure(self):
        self.inputs['components'][0]['licence_declared'] = '(MIT OR Apache-2.0) AND BSD-3-Clause'
        validate_document(self.emit())
        for expression in ['MIT OR', 'MIT Apache-2.0', '(MIT', 'MIT AND )']:
            self.inputs['components'][0]['licence_declared'] = expression
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                self.emit()

    def test_declared_dependencies_only_and_build_direction(self):
        self.inputs['components'] += [component('tool', kind='tool', usage='build-only'), component('other')]
        self.inputs['subjects'][0]['components'] += ['tool', 'other']
        self.inputs['components'][0]['dependencies'] = ['tool']
        doc = self.emit()
        app, tool, other = [package(doc, name)['SPDXID'] for name in ['app', 'tool', 'other']]
        self.assertIn((tool, 'BUILD_DEPENDENCY_OF', app), self.edges(doc))
        self.assertNotIn((app, 'DEPENDS_ON', other), self.edges(doc))
        self.assertNotIn((doc['documentDescribes'][0], 'CONTAINS', tool), self.edges(doc))

    def test_dependency_outside_subject_is_described_without_containment(self):
        self.inputs['components'].append(component('dependency'))
        self.inputs['components'][0]['dependencies'] = ['dependency']
        doc = self.emit()
        dependency = package(doc, 'dependency')['SPDXID']
        self.assertIn((package(doc, 'app')['SPDXID'], 'DEPENDS_ON', dependency), self.edges(doc))
        self.assertNotIn((doc['documentDescribes'][0], 'CONTAINS', dependency), self.edges(doc))

    def test_mounted_model_is_not_contained_in_saved_image(self):
        self.inputs['subjects'][0]['kind'] = 'container'
        self.inputs['subjects'][0]['image'] = dict(binding=dict(config_digest='sha256:' + 'f' * 64))
        self.inputs['components'][0].update(kind='model', usage='external-model', revision='e' * 40)
        doc = self.emit()
        self.assertNotIn((doc['documentDescribes'][0], 'CONTAINS', package(doc, 'app')['SPDXID']), self.edges(doc))
        self.assertIn('sha256:' + 'f' * 64, root_package(doc)['sourceInfo'])
        self.assertEqual(root_package(doc)['checksums'][0]['checksumValue'], 'a' * 64)

    def test_serialised_image_provenance_participates_in_namespace(self):
        subject = self.inputs['subjects'][0]
        subject['kind'] = 'container'
        subject['image'] = dict(binding=dict(config_digest='sha256:' + 'f' * 64),
                               inventory=measured('inventory.json', 'b'))
        first = self.emit()
        first_contents = canonical(self.emit(kind='contents'))
        subject['image']['inventory']['sha256'] = 'c' * 64
        second = self.emit()
        self.assertNotEqual(root_package(first)['sourceInfo'], root_package(second)['sourceInfo'])
        self.assertNotEqual(first['documentNamespace'], second['documentNamespace'])
        # Contents emission does not serialise this final archive provenance.
        self.assertEqual(first_contents, canonical(self.emit(kind='contents')))

    def test_generated_from_is_not_runtime_dependency(self):
        self.inputs['components'] += [component('source', kind='sdist', usage='source')]
        self.inputs['generated_from'] = [dict(result='app', source='source', evidence=measured('build.json'))]
        doc = self.emit()
        self.assertIn((package(doc, 'app')['SPDXID'], 'GENERATED_FROM', package(doc, 'source')['SPDXID']), self.edges(doc))
        self.assertNotIn((package(doc, 'app')['SPDXID'], 'DEPENDS_ON', package(doc, 'source')['SPDXID']), self.edges(doc))

    def test_generated_metadata_excluded_and_own_subject_rejected(self):
        self.inputs['components'][0]['files'] += [measured('sbom/disc1.contents.spdx.json'), measured('SHA256SUMS'), measured('PAYLOAD.json')]
        doc = self.emit(kind='contents')
        self.assertFalse(any(row['fileName'].endswith(('.spdx.json', 'SHA256SUMS', 'PAYLOAD.json')) for row in doc['files']))
        self.inputs['components'][0]['files'].append(copy.deepcopy(self.inputs['subjects'][0]['file']))
        with self.assertRaisesRegex(ValueError, 'own subject'):
            self.emit(kind='contents')

    def test_runtime_contents_ignores_own_archive_and_metadata(self):
        subject = self.inputs['subjects'][0]
        subject['kind'] = 'runtime'
        subject['runtime'] = dict(members=[dict(path='deep32/kit/run.py', size=1, sha256='e' * 64, mode=0o755)])
        first = self.emit(kind='contents')
        subject['runtime'].update(size=777, sha256='f' * 64)
        subject['runtime']['members'].append(dict(path='deep32/kit/sbom/runtime.contents.spdx.json', size=123, sha256='b' * 64))
        self.assertEqual(canonical(first), canonical(self.emit(kind='contents')))
        subject['runtime']['members'][0]['sha256'] = 'd' * 64
        self.assertNotEqual(first['documentNamespace'], self.emit(kind='contents')['documentNamespace'])

    def test_runtime_and_source_file_name_collision_refused(self):
        subject = self.inputs['subjects'][0]
        subject['kind'] = 'runtime'
        subject['runtime'] = dict(members=[dict(path='deep32/kit/run.py', size=12, sha256='a' * 64, mode=0o755)])
        self.inputs['components'][0]['files'] = [measured('kit/run.py', root='deep32')]
        with self.assertRaisesRegex(ValueError, 'duplicate SPDX file name'):
            self.emit(kind='contents')

    def test_validator_duplicate_file_names_refused(self):
        doc = self.emit()
        other = copy.deepcopy(doc['files'][0])
        other['SPDXID'] = stable_id('file', {'different': 'identity'})
        doc['files'].append(other)
        with self.assertRaisesRegex(ValueError, 'duplicate SPDX file name'):
            validate_document(doc)

    def test_same_subject_contents_external_binding(self):
        contents = self.emit(kind='contents')
        checksum = hashlib.sha256(canonical(contents).encode('ascii')).hexdigest()
        final = self.emit(child_documents=[dict(subject=self.inputs['subjects'][0], document=contents, sha256=checksum)])
        validate_document(final, {contents['documentNamespace']: contents})
        self.assertTrue(any(row['relationshipType'] == 'DESCRIBED_BY' for row in final['relationships']))
        with self.assertRaisesRegex(ValueError, 'unresolved external'):
            validate_document(final)
        tampered = copy.deepcopy(contents)
        tampered['name'] += '-changed'
        with self.assertRaisesRegex(ValueError, 'SHA-256 differs'):
            validate_document(final, {contents['documentNamespace']: tampered})

    def test_external_child_corrupt_hash_refused(self):
        contents = self.emit(kind='contents')
        with self.assertRaisesRegex(ValueError, 'SHA-256 differs'):
            self.emit(child_documents=[dict(subject=self.inputs['subjects'][0], document=contents, sha256='0' * 64)])

    def test_unresolved_relationship_and_duplicate_ids_refused(self):
        doc = self.emit()
        doc['relationships'].append(dict(spdxElementId=doc['documentDescribes'][0], relationshipType='DEPENDS_ON', relatedSpdxElement='SPDXRef-missing'))
        with self.assertRaisesRegex(ValueError, 'unresolved local'):
            validate_document(doc)
        doc = self.emit()
        doc['packages'].append(copy.deepcopy(doc['packages'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            validate_document(doc)

    def test_cyclic_contains_and_fake_package_analysis_refused(self):
        doc = self.emit()
        doc['relationships'].append(dict(spdxElementId=package(doc, 'app')['SPDXID'], relationshipType='CONTAINS', relatedSpdxElement=doc['documentDescribes'][0]))
        with self.assertRaisesRegex(ValueError, 'cyclic'):
            validate_document(doc)
        doc = self.emit()
        doc['packages'][0]['filesAnalyzed'] = True
        with self.assertRaisesRegex(ValueError, 'analysis'):
            validate_document(doc)

    def test_unsafe_paths_and_missing_required_identity_refused(self):
        self.inputs['components'][0]['files'][0]['path'] = '../private.py'
        with self.assertRaises(ValueError):
            self.emit()
        self.inputs = fixture()
        self.inputs['components'][0]['version'] = 'NOASSERTION'
        with self.assertRaisesRegex(ValueError, 'resolved'):
            self.emit()

    def test_unsupported_epoch_and_subject_refused(self):
        for epoch in [True, -1, 10 ** 30]:
            self.inputs['source_date_epoch'] = epoch
            with self.subTest(epoch=epoch), self.assertRaises(ValueError):
                self.emit()
        self.inputs = fixture()
        wrong = copy.deepcopy(self.inputs['subjects'][0])
        wrong['id'] = 'absent'
        with self.assertRaisesRegex(ValueError, 'absent'):
            emit_document(self.inputs, wrong)


if __name__ == '__main__':
    unittest.main(verbosity=2)
