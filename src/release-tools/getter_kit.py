#!/usr/bin/env python3
"""Export the exact offline downloader and installer-entrypoint support kit.

The archive keeps repository-relative paths. Its bytes are an ordinary small
asset: the release owner must explicitly put it in the signed metadata list.
GPU runtime files come from the separately verified mounted media.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[2]
FILES = (
    'LICENSE', 'NOTICE', 'notices/NOTICE', 'notices/THIRD-PARTY-NOTICES.md',
    'install/deep32-inventory.sh', 'install/deep32-inventory.ps1',
    'install/matrix.json', 'install/prerequisites.json',
    'install/release-key.gpg', 'install/release-key.fingerprint',
    'src/gpu-pack/autoinstall/render-settings.py',
    *(f'src/gpu-pack/forms/{name}.py' for name in (
        'forge_publication', 'host_security', 'installed_runtime')),
    'src/gpu-pack/pairing/bin/pairing-helper',
    *(f'src/gpu-pack/pairing/deep32net/{name}.py' for name in (
        '__init__', '__main__', 'cli', 'codes', 'discovery', 'errors', 'files',
        'identity', 'p256', 'pairing', 'pairlock', 'spake2', 'store',
        'tokens', 'transport', 'window', 'wire')),
    'src/workstation/release/tools/raw2vhd.py',
    *(f'src/release-tools/{name}' for name in (
        'deep32-get.sh', 'deep32-get.ps1', 'deep32_get_manifest.py',
        'deep32_get_whole.py', 'deep32_download.py', 'deep32_settings.py',
        'contract_validate.py', 'capabilities.py', 'getter_kit.py',
        'forge_preflight.py', 'forge_requirements.py', 'forge_ports.py',
        'forge_pair_windows.py', 'forge_preflight_windows.py',
        'verify-release.sh', 'verify-release.ps1',
        'join_and_verify.sh', 'join_and_verify.ps1')),
    *(f'src/release-tools/contracts/schemas/{name}.schema.json' for name in (
        'asset-map', 'capability-manifest', 'doctor-install', 'doctor-output',
        'hash-graph', 'mirrors', 'mirrors-v1', 'release-metadata', 'reproducibility',
        'sbom-gap', 'sbom-index', 'sbom-input', 'selftest-calibration',
        'selftest-questions', 'selftest-reference', 'selftest-result')),
)


def regular(path):
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Getter kit sources must not contain links or reparse points')
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('A required getter kit source is not a regular file')
    return path


def archive_bytes(root=ROOT):
    root = Path(root).absolute()
    sources = {}
    for name in sorted(FILES):
        source = regular(root / name)
        if not 0 < source.stat().st_size <= 5 * 1024 * 1024:
            raise ValueError('A getter kit source has an invalid size')
        sources[name] = source.read_bytes()
    manifest = {'format': 'deep32-getter-kit', 'format_version': 1,
                'files': [{'path': name, 'size': len(data),
                           'sha256': hashlib.sha256(data).hexdigest()}
                          for name, data in sorted(sources.items())]}
    sources['GETTER-KIT.json'] = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode('ascii')
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as target:
        for name, data in sorted(sources.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100755 if name.endswith('.sh') else 0o100644) << 16
            target.writestr(info, data)
    data = output.getvalue()
    if len(data) > 5 * 1024 * 1024:
        raise ValueError('Getter kit exceeds the small metadata asset limit')
    return data


def build(output):
    output = Path(output).absolute()
    for part in (*reversed(output.parent.parents), output.parent):
        info = part.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Getter kit output ancestors must be ordinary directories')
    data = archive_bytes()
    with output.open('xb') as stream:
        stream.write(data)
    return len(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        print('Wrote %d bytes.' % build(args.out))
    except (OSError, ValueError) as error:
        parser.exit(1, 'Getter kit refused: %s\n' % error)


if __name__ == '__main__':
    main()
