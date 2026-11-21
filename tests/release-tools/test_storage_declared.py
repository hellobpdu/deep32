#!/usr/bin/env python3
"""Declared storage budgets: contract, validator, readers and the visible label."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / 'src/release-tools'
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import capabilities
import contract_validate as contracts
import deep32_get_manifest as getter
import deep32_get_whole as whole_getter
import forge_preflight
import test_alpha2_assembly as assembly_tests
from test_alpha2_assembly import checksum, synthetic_storage

DECLARED = 'declared-upper-bound'
FORMULA = {'name': 'deep32-copy-path', 'version': 1}
GIB = 1024 ** 3
LABEL = '(declared upper bound; no measurement yet)'
BASH = 'C:/Program Files/Git/bin/bash.exe' if os.name == 'nt' else '/bin/bash'
POWERSHELL = 'C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def domains_of(backend):
    return ('download', 'data', 'docker') + (('desktop-backing',) if backend == 'docker-desktop' else ())


def declared_entries(bundle, backend, iso_bytes=40000, carrier_bytes=300):
    """Formula budgets and inputs for one bundle and backend of a synthetic manifest."""
    members = bundle['members']
    facts = dict(iso_bytes=iso_bytes, carrier_bytes=carrier_bytes,
                 member_bytes=sum(row['size'] for row in members),
                 image_archive_bytes=sum(row['size'] for row in members if row['category'] == 'image'),
                 member_slack_bytes=4096 * len(members))
    entries = []
    for domain in domains_of(backend):
        names = capabilities.declared_input_names(bundle['kind'], domain)
        budget = capabilities.declared_budget(bundle['kind'], backend, domain, {name: facts[name] for name in names})
        entries.append(dict(budget=budget, method=DECLARED, formula=dict(FORMULA),
                            inputs=[dict(name=name, bytes=facts[name], source='synthetic ' + name)
                                    for name in names]))
    return entries


def declare(capability, document, selected=lambda bundle, backend: True, **facts):
    """Replace the selected rows of a measured document with declared rows, in place."""
    rows = {(row['bundle_id'], row['backend']): row for row in document['measurements']}
    for bundle in capability['bundles']:
        for budget in bundle['storage_budgets']:
            if selected(bundle, budget['backend']):
                entries = declared_entries(bundle, budget['backend'], **facts)
                budget['domains'] = [copy.deepcopy(entry['budget']) for entry in entries]
                budget['method'] = DECLARED
                rows[bundle['id'], budget['backend']]['domains'] = entries
    return document


class DeclaredStorage(unittest.TestCase):
    def setUp(self):
        self.capability = contracts.strict_load(TOOLS / 'contracts/fixtures/capabilities/manifest.json')
        self.capability['bundles'] = [row for row in self.capability['bundles']
                                      if row['id'] not in ('full', 'source-build-full')]
        for bundle in self.capability['bundles']:
            for budget in bundle['storage_budgets']:
                budget['evidence'] = capabilities.STORAGE_ASSET
        self.document = declare(self.capability, synthetic_storage(self.capability))

    def check(self, document=None, **options):
        raw = contracts.canonical(self.document if document is None else document)
        self.capability['storage_evidence'] = dict(file=capabilities.STORAGE_ASSET, sha256=checksum(raw))
        capabilities.verify_detached_storage(self.capability, raw, checksum(raw), **options)
        return capabilities.validate_manifest(self.capability)

    def entry(self, document, bundle='core', backend='docker-engine', domain='docker'):
        row = next(row for row in document['measurements']
                   if (row['bundle_id'], row['backend']) == (bundle, backend))
        return next(entry for entry in row['domains'] if entry['budget']['domain'] == domain)

    def budget(self, bundle='core', backend='docker-engine', domain='docker'):
        row = next(row for row in self.capability['bundles'] if row['id'] == bundle)
        budget = next(row for row in row['storage_budgets'] if row['backend'] == backend)
        return next(row for row in budget['domains'] if row['domain'] == domain)

    def refused(self, message, mutate, **place):
        document = copy.deepcopy(self.document)
        mutate(self.entry(document, **place))
        with self.assertRaisesRegex(ValueError, message):
            self.check(document)

    def test_declared_rows_are_accepted_and_labeled_in_the_manifest(self):
        manifest = self.check()
        self.assertEqual(36, len(self.document['measurements']))
        for bundle in manifest['bundles']:
            for budget in bundle['storage_budgets']:
                self.assertEqual(DECLARED, budget['method'])
        for row in self.document['measurements']:
            for entry in row['domains']:
                self.assertEqual({'budget', 'method', 'formula', 'inputs'}, set(entry))
                self.assertEqual(DECLARED, entry['method'])

    def test_declared_row_without_formula_or_inputs_is_refused(self):
        self.refused('states its formula and inputs', lambda entry: entry.pop('formula'))
        self.refused('states its formula and inputs', lambda entry: entry.pop('inputs'))
        self.refused('lists exactly the inputs', lambda entry: entry.update(inputs=[]))
        self.refused('lists exactly the inputs', lambda entry: entry['inputs'].pop())
        self.refused('lists exactly the inputs', lambda entry: entry['inputs'][0].pop('source'))
        self.refused('lists exactly the inputs', lambda entry: entry['inputs'][0].update(source=''))
        self.refused('lists exactly the inputs', lambda entry: entry['inputs'][0].update(bytes=1.5))
        self.refused('lists exactly the inputs', lambda entry: entry['inputs'].reverse())
        self.refused('unknown formula', lambda entry: entry.update(formula=None))
        self.refused('unknown formula', lambda entry: entry['formula'].update(version=2))
        self.refused('unknown formula', lambda entry: entry['formula'].update(version=True))
        self.refused('unknown formula', lambda entry: entry['formula'].update(name='another-formula'))

    def test_declared_row_carries_no_trace_or_samples(self):
        self.refused('has no trace', lambda entry: entry.update(trace_sha256='a' * 64))
        self.refused('has no trace', lambda entry: entry.update(allocated_bytes={'baseline': 0}))

    def test_declared_row_below_the_sum_of_its_inputs_is_refused(self):
        entry = self.entry(self.document)
        stated = sum(row['bytes'] for row in entry['inputs'])
        self.assertGreater(stated, 0)
        low = dict.fromkeys(capabilities.BUDGET_FIELDS, 0)
        low.update(domain='docker', peak_bytes=stated - 1)
        self.budget().update(low)
        entry['budget'].update(low)
        with self.assertRaisesRegex(ValueError, 'below the sum of its stated inputs'):
            self.check()
        # The plain sum alone is still below the formula.
        self.budget().update(peak_bytes=stated)
        entry['budget'].update(peak_bytes=stated)
        with self.assertRaisesRegex(ValueError, 'below its formula'):
            self.check()

    def test_declared_row_may_be_raised_and_never_lowered(self):
        for key in ('image_bytes', 'transient_bytes', 'workspace_bytes', 'headroom_bytes', 'peak_bytes'):
            with self.subTest(lowered=key):
                document = copy.deepcopy(self.document)
                original = dict(self.budget())
                self.budget()[key] -= 1
                if key in ('image_bytes', 'workspace_bytes'):
                    self.budget()['steady_bytes'] -= 1
                self.entry(document)['budget'].update(self.budget())
                with self.assertRaisesRegex(ValueError, 'below its formula'):
                    self.check(document)
                self.budget().update(original)
        raised = self.budget()
        raised.update(headroom_bytes=raised['headroom_bytes'] + GIB, peak_bytes=raised['peak_bytes'] + GIB)
        self.entry(self.document)['budget'].update(raised)
        self.check()

    def test_declared_inputs_must_equal_the_bundle_and_the_observed_objects(self):
        for name in ('member_bytes', 'image_archive_bytes', 'member_slack_bytes'):
            def grow(entry, name=name):
                next(row for row in entry['inputs'] if row['name'] == name)['bytes'] += 1
            with self.subTest(input=name):
                self.refused('differs from the bundle', grow, domain='data')
        self.check(observed={'core': {'iso_bytes': 40000}, 'source-build-core': {'carrier_bytes': 300}})
        with self.assertRaisesRegex(ValueError, 'differs from the bundle'):
            self.check(observed={'core': {'iso_bytes': 40001}})
        with self.assertRaisesRegex(ValueError, 'differs from the bundle'):
            self.check(observed={'source-build-core': {'carrier_bytes': 299}})

    def test_measured_rows_still_work_and_both_labels_must_agree(self):
        for bundle in self.capability['bundles']:
            for budget in bundle['storage_budgets']:
                budget.pop('method')
        measured = synthetic_storage(self.capability)
        manifest = self.check(measured)
        self.assertFalse(any('method' in budget for bundle in manifest['bundles']
                             for budget in bundle['storage_budgets']))
        # A manifest label without declared rows, and declared rows without the label.
        self.capability['bundles'][0]['storage_budgets'][0]['method'] = DECLARED
        with self.assertRaisesRegex(ValueError, 'measured or declared as a whole'):
            self.check(measured)
        self.capability['bundles'][0]['storage_budgets'][0].pop('method')
        declared = declare(copy.deepcopy(self.capability), copy.deepcopy(measured),
                           lambda bundle, backend: bundle['id'] == 'core')
        with self.assertRaises(ValueError):
            self.check(declared)

    def test_one_row_cannot_mix_measured_and_declared_domains(self):
        measured = synthetic_storage(self.capability)
        document = copy.deepcopy(self.document)
        row = next(row for row in document['measurements']
                   if (row['bundle_id'], row['backend']) == ('core', 'docker-engine'))
        other = next(row for row in measured['measurements']
                     if (row['bundle_id'], row['backend']) == ('core', 'docker-engine'))
        row['domains'][0] = other['domains'][0]
        with self.assertRaisesRegex(ValueError, 'measured or declared as a whole'):
            self.check(document)

    def test_mixed_documents_are_accepted(self):
        for bundle in self.capability['bundles']:
            for budget in bundle['storage_budgets']:
                budget.pop('method')
        mixed = declare(self.capability, synthetic_storage(self.capability),
                        lambda bundle, backend: bundle['kind'] == 'embedder'
                        or (bundle['id'], backend) == ('core', 'docker-desktop'))
        manifest = self.check(mixed)
        labels = {(bundle['id'], budget['backend']): budget.get('method')
                  for bundle in manifest['bundles'] for budget in bundle['storage_budgets']}
        self.assertEqual(DECLARED, labels['embedder-turing', 'docker-engine'])
        self.assertEqual(DECLARED, labels['core', 'docker-desktop'])
        self.assertIsNone(labels['core', 'docker-engine'])
        methods = {entry['method'] for row in mixed['measurements'] for entry in row['domains']}
        self.assertEqual({DECLARED, 'allocated-filesystem-bytes', 'allocated-vhd-bytes'}, methods)
        selected = capabilities.select_runtime_bundles(manifest, 'turing', include_ingestion=True)
        discs = [dict(size=1, sha256='a' * 64) for _ in selected]
        self.assertEqual(DECLARED, whole_getter.forge_budget_document(selected, discs, 'turing')['basis'])

    def test_declared_budget_needs_the_detached_asset_and_a_known_label(self):
        self.check()
        self.capability.pop('storage_evidence')
        with self.assertRaisesRegex(ValueError, 'requires the detached storage asset'):
            capabilities.validate_manifest(self.capability)
        self.check()
        self.capability['bundles'][0]['storage_budgets'][0]['method'] = 'estimated'
        with self.assertRaises(ValueError):
            capabilities.validate_manifest(self.capability)

    def test_full_bundle_has_no_declared_formula(self):
        with self.assertRaisesRegex(ValueError, 'No declared storage formula'):
            capabilities.declared_input_names('full', 'docker')
        with self.assertRaisesRegex(ValueError, 'differ from the formula'):
            capabilities.declared_budget('core', 'docker-engine', 'desktop-backing',
                                         {'member_bytes': 2, 'image_archive_bytes': 1})
        with self.assertRaisesRegex(ValueError, 'differ from the formula'):
            capabilities.declared_budget('core', 'docker-engine', 'docker',
                                         {'member_bytes': 1, 'image_archive_bytes': 2})

    def test_readers_sum_declared_budgets_like_measured_ones_and_keep_the_label(self):
        manifest = self.check()
        selected = capabilities.select_runtime_bundles(manifest, 'ada', include_ingestion=True)
        discs = [dict(size=index, sha256=str(index) * 64) for index in (1, 2, 3)]
        for backend in ('docker-engine', 'docker-desktop'):
            with self.subTest(backend=backend):
                budget = whole_getter.forge_budget_document(selected, discs, 'ada', backend=backend)
                self.assertEqual(DECLARED, budget['basis'])
                for domain in domains_of(backend)[1:]:
                    expected = sum(row['peak_bytes'] for bundle in selected for item in bundle['storage_budgets']
                                   if item['backend'] == backend for row in item['domains']
                                   if row['domain'] == domain)
                    self.assertEqual(expected, budget[domain + '_bytes'])
        linux = whole_getter.forge_budget_document(selected, discs, 'ada')
        info = {'OSType': 'linux', 'Driver': 'overlay2', 'DriverStatus': [], 'DockerRootDir': '/synthetic/docker'}
        rows = forge_preflight.storage_checks(info, '/synthetic/data', linux, lambda path, existing: ('device', 7))
        self.assertEqual([
            {'filesystem_id': 'device', 'needed_bytes': linux['data_bytes'], 'free_bytes': 7, 'basis': DECLARED},
            {'filesystem_id': 'device', 'needed_bytes': linux['docker_bytes'], 'free_bytes': 7, 'basis': DECLARED},
        ], rows)
        windows = module('declared_windows_preflight', TOOLS / 'forge_preflight_windows.py')
        desktop = whole_getter.forge_budget_document(selected, discs, 'ada', backend='docker-desktop')
        rows = windows.physical_storage({'data_root': str(ROOT / 'synthetic-target')}, desktop,
                                        ROOT / 'docker_data.vhdx', 9, observe=lambda *a, **kw: ('volume', 8))
        self.assertEqual([DECLARED] * 3, [row['basis'] for row in rows])
        self.assertEqual([desktop['docker_bytes'], desktop['data_bytes'], desktop['desktop-backing_bytes']],
                         [row['needed_bytes'] for row in rows])
        # A measured selection keeps the earlier row shape.
        for bundle in selected:
            for budget in bundle['storage_budgets']:
                budget.pop('method')
        measured = whole_getter.forge_budget_document(selected, discs, 'ada')
        self.assertNotIn('basis', measured)
        rows = forge_preflight.storage_checks(info, '/synthetic/data', measured, lambda path, existing: ('device', 7))
        self.assertTrue(all(set(row) == {'filesystem_id', 'needed_bytes', 'free_bytes'} for row in rows))


class DeclaredAssembly(unittest.TestCase):
    """The assembler and both getter paths read a declared document like a measured one."""

    setUp = assembly_tests.Alpha2Assembly.setUp
    sync = assembly_tests.Alpha2Assembly.sync
    assemble = assembly_tests.Alpha2Assembly.assemble
    refused = assembly_tests.Alpha2Assembly.refused
    bind_storage = assembly_tests.Alpha2Assembly.bind_storage

    def declared_document(self, iso_bytes):
        """Declared rows with the given ISO lengths; a short synthetic ISO raises its download budget."""
        document = synthetic_storage(self.capability)
        rows = {(row['bundle_id'], row['backend']): row for row in document['measurements']}
        for bundle in self.capability['bundles']:
            members = sum(member['size'] for member in bundle['members'])
            for budget in bundle['storage_budgets']:
                entries = declared_entries(bundle, budget['backend'], iso_bytes=iso_bytes.get(bundle['id'], 40000))
                for entry in entries:
                    row = entry['budget']
                    if row['domain'] == 'download' and row['download_bytes'] < members:
                        # A declared budget may ask for more than its formula.
                        row['peak_bytes'] += members - row['download_bytes']
                        row['download_bytes'] = members
                budget['domains'] = [copy.deepcopy(entry['budget']) for entry in entries]
                budget['method'] = DECLARED
                rows[bundle['id'], budget['backend']]['domains'] = entries
        return contracts.canonical(document)

    def lengths(self):
        return {key: path.stat().st_size for key, path in self.sources.items()}

    def test_declared_document_is_assembled_and_read_by_both_getter_paths(self):
        raw = self.declared_document(self.lengths())
        name = self.bind_storage(raw)['file']
        self.assemble()
        github = self.out / 'github'
        mirrors = getter.validate_whole_release(github, scope='whole', require_signatures=False)
        self.assertEqual(raw, (github / name).read_bytes())
        online = self.root / 'online-metadata-fixture'
        online.mkdir()
        (online / 'SHA256SUMS').write_bytes((github / 'SHA256SUMS').read_bytes())
        with mock.patch.object(whole_getter, 'fetch_metadata', side_effect=lambda url, **kw:
                               (github / url.rpartition('/')[2]).read_bytes()):
            received = whole_getter.capability_metadata(online, mirrors, base='synthetic-fixture/')
        self.assertEqual({DECLARED}, {budget.get('method') for bundle in received['bundles']
                                      for budget in bundle['storage_budgets']})
        self.assertEqual(raw, (online / name).read_bytes())

    def test_declared_iso_length_must_match_the_published_image(self):
        lengths = self.lengths()
        lengths['embedder-hopper'] += 1
        self.bind_storage(self.declared_document(lengths))
        self.sync()
        with self.assertRaisesRegex(ValueError, 'differs from the bundle it describes'):
            assembly_tests.alpha.assemble(self.inputs / 'input.json', self.out)
        self.assertFalse(self.out.exists())


class CopyPathFormula(unittest.TestCase):
    """Formula version 1 against the sealed loader's gate and the pilot observations."""

    # Byte counts of the original media: members, image archives, member count, ISO length.
    FACTS = {'core': (14653521196, 3834525184, 148, 14654439424),
             'embedder': (3147036106, 3055452160, 5, 3147628544),
             'ingestion': (8561666922, 5154231808, 90, 8562475008)}

    def budget(self, kind, backend, domain):
        members, archives, count, iso = self.FACTS[kind]
        facts = dict(member_bytes=members, image_archive_bytes=archives, member_slack_bytes=4096 * count,
                     iso_bytes=iso)
        names = capabilities.declared_input_names(kind, domain)
        return capabilities.declared_budget(kind, backend, domain, {name: facts[name] for name in names})

    def test_budget_arithmetic_holds_for_every_row(self):
        for kind in self.FACTS:
            for backend in ('docker-engine', 'docker-desktop'):
                for domain in domains_of(backend):
                    row = self.budget(kind, backend, domain)
                    steady = sum(row[key] for key in ('retained_bytes', 'image_bytes', 'rollback_bytes',
                                                      'workspace_bytes'))
                    self.assertEqual(steady, row['steady_bytes'])
                    self.assertGreaterEqual(row['peak_bytes'], steady + sum(
                        row[key] for key in ('download_bytes', 'transient_bytes', 'headroom_bytes')))

    def test_no_peak_is_below_the_sealed_loader_gate(self):
        check = module('declared_host_requirements', ROOT / 'src/gpu-pack/forms/host-requirements.py')
        with tempfile.TemporaryDirectory(prefix='declared-gate-') as temporary:
            payload = Path(temporary) / 'payload'
            (payload / 'images').mkdir(parents=True)
            (payload / 'models').mkdir()
            (payload / 'images/one.tar').write_bytes(b'i' * 3000)
            (payload / 'images/two.tar').write_bytes(b'j' * 2000)
            (payload / 'models/weights.bin').write_bytes(b'm' * 9000)
            asked = []

            class Free:
                free = 2 ** 62

            def usage(path):
                asked.append(path)
                return Free

            gate = check.check_storage(payload, Path(temporary), Path(temporary) / 'docker', usage,
                                       target=Path(temporary) / 'data')
        self.assertEqual(14000 + 2 * 5000 + GIB, gate)
        self.assertEqual(2, len(asked))
        facts = {'member_bytes': 14000, 'image_archive_bytes': 5000}
        for kind in ('core', 'embedder', 'ingestion'):
            linux = capabilities.declared_budget(kind, 'docker-engine', 'docker', facts)
            guest = capabilities.declared_budget(kind, 'docker-desktop', 'docker', facts)
            backing = capabilities.declared_budget(kind, 'docker-desktop', 'desktop-backing', facts)
            data = capabilities.declared_budget(kind, 'docker-engine', 'data', dict(facts, member_slack_bytes=12288))
            self.assertGreaterEqual(linux['peak_bytes'], gate)
            self.assertGreaterEqual(backing['peak_bytes'], gate)
            # The Desktop guest must still have the gate free after the images are loaded.
            self.assertGreaterEqual(guest['peak_bytes'], guest['image_bytes'] + gate)
            # The data gate is checked after the joined cache is written.
            self.assertGreaterEqual(data['peak_bytes'], 14000 + gate)

    def test_formula_covers_the_pilot_observations(self):
        # Growth of the classic overlay2 store on ext4 for each bundle's loads, in the order core,
        # turing, ingestion, Docker 29.1.3 (2026-11-18, linux-storage-turing-chain-r1/imports.csv).
        store = {'core': 6843604992, 'embedder': 4831350784, 'ingestion': 11062935552}
        # Docker Desktop virtual disk growth per bundle, same day.
        disk = {'core': 10267656192, 'embedder': 6677331968, 'ingestion': 12046041088}
        for kind in self.FACTS:
            linux = self.budget(kind, 'docker-engine', 'docker')
            backing = self.budget(kind, 'docker-desktop', 'desktop-backing')
            self.assertGreaterEqual(linux['image_bytes'], 1.5 * store[kind])
            self.assertGreaterEqual(backing['image_bytes'] + backing['transient_bytes'], 1.5 * disk[kind])
        # Joined cache of the three components: 26,085,388,288 bytes allocated on ext4; the
        # retained model files are 13,947,649,630 bytes by member size.
        cache = sum(self.budget(kind, 'docker-engine', 'data')['retained_bytes'] for kind in self.FACTS)
        self.assertGreaterEqual(cache, 26085388288 + 13947649630)
        # Linux peak while loading: the store plus one archive in Docker's temporary directory.
        self.assertGreaterEqual(sum(self.budget(kind, 'docker-engine', 'docker')['peak_bytes']
                                    for kind in self.FACTS), 2 * 27035983872)

    def test_source_companion_rows_cover_the_pilot_allocations(self):
        # Carrier bytes, member bytes, member count; then the allocations the pilot saw.
        pilots = {'core': ((300745, 758670, 56), 1191936, 888832),
                  'embedder-turing': ((1218324, 5140758, 298), 7036928, 5812224),
                  'ingestion': ((192411504, 228141600, 2600), 447383816, 233992192)}
        kind = 'corresponding-source-build'
        for name, ((carriers, members, count), combined, unpacked) in pilots.items():
            with self.subTest(companion=name):
                facts = dict(carrier_bytes=carriers, member_bytes=members, member_slack_bytes=4096 * count)
                download = capabilities.declared_budget(kind, 'docker-desktop', 'download', facts)
                data = capabilities.declared_budget(kind, 'docker-engine', 'data', {
                    key: facts[key] for key in ('member_bytes', 'member_slack_bytes')})
                self.assertGreaterEqual(download['download_bytes'], members)
                self.assertGreaterEqual(download['peak_bytes'], combined)
                self.assertGreaterEqual(data['peak_bytes'], unpacked)
                for backend in ('docker-engine', 'docker-desktop'):
                    for domain in domains_of(backend)[2:]:
                        self.assertEqual(0, capabilities.declared_budget(
                            kind, backend, domain, {'image_archive_bytes': 0})['peak_bytes'])


