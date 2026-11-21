#!/usr/bin/env python3
"""Build payload-free forms from explicit runtime file lists, offline only."""
import argparse
import io
import json
from pathlib import Path
import tarfile

LIMIT = 1024 ** 3
# The WSL form is Canonical's clean Ubuntu base (about 1.13 GiB unpacked for 24.04.5) plus the runtime kit, so it
# gets its own ceiling; both limits still refuse any form that carries payload images (lead fix 2026-11-16).
WSL_LIMIT = 2 * 1024 ** 3
PAIRING_MODULES = ('__init__', '__main__', 'cli', 'codes', 'discovery', 'errors', 'files',
                   'identity', 'p256', 'pairing', 'pairlock', 'spake2', 'store', 'tokens',
                   'transport', 'window', 'wire')


def runtime_mode(path):
    return 0o755 if path.suffix == '.sh' or path.name in ('deep32-pair', 'pairing-helper') else 0o644


def pairing_files(kit):
    """The complete pairing runtime, without reference tests or vectors."""
    names = ['pairing/bin/deep32-pair', 'pairing/bin/pairing-helper']
    names += ['pairing/deep32net/' + name + '.py' for name in PAIRING_MODULES]
    paths = [kit / name for name in names]
    for path in paths:
        if (not path.is_file() or path.is_symlink() or path.is_junction()
                or any(p.is_symlink() or p.is_junction() for p in path.parents)):
            raise ValueError('missing or unsafe pairing runtime: ' + path.relative_to(kit).as_posix())
    return paths


def runtime_files(kit):
    paths = pairing_files(kit)
    # licenses/ holds third-party license texts that travel with the kit (MinerU's, for the converter).
    for name in ('autoinstall', 'firstboot', 'import', 'licenses'):
        paths.extend(p for p in (kit / name).rglob('*') if p.is_file())
    paths.extend(p for p in (kit / 'forms').rglob('*') if p.is_file() and p.suffix in ('.py', '.sh'))
    if (kit / 'containers').is_dir():
        paths.extend(p for p in (kit / 'containers').iterdir() if p.is_file())
        # Runtime helpers only; Dockerfiles and staged engine source belong to
        # offline image build contexts, never to a second payload in the form.
        full = kit / 'containers/full'
        if full.is_dir():
            paths.extend(p for p in full.rglob('*.py') if p.is_file())
    # LICENSE and NOTICE are the Apache-2.0 terms for Deep32's own code; every copy of the kit carries both.
    paths.extend(kit / name for name in ('LICENSE', 'NOTICE', 'pins.json', 'build/provenance.py', 'build/thin_forms.py'))
    loader = kit / 'firstboot/installed-inputs.py'
    if loader.is_file() and 'image_binding' in loader.read_text(encoding='ascii'):
        paths.append(kit / 'build/image_binding.py')
    if (kit / 'build/release_profile.py').exists():
        paths.append(kit / 'build/release_profile.py')
    for name in ('release-key.gpg', 'release-key.fingerprint'):
        public = kit / 'release' / name
        if public.exists():
            paths.append(public)
    # Interpreter caches are generated state, never media inputs or runtime identity.
    result = sorted({p for p in paths if '__pycache__' not in p.parts and p.suffix != '.pyc'},
                    key=lambda p: p.relative_to(kit).as_posix())
    for path in result:
        if path.is_symlink() or path.is_junction() or not path.is_file() or '.env' in path.name:
            raise ValueError('unsafe runtime file: ' + path.name)
    return result


def runtime_members(kit, pins=None):
    """Map runtime member names to sources, without shipping ISO seeds in a pack."""
    config = json.loads((kit / 'pins.json' if pins is None else pins).read_text(encoding='ascii'))
    profile = config.get('release_profile', 'iso')
    if profile not in ('iso', 'pack'):
        raise ValueError('unknown release profile')
    result = {}
    for path in runtime_files(kit):
        name = path.relative_to(kit).as_posix()
        if profile == 'pack' and name.startswith('autoinstall/'):
            if name not in ('autoinstall/install-packages.sh', 'autoinstall/render-settings.py'):
                continue
            name = 'forms/' + path.name
        if name in result:
            raise ValueError('duplicate runtime member: ' + name)
        result[name] = pins if name == 'pins.json' and pins is not None else path
    return dict(sorted(result.items()))


def tar_bytes(size):
    return 512 + ((size + 511) // 512) * 512


def kit_size(kit):
    files = list(runtime_members(kit).values())
    return dict(files=len(files), bytes=sum(p.stat().st_size for p in files),
                tar_member_bytes=sum(tar_bytes(p.stat().st_size) for p in files))


def write_size_report(output, limit=LIMIT):
    with tarfile.open(output, 'r:') as archive:
        files = [dict(path=m.name, size=m.size, kind='file' if m.isfile() else 'link' if m.issym() or m.islnk() else 'directory')
                 for m in archive.getmembers()]
    value = dict(format='deep32-thin-form-files/1', bytes=output.stat().st_size,
                 limit_bytes=limit, files=files)
    output.with_suffix('.files.json').write_text(json.dumps(value, sort_keys=True, indent=2) + '\n', encoding='ascii', newline='\n')


def docker(kit, output, epoch, pins=None):
    with tarfile.open(output, 'w', format=tarfile.GNU_FORMAT) as target:
        entries = [('deep32/kit/' + name, path) for name, path in runtime_members(kit, pins).items()]
        entries.append(('deep32/load.sh', kit / 'forms/load-docker.sh'))
        for name, path in sorted(entries):
            entry = tarfile.TarInfo(name)
            entry.size = path.stat().st_size
            entry.mtime = epoch
            entry.mode = runtime_mode(path)
            with path.open('rb') as stream:
                target.addfile(entry, stream)
    if output.stat().st_size >= LIMIT:
        output.unlink()
        raise ValueError('thin Docker form exceeds the 1 GiB limit')
    write_size_report(output)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--kit', type=Path, required=True)
    ap.add_argument('--output', type=Path)
    ap.add_argument('--epoch', type=int, default=0)
    ap.add_argument('--sizes', action='store_true')
    ap.add_argument('--pins', type=Path)
    args = ap.parse_args()
    if args.sizes:
        print(json.dumps(kit_size(args.kit), sort_keys=True))
    elif args.output is None or args.output.exists():
        ap.error('a new --output is required')
    else:
        docker(args.kit, args.output, args.epoch, args.pins)


if __name__ == '__main__':
    main()
