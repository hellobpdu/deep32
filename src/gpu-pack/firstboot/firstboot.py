#!/usr/bin/env python3
"""First boot: fresh identities, C6 status, human sign-in and S7 pairing adapter.
Usage: firstboot.py [--dry-run]. Exit 0 ready; 1 stopped safely. Run on the new node only.
"""
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import subprocess
import time
import pwd
import urllib.request

from status import Status

KIT = Path('/opt/deep32-forge')
STATE = Path('/var/lib/deep32-forge')
HELPER = Path('/opt/deep32-payload/bin/pairing-helper')
COMPOSE = ['docker', 'compose', '--project-name', 'deep32-forge', '--env-file', '/etc/deep32-forge/images.conf',
           '-f', '/etc/deep32-forge/compose.yaml']


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def settings_renderer(config):
    return KIT / ('forms/render-settings.py' if config.get('release_profile', 'iso') == 'pack'
                  else 'autoinstall/render-settings.py')


def settings(status, form):
    path = Path('/etc/deep32-forge/settings.json')
    while not path.is_file():
        status.update('firstboot', 'waiting', msg='Supply C7 settings on the installed node')
        for _ in range(6):
            time.sleep(10)
            status.repeat()
            if path.is_file():
                break
    config = json.loads((KIT / 'pins.json').read_text(encoding='ascii'))
    spec = importlib.util.spec_from_file_location('renderer', settings_renderer(config))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate(json.loads(path.read_text(encoding='ascii')), bare_metal=(form == 'iso'))


