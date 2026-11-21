"""Invented J07 observations only; no Docker, native process or network calls."""

from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
from pathlib import Path, PureWindowsPath
import socket
import subprocess
import sys
from types import MappingProxyType
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src' / 'diagnostics'))
from deep32_diag import forge, report, trust
from deep32_diag.errors import DiagError
from deep32_diag.models import State
from deep32_diag.platform import linux_forge, windows_forge

# Reuse only these reviewed pure owner functions. Its main is guarded; no
# lifecycle/provisioning module is loaded and no native call occurs at import.
SPEC = importlib.util.spec_from_file_location('synthetic_forge_host_rules',
    ROOT / 'src' / 'gpu-pack' / 'forms' / 'host-requirements.py')
HOST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOST)
RULES = forge.HostRules(HOST.version, HOST.nvidia_cdi)
IMAGE = 'sha256:' + '1' * 64
REVISION = '2' * 40
MODEL = 'public-demo/model'
NOW_TEXT = '2026-11-17T12:00:00Z'
NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc).timestamp()
SECRET = 'synthetic-secret-forge-observation'
ALL = frozenset(('driver', 'engine', 'compose', 'runtime', 'containers', 'models', 'listeners', 'jobs'))


def receipt(platform='linux', optional=False):
    services = [{'alias': name, 'image': IMAGE, 'required': True, 'active_job': False}
                for name in ('embeddings', 'reranker', 'door')]
    if optional:
        services += [{'alias': name, 'image': IMAGE, 'required': False, 'active_job': False}
                     for name in ('converter', 'builder', 'qdrant')]
    inventory = {'services': services,
                 'models': [{'alias': name, 'id': MODEL, 'revision': REVISION}
                            for name in ('embed', 'rerank')],
                 'ports': [{'alias': 'embeddings', 'port': 8081},
                           {'alias': 'reranker', 'port': 8082}, {'alias': 'pairing', 'port': 7453}],
                 'routes': [], 'filesystems': [], 'credential_slots': []}
    frozen = MappingProxyType({key: tuple(MappingProxyType(x) for x in rows)
                               for key, rows in inventory.items()})
    return trust.InstallReceipt('forge', platform, trust.RELEASE, '3' * 64, frozen)


def policy():
    return forge.RuntimePolicy(('550.10', '580.65.6', '570.42'), '19.03.0', '2.30.0', 2,
        {'door': 30}, frozenset(('door',)), {'embed': 'embeddings', 'rerank': 'reranker'})


def observation(platform='linux'):
    engine = {'Version': '28.3.1', 'OSType': 'linux', 'Runtimes': {'nvidia': {}},
              'endpoint': forge.LINUX_SOCKET if platform == 'linux' else forge.DESKTOP_PIPE,
              'OperatingSystem': 'Docker Desktop' if platform == 'windows' else 'Synthetic Linux',
              'KernelVersion': '6.6.0-microsoft-standard-WSL2' if platform == 'windows' else '6.6.0'}
    containers = {name: forge.ContainerObservation('running', IMAGE, True,
                  'healthy' if name == 'door' else None, True, True, 100)
                  for name in ('embeddings', 'reranker', 'door')}
    attestation = {'gpu_ext': 'on'}
    for name in ('embed', 'rerank'):
        attestation[name] = {'verified': True, 'model_id': MODEL, 'revision': REVISION,
                             'measurement_verified': True, 'device_kind': 'gpu', 'checked_at': NOW_TEXT}
    models = {name: forge.ModelObservation(attestation, True, 'gpu') for name in ('embed', 'rerank')}
    listeners = {name: forge.ListenerObservation(True, True, True, port)
                 for name, port in (('embeddings', 8081), ('reranker', 8082))}
    listeners['pairing'] = forge.ListenerObservation(False)
    return forge.ForgeObservation(ALL, ('580.65.6', '590.1'), engine, 'v2.30.0', containers,
                                  models, listeners, forge.JobObservation())


def rows(*, install=None, pins=None, seen=None):
    return {item.id: item for item in forge.forge_rows(install or receipt(), pins or policy(),
             seen or observation(), RULES, now=NOW)}


