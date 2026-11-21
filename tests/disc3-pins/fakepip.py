#!/usr/bin/env python3
"""Stand-in for pip used only by the offline tests (through lockgen.py's D32_LOCKGEN_PIP hook).

Reads a JSON universe (D32_FAKEPIP_UNIVERSE) of indexes -> distributions and emulates the three
pip calls lockgen.py makes: '--version', 'install --dry-run --report' and
'download --require-hashes --no-deps'. Never touches the network.
"""
import json
import os
from pathlib import Path
import re
import sys


def norm(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def matches(spec_version, version):
    return version == spec_version or ('+' not in spec_version and version.split('+')[0] == spec_version)


def option(args, name):
    return args[args.index(name) + 1] if name in args else None


def content(filename):
    return ('fake wheel ' + filename).encode('ascii')


def find(universe, index, name, version, hashes=None):
    for row in universe['indexes'].get(index, {}).get(norm(name), []):
        if matches(version, row['version']) and (hashes is None or row['sha256'] in hashes):
            return row
    return None


def resolve(universe, index, lines, no_deps):
    chosen, queue = {}, [(line, True) for line in lines]
    while queue:
        spec, requested = queue.pop(0)
        direct = None
        if ' @ ' in spec:
            name, url = spec.split(' @ ', 1)
            base, _, fragment = url.partition('#')
            rows = [row for rows in universe['indexes'].values() for values in rows.values() for row in values if row['url'] == base]
            if not rows or fragment != 'sha256=' + rows[0]['sha256']:
                raise SystemExit('fakepip: direct URL not found or hash differs: ' + url)
            row, direct = rows[0], base
        else:
            name, version = spec.split('==')
            row = find(universe, index, name, version)
            if row is None:
                raise SystemExit('fakepip: no candidate for ' + spec + ' on ' + index)
        key = norm(row['name'])
        if key in chosen:
            if chosen[key]['metadata']['version'] != row['version']:
                raise SystemExit('fakepip: conflict on ' + key)
            continue
        chosen[key] = {'metadata': {'name': row['name'], 'version': row['version']}, 'requested': requested,
                       'is_direct': bool(direct), 'is_yanked': False,
                       'download_info': {'url': row['url'], 'archive_info': {} if row.get('nohash') else
                                         {'hashes': {'sha256': row['sha256']}, 'hash': 'sha256=' + row['sha256']}}}
        if not no_deps:
            queue += [(dep, False) for dep in row.get('deps', [])]
    return [chosen[key] for key in sorted(chosen)]


def main():
    args = sys.argv[1:]
    calls = os.environ.get('D32_FAKEPIP_CALLS')
    if calls:
        with Path(calls).open('a', encoding='ascii', newline='\n') as stream:
            stream.write(json.dumps(args, ensure_ascii=True) + '\n')
    if args == ['--version']:
        print('pip 99.0 (fakepip)')
        return
    universe = json.loads(Path(os.environ['D32_FAKEPIP_UNIVERSE']).read_text(encoding='utf-8'))
    index = option(args, '--index-url')
    requirement = option(args, '--requirement')
    lines = []
    if requirement:
        lines += [line.strip() for line in Path(requirement).read_text(encoding='ascii').splitlines()
                  if line.strip() and not line.startswith('#')]
    values = {option(args, name) for name in ('--index-url', '--requirement', '--report', '--dest')}
    lines += [arg for arg in args if not arg.startswith('-') and arg not in values and arg not in ('install', 'download')]
    if 'install' in args:
        report = {'version': '1', 'pip_version': '99.0', 'environment': {},
                  'install': resolve(universe, index, lines, '--no-deps' in args)}
        Path(option(args, '--report')).write_text(json.dumps(report), encoding='utf-8')
    elif 'download' in args:
        if '--require-hashes' not in args or '--no-deps' not in args:
            raise SystemExit('fakepip: download needs the kit flags')
        dest = Path(option(args, '--dest'))
        for line in lines:
            spec, _, rest = line.partition(' ')
            name, version = spec.split('==')
            binary = '--no-binary=' + norm(name) if norm(name) == 'jieba' else '--only-binary=:all:'
            if binary not in args:
                raise SystemExit('fakepip: download needs ' + binary)
            hashes = set(re.findall(r'--hash=sha256:([0-9a-f]{64})', rest))
            row = find(universe, index, name, version, hashes)
            if row is None:
                raise SystemExit('fakepip: hash-checked download failed for ' + line)
            (dest / row['filename']).write_bytes(content(row['filename']))
    else:
        raise SystemExit('fakepip: unsupported call')


if __name__ == '__main__':
    main()
