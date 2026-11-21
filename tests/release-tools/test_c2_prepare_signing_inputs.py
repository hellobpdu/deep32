#!/usr/bin/env python3
"""Synthetic offline tests for the C2 signing input helper and the declared storage generator.

Adapted from the T-0241 owner helper tests (work/codex/T-0241/test-c2-prepare-signing-inputs.py).
The measured cases are kept; the declared cases run storage_declare.py on the same synthetic
producer tree and feed its document to the helper without a trace catalog.
"""

import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / 'src/release-tools'
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(TOOLS))
from test_alpha2_assembly import synthetic_storage
from test_capabilities import fixture, sha
import alpha2_assemble as alpha
import capabilities
import contract_validate as contracts

DECLARED = 'declared-upper-bound'


class PrepareSigningInputs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='c2-helper-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / 'unsigned'
        cap, pins, aggregate, payloads = fixture(include_full=False)
        self.documents = {
            'release-record': dict(source_commit=cap['source_commit'], model_licences={
                m['role']: m['licence_path'] for b in cap['bundles'] for m in b['models']}),
            'runtime-layout': dict(format='deep32-runtime-input-facts/1', release=alpha.VERSION, bundles=[]),
            'companion-inputs': dict(format='deep32-source-build-owner-staging/1', release=alpha.RELEASE,
                                     carriers=[], companions=[]),
            'image-index': aggregate,
            'source-payload': [dict(path='images/index.json', size=len(contracts.canonical(aggregate)),
                                    sha256=cap['image_index_sha256'])],
        }
        self.aggregate = {row['role']: row for row in aggregate}
        self.pins = contracts.canonical(pins)
        self.source_hash = sha(contracts.canonical(self.documents['source-payload']))
        self.cap = cap
        self.native = self.root / 'components'
        self.carriers = self.root / 'source-build'
        self.carriers.mkdir()
        self.pack(cap['bundles'], payloads)
        self.documents['storage-evidence'] = synthetic_storage(cap)
        traces = {}
        for row in self.documents['storage-evidence']['measurements']:
            for domain in row['domains']:
                name = domain['budget']['domain']
                raw = f"synthetic trace for {row['bundle_id']} {row['backend']} {name}".encode('ascii')
                digest = domain['trace_sha256']
                self.assertEqual(sha(raw), digest)
                path = self.root / (digest + '.trace')
                path.write_bytes(raw)
                traces[digest] = dict(sha256=digest, path=path.name)
        self.documents['trace-catalog'] = dict(format='deep32-physical-storage-traces/1',
                                               traces=list(traces.values()))
        for key in self.documents:
            self.write(key)

    def write(self, key):
        (self.root / (key + '.json')).write_bytes(contracts.canonical(self.documents[key]))

    def pack(self, bundles, payloads):
        source_members = {}
        for bundle in bundles:
            name = bundle['id']
            payload = payloads[name]
            if bundle['kind'] == 'corresponding-source-build':
                source_members.update(payload)
                rows = [dict(path=member, entry=member) for member in sorted(payload)]
                self.documents['companion-inputs']['companions'].append(
                    dict(id=name, members=bundle['members'], bindings=rows))
                continue
            layout = dict(id=name, models=[{k: v for k, v in model.items() if k != 'licence_path'}
                                          for model in bundle['models']],
                          members=[dict(row, size=0, sha256='f' * 64) for row in bundle['members']])
            self.documents['runtime-layout']['bundles'].append(layout)
            native = self.native / name / 'media'
            files = {('deep32/' if path.startswith('kit/') else 'deep32/payload/') + path: raw
                     for path, raw in payload.items()}
            # Like the real media, the native index omits source_digest where it has none.
            files['deep32/payload/images/index.json'] = contracts.canonical(
                [{key: value for key, value in self.aggregate[row['role']].items()
                  if key != 'source_digest' or value is not None} for row in bundle['images']])
            prefix = 'deep32/payload/'
            inventory = [dict(path=path.removeprefix(prefix), size=len(raw), sha256=sha(raw))
                         for path, raw in sorted(files.items()) if path.startswith(prefix)]
            files['deep32/PAYLOAD.json'] = contracts.canonical(inventory)
            files['deep32/PAYLOAD.SHA256'] = ''.join(
                f"{row['sha256']}  {row['path']}\n" for row in inventory).encode('ascii')
            files['pins.json'] = self.pins
            files['deep32/RUNTIME.json'] = contracts.canonical(dict(format='deep32-runtime-stage/1',
                component=bundle['kind'], gpu_class=bundle['hardware_class'], source_date_epoch=946684800,
                pins_sha256=sha(self.pins), source_payload_sha256=self.source_hash,
                allowlist_sha256='5' * 64, roles=sorted(row['role'] for row in bundle['images']),
                payload_sha256=sha(files['deep32/PAYLOAD.json'])))
            files['SHA256SUMS'] = ''.join(
                f'{sha(raw)}  {path}\n' for path, raw in sorted(files.items())).encode('ascii')
            for path, raw in files.items():
                target = native / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)

        memory = io.BytesIO()
        with tarfile.open(fileobj=memory, mode='w:gz') as archive:
            for path, raw in sorted(source_members.items()):
                info = tarfile.TarInfo(path)
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
        raw = memory.getvalue()
        digest = sha(raw)
        (self.carriers / (digest + '.tar.gz')).write_bytes(raw)
        source = self.documents['companion-inputs']
        source['carriers'] = [dict(sha256=digest, size=len(raw))]
        for row in source['companions']:
            for member in row['bindings']:
                member['carrier_sha256'] = digest

    def invoke(self, *extra, check=False):
        args = [sys.executable, '-I', '-B', str(TOOLS / 'c2_prepare_signing_inputs.py'),
                '--tools', str(TOOLS), '--components', str(self.native), '--source-carriers', str(self.carriers)]
        for key in self.documents:
            args.extend(('--' + key, str(self.root / (key + '.json'))))
        args.extend(extra)
        args.extend(['--check'] if check else ['--output', str(self.out)])
        return subprocess.run(args, capture_output=True, text=True, check=False)

    def refused(self, message, *extra):
        result = self.invoke(*extra)
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn(message, result.stderr)
        self.assertFalse(self.out.exists())
        self.assertFalse(list(self.root.glob('.c2-inputs-*')))

    # Declared storage rows from the generator.

    def images(self):
        """Synthetic component ISO files, each at least as long as its members."""
        folder = self.root / 'images'
        if not folder.exists():
            folder.mkdir()
            for bundle in self.cap['bundles']:
                if bundle['kind'] != 'corresponding-source-build':
                    size = sum(member['size'] for member in bundle['members']) + 2048
                    (folder / alpha.image_name(bundle['id'])).write_bytes(b'\0' * size)
        return folder

    def declare(self, *extra, name='declared'):
        """Run storage_declare.py on the synthetic producer and use its document as the storage evidence."""
        output, summary = self.root / (name + '.json'), self.root / (name + '-summary.json')
        result = subprocess.run(
            [sys.executable, '-I', '-B', str(TOOLS / 'storage_declare.py'), '--components', str(self.native),
             '--images', str(self.images()), '--image-index', str(self.root / 'image-index.json'),
             '--companion-inputs', str(self.root / 'companion-inputs.json'), '--output', str(output),
             '--summary', str(summary), *extra], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            self.documents['storage-evidence'] = contracts.strict_load(output)
            self.write('storage-evidence')
        return result

    def test_generator_document_is_accepted_without_traces(self):
        result = self.declare()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('rows: 36 declared, 0 measured', result.stdout)
        self.documents.pop('trace-catalog')
        emitted = self.invoke('--images', str(self.images()))
        self.assertEqual(0, emitted.returncode, emitted.stderr)
        prepared = contracts.strict_load(self.out / 'CAPABILITIES.json')
        self.assertEqual({DECLARED}, {budget.get('method') for bundle in prepared['bundles']
                                      for budget in bundle['storage_budgets']})
        self.assertEqual((self.root / 'declared.json').read_bytes(),
                         (self.out / 'STORAGE-MEASUREMENTS.json').read_bytes())
        # An image built here has no upstream source digest; the capability states null.
        core = next(bundle for bundle in prepared['bundles'] if bundle['id'] == 'core')
        self.assertIn(None, [row['source_digest'] for row in core['images']])
        # Every declared row states the formula's least value for its own inputs.
        for row in self.documents['storage-evidence']['measurements']:
            bundle = next(bundle for bundle in prepared['bundles'] if bundle['id'] == row['bundle_id'])
            for entry in row['domains']:
                inputs = {item['name']: item['bytes'] for item in entry['inputs']}
                self.assertEqual(capabilities.declared_budget(bundle['kind'], row['backend'],
                                                              entry['budget']['domain'], inputs), entry['budget'])
        summary = contracts.strict_load(self.root / 'declared-summary.json')
        self.assertEqual({DECLARED}, {row['basis'] for row in summary['forge']})
        self.assertEqual(14, len(summary['forge']))

    def test_declared_iso_length_must_match_the_image_file(self):
        self.assertEqual(0, self.declare().returncode)
        self.documents.pop('trace-catalog')
        with (self.images() / alpha.image_name('embedder-ada')).open('ab') as stream:
            stream.write(b'\0' * 2048)
        self.refused('differs from the bundle it describes', '--images', str(self.images()))

    def test_lowered_declared_budget_is_refused(self):
        self.assertEqual(0, self.declare().returncode)
        self.documents.pop('trace-catalog')
        entry = self.documents['storage-evidence']['measurements'][0]['domains'][2]['budget']
        for key in ('image_bytes', 'steady_bytes', 'peak_bytes'):
            entry[key] -= 1
        self.write('storage-evidence')
        self.refused('below its formula')

    def test_mixed_document_keeps_measured_rows_and_their_traces(self):
        measured = self.documents['storage-evidence']
        measured['measurements'] = [row for row in measured['measurements'] if row['bundle_id'] == 'core']
        self.write('storage-evidence')
        result = self.declare('--measured', str(self.root / 'storage-evidence.json'), name='mixed')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('rows: 34 declared, 2 measured', result.stdout)
        kept = {entry['trace_sha256'] for row in measured['measurements'] for entry in row['domains']}
        catalog = self.documents['trace-catalog']
        catalog['traces'] = [row for row in catalog['traces'] if row['sha256'] in kept]
        self.write('trace-catalog')
        emitted = self.invoke('--images', str(self.images()))
        self.assertEqual(0, emitted.returncode, emitted.stderr)
        prepared = contracts.strict_load(self.out / 'CAPABILITIES.json')
        labels = {(bundle['id'], budget['backend']): budget.get('method')
                  for bundle in prepared['bundles'] for budget in bundle['storage_budgets']}
        self.assertEqual({None}, {labels['core', backend] for backend in ('docker-engine', 'docker-desktop')})
        self.assertEqual(DECLARED, labels['ingestion', 'docker-desktop'])
        self.documents.pop('trace-catalog')
        shutil.rmtree(self.out)
        self.refused('Measured storage rows require the trace catalog')

    def test_generator_refuses_media_that_differ_from_their_lists(self):
        (self.native / 'core/media/unlisted.txt').write_bytes(b'synthetic extra')
        result = self.declare()
        self.assertEqual(2, result.returncode)
        self.assertIn('name different files: core', result.stderr)
        self.assertFalse((self.root / 'declared.json').exists())

    def test_generator_refuses_a_payload_inventory_that_differs_from_the_files(self):
        media = self.native / 'ingestion/media'
        inventory = json.loads((media / 'deep32/PAYLOAD.json').read_bytes())
        inventory[0]['size'] += 1
        (media / 'deep32/PAYLOAD.json').write_bytes(contracts.canonical(inventory))
        result = self.declare()
        self.assertEqual(2, result.returncode)
        self.assertIn('payload inventory differs from the media files: ingestion', result.stderr)

    def test_generator_keeps_an_existing_output(self):
        (self.root / 'declared.json').write_bytes(b'unchanged fixture')
        result = self.declare()
        self.assertEqual(2, result.returncode)
        self.assertEqual(b'unchanged fixture', (self.root / 'declared.json').read_bytes())

    # Measured storage rows, as in the T-0241 helper tests.

    def test_actual_validator_pipeline_and_determinism(self):
        checked = self.invoke(check=True)
        self.assertEqual(0, checked.returncode, checked.stderr)
        self.assertFalse(self.out.exists())
        emitted = self.invoke()
        self.assertEqual(0, emitted.returncode, emitted.stderr)
        self.assertEqual(checked.stdout, emitted.stdout)
        names = {'CAPABILITIES.json', 'SOURCE-BUILD-CATALOG.json', 'STORAGE-MEASUREMENTS.json'}
        self.assertEqual(names, {p.name for p in self.out.iterdir()})
        prepared = contracts.strict_load(self.out / 'CAPABILITIES.json')
        actual = {b['id']: b for b in prepared['bundles']}
        self.assertEqual(18, len(actual))
        for original in self.cap['bundles']:
            self.assertEqual({m['path']: m for m in original['members']},
                             {m['path']: m for m in actual[original['id']]['members']})
        self.assertFalse(any('method' in budget for bundle in prepared['bundles']
                             for budget in bundle['storage_budgets']))
        storage = (self.root / 'storage-evidence.json').read_bytes()
        self.assertEqual(storage, (self.out / 'STORAGE-MEASUREMENTS.json').read_bytes())
        catalog = contracts.strict_load(self.out / 'SOURCE-BUILD-CATALOG.json')
        self.assertEqual(sha((self.out / 'CAPABILITIES.json').read_bytes()), catalog['capability_sha256'])
        self.assertEqual(9, len(catalog['companions']))

    def test_missing_backend_refused(self):
        self.documents['storage-evidence']['measurements'].pop()
        self.write('storage-evidence')
        self.refused('Missing bundle/backend storage evidence')

    def test_missing_trace_registration_refused(self):
        self.documents['trace-catalog']['traces'].pop()
        self.write('trace-catalog')
        self.refused('exactly the referenced trace digests')

    def test_wrong_trace_bytes_refused(self):
        row = self.documents['trace-catalog']['traces'][0]
        (self.root / row['path']).write_bytes(b'changed synthetic allocation trace')
        self.refused('Storage trace differs')

    def test_measured_rows_without_the_trace_catalog_refused(self):
        self.documents.pop('trace-catalog')
        self.refused('Measured storage rows require the trace catalog')

    def test_stale_aggregate_inventory_refused(self):
        self.documents['source-payload'][0]['sha256'] = 'a' * 64
        self.write('source-payload')
        self.refused('must bind the exact aggregate image index')

    def test_stale_release_measurement_refused(self):
        self.documents['storage-evidence']['pins_sha256'] = 'f' * 64
        self.write('storage-evidence')
        self.refused('Storage evidence must bind the final pins')

    def test_unlisted_media_refused(self):
        extra = self.native / 'core/media/unlisted.txt'
        extra.write_bytes(b'synthetic extra')
        self.refused('does not cover the exact media bytes')

    def test_missing_carrier_refused(self):
        next(self.carriers.iterdir()).unlink()
        self.refused('missing or extra entries')

    def test_existing_output_preserved(self):
        self.out.mkdir()
        keep = self.out / 'sentinel'
        keep.write_bytes(b'unchanged fixture')
        result = self.invoke()
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn('fresh output directory', result.stderr)
        self.assertEqual(b'unchanged fixture', keep.read_bytes())


if __name__ == '__main__':
    unittest.main(verbosity=2)
