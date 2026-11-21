"""Qualify medium inventories with synthetic offline evidence."""

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[2] / 'src' / 'release-tools'
sys.path.insert(0, str(TOOLS))
from sbom.common import canonical, digest
from sbom.inputs import normalise_inputs
from sbom.owner_media import disc_sbom
from sbom.workflow import verify
from contract_validate import validate_contract_set


class OwnerMedia(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='owner-medium-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.medium = self.base / 'medium'
        self.medium.mkdir()
        self.image = self.base / 'disc1.img'
        self.image.write_bytes(b'SYNTHETIC IMMUTABLE MEDIUM IMAGE\n')
        self.output = self.base / 'output'
        self.listing = self.base / 'MEDIUM-FILES.txt'
        self.packages = self.base / 'MEDIUM-PACKAGES.sha256'
        self.sets = self.base / 'SETS.SHA256'
        self.kwargs = dict(epoch=946684800, source_revision='a' * 40, medium_root=self.medium)

    def put(self, name, data):
        path = self.medium / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def files(self):
        return sorted(p for p in self.medium.rglob('*') if p.is_file())

    def prepare_disc1(self):
        self.put('8.0/amd64/base80.tgz', b'SYNTHETIC BASE SET\n')
        self.put('8.0/amd64/site80.tgz', b'SYNTHETIC APPLICATION SET\n')
        self.put('packages/amd64/tailscale-1.90.1p0.tgz', b'SYNTHETIC PACKAGE\n')
        self.put('packages/amd64/roots', b'tailscale\n')
        self.put('bsd', b'SYNTHETIC KERNEL\n')
        self.put('LIBRARY-CREDITS.tsv', b'path\tlicense\n8.0/amd64/site80.tgz\tMIT\n')
        self.listing.write_text(''.join('./' + p.relative_to(self.medium).as_posix() + '\n' for p in self.files()), encoding='ascii')
        self.packages.write_text(''.join(digest(p) + ' ./'+ p.name + '\n' for p in self.files()
                                        if p.parent == self.medium / 'packages/amd64'), encoding='ascii')
        self.sets.write_text('SHA256 (base80.tgz) = ' + digest(self.medium / '8.0/amd64/base80.tgz') + '\n', encoding='ascii')
        return dict(medium_list=self.listing, checksums=[self.packages, self.sets], credits=self.medium / 'LIBRARY-CREDITS.tsv')

    def prepare_disc2(self):
        self.image = self.base / 'disc2.iso'
        self.image.write_bytes(b'SYNTHETIC UNCHANGED CONTENT ISO\n')
        self.put('kb/generation-9/BUNDLE.json', b'SYNTHETIC OPAQUE KB BYTES\n')
        self.put('library/lib-demo/lib-demo-generation-8.tar.part001', b'SYNTHETIC LIBRARY PART\n')
        self.put('corpus/rfc/block.json', b'SYNTHETIC OPAQUE CORPUS BYTES\n')
        self.put('library/cisco-public.sqlite', b'SYNTHETIC CATALOG BYTES\n')
        self.put('code/source.py', b'SYNTHETIC SOURCE BYTES\n')
        self.put('LIBRARY-CREDITS.tsv', b'pack\tlicence\nlib-demo\tCC-BY-4.0\n')
        rows = [dict(path=p.relative_to(self.medium).as_posix(), size=p.stat().st_size, sha256=digest(p))
                for p in self.files()]
        self.content = dict(format='deep32-content-manifest', format_version=1,
                            members=rows, counts=dict(files=len(rows), bytes=sum(row['size'] for row in rows)),
                            kb=dict(path='kb/generation-9', generation='generation-9'),
                            library=[dict(pack='lib-demo', generation='generation-8', parts=[
                                dict(path='library/lib-demo/lib-demo-generation-8.tar.part001')])],
                            blocks=[dict(name='rfc', version='generation-7')],
                            cisco_public=dict(path='library/cisco-public.sqlite', catalogue_generation='generation-6'))
        self.manifest = self.put('MANIFEST.json', canonical(self.content).encode('ascii'))
        self.put('MANIFEST.json.asc', b'SYNTHETIC PUBLIC SIGNATURE EVIDENCE\n')
        return dict(manifest=self.manifest, credits=self.medium / 'LIBRARY-CREDITS.tsv')

    def generate(self, disc='disc1', extra=None):
        return disc_sbom(disc, self.image, self.output, **self.kwargs, **(extra or self.prepare_disc1()))

    def normal(self):
        return normalise_inputs(self.output / 'MEASURED.json',
                                {key: self.output / key for key in ('medium', 'subject')})

    def test_disc1_complete_exact_files_versions_frozen_adapter(self):
        extra = self.prepare_disc1()
        original = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in [self.image, *self.files()]}
        result = self.generate(extra=extra)
        normal = self.normal()
        self.assertEqual(result['components'], len(self.files()))
        self.assertEqual(normal['subjects'][0]['component_coverage'], 'complete')
        components = {row['files'][0]['path']: row for row in normal['components']}
        self.assertEqual(set(components), {p.relative_to(self.medium).as_posix() for p in self.files()})
        for name, row in components.items():
            self.assertEqual(len(row['files']), 1)
            self.assertEqual(row['files'][0]['sha256'], digest(self.medium / name))
        self.assertEqual(components['8.0/amd64/base80.tgz']['kind'], 'openbsd-set')
        self.assertEqual(components['8.0/amd64/base80.tgz']['version'], '8.0')
        self.assertEqual(components['8.0/amd64/site80.tgz']['kind'], 'application')
        self.assertEqual(components['8.0/amd64/site80.tgz']['licence_declared'], 'MIT')
        self.assertEqual(components['packages/amd64/tailscale-1.90.1p0.tgz']['name'], 'tailscale')
        self.assertEqual(components['packages/amd64/tailscale-1.90.1p0.tgz']['version'], '1.90.1p0')
        self.assertFalse(any(row['field'] == 'components' for row in normal['gaps']))
        roots = {key: self.output / key for key in ('medium', 'subject')}
        self.assertEqual(verify(self.output/'MEASURED.json', roots, self.output/'metadata',
                                contract_path=self.output/'FROZEN.json')['subjects'], 1)
        self.assertEqual(original, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in original})
        self.assertNotIn(str(self.base), (self.output/'ASSEMBLY.json').read_text())

    def test_disc2_complete_medium_includes_manifest_and_signature_and_unit_generations(self):
        result = self.generate('disc2', self.prepare_disc2())
        normal = self.normal()
        components = {row['files'][0]['path']: row for row in normal['components']}
        self.assertEqual(result['components'], len(self.files()))
        self.assertIn('MANIFEST.json', components)
        self.assertIn('MANIFEST.json.asc', components)
        self.assertEqual(components['kb/generation-9/BUNDLE.json']['version'], 'generation-9')
        self.assertEqual(components['library/lib-demo/lib-demo-generation-8.tar.part001']['version'], 'generation-8')
        self.assertEqual(components['corpus/rfc/block.json']['version'], 'generation-7')
        self.assertEqual(components['library/cisco-public.sqlite']['version'], 'generation-6')
        self.assertEqual(components['code/source.py']['kind'], 'source')
        self.assertEqual(components['code/source.py']['version'], '0.1.0-alpha.1')
        self.assertEqual(components['library/lib-demo/lib-demo-generation-8.tar.part001']['licence_declared'], 'CC-BY-4.0')
        contract = json.loads((self.output/'FROZEN.json').read_bytes())
        index = json.loads((self.output/'metadata/SBOM-INDEX.json').read_bytes())
        validate_contract_set({'sbom-input': contract, 'sbom-index': index})
        self.assertEqual(index['subjects'][0]['coverage']['components'], 'complete')
        self.assertEqual(index['subjects'][0]['sbom']['media_paths'], [])

    def test_measured_json_listing_binds_image_and_every_file(self):
        extra = self.prepare_disc1()
        self.listing.write_text(canonical(dict(format='deep32-medium-files/1',
            image=dict(size=self.image.stat().st_size, sha256=digest(self.image)),
            files=[dict(path=p.relative_to(self.medium).as_posix(), size=p.stat().st_size, sha256=digest(p))
                   for p in self.files()])), encoding='ascii')
        self.generate(extra=extra)
        self.assertTrue((self.output/'metadata/disc1.spdx.json').is_file())

    def test_hash_mismatch_refuses_without_publication(self):
        extra = self.prepare_disc2()
        self.put('code/source.py', b'SYNTHETIC CHANGED BYTES\n')
        with self.assertRaisesRegex(ValueError, 'SHA-256 differs'):
            self.generate('disc2', extra)
        self.assertFalse(self.output.exists())

    def test_package_hash_mismatch_refuses(self):
        extra = self.prepare_disc1()
        self.put('packages/amd64/roots', b'SYNTHETIC CHANGED ROOT\n')
        with self.assertRaisesRegex(ValueError, 'checksum differs'):
            self.generate(extra=extra)
        self.assertFalse(self.output.exists())

    def test_missing_medium_file_refuses(self):
        extra = self.prepare_disc1()
        (self.medium/'bsd').unlink()
        with self.assertRaisesRegex(ValueError, 'missing or unexpected'):
            self.generate(extra=extra)
        self.assertFalse(self.output.exists())

    def test_extra_regular_medium_file_refuses(self):
        extra = self.prepare_disc2()
        self.put('unlisted.dat', b'SYNTHETIC EXTRA FILE\n')
        with self.assertRaisesRegex(ValueError, 'absent from its content manifest'):
            self.generate('disc2', extra)

    def test_missing_disc2_manifest_signature_refuses(self):
        extra = self.prepare_disc2()
        (self.medium/'MANIFEST.json.asc').unlink()
        with self.assertRaisesRegex(ValueError, 'detached content manifest signature'):
            self.generate('disc2', extra)
        self.assertFalse(self.output.exists())

    def test_missing_image_refuses(self):
        extra = self.prepare_disc1()
        self.image.unlink()
        with self.assertRaises(FileNotFoundError):
            self.generate(extra=extra)
        self.assertFalse(self.output.exists())

    def test_absolute_and_traversal_paths_refuse(self):
        extra = self.prepare_disc1()
        for name in ('/absolute.txt', '../escape.txt', 'C:/absolute.txt'):
            with self.subTest(name=name):
                self.listing.write_text(name+'\n', encoding='ascii')
                with self.assertRaises(ValueError):
                    self.generate(extra=extra)
                self.assertFalse(self.output.exists())

    def test_missing_package_or_set_coverage_refuses(self):
        extra = self.prepare_disc1()
        for paths in ([self.sets], [self.packages]):
            with self.subTest(paths=paths):
                with self.assertRaises(ValueError):
                    self.generate(extra=dict(extra, checksums=paths))
                self.assertFalse(self.output.exists())

    def test_emitter_excluded_metadata_refuses_exact_coverage_claim(self):
        extra = self.prepare_disc1()
        self.put('SHA256SUMS', b'SYNTHETIC MEDIUM CHECKSUM METADATA\n')
        with self.assertRaisesRegex(ValueError, 'unchanged emitter metadata rules'):
            self.generate(extra=extra)
        self.assertFalse(self.output.exists())

    def test_on_medium_explicit_credits_are_used_when_flags_are_omitted(self):
        extra = self.prepare_disc1()
        extra.pop('credits')
        self.generate(extra=extra)
        component = next(row for row in self.normal()['components']
                         if row['files'][0]['path'] == '8.0/amd64/site80.tgz')
        self.assertEqual(component['licence_declared'], 'MIT')
        self.assertEqual([row['path'] for row in component['licence_evidence']], ['LIBRARY-CREDITS.tsv'])

    def test_listing_image_digest_mismatch_refuses(self):
        extra = self.prepare_disc1()
        self.listing.write_text(canonical(dict(files=[dict(path=p.relative_to(self.medium).as_posix(),
            size=p.stat().st_size, sha256=digest(p)) for p in self.files()],
            image=dict(size=self.image.stat().st_size, sha256='f'*64))), encoding='ascii')
        with self.assertRaisesRegex(ValueError, 'different image bytes'):
            self.generate(extra=extra)

    def test_deterministic_output_under_reversed_listing_and_checksums(self):
        extra = self.prepare_disc1()
        self.generate(extra=extra)
        first = {p.name: p.read_bytes() for p in (self.output/'metadata').iterdir()}
        self.listing.write_text(''.join(line+'\n' for line in reversed(self.listing.read_text().splitlines())), encoding='ascii')
        self.output = self.base/'again'
        self.generate(extra=dict(extra, checksums=list(reversed(extra['checksums']))))
        # The evidence receipt records the supplied listing bytes, so changing that input changes provenance.
        # SPDX identities remain derived from medium files, source revision, epoch, and exact image bytes.
        self.assertEqual(first['disc1.spdx.json'], (self.output/'metadata/disc1.spdx.json').read_bytes())
        self.assertEqual(first['disc1.contents.spdx.json'], (self.output/'metadata/disc1.contents.spdx.json').read_bytes())

    def test_generated_medium_sboms_pass_actual_alpha2_assembly_validator(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_alpha2_assembly as fixture
        host = fixture.Alpha2Assembly(methodName='runTest')
        self.addCleanup(host.doCleanups)
        host.setUp()
        for disc in ('disc1', 'disc2'):
            extra = self.prepare_disc1() if disc == 'disc1' else self.prepare_disc2()
            self.image = self.base / ('deep32-0.1.0-alpha.1-disc2-content.iso' if disc == 'disc2' else 'original.img')
            shutil.copyfile(host.sources[disc], self.image)
            self.output = host.inputs / (disc + '-output')
            result = self.generate(disc, extra)
            index = json.loads((self.output/'metadata/SBOM-INDEX.json').read_bytes())
            host.index['subjects'] = [row for row in host.index['subjects']
                                      if row['subject']['path'] != host.sources[disc].name] + index['subjects']
            for row in host.manifest['images']:
                if row['id'] == disc:
                    row.update({key: self.output.name + '/' + result['image'][key]
                                for key in ('source', 'sums', 'sbom')})
                    self.assertEqual((host.inputs/row['source']).name, fixture.alpha.image_name(disc, self.image.suffix))
            for metadata in result['metadata']:
                shutil.copyfile(self.output/metadata['source'], host.inputs/metadata['file'])
                host.manifest['metadata'] = [row for row in host.manifest['metadata'] if row['file'] != metadata['file']]
                host.manifest['metadata'].append(dict(file=metadata['file'], source=metadata['file'], discs=metadata['discs']))
            # Start the next synthetic medium from an empty tree.
            for path in sorted(self.medium.iterdir()):
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
        host.sync()
        prepared = fixture.alpha.prepare(host.inputs/'input.json')
        self.assertEqual(len(prepared['images']), 11)
        self.assertEqual(digest(host.sources['disc2']), prepared['images']['disc2']['sha256'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
