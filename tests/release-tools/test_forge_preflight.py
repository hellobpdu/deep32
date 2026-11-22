#!/usr/bin/env python3
"""Offline checks of the Linux C10 evidence collector and its trust boundaries."""
import argparse
import contextlib
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[2] / 'src/release-tools'
spec = importlib.util.spec_from_file_location('synthetic_forge_preflight', TOOLS / 'forge_preflight.py')
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)
SCRATCH = None
PREFIX = 'synthetic-net.ts.net'
HOST = 'synthetic-forge.' + PREFIX
ASSET = 'FORGE-PREREQUISITES.json'


def alpha2_recipes():
    """forge-recipes-0.1.0-alpha.2.json is alpha.2's signed waiver record, kept as history. Its recipes still apply,
    so the tests read it with every waiver's release set to the release under test, in memory only."""
    rows = json.loads((TOOLS / 'forge-recipes-0.1.0-alpha.2.json').read_bytes())
    for row in rows:
        if isinstance(row.get('waiver'), dict):
            row['waiver']['release'] = subject.requirements.RELEASE
    return rows


class ForgeEvidence(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-forge-proof-', dir=SCRATCH)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / 'signed-folder'
        self.source.mkdir()
        self.data = self.root / 'forge-data'
        self.media = [
            dict(component='core', size=11, sha256='a' * 64),
            dict(component='embedder-ampere86', size=22, sha256='b' * 64),
            dict(component='ingestion', size=33, sha256='c' * 64),
        ]
        self.budget = {'media_components': self.media, 'data_bytes': 900, 'docker_bytes': 1200}
        self.recipe = dict(id='synthetic-linux-qualification', os_id='ubuntu', os_version='24.04',
                           architecture='amd64', journey='J2', accepted_on='2026-11-18',
                           recipe_sha256='d' * 64, acceptance_sha256='e' * 64)
        self.envelope = dict(format='deep32-forge-prerequisites/1', release='0.1.0-alpha.3',
                             capability_sha256='7' * 64, pins_sha256='8' * 64,
                             driver_floors=dict(tei_minimum_driver='810.199',
                                                cuda_minimum_driver='810.200',
                                                mineru_minimum_driver='700.0'),
                             minimum_vram_mib=10752, vram_policy='C1-install-matrix-tier-11',
                             recipes=[self.recipe])
        self.docker_root = '/synthetic/docker-root'
        self.observed = {
            'docker': {'OSType': 'linux', 'Driver': 'overlay2', 'DriverStatus': [],
                       'DockerRootDir': self.docker_root},
            'tailscale': {'BackendState': 'Running', 'Self': {'Online': True, 'DNSName': HOST + '.'},
                          'CurrentTailnet': {'MagicDNSSuffix': PREFIX}},
        }
        settings = subject.settings
        choices = {'format': 'deep32-settings', 'format_version': 1, 'role': 'forge',
                   **settings.DEFAULTS, 'forge_accept_ct': True}
        self.plan = settings.make_plan(choices, self.media[0]['sha256'], self.source, 'J2', 'docker', 'linux',
                                       'forge', settings.validator().profiles, self.data,
                                       media_components=self.media, tailnet_binding=settings.tailnet_digest(PREFIX))
        path = self.root / 'consented-plan.json'
        path.write_bytes(settings.canonical(self.plan))
        self.args = argparse.Namespace(source=str(self.source), forge_data_root=str(self.data),
                                       plan=str(path), accept_plan=self.plan['digest'], gpu_class='ampere86',
                                       journey='J2', architecture='x86_64', os_id='ubuntu', os_version='24.04')
        self.sample = '810.200, 8.6, 10752\n810.200.0, 8.7, 12288\n'

    def observe(self, name, *, existing):
        self.assertEqual(name, self.docker_root if existing else str(self.data))
        return 'synthetic-device', 5000

    def evaluate(self, envelope=True, port_error=None, stdout=None):
        completed = subprocess.CompletedProcess(subject.GPU_QUERY, 0, self.sample if stdout is None else stdout)
        run = mock.Mock(return_value=completed)
        with mock.patch.object(subject, 'signed_evidence', return_value=(self.budget, self.envelope if envelope is True else envelope)), \
                mock.patch.object(subject.ports, 'check_linux_ports', side_effect=port_error) as ports:
            result = subject.collect(self.args, self.observed, run=run, observe=self.observe)
        run.assert_called_once_with(subject.GPU_QUERY, capture_output=True, text=True, timeout=4)
        return result, ports

    def test_valid_recipe_driver_vram_tailnet_and_ports(self):
        result, ports = self.evaluate()
        self.assertEqual(result['prerequisites'], {
            'platform.recipe': True, 'gpu.release-envelope': True, 'gpu.driver': True,
            'gpu.capability': True, 'gpu.memory': True, 'host.membership-confirmed': True,
            'gpu.ports': True, 'docker.storage': True,
        })
        self.assertEqual([row['needed_bytes'] for row in result['storage_plan']], [900, 1200])
        self.assertEqual(ports.call_args.args[:3], (self.data, self.plan['settings'], HOST))
        self.assertNotIn(PREFIX, json.dumps(result))
        self.assertFalse(self.data.exists())

    def test_unqualified_store_has_no_storage_observation_or_pass(self):
        docker = self.observed['docker']
        docker['Driver'] = 'overlayfs'
        cases = [([['driver-type', 'io.containerd.snapshotter.v1']], None, 'docker-store-unknown'),
                 ([], None, 'docker-store-unknown'), (None, None, 'docker-store-unknown')]
        for status, expected, code in cases:
            docker['DriverStatus'] = status
            with self.subTest(status=status), mock.patch.object(self, 'observe') as observe:
                result, _ = self.evaluate()
                observe.assert_not_called()
                self.assertIs(result['prerequisites'].get('docker.storage'), expected)
                issue = result['docker_image_store']
                self.assertEqual(issue['code'], code)
                self.assertIn('classic overlay2', issue['next_step'])
                self.assertIn(code, result['details']['docker.storage'])
                self.assertEqual(result['storage_plan'], [])

    def test_second_pass_credits_the_joined_copy_already_in_place(self):
        # The getter writes the joined copy to <data root>.deep32-media/<sha256 of the component digests>
        # (deep32_settings.media_cache) before the final prerequisite pass.
        key = hashlib.sha256(''.join(row['sha256'] for row in self.media).encode('ascii')).hexdigest()
        cache = self.data.with_name(self.data.name + '.deep32-media') / key
        (cache / 'payload/images').mkdir(parents=True)
        (cache / 'payload/a.bin').write_bytes(b'x' * 10)
        (cache / 'payload/images/b.tar').write_bytes(b'y' * 20)
        manifest = json.dumps([{'path': 'a.bin', 'size': 10, 'sha256': 'a' * 64},
                               {'path': 'images/b.tar', 'size': 20, 'sha256': 'b' * 64}]).encode('ascii')
        (cache / 'PAYLOAD.json').write_bytes(manifest)
        (cache / 'PAYLOAD.SHA256').write_bytes(b'sums\n')
        (cache / 'RUNTIME.json').write_bytes(b'{}\n')
        copy_bytes = 10 + 20 + len(manifest) + len(b'sums\n') + len(b'{}\n')
        with mock.patch.object(self, 'observe', return_value=('synthetic-device', 5000)):
            rows = self.evaluate()[0]['storage_plan']
        self.assertEqual([row['needed_bytes'] for row in rows], [900 - copy_bytes, 1200])
        (cache / 'payload/images/b.tar').write_bytes(b'y' * 19)   # one byte short: not a complete copy
        with mock.patch.object(self, 'observe', return_value=('synthetic-device', 5000)):
            rows = self.evaluate()[0]['storage_plan']
        self.assertEqual([row['needed_bytes'] for row in rows], [900, 1200])
        (cache / 'payload/images/b.tar').write_bytes(b'y' * 20)
        devices = iter([('data-device', 5000), ('docker-device', 5000), ('other-device', 5000)])
        with mock.patch.object(self, 'observe', side_effect=lambda *args, **kwargs: next(devices)):
            rows = self.evaluate()[0]['storage_plan']
        self.assertEqual([row['needed_bytes'] for row in rows], [900, 1200])   # the copy is on another filesystem

    def test_recipe_mismatch_and_missing_or_bad_envelope(self):
        self.args.os_version = '22.04'
        result, _ = self.evaluate()
        self.assertIs(result['prerequisites']['platform.recipe'], False)
        for envelope in (None, False):
            with self.subTest(envelope=envelope):
                checks = self.evaluate(envelope=envelope)[0]['prerequisites']
                self.assertNotIn('platform.recipe', checks)
                self.assertNotIn('gpu.driver', checks)
                self.assertNotIn('gpu.memory', checks)
                self.assertIs(checks.get('gpu.release-envelope'), envelope)

    def test_different_tailnet_never_qualifies_membership_or_ports(self):
        self.observed['tailscale']['CurrentTailnet']['MagicDNSSuffix'] = 'other-synthetic.ts.net'
        self.observed['tailscale']['Self']['DNSName'] = 'synthetic-forge.other-synthetic.ts.net'
        result, ports = self.evaluate()
        self.assertIs(result['prerequisites']['host.membership-confirmed'], False)
        self.assertNotIn('gpu.ports', result['prerequisites'])
        ports.assert_not_called()
        self.assertNotIn('other-synthetic', json.dumps(result))

    def test_port_conflicts_keep_the_port_and_unknown_remains_unknown(self):
        for problem, expected in ((subject.ports.PortConflict('Port 8081 has an unverified local TCP listener.'), False),
                                  (subject.ports.PortUnavailable('Inspect the Forge port owner and retry.'), None)):
            with self.subTest(problem=problem):
                result, _ = self.evaluate(port_error=problem)
                self.assertIs(result['prerequisites'].get('gpu.ports'), expected)
                self.assertEqual(str(problem), result['details']['gpu.ports'])

    def test_any_visible_gpu_below_floor_or_in_wrong_class_refuses(self):
        result, _ = self.evaluate(stdout=self.sample + '810.199, 8.9, 10751\n')
        for name in ('gpu.driver', 'gpu.capability', 'gpu.memory'):
            self.assertFalse(result['prerequisites'][name])
        checks = self.evaluate(stdout='malformed')[0]['prerequisites']
        self.assertNotIn('gpu.driver', checks)
        self.assertNotIn('gpu.memory', checks)

    def test_plan_mismatch_fails_before_release_or_host_probe(self):
        self.args.accept_plan = 'f' * 64
        with mock.patch.object(subject, 'signed_evidence') as signed:
            with self.assertRaises(ValueError):
                subject.collect(self.args, self.observed)
        signed.assert_not_called()

    def test_signed_root_binds_exact_envelope_and_plan_selection(self):
        raw = json.dumps(self.envelope).encode('ascii')
        digest = hashlib.sha256(raw).hexdigest()
        index = dict(format_version=2, capability_manifest={'sha256': self.envelope['capability_sha256']},
                     bundles=[dict(bundle_id=row['component'], size=row['size'], sha256=row['sha256'])
                              for row in self.media])
        cap = dict(pins_sha256=self.envelope['pins_sha256'], release=self.envelope['release'])
        with mock.patch.object(subject.getter, 'metadata',
                               return_value=contextlib.nullcontext((self.source, index, self.source))), \
                mock.patch.object(subject.getter.manifest, 'read_mirror_capabilities', return_value=cap), \
                mock.patch.object(subject.getter.manifest, 'read_sums', return_value={ASSET: digest}), \
                mock.patch.object(subject.getter, 'select_bundles',
                                  return_value=[{'id': row['component']} for row in self.media]), \
                mock.patch.object(subject.getter, 'forge_budget_document', return_value=self.budget), \
                mock.patch.object(subject.getter, 'read_checked', return_value=raw) as read:
            self.assertEqual(subject.signed_evidence(self.args, self.plan['identity'])[1], self.envelope)
            read.assert_called_once_with(self.source / ASSET, max_bytes=65536)
            read.return_value = raw + b' '
            self.assertIs(subject.signed_evidence(self.args, self.plan['identity'])[1], False)
            wrong = dict(self.plan['identity'], media_components=self.media[::-1])
            with self.assertRaisesRegex(ValueError, 'signed Forge media selection'):
                subject.signed_evidence(self.args, wrong)

    def waive(self, **changes):
        """Accept the recipe by owner waiver in place of its passed lifecycle record."""
        row = {key: value for key, value in self.recipe.items() if key != 'acceptance_sha256'}
        waiver = dict(granted_by='release-owner', granted_on=row['accepted_on'], release='0.1.0-alpha.3',
                      recipe_id=row['id'], not_run=['The full install from the signed release.'],
                      evidence=[dict(file='SYNTHETIC-COMPONENT-EVIDENCE.md', sha256='f' * 64)])
        self.recipe = dict(row, waiver=dict(waiver, **changes))
        self.envelope['recipes'] = [self.recipe]
        subject.requirements.validate_recipes(self.envelope['recipes'])
        return subject.requirements.waiver_notice(self.recipe)

    def replan(self, journey):
        settings = subject.settings
        choices = {'format': 'deep32-settings', 'format_version': 1, 'role': 'forge',
                   **settings.DEFAULTS, 'forge_accept_ct': True}
        self.plan = settings.make_plan(choices, self.media[0]['sha256'], self.source, journey, 'docker', 'linux',
                                       'forge', settings.validator().profiles, self.data,
                                       media_components=self.media, tailnet_binding=settings.tailnet_digest(PREFIX))
        path = self.root / ('consented-plan-' + journey + '.json')
        path.write_bytes(settings.canonical(self.plan))
        self.args.plan, self.args.accept_plan, self.args.journey = str(path), self.plan['digest'], journey

    def test_owner_waiver_accepts_a_matching_host_with_one_plain_line(self):
        passed, _ = self.evaluate()
        self.assertNotIn('notice', passed)
        notice = self.waive()
        self.assertEqual(notice, 'Note: this host type is accepted for 0.1.0-alpha.3 under an owner waiver dated '
                                 '2026-11-18 (recipe synthetic-linux-qualification). I have not yet run a full '
                                 'install on it; FORGE-PREREQUISITES.json lists what was not run.')
        waived, ports = self.evaluate()
        self.assertEqual(waived.pop('notice'), notice)
        self.assertEqual(waived, passed)
        self.assertTrue(all(waived['prerequisites'].values()))
        self.assertEqual(len(waived['prerequisites']), 8)
        ports.assert_called_once()
        for architecture in ('x86_64', 'amd64', 'x64'):
            self.args.architecture = architecture
            self.assertEqual(self.evaluate()[0].get('notice'), notice)

    def test_a_waiver_opens_no_other_host_and_no_other_journey(self):
        notice = self.waive()
        host = dict(os_id='ubuntu', os_version='24.04', architecture='x86_64')
        for field, value in (('os_id', 'debian'), ('os_version', '22.04'), ('os_version', '24.10'),
                             ('architecture', 'aarch64')):
            with self.subTest(field=field, value=value):
                setattr(self.args, field, value)
                result, _ = self.evaluate()
                self.assertIs(result['prerequisites']['platform.recipe'], False)
                self.assertNotIn('notice', result)
                self.assertIn('accepted signed Linux host preparation', result['details']['platform.recipe'])
                setattr(self.args, field, host[field])
        self.assertEqual(self.evaluate()[0]['notice'], notice)
        self.recipe['journey'] = 'J4'
        result, _ = self.evaluate()
        self.assertIs(result['prerequisites']['platform.recipe'], False)
        self.assertNotIn('notice', result)

    def test_every_technical_gate_still_refuses_under_a_waiver(self):
        notice = self.waive()
        for stdout, failed in (('810.199, 8.6, 10752\n', 'gpu.driver'), ('810.200, 8.6, 10751\n', 'gpu.memory'),
                               ('810.200, 8.9, 10752\n', 'gpu.capability')):
            with self.subTest(failed=failed):
                checks = self.evaluate(stdout=stdout)[0]['prerequisites']
                self.assertEqual([name for name, state in checks.items() if state is False], [failed])
        self.assertEqual(self.evaluate()[0]['details'], {})
        low = self.evaluate(stdout='810.199, 8.6, 10751\n')[0]
        self.assertIn('810.200', low['details']['gpu.driver'])
        self.assertIn('10752 MiB', low['details']['gpu.memory'])
        self.assertEqual(low['notice'], notice)

        conflict = subject.ports.PortConflict('Port 8081 has an unverified local TCP listener.')
        self.assertIs(self.evaluate(port_error=conflict)[0]['prerequisites']['gpu.ports'], False)
        unknown = subject.ports.PortUnavailable('Inspect the Forge port owner and retry.')
        self.assertNotIn('gpu.ports', self.evaluate(port_error=unknown)[0]['prerequisites'])

        tailnet = self.observed['tailscale']
        saved = (tailnet['CurrentTailnet']['MagicDNSSuffix'], tailnet['Self']['DNSName'])
        tailnet['CurrentTailnet']['MagicDNSSuffix'] = 'other-synthetic.ts.net'
        tailnet['Self']['DNSName'] = 'synthetic-forge.other-synthetic.ts.net'
        result, ports = self.evaluate()
        self.assertIs(result['prerequisites']['host.membership-confirmed'], False)
        self.assertNotIn('gpu.ports', result['prerequisites'])
        ports.assert_not_called()
        tailnet['CurrentTailnet']['MagicDNSSuffix'], tailnet['Self']['DNSName'] = saved

        docker = self.observed['docker']
        docker.update(Driver='overlayfs', DriverStatus=[['driver-type', 'io.containerd.snapshotter.v1']])
        result, _ = self.evaluate()
        self.assertIsNone(result['prerequisites'].get('docker.storage'))
        self.assertEqual(result['storage_plan'], [])
        docker['Containerd'] = {'Address': '/run/containerd/containerd.sock'}
        with mock.patch.object(self, 'observe', return_value=('synthetic-device', 899)) as observe:
            result, _ = self.evaluate()
        self.assertIs(result['prerequisites']['docker.storage'], True)
        self.assertEqual(observe.call_args_list[1].args[0], '/var/lib/containerd')
        del docker['Containerd']
        docker.update(Driver='overlay2', DriverStatus=[])
        with mock.patch.object(self, 'observe', return_value=('synthetic-device', 899)):
            rows = self.evaluate()[0]['storage_plan']
        self.assertEqual([(row['needed_bytes'], row['free_bytes']) for row in rows], [(900, 899), (1200, 899)])

        for envelope in (None, False):
            with self.subTest(envelope=envelope):
                result = self.evaluate(envelope=envelope)[0]
                for name in ('platform.recipe', 'gpu.driver', 'gpu.memory'):
                    self.assertNotIn(name, result['prerequisites'])
                self.assertIs(result['prerequisites'].get('gpu.release-envelope'), envelope)
                self.assertNotIn('notice', result)

        self.args.accept_plan = 'f' * 64
        with mock.patch.object(subject, 'signed_evidence') as signed, self.assertRaises(ValueError):
            subject.collect(self.args, self.observed)
        signed.assert_not_called()

    def test_changed_or_misdirected_signed_waiver_is_not_evidence(self):
        self.waive()
        index = dict(format_version=2, capability_manifest={'sha256': self.envelope['capability_sha256']},
                     bundles=[dict(bundle_id=row['component'], size=row['size'], sha256=row['sha256'])
                              for row in self.media])
        cap = dict(pins_sha256=self.envelope['pins_sha256'], release=self.envelope['release'])

        def read_back(document, digest=None):
            raw = json.dumps(document).encode('ascii')
            sums = {ASSET: digest or hashlib.sha256(raw).hexdigest()}
            with mock.patch.object(subject.getter, 'metadata',
                                   return_value=contextlib.nullcontext((self.source, index, self.source))), \
                    mock.patch.object(subject.getter.manifest, 'read_mirror_capabilities', return_value=cap), \
                    mock.patch.object(subject.getter.manifest, 'read_sums', return_value=sums), \
                    mock.patch.object(subject.getter, 'select_bundles',
                                      return_value=[{'id': row['component']} for row in self.media]), \
                    mock.patch.object(subject.getter, 'forge_budget_document', return_value=self.budget), \
                    mock.patch.object(subject.getter, 'read_checked', return_value=raw):
                return subject.signed_evidence(self.args, self.plan['identity'])[1]

        def changed(**fields):
            document = json.loads(json.dumps(self.envelope))
            document['recipes'][0]['waiver'].update(fields)
            return document

        self.assertEqual(read_back(self.envelope)['recipes'], [self.recipe])
        signed = hashlib.sha256(json.dumps(self.envelope).encode('ascii')).hexdigest()
        # Any edit after signing changes the bytes that the signed checksum list names.
        for fields in (dict(not_run=['Nothing was left out.']), dict(granted_on='2026-11-19'),
                       dict(evidence=[dict(file='OTHER.md', sha256='e' * 64)])):
            with self.subTest(fields=fields):
                self.assertIs(read_back(changed(**fields), signed), False)
        # A correctly signed file whose waiver is for something else is still refused by the reader.
        for fields in (dict(release='0.1.0-alpha.1'), dict(recipe_id='synthetic-other-recipe'),
                       dict(granted_on='2026-11-19'), dict(granted_by='worker'), dict(not_run=[]),
                       dict(evidence=[]), dict(reviewed=True)):
            with self.subTest(signed_fields=fields):
                self.assertIs(read_back(changed(**fields)), False)
        missing = changed()
        del missing['recipes'][0]['waiver']['evidence']
        self.assertIs(read_back(missing), False)

    def test_alpha2_recipes_match_ubuntu_2404_on_its_two_linux_journeys(self):
        requirements = subject.requirements
        rows = requirements.cite(
            alpha2_recipes(), {'prerequisites.json': 'a' * 64},
            {name: 'b' * 64 for name in ('LINUX-MINERU-R3-EVIDENCE.md', 'WINDOWS-RAW-QUALIFICATION.md')})
        requirements.validate_recipes(rows)
        self.envelope['recipes'] = rows
        # install/deep32-inventory.sh reports ID and VERSION_ID from /etc/os-release and uname -m.
        # Ubuntu 24.04 gives ubuntu, 24.04 and x86_64 on bare metal and inside WSL 2 alike.
        self.assertEqual((self.args.os_id, self.args.os_version, self.args.architecture),
                         ('ubuntu', '24.04', 'x86_64'))
        for journey in ('J2', 'J4'):
            with self.subTest(journey=journey):
                self.replan(journey)
                result, _ = self.evaluate()
                self.assertTrue(all(result['prerequisites'].values()))
                self.assertIn('(recipe ubuntu-24.04-docker-engine-' + journey.lower() + ')', result['notice'])
        self.replan('J1')
        result, _ = self.evaluate()
        self.assertIs(result['prerequisites']['platform.recipe'], False)
        self.assertNotIn('notice', result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scratch-root', type=Path, required=True)
    args, rest = parser.parse_known_args()
    SCRATCH = args.scratch_root.resolve()
    unittest.main(argv=[sys.argv[0], *rest], verbosity=2)