def active_converter_receipt():
    install = receipt(optional=True)
    data = dict(install.inventory)
    data['services'] = tuple(dict(item, active_job=True) if item['alias'] == 'converter' else item
                             for item in data['services'])
    return replace(install, inventory=data)


class ForgePolicyTests(unittest.TestCase):
    def setUp(self):
        # Every test is observation-only, even when an implementation regresses.
        self.process = patch.object(subprocess, 'Popen', side_effect=AssertionError('native process forbidden')).start()
        self.socket = patch.object(socket, 'socket', side_effect=AssertionError('socket forbidden')).start()
        self.addCleanup(patch.stopall)

    def test_complete_linux_and_report_typed_redaction(self):
        checks = tuple(rows().values())
        self.assertTrue(all(x.state in (State.OK, State.SKIP) for x in checks))
        result = report.build_report('forge', 'linux', 'synthetic-forge', NOW_TEXT, checks)
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['summary']['skip'], 4)
        self.assertTrue(next(x['fix'] for x in result['checks'] if x['id'] == 'container.converter'))

    def test_pin_selection_core_addon_and_windows_are_explicit(self):
        runtime = {'tei_minimum_driver': '550.1', 'cuda_minimum_driver': '580.65.6',
                   'mineru_minimum_driver': '610.1', 'windows_cuda13_driver_build': '590.2',
                   'docker_minimum_version': '19.03.0', 'compose_minimum_version': '2.30.0'}
        for install, expected in ((receipt(), ('550.1', '580.65.6')),
                                  (receipt(optional=True), ('550.1', '580.65.6', '610.1')),
                                  (receipt('windows'), ('550.1', '580.65.6', '590.2'))):
            actual = forge.runtime_policy_from_pins(install, runtime, selected_gpu_count=2,
                      model_services=policy().model_services)
            self.assertEqual(actual.driver_floors, expected)
        missing = dict(runtime)
        del missing['windows_cuda13_driver_build']
        current = forge.runtime_policy_from_pins(receipt('windows'), missing, selected_gpu_count=2,
                      model_services=policy().model_services)
        self.assertEqual(forge.driver_row(current, observation('windows'), RULES).state, State.FAIL)

    def test_maximum_floor_and_every_selected_gpu(self):
        seen = replace(observation(), drivers=('590.1', '580.65.5'))
        actual = rows(seen=seen)['driver']
        self.assertEqual(actual.state, State.FAIL)
        self.assertEqual(dict(actual.observed_items)['minimum_version'], '580.65.6.0')
        for drivers in ((), ('590.1',), ('590.1', SECRET), None):
            self.assertEqual(rows(seen=replace(seen, drivers=drivers))['driver'].state, State.FAIL)

    def test_missing_pins_do_not_lower_or_guess_floor(self):
        for floors in ((), ('broken',), ('580.65.6', None)):
            self.assertEqual(rows(pins=replace(policy(), driver_floors=floors))['driver'].state, State.FAIL)
        self.assertEqual(rows(pins=replace(policy(), selected_gpu_count=None))['driver'].state, State.FAIL)

    def test_owner_version_parser_and_suffix_projection(self):
        seen = replace(observation(), drivers=('590.1-vendor', '590.1+build'))
        self.assertEqual(rows(seen=seen)['driver'].state, State.OK)
        self.assertEqual(rows(pins=replace(policy(), compose_minimum='2.31'))['docker-compose'].state, State.FAIL)
        self.assertEqual(rows(pins=replace(policy(), docker_minimum='1.0'))['docker-engine'].state, State.FAIL)
        self.assertEqual(rows(pins=replace(policy(), compose_minimum='1.0'))['docker-compose'].state, State.FAIL)

    def test_no_native_qualification_cannot_claim_ok(self):
        checks = rows(seen=replace(observation(), qualified=frozenset()))
        for name in ('driver', 'docker-engine', 'docker-compose', 'gpu-runtime', 'gpu-hold',
                     'container.embeddings', 'model.embed', 'port.embeddings'):
            self.assertEqual(checks[name].state, State.WARN)

    def test_local_engine_endpoint_os_and_version(self):
        for change in ({'endpoint': 'tcp://remote:2375'}, {'OSType': 'windows'}, {'Version': '18.1'},
                       {'endpoint': forge.DESKTOP_PIPE}, {'Version': SECRET}):
            engine = dict(observation().engine, **change)
            check = rows(seen=replace(observation(), engine=engine))
            self.assertEqual(check['docker-engine'].state, State.FAIL)
            self.assertEqual(check['gpu-runtime'].state, State.SKIP)
            self.assertEqual(check['gpu-runtime'].blocked_by, ('docker-engine',))

    def test_windows_requires_desktop_linux_pipe_wsl2(self):
        good = observation('windows')
        self.assertEqual(rows(install=receipt('windows'), seen=good)['docker-engine'].state, State.OK)
        for change in ({'OperatingSystem': 'Synthetic Linux'}, {'KernelVersion': 'microsoft'},
                       {'KernelVersion': 'WSL2'}, {'endpoint': forge.LINUX_SOCKET}):
            current = replace(good, engine=dict(good.engine, **change))
            self.assertEqual(rows(install=receipt('windows'), seen=current)['docker-engine'].state, State.FAIL)

    def test_runtime_requires_discovered_nvidia_device(self):
        current = observation()
        for changed, state in (({'Runtimes': {}}, State.FAIL),
                ({'Runtimes': {}, 'CDISpecDirs': ['/etc/cdi']}, State.FAIL),
                ({'Runtimes': {}, 'CDIDevices': ['nvidia.com/gpu=0']}, State.OK)):
            self.assertEqual(rows(seen=replace(current, engine=dict(current.engine, **changed)))['gpu-runtime'].state, state)

    def test_containers_state_health_image_owner_and_application(self):
        original = observation()
        for change in ({'state': 'exited'}, {'state': 'restarting'}, {'image': 'sha256:' + '4' * 64},
                       {'owner_verified': False}, {'application_healthy': False}, {'authenticated': False}):
            current = dict(original.containers, embeddings=replace(original.containers['embeddings'], **change))
            check = rows(seen=replace(original, containers=current))
            self.assertEqual(check['container.embeddings'].state, State.FAIL)
            self.assertEqual(check['model.embed'].state, State.SKIP)
        for health in ('unhealthy', None):
            current = dict(original.containers, door=replace(original.containers['door'], health=health))
            self.assertEqual(rows(seen=replace(original, containers=current))['container.door'].state, State.FAIL)

    def test_startup_grace_is_signed_bounded_and_never_masks_wrong_image(self):
        original = observation()
        for age, image, state in ((10, IMAGE, State.WARN), (31, IMAGE, State.FAIL),
                                  (-1, IMAGE, State.FAIL), (10, 'sha256:' + '5' * 64, State.FAIL)):
            current = dict(original.containers, door=forge.ContainerObservation('running', image, True, 'starting', age_seconds=age))
            self.assertEqual(rows(seen=replace(original, containers=current))['container.door'].state, state)

    def test_optional_inactive_skips_active_recorded_job_requires_container(self):
        install = receipt(optional=True)
        self.assertEqual(rows(install=install)['container.converter'].state, State.SKIP)
        data = dict(install.inventory)
        data['services'] = tuple(dict(x, active_job=True) if x['alias'] == 'converter' else x for x in data['services'])
        self.assertEqual(rows(install=replace(install, inventory=data))['container.converter'].state, State.FAIL)
        current = dict(observation().containers, converter=forge.ContainerObservation('running', IMAGE, True))
        self.assertEqual(rows(install=install, seen=replace(observation(), containers=current))['container.converter'].state, State.FAIL)
        current['converter'] = replace(current['converter'], state='paused')
        self.assertEqual(rows(install=install, seen=replace(observation(), containers=current))['container.converter'].state, State.FAIL)

    def test_active_job_inventory_requires_matching_observed_journal(self):
        install = active_converter_receipt()
        current = replace(observation(), containers=dict(observation().containers,
                           converter=forge.ContainerObservation('running', IMAGE, True)))
        self.assertEqual(rows(install=install, seen=current)['container.converter'].state, State.OK)
        self.assertEqual(rows(install=install, seen=current)['gpu-hold'].state, State.FAIL)
        for job in (forge.JobObservation(active=True),
                    forge.JobObservation(False, False, True, True, False, True, ('builder',))):
            self.assertEqual(rows(install=install, seen=replace(current, job=job))['gpu-hold'].state, State.FAIL)
        coherent = forge.JobObservation(False, False, True, True, False, True, ('converter',))
        self.assertEqual(rows(install=install, seen=replace(current, job=coherent))['gpu-hold'].state, State.OK)

    def test_service_origin_device_cpu_missing_and_unsupported(self):
        for supported, device, state in ((True, 'cpu', State.FAIL), (False, 'cpu', State.FAIL), (True, None, State.FAIL),
                                         (False, None, State.WARN), (True, 'gpu', State.OK)):
            current = dict(observation().models, embed=replace(observation().models['embed'], device_supported=supported, device_kind=device))
            self.assertEqual(rows(seen=replace(observation(), models=current))['gpu-visible.embed'].state, state)

    def test_model_identity_authentication_freshness_and_measurement(self):
        original = observation()
        for change, state in (({'model_id': SECRET}, State.FAIL), ({'revision': '6' * 40}, State.FAIL),
                              ({'verified': False, 'reason': 'forge-auth-rejected'}, State.FAIL),
                              ({'checked_at': '2026-11-16T12:00:00Z'}, State.WARN),
                              ({'checked_at': '2026-11-18T12:00:00Z'}, State.WARN)):
            attestation = dict(original.models['embed'].attestation)
            attestation['embed'] = dict(attestation['embed'], **change)
            models = dict(original.models, embed=replace(original.models['embed'], attestation=attestation))
            check = rows(seen=replace(original, models=models))['model.embed']
            self.assertEqual(check.state, state)
            self.assertNotIn(SECRET, repr(check))
        attestation = dict(original.models['embed'].attestation)
        attestation['embed'] = dict(attestation['embed'])
        del attestation['embed']['measurement_verified']
        models = dict(original.models, embed=replace(original.models['embed'], attestation=attestation))
        self.assertEqual(rows(seen=replace(original, models=models))['model.embed'].state, State.WARN)

    def test_passive_listener_exact_port_loopback_and_verified_owner(self):
        original = observation()
        for change in ({'present': False}, {'owner_verified': False}, {'loopback_only': False}, {'port': 8089}):
            listeners = dict(original.listeners, embeddings=replace(original.listeners['embeddings'], **change))
            self.assertEqual(rows(seen=replace(original, listeners=listeners))['port.embeddings'].state, State.FAIL)
        for helper, state in ((False, State.FAIL), (True, State.OK)):
            listeners = dict(original.listeners, pairing=forge.ListenerObservation(True, False, True, 7453, helper))
            self.assertEqual(rows(seen=replace(original, listeners=listeners))['port.pairing'].state, state)

    def test_hold_orphan_recovery_and_coherent_job(self):
        for job, state in ((forge.JobObservation(True), State.FAIL),
                           (forge.JobObservation(False, True), State.FAIL),
                           (forge.JobObservation(True, True, True, True, True, True), State.FAIL),
                           (forge.JobObservation(False, False, True, True, False, True), State.FAIL)):
            self.assertEqual(rows(seen=replace(observation(), job=job))['gpu-hold'].state, state)

    def test_intentional_conversion_pause_does_not_claim_models_live(self):
        original = observation()
        containers = {key: replace(value, state='exited') if key != 'door' else value
                      for key, value in original.containers.items()}
        containers['converter'] = forge.ContainerObservation('running', IMAGE, True)
        current = replace(original, containers=containers,
                          job=forge.JobObservation(False, True, True, True, True, True, ('converter',)))
        checks = rows(install=active_converter_receipt(), seen=current)
        self.assertEqual(checks['container.embeddings'].state, State.WARN)
        self.assertEqual(checks['gpu-visible.embed'].state, State.WARN)
        self.assertEqual(checks['model.embed'].state, State.WARN)

    def test_windows_pairing_stays_fail_despite_healthy_services(self):
        checks = rows(install=receipt('windows'), seen=observation('windows'))
        self.assertEqual(checks['windows-pairing'].state, State.FAIL)
        self.assertEqual(forge.windows_pairing_row('windows', protected_adapter_qualified=True).state, State.OK)
        self.assertEqual(forge.windows_pairing_row('linux').state, State.SKIP)

    def test_missing_core_inventory_fails_installation(self):
        install = receipt()
        inventory = dict(install.inventory, services=())
        self.assertEqual(rows(install=replace(install, inventory=inventory))['installation'].state, State.FAIL)
        for field in ('models', 'ports'):
            self.assertEqual(rows(install=replace(install, inventory=dict(install.inventory, **{field: ()})))['installation'].state, State.FAIL)
        services = tuple(dict(item, required=False) for item in install.inventory['services'])
        self.assertEqual(rows(install=replace(install, inventory=dict(install.inventory, services=services)))['installation'].state, State.FAIL)

    def test_adapter_qualification_gate_and_operation_allowlist(self):
        called = []
        def observer(request):
            called.append(request.operation)
            self.assertEqual(type(request), linux_forge.ForgeRequest)
            return ('590.1', '590.1')
        self.assertEqual(linux_forge.observe(linux_forge.ForgeReader(observer)).qualified, frozenset())
        self.assertFalse(called)
        source = linux_forge.ForgeReader(observer, frozenset((linux_forge.ForgeRead.DRIVER,)))
        result = linux_forge.observe(source)
        self.assertEqual(called, [linux_forge.ForgeRead.DRIVER])
        self.assertEqual(result.drivers, ('590.1', '590.1'))
        with self.assertRaises(DiagError):
            linux_forge.observe(linux_forge.ForgeReader(observer, frozenset(('docker-run',))))

    def test_adapter_failure_redacts_external_exception(self):
        def failed(_):
            raise RuntimeError(SECRET)
        source = linux_forge.ForgeReader(failed, frozenset((linux_forge.ForgeRead.DRIVER,)))
        actual = linux_forge.observe(source)
        self.assertIsNone(actual.drivers)
        self.assertNotIn(SECRET, repr(actual))
        self.assertEqual(forge.driver_row(policy(), actual, RULES).state, State.FAIL)

    def test_adapter_uses_shared_budget_observation_gate(self):
        class DeniedBudget:
            def __init__(self):
                self.requests = []
            def deadline(self):
                return 10
            def observe(self, callback, request):
                self.requests.append(request)
                raise DiagError('deadline')
        gate = DeniedBudget()
        called = []
        reader = linux_forge.ForgeReader(lambda request: called.append(request),
                    frozenset((linux_forge.ForgeRead.DRIVER,)))
        seen = linux_forge.observe(reader, budget=gate)
        self.assertEqual(len(gate.requests), 1)
        self.assertEqual(gate.requests[0].argv, ())
        self.assertIsNone(seen.drivers)
        self.assertFalse(called)

    def test_windows_adapter_preserves_open_gate(self):
        checks = windows_forge.diagnose(receipt('windows'), policy(), RULES, now=NOW)
        self.assertEqual(next(x for x in checks if x.id == 'windows-pairing').state, State.FAIL)
        with self.assertRaises(DiagError):
            linux_forge.diagnose(receipt('windows'), policy(), RULES, now=NOW)

    def test_windows_backing_rejects_remote_device_relative_and_links(self):
        path = 'C:\\SyntheticDocker\\disk'
        facts = tuple(windows_forge.BackingPathObservation(str(member), True, True, False, True)
                      for member in (PureWindowsPath(path), *PureWindowsPath(path).parents))
        self.assertTrue(windows_forge.backing_path_safe(path, facts))
        for unsafe in ('\\\\server\\share\\Docker', '\\\\?\\C:\\Docker', 'C:relative', 'relative',
                       'C:\\SyntheticDocker\\..\\disk', 'C:\\Docker:alternate'):
            self.assertFalse(windows_forge.backing_path_safe(unsafe, facts))
        for change in ({'reparse': True}, {'local_fixed_volume': False}, {'exists': False},
                       {'directory': False}, {'path': 'C:\\another'}):
            changed = (replace(facts[0], **change), *facts[1:])
            self.assertFalse(windows_forge.backing_path_safe(path, changed))
        self.assertFalse(windows_forge.backing_path_safe(path, facts[:-1]))


if __name__ == '__main__':
    unittest.main()
