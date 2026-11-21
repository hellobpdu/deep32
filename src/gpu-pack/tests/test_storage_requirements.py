#!/usr/bin/env python3
"""Offline physical-capacity examples; every byte value is synthetic test data."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

KIT = Path(__file__).resolve().parents[1]
SCRATCH = None


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


storage = module('tested_storage', KIT / 'forms/storage_requirements.py')
host = module('storage_host', KIT / 'forms/host-requirements.py')


def fixture(desktop=False):
    # D/M/K/U/T/R/B/H are measured allocations, never product estimates.
    sizes = {'D': [100, 100, 100, 100], 'M': [0, 30, 30, 30],
             'K': [0, 5, 5, 5], 'U': [0, 40, 40, 40],
             'T': [0, 50, 10, 0], 'R': [0, 0, 0, 0],
             'B': [0, 0, 0, 0], 'H': [2, 2, 2, 2]}
    domains = dict(D='download', M='data', K='data', U='docker',
                   T='docker', R='docker', B='data', H='data')
    profile = dict(id='synthetic-profile', capabilities=['core', 'embedder'], card_class='turing',
                   backend=dict(engine='desktop-wsl2' if desktop else 'linux', store='synthetic-store'),
                   operation='install', filesystems={d: 'synthetic-fs' for d in set(domains.values())},
                   phases=['download', 'copy-load', 'initialise', 'steady'],
                   allocations=[dict(id=k.lower(), unit=k.lower(), identity=hashlib.sha256(k.encode()).hexdigest(),
                                     domain=domains[k], category=k, bytes=v) for k, v in sizes.items()],
                   running_floor={d: 1 for d in set(domains.values())})
    if desktop:
        profile['filesystems']['backing'] = 'synthetic-fs'
        profile['running_floor']['backing'] = 1
        profile['allocations'].append(dict(id='backing-growth', unit='backing-growth', identity='f' * 64,
            domain='backing', category='U', bytes=[0, 70, 60, 40]))
    return dict(schema=storage.SCHEMA, release='a' * 64, profiles=[profile])


class StorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (SCRATCH or os.environ.get('D32_STORAGE_TEST_SCRATCH')):
            raise unittest.SkipTest('Set D32_STORAGE_TEST_SCRATCH or pass --scratch')

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='synthetic-storage-',
            dir=SCRATCH or os.environ.get('D32_STORAGE_TEST_SCRATCH'))
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.value = fixture()
        self.samples = {name: dict(volume=name, filesystem='synthetic-fs', free_bytes=1000)
                        for name in ('download', 'data', 'docker', 'backing')}
        for owner, name in ((subprocess, 'run'), (subprocess, 'Popen'), (socket, 'socket')):
            guard = patch.object(owner, name, side_effect=AssertionError('host/process/network forbidden'))
            guard.start()
            self.addCleanup(guard.stop)

    @property
    def profile(self):
        return self.value['profiles'][0]

    def allocation(self, name):
        return next(row for row in self.profile['allocations'] if row['id'] == name)

    def args(self, **extra):
        data = json.dumps(self.value, sort_keys=True).encode()
        desktop = self.profile['backend']['engine'] == 'desktop-wsl2'
        names = ('download', 'data', 'backing') if desktop else ('download', 'data', 'docker')
        options = dict(expected_sha256=hashlib.sha256(data).hexdigest(), expected_release='a' * 64,
                       capabilities=['core', 'embedder'], card_class='turing',
                       observed_backend=copy.deepcopy(self.profile['backend']), operation='install',
                       locations={name: name for name in names},
                       observe=lambda path: self.samples[path])
        if desktop:
            options['desktop'] = self.samples['docker']
        options.update(extra)
        return data, options

    def plan(self, **extra):
        data, options = self.args(**extra)
        return storage.plan(data, **options)

    def credit(self, name, size, **extra):
        row = self.allocation(name)
        return dict(profile=self.profile['id'], allocation=name, identity=row['identity'],
                    volume=self.samples[row['domain']]['volume'], bytes=size) | extra

    def test_distinct_volumes_only_own_their_allocations(self):
        rows = {r['volume']: r for r in self.plan()['volumes']}
        self.assertEqual({k: r['peak_additional_bytes'] for k, r in rows.items()},
                         dict(data=37, docker=90, download=100))
        self.assertTrue(self.plan()['fits'])

    def test_shared_volume_charged_once(self):
        for sample in self.samples.values():
            sample['volume'] = 'shared'
        rows = self.plan()['volumes']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['peak_additional_bytes'], 227)
        self.assertEqual(rows[0]['domains'], ['data', 'docker', 'download'])

    def test_partial_shared_layout(self):
        self.samples['data']['volume'] = 'download'
        rows = {r['volume']: r for r in self.plan()['volumes']}
        self.assertEqual(rows['download']['peak_additional_bytes'], 137)
        self.assertEqual(rows['docker']['peak_additional_bytes'], 90)

    def test_peak_uses_simultaneous_phases(self):
        self.allocation('u')['bytes'] = [0, 0, 100, 0]
        self.allocation('t')['bytes'] = [0, 100, 0, 0]
        row = next(r for r in self.plan()['volumes'] if r['volume'] == 'docker')
        self.assertEqual(row['peak_additional_bytes'], 100)

    def test_existing_matching_image_is_not_recharged(self):
        result = self.plan(present=[self.credit('u', 40)])
        row = next(r for r in result['volumes'] if r['volume'] == 'docker')
        self.assertEqual(row['peak_additional_bytes'], 50)

    def test_compressed_and_unpacked_units_both_persist(self):
        row = copy.deepcopy(self.allocation('u'))
        row.update(id='compressed', unit='compressed', bytes=[0, 20, 20, 20])
        self.profile['allocations'].append(row)
        result = self.plan(present=[self.credit('u', 40)])
        docker = next(r for r in result['volumes'] if r['volume'] == 'docker')
        self.assertEqual(docker['peak_additional_bytes'], 70)

    def test_validated_interrupted_download_only_credits_prefix(self):
        result = self.plan(present=[self.credit('d', 30)])
        self.assertEqual(next(r for r in result['volumes'] if r['volume'] == 'download')['peak_additional_bytes'], 70)

    def test_interrupted_model_copy_cannot_credit_other_unit(self):
        row = copy.deepcopy(self.allocation('m'))
        row.update(id='copy-temporary', unit='copy-temporary', category='T', bytes=[0, 30, 0, 0])
        self.profile['allocations'].append(row)
        data = next(r for r in self.plan(present=[self.credit('copy-temporary', 10)])['volumes'] if r['volume'] == 'data')
        self.assertEqual(data['peak_additional_bytes'], 57)

    def test_upgrade_retains_old_generation_without_pruning(self):
        self.profile['operation'] = 'upgrade'
        self.allocation('r')['bytes'] = [60] * 4
        old = self.credit('r', 60)
        needed = next(r for r in self.plan(operation='upgrade', present=[old])['volumes'] if r['volume'] == 'docker')
        self.assertEqual(needed['peak_additional_bytes'], 90)
        self.assertEqual(self.allocation('r')['bytes'], [60] * 4)

    def test_rollback_and_content_job_budgets_are_explicit(self):
        self.profile['operation'] = 'rollback'
        self.allocation('r')['bytes'] = [60] * 4
        self.allocation('b')['bytes'] = [0, 25, 80, 25]
        rows = {r['volume']: r for r in self.plan(operation='rollback')['volumes']}
        self.assertEqual(rows['docker']['peak_additional_bytes'], 150)
        self.assertEqual(rows['data']['peak_additional_bytes'], 117)

    def test_missing_optional_capability_never_uses_core_estimate(self):
        with self.assertRaises(ValueError):
            self.plan(capabilities=['core', 'embedder', 'ingestion'])

    def test_shared_core_plus_selected_class_and_ingestion_profile(self):
        self.profile['capabilities'].append('ingestion')
        self.allocation('m')['bytes'] = [0, 100, 100, 100]
        result = self.plan(capabilities=['core', 'embedder', 'ingestion'])
        self.assertEqual(next(r for r in result['volumes'] if r['volume'] == 'data')['peak_additional_bytes'], 107)

    def test_every_supported_class_uses_its_exact_profile(self):
        for card in sorted(storage.CLASSES):
            with self.subTest(card=card):
                self.profile['card_class'] = card
                self.assertTrue(self.plan(card_class=card)['fits'])
                with self.assertRaises(ValueError):
                    self.plan(card_class='tei-cpu')

    def test_no_hardware_fallback(self):
        with self.assertRaises(ValueError):
            self.plan(card_class='ada')

    def test_backend_driver_and_filesystem_must_match(self):
        for change in (dict(observed_backend=dict(engine='linux', store='different')),
                       dict(expected_release='b' * 64), dict(operation='resume')):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.plan(**change)
        self.samples['data']['filesystem'] = 'unknown'
        with self.assertRaises(ValueError):
            self.plan()

    def test_unknown_filesystem_never_activates_a_profile(self):
        self.profile['filesystems']['data'] = 'unknown'
        self.samples['data']['filesystem'] = 'unknown'
        with self.assertRaises(ValueError):
            self.plan()

    def test_alias_cannot_charge_a_physical_unit_twice_across_domains(self):
        row = copy.deepcopy(self.allocation('d'))
        row.update(id='data-alias', domain='data')
        self.profile['allocations'].append(row)
        with self.assertRaises(ValueError):
            self.plan()

    def test_desktop_internal_and_backing_are_independent(self):
        self.value = fixture(desktop=True)
        for sample in self.samples.values():
            sample['volume'] = 'same-id'
        rows = {r['kind']: r for r in self.plan()['volumes']}
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows['internal']['peak_additional_bytes'], 90)
        self.assertEqual(rows['host']['peak_additional_bytes'], 207)

    def test_desktop_low_internal_never_passes_with_large_backing(self):
        self.value = fixture(desktop=True)
        self.samples['docker']['free_bytes'] = 89
        self.assertFalse(self.plan()['fits'])

    def test_desktop_low_backing_never_passes_with_large_internal(self):
        self.value = fixture(desktop=True)
        self.samples['backing']['free_bytes'] = 69
        self.assertFalse(self.plan()['fits'])

    def test_desktop_requires_internal_observation(self):
        self.value = fixture(desktop=True)
        with self.assertRaises(ValueError):
            self.plan(desktop=None)

    def test_low_space_on_each_volume_and_at_each_phase(self):
        for phase, required in [('download', dict(download=100, data=37, docker=90)),
                                ('copy-load', dict(download=100, data=37, docker=90)),
                                ('initialise', dict(download=100, data=37, docker=50)),
                                ('steady', dict(download=100, data=37, docker=40))]:
            for domain, size in required.items():
                with self.subTest(phase=phase, domain=domain):
                    self.samples[domain]['free_bytes'] = size - 1
                    with self.assertRaises(ValueError):
                        storage.require(self.plan(phase=phase))
                    self.samples[domain]['free_bytes'] = size
                    self.assertTrue(self.plan(phase=phase)['fits'])
                    self.samples[domain]['free_bytes'] = 1000

    def test_running_floor_and_next_operation_are_distinct(self):
        self.samples['docker']['free_bytes'] = 50
        self.assertEqual(next(r for r in self.plan()['volumes'] if r['volume'] == 'docker')['state'], 'WARN')
        self.samples['docker']['free_bytes'] = 0
        self.assertEqual(next(r for r in self.plan()['volumes'] if r['volume'] == 'docker')['state'], 'FAIL')

    def test_headroom_not_disguised_as_used_bytes(self):
        data = next(r for r in self.plan()['volumes'] if r['volume'] == 'data')
        self.assertEqual(data['headroom_bytes'], {p: 2 for p in self.profile['phases']})

    def test_wrong_or_untrusted_digest_refused_before_observation(self):
        data, options = self.args(expected_sha256='0' * 64)
        observer = Mock(side_effect=AssertionError('observe before trust'))
        with self.assertRaises(ValueError):
            storage.plan(data, **(options | dict(observe=observer)))
        observer.assert_not_called()

    def test_strict_json_and_bounded_input(self):
        for data in (b'{"schema":1,"schema":2}', b'NaN', b'{}', b'x' * (storage.LIMIT + 1)):
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                storage.load(data, hashlib.sha256(data).hexdigest())

    def test_negative_bool_float_unknown_and_missing_measurements_refused(self):
        original = copy.deepcopy(self.value)
        for bad in (-1, True, 1.5, '10', 2 ** 63):
            self.value = copy.deepcopy(original)
            self.allocation('m')['bytes'][0] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.plan()
        for field in ('running_floor', 'allocations', 'phases'):
            self.value = copy.deepcopy(original)
            del self.profile[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.plan()
        self.value = original
        self.profile['guessed_bytes'] = 1
        with self.assertRaises(ValueError):
            self.plan()

    def test_every_category_even_zero_requires_explicit_measurement(self):
        self.profile['allocations'] = [row for row in self.profile['allocations'] if row['category'] != 'R']
        with self.assertRaises(ValueError):
            self.plan()

    def test_duplicate_profile_allocation_and_physical_unit_refused(self):
        original = copy.deepcopy(self.value)
        for mode in ('profile', 'allocation', 'unit'):
            self.value = copy.deepcopy(original)
            if mode == 'profile':
                self.value['profiles'].append(copy.deepcopy(self.profile))
            else:
                row = copy.deepcopy(self.allocation('u'))
                if mode == 'unit':
                    row['id'] = 'alias-of-u'
                self.profile['allocations'].append(row)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.plan()

    def test_shared_volume_uses_lowest_capacity_observation(self):
        self.samples['download']['volume'] = 'data'
        self.samples['download']['free_bytes'] = 999
        data = next(r for r in self.plan()['volumes'] if r['volume'] == 'data')
        self.assertEqual(data['free_bytes'], 999)

    def test_contradictory_same_volume_filesystem_refused(self):
        self.samples['download']['volume'] = 'data'
        self.profile['filesystems']['download'] = 'other-fs'
        self.samples['download']['filesystem'] = 'other-fs'
        with self.assertRaises(ValueError):
            self.plan()

    def test_existing_bytes_need_identity_profile_volume_and_bound(self):
        for extra in (dict(identity='0' * 64), dict(profile='other'), dict(volume='other'),
                      dict(bytes=41), dict(bytes=True), dict(allocation='unknown')):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.plan(present=[self.credit('u', 40, **extra)])
        with self.assertRaises(ValueError):
            self.plan(present=[self.credit('u', 40)] * 2)
        with self.assertRaises(ValueError):
            self.plan(present=[self.credit('h', 1)])

    def test_caller_arguments_and_future_phases_are_strict(self):
        for args in (dict(capabilities=['embedder', 'core']), dict(capabilities=['core', 'embedder', 'embedder']),
                     dict(phase='absent'), dict(locations={}), dict(card_class=[])):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.plan(**args)

    def test_host_owner_enforces_or_returns_diagnostic_report(self):
        self.samples['docker']['free_bytes'] = 0
        data, args = self.args()
        with self.assertRaises(ValueError):
            host.measured_storage(data, **args)
        self.assertFalse(host.measured_storage(data, enforce=False, **args)['fits'])

    def test_legacy_gate_is_not_lowered_without_release_inputs(self):
        payload = self.work / 'payload'
        (payload / 'images').mkdir(parents=True)
        (payload / 'images/a.tar').write_bytes(b'x' * 10)
        (payload / 'model').write_bytes(b'x' * 7)
        usage = Mock(return_value=type('Usage', (), dict(free=10 ** 12))())
        result = host.check_storage(payload, self.work, self.work, disk_usage=usage)
        self.assertEqual(result, 17 + 20 + host.GIB)
        self.assertEqual(usage.call_count, 2)

    def test_planning_is_deterministic_and_writes_nothing(self):
        before = list(self.work.iterdir())
        self.assertEqual(self.plan(), self.plan())
        self.assertEqual(list(self.work.iterdir()), before)

    def test_local_observer_rejects_relative_and_network_paths(self):
        for path in ('relative/target', '//server/share/target', '\\\\server\\share\\target'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                storage.observe_volume(path)

    def test_observation_errors_use_constant_refusal(self):
        data, options = self.args()
        for error in (PermissionError('synthetic-private-path'), ValueError('synthetic-private-path')):
            with self.subTest(error=type(error).__name__), self.assertRaisesRegex(ValueError, '^' + storage.REFUSAL + '$'):
                storage.plan(data, **(options | dict(observe=Mock(side_effect=error))))
        with patch.object(storage, '_local_volume', side_effect=PermissionError('synthetic-private-path')):
            with self.assertRaisesRegex(ValueError, '^' + storage.REFUSAL + '$'):
                storage.observe_volume(self.work)

    @unittest.skipUnless(os.name == 'nt', 'Windows volume API fixture')
    def test_windows_volume_api_resolves_nonexistent_target_with_fakes(self):
        def mount(path, buffer, length):
            self.assertEqual(path, str(self.work))
            buffer.value = 'C:\\'
            return 1
        def volume(path, buffer, length):
            self.assertEqual(path, 'C:\\')
            buffer.value = 'synthetic-volume-guid'
            return 1
        def information(path, label, length, serial, component, flags, filesystem, capacity):
            filesystem.value = 'NTFS'
            return 1
        kernel = Mock(GetVolumePathNameW=Mock(side_effect=mount),
                      GetVolumeNameForVolumeMountPointW=Mock(side_effect=volume),
                      GetVolumeInformationW=Mock(side_effect=information))
        with patch.object(storage.ctypes, 'WinDLL', return_value=kernel), \
                patch.object(storage.shutil, 'disk_usage', return_value=Mock(free=123)):
            self.assertEqual(storage.observe_volume(self.work / 'future' / 'target'),
                             dict(volume='synthetic-volume-guid', filesystem='ntfs', free_bytes=123))

    @unittest.skipUnless(os.name == 'nt', 'Windows volume API fixture')
    def test_windows_volume_api_failure_never_queries_capacity(self):
        for method in ('GetVolumePathNameW', 'GetVolumeNameForVolumeMountPointW', 'GetVolumeInformationW'):
            kernel = Mock()
            getattr(kernel, method).return_value = 0
            with self.subTest(method=method), patch.object(storage.ctypes, 'WinDLL', return_value=kernel), \
                    patch.object(storage.shutil, 'disk_usage', side_effect=AssertionError('failed volume observation')):
                with self.assertRaisesRegex(ValueError, '^' + storage.REFUSAL + '$'):
                    storage.observe_volume(self.work / 'future')

    def test_reparse_or_link_ancestor_refuses_before_capacity_query(self):
        unsafe = self.work / 'unsafe'
        unsafe.mkdir()
        with patch.object(Path, 'is_junction', side_effect=lambda: True), \
                patch.object(storage.shutil, 'disk_usage', side_effect=AssertionError('unsafe disk query')):
            with self.assertRaises(ValueError):
                storage.observe_volume(unsafe / 'not-created')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scratch', type=Path, required=True)
    args, rest = parser.parse_known_args()
    if not args.scratch.is_dir() or args.scratch.is_symlink() or args.scratch.is_junction():
        parser.error('Use an existing private synthetic scratch directory.')
    SCRATCH = args.scratch
    result = unittest.main(argv=[sys.argv[0], *rest], exit=False).result
    executed = result.testsRun - len(result.skipped)
    sys.exit(0 if result.wasSuccessful() and executed > 0 else 1)
