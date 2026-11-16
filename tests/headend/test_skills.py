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


if __name__ == '__main__':
    unittest.main(verbosity=2 if '-v' in sys.argv else 1, argv=[sys.argv[0]])
