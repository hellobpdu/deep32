#!/usr/bin/env python3
"""First boot: validated settings, preserved identities, C6 and S7 pairing.
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
import stat
import subprocess
import sys
sys.dont_write_bytecode = True
import time
import pwd
import urllib.request
import urllib.error
import contextlib

from status import Status, command

KIT = Path('/opt/deep32-forge')
STATE = Path('/var/lib/deep32-forge')
HELPER = KIT / 'pairing/bin/pairing-helper'
COMPOSE = ['docker', 'compose', '--project-name', 'deep32-forge', '--env-file', '/etc/deep32-forge/images.conf',
           '-f', '/etc/deep32-forge/compose.yaml']


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def settings_renderer(config):
    return KIT / ('forms/render-settings.py' if config.get('release_profile', 'iso') == 'pack'
                  else 'autoinstall/render-settings.py')


class StageError(RuntimeError):
    def __init__(self, reason, message):
        self.reason = reason
        super().__init__(message)


def settings(status, form, path=Path('/etc/deep32-forge/settings.json')):
    config = json.loads((KIT / 'pins.json').read_text(encoding='ascii'))
    spec = importlib.util.spec_from_file_location('renderer', settings_renderer(config))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return module.load(path, bare_metal=(form == 'iso'))
    except ValueError:
        raise StageError('forge-settings-invalid', module.REFUSAL) from None


def wait_process(process, status, seconds, reason, message):
    deadline = time.monotonic() + seconds
    try:
        while process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StageError(reason, message)
            try:
                process.wait(timeout=min(10, remaining))
            except subprocess.TimeoutExpired:
                status.repeat()
        if process.returncode:
            raise StageError(reason, message)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def service_command(argv, status, seconds, reason, message):
    process = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_process(process, status, seconds, reason, message)


def make_identities():
    private = STATE / 'private'
    token = private / 'forge-token'
    key = private / 'kb-private.pem'
    public = private / 'kb-public.pem'
    for path in (STATE, private, token, key, public, private / 'kb-private.new',
                 private / 'forge-token.new', private / 'kb-public.new'):
        for part in (path, *path.parents):
            if part.is_symlink() or part.is_junction():
                raise StageError('forge-identity-unsafe', 'Restore the private identity files and resume the same installation.')
        if path.exists():
            info = path.stat()
            if (not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                    or stat.S_ISREG(info.st_mode) and info.st_nlink != 1
                    or os.name == 'posix' and (info.st_uid != os.geteuid() or info.st_mode & 0o077)):
                raise StageError('forge-identity-unsafe', 'Restore the private identity files and resume the same installation.')
    if public.exists() and not key.exists() or not token.exists() and (key.exists() or public.exists() or (STATE / 'pairing').exists()):
        raise StageError('forge-identity-incomplete', 'Restore the private signing key and resume the same installation.')
    if token.exists() and not re.fullmatch(r'[0-9a-f]{64}\n?', token.read_text(encoding='ascii')):
        raise StageError('forge-identity-invalid', 'Restore the private Forge credential and resume the same installation.')
    private.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(STATE, 0o700)
    os.chmod(private, 0o700)
    if not token.exists():
        temporary = private / 'forge-token.new'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'w', encoding='ascii') as stream:
            stream.write(secrets.token_hex(32) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(token)
    if not key.exists():
        temporary = private / 'kb-private.new'
        run('openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(temporary), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        temporary.replace(key)
    if not public.exists():
        temporary = private / 'kb-public.new'
        if temporary.is_symlink() or temporary.is_junction() or temporary.exists() and temporary.stat().st_nlink != 1:
            raise StageError('forge-identity-unsafe', 'Restore the private identity files and resume the same installation.')
        run('openssl', 'pkey', '-in', str(key), '-pubout', '-out', str(temporary), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        temporary.chmod(0o600)
        temporary.replace(public)
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
        run('useradd', '--create-home', '--shell', '/bin/bash', '--groups', 'adm,sudo', user, timeout=30)
    if form == 'wsl':
        Path('/etc/wsl.conf').write_text('[boot]\nsystemd=true\n[user]\ndefault=' + user + '\n', encoding='ascii')
    marker = STATE / 'console-account-set'
    if form == 'iso' and not marker.exists():
        status.update('firstboot', 'waiting', msg='Set the local account password on this console within five minutes, then resume if it expires')
        # Password input stays on the console. It never passes through C6 or a settings file.
        with open('/dev/console', 'r+b', buffering=0) as console:
            process = subprocess.Popen(['passwd', user], stdin=console, stdout=console, stderr=console)
            wait_process(process, status, 300, 'forge-console-expired',
                         'Set the account password on the local console and resume the same installation.')
        marker.write_text('set\n', encoding='ascii')


def signin(status, form, data):
    if data.get('transport', 'tailnet') == 'loopback':
        return data['node_name']
    spec = importlib.util.spec_from_file_location('firstboot_host', KIT / 'forms/host-requirements.py')
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    # No login/up/rename/DNS command is ever invoked by first boot.
    try:
        return host.tailscale_status(form)['name']
    except host.PreflightError as exc:
        raise StageError(exc.reason, str(exc)) from None


def pairing_committed(data, token, public):
    """Validate the core's durable enrolment, never a legacy marker alone."""
    runtime = str(KIT / 'pairing')
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    from deep32net import pairlock, store
    from deep32net.identity import ensure_identity
    from deep32net.tokens import read_kb_public, read_token
    state = STATE / 'pairing'
    if not state.exists():
        return False
    for path in (STATE, state, *sorted(state.rglob('*')), token, public):
        info = path.lstat()
        if (path.is_symlink() or path.is_junction()
                or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
                or stat.S_ISREG(info.st_mode) and info.st_nlink != 1
                or os.name == 'posix' and (info.st_uid != os.geteuid() or info.st_mode & 0o077)):
            raise RuntimeError('unsafe pairing state')
    rows = store.list_peers(state)
    if not rows:
        return False
    if not (state / 'identity').is_dir():
        raise RuntimeError('pairing identity missing')
    identity = ensure_identity(state, 'forge')
    claim = state / 'pairing/forge-joined.pairlock'
    if not claim.is_file() or pairlock.load(claim).in_progress:
        raise RuntimeError('pairing commit incomplete')
    read_token(token)
    read_kb_public(public)
    for row in rows:
        if (row['kind'] != 'forge' or row['side'] != 'joined' or row['peer_role'] != 'workstation'
                or row['own_pin'] != identity.pin or row['peer_pin'] == identity.pin
                or row['transport'] != data.get('transport', 'tailnet')
                or row['publish'] != data.get('publish', 'serve-https')
                or row['given'] not in (['forge-token', 'kb-public.pem'],
                                        ['forge-token', 'kb-public.pem', 'model-endpoints.json']) or row['received']):
            raise RuntimeError('pairing record conflicts with this node')
    return True


