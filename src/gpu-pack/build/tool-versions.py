#!/usr/bin/env python3
"""Print or compare exact build-tool version lines. Never downloads tools."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def version(name):
    if name == 'python':
        return sys.version.split()[0]
    args = {'tar': ['tar', '--version'], 'xorriso': ['xorriso', '-version']}[name]
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    return next(line.strip() for line in result.stdout.splitlines() if line.strip())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--pins', type=Path)
    ap.add_argument('--require', choices=['tar', 'xorriso', 'python'], default='python')
    ap.add_argument('--show', action='store_true')
    args = ap.parse_args()
    actual = {name: version(name) for name in sorted({'python', args.require})}
    if args.show:
        print(json.dumps(actual, sort_keys=True, indent=2))
        return
    if args.pins is None:
        ap.error('--pins or --show is required')
    expected = json.loads(args.pins.read_text(encoding='ascii'))['runtime']['build_tools']
    if any(expected[name] != value for name, value in actual.items()):
        ap.exit(2, 'Build tool version differs from pins\n')


if __name__ == '__main__':
    main()
