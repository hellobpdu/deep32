"""Synthetic J05 capability contracts; no native tool/network qualification."""
from __future__ import annotations

from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
import base64
import builtins
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src' / 'diagnostics'))
from deep32_diag.errors import DiagError
from deep32_diag import models, report, runner, trust

SENTINEL = 'synthetic-secret-sentinel-a7b83f'
NOW_TEXT = '2026-11-17T12:00:00Z'
NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc).timestamp()
MODEL = 'public-demo/model'
REVISION = '1' * 40
FINGERPRINT = '2' * 64
GENERATION = 'public-demo-generation'
PUBLIC_IDS = frozenset((MODEL, REVISION, FINGERPRINT, GENERATION))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')


def good_signature():
    return ('[GNUPG:] GOODSIG A078C5F25D025DD6 Synthetic test key\n'
            '[GNUPG:] VALIDSIG ' + trust.PIN + ' 2026-11-17 1794916800 0 4 0 1 10 00 ' + trust.PIN + '\n').encode('ascii')


def snapshot_tree(root):
    result = {}
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        result[str(path.relative_to(root))] = (
            stat.S_IFMT(info.st_mode), info.st_mode, info.st_size, info.st_mtime_ns,
            info.st_uid if hasattr(info, 'st_uid') else None,
            info.st_gid if hasattr(info, 'st_gid') else None,
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() and not path.is_symlink() else None,
        )
    return result


class SyntheticCase(unittest.TestCase):
    def setUp(self):
        base = Path(os.environ['D32_SUITE_SCRATCH'])
        if not base.is_absolute() or base.is_symlink():
            raise AssertionError('Synthetic scratch must be an absolute existing test root')
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='synthetic-j05-', dir=base)
        self.work = Path(self.temporary.name)
        if not self.work.resolve().is_relative_to(base.resolve()):
            raise AssertionError('Synthetic temporary fixture escaped the selected root')
        self.addCleanup(self.temporary.cleanup)

    def assert_code(self, expected, function, *args, **kwargs):
        with self.assertRaises(DiagError) as caught:
            function(*args, **kwargs)
        self.assertEqual(str(caught.exception), expected)
        self.assertNotIn(SENTINEL, repr(caught.exception))
        return caught.exception

    def make_trust(self, manifest=None, observer=None):
        manifest = manifest or {
            'format': 'deep32-release-manifest', 'format_version': 1,
            'release': {'version': trust.RELEASE},
            'signer': {'type': 'openpgp', 'fingerprint': trust.PIN},
        }
        data = {'manifest': canonical(manifest), 'signature': b'synthetic detached signature',
                'keyring': b'synthetic public keyring', 'verifier': b'synthetic verifier image'}
        paths = {}
        for slot, raw in data.items():
            path = self.work / slot
            path.write_bytes(raw)
            paths[slot] = str(path)
        home = self.work / 'existing-read-only-home'
        home.mkdir()
        self.files = runner.ExistingFiles(paths)
        self.paths = paths
        self.qualification = trust.VerifierQualification(
            paths['verifier'], str(home), hashlib.sha256(data['verifier']).hexdigest(),
            hashlib.sha256(data['keyring']).hexdigest(), 'synthetic-qualification', True, True)
        self.requests = []
        def fake(request):
            self.requests.append(request)
            return runner.CommandOutput(0, good_signature())
        self.verifier = trust.ReleaseVerifier(self.files, paths, qualification=self.qualification,
                                             observer=observer or fake)
        return self.verifier

    def receipt(self, release, *, content=None):
        inventory = {
            'services': [{'alias': 'embeddings', 'image': 'sha256:' + '3' * 64,
                          'required': True, 'active_job': False}],
            'models': [{'alias': 'embeddings', 'id': MODEL, 'revision': REVISION}],
            'ports': [{'alias': 'embeddings', 'port': 8081}],
            'routes': [{'alias': 'mcp', 'port': 443, 'route': '/mcp'}],
            'filesystems': [{'alias': 'data', 'required_bytes': 100, 'safe_running_bytes': 10}],
            'credential_slots': [{'alias': 'selected-agent', 'slot': 'headend-bearer'}],
        }
        raw = {'schema': 'deep32.doctor-install.v1', 'role': 'headend', 'platform': 'linux',
               'release': {'version': trust.RELEASE, 'manifest_sha256': release.manifest_sha256,
                           'signer_fingerprint': trust.PIN}, 'inventory': inventory, 'content': content}
        return raw, inventory

    def validate(self, raw, release, inventory, **kwargs):
        return trust._validate_projected_receipt(canonical(raw), release, inventory,
                                      role='headend', platform='linux', **kwargs)


