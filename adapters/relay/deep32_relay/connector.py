"""Existing Connect's relay lifecycle, with resumable owned-file transactions.

No agent, enrolment, socket, startup command or route is invoked by default.
Native qualification cannot be enabled by a receipt or a command-line flag.
"""
import argparse
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
from .platform import PlatformAdapter, ServiceSpec, platform_name, render, state_base
from .state import OWNER_SCHEMA, OwnedRoot, StateError, json_bytes, safe_path, valid_hash, valid_id
from .status import read_status
from .wire import ProtocolError

SCHEMA = 'deep32.relay-install.v1'
CONFIG_SCHEMA = 'deep32.relay-config.v1'


def _adapter(owner, spec, definition, api):
    if api == 'native':
        from .platform import NativePlatformAPI
        api = NativePlatformAPI(owner, spec, definition)
    return PlatformAdapter(api)


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def source_package(source=None):
    source = safe_path(source or Path(__file__).parent.parent)
    files = {}
    # Copy only source and explicitly reviewed packaged instruction artefacts.
    # Never copy runtime/private state, caches, accounts, fixtures or hidden files.
    candidates = [source / 'run_relay.py']
    lock = source / 'tls-provider-lock.json'
    if lock.is_file():
        candidates.append(lock)
    if (source / 'requirements.lock').is_file():
        candidates.append(source / 'requirements.lock')
    if (source / 'RESEARCH.md').is_file():
        candidates.append(source / 'RESEARCH.md')
    candidates += sorted((source / 'deep32_relay').glob('*.py'))
    candidates += sorted((source / 'deep32_relay').glob('*.txt'))
    if (not candidates[0].is_file() or not (source / 'deep32_relay/__init__.py').is_file() or
            not (source / 'RESEARCH.md').is_file()):
        raise StateError('relay-package-missing')
    for path in candidates:
        safe_path(path)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024 * 1024:
            raise StateError('unsafe-package')
        relative = path.relative_to(source).as_posix()
        files[relative] = path.read_bytes()
    manifest = {name: _digest(raw) for name, raw in sorted(files.items())}
    return _digest(json_bytes(manifest)), files


def _absolute_binary(value, *, agent=False):
    path = Path(value)
    if not path.is_absolute() or any(ord(char) < 32 for char in str(path)):
        raise StateError('absolute-binary-required')
    # Keep the agent's selected stable entry point in the receipt. Its native
    # installer may retarget a symlink on upgrade; only launch-time resolution
    # selects a version. Owned state and the Python interpreter remain no-link.
    return str(Path(os.path.abspath(path)) if agent else safe_path(path))


def binary_found(value):
    try:
        from .engine import resolve_agent_binary
        resolve_agent_binary(value)
        return True
    except (OSError, StateError, TypeError, ValueError):
        return False


def _config(install_id, engine, transport='unconfigured', port=7452):
    if transport not in ('unconfigured', 'loopback', 'tailnet') or type(port) is not int or not 1024 <= port <= 65535:
        raise StateError('invalid-relay-transport')
    return {'schema': CONFIG_SCHEMA, 'install_id': install_id, 'agent': engine,
            'profile_alias': 'relay-' + engine, 'listener': '127.0.0.1', 'port': port,
            'transport': transport, 'intake_enabled': transport != 'unconfigured',
            'max_active_sessions': 1, 'max_queue': 2, 'queue_timeout_seconds': 30,
            'turn_timeout_seconds': 170, 'idle_timeout_seconds': 60,
            'session_timeout_seconds': 3600, 'turns_per_hour': 30}


def validate_receipt(owner, *, allow_missing_owned=False):
    install_id = owner.validate()
    receipt = owner.read_json('install.json')
    fields = {'schema', 'install_id', 'agent', 'profile_alias', 'release_id', 'previous_release_id',
              'agent_binary', 'python_binary', 'platform', 'state', 'capability_id',
              'startup_registered', 'startup_name', 'startup_file', 'owned_files', 'routes',
              'usage_consent', 'unavailable_reasons'}
    if (type(receipt) is not dict or set(receipt) not in (fields, fields | {'config'}) or receipt['schema'] != SCHEMA or
            receipt['install_id'] != install_id or receipt['agent'] not in ('codex', 'claude') or
            receipt['profile_alias'] != 'relay-' + receipt['agent'] or
            not valid_hash(receipt['release_id']) or
            not (receipt['previous_release_id'] is None or valid_hash(receipt['previous_release_id'])) or
            receipt['platform'] not in ('windows', 'linux', 'macos') or
            receipt['state'] not in ('installed-unavailable', 'removed') or
            receipt['capability_id'] != 'unqualified' or receipt['routes'] != [] or
            type(receipt['startup_registered']) is not bool or receipt['usage_consent'] is not True or
            receipt['unavailable_reasons'] != ['native-agent-unqualified', 'pairing-bootstrap-unavailable', 'autostart-unqualified'] or
            type(receipt['owned_files']) is not dict or
            receipt['startup_name'] != 'deep32-relay-' + install_id):
        raise StateError('invalid-install-receipt')
    _absolute_binary(receipt['agent_binary'], agent=True)
    _absolute_binary(receipt['python_binary'])
    spec = _spec(owner, receipt)
    startup_file, definition = render(spec)
    if receipt['startup_file'] != 'startup/' + receipt['release_id'] + '/' + startup_file:
        raise StateError('invalid-install-receipt')
    saved_config = receipt.get('config')
    if 'config' in receipt and (type(saved_config) is not dict or saved_config != _config(
            install_id, receipt['agent'], saved_config.get('transport'), saved_config.get('port'))):
        raise StateError('invalid-install-receipt')
    if receipt['state'] == 'removed':
        if receipt['owned_files'] or receipt['startup_registered']:
            raise StateError('invalid-install-receipt')
        return receipt
    required = {'config.json', receipt['startup_file'],
                'packages/' + receipt['release_id'] + '/run_relay.py',
                'packages/' + receipt['release_id'] + '/deep32_relay/__init__.py'}
    if not required.issubset(receipt['owned_files']):
        raise StateError('invalid-install-receipt')
    if saved_config is not None and _digest(json_bytes(saved_config)) != receipt['owned_files']['config.json']:
        raise StateError('invalid-install-receipt')
    for relative, digest in receipt['owned_files'].items():
        if (not valid_hash(digest) or
                not (relative in ('config.json', receipt['startup_file']) or
                     relative.startswith('packages/' + receipt['release_id'] + '/'))):
            raise StateError('invalid-install-receipt')
        owner.path(relative)
    if not allow_missing_owned or owner.path('config.json').exists():
        config = owner.read_json('config.json')
        if type(config) is not dict:
            raise StateError('invalid-relay-config')
        if config != _config(install_id, receipt['agent'], config.get('transport'), config.get('port')):
            raise StateError('modified-owned-file')
        if saved_config is not None and config != saved_config:
            raise StateError('modified-owned-file')
    if ((not allow_missing_owned or owner.path(receipt['startup_file']).exists()) and
            owner.read(receipt['startup_file']) != definition):
        raise StateError('modified-owned-file')
    return receipt