def make_identities():
    private = STATE / 'private'
    private.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(STATE, 0o700)
    os.chmod(private, 0o700)
    token = private / 'forge-token'
    if not token.exists():
        fd = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='ascii') as stream:
            stream.write(secrets.token_hex(32) + '\n')
    key = private / 'kb-private.pem'
    public = private / 'kb-public.pem'
    if not key.exists():
        temporary = private / 'kb-private.new'
        run('openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(temporary), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        temporary.replace(key)
    if not public.exists():
        run('openssl', 'pkey', '-in', str(key), '-pubout', '-out', str(public), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for path in (token, key, public):
        os.chmod(path, 0o600)
    return token, public


def local_account(status, form, data):
    if form == 'docker':
        return
    user = data['user_name']
    try:
        pwd.getpwnam(user)
    except KeyError:
        run('useradd', '--create-home', '--shell', '/bin/bash', '--groups', 'adm,sudo', user)
    if form == 'wsl':
        Path('/etc/wsl.conf').write_text('[boot]\nsystemd=true\n[user]\ndefault=' + user + '\n', encoding='ascii')
    marker = STATE / 'console-account-set'
    if form == 'iso' and not marker.exists():
        status.update('firstboot', 'waiting', msg='Set the local account password on this console')
        # Password input stays on the console. It never passes through C6 or a settings file.
        with open('/dev/console', 'r+b', buffering=0) as console:
            process = subprocess.Popen(['passwd', user], stdin=console, stdout=console, stderr=console)
            while process.poll() is None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    status.repeat()
            if process.returncode:
                raise RuntimeError('console account setup failed')
        marker.write_text('set\n', encoding='ascii')


def signed_in():
    value = subprocess.run(['tailscale', 'status', '--json'], capture_output=True, text=True)
    if value.returncode:
        return False
    return json.loads(value.stdout).get('BackendState') == 'Running'


def docker_host_name(status):
    # The Docker form observes the host. It never starts, signs in, renames or
    # changes DNS settings on the host's Tailscale node.
    status.update('signin', 'waiting', msg='Sign in to Tailscale on the Docker host')
    while True:
        result = subprocess.run(['tailscale', 'status', '--json'], capture_output=True, text=True)
        try:
            value = json.loads(result.stdout) if result.returncode == 0 else {}
        except ValueError:
            value = {}
        if value.get('BackendState') == 'Running':
            name = value.get('Self', {}).get('HostName', '')
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', name):
                raise ValueError('host status lacks a valid name')
            return name
        time.sleep(10)
        status.repeat()


def signin(status, form, data):
    if data.get('transport', 'tailnet') == 'loopback':
        return data['node_name']
    if form == 'docker':
        name = docker_host_name(status)
        status.update('tailnet', 'done', name=name)
        return name
    if form == 'wsl':
        # WSL never starts its own node. The reviewed S7 adapter talks to the Windows host.
        status.update('signin', 'waiting', msg='Sign in on the Windows host through the installer')
        process = subprocess.Popen([str(HELPER), 'host-signin'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while process.poll() is None:
            time.sleep(10)
            status.repeat()
        if process.returncode:
            raise RuntimeError('host sign-in failed')
        status.update('tailnet', 'done', name=data['node_name'])
        return data['node_name']
    run('systemctl', 'start', 'tailscaled.service', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not signed_in():
        process = subprocess.Popen(['tailscale', 'up', '--hostname=' + data['node_name'], '--accept-dns=false'],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=False)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        buffered = b''
        status.update('signin', 'waiting', msg='Waiting for the Tailscale sign-in address')
        try:
            while not signed_in():
                for key, _ in selector.select(timeout=10):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    buffered = (buffered + chunk)[-8192:]
                    match = re.search(rb'https://login\.tailscale\.com/[A-Za-z0-9/_?=.-]+', buffered)
                    if match:
                        status.update('signin', 'waiting', signin=match[0].decode('ascii'))
                if process.poll() not in (None, 0):
                    raise RuntimeError('Tailscale sign-in failed')
                status.repeat()
        finally:
            selector.close()
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            process.stdout.close()
    status.update('tailnet', 'done', name=data['node_name'])
    return data['node_name']


def pair(status, form, data, token, public):
    if (STATE / 'paired').is_file():
        return
    # The helper is part of S7, verified in the payload. No substitute cryptography is supplied here.
    # It implements SPAKE2, TLS identity binding, one attempt, expiry and persistent lockout.
    code = base64.b32encode(secrets.token_bytes(7)).decode('ascii')[:10]
    status.update('pairing', 'waiting', pair=code, pair_with='workstation', expires=300)
    request = {'code': code, 'expires_seconds': 300, 'attempts': 1, 'form': form,
               'transport': data.get('transport', 'tailnet'), 'publish': data.get('publish', 'serve-https'),
               'token_path': str(token), 'kb_public_path': str(public), 'state_dir': str(STATE / 'pairing')}
    process = subprocess.Popen([str(HELPER), 'pair-forge'], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    process.stdin.write((json.dumps(request) + '\n').encode('ascii'))
    process.stdin.close()
    deadline = time.monotonic() + 300
    try:
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(min(10, max(0, deadline - time.monotonic())))
            remaining = max(0, int(deadline - time.monotonic()))
            if remaining:
                status.update('pairing', 'waiting', pair=code, pair_with='workstation', expires=remaining)
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        if process.returncode != 0:
            raise RuntimeError('pairing expired or failed; helper enforces lockout')
        # The trusted helper returns zero only after proof, durable peer pins and token handover.
        (STATE / 'paired').write_text('paired\n', encoding='ascii')
    finally:
        status.update('pairing', 'done' if process.returncode == 0 else 'failed')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if args.dry_run:
        print('Would verify inputs on first boot or payload change; detect the TEI card class; load images on first boot, payload/card change or missing image; wait for C7 settings; make fresh local identities; print C6; request human sign-in (Docker waits for host sign-in without changing it); run S7 pairing and check authenticated models. Downloads: none. No keys or tokens made in dry run.')
        return
    os.umask(0o077)
    status = Status()
    status.update('firstboot', 'running')
    try:
        form = Path('/etc/deep32-forge/form').read_text(encoding='ascii').strip()
        if form not in {'iso', 'wsl', 'docker'}:
            raise ValueError('invalid installed form')
        spec = importlib.util.spec_from_file_location('installed_inputs', KIT / 'firstboot/installed-inputs.py')
        inputs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inputs)
        config = inputs.pins()
        identity = inputs.verify_payload(config, cached=True)
        data = settings(status, form)
        status.update('keys', 'running')
        token, public = make_identities()
        run('systemctl', 'start', 'docker.service', stdout=subprocess.DEVNULL)
        inputs.load_images(config, status, form, identity)
        if form == 'docker':
            subprocess.run(COMPOSE + ['up', '-d', '--pull', 'never', '--no-build', 'status'], check=True, stdout=subprocess.DEVNULL)
        status.update('keys', 'done')
        local_account(status, form, data)
        display_name = signin(status, form, data)
        pair(status, form, data, token, public)
        subprocess.run(COMPOSE + ['up', '-d', '--pull', 'never', '--no-build', '--wait', 'embeddings', 'reranker', 'door'], check=True, stdout=subprocess.DEVNULL)
        for port in (8081, 8082):
            deadline = time.monotonic() + 600
            while True:
                try:
                    request = urllib.request.Request('http://127.0.0.1:' + str(port) + '/health', headers={'Authorization': 'Bearer ' + token.read_text().strip()})
                    with urllib.request.urlopen(request, timeout=30) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError('model health failed')
                time.sleep(10)
                status.repeat()
        status.update('ready', 'done', name=display_name)
    except Exception:
        # C10 identifiers cross the C6 channel. Never log exception text or helper output.
        # Keep a more specific failed line already written.
        if not (status.state.get('state') == 'failed' and 'error' in status.state):
            status.update('firstboot', 'failed', error='forge-firstboot-failed', msg='Inspect local input pins and the build-day checks')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
