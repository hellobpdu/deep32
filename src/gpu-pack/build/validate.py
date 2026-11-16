#!/usr/bin/env python3
"""Validate complete staged inputs, without downloads. Exit 0 valid, 2 invalid."""
import argparse
import importlib.util
import json
from pathlib import Path
import re

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))  # siblings (image_binding, provenance) under any loader
from provenance import digest, unresolved, verify_manifest
from distributions import validate_wheelhouse
from image_binding import check_archive
_builder_spec = importlib.util.spec_from_file_location('validated_builder_inputs', Path(__file__).with_name('builder_inputs.py'))
_builder_inputs = importlib.util.module_from_spec(_builder_spec)
_builder_spec.loader.exec_module(_builder_inputs)
lock_matches = _builder_inputs.lock_matches
_mineru_spec = importlib.util.spec_from_file_location('validated_mineru_config', Path(__file__).with_name('pdf_model_config.py'))
_mineru_config = importlib.util.module_from_spec(_mineru_spec)
_mineru_spec.loader.exec_module(_mineru_config)
_layout_spec = importlib.util.spec_from_file_location('validated_mineru_layout', Path(__file__).with_name('pdf_model_layout.py'))
_mineru_layout = importlib.util.module_from_spec(_layout_spec)
_layout_spec.loader.exec_module(_mineru_layout)

_profile_spec = importlib.util.spec_from_file_location('forge_release_profile', Path(__file__).with_name('release_profile.py'))
release_profile = importlib.util.module_from_spec(_profile_spec)
_profile_spec.loader.exec_module(release_profile)


def validate_mineru(payload, pins):
    home = payload / 'models/mineru-home'
    config = home / 'config.yaml'
    if config.is_symlink() or not config.is_file():
        raise ValueError('missing MinerU config.yaml')
    _mineru_config.validate_mineru_config(config.read_bytes())
    _mineru_layout.validate_home(home)
    for role in ('mineru_small', 'mineru_vlm'):
        model = pins['models'][role]
        if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', model['id'])
                or any(part in {'.', '..'} for part in model['id'].split('/'))
                or not re.fullmatch('[0-9a-f]{40}', model['revision'])):
            raise ValueError('invalid pinned MinerU model: ' + role)
        root = home / 'models' / model['id'].split('/')[1]
        rows = model['files']
        _mineru_layout.refuse_upstream_markers(row['path'] for row in rows)
        expected = {row['path']: row['sha256'] for row in rows}
        if not expected or len(expected) != len(rows) or root.is_symlink() or not root.is_dir():
            raise ValueError('missing complete MinerU model: ' + role)
        actual = {}
        for path in sorted(root.rglob('*')):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError('special MinerU snapshot member: ' + role)
            if path.is_file():
                if path.relative_to(home).as_posix() not in _mineru_layout.COMPLETION_MARKERS:
                    actual[path.relative_to(root).as_posix()] = digest(path)
        if actual != expected:
            raise ValueError('MinerU snapshot differs from complete pinned manifest: ' + role)


def requirement_key(line):
    """Name and exact version, with the project name normalised as pip does (PyMuPDF == pymupdf)."""
    name, _, rest = line.strip().partition('==')
    return re.sub(r'[-_.]+', '-', name).lower(), rest.split(' ', 1)[0]


def validate_runtime_requirements(pins):
    for field, floor in (('docker_minimum_version', (19, 3, 0)), ('compose_minimum_version', (2, 30, 0))):
        value = pins.get('runtime', {}).get(field)
        if value is None:
            continue  # Registers from before the pack profile have no host requirement pins.
        if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', value):
            raise ValueError('invalid runtime.' + field)
        actual = tuple(int(part) for part in value.split('.'))
        actual += (0,) * (3 - len(actual))
        if actual < floor:
            raise ValueError('runtime.' + field + ' must be at least ' + '.'.join(map(str, floor)))