class ExistingReaderTests(SyntheticCase):
    def test_exact_slot_snapshot_and_no_mutation(self):
        path = self.work / 'existing-public.json'
        path.write_bytes(b'{"ok":true}')
        files = runner.ExistingFiles({'state': path})
        before = snapshot_tree(self.work)
        snap = files.read_existing_regular('state')
        self.assertEqual(snap.data, b'{"ok":true}')
        self.assertTrue(files.unchanged('state', snap))
        self.assertEqual(before, snapshot_tree(self.work))
        self.assertNotIn('true', repr(snap))

    def test_missing_state_stays_missing(self):
        missing = self.work / 'absent-root' / 'state.json'
        files = runner.ExistingFiles({'state': missing})
        self.assert_code('missing-file', files.read_existing_regular, 'state')
        self.assertFalse(missing.parent.exists())

    def test_unknown_and_relative_slots_refused(self):
        self.assert_code('unsafe-file', runner.ExistingFiles, {'state': 'relative.json'})
        self.assert_code('unsafe-file', runner.ExistingFiles, {'state': None})
        self.assert_code('unsafe-file', runner.ExistingFiles, [])
        files = runner.ExistingFiles({'state': self.work / 'missing'})
        self.assert_code('unsafe-file', files.read_existing_regular, 'other')

    def test_directory_and_oversize_are_refused(self):
        path = self.work / 'large'
        path.write_bytes(b'x' * 20)
        files = runner.ExistingFiles({'directory': self.work, 'large': path})
        self.assert_code('unsafe-file', files.read_existing_regular, 'directory')
        self.assert_code('oversized', files.read_existing_regular, 'large', maximum=10)

    def test_changed_existing_file_is_not_stable(self):
        path = self.work / 'state'
        path.write_bytes(b'first')
        files = runner.ExistingFiles({'state': path})
        snap = files.read_existing_regular('state')
        path.write_bytes(b'other')
        try:
            self.assertFalse(files.unchanged('state', snap))
        except DiagError as error:
            self.assertEqual(error.code, 'changed-file')

    def test_symlink_and_reparse_component_types_are_denied(self):
        class FakePath:
            parents = ()
            def __init__(self, mode, attributes=0):
                self.mode, self.attributes = mode, attributes
            def lstat(self):
                return type('Info', (), {'st_mode': self.mode, 'st_file_attributes': self.attributes})()
        self.assert_code('unsafe-file', runner._unlinked, FakePath(stat.S_IFLNK | 0o600))
        self.assert_code('unsafe-file', runner._unlinked, FakePath(stat.S_IFDIR | 0o700, 0x400))

    def test_protected_slot_uses_read_only_acl_callback(self):
        path = self.work / 'synthetic-credential'
        path.write_bytes(SENTINEL.encode('ascii'))
        seen = []
        files = runner.ExistingFiles({'credential': path}, verify_access=lambda p: seen.append(p) or True)
        self.assertEqual(files.read_existing_regular('credential', protected=True).data, SENTINEL.encode('ascii'))
        self.assertEqual(seen, [path])
        denied = runner.ExistingFiles({'credential': path}, verify_access=lambda _: False)
        self.assert_code('unsafe-file', denied.read_existing_regular, 'credential', protected=True)

    def test_strict_json_rejects_duplicate_nonfinite_and_invalid_bytes(self):
        for raw in (b'{"a":1,"a":2}', b'{"n":NaN}', b'\xff', b'{'):
            self.assert_code('invalid-json', runner.strict_json, raw)
        self.assert_code('oversized', runner.strict_json, b'123', 2)


