"""Authenticated local C8 enrolment, with durable protected service files."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys


def _pairing(kit):
    path = Path(kit) / 'pairing'
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    from deep32net import files, tokens, store
    return files, tokens, store


def _protected(path, directory=False):
    st = path.stat()
    if not (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)) or st.st_nlink != 1 and not directory:
        raise ValueError('Restore the full-role private files from the same installation before restarting.')
    if os.name == 'posix' and (st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) & 0o077):
        raise ValueError('Give the full-role private files to the service owner with mode 0400 and directories 0700.')
    if os.name == 'nt':
        spec = importlib.util.spec_from_file_location('local_enrol_host_permissions',
            Path(__file__).resolve().parents[2] / 'forms/host-requirements.py')
        permissions = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(permissions)
        permissions.windows_private(path)


def _keys(private):
    """Use firstboot's per-install Ed25519 bundle identity, never release keys."""
    executable = shutil.which('openssl')
    if not executable:
        raise ValueError('Install the release-qualified OpenSSL host tool before local enrolment.')
    secret, public = private / 'kb-private.pem', private / 'kb-public.pem'
    for path in (secret, public):
        if path.exists():
            _protected(path)
    if not secret.exists():
        temporary = private / 'kb-private.new'
        subprocess.run([executable, 'genpkey', '-algorithm', 'ED25519', '-out', str(temporary)],
                       check=True, timeout=30, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        temporary.chmod(0o600)
        temporary.replace(secret)
    if not public.exists():
        temporary = private / 'kb-public.new'
        subprocess.run([executable, 'pkey', '-in', str(secret), '-pubout', '-out', str(temporary)],
                       check=True, timeout=30, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        temporary.chmod(0o600)
        temporary.replace(public)
    return public


def local_enrol(data_root, kit=None, key_factory=None, identity_factory=None, emit=print):
    """Run both unchanged C8 state machines on a private process-local channel.

    Only the committed Forge and MCP handovers become mounted service files.
    Restart revalidates reciprocal peer records and keeps the same credentials.
    The factories are fixture seams; runtime uses OpenSSL identities and keys.
    """
    kit = Path(kit) if kit is not None else Path(__file__).resolve().parents[2]
    files, tokens, store = _pairing(kit)
    spec = importlib.util.spec_from_file_location('deep32_local_channel', Path(__file__).with_name('local_channel.py'))
    channel = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(channel)
    root = files.safe(data_root)
    if os.name == 'nt':
        # Child identities and credentials inherit this owner's protected ACL.
        # Check before the pairing code can create any private state.
        _protected(root, True)
    private = files.safe(root / 'private')
    marker = files.safe(private / 'local-enrolment.json')
    committed = marker.exists()
    if private.exists():
        _protected(private, True)
    private = files.private_dir(private)
    for name in ('forge-token', 'core-token'):
        path = files.safe(private / name)
        if path.exists():
            _protected(path)
            tokens.read_token(path)
        elif committed:
            raise ValueError('Restore the missing local credential from this installation before restarting.')
        else:
            tokens.write_secret(path, tokens.make_token())
    if committed and any(not files.safe(private / name).is_file() for name in
                         ('kb-private.pem', 'kb-public.pem', 'mcp-token', 'mcp-tokens')):
        raise ValueError('Restore all committed local enrolment files before restarting.')
    with channel.waiting(emit, 'the private bundle identity'):
        public = (key_factory or _keys)(private)
    tokens.read_kb_public(public)
    if committed:
        for name in ('forge', 'engine', 'gateway', 'headend'):
            identity = files.safe(root / 'pairing' / name / 'identity')
            if not identity.is_dir() or any(not files.safe(identity / item).is_file()
                                           for item in ('id', 'cert.pem', 'key.pem')):
                raise ValueError('Restore all committed local pairing identities before restarting; they will not be regenerated.')
            _protected(identity, True)
            for item in ('id', 'cert.pem', 'key.pem'):
                _protected(files.safe(identity / item))
    states = {name: files.private_dir(root / 'pairing' / name) for name in
              ('forge', 'engine', 'gateway', 'headend')}
    forge = channel.pair_local('forge', states['forge'], states['engine'],
                               token_path=private / 'forge-token', kb_public_path=public,
                               identity_factory=identity_factory, emit=emit)
    mcp = channel.pair_local('gateway', states['gateway'], states['headend'],
                             identity_factory=identity_factory, emit=emit)
    received_forge = store.received_dir(states['engine'], 'forge', forge['peer_id'])
    if tokens.read_token(received_forge / 'forge-token') != tokens.read_token(private / 'forge-token'):
        raise ValueError('Restore the enrolled Forge credential before restarting the model bridge.')
    if tokens.read_kb_public(received_forge / 'kb-public.pem') != tokens.read_kb_public(public):
        raise ValueError('Restore the enrolled bundle public key before restarting.')
    received_mcp = store.received_dir(states['headend'], 'gateway', mcp['peer_id']) / 'mcp-token'
    enrolled = tokens.read_token(received_mcp)
    issued = store.mcp_tokens(states['gateway'])
    if enrolled not in issued.values():
        raise ValueError('Restore both sides of the MCP enrolment before restarting.')
    for name, raw in (('mcp-token', (enrolled + '\n').encode('ascii')),
                      ('mcp-tokens', (enrolled + '\n').encode('ascii'))):
        path = files.safe(private / name)
        if path.exists():
            _protected(path)
            values = files.read_bytes(path)
            if name == 'mcp-token' and tokens.read_token(path) != enrolled or name == 'mcp-tokens' and (
                    not re.fullmatch(rb'(?:[0-9a-f]{64}\n)+', values) or enrolled.encode('ascii') not in values.splitlines()):
                raise ValueError('Restore the enrolled local MCP authority before restarting.')
        elif committed:
            raise ValueError('Restore the missing local MCP authority before restarting.')
        else:
            files.atomic_write(path, raw, overwrite=False)
    record = {'schema': 'deep32.local-enrolment.v1', 'transport': 'protected-process-channel',
              'forge_peer': forge['peer_id'], 'mcp_peer': mcp['peer_id'],
              'credentials': ['forge-token', 'core-token', 'mcp-token'], 'mcp_allow_list': 'mcp-tokens'}
    if committed:
        _protected(marker)
        if json.loads(files.read_bytes(marker)) != record:
            raise ValueError('Restore the original local enrolment record before restarting.')
    else:
        files.atomic_write(marker, (json.dumps(record, sort_keys=True) + '\n').encode('ascii'), overwrite=False)
    if os.name == 'posix':
        for name in ('forge-token', 'core-token', 'mcp-tokens'):
            (private / name).chmod(0o400)
    return {'enrolled': True, 'transport': 'protected-process-channel',
            'private_directory': str(private), 'credential_files': record['credentials'],
            'mcp_allow_list': 'mcp-tokens'}
