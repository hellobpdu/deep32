#!/usr/bin/env python3
"""Synthetic offline qualification of the exact alpha.2 release image set."""

import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tarfile
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[2] / 'src' / 'release-tools'
sys.path.insert(0, str(TOOLS))
import alpha2_assemble as alpha
import contract_validate as contracts
import deep32_get_manifest as getter
import deep32_get_whole as whole_getter
import forge_preflight
from sbom.common import canonical as spdx_canonical
from sbom.spdx import emit_document


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def synthetic_storage(capability):
    rows = []
    for bundle in capability['bundles']:
        for budget in bundle['storage_budgets']:
            domains = []
            for domain in budget['domains']:
                name = domain['domain']
                trace = f"synthetic trace for {bundle['id']} {budget['backend']} {name}".encode('ascii')
                method = 'allocated-vhd-bytes' if name == 'desktop-backing' else 'allocated-filesystem-bytes'
                allocated = 1024 + domain['peak_bytes'] - domain['headroom_bytes']
                samples = {phase: allocated for phase in ('download', 'mount', 'join', 'import',
                                                          'retained', 'conversion', 'rollback')}
                samples['baseline'] = 1024
                domains.append(dict(budget=copy.deepcopy(domain), method=method, trace_sha256=checksum(trace),
                                    allocated_bytes=samples))
            rows.append(dict(bundle_id=bundle['id'], backend=budget['backend'], domains=domains))
    return dict(format='deep32-physical-storage-measurements/1', release=capability['release'],
                pins_sha256=capability['pins_sha256'], image_index_sha256=capability['image_index_sha256'],
                measurements=rows)


def synthetic_iso():
    blocks = [bytearray(2048) for _ in range(19)]
    for block, kind in zip(blocks[16:], (1, 2, 255)):
        block[0] = kind
        block[1:6] = b'CD001'
        block[6] = 1
    blocks[16][40:72] = b'DEEP32_GPU_1_0'.ljust(32, b' ')
    blocks[17][88:91] = b'%/E'
    return b''.join(blocks)


