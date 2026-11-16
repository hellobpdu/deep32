#!/usr/bin/env python3
"""Render secret-free C7 autoinstall seed. Exit 0 success, 2 invalid input.
Usage: render-settings.py settings.json --disk-serial SERIAL --output seed
The target disk is a separate explicit build-day choice, never guessed.
"""
import argparse
import json
from pathlib import Path
import re

ALLOWED = {'format', 'format_version', 'role', 'node_name', 'user_name', 'time_zone', 'keyboard', 'profile',
           'hw_profile', 'size', 'disk_encryption', 'transport', 'publish', 'features', 'written_by'}


def validate(data, bare_metal=False):
    if set(data) - ALLOWED or data.get('format') != 'deep32-settings' or type(data.get('format_version')) is not int or data['format_version'] != 1 or data.get('role') != 'forge':
        raise ValueError('unknown C7 field, format or role')
    for field, pattern in [('node_name', r'[a-z][a-z0-9-]{0,62}'), ('user_name', r'[a-z_][a-z0-9_-]{0,30}'),
                           ('time_zone', r'[A-Za-z_]+(?:/[A-Za-z_+-]+)*'), ('keyboard', r'[a-z0-9_-]{1,32}'),
                           ('profile', r'[a-z][a-z0-9-]{0,62}')]:
        if not isinstance(data.get(field), str) or not re.fullmatch(pattern, data[field]):
            raise ValueError('missing or invalid ' + field)
    if data['user_name'] == 'root' or data.get('disk_encryption', False) is not False:
        raise ValueError('root account and encrypted unattended install are unsupported; use console provisioning')
    if bare_metal and data.get('disk_encryption') is not False:
        raise ValueError('C7 absent disk_encryption means on; explicitly set false or use console provisioning')
    for key, choices in [('transport', {'tailnet', 'loopback'}), ('publish', {'serve-https', 'tailnet-plain'}), ('hw_profile', {'vm', 'latitude-5430'})]:
        if key in data and data[key] not in choices:
            raise ValueError('invalid ' + key)
    if 'size' in data and (not isinstance(data['size'], dict) or set(data['size']) != {'threads', 'mem_gb', 'disk_gb'} or
                           any(type(v) is not int or v <= 0 for v in data['size'].values())):
        raise ValueError('invalid size')
    if 'features' in data and (not isinstance(data['features'], dict) or set(data['features']) - {'web_switch'} or
                               any(type(v) is not bool for v in data['features'].values())):
        raise ValueError('invalid features')
    if 'written_by' in data and (not isinstance(data['written_by'], str) or not re.fullmatch(r'[A-Za-z0-9._ /-]{1,80}', data['written_by'])):
        raise ValueError('invalid written_by')
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('settings', type=Path)
    ap.add_argument('--disk-serial', required=True)
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if args.dry_run:
        print('Would validate C7 settings and explicit disk serial; render locked account and NoCloud seed; downloads: none')
        return
    try:
        data = validate(json.loads(args.settings.read_text(encoding='ascii')), bare_metal=True)
        if not re.fullmatch(r'[A-Za-z0-9._:+-]{1,128}', args.disk_serial):
            raise ValueError('disk serial must be an exact literal, without wildcard characters')
        text = Path(__file__).with_name('user-data').read_text(encoding='ascii')
        for key, value in {'KEYBOARD': data['keyboard'], 'TIMEZONE': data['time_zone'], 'NODE': data['node_name'],
                           'USER': data['user_name'], 'DISK-SERIAL': args.disk_serial}.items():
            text = text.replace('TO-SET-' + key, json.dumps(value))
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / 'user-data').write_text(text, encoding='ascii', newline='\n')
        (args.output / 'meta-data').write_bytes(Path(__file__).with_name('meta-data').read_bytes())
        (args.output / 'settings.json').write_text(json.dumps(data, sort_keys=True, indent=2) + '\n', encoding='ascii', newline='\n')
    except (OSError, ValueError, TypeError) as exc:
        ap.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
