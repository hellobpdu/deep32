#!/usr/bin/env python3
"""Compare pinned source dependencies, without authorizing build-output reuse."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys


MAX_PIN_BYTES = 64 * 1024 * 1024
LINE = re.compile(r'([0-9a-f]{64})  (.+)\n')
# Conservative closures from the canonical stages 20,30,40,50,55 and the scripts
# they call. The whole pyrag subtree intentionally overapproximates each stage.
STAGES = {
    '20-py312': {
        'dependencies': ['pyrag/'],
        'required': ['pyrag/build-py312.sh', 'pyrag/d32log/'],
        'outputs': ['/usr/local/d32/py312', '/usr/local/d32/sqlite'],
        'external': ['cpython-and-sqlite-source-archives', 'ports-7.8-python-patches',
                     'identity-aor-and-glibc-inputs', 'openbsd-build-toolchain'],
    },
    '30-wheels': {
        'dependencies': ['pyrag/'],
        'required': ['pyrag/build-wheels.sh', 'pyrag/locks/'],
        'outputs': ['/build/lanes/pyrag/wheelhouse/cp312',
                    '/build/lanes/pyrag/wheelhouse/cp314',
                    '/build/lanes/pyrag/wheelhouse/SHA256SUMS'],
        'external': ['pyrag-dist-vendor-abseil-inputs', 'ports-8.0-patches',
                     'stage-20-output-identity', 'python-3.14-and-native-package-identities',
                     'openbsd-build-toolchain', 'jobs-setting'],
    },
    '40-venvs': {
        'dependencies': ['pyrag/'],
        'required': ['pyrag/make-venvs.sh', 'pyrag/locks/', 'pyrag/node/'],
        'outputs': ['/usr/local/d32/venv/deep32-engine',
                    '/usr/local/d32/venv/deep32-gateway',
                    '/usr/local/d32/deep32-engine', '/usr/local/d32/deep32-gateway',
                    '/usr/local/d32/node', '/usr/local/d32/syspick/edge'],
        'external': ['engine-and-gateway-source-archive-pins', 'stage-20-output-identity',
                     'stage-30-wheelhouse-identity', 'python-3.14-and-syspick-package-identities',
                     'openbsd-build-toolchain'],
    },
    '50-library': {
        'dependencies': ['library/', 'node/asklink/', 'ship/tools/stage.sh'],
        'required': ['library/', 'node/asklink/', 'ship/tools/stage.sh'],
        'outputs': ['/build/pobj/ship/library/d32library',
                    '/build/lanes/ship/build/asklink/d32-chatd',
                    '/build/lanes/ship/build/asklink/d32-chatctl'],
        'external': ['taxonomy-json-identity', 'native-header-and-library-identities',
                     'openbsd-build-toolchain'],
    },
    '55-update-builds': {
        'dependencies': ['ship/u10/', 'ship/u14/apps/', 'ship/u2/', 'ask-client/'],
        'required': ['ship/u10/bundle/build-native.sh', 'ship/u14/apps/build.sh',
                     'ship/u2/hardening/build.sh', 'ship/u2/src/',
                     'ask-client/window/Makefile'],
        'outputs': ['/build/dist/work/55-update-builds'],
        'external': ['openbsd-mac-vendor-package-pin', 'geo-source-pins',
                     'native-header-and-library-identities', 'openbsd-build-toolchain',
                     'allow-missing-setting'],
    },
}


class PinError(ValueError):
    pass


def parse_pin(raw):
    if type(raw) is not bytes or not raw or len(raw) > MAX_PIN_BYTES:
        raise PinError('pin-size')
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        raise PinError('pin-encoding') from None
    entries = {}
    previous = None
    for line in text.splitlines(keepends=True):
        match = LINE.fullmatch(line)
        if match is None:
            raise PinError('pin-format')
        digest, name = match.groups()
        path = PurePosixPath(name)
        if (not name or name != name.strip() or '\\' in name or ':' in name
                or any(ord(c) < 32 or ord(c) == 127 for c in name)
                or path.is_absolute() or path.as_posix() != name
                or '..' in path.parts or name in ('.', '..')):
            raise PinError('pin-path')
        if previous is not None and name <= previous:
            raise PinError('pin-order-or-duplicate')
        entries[name] = digest
        previous = name
    return entries


def read_pin(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_PIN_BYTES + 1)
    return raw, parse_pin(raw)


def selected(name, dependency):
    return name.startswith(dependency) if dependency.endswith('/') else name == dependency


def compare(old_raw, new_raw):
    old, new = parse_pin(old_raw), parse_pin(new_raw)
    stages = []
    for stage, spec in STAGES.items():
        old_names = {name for name in old if any(selected(name, dep) for dep in spec['dependencies'])}
        new_names = {name for name in new if any(selected(name, dep) for dep in spec['dependencies'])}
        changes = []
        for name in sorted(old_names | new_names):
            if old.get(name) != new.get(name):
                kind = 'added' if name not in old else 'deleted' if name not in new else 'modified'
                changes.append({'path': name, 'change': kind,
                                'old_sha256': old.get(name), 'new_sha256': new.get(name)})
        missing = []
        for label, entries in [('old', old), ('new', new)]:
            for dep in spec['required']:
                if not any(selected(name, dep) for name in entries):
                    missing.append({'pin': label, 'dependency': dep})
        stages.append({'stage': stage, 'pinned_sources_equal': not changes and not missing,
                       'dependencies': spec['dependencies'], 'changed': changes,
                       'missing': missing, 'old_members': len(old_names), 'new_members': len(new_names),
                       'required_existing_outputs': spec['outputs'],
                       'required_external_checks': [*spec['external'],
                           'stage-script-and-shared-lib-identity', 'existing-output-integrity'],
                       'reuse_authorized': False})
    return {'schema': 'deep32.build-source-reuse.v1',
            'old_pin_sha256': hashlib.sha256(old_raw).hexdigest(),
            'new_pin_sha256': hashlib.sha256(new_raw).hexdigest(),
            'stages': stages, 'reuse_authorized': False,
            'unverified': ['input-pins-authentic-and-complete', 'tree-bytes-match-pins',
                           'external-input-identity', 'toolchain-and-stage-driver-identity',
                           'existing-output-integrity'],
            'output_hazards': {'30-wheels': 'stage-10-removes-pyrag-wheelhouse-preserve-and-verify-before-stage-10',
                               '40-venvs': 'rerunning-stage-40-removes-stage-45-installed-files',
                               '50-library': 'stage-50-rebuild-removes-previous-ship-build-and-object-folders'}}


def main(argv=None):
    # CLI usage and fixed input failure are lead-review text.
    parser = argparse.ArgumentParser(description='Compare pinned build sources before reviewing output reuse.')
    parser.add_argument('--old-pin', type=Path, required=True)
    parser.add_argument('--new-pin', type=Path, required=True)
    parser.add_argument('--require', default='')
    args = parser.parse_args(argv)
    required = args.require.split(',') if args.require else []
    aliases = {name[:2]: name for name in STAGES} | {name: name for name in STAGES}
    if any(stage not in aliases for stage in required) or len(set(required)) != len(required):
        parser.error('The required stage list is invalid.')
    try:
        old_raw, _ = read_pin(args.old_pin)
        new_raw, _ = read_pin(args.new_pin)
        report = compare(old_raw, new_raw)
    except (OSError, PinError):
        # no raw path, PIN line or exception is printed.
        print('The pinned source comparison could not read valid PIN files.', file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    by_name = {row['stage']: row['pinned_sources_equal'] for row in report['stages']}
    return 0 if all(by_name[aliases[name]] for name in required) else 1


if __name__ == '__main__':
    raise SystemExit(main())