class CommandTests(SyntheticCase):
    def command(self, observer=None, **kwargs):
        return runner.ReadOnlyCommands(
            {'tailscale': self.work / 'approved-tailscale', 'docker': self.work / 'approved-docker'},
            observer=observer or (lambda _: runner.CommandOutput(0, b'{"ok":true}')),
            qualified=True, docker_endpoint='npipe:////./pipe/dockerDesktopLinuxEngine', **kwargs)

    def test_unqualified_default_never_calls_observer(self):
        called = []
        commands = runner.ReadOnlyCommands({'tailscale': self.work / 'approved'},
                                          observer=lambda request: called.append(request))
        self.assert_code('unqualified-command', commands.observe_command, runner.Command.TAILSCALE_STATUS)
        self.assertFalse(called)

    def test_enum_templates_and_redacted_request(self):
        seen = []
        commands = self.command(observer=lambda request: seen.append(request) or runner.CommandOutput(0, b'{}'))
        commands.observe_command(runner.Command.TAILSCALE_STATUS)
        commands.observe_command(runner.Command.TAILSCALE_SERVE_STATUS)
        self.assertEqual(seen[0].argv[1:], ('status', '--json'))
        self.assertEqual(seen[1].argv[1:], ('serve', 'status', '--json'))
        self.assertNotIn(str(self.work), repr(seen[0]))
        self.assertNotIn(str(self.work), repr(runner.CommandOutput(0, SENTINEL.encode('ascii'))))
        self.assert_code('unsafe-command', commands.observe_command, 'tailscale serve reset')

    def test_docker_only_recorded_local_endpoint_project_and_container(self):
        seen = []
        container = 'a' * 64
        commands = self.command(observer=lambda request: seen.append(request) or runner.CommandOutput(0, b'{}'),
                                compose_file=self.work / 'owned-compose.yaml', project='deep32-synthetic',
                                containers=(container,))
        for enum in (runner.Command.DOCKER_VERSION, runner.Command.DOCKER_INFO,
                     runner.Command.DOCKER_COMPOSE_PS):
            commands.observe_command(enum)
        commands.observe_command(runner.Command.DOCKER_INSPECT, container=container)
        inspect = ' '.join(seen[-1].argv)
        for forbidden in ('Config.Env', 'Mounts', 'Health.Log', 'Secrets'):
            self.assertNotIn(forbidden, inspect)
        self.assertIn('--host', seen[-1].argv)
        self.assert_code('unsafe-command', commands.observe_command, runner.Command.DOCKER_INSPECT, container='b' * 64)
        self.assert_code('unsafe-target', runner.ReadOnlyCommands, {}, docker_endpoint='tcp://remote:2375')

    def test_arbitrary_paths_argv_and_malformed_constructor_refused(self):
        for values in ({'arbitrary': self.work / 'tool'}, {'tailscale': 'relative'}, {'tailscale': None}):
            self.assert_code('unsafe-command', runner.ReadOnlyCommands, values)
        for kwargs in ({'containers': (None,)}, {'project': []}, {'compose_file': []}):
            self.assert_code('unsafe-command', runner.ReadOnlyCommands, {}, **kwargs)

    def test_output_caps_failures_and_sentinel_errors(self):
        for output, code in ((runner.CommandOutput(0, b'x' * (runner.MAX_LOCAL + 1)), 'oversized'),
                             (runner.CommandOutput(1, SENTINEL.encode('ascii')), 'command-failed'),
                             (runner.CommandOutput(True, b'{}'), 'command-failed'),
                             (runner.CommandOutput(0, SENTINEL), 'command-failed')):
            self.assert_code(code, self.command(observer=lambda _, output=output: output).observe_command,
                             runner.Command.TAILSCALE_STATUS)
        def bad(_):
            raise RuntimeError(SENTINEL)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            error = self.assert_code('command-failed', self.command(observer=bad).observe_command,
                                     runner.Command.TAILSCALE_STATUS)
            self.assertIsNone(error.__context__)
        self.assertNotIn(SENTINEL, out.getvalue() + err.getvalue())

    def test_absolute_deadline_and_global_render_reserve(self):
        clock = [0.0]
        budget = runner.Budget(clock=lambda: clock[0])
        def late(request):
            self.assertEqual(request.deadline, 10.0)
            clock[0] = 10.01
            return runner.CommandOutput(0, b'{}')
        self.assert_code('deadline', self.command(observer=late, budget=budget).observe_command,
                         runner.Command.TAILSCALE_STATUS)
        clock[0] = 84.0
        self.assertEqual(budget.deadline(), 85.0)
        clock[0] = 85.0
        self.assert_code('deadline', budget.deadline)


    def test_shared_budget_rejects_fifth_inflight_and_releases_slots(self):
        budget = runner.Budget(clock=lambda: 0.0)
        request = runner.CommandRequest(('synthetic-approved-tool',), 10.0)
        depth, maximum = [0], [0]
        def nested(active):
            depth[0] += 1
            maximum[0] = max(maximum[0], depth[0])
            try:
                if depth[0] < 4:
                    budget.observe(nested, active)
                else:
                    self.assert_code('deadline', budget.observe, nested, active)
                return runner.CommandOutput(0, b'{}')
            finally:
                depth[0] -= 1
        self.assertEqual(budget.observe(nested, request).returncode, 0)
        self.assertEqual((depth[0], maximum[0]), (0, 4))
        def failing(_):
            raise RuntimeError(SENTINEL)
        error = self.assert_code('command-failed', budget.observe, failing, request)
        self.assertIsNone(error.__context__)
        self.assertEqual(budget.observe(lambda _: runner.CommandOutput(0, b'{}'), request).stdout, b'{}')

    def test_forged_deadline_cannot_bypass_global_render_reserve(self):
        clock = [0.0]
        budget = runner.Budget(clock=lambda: clock[0])
        clock[0] = 89.0
        calls = []
        forged = runner.CommandRequest(('synthetic-approved-tool',), 1000.0)
        self.assert_code('deadline', budget.observe, lambda request: calls.append(request), forged)
        self.assertEqual(calls, [])

