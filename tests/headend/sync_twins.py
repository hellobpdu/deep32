"""sync_twins.py: keeps the Codex twin of the deep32 research skill in step with the Claude twin.

The Claude twin (adapters/claude-code/skills/deep32) is the source for the nine shared reference files. Each is copied to the Codex twin
(adapters/codex/skills/deep32) with one change: the path style. ${CLAUDE_SKILL_DIR} becomes ${CODEX_HOME}/skills/deep32,
which the installer rewrites to the absolute path of the person's Codex home (as the old kit's setup.ps1 did).
SKILL.md and deep-engine.md are each twin's own (Claude runs rounds as parallel tool calls, Codex as code-mode
scripts) and are never copied; the check only confirms they exist and use their own path style.

Usage:   python -I sync_twins.py --check  [--root <repository>]   the diff test: report drift, change nothing
         python -I sync_twins.py --write  [--root <repository>]   copy the shared files, then run the check
Exit:    0 twins in step; 1 drift found (each difference is printed); 2 bad arguments or a twin folder missing.
"""
import argparse
import difflib
import os
import sys

SHARED = ['citation-style.md', 'collections.md', 'expanded-answer.md', 'outline-check.md', 'query-planning.md',
          'quick-default.md', 'release-drift.md', 'rfc-pitfalls.md', 'troubleshooting.md']
OWN = ['SKILL.md', 'deep-engine.md']
CLAUDE_DIR, CODEX_DIR = '${CLAUDE_SKILL_DIR}', '${CODEX_HOME}/skills/deep32'


def to_codex(text):
    return text.replace(CLAUDE_DIR, CODEX_DIR)


def read(path):
    with open(path, encoding='utf-8', newline='') as f:
        return f.read()


def check(claude, codex):
    problems = []
    want = sorted(SHARED + OWN)
    for name, folder in (('claude', claude), ('codex', codex)):
        have = sorted(n for n in os.listdir(folder) if os.path.isfile(os.path.join(folder, n)))
        if have != want:
            problems.append('%s twin file set differs: extra %s, missing %s' % (
                name, sorted(set(have) - set(want)), sorted(set(want) - set(have))))
        subdirs = [n for n in os.listdir(folder) if os.path.isdir(os.path.join(folder, n))]
        if subdirs:
            problems.append('%s twin has sub-folders %s' % (name, subdirs))
    for n in SHARED:
        a, b = os.path.join(claude, n), os.path.join(codex, n)
        if not (os.path.isfile(a) and os.path.isfile(b)):
            continue
        expected, actual = to_codex(read(a)), read(b)
        if expected != actual:
            diff = list(difflib.unified_diff(expected.splitlines(), actual.splitlines(), 'expected/' + n, 'codex/' + n, lineterm='', n=0))
            problems.append('shared file %s differs (%d diff lines):\n  %s' % (n, len(diff), '\n  '.join(diff[:12])))
    for n in SHARED + OWN:
        a, b = os.path.join(claude, n), os.path.join(codex, n)
        if os.path.isfile(a) and CODEX_DIR in read(a):
            problems.append('claude twin %s uses the Codex path style' % n)
        if os.path.isfile(b) and CLAUDE_DIR in read(b):
            problems.append('codex twin %s uses the Claude path style' % n)
    return problems


def main():
    ap = argparse.ArgumentParser(add_help=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--check', action='store_true')
    g.add_argument('--write', action='store_true')
    ap.add_argument('--root', default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    try:
        args = ap.parse_args()
    except SystemExit:
        return 2
    claude = os.path.join(args.root, 'adapters', 'claude-code', 'skills', 'deep32')
    codex = os.path.join(args.root, 'adapters', 'codex', 'skills', 'deep32')
    if not (os.path.isdir(claude) and os.path.isdir(codex)):
        print('twin folder missing under %s' % args.root)
        return 2
    if args.write:
        for n in SHARED:
            with open(os.path.join(codex, n), 'w', encoding='utf-8', newline='') as f:
                f.write(to_codex(read(os.path.join(claude, n))))
        print('wrote %d shared files to the Codex twin' % len(SHARED))
    problems = check(claude, codex)
    for p in problems:
        print('DRIFT: ' + p)
    print('twins in step' if not problems else '%d difference(s)' % len(problems))
    return 0 if not problems else 1


if __name__ == '__main__':
    sys.exit(main())