def _spec(owner, receipt):
    return ServiceSpec(receipt['install_id'], receipt['python_binary'],
                       str(owner.root / 'packages' / receipt['release_id'] / 'run_relay.py'),
                       str(owner.root), receipt['platform'])


def _verify_files(owner, receipt, *, allow_missing=False):
    for relative, digest in sorted(receipt['owned_files'].items()):
        if allow_missing and not owner.path(relative).exists():
            continue
        if _digest(owner.read(relative)) != digest:
            raise StateError('modified-owned-file')
    # A receipt authorises exact regular files, never arbitrary directory trees.
    for base in ('packages/' + receipt['release_id'], 'startup/' + receipt['release_id']):
        directory = owner.path(base)
        for path in sorted(directory.rglob('*')):
            relative = path.relative_to(owner.root).as_posix()
            safe_path(path)
            info = path.lstat()
            owner.security.verify(path, directory=stat.S_ISDIR(info.st_mode))
            if stat.S_ISDIR(info.st_mode):
                if not any(name.startswith(relative + '/') for name in receipt['owned_files']):
                    raise StateError('unrecorded-owned-file')
            elif relative not in receipt['owned_files']:
                raise StateError('unrecorded-owned-file')


def _transaction(owner, receipt):
    if not owner.path('transaction.json').exists():
        return None
    pending = owner.read_json('transaction.json', 2048)
    if (type(pending) is not dict or set(pending) != {'schema', 'install_id', 'release_id', 'phase'} or
            pending['schema'] != 'deep32.relay-transaction.v1' or
            pending['install_id'] != receipt['install_id'] or not valid_hash(pending['release_id']) or
            (pending['phase'] == 'removing' and pending['release_id'] != receipt['release_id']) or
            pending['phase'] not in ('preparing', 'registered', 'removing')):
        raise StateError('transaction-recovery-required')
    return pending


def _retry_receipt(owner):
    # Missing owned files are valid only after a recorded removal has begun.
    # Ordinary installs retain their existing collision and modification checks.
    receipt = validate_receipt(owner, allow_missing_owned=True)
    pending = _transaction(owner, receipt)
    if receipt['state'] != 'removed' and (pending is None or pending['phase'] != 'removing'):
        return validate_receipt(owner)
    return receipt


def _resume_removal(owner, *, platform_api=None, route_api=None):
    if not owner.path('install.json').exists():
        return
    with owner.lock():
        receipt = _retry_receipt(owner)
        pending = _transaction(owner, receipt)
    if pending is not None and pending['phase'] == 'removing':
        result = remove(owner, platform_api=platform_api, route_api=route_api)
        if not result['removal']['complete']:
            raise StateError('relay-removal-incomplete')


def install(root, engine, agent_binary, python_binary=None, source=None, platform=None,
            platform_api=None, install_id=None, security=None, transport='unconfigured', port=7452,
            route_api=None):
    # A registered service starts only here: after the receipt, qualification
    # and journal are committed and the operation lock is released. The service
    # checks the committed receipt and takes that lock itself.
    result, start = _install(root, engine, agent_binary, python_binary=python_binary, source=source,
                             platform=platform, platform_api=platform_api, install_id=install_id,
                             security=security, transport=transport, port=port, route_api=route_api)
    if start is not None:
        start()
    return result


def _starter(adapter, spec, receipt):
    if not receipt['startup_registered']:
        return None
    return lambda: adapter.start(spec, render(spec)[1])


def _forget_attempt(owner):
    # The committed receipt now selects this root. Clearing the attempt marker
    # under the install lock leaves nothing that needs the lock after the start.
    if owner.path('install-attempt.json').exists():
        owner.path('install-attempt.json').unlink()