class TrustTests(SyntheticCase):
    def test_qualified_fake_exact_template_and_stable_tree(self):
        verifier = self.make_trust()
        before = snapshot_tree(self.work)
        release = verifier.verify()
        self.assertEqual(release.fingerprint, trust.PIN)
        self.assertEqual(release.manifest_sha256, hashlib.sha256(release.manifest).hexdigest())
        self.assertEqual(before, snapshot_tree(self.work))
        request = self.requests[0]
        self.assertEqual(request.argv[1:3], ('--homedir', self.qualification.home))
        self.assertEqual(request.argv[3:7], ('--keyring', self.paths['keyring'], '--status-fd', '1'))
        self.assertEqual(request.max_bytes, 65536)
        self.assertNotIn(str(self.work), repr(release) + repr(request))

    def test_unqualified_default_has_no_probe(self):
        self.make_trust()
        verifier = trust.ReleaseVerifier(self.files, self.paths)
        self.assert_code('unqualified-verifier', verifier.verify)
        self.assertFalse(self.requests)

    def test_wrong_measured_verifier_and_keyring_are_refused(self):
        self.make_trust()
        for name in ('binary_sha256', 'keyring_sha256'):
            q = replace(self.qualification, **{name: '0' * 64})
            verifier = trust.ReleaseVerifier(self.files, self.paths, qualification=q,
                                             observer=lambda _: self.fail('must not invoke verifier'))
            self.assert_code('unqualified-verifier', verifier.verify)

    def test_slot_path_mismatch_is_refused(self):
        self.make_trust()
        paths = dict(self.paths, manifest=str(self.work / 'other'))
        verifier = trust.ReleaseVerifier(self.files, paths, qualification=self.qualification,
                                         observer=lambda _: self.fail('must not invoke verifier'))
        self.assert_code('unqualified-verifier', verifier.verify)

    def test_signature_acceptance_exactly_one_binary_primary(self):
        good = good_signature()
        self.assertEqual(trust.signature_status(runner.CommandOutput(0, good)), trust.PIN)
        bad_outputs = (good + good, good.replace(b' 00 ', b' 01 '),
                       good.replace(trust.PIN.encode('ascii'), b'0' * 40),
                       good + b'[GNUPG:] BADSIG synthetic\n',
                       good + b'[GNUPG:] EXPKEYSIG synthetic\n',
                       b'[GNUPG:] GOODSIG synthetic\n', b'', b'\xff')
        for raw in bad_outputs:
            self.assert_code('bad-signature', trust.signature_status, runner.CommandOutput(0, raw))
        self.assert_code('bad-signature', trust.signature_status, runner.CommandOutput(1, good))
        self.assert_code('bad-signature', trust.signature_status, runner.CommandOutput(0, b'x' * 65537))
        self.assert_code('bad-signature', trust.signature_status, runner.CommandOutput(True, good))
        self.assert_code('bad-signature', trust.signature_status, runner.CommandOutput(0, SENTINEL))

    def test_manifest_version_format_signer_and_duplicate_keys_refused(self):
        for mutation in ({'release': {'version': '0.1.0-alpha.1'}}, {'format': 'other'},
                         {'format_version': True}, {'signer': {'type': 'other', 'fingerprint': trust.PIN}}):
            root = self.work / str(len(list(self.work.iterdir())))
            root.mkdir()
            old = self.work
            self.work = root
            value = {'format': 'deep32-release-manifest', 'format_version': 1,
                     'release': {'version': trust.RELEASE}, 'signer': {'type': 'openpgp', 'fingerprint': trust.PIN}}
            value.update(mutation)
            self.assert_code('manifest-mismatch', self.make_trust(value).verify)
            self.work = old

    def test_ordinary_concurrent_manifest_change_fails(self):
        def changed(_):
            Path(self.paths['manifest']).write_bytes(b'{}')
            return runner.CommandOutput(0, good_signature())
        verifier = self.make_trust(observer=changed)
        self.assert_code('snapshot-changed', verifier.verify)

    def test_verifier_delayed_fake_respects_absolute_deadline(self):
        self.make_trust()
        clock = [0.0]
        budget = runner.Budget(clock=lambda: clock[0])
        def late(request):
            clock[0] = request.deadline + .1
            return runner.CommandOutput(0, good_signature())
        verifier = trust.ReleaseVerifier(self.files, self.paths, qualification=self.qualification,
                                         observer=late, budget=budget)
        self.assert_code('deadline', verifier.verify)

    def test_receipt_inventory_and_immutable_public_snapshot(self):
        release = self.make_trust().verify()
        raw, inventory = self.receipt(release)
        before = snapshot_tree(self.work)
        receipt = self.validate(raw, release, inventory)
        self.assertEqual(receipt.role, 'headend')
        with self.assertRaises(TypeError):
            receipt.inventory['services'][0]['image'] = 'changed'
        self.assertEqual(before, snapshot_tree(self.work))

    def test_receipt_cannot_mint_release_or_ownership(self):
        release = self.make_trust().verify()
        raw, inventory = self.receipt(release)
        forged = trust.VerifiedRelease(release.manifest, release.manifest_sha256, trust.PIN, 'synthetic')
        self.assert_code('untrusted-release', self.validate, raw, forged, inventory)
        raw['inventory']['ports'][0]['port'] = 8082
        # Preserve the authoritative inventory independently from the receipt.
        authoritative = json.loads(canonical(inventory))
        authoritative['ports'][0]['port'] = 8081
        self.assert_code('inventory-mismatch', self.validate, raw, release, authoritative)

    def test_receipt_release_hash_fingerprint_version_and_unknown_fields_refused(self):
        release = self.make_trust().verify()
        raw, inventory = self.receipt(release)
        for key, value in (('version', 'old'), ('manifest_sha256', '0' * 64),
                           ('signer_fingerprint', '0' * 40)):
            changed = json.loads(canonical(raw))
            changed['release'][key] = value
            self.assert_code('untrusted-release', self.validate, changed, release, inventory)
        raw['credential'] = SENTINEL
        self.assert_code('invalid-receipt', self.validate, raw, release, inventory)

    def test_content_requires_independent_authenticated_expectation(self):
        release = self.make_trust().verify()
        content = {'generation': GENERATION, 'fingerprint': FINGERPRINT}
        raw, inventory = self.receipt(release, content=content)
        self.assert_code('inventory-mismatch', self.validate, raw, release, inventory)
        receipt = self.validate(raw, release, inventory, authoritative_content=content)
        self.assertEqual(receipt.generation, GENERATION)
        changed = dict(content, fingerprint='3' * 64)
        self.assert_code('inventory-mismatch', self.validate, raw, release, inventory, authoritative_content=changed)

    def test_inventory_unknown_fields_duplicate_aliases_and_numeric_bools_refused(self):
        release = self.make_trust().verify()
        original, inventory = self.receipt(release)
        for change in ('unknown', 'duplicate', 'bool', 'unsafe-route', 'secret-slot'):
            raw = json.loads(canonical(original))
            if change == 'unknown': raw['inventory']['services'][0]['secret'] = SENTINEL
            if change == 'duplicate': raw['inventory']['ports'] *= 2
            if change == 'bool': raw['inventory']['ports'][0]['port'] = True
            if change == 'unsafe-route': raw['inventory']['routes'][0]['route'] = '/admin'
            if change == 'secret-slot': raw['inventory']['credential_slots'][0]['slot'] = SENTINEL
            self.assert_code('invalid-receipt', self.validate, raw, release, inventory)


    def test_unsupported_signed_manifest_version_two_refused(self):
        value = {'format': 'deep32-release-manifest', 'format_version': 2,
                 'release': {'version': trust.RELEASE},
                 'signer': {'type': 'openpgp', 'fingerprint': trust.PIN}}
        self.assert_code('manifest-mismatch', self.make_trust(value).verify)

    def test_dataclass_replace_cannot_preserve_authenticated_release_seal(self):
        release = self.make_trust().verify()
        raw, inventory = self.receipt(release)
        forged = replace(release, manifest_sha256='0' * 64)
        self.assertIsNone(forged._seal)
        raw['release']['manifest_sha256'] = forged.manifest_sha256
        self.assert_code('untrusted-release', self.validate, raw, forged, inventory)
        copied = replace(release)
        self.assertIsNone(copied._seal)
        self.assert_code('untrusted-release', self.validate, self.receipt(release)[0], copied, inventory)

    def test_model_dotdot_segments_refused_in_receipt(self):
        release = self.make_trust().verify()
        raw, inventory = self.receipt(release)
        raw['inventory']['models'][0]['id'] = '../..'
        self.assert_code('invalid-receipt', self.validate, raw, release, inventory)

