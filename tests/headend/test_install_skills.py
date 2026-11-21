"""Offline checks for the deep32-install project skills and the agent install guide.

The Claude Code skill (.claude/skills/deep32-install) and the Codex skill (.agents/skills/deep32-install) must stay
twins (install_skill_refs.py --check), carry their front matter, state the person's safety rules, and link only to
files that exist. When the wizard's data files are in install/, every wizard code the skills name must be a row of the
wizard's error table. Nothing is executed except the twin check; no profile or configuration is read.
"""
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = ROOT / '.claude/skills/deep32-install'
CODEX = ROOT / '.agents/skills/deep32-install'
HYPERVISORS = ('proxmox', 'esxi', 'vmware', 'virtualbox', 'hyper-v', 'kvm', 'other')
# One phrase from each of the seven rules every install skill states.
RULES = ('Work only on machines the person names',
         'Before every command that changes a machine',
         'Never type, ask for, read or repeat a password, passphrase, token, API token secret or Tailscale key',
         'Never pass a plan digest',
         'Never change, re-sign or convert in place any image, and never skip one of its checks',
         'Never turn off a firewall, a TLS or certificate check, or a signature check',
         'Stop and report')
CODE = re.compile(r'\b(?:VM|KIT|PVE|TS|NET)_[A-Z0-9_]+\b')


def front_matter(path):
    text = path.read_text(encoding='utf-8')
    if not text.startswith('---\n'):
        return {}
    block = text[4:text.index('\n---\n', 4)]
    fields = {}
    for line in block.splitlines():
        key, _, value = line.partition(':')
        fields[key.strip()] = value.strip()
    return fields


def markdown_files():
    yield ROOT / 'AGENTS.md'
    yield ROOT / 'CLAUDE.md'
    for folder in (CLAUDE, CODEX):
        yield from sorted(folder.rglob('*.md'))


class InstallSkills(unittest.TestCase):
    def test_1_twins_in_step(self):
        r = subprocess.run([sys.executable, '-I', '-B', str(Path(__file__).with_name('install_skill_refs.py')),
                            '--check', '--root', str(ROOT)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_2_front_matter(self):
        for folder in (CLAUDE, CODEX):
            fields = front_matter(folder / 'SKILL.md')
            self.assertEqual(fields.get('name'), 'deep32-install', folder)
            self.assertTrue(fields.get('description'), folder)
            self.assertLessEqual(len(fields['description']), 1024, folder)
            self.assertNotIn('allowed-tools', fields, 'the install skill must not pre-approve tools')
        self.assertIn('/deep32-install', front_matter(CLAUDE / 'SKILL.md')['description'])
        self.assertIn('$deep32-install', front_matter(CODEX / 'SKILL.md')['description'])
        meta = (CODEX / 'agents/openai.yaml').read_text(encoding='utf-8')
        self.assertIn('display_name: "deep32-install"', meta)
        self.assertIn('allow_implicit_invocation:', meta)

    def test_3_rules_stated(self):
        for folder in (CLAUDE, CODEX):
            text = ' '.join((folder / 'SKILL.md').read_text(encoding='utf-8').split())
            missing = [rule for rule in RULES if rule not in text]
            self.assertEqual(missing, [], folder)

    def test_4_links_resolve(self):
        missing = []
        for path in markdown_files():
            for target in re.findall(r'\]\(([^)#\s]+)(?:#[^)]*)?\)', path.read_text(encoding='utf-8')):
                if re.match(r'[a-z]+:', target):
                    continue
                if not (path.parent / target).resolve().exists():
                    missing.append(f'{path.relative_to(ROOT).as_posix()} -> {target}')
        for folder in (CLAUDE, CODEX):
            for hv in HYPERVISORS:
                if not (folder / 'hypervisors' / f'{hv}.md').is_file():
                    missing.append(f'{folder.relative_to(ROOT).as_posix()}/hypervisors/{hv}.md')
        self.assertEqual(missing, [])

    def test_5_codes_are_in_the_error_table(self):
        table = ROOT / 'install/deep32-wizard-errors.tsv'
        if not table.is_file():
            self.skipTest('install/deep32-wizard-errors.tsv is not in this tree yet')
        known = {line.split('\t')[1] for line in table.read_text(encoding='utf-8').splitlines()
                 if line.startswith('code\t')}
        unknown = sorted({code for path in markdown_files() for code in CODE.findall(path.read_text(encoding='utf-8'))}
                         - known)
        self.assertEqual(unknown, [])


if __name__ == '__main__':
    unittest.main(verbosity=2 if '-v' in sys.argv else 1, argv=[sys.argv[0]])
