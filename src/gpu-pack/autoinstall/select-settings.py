#!/usr/bin/env python3
"""Select the C7 optical disc. Fail closed before storage changes; no network."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile


def main():
    devices = subprocess.check_output(['blkid', '-t', 'LABEL=CIDATA', '-o', 'device'], text=True).splitlines()
    if len(devices) != 1:
        raise ValueError('Attach exactly one C7 CIDATA settings disc before installing')
    with tempfile.TemporaryDirectory(prefix='deep32-settings-') as directory:
        subprocess.run(['mount', '-o', 'ro,nosuid,nodev,noexec', devices[0], directory], check=True)
        try:
            root = Path(directory)
            if {p.name for p in root.iterdir()} != {'settings.json', 'user-data', 'meta-data'}:
                raise ValueError('C7 disc must hold only settings.json, user-data and meta-data')
            spec = importlib.util.spec_from_file_location('renderer', Path(__file__).with_name('render-settings.py'))
            renderer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(renderer)
            settings = renderer.validate(json.loads((root / 'settings.json').read_text(encoding='ascii')), bare_metal=True)
            seed = (root / 'user-data').read_text(encoding='ascii')
            # Exact renderer output only. Arbitrary cloud-init commands on a settings disc are refused.
            template = Path(__file__).with_name('user-data').read_text(encoding='ascii')
            import re
            serial_match = re.search(r'^        serial: ("[A-Za-z0-9._:+-]{1,128}")$', seed, re.MULTILINE)
            if not serial_match:
                raise ValueError('missing exact target disk serial')
            substitutions = {'KEYBOARD': settings['keyboard'], 'TIMEZONE': settings['time_zone'],
                             'NODE': settings['node_name'], 'USER': settings['user_name'],
                             'DISK-SERIAL': json.loads(serial_match[1])}
            for key, value in substitutions.items():
                template = template.replace('TO-SET-' + key, json.dumps(value))
            if seed != template or (root / 'meta-data').read_bytes() != Path(__file__).with_name('meta-data').read_bytes():
                raise ValueError('settings seed differs from the approved renderer')
            Path('/run/deep32-settings.json').write_text(json.dumps(settings, sort_keys=True) + '\n', encoding='ascii')
            Path('/autoinstall.yaml').write_text(seed, encoding='ascii')
        finally:
            subprocess.run(['umount', directory], check=True)


if __name__ == '__main__':
    main()