class ModelTests(SyntheticCase):
    def test_per_check_allowlist_and_numeric_types(self):
        self.assertEqual(models.observed('disk.data', {'free_bytes': 100}), {'free_bytes': 100})
        for check, values in (('disk.data', {'model': MODEL}), ('port.pairing', {'port': True}),
                              ('port.pairing', {'port': 65536}), ('dense', {'healthy': 1}),
                              ('peer-clock.forge', {'rtt_ms': -1}), ('peer-clock.forge', {'rtt_ms': float('nan')}),
                              ('tailscale-signin', {'state': SENTINEL}), ('driver', {'version': SENTINEL})):
            self.assert_code('invalid-observation', models.observed, check, values, public_identities=PUBLIC_IDS)

    def test_public_identity_only_from_authenticated_allowlist(self):
        self.assert_code('invalid-observation', models.observed, 'model.embeddings', {'revision': REVISION})
        value = models.observed('model.embeddings', {'revision': REVISION, 'model': MODEL}, public_identities=PUBLIC_IDS)
        self.assertEqual(value['revision'], REVISION)
        self.assert_code('invalid-observation', models.observed, 'index-generation', {'generation': SENTINEL}, public_identities=PUBLIC_IDS)

    def attest(self, row=None, **kwargs):
        row = row or {'verified': True, 'checked_at': NOW_TEXT, 'model_id': MODEL,
                      'revision': REVISION, 'measurement_verified': True, 'device_kind': 'gpu'}
        return models.observe_attestation({'gpu_ext': 'on', 'embed': row}, 'embed',
                                          expected_model=MODEL, expected_revision=REVISION,
                                          now=NOW, maximum_age=60, **kwargs)

    def test_attestation_verified_identity_and_device(self):
        self.assertEqual(self.attest().state, models.State.OK)
        for changed, state, reason in (({'device_kind': 'cpu'}, models.State.FAIL, 'device'),
                                       ({'model_id': 'other/model'}, models.State.FAIL, 'mismatch'),
                                       ({'measurement_verified': False}, models.State.FAIL, 'measurement')):
            row = {'verified': True, 'checked_at': NOW_TEXT, 'model_id': MODEL,
                   'revision': REVISION, 'measurement_verified': True, 'device_kind': 'gpu'}
            row.update(changed)
            result = self.attest(row)
            self.assertEqual((result.state, result.reason), (state, reason))

    def test_stale_missing_and_authentication_attestation_are_honest(self):
        self.assertEqual(self.attest({'verified': True, 'checked_at': '2026-11-17T11:00:00Z'}).reason, 'stale')
        self.assertEqual(self.attest({'verified': True, 'checked_at': NOW_TEXT}).reason, 'unmeasured')
        self.assertEqual(self.attest({'verified': True, 'checked_at': SENTINEL}).reason, 'freshness-unknown')
        result = self.attest({'verified': False, 'reason': 'forge-auth-rejected:' + SENTINEL})
        self.assertEqual((result.state, result.reason), (models.State.FAIL, 'authentication'))
        self.assertNotIn(SENTINEL, repr(result))


    def test_tailnet_path_l3_direct_relay_missing_and_unrelated_peer(self):
        expected_fix = ('Allow outbound UDP between the two machines so Tailscale can use a direct path; '
                        'relayed links add tens of milliseconds to every search.')
        rows = {'selected': {'CurAddr': SENTINEL, 'Relay': SENTINEL},
                'unrelated': {'CurAddr': SENTINEL, 'Relay': SENTINEL}}
        direct = models.tailnet_path({'Peer': rows}, 'selected')
        self.assertEqual((direct.state, direct.path, direct.fix), (models.State.OK, 'direct', None))
        rows['selected'] = {'CurAddr': '', 'Relay': SENTINEL}
        relayed = models.tailnet_path({'Peer': rows}, 'selected')
        self.assertEqual((relayed.state, relayed.path, relayed.fix), (models.State.WARN, 'relayed', expected_fix))
        unknown = models.tailnet_path({'Peer': rows}, 'absent')
        self.assertEqual((unknown.state, unknown.path), (models.State.WARN, 'unknown'))
        self.assertNotIn(SENTINEL, repr(direct) + repr(relayed) + repr(unknown))
        self.assert_code('invalid-observation', models.tailnet_path,
                         {'Peer': {'selected': {'CurAddr': [], 'Relay': ''}}}, 'selected')

    def test_dotdot_model_cannot_be_printed_or_be_expected_identity(self):
        self.assert_code('invalid-observation', models.observed, 'model.embeddings',
                         {'model': '../..'}, public_identities=frozenset(('../..',)))
        self.assert_code('invalid-observation', models.observe_attestation,
                         {'gpu_ext': 'on', 'embed': {'verified': True}}, 'embed',
                         expected_model='../..', expected_revision=REVISION,
                         now=NOW, maximum_age=60)