def validate_images(payload, pins):
    """Verify every archive against its recorded pair and source/runtime pins."""
    images = json.loads((payload / 'images/index.json').read_text(encoding='ascii'))
    roles = {row['role']: row for row in images}
    needed = {'reranker', 'converter', 'door', 'builder', 'qdrant'} | {'tei-' + name for name in pins['tei']['variants']}
    if len(roles) != len(images) or set(roles) != needed:
        raise ValueError('image roles must include all card classes and all application images')
    for role, row in roles.items():
        if not re.fullmatch(r'images/[A-Za-z0-9._-]+\.tar', row['path']) or digest(payload / row['path']) != row['sha256']:
            raise ValueError('image archive hash differs: ' + role)
        check_archive(payload / row['path'], row, row.get('source_digest'))
        if role.startswith('tei-'):
            variant = pins['tei']['variants'][role[4:]]
            if not re.fullmatch('sha256:[0-9a-f]{64}', variant['digest']) or row.get('source_digest') != variant['digest']:
                raise ValueError('TEI source digest differs: ' + role)
        else:
            expected_id = pins['runtime'][role + '_image_id']
            if (not isinstance(expected_id, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', expected_id)
                    or expected_id not in (row['config_digest'], row['index_digest'])):
                raise ValueError('application image pin differs: ' + role)
        if role == 'qdrant':
            expected = pins['builder']['qdrant']
            if expected['version'] != '1.19.1' or row.get('version') != '1.19.1' or row.get('source_digest') != expected['image'].split('@')[1]:
                raise ValueError('Qdrant version/source digest differs')
            check_archive(payload / row['path'], expected, expected['image'].split('@')[1])


def validate(stage, kit):
    pins = json.loads((stage / 'pins.json').read_text(encoding='ascii'))
    missing = release_profile.unresolved(pins)
    if missing:
        raise ValueError('TO-PIN: ' + ', '.join(missing))
    if pins['format'] != 'deep32-forge-pins/1' or pins['ubuntu']['release'] != '24.04.5':
        raise ValueError('Ubuntu release or pins format changed; review the kit before proceeding')
    validate_runtime_requirements(pins)
    if not release_profile.is_pack(pins):
        if pins['ubuntu']['sha256'] != '97f3d7ffb032c3eb3b23d2c8be9cc76e60c2c1f2c0146ba5ba9fe01cafae0fd8':
            raise ValueError('unexpected Ubuntu ISO pin')
        track = pins['ubuntu']['kernel_track']
        expected = {'ga': ('linux-generic', 'linux-modules-nvidia-580-server-generic'),
                    'hwe': ('linux-generic-hwe-24.04', 'linux-modules-nvidia-580-server-generic-hwe-24.04')}
        if track not in expected:
            raise ValueError('unsupported kernel track')
        for field, name in zip(('kernel_package', 'modules_package'), expected[track]):
            if not pins['ubuntu'][field].startswith(name + '='):
                raise ValueError('kernel and signed module track differ')
    for package in release_profile.packages(pins) + ([] if release_profile.is_pack(pins) else pins['ubuntu']['driver_packages'] + [pins['ubuntu']['kernel_package'], pins['ubuntu']['modules_package']]):
        if not re.fullmatch(r'[a-z0-9.+-]+=[A-Za-z0-9.:+~_-]+', package) or 'dkms' in package:
            raise ValueError('invalid or DKMS package')
    release_profile.require_package_closure(pins)
    count = verify_manifest(stage / 'payload', stage / 'PAYLOAD.json')
    payload = stage / 'payload'
    required = ['models/tei', 'models/reranker', 'models/mineru-home/config.yaml', 'images', 'repos/ubuntu',
                'repos/nvidia', 'repos/tailscale', 'wheelhouse/reranker', 'wheelhouse/converter',
                'locks/reranker.lock', 'locks/converter.lock', 'licences/AGPL-3.0.txt',
                'bin/pairing-helper', 'source/production-converter', 'evidence/input-attestation.json',
                'keyrings/nvidia.gpg', 'keyrings/tailscale.gpg']
    for relative in required:
        path = payload / relative
        if not path.exists() or (path.is_dir() and not any(path.iterdir())) or (path.is_file() and not path.stat().st_size):
            raise ValueError('missing input: ' + relative)
    if digest(payload / 'bin/pairing-helper') != pins['runtime']['pairing_helper_sha256']:
        raise ValueError('pairing helper hash differs')
    validate_mineru(payload, pins)
    for role in ('tei', 'reranker'):
        revision = pins['models'][role]['revision']
        snapshot = payload / 'models' / role / ('models--' + pins['models'][role]['id'].replace('/', '--')) / 'snapshots' / revision
        if not re.fullmatch('[0-9a-f]{40}', revision) or not (snapshot / 'config.json').is_file() or not any(snapshot.glob('*.safetensors')):
            raise ValueError('missing immutable model snapshot: ' + role)
        weight_hash = {'tei': '0362107c2b13e18284c5152c4d4f667a4dec4665abdfe4f5e43a9bb799ba2276',
                       'reranker': '50c684b703a1770389cd2992c6ca3c5f76ad0a270e975374c182cde3e5dfb6fe'}[role]  # SHA256
        if digest(snapshot / 'model.safetensors') != weight_hash:
            raise ValueError('model weights differ from the locally recorded pin: ' + role)
    for lock_name in ('reranker', 'converter'):
        lines = (payload / 'locks' / (lock_name + '.lock')).read_text(encoding='ascii').splitlines()
        requirements = [line for line in lines if line.strip() and not line.startswith('#')]
        if not requirements or any(not re.fullmatch(r'[A-Za-z0-9_.-]+==[A-Za-z0-9.+_-]+(?: --hash=sha256:[0-9a-f]{64})+', line) for line in requirements):
            raise ValueError('lock must contain full exact, hashed, single-line requirements: ' + lock_name)
        validate_wheelhouse(payload / 'wheelhouse' / lock_name, '\n'.join(requirements))
        direct = (kit / 'containers' / ('converter-agpl' if lock_name == 'converter' else lock_name) / 'requirements-direct.txt').read_text(encoding='ascii').splitlines()
        for requirement in direct:
            if not requirement or requirement.startswith('#'):
                continue
            requirement = requirement.replace('TO-PIN', pins['runtime']['pymupdf_version'])
            if requirement_key(requirement) not in {requirement_key(line) for line in requirements}:
                raise ValueError('direct requirement differs: ' + requirement)
    attestation = json.loads((payload / 'evidence/input-attestation.json').read_text(encoding='ascii'))
    for name in ('archive_signatures_verified', 'rootfs_clean', 'model_revisions_verified', 'converter_source_reviewed', 'licences_complete'):
        if attestation.get(name) is not True:
            raise ValueError('missing operator evidence: ' + name)
    if not attestation.get('records'):
        raise ValueError('attestation must name relative evidence records in the payload')
    for name in attestation['records']:
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or not (payload / path).is_file():
            raise ValueError('invalid evidence record')
    validate_images(payload, pins)
    for relative in ('locks/builder.lock', 'wheelhouse/builder'):
        if not (payload / relative).exists():
            raise ValueError('missing builder payload: ' + relative)
    if digest(payload / 'locks/builder.lock') != pins['builder']['engine_lock_sha256']:
        raise ValueError('builder engine lock hash differs')
    lock_matches(payload / 'locks/builder.lock', kit / 'containers/builder/requirements-engine.in')
    validate_wheelhouse(payload / 'wheelhouse/builder', (payload / 'locks/builder.lock').read_text(encoding='ascii'))
    return count


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--stage', type=Path, required=True)
    ap.add_argument('--kit', type=Path, required=True)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if args.dry_run:
        print('Would validate pins, complete hashed payload, archive evidence, model snapshots, locks and image inventory; downloads: none')
        return
    try:
        print('Staged files verified:', validate(args.stage, args.kit))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        ap.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
