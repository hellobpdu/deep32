"""The relay's own Claude profile may hold Claude Code's built-in plugin records, and the empty lock folder Claude
Code leaves beside one when the relay ends it mid-write; anything else under plugins/ is a customisation."""
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401  (puts adapters/relay on the import path)
from deep32_relay import native

RECORD = 'cc-plugin-agents-md_builtin-b049b47b3618.json'


class BuiltinPluginRecords(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plugins = Path(self.tmp.name) / 'plugins'
        (self.plugins / 'store').mkdir(parents=True)
        (self.plugins / 'store' / RECORD).write_text('{}', encoding='ascii')

    def check(self):
        return native._builtin_plugin_records_only(self.plugins)

    def test_builtin_record_alone_is_not_a_customisation(self):
        self.assertTrue(self.check())

    def test_empty_lock_beside_a_builtin_record_is_not_a_customisation(self):
        (self.plugins / 'store' / (RECORD + '.lock')).mkdir()
        self.assertTrue(self.check())

    def test_lock_with_content_is_a_customisation(self):
        lock = self.plugins / 'store' / (RECORD + '.lock')
        lock.mkdir()
        (lock / 'x').write_text('x', encoding='ascii')
        self.assertFalse(self.check())

    def test_lock_file_is_a_customisation(self):
        (self.plugins / 'store' / (RECORD + '.lock')).write_text('', encoding='ascii')
        self.assertFalse(self.check())

    def test_lock_for_another_name_is_a_customisation(self):
        (self.plugins / 'store' / 'someone-elses-plugin.json.lock').mkdir()
        self.assertFalse(self.check())

    def test_installed_plugin_is_a_customisation(self):
        (self.plugins / 'marketplaces').mkdir()
        self.assertFalse(self.check())


if __name__ == '__main__':
    unittest.main()
