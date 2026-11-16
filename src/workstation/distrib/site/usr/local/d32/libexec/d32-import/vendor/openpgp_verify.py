"""Verify one detached binary OpenPGP signature with a pinned primary key."""
from __future__ import annotations
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile

def find_gpgv(path_env, candidates, is_file):
    for candidate in candidates:
        if '/' in candidate or '\\' in candidate:
            if is_file(candidate):
                return [candidate]
        else:
            for folder in path_env.split(os.pathsep):
                if not folder:
                    continue
                for suffix in (('', '.exe') if os.name == 'nt' else ('',)):
                    name = os.path.join(folder, candidate + suffix)
                    if is_file(name):
                        return [name]
    return None

def good_status(text, fingerprint):
    rows = [line[len('[GNUPG:] '):].split() for line in text.splitlines() if line.startswith('[GNUPG:] ')]
    names = [row[0] for row in rows if row]
    forbidden = {'BADSIG', 'ERRSIG', 'EXPSIG', 'EXPKEYSIG', 'REVKEYSIG', 'NO_PUBKEY'}
    valid = [r[1:] for r in rows if r and r[0] == 'VALIDSIG']
    return (names.count('GOODSIG') == 1 and len(valid) == 1 and not forbidden.intersection(names) and
            len(valid[0]) == 10 and valid[0][8] == '00' and valid[0][-1].upper() == fingerprint.upper())

class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('arguments')

def main(argv=None):
    p = Parser()
    p.add_argument('--keyring', required=True)
    p.add_argument('--fingerprint', required=True)
    p.add_argument('--gpgv-argv')
    p.add_argument('--tmp-dir')
    p.add_argument('bundle_json')
    p.add_argument('sig')
    try:
        a = p.parse_args(argv)
        if not re.fullmatch('[0-9a-fA-F]{40}', a.fingerprint):
            return 1
        command = json.loads(a.gpgv_argv) if a.gpgv_argv else find_gpgv(os.environ.get('PATH', ''), ['gpgv', 'C:/Program Files/Git/usr/bin/gpgv.exe', 'C:/Program Files/GnuPG/bin/gpgv.exe', 'C:/Program Files (x86)/GnuPG/bin/gpgv.exe'], os.path.isfile)
        if command is not None and (not isinstance(command, list) or not command or not all(isinstance(v, str) and v and '\0' not in v for v in command)):
            return 1
    except (ValueError, TypeError):
        return 1
    if not all(Path(v).is_file() for v in (a.keyring, a.bundle_json, a.sig)):
        return 2
    if command is None:
        return 3
    try:
        with tempfile.TemporaryDirectory(prefix='bundle-verify-', dir=a.tmp_dir) as name:
            directory = Path(name)
            (directory / 'home').mkdir()
            for src, dest in [(a.keyring, 'keyring.gpg'), (a.bundle_json, 'data.json'), (a.sig, 'data.json.sig')]:
                shutil.copyfile(src, directory / dest)
            try:
                result = subprocess.run(command + ['--homedir', './home', '--keyring', './keyring.gpg', '--status-fd', '1', './data.json.sig', './data.json'], cwd=directory, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, shell=False, env=os.environ.copy())
            except subprocess.TimeoutExpired:
                return 4
            except OSError:
                return 3
            return 0 if result.returncode == 0 and good_status(result.stdout.decode('ascii', 'replace'), a.fingerprint) else 4
    except OSError:
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