def _install(root, engine, agent_binary, python_binary=None, source=None, platform=None,
             platform_api=None, install_id=None, security=None, transport='unconfigured', port=7452,
             route_api=None):
    if engine not in ('codex', 'claude'):
        raise StateError('one-agent-required')
    _config(install_id or '0' * 32, engine, transport, port)
    if transport == 'tailnet' and route_api is None:
        raise StateError('tailnet-forwarder-required')
    agent_binary = _absolute_binary(agent_binary, agent=True)
    # Resolve only the interpreter already executing this connector. An
    # explicitly selected binary still passes the strict no-link validation.
    python_binary = _absolute_binary(python_binary if python_binary is not None
                                     else Path(sys.executable).resolve(strict=True))
    release_id, package = source_package(source)
    owner = root if isinstance(root, OwnedRoot) else OwnedRoot(root, security=security)
    platform = platform or platform_name()
    ident = install_id or (owner.root.name if valid_id(owner.root.name) else secrets.token_hex(16))
    if not valid_id(ident):
        raise StateError('invalid-install-id')
    new = owner.create()
    if not new and not (owner.root / 'owner.json').exists() and next(owner.root.iterdir(), None) is not None:
        raise StateError('foreign-state-root')
    if not (owner.root / 'owner.json').exists():
        owner.write_json('owner.json', {'schema': OWNER_SCHEMA, 'install_id': ident})
    owner.validate(ident)
    _resume_removal(owner, platform_api=platform_api, route_api=route_api)
    with owner.lock():
        prior = None
        if (owner.root / 'install.json').exists():
            prior = validate_receipt(owner)
            if prior['state'] == 'removed':
                # Alpha.2 upgrade is remove then install. Durable identity,
                # pins, credentials and relay-owned profiles remain in place.
                _clear_stop(owner, prior)
                from .platform import OwnedServeRoute
                route_record = owner.path(OwnedServeRoute.RECORD)
                if route_record.exists():
                    recorded = owner.read_json(OwnedServeRoute.RECORD, 4096)
                    old_route = OwnedServeRoute(owner, route_api, recorded.get('port') if isinstance(recorded, dict) else None)
                    if old_route._record()['phase'] != 'off':
                        raise StateError('cleanup-unconfirmed')
                    if route_api is not None:
                        old_route.remove()
                    route_record.unlink()
                    if route_record.exists():
                        raise StateError('cleanup-unconfirmed')
            else:
                if prior['release_id'] != release_id:
                    raise StateError('remove-before-install')
                _verify_files(owner, prior)
            if (prior['state'] != 'removed' and (prior['agent'] != engine or prior['agent_binary'] != agent_binary or
                    prior['python_binary'] != python_binary or prior['platform'] != platform)):
                raise StateError('install-binding-mismatch')
            if prior['state'] != 'removed' and owner.read_json('config.json') != _config(ident, engine, transport, port):
                raise StateError('install-binding-mismatch')
            observation = read_status(owner)
            if (prior['state'] != 'removed' and not prior['startup_registered'] and (owner.root / 'runtime/status.json').exists() and
                    (observation['snapshot'] is None or observation['snapshot']['pid'] != 0)):
                raise StateError('cleanup-unconfirmed')
            _clear_stop(owner, prior)
        if (owner.root / 'transaction.json').exists():
            pending = owner.read_json('transaction.json', 2048)
            if (type(pending) is not dict or set(pending) != {'schema', 'install_id', 'release_id', 'phase'} or
                    pending['schema'] != 'deep32.relay-transaction.v1' or
                    pending['install_id'] != ident or pending['release_id'] != release_id or
                    pending['phase'] not in ('preparing', 'registered', 'removing')):
                raise StateError('transaction-recovery-required')
            if pending['phase'] == 'removing':
                raise StateError('transaction-recovery-required')
        if prior and prior['state'] != 'removed' and prior['release_id'] == release_id and not (owner.root / 'transaction.json').exists():
            spec = _spec(owner, prior)
            adapter = _adapter(owner, spec, render(spec)[1], platform_api)
            if prior['startup_registered']:
                if not adapter.api.qualified:
                    raise StateError('autostart-unqualified')
                if adapter.api.inspect(prior['startup_name']) != render(_spec(owner, prior))[1]:
                    raise StateError('foreign-startup-entry')
            elif adapter.api.qualified:
                # Pairing may deliberately precede autostart. A repeated
                # explicit install can then register the same immutable build.
                registered = adapter.register(spec, render(spec)[1])
                prior['startup_registered'] = registered
                owner.write_json('install.json', prior)
            if transport == 'tailnet':
                from .platform import OwnedServeRoute
                OwnedServeRoute(owner, route_api, port).publish()
            from .engine import qualify_install
            qualify_install(owner, prior, executable=agent_binary, platform=platform)
            _clear_stop(owner, prior)
            _forget_attempt(owner)
            return _observed_install(owner, prior), _starter(adapter, spec, prior)
        journal = {'schema': 'deep32.relay-transaction.v1', 'install_id': ident,
                   'release_id': release_id, 'phase': 'preparing'}
        owner.write_json('transaction.json', journal)
        # This path is relay-owned from first creation. Never inspect native
        # account/configuration files or copy any ordinary agent profile.
        for directory in ('profiles', 'profiles/' + engine, 'private', 'private/link',
                          'private/enrolment', 'runtime', 'runtime/sessions', 'logs',
                          'packages', 'packages/' + release_id, 'startup'):
            owner.mkdir(directory)
        receipt = {'schema': SCHEMA, 'install_id': ident, 'agent': engine,
                   'profile_alias': 'relay-' + engine, 'release_id': release_id,
                   'previous_release_id': prior['release_id'] if prior and prior['release_id'] != release_id else (prior['previous_release_id'] if prior else None),
                   'agent_binary': agent_binary, 'python_binary': python_binary,
                   'platform': platform, 'state': 'installed-unavailable',
                   'capability_id': 'unqualified', 'startup_registered': False,
                   'startup_name': 'deep32-relay-' + ident, 'startup_file': '',
                   'owned_files': {}, 'routes': [], 'usage_consent': True,
                   'config': _config(ident, engine, transport, port),
                   'unavailable_reasons': ['native-agent-unqualified', 'pairing-bootstrap-unavailable', 'autostart-unqualified']}
        spec = _spec(owner, receipt)
        startup_name, definition = render(spec)
        receipt['startup_file'] = 'startup/' + release_id + '/' + startup_name
        files = {'packages/' + release_id + '/' + name: raw for name, raw in package.items()}
        files.update({'config.json': json_bytes(receipt['config']), receipt['startup_file']: definition})
        for relative, raw in sorted(files.items()):
            parent = Path(relative).parent.as_posix()
            if parent != '.':
                owner.mkdir(parent)
            path = owner.path(relative)
            if path.exists() and owner.read(relative) != raw:
                raise StateError('modified-owned-file')
            if not path.exists() or owner.read(relative) != raw:
                owner.write(relative, raw)
            receipt['owned_files'][relative] = _digest(raw)
        adapter = _adapter(owner, spec, definition, platform_api)
        registered = False
        removed_previous = False
        try:
            if transport == 'tailnet':
                from .platform import OwnedServeRoute
                OwnedServeRoute(owner, route_api, port).publish()
            if prior and prior['startup_registered'] and prior['release_id'] != release_id:
                oldspec = _spec(owner, prior)
                _adapter(owner, oldspec, render(oldspec)[1], platform_api).remove(oldspec, render(oldspec)[1])
                removed_previous = True
            registered = adapter.register(spec, definition)
            receipt['startup_registered'] = registered
            if registered:
                journal['phase'] = 'registered'
                owner.write_json('transaction.json', journal)
            # Receipt commit selects the complete package atomically. Old
            # packages and all durable private/profile state survive upgrades.
            owner.write_json('install.json', receipt)
            # This function records an unavailable result without invoking an
            # agent when native qualification inputs have not been approved.
            from .engine import qualify_install
            qualify_install(owner, receipt, executable=agent_binary, platform=platform)
            _clear_stop(owner, receipt)
        except BaseException:
            if registered:
                adapter.remove(spec, definition)
            if removed_previous:
                oldspec = _spec(owner, prior)
                _adapter(owner, oldspec, render(oldspec)[1], platform_api).register(oldspec, render(oldspec)[1])
                owner.write(prior['startup_file'], render(oldspec)[1])
            raise
        owner.path('transaction.json').unlink()
        _forget_attempt(owner)
        return _observed_install(owner, receipt), _starter(adapter, spec, receipt)


