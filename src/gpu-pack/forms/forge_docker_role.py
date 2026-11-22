#!/usr/bin/env python3
"""Portable Forge lifecycle: models, conversion and building for a Workstation.

The host owns Docker and Tailscale, including journalled tailnet-only model and
pairing routes. Engine and MCP gateway remain on the Workstation. Real host and
media qualification is separate from these source-level lifecycle checks.
"""
import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

_spec = importlib.util.spec_from_file_location('forge_docker_common', Path(__file__).with_name('docker_role.py'))
common = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(common)
SCHEMA = 'deep32-forge-docker-role/1'
SERVICES = ('embeddings', 'reranker', 'door')


def capability_roles(images, *, legacy=False):
    """Validate a role set supplied by the verified image selector.

    Public capability-media parsing belongs to the release contract producer.
    The legacy converter-only pack remains an explicit compatibility path.
    """
    core = {'TEI_IMAGE', 'RERANKER_IMAGE', 'DOOR_IMAGE'}
    ingestion = {'CONVERTER_IMAGE', 'BUILDER_IMAGE', 'QDRANT_IMAGE'}
    names = set(images)
    if (not core <= names or names - core - ingestion or
            names & ingestion not in (set(), ingestion) and
            not (legacy and names & ingestion == {'CONVERTER_IMAGE'})):
        # ingestion capability refusal, for lead review.
        raise ValueError('Use the exact verified search core and complete ingestion add-on role set.')
    return names


