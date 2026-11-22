"""Invented frozen producer/consumer bindings; no actual release inputs."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[2] / 'src/release-tools'
sys.path.insert(0, str(TOOLS))
from contract_validate import validate_contract_set
from sbom.common import canonical, digest
from sbom.workflow import contents, finalise, generator_sha256, verify


class Frozen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sbom-frozen-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'sealed'
        self.root.mkdir()
        self.roots = {'media': self.root}
        source = b'Invented source.\n'
        (self.root / 'source.txt').write_bytes(source)
        (self.root / 'LICENCE.txt').write_bytes(b'Invented licence evidence.\n')
        (self.root / 'manifest.json').write_bytes(b'{"synthetic":true}\n')
        for name in ['disc1.img', 'disc2.iso']:
            (self.root / name).write_bytes(('Invented frozen ' + name).encode('ascii'))
        with tarfile.open(self.root / 'forge-docker.tar', 'w') as archive:
            member = tarfile.TarInfo('source.txt')
            member.size = len(source)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(source))
        component = dict(id='app', kind='application', name='demo', version='1.0', architecture='all',
                         usage='runtime', files=[dict(root='media', path='source.txt')], dependencies=[],
                         licence_declared='MIT', licence_evidence=[dict(root='media', path='LICENCE.txt')])
        subjects = [dict(id='disc1', kind='disc', file=dict(root='media', path='disc1.img'), role='workstation', aliases=[],
                         components=['app'], component_coverage='complete', licence_coverage='complete'),
                    dict(id='disc2', kind='disc', file=dict(root='media', path='disc2.iso'), role='content', aliases=['compat'],
                         components=['app'], component_coverage='complete', licence_coverage='complete'),
                    dict(id='forge', kind='runtime', file=dict(root='media', path='forge-docker.tar'), role='forge-runtime', aliases=[],
                         components=['app'], component_coverage='complete', licence_coverage='complete',
                         runtime_members=[dict(path='source.txt', size=len(source), sha256=hashlib.sha256(source).hexdigest(), mode=0o644)])]
        self.measured = dict(format='deep32-sbom-input/1', release='v0.1.0-alpha.3', source_revision='1'*40,
                             source_date_epoch=946684800, generator_sha256=generator_sha256(),
                             roots=[dict(id='media', files=[dict(path=p.name, size=p.stat().st_size, sha256=digest(p))
                                                         for p in sorted(self.root.iterdir())])],
                             subjects=subjects, components=[component], gaps=dict(format='deep32-sbom-gaps/1', gaps=[]))
        reference = dict(path='inputs/manifest.json', sha256=digest(self.root / 'manifest.json'))
        self.contract = dict(format='deep32-sbom-input', format_version=1, release=self.measured['release'],
                             source_commit=self.measured['source_revision'], source_date_epoch=self.measured['source_date_epoch'],
                             generator_sha256=self.measured['generator_sha256'],
                             input_roots=[dict(id='media', path='inputs', manifest=reference)],
                             subjects=[dict(kind=s['kind'], path=s['file']['path'], size=(self.root / s['file']['path']).stat().st_size,
                                            sha256=digest(self.root / s['file']['path']),
                                            aliases=[dict(role=role, path=s['file']['path']) for role in s['aliases']],
                                            components=['app'], input_manifests=[reference], contents_sboms=[]) for s in subjects],
                             components=[dict(id='app', name='demo', version='1.0', purpose='APPLICATION', scope='runtime',
                                              sha256=digest(self.root / 'source.txt'), source_url='NOASSERTION', licence='MIT',
                                              licence_evidence=[dict(path='inputs/LICENCE.txt', sha256=digest(self.root / 'LICENCE.txt'))],
                                              dependencies=[], base_inventory=None)], exceptions=[], excluded_metadata=['SHA256SUMS'])
        self.manifest = self.base / 'MEASURED.json'
        self.frozen = self.base / 'FROZEN.json'
        self.content = self.base / 'contents'
        self.output = self.base / 'output'
        self.write()

    def write(self):
        self.manifest.write_text(canonical(self.measured), encoding='ascii')
        self.frozen.write_text(canonical(self.contract), encoding='ascii')

    def emit(self):
        contents(self.manifest, self.roots, self.content)
        return finalise(self.manifest, self.roots, self.content, self.output, contract_path=self.frozen)

    def test_frozen_index_reaches_shared_consumer_with_exact_subjects_and_external_assets(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        self.emit()
        index = json.loads((self.output / 'SBOM-INDEX.json').read_bytes())
        asset_map = dict(format='deep32-asset-map', format_version=1, release=self.contract['release'],
                         entries=[dict(asset=row['sbom']['asset'], media_paths=[], install=[]) for row in index['subjects']])
        validate_contract_set({'sbom-input': self.contract, 'sbom-index': index, 'asset-map': asset_map})
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        self.assertEqual(verify(self.manifest, self.roots, self.output, contract_path=self.frozen)['subjects'], 3)
        self.assertFalse((self.output / 'GAPS.json').exists())
        self.assertEqual(index['format'], 'deep32-sbom-index')
        self.assertEqual(next(r for r in index['subjects'] if r['subject']['kind']=='runtime')['sbom']['media_paths'], [])

    def test_cli_finalise_and_verify_frozen_mode(self):
        contents(self.manifest, self.roots, self.content)
        common = ['--manifest', str(self.manifest), '--root', 'media=' + str(self.root), '--contract', str(self.frozen)]
        for verb, extra in [('finalise', ['--contents', str(self.content), '--out', str(self.output)]),
                            ('verify', ['--sboms', str(self.output)])]:
            run = subprocess.run([sys.executable, '-I', '-B', str(TOOLS/'sbom.py'), verb, *common, *extra], capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
        index = self.output/'SBOM-INDEX.json'
        original = index.read_bytes()
        for invalid in ['tampered-metadata', 'malformed-contract']:
            with self.subTest(invalid=invalid):
                if invalid == 'tampered-metadata':
                    index.write_bytes(b'{}\n')
                else:
                    index.write_bytes(original)
                    self.frozen.write_bytes(b'{"format":')
                run = subprocess.run([sys.executable, '-I', '-B', str(TOOLS/'sbom.py'), 'verify',
                                      *common, '--sboms', str(self.output)], capture_output=True)
                self.assertEqual(run.returncode, 2, run.stderr)
                self.assertIn(b'SBOM verification failed:', run.stderr)
                self.assertNotIn(str(self.base), run.stderr.decode('utf-8', errors='replace'))

    def test_unknown_licences_publish_frozen_gap_contracts(self):
        self.measured['components'][0].update(licence_declared='NOASSERTION', licence_evidence=[])
        self.contract['components'][0].update(licence='NOASSERTION', licence_evidence=[])
        for subject in self.measured['subjects']:
            subject['licence_coverage'] = 'partial'
        self.measured['gaps']['gaps'] = [dict(id='licence-gap', target='app', field='licence', reason='Synthetic unknown licence.')]
        self.write()
        self.emit()
        from contract_validate import validate_contract
        index = json.loads((self.output/'SBOM-INDEX.json').read_bytes())
        for row in index['subjects']:
            binding = row['gap']
            self.assertEqual(binding['sha256'], digest(self.output/binding['path']))
            validate_contract('sbom-gap', json.loads((self.output/binding['path']).read_bytes()))

    def test_public_finalisation_refuses_already_embedded_sbom(self):
        contents(self.manifest, self.roots, self.content)
        raw = (self.content/'forge.contents.spdx.json').read_bytes()
        member_name = 'deep32/kit/sbom/forge-docker.contents.spdx.json'
        archive_path = self.root/'forge-docker.tar'
        with tarfile.open(archive_path, 'a') as archive:
            member = tarfile.TarInfo(member_name)
            member.size, member.mode = len(raw), 0o644
            archive.addfile(member, io.BytesIO(raw))
        self.measured['subjects'][2]['runtime_members'].append(
            dict(path=member_name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest(), mode=0o644))
        file_row = next(row for row in self.measured['roots'][0]['files'] if row['path']=='forge-docker.tar')
        file_row.update(size=archive_path.stat().st_size, sha256=digest(archive_path))
        self.contract['subjects'][2].update(size=file_row['size'], sha256=file_row['sha256'])
        self.write()
        with self.assertRaisesRegex(ValueError, 'embedded SBOM metadata'):
            finalise(self.manifest, self.roots, self.content, self.output, contract_path=self.frozen)
        self.assertFalse(self.output.exists())

    def test_frozen_mismatch_refuses_before_publication(self):
        for mutation in ['sha256', 'aliases', 'alias-path', 'component', 'evidence', 'manifest', 'commit']:
            with self.subTest(mutation=mutation):
                original = copy.deepcopy(self.contract)
                if mutation == 'sha256': self.contract['subjects'][0]['sha256'] = 'f'*64
                elif mutation == 'aliases': self.contract['subjects'][1]['aliases'][0]['role'] = 'different'
                elif mutation == 'alias-path': self.contract['subjects'][1]['aliases'][0]['path'] = 'different.iso'
                elif mutation == 'component': self.contract['components'][0]['sha256'] = 'f'*64
                elif mutation == 'evidence': self.contract['components'][0]['licence_evidence'][0]['sha256'] = 'f'*64
                elif mutation == 'manifest':
                    self.contract['input_roots'][0]['manifest']['sha256'] = 'f'*64
                    for subject in self.contract['subjects']: subject['input_manifests'][0]['sha256'] = 'f'*64
                else: self.contract['source_commit'] = 'f'*40
                self.write()
                from sbom.inputs import normalise_inputs
                from sbom.frozen import bind
                with self.assertRaises(ValueError): bind(self.frozen, normalise_inputs(self.manifest, self.roots))
                self.assertFalse(self.output.exists())
                self.contract = original

    def prepare_assembly(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_whole_assembly as whole
        from contract_validate import mirror_transports
        host = whole.WholeAssembly(methodName='runTest')
        self.addCleanup(host.doCleanups)
        host.setUp()
        subjects, frozen_subjects = [], []
        reference = self.contract['input_roots'][0]['manifest']
        for descriptor in mirror_transports(host.mirrors):
            identity = descriptor.get('role', descriptor.get('bundle_id'))
            name = descriptor['file']
            (self.root/name).write_bytes(host.sources[identity].read_bytes())
            subjects.append(dict(id=identity, kind='disc', file=dict(root='media', path=name), role='role-'+identity,
                                 aliases=[], components=['app'], component_coverage='complete', licence_coverage='complete'))
            frozen_subjects.append(dict(kind='disc', path=name, size=descriptor['size'], sha256=descriptor['sha256'],
                                        aliases=[], input_manifests=[reference], components=['app'], contents_sboms=[]))
        self.measured['subjects'] = subjects
        self.measured['roots'][0]['files'] = [dict(path=p.name, size=p.stat().st_size, sha256=digest(p))
                                             for p in sorted(self.root.iterdir())]
        self.contract['subjects'] = frozen_subjects
        self.write()
        self.emit()
        for path in sorted(self.output.iterdir()):
            host.add_asset(path.name, path.read_bytes(), [])
        index = json.loads((self.output/'SBOM-INDEX.json').read_bytes())
        host.asset_map['entries'].extend(dict(asset=row['sbom']['asset'], media_paths=[], install=[])
                                         for row in index['subjects'])
        (host.inputs/'ASSET-MAP.json').write_bytes(canonical(host.asset_map).encode('ascii'))
        host.sync()
        return host, whole.assembly, index

    def test_emitted_frozen_index_passes_actual_whole_assembly_without_changing_content(self):
        host, assembly, index = self.prepare_assembly()
        content = host.sources['disc2'].read_bytes()
        self.assertEqual(assembly.assemble(host.args), 0)
        self.assertEqual((host.out/'github/SBOM-INDEX.json').read_bytes(),
                         (self.output/'SBOM-INDEX.json').read_bytes())
        self.assertEqual(host.sources['disc2'].read_bytes(), content)
        self.assertTrue(all(not row['sbom']['media_paths'] for row in index['subjects']))

    def test_actual_assembly_refuses_bad_schema_subject_hash_and_asset_binding(self):
        host, assembly, index = self.prepare_assembly()
        for mutation in ['schema', 'digest', 'asset-map']:
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(index)
                if mutation == 'schema': changed['format'] = 'deep32-sbom-index/1'
                elif mutation == 'digest': changed['subjects'][0]['subject']['sha256'] = 'f'*64
                else: changed['subjects'][0]['sbom']['media_paths'] = [dict(disc='disc1', path='sbom/different.spdx.json')]
                (host.inputs/'SBOM-INDEX.json').write_text(canonical(changed), encoding='ascii')
                with self.assertRaises(ValueError): assembly.assemble(host.args)
                self.assertFalse(host.out.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
