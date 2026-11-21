#!/usr/bin/env python3
"""Run the accepted guarded suite against each installed node's actual source.

Usage: python -I -B tests/pairing/run_nodes.py --tmp SCRATCH
Scratch holds only generated synthetic test material. No remote service is used.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
REFERENCE = HERE / 'reference'
NODES = {
    'workstation': REPO / 'src/workstation/distrib/site/usr/local/d32',
    'forge': REPO / 'src/gpu-pack/pairing',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage(node, target):
    """Keep the tests unchanged; libexec and bin have identical parent layout."""
    runtime = NODES[node]
    target.mkdir()
    (target / 'bin').mkdir()
    launcher_dir = runtime / ('libexec' if node == 'workstation' else 'bin')
    hashes = {}
    for name in ('deep32-pair', 'pairing-helper'):
        source = launcher_dir / name
        shutil.copy2(source, target / 'bin' / name)
        hashes['bin/' + name] = digest(source)
    shutil.copytree(runtime / 'deep32net', target / 'deep32net',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for source in sorted((runtime / 'deep32net').glob('*.py')):
        hashes['deep32net/' + source.name] = digest(source)
    for name in ('tests', 'vectors', 'tools'):
        shutil.copytree(REFERENCE / name, target / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    return hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tmp', required=True, type=Path)
    parser.add_argument('--node', choices=(*NODES, 'all'), default='all')
    parser.add_argument('--only', choices=('reference', 'integration', 'all'), default='all')
    args = parser.parse_args()
    scratch = args.tmp.resolve()
    if scratch == Path(scratch.anchor) or scratch == REPO or REPO in scratch.parents:
        parser.error('--tmp must be a separate scratch directory outside the repository')
    scratch.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TMP=str(scratch), TEMP=str(scratch), TMPDIR=str(scratch))
    reports = []
    selected = tuple(NODES) if args.node == 'all' else (args.node,)
    if args.only in ('reference', 'all'):
        for node in selected:
            with tempfile.TemporaryDirectory(prefix='synthetic-' + node + '-', dir=scratch) as work:
                work = Path(work)
                hashes = stage(node, work / 'runtime')
                report = scratch / (node + '-reference.json')
                command = [sys.executable, '-I', '-B', str(work / 'runtime/tests/run_all.py'),
                           '--tmp', str(work / 'generated'), '--report', str(report)]
                process = subprocess.run(command, env=env, check=False)
                data = json.loads(report.read_bytes()) if report.is_file() else {'tests_errors': 1}
                data.update(node=node, source_sha256=hashes, exit=process.returncode)
                report.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='ascii')
                reports.append(data)
                print(node + ': ' + data.get('ran', 'suite failed before report'), flush=True)
    if args.only in ('integration', 'all'):
        report = scratch / 'cross-node-integration.json'
        command = [sys.executable, '-I', '-B', str(HERE / 'test_integration.py'),
                   '--tmp', str(scratch), '--report', str(report)]
        process = subprocess.run(command, env=env, check=False)
        data = json.loads(report.read_bytes()) if report.is_file() else {'tests_errors': 1}
        data.update(exit=process.returncode)
        reports.append(data)
    return int(any(data.get('exit') or data.get('tests_failed') or data.get('tests_errors')
                   for data in reports))


if __name__ == '__main__':
    raise SystemExit(main())