def public_install(receipt, agent_found=False):
    return {'schema': SCHEMA, 'install_id': receipt['install_id'], 'agent': receipt['agent'],
            'profile_alias': receipt['profile_alias'], 'release_id': receipt['release_id'],
            'state': receipt['state'], 'capability_id': 'unqualified', 'ready': False,
            'agent_binary_found': bool(agent_found), 'startup_registered': receipt['startup_registered'],
            'unavailable_reasons': list(receipt['unavailable_reasons']),
            'remote_revocation': 'not-performed'}


def _clear_stop(owner, receipt):
    """Clear only a validated old stop intent after owned cleanup is proved."""
    path = owner.path('runtime/stop-request.json')
    if not path.exists():
        return
    value = owner.read_json('runtime/stop-request.json', 1024)
    if value != {'schema': 'deep32.relay-stop.v1', 'install_id': receipt['install_id']}:
        raise StateError('invalid-stop-request')
    snapshot = read_status(owner)['snapshot']
    if (receipt['state'] != 'removed' and (snapshot is None or snapshot['pid'] != 0 or snapshot['running'] or
            snapshot['link_state'] != 'off' or snapshot['last_session_result']['reason'] == 'cleanup-failed')):
        raise StateError('cleanup-unconfirmed')
    path.unlink()


def status(root, security=None, **status_options):
    owner = root if isinstance(root, OwnedRoot) else OwnedRoot(root, security=security)
    receipt = validate_receipt(owner)
    result = _observed_install(owner, receipt)
    result['supervisor'] = read_status(owner, **status_options)
    from .engine import qualification_status
    result['qualification'] = qualification_status(owner, receipt, executable=receipt['agent_binary'])
    return result


def _observed_install(owner, receipt):
    result = public_install(receipt, agent_found=binary_found(receipt['agent_binary']))
    if receipt['state'] == 'removed':
        return result
    from .engine import qualification_status
    from .identity import IdentityStore
    from .tls import provider_readiness
    qualification = qualification_status(owner, receipt, executable=receipt['agent_binary'])
    reasons = []
    if qualification.get('state') != 'qualified':
        reasons.append('native-agent-unqualified')
    if IdentityStore(owner).load_peer() is None:
        reasons.append('pairing-required')
    provider_ready = provider_readiness()
    provider_file = owner.root / 'private/provider-install.json'
    if not provider_ready and provider_file.exists():
        provider_record = owner.read_json('private/provider-install.json', 4 * 1024 * 1024)
        provider_ready = (provider_record.get('schema') == 'deep32.relay-provider.v1'
                          and provider_record.get('complete') is True
                          and binary_found(receipt['python_binary']))
    if not provider_ready:
        reasons.append('tls-provider-unqualified')
    if not owner.read_json('config.json')['intake_enabled']:
        reasons.append('relay-intake-disabled')
    result.update(qualification=qualification, ready=not reasons, unavailable_reasons=reasons)
    if qualification.get('state') == 'qualified':
        result.update(state='installed', capability_id=receipt['release_id'])
        from .selftest import advertised_choices
        choices = advertised_choices(owner, receipt)
        if choices is not None:
            result['advertised'] = choices
    return result