def prepare_config(kit, payload, root, images, config, settings=None):
    """Render the existing Forge services with host-local, non-secret paths."""
    paths = {name: common.absolute_path(value) for name, value in
             (('kit', kit), ('payload', payload), ('root', root))}
    windows = bool(re.match(r'^[A-Za-z]:', str(paths['root'])))
    if any(bool(re.match(r'^[A-Za-z]:', str(value))) != windows for value in paths.values()):
        raise ValueError('Use paths belonging to the same Docker host.')
    names = capability_roles(images, legacy=True)
    if set(images) != names or any(re.fullmatch(r'sha256:[0-9a-f]{64}', images[key]) is None for key in names):
        raise ValueError('Load and verify all selected Forge image archives before starting.')
    revision = config.get('models', {}).get('tei', {}).get('revision', '')
    dtype = config.get('runtime', {}).get('reranker_dtype', '')
    if re.fullmatch(r'[0-9a-f]{40}', revision) is None or dtype not in ('float16', 'float32'):
        raise ValueError('Use the signed embedding revision and reranker dtype.')

    def bind(source, target, readonly=False):
        return dict(type='bind', source=Path(source).as_posix(), target=target,
                    read_only=readonly, bind={'create_host_path': False})

    def service(image, **values):
        return dict(image=images[image], pull_policy='never',
                    security_opt=['no-new-privileges:true'], cap_drop=['ALL'], **values)

    publication = common.module('forge_compose_ports', Path(__file__).with_name('forge_publication.py'))
    selected = publication.ports(settings or {})
    services = {
        'embeddings': service('TEI_IMAGE', restart='unless-stopped',
            command=['--model-id', 'jinaai/jina-embeddings-v5-text-small-retrieval', '--revision', revision,
                     '--served-model-name', 'jina-embeddings-v5-text-small', '--dtype', 'float32',
                     '--pooling', 'last-token', '--max-batch-tokens', '4096', '--max-client-batch-size', '8'],
            environment={'HF_HUB_OFFLINE': '1', 'USE_FLASH_ATTENTION': 'False'},
            volumes=[bind(paths['payload'] / 'models/tei', '/data', True)],
            networks=['models'], gpus='all', mem_limit='8g'),
        'reranker': service('RERANKER_IMAGE', restart='unless-stopped',
            environment={'HF_HOME': '/models', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                         'RERANKER_DTYPE': dtype, 'RERANKER_DEVICE': 'cuda'},
            volumes=[bind(paths['payload'] / 'models/reranker', '/models/hub', True)],
            networks=['models'], gpus='all', mem_limit='9g', shm_size='1g'),
        'door': service('DOOR_IMAGE', restart='unless-stopped', command=['python', '/app/door.py'],
            ports=['127.0.0.1:' + str(selected['embeddings']) + ':8081',
                   '127.0.0.1:' + str(selected['reranker']) + ':8082'], secrets=['forge-token'],
            networks=['models', 'access'], read_only=True,
            volumes=[bind(paths['kit'] / 'containers/full/health.py', '/opt/deep32/health.py', True)],
            healthcheck=dict(test=['CMD', 'python', '-I', '-B', '/opt/deep32/health.py', 'door'],
                             interval='10s', timeout='12s', retries=12, start_period='30s')),
    }
    if 'CONVERTER_IMAGE' in images:
        services['converter'] = service('CONVERTER_IMAGE', profiles=['conversion'], network_mode='none', gpus='all',
            environment={'MINERU_HOME': '/models', 'MINERU_MODEL_SOURCE': 'local',
                         'MINERU_MODEL_SMALL_BACKEND': 'torch', 'MINERU_MODEL_VLM_ENGINE': 'vllm',
                         'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'},
            volumes=[bind(paths['payload'] / 'models/mineru-home', '/models', True),
                     dict(type='tmpfs', target='/models/models/.locks', tmpfs=dict(size=1048576, mode=0o1777)),
                     bind(paths['root'] / 'jobs', '/jobs')], shm_size='2g')
    if 'BUILDER_IMAGE' in images:
        services['qdrant'] = service('QDRANT_IMAGE', profiles=['builder'], restart='unless-stopped',
            user='10032:10032', environment={'QDRANT__TELEMETRY_DISABLED': 'true',
                'QDRANT__STORAGE__SNAPSHOTS_PATH': '/qdrant/storage/snapshots'},
            volumes=[bind(paths['root'] / 'builder/qdrant', '/qdrant/storage')], networks=['models'])
        services['builder'] = service('BUILDER_IMAGE', profiles=['builder'], user='10032:10032',
            environment={'RAG_QDRANT_URL': 'http://qdrant:6333',
                         'RAG_EMBEDDING_URL': 'http://embeddings:80/v1/embeddings',
                         'RAG_EMBEDDING_MODEL': 'jina-embeddings-v5-text-small',
                         'RAG_EMBEDDING_REVISION': revision, 'RAG_RERANK_URL': 'http://reranker:8082/v1/rerank',
                         'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'PYTHONHASHSEED': '0'},
            volumes=[bind(paths['root'] / 'builder', '/var/lib/deep32-forge')], networks=['models'])
    return dict(name='deep32-forge', services=services,
                secrets={'forge-token': {'file': (paths['root'] / 'private/forge-token').as_posix()}},
                networks={'models': {'internal': True}, 'access': {}})


def identities(root, kit, committed=False, key_factory=None):
    """Retain the same protected Forge authority; never enrol a local engine."""
    enrol = common.module('forge_identity_helpers', kit / 'containers/full/local_enrol.py')
    files, tokens, _ = enrol._pairing(kit)
    private = root / 'private'
    common.safe_path(private)
    if private.exists():
        enrol._protected(private, True)
    common.private_directory(private)
    members = ('forge-token', 'kb-private.pem', 'kb-public.pem')
    for name in (*members, 'kb-private.new', 'kb-public.new', 'forge-identity.json'):
        path = private / name
        common.safe_path(path)
        if path.exists():
            enrol._protected(path)
    marker = private / 'forge-identity.json'
    if (committed or marker.exists()) and any(not (private / name).is_file() for name in members):
        raise common.Refusal('Restore all Forge identity files from this installation before restarting.')
    token, key, public = (private / name for name in members)
    if public.exists() and not key.exists() or not token.exists() and key.exists():
        raise common.Refusal('Restore the incomplete Forge identity; existing credentials will not be regenerated.')
    if not token.exists():
        tokens.write_secret(token, tokens.make_token())
    tokens.read_token(token)
    (key_factory or enrol._keys)(private)
    tokens.read_kb_public(public)
    for path in (token, key, public):
        if os.name == 'nt':
            common.windows_security(path, verify=True)
        else:
            path.chmod(0o600)
        enrol._protected(path)
    expected = {'schema': 'deep32.forge-identity/1', 'files': {
        name: hashlib.sha256((private / name).read_bytes()).hexdigest() for name in members}}
    if marker.exists():
        recorded = common.read_json(marker)
        # The knowledge-base key pair is the Forge's lasting authority and never changes. The bearer is meant to
        # rotate: unpair rotates it when a Workstation is revoked (INTERFACES.md, Removal), and read_token above
        # has checked the new one. Record its digest so later starts compare against it.
        if (not isinstance(recorded, dict) or recorded.get('schema') != expected['schema']
                or not isinstance(recorded.get('files'), dict) or set(recorded['files']) != set(members)
                or any(recorded['files'][name] != expected['files'][name] for name in ('kb-private.pem', 'kb-public.pem'))):
            raise common.Refusal('Restore the original Forge identity files before restarting.')
        if recorded['files']['forge-token'] != expected['files']['forge-token']:
            common.atomic_json(marker, expected)
    elif committed:
        raise common.Refusal('Restore the retained Forge identity record before restarting.')
    else:
        common.atomic_json(marker, expected)


class Lifecycle(common.Lifecycle):
    def publication_module(self):
        return common.module('forge_host_publication', Path(__file__).with_name('forge_publication.py'))

    def publication_controller(self):
        return self.publication_module().Publication(common, self.root, self.platform, self.call)

    def owned_local_ports(self, data, settings):
        """Accept busy model ports only from this release's inspected door."""
        if not data:
            return ()
        raw = self.compose(data, 'ps', '--quiet', 'door', timeout=30).strip()
        if not raw:
            return ()
        if re.fullmatch('[0-9a-f]{12,64}', raw) is None:
            raise common.Refusal('Restore the recorded Forge door container before retrying its local port check.')
        text = self.call(['docker', 'inspect', raw], 'owned Forge door bindings',
                         'restore the recorded Compose container and retry.', 30)
        try:
            values = json.loads(text)
            row = values[0]
            labels = row['Config']['Labels']
            # During an upgrade the running door still belongs to the previous release of this
            # installation, so accept any of this installation's own release folders.
            config = common.absolute_path(labels['com.docker.compose.project.config_files'])
            if (len(values) != 1 or labels['com.docker.compose.project'] != 'deep32-' + data['install_id'] or
                    labels['com.docker.compose.service'] != 'door' or config.name != 'compose.json' or
                    config.parent.parent != self.root / 'releases' or
                    re.fullmatch('[0-9a-f]{64}', config.parent.name) is None):
                raise ValueError()
            if not row['State']['Running']:
                return ()
            bindings = row['HostConfig']['PortBindings']
            old = self.publication_module().ports(data['settings'])
            expected = {str(port) + '/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(old[name])}]
                        for name, port in (('embeddings', 8081), ('reranker', 8082))}
            if bindings != expected:
                raise ValueError()
            return tuple(old[name] for name in ('embeddings', 'reranker')
                         if old[name] == self.publication_module().ports(settings)[name])
        except (ValueError, KeyError, IndexError, TypeError):
            raise common.Refusal('The busy Forge local ports are not owned by this installation; change C7 forge_ports.') from None

    def preflight_publication(self, install_id, dns, settings, old=None):
        if settings.get('forge_accept_ct') is not True:
            raise common.Refusal('HTTPS certificates publish this node name in Certificate Transparency; set C7 forge_accept_ct to true to agree.')
        self.publication_controller().preflight(install_id, dns, settings)
        try:
            self.publication_module().check_local(settings, owned=self.owned_local_ports(old, settings))
        except ValueError as exc:
            raise common.Refusal(str(exc)) from None

    def job_guard(self):
        for relative, code, message in (
                ('conversion-recovery.json', 3, 'Run the recorded Forge conversion recovery before starting or upgrading this installation.'),
                ('status/gpu-hold', 4, 'Resolve the retained Forge GPU hold before starting or upgrading this installation.')):
            marker = self.root / relative
            common.safe_path(marker)
            if marker.exists():
                raise common.Refusal(message, code)

    def settings(self, value):
        renderer = self.kit / 'autoinstall/render-settings.py'
        if not renderer.exists():
            renderer = self.kit / 'forms/render-settings.py'
        result = common.module('forge_c7_renderer', renderer).validate(value)
        if result.get('transport', 'tailnet') != 'tailnet' or result.get('publish', 'serve-https') != 'serve-https':
            raise common.Refusal('Select tailnet transport and serve-https publication for the Docker Forge.')
        return result

    def state(self):
        try:
            if os.name == 'nt':
                common.windows_security(self.root, verify=True, directory=True)
                common.windows_security(self.record, verify=True)
            data = common.read_json(self.record)
            allowed = {'schema', 'install_id', 'platform', 'release', 'stage', 'settings'}
            if data.get('platform') == 'windows':
                allowed.add('docker_storage_path')
            if (set(data) != allowed or data['schema'] != SCHEMA or
                    data['platform'] not in ('linux', 'windows') or
                    re.fullmatch('[0-9a-f]{16}', data['install_id']) is None or
                    re.fullmatch('[0-9a-f]{64}', data['release']) is None or
                    data['stage'] not in ('prepared', 'local-ready-publication-required', 'published-pairing-required', 'stopped')):
                raise ValueError('invalid record')
            self.platform = data['platform']
            if self.platform == 'windows':
                saved = common.absolute_path(data['docker_storage_path'])
                if self.docker_storage_path is not None and common.absolute_path(self.docker_storage_path) != saved:
                    raise common.Refusal('Keep the recorded Docker Desktop backing directory for this installation.')
                self.docker_storage_path = saved
            self.settings(data['settings'])
            return data
        except (OSError, ValueError, TypeError, KeyError):
            raise common.Refusal('No valid Forge installation was found; use its original dedicated data directory.') from None

    def image_roles(self, selection, config, rows=None):
        wanted = {'tei-' + selection, 'reranker', 'door'}
        # The ingestion add-on (the converter, and the builder with Qdrant) is optional (DR-64): a Forge installed from
        # the core and its embedder serves search, and forge_jobs refuses conversion and build jobs. The verified
        # payload index says which was installed; without one, keep the full set.
        if rows is None or any(isinstance(row, dict) and row.get('role') == 'converter' for row in rows):
            wanted.add('converter')
            if 'builder' in config:
                wanted.update(('builder', 'qdrant'))
        return wanted

    def storage_image(self, images):
        return images['DOOR_IMAGE']

    def host(self, payload, platform, selected_roles=None, installed_inventory=None):
        self.platform = platform
        check = common.module('forge_host_requirements', self.kit / 'forms/host-requirements.py')
        options = {}
        if selected_roles is not None:
            options['selected_roles'] = selected_roles
        if installed_inventory is not None:
            options['installed_inventory'] = installed_inventory
        if platform == 'windows':
            local = os.environ.get('LOCALAPPDATA')
            if not self.docker_storage_path and not local:
                raise common.Refusal('Select the existing local Docker Desktop backing directory with --docker-storage-path.')
            self.docker_storage_path = common.absolute_path(self.docker_storage_path or str(Path(local) / 'Docker'))
            options.update(host_platform='windows', docker_storage_path=self.docker_storage_path)
        try:
            result = check.check('docker', payload, self.kit, check_loopback_ports=False,
                storage_target=self.root, run=lambda argv, **kw: subprocess.CompletedProcess(argv, 0,
                    self.call(argv, 'host prerequisites', 'restore the reported prerequisite and retry.', 30), ''), **options)
            self.storage_required = result.get('free_bytes_required') if isinstance(result, dict) else None
        except ValueError:
            raise common.Refusal('Install the complete qualified host prerequisites and retry this action.') from None
        text = self.call(['tailscale.exe' if platform == 'windows' else 'tailscale', 'status', '--json'],
                         'host Tailscale sign-in', 'complete host sign-in and retry.', 30)
        try:
            status = json.loads(text)
            dns = status['Self']['DNSName'].rstrip('.')
            if status['BackendState'] != 'Running' or re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,250}\.ts\.net', dns) is None:
                raise ValueError()
        except (ValueError, KeyError, TypeError, AttributeError):
            raise common.Refusal('Complete host Tailscale sign-in with its qualified DNS name before retrying.') from None
        self.call(['openssl', 'version'], 'local Forge identity tools', 'install the qualified OpenSSL host tool and retry.', 30)
        return dns

    def publication(self, install_id, dns, settings=None):
        value = common.publication_request(install_id, dns)
        value['routes'] = [row for row in value['routes'] if row['path'] != '/mcp']
        ports = self.publication_module().ports(settings or {})
        for row in value['routes']:
            name = 'embeddings' if row['https_port'] == 443 else 'reranker'
            row['https_port'] = ports[name + '_https']
            row['upstream'] = 'http://127.0.0.1:' + str(ports[name])
        value['model_endpoints'] = self.publication_module().endpoints(dns, settings or {})
        value['pairing_port'] = ports['pairing_tcp']
        return value

    def prepare(self, payload, settings, platform='linux', upgrade=False, retain_runtime=True):
        self.job_guard()
        if platform not in ('linux', 'windows'):
            raise common.Refusal('Use Linux Docker or Windows Docker Desktop for the Forge role.')
        if self.root.exists() and not self.record.exists() and any(self.root.iterdir()) and not (self.root / 'owner.json').is_file():
            raise common.Refusal('Choose an empty dedicated directory or the recorded Forge installation directory.')
        payload = common.absolute_path(payload)
        common.safe_path(self.kit)
        if payload == self.root or payload.is_relative_to(self.root) or self.root.is_relative_to(payload):
            raise common.Refusal('Choose separate installation and GPU payload paths.')
        if self.kit == self.root or self.root.is_relative_to(self.kit):
            raise common.Refusal('Choose separate installation and runtime kit paths.')
        if self.kit.is_relative_to(self.root) and re.fullmatch(
                r'releases/[0-9a-f]{64}/kit', self.kit.relative_to(self.root).as_posix()) is None:
            raise common.Refusal('Use the mounted runtime kit or this installation\'s exact retained release kit.')
        validated = self.settings(common.read_json(settings))
        provision = common.module('forge_provision', self.kit / 'forms/provision.py')
        with common.progress('complete payload verification', self.emit):
            identity = provision.verified_payload(payload)
        config = common.read_json(self.kit / 'pins.json')
        profile = common.module('forge_release_profile', self.kit / 'build/release_profile.py')
        # Full-role pins are unrelated to this Workstation-owned deployment.
        forge_pins = {key: value for key, value in config.items() if key != 'full'}
        forge_pins['runtime'] = {key: value for key, value in config.get('runtime', {}).items()
                                 if key not in ('full_image_id', 'model_bridge_image_id')}
        if profile.unresolved(forge_pins):
            raise common.Refusal('Use rebuilt signed disc 3 with all Forge input pins resolved.')
        old = self.state() if self.record.exists() else None
        if old and old['platform'] != platform:
            raise common.Refusal('Keep the recorded platform or choose a new empty installation directory.')
        if old and self.publication_module().ports(old['settings']) != self.publication_module().ports(validated):
            peers = self.root / 'pairing/peers/forge-joined'
            common.safe_path(peers)
            if peers.exists() and any(peers.iterdir()):
                raise common.Refusal('Revoke and unpair this Forge on both nodes before changing C7 forge_ports, then pair again to hand over the new URLs.')
        release_id = hashlib.sha256((identity + provision.digest(self.kit / 'pins.json')).encode('ascii')).hexdigest()
        if old and old['release'] != release_id and not upgrade:
            raise common.Refusal('Different media are installed; use upgrade with the verified new media.')
        install_id = old['install_id'] if old else hashlib.sha256(str(self.root).encode('utf-8')).hexdigest()[:16]
        release = self.root / 'releases' / release_id
        owner = {'schema': SCHEMA, 'install_id': install_id}
        if not old and (self.root / 'owner.json').exists() and common.read_json(self.root / 'owner.json') != owner:
            raise common.Refusal('Restore the owned Forge marker or choose a new empty data directory.')
        dns = self.host(payload, platform)
        self.preflight_publication(install_id, dns, validated, old)
        images = self.images(payload, config)
        self.daemon_storage(images, platform)
        runtime_payload = release / 'payload' if retain_runtime else payload
        compose = prepare_config(release / 'kit', runtime_payload, self.root, images, config, validated)
        common.private_directory(self.root)
        with common.installation_lock(self.root):
            self.job_guard()
            if self.record.exists() and self.state() != old:
                raise common.Refusal('The installation changed during preflight; retry the same action.', 11)
            common.atomic_json(self.root / 'owner.json', owner)
            for name in ('config', 'jobs', 'builder', 'status', 'logs'):
                common.private_directory(self.root / name)
            if 'builder' in config:
                builder = common.module('forge_builder_state', self.kit / 'containers/builder-state.py')
                if os.name == 'posix':
                    builder.prepare(self.root / 'builder')
                else:
                    for name in builder.DIRECTORIES:
                        common.private_directory(self.root / 'builder' / name)
            common.private_directory(release)
            provision.copy_kit(self.kit, release / 'kit')
            if retain_runtime:
                retained = common.module('forge_install_runtime', self.kit / 'forms/installed_runtime.py')
                prefixes = ['models/tei/', 'models/reranker/']
                if 'CONVERTER_IMAGE' in images:
                    prefixes.append('models/mineru-home/')
                retained.prepare(payload, self.kit, release, images, prefixes,
                                 selection=getattr(self, 'selected_class', None))
            common.atomic_json(release / 'media.json', {'payload': str(payload), 'identity': identity})
            common.atomic_json(release / 'compose.json', compose)
            common.atomic_json(self.root / 'config/settings.json', validated)
            common.atomic_json(self.root / 'config/publication-request.json', self.publication(install_id, dns, validated))
            identities(self.root, self.kit, committed=old is not None)
            data = dict(schema=SCHEMA, install_id=install_id, platform=platform, release=release_id,
                        stage='prepared', settings=validated)
            if platform == 'windows':
                data['docker_storage_path'] = str(self.docker_storage_path)
            if old and old['release'] != release_id:
                common.atomic_json(self.root / 'previous.json', old)
            common.atomic_json(self.record, data)
            common.startup_hook(self.root, release, data.get('docker_storage_path'), adapter='forge_docker_role.py')
            self.start(data)
        return data

    def runtime_inputs(self, data, allow_load=False):
        """Verify this release's installed bytes or its explicit legacy media path."""
        release = self.root / 'releases' / data['release']
        self.kit = release / 'kit'
        common.safe_path(release / 'installed-runtime')
        receipt = release / 'installed-runtime/receipt.json'
        common.safe_path(receipt)
        if receipt.exists():
            retained = common.module('forge_verify_runtime', self.kit / 'forms/installed_runtime.py')
            installed = retained.verify(release, expected_release=data['release'])
            config = common.read_json(self.kit / 'pins.json')
            images = installed['images']
            capability_roles(images, legacy=True)
            self.installed_inventory = release / 'installed-runtime/PAYLOAD.json'
            identities(self.root, self.kit, committed=True)
            self.verify_installed_images(installed)
            return release / 'payload', config, images
        # Refuse downgrade from an interrupted/tampered retained installation.
        if (release / 'installed-runtime').exists() or (release / 'payload').exists():
            raise common.Refusal('Restore the complete retained runtime receipt using the same authenticated media.')
        self.installed_inventory = None
        media = common.read_json(release / 'media.json')
        payload = common.absolute_path(media['payload'])
        provision = common.module('forge_start_provision', self.kit / 'forms/provision.py')
        with common.progress('recorded payload verification', self.emit):
            identity = provision.verified_payload(payload)
        if (identity != media['identity'] or hashlib.sha256(
                (identity + provision.digest(self.kit / 'pins.json')).encode('ascii')).hexdigest() != data['release']):
            raise common.Refusal('Restore the recorded verified GPU media and retained runtime pins before restarting.')
        config = common.read_json(self.kit / 'pins.json')
        identities(self.root, self.kit, committed=True)
        return payload, config, self.images(payload, config, allow_load=allow_load)

    def verify_installed_images(self, installed):
        """Inspect immutable local image IDs; repair requires authenticated media."""
        inputs = common.module('forge_retained_gpu', self.kit / 'firstboot/installed-inputs.py')
        caps = self.call(['nvidia-smi.exe' if self.platform == 'windows' else 'nvidia-smi',
                          '--query-gpu=compute_cap', '--format=csv,noheader'],
                         'installed GPU class', 'restore the recorded supported NVIDIA GPU class.', 30)
        try:
            classes = {inputs.CAPABILITIES[line.strip()] for line in caps.splitlines() if line.strip()}
            if len(classes) != 1 or classes.pop() != installed['selection']:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise common.Refusal('Restore the recorded supported GPU class before starting this installed runtime.') from None
        for image in sorted(set(installed['images'].values())):
            raw = self.call(['docker', 'image', 'inspect', image], 'installed image identity',
                            'restore the missing image from the same authenticated media and prepare again.', 30)
            try:
                rows = json.loads(raw)
                if (len(rows) != 1 or rows[0]['Id'] != image or rows[0]['Os'] != 'linux'
                        or rows[0]['Architecture'] != 'amd64'):
                    raise ValueError()
            except (ValueError, TypeError, KeyError, IndexError):
                raise common.Refusal('Restore the exact missing installed image from the same authenticated media.') from None

    def start(self, data=None):
        self.job_guard()
        data = data or self.state()
        self.platform = data['platform']
        release = self.root / 'releases' / data['release']
        payload, config, images = self.runtime_inputs(data, allow_load=False)
        dns = self.host(payload, data['platform'], selected_roles=images,
                        installed_inventory=self.installed_inventory)
        self.preflight_publication(data['install_id'], dns, data['settings'], data)
        self.daemon_storage(images, data['platform'])
        common.atomic_json(release / 'compose.json', prepare_config(self.kit, payload, self.root, images, config, data['settings']))
        common.atomic_json(self.root / 'config/publication-request.json', self.publication(data['install_id'], dns, data['settings']))
        self.compose(data, 'config', '--quiet')
        self.compose(data, 'up', '-d', '--pull', 'never', '--no-build', '--force-recreate', '--wait',
                     '--wait-timeout', '600', *SERVICES, timeout=630)
        self.publication_controller().publish(data['install_id'], dns, data['settings'])
        request = self.publication(data['install_id'], dns, data['settings'])
        self.emit('Forge embeddings URL: ' + request['model_endpoints']['embeddings'] +
                  '; reranker URL: ' + request['model_endpoints']['reranker'] +
                  '; published pairing TCP port: ' + str(request['pairing_port']) + '.')
        self.pair_local(data)
        data['stage'] = 'published-pairing-required'
        common.atomic_json(self.record, data)
        self.emit('Forge model and pairing routes are published on the owning host; complete Workstation pairing and authenticated model attestation before remote use.')

    def stop(self):
        data = self.state()
        self.publication_controller().remove()
        self.compose(data, 'stop', '--timeout', '30')
        data['stage'] = 'stopped'
        common.atomic_json(self.record, data)
        self.emit('Forge stopped and its owned host routes were removed; data and pairing identity are retained.')

    def uninstall(self):
        # Deliberately retain data/keys and the journal for review and revocation.
        self.stop()
        for name in ('startup-hook-v1.sh', 'startup-hook-v1.ps1'):
            path = self.root / name
            common.safe_path(path)
            if path.exists():
                path.unlink()
        self.emit('The Forge launcher was removed; retained private data can be removed after Workstation revocation.')

    def pair_local(self, data):
        """Preserve Linux's bounded local C8 caller and its protected C6 code."""
        if data['platform'] != 'linux':
            self.emit('Local Forge pairing still requires the qualified protected Windows host-console adapter.')
            return
        c6 = common.module('forge_portable_c6', self.kit / 'firstboot/status.py')
        previous = sys.modules.get('status')
        sys.modules['status'] = c6
        try:
            firstboot = common.module('forge_portable_firstboot', self.kit / 'firstboot/firstboot.py')
        finally:
            if previous is None:
                sys.modules.pop('status', None)
            else:
                sys.modules['status'] = previous
        firstboot.KIT = self.kit
        firstboot.STATE = self.root
        firstboot.HELPER = self.kit / 'pairing/bin/pairing-helper'
        status = c6.Status(path=self.root / 'status/status')
        try:
            firstboot.pair(status, 'docker', data['settings'], self.root / 'private/forge-token',
                           self.root / 'private/kb-public.pem')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            raise common.Refusal('Complete the bounded local Forge pairing on its protected console, then resume the same installation after any lockout expires.') from None

    def resume(self):
        self.job_guard()
        return super().resume()

    def status(self):
        data = self.state()
        raw = self.compose(data, 'ps', '--all', '--format', 'json', timeout=30)
        try:
            rows = json.loads(raw) if raw.lstrip().startswith('[') else [json.loads(line) for line in raw.splitlines() if line.strip()]
            current = {row['Service']: row for row in rows if row.get('Service') in SERVICES}
            healthy = all(name in current and current[name].get('State') == 'running' and
                          (name != 'door' or current[name].get('Health') == 'healthy') for name in SERVICES)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise common.Refusal('Restore Docker Compose status output and retry the same status action.') from None
        publication = self.publication_controller()
        saved = publication.load()
        published = bool(saved and saved['phase'] == 'ready')
        if published:
            observed = publication.status()
            publication.check(observed, saved)
            published = all(publication.slice(observed, row) == publication.expected(saved['host'], row)
                            for row in saved['routes'])
        result = dict(schema=SCHEMA, install_id=data['install_id'], platform=data['platform'], stage=data['stage'],
                      local_services_healthy=healthy, publication='published' if published else 'not-published',
                      pairing='requires-workstation-enrolment', ready=False,
                      next='Complete Workstation pairing and authenticated model attestation.')
        self.emit(json.dumps(result, sort_keys=True))
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'start', 'resume', 'stop', 'status', 'upgrade', 'uninstall'))
    parser.add_argument('--platform', choices=('linux', 'windows'), default='windows' if os.name == 'nt' else 'linux')
    parser.add_argument('--data-root')
    parser.add_argument('--kit', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--payload')
    parser.add_argument('--settings', type=Path)
    parser.add_argument('--docker-storage-path')
    args = parser.parse_args(argv)
    if ((args.platform == 'linux' and (os.name != 'posix' or os.geteuid() != 0)) or
            (args.platform == 'windows' and (os.name != 'nt' or not ctypes.windll.shell32.IsUserAnAdmin()))):
        parser.exit(10, 'Run the Forge lifecycle as the installation administrator on its Docker host.\n')
    try:
        default = str(Path(os.environ.get('LOCALAPPDATA', '')) / 'Deep32') if os.name == 'nt' else '/opt/deep32'
        root = common.absolute_path(args.data_root or default)
        lifecycle = Lifecycle(root, args.kit, docker_storage_path=args.docker_storage_path, host_platform=args.platform)
        if args.action == 'status':
            lifecycle.status()
            return
        if args.action in ('prepare', 'upgrade'):
            if not all((args.payload, args.settings)):
                raise common.Refusal('Supply --payload and --settings from the matching verified GPU media.')
            lifecycle.prepare(args.payload, args.settings, args.platform, args.action == 'upgrade')
        else:
            lifecycle.state()
            with common.installation_lock(root):
                getattr(lifecycle, args.action)()
    except common.Refusal as exc:
        parser.exit(exc.code, str(exc) + '\n')
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        parser.exit(1, 'The Forge operation failed; restore its matching verified media and retry the same action.\n')


if __name__ == '__main__':
    main()
