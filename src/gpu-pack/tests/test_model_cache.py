"""Offline cache lookup simulation and producer regression tests."""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import unittest
from unittest.mock import patch

BUILD = Path(__file__).resolve().parents[1] / 'build'
sys.path.insert(0, str(BUILD))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import alpha2_disc3 as producer
import model_cache
import test_runtime_stage as fixtures
from provenance import canonical, digest, members, verify_manifest

EPOCH = 1234567890


def simulate_try_to_load_from_cache(repo_id, filename, cache_dir, revision=None):
    """Stdlib simulation of local Hugging Face cache lookup.

    This is not an invocation of the third-party hub package.
    No revision selects main; an existing ref resolves to a snapshot.
    """
    cache = Path(cache_dir) / ('models--' + repo_id.replace('/', '--'))
    revision = revision or 'main'
    reference = cache / 'refs' / revision
    if reference.is_file():
        revision = reference.read_text(encoding='ascii')
    candidate = cache / 'snapshots' / revision / filename
    return str(candidate) if candidate.is_file() else None


class ModelCacheTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RuntimeStageTests('test_deterministic_bytes_and_epoch')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.stage, self.kit = self.fixture.root, self.fixture.stage, self.fixture.kit
        # Exercise the actual release identities without duplicating their pins.
        release = json.loads((BUILD.parent / 'pins.json').read_text(encoding='ascii'))
        for role in ('tei', 'reranker'):
            old = self.fixture.pins['models'][role]
            pinned = release['models'][role]
            old_cache = self.fixture.payload / 'models' / role / ('models--' + old['id'].replace('/', '--'))
            new_cache = old_cache.with_name('models--' + pinned['id'].replace('/', '--'))
            old_cache.rename(new_cache)
            (new_cache / 'snapshots' / old['revision']).rename(new_cache / 'snapshots' / pinned['revision'])
            old.update(id=pinned['id'], revision=pinned['revision'])
        self.pins = self.fixture.pins
        self.fixture.seal()

    def runtime_kit(self):
        for name in ('LICENSE', 'NOTICE', 'pins.json', 'README.txt', 'build/provenance.py', 'build/thin_forms.py',
                     'forms/load-docker.sh', 'pairing/bin/deep32-pair', 'pairing/bin/pairing-helper',
                     *('pairing/deep32net/' + value + '.py' for value in producer.pack._thin.PAIRING_MODULES)):
            target = self.kit / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('{}\n' if name == 'pins.json' else '# synthetic fixture\n',
                              encoding='ascii', newline='\n')

    def derived(self):
        allow = self.root / 'allow.json'
        allow.write_text(canonical(producer.selected_allowlist(self.stage, 'core')), encoding='ascii')
        output = self.root / 'core'
        producer.runtime.stage_runtime(self.stage, self.kit, allow, 'core', output, EPOCH)
        return output

    def legacy_media(self):
        self.runtime_kit()
        docker, alternate = self.root / 'docker.tar', self.root / 'alternate.tar'
        docker.write_bytes(b'synthetic thin Docker form')
        alternate.write_bytes(b'synthetic alternate form')
        output = self.root / 'legacy-media'
        producer.pack.prepare(self.stage, self.kit, docker, alternate, output, EPOCH)
        return output

    def check_pointers(self, payload):
        expected = model_cache.reference_files(self.pins)
        self.assertEqual(len(expected), 4)
        for name, content in expected.items():
            path = payload / name
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(len(content), 40)
            self.assertNotIn(b'\n', content)
            self.assertEqual(path.stat().st_mtime, EPOCH)
            if os.name == 'posix':
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
            else:
                self.assertTrue(path.stat().st_mode & stat.S_IWRITE)
        for role in ('tei', 'reranker'):
            model = self.pins['models'][role]
            cache_dir = payload / 'models' / role
            cache = cache_dir / ('models--' + model['id'].replace('/', '--'))
            for revision in (None, model['revision']):
                for filename in ('config.json', 'tokenizer.json'):
                    resolved = simulate_try_to_load_from_cache(model['id'], filename, cache_dir, revision)
                    target = cache / 'snapshots' / model['revision'] / filename
                    self.assertEqual(resolved, str(target))
                    self.assertEqual(Path(resolved).read_bytes(), target.read_bytes())
                    # Rust hf-hub also consults refs for an explicit commit.
                    ref = cache / 'refs' / (revision or 'main')
                    self.assertEqual(ref.read_bytes(), model['revision'].encode('ascii'))

    def check_manifests(self, media):
        payload = media / 'deep32/payload'
        verify_manifest(payload, media / 'deep32/PAYLOAD.json')
        inventory = {row['path']: row for row in json.loads((media / 'deep32/PAYLOAD.json').read_bytes())}
        payload_sums = dict(line.split('  ', 1)[::-1]
                            for line in (media / 'deep32/PAYLOAD.SHA256').read_text().splitlines())
        media_sums = dict(line.split('  ', 1)[::-1]
                          for line in (media / 'SHA256SUMS').read_text().splitlines())
        for name, content in model_cache.reference_files(self.pins).items():
            checksum = hashlib.sha256(content).hexdigest()
            self.assertEqual(inventory[name], dict(path=name, size=40, sha256=checksum))
            self.assertEqual(payload_sums[name], checksum)
            self.assertEqual(media_sums['deep32/payload/' + name], checksum)
        for name, checksum in media_sums.items():
            self.assertEqual(digest(media / name), checksum)

    def test_missing_main_reproduces_failure_then_core_stage_and_media_resolve_offline(self):
        before = members(self.stage)
        sealed_hash = digest(self.stage / 'PAYLOAD.json')
        for role in ('tei', 'reranker'):
            model = self.pins['models'][role]
            self.assertIsNone(simulate_try_to_load_from_cache(model['id'], 'tokenizer.json',
                              self.fixture.payload / 'models' / role))
        stage = self.derived()
        self.check_pointers(stage / 'payload')
        self.assertEqual(members(stage / 'payload'), json.loads((stage / 'PAYLOAD.json').read_bytes()))
        receipt = json.loads((stage / 'RUNTIME.json').read_bytes())
        self.assertEqual(receipt['payload_sha256'], digest(stage / 'PAYLOAD.json'))
        self.assertEqual(receipt['source_payload_sha256'], sealed_hash)
        self.runtime_kit()
        media = self.root / 'core-media'
        producer.prepare_media(stage, self.kit, 'core', media, EPOCH)
        self.check_pointers(media / 'deep32/payload')
        self.check_manifests(media)
        mapping = self.root / 'maps.txt'
        producer.pack.maps(media, mapping)
        text = mapping.read_text(encoding='ascii')
        for name in model_cache.reference_files(self.pins):
            self.assertIn('/deep32/payload/' + name, text)
        self.assertEqual(members(self.stage), before)
        self.assertEqual(digest(self.stage / 'PAYLOAD.json'), sealed_hash)

    def test_legacy_media_gains_same_pointers_and_regenerated_inventories(self):
        before = members(self.stage)
        media = self.legacy_media()
        self.check_pointers(media / 'deep32/payload')
        self.check_manifests(media)
        self.assertEqual(members(self.stage), before)
        stage = self.derived()
        for name in model_cache.reference_files(self.pins):
            self.assertEqual((stage / 'payload' / name).read_bytes(),
                             (media / 'deep32/payload' / name).read_bytes())

    def test_stale_acquisition_refs_are_replaced_only_in_output(self):
        for name in model_cache.reference_files(self.pins):
            self.fixture.write(name, b'stale source pointer\n')
        self.fixture.seal()
        before = members(self.stage)
        derived = self.derived()
        self.check_pointers(derived / 'payload')
        media = self.legacy_media()
        self.check_pointers(media / 'deep32/payload')
        self.check_manifests(media)
        self.assertEqual(members(self.stage), before)

    def test_pins_are_the_only_revision_source(self):
        changed = copy.deepcopy(self.pins)
        for role, digit in (('tei', 'b'), ('reranker', 'c')):
            changed['models'][role]['revision'] = digit * 40
        refs = model_cache.reference_files(changed)
        for role in ('tei', 'reranker'):
            model = changed['models'][role]
            prefix = 'models/' + role + '/models--' + model['id'].replace('/', '--') + '/refs/'
            self.assertEqual(refs[prefix + 'main'], model['revision'].encode('ascii'))
            self.assertEqual(refs[prefix + model['revision']], model['revision'].encode('ascii'))

    def test_writer_requests_exact_posix_modes(self):
        original = Path.chmod
        modes = []
        def chmod(path, mode, **kwargs):
            modes.append((path.relative_to(self.fixture.payload).as_posix(), mode))
            return original(path, mode, **kwargs)
        with patch.object(Path, 'chmod', chmod):
            model_cache.write_references(self.fixture.payload, self.pins)
        self.assertEqual(modes, [(name, 0o644) for name in model_cache.reference_files(self.pins)])

    def test_bad_pin_or_absent_snapshot_refuses_before_any_pointer(self):
        for value in ('main', 'a' * 39, 'A' * 40, '../outside', None):
            with self.subTest(revision=value):
                bad = copy.deepcopy(self.pins)
                bad['models']['reranker']['revision'] = value
                with self.assertRaises(ValueError):
                    model_cache.write_references(self.fixture.payload, bad)
        for value in ('../outside', 'owner/..', '/absolute', 'owner/name/extra'):
            bad = copy.deepcopy(self.pins)
            bad['models']['reranker']['id'] = value
            with self.assertRaises(ValueError):
                model_cache.write_references(self.fixture.payload, bad)
        bad = copy.deepcopy(self.pins)
        bad['models']['reranker']['revision'] = 'd' * 40
        with self.assertRaises(ValueError):
            model_cache.write_references(self.fixture.payload, bad)
        self.assertFalse(any('/refs/' in row['path'] for row in members(self.fixture.payload)))


if __name__ == '__main__':
    unittest.main()
