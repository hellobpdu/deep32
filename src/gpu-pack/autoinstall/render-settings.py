#!/usr/bin/env python3
"""Render secret-free C7 autoinstall seed. Exit 0 success, 2 invalid input.
Usage: render-settings.py settings.json --disk-serial SERIAL --output seed
The target disk is a separate explicit build-day choice, never guessed.
"""
import argparse
import json
from pathlib import Path
import re
import stat
import os

REFUSAL = 'Supply valid non-secret C7 settings and run the same loader again.'
# C1 matrix v0.3.1; new profiles require the contract and this reader to change together.
PROFILES = {'win-nvidia', 'proxmox-passthrough', 'linux-nvidia', 'no-gpu', 'two-machines',
            'headless', 'baremetal', 'headend-only', 'win-home', 'virtualbox', 'vmware', 'esxi'}

ALLOWED = {'format', 'format_version', 'role', 'node_name', 'user_name', 'time_zone', 'keyboard', 'profile',
           'hw_profile', 'size', 'disk_encryption', 'transport', 'publish', 'features', 'written_by',
           'forge_ports', 'forge_accept_ct'}

FORGE_PORTS = {'embeddings': 8081, 'reranker': 8082, 'pairing': 7453,
               'embeddings_https': 443, 'reranker_https': 8443, 'pairing_tcp': 7453}


def forge_ports(data):
    supplied = data.get('forge_ports', {})
    if (not isinstance(supplied, dict) or set(supplied) - set(FORGE_PORTS)
            or any(type(value) is not int or not 1 <= value <= 65535 for value in supplied.values())):
        raise ValueError(REFUSAL)
    ports = dict(FORGE_PORTS, **supplied)
    for keys in (('embeddings', 'reranker', 'pairing'),
                 ('embeddings_https', 'reranker_https', 'pairing_tcp')):
        if len({ports[key] for key in keys}) != len(keys):
            raise ValueError(REFUSAL)
    return ports


def secret_shaped(value):
    if isinstance(value, dict):
        return any(secret_shaped(item) for item in value.values())
    if isinstance(value, list):
        return any(secret_shaped(item) for item in value)
    if not isinstance(value, str):
        return False
    return bool(re.search(r'(?i)(?:https?://|tskey-|bearer\s|-----BEGIN|password\s*[:=]|token\s*[:=]|'
                          r'eyJ[A-Za-z0-9_-]+\.|[a-f0-9]{32,}|[A-Za-z0-9+/=_-]{40,})', value)
                or re.fullmatch(r'[A-Z2-7]{10}', value))


def _validate(data, bare_metal=False):
    if not isinstance(data, dict) or secret_shaped(data):
        raise ValueError(REFUSAL)
    if set(data) - ALLOWED or data.get('format') != 'deep32-settings' or type(data.get('format_version')) is not int or data['format_version'] != 1 or data.get('role') != 'forge':
        raise ValueError('unknown C7 field, format or role')
    for field, pattern in [('node_name', r'[a-z][a-z0-9-]{0,62}'), ('user_name', r'[a-z_][a-z0-9_-]{0,30}'),
                           ('time_zone', r'[A-Za-z_]+(?:/[A-Za-z_+-]+)*'), ('keyboard', r'[a-z0-9_-]{1,32}'),
                           ('profile', r'[a-z][a-z0-9-]{0,62}')]:
        if not isinstance(data.get(field), str) or not re.fullmatch(pattern, data[field]):
            raise ValueError('missing or invalid ' + field)
    if data['user_name'] == 'root' or data.get('disk_encryption', False) is not False:
        raise ValueError('root account and encrypted unattended install are unsupported; use console provisioning')
    if data['profile'] not in PROFILES:
        raise ValueError(REFUSAL)
    forge_ports(data)
    if 'forge_accept_ct' in data and type(data['forge_accept_ct']) is not bool:
        raise ValueError(REFUSAL)
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


def validate(data, bare_metal=False):
    """Shared C7 reader: never reflect a field name or supplied value in errors."""
    try:
        return _validate(data, bare_metal)
    except (ValueError, TypeError, KeyError):
        raise ValueError(REFUSAL) from None


def load(path, bare_metal=False, installed=True):
    """Read bounded ASCII JSON without duplicate keys, links or shared credentials."""
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError(REFUSAL)
            result[key] = value
        return result
    try:
        path = Path(path)
        for part in (path, *path.parents):
            if part.is_symlink() or part.is_junction():
                raise ValueError(REFUSAL)
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536
                    or installed and os.name == 'posix' and (info.st_uid != 0 or info.st_mode & 0o077)
                    or getattr(info, 'st_file_attributes', 0) & 0x400):
                raise ValueError(REFUSAL)
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError(REFUSAL)
        return validate(json.loads(raw.decode('ascii'), object_pairs_hook=pairs), bare_metal)
    except (OSError, ValueError, TypeError, UnicodeError):
        raise ValueError(REFUSAL) from None


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
        # Input seed choices need not yet have installed-node ownership/modes.
        data = load(args.settings, bare_metal=True, installed=False)
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
