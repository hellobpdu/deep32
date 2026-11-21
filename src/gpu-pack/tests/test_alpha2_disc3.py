"""Offline synthetic producer tests; no Docker or image tool runs."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tarfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'build'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import alpha2_disc3 as producer
import test_runtime_stage as fixtures
from provenance import canonical, digest, members

# The owner's Apache-2.0 terms for Deep32's own code: the standard license text and the project NOTICE.
TERMS = {'LICENSE': 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30',
         'NOTICE': 'eca7bc5d138e5ee111092c9f3b2302754914a04bfbfa973cc0717c7ad1724cc9'}
# MinerU 4.0.7's LICENSE.md (Apache-2.0 with additional terms), the same bytes as its wheel's dist-info copy.
THIRD_PARTY = {'licenses/MinerU-4.0.7-LICENSE.md': '2d9aeb5d15159329a20dd804f251c64860ba954fbd26ec02f802220e6d71cf5c'}


def fake_iso(path):
    block = bytearray(2048)
    block[1:7] = b'CD001\x01'
    primary = bytearray(block)
    primary[0] = 1
    primary[40:72] = producer.pack.VOLUME.encode('ascii').ljust(32)
    joliet = bytearray(block)
    joliet[0] = 2
    joliet[88:91] = b'%/E'
    terminal = bytearray(block)
    terminal[0] = 255
    Path(path).write_bytes(b'\0' * 32768 + primary + joliet + terminal)


class ProducerTests(unittest.TestCase):
    def test_pack_cli_verifies_data_only_image_in_isolated_mode(self):
        image = self.root / 'isolated.iso'
        fake_iso(image)
        result = subprocess.run([sys.executable, '-I', '-B', str(producer.HERE / 'pack.py'),
                                 'verify', str(image)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['boot_records'], 0)

    def setUp(self):
        self.fixture = fixtures.RuntimeStageTests('test_deterministic_bytes_and_epoch')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.stage, self.kit = self.fixture.root, self.fixture.stage, self.fixture.kit

    def derived(self, component, name=None):
        allow = self.root / ('producer-allow-' + (name or component) + '.json')
        allow.write_text(canonical(producer.selected_allowlist(self.stage, component)), encoding='ascii')
        kind = 'embedder' if component.startswith('embedder-') else component
        card = component[9:] if kind == 'embedder' else None
        out = self.root / ('derived-' + (name or component))
        producer.runtime.stage_runtime(self.stage, self.kit, allow, kind, out, 1234567890, card)
        return out

    def runtime_kit(self):
        names = ('LICENSE', 'NOTICE', 'pins.json', 'build/provenance.py', 'build/thin_forms.py',
                 'forms/load-docker.sh', 'pairing/bin/deep32-pair', 'pairing/bin/pairing-helper',
                 *('pairing/deep32net/' + value + '.py' for value in producer.pack._thin.PAIRING_MODULES))
        for name in names:
            target = self.kit / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('{}\n' if name == 'pins.json' else '# invented fixture\n', encoding='ascii')

    def test_exact_nine_components_and_each_class_selection(self):
        self.assertEqual(len(producer.COMPONENTS), 9)
        self.assertEqual(set(producer.CLASSES), producer.runtime.CLASSES)
        before = members(self.stage)
        for component in producer.COMPONENTS:
            derived = self.derived(component)
            images = producer.runtime.read_json(derived / 'payload/images/index.json')
            expected = ({'door', 'reranker'} if component == 'core' else
                        {'converter', 'builder', 'qdrant'} if component == 'ingestion' else
                        {'tei-' + component[9:]})
            self.assertEqual({row['role'] for row in images}, expected)
            self.assertFalse(any(row['path'].startswith(('wheelhouse/', 'repos/', 'base/'))
                                 for row in members(derived / 'payload')))
        self.assertEqual(before, members(self.stage))

    def test_core_media_contains_single_docker_form_and_no_tei_image(self):
        self.runtime_kit()
        stage = self.derived('core')
        media = self.root / 'core-media'
        producer.prepare_media(stage, self.kit, 'core', media, 1234567890)
        self.assertTrue((media / 'forms/forge-docker.tar').is_file())
        self.assertFalse((media / 'forms/forge-wsl.tar').exists())
        self.assertEqual({row['role'] for row in producer.runtime.read_json(media / 'deep32/payload/images/index.json')},
                         {'door', 'reranker'})
        self.assertEqual(members(stage / 'payload'), members(media / 'deep32/payload'))
        for line in (media / 'SHA256SUMS').read_text(encoding='ascii').splitlines():
            checksum, name = line.split('  ', 1)
            self.assertEqual(digest(media / name), checksum)

    def test_runtime_kit_carries_the_apache_license_and_notice(self):
        kit = producer.HERE.parent
        files = producer.pack._thin.runtime_files(kit)
        members = producer.pack._thin.runtime_members(kit)
        for name, expected in {**TERMS, **THIRD_PARTY}.items():
            with self.subTest(name=name):
                self.assertIn(kit / name, files)
                self.assertEqual(members[name], kit / name)
                self.assertEqual(digest(kit / name), expected)

    def test_core_media_and_docker_form_carry_license_and_notice(self):
        self.runtime_kit()
        for name in (*TERMS, *THIRD_PARTY):
            (self.kit / name).parent.mkdir(parents=True, exist_ok=True)
            (self.kit / name).write_bytes(b'synthetic ' + name.encode('ascii') + b' text\n')
        stage = self.derived('core')
        media = self.root / 'terms-core-media'
        producer.prepare_media(stage, self.kit, 'core', media, 1234567890)
        sums = (media / 'SHA256SUMS').read_text(encoding='ascii').splitlines()
        with tarfile.open(media / 'forms/forge-docker.tar') as archive:
            for name in (*TERMS, *THIRD_PARTY):
                with self.subTest(name=name):
                    expected = (self.kit / name).read_bytes()
                    self.assertEqual((media / name).read_bytes(), expected)
                    self.assertIn(digest(media / name) + '  ' + name, sums)
                    self.assertEqual(archive.extractfile('deep32/kit/' + name).read(), expected)

    def test_kit_without_license_or_notice_refuses(self):
        self.runtime_kit()
        for name in TERMS:
            with self.subTest(name=name):
                saved = (self.kit / name).read_bytes()
                (self.kit / name).unlink()
                with self.assertRaisesRegex(ValueError, name):
                    producer.pack._thin.runtime_files(self.kit)
                (self.kit / name).write_bytes(saved)

    def test_addon_media_has_no_kit_models_or_alternate_class(self):
        for card in producer.CLASSES:
            component = 'embedder-' + card
            stage = self.derived(component)
            media = self.root / ('media-' + card)
            producer.prepare_media(stage, self.kit, component, media, 1234567890)
            self.assertFalse((media / 'forms').exists())
            self.assertFalse((media / 'deep32/payload/models').exists())
            self.assertEqual({row['path'] for row in members(media / 'deep32/payload')},
                             {'images/index.json', 'images/tei-' + card + '.tar'})

    def test_two_fresh_core_stages_produce_identical_media_and_descriptor_images(self):
        self.runtime_kit()
        outputs = []
        calls = []
        def fake_run(command, **kwargs):
            calls.append(command)
            fake_iso(command[command.index('-outdev') + 1])
            return subprocess.CompletedProcess(command, 0)
        for name in ('one', 'two'):
            stage = self.derived('core', name)
            output = self.root / ('image-' + name)
            outputs.append(producer.build_image(stage, self.kit, 'core', output, 1234567890, run=fake_run))
        self.assertEqual(outputs[0].read_bytes(), outputs[1].read_bytes())
        self.assertEqual(members(outputs[0].parent / 'media'), members(outputs[1].parent / 'media'))
        self.assertEqual(len(calls), 2)
        self.assertEqual(producer.pack.verify_iso(outputs[0])['boot_records'], 0)

    def test_refuses_stale_receipt_or_replaced_member(self):
        stage = self.derived('embedder-turing')
        receipt = producer.runtime.read_json(stage / 'RUNTIME.json')
        receipt['gpu_class'] = 'ada'
        producer.write_json(stage / 'RUNTIME.json', receipt)
        with self.assertRaises(ValueError):
            producer.prepare_media(stage, self.kit, 'embedder-turing', self.root / 'bad', 0)
        self.assertFalse((self.root / 'bad').exists())

    def test_dry_run_is_read_only_and_prints_complete_class_and_offline_commands(self):
        out = self.root / 'nonexistent'
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            code = producer.main(['--checkout', str(self.root / 'unread-checkout'),
                                  '--sealed-stage', str(self.root / 'unread-stage'),
                                  '--out', str(out), '--epoch', '1234567890',
                                  '--release-gates', str(self.root / 'unread-gates.json'),
                                  '--sbom-inventories', str(self.root / 'unread-inventories'),
                                  '--dry-run'])
        self.assertEqual(code, 0)
        self.assertFalse(out.exists())
        text = capture.getvalue()
        self.assertEqual(text.count('docker build --network none --pull=false'), 5)
        self.assertEqual(len(__import__('re').findall(r"pack\.py'? verify", text)), 10)
        for component in producer.COMPONENTS:
            self.assertIn(producer.image_name(component), text)
        self.assertNotIn('wsl.exe', text)

    def test_output_overlap_and_existing_paths_refused(self):
        for path in (self.stage, self.stage / 'nested', self.root, self.kit / 'nested'):
            with self.assertRaises(ValueError):
                producer.no_overlap(path, (self.stage, self.kit))

    def test_changed_seal_is_refused_before_allowlist_is_created(self):
        self.fixture.write('evidence/receipt.json', b'changed')
        with self.assertRaises(ValueError):
            producer.selected_allowlist(self.stage, 'core')


if __name__ == '__main__':
    unittest.main()
