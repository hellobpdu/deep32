"""Offline checks for the shipped Claude Code and Codex research skills.

The upstream relay-only checks apply to a planned component and are not part of this
source release. Twin checks, isolated drift repair, reference resolution, restricted
wording checks and MCP identity checks cover every shipped skill and setup template.
No setup script is executed and no user profile or configuration is read or written.
The drift test uses a disposable tree under the caller's TMP folder.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.dirname(os.path.dirname(HERE))
CLAUDE = os.path.join(WORK, 'adapters', 'claude-code', 'skills', 'deep32')
CODEX_SKILLS = os.path.join(WORK, 'adapters', 'codex', 'skills')
RELAY_SKILLS = None
RELAY_CODE = []
RELAY_HOST = []
CODEX_KIT_FILES = [os.path.join(WORK, 'adapters', 'codex', n) for n in
                   ('AGENTS.deep32.md', 'config.deep32.toml', 'setup.ps1', 'setup.sh')]

# Restricted-wording policy is supplied by the release checker; private policy never ships.
ALLOW = []

# The alpha.3 skill-text package (WP-A): a rule it retired never comes back in a skill or standing block, and a rule
# it added stays. Each item of the package adds its own entries.
STANDING = [os.path.join(WORK, 'adapters', 'claude-code', 'CLAUDE.deep32.md'),
            os.path.join(WORK, 'adapters', 'codex', 'AGENTS.deep32.md')]
RETIRED = [
    (r'(?<![\d.])2\.4\.\d(?![\d.])', 'A1: a gateway version number'),
    (r'version before', 'A1: a gateway version test'),
    (r'sections read: <section ids>', 'A2: a gap label with no outline read'),
    (r'Skip a document whose matching section', 'A2: the skip rule that wrote gaps without an outline'),
    (r'belongs in How it works', 'A3: a background rule narrower than expanded-answer.md'),
    (r'scored (?:invalid|fuzzy)', 'A5: a quote scorer that does not ship'),
    (r'at most 2 omissions', 'A5: quotes stitched with omissions'),
    (r'not an error', 'A6: a backend that is off presented as full strength'),
    (r'first answer of the session', 'A6: rag_status once per session'),
    (r'has not called it yet', 'A6: rag_status once per thread'),
    (r'Budget: about 8 to 15', 'A7: a second call budget'),
    (r'two independent sources agree', 'A7: a second stop rule'),
    (r'RAG: <n>', 'A8: a call count written by the model'),
    (r'`RAG:` line', 'A8: a RAG line as the last line'),
    (r'web unless the user asks', 'A8: a web allowance against the skill'),
    (r"answer's last line|last line may offer|You may end with one line", 'A8: a last line other than Read'),
    (r'labelled as constructed', 'A9: constructed output not tied to a verified sample'),
    (r'all in one batch', 'A11: a quote batch with no size limit'),
    # retired rules: each WP-A item adds its entries above this line
]
REQUIRED = [
    ('adapters/claude-code/skills/deep32/deep-engine.md', 'comes back with hits and no tier header', 'A1'),
    ('adapters/codex/skills/deep32/deep-engine.md', 'at most 8 plain hits and no tier header line', 'A1'),
    ('adapters/claude-code/skills/deep32/outline-check.md', '`${CLAUDE_SKILL_DIR}/blueprint.md` names for the topic', 'A2'),
    ('adapters/claude-code/skills/deep32/quick-default.md', 'outside the cap, with its own budget', 'A2'),
    ('adapters/claude-code/skills/deep32/expanded-answer.md', '## When the main point is not covered', 'A3'),
    ('adapters/codex/skills/deep32/expanded-answer.md', '## When the main point is not covered', 'A3'),
    ('adapters/claude-code/skills/deep32/SKILL.md', 'pins of section 0 as bare release numbers', 'A4'),
    ('adapters/codex/skills/deep32/SKILL.md', 'pins of section 0 as bare release numbers', 'A4'),
    ('adapters/claude-code/skills/deep32/SKILL.md', '`pin="other"` on a hit', 'A4'),
    ('adapters/codex/skills/deep32/SKILL.md', '`pin="other"` on a hit', 'A4'),
    ('adapters/claude-code/skills/deep32/quick-default.md', '`search_many` with `prefer_release`', 'A4'),
    ('adapters/codex/skills/deep32/deep-engine.md', 'j.args.prefer_release ??= PREFER;', 'A4'),
    ('adapters/claude-code/skills/deep32/quick-default.md', 'stop as soon as each part of the question has a section read that settles it', 'A7'),
    ('adapters/codex/skills/deep32-configure/SKILL.md', 'labelled `inference (from [cite] + [cite])`', 'A9'),
    ('adapters/codex/skills/deep32-describe/SKILL.md', 'labelled `inference (from [cite] + [cite])`', 'A9'),
    ('adapters/codex/skills/deep32-design/SKILL.md', 'labelled `inference (from [cite] + [cite])`', 'A9'),
    ('adapters/codex/skills/deep32-troubleshoot/SKILL.md', 'labelled `inference (from [cite] + [cite])`', 'A9'),
    ('adapters/codex/skills/deep32-verify/SKILL.md', 'labelled `inference (from [cite] + [cite])`', 'A9'),
    ('adapters/claude-code/skills/deep32/collections.md', 'In Codex, the five lens skills', 'A9'),
    ('adapters/claude-code/skills/deep32/quick-default.md', 'In every mode send no `max_chars`', 'A11'),
    ('adapters/claude-code/skills/deep32/SKILL.md', 'go on until a reply names no successor', 'A12'),
    ('adapters/codex/skills/deep32/SKILL.md', 'go on until a reply names no successor', 'A12'),
    ('adapters/claude-code/skills/deep32/SKILL.md', 'one call checks up to 16 quotes', 'A14'),
    ('adapters/codex/skills/deep32/SKILL.md', 'one call checks up to 16 quotes', 'A14'),
    ('adapters/claude-code/skills/deep32/SKILL.md', "`around=<the hit's id>`", 'A16'),
    ('adapters/claude-code/skills/deep32/release-drift.md', '`same section in other releases:`', 'A16'),
    ('adapters/claude-code/skills/deep32/SKILL.md', "A hit's `platform`", 'A17'),
    ('adapters/claude-code/skills/deep32/SKILL.md', '`covers="..."` on a hit', 'A17'),
    ('adapters/claude-code/skills/deep32/citation-style.md', '`found outside <ref>:', 'A18'),
    ('adapters/claude-code/skills/deep32/release-drift.md', 'kind="history"', 'A15'),
    # added rules: each WP-A item adds its entries above this line
]


def files(roots, exts=('.md', '.cs', '.ps1', '.toml', '.yaml', '.sh')):
    for r in roots:
        if r is None:
            continue
        if os.path.isfile(r):
            yield r
            continue
        for d, _, names in os.walk(r):
            for n in names:
                if n.endswith(exts):
                    yield os.path.join(d, n)


def read(p):
    with open(p, encoding='utf-8', errors='replace') as f:
        return f.read()


def rel(p):
    return os.path.relpath(p, WORK).replace('\\', '/')


def allowed(path, term):
    r = rel(path)
    return any((r.endswith(suffix) or (suffix.endswith('/') and r.startswith(suffix))) and term == t for suffix, t, _ in ALLOW)


class Skills(unittest.TestCase):
    def test_1_twins_in_step(self):
        r = subprocess.run([sys.executable, '-I', '-B', os.path.join(HERE, 'sync_twins.py'), '--check'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_2_drift_is_detected_and_repaired(self):
        base = os.environ.get('TMPDIR') or os.environ.get('TEMP') or tempfile.gettempdir()
        os.makedirs(base, exist_ok=True)
        tmp = tempfile.mkdtemp(prefix='d32-twins-', dir=base)
        try:
            shutil.copytree(os.path.join(WORK, 'adapters', 'claude-code'), os.path.join(tmp, 'adapters', 'claude-code'))
            shutil.copytree(os.path.join(WORK, 'adapters', 'codex'), os.path.join(tmp, 'adapters', 'codex'))
            p = os.path.join(tmp, 'adapters', 'codex', 'skills', 'deep32', 'release-drift.md')
            with open(p, 'a', encoding='utf-8') as f:
                f.write('one drifted line\n')
            cmd = [sys.executable, '-I', '-B', os.path.join(HERE, 'sync_twins.py')]
            r = subprocess.run(cmd + ['--check', '--root', tmp], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn('release-drift.md', r.stdout)
            r = subprocess.run(cmd + ['--write', '--root', tmp], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_3_references_resolve(self):
        missing = []
        for p in files([CLAUDE]):
            for m in re.findall(r'\$\{CLAUDE_SKILL_DIR\}/([A-Za-z0-9_.-]+)', read(p)):
                if not os.path.isfile(os.path.join(CLAUDE, m)):
                    missing.append('%s -> %s' % (rel(p), m))
        for p in files([CODEX_SKILLS] + CODEX_KIT_FILES):
            for m in re.findall(r'\$\{CODEX_HOME\}/skills/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)', read(p)):
                if not os.path.isfile(os.path.join(CODEX_SKILLS, m[0], m[1])):
                    missing.append('%s -> %s/%s' % (rel(p), m[0], m[1]))
        for p in files([RELAY_SKILLS]):
            for m in re.findall(r'\]\(([A-Za-z0-9_./-]+\.md)\)', read(p)):
                if not os.path.isfile(os.path.normpath(os.path.join(os.path.dirname(p), m))):
                    missing.append('%s -> %s' % (rel(p), m))
        self.assertEqual(missing, [])

    def test_4_no_cut_wording(self):
        policy_path = os.environ.get('DEEP32_SKILLS_POLICY')
        if not policy_path:
            self.skipTest('private restricted-wording policy not supplied; full release gate remains required')
        with open(policy_path, encoding='utf-8') as policy_file:
            policy = json.load(policy_file)
        self.assertIsInstance(policy, dict)
        self.assertTrue(policy)
        for patterns in policy.values():
            self.assertIsInstance(patterns, list)
            self.assertTrue(patterns)
            self.assertTrue(all(isinstance(pattern, str) for pattern in patterns))
        hits = []
        roots = [CLAUDE, CODEX_SKILLS, RELAY_SKILLS] + RELAY_CODE + RELAY_HOST + CODEX_KIT_FILES
        for p in files(roots):
            for n, line in enumerate(read(p).splitlines(), 1):
                for kind, terms in policy.items():
                    for t in terms:
                        if re.search(t, line, re.I) and not allowed(p, t):
                            hits.append('%s:%d [%s %s] %s' % (rel(p), n, kind, t, line.strip()[:90]))
        self.assertEqual(hits, [], '\n' + '\n'.join(hits[:40]))

    def test_5_mcp_names(self):
        bad = []
        for p in files([CLAUDE, CODEX_SKILLS, RELAY_SKILLS] + CODEX_KIT_FILES):
            t = read(p)
            if 'mcp__rag' in t or 'mcp_servers.rag' in t:
                bad.append(rel(p))
        self.assertEqual(bad, [])
        skill = read(os.path.join(CLAUDE, 'SKILL.md'))
        self.assertEqual(len(re.findall(r'mcp__deep32__[a-z_]+', skill.split('ToolSearch call:')[1].split('\n')[0])), 11)

    def test_6_identity_is_consistent(self):
        import tomllib
        kit = os.path.join(WORK, 'adapters', 'codex')
        config = tomllib.loads(read(os.path.join(kit, 'config.deep32.toml')))
        self.assertEqual(list(config['mcp_servers']), ['deep32'])
        server = config['mcp_servers']['deep32']
        self.assertEqual(server['url'], '{{WORKSTATION_MCP_URL}}')
        self.assertEqual(server['bearer_token_env_var'], 'DEEP32_MCP_TOKEN')
        self.assertEqual(server['startup_timeout_sec'], 30)
        self.assertEqual(server['tool_timeout_sec'], 120)
        for name in ('config.deep32.toml', 'setup.ps1', 'setup.sh'):
            self.assertIn('DEEP32_MCP_TOKEN', read(os.path.join(kit, name)), name)
        for name in ('setup.ps1', 'setup.sh'):
            self.assertIn('{{WORKSTATION_MCP_URL}}', read(os.path.join(kit, name)), name)
        self.assertEqual(sorted(n for n in os.listdir(CODEX_SKILLS)
                                if os.path.isdir(os.path.join(CODEX_SKILLS, n))),
                         ['deep32', 'deep32-configure', 'deep32-describe', 'deep32-design',
                          'deep32-troubleshoot', 'deep32-verify'])

    def test_7_retired_rules_stay_out(self):
        hits = []
        for p in files([CLAUDE, CODEX_SKILLS] + STANDING):
            for n, line in enumerate(read(p).splitlines(), 1):
                for pattern, why in RETIRED:
                    if re.search(pattern, line):
                        hits.append('%s:%d [%s] %s' % (rel(p), n, why, line.strip()[:90]))
        self.assertEqual(hits, [], '\n' + '\n'.join(hits))

    def test_9_blueprint_names_1718_beside_26x(self):
        text = read(os.path.join(CLAUDE, 'blueprint.md'))
        for area in ('inthw', 'ip', 'lyr2', 'nmgmt', 'qos', 'rtng', 'sec', 'sysmgmt', 'vlan'):
            newer = re.findall(r'(?<![A-Za-z0-9-])c9300-26x-%s(?![A-Za-z0-9-])' % area, text)
            twins = re.findall(r'c9300-1718-%s, c9300-26x-%s(?![A-Za-z0-9-])' % (area, area), text)
            self.assertTrue(newer, area)
            self.assertEqual(len(twins), len(newer), area)

    def test_8_added_rules_stay(self):
        missing = [(path, phrase, item) for path, phrase, item in REQUIRED
                   if phrase not in read(os.path.join(WORK, *path.split('/')))]
        self.assertEqual(missing, [])


if __name__ == '__main__':
    unittest.main(verbosity=2 if '-v' in sys.argv else 1, argv=[sys.argv[0]])