class ReportTests(SyntheticCase):
    def build(self, rows, **kwargs):
        return report.build_report('headend', 'linux', 'demo-host', NOW_TEXT, rows, **kwargs)

    def test_exit_summary_skip_dependency_and_pure_json(self):
        rows = [report.row('installation', models.State.OK, report.Reason.PASSED, {'matched': True}),
                report.row('gateway-auth', models.State.FAIL, report.Reason.UNAVAILABLE),
                report.row('mcp-rag-status', models.State.SKIP, report.Reason.BLOCKED, blocked_by=('gateway-auth',)),
                report.row('first-start', models.State.SKIP, report.Reason.INAPPLICABLE),
                report.row('agent-signin', models.State.WARN, report.Reason.UNSUPPORTED)]
        value = self.build(rows)
        self.assertEqual(value['summary'], {'ok': 1, 'warn': 1, 'fail': 1, 'skip': 2})
        self.assertEqual(value['exit_code'], 2)
        self.assertTrue(all(row['fix'] for row in value['checks'][1:]))
        encoded = report.encode_report('headend', 'linux', 'demo-host', NOW_TEXT, rows)
        self.assertEqual(json.loads(encoded), value)
        self.assertLess(len(encoded.encode('ascii')), 256 * 1024)

    def test_ok_and_warn_precedence(self):
        ok = report.row('installation', models.State.OK, report.Reason.PASSED)
        warn = report.row('agent-signin', models.State.WARN, report.Reason.UNSUPPORTED)
        self.assertEqual(self.build([ok])['exit_code'], 0)
        self.assertEqual(self.build([ok, warn])['exit_code'], 1)

    def test_skip_cannot_hide_missing_or_nonfailed_prerequisite(self):
        blocked = report.row('mcp-rag-status', models.State.SKIP, report.Reason.BLOCKED, blocked_by=('gateway-auth',))
        self.assert_code('invalid-report', self.build, [blocked])
        good = report.row('gateway-auth', models.State.OK, report.Reason.PASSED)
        self.assert_code('invalid-report', self.build, [good, blocked])
        self.assert_code('invalid-report', report.row, 'mcp-rag-status', models.State.SKIP, report.Reason.BLOCKED)

    def test_duplicate_ids_row_forgery_and_limits(self):
        value = report.row('installation', models.State.OK, report.Reason.PASSED)
        self.assert_code('invalid-report', self.build, [value, value])
        self.assert_code('invalid-report', self.build, [report.Row('installation', models.State.OK, report.Reason.PASSED)])
        maximum = [report.row('port.p' + str(i), models.State.OK, report.Reason.PASSED) for i in range(256)]
        self.assertEqual(len(self.build(maximum)['checks']), 256)
        self.assert_code('invalid-report', self.build, maximum + [report.row('port.extra', models.State.OK, report.Reason.PASSED)])

    def test_unsupported_role_platform_and_incomplete_run(self):
        ok = report.row('installation', models.State.OK, report.Reason.PASSED)
        self.assert_code('invalid-report', report.build_report, 'forge', 'macos', 'demo-host', NOW_TEXT, [ok])
        self.assert_code('invalid-report', self.build, [ok], complete=False)
        failed = report.row('doctor', models.State.FAIL, report.Reason.UNAVAILABLE)
        self.assertFalse(self.build([failed], complete=False)['complete'])
        argument = report.row('arguments', models.State.FAIL, report.Reason.UNAVAILABLE)
        self.assertEqual(report.build_report(None, None, 'demo-host', NOW_TEXT, [argument])['exit_code'], 2)

    def test_malformed_types_states_and_secret_observations_refused(self):
        for values in ([], '', 0, False):
            self.assert_code('invalid-observation', report.row, 'installation', models.State.OK, report.Reason.PASSED, values)
        self.assert_code('invalid-report', report.row, 'installation', models.State.FAIL, report.Reason.PASSED)
        self.assert_code('invalid-report', report.row, 'installation', models.State.OK, report.Reason.UNAVAILABLE)
        self.assert_code('invalid-observation', report.row, 'installation', models.State.OK, report.Reason.PASSED, {'version': SENTINEL})
        self.assert_code('invalid-report', report.build_report, 'headend', 'linux', 'demo\x1b-host', NOW_TEXT,
                         [report.row('installation', models.State.OK, report.Reason.PASSED)])

    def test_snapshot_changed_requires_warn_and_fixed_reason(self):
        changed = report.row('snapshot-changed', models.State.WARN, report.Reason.SNAPSHOT_CHANGED)
        self.assertEqual(self.build([changed])['exit_code'], 1)
        self.assert_code('invalid-report', report.row, 'installation', models.State.WARN, report.Reason.SNAPSHOT_CHANGED)


    def test_report_timestamp_is_strict_utc_datetime(self):
        item = report.row('installation', models.State.OK, report.Reason.PASSED)
        for stamp in ('2026-11-17Z', '2026-11-17 12:00:00Z', '2026-11-17T12:00:00+00:00',
                      '2026-02-30T12:00:00Z', '2026-11-17T12:00:60Z'):
            self.assert_code('invalid-report', report.build_report,
                             'headend', 'linux', 'demo-host', stamp, [item])
        value = report.build_report('headend', 'linux', 'demo-host', '2026-11-17T12:00:00.123Z', [item])
        self.assertEqual(value['checked_at'], '2026-11-17T12:00:00.123Z')

    def test_infinite_row_iterable_consumes_at_most_257_values(self):
        item = report.row('installation', models.State.OK, report.Reason.PASSED)
        class InfiniteRows:
            reads = 0
            def __iter__(self): return self
            def __next__(self):
                self.reads += 1
                if self.reads > 257:
                    raise AssertionError('Report over-read the bounded row source')
                return item
        source = InfiniteRows()
        self.assert_code('invalid-report', self.build, source)
        self.assertEqual(source.reads, 257)

    def test_dataclass_replace_cannot_preserve_validated_row_seal(self):
        original = report.row('installation', models.State.OK, report.Reason.PASSED, {'matched': True})
        for forged in (replace(original, state=models.State.FAIL),
                       replace(original, observed_items=(('credential', SENTINEL),))):
            self.assertIsNone(forged._seal)
            self.assert_code('invalid-report', self.build, [forged])