class Alpha2Assembly(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='alpha2-assembly-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()
        self.images = self.inputs / 'images'
        self.images.mkdir()
        self.out = self.root / 'release'
        fixtures = TOOLS / 'contracts' / 'fixtures'
        self.capability = contracts.strict_load(fixtures / 'capabilities' / 'manifest.json')
        self.capability['bundles'] = [r for r in self.capability['bundles']
                                      if r['id'] not in ('full', 'source-build-full')]
        self.mirrors = contracts.strict_load(fixtures / 'valid' / 'mirrors-v2.json')
        self.mirrors['bundles'] = [r for r in self.mirrors['bundles'] if r['bundle_id'] != 'full']
        self.index = dict(format='deep32-sbom-index', format_version=1, release=alpha.RELEASE, subjects=[])
        self.manifest = dict(format='deep32-alpha2-assembly-input', format_version=1, images=[],
                             disc3_image_dir='images', capability_manifest='capabilities.json',
                             mirrors_template='mirrors.json', sbom_indexes=['index.json'], metadata=[],
                             reproducibility=None)
        self.sources = {}
        for key in alpha.IMAGE_IDS:
            folder = self.inputs if key in ('disc1', 'disc2') else self.images
            name = alpha.image_name(key, '.img' if key == 'disc1' else '.iso')
            source = folder / name
            data = ('SYNTHETIC TEST IMAGE ' + key + '\n').encode('ascii')
            if key == 'core':
                data = synthetic_iso()
            source.write_bytes(data)
            sums = folder / (name + '.sha256')
            sums.write_text(checksum(data) + '  ' + name + '\n', encoding='ascii')
            sbom = key + '.spdx.json'
            content = key + '.contents.spdx.json'
            subject = dict(id=key, kind='disc', role=key, aliases=[],
                           file=dict(root='media', path=name, size=len(data), sha256=checksum(data)),
                           components=['application'], component_coverage='complete', licence_coverage='complete')
            normal = dict(format='deep32-sbom-normalised/1', release=alpha.VERSION,
                          source_revision='a' * 40, generator_sha256='b' * 64, source_date_epoch=946684800,
                          subjects=[subject], components=[dict(id='application', kind='application', name='demo',
                              version='1.0', architecture='all', usage='runtime', files=[], dependencies=[],
                              licence_declared='MIT', licence_evidence=[])])
            contents = emit_document(normal, subject, kind='contents')
            content_data = spdx_canonical(contents).encode('ascii')
            doc = emit_document(normal, subject, child_documents=[dict(
                subject=subject, document=contents, sha256=checksum(content_data))])
            sbom_data = spdx_canonical(doc).encode('ascii')
            (self.inputs / sbom).write_bytes(sbom_data)
            (self.inputs / content).write_bytes(content_data)
            self.manifest['metadata'].append(dict(file=content, source=content, discs=[]))
            self.manifest['images'].append(dict(id=key, source=source.relative_to(self.inputs).as_posix(),
                                               sums=sums.relative_to(self.inputs).as_posix(), sbom=sbom))
            self.index['subjects'].append(dict(subject=dict(kind='disc', path=name, size=len(data),
                sha256=checksum(data), aliases=[]), sbom=dict(asset=sbom, sha256=checksum(sbom_data), media_paths=[]),
                contents=[dict(path=content, sha256=checksum(content_data))],
                coverage=dict(components='complete', licences='complete'), gap=None))
            self.sources[key] = source
        self.sync()

    def sync(self):
        for name, document in [('input.json', self.manifest), ('capabilities.json', self.capability),
                               ('mirrors.json', self.mirrors), ('index.json', self.index)]:
            (self.inputs / name).write_bytes(contracts.canonical(document))

    def assemble(self):
        self.sync()
        return alpha.assemble(self.inputs / 'input.json', self.out)

    def refused(self):
        self.sync()
        with self.assertRaises((ValueError, OSError, alpha.whole.Refusal)):
            alpha.assemble(self.inputs / 'input.json', self.out)
        self.assertFalse(self.out.exists())
        self.assertFalse(any(p.name.startswith('.alpha2-input-') for p in self.root.iterdir()))

    def bind_storage(self, raw, discs=('disc3',)):
        name = 'STORAGE-MEASUREMENTS.json'
        self.capability['storage_evidence'] = dict(file=name, sha256=checksum(raw))
        for bundle in self.capability['bundles']:
            for budget in bundle['storage_budgets']:
                budget['evidence'] = name
        (self.inputs / name).write_bytes(raw)
        row = dict(file=name, source=name, discs=list(discs))
        self.manifest['metadata'].append(row)
        return row

    def forge_envelope(self):
        candidate = dict(format='deep32-forge-prerequisites/1', release=self.capability['release'],
                         capability_sha256=checksum(contracts.canonical(self.capability)),
                         pins_sha256=self.capability['pins_sha256'], minimum_vram_mib=10752,
                         vram_policy='C1-install-matrix-tier-11',
                         driver_floors={'tei_minimum_driver': '800.1', 'cuda_minimum_driver': '810.2',
                                        'mineru_minimum_driver': '805.0'},
                         recipes=[dict(id='synthetic-accepted-recipe', accepted_on='2026-11-18',
                                       os_id='ubuntu', os_version='24.04', architecture='amd64', journey='J2',
                                       recipe_sha256='b' * 64, acceptance_sha256='c' * 64)])
        if 'storage_evidence' in self.capability:
            candidate['storage_evidence_sha256'] = self.capability['storage_evidence']['sha256']
        return candidate

    def bind_sources(self, archive_extra=False, archive_link=False, archive_changed=False):
        files, companions = {}, []
        for bundle in self.capability['bundles']:
            if bundle['kind'] != 'corresponding-source-build':
                continue
            name = bundle['id']
            bindings, members = [], []
            for category in ('source', 'lock', 'build', 'licence', 'provenance'):
                shared = category == 'source' and bundle['corresponds_to'].startswith('embedder-')
                logical = 'source/tei-source.rs' if shared else category + '/' + name + '.txt'
                original = 'tei/.github/source.rs' if shared else 'other/' + logical
                payload = ('public fixture ' + logical).encode('ascii')
                files[original] = payload
                members.append(dict(path=logical, category=category, size=len(payload), sha256=checksum(payload)))
                bindings.append(dict(path=logical, entry=original))
            bundle['members'] = members
            for budget in bundle['storage_budgets']:
                budget['evidence'] = 'provenance/' + name + '.txt'
                for domain in budget['domains']:
                    if domain['domain'] == 'download':
                        domain['download_bytes'] = sum(row['size'] for row in members)
                        domain['peak_bytes'] = domain['steady_bytes'] + sum(
                            domain[k] for k in ('download_bytes', 'headroom_bytes', 'transient_bytes'))
            companions.append(dict(id=name, members=bindings))
        if archive_extra:
            files['other/unlisted.txt'] = b'extra'
        if archive_changed:
            original = files['tei/.github/source.rs']
            files['tei/.github/source.rs'] = b'x' + original[1:]
        folder = self.inputs / 'carriers'
        folder.mkdir()
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            for entry, payload in sorted(files.items()):
                item = tarfile.TarInfo(entry)
                if archive_link and entry == 'tei/.github/source.rs':
                    item.type = tarfile.SYMTYPE
                    item.linkname = 'other/path'
                    archive.addfile(item)
                else:
                    item.size = len(payload)
                    archive.addfile(item, io.BytesIO(payload))
        raw = buffer.getvalue()
        digest = checksum(raw)
        (folder / (digest + '.tar.gz')).write_bytes(raw)
        for row in companions:
            for member in row['members']:
                member['carrier_sha256'] = digest
        catalog = dict(format=alpha.source_build_catalog.FORMAT, release=self.capability['release'],
                       capability_sha256=checksum(contracts.canonical(self.capability)),
                       carriers=[dict(sha256=digest, size=len(raw))], companions=companions)
        self.manifest.update(format_version=2, source_build_catalog='sources.json',
                             source_build_carrier_dir='carriers')
        self.save_sources(catalog)
        return catalog, digest

    def save_sources(self, catalog):
        (self.inputs / 'sources.json').write_bytes(contracts.canonical(catalog))

    def test_v2_catalog_and_one_shared_carrier_are_staged_and_bound(self):
        catalog, digest = self.bind_sources()
        self.assemble()
        asset = alpha.source_build_catalog.ASSET
        raw = (self.out / 'github' / asset).read_bytes()
        self.assertEqual(contracts.strict_loads(raw), catalog)
        self.assertEqual(len(catalog['companions']), 9)
        for name in ('SHA256SUMS', contracts.mirror_child_names(self.mirrors)['disc3']):
            self.assertIn(checksum(raw) + '  ' + asset,
                          (self.out / 'github' / name).read_text(encoding='ascii'))
        carrier = self.out / 'source-build' / (digest + '.tar.gz')
        self.assertEqual(checksum(carrier.read_bytes()), digest)
        self.assertEqual(list(carrier.parent.iterdir()), [carrier])

    def test_v2_missing_or_changed_companion_refuses(self):
        catalog, digest = self.bind_sources()
        missing = catalog['companions'].pop()
        self.save_sources(catalog)
        self.refused()
        catalog['companions'].append(missing)
        self.save_sources(catalog)
        carrier = self.inputs / 'carriers' / (digest + '.tar.gz')
        raw = carrier.read_bytes()
        carrier.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        self.refused()

    def test_v2_unlisted_carrier_member_refuses(self):
        self.bind_sources(archive_extra=True)
        self.refused()

    def test_v2_archive_internal_bytes_must_match_signed_member(self):
        self.bind_sources(archive_changed=True)
        self.refused()

    def test_v2_linked_carrier_member_refuses(self):
        self.bind_sources(archive_link=True)
        self.refused()

    def test_v2_catalog_capability_identity_required(self):
        self.bind_sources()
        self.capability['pins_sha256'] = 'f' * 64
        self.refused()

    def test_v1_rejects_catalog_disguised_as_ordinary_metadata(self):
        self.bind_sources()
        self.manifest.pop('source_build_catalog')
        self.manifest.pop('source_build_carrier_dir')
        self.manifest['format_version'] = 1
        self.manifest['metadata'].append(dict(file=alpha.source_build_catalog.ASSET,
                                              source='sources.json', discs=['disc3']))
        self.refused()

    def test_exact_eleven_images_and_external_sbom_signed_list_coverage(self):
        before = {key: (source.read_bytes(), source.stat().st_mtime_ns) for key, source in self.sources.items()}
        result = self.assemble()
        self.assertEqual(result['images'], 11)
        self.assertTrue(result['unsigned'])
        self.assertEqual(result['pending_signatures'], 4)
        github = self.out / 'github'
        mirrors = contracts.strict_load(github / 'MIRRORS.json')
        sums = getter.read_sums(github / 'SHA256SUMS')
        self.assertEqual(len(list((self.out / 'images').iterdir())), 11)
        self.assertEqual({r['bundle_id'] for r in mirrors['bundles']}, set(alpha.BUNDLES))
        for key, source in self.sources.items():
            self.assertTrue(os.path.samefile(source, self.out / 'images' / source.name))
            self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns), before[key])
            self.assertEqual(sums[source.name], checksum(source.read_bytes()))
            self.assertEqual(sums[key + '.spdx.json'], checksum((github / (key + '.spdx.json')).read_bytes()))
            role = key if key in ('disc1', 'disc2') else 'disc3'
            child = getter.read_sums(github / contracts.mirror_child_names(mirrors)[role])
            self.assertIn(key + '.spdx.json', child)
            self.assertIn(source.name, child)
        self.assertFalse(any(p.suffix == '.sig' for p in github.iterdir()))
        assets = (github / 'ASSETS.txt').read_text().splitlines()
        self.assertEqual(sum(n.endswith('.sig') for n in assets), 4)
        getter.validate_whole_release(github, scope='whole', require_signatures=False)
        for path in github.iterdir():
            self.assertNotIn(str(self.root).encode('ascii'), path.read_bytes())

    def test_forge_prerequisites_are_bound_to_root_and_disc3(self):
        name = alpha.forge_requirements.ASSET
        candidate = self.forge_envelope()
        path = self.inputs / name
        raw = contracts.canonical(candidate)
        path.write_bytes(raw)
        row = dict(file=name, source=name, discs=['disc3'])
        self.manifest['metadata'].append(row)
        self.assemble()
        github = self.out / 'github'
        child = contracts.mirror_child_names(contracts.strict_load(github / 'MIRRORS.json'))
        for sums in ('SHA256SUMS', child['disc3']):
            self.assertEqual(checksum(raw), getter.read_sums(github / sums)[name])
        for role in ('disc1', 'disc2'):
            self.assertNotIn(name, getter.read_sums(github / child[role]))
        self.out = self.root / 'bad-release'
        row['discs'] = ['disc2']
        self.refused()
        row['discs'] = ['disc3']
        candidate['pins_sha256'] = 'f' * 64
        path.write_bytes(contracts.canonical(candidate))
        self.refused()

    def test_detached_storage_exact_root_and_both_linux_inventory_paths(self):
        raw = contracts.canonical(synthetic_storage(self.capability))
        row = self.bind_storage(raw)
        name = row['file']
        envelope = self.forge_envelope()
        envelope_name = alpha.forge_requirements.ASSET
        (self.inputs / envelope_name).write_bytes(contracts.canonical(envelope))
        self.manifest['metadata'].append(dict(file=envelope_name, source=envelope_name, discs=['disc3']))
        self.assemble()
        github = self.out / 'github'
        mirrors = getter.validate_whole_release(github, scope='whole', require_signatures=False)
        root = getter.read_sums(github / 'SHA256SUMS')
        children = contracts.mirror_child_names(mirrors)
        self.assertEqual(root[name], checksum(raw))
        self.assertEqual(getter.read_sums(github / children['disc3'])[name], root[name])
        for role in ('disc1', 'disc2'):
            self.assertNotIn(name, getter.read_sums(github / children[role]))
        online = self.root / 'online-metadata-fixture'
        online.mkdir()
        (online / 'SHA256SUMS').write_bytes((github / 'SHA256SUMS').read_bytes())
        with mock.patch.object(whole_getter, 'fetch_metadata', side_effect=lambda url, **kw:
                              (github / url.rpartition('/')[2]).read_bytes()) as fetch:
            received = whole_getter.capability_metadata(online, mirrors, base='synthetic-fixture/')
        self.assertEqual(received['storage_evidence']['sha256'], root[name])
        self.assertEqual((online / name).read_bytes(), raw)
        self.assertEqual(fetch.call_args_list[-1].kwargs['max_bytes'], alpha.capabilities.STORAGE_LIMIT)
        (github / 'SHA256SUMS.sig').write_bytes(b'synthetic signature verified by test double')
        args = argparse.Namespace(release=alpha.RELEASE, disc='disc3', gpu_class='ada', source=str(github),
                                  ingestion=True, full=False, forge_data_root=str(self.root / 'data'),
                                  key='synthetic-public-key', fingerprint='A' * 40, gpg='')
        ids = ['core', 'embedder-ada', 'ingestion']
        identity = {'download_directory': str(github), 'media_components': [
            dict(component=component, size=self.sources[component].stat().st_size,
                 sha256=checksum(self.sources[component].read_bytes())) for component in ids]}
        info = b'{"OSType":"linux","Driver":"overlay2","DriverStatus":[],"DockerRootDir":"/synthetic/docker-root"}'
        observe = mock.Mock(return_value=('synthetic-volume', 10**9))
        with mock.patch.object(whole_getter.manifest, 'authenticate'), \
                mock.patch.object(forge_preflight.getter.manifest, 'authenticate'):
            budget, accepted = forge_preflight.signed_evidence(args, identity)
            self.assertEqual(accepted['storage_evidence_sha256'], root[name])
            storage = whole_getter.linux_forge_storage(args, info, observe=observe)
            self.assertEqual([row['needed_bytes'] for row in storage], [budget['data_bytes'], budget['docker_bytes']])
            observe.reset_mock()
            asset = github / name
            asset.write_bytes(raw + b' ')
            for corrupt in (True, False):
                if not corrupt:
                    asset.unlink()
                with self.assertRaises((ValueError, OSError, whole_getter.manifest.Refusal,
                                        whole_getter.transfer.DownloadError)):
                    whole_getter.linux_forge_storage(args, info, observe=observe)
                with self.assertRaises((ValueError, OSError, forge_preflight.getter.manifest.Refusal,
                                        forge_preflight.getter.transfer.DownloadError)):
                    forge_preflight.signed_evidence(args, identity)
            observe.assert_not_called()

    def test_detached_storage_refuses_undeclared_missing_and_wrong_child(self):
        raw = contracts.canonical(synthetic_storage(self.capability))
        with self.assertRaisesRegex(ValueError, 'without a capability binding'):
            alpha.capabilities.verify_detached_storage(self.capability, raw, checksum(raw))
        row = self.bind_storage(raw, discs=('disc2',))
        self.refused()
        row['discs'] = ['disc3']
        self.manifest['metadata'].remove(row)
        self.refused()
        self.manifest['metadata'].append(row)
        self.assemble()
        github = self.out / 'github'
        mirrors = contracts.strict_load(github / 'MIRRORS.json')
        child = github / contracts.mirror_child_names(mirrors)['disc3']
        child.write_bytes(b''.join(line for line in child.read_bytes().splitlines(keepends=True)
                                  if not line.endswith(b'  STORAGE-MEASUREMENTS.json\n')))
        root_path = github / 'SHA256SUMS'
        root = getter.read_sums(root_path)
        root[child.name] = checksum(child.read_bytes())
        root_path.write_text(''.join(f'{digest}  {name}\n' for name, digest in sorted(root.items())),
                             encoding='ascii', newline='\n')
        with self.assertRaisesRegex(getter.Refusal, 'Disc 3 checksum list only'):
            getter.validate_whole_release(github, require_signatures=False)
        (github / row['file']).unlink()
        root.pop(row['file'])
        root_path.write_text(''.join(f'{digest}  {name}\n' for name, digest in sorted(root.items())),
                             encoding='ascii', newline='\n')
        with self.assertRaisesRegex(ValueError, 'storage evidence differs'):
            getter.validate_whole_release(github, require_signatures=False)

    def test_detached_storage_rejects_rehashed_inconsistent_measurements(self):
        document = synthetic_storage(self.capability)
        raw = contracts.canonical(document)
        self.bind_storage(raw)
        subject = alpha.capabilities.verify_detached_storage
        subject(self.capability, raw, checksum(raw))
        mutations = {
            'backend': lambda v: v['measurements'][0].update(backend='unqualified'),
            'phase': lambda v: v['measurements'][0]['domains'][0]['allocated_bytes'].pop('import'),
            'method': lambda v: v['measurements'][0]['domains'][0].update(method='logical-image-size'),
            'figure': lambda v: v['measurements'][0]['domains'][0]['budget'].update(peak_bytes=1),
            'peak': lambda v: v['measurements'][0]['domains'][0]['allocated_bytes'].update(download=2**40),
            'coverage': lambda v: v['measurements'].pop(),
        }
        for name, mutate in mutations.items():
            candidate = copy.deepcopy(document)
            mutate(candidate)
            changed = contracts.canonical(candidate)
            digest = checksum(changed)
            self.capability['storage_evidence']['sha256'] = digest
            with self.subTest(mutation=name), self.assertRaises(ValueError):
                subject(self.capability, changed, digest)

    def test_detached_storage_accepts_raw_open_only_for_desktop_split_runtime(self):
        optional = contracts.strict_load(TOOLS / 'contracts/fixtures/capabilities/manifest.json')
        self.capability['bundles'].extend(row for row in optional['bundles']
                                          if row['id'] in ('full', 'source-build-full'))
        document = synthetic_storage(self.capability)
        original = contracts.canonical(document)
        self.bind_storage(original)
        capability = alpha.capabilities
        capability.verify_detached_storage(self.capability, original, checksum(original))
        split = {'core', 'ingestion', *('embedder-' + card for card in capability.GPU_CLASSES)}

        def check(candidate):
            raw = contracts.canonical(candidate)
            self.capability['storage_evidence']['sha256'] = checksum(raw)
            capability.verify_detached_storage(self.capability, raw, checksum(raw))

        def raw_open(row, *, only_first=False):
            for domain in row['domains'][:1] if only_first else row['domains']:
                sample = domain['allocated_bytes']
                sample['raw-open'] = sample.pop('mount')

        target = copy.deepcopy(document)
        for row in target['measurements']:
            if row['backend'] == 'docker-desktop' and row['bundle_id'] in split:
                raw_open(row)
        check(target)

        for backend, bundle in (('docker-engine', 'core'), ('docker-desktop', 'full'),
                                ('docker-desktop', 'source-build-core')):
            with self.subTest(backend=backend, bundle=bundle), self.assertRaises(ValueError):
                candidate = copy.deepcopy(document)
                raw_open(next(row for row in candidate['measurements'] if
                              row['backend'] == backend and row['bundle_id'] == bundle))
                check(candidate)

        for mutation in ('mixed domains', 'both phases', 'missing phases'):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                candidate = copy.deepcopy(document)
                row = next(row for row in candidate['measurements'] if
                           row['backend'] == 'docker-desktop' and row['bundle_id'] == 'core')
                sample = row['domains'][0]['allocated_bytes']
                if mutation == 'mixed domains':
                    raw_open(row, only_first=True)
                elif mutation == 'both phases':
                    sample['raw-open'] = sample['mount']
                else:
                    sample.pop('mount')
                check(candidate)

    def test_metadata_determinism_under_reordered_inputs(self):
        self.assemble()
        first = {p.name: p.read_bytes() for p in (self.out / 'github').iterdir()}
        self.out = self.root / 'again'
        self.manifest['images'].reverse()
        self.manifest['metadata'].reverse()
        self.index['subjects'].reverse()
        self.assemble()
        self.assertEqual(first, {p.name: p.read_bytes() for p in (self.out / 'github').iterdir()})

    def test_isolated_cli_and_merged_unsigned_preflight(self):
        result = subprocess.run([sys.executable, '-I', '-B', str(TOOLS / 'alpha2_assemble.py'),
                                 '--manifest', str(self.inputs / 'input.json'), '--output', str(self.out)],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
        self.assertTrue(result.stdout.startswith(b'Assembled '))
        result = subprocess.run([sys.executable, '-I', '-B', str(TOOLS / 'deep32_get_manifest.py'),
            'sign-preflight', '--release-dir', str(self.out / 'github'), '--scope', 'whole'],
            capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))

    def test_missing_image_row(self):
        self.manifest['images'].pop()
        self.refused()

    def test_extra_image_row(self):
        self.manifest['images'].append(copy.deepcopy(self.manifest['images'][-1]))
        self.refused()

    def test_extra_image_file(self):
        (self.images / 'unexpected.iso').write_bytes(b'SYNTHETIC EXTRA IMAGE')
        self.refused()

    def test_extra_nonimage_file(self):
        (self.images / 'unexpected.txt').write_bytes(b'SYNTHETIC EXTRA FILE')
        self.refused()

    def test_missing_image_file(self):
        self.sources['embedder-ada'].unlink()
        self.refused()

    def test_duplicate_image_id(self):
        self.manifest['images'][-1]['id'] = 'core'
        self.refused()

    def test_alias_source_hardlink(self):
        source = self.sources['embedder-ada']
        source.unlink()
        os.link(self.sources['core'], source)
        source.with_suffix('.iso.sha256').write_text(checksum(source.read_bytes()) + '  ' + source.name + '\n', encoding='ascii')
        self.refused()

    def test_alias_image_bytes(self):
        source = self.sources['embedder-ada']
        source.write_bytes(self.sources['core'].read_bytes())
        source.with_suffix('.iso.sha256').write_text(checksum(source.read_bytes()) + '  ' + source.name + '\n', encoding='ascii')
        self.refused()

    def test_changed_producer_bytes(self):
        self.sources['disc2'].write_bytes(b'SYNTHETIC CHANGED CONTENT')
        self.refused()

    def test_missing_sbom(self):
        (self.inputs / 'core.spdx.json').unlink()
        self.refused()

    def test_changed_sbom(self):
        path = self.inputs / 'core.spdx.json'
        path.write_bytes(path.read_bytes() + b'\n')
        self.refused()

    def test_index_image_alias(self):
        self.index['subjects'][0]['subject']['aliases'] = [dict(role='alias', path=self.sources['disc1'].name)]
        self.refused()

    def test_index_missing_image(self):
        self.index['subjects'].pop()
        self.refused()

    def test_index_extra_image(self):
        row = copy.deepcopy(self.index['subjects'][0])
        row['subject']['path'] = 'unexpected.img'
        row['sbom']['asset'] = 'unexpected.spdx.json'
        self.index['subjects'].append(row)
        self.refused()

    def test_index_hash_binding(self):
        self.index['subjects'][0]['subject']['sha256'] = '0' * 64
        self.refused()

    def test_embedded_final_sbom_refused(self):
        self.index['subjects'][0]['sbom']['media_paths'] = [dict(disc='disc1', path='sbom/final.spdx.json')]
        self.refused()

    def test_spdx_header(self):
        row = self.index['subjects'][0]
        path = self.inputs / row['sbom']['asset']
        doc = json.loads(path.read_bytes())
        doc['spdxVersion'] = 'SPDX-2.2'
        data = spdx_canonical(doc).encode('ascii')
        path.write_bytes(data)
        row['sbom']['sha256'] = checksum(data)
        self.refused()

    def test_valid_spdx_for_another_image_cannot_replace_final_root(self):
        row = self.index['subjects'][0]
        source = self.inputs / self.index['subjects'][1]['sbom']['asset']
        target = self.inputs / row['sbom']['asset']
        doc = json.loads(source.read_bytes())
        doc['documentNamespace'] = 'urn:uuid:00000000-0000-0000-0000-000000000001'
        data = spdx_canonical(doc).encode('ascii')
        target.write_bytes(data)
        row['sbom']['sha256'] = checksum(data)
        self.refused()

    def test_malformed_spdx_document_is_a_refusal(self):
        row = self.index['subjects'][0]
        target = self.inputs / row['sbom']['asset']
        target.write_bytes(b'[]\n')
        row['sbom']['sha256'] = checksum(target.read_bytes())
        self.refused()

    def comparison_fixture(self, *, proven=False):
        metadata = alpha.capabilities._module(TOOLS.parent / 'gpu-pack/build/alpha2_metadata.py',
                                              '_alpha2_assembly_test_metadata')
        stage = self.inputs / 'comparison-stage'
        payload = stage / 'payload'
        (payload / 'images').mkdir(parents=True)
        (payload / 'images/index.json').write_bytes(contracts.canonical([]))
        (stage / 'pins.json').write_bytes(contracts.canonical(dict(synthetic=True)))
        (stage / 'PAYLOAD.json').write_bytes(contracts.canonical(metadata.members(payload)))
        first = self.sources['core']
        second = self.inputs / 'second-core' / first.name
        second.parent.mkdir()
        second.write_bytes(first.read_bytes())
        epoch, revision = 946684800, 'a' * 40
        receipts = None
        if proven:
            identity = metadata.input_identity(stage, epoch, revision)
            toolchain = dict(platform='synthetic-linux', architecture='amd64',
                             tool_versions=dict(python='3.15.0', xorriso='1.5.6'))
            receipts = []
            for name, image in [('synthetic-run-a', first), ('synthetic-run-b', second)]:
                run = dict(format='deep32-alpha2-build-run/1', run_id=name, clean_build=True,
                           clean_build_method='Distinct synthetic fixture folders.',
                           cache_method='Synthetic fixtures do not use a build cache.',
                           input_manifest_sha256=identity['sha256'], recipe_sha256=metadata.recipe_identity(),
                           toolchain_sha256=metadata._hash(toolchain), **toolchain,
                           environment=dict(TZ='UTC', LC_ALL='C', umask='0022', SOURCE_DATE_EPOCH=epoch),
                           process_exit_results=dict(assembly=0),
                           verification=dict(data_only_iso=True, media_manifest=True, sbom_coverage=True),
                           output=dict(path=image.name, size=image.stat().st_size, sha256=checksum(image.read_bytes())))
                path = self.inputs / (name + '.json')
                path.write_bytes(contracts.canonical(run))
                receipts.append(path)
        folder = self.inputs / 'comparison'
        result = metadata.compare_core(first, second, folder, epoch, revision, stage, run_receipts=receipts)
        self.assertEqual(result['status'], 'proven' if proven else 'not-proven')
        receipt = folder / 'core-comparison.receipt.json'
        self.manifest['reproducibility'] = 'comparison/REPRODUCIBILITY.json'
        self.manifest['metadata'].append(dict(file=receipt.name, source='comparison/' + receipt.name, discs=['disc3']))
        return folder

    def test_core_comparison_expands_to_exact_signed_release_coverage(self):
        folder = self.comparison_fixture()
        self.assemble()
        github = self.out / 'github'
        public = contracts.strict_load(github / 'REPRODUCIBILITY.json')
        self.assertEqual(public['format'], 'deep32-reproducibility')
        self.assertEqual(len(public['subjects']), 11)
        self.assertTrue(all(r['status'] == 'not-proven' and not r['runs'] for r in public['subjects']))
        self.assertIn('REPRODUCIBILITY.json', getter.read_sums(github / 'SHA256SUMS'))
        self.assertIn('core-comparison.receipt.json', getter.read_sums(github / 'SHA256SUMS'))
        self.assertEqual((github / 'core-comparison.receipt.json').read_bytes(),
                         (folder / 'core-comparison.receipt.json').read_bytes())

    def test_proven_core_comparison_retains_two_runs_without_proving_other_images(self):
        self.comparison_fixture(proven=True)
        self.assemble()
        github = self.out / 'github'
        public = contracts.strict_load(github / 'REPRODUCIBILITY.json')
        rows = {r['subject']['path']: r for r in public['subjects']}
        self.assertEqual(len(rows), 11)
        core = rows.pop(self.sources['core'].name)
        self.assertEqual(core['status'], 'proven')
        self.assertTrue(core['byte_comparison'])
        self.assertEqual([r['id'] for r in core['runs']], ['synthetic-run-a', 'synthetic-run-b'])
        self.assertEqual(core['subject']['sha256'], checksum(self.sources['core'].read_bytes()))
        self.assertTrue(all(r['status'] == 'not-proven' and not r['runs'] and r['byte_comparison'] is None
                            for r in rows.values()))
        for row in rows.values():
            self.assertTrue(all(row[key] is None for key in
                                ('input_manifest_sha256', 'recipe_sha256', 'toolchain_sha256')))
        contracts.validate_contract_set({'sbom-index': contracts.strict_load(github / 'SBOM-INDEX.json'),
                                          'reproducibility': public})
        getter.validate_whole_release(github, scope='whole', require_signatures=False)

    def test_core_comparison_cannot_contradict_checksum_bound_receipt(self):
        folder = self.comparison_fixture()
        path = folder / 'REPRODUCIBILITY.json'
        document = contracts.strict_load(path)
        document['subjects'][0]['status'] = 'proven'
        path.write_bytes(contracts.canonical(document))
        self.sync()
        with self.assertRaisesRegex(ValueError, 'contradicts its checksum-bound comparison receipt'):
            alpha.assemble(self.inputs / 'input.json', self.out)
        self.assertFalse(self.out.exists())

    def test_core_comparison_receipt_digest_is_checked(self):
        folder = self.comparison_fixture(proven=True)
        path = folder / 'core-comparison.receipt.json'
        path.write_bytes(path.read_bytes() + b'\n')
        self.sync()
        with self.assertRaisesRegex(ValueError, 'comparison receipt checksum differs'):
            alpha.assemble(self.inputs / 'input.json', self.out)
        self.assertFalse(self.out.exists())

    def test_missing_contents_document(self):
        self.manifest['metadata'].pop()
        self.refused()

    def test_extra_full_runtime_template(self):
        fixture = contracts.strict_load(TOOLS / 'contracts' / 'fixtures' / 'valid' / 'mirrors-v2.json')
        self.mirrors['bundles'].append(fixture['bundles'][-1])
        self.refused()

    def test_exact_class_set(self):
        self.capability['hardware_classes'].pop()
        self.refused()

    def test_metadata_case_collision(self):
        row = copy.deepcopy(self.manifest['metadata'][0])
        row['file'] = row['file'].upper()
        self.manifest['metadata'].append(row)
        self.refused()

    def test_multiple_public_indexes_are_composed(self):
        other = copy.deepcopy(self.index)
        other['subjects'] = self.index['subjects'][5:]
        self.index['subjects'] = self.index['subjects'][:5]
        (self.inputs / 'other-index.json').write_bytes(contracts.canonical(other))
        self.manifest['sbom_indexes'].append('other-index.json')
        self.assemble()
        index = contracts.strict_load(self.out / 'github' / 'SBOM-INDEX.json')
        self.assertEqual(len(index['subjects']), 11)

    def test_existing_output_untouched(self):
        self.out.mkdir()
        (self.out / 'keep').write_bytes(b'SYNTHETIC USER DATA')
        with self.assertRaises(ValueError):
            alpha.assemble(self.inputs / 'input.json', self.out)
        self.assertEqual((self.out / 'keep').read_bytes(), b'SYNTHETIC USER DATA')

    def test_hardlink_failure_cleans_only_owned_output(self):
        with mock.patch.object(alpha.whole.os, 'link', side_effect=OSError('synthetic link refusal')):
            self.refused()
        self.assertTrue(all(p.exists() for p in self.sources.values()))

    def test_unknown_manifest_fields(self):
        self.manifest['unknown'] = True
        self.refused()

    def test_duplicate_manifest_json_keys(self):
        (self.inputs / 'input.json').write_bytes(b'{"format":1,"format":2}')
        with self.assertRaises(ValueError):
            alpha.assemble(self.inputs / 'input.json', self.out)
        self.assertFalse(self.out.exists())

    def media_fixture(self):
        capability = copy.deepcopy(self.capability)
        roots = {}
        pins = b'{"synthetic":true}\n'
        capability['pins_sha256'] = checksum(pins)
        for bundle in capability['bundles']:
            key = bundle['id']
            if key not in alpha.BUNDLES:
                continue
            media = self.inputs / 'media' / key
            media.mkdir(parents=True)
            roots[key] = media
            files = {}
            for image in bundle['images']:
                config = contracts.canonical(dict(architecture='amd64', os='linux',
                    rootfs=dict(type='layers', diff_ids=[]), config=dict(Labels=dict(synthetic=image['role']))))
                identity = checksum(config)
                config_name = identity + '.json'
                manifest = contracts.canonical([dict(Config=config_name, Layers=[], RepoTags=['synthetic:test'])])
                archive = io.BytesIO()
                with tarfile.open(fileobj=archive, mode='w', format=tarfile.USTAR_FORMAT) as output:
                    for name, data in [(config_name, config), ('manifest.json', manifest)]:
                        entry = tarfile.TarInfo(name)
                        entry.size = len(data)
                        output.addfile(entry, io.BytesIO(data))
                files[image['path']] = archive.getvalue()
                image.update(image_id='sha256:' + identity, config_digest='sha256:' + identity,
                             index_digest=None, manifest_digest=None, source_digest=None, archive_format='classic')
            for member in bundle['members']:
                name = member['path']
                data = files.get(name, ('SYNTHETIC ' + name + '\n').encode('ascii'))
                member.update(size=len(data), sha256=checksum(data))
                physical = 'deep32/' + name if name.startswith('kit/') else 'deep32/payload/' + name
                target = media / physical
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            index = contracts.canonical([dict(image, sha256=checksum(files[image['path']]))
                                         for image in bundle['images']])
            target = media / 'deep32/payload/images/index.json'
            target.write_bytes(index)
            for name, data in [('pins.json', pins), ('deep32/PAYLOAD.json', b'[]\n'),
                               ('deep32/PAYLOAD.SHA256', b'\n'), ('deep32/RUNTIME.json', b'{}\n')]:
                (media / name).write_bytes(data)
            for budget in bundle['storage_budgets']:
                for domain in budget['domains']:
                    if domain['domain'] == 'download':
                        domain['download_bytes'] = 1000000
                    elif domain['domain'] == 'data':
                        domain['retained_bytes'] = 1000000
                    domain['steady_bytes'] = sum(domain[k] for k in
                        ('retained_bytes', 'image_bytes', 'rollback_bytes', 'workspace_bytes'))
                    domain['peak_bytes'] = domain['steady_bytes'] + sum(domain[k] for k in
                        ('download_bytes', 'transient_bytes', 'headroom_bytes'))
            self.seal_media(media)
        for bundle in capability['bundles']:
            if bundle['id'] not in alpha.BUNDLES:
                for member in bundle['members']:
                    data = ('SYNTHETIC ' + member['path'] + '\n').encode('ascii')
                    member.update(size=len(data), sha256=checksum(data))
        contracts.validate_contract('capability-manifest', capability)
        return capability, roots

    def seal_media(self, media):
        (media / 'SHA256SUMS').write_text(''.join(checksum(path.read_bytes()) + '  ' +
            path.relative_to(media).as_posix() + '\n' for path in sorted(media.rglob('*'))
            if path.is_file() and path.name != 'SHA256SUMS'), encoding='ascii', newline='\n')

    def test_capabilities_bound_to_measured_component_media(self):
        capability, roots = self.media_fixture()
        result = alpha.validate_media(capability, roots)
        self.assertEqual(set(result['members']), set(alpha.BUNDLES))
        self.assertTrue(all(result['members'].values()))

    def test_resealed_wrong_media_bytes_cannot_use_placeholder_capabilities(self):
        capability, roots = self.media_fixture()
        media = roots['core']
        (media / 'deep32/payload/images/door.tar').write_bytes(b'SYNTHETIC WRONG ARCHIVE')
        self.seal_media(media)
        with self.assertRaisesRegex(ValueError, 'capability member differs'):
            alpha.validate_media(capability, roots)

    def test_extra_resealed_media_payload_refused(self):
        capability, roots = self.media_fixture()
        media = roots['embedder-ada']
        (media / 'deep32/payload/unrequested.bin').write_bytes(b'SYNTHETIC EXTRA PAYLOAD')
        self.seal_media(media)
        with self.assertRaisesRegex(ValueError, 'undeclared capability member'):
            alpha.validate_media(capability, roots)

    def test_media_capability_pins_binding(self):
        capability, roots = self.media_fixture()
        media = roots['core']
        (media / 'pins.json').write_bytes(b'{"synthetic":"changed"}\n')
        self.seal_media(media)
        with self.assertRaisesRegex(ValueError, 'media pins differ'):
            alpha.validate_media(capability, roots)


if __name__ == '__main__':
    unittest.main(verbosity=2)
