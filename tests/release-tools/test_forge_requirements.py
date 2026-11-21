#!/usr/bin/env python3
"""Offline signed-prerequisite asset builder and validation contract."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[2] / 'src/release-tools'
spec = importlib.util.spec_from_file_location('synthetic_forge_requirement_builder', TOOLS / 'forge_requirements.py')
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


def canonical(value):
    return (json.dumps(value, sort_keys=True) + '\n').encode('ascii')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class ForgeRequirements(unittest.TestCase):
    def setUp(self):
        self.floors = {'tei_minimum_driver': '810.2', 'cuda_minimum_driver': '810.100',
                       'mineru_minimum_driver': '805.0'}
        self.cap = json.loads((TOOLS / 'contracts/fixtures/capabilities/manifest.json').read_bytes())
        self.pins = canonical({'runtime': self.floors})
        self.cap['pins_sha256'] = sha(self.pins)
        self.records = [dict(id='synthetic-ubuntu-j2', accepted_on='2026-11-18', os_id='ubuntu',
                             os_version='24.04', architecture='amd64', journey='J2',
                             recipe_sha256='b' * 64, acceptance_sha256='c' * 64)]

    def build(self):
        return subject.build(canonical(self.cap), self.pins, canonical(self.records))

    def verify(self, raw):
        return subject.validate(raw, capability_sha256=sha(canonical(self.cap)),
                                pins_sha256=sha(self.pins), release=self.cap['release'],
                                storage_evidence_sha256=self.cap.get('storage_evidence', {}).get('sha256'))

    def test_exact_resolved_floor_release_bindings_and_memory_policy(self):
        raw = self.build()
        doc = self.verify(raw)
        self.assertEqual(doc['driver_floors'], self.floors)
        self.assertEqual(max(self.floors.values(), key=subject.version_tuple), '810.100')
        self.assertEqual(subject.version_tuple('810.100.0'), (810, 100, 0, 0))
        self.assertEqual(doc['minimum_vram_mib'], 10752)
        self.assertEqual(doc['recipes'], self.records)
        self.assertEqual(raw, self.build())

    def test_detached_storage_digest_copied_and_enforced(self):
        asset = 'STORAGE-MEASUREMENTS.json'
        expected = sha(b'synthetic detached measurements')
        self.cap['storage_evidence'] = dict(file=asset, sha256=expected)
        for bundle in self.cap['bundles']:
            for budget in bundle['storage_budgets']:
                budget['evidence'] = asset
        document = self.verify(self.build())
        self.assertEqual(document['storage_evidence_sha256'], expected)
        with self.assertRaisesRegex(ValueError, 'signed storage measurements'):
            self.verify(canonical(dict(document, storage_evidence_sha256='d' * 64)))
        document.pop('storage_evidence_sha256')
        with self.assertRaises(ValueError):
            self.verify(canonical(document))

    def test_missing_unresolved_or_different_pins_are_refused(self):
        for sample in (dict(self.floors, mineru_minimum_driver='TO-PIN'),
                       dict(self.floors, cuda_minimum_driver=True),
                       {'tei_minimum_driver': self.floors['tei_minimum_driver']}):
            with self.subTest(sample=sample):
                self.pins = canonical({'runtime': sample})
                self.cap['pins_sha256'] = sha(self.pins)
                with self.assertRaises(ValueError):
                    self.build()
        self.pins = canonical({'runtime': self.floors})
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.build()

    def test_absent_bad_duplicate_or_unreviewed_recipe_fields_are_refused(self):
        row = self.records[0]
        for records in ([], [dict(row, accepted_on='2026-02-30')], [dict(row, acceptance_sha256='')],
                        [dict(row, journey='J3')], [dict(row, architecture='arm64')],
                        [row, dict(row, id='synthetic-other')], [dict(row, accepted=True)]):
            with self.subTest(records=records):
                self.records = records
                with self.assertRaises(ValueError):
                    self.build()

    def add_windows(self):
        self.pins = canonical({'runtime': dict(self.floors, windows_cuda13_driver_build='820.0')})
        self.cap['pins_sha256'] = sha(self.pins)
        recipe = dict(id='synthetic-windows-j2', accepted_on='2026-11-18', os_id='windows',
                      os_version='26100+', architecture='amd64', journey='J2',
                      recipe_sha256='d' * 64, acceptance_sha256='e' * 64,
                      desktop=dict(backend='wsl2', editions=['education', 'enterprise', 'home', 'pro'],
                                   version_min='4.99.0', engine_version_min='29.0.0'))
        self.records.append(recipe)
        return recipe

    def test_windows_recipe_pins_separate_floor_and_preserves_linux_threshold(self):
        recipe = self.add_windows()
        raw = self.build()
        doc = self.verify(raw)
        self.assertEqual(doc['driver_floors'], self.floors)
        self.assertEqual(doc[subject.WINDOWS_DRIVER_KEY], '820.0')
        self.assertEqual(doc['recipes'][1], recipe)
        self.assertEqual(raw, self.build())
        del doc[subject.WINDOWS_DRIVER_KEY]
        with self.assertRaisesRegex(ValueError, 'Windows CUDA driver floor'):
            self.verify(canonical(doc))

    def test_windows_floor_requires_an_accepted_windows_recipe(self):
        recipe = self.add_windows()
        doc = self.verify(self.build())
        doc['recipes'].remove(recipe)
        with self.assertRaisesRegex(ValueError, 'Windows CUDA driver floor'):
            self.verify(canonical(doc))
        self.records.remove(recipe)
        self.assertNotIn(subject.WINDOWS_DRIVER_KEY, self.verify(self.build()))

    def test_windows_recipe_requires_a_numeric_resolved_platform_pin(self):
        self.add_windows()
        for floor in (None, True, 'TO-PIN'):
            with self.subTest(floor=floor):
                pins = self.floors if floor is None else dict(self.floors, windows_cuda13_driver_build=floor)
                self.pins = canonical({'runtime': pins})
                self.cap['pins_sha256'] = sha(self.pins)
                with self.assertRaisesRegex(ValueError, 'resolved numeric'):
                    self.build()

    def test_windows_recipe_requires_well_formed_floors_that_cover_the_evidence(self):
        recipe = self.add_windows()
        desktop = recipe['desktop']
        malformed = {
            'os_version': ['19045+', '22000+', '26099+', '26201+', '26100', '26100.1234', '26100.0+', '026100+',
                           '>=26100', '+', '26100++', ''],
            'backend': ['hyperv', 'WSL2', '', None],
            'editions': [[], 'pro', ['pro', 'pro'], ['server', 'pro'], ['core', 'pro'], ['Pro'], ['home'],
                         ['pro', 'home'], [None], [['pro']], None],
            'version_min': ['4.98.9', '4.100.0', '5.0.0', '4.99', '4.99.0.0', 'v4.99.0', '04.99.0', '4.099.0',
                            '4.99.0-beta', '4.99.0 ', 'latest', '', None, 4.99, [4, 99, 0]],
            'engine_version_min': ['28.5.0', '28.99.99', '29.9.4', '30.0.0', '29', '29.0.0.0', '29.0.0-rc.1',
                                   'latest', '', None, 29],
        }
        cases = [{'os_version': value} for value in malformed.pop('os_version')]
        cases += [{'desktop': dict(desktop, **{field: value})} for field, values in malformed.items() for value in values]
        cases += [{'desktop': {key: value for key, value in desktop.items() if key != field}} for field in desktop]
        cases += [{'desktop': dict(desktop, **extra)} for extra in (
            {'kernel_release': '6.18.33.2-microsoft-standard-WSL2'}, {'edition': 'pro'}, {'os_build_min': 26100},
            {'version': '4.99.0'}, {'engine_version': '29.9.3'})]
        cases += [{'desktop': None}, {'desktop': 'wsl2'}, {'desktop': []}, {'desktop': {}}]
        for changes in cases:
            with self.subTest(changes=changes):
                self.records[1] = dict(recipe, **changes)
                with self.assertRaises(ValueError):
                    self.build()
        self.records[1] = {key: value for key, value in recipe.items() if key != 'desktop'}
        with self.assertRaises(ValueError):
            self.build()
        self.records[1] = dict(recipe, os_id='ubuntu')
        with self.assertRaises(ValueError):
            self.build()
        # The highest floors that still admit the tested host, and narrower edition sets that include it.
        for changes in ({'os_version': '26200+'}, {'desktop': dict(desktop, engine_version_min='29.9.3')},
                        {'desktop': dict(desktop, editions=['pro'])},
                        {'desktop': dict(desktop, editions=['enterprise', 'pro'])}):
            with self.subTest(accepted=changes):
                self.records[1] = dict(recipe, **changes)
                self.assertEqual(self.verify(self.build())['recipes'][1], self.records[1])

    def test_windows_floors_compare_numerically_and_admit_newer_hosts(self):
        recipe = self.add_windows()
        version = subject.release_version
        self.assertGreater(version('4.100.0'), version('4.99.0'))
        self.assertLess('4.100.0', '4.99.0')  # what a string comparison would have said
        self.assertGreater(version('29.10.0'), version('29.9.3'))
        for text in ('4.99', '4.99.0.0', 'v4.99.0', '04.99.0', '4.99.0-beta', '29.9.3+desktop', ' 4.99.0', None, 4):
            with self.subTest(text=text):
                self.assertIsNone(version(text))
        tested = (26200, 'pro', (4, 99, 0), (29, 9, 3))
        self.assertTrue(subject.windows_admits(recipe, *tested))
        newer = ((26300, 'pro', (4, 99, 0), (29, 9, 3)), (26200, 'pro', (4, 100, 0), (29, 9, 3)),
                 (26200, 'pro', (5, 0, 0), (29, 9, 3)), (26200, 'pro', (4, 99, 0), (29, 10, 0)),
                 (26200, 'pro', (4, 99, 0), (30, 0, 0)), (26100, 'home', (4, 99, 0), (29, 0, 0)),
                 (26200, 'enterprise', (4, 99, 0), (29, 9, 3)), (26200, 'education', (4, 99, 0), (29, 9, 3)),
                 (27000, 'home', (5, 1, 2), (31, 0, 0)))
        for host in newer:
            with self.subTest(admitted=host):
                self.assertTrue(subject.windows_admits(recipe, *host))
        older = ((26099, 'pro', (4, 99, 0), (29, 9, 3)), (22631, 'pro', (4, 99, 0), (29, 9, 3)),
                 (26200, 'pro', (4, 98, 9), (29, 9, 3)), (26200, 'pro', (4, 42, 0), (29, 9, 3)),
                 (26200, 'pro', (4, 99, 0), (28, 5, 0)), (26200, 'server', (4, 99, 0), (29, 9, 3)),
                 (26200, 'core', (4, 99, 0), (29, 9, 3)), (26200, 'Pro', (4, 99, 0), (29, 9, 3)),
                 ('26200', 'pro', (4, 99, 0), (29, 9, 3)), (26200.5, 'pro', (4, 99, 0), (29, 9, 3)))
        for host in older:
            with self.subTest(refused=host):
                self.assertFalse(subject.windows_admits(recipe, *host))
        narrow = dict(recipe, desktop=dict(recipe['desktop'], editions=['pro']))
        self.assertFalse(subject.windows_admits(narrow, 26200, 'home', (4, 99, 0), (29, 9, 3)))
        self.assertEqual(subject.windows_refusal([recipe], 'J2'),
                         'Use Windows 11 build 26100 or later (Home, Pro, Enterprise or Education), Docker Desktop '
                         '4.99.0 or later and Docker Engine 29.0.0 or later on the WSL 2 backend.')
        self.assertIn('(Pro), Docker Desktop', subject.windows_refusal([narrow], 'J2'))
        self.assertEqual(subject.windows_refusal([recipe], 'J1'),
                         'Obtain an accepted signed Forge recipe for this Windows journey and Docker Desktop.')

    def test_one_windows_recipe_per_journey_and_edition(self):
        recipe = self.add_windows()
        other = dict(recipe, id='synthetic-windows-j1', journey='J1')
        self.records.append(other)
        self.assertEqual([row['id'] for row in self.verify(self.build())['recipes']],
                         ['synthetic-ubuntu-j2', 'synthetic-windows-j1', 'synthetic-windows-j2'])
        for second in (dict(recipe, id='synthetic-windows-j2-later', os_version='26200+'),
                       dict(recipe, id='synthetic-windows-j2-pro', desktop=dict(recipe['desktop'], editions=['pro'])),
                       dict(recipe, id='synthetic-windows-j2-engine',
                            desktop=dict(recipe['desktop'], engine_version_min='29.9.3'))):
            with self.subTest(second=second['id']):
                self.records[2] = second
                with self.assertRaisesRegex(ValueError, 'Duplicate approved Forge host recipe'):
                    self.build()

    def test_wrong_release_weak_memory_or_driver_omission_is_refused(self):
        selected = json.loads(self.build())
        for key, value in (('release', '0.1.0-alpha.1'), ('minimum_vram_mib', 7680),
                           ('capability_sha256', 'd' * 64), ('driver_floors', {})):
            with self.subTest(key=key):
                wrong = copy.deepcopy(selected)
                wrong[key] = value
                with self.assertRaises(ValueError):
                    self.verify(canonical(wrong))
        with self.assertRaises(ValueError):
            self.verify(self.build().replace(b'"release": ', b'"release": "earlier", "release": ', 1))


EVIDENCE = 'SYNTHETIC-COMPONENT-EVIDENCE.md'
ALPHA2_RECIPES = TOOLS / 'forge-recipes-0.1.0-alpha.2.json'
ALPHA2_EVIDENCE = ('LINUX-MINERU-R3-EVIDENCE.md', 'WINDOWS-RAW-QUALIFICATION.md')
WINDOWS_ID = 'windows-11-docker-desktop-wsl2-j1'


class OwnerWaiver(unittest.TestCase):
    """An owner waiver accepts a recipe in place of a passed lifecycle record, and nothing else."""
    setUp, build, verify, add_windows = (getattr(ForgeRequirements, name)
                                         for name in ('setUp', 'build', 'verify', 'add_windows'))

    def waived(self, row=None, **changes):
        row = {key: value for key, value in (row or self.records[0]).items() if key != 'acceptance_sha256'}
        waiver = dict(granted_by='release-owner', granted_on=row['accepted_on'], release=self.cap['release'],
                      recipe_id=row['id'], not_run=['The full install from the signed release.'],
                      evidence=[dict(file=EVIDENCE, sha256='f' * 64)])
        waiver.update(changes)
        return dict(row, waiver=waiver)

    def signed(self, *rows):
        """Return a signed-form document whose recipes are exactly these rows."""
        if not hasattr(self, 'passed_document'):
            self.passed_document = json.loads(self.build())
        return canonical(dict(self.passed_document, recipes=list(rows)))

    def test_waiver_is_accepted_and_distinct_from_a_passed_record(self):
        passed, waived = self.records[0], self.waived()
        document = self.verify(self.signed(waived))
        self.assertEqual(document['recipes'], [waived])
        self.assertNotIn('acceptance_sha256', waived)
        self.assertEqual(set(waived['waiver']), {'granted_by', 'granted_on', 'release', 'recipe_id',
                                                 'not_run', 'evidence'})
        self.assertEqual(document['format'], 'deep32-forge-prerequisites/1')
        self.assertEqual(set(document), set(json.loads(self.build())))
        self.assertIsNone(subject.waiver_notice(passed))
        self.assertEqual(subject.waiver_notice(waived),
                         'Note: this host type is accepted for 0.1.0-alpha.2 under an owner waiver dated 2026-11-18 '
                         '(recipe synthetic-ubuntu-j2). I have not yet run a full install on it; '
                         'FORGE-PREREQUISITES.json lists what was not run.')
        longest = self.waived(dict(passed, id='a' * 96))
        self.verify(self.signed(longest))
        self.assertRegex(subject.waiver_notice(longest), r'\A[ -~]{1,300}\Z')
        for both_or_neither in (dict(waived, acceptance_sha256='c' * 64),
                                {key: value for key, value in passed.items() if key != 'acceptance_sha256'}):
            with self.assertRaises(ValueError):
                self.verify(self.signed(both_or_neither))

    def test_passed_and_waived_recipes_share_one_file_and_every_technical_field(self):
        waived = self.waived(dict(self.records[0], id='synthetic-ubuntu-j4', journey='J4'))
        document = self.verify(self.signed(self.records[0], waived))
        self.assertEqual([('waiver' in row, 'acceptance_sha256' in row) for row in document['recipes']],
                         [(False, True), (True, False)])
        for field, value in (('journey', 'J3'), ('architecture', 'arm64'), ('os_version', ''),
                             ('recipe_sha256', 'b' * 63), ('accepted_on', '2026-02-30'), ('id', 'Upper')):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify(self.signed(self.waived(dict(self.records[0], **{field: value}))))
        with self.assertRaisesRegex(ValueError, 'Duplicate approved Forge host recipe'):
            self.verify(self.signed(self.records[0], self.waived(dict(self.records[0], id='synthetic-second'))))
        for key, value in (('minimum_vram_mib', 7680), ('driver_floors', {}), ('pins_sha256', 'd' * 64),
                           ('capability_sha256', 'd' * 64), ('vram_policy', 'waived')):
            with self.subTest(key=key), self.assertRaises(ValueError):
                wrong = json.loads(self.signed(waived))
                wrong[key] = value
                self.verify(canonical(wrong))

    def test_waived_windows_recipe_keeps_the_desktop_floors_and_driver_floor(self):
        recipe = self.add_windows()
        waived = self.waived(recipe)
        self.records[1] = recipe
        document = json.loads(self.build())
        document['recipes'] = [self.records[0], waived]
        self.assertEqual(self.verify(canonical(document))['recipes'][1]['desktop'], recipe['desktop'])
        for changes in ({'os_version': '19045+'}, {'os_version': '26099+'}, {'os_version': '26200.9974'},
                        {'desktop': None},
                        {'desktop': dict(recipe['desktop'], backend='hyperv')},
                        {'desktop': dict(recipe['desktop'], editions=['home'])},
                        {'desktop': dict(recipe['desktop'], version_min='4.98.9')},
                        {'desktop': dict(recipe['desktop'], engine_version_min='28.5.0')},
                        {'desktop': dict(recipe['desktop'], kernel_release='6.6.87.2-microsoft-standard-WSL2')}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.verify(canonical(dict(document, recipes=[self.records[0], self.waived(dict(recipe, **changes))])))
        floorless = {key: value for key, value in document.items() if key != subject.WINDOWS_DRIVER_KEY}
        with self.assertRaisesRegex(ValueError, 'Windows CUDA driver floor'):
            self.verify(canonical(floorless))

    def test_each_waiver_field_missing_malformed_or_changed_is_refused(self):
        good = self.waived()['waiver']
        entry = good['evidence'][0]
        cases = [None, [], 'waived', True, dict(good, approved=True)]
        cases += [{key: value for key, value in good.items() if key != field} for field in sorted(good)]
        malformed = {
            'granted_by': ['owner', '', None, ['release-owner'], 'Release-Owner'],
            'granted_on': ['2026-11-19', '2026-11-17', '2026-02-30', '', '18 November 2026', 20261118, None],
            'release': ['0.1.0-alpha.1', '0.1.0-alpha.3', 'v0.1.0-alpha.2', '', None, ['0.1.0-alpha.2']],
            'recipe_id': ['synthetic-ubuntu-j4', 'synthetic-ubuntu', '', None],
            'not_run': [[], 'The full install.', None, [''], [' leading space'], ['trailing space '], ['x' * 301],
                        ['caf' + chr(233)], ['two\nlines'], [7], [None], ['same', 'same'], ['line %d' % n for n in range(17)]],
            'evidence': [[], EVIDENCE, None, [EVIDENCE], [{}], [dict(file=EVIDENCE)], [dict(sha256='f' * 64)],
                         [dict(entry, sha256='F' * 64)], [dict(entry, sha256='f' * 63)], [dict(entry, sha256=None)],
                         [dict(entry, file='../' + EVIDENCE)], [dict(entry, file='folder/' + EVIDENCE)],
                         [dict(entry, file='')], [dict(entry, file=7)], [dict(entry, note='reviewed')],
                         [entry, dict(entry, sha256='e' * 64)],
                         [dict(file='evidence-%d.md' % n, sha256='f' * 64) for n in range(17)]],
        }
        self.assertEqual(set(malformed), set(good))
        cases += [dict(good, **{field: value}) for field, values in malformed.items() for value in values]
        for waiver in cases:
            with self.subTest(waiver=waiver), self.assertRaises(ValueError):
                self.verify(self.signed(dict(self.waived(), waiver=waiver)))
        self.verify(self.signed(self.waived()))

    def test_waiver_for_another_release_recipe_or_date_is_refused(self):
        first = self.waived()
        second = self.waived(dict(self.records[0], id='synthetic-ubuntu-j4', journey='J4'))
        self.verify(self.signed(first, second))
        swapped = (dict(first, waiver=second['waiver']), dict(second, waiver=first['waiver']))
        with self.assertRaisesRegex(ValueError, 'another grantor, date, release or recipe'):
            self.verify(self.signed(*swapped))
        for row in (self.waived(release='0.1.0-alpha.1'), self.waived(release='0.1.0-alpha.3'),
                    dict(first, accepted_on='2026-11-19'), dict(first, id='synthetic-renamed')):
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, 'another grantor, date, release or recipe'):
                self.verify(self.signed(row))
        raw = self.signed(first)
        with self.assertRaises(ValueError):
            subject.validate(raw, capability_sha256=sha(canonical(self.cap)), pins_sha256=sha(self.pins),
                             release='0.1.0-alpha.3')
        with self.assertRaises(ValueError):
            subject.validate_recipes([first], '0.1.0-alpha.3')
        self.assertFalse(subject.validate_recipes([first], '0.1.0-alpha.2'))

    def named(self, row=None, **changes):
        """Return a waived recipes-input row: evidence by file name, for the builder to hash."""
        return self.waived(row, **dict(dict(evidence=[EVIDENCE]), **changes))

    def test_builder_fills_recipe_and_evidence_digests_from_the_real_files(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-waiver-build-') as name:
            root = Path(name)
            files = {EVIDENCE: b'synthetic component evidence\n', 'synthetic-recipe.json': b'{"recipe": true}\n',
                     'capabilities.json': canonical(self.cap), 'pins.json': self.pins}
            row = self.named()
            del row['recipe_sha256']
            files['recipes.json'] = canonical([dict(row, recipe_file='synthetic-recipe.json')])
            for file, raw in files.items():
                (root / file).write_bytes(raw)
            argv = ['--capabilities', str(root / 'capabilities.json'), '--pins', str(root / 'pins.json'),
                    '--recipes', str(root / 'recipes.json'), '--recipe-file', str(root / 'synthetic-recipe.json'),
                    '--evidence', str(root / EVIDENCE)]
            subject.main([*argv, '--output', str(root / 'first.json')])
            subject.main([*argv, '--output', str(root / 'second.json')])
            raw = (root / 'first.json').read_bytes()
            self.assertEqual(raw, (root / 'second.json').read_bytes())
            built = self.verify(raw)['recipes'][0]
            self.assertEqual(built['recipe_sha256'], sha(files['synthetic-recipe.json']))
            self.assertEqual(built['waiver']['evidence'], [dict(file=EVIDENCE, sha256=sha(files[EVIDENCE]))])
            self.assertNotIn('recipe_file', built)
            self.assertNotIn('acceptance_sha256', built)
            (root / EVIDENCE).write_bytes(b'synthetic component evidence, edited\n')
            subject.main([*argv, '--output', str(root / 'third.json')])
            self.assertNotEqual(raw, (root / 'third.json').read_bytes())
            for wrong in (argv[:-2], [*argv, '--evidence', str(root / 'pins.json')], argv[:6] + argv[8:],
                          [*argv[:-1], str(root / 'absent.md')]):
                with self.subTest(wrong=wrong[6:]), self.assertRaises(SystemExit) as refused, \
                        contextlib.redirect_stderr(io.StringIO()) as message:
                    subject.main([*wrong, '--output', str(root / 'refused.json')])
                self.assertEqual(refused.exception.code, 1)
                self.assertIn('Forge prerequisites refused', message.getvalue())
                self.assertFalse((root / 'refused.json').exists())
            (root / 'empty.md').write_bytes(b'')
            (root / 'other').mkdir()
            (root / 'other' / EVIDENCE).write_bytes(b'same name, other folder\n')
            (root / 'plain name.md').write_bytes(b'a name with a space\n')
            for paths in ([root / 'empty.md'], [root / EVIDENCE, root / 'other' / EVIDENCE],
                          [root / 'plain name.md']):
                with self.subTest(paths=paths), self.assertRaises(ValueError):
                    subject.hashed(paths)

    def test_builder_refuses_evidence_it_did_not_hash(self):
        digest = {EVIDENCE: 'f' * 64}

        def build(rows, evidence=digest, recipes=None):
            return subject.build(canonical(self.cap), self.pins, canonical(rows),
                                 recipe_files=recipes, evidence_files=evidence)

        self.assertEqual(json.loads(build([self.named()]))['recipes'], [self.waived()])
        for rows, evidence, recipes in (
                ([self.waived()], digest, None),                      # digests typed into the input
                ([self.named()], {}, None),                           # named, never supplied
                ([self.named()], None, None),
                ([self.named(evidence=[EVIDENCE, 'OTHER.md'])], digest, None),
                ([self.named()], dict(digest, **{'UNUSED.md': 'e' * 64}), None),
                ([self.records[0]], digest, None),                    # supplied, named by no recipe
                ([self.named(evidence=EVIDENCE)], digest, None),
                ([self.named(evidence=[])], {}, None),
                ([dict(self.named(), recipe_file='recipe.json')], digest, {'recipe.json': 'a' * 64}),
                ([self.named()], digest, {'recipe.json': 'a' * 64}),
                ([{key: value for key, value in dict(self.named(), recipe_file='recipe.json').items()
                   if key != 'recipe_sha256'}], digest, None)):
            with self.subTest(rows=rows, evidence=evidence, recipes=recipes), self.assertRaises(ValueError):
                build(rows, evidence, recipes)
        # A passed recipe needs no evidence files, exactly as before.
        self.assertEqual(json.loads(build([self.records[0]], None))['recipes'], self.records)

    def test_alpha2_recipes_input_builds_into_three_waived_recipes(self):
        rows = json.loads(ALPHA2_RECIPES.read_bytes())
        self.pins = canonical({'runtime': dict(tei_minimum_driver='575.51.03', cuda_minimum_driver='580.65.06',
                                              mineru_minimum_driver='580.65.06',
                                              windows_cuda13_driver_build='580.00')})
        self.cap['pins_sha256'] = sha(self.pins)
        recipe = (TOOLS.parents[1] / 'install/prerequisites.json').read_bytes()
        raw = subject.build(canonical(self.cap), self.pins, ALPHA2_RECIPES.read_bytes(),
                            recipe_files={'prerequisites.json': sha(recipe)},
                            evidence_files={file: sha(file.encode('ascii')) for file in ALPHA2_EVIDENCE})
        self.assertLess(len(raw), subject.LIMIT)
        document = self.verify(raw)
        self.assertEqual(document['driver_floors'], dict(tei_minimum_driver='575.51.03',
                                                         cuda_minimum_driver='580.65.06',
                                                         mineru_minimum_driver='580.65.06'))
        self.assertEqual((document['minimum_vram_mib'], document[subject.WINDOWS_DRIVER_KEY]), (10752, '580.00'))
        built = {row['id']: row for row in document['recipes']}
        self.assertEqual({key: (row['os_id'], row['os_version'], row['architecture'], row['journey'])
                          for key, row in built.items()}, {
            'ubuntu-24.04-docker-engine-j2': ('ubuntu', '24.04', 'amd64', 'J2'),
            'ubuntu-24.04-docker-engine-j4': ('ubuntu', '24.04', 'amd64', 'J4'),
            WINDOWS_ID: ('windows', '26100+', 'amd64', 'J1')})
        self.assertEqual(len(rows), len(built))
        for row in built.values():
            waiver = row['waiver']
            self.assertNotIn('acceptance_sha256', row)
            self.assertEqual(row['recipe_sha256'], sha(recipe))
            self.assertEqual((waiver['granted_by'], waiver['granted_on'], waiver['release'], waiver['recipe_id']),
                             ('release-owner', '2026-11-18', '0.1.0-alpha.2', row['id']))
            self.assertEqual(row['accepted_on'], '2026-11-18')
            self.assertTrue(set(entry['file'] for entry in waiver['evidence']) <= set(ALPHA2_EVIDENCE))
            self.assertGreaterEqual(len(waiver['not_run']), 5)
            self.assertRegex(subject.waiver_notice(row), r'\A[ -~]{1,300}\Z')
        self.assertEqual([entry['file'] for entry in built['ubuntu-24.04-docker-engine-j2']['waiver']['evidence']],
                         ['LINUX-MINERU-R3-EVIDENCE.md'])
        windows = built[WINDOWS_ID]
        self.assertEqual(sorted(entry['file'] for entry in windows['waiver']['evidence']), sorted(ALPHA2_EVIDENCE))
        self.assertEqual(windows['desktop'], dict(
            backend='wsl2', editions=['education', 'enterprise', 'home', 'pro'], version_min='4.99.0',
            engine_version_min='29.0.0'))
        # The cited host is admitted; the waiver says it was the only setup run and names the floors.
        self.assertTrue(subject.windows_admits(windows, 26200, 'pro', (4, 99, 0), (29, 9, 3)))
        self.assertIn('Any Windows setup other than the one cited: Windows 11 build 26200.9974 Pro with Docker Desktop '
                      '4.99.0, Docker Engine 29.9.3 and one RTX 2080 Ti.', windows['waiver']['not_run'])
        floors = [line for line in windows['waiver']['not_run'] if line.startswith('This recipe accepts by its floors')]
        self.assertEqual(len(floors), 1)
        for words in ('from 26100 on', 'Home, Pro, Enterprise and Education', 'Docker Desktop 4.99.0 or later',
                      'Docker Engine 29.0.0 or later', 'without a run'):
            self.assertIn(words, floors[0])
        self.assertEqual(subject.waiver_notice(windows),
                         'Note: this host type is accepted for 0.1.0-alpha.2 under an owner waiver dated 2026-11-18 '
                         '(recipe windows-11-docker-desktop-wsl2-j1). I have not yet run a full install on it; '
                         'FORGE-PREREQUISITES.json lists what was not run.')

    def test_alpha2_windows_waiver_still_binds_release_recipe_and_date(self):
        rows = subject.cite(json.loads(ALPHA2_RECIPES.read_bytes()), {'prerequisites.json': 'a' * 64},
                            {file: 'b' * 64 for file in ALPHA2_EVIDENCE})
        self.assertTrue(subject.validate_recipes(rows))
        windows = rows[2]
        self.assertEqual(windows['id'], WINDOWS_ID)
        for wrong in (dict(windows, id='windows-11-docker-desktop-wsl2-j2'),
                      dict(windows, accepted_on='2026-11-19'),
                      dict(windows, waiver=dict(windows['waiver'], recipe_id='windows-11-26200.9974-docker-desktop-4.99.0-j1')),
                      dict(windows, waiver=dict(windows['waiver'], granted_on='2026-11-19')),
                      dict(windows, waiver=dict(windows['waiver'], release='0.1.0-alpha.3'))):
            with self.subTest(wrong=wrong), self.assertRaisesRegex(ValueError, 'another grantor, date, release or recipe'):
                subject.validate_recipes(rows[:2] + [wrong])
        with self.assertRaises(ValueError):
            subject.validate_recipes(rows, '0.1.0-alpha.3')


if __name__ == '__main__':
    unittest.main(verbosity=2)
