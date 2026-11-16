#!/usr/bin/env python3
"""Lock helper run INSIDE the pinned runtime base container by resolve-pins.py (locks family).

Usage: lockgen.py pick-torch --spec 'torch==V' --index URL
       lockgen.py resolve --requirements FILE --index URL
       lockgen.py verify --lock FILE --index URL --torch-index URL
Prints one line 'D32-JSON {...}' on stdout; pip's own output goes to stderr.
pick-torch and resolve use 'pip install --dry-run --report' (no install, metadata only where the
index offers it). verify mirrors the kit's wheel groups: wheels split into
torch and the rest, fetched with 'pip --isolated download --require-hashes --no-deps
--only-binary=:all: --no-cache-dir'; the allowed jieba sdist uses --no-binary=jieba alone.
The sdist's downloaded bytes are independently checked against the lead's table;
the kit fetches that archive from its fixed, reviewed URL. All verification downloads
use temporary folders that are removed afterwards.
Standard library only. Exit 0 on success; non-zero when pip fails.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import sysconfig
import tempfile


# Lead ruling 2026-11-15: jieba 0.42.1 publishes no wheel on PyPI.
# Keep this table equal to SDIST_ALLOWED in resolve-pins.py; tests check parity.
SDIST_ALLOWED = {('jieba', '0.42.1'): '055ca12f62674fafed09427f176506079bc135638a14e23e25be909131928db2'}  # sha256 or public signing fingerprint


def normalise_name(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def pip_command():
    # D32_LOCKGEN_PIP is a test hook: a stand-in pip program. Never set on the lab host.
    stand_in = os.environ.get('D32_LOCKGEN_PIP')
    return [sys.executable, '-I', '-B', stand_in] if stand_in else [sys.executable, '-I', '-B', '-m', 'pip']


def pip(arguments):
    subprocess.run(pip_command() + arguments, check=True, stdout=sys.stderr)


def environment():
    version = subprocess.run(pip_command() + ['--version'], capture_output=True, text=True)
    glibc = os.confstr('CS_GNU_LIBC_VERSION') if hasattr(os, 'confstr') and 'CS_GNU_LIBC_VERSION' in os.confstr_names else ''
    return {'python': platform.python_version(), 'implementation': platform.python_implementation(),
            'platform': sysconfig.get_platform(), 'glibc': glibc or '', 'pip': version.stdout.strip()}


def emit(value):
    print('D32-JSON ' + json.dumps(value, sort_keys=True), flush=True)


def report(arguments):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'report.json'
        exceptions = ['--no-binary=' + name for name in sorted({key[0] for key in SDIST_ALLOWED})]
        pip(['--isolated', 'install', '--dry-run', '--ignore-installed', '--only-binary=:all:'] + exceptions + ['--no-cache-dir',
             '--disable-pip-version-check', '--quiet', '--report', str(path)] + arguments)
        result = json.loads(path.read_text(encoding='utf-8'))
        for item in result.get('install', []):
            url = (item.get('download_info') or {}).get('url', '')
            if url.endswith('.whl'):
                continue
            name, version = item['metadata']['name'], item['metadata']['version']
            key = (normalise_name(name), version)
            if key not in SDIST_ALLOWED:
                raise SystemExit('sdist not allowed: ' + name + ' ' + version + ' (' + url + ')')
            digest, wanted = item_hash(item), SDIST_ALLOWED[key]
            if digest != wanted:
                raise SystemExit('sdist hash differs: ' + name + ' ' + version + ': report ' + digest + ', allowed ' + wanted)
        return result


def item_hash(item):
    archive = (item.get('download_info') or {}).get('archive_info') or {}
    value = (archive.get('hashes') or {}).get('sha256')
    if not value and str(archive.get('hash', '')).startswith('sha256='):
        value = archive['hash'][len('sha256='):]
    return value or ''


def pick_torch(args):
    result = report(['--no-deps', '--index-url', args.index, args.spec])
    items = result.get('install', [])
    if len(items) != 1:
        raise SystemExit('expected exactly one torch wheel, got ' + str(len(items)))
    item = items[0]
    emit({'env': environment(), 'name': item['metadata']['name'], 'version': item['metadata']['version'],
          'url': item['download_info']['url'], 'sha256': item_hash(item)})


def resolve(args):
    emit({'env': environment(), 'report': report(['--index-url', args.index, '--requirement', args.requirements])})


def verify(args):
    # Same grouping as kit/build/fetch-inputs.py fetch_wheels().
    groups = {'torch': [], 'other': []}
    sdists = {}
    for line in Path(args.lock).read_text(encoding='ascii').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        spec = line.partition(' ')[0]
        name, _, version = spec.partition('==')
        name = normalise_name(name)
        if (name, version) in SDIST_ALLOWED:
            wanted = SDIST_ALLOWED[(name, version)]
            hashes = re.findall(r'--hash=sha256:([0-9a-f]{64})', line)
            if hashes != [wanted]:
                raise SystemExit('sdist lock hash differs: ' + name + ' ' + version)
            sdists[(name, version)] = line
            continue
        groups['torch' if name == 'torch' else 'other'].append(line)
    if not groups['torch'] or not (groups['other'] or sdists):
        raise SystemExit('lock must include torch and its complete dependency closure')
    with tempfile.TemporaryDirectory() as directory:
        dest = Path(directory) / 'wheelhouse'
        dest.mkdir()
        for group, lines in groups.items():
            if not lines:
                continue
            requirement = Path(directory) / (group + '.lock')
            requirement.write_text('\n'.join(lines) + '\n', encoding='ascii', newline='\n')
            pip(['--isolated', 'download', '--disable-pip-version-check', '--require-hashes', '--no-deps',
                 '--only-binary=:all:', '--no-cache-dir', '--index-url', args.torch_index if group == 'torch' else args.index,
                 '--requirement', str(requirement), '--dest', str(dest)])
        for (name, version), line in sorted(sdists.items()):
            requirement = Path(directory) / (name + '.lock')
            requirement.write_text(line + '\n', encoding='ascii', newline='\n')
            source_dest = Path(directory) / name
            source_dest.mkdir()
            pip(['--isolated', 'download', '--disable-pip-version-check', '--require-hashes', '--no-deps',
                 '--no-binary=' + name, '--no-cache-dir', '--index-url', args.index,
                 '--requirement', str(requirement), '--dest', str(source_dest)])
            filename = name + '-' + version + '.tar.gz'
            if sorted(path.name for path in source_dest.iterdir()) != [filename]:
                raise SystemExit('sdist download differs: ' + name + ' ' + version + ': expected ' + filename)
            archive = source_dest / filename
            digest = hashlib.sha256(archive.read_bytes()).hexdigest() if archive.is_file() else ''
            wanted = SDIST_ALLOWED[(name, version)]
            if digest != wanted:
                raise SystemExit('sdist hash differs: ' + name + ' ' + version + ': downloaded ' + digest + ', allowed ' + wanted)
            archive.replace(dest / filename)
        files = []
        for path in sorted(dest.iterdir()):
            value = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1 << 20), b''):
                    value.update(block)
            files.append({'filename': path.name, 'sha256': value.hexdigest(), 'size': path.stat().st_size})
    emit({'env': environment(), 'files': files})


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    one = commands.add_parser('pick-torch')
    one.add_argument('--spec', required=True)
    one.add_argument('--index', required=True)
    two = commands.add_parser('resolve')
    two.add_argument('--requirements', required=True)
    two.add_argument('--index', required=True)
    three = commands.add_parser('verify')
    three.add_argument('--lock', required=True)
    three.add_argument('--index', required=True)
    three.add_argument('--torch-index', required=True)
    args = parser.parse_args()
    {'pick-torch': pick_torch, 'resolve': resolve, 'verify': verify}[args.command](args)


if __name__ == '__main__':
    main()