def pair(status, form, data, token, public):
    if pairing_committed(data, token, public):
        return
    # The bundled, reviewed core owns proof, TLS binding and persistent lockout.
    code = base64.b32encode(secrets.token_bytes(7)).decode('ascii')[:10]
    request = {'code': code, 'expires_seconds': 300, 'attempts': 1, 'form': form,
               'transport': data.get('transport', 'tailnet'), 'publish': data.get('publish', 'serve-https'),
               'token_path': str(token), 'kb_public_path': str(public), 'state_dir': str(STATE / 'pairing')}
    if 'forge_ports' in data:
        request['port'] = data['forge_ports'].get('pairing', 7453)
    publication = STATE / 'config/publication-request.json'
    if publication.is_file():
        handover = json.loads(publication.read_text(encoding='ascii'))
        if (handover.get('schema') != 'deep32.host-publication-request/1'
                or not isinstance(handover.get('model_endpoints'), dict)):
            raise RuntimeError('invalid Forge publication handover')
        request['model_endpoints'] = handover['model_endpoints']
    process = None
    committed = False
    try:
        status.update('pairing', 'waiting', pair=code, pair_with='workstation', expires=300,
                      msg='Pair from the Workstation within five minutes, then resume the same installation if it expires')
        # The interpreter running this lifecycle (3.12 or newer for the Docker form, the guest's own for WSL), not
        # the helper's #!/usr/bin/python3: appliance hosts have no usable system Python.
        process = subprocess.Popen([sys.executable, '-I', '-B', str(HELPER), 'pair-forge'], stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        process.stdin.write((json.dumps(request) + '\n').encode('ascii'))
        process.stdin.close()
        deadline = time.monotonic() + 300
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(min(10, max(0, deadline - time.monotonic())))
            remaining = max(0, int(deadline - time.monotonic()))
            if remaining:
                status.update('pairing', 'waiting', pair=code, pair_with='workstation', expires=remaining,
                              msg='Pair from the Workstation before expiry, then resume the same installation if needed')
        if process.poll() is None:
            raise StageError('forge-pairing-expired', 'Open Forge pairing on the Workstation and resume the same installation after any lockout expires.')
        if process.returncode != 0:
            raise StageError('forge-pairing-failed', 'Open Forge pairing on the Workstation and resume the same installation after any lockout expires.')
        committed = pairing_committed(data, token, public)
        if not committed:
            raise RuntimeError('pairing helper returned without a durable commit')
    finally:
        try:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        finally:
            status.update('pairing', 'done' if committed else 'failed')


def start_models(status, token):
    """Warm authenticated loopback doors before starting a short pairing window."""
    status.update('firstboot', 'waiting', msg='Starting verified model containers; inspect Docker on this host and resume if it fails')
    command(COMPOSE + ['up', '-d', '--pull', 'never', '--no-build', 'embeddings', 'reranker', 'door'], status, 60)
    bearer = token.read_text(encoding='ascii').strip()
    if not re.fullmatch(r'[0-9a-f]{64}', bearer):
        raise RuntimeError('invalid local Forge credential')
    # Loopback door endpoints have no redirect target; refuse one rather than
    # forwarding the service credential outside this request.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for port in (8081, 8082):
        status.update('firstboot', 'waiting', msg='Waiting for authenticated model health on 127.0.0.1:' + str(port)
                      + '; inspect the model container and resume if it fails')
        deadline = time.monotonic() + 600
        while True:
            try:
                request = urllib.request.Request('http://127.0.0.1:' + str(port) + '/health', headers={'Authorization': 'Bearer ' + bearer})
                with opener.open(request, timeout=5) as response:
                    if response.status == 200:
                        break
            except urllib.error.HTTPError as exc:
                exc.close()
                if exc.code in (401, 403):
                    raise StageError('forge-model-auth-denied', 'Restore the enrolled local Forge credential and resume the same installation.') from None
                if 300 <= exc.code < 400:
                    raise StageError('forge-model-response-invalid', 'Restore the local authenticated model-door configuration and resume the same installation.') from None
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise StageError('forge-model-health-timeout', 'Inspect the local model containers and resume the same installation.')
            time.sleep(min(5, max(0, deadline - time.monotonic())))
            status.repeat()


@contextlib.contextmanager
def firstboot_lock():
    for part in (STATE, *STATE.parents):
        if part.is_symlink() or part.is_junction():
            raise StageError('forge-identity-unsafe', 'Restore the private state directory and resume the same installation.')
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = STATE.stat()
    if os.name == 'posix' and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
        raise StageError('forge-identity-unsafe', 'Restore the private state directory permissions and resume the same installation.')
    path = STATE / 'firstboot.lock'
    if path.is_symlink() or path.is_junction() or path.exists() and path.stat().st_nlink != 1:
        raise StageError('forge-identity-unsafe', 'Restore the private state directory and resume the same installation.')
    with path.open('a+b') as stream:
        if os.name == 'posix':
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StageError('forge-firstboot-running', 'Wait for the running first boot to finish before resuming this installation.') from None
        yield


def boot(status, form, data, display_name):
    spec = importlib.util.spec_from_file_location('installed_inputs', KIT / 'firstboot/installed-inputs.py')
    inputs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inputs)
    config = inputs.pins()
    status.update('firstboot', 'running', msg='Verifying the installed payload; restore the same verified media if this fails')
    identity = inputs.verify_payload(config, cached=True)
    status.update('keys', 'running')
    token, public = make_identities()
    if form == 'docker':
        status.update('keys', 'waiting', msg='Checking the existing Docker daemon; start Docker on this host and resume if it fails')
        command(['docker', 'info'], status, 10)
    else:
        status.update('keys', 'waiting', msg='Starting the Docker service; repair this distribution and resume if it fails')
        command(['systemctl', 'start', 'docker.service'], status, 30)
    inputs.load_images(config, status, form, identity)
    if form == 'docker':
        subprocess.run(COMPOSE + ['up', '-d', '--pull', 'never', '--no-build', 'status'], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    status.update('keys', 'done')
    local_account(status, form, data)
    status.update('tailnet', 'done', name=display_name)
    start_models(status, token)
    pair(status, form, data, token, public)
    status.update('ready', 'done', name=display_name)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    if args.dry_run:
        print('Would validate non-secret C7 settings and read the owning host Tailscale status before writes; verify inputs; detect the TEI card class; reuse verified images and existing identities; print C6 with bounded waits; warm both authenticated loopback model doors; run bundled pairing and validate its durable commit. Downloads: none. No keys or tokens made in dry run.')
        return
    os.umask(0o077)
    status = Status()
    preflight_done = False
    try:
        form = Path('/etc/deep32-forge/form').read_text(encoding='ascii').strip()
        if form not in {'iso', 'wsl', 'docker'}:
            raise ValueError('invalid installed form')
        data = settings(status, form)
        display_name = signin(status, form, data)
        preflight_done = True
        with firstboot_lock():
            boot(status, form, data, display_name)
    except StageError as exc:
        if preflight_done:
            status.update('firstboot', 'failed', error=exc.reason, msg=str(exc))
        else:
            print(str(exc), file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        # C10 identifiers cross the C6 channel. Never log exception text or helper output.
        # Keep a more specific failed line already written.
        if not preflight_done:
            print('Restore valid installation settings and the host connection, then resume the same installation.', file=sys.stderr)
        elif not (status.state.get('state') == 'failed' and 'error' in status.state):
            status.update('firstboot', 'failed', error='forge-firstboot-failed', msg='Inspect the verified input pins and local containers, then resume the same installation')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
