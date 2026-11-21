"""Offline fixtures for resolve-pins.py: a fake web (registry, Hugging Face, apt repositories,
PyPI and the torch index, key sources), a fake host (docker, dpkg and friends) and throwaway
OpenPGP keys made by the real gpg inside the test's temporary folder.

Nothing here reaches the network: the resolver's Net rewrites every https://host/path to the
loopback server below (its --test-web hook), and docker/dpkg are answered by FakeHost.
"""
import base64
import contextlib
import gzip
import hashlib
import http.server
import importlib.util
import io
import json
import lzma
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1] / 'src' / 'build' / 'disc3-pins'
FAKEPIP = HERE / 'fakepip.py'


def find_kit():
    for candidate in (os.environ.get('D32_KIT'), HERE.parents[1] / 'src' / 'gpu-pack'):
        if candidate and (Path(candidate) / 'pins.json').is_file():
            return Path(candidate).resolve()
    raise SystemExit('kit not found: set D32_KIT to the kit folder (the one holding pins.json)')


KIT = find_kit()


def load_resolver():
    path = Path(os.environ.get('D32_RESOLVER') or ROOT / 'resolve-pins.py')
    spec = importlib.util.spec_from_file_location('resolve_pins', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


resolver = load_resolver()


def find_tool(name):
    found = shutil.which(name)
    if found:
        return found
    git = Path('C:/Program Files/Git/usr/bin') / (name + '.exe')
    return str(git) if git.is_file() else None


GPG, GPGV, GPGCONF = find_tool('gpg'), find_tool('gpgv'), find_tool('gpgconf')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def tool_path(value):
    text = Path(value).resolve().as_posix()
    if os.name == 'nt' and GPG and Path(GPG).parent.parent.name.lower() == 'usr' and text[1:3] == ':/':
        text = '/' + text[0].lower() + text[2:]
    return text


class Keys:
    """Throwaway signing keys, made in a temporary GNUPGHOME and removed afterwards."""

    def __init__(self, names):
        self.folder = Path(tempfile.mkdtemp(prefix='deep32-resolver-keys-'))
        self.home = self.folder / 'home'
        self.home.mkdir()
        os.chmod(self.home, 0o700)
        self.fpr = {}
        for name in names:
            self._gpg('--quick-generate-key', 'Test ' + name + ' <' + name.lower() + '@example.invalid>', 'ed25519', 'sign', 'never')
            listing = self._gpg('--with-colons', '--list-keys', name.lower() + '@example.invalid').decode()
            self.fpr[name] = [line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:')][0]

    def _gpg(self, *args, data=None):
        argv = [GPG, '--batch', '--yes', '--pinentry-mode', 'loopback', '--passphrase', '', '--homedir', tool_path(self.home)] + list(args)
        result = subprocess.run(argv, input=data, capture_output=True)
        if result.returncode:
            raise RuntimeError('gpg failed: ' + result.stderr.decode(errors='replace'))
        return result.stdout

    def binary(self, name):
        return self._gpg('--export', self.fpr[name])

    def armored(self, name):
        return self._gpg('--armor', '--export', self.fpr[name])

    def clearsign(self, name, data):
        return self._gpg('--local-user', self.fpr[name], '--clearsign', data=data)

    def detach(self, name, data):
        return self._gpg('--local-user', self.fpr[name], '--detach-sign', data=data)

    def close(self):
        if GPGCONF:
            subprocess.run([GPGCONF, '--homedir', tool_path(self.home), '--kill', 'all'], capture_output=True)
        shutil.rmtree(self.folder, ignore_errors=True)


class FakeWeb:
    def __init__(self):
        self.routes = {}
        self.requests = []
        world = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                host, _, rest = self.path[1:].partition('/')
                rest = '/' + rest
                world.requests.append((host, rest))
                route = world.routes.get((host, rest)) or world.routes.get((host, rest.split('?')[0], '*'))
                if route is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                body, status, headers, bearer = route
                if bearer and self.headers.get('Authorization') != 'Bearer ' + bearer[0]:
                    self.send_response(401)
                    self.send_header('WWW-Authenticate', bearer[1])
                    self.end_headers()
                    return
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_address[1])

    def add(self, url, body, status=200, headers=None, bearer=None, any_query=False):
        parts = urllib.parse.urlsplit(url)
        path = parts.path + ('?' + parts.query if parts.query else '')
        key = (parts.netloc, parts.path, '*') if any_query else (parts.netloc, path)
        if isinstance(body, str):
            body = body.encode('utf-8')
        elif not isinstance(body, bytes):
            body = json.dumps(body).encode('utf-8')
        self.routes[key] = (body, status, headers or {}, bearer)

    def remove(self, url):
        parts = urllib.parse.urlsplit(url)
        self.routes.pop((parts.netloc, parts.path + ('?' + parts.query if parts.query else '')), None)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


OCI_INDEX = 'application/vnd.oci.image.index.v1+json'
OCI_MANIFEST = 'application/vnd.oci.image.manifest.v1+json'
SNAPSHOT = '20261101T000000Z'
TEI_TAGS = {'ada': '89-1.9', 'ampere80': '1.9', 'ampere86': '86-1.9', 'blackwell100': '100-1.9',
            'blackwell120': '120-1.9', 'cpu': 'cpu-1.9', 'hopper': 'hopper-1.9', 'turing': 'turing-1.9'}
KERNEL = '6.8.0-149'


class World:
    """One complete fake upstream. Knobs switch single defects on for the refusal tests."""

    def __init__(self, keys, folder, **knobs):
        self.keys, self.tmp, self.knobs = keys, Path(folder), knobs
        self.web = FakeWeb()
        self.out = self.tmp / 'out'
        self.commands, self.pulls = [], []
        self.container_python = '3.12.14'
        self.template = json.loads((KIT / 'pins.json').read_text(encoding='ascii'))
        # Existing regression cases exercise the bare-metal families explicitly,
        # even when the shipping register selects the pack. New pack tests use
        # their own in-memory apt and lead-input fixtures.
        self.template['release_profile'] = 'iso'
        self.template = json.loads(json.dumps(self.template).replace('deferred: v1.1 ISO form', 'TO-PIN'))
        self.template_path = self.tmp / 'synthetic-iso-template.json'
        self.template_path.write_text(json.dumps(self.template), encoding='ascii')
        self.lead = {'format': resolver.LEAD_FORMAT}
        self.build_keys()
        self.build_iso()
        self.build_registry()
        self.build_models()
        self.build_apt()
        self.build_python()
        self.build_lead_files()
        self.write_lead()

    # -- registry
    def registry_image(self, api, repo, tag, labels=None, env=None, arch='amd64', config_arch=None, platforms=True,
                       nested=False, bearer=None, created='2026-11-01T00:00:00Z'):
        def add(path, body, media):
            digest = 'sha256:' + sha(body)
            self.web.add('https://' + api + '/v2/' + repo + path, body,
                         headers={'Content-Type': media, 'Docker-Content-Digest': digest}, bearer=bearer)
            return digest
        config = json.dumps({'architecture': config_arch or arch, 'os': 'linux', 'created': created,
                             'config': {'Labels': labels or {}, 'Env': env or []}}).encode()
        config_digest = 'sha256:' + sha(config)
        self.web.add('https://' + api + '/v2/' + repo + '/blobs/' + config_digest, config, bearer=bearer)
        manifest = json.dumps({'schemaVersion': 2, 'mediaType': OCI_MANIFEST,
                               'config': {'mediaType': 'application/vnd.oci.image.config.v1+json', 'digest': config_digest,
                                          'size': len(config)}, 'layers': []}).encode()
        manifest_digest = 'sha256:' + sha(manifest)
        add('/manifests/' + manifest_digest, manifest, OCI_MANIFEST)
        if not platforms:
            add('/manifests/' + tag, manifest, OCI_MANIFEST)
            return manifest_digest
        other = json.dumps({'schemaVersion': 2, 'mediaType': OCI_MANIFEST, 'config': {'digest': config_digest}, 'layers': []}).encode()
        other_digest = add('/manifests/sha256:' + sha(other), other, OCI_MANIFEST)
        entries = [{'mediaType': OCI_MANIFEST, 'digest': other_digest, 'size': len(other),
                    'platform': {'architecture': 'arm64', 'os': 'linux'}},
                   {'mediaType': OCI_MANIFEST, 'digest': other_digest, 'size': len(other),
                    'platform': {'architecture': 'unknown', 'os': 'unknown'}}]
        if arch == 'amd64':
            target, media = manifest_digest, OCI_MANIFEST
            if nested:
                inner = json.dumps({'schemaVersion': 2, 'mediaType': OCI_INDEX, 'manifests': [
                    {'mediaType': OCI_MANIFEST, 'digest': manifest_digest, 'size': len(manifest),
                     'platform': {'architecture': 'amd64', 'os': 'linux'}}]}).encode()
                # 'labelled': the index entry says it is an index; 'mislabelled': it claims an image manifest.
                target = add('/manifests/sha256:' + sha(inner), inner, OCI_INDEX)
                media = OCI_INDEX if nested == 'labelled' else OCI_MANIFEST
            entries.insert(0, {'mediaType': media, 'digest': target, 'size': 1, 'platform': {'architecture': 'amd64', 'os': 'linux'}})
        index = json.dumps({'schemaVersion': 2, 'mediaType': OCI_INDEX, 'manifests': entries}).encode()
        add('/manifests/' + tag, index, OCI_INDEX)
        add('/manifests/sha256:' + sha(index), index, OCI_INDEX)
        return manifest_digest

    def build_registry(self):
        repo = 'huggingface/text-embeddings-inference'
        bearer = ('ghcr-test-token', 'Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="repository:' + repo + ':pull"')
        self.web.add('https://ghcr.io/token', {'token': 'ghcr-test-token'}, any_query=True)
        labels = {'org.opencontainers.image.version': '1.9.4', 'org.opencontainers.image.revision': 'e80ef225' + '0' * 32}
        self.tei = {}
        for name, minor in TEI_TAGS.items():
            defect = self.knobs.get('tei', {}).get(name)
            exact = minor[:-3] + '1.9.4'
            kwargs = {'labels': labels, 'bearer': bearer, 'created': '2026-11-01T00:00:0' + str(len(name) % 10) + 'Z'}
            if defect == 'amd64-missing':
                kwargs['arch'] = 'arm64'
            elif defect in ('nested-index', 'nested-index-mislabelled'):
                kwargs['nested'] = 'labelled' if defect == 'nested-index' else 'mislabelled'
            elif defect == 'wrong-platform':
                kwargs['config_arch'] = 'arm64'
            elif defect == 'bad-label':
                kwargs['labels'] = dict(labels, **{'org.opencontainers.image.version': '1.8.2'})
            if defect != 'no-exact-tag':
                self.tei[name] = self.registry_image('ghcr.io', repo, exact, **kwargs)
            self.registry_image('ghcr.io', repo, minor, **kwargs)
        hub = ('hub-test-token', 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io",scope="repository:library/python:pull"')
        self.web.add('https://auth.docker.io/token', {'token': 'hub-test-token'}, any_query=True)
        self.base = self.registry_image('registry-1.docker.io', 'library/python', '3.12.14-bookworm', bearer=hub,
                                        env=['PATH=/usr/local/bin', 'PYTHON_VERSION=3.12.14'])
        self.registry_image('registry-1.docker.io', 'library/python', '3.12', bearer=hub, env=['PYTHON_VERSION=3.12.14'])
        hub_ubuntu = ('hub-test-token', 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io",scope="repository:library/ubuntu:pull"')
        self.ubuntu_image = self.registry_image('registry-1.docker.io', 'library/ubuntu', '24.04', bearer=hub_ubuntu)

    def move_tei_tag(self, name):
        """Upstream republishes a tag with a new image (for the changed-value test)."""
        repo = 'huggingface/text-embeddings-inference'
        bearer = ('ghcr-test-token', 'Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="repository:' + repo + ':pull"')
        labels = {'org.opencontainers.image.version': '1.9.4', 'org.opencontainers.image.revision': 'e80ef225' + '1' * 32}
        self.tei[name] = self.registry_image('ghcr.io', repo, TEI_TAGS[name][:-3] + '1.9.4', labels=labels, bearer=bearer,
                                             created='2026-12-01T00:00:00Z')

    # -- keys and ISO
    def build_keys(self):
        k = self.keys
        self.ubuntu_keyring = self.tmp / 'host-ubuntu-archive-keyring.gpg'
        self.ubuntu_keyring.write_bytes(k.binary('U') + (k.binary('X') if self.knobs.get('ubuntu_keyring_extra') else b''))
        self.web.add('https://nvidia.github.io/libnvidia-container/gpgkey', k.armored('N'))
        self.web.add('https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg', k.binary('T'))
        for name in ('U', 'R', 'N', 'T', 'X'):
            self.web.add(resolver.KEYSERVER.format(fingerprint=k.fpr[name]), k.armored(name))
        self.lead['keys'] = {
            'ubuntu': {'fingerprints': [k.fpr['U']] + ([k.fpr['X']] if self.knobs.get('ubuntu_keyring_extra') else []),
                       'signer': k.fpr['U'], 'source': 'test record',
                       'sources': [{'url': self.ubuntu_keyring.resolve().as_uri()}, {'keyserver': True}]},
            'ubuntu-release': {'fingerprints': [k.fpr['R']], 'source': 'test record'},
            'nvidia': {'fingerprints': [k.fpr['X'] if self.knobs.get('nvidia_wrong_record') else k.fpr['N']], 'source': 'test record'},
            'tailscale': {'fingerprints': [k.fpr['T']], 'source': 'test record'},
        }

    def build_iso(self):
        server = '1' * 64 if self.knobs.get('iso_other_hash') else self.template['ubuntu']['sha256']
        sums = ('0' * 64 + ' *ubuntu-24.04.5-desktop-amd64.iso\n' + server + ' *ubuntu-24.04.5-live-server-amd64.iso\n').encode()
        self.web.add('https://releases.ubuntu.com/24.04/SHA256SUMS', sums)
        self.web.add('https://releases.ubuntu.com/24.04/SHA256SUMS.gpg', self.keys.detach(self.knobs.get('iso_signer', 'R'), sums))

    # -- Hugging Face
    def hf_repo(self, model_id, revision, files, extra_tree=()):
        siblings, tree = [], []
        for path, spec in sorted(files.items()):
            if isinstance(spec, dict):
                siblings.append({'rfilename': path, 'blobId': 'b' * 40, 'size': spec['size'],
                                 'lfs': {'sha256': spec['sha256'], 'size': spec['size'], 'pointerSize': 134}})
                tree_oid = 'e' * 64 if self.knobs.get('hf_lfs_mismatch') == (model_id, path) else spec['sha256']
                tree.append({'type': 'file', 'path': path, 'oid': 'b' * 40, 'size': spec['size'],
                             'lfs': {'oid': tree_oid, 'size': spec['size'], 'pointerSize': 134}})
            else:
                blob = hashlib.sha1(b'blob ' + str(len(spec)).encode() + b'\0' + spec).hexdigest()
                siblings.append({'rfilename': path, 'blobId': blob, 'size': len(spec)})
                tree.append({'type': 'file', 'path': path, 'oid': blob, 'size': len(spec)})
                served = spec + b'tampered' if self.knobs.get('hf_blob_tampered') == (model_id, path) else spec
                self.web.add('https://huggingface.co/' + model_id + '/resolve/' + revision + '/' + urllib.parse.quote(path, safe='/'), served)
        for path in extra_tree:
            tree.append({'type': 'file', 'path': path, 'oid': 'c' * 40, 'size': 3})
        tree.append({'type': 'directory', 'path': 'onnx', 'oid': 'd' * 40, 'size': 0})
        api = 'https://huggingface.co/api/models/' + model_id
        self.web.add(api + '/revision/' + revision + '?blobs=true', {'id': model_id, 'sha': revision, 'siblings': siblings})
        self.web.add(api + '/revision/' + revision, {'id': model_id, 'sha': revision})
        half = len(tree) // 2
        cursor = api + '/tree/' + revision + '?recursive=true&cursor=page2'
        self.web.add(api + '/tree/' + revision + '?recursive=true', tree[:half], headers={'Link': '<' + cursor + '>; rel="next"'})
        self.web.add(cursor, tree[half:])

    def build_models(self):
        t = self.template['models']
        config = {'architectures': ['TestModel']}
        tei_files = {'config.json': json.dumps(config).encode(), 'tokenizer.json': b'{"model": {}}', 'README.md': b'# test\n',
                     'model.safetensors': {'sha256': '7' * 64 if self.knobs.get('tei_weight_changed') else t['tei']['files'][1]['sha256'],
                                           'size': 1000}}
        self.hf_repo(t['tei']['id'], t['tei']['revision'], tei_files,
                     extra_tree=('onnx/model.onnx',) if self.knobs.get('hf_incomplete') else ())
        reranker_config = {'architectures': ['RR'], 'auto_map': {'AutoModel': 'modeling_rr.RRModel', 'AutoConfig': 'configuration_rr.RRConfig'}}
        reranker_files = {'config.json': json.dumps(reranker_config).encode(), 'configuration_rr.py': b'class RRConfig: pass\n',
                          'model.safetensors': {'sha256': t['reranker']['files'][1]['sha256'], 'size': 2000}}
        if not self.knobs.get('hf_remote_missing'):
            reranker_files['modeling_rr.py'] = b'class RRModel: pass\n'
        self.hf_repo(t['reranker']['id'], t['reranker']['revision'], reranker_files)
        vlm_revision = t['mineru_vlm']['revision']
        self.hf_repo('opendatalab/MinerU2.5-Pro-2605-1.2B', vlm_revision,
                     {'config.json': b'{}', 'model.safetensors': {'sha256': '9' * 64, 'size': 2312126640 - 2}})
        self.web.add('https://huggingface.co/api/models?search=MinerU2.5-Pro-2605-1.2B&limit=100',
                     [{'id': 'opendatalab/MinerU2.5-Pro-2605-1.2B'}, {'id': 'someone/MinerU2.5-Pro-2605-1.2B'}])
        self.small_revision = '5' * 40
        self.hf_repo('example-org/MinerU-4_models_torch', self.small_revision,
                     {'config.json': b'{}', 'models/Layout/config.json': b'{}', 'models/Layout/model.pt': {'sha256': '8' * 64, 'size': 500}})
        self.lead['models'] = {'mineru_small': {'id': 'example-org/MinerU-4_models_torch', 'revision': self.small_revision,
                                                'source': 'test model manifest'}}

    # -- apt
    def packages(self):
        u = {'libc6': ('2.39-0ubuntu8', 'amd64', 'noble', 'main'), 'python3': ('3.12.3-0ubuntu1', 'amd64', 'noble', 'main'),
             'openssl': ('3.0.13-0ubuntu3', 'amd64', 'noble', 'main'), 'ca-certificates': ('20240203', 'all', 'noble', 'main'),
             'gnupg': ('2.4.4-2ubuntu17', 'all', 'noble', 'main'), 'kmod': ('31+20240202-2ubuntu7', 'amd64', 'noble', 'main'),
             'util-linux': ('2.39.3-9ubuntu6', 'amd64', 'noble', 'main'),
             'ubuntu-drivers-common': ('1:0.9.7.6ubuntu3', 'amd64', 'noble', 'main'),
             'iptables': ('1.8.10-3ubuntu2', 'amd64', 'noble', 'main'),
             'docker.io': ('24.0.7-0ubuntu4', 'amd64', 'noble', 'universe'), 'containerd': ('1.7.12-0ubuntu4', 'amd64', 'noble', 'main'),
             'runc': ('1.1.12-0ubuntu3', 'amd64', 'noble', 'main'), 'docker-compose-v2': ('2.24.6+ds1-0ubuntu2', 'amd64', 'noble', 'universe'),
             'linux-generic': ('6.8.0-31.31', 'amd64', 'noble', 'main')}
        updates = {'libc6': ('2.39-0ubuntu8.4', 'amd64', 'noble-updates', 'main'),
                   'docker.io': ('29.1.3-0ubuntu3~24.04.2', 'amd64', 'noble-updates', 'universe'),
                   'containerd': ('2.2.1-0ubuntu1~24.04.4', 'amd64', 'noble-updates', 'main'),
                   'runc': ('1.3.4-0ubuntu1~24.04.1', 'amd64', 'noble-updates', 'main'),
                   'docker-compose-v2': ('2.40.3+ds1-0ubuntu1~24.04.1', 'amd64', 'noble-updates', 'universe'),
                   'linux-generic': (KERNEL + '.149', 'amd64', 'noble-updates', 'main'),
                   'linux-image-generic': (KERNEL + '.149', 'amd64', 'noble-updates', 'main'),
                   'linux-image-' + KERNEL + '-generic': (KERNEL + '.149', 'amd64', 'noble-updates', 'main'),
                   'linux-modules-nvidia-580-server-generic': (KERNEL + '.149+1', 'amd64', 'noble-updates', 'restricted'),
                   'linux-modules-nvidia-580-server-' + KERNEL + '-generic': (KERNEL + '.149+1', 'amd64', 'noble-updates', 'restricted'),
                   'nvidia-utils-580-server': ('580.95.05-0ubuntu0.24.04.2', 'amd64', 'noble-updates', 'restricted'),
                   'libnvidia-compute-580-server': ('580.95.05-0ubuntu0.24.04.2', 'amd64', 'noble-updates', 'restricted')}
        security = {'libc6': ('2.39-0ubuntu8.4', 'amd64', 'noble-security', 'main')}
        nvidia = {name: [('1.17.8-1', 'amd64'), ('1.18.0-1', 'amd64')] for name in resolver.NVIDIA_TOOLKIT}
        tailscale = {'tailscale': [('1.88.3', 'amd64'), ('1.90.1', 'amd64')]}
        if self.knobs.get('closure_dkms'):
            updates['nvidia-dkms-580-server'] = ('580.95.05-0ubuntu0.24.04.2', 'amd64', 'noble-updates', 'restricted')
        if self.knobs.get('closure_driver_meta'):
            updates['nvidia-driver-580-server'] = ('580.95.05-0ubuntu0.24.04.2', 'amd64', 'noble-updates', 'restricted')
        return u, updates, security, nvidia, tailscale

    def deb(self, name, version):
        return ('deb ' + name + ' ' + version).encode()

    def ubuntu_suite(self, suite, rows, signer):
        base = 'https://snapshot.ubuntu.com/ubuntu/' + SNAPSHOT + '/'
        components = {component: [] for component in ('main', 'restricted', 'universe', 'multiverse')}
        for name, (version, arch, _, component) in sorted(rows.items()):
            data = self.deb(name, version)
            upstream = version.split(':')[-1]
            filename = 'pool/' + component + '/' + name[0] + '/' + name + '/' + name + '_' + upstream + '_' + arch + '.deb'
            self.web.add(base + filename, data)
            components[component].append('Package: ' + name + '\nVersion: ' + version + '\nArchitecture: ' + arch +
                                         '\nFilename: ' + filename + '\nSize: ' + str(len(data)) + '\nSHA256: ' + sha(data) +
                                         '\nDescription: test package\n with a continuation line\n')
        entries = []
        for component, stanzas in components.items():
            data = lzma.compress('\n'.join(stanzas).encode())
            path = 'dists/' + suite + '/' + component + '/binary-amd64/Packages.xz'
            served = data
            if self.knobs.get('bad_packages') == suite and component == 'universe':
                served = lzma.compress(('\n'.join(stanzas) + '\nPackage: evil\nVersion: 1\nArchitecture: amd64\n').encode())
            self.web.add(base + path, served)
            entries.append(' ' + sha(data) + ' ' + str(len(data)) + ' ' + component + '/binary-amd64/Packages.xz')
        label = 'noble-proposed' if self.knobs.get('apt_suite_swap') == suite else suite
        release = ('Origin: Ubuntu\nLabel: Ubuntu\nSuite: ' + label + '\nCodename: noble\nDate: Sat, 15 Nov 2026 00:00:00 UTC\n'
                   'Architectures: amd64\nComponents: main restricted universe multiverse\nSHA256:\n' + '\n'.join(entries) + '\n')
        self.web.add(base + 'dists/' + suite + '/InRelease', self.keys.clearsign(signer, release.encode()))

    def flat_repo(self, base, rows, signer, release_dir, packages_path, filename_prefix):
        stanzas = []
        for name, versions in sorted(rows.items()):
            for version, arch in versions:
                data = self.deb(name, version)
                filename = filename_prefix + name + '_' + version + '_' + arch + '.deb'
                self.web.add(base + filename.removeprefix('./'), data)
                stanzas.append('Package: ' + name + '\nVersion: ' + version + '\nArchitecture: ' + arch + '\nFilename: ' + filename +
                               '\nSize: ' + str(len(data)) + '\nSHA256: ' + sha(data) + '\n')
        data = gzip.compress('\n'.join(stanzas).encode(), mtime=0)
        self.web.add(base + release_dir + packages_path, data)
        expiry = 'Valid-Until: Sun, 01 Nov 2026 00:00:00 UTC\n' if self.knobs.get('expired') == base else ''
        release = ('Origin: test\nSuite: ' + ('stable' if release_dir else '') + '\nCodename: noble\nDate: Sat, 15 Nov 2026 00:00:00 UTC\n' +
                   expiry + 'SHA256:\n ' + sha(data) + ' ' + str(len(data)) + ' ' + packages_path + '\n')
        self.web.add(base + release_dir + 'InRelease', self.keys.clearsign(signer, release.encode()))

    def build_apt(self):
        u, updates, security, nvidia, tailscale = self.packages()
        signer = self.knobs.get('apt_signer', 'U')
        self.ubuntu_suite('noble', u, signer)
        self.ubuntu_suite('noble-updates', updates, 'U')
        self.ubuntu_suite('noble-security', security, 'U')
        self.flat_repo('https://nvidia.github.io/libnvidia-container/stable/deb/amd64/', nvidia, 'N', '', 'Packages.gz', './')
        self.flat_repo('https://pkgs.tailscale.com/stable/ubuntu/', tailscale, 'T', 'dists/noble/', 'main/binary-amd64/Packages.gz', 'pool/')
        latest = {}
        for table in (u, updates, security):
            for name, (version, arch, suite, _) in table.items():
                latest[name] = (version, arch, suite)
        for name, versions in nvidia.items():
            latest[name] = (versions[-1][0], versions[-1][1], 'nvidia')
        latest['tailscale'] = ('1.90.1', 'amd64', 'tailscale')
        self.closure = latest
        self.lead.update(ubuntu_snapshot=SNAPSHOT, kernel_abi=KERNEL, apt_tool_image='docker.io/library/ubuntu:24.04')

    def apt_answer(self, mode, request):
        if mode == 'closure':
            for entry in request:
                name, _, version = entry.partition('=')
                if name not in self.closure or (version and version != self.closure[name][0]):
                    return 100, b'', ('E: Version ' + version + ' for ' + name + ' was not found').encode()
            lines = ['D32-INST Inst ' + name + ' (' + version + ' Ubuntu:24.04/' + suite + ' [' + arch + '])'
                     for name, (version, arch, suite) in sorted(self.closure.items())]
            if self.knobs.get('closure_unsigned'):
                lines.append('D32-INST Inst rogue (6.6.6 local [amd64])')
            # The second simulation, with recommends, as the kit's install-packages.sh runs it.
            lines += ['D32-KIT ' + line[len('D32-INST '):] for line in lines]
            if self.knobs.get('recommends_driver'):
                lines.append('D32-KIT Inst nvidia-driver-580-server (580.95.05-0ubuntu0.24.04.2 Ubuntu:24.04/noble-updates [amd64])')
            return 0, ('\n'.join(lines) + '\n').encode(), b''
        name, _, version = request[0].partition('=')
        module_path = self.knobs.get('module_path', 'kernel/nvidia-580srv/nvidia.ko.zst')
        out = ['D32-MOD file=/lib/modules/' + KERNEL + '-generic/' + module_path,
               'D32-MOD version=580.95.05', 'D32-MOD signer=Canonical Ltd. Kernel Module Signing',
               'D32-PKG ' + name + '\tinstall ok installed\t' + version]
        return 0, ('\n'.join(out) + '\n').encode(), b''

    # -- Python indexes
    def build_python(self):
        files_host = 'https://files.pythonhosted.org/packages/ab/cd/'
        torch_host = 'https://download.pytorch.org/whl/cu130/'
        pypi = {}
        torch = {}

        def wheel(name, version, deps=(), index='pypi', licence=None, sdist=False):
            tag = 'cp312-cp312-manylinux_2_28_x86_64'
            filename = name.replace('-', '_') + '-' + version + '-' + tag + '.whl'
            content = ('fake wheel ' + filename).encode()
            row = {'name': name, 'version': version, 'filename': filename, 'sha256': sha(content), 'deps': list(deps),
                   'url': (torch_host if index == 'torch' else files_host) + urllib.parse.quote(filename)}
            if self.knobs.get('report_unhashed') == name:
                row['nohash'] = True
            table = torch if index == 'torch' else pypi
            table.setdefault(resolver.normalise_name(name), []).append(row)
            if index == 'pypi':
                published = row['sha256']
                if self.knobs.get('pypi_mismatch') == name:
                    published = '0' * 64
                urls = [{'filename': filename, 'digests': {'sha256': published}, 'packagetype': 'bdist_wheel', 'url': row['url']}]
                if sdist:
                    source = name + '-' + version + '.tar.gz'
                    urls.append({'filename': source, 'digests': {'sha256': sha(source.encode())}, 'packagetype': 'sdist',
                                 'url': files_host + source})
                info = {'name': name, 'version': version, 'license_expression': licence, 'classifiers': []}
                self.web.add(resolver.PYPI_JSON.format(name=name, version=version), {'info': info, 'urls': urls})
            return row
        common = [wheel('filelock', '3.20.0'), wheel('nvidia-cuda-runtime', '13.0.96')]
        wheel('setuptools', '80.10.2')
        wheel('wheel', '0.48.0')
        for version in ('2.14.0+cu130', '2.13.0+cu130'):
            wheel('torch', version, deps=('filelock==3.20.0', 'nvidia-cuda-runtime==13.0.96'), index='torch')
        wheel('transformers', '5.17.0', deps=('numpy==2.5.3', 'tokenizers==0.23.2'))
        for name, version in (('fastapi', '0.141.1'), ('uvicorn', '0.54.0'), ('tokenizers', '0.23.2'), ('accelerate', '1.15.0'),
                              ('numpy', '2.5.3'), ('numpy', '2.3.5'), ('vllm', '0.28.0'), ('mineru-llama-cpp', '0.1.2')):
            wheel(name, version)
        wheel('mineru', '4.0.7', licence='AGPL-3.0-only', sdist=True)
        wheel('PyMuPDF', '1.26.5', licence='AGPL-3.0-or-later', sdist=True)
        # The converter keeps transformers 5.17.0 but numpy 2.3.5: give it its own transformers dependency set.
        pypi['transformers'][0]['deps'] = ['tokenizers==0.23.2']
        anchors = ''.join('<a href="/whl/cu130/' + urllib.parse.quote(row['filename']) + '#sha256=' +
                          ('1' * 64 if self.knobs.get('torch_listing_mismatch') else row['sha256']) + '">' + row['filename'] + '</a>\n'
                          for row in torch['torch'])
        self.web.add('https://download.pytorch.org/whl/cu130/torch/', '<html><body>' + anchors + '</body></html>')
        self.universe = self.tmp / 'pip-universe.json'
        self.universe.write_text(json.dumps({'indexes': {'https://pypi.org/simple': pypi,
                                                         'https://download.pytorch.org/whl/cu130': torch}}), encoding='utf-8')
        self.lead.update(pymupdf_version='1.26.5', pymupdf_source='test converter environment',
                         runtime_base='docker.io/library/python:3.12.14-bookworm', runtime_base_os='debian 12',
                         corresponding_source=['PyMuPDF', 'mineru'])

    # -- lead files
    def build_lead_files(self):
        lead = self.tmp / 'lead'
        (lead / 'chain' / 'rules').mkdir(parents=True)
        (lead / 'pairing-helper').write_bytes(b'#!/bin/sh\nexit 0\n')
        (lead / 'config.yaml').write_bytes(b'model:\n  source: local\n  base_dir: /models/models\n  small_backend: torch\n  vlm:\n    engine: vllm\n')
        agpl = (b'                    GNU AFFERO GENERAL PUBLIC LICENSE\n'
                b'                       Version 3, 19 November 2007\n\n'
                b' Copyright (C) 2007 Free Software Foundation, Inc.\n')
        (lead / 'AGPL-3.0.txt').write_bytes(agpl)
        self.lead.update(agpl_path=str(lead / 'AGPL-3.0.txt'), agpl_sha256=sha(agpl))
        (lead / 'chain' / 'adapter.py').write_text('print("adapter")\n', encoding='ascii')
        (lead / 'chain' / 'chain.py').write_text('print("chain")\n', encoding='ascii')
        (lead / 'chain' / 'rules' / 'postclean.py').write_text('print("rules")\n', encoding='ascii')
        self.lead.update(pairing_helper={'path': str(lead / 'pairing-helper'), 'review': 'test review'},
                         mineru_config={'path': str(lead / 'config.yaml'), 'review': 'test review'},
                         production_converter={'dir': str(lead / 'chain'), 'revision': '7' * 40, 'review': 'test review'})

    def write_lead(self):
        self.lead['driver_requirements'] = {
            field: {'version': '580.1.2', 'source': 'https://vendor.invalid/synthetic-driver-requirements',
                    'review': 'Synthetic test evidence, never a release driver requirement'}
            for field in ('cuda_minimum_driver', 'tei_minimum_driver')}
        self.lead_path = self.tmp / 'lead-inputs.json'
        self.lead_path.write_text(json.dumps(self.lead, indent=2), encoding='utf-8')

    # -- running the resolver
    def run(self, *args):
        argv = ['--kit', str(KIT), '--template', str(self.template_path), '--out', str(self.out), '--lead-inputs', str(self.lead_path), '--test-web', self.web.base,
                '--gpg', GPG, '--gpgv', GPGV] + list(args)
        saved = resolver.HOST_FACTORY
        resolver.HOST_FACTORY = lambda log, offline=False: FakeHost(self, log, offline)
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                code = resolver.main(argv)
        finally:
            resolver.HOST_FACTORY = saved
        return code, output.getvalue()

    def filled(self):
        return json.loads((self.out / 'filled-pins.json').read_text(encoding='ascii'))

    def evidence(self):
        return {row['path']: row for row in json.loads((self.out / 'EVIDENCE.json').read_text(encoding='ascii'))['records']}

    def close(self):
        self.web.close()


class FakeHost:
    """Answers docker, dpkg and the other host commands; passes gpg and gpgv to the real programs."""

    def __init__(self, world, log, offline=False):
        self.world, self.log, self.offline = world, log, offline

    def which(self, name):
        if name == 'gpg':
            return GPG
        if name == 'gpgv':
            return GPGV
        return '/usr/bin/' + name

    def done(self, code=0, out=b'', err=b''):
        return subprocess.CompletedProcess([], code, out, err)

    def run(self, argv, input=None, timeout=None):
        if self.offline:
            raise resolver.UsageError('host command refused in this mode')
        argv = [str(part) for part in argv]
        self.world.commands.append(argv)
        program = argv[0]
        if program == 'docker':
            return self.docker(argv[1:], input)
        if program == 'python3' and argv[-2] == '--require':
            values = {'python': '3.12.3', 'tar': 'tar (GNU tar) 1.35',
                      'xorriso': 'xorriso 1.5.6 : RockRidge filesystem manipulator, libburnia project.'}
            return self.done(out=json.dumps({'python': values['python'], argv[-1]: values[argv[-1]]}).encode())
        if program == 'readlink':
            return self.done(out=(argv[-1] + ('.12' if argv[-1].endswith('python3') else '') + '\n').encode())
        if program == 'dpkg-query' and argv[1] == '-S':
            owner = {'python3.12': 'python3.12-minimal'}.get(Path(argv[-1]).name, Path(argv[-1]).name)
            return self.done(out=(owner + ': ' + argv[-1] + '\n').encode())
        if program == 'dpkg-query' and argv[1] == '-W':
            return self.done(out=(argv[-1] + '=1.0-test').encode())
        if program == 'dpkg':
            return self.done(out=self.world.knobs.get('dpkg_verify', '').encode())
        if program == 'sha256sum':
            return self.done(out=(sha(argv[-1].encode()) + '  ' + argv[-1] + '\n').encode())
        return subprocess.run(argv, input=input, capture_output=True, timeout=timeout)

    def docker(self, args, input):
        if args[:2] == ['image', 'inspect']:
            return self.done(0 if args[-1] in self.world.pulls else 1)
        if args[0] == 'pull':
            if '@sha256:' not in args[-1]:
                return self.done(1, err=b'test host: pull by digest only')
            self.world.pulls.append(args[-1])
            return self.done()
        if args[0] != 'run':
            return self.done(1, err=b'test host: unsupported docker call')
        position = next(i for i, part in enumerate(args) if '@sha256:' in part)
        image, command = args[position], args[position + 1:]
        mounts = {}
        for i, part in enumerate(args[:position]):
            if part == '-v':
                host, _, inside = args[i + 1].rpartition(':/')
                if inside.endswith(':ro'):
                    inside = inside[:-3]
                mounts['/' + inside] = host
        if command[:2] == ['sh', '-c']:
            out = ('ID=debian\nVERSION_ID="12"\nPRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nD32-SEP\n' + self.world.container_python +
                   '\nD32-SEP\npip 25.3 from /usr/local/lib/python3.12/site-packages/pip (python 3.12)\nD32-SEP\n'
                   'gcc (Debian 12.2.0-14) 12.2.0\nD32-SEP\n2\n')
            return self.done(out=out.encode())
        if command[:2] == ['bash', '-s']:
            request = (Path(mounts['/in']) / 'request.txt').read_text(encoding='ascii').split()
            code, out, err = self.world.apt_answer(command[-1], request)
            return self.done(code, out, err)
        if command[:4] == ['python', '-I', '-B', '/work/lockgen.py']:
            work = Path(mounts['/work'])
            arguments = [str(work / part[len('/work/'):]) if part.startswith('/work/') else part for part in command[4:]]
            env = dict(os.environ, D32_LOCKGEN_PIP=str(FAKEPIP), D32_FAKEPIP_UNIVERSE=str(self.world.universe))
            result = subprocess.run([sys.executable, '-I', '-B', str(work / 'lockgen.py')] + arguments, capture_output=True, env=env)
            lines = []
            for line in result.stdout.decode().splitlines():
                if line.startswith('D32-JSON '):
                    value = json.loads(line[len('D32-JSON '):])
                    # The fake container reports the runtime base's interpreter, not the test host's.
                    value['env']['python'] = self.world.container_python
                    value['env']['platform'] = 'linux-x86_64'
                    line = 'D32-JSON ' + json.dumps(value)
                lines.append(line)
            return self.done(result.returncode, ('\n'.join(lines) + '\n').encode(), result.stderr)
        return self.done(1, err=b'test host: unsupported container command')
