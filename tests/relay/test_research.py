"""The Ask relay's research instruction is the deep32 skill that Connect installs, inlined (adapters/relay/RESEARCH.md
is written by adapters/relay/make_research.py)."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('make_research', ROOT / 'adapters' / 'relay' / 'make_research.py')
make_research = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_research)


class ResearchInstruction(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / 'adapters' / 'relay' / 'RESEARCH.md').read_bytes().decode('utf-8')

    def test_is_the_generated_skill(self):
        self.assertEqual(self.text, make_research.build(), 'run adapters/relay/make_research.py')

    def test_boundary_comes_first(self):
        first = self.text.index('# Skill: SKILL.md')
        for rule in ('Use only the eleven Deep32 tools', 'start another agent', 'the boundary wins',
                     'Do not retrieve or quote restricted vendor material'):
            self.assertLess(self.text.index(rule), first, rule)

    def test_every_named_reference_file_is_inlined(self):
        skill = (ROOT / 'adapters/claude-code/skills/deep32/SKILL.md').read_text(encoding='utf-8')
        import re
        for name in set(re.findall(r'\$\{CLAUDE_SKILL_DIR\}/([a-z0-9-]+\.md)', skill)):
            self.assertIn('# Reference file: ' + name + '\n', self.text)

    def test_skill_front_matter_is_not_inlined(self):
        self.assertNotIn('\nwhen_to_use:', self.text)
        self.assertNotIn('no-allowed-tools', self.text)

    def test_fits_the_relay_package(self):
        self.assertLess(len(self.text.encode('utf-8')), 1024 * 1024)


if __name__ == '__main__':
    unittest.main()