class ReadOnlyCapabilityTests(SyntheticCase):
    def test_core_observation_with_write_process_and_network_apis_denied(self):
        release_verifier = self.make_trust()
        before = snapshot_tree(self.work)
        original_open = os.open
        def safe_os_open(path, flags, *args, **kwargs):
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                raise AssertionError('write denied')
            return original_open(path, flags, *args, **kwargs)
        def denied(*args, **kwargs):
            raise AssertionError('mutation or native activity denied')
        path_mutators = ('write_bytes', 'write_text', 'mkdir', 'unlink', 'rename', 'replace', 'touch', 'chmod', 'rmdir')
        os_mutators = ('mkdir', 'makedirs', 'remove', 'unlink', 'rename', 'replace', 'chmod', 'rmdir', 'system')
        with ExitStack() as stack:
            stack.enter_context(patch.object(os, 'open', safe_os_open))
            for name in path_mutators: stack.enter_context(patch.object(Path, name, denied))
            for name in os_mutators: stack.enter_context(patch.object(os, name, denied))
            stack.enter_context(patch.object(socket, 'socket', denied))
            stack.enter_context(patch.object(subprocess, 'Popen', denied))
            release = release_verifier.verify()
            raw, inventory = self.receipt(release)
            validated = self.validate(raw, release, inventory)
            self.assertEqual(validated.role, 'headend')
            commands = runner.ReadOnlyCommands({'tailscale': self.work / 'approved'},
                       observer=lambda _: runner.CommandOutput(0, b'{}'), qualified=True)
            self.assertEqual(commands.observe_command(runner.Command.TAILSCALE_STATUS), b'{}')
            item = report.row('installation', models.State.OK, report.Reason.PASSED, {'matched': True})
            self.assertEqual(report.build_report('headend', 'linux', 'demo-host', NOW_TEXT, [item])['exit_code'], 0)
        self.assertEqual(before, snapshot_tree(self.work))
        self.assertFalse(any(path.name == '__pycache__' for path in self.work.rglob('*')))

    def test_no_mutator_or_native_imports_in_new_core(self):
        import ast
        forbidden_imports = {'subprocess', 'socket', 'tempfile', 'requests', 'urllib.request', 'http.client',
                             'ctypes', 'winreg', 'shutil'}
        forbidden_calls = {'mkdir', 'makedirs', 'unlink', 'remove', 'rmdir', 'rename',
                           'write_bytes', 'write_text', 'touch', 'chmod', 'chown', 'bind', 'listen',
                           'Popen', 'run', 'system', 'list_peers', 'state_dir', 'ensure_identity',
                           'pairing_committed', 'enrol_forge', 'publish', 'recover_pending'}
        for filename in ('errors.py', 'runner.py', 'trust.py', 'models.py', 'report.py'):
            tree = ast.parse((ROOT / 'src' / 'diagnostics' / 'deep32_diag' / filename).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertFalse({item.name for item in node.names} & forbidden_imports, filename)
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn(node.module, forbidden_imports, filename)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, forbidden_calls, filename)


if __name__ == '__main__':
    unittest.main()