def check(root, **options):
    # Pure selected-install inspection: no native --version/auth probe.
    owner = root if isinstance(root, OwnedRoot) else OwnedRoot(root, security=options.pop('security', None))
    receipt = validate_receipt(owner)
    _verify_files(owner, receipt)
    return status(owner, **options)


def pair(root, *, pair_console=None):
    """The local foreground console is the only pairing-window authority."""
    owner = root if isinstance(root, OwnedRoot) else OwnedRoot(root)
    with owner.lock():
        receipt = validate_receipt(owner)
        if receipt['state'] == 'removed' or (owner.root / 'transaction.json').exists():
            raise StateError('pairing-install-unavailable')
        _verify_files(owner, receipt)
        # read_status marks the snapshot running only while its process is alive and
        # is the same instance. A stopped service leaves its last pid in the file
        # (systemd ends it with SIGTERM), so the pid alone is no evidence it still
        # runs. The service lock taken below is the authoritative guard.
        snapshot = read_status(owner)['snapshot']
        if snapshot is not None and snapshot['running']:
            raise StateError('relay-stop-required')
        if pair_console is None:
            from .transport import pair_console
        with owner.lock('service.lock'):
            record = pair_console(owner)
        # No node id, pin, SAS, certificate, path or native exception is public.
        return {'schema': 'deep32.relay-pair-result.v1', 'paired': record is not None,
                'agent': receipt['agent'], 'profile_alias': receipt['profile_alias'],
                'install_id': receipt['install_id']}


def unpair(root):
    """Explicit local administration: forget the paired Workstation.

    Deletes the Workstation's stored pin and clears the pairing fields of the
    last saved service observation. The relay's own identity, agent profile and
    sign-in, packages, provider and install receipt stay, so the next pairing
    presents the same relay certificate. Nothing native, no socket and no TLS
    provider is used, so the source kit's interpreter runs it directly.
    """
    from contextlib import ExitStack
    from .identity import IdentityStore
    owner = root if isinstance(root, OwnedRoot) else OwnedRoot(root)
    with owner.lock(), ExitStack() as stopped_scope:
        receipt = validate_receipt(owner)
        if owner.path('transaction.json').exists():
            raise StateError('transaction-recovery-required')
        # Same rule as pair(): a snapshot whose process is still the relay
        # refuses at once, and the service lock below is the authoritative guard.
        snapshot = read_status(owner)['snapshot']
        if snapshot is not None and snapshot['running']:
            raise StateError('relay-stop-required')
        try:
            stopped_scope.enter_context(owner.lock('service.lock'))
        except StateError as exc:
            # Connector actions take the operation lock first, so under it only
            # a running service can hold the service lock.
            if exc.reason == 'operation-pending':
                raise StateError('relay-stop-required') from None
            raise
        changed = (owner.root / 'private/link/peer.pin').exists()
        if changed:
            IdentityStore(owner).revoke_peer()
            if owner.path('private/link/peer.pin').exists():
                raise StateError('cleanup-unconfirmed')
        _forget_paired_status(owner, receipt['install_id'])
        # No node id, pin, certificate or path is public.
        return {'schema': 'deep32.relay-unpair-result.v1', 'paired': False, 'changed': changed,
                'agent': receipt['agent'], 'profile_alias': receipt['profile_alias'],
                'install_id': receipt['install_id']}


def _forget_paired_status(owner, install_id):
    """Clear only the pairing fields of the last saved service observation."""
    from .status import MAX_STATUS, publish_status, validate_snapshot
    if not (owner.root / 'runtime/status.json').exists():
        return
    try:
        saved = validate_snapshot(owner.read_json('runtime/status.json', MAX_STATUS))
    except (StateError, ValueError):
        # An unreadable snapshot already reads as no observation at all.
        return
    if saved['install_id'] == install_id and saved['paired_workstation']:
        # The process, link and time fields keep their observed values; removal
        # still relies on the service's own pid-zero acknowledgement.
        publish_status(owner, dict(saved, paired_workstation=False, workstation_alias='none'))


