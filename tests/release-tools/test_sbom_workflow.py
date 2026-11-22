"""Offline, synthetic contents/finalisation and exact coverage checks.

Temporary directories inherit the runner's task-local TEMP/TMP/TMPDIR.
These tests make no schema, signing, producer integration or rebuild claim.
"""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[2] / 'src' / 'release-tools'
RUNTIME_CONTENTS = 'deep32/kit/sbom/forge-docker.contents.spdx.json'
sys.path.insert(0, str(TOOLS))
from sbom.common import canonical, digest
from sbom.workflow import contents, finalise, generator_sha256, verify


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(path.read_bytes())


def snapshot(folder):
    return {path.name: path.read_bytes() for path in sorted(folder.iterdir())}


def write_tar(path, members):
    with tarfile.open(path, 'w', format=tarfile.USTAR_FORMAT) as archive:
        for name, data, mode in members:
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            entry.mode = mode
            entry.mtime = 0
            archive.addfile(entry, io.BytesIO(data))


class Workflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sbom-workflow-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'inputs'
        self.root.mkdir()
        self.roots = {'media': self.root}
        self.manifest_path = self.base / 'INPUT.json'
        self.contents_dir = self.base / 'contents'
        self.release_dir = self.base / 'release'
        source = b'pass\n'
        (self.root / 'source.py').write_bytes(source)
        (self.root / 'LICENCE.txt').write_bytes(b'Synthetic public licence evidence.\n')
        for name in ('disc1.img', 'disc2.iso', 'disc3.iso'):
            (self.root / name).write_bytes(('Synthetic frozen ' + name + '\n').encode('ascii'))
        write_tar(self.root / 'forge-docker.tar', [('deep32/kit/source.py', source, 0o755)])
        config = canonical(dict(architecture='amd64', os='linux', rootfs=dict(type='layers', diff_ids=[]))).encode('ascii')
        self.config_digest = checksum(config)
        config_name = self.config_digest + '.json'
        manifest = canonical([dict(Config=config_name, Layers=[], RepoTags=['synthetic:test'])]).encode('ascii')
        write_tar(self.root / 'door.tar', [(config_name, config, 0o644), ('manifest.json', manifest, 0o644)])
        app = dict(id='app', kind='application', name='demo', version='1.0', architecture='all', usage='runtime',
                   files=[dict(root='media', path='source.py')], dependencies=[], licence_declared='MIT',
                   licence_evidence=[dict(root='media', path='LICENCE.txt')])
        inherited = copy.deepcopy(app)
        inherited.update(id='base', kind='base-package', name='inherited-base', version='2.0', architecture='amd64', files=[])
        inventory = dict(format='deep32-sbom-image-inventory/1', config_digest='sha256:' + self.config_digest,
                         coverage='complete', components=[inherited])
        (self.root / 'inventory.json').write_text(canonical(inventory), encoding='ascii')
        subjects = [dict(id='disc' + str(number), kind='disc',
                         file=dict(root='media', path='disc' + str(number) + ('.img' if number == 1 else '.iso')),
                         role='disc-role-' + str(number), aliases=['desktop'] if number == 1 else [], components=['app'],
                         component_coverage='complete', licence_coverage='complete') for number in (1, 2, 3)]
        subjects.append(dict(id='forge-docker', kind='runtime', file=dict(root='media', path='forge-docker.tar'),
                             role='runtime', aliases=[], components=['app'], component_coverage='complete', licence_coverage='complete',
                             runtime_members=[dict(path='deep32/kit/source.py', size=len(source), sha256=checksum(source), mode=0o755)]))
        subjects.append(dict(id='saved-door', kind='container', file=dict(root='media', path='door.tar'), role='door',
                             aliases=['model-bridge', 'gateway'], components=[], component_coverage='complete', licence_coverage='complete',
                             image=dict(binding=dict(image_id='sha256:' + self.config_digest, config_digest='sha256:' + self.config_digest,
                                                     index_digest=None, manifest_digest=None, archive_format='classic'),
                                        inventory=dict(root='media', path='inventory.json'))))
        self.m = dict(format='deep32-sbom-input/1', release='0.1.0-alpha.3', source_date_epoch=946684800,
                      source_revision='1' * 40, generator_sha256=generator_sha256(), roots=[dict(id='media', files=[])],
                      components=[app], subjects=subjects, gaps=dict(format='deep32-sbom-gaps/1', gaps=[]),
                      absent_roles=['converter', 'builder'])
        self.seal()

    def seal(self):
        self.m['roots'][0]['files'] = [dict(path=path.relative_to(self.root).as_posix(), size=path.stat().st_size, sha256=digest(path))
                                     for path in sorted(self.root.rglob('*')) if path.is_file()]
        self.write_manifest()

    def write_manifest(self):
        self.manifest_path.write_text(canonical(self.m), encoding='ascii')

    def emit(self):
        before = snapshot(self.root)
        contents(self.manifest_path, self.roots, self.contents_dir)
        self.assertEqual(before, snapshot(self.root))
        self.freeze_runtime()
        before = snapshot(self.root)
        result = finalise(self.manifest_path, self.roots, self.contents_dir, self.release_dir)
        self.assertEqual(before, snapshot(self.root))
        return result

    def freeze_runtime(self, *, metadata=None, include_contents=True):
        """Producer fixture: embed the emitted contents bytes, then reseal input."""
        subject = next(row for row in self.m['subjects'] if row['kind'] == 'runtime')
        source = (self.root / 'source.py').read_bytes()
        members = [('deep32/kit/source.py', source, 0o755)]
        subject['runtime_members'] = [dict(path='deep32/kit/source.py', size=len(source), sha256=checksum(source), mode=0o755)]
        if include_contents:
            if metadata is None:
                metadata = (self.contents_dir / 'forge-docker.contents.spdx.json').read_bytes()
            members.append((RUNTIME_CONTENTS, metadata, 0o644))
            subject['runtime_members'].append(dict(path=RUNTIME_CONTENTS, size=len(metadata), sha256=checksum(metadata), mode=0o644))
        write_tar(self.root / subject['file']['path'], members)
        self.seal()

    def assert_binding(self, row, path):
        data = path.read_bytes()
        self.assertEqual(row, dict(path=path.name, size=len(data), sha256=checksum(data)))

    def assert_new_output_failure(self, call, out):
        with self.assertRaises((ValueError, OSError)):
            call()
        self.assertFalse(out.exists())
        self.assertFalse(any(path.name.startswith('.sbom-') for path in out.parent.iterdir()))

    def test_complete_five_subject_graph_and_flat_metadata_list(self):
        result = self.emit()
        self.assertEqual(result['subjects'], 5)
        self.assertEqual(verify(self.manifest_path, self.roots, self.release_dir)['subjects'], 5)
        expected_contents = {'disc1.contents.spdx.json', 'disc2.contents.spdx.json', 'disc3.contents.spdx.json',
                             'forge-docker.contents.spdx.json', 'image-saved-door.spdx.json'}
        self.assertEqual(set(snapshot(self.contents_dir)), expected_contents)
        expected = expected_contents | {'disc1.spdx.json', 'disc2.spdx.json', 'disc3.spdx.json', 'forge-docker.spdx.json',
                                        'SBOM-INDEX.json', 'GAPS.json', 'PROVENANCE.json', 'METADATA.json'}
        self.assertEqual(set(snapshot(self.release_dir)), expected)
        for name in expected_contents:
            self.assertEqual((self.contents_dir / name).read_bytes(), (self.release_dir / name).read_bytes())
        metadata = read_json(self.release_dir / 'METADATA.json')
        listed = {row['path']: row for row in metadata['files']}
        self.assertEqual(set(listed), expected - {'METADATA.json'})
        self.assertEqual(len(listed), len(metadata['files']))
        for name, row in listed.items():
            self.assertEqual(Path(name).name, name)
            self.assertNotIn('/', name)
            self.assertNotIn('\\', name)
            self.assert_binding(row, self.release_dir / name)
        self.assertEqual({row['path'] for row in result['files']}, expected)

    def test_index_exact_subjects_aliases_absent_roles_and_measured_archive_hash(self):
        self.emit()
        index = read_json(self.release_dir / 'SBOM-INDEX.json')
        rows = {row['id']: row for row in index['subjects']}
        self.assertEqual(set(rows), {'disc1', 'disc2', 'disc3', 'forge-docker', 'saved-door'})
        self.assertEqual(len(rows), len(index['subjects']))
        self.assertEqual(index['absent_roles'], ['builder', 'converter'])
        self.assertEqual(rows['saved-door']['aliases'], ['gateway', 'model-bridge'])
        self.assertEqual(rows['disc1']['aliases'], ['desktop'])
        self.assertEqual(rows['saved-door']['sbom']['path'], 'image-saved-door.spdx.json')
        self.assertEqual(rows['saved-door']['contents'], rows['saved-door']['sbom'])
        self.assertEqual(rows['saved-door']['artefact']['sha256'], digest(self.root / 'door.tar'))
        self.assertNotEqual(rows['saved-door']['artefact']['sha256'], self.config_digest)
        for row in rows.values():
            artefact = self.root / row['artefact']['path']
            self.assert_binding(row['artefact'], artefact)
            self.assert_binding(row['sbom'], self.release_dir / row['sbom']['path'])
            self.assert_binding(row['contents'], self.release_dir / row['contents']['path'])
            self.assert_binding(row['gaps'], self.release_dir / 'GAPS.json')
            self.assert_binding(row['provenance'], self.release_dir / 'PROVENANCE.json')
        provenance = read_json(self.release_dir / 'PROVENANCE.json')
        image = next(row for row in provenance['subjects'] if row['id'] == 'saved-door')['image']
        self.assertEqual(image['binding']['config_digest'], 'sha256:' + self.config_digest)

    def test_no_final_disc_or_runtime_hash_in_contents_and_external_refs_resolve(self):
        self.emit()
        documents = {name: json.loads(data) for name, data in snapshot(self.release_dir).items() if name.endswith('.spdx.json')}
        namespaces = {doc['documentNamespace']: name for name, doc in documents.items()}
        for subject in self.m['subjects']:
            if subject['kind'] == 'container':
                continue
            name = subject['id'] + '.contents.spdx.json'
            data = (self.release_dir / name).read_bytes()
            self.assertNotIn(digest(self.root / subject['file']['path']).encode('ascii'), data)
            self.assertNotIn(checksum(data).encode('ascii'), data)
            root_id = documents[name]['documentDescribes'][0]
            root = next(row for row in documents[name]['packages'] if row['SPDXID'] == root_id)
            self.assertFalse(root.get('checksums'))
            wrapper = documents[subject['id'] + '.spdx.json']
            refs = wrapper['externalDocumentRefs']
            self.assertEqual(len(refs), 1)
            self.assertEqual(refs[0]['spdxDocument'], documents[name]['documentNamespace'])
        for document in documents.values():
            self.assertEqual(document['creationInfo']['created'], '2000-01-01T00:00:00Z')
            for ref in document.get('externalDocumentRefs', []):
                target = self.release_dir / namespaces[ref['spdxDocument']]
                self.assertEqual(ref['checksum'], dict(algorithm='SHA256', checksumValue=digest(target)))

    def test_path_order_mtime_environment_and_wall_clock_do_not_change_bytes(self):
        self.emit()
        first_contents, first_release = snapshot(self.contents_dir), snapshot(self.release_dir)
        relocated = self.base / 'relocated-inputs'
        shutil.copytree(self.root, relocated)
        for path in relocated.iterdir():
            os.utime(path, (123456, 123456))
        reordered = copy.deepcopy(self.m)
        reordered['roots'][0]['files'].reverse()
        reordered['subjects'].reverse()
        reordered['absent_roles'].reverse()
        for subject in reordered['subjects']:
            subject['aliases'].reverse()
        other_manifest = self.base / 'RELOCATED.json'
        other_manifest.write_text(canonical(reordered), encoding='ascii')
        other_contents, other_release = self.base / 'other-contents', self.base / 'other-release'
        with patch('time.time', return_value=1), patch.dict(os.environ, {'SOURCE_DATE_EPOCH': '1', 'TZ': 'Pacific/Honolulu'}):
            contents(other_manifest, {'media': relocated}, other_contents)
            finalise(other_manifest, {'media': relocated}, other_contents, other_release)
        self.assertEqual(first_contents, snapshot(other_contents))
        self.assertEqual(first_release, snapshot(other_release))
        for data in first_release.values():
            self.assertNotIn(str(self.base).encode('ascii'), data)
            self.assertNotIn(self.base.as_posix().encode('ascii'), data)

    def test_contents_before_disc_and_runtime_are_frozen(self):
        for subject in self.m['subjects']:
            if subject['kind'] != 'container':
                (self.root / subject['file']['path']).unlink()
        self.seal()
        result = contents(self.manifest_path, self.roots, self.contents_dir)
        self.assertEqual(len(result['files']), 5)
        self.assert_new_output_failure(lambda: finalise(self.manifest_path, self.roots, self.contents_dir, self.release_dir), self.release_dir)

    def test_prefreeze_contents_survive_embedded_metadata_and_final_measurement(self):
        frozen = {}
        for subject in self.m['subjects']:
            if subject['kind'] != 'container':
                path = self.root / subject['file']['path']
                frozen[subject['id']] = path.read_bytes()
                path.unlink()
        self.seal()
        contents(self.manifest_path, self.roots, self.contents_dir)
        before = snapshot(self.contents_dir)
        for subject in self.m['subjects']:
            if subject['kind'] == 'disc':
                data = frozen[subject['id']] + before[subject['id'] + '.contents.spdx.json']
                (self.root / subject['file']['path']).write_bytes(data)
        self.freeze_runtime()
        finalise(self.manifest_path, self.roots, self.contents_dir, self.release_dir)
        for name, data in before.items():
            self.assertEqual((self.release_dir / name).read_bytes(), data)
        with tarfile.open(self.root / 'forge-docker.tar') as archive:
            self.assertEqual(archive.extractfile(RUNTIME_CONTENTS).read(), before['forge-docker.contents.spdx.json'])
        self.assertEqual(verify(self.manifest_path, self.roots, self.release_dir)['subjects'], 5)

    def test_unknown_licence_remains_noassertion_and_has_public_gap(self):
        app = self.m['components'][0]
        app.pop('licence_declared')
        app.pop('licence_evidence')
        self.m['gaps']['gaps'] = [dict(id='app-licence', target='app', field='licence', reason='Declaration not recorded')]
        for subject in self.m['subjects']:
            if 'app' in subject['components']:
                subject['licence_coverage'] = 'partial'
        self.write_manifest()
        self.emit()
        gaps = read_json(self.release_dir / 'GAPS.json')
        self.assertEqual(gaps['gaps'], self.m['gaps']['gaps'])
        for name in ('disc1.contents.spdx.json', 'forge-docker.contents.spdx.json'):
            app_row = next(row for row in read_json(self.release_dir / name)['packages'] if row['name'] == 'demo')
            self.assertEqual(app_row['licenseDeclared'], 'NOASSERTION')
        index = read_json(self.release_dir / 'SBOM-INDEX.json')
        self.assertTrue(all(row['licence_coverage'] == 'partial' for row in index['subjects'] if row['id'] != 'saved-door'))
        self.assertEqual(verify(self.manifest_path, self.roots, self.release_dir)['subjects'], 5)

    def test_wrong_generator_source_binding_fails_without_output(self):
        self.m['generator_sha256'] = '0' * 64
        self.write_manifest()
        self.assert_new_output_failure(lambda: contents(self.manifest_path, self.roots, self.contents_dir), self.contents_dir)

    def test_flat_subject_asset_name_collisions_fail_before_output(self):
        original = copy.deepcopy(self.m)
        for disc_id, container_id in [('image-door', 'door'), ('image-x', 'x.contents')]:
            with self.subTest(disc_id=disc_id, container_id=container_id):
                self.m = copy.deepcopy(original)
                self.m['subjects'][0]['id'] = disc_id
                self.m['subjects'][-1]['id'] = container_id
                self.write_manifest()
                self.assert_new_output_failure(lambda: contents(self.manifest_path, self.roots, self.contents_dir), self.contents_dir)

    def test_mutated_sealed_input_fails_without_output(self):
        (self.root / 'source.py').write_bytes(b'changed\n')
        self.assert_new_output_failure(lambda: contents(self.manifest_path, self.roots, self.contents_dir), self.contents_dir)

    def test_finalise_rejects_extra_missing_and_tampered_contents(self):
        contents(self.manifest_path, self.roots, self.contents_dir)
        self.freeze_runtime()
        target = self.contents_dir / 'disc1.contents.spdx.json'
        original = target.read_bytes()
        for change in ('extra', 'missing', 'tampered'):
            with self.subTest(change=change):
                extra = self.contents_dir / 'unexpected.json'
                if change == 'extra':
                    extra.write_bytes(b'{}\n')
                elif change == 'missing':
                    target.unlink()
                else:
                    target.write_bytes(original + b' ')
                self.assert_new_output_failure(lambda: finalise(self.manifest_path, self.roots, self.contents_dir, self.release_dir), self.release_dir)
                if extra.exists():
                    extra.unlink()
                target.write_bytes(original)

    def test_finalise_rejects_resealed_wrong_or_absent_embedded_runtime_contents(self):
        contents(self.manifest_path, self.roots, self.contents_dir)
        canonical_metadata = (self.contents_dir / 'forge-docker.contents.spdx.json').read_bytes()
        for change in ('changed', 'absent'):
            with self.subTest(change=change):
                if change == 'changed':
                    self.freeze_runtime(metadata=canonical_metadata + b' ')
                else:
                    self.freeze_runtime(include_contents=False)
                self.assert_new_output_failure(lambda: finalise(self.manifest_path, self.roots, self.contents_dir, self.release_dir), self.release_dir)

    def test_verify_rejects_each_tampered_metadata_file(self):
        self.emit()
        for name in snapshot(self.release_dir):
            with self.subTest(name=name):
                target = self.release_dir / name
                original = target.read_bytes()
                target.write_bytes(original + b' ')
                with self.assertRaises(ValueError):
                    verify(self.manifest_path, self.roots, self.release_dir)
                target.write_bytes(original)

    def test_verify_rejects_missing_and_extra_metadata(self):
        self.emit()
        target = self.release_dir / 'GAPS.json'
        original = target.read_bytes()
        target.unlink()
        with self.assertRaises(ValueError) as refusal:
            verify(self.manifest_path, self.roots, self.release_dir)
        self.assertNotIn(str(self.base), str(refusal.exception))
        target.write_bytes(original)
        (self.release_dir / 'unexpected.json').write_bytes(b'{}\n')
        with self.assertRaises(ValueError):
            verify(self.manifest_path, self.roots, self.release_dir)

    def test_verify_rejects_frozen_subject_mutation(self):
        self.emit()
        for name in ('disc1.img', 'forge-docker.tar', 'door.tar'):
            with self.subTest(name=name):
                target = self.root / name
                original = target.read_bytes()
                target.write_bytes(original + b'changed')
                with self.assertRaises((ValueError, OSError)):
                    verify(self.manifest_path, self.roots, self.release_dir)
                target.write_bytes(original)

    def test_refuse_existing_output_and_input_overlap(self):
        self.contents_dir.mkdir()
        marker = self.contents_dir / 'keep.txt'
        marker.write_bytes(b'preserve')
        with self.assertRaises(ValueError):
            contents(self.manifest_path, self.roots, self.contents_dir)
        self.assertEqual(marker.read_bytes(), b'preserve')
        overlap = self.root / 'new-output'
        self.assert_new_output_failure(lambda: contents(self.manifest_path, self.roots, overlap), overlap)
        marker.unlink()
        self.contents_dir.rmdir()
        contents(self.manifest_path, self.roots, self.contents_dir)
        self.freeze_runtime()
        overlap = self.contents_dir / 'new-output'
        self.assert_new_output_failure(lambda: finalise(self.manifest_path, self.roots, self.contents_dir, overlap), overlap)
        self.release_dir.mkdir()
        marker = self.release_dir / 'keep.txt'
        marker.write_bytes(b'preserve')
        with self.assertRaises(ValueError):
            finalise(self.manifest_path, self.roots, self.contents_dir, self.release_dir)
        self.assertEqual(marker.read_bytes(), b'preserve')

    def test_atomic_output_cleanup_on_publish_failure(self):
        with patch('sbom.workflow.os.rename', side_effect=OSError('synthetic publish failure')):
            self.assert_new_output_failure(lambda: contents(self.manifest_path, self.roots, self.contents_dir), self.contents_dir)

    def test_cli_round_trip_and_invalid_input_exit_codes(self):
        def run(*arguments):
            return subprocess.run([sys.executable, '-I', '-B', str(TOOLS / 'sbom.py'), *map(str, arguments)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        identity = run('generator-hash')
        self.assertEqual(identity.returncode, 0, identity.stderr)
        self.assertEqual(identity.stdout.strip(), generator_sha256())
        common = ['--manifest', self.manifest_path, '--root', 'media=' + str(self.root)]
        result = run('contents', *common, '--out', self.contents_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)['files']), 5)
        self.freeze_runtime()
        result = run('finalise', *common, '--contents', self.contents_dir, '--out', self.release_dir)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(self.release_dir.exists())
        result = run('verify', *common, '--sboms', self.release_dir)
        self.assertEqual(result.returncode, 2, result.stderr)
        # Detailed five-subject measurement qualification remains library-only;
        # the frozen public CLI round trip has its own producer/consumer fixture.
        finalise(self.manifest_path, self.roots, self.contents_dir, self.release_dir)
        self.assertEqual(verify(self.manifest_path, self.roots, self.release_dir)['subjects'], 5)
        (self.release_dir / 'GAPS.json').write_bytes(b'{}\n')
        with self.assertRaises(ValueError) as refusal:
            verify(self.manifest_path, self.roots, self.release_dir)
        self.assertNotIn(str(self.base), str(refusal.exception))
        result = run('contents', *common, '--root', 'media=' + str(self.root), '--out', self.base / 'invalid-output')
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.base / 'invalid-output').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
