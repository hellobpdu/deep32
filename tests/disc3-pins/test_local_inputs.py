"""Reviewed local MinerU 4 config and AGPL text: no network or host commands."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # fixtures.py and fakepip.py

import copy
from pathlib import Path
import tempfile
import types
import unittest

from fixtures import KIT, resolver


CONFIG = (b'model:\n  source: local\n  base_dir: /models/models\n'
          b'  small_backend: torch\n  vlm:\n    engine: vllm\n')
AGPL = (b'GNU AFFERO GENERAL PUBLIC LICENSE\n'
        b'Version 3, 19 November 2007\n\nSynthetic licence test material.\n')
CONFIG_STAGE = 'payload/models/mineru-home/config.yaml'
AGPL_STAGE = 'payload/licences/AGPL-3.0.txt'


class NoNetwork:
    def __getattr__(self, name):
        raise AssertionError('local inputs must never use the network: ' + name)


class LocalInputs(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='d32-local-inputs-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / 'config.yaml'
        self.config.write_bytes(CONFIG)
        self.agpl = self.root / 'AGPL-3.0.txt'
        self.agpl.write_bytes(AGPL)
        self.doc = __import__('json').loads((KIT / 'pins.json').read_text(encoding='ascii'))
        self.ctx = types.SimpleNamespace(
            lead={'mineru_config': {'path': str(self.config), 'review': 'synthetic test review'},
                  'agpl_path': str(self.agpl), 'agpl_sha256': resolver.sha256_bytes(AGPL)},
            store=self.root / 'store', net=NoNetwork(),
            fetch=resolver.load_module('kit_fetch_local_inputs', KIT / 'build' / 'fetch-inputs.py'),
            store_uri=lambda path: Path(path).resolve().as_uri())
        self.state = types.SimpleNamespace(doc=self.doc, records={})

    def publish(self, item):
        result = item.resolve(self.ctx, self.doc)
        self.assertEqual(len(result.file_rows), 1)
        stage, url, digest, source, auth, extra = result.file_rows[0]
        index = resolver.file_row(self.doc, stage)
        self.doc['fetch']['files'][index].update(url=url, sha256=digest)
        self.state.records['fetch.files.' + str(index) + '.sha256'] = {'authentication': auth}
        return self.doc['fetch']['files'][index]

    def check(self):
        report, blocking = [], []
        resolver.check_lead_payloads(self.ctx, self.state, report, blocking)
        return report, blocking

    def test_config_yaml_is_copied_exactly_without_network(self):
        row = self.publish(resolver.LeadMineruConfig())
        self.assertEqual(resolver.read_file_url(row['url']), CONFIG)
        self.assertEqual(row['sha256'], resolver.sha256_bytes(CONFIG))
        self.assertEqual(row['path'], CONFIG_STAGE)
        self.assertEqual(self.check()[1], [])

    def test_config_rejects_wrong_values_or_yaml_structure_before_storing(self):
        bad = [CONFIG.replace(b'local', b'huggingface'),
               CONFIG.replace(b'/models/models', b'/models/other'),
               CONFIG.replace(b'torch', b'onnx'), CONFIG.replace(b'vllm', b'lmdeploy'),
               CONFIG.replace(b'  vlm:\n    engine: vllm\n', b''),
               CONFIG + b'  extra: true\n', CONFIG + b'  source: local\n',
               b'{"model": {"source": "local"}}\n', b'model: [local]\n',
               b'!!python/object:danger {}\n', b'model: &a\n  source: *a\n']
        for data in bad:
            with self.subTest(data=data):
                self.config.write_bytes(data)
                with self.assertRaises(resolver.Refusal):
                    resolver.LeadMineruConfig().resolve(self.ctx, self.doc)
                self.assertFalse((self.ctx.store / 'lead' / 'config.yaml').exists())

    def test_config_requires_reviewed_regular_nonempty_file(self):
        for field, value in [('review', ''), ('path', ''), ('path', str(self.root))]:
            with self.subTest(field=field, value=value):
                original = self.ctx.lead['mineru_config'][field]
                self.ctx.lead['mineru_config'][field] = value
                with self.assertRaises(resolver.Refusal):
                    resolver.LeadMineruConfig().resolve(self.ctx, self.doc)
                self.ctx.lead['mineru_config'][field] = original
        self.config.write_bytes(b'')
        with self.assertRaises(resolver.Refusal):
            resolver.LeadMineruConfig().resolve(self.ctx, self.doc)

    def test_agpl_is_hashed_and_copied_exactly_without_network(self):
        row = self.publish(resolver.AgplText())
        self.assertEqual(row['path'], AGPL_STAGE)
        self.assertEqual(resolver.read_file_url(row['url']), AGPL)
        self.assertEqual(row['sha256'], self.ctx.lead['agpl_sha256'])
        self.agpl.unlink()
        self.assertEqual(self.check()[1], [])

    def test_agpl_requires_path_and_exact_lowercase_sha256(self):
        for field, value in [('agpl_path', ''), ('agpl_path', None), ('agpl_path', str(self.root)),
                             ('agpl_path', str(self.root / 'missing')),
                             ('agpl_sha256', ''), ('agpl_sha256', None), ('agpl_sha256', 'xyz'),
                             ('agpl_sha256', 'A' * 64), ('agpl_sha256', '0' * 64)]:
            with self.subTest(field=field, value=value):
                original = self.ctx.lead[field]
                self.ctx.lead[field] = value
                with self.assertRaises(resolver.Refusal):
                    resolver.AgplText().resolve(self.ctx, self.doc)
                self.ctx.lead[field] = original
                self.assertFalse((self.ctx.store / 'licences' / 'AGPL-3.0.txt').exists())

    def test_agpl_requires_correct_header_even_with_matching_hash(self):
        for data in (b'', b'not the AGPL\n', AGPL.replace(b'Version 3', b'Version 2')):
            with self.subTest(data=data):
                self.agpl.write_bytes(data)
                self.ctx.lead['agpl_sha256'] = resolver.sha256_bytes(data)
                with self.assertRaises(resolver.Refusal):
                    resolver.AgplText().resolve(self.ctx, self.doc)

    def test_check_verifies_both_copies_without_lead_inputs(self):
        self.publish(resolver.LeadMineruConfig())
        self.publish(resolver.AgplText())
        self.ctx.lead = {}
        report, blocking = self.check()
        self.assertEqual(blocking, [])
        self.assertEqual(len(report), 2)

    def test_check_refuses_missing_changed_and_nonlocal_bytes(self):
        for item, stage in [(resolver.LeadMineruConfig(), CONFIG_STAGE), (resolver.AgplText(), AGPL_STAGE)]:
            row = self.publish(item)
            original_url = row['url']
            row['url'] = 'https://example.invalid/input'
            self.assertTrue(self.check()[1])
            row['url'] = original_url
            target = self.ctx.store / ('lead' if stage == CONFIG_STAGE else 'licences') / Path(stage).name
            original = target.read_bytes()
            target.write_bytes(original + b'changed\n')
            self.assertTrue(self.check()[1])
            target.unlink()
            self.assertTrue(self.check()[1])
            target.write_bytes(original)

    def test_check_revalidates_config_content_even_with_updated_pin(self):
        row = self.publish(resolver.LeadMineruConfig())
        data = CONFIG.replace(b'local', b'remote')
        (self.ctx.store / 'lead' / 'config.yaml').write_bytes(data)
        row['sha256'] = resolver.sha256_bytes(data)
        self.assertIn('config.yaml', self.check()[1][0])

    def test_check_requires_agpl_pin_to_match_recorded_and_current_lead_hash(self):
        row = self.publish(resolver.AgplText())
        index = resolver.file_row(self.doc, AGPL_STAGE)
        auth = self.state.records['fetch.files.' + str(index) + '.sha256']['authentication']
        original = copy.deepcopy(auth)
        for expected in ('', '0' * 64, None):
            with self.subTest(expected=expected):
                auth['lead_sha256'] = expected
                self.assertTrue(self.check()[1])
        auth.update(original)
        self.ctx.lead['agpl_sha256'] = '0' * 64
        self.assertTrue(self.check()[1])
        self.assertEqual(row['sha256'], resolver.sha256_bytes(AGPL))


if __name__ == '__main__':
    unittest.main()