def remove(root, platform_api=None, security=None, route_api=None):
    from contextlib import ExitStack
    owner = root if isinstance(root, OwnedRoot) else OwnedRoot(root, security=security)
    with owner.lock(), ExitStack() as stopped_scope:
        receipt = validate_receipt(owner, allow_missing_owned=True)
        if receipt['state'] == 'removed':
            pending = _transaction(owner, receipt)
            if pending is not None and pending['phase'] == 'removing':
                owner.path('transaction.json').unlink()
            result = public_install(receipt)
            result['removal'] = {'complete': True, 'leftovers': []}
            return result
        _verify_files(owner, receipt, allow_missing=True)
        if 'config' not in receipt and owner.path('config.json').exists():
            # Upgrade an older receipt while its validated configuration is
            # still available; removal must not discard the selected endpoint.
            receipt['config'] = owner.read_json('config.json')
            owner.write_json('install.json', receipt)
        journal = {'schema': 'deep32.relay-transaction.v1', 'install_id': receipt['install_id'],
                   'release_id': receipt['release_id'], 'phase': 'removing'}
        owner.write_json('transaction.json', journal)
        leftovers = []
        provider_details = {}
        if receipt['startup_registered']:
            spec = _spec(owner, receipt)
            try:
                _adapter(owner, spec, render(spec)[1], platform_api).remove(spec, render(spec)[1])
            except (StateError, OSError):
                leftovers.append('startup')
        else:
            # A manually run relay holds this exact lifetime lease. Stop it
            # cooperatively, then test the lease; no descendant observer.
            from .platform import request_stop
            try:
                request_stop(owner, receipt['install_id'])
            except (StateError, OSError):
                leftovers.append('service')
        if not any(name in leftovers for name in ('startup', 'service')):
            try:
                # Also excludes a manually started daemon sharing a registered
                # installation. Hold the exact lease throughout file removal.
                stopped_scope.enter_context(owner.lock('service.lock'))
            except (StateError, OSError):
                leftovers.append('service')
        from .platform import OwnedServeRoute
        route_path = owner.path(OwnedServeRoute.RECORD)
        if route_path.exists():
            record = owner.read_json(OwnedServeRoute.RECORD, 4096)
            if route_api is None and record.get('phase') != 'off':
                leftovers.append('serve-route')
            elif route_api is not None:
                try:
                    OwnedServeRoute(owner, route_api, record.get('port')).remove()
                except (StateError, OSError):
                    leftovers.append('serve-route')
        # Never remove running service code. Otherwise remove only exact
        # receipt-listed regular files, reading every unlink back individually.
        if not any(name in leftovers for name in ('startup', 'service')):
            for relative in sorted(receipt['owned_files'], reverse=True):
                try:
                    path = owner.path(relative)
                    if path.exists():
                        if _digest(owner.read(relative)) != receipt['owned_files'][relative]:
                            raise StateError('modified-owned-file')
                        path.unlink()
                    if path.exists():
                        leftovers.append(relative)
                except (StateError, OSError):
                    leftovers.append(relative)
        if not leftovers and owner.path('private/provider-install.json').exists():
            from .provider import remove_provider
            try:
                provider_details = {}
                leftovers.extend(remove_provider(owner, details=provider_details))
            except (StateError, OSError, ValueError, TypeError, KeyError) as exc:
                leftovers.append('provider')
                provider_details = {'code': _exception_code(exc)}
        if leftovers:
            result = public_install(receipt, agent_found=binary_found(receipt['agent_binary']))
            result['removal'] = {'complete': False, 'leftovers': sorted(set(leftovers))}
            if provider_details and provider_details.get('code') != 'relay-operation-unavailable':
                result['removal']['provider'] = provider_details
            return result
        receipt.update(state='removed', startup_registered=False, owned_files={})
        owner.write_json('install.json', receipt)
        owner.path('transaction.json').unlink()
        result = public_install(receipt)
        result['removal'] = {'complete': True, 'leftovers': []}
        return result


ATTEMPT_SCHEMA = 'deep32.relay-install-attempt.v1'


def _attempt(owner):
    value = owner.read_json('install-attempt.json', 8192)
    if (type(value) is not dict or set(value) != {'schema', 'install_id', 'agent', 'agent_binary'}
            or value['schema'] != ATTEMPT_SCHEMA or value['install_id'] != owner.validate()
            or value['agent'] not in ('claude', 'codex')):
        raise StateError('invalid-install-attempt')
    _absolute_binary(value['agent_binary'], agent=True)
    return value


def _select_existing(agent, *, for_install=False):
    """Reuse an owned failed attempt; never accumulate anonymous retry roots."""
    candidates = []
    base = state_base()
    if base.exists():
        for path in sorted(base.iterdir()):
            if not valid_id(path.name):
                continue
            selected = OwnedRoot(path)
            if selected.path('install.json').exists():
                record = _retry_receipt(selected) if for_install else validate_receipt(selected)
                usable = for_install or record['state'] != 'removed'
            elif for_install and selected.path('install-attempt.json').exists():
                record = _attempt(selected)
                usable = True
            else:
                continue
            if record['agent'] == agent and usable:
                candidates.append(path.name)
    if len(candidates) > 1 or not candidates and not for_install:
        raise StateError('existing-install-selector-required')
    # The ID is a public ownership selector, never an authentication secret.
    # Concurrent first installs must choose the same per-agent lock/root.
    return candidates[0] if candidates else hashlib.sha256(
        ('deep32.relay.implicit-install.v1:' + agent).encode('ascii')).hexdigest()[:32]


def _record_attempt(owner, ident, agent, executable):
    expected = {'schema': ATTEMPT_SCHEMA, 'install_id': ident, 'agent': agent,
                'agent_binary': _absolute_binary(executable, agent=True)}
    if owner.path('install.json').exists():
        prior = _retry_receipt(owner)
        if prior['state'] != 'removed' and (prior['agent'] != agent or prior['agent_binary'] != expected['agent_binary']):
            raise StateError('install-binding-mismatch')
        if owner.path('install-attempt.json').exists():
            owner.path('install-attempt.json').unlink()
        return
    if owner.path('install-attempt.json').exists():
        if _attempt(owner) != expected:
            raise StateError('install-binding-mismatch')
    else:
        owner.write_json('install-attempt.json', expected)


