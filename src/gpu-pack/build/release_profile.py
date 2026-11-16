#!/usr/bin/env python3
"""Select the required inputs for the container pack or the later ISO form."""
DEFERRED = 'deferred: v1.1 ISO form'
ISO_PACKAGES = frozenset({'ubuntu-drivers-common', 'kmod'})
ISO_UBUNTU = frozenset({'iso', 'sha256', 'kernel_track', 'kernel_package',
                       'modules_package', 'driver_packages', 'module_version', 'module_signer'})


def release_profile(pins):
    value = pins.get('release_profile', 'iso')
    if value not in ('pack', 'iso'):
        raise ValueError('release_profile must be pack or iso')
    return value


def is_pack(pins):
    return release_profile(pins) == 'pack'


def package_is_iso_only(name):
    name = name.split('=', 1)[0].split(':', 1)[0]
    nvidia_driver = (name.startswith(('nvidia-', 'libnvidia-')) and
                     not name.startswith(('nvidia-container-', 'libnvidia-container')))
    return (name in ISO_PACKAGES or nvidia_driver or 'dkms' in name or
            name.startswith(('linux-', 'cuda-drivers', 'xserver-xorg-video-nvidia-')))


def packages(pins):
    return [value for value in pins['packages']
            if not is_pack(pins) or not package_is_iso_only(value)]


def file_is_iso_only(row):
    return row['path'].startswith('iso/') or row['path'] == 'payload/keyrings/ubuntu-release.gpg'


def files(pins):
    return [row for row in pins['fetch']['files'] if not is_pack(pins) or not file_is_iso_only(row)]


def leaves(value, prefix=''):
    if isinstance(value, dict):
        for key, child in sorted(value.items()):
            yield from leaves(child, (prefix + '.' + key).lstrip('.'))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from leaves(child, prefix + '.' + str(index))
    else:
        yield prefix, value


def deferred_paths(pins):
    """Paths outside the pack's input set; they remain in the pins register."""
    if not is_pack(pins):
        return set()
    prefixes = ['ubuntu.' + name for name in ISO_UBUNTU] + ['fetch.iso_signing_fingerprint']
    prefixes += ['fetch.files.' + str(i) for i, row in enumerate(pins.get('fetch', {}).get('files', [])) if file_is_iso_only(row)]
    prefixes += ['packages.' + str(i) for i, value in enumerate(pins.get('packages', [])) if package_is_iso_only(value)]
    return {path for path, _ in leaves(pins)
            if any(path == prefix or path.startswith(prefix + '.') for prefix in prefixes)}


def unresolved(pins):
    excluded = deferred_paths(pins)
    return [path for path, value in leaves(pins) if path not in excluded and
            isinstance(value, str) and (not value or 'TO-PIN' in value or 'TO-SET' in value or DEFERRED in value)]


def require_package_closure(pins):
    """Refuse a pack repository that could install an ISO kernel or host driver."""
    if is_pack(pins):
        for repo in pins['fetch']['repositories'].values():
            for row in repo['packages']:
                if package_is_iso_only(row['name']):
                    raise ValueError('pack repository contains an ISO-only package: ' + row['name'])
