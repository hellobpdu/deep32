#!/usr/bin/env python3
"""Deterministic provenance. Usage: provenance.py --root DIR --output FILE.
Exit 0: written; 2: invalid or incomplete input. No network access.
"""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + '\n'


def members(root):
    root = Path(root).resolve()
    rows = []
    for path in sorted(root.rglob('*'), key=lambda p: p.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError('unsupported member: ' + relative)
        if path.is_file():
            rows.append({'path': relative, 'size': path.stat().st_size, 'sha256': digest(path)})
    return rows


def record(root, pins, epoch):
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
        raise ValueError('SOURCE_DATE_EPOCH must be a non-negative integer')
    created = dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat().replace('+00:00', 'Z')
    return {'format': 'deep32-forge-provenance/1', 'created': created,
            'source_date_epoch': epoch, 'pins': pins, 'models': pins['models'],
            'inputs': members(root)}


def verify_manifest(root, manifest):
    rows = json.loads(Path(manifest).read_text(encoding='ascii'))
    actual = members(root)
    if not isinstance(rows, list) or rows != actual:
        raise ValueError('payload inventory differs: missing, extra, changed or unsorted member')
    return len(rows)


def unresolved(value, prefix=''):
    if not prefix and isinstance(value, dict) and 'release_profile' in value:
        spec = importlib.util.spec_from_file_location('provenance_release_profile', Path(__file__).with_name('release_profile.py'))
        profile = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(profile)
        return profile.unresolved(value)
    found = []
    if isinstance(value, dict):
        for key, child in sorted(value.items()):
            found += unresolved(child, prefix + '.' + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found += unresolved(child, prefix + '.' + str(index))
    elif isinstance(value, str) and ('TO-PIN' in value or 'TO-SET' in value or 'deferred: v1.1 ISO form' in value or not value):
        found.append(prefix.lstrip('.'))
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', required=True, type=Path)
    ap.add_argument('--pins', required=True, type=Path)
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if args.dry_run:
        print('Would hash every sorted input, record pins and model revisions, and write provenance using SOURCE_DATE_EPOCH; downloads: none')
        return
    try:
        root = args.root.resolve()
        if args.output.resolve().is_relative_to(root):
            raise ValueError('provenance output must be outside the input tree')
        pins = json.loads(args.pins.read_text(encoding='ascii'))
        if unresolved(pins):
            raise ValueError('unresolved pins: ' + ', '.join(unresolved(pins)))
        value = record(root, pins, int(os.environ['SOURCE_DATE_EPOCH']))
        args.output.write_text(canonical(value), encoding='ascii')
    except (ValueError, KeyError, OSError) as exc:
        ap.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
