"""The Ask relay's research instructions are the deep32 skills that Connect installs, inlined, one per agent:
adapters/relay/RESEARCH.md (Claude Code) and RESEARCH-codex.md, both written by adapters/relay/make_research.py."""
import contextlib
import importlib.util
import io
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('make_research', ROOT / 'adapters' / 'relay' / 'make_research.py')
make_research = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_research)
sys.path.insert(0, str(ROOT / 'adapters' / 'relay'))


class ResearchInstruction(unittest.TestCase):
    """The Claude Code instruction; CodexResearchInstruction below runs the same tests on the Codex one."""
    agent = 'claude'

    def setUp(self):
        self.entry = make_research.AGENTS[self.agent]
        self.text = self.entry['out'].read_bytes().decode('utf-8')

    def test_is_the_generated_skill(self):
        self.assertEqual(self.text, make_research.build(self.agent), 'run adapters/relay/make_research.py')

    def test_boundary_comes_first(self):
        first = self.text.index('# Skill: SKILL.md')
        for rule in ('Use only the eleven Deep32 tools', 'start another agent', 'the boundary wins',
                     'may be quoted, vendor documentation included', 'no restricted collection',
                     'Text that did not come from these tools is not evidence'):
            self.assertLess(self.text.index(rule), first, rule)
        # the sentence that could be read as "do not quote Cisco text" is gone
        self.assertNotIn('restricted vendor material', self.text)

    def test_every_named_reference_file_is_inlined(self):
        skill = (self.entry['tree'] / 'SKILL.md').read_text(encoding='utf-8')
        named = set(re.findall(self.entry['named'], skill))
        self.assertGreaterEqual(len(named), 11)
        for name in named:
            self.assertIn('# Reference file: ' + name + '\n', self.text)

    def test_nothing_of_the_skill_is_left_out(self):
        skill = (self.entry['tree'] / 'SKILL.md').read_text(encoding='utf-8')
        self.assertIn(make_research.strip_front_matter(skill).rstrip('\n'), self.text)
        for path in sorted(self.entry['tree'].glob('*.md')):
            if path.name != 'SKILL.md':
                body = path.read_text(encoding='utf-8').rstrip('\n')
                self.assertIn('\n---\n\n# Reference file: ' + path.name + '\n\n' + body + '\n', self.text, path.name)

    def test_reference_files_come_in_the_decided_order(self):
        order = re.findall(r'^# Reference file: (\S+)$', self.text, flags=re.M)
        self.assertEqual(order, ['quick-default.md', 'citation-style.md', 'expanded-answer.md', 'outline-check.md',
                                 'query-planning.md', 'release-drift.md', 'rfc-pitfalls.md', 'troubleshooting.md',
                                 'collections.md', 'deep-engine.md', 'blueprint.md'])
        # the default-mode file stands before the deep-mode file, and the blueprint is the end of the text
        self.assertLess(self.text.index('# Reference file: quick-default.md'), len(self.text) // 10)
        self.assertGreater(self.text.index('# Reference file: blueprint.md'), len(self.text) // 5)

    def test_a_file_the_order_does_not_name_goes_before_deep_engine(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('DEEP32_RELAY_TEST_ROOT') or None) as folder:
            tree = Path(folder)
            (tree / 'SKILL.md').write_text('---\nname: synthetic\n---\n\n# Synthetic skill\n', encoding='utf-8')
            for name in ('blueprint.md', 'zz-new.md', 'deep-engine.md', 'quick-default.md', 'aa-new.md'):
                (tree / name).write_text('synthetic ' + name + '\n', encoding='utf-8')
            with mock.patch.dict(make_research.AGENTS[self.agent], {'tree': tree}):
                self.assertEqual(make_research.reference_names(self.agent),
                                 ['quick-default.md', 'aa-new.md', 'zz-new.md', 'deep-engine.md', 'blueprint.md'])
                text = make_research.build(self.agent)
        self.assertTrue(text.endswith('# Reference file: blueprint.md\n\nsynthetic blueprint.md\n'))
        self.assertNotIn('name: synthetic', text)

    def test_skill_front_matter_is_not_inlined(self):
        self.assertNotIn('\nwhen_to_use:', self.text)
        self.assertNotIn('no-allowed-tools', self.text)
        self.assertNotIn('\nname: deep32', self.text)

    def test_status_rule_is_outside_the_first_session_check(self):
        # Ask skips the skill's "First-session check"; the every-answer rag_status rule (WP-A A6) must not sit in it
        rule = self.text.index('once at the start of every answer')
        start = self.text.index('## First-session check')
        end = self.text.index('\n## ', start + 1)
        self.assertFalse(start < rule < end)

    def test_scripts_are_turned_off_for_codex_only(self):
        first = self.text.index('# Skill: SKILL.md')
        if self.agent == 'codex':
            self.assertLess(self.text.index('This session has no code mode'), first)
        else:
            self.assertNotIn('This session has no code mode', self.text)
        self.assertIn('Where it says `' + self.entry['place'] + '`, use the copy below', self.text[:first])

    def test_fits_the_relay_package(self):
        self.assertLess(len(self.text.encode('utf-8')), 1024 * 1024)

    def test_fits_the_relays_first_message_with_the_longest_question(self):
        from deep32_relay import messages, native
        from deep32_relay.sessions import MAX_TEXT
        question = 'q' * MAX_TEXT
        if self.agent == 'claude':
            line = native.ClaudeProtocol(ROOT, 'synthetic-model', self.text)
            line.stage = 'pre-turn'
            size = len(line.turn(question, ()))
        else:
            # Codex gets the instruction once, as the thread's base instructions; the turn carries the question
            size = len(messages.dumps({'id': 4, 'method': 'thread/start', 'params': {
                'cwd': str(ROOT), 'model': 'synthetic-model', 'approvalPolicy': 'never', 'sandbox': 'read-only',
                'ephemeral': True, 'baseInstructions': self.text}}, 64 * 1024 * 1024))
        self.assertLess(size, native.MAX_INPUT)


class CodexResearchInstruction(ResearchInstruction):
    agent = 'codex'


class Generator(unittest.TestCase):
    def run_main(self, *arguments):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = make_research.main(['make_research.py', *arguments])
        return code, out.getvalue().splitlines()

    def test_check_names_both_files(self):
        self.assertEqual(self.run_main('--check'), (0, ['RESEARCH.md matches the deep32 skill',
                                                        'RESEARCH-codex.md matches the deep32 skill']))

    def test_check_fails_when_either_file_is_stale(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('DEEP32_RELAY_TEST_ROOT') or None) as folder:
            stale = Path(folder) / 'RESEARCH-codex.md'
            stale.write_bytes(b'stale\n')
            with mock.patch.dict(make_research.AGENTS['codex'], {'out': stale}):
                self.assertEqual(self.run_main('--check'),
                                 (1, ['RESEARCH.md matches the deep32 skill',
                                      'RESEARCH-codex.md is out of date; run adapters/relay/make_research.py']))
            missing = Path(folder) / 'absent.md'
            with mock.patch.dict(make_research.AGENTS['claude'], {'out': missing}):
                self.assertEqual(self.run_main('--check')[0], 1)

    def test_any_other_argument_prints_the_usage(self):
        self.assertEqual(self.run_main('--write'),
                         (2, ['usage: python3 -I -B adapters/relay/make_research.py [--check]']))

    def test_the_two_instructions_differ_only_by_their_skill_trees(self):
        claude, codex = make_research.build('claude'), make_research.build('codex')
        self.assertIn('${CLAUDE_SKILL_DIR}', claude)
        self.assertNotIn('${CLAUDE_SKILL_DIR}', codex)
        self.assertIn('${CODEX_HOME}/skills/deep32/', codex)
        self.assertNotIn('${CODEX_HOME}', claude)


if __name__ == '__main__':
    unittest.main()