def login(root, *, runner=None):
    """Native sign-in selects only the relay profile, followed by a no-model check."""
    from .engine import clean_environment, qualify_install, resolve_agent_binary
    from .runtime import Scratch
    owner = root if isinstance(root, OwnedRoot) else OwnedRoot(root)
    with owner.lock(), owner.lock('service.lock'):
        receipt = validate_receipt(owner)
        if receipt['state'] == 'removed' or owner.path('transaction.json').exists():
            raise StateError('login-install-unavailable')
        _verify_files(owner, receipt)
        profile = owner.path('profiles/' + receipt['agent'])
        if not profile.is_dir():
            raise StateError('login-profile-unavailable')
        owner.security.verify(profile, directory=True)
        scratch = Scratch(owner.path('runtime/sessions'), validate_owner=owner.validate)
        try:
            env = clean_environment(scratch.path, platform=receipt['platform'])
            env['CLAUDE_CONFIG_DIR' if receipt['agent'] == 'claude' else 'CODEX_HOME'] = str(profile)
            env.update({'DISABLE_AUTOUPDATER': '1', 'DISABLE_TELEMETRY': '1',
                        'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'})
            executable = resolve_agent_binary(receipt['agent_binary'])
            argv = [str(executable), *(['auth', 'login'] if receipt['agent'] == 'claude' else ['login'])]
            # Auth stays on this console. Never capture, persist or relay it.
            completed = (runner or subprocess.run)(argv, cwd=scratch.path, env=env,
                                                   shell=False, check=False)
            if completed.returncode != 0:
                raise StateError('agent-login-failed')
        finally:
            if scratch.cleanup() is not True:
                raise StateError('cleanup-unconfirmed')
        qualify_install(owner, receipt, executable=receipt['agent_binary'], platform=receipt['platform'])
        return status(owner)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Manage the selected Ask relay installation.')
    parser.add_argument('action', choices=('install', 'remove', 'status', 'check', 'pair', 'unpair', 'selftest', 'login'))
    parser.add_argument('--relay-id')
    parser.add_argument('--agent', choices=('codex', 'claude'))
    parser.add_argument('--agent-binary')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--relay-transport', choices=('loopback', 'tailnet'))
    parser.add_argument('--relay-port', type=int)
    parser.add_argument('--tailscale-binary')
    parser.add_argument('--no-autostart', action='store_true')
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.action == 'install':
            if not args.agent:
                raise StateError('agent-and-binary-required')
            args.agent_binary = args.agent_binary or shutil.which(args.agent)
            if not args.agent_binary or not binary_found(args.agent_binary):
                raise StateError('agent-and-binary-required')
            ident = args.relay_id or _select_existing(args.agent, for_install=True)
        else:
            if args.agent_binary:
                raise StateError('existing-install-selector-required')
            ident = args.relay_id
            if ident is None and args.agent:
                ident = _select_existing(args.agent)
        if not valid_id(ident):
            raise StateError('invalid-install-id')
        if args.action == 'pair' and args.json:
            raise StateError('pair-console-required')
        if args.action == 'login' and (args.json or not args.agent):
            raise StateError('login-console-and-agent-required')
        root = state_base() / ident
        if args.live and args.action != 'selftest':
            raise StateError('live-selftest-only')
        if args.action != 'install' and args.agent:
            selected = validate_receipt(OwnedRoot(root), allow_missing_owned=args.action == 'remove')
            if selected['agent'] != args.agent:
                raise StateError('install-binding-mismatch')
        route_api = None
        if args.tailscale_binary:
            from .platform import ServeAPI
            route_api = ServeAPI(args.tailscale_binary)
        if args.action == 'install':
            owner = OwnedRoot(root)
            owner.create()
            if not (owner.root / 'owner.json').exists():
                if next(owner.root.iterdir(), None) is not None:
                    raise StateError('foreign-state-root')
                owner.write_json('owner.json', {'schema': OWNER_SCHEMA, 'install_id': ident})
            owner.validate(ident)
            with owner.lock():
                _record_attempt(owner, ident, args.agent, args.agent_binary)
                saved = _retry_receipt(owner).get('config', {}) if owner.path('install.json').exists() else {}
                if not saved and owner.path('config.json').exists():
                    saved = owner.read_json('config.json')
            _resume_removal(owner, platform_api=None if args.no_autostart else 'native', route_api=route_api)
            from .provider import install_provider
            with owner.lock():
                provider_python = install_provider(owner)
            result = install(owner, args.agent, args.agent_binary, python_binary=provider_python, install_id=ident,
                transport=args.relay_transport or saved.get('transport', 'loopback'),
                port=args.relay_port if args.relay_port is not None else saved.get('port', 7452),
                platform_api=None if args.no_autostart else 'native', route_api=route_api)
        elif args.action == 'remove':
            result = remove(root, platform_api='native', route_api=route_api)
        else:
            if args.relay_transport or args.relay_port not in (None, 7452) or args.no_autostart or args.tailscale_binary:
                raise StateError('install-options-not-permitted')
            owner = OwnedRoot(root)
            receipt = validate_receipt(owner)
            if args.agent and receipt['agent'] != args.agent:
                raise StateError('install-binding-mismatch')
            if args.action in ('pair', 'selftest') and (os.path.normcase(os.path.abspath(sys.executable)) !=
                    os.path.normcase(os.path.abspath(receipt['python_binary']))):
                # Pairing needs the owned provider, with no user site or Python
                # environment inheritance. Forward only public selector flags.
                package = owner.path('packages/' + receipt['release_id'])
                code = 'import sys; sys.path.insert(0,sys.argv.pop(1)); from deep32_relay.connector import main; raise SystemExit(main())'
                forwarded = [args.action, '--relay-id', ident]
                if args.agent: forwarded.extend(('--agent', args.agent))
                if args.json: forwarded.append('--json')
                if args.live: forwarded.append('--live')
                return subprocess.run([receipt['python_binary'], '-I', '-B', '-c', code, str(package), *forwarded], check=False).returncode
            if args.action == 'login':
                result = login(owner)
            elif args.action == 'selftest':
                from .engine import qualify_install, live_selftest
                if args.live:
                    print('The live relay self-test uses your model allowance.', file=sys.stderr, flush=True)
                    qualification = live_selftest(owner, receipt, allow_model_usage=True)
                else:
                    qualification = qualify_install(owner, receipt, executable=receipt['agent_binary'], platform=receipt['platform'])
                result = status(owner)
                result['selftest'] = qualification
            else:
                result = globals()[args.action](owner)
        if args.json:
            print(json_bytes(result).decode('ascii'), end='')
        else:
            if args.action == 'pair':
                print('Relay pairing is committed.' if result['paired'] else
                      'Relay pairing did not complete.')
                return 0 if result['paired'] else 10
            if args.action == 'unpair':
                print('Relay is no longer paired with a Workstation.' if result['changed'] else
                      'Relay was not paired with a Workstation; nothing changed.')
                return 0
            print('Relay uses ' + result['agent'] + ' with profile ' + result['profile_alias'] + '.')
            print('Relay state is ' + result['state'] + '; install ID ' + result['install_id'] + '.')
            if result.get('advertised'):
                print('Ask offers models ' + ', '.join(result['advertised']['models']) + ' and efforts ' +
                      ', '.join(result['advertised']['efforts']) + '; the first of each is the default.')
            if args.action == 'install':
                print('Ask uses the selected agent profile\'s model allowance.')
                print('Relay profile requires its own agent sign-in.')
            if result.get('removal', {}).get('leftovers'):
                print('Recorded relay objects remain; retain the removal receipt for review.')
                provider_code = result['removal'].get('provider', {}).get('code')
                if provider_code:
                    print('Relay provider removal code: ' + _local_code(provider_code) + '.', file=sys.stderr)
            elif not result.get('ready', False) and args.action != 'remove':
                print('Complete the reported relay checks before using Ask.')
            reason = result.get('qualification', {}).get('reason')
            if reason and reason != 'none':
                print('Relay check code: ' + _local_code(reason) + '.', file=sys.stderr)
                if reason == 'claude-sign-in-required':
                    print('Sign in with connect --relay login --agent claude, then retry install.', file=sys.stderr)
        failed = ((args.action == 'check' and not result['ready']) or
                  (args.action == 'remove' and not result.get('removal', {}).get('complete', True)) or
                  (args.action == 'selftest' and args.live and result.get('selftest', {}).get('state') != 'passed') or
                  (args.action in ('install', 'selftest', 'login') and result.get('qualification', {}).get('state') != 'qualified'))
        return 10 if failed else 0
    except (StateError, OSError, ValueError, KeyError, TypeError, ProtocolError) as exc:
        # Never print a path, native exception or untrusted field as a diagnostic.
        if args.json:
            print(json_bytes({'ready': False, 'reason': 'relay-operation-unavailable',
                              'code': _exception_code(exc)}).decode('ascii'), end='')
        else:
            code = _exception_code(exc)
            print('Relay operation is unavailable (code: ' + code + '); preserve the selected installation for review.', file=sys.stderr)
            if code == 'tailscale-operator-required':
                print('Run this one-time Linux fix: sudo tailscale set --operator=$USER', file=sys.stderr)
            if code == 'relay-stop-required':
                print('Stop the relay service, then run this command again.', file=sys.stderr)
            if code in ('peer-already-paired', 'peer-revocation-required'):
                print('This relay is still paired with ' +
                      ('a Workstation, and that Workstation connected while pairing was open.'
                       if code == 'peer-already-paired' else 'another Workstation.'), file=sys.stderr)
                print('To pair a different Workstation, stop the Ask link on the old one (d32-chatctl unpair there) '
                      'or turn it off, then run connect --relay unpair --relay-id ' + ident +
                      (' --agent ' + args.agent if args.agent else '') + ' and pair again.', file=sys.stderr)
            if code == 'remove-before-install':
                print('Remove this build with connect --relay remove --relay-id ' + ident +
                      ', then run connect --relay install --agent ' + args.agent + ' --relay-id ' + ident +
                      '. Identity, pins and the relay profile sign-in are kept.', file=sys.stderr)
                print('Keep the required transport and Tailscale executable options on these commands.', file=sys.stderr)
        return 10


def _local_code(value):
    import re
    return value if isinstance(value, str) and re.fullmatch('[a-z][a-z0-9-]{0,79}', value) else 'relay-operation-unavailable'


def _exception_code(exc):
    if isinstance(exc, StateError):
        return _local_code(exc.reason)
    if isinstance(exc, FileNotFoundError):
        return 'relay-file-missing'
    if isinstance(exc, PermissionError):
        return 'local-permission-denied'
    if isinstance(exc, FileExistsError):
        return 'relay-file-exists'
    if isinstance(exc, NotADirectoryError):
        return 'local-path-not-directory'
    if isinstance(exc, IsADirectoryError):
        return 'local-path-is-directory'
    if isinstance(exc, TimeoutError):
        return 'local-timeout'
    if isinstance(exc, OSError) and exc.errno is not None:
        return 'local-io-error'
    return 'relay-operation-unavailable'
