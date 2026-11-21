#!/usr/bin/env python3
"""Installed payload verification, signed module check and immutable image settings."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True

KIT = Path('/opt/deep32-forge')
PAYLOAD = Path('/opt/deep32-payload')
STATE = Path('/var/lib/deep32-forge')
ETC = Path('/etc/deep32-forge')
MANIFEST = Path('/opt/PAYLOAD.json')
CAPABILITIES = {'7.5': 'turing', '8.0': 'ampere80', '8.6': 'ampere86', '8.7': 'ampere86',
                '8.9': 'ada', '9.0': 'hopper', '10.0': 'blackwell100',
                '12.0': 'blackwell120', '12.1': 'spark121'}


def image_binding():
    spec = importlib.util.spec_from_file_location('installed_image_binding', KIT / 'build/image_binding.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verified_image(config, row, binding):
    """Verify the sealed archive and its independent role pins before Docker."""
    role = row['role']
    if (not re.fullmatch(r'images/[A-Za-z0-9._-]+\.tar', row['path'])
            or not re.fullmatch(r'[0-9a-f]{64}', row.get('sha256', ''))):
        raise ValueError('invalid image archive record')
    archive = PAYLOAD / row['path']
    if archive.is_symlink() or not archive.is_file():
        raise ValueError('image archive must be a regular file')
    measured = hashlib.sha256()
    with archive.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            measured.update(block)
    if measured.hexdigest() != row['sha256']:
        raise ValueError('image archive hash differs: ' + role)
    source = None
    if role.startswith('tei-'):
        source = config['tei']['variants'][role[4:]]['digest']
        if (not isinstance(source, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', source)
                or row.get('source_digest') != source):
            raise ValueError('TEI source digest differs: ' + role)
    else:
        expected = config['runtime'][role + '_image_id']
        if (not isinstance(expected, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', expected)
                or expected not in {row.get('config_digest'), row.get('index_digest')}):
            raise ValueError('application image pin differs: ' + role)
        if role == 'qdrant':
            expected = config['builder']['qdrant']
            source = expected['image'].rsplit('@', 1)[1]
            if (expected['version'] != '1.19.1' or row.get('version') != '1.19.1'
                    or row.get('source_digest') != source):
                raise ValueError('Qdrant source digest or version differs')
            binding.check_archive(archive, expected, source)
    return binding.check_archive(archive, row, source)


def pins():
    return json.loads((KIT / 'pins.json').read_text(encoding='ascii'))


def write_state(path, value):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temporary = path.with_suffix('.new')
    temporary.write_text(json.dumps(value, sort_keys=True) + '\n', encoding='ascii')
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def read_state(path):
    try:
        return json.loads(path.read_text(encoding='ascii'))
    except (OSError, ValueError):
        return None


def payload_identity():
    # Installed payload is immutable. Detect a replacement by its small inventory
    # and pins; do not rehash tens of GB on every service restart.
    value = hashlib.sha256()
    spec = importlib.util.spec_from_file_location('installed_runtime', KIT / 'build/thin_forms.py')
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    paths = (MANIFEST, *runtime.runtime_files(KIT))
    for path in paths:
        value.update(path.read_bytes())
    return value.hexdigest()


def verify_payload(config, cached=False):
    identity = payload_identity()
    marker = STATE / 'payload-verified.json'
    if cached and read_state(marker) == {'identity': identity}:
        return identity
    spec = importlib.util.spec_from_file_location('provenance', KIT / 'build/provenance.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.unresolved(config):
        raise ValueError('unresolved input pin')
    module.verify_manifest(PAYLOAD, MANIFEST)
    if cached:
        write_state(marker, {'identity': identity})
    return identity


def nvidia_display_present(devices=Path('/sys/bus/pci/devices')):
    # PCI vendor 0x10de with a display or 3D controller class (0x03xxxx).
    # If the bus cannot be read, answer True so a failed query still stops.
    try:
        for device in sorted(devices.iterdir()):
            vendor = (device / 'vendor').read_text(encoding='ascii').strip()
            if vendor == '0x10de' and (device / 'class').read_text(encoding='ascii').strip().startswith('0x03'):
                return True
    except OSError:
        return True
    return False


def detect_tei(form, override=None):
    executable = '/usr/lib/wsl/lib/nvidia-smi' if form == 'wsl' else shutil.which('nvidia-smi')
    if executable is None or (form == 'wsl' and not Path(executable).is_file()):
        classes = {'cpu'}
    else:
        result = subprocess.run([executable, '--query-gpu=compute_cap', '--format=csv,noheader'],
                                capture_output=True, text=True)
        if result.returncode:
            # The ISO always installs the driver user space. On a machine with no NVIDIA
            # card the module does not load and the query fails; that is processor mode.
            # A failed query with an NVIDIA card present (or an unreadable bus) still stops.
            no_card = form != 'wsl' and not nvidia_display_present()
            if 'no devices were found' not in (result.stdout + result.stderr).lower() and not no_card:
                raise ValueError('GPU capability query failed')
            classes = {'cpu'}
        else:
            caps = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if any(cap not in CAPABILITIES for cap in caps):
                raise ValueError('GPU capability below 7.5 or unknown')
            classes = {CAPABILITIES[cap] for cap in caps} or {'cpu'}
    if override is not None:
        if override not in set(CAPABILITIES.values()) | {'cpu'}:
            raise ValueError('invalid TEI operator override')
        return override
    if len(classes) != 1:
        raise ValueError('different GPU classes require an explicit reviewed TEI override')
    return classes.pop()


def compose_for_class(compose, selection):
    """Apply the detected processor mode to every service, including converter.

    The conversion profile uses the same installed file as first boot. A CPU
    Forge can run its pymupdf and production backends without a GPU reservation;
    convert-job.sh keeps MinerU's separate processor-mode refusal.
    """
    if selection not in set(CAPABILITIES.values()) | {'cpu'}:
        raise ValueError('invalid TEI class for compose')
    if selection == 'cpu':
        compose = compose.replace('    gpus: all\n', '')
        if re.search(r'^\s+gpus:', compose, flags=re.MULTILINE):
            raise ValueError('unrecognised GPU reservation in processor compose')
    return compose


def load_images(config, status, form, identity):
    try:
        selection = detect_tei(form, config['tei'].get('selected'))
    except ValueError:
        status.update('firstboot', 'failed', error='forge-gpu-class-unsupported',
                      msg='Cannot select TEI: unsupported GPU capability or query failure')
        raise
    if selection not in config['tei']['variants']:
        raise ValueError('selected TEI class is absent from the disc')
    status.update('firstboot', 'running', msg='TEI class ' + selection +
                  (' (operator override)' if config['tei'].get('selected') is not None else ' (detected)'))
    rows = json.loads((PAYLOAD / 'images/index.json').read_text(encoding='ascii'))
    required = {'tei-' + selection, 'reranker', 'converter', 'door'}
    if 'builder' in config:
        required.update(('builder', 'qdrant'))
    selected = [row for row in rows if row['role'] in required]
    if len(selected) != len(required) or {row['role'] for row in selected} != required:
        raise ValueError('missing or duplicate selected image')
    marker = STATE / 'images-loaded.json'
    expected = {'identity': identity, 'selection': selection}
    reuse = read_state(marker) == expected and (ETC / 'images.conf').is_file() and (ETC / 'compose.yaml').is_file()
    binding = image_binding()
    # Verify every selected archive before the first Docker load.
    checked = [(row, verified_image(config, row, binding)) for row in selected]
    chosen = {}
    for row, entry in checked:
        actual = None
        if reuse:
            try:
                actual = binding.resolve_loaded(entry)
            except (ValueError, subprocess.CalledProcessError):
                pass
        if actual is None:
            from status import command
            status.update('firstboot', 'waiting', msg='Loading verified image ' + row['role']
                          + '; restore the same verified media and resume if it fails')
            command(['docker', 'image', 'load', '--input', str(PAYLOAD / row['path'])], status, 600)
            actual = binding.resolve_loaded(entry)
        chosen[row['role']] = actual
    values = {'TEI_CLASS': selection, 'TEI_IMAGE': chosen['tei-' + selection], 'RERANKER_IMAGE': chosen['reranker'],
              'CONVERTER_IMAGE': chosen['converter'], 'DOOR_IMAGE': chosen['door'],
              'TEI_REVISION': config['models']['tei']['revision'],
              'RERANKER_DTYPE': config['runtime']['reranker_dtype'], 'RERANKER_DEVICE': 'cuda'}
    if 'builder' in config:
        values.update(BUILDER_IMAGE=chosen['builder'], QDRANT_IMAGE=chosen['qdrant'])
    if values['RERANKER_DTYPE'] not in {'float16', 'float32'}:
        raise ValueError('invalid explicit reranker dtype')
    if selection == 'cpu':
        values.update(RERANKER_DTYPE='float32', RERANKER_DEVICE='cpu')
    ETC.mkdir(parents=True, exist_ok=True)
    (ETC / 'images.conf').write_text(''.join(k + '=' + v + '\n' for k, v in sorted(values.items())), encoding='ascii')
    compose = compose_for_class((KIT / 'containers/compose.yaml').read_text(encoding='ascii'), selection)
    (ETC / 'compose.yaml').write_text(compose, encoding='ascii', newline='\n')
    write_state(marker, expected)
    return selection


def signed_module(config, modules=Path('/lib/modules')):
    """Check the newest target kernel with a module; curtin runs another kernel."""
    ubuntu = config['ubuntu']
    request = ubuntu['modules_package'].split('=', 1)
    branch = re.fullmatch(r'linux-modules-nvidia-(\d+)-server-(?:generic|\d+\.\d+\.\d+-\d+-generic)', request[0])
    observed = []
    kernels = {}
    for path in sorted(modules.glob('*/**/nvidia.ko*')):
        if not path.is_file():
            continue
        relative = path.relative_to(modules)
        row = {'file': '/lib/modules/' + relative.as_posix()}
        for field in ('signer', 'version'):
            reply = subprocess.run(['modinfo', '-F', field, str(path)], capture_output=True, text=True)
            row[field] = reply.stdout.strip() if reply.returncode == 0 else ''
        observed.append(row)
        # An unsupported compressed module still makes this a target kernel;
        # selection below refuses it instead of falling back to an older ABI.
        if path.name in {'nvidia.ko', 'nvidia.ko.zst', 'nvidia.ko.xz', 'nvidia.ko.gz'}:
            kernels.setdefault(relative.parts[0], []).append(row)

    def refuse(reason):
        raise ValueError(reason + '; do not substitute DKMS; matches: ' +
                         json.dumps(observed, sort_keys=True, ensure_ascii=True))

    if branch is None or len(request) != 2:
        refuse('invalid pinned Ubuntu server module package')
    if not kernels:
        refuse('No Ubuntu signed NVIDIA module found')
    # Compare numeric ABI components, so ABI 149 sorts after ABI 99.
    def kernel_key(value):
        return tuple((1, int(part)) if part.isdigit() else (0, part)
                     for part in re.split(r'(\d+)', value))
    kernel = max(kernels, key=kernel_key)
    if re.fullmatch(r'\d+\.\d+\.\d+-\d+-generic', kernel) is None:
        refuse('target kernel is not a pinned generic ABI')
    package = 'linux-modules-nvidia-' + branch[1] + '-server-' + kernel
    if request[0] not in {package, 'linux-modules-nvidia-' + branch[1] + '-server-generic'}:
        refuse('pinned module package does not match target kernel ' + kernel)
    versions = [row['version'] for name, repo in config.get('fetch', {}).get('repositories', {}).items()
                if name in {'ubuntu-noble', 'ubuntu-updates', 'ubuntu-security'}
                for row in repo['packages'] if row['name'] == package]
    if request[0] == package:
        versions.append(request[1])
    if not versions or len(set(versions)) != 1 or not versions[0] or 'TO-' in versions[0]:
        refuse('missing or conflicting pinned package version for ' + package)
    directory = '/lib/modules/' + kernel + '/kernel/nvidia-' + branch[1] + 'srv/'
    selected = [row for row in kernels[kernel]
                if row['file'] in {directory + 'nvidia.ko', directory + 'nvidia.ko.zst'}]
    if len(selected) != 1:
        refuse('expected exactly one Ubuntu server module for ' + kernel)
    selected = selected[0]
    if (not ubuntu['module_version'] or 'TO-' in ubuntu['module_version'] or
            selected['version'] != ubuntu['module_version'] or
            ubuntu['module_signer'] != 'Canonical Ltd. Kernel Module Signing' or
            selected['signer'] != ubuntu['module_signer']):
        refuse('module version or archive signer differs')
    installed = subprocess.run(['dpkg-query', '-W', '-f', '${Status} ${Version}', package],
                               capture_output=True, text=True)
    if installed.returncode or installed.stdout.strip() != 'install ok installed ' + versions[0]:
        refuse('installed package status or version differs for ' + package)
    return selected


def main():
    action = sys.argv[1]
    config = pins()
    if action == 'verify':
        verify_payload(config)
    elif action == 'packages':
        items = list(config['packages'])
        if config.get('release_profile', 'iso') == 'pack':
            items = [value for value in items if value.split('=', 1)[0] not in {'ubuntu-drivers-common', 'kmod'}
                     and 'dkms' not in value.split('=', 1)[0]
                     and not value.startswith(('linux-', 'cuda-drivers', 'xserver-xorg-video-nvidia-'))
                     and (not value.startswith(('nvidia-', 'libnvidia-'))
                          or value.startswith(('nvidia-container-', 'libnvidia-container')))]
        if sys.argv[2] == 'iso':
            items += [config['ubuntu']['kernel_package'], config['ubuntu']['modules_package']] + config['ubuntu']['driver_packages']
        print('\n'.join(items))
    elif action == 'signed-module':
        signed_module(config)
    elif action == 'load-images':
        from status import Status
        load_images(config, Status(), (ETC / 'form').read_text(encoding='ascii').strip(), verify_payload(config, cached=True))
    else:
        raise ValueError('unknown installed-input operation')


if __name__ == '__main__':
    main()