class DeclaredLabelInTheInventory(unittest.TestCase):
    """Both inventory implementations print the label on the storage line."""

    ROWS = [{'filesystem_id': 'synthetic-a', 'needed_bytes': 50, 'free_bytes': 100, 'basis': DECLARED},
            {'filesystem_id': 'synthetic-a', 'needed_bytes': 20, 'free_bytes': 90},
            {'filesystem_id': 'synthetic-b', 'needed_bytes': 20, 'free_bytes': 90}]

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='declared-inventory-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)

    def implementations(self):
        yield 'sh', [BASH, str(ROOT / 'install/deep32-inventory.sh')]
        if os.name == 'nt':
            yield 'ps1', [POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                          str(ROOT / 'install/deep32-inventory.ps1')]

    def run_inventory(self, rows, *options):
        fixture = self.work / 'synthetic-facts.json'
        fixture.write_text(json.dumps({'facts': {'os': {'family': 'linux'}, 'storage_plan': rows}}),
                           encoding='ascii')
        results = {}
        for name, prefix in self.implementations():
            result = subprocess.run(
                prefix + ['--check-prereqs', '--journey', 'J3', '--role', 'gpu-host', *options,
                          '--facts', str(fixture)], cwd=self.work, capture_output=True, text=True, timeout=60)
            self.assertEqual(10, result.returncode, result.stdout + result.stderr)
            self.assertEqual('', result.stderr)
            results[name] = result.stdout
        return results

    def test_declared_rows_are_checked_and_labeled(self):
        documents = []
        for name, output in self.run_inventory(self.ROWS, '--json').items():
            with self.subTest(implementation=name):
                document = json.loads(output)
                self.assertEqual([
                    {'filesystem': 'filesystem-1', 'needed_bytes': 70, 'free_bytes': 90, 'basis': DECLARED},
                    {'filesystem': 'filesystem-2', 'needed_bytes': 20, 'free_bytes': 90},
                ], document['storage_plan'])
                row = next(row for row in document['prerequisites'] if row['id'] == 'storage.plan')
                self.assertEqual('met', row['state'])
                self.assertTrue(row['need'].endswith(' ' + LABEL), row['need'])
                document.pop('impl')
                documents.append(document)
        self.assertTrue(all(document == documents[0] for document in documents))
        for name, output in self.run_inventory(self.ROWS).items():
            with self.subTest(implementation=name, text=True):
                line = [line for line in output.splitlines() if line.startswith('storage.plan=')]
                self.assertEqual(1, len(line))
                self.assertTrue(line[0].startswith('storage.plan=met: '))
                self.assertTrue(line[0].endswith(' ' + LABEL), line[0])

    def test_a_declared_budget_that_does_not_fit_is_missing_like_a_measured_one(self):
        rows = [dict(self.ROWS[0], needed_bytes=101)]
        for name, output in self.run_inventory(rows, '--json').items():
            with self.subTest(implementation=name):
                document = json.loads(output)
                row = next(row for row in document['prerequisites'] if row['id'] == 'storage.plan')
                self.assertEqual('missing', row['state'])
                self.assertEqual(DECLARED, document['storage_plan'][0]['basis'])

    def test_measured_rows_keep_the_plain_line_and_an_unknown_label_is_not_a_budget(self):
        measured = [{key: value for key, value in row.items() if key != 'basis'} for row in self.ROWS]
        for name, output in self.run_inventory(measured).items():
            with self.subTest(implementation=name):
                line = next(line for line in output.splitlines() if line.startswith('storage.plan='))
                self.assertNotIn('declared', line)
        for basis in ('estimated', 7, None):
            for name, output in self.run_inventory([dict(self.ROWS[0], basis=basis)], '--json').items():
                with self.subTest(implementation=name, basis=basis):
                    document = json.loads(output)
                    row = next(row for row in document['prerequisites'] if row['id'] == 'storage.plan')
                    self.assertEqual('unknown', row['state'])
                    self.assertEqual([], document['storage_plan'])
                    self.assertNotIn('declared', row['need'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
