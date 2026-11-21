#!/usr/bin/env python3
"""Fill the disc 3 build kit's TO-PIN values from authenticated upstream evidence.

Usage (run from any directory; every path may be absolute):
  resolve-pins.py --kit KIT --out OUT [--lead-inputs FILE] --dry-run [--family NAME ...]
  resolve-pins.py --kit KIT --out OUT [--lead-inputs FILE] --family NAME [--family NAME ...]
  resolve-pins.py --kit KIT --out OUT [--lead-inputs FILE] --all
  resolve-pins.py --kit KIT --out OUT --check
Options for a run: --recheck (re-resolve finished items and refuse any change),
--force-family NAME (accept a changed value for that family).

Input: KIT/pins.json, the template. Output in OUT: filled-pins.json (same schema),
EVIDENCE.json (one record per pin), RESOLVE.log (one line per step) and store/, the
authenticated local copies that file: URLs in filled-pins.json point at.
Families, in their working order: lead keys iso tei base models tools apt module locks licences.
Exit codes: 0 done; 2 usage or input error; 3 some pins stay TO-PIN (reason in
EVIDENCE.json); 4 an already resolved value would change (refused without --force-family).
Standard library only. Never writes the kit, never pins a floating tag, never guesses:
whatever cannot be authenticated stays TO-PIN with its reason.
"""
import argparse
import copy
import datetime as dt
import email.utils
import gzip
import hashlib
import importlib.util
import io
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import string
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
EVIDENCE_FORMAT = 'deep32-forge-pin-evidence/1'
LEAD_FORMAT = 'deep32-forge-pin-lead-inputs/1'
USER_AGENT = 'deep32-pin-resolver/1'
HEX40 = r'[0-9a-f]{40}'
HEX64 = r'[0-9a-f]{64}'
FINGERPRINT = r'[0-9A-F]{40}'
SNAPSHOT_ID = r'\d{8}T\d{6}Z'
FAMILIES = ('lead', 'keys', 'iso', 'tei', 'base', 'models', 'tools', 'apt', 'module', 'locks', 'licences')
TEI_REPOSITORY = 'ghcr.io/huggingface/text-embeddings-inference'
HF = 'https://huggingface.co'
PYPI_JSON = 'https://pypi.org/pypi/{name}/{version}/json'
KEYSERVER = 'https://keyserver.ubuntu.com/pks/lookup?op=get&options=mr&search=0x{fingerprint}'
REPOSITORIES = ('ubuntu-noble', 'ubuntu-updates', 'ubuntu-security', 'nvidia', 'tailscale')
NVIDIA_TOOLKIT = ('nvidia-container-toolkit', 'nvidia-container-toolkit-base', 'libnvidia-container1',
                  'libnvidia-container-tools')
DRIVER_BRANCH = '580-server'
MEASURED_BASE = ('runtime.base_image_id', 'runtime.base_archive_sha256')
MINERU_VLM_RECORDED_BYTES = 2312126640
PIPELINE_BUNDLE_ROLES = frozenset({'mineru_small'})
INDEX_TYPES = ('application/vnd.oci.image.index.v1+json', 'application/vnd.docker.distribution.manifest.list.v2+json')
MANIFEST_TYPES = ('application/vnd.oci.image.manifest.v1+json', 'application/vnd.docker.distribution.manifest.v2+json')
# Lead ruling 2026-11-15: jieba 0.42.1 publishes no wheel on PyPI.
# Keep this table equal to SDIST_ALLOWED in lockgen.py; tests check parity.
SDIST_ALLOWED = {('jieba', '0.42.1'): '055ca12f62674fafed09427f176506079bc135638a14e23e25be909131928db2'}  # sha256 or public signing fingerprint
SDIST_ALLOWED_BY = 'lead 2026-11-15: jieba 0.42.1 has no wheel on PyPI'

# Built-in decisions and independent records. Lead inputs may extend or replace them.
DEFAULT_DECISIONS = {
    'tei_drop': {'spark121': 'release specification section 15 item 24 (lead design call, 2026-11-14): the DGX Spark '
                             'embeddings class (spark121, an ARM machine) is dropped from the amd64 GPU disc'},
}
DEFAULT_KEYS = {
    'ubuntu': {
        'fingerprints': ['F6ECB3762474EDA9D21B7022871920D1991BC93C', '790BC7277767219C42C86F933B4FE6ACC0B21F32'],  # sha256 or public signing fingerprint
        'signer': 'F6ECB3762474EDA9D21B7022871920D1991BC93C',  # sha256 or public signing fingerprint
        'source': 'https://wiki.ubuntu.com/SecurityTeam/FAQ (Ubuntu Archive Automatic Signing Keys 2018 and 2012, '
                  'read 2026-11-14)'},
    'ubuntu-release': {
        'fingerprints': ['843938DF228D22F7B3742BC0D94AA3F0EFE21092', 'C5986B4F1257FFA86632CBA746181433FBB75451'],  # sha256 or public signing fingerprint
        'signer': '843938DF228D22F7B3742BC0D94AA3F0EFE21092',  # sha256 or public signing fingerprint
        'source': 'https://ubuntu.com/tutorials/how-to-verify-ubuntu (Ubuntu CD Image Automatic Signing Keys, '
                  'read 2026-11-14)'},
}
KEY_SOURCES = {
    'ubuntu': [{'url': 'file:///usr/share/keyrings/ubuntu-archive-keyring.gpg'}, {'keyserver': True}],
    'ubuntu-release': [{'keyserver': True}],
    'nvidia': [{'url': 'https://nvidia.github.io/libnvidia-container/gpgkey'}, {'keyserver': True}],
    'tailscale': [{'url': 'https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg'}, {'keyserver': True}],
}
KEY_FILES = {'ubuntu': 'payload/keyrings/ubuntu.gpg', 'ubuntu-release': 'payload/keyrings/ubuntu-release.gpg',
             'nvidia': 'payload/keyrings/nvidia.gpg', 'tailscale': 'payload/keyrings/tailscale.gpg'}
KEY_REPOSITORIES = {'ubuntu': ('ubuntu-noble', 'ubuntu-updates', 'ubuntu-security'), 'ubuntu-release': (),
                    'nvidia': ('nvidia',), 'tailscale': ('tailscale',)}
KEY_RUNTIME = {'nvidia': 'nvidia_repository_key_fingerprint', 'tailscale': 'tailscale_repository_key_fingerprint'}
LEAD_FILES = {'pairing_helper': 'payload/bin/pairing-helper', 'mineru_config': 'payload/models/mineru-home/config.yaml',
              'adapter': 'payload/source/production-converter/adapter.py'}
CONVERTER_SOURCE_PREFIX = 'payload/source/production-converter/'
LICENCE_SOURCE_PREFIX = 'payload/licences/source/'


class Refusal(Exception):
    """The input cannot be authenticated; its pins stay TO-PIN with this reason."""


class UsageError(Exception):
    """Bad arguments or input files."""


class NetError(Exception):
    """Network failure after retries."""


# ---------------------------------------------------------------- small helpers

def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + '\n'


def write_atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.part')
    with temporary.open('wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def dotted(path):
    return '.'.join(str(part) for part in path)


def is_unresolved(value):
    """Same rule as the kit's provenance.unresolved, applied to one value or a whole subtree."""
    if isinstance(value, dict):
        return any(is_unresolved(child) for child in value.values())
    if isinstance(value, list):
        return any(is_unresolved(child) for child in value)
    return isinstance(value, str) and ('TO-PIN' in value or 'TO-SET' in value or 'deferred: v1.1 ISO form' in value or not value)


def get(doc, path):
    for part in path:
        doc = doc[part]
    return doc


def get_optional(doc, path):
    try:
        return get(doc, path)
    except (KeyError, IndexError, TypeError):
        return None


def put(doc, path, value):
    get(doc, path[:-1])[path[-1]] = value


def file_row(doc, stage_path):
    for index, row in enumerate(doc['fetch']['files']):
        if row['path'] == stage_path:
            return index
    raise UsageError('pins file has no fetch.files row for ' + stage_path)


def normalise_name(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def tail(data, limit=600):
    text = data.decode('utf-8', 'replace') if isinstance(data, bytes) else str(data)
    text = ' '.join(text.split())
    return text[-limit:]


def relative_ok(value):
    """The kit's relative() rule for stage and repository paths."""
    path = PurePosixPath(value)
    return bool(value) and not value.startswith('/') and '\\' not in value and ':' not in value \
        and '..' not in path.parts and str(path) == value


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_file_url(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != 'file' or parts.netloc not in ('', 'localhost'):
        raise NetError('not a local file URL: ' + url)
    return Path(urllib.request.url2pathname(parts.path)).read_bytes()


# ---------------------------------------------------------------- Debian versions

def _order(char):
    if char == '':
        return 0
    if char.isdigit():
        return 0
    if char in string.ascii_letters:
        return ord(char)
    if char == '~':
        return -1
    return ord(char) + 256


def _verrevcmp(a, b):
    i = j = 0
    while i < len(a) or j < len(b):
        first_diff = 0
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            ac = _order(a[i] if i < len(a) else '')
            bc = _order(b[j] if j < len(b) else '')
            if ac != bc:
                return ac - bc
            i += 1
            j += 1
        while i < len(a) and a[i] == '0':
            i += 1
        while j < len(b) and b[j] == '0':
            j += 1
        while i < len(a) and a[i].isdigit() and j < len(b) and b[j].isdigit():
            if not first_diff:
                first_diff = ord(a[i]) - ord(b[j])
            i += 1
            j += 1
        if i < len(a) and a[i].isdigit():
            return 1
        if j < len(b) and b[j].isdigit():
            return -1
        if first_diff:
            return first_diff
    return 0


def deb_compare(left, right):
    """dpkg's version comparison: negative, zero or positive."""
    def split(version):
        epoch, _, rest = version.rpartition(':') if ':' in version else ('0', '', version)
        upstream, _, revision = rest.rpartition('-') if '-' in rest else (rest, '', '')
        return int(epoch or 0), upstream, revision
    le, lu, lr = split(left)
    re_, ru, rr = split(right)
    if le != re_:
        return le - re_
    return _verrevcmp(lu, ru) or _verrevcmp(lr, rr)


# ---------------------------------------------------------------- apt metadata parsing

def parse_stanzas(text):
    rows = []
    for block in re.split(r'\n[ \t]*\n', text.replace('\r\n', '\n').strip()):
        fields = {}
        key = None
        for line in block.split('\n'):
            if line[:1] in (' ', '\t') and key is not None:
                fields[key] += '\n' + line.strip()
            elif ':' in line:
                key, _, value = line.partition(':')
                key = key.strip()
                fields[key] = value.strip()
        if fields:
            rows.append(fields)
    return rows


def release_field(text, name):
    found = re.search(r'^' + re.escape(name) + r':[ \t]*(.*)$', text, re.MULTILINE)
    return found[1].strip() if found else None


def release_hashes(text):
    """Select the strongest signed digest per path; retain weak entries for refusal."""
    ranks = {'MD5Sum': 0, 'SHA1': 1, 'SHA256': 2, 'SHA512': 3}
    lengths = {'MD5Sum': 32, 'SHA1': 40, 'SHA256': 64, 'SHA512': 128}
    hashes, algorithm = {}, None
    for line in text.replace('\r\n', '\n').split('\n'):
        if line[:1] not in (' ', '\t'):
            algorithm = line.rstrip()[:-1] if line.rstrip().endswith(':') else None
            continue
        if algorithm not in ranks:
            continue
        parts = line.split()
        if len(parts) != 3 or not re.fullmatch(r'[0-9a-fA-F]{' + str(lengths[algorithm]) + r'}', parts[0]) or not parts[1].isdigit():
            raise Refusal('malformed ' + algorithm + ' entry in the signed Release')
        digest, size, path = parts[0].lower(), int(parts[1]), parts[2]
        entry = {'algorithm': algorithm, 'digest': digest, 'size': size}
        previous = hashes.get(path)
        if previous and previous['algorithm'] == algorithm and previous != entry:
            raise Refusal('conflicting ' + algorithm + ' entries for ' + path + ' in the signed Release')
        if previous is None or ranks[algorithm] > ranks[previous['algorithm']]:
            hashes[path] = entry
    return hashes


def verify_release_bytes(hashes, path, body, repo):
    """Authenticate bytes with the signed digest, then record their SHA256 pin."""
    entry = hashes.get(path)
    if entry is None:
        raise Refusal(repo + ': ' + path + ' is not listed in the signed Release')
    algorithm = entry['algorithm']
    if algorithm not in ('SHA512', 'SHA256'):
        raise Refusal(repo + ': ' + path + ' has only MD5Sum or SHA1 in the signed Release; SHA512 or SHA256 is required')
    digest = hashlib.new(algorithm.lower(), body).hexdigest()
    if (digest, len(body)) != (entry['digest'], entry['size']):
        raise Refusal(repo + ': Packages hash mismatch for ' + path +
                      ' (downloaded file differs from the signed Release entry)')
    return {'signed_release_entry': dict(entry, path=path), 'sha256': sha256_bytes(body)}


def decompress(name, data):
    if name.endswith('.xz'):
        return lzma.decompress(data)
    if name.endswith('.gz'):
        return gzip.decompress(data)
    return data


def validsig(status_lines):
    pairs = []
    for line in status_lines:
        if line.startswith('[GNUPG:] VALIDSIG '):
            fields = line.split()
            pairs.append((fields[2], fields[-1]))
    return pairs


# ---------------------------------------------------------------- network

class Response:
    def __init__(self, status, headers, body, url):
        self.status, self.headers, self.body, self.url = status, headers, body, url

    def json(self):
        return json.loads(self.body.decode('utf-8'))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Net:
    """HTTPS GETs with manual redirects (credentials never follow a host change).

    rewrite_base is a test hook only: https://host/path is fetched from
    rewrite_base/host/path, and only a loopback base is accepted.
    """

    def __init__(self, log, rewrite_base=None, retries=3, timeout=120, offline=False):
        if rewrite_base is not None:
            host = urllib.parse.urlsplit(rewrite_base).hostname
            if host not in ('127.0.0.1', 'localhost', '::1'):
                raise UsageError('the test rewrite base must be a loopback address')
        self.log, self.rewrite_base, self.retries, self.timeout, self.offline = log, rewrite_base, retries, timeout, offline
        self.opener = urllib.request.build_opener(_NoRedirect)
        self.requests = 0

    def _target(self, url):
        if self.rewrite_base is None:
            return url
        parts = urllib.parse.urlsplit(url)
        return self.rewrite_base.rstrip('/') + '/' + parts.netloc + parts.path + ('?' + parts.query if parts.query else '')

    def fetch(self, url, headers=None, *, redirect_hosts=None):
        if self.offline:
            raise NetError('network use refused in this mode: ' + url)
        if url.startswith('file:'):
            try:
                return Response(200, {}, read_file_url(url), url)
            except OSError as exc:
                raise NetError(str(exc))
        if not url.startswith('https://'):
            raise NetError('only HTTPS or local file URLs are allowed: ' + url)
        if redirect_hosts is not None and urllib.parse.urlsplit(url).netloc not in redirect_hosts:
            raise NetError('host is not permitted for this fetch: ' + url)
        headers = dict(headers or {})
        current = url
        for _ in range(8):
            response = self._once(current, headers)
            if response.status in (301, 302, 303, 307, 308) and 'location' in response.headers:
                following = urllib.parse.urljoin(current, response.headers['location'])
                if urllib.parse.urlsplit(following).scheme != 'https':
                    raise NetError('redirect away from HTTPS: ' + following)
                if redirect_hosts is not None and urllib.parse.urlsplit(following).netloc not in redirect_hosts:
                    raise NetError('redirect away from permitted host: ' + following)
                if urllib.parse.urlsplit(following).netloc != urllib.parse.urlsplit(current).netloc:
                    headers.pop('Authorization', None)
                current = following
                continue
            return response
        raise NetError('too many redirects: ' + url)

    def _once(self, url, headers):
        last = None
        for attempt in range(self.retries):
            self.requests += 1
            self.log('GET ' + url)
            request = urllib.request.Request(self._target(url), headers=dict({'User-Agent': USER_AGENT}, **headers))
            try:
                with self.opener.open(request, timeout=self.timeout) as answer:
                    return Response(answer.status, {k.lower(): v for k, v in answer.headers.items()}, answer.read(), url)
            except urllib.error.HTTPError as exc:
                body = exc.read()
                if exc.code >= 500 or exc.code == 429:
                    last = 'HTTP ' + str(exc.code)
                else:
                    return Response(exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, body, url)
            except (urllib.error.URLError, OSError) as exc:
                last = str(exc)
            if attempt + 1 < self.retries:
                time.sleep(2 * 3 ** attempt)
        raise NetError(url + ': ' + str(last))

    def get_ok(self, url, headers=None, *, redirect_hosts=None):
        try:
            response = self.fetch(url, headers) if redirect_hosts is None else \
                self.fetch(url, headers, redirect_hosts=redirect_hosts)
        except NetError as exc:
            raise Refusal('could not fetch ' + url + ': ' + str(exc))
        if response.status != 200:
            raise Refusal('HTTP ' + str(response.status) + ' for ' + url)
        return response


# ---------------------------------------------------------------- host commands

class Host:
    def __init__(self, log, offline=False):
        self.log, self.offline = log, offline

    def run(self, argv, input=None, timeout=None):
        if self.offline:
            raise UsageError('host command refused in this mode: ' + argv[0])
        self.log('RUN ' + shlex.join(str(part) for part in argv))
        try:
            return subprocess.run([str(part) for part in argv], input=input, capture_output=True, timeout=timeout)
        except FileNotFoundError as exc:
            return subprocess.CompletedProcess(argv, 127, b'', str(exc).encode())

    def which(self, name):
        return shutil.which(name)


class Gpg:
    """gpg/gpgv in a private, empty home directory. Accepts Git for Windows' MSYS build for tests."""

    def __init__(self, host, home, gpg=None, gpgv=None):
        self.host, self.home = host, Path(home)
        self.gpg = gpg or host.which('gpg')
        self.gpgv = gpgv or host.which('gpgv')
        self.msys = os.name == 'nt' and self.gpg is not None and Path(self.gpg).parent.parent.name.lower() == 'usr'

    def path(self, value):
        text = Path(value).resolve().as_posix()
        if self.msys and re.match(r'[A-Za-z]:/', text):
            text = '/' + text[0].lower() + text[2:]
        return text

    def _home(self):
        if not self.gpg or not self.gpgv:
            raise Refusal('gpg and gpgv are required on this host')
        self.home.mkdir(parents=True, exist_ok=True)
        os.chmod(self.home, 0o700)
        return self.path(self.home)

    def primary_fingerprints(self, data):
        home = self._home()
        with tempfile.TemporaryDirectory(dir=self.home) as directory:
            source = Path(directory) / 'key'
            source.write_bytes(data)
            result = self.host.run([self.gpg, '--batch', '--no-autostart', '--homedir', home, '--with-colons',
                                    '--show-keys', self.path(source)])
        if result.returncode:
            raise Refusal('gpg cannot read the key: ' + tail(result.stderr))
        found = []
        expect = False
        for line in result.stdout.decode('utf-8', 'replace').splitlines():
            fields = line.split(':')
            if fields[0] == 'pub':
                expect = True
            elif fields[0] == 'fpr' and expect:
                found.append(fields[9].upper())
                expect = False
        return found

    def dearmor(self, data):
        if not data.lstrip().startswith(b'-----BEGIN PGP PUBLIC KEY BLOCK-----'):
            return data
        home = self._home()
        with tempfile.TemporaryDirectory(dir=self.home) as directory:
            source, target = Path(directory) / 'key.asc', Path(directory) / 'key.gpg'
            source.write_bytes(data)
            result = self.host.run([self.gpg, '--batch', '--no-autostart', '--homedir', home, '--yes',
                                    '--output', self.path(target), '--dearmor', self.path(source)])
            if result.returncode or not target.is_file():
                raise Refusal('gpg cannot dearmor the key: ' + tail(result.stderr))
            return target.read_bytes()

    def verify(self, keyring, files, output=None):
        home = self._home()
        argv = [self.gpgv, '--homedir', home, '--status-fd=1', '--keyring', self.path(keyring)]
        if output is not None:
            argv += ['--output', self.path(output)]
        result = self.host.run(argv + [self.path(name) for name in files])
        return result.returncode, result.stdout.decode('utf-8', 'replace').splitlines(), tail(result.stderr)


# ---------------------------------------------------------------- registry

def parse_reference(reference):
    """[host/]path[:tag][@sha256:...] -> (host, api_host, path, tag, digest)."""
    name, _, digest = reference.partition('@')
    tag = None
    last = name.rsplit('/', 1)[-1]
    if ':' in last:
        name, tag = name.rsplit(':', 1)
    first = name.split('/', 1)[0]
    if '/' in name and ('.' in first or ':' in first or first == 'localhost'):
        host, path = name.split('/', 1)
    else:
        host, path = 'docker.io', name
    if host == 'docker.io' and '/' not in path:
        path = 'library/' + path
    api = 'registry-1.docker.io' if host == 'docker.io' else host
    if digest and not re.fullmatch('sha256:' + HEX64, digest):
        raise Refusal('malformed digest in ' + reference)
    return host, api, path, tag, digest or None


class Registry:
    ACCEPT = ', '.join(INDEX_TYPES + MANIFEST_TYPES)

    def __init__(self, net):
        self.net = net
        self.tokens = {}

    def _token(self, challenge):
        if not challenge.lower().startswith('bearer '):
            raise Refusal('registry asks for an unsupported authentication scheme')
        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        realm = params.get('realm', '')
        if not realm.startswith('https://'):
            raise Refusal('registry token realm is not HTTPS')
        query = urllib.parse.urlencode({key: params[key] for key in ('service', 'scope') if key in params})
        key = realm + '?' + query
        if key not in self.tokens:
            answer = self.net.get_ok(key).json()
            token = answer.get('token') or answer.get('access_token')
            if not token:
                raise Refusal('registry returned no anonymous pull token')
            self.tokens[key] = token
        return self.tokens[key]

    def _get(self, api, path, headers):
        url = 'https://' + api + '/v2/' + path
        try:
            response = self.net.fetch(url, headers)
            if response.status == 401:
                token = self._token(response.headers.get('www-authenticate', ''))
                response = self.net.fetch(url, dict(headers, Authorization='Bearer ' + token))
        except NetError as exc:
            raise Refusal('registry request failed: ' + str(exc))
        return response

    def manifest(self, api, path, reference):
        response = self._get(api, path + '/manifests/' + reference, {'Accept': self.ACCEPT})
        if response.status == 404:
            raise Refusal('no manifest for ' + api + '/' + path + ':' + reference)
        if response.status != 200:
            raise Refusal('HTTP ' + str(response.status) + ' for manifest ' + reference)
        computed = 'sha256:' + sha256_bytes(response.body)
        header = response.headers.get('docker-content-digest')
        if reference.startswith('sha256:') and computed != reference:
            raise Refusal('manifest body does not hash to its digest ' + reference)
        if header and header != computed:
            raise Refusal('registry digest header differs from the manifest body hash for ' + reference)
        body = json.loads(response.body.decode('utf-8'))
        media = body.get('mediaType') or response.headers.get('content-type', '').split(';')[0].strip()
        return body, media, computed

    def blob(self, api, path, digest):
        response = self._get(api, path + '/blobs/' + digest, {})
        if response.status != 200:
            raise Refusal('HTTP ' + str(response.status) + ' for blob ' + digest)
        if 'sha256:' + sha256_bytes(response.body) != digest:
            raise Refusal('blob body does not hash to ' + digest)
        return response.body

    def resolve_amd64(self, reference):
        """Return the linux/amd64 platform manifest digest behind a tag or digest, with checks."""
        host, api, path, tag, digest = parse_reference(reference)
        body, media, top = self.manifest(api, path, digest or tag)
        record = {'reference': reference, 'repository': host + '/' + path, 'tag': tag, 'top_digest': top,
                  'top_media_type': media}
        if media in INDEX_TYPES:
            entries = [entry for entry in body.get('manifests', [])
                       if (entry.get('platform') or {}).get('os') == 'linux'
                       and (entry.get('platform') or {}).get('architecture') == 'amd64'
                       and not (entry.get('platform') or {}).get('variant')]
            if not entries:
                raise Refusal(reference + ': the index has no linux/amd64 manifest')
            if len(entries) > 1:
                raise Refusal(reference + ': the index has several linux/amd64 manifests; no choice is made')
            entry = entries[0]
            if entry.get('mediaType') in INDEX_TYPES:
                raise Refusal(reference + ': the amd64 entry is another index, not a platform manifest; '
                              'an index digest is never pinned')
            record['index_digest'] = top
            manifest, manifest_media, manifest_digest = self.manifest(api, path, entry['digest'])
        elif media in MANIFEST_TYPES:
            manifest, manifest_media, manifest_digest = body, media, top
        else:
            raise Refusal(reference + ': unknown manifest media type ' + repr(media))
        if manifest_media not in MANIFEST_TYPES or 'config' not in manifest:
            raise Refusal(reference + ': ' + manifest_digest + ' is not a platform image manifest; '
                          'an index digest is never pinned')
        config_digest = manifest['config']['digest']
        config = json.loads(self.blob(api, path, config_digest).decode('utf-8'))
        if config.get('os') != 'linux' or config.get('architecture') != 'amd64':
            raise Refusal(reference + ': the image configuration says ' + str(config.get('os')) + '/' +
                          str(config.get('architecture')) + ', not linux/amd64')
        inner = config.get('config') or {}
        record.update(manifest_digest=manifest_digest, manifest_media_type=manifest_media, config_digest=config_digest,
                      labels=inner.get('Labels') or {}, env=inner.get('Env') or [], created=config.get('created'))
        return record


# ---------------------------------------------------------------- run state

class Result:
    def __init__(self):
        self.updates = {}
        self.deletes = []
        self.records = []
        self.file_rows = []
        self.tools = []
        self.decisions = []

    def upsert_file(self, stage_path, url, digest, source, authentication, **extra):
        """Fill (or append) the fetch.files row for stage_path; other rows are never touched."""
        self.file_rows.append((stage_path, url, digest, source, authentication, extra))

    def set(self, path, value, source, authentication, **extra):
        self.updates[tuple(path)] = value
        if isinstance(value, list):
            per = extra.pop('elements', None) or [{} for _ in value]
            for index, element in enumerate(value):
                self.records.append(dict(path=dotted(tuple(path) + (index,)), value=element, source=source,
                                         authentication=dict(authentication, **per[index]), **extra))
        else:
            self.records.append(dict(path=dotted(path), value=value, source=source,
                                     authentication=authentication, **extra))


class Context:
    def __init__(self, args, log, net, host, gpg, lead, kit, out, fetch_module, prov):
        self.args, self.log_line, self.net, self.host, self.gpg = args, log, net, host, gpg
        self.lead, self.kit, self.out, self.fetch, self.prov = lead, kit, out, fetch_module, prov
        self.store = out / 'store'
        self.work = out / 'work'
        self.registry = Registry(net)
        self.never = set(fetch_module.LATER_STEP_PINS) | set(MEASURED_BASE)
        self.pulled = set()

    def log(self, item, kind, message):
        self.log_line(item + ' ' + kind + ' ' + message)

    def decision(self, key, name):
        merged = dict(DEFAULT_DECISIONS.get(key, {}))
        merged.update(self.lead.get(key, {}))
        return merged.get(name)

    def key_spec(self, name):
        # A lead record replaces the built-in one as a whole (fingerprints, signer, source, sources).
        return dict(self.lead.get('keys', {}).get(name) or DEFAULT_KEYS.get(name, {}))

    def store_uri(self, path):
        return Path(path).resolve().as_uri()

    def keyring(self, doc, stage_path):
        """Store copy of a resolved keyring, checked against its pinned hash."""
        row = doc['fetch']['files'][file_row(doc, stage_path)]
        if is_unresolved(row['sha256']):
            raise Refusal(stage_path + ' is not resolved yet: run --family keys first')
        path = self.store / 'keyrings' / PurePosixPath(stage_path).name
        if not path.is_file() or sha256_file(path) != row['sha256']:
            raise Refusal('store copy of ' + stage_path + ' is missing or differs from its pin: re-run --family keys')
        return path

    def docker_pull(self, reference):
        if reference in self.pulled:
            return
        # A digest already in the local store needs no registry call (Docker Hub counts anonymous pulls).
        if self.host.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', reference]).returncode == 0:
            self.pulled.add(reference)
            return
        result = self.host.run(['docker', 'pull', '--platform', 'linux/amd64', reference], timeout=3600)
        if result.returncode:
            raise Refusal('docker pull ' + reference + ' failed: ' + tail(result.stderr))
        self.pulled.add(reference)

    def tool_image(self, result, lead_key, default):
        reference = self.lead.get(lead_key) or default
        if '@' not in reference and (':' not in reference.rsplit('/', 1)[-1] or reference.endswith(':latest')):
            raise Refusal('tool image ' + reference + ' must name a tag other than latest, or a digest')
        record = self.registry.resolve_amd64(reference)
        pinned = record['repository'] + '@' + record['manifest_digest']
        self.docker_pull(pinned)
        result.tools.append({'name': lead_key, 'requested': reference, 'pinned': pinned, 'used_by': 'resolver',
                             'config_digest': record['config_digest'], 'retrieved_at': utcnow(),
                             'note': 'tool image only; never shipped'})
        return pinned

    def mount_user(self):
        if hasattr(os, 'getuid'):
            return ['--user', str(os.getuid()) + ':' + str(os.getgid())]
        return []


# ---------------------------------------------------------------- items

class Item:
    family = ''
    name = ''

    @property
    def key(self):
        return self.family + '/' + self.name

    def paths(self, doc):
        return []

    def pending(self, ctx, doc):
        return any(is_unresolved(get_optional(doc, path)) for path in self.paths(doc)
                   if get_optional(doc, path) is not None)

    def plan(self, ctx, doc):
        return []

    def resolve(self, ctx, doc):
        raise NotImplementedError


def files_paths(doc, stage_path, fields=('url', 'sha256')):
    index = file_row(doc, stage_path)
    return [('fetch', 'files', index, field) for field in fields]


def lead_file(ctx, result, doc, key, stage_path, validator=None):
    spec = ctx.lead.get(key) or {}
    source = spec.get('path', '')
    if not source:
        raise Refusal('lead-supplied reviewed input ' + key + '.path is not given in the lead inputs')
    if not spec.get('review'):
        raise Refusal(key + ' has no review note in the lead inputs; an unreviewed file is never pinned')
    path = Path(source)
    if path.is_symlink() or not path.is_file() or not path.stat().st_size:
        raise Refusal(key + ': ' + source + ' is not a regular, non-empty file')
    data = path.read_bytes()
    if validator is not None:
        validator(data)
    target = ctx.store / 'lead' / PurePosixPath(stage_path).name
    write_atomic(target, data)
    digest = sha256_bytes(data)
    authentication = {'method': 'lead-reviewed local file', 'review': spec['review'], 'original': source}
    result.upsert_file(stage_path, ctx.store_uri(target), digest, 'file copy of ' + source, authentication,
                       retrieved_at=utcnow())
    return data, digest


class LeadPairingHelper(Item):
    family, name = 'lead', 'pairing_helper'

    def paths(self, doc):
        return files_paths(doc, LEAD_FILES['pairing_helper']) + [('runtime', 'pairing_helper_sha256')]

    def plan(self, ctx, doc):
        return ['copy lead input pairing_helper.path into store/lead, hash it, pin fetch.files and runtime.pairing_helper_sha256']

    def resolve(self, ctx, doc):
        result = Result()
        _, digest = lead_file(ctx, result, doc, 'pairing_helper', LEAD_FILES['pairing_helper'])
        result.set(('runtime', 'pairing_helper_sha256'), digest, 'sha256 of the lead-supplied S7 helper',
                   {'method': 'lead-reviewed local file', 'review': ctx.lead['pairing_helper']['review']},
                   retrieved_at=utcnow())
        return result


class LeadMineruConfig(Item):
    family, name = 'lead', 'mineru_config'

    def paths(self, doc):
        return files_paths(doc, LEAD_FILES['mineru_config'])

    def plan(self, ctx, doc):
        return ['copy lead input mineru_config.path to mineru-home/config.yaml; validate the reviewed local MinerU 4 YAML']

    def resolve(self, ctx, doc):
        result = Result()

        def validate(data):
            try:
                ctx.fetch.validate_mineru_config(data)
            except ValueError as exc:
                raise Refusal(str(exc))
        lead_file(ctx, result, doc, 'mineru_config', LEAD_FILES['mineru_config'], validator=validate)
        return result


class LeadConverterSource(Item):
    family, name = 'lead', 'converter_source'

    def paths(self, doc):
        rows = [index for index, row in enumerate(doc['fetch']['files'])
                if row['path'].startswith(CONVERTER_SOURCE_PREFIX)]
        return [('runtime', 'production_converter_source_revision')] + \
            [('fetch', 'files', index, field) for index in rows for field in ('url', 'sha256')]

    def plan(self, ctx, doc):
        return ['copy every file of lead input production_converter.dir (adapter.py included) into store/lead/production-converter',
                'if the directory is a git checkout: HEAD must equal production_converter.revision and the tree must be clean',
                'pin runtime.production_converter_source_revision, the adapter row and one fetch.files row per other source file']

    def resolve(self, ctx, doc):
        spec = ctx.lead.get('production_converter') or {}
        source, revision, review = spec.get('dir', ''), spec.get('revision', ''), spec.get('review', '')
        if not source or not revision or not review:
            raise Refusal('lead inputs production_converter.dir, .revision and .review are required '
                          '(reviewed production chain, restricted converters excluded)')
        if not re.fullmatch(HEX40, revision):
            raise Refusal('production_converter.revision must be a 40-hex git commit id, or the git tree id of the reviewed export')
        root = Path(source)
        if not (root / 'adapter.py').is_file():
            raise Refusal('the reviewed production chain has no adapter.py')
        authentication = {'method': 'lead-reviewed export', 'review': review, 'revision': revision}
        if (root / '.git').exists():
            head = ctx.host.run(['git', '-C', str(root), 'rev-parse', 'HEAD'])
            dirty = ctx.host.run(['git', '-C', str(root), 'status', '--porcelain'])
            if head.returncode or head.stdout.decode().strip() != revision:
                raise Refusal('git HEAD of the production chain is not ' + revision)
            if dirty.returncode or dirty.stdout.strip():
                raise Refusal('the production chain checkout has uncommitted changes')
            authentication['method'] = 'git commit with a clean tree'
        files = []
        for path in sorted(root.rglob('*'), key=lambda item: item.relative_to(root).as_posix()):
            relative = path.relative_to(root).as_posix()
            if relative.split('/')[0] == '.git':
                continue
            if path.is_symlink():
                raise Refusal('symlink in the production chain: ' + relative)
            if path.is_file():
                if not relative_ok(relative) or not relative.isascii():
                    raise Refusal('unsafe file name in the production chain: ' + relative)
                files.append((relative, path.read_bytes()))
        result = Result()
        stamp = utcnow()
        for relative, data in files:
            target = ctx.store / 'lead' / 'production-converter' / relative
            write_atomic(target, data)
            result.upsert_file(CONVERTER_SOURCE_PREFIX + relative, ctx.store_uri(target), sha256_bytes(data),
                               'file copy of ' + str(root / relative), authentication, retrieved_at=stamp)
        result.set(('runtime', 'production_converter_source_revision'), revision, 'lead input production_converter.revision',
                   authentication, retrieved_at=stamp)
        return result


class LeadPymupdf(Item):
    family, name = 'lead', 'pymupdf'

    def paths(self, doc):
        return [('runtime', 'pymupdf_version')]

    def plan(self, ctx, doc):
        return ['take lead input pymupdf_version (with its source), confirm on ' + PYPI_JSON.format(name='PyMuPDF', version='<v>') +
                ' that a CPython 3.12 Linux x86_64 wheel exists']

    def resolve(self, ctx, doc):
        version, source = ctx.lead.get('pymupdf_version', ''), ctx.lead.get('pymupdf_source', '')
        if not version or not source:
            raise Refusal('lead inputs pymupdf_version and pymupdf_source (the reviewed production converter '
                          'environment) are required')
        if not re.fullmatch(r'\d+(\.\d+)+', version):
            raise Refusal('pymupdf_version must be an exact release number')
        url = PYPI_JSON.format(name='PyMuPDF', version=version)
        files = ctx.net.get_ok(url).json().get('urls', [])
        wheels = [row['filename'] for row in files
                  if re.search(r'-(cp312-cp312|cp3\d+-abi3)-[^-]*manylinux[^-]*x86_64\.whl$', row['filename'])]
        if not wheels:
            raise Refusal('PyPI has no CPython 3.12 Linux x86_64 wheel for PyMuPDF ' + version)
        result = Result()
        result.set(('runtime', 'pymupdf_version'), version, url,
                   {'method': 'lead statement checked against PyPI', 'lead_source': source, 'wheels': wheels},
                   retrieved_at=utcnow())
        return result


class LeadDriverRequirements(Item):
    family, name = 'lead', 'driver_requirements'

    def paths(self, doc):
        return [('runtime', field) for field in ('cuda_minimum_driver', 'tei_minimum_driver')
                if field in doc['runtime']]

    def plan(self, ctx, doc):
        return ['take driver_requirements.<pin>.version, source and review from the lead inputs',
                'source must be a public HTTPS citation for the exact pinned image/CUDA runtime; no driver version is guessed']

    def resolve(self, ctx, doc):
        result = Result()
        requirements = ctx.lead.get('driver_requirements') or {}
        if not isinstance(requirements, dict):
            raise Refusal('driver_requirements must be an object of reviewed driver requirements')
        for _, field in self.paths(doc):
            spec = requirements.get(field) or {}
            if not isinstance(spec, dict):
                raise Refusal('driver_requirements.' + field + ' must be an object with version, source and review')
            version, source, review = (spec.get(key, '') for key in ('version', 'source', 'review'))
            if not all(isinstance(value, str) for value in (version, source, review)):
                raise Refusal('driver_requirements.' + field + ' needs an exact numeric version, public HTTPS source and review')
            try:
                url = urllib.parse.urlsplit(source)
            except ValueError as exc:
                raise Refusal('driver_requirements.' + field + ' has an invalid source URL') from exc
            if (not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', version) or not review.strip() or
                    url.scheme != 'https' or not url.netloc or url.username or url.password or any(char.isspace() for char in source)):
                raise Refusal('driver_requirements.' + field + ' needs an exact numeric version, public HTTPS source and review')
            result.set(('runtime', field), version, source,
                       {'method': 'lead-reviewed requirement for the pinned image and CUDA runtime', 'review': review},
                       retrieved_at=utcnow())
        return result


class KeyItem(Item):
    family = 'keys'

    def __init__(self, name):
        self.name = name

    def paths(self, doc):
        paths = files_paths(doc, KEY_FILES[self.name])
        paths += [('fetch', 'repositories', repo, 'fingerprint') for repo in KEY_REPOSITORIES[self.name]]
        if self.name in KEY_RUNTIME:
            paths.append(('runtime', KEY_RUNTIME[self.name]))
        return paths

    def plan(self, ctx, doc):
        spec = ctx.key_spec(self.name)
        sources = spec.get('sources') or KEY_SOURCES[self.name]
        lines = ['expected fingerprints (independent record): ' + (', '.join(spec.get('fingerprints', [])) or 'MISSING: lead input keys.' + self.name)]
        for source in sources:
            lines.append('key source: ' + ('keyserver.ubuntu.com by fingerprint' if source.get('keyserver') else source['url']))
        lines.append('gpg --show-keys on each copy; every source must hold exactly the expected primary keys; store a binary keyring')
        return lines

    def _source_bytes(self, ctx, source, fingerprints):
        """(label, bytes as served, binary keyring bytes)."""
        if source.get('keyserver'):
            raw = [ctx.net.get_ok(KEYSERVER.format(fingerprint=fpr)).body for fpr in fingerprints]
            return 'keyserver.ubuntu.com', b''.join(raw), b''.join(ctx.gpg.dearmor(part) for part in raw)
        raw = ctx.net.get_ok(source['url']).body
        return source['url'], raw, ctx.gpg.dearmor(raw)

    def resolve(self, ctx, doc):
        spec = ctx.key_spec(self.name)
        fingerprints = [value.replace(' ', '').upper() for value in spec.get('fingerprints', [])]
        if not fingerprints or any(not re.fullmatch(FINGERPRINT, value) for value in fingerprints):
            raise Refusal('no full fingerprint for key ' + self.name + ' from an independent source '
                          '(lead input keys.' + self.name + '.fingerprints)')
        if not spec.get('source'):
            raise Refusal('the fingerprint record for ' + self.name + ' cites no source')
        signer = (spec.get('signer') or fingerprints[0]).replace(' ', '').upper()
        if signer not in fingerprints:
            raise Refusal('the named signer is not among the expected fingerprints')
        sources = spec.get('sources') or KEY_SOURCES[self.name]
        # A fingerprint read off the key file, the keyserver or the repository it vouches for is not
        # independent: whoever controls that place controls both the key and the "record".
        own = {urllib.parse.urlsplit(KEYSERVER).hostname if source.get('keyserver') else
               urllib.parse.urlsplit(source.get('url', '')).hostname for source in sources}
        for repo in KEY_REPOSITORIES[self.name]:
            own.update(urllib.parse.urlsplit(row['url']).hostname for row in doc['fetch']['repositories'][repo]['metadata'])
        if self.name == 'ubuntu-release':
            own.add(urllib.parse.urlsplit(doc['fetch']['files'][file_row(doc, 'iso/SHA256SUMS')]['url']).hostname)
        cited = {host.lower() for host in re.findall(r'https?://([A-Za-z0-9.-]+)', spec['source'])}
        circular = sorted(cited & {host.lower() for host in own if host})
        if circular:
            raise Refusal('the fingerprint record for ' + self.name + ' cites ' + ', '.join(circular) + ', the same place the '
                          'key or the repository it vouches for comes from; an independent source is required')
        checks = []
        primary = None
        for position, source in enumerate(sources):
            try:
                label, raw, data = self._source_bytes(ctx, source, fingerprints)
                found = ctx.gpg.primary_fingerprints(data)
            except Refusal as exc:
                if position == 0:
                    raise Refusal('primary key source failed: ' + str(exc))
                checks.append({'source': source.get('url', 'keyserver.ubuntu.com'), 'result': 'unavailable: ' + str(exc)[:200]})
                continue
            # Every key in the file must be in the independent record, and the signer must be there.
            if not set(found) <= set(fingerprints) or signer not in found or len(found) != len(set(found)):
                raise Refusal('key from ' + label + ' holds primary keys ' + ', '.join(found) +
                              '; the independent record lists ' + ', '.join(fingerprints) + ' with signer ' + signer)
            checks.append({'source': label, 'result': 'same primary fingerprints', 'sha256': sha256_bytes(data)})
            if position == 0:
                primary = (label, raw, data)
        confirmed = sum(1 for row in checks if row['result'] == 'same primary fingerprints')
        if confirmed < int(spec.get('min_sources', 1)):
            raise Refusal('only ' + str(confirmed) + ' key source(s) confirmed the fingerprints; the record asks for ' +
                          str(spec['min_sources']))
        label, raw, data = primary
        authentication = {'method': 'openpgp fingerprints from independent sources', 'expected': fingerprints,
                          'expected_source': spec['source'], 'signer': signer, 'checks': checks,
                          'keyring_fingerprints': ctx.gpg.primary_fingerprints(data)}
        if label.startswith('file:///usr/share/keyrings/'):
            local = urllib.request.url2pathname(urllib.parse.urlsplit(label).path)
            owner = ctx.host.run(['dpkg-query', '-S', local])
            package = owner.stdout.decode().split(':')[0].strip() if not owner.returncode else ''
            verify = ctx.host.run(['dpkg', '-V', package]) if package else None
            if not package or verify.returncode or local in verify.stdout.decode():
                raise Refusal(local + ' is not an unmodified file of an installed package')
            authentication['dpkg'] = {'package': package, 'verified': True}
        target = ctx.store / 'keyrings' / PurePosixPath(KEY_FILES[self.name]).name
        write_atomic(target, data)
        template_url = doc['fetch']['files'][file_row(doc, KEY_FILES[self.name])]['url']
        # A vendor's binary keyring at the template's own URL stays a public HTTPS source;
        # anything converted or taken from this host is pinned as the stored local copy.
        keep_https = label.startswith('https://') and template_url == label and raw == data
        url = label if keep_https else ctx.store_uri(target)
        result = Result()
        stamp = utcnow()
        result.upsert_file(KEY_FILES[self.name], url, sha256_bytes(data), label, authentication, retrieved_at=stamp)
        for repo in KEY_REPOSITORIES[self.name]:
            result.set(('fetch', 'repositories', repo, 'fingerprint'), signer, spec['source'], authentication, retrieved_at=stamp)
        if self.name in KEY_RUNTIME:
            result.set(('runtime', KEY_RUNTIME[self.name]), signer, spec['source'], authentication, retrieved_at=stamp)
        return result


class IsoSums(Item):
    family, name = 'iso', 'sums'

    def paths(self, doc):
        return [('fetch', 'files', file_row(doc, 'iso/SHA256SUMS'), 'sha256'),
                ('fetch', 'files', file_row(doc, 'iso/SHA256SUMS.gpg'), 'sha256'),
                ('fetch', 'iso_signing_fingerprint')]

    def plan(self, ctx, doc):
        sums = doc['fetch']['files'][file_row(doc, 'iso/SHA256SUMS')]['url']
        return ['GET ' + sums + ' and its .gpg; gpgv --status-fd=1 --keyring store/keyrings/ubuntu-release.gpg (exit 0 as the kit requires)',
                'VALIDSIG must name an expected ISO key; the ' + doc['ubuntu']['iso'] + ' line must equal ' + doc['ubuntu']['sha256']]

    def resolve(self, ctx, doc):
        keyring = ctx.keyring(doc, KEY_FILES['ubuntu-release'])
        spec = ctx.key_spec('ubuntu-release')
        expected = [value.replace(' ', '').upper() for value in spec.get('fingerprints', [])]
        sums_index, sig_index = file_row(doc, 'iso/SHA256SUMS'), file_row(doc, 'iso/SHA256SUMS.gpg')
        sums_url, sig_url = doc['fetch']['files'][sums_index]['url'], doc['fetch']['files'][sig_index]['url']
        sums, signature = ctx.net.get_ok(sums_url).body, ctx.net.get_ok(sig_url).body
        folder = ctx.store / 'iso'
        write_atomic(folder / 'SHA256SUMS', sums)
        write_atomic(folder / 'SHA256SUMS.gpg', signature)
        code, status, error = ctx.gpg.verify(keyring, [folder / 'SHA256SUMS.gpg', folder / 'SHA256SUMS'])
        if code:
            raise Refusal('gpgv rejected SHA256SUMS (exit ' + str(code) + '; the kit\'s verify_iso would fail): ' + error)
        signed_by = [fpr for fpr in expected if any(fpr in pair for pair in validsig(status))]
        if not signed_by:
            raise Refusal('SHA256SUMS is not signed by an expected ISO key')
        entries = {}
        for line in sums.decode('ascii', 'replace').splitlines():
            parts = line.split()
            if len(parts) == 2:
                entries[parts[1].lstrip('*')] = parts[0]
        if entries.get(doc['ubuntu']['iso']) != doc['ubuntu']['sha256']:
            raise Refusal('the signed SHA256SUMS does not list ' + doc['ubuntu']['iso'] + ' with the pinned hash')
        authentication = {'method': 'gpgv detached signature', 'keyring_sha256': sha256_file(keyring),
                          'validsig': validsig(status), 'iso_line': doc['ubuntu']['iso'] + ' ' + doc['ubuntu']['sha256']}
        result = Result()
        stamp = utcnow()
        result.set(('fetch', 'files', sums_index, 'sha256'), sha256_bytes(sums), sums_url, authentication, retrieved_at=stamp)
        result.set(('fetch', 'files', sig_index, 'sha256'), sha256_bytes(signature), sig_url, authentication, retrieved_at=stamp)
        result.set(('fetch', 'iso_signing_fingerprint'), signed_by[0], sig_url, authentication, retrieved_at=stamp)
        return result


class TeiVariant(Item):
    family = 'tei'

    def __init__(self, name):
        self.name = name

    def paths(self, doc):
        return [('tei', 'variants', self.name, 'digest')]

    def pending(self, ctx, doc):
        if ctx.decision('tei_drop', self.name):
            return self.name in doc['tei']['variants']
        return super().pending(ctx, doc)

    def exact_tag(self, doc):
        variant = doc['tei']['variants'][self.name]
        version = doc['tei']['version']
        minor = '.'.join(version.split('.')[:2])
        tag = variant['tag']
        if re.fullmatch(r'(?:[A-Za-z0-9_.]+-)?' + re.escape(version), tag):
            return tag
        match = re.fullmatch(r'((?:[A-Za-z0-9_.]+-)?)' + re.escape(minor), tag)
        if not match:
            raise Refusal('tag ' + tag + ' does not carry TEI ' + version + ' or its minor ' + minor +
                          '; a floating tag is never pinned')
        return match[1] + version

    def plan(self, ctx, doc):
        reason = ctx.decision('tei_drop', self.name)
        if reason:
            return ['DROP tei.variants.' + self.name + ': ' + reason]
        try:
            tag = self.exact_tag(doc)
        except Refusal as exc:
            return ['REFUSE: ' + str(exc)]
        return ['registry GET ' + TEI_REPOSITORY + ':' + tag + ' (anonymous token), pick the single linux/amd64 entry,',
                'GET that manifest by digest and its config blob (sha256 checked); config must say linux/amd64;',
                'labels must not contradict ' + doc['tei']['version'] + ' / revision ' + (ctx.lead.get('tei_revision_prefix') or 'e80ef225')]

    def resolve(self, ctx, doc):
        result = Result()
        reason = ctx.decision('tei_drop', self.name)
        if reason:
            result.deletes.append(('tei', 'variants', self.name))
            result.decisions.append({'id': 'tei_drop.' + self.name, 'what': 'tei.variants.' + self.name + ' removed',
                                     'source': reason})
            result.records.append({'path': 'tei.variants.' + self.name, 'status': 'dropped', 'value': None,
                                   'source': reason, 'authentication': {'method': 'lead decision'}, 'retrieved_at': utcnow()})
            return result
        version = doc['tei']['version']
        tag = self.exact_tag(doc)
        record = ctx.registry.resolve_amd64(TEI_REPOSITORY + ':' + tag)
        labels = record['labels']
        label_version = labels.get('org.opencontainers.image.version')
        if label_version and not re.search(r'(^|[^0-9.])' + re.escape(version) + r'($|[^0-9.])', label_version):
            raise Refusal('image label version ' + label_version + ' is not ' + version)
        prefix = ctx.lead.get('tei_revision_prefix') or 'e80ef225'
        label_revision = labels.get('org.opencontainers.image.revision')
        if label_revision and not label_revision.startswith(prefix):
            raise Refusal('image label revision ' + label_revision + ' does not start with the recorded ' + prefix)
        try:
            _, api, path, _, _ = parse_reference(TEI_REPOSITORY)
            _, _, minor_digest = ctx.registry.manifest(api, path, doc['tei']['variants'][self.name]['tag'])
            record['minor_tag'] = {'tag': doc['tei']['variants'][self.name]['tag'], 'digest': minor_digest,
                                   'same_as_exact': minor_digest == record['top_digest']}
        except Refusal as exc:
            record['minor_tag'] = {'unavailable': str(exc)[:200]}
        record['version_label_present'] = bool(label_version)
        record['note'] = '/info must report ' + version + ' in the lab check (BUILD-DAY step 4)'
        result.set(('tei', 'variants', self.name, 'digest'), record['manifest_digest'],
                   'https://ghcr.io/v2/huggingface/text-embeddings-inference/manifests/' + tag,
                   dict(record, method='registry manifest digest of an immutable reference'), retrieved_at=utcnow())
        return result


class BaseImage(Item):
    family, name = 'base', 'base'

    def paths(self, doc):
        return [('runtime', 'base_image')]

    def plan(self, ctx, doc):
        return ['resolve lead input runtime_base (' + (ctx.lead.get('runtime_base') or 'MISSING') + ') to its linux/amd64 manifest digest;',
                'config Env must hold PYTHON_VERSION=' + doc['runtime']['python'] + '; docker pull by digest;',
                'docker run --network none: os-release (must match lead input runtime_base_os), python, pip, gcc, libgomp',
                'runtime.base_image_id and runtime.base_archive_sha256 stay TO-PIN: the first real fetch measures them']

    def resolve(self, ctx, doc):
        reference = ctx.lead.get('runtime_base', '')
        python = doc['runtime']['python']
        if not reference:
            raise Refusal('lead input runtime_base (repository:tag of the CPython ' + python + ' base) is not given')
        _, _, _, tag, digest = parse_reference(reference)
        if not digest and (not tag or not re.search(r'(^|[^0-9.])' + re.escape(python) + r'($|[^0-9.])', tag)):
            raise Refusal('base tag ' + str(tag) + ' does not carry the exact Python ' + python + '; a floating tag is never pinned')
        record = ctx.registry.resolve_amd64(reference)
        if 'PYTHON_VERSION=' + python not in record['env']:
            raise Refusal('base image environment does not say PYTHON_VERSION=' + python)
        pinned = record['repository'] + '@' + record['manifest_digest']
        ctx.docker_pull(pinned)
        script = ('cat /etc/os-release; echo D32-SEP; python -c "import sys; print(sys.version.split()[0])"; echo D32-SEP; '
                  'python -m pip --version; echo D32-SEP; gcc --version | head -n 1; echo D32-SEP; ldconfig -p | grep -c "libgomp.so.1" || true')
        run = ctx.host.run(['docker', 'run', '--rm', '--network', 'none', '--platform', 'linux/amd64', pinned, 'sh', '-c', script],
                           timeout=600)
        if run.returncode:
            raise Refusal('base image checks failed to run: ' + tail(run.stderr))
        parts = run.stdout.decode('utf-8', 'replace').split('D32-SEP')
        if len(parts) != 5:
            raise Refusal('base image checks gave unexpected output')
        release = dict(re.findall(r'^([A-Z_]+)="?([^"\n]*)"?$', parts[0], re.MULTILINE))
        found = {'os': (release.get('ID', '') + ' ' + release.get('VERSION_ID', '')).strip(), 'python': parts[1].strip(),
                 'pip': parts[2].strip(), 'gcc': parts[3].strip(), 'libgomp_entries': parts[4].strip()}
        if found['python'] != python:
            raise Refusal('the base runs Python ' + found['python'] + ', not ' + python)
        if not found['pip'].startswith('pip ') or not found['gcc'] or found['libgomp_entries'] in ('', '0'):
            raise Refusal('the base lacks pip, gcc or libgomp: ' + json.dumps(found))
        wanted = (ctx.lead.get('runtime_base_os') or '').strip().lower()
        if not wanted:
            raise Refusal('PINS.md asks for an Ubuntu 24.04 base; this image is ' + found['os'] +
                          '. State the accepted OS in lead input runtime_base_os')
        if found['os'].lower() != wanted:
            raise Refusal('the base OS is ' + found['os'] + ', the lead accepted ' + wanted)
        result = Result()
        result.set(('runtime', 'base_image'), pinned, 'registry ' + reference,
                   dict(record, method='registry manifest digest of an immutable reference', checks=found,
                        accepted_os=wanted), retrieved_at=utcnow())
        return result


def hf_files(ctx, model_id, revision, role=None):
    """Return (revision info, sorted file rows, per-file evidence, file bytes of small files)."""
    info = ctx.net.get_ok(HF + '/api/models/' + model_id + '/revision/' + revision + '?blobs=true').json()
    if info.get('sha') != revision:
        raise Refusal('Hugging Face did not return commit ' + revision + ' for ' + model_id)
    siblings = {row['rfilename']: row for row in info.get('siblings') or []}
    bundle_prefix = ('pipeline bundle ' + role + ': '
                     if role in PIPELINE_BUNDLE_ROLES and 'config.json' not in siblings else '')
    if not siblings:
        raise Refusal(bundle_prefix + 'the revision API lists no files for ' + model_id)
    tree = {}
    url = HF + '/api/models/' + model_id + '/tree/' + revision + '?recursive=true'
    for _ in range(1000):
        response = ctx.net.get_ok(url)
        for entry in response.json():
            if entry.get('type') == 'file':
                tree[entry['path']] = entry
        link = re.search(r'<([^>]+)>;\s*rel="next"', response.headers.get('link', ''))
        if not link:
            break
        url = link[1]
    if set(tree) != set(siblings):
        missing = sorted(set(tree) ^ set(siblings))
        raise Refusal(bundle_prefix + 'incomplete file list for ' + model_id + '@' + revision + ': revision API and tree listing differ on ' +
                      ', '.join(missing[:10]))
    rows, evidence, small = [], [], {}
    for path in sorted(siblings):
        sibling, entry = siblings[path], tree[path]
        if not relative_ok(path):
            raise Refusal(bundle_prefix + 'unsafe file name in the snapshot: ' + path)
        lfs = sibling.get('lfs')
        if lfs:
            if bundle_prefix and not isinstance(lfs.get('sha256'), str):
                raise Refusal(bundle_prefix + 'LFS sha256 for ' + path + ' must be a full 64-hex string')
            digest = (lfs.get('sha256') or '').lower()
            if not re.fullmatch(HEX64, digest) or (entry.get('lfs') or {}).get('oid') != digest:
                raise Refusal(bundle_prefix + 'LFS sha256 for ' + path + ' is missing or differs between the two listings')
            rows.append({'path': path, 'sha256': digest})
            evidence.append({'lfs': True, 'size': lfs.get('size'), 'blob_id': sibling.get('blobId')})
        else:
            blob = sibling.get('blobId')
            if not isinstance(blob, str) or not re.fullmatch(HEX40, blob) or entry.get('oid') != blob:
                raise Refusal(bundle_prefix + 'git blob id for ' + path + ' is missing or differs between the two listings')
            data = ctx.net.get_ok(HF + '/' + model_id + '/resolve/' + revision + '/' + urllib.parse.quote(path, safe='/')).body
            if hashlib.sha1(b'blob ' + str(len(data)).encode('ascii') + b'\0' + data).hexdigest() != blob:
                raise Refusal(bundle_prefix + 'downloaded ' + path + ' does not match its git blob id')
            rows.append({'path': path, 'sha256': sha256_bytes(data)})
            evidence.append({'lfs': False, 'size': len(data), 'blob_id': blob})
            small[path] = data
    return info, rows, evidence, small


def remote_code_problems(files, small):
    """auto_map and sentence-transformers module references that the snapshot does not carry."""
    problems, cross = [], []
    names = set(files)
    for path, data in small.items():
        if not path.endswith('.json') or '/' in path:
            continue
        try:
            config = json.loads(data.decode('utf-8'))
        except ValueError:
            continue
        references = []
        if isinstance(config, dict):
            for value in (config.get('auto_map') or {}).values():
                references += value if isinstance(value, list) else [value]
        if path == 'modules.json' and isinstance(config, list):
            references += [row.get('type', '') for row in config if isinstance(row, dict)
                           and not str(row.get('type', '')).startswith('sentence_transformers.')]
        for reference in references:
            if not isinstance(reference, str) or not reference:
                continue
            if '--' in reference:
                cross.append(path + ': ' + reference)
                continue
            module = reference.rsplit('.', 1)[0].replace('.', '/') + '.py'
            if module not in names:
                problems.append(path + ' needs ' + module)
    return problems, cross


class ModelItem(Item):
    family = 'models'
    REMOTE_CODE = ('reranker',)

    def __init__(self, name):
        self.name = name

    def paths(self, doc):
        return [('models', self.name, 'id'), ('models', self.name, 'revision'), ('models', self.name, 'files')]

    def plan(self, ctx, doc):
        model = doc['models'][self.name]
        return ['id ' + model['id'] + ' revision ' + model['revision'] + ' (TO-PIN parts from lead inputs; never a branch head)',
                'GET ' + HF + '/api/models/<id>/revision/<rev>?blobs=true and the recursive tree listing; both must agree',
                'LFS files: sha256 from the listings; other files: download at the revision, check the git blob id, hash',
                'remote code: every auto_map / modules.json reference must be in the snapshot']

    def _candidates(self, ctx, name):
        response = ctx.net.get_ok(HF + '/api/models?search=' + urllib.parse.quote(name) + '&limit=100').json()
        return sorted({row['id'] for row in response if isinstance(row, dict) and row.get('id', '').split('/')[-1] == name})

    def _has_revision(self, ctx, model_id, revision):
        try:
            response = ctx.net.fetch(HF + '/api/models/' + model_id + '/revision/' + revision)
        except NetError as exc:
            raise Refusal(str(exc))
        return response.status == 200 and response.json().get('sha') == revision

    def resolve(self, ctx, doc):
        model = doc['models'][self.name]
        lead = (ctx.lead.get('models') or {}).get(self.name) or {}
        model_id, revision = model['id'], model['revision']
        basis = {}
        if is_unresolved(revision):
            revision = lead.get('revision', '')
            if not re.fullmatch(HEX40, revision) or not lead.get('source'):
                raise Refusal('revision is TO-PIN: give the 40-hex commit from the approved local model manifest '
                              'in lead input models.' + self.name + '.revision with its source; a branch head is never taken')
            basis['revision'] = lead['source']
        if not re.fullmatch(HEX40, revision):
            raise Refusal('revision ' + revision + ' is not a 40-hex commit')
        if is_unresolved(model_id):
            name = model_id.split('/', 1)[-1]
            candidates = [lead['id']] if lead.get('id') else self._candidates(ctx, name)
            holders = [value for value in candidates if self._has_revision(ctx, value, revision)]
            if len(holders) != 1:
                raise Refusal('cannot authenticate the repository of ' + name + ': ' + str(len(holders)) +
                              ' of ' + str(len(candidates)) + ' candidates hold commit ' + revision)
            model_id = holders[0]
            basis['id'] = 'the only candidate repository holding commit ' + revision
        info, rows, evidence, small = hf_files(ctx, model_id, revision, role=self.name)
        names = {row['path'] for row in rows}
        pipeline_bundle = 'config.json' not in names and self.name in PIPELINE_BUNDLE_ROLES
        if 'config.json' not in names:
            if self.name not in PIPELINE_BUNDLE_ROLES:
                raise Refusal('snapshot has no root config.json; the kit fetch preflight requires one, so this '
                              'repository needs a reviewed kit change before it can be pinned (role ' + self.name + ')')
            if not rows:
                raise Refusal('pipeline bundle ' + self.name + ' requires a non-empty complete snapshot file list')
            for row in rows:
                if not isinstance(row.get('sha256'), str) or not re.fullmatch(HEX64, row['sha256']):
                    raise Refusal('pipeline bundle ' + self.name + ' requires a full 64-hex sha256: ' + row['path'])
            if not any(path.endswith(('.safetensors', '.pth', '.pt', '.onnx')) for path in names):
                raise Refusal('pipeline bundle ' + self.name + ' requires a weight file (.safetensors, .pth, .pt or .onnx)')
            python_paths = sorted(path for path in names if path.endswith('.py'))
            if python_paths:
                raise Refusal('pipeline bundle ' + self.name + ' carries Python code: ' + python_paths[0])
        problems, cross = remote_code_problems({row['path'] for row in rows}, small)
        if problems:
            if pipeline_bundle:
                raise Refusal('pipeline bundle ' + self.name + ' has incomplete remote code: ' + '; '.join(problems))
            raise Refusal('incomplete snapshot, remote code missing: ' + '; '.join(problems))
        if cross and self.name in self.REMOTE_CODE:
            raise Refusal('remote code is taken from another repository, not pinned by this revision: ' + '; '.join(cross))
        for path, data in small.items():
            write_atomic(ctx.store / 'models' / self.name / path, data)
        total = sum(item.get('size') or 0 for item in evidence)
        authentication = {'method': 'Hugging Face commit id with the revision file list and LFS sha256',
                          'revision_api': HF + '/api/models/' + model_id + '/revision/' + revision + '?blobs=true',
                          'commit': info.get('sha'), 'files': len(rows), 'total_bytes': total, 'basis': basis,
                          'cross_repository_code': cross}
        if self.name == 'mineru_vlm':
            authentication['recorded_size_match'] = total == MINERU_VLM_RECORDED_BYTES
        result = Result()
        stamp = utcnow()
        source = HF + '/' + model_id + '/tree/' + revision
        result.set(('models', self.name, 'id'), model_id, source, authentication, retrieved_at=stamp)
        result.set(('models', self.name, 'revision'), revision, source, authentication, retrieved_at=stamp)
        result.set(('models', self.name, 'files'), rows, source, authentication, retrieved_at=stamp, elements=evidence)
        return result


class BuildTools(Item):
    family, name = 'tools', 'build_tools'

    def paths(self, doc):
        return [('runtime', 'build_tools', tool) for tool in ('python', 'tar', 'xorriso')]

    def plan(self, ctx, doc):
        return ['python3 -I -B kit/build/tool-versions.py --show --require tar|xorriso on this build host',
                'each executable: readlink -f, dpkg-query -S, dpkg-query -W, dpkg -V (unmodified), sha256sum']

    def resolve(self, ctx, doc):
        script = ctx.kit / 'build' / 'tool-versions.py'
        values = {}
        for tool in ('tar', 'xorriso'):
            run = ctx.host.run(['python3', '-I', '-B', str(script), '--show', '--require', tool])
            if run.returncode:
                raise Refusal('tool-versions.py failed for ' + tool + ': ' + tail(run.stderr))
            values.update(json.loads(run.stdout.decode('utf-8')))
        checks = {}
        for tool, command in (('python', 'python3'), ('tar', 'tar'), ('xorriso', 'xorriso')):
            where = ctx.host.which(command)
            if not where:
                raise Refusal(command + ' is not installed on this host')
            real = ctx.host.run(['readlink', '-f', where]).stdout.decode().strip() or where
            owner = ctx.host.run(['dpkg-query', '-S', real])
            if owner.returncode:
                raise Refusal(real + ' is not owned by an installed package')
            package = owner.stdout.decode().split(':')[0].strip()
            version = ctx.host.run(['dpkg-query', '-W', '-f=${Package}=${Version}', package]).stdout.decode().strip()
            verify = ctx.host.run(['dpkg', '-V', package])
            if verify.returncode or real in verify.stdout.decode():
                raise Refusal(real + ' differs from its package (dpkg -V)')
            digest = ctx.host.run(['sha256sum', real]).stdout.decode().split()[:1]
            checks[tool] = {'executable': real, 'package': version, 'dpkg_verify': 'clean',
                            'sha256': digest[0] if digest else ''}
        result = Result()
        for tool in ('python', 'tar', 'xorriso'):
            result.set(('runtime', 'build_tools', tool), values[tool], 'kit/build/tool-versions.py --show',
                       {'method': 'installed Ubuntu package, files verified by dpkg', **checks[tool]}, retrieved_at=utcnow())
        return result


def repo_base(doc, repo, snapshot):
    rows = doc['fetch']['repositories'][repo]['metadata']
    inrelease = [row for row in rows if row['path'].endswith('InRelease')]
    if not inrelease:
        raise Refusal(repo + ': only InRelease metadata is supported by this resolver')
    url = inrelease[0]['url']
    if repo.startswith('ubuntu-'):
        url = url.replace('TO-PIN', snapshot)
    if not url.endswith(inrelease[0]['path']):
        raise Refusal(repo + ': InRelease URL does not end with its repository path')
    return url[:-len(inrelease[0]['path'])], inrelease[0]


def snapshot_id(ctx, repo):
    if not repo.startswith('ubuntu-'):
        return None
    value = ctx.lead.get('ubuntu_snapshot', '')
    if not re.fullmatch(SNAPSHOT_ID, value):
        raise Refusal('lead input ubuntu_snapshot (YYYYMMDDTHHMMSSZ, a dated snapshot.ubuntu.com state) is not given')
    try:
        moment = dt.datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=dt.timezone.utc)
    except ValueError:
        raise Refusal('ubuntu_snapshot ' + value + ' is not a valid UTC date and time')
    # A time still to come is not a fixed archive state yet; the service starts on 1 March 2023.
    if moment > dt.datetime.now(dt.timezone.utc):
        raise Refusal('ubuntu_snapshot ' + value + ' is in the future; choose a time that has passed')
    if moment < dt.datetime(2023, 3, 1, tzinfo=dt.timezone.utc):
        raise Refusal('ubuntu_snapshot ' + value + ' is before 1 March 2023, where the snapshot service starts')
    return value


def verify_release(ctx, repo, rdoc, keyring, inrelease_path):
    """gpgv the stored InRelease; returns (signed text, status, VALIDSIG pairs)."""
    raw = inrelease_path.read_bytes()
    if not raw.startswith(b'-----BEGIN PGP SIGNED MESSAGE-----'):
        raise Refusal(repo + ': InRelease does not begin with a clearsigned header')
    output = inrelease_path.with_name(inrelease_path.name + '.verified')
    if output.exists():
        output.unlink()
    code, status, error = ctx.gpg.verify(keyring, [inrelease_path], output=output)
    if code:
        raise Refusal(repo + ': gpgv rejected InRelease (exit ' + str(code) + '): ' + error)
    pairs = validsig(status)
    if not any(rdoc['fingerprint'] in pair for pair in pairs):
        raise Refusal(repo + ': InRelease is signed by an unexpected key ' + ', '.join(pair[1] for pair in pairs) +
                      '; the pinned signer is ' + rdoc['fingerprint'])
    text = output.read_bytes().decode('utf-8')
    if rdoc['suite'] != '/':
        # Every Ubuntu pocket carries Codename noble, so for Ubuntu only the Suite field proves the
        # pocket (noble-proposed's signed InRelease must not pass as noble). Vendors may name only a codename.
        accepted = (release_field(text, 'Suite'),) if repo.startswith('ubuntu-') else \
            (release_field(text, 'Suite'), release_field(text, 'Codename'))
        if rdoc['suite'] not in accepted:
            raise Refusal(repo + ': signed Release is for suite ' + str(release_field(text, 'Suite')) + ', not ' + rdoc['suite'])
    valid_until = release_field(text, 'Valid-Until')
    if valid_until and email.utils.parsedate_to_datetime(valid_until) <= dt.datetime.now(dt.timezone.utc):
        raise Refusal(repo + ': signed Release expired at ' + valid_until)
    return text, status, pairs


def release_relative(rdoc, path):
    prefix = '' if rdoc['release_dir'] in ('', '.') else rdoc['release_dir'] + '/'
    if not path.startswith(prefix):
        raise Refusal('metadata path ' + path + ' is outside ' + rdoc['release_dir'])
    return path[len(prefix):]


class AptMetadata(Item):
    family = 'apt'

    def __init__(self, repo):
        self.repo = repo
        self.name = 'metadata-' + repo

    def paths(self, doc):
        return [('fetch', 'repositories', self.repo, 'metadata')]

    def plan(self, ctx, doc):
        rdoc = doc['fetch']['repositories'][self.repo]
        lines = ['signer ' + rdoc['fingerprint'] + ', keyring ' + rdoc['keyring'] + (', snapshot ' + (ctx.lead.get('ubuntu_snapshot') or 'MISSING') if self.repo.startswith('ubuntu-') else '')]
        for row in rdoc['metadata']:
            lines.append('GET ' + row['url'].replace('TO-PIN', ctx.lead.get('ubuntu_snapshot') or 'TO-PIN') + (' ; gpgv --output (signed text), signer, suite, Valid-Until' if row['path'].endswith('InRelease') else ' ; SHA512 (preferred) or SHA256 and size must equal the signed Release entry'))
        return lines

    def resolve(self, ctx, doc):
        rdoc = doc['fetch']['repositories'][self.repo]
        if is_unresolved(rdoc['fingerprint']):
            raise Refusal(self.repo + ': signer fingerprint not resolved: run --family keys first')
        keyring = ctx.keyring(doc, rdoc['keyring'])
        snapshot = snapshot_id(ctx, self.repo)
        base, inrelease_row = repo_base(doc, self.repo, snapshot)
        folder = ctx.store / 'repos' / self.repo
        data = ctx.net.get_ok(base + inrelease_row['path']).body
        write_atomic(folder / inrelease_row['path'], data)
        text, status, pairs = verify_release(ctx, self.repo, rdoc, keyring, folder / inrelease_row['path'])
        hashes = release_hashes(text)
        rows, elements = [], []
        result = Result()
        for row in rdoc['metadata']:
            row = dict(row)
            if row['path'] == inrelease_row['path']:
                row['url'] = base + row['path']
                row['sha256'] = sha256_bytes(data)
                rows.append(row)
                elements.append({'signed_by': pairs, 'date': release_field(text, 'Date'),
                                 'valid_until': release_field(text, 'Valid-Until'), 'suite': release_field(text, 'Suite')})
                continue
            relative = release_relative(rdoc, row['path'])
            if relative not in hashes:
                stem = re.sub(r'\.(xz|gz)$', '', relative)
                choices = [stem + suffix for suffix in ('.xz', '.gz', '') if stem + suffix in hashes]
                if not choices:
                    raise Refusal(self.repo + ': ' + relative + ' is not listed in the signed Release')
                replacement = row['path'][:len(row['path']) - len(relative)] + choices[0]
                result.decisions.append({'id': 'apt.' + self.repo + '.' + relative,
                                         'what': row['path'] + ' replaced by ' + replacement,
                                         'source': 'the signed Release lists only ' + choices[0]})
                row['path'], relative = replacement, choices[0]
            row['url'] = base + row['path']
            body = ctx.net.get_ok(row['url']).body
            evidence = verify_release_bytes(hashes, relative, body, self.repo)
            write_atomic(folder / row['path'], body)
            row['sha256'] = evidence['sha256']
            rows.append(row)
            elements.append(evidence)
        result.set(('fetch', 'repositories', self.repo, 'metadata'), rows, base + inrelease_row['path'],
                   {'method': 'signed InRelease, gpgv against the pinned keyring', 'signer': rdoc['fingerprint'],
                    'keyring_sha256': sha256_file(keyring)}, retrieved_at=utcnow(), elements=elements)
        return result


APT_SCRIPT = r'''set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
mode=$1
mkdir -p /tmp/parts
opts=(-o Dir::Etc::sourcelist=/in/sources.list -o Dir::Etc::sourceparts=/tmp/parts -o Acquire::Languages=none -o Acquire::Retries=3)
apt-get "${opts[@]}" update >&2
mapfile -t req < /in/request.txt
if [ "$mode" = closure ]; then
  : > /tmp/empty-status
  apt-get "${opts[@]}" -o Dir::State::status=/tmp/empty-status -s --no-install-recommends install "${req[@]}" > /tmp/plan
  grep '^Inst ' /tmp/plan | sed 's/^/D32-INST /'
  # The kit's install-packages.sh simulates WITH recommends before refusing DKMS or a driver metapackage.
  apt-get "${opts[@]}" -o Dir::State::status=/tmp/empty-status -s install "${req[@]}" > /tmp/kit-plan
  grep '^Inst ' /tmp/kit-plan | sed 's/^/D32-KIT /'
else
  apt-get "${opts[@]}" -y --no-install-recommends install kmod "${req[@]}" >&2
  for f in $(find /lib/modules -type f -name 'nvidia.ko*' | sort); do
    echo "D32-MOD file=$f"
    echo "D32-MOD version=$(modinfo -F version "$f" 2>/dev/null || true)"
    echo "D32-MOD signer=$(modinfo -F signer "$f" 2>/dev/null || true)"
  done
  for r in "${req[@]}"; do
    dpkg-query -W -f='D32-PKG ${Package}\t${Status}\t${Version}\n' "${r%%=*}" || true
  done
fi
'''


def load_indexes(ctx, doc):
    """Re-verify every stored repository from its pins (offline when the store is complete)."""
    indexes, bases = {}, {}
    for repo in REPOSITORIES:
        rdoc = doc['fetch']['repositories'][repo]
        if is_unresolved(rdoc['metadata']) or is_unresolved(rdoc['fingerprint']):
            raise Refusal(repo + ' metadata is not resolved: run the apt metadata items first')
        keyring = ctx.keyring(doc, rdoc['keyring'])
        folder = ctx.store / 'repos' / repo
        for row in rdoc['metadata']:
            path = folder / row['path']
            if not path.is_file() or sha256_file(path) != row['sha256']:
                write_atomic(path, ctx.net.get_ok(row['url']).body)
            if sha256_file(path) != row['sha256']:
                raise Refusal(repo + ': ' + row['path'] + ' differs from its pinned hash')
        inrelease = [row for row in rdoc['metadata'] if row['path'].endswith('InRelease')][0]
        text, _, _ = verify_release(ctx, repo, rdoc, keyring, folder / inrelease['path'])
        hashes = release_hashes(text)
        index = {}
        for row in rdoc['metadata']:
            if row is inrelease:
                continue
            relative = release_relative(rdoc, row['path'])
            data = (folder / row['path']).read_bytes()
            verify_release_bytes(hashes, relative, data, repo)
            for stanza in parse_stanzas(decompress(row['path'], data).decode('utf-8')):
                if 'Package' in stanza and 'Version' in stanza and 'Architecture' in stanza:
                    index[(stanza['Package'], stanza['Version'], stanza['Architecture'])] = dict(stanza, _index=row['path'])
        indexes[repo] = index
        bases[repo] = inrelease['url'][:-len(inrelease['path'])]
    return indexes, bases


def profile(doc):
    value = doc.get('release_profile', 'iso')
    if value not in ('pack', 'iso'):
        raise UsageError('release_profile must be pack or iso')
    return value


def iso_package(name):
    name = name.split('=', 1)[0]
    nvidia_driver = (name.startswith(('nvidia-', 'libnvidia-')) and
                     not name.startswith(('nvidia-container-', 'libnvidia-container')))
    return (name in {'ubuntu-drivers-common', 'kmod'} or nvidia_driver or 'dkms' in name or
            name.startswith(('linux-', 'cuda-drivers', 'xserver-xorg-video-nvidia-')))


def direct_entries(doc):
    """(path, list position or None, name, current version) for every directly pinned package."""
    rows = []
    for position, value in enumerate(doc['packages']):
        if profile(doc) == 'pack' and iso_package(value):
            continue
        rows.append((('packages',), position, value.split('=', 1)[0], value.split('=', 1)[1]))
    if profile(doc) == 'pack':
        return rows
    for position, value in enumerate(doc['ubuntu']['driver_packages']):
        rows.append((('ubuntu', 'driver_packages'), position, value.split('=', 1)[0], value.split('=', 1)[1]))
    for field in ('kernel_package', 'modules_package'):
        value = doc['ubuntu'][field]
        rows.append((('ubuntu', field), None, value.split('=', 1)[0], value.split('=', 1)[1]))
    return rows


def home_repository(name):
    if name in NVIDIA_TOOLKIT:
        return ('nvidia',)
    if name == 'tailscale':
        return ('tailscale',)
    if name.startswith(('linux-', 'nvidia-utils-', 'libnvidia-compute-')) or DRIVER_BRANCH in name:
        return ('ubuntu-noble', 'ubuntu-updates', 'ubuntu-security')
    return REPOSITORIES


# apt-get -s may end an Inst line with ' []' (lab 2026-11-15 on 901: 'Inst libc6 (2.39-0ubuntu8.12
# Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security [amd64]) []'): one optional trailing bracket group.
INST = re.compile(r'^Inst (\S+) (?:\[[^\]]*\] )?\((\S+) .*\[([^\]]+)\]\)(?: \[[^\]]*\])?\s*$')
# The exact refusal of kit/autoinstall/install-packages.sh (grep -Ei) on its install-time simulation.
KIT_PLAN_REFUSAL = re.compile(r'(^Inst .*dkms|^Inst (cuda-drivers|nvidia-driver))', re.IGNORECASE)


class AptClosure(Item):
    family, name = 'apt', 'closure'

    def paths(self, doc):
        return [('fetch', 'repositories', repo, 'packages') for repo in REPOSITORIES] + \
            ([('packages',)] if profile(doc) == 'pack' else [('packages',), ('ubuntu', 'driver_packages'), ('ubuntu', 'kernel_package'), ('ubuntu', 'modules_package')])

    def pending(self, ctx, doc):
        if profile(doc) == 'pack':
            pending = (any(is_unresolved(current) for _, _, _, current in direct_entries(doc)) or
                       any(is_unresolved(doc['fetch']['repositories'][repo]['packages']) for repo in REPOSITORIES))
        else:
            pending = super().pending(ctx, doc)
        if pending:
            return True
        # Template placeholders count as unresolved even when the lists are empty.
        return any(not doc['fetch']['repositories'][repo]['packages'] for repo in ('ubuntu-noble', 'nvidia', 'tailscale'))

    def plan(self, ctx, doc):
        return ['re-verify every stored InRelease and Packages index against its pins (gpgv, SHA512 or SHA256, size)',
                'tool container: ' + (ctx.lead.get('apt_tool_image') or 'docker.io/library/ubuntu:24.04') + ' resolved to its amd64 digest, pulled by digest',
                'in the container: apt-get update against the five signed sources (same keyrings), then',
                '  apt-get -s --no-install-recommends install <direct packages> with an EMPTY dpkg status (full closure)',
                'every Inst line must match one signed Packages entry (name, version, architecture); the sha256 comes from that entry',
                'NVIDIA toolkit from the NVIDIA repository, tailscale from Tailscale, kernel and driver packages from Ubuntu; no DKMS',
                'no DKMS or driver metapackage either in that closure or in a second simulation WITH recommends (the kit\'s install-time check)',
                ('pack: refuse kernel, module and host-driver packages; WSL uses the Windows NVIDIA driver' if profile(doc) == 'pack' else
                 'kernel ABI ' + (ctx.lead.get('kernel_abi') or '(lead input kernel_abi)') + ' and its signed ' + DRIVER_BRANCH + ' module package must be in the closure')]

    def apt_container(self, ctx, result, doc, request, mode, bases):
        image = ctx.tool_image(result, 'apt_tool_image', 'docker.io/library/ubuntu:24.04')
        folder = ctx.work / 'apt-in'
        if folder.exists():
            shutil.rmtree(folder)
        (folder / 'keyrings').mkdir(parents=True)
        lines = []
        for repo in REPOSITORIES:
            rdoc = doc['fetch']['repositories'][repo]
            keyring = PurePosixPath(rdoc['keyring']).name
            shutil.copyfile(ctx.keyring(doc, rdoc['keyring']), folder / 'keyrings' / keyring)
            option = '[arch=amd64 signed-by=/in/keyrings/' + keyring + ']'
            if repo.startswith('ubuntu-'):
                lines.append('deb ' + option + ' ' + bases[repo].rstrip('/') + ' ' + rdoc['suite'] +
                             ' main restricted universe multiverse')
            elif rdoc['suite'] == '/':
                lines.append('deb ' + option + ' ' + bases[repo].rstrip('/') + ' /')
            else:
                lines.append('deb ' + option + ' ' + bases[repo].rstrip('/') + ' ' + rdoc['suite'] + ' main')
        (folder / 'sources.list').write_text('\n'.join(lines) + '\n', encoding='ascii')
        (folder / 'request.txt').write_text('\n'.join(request) + '\n', encoding='ascii')
        argv = ['docker', 'run', '--rm', '-i', '--network', 'bridge', '-v', str(folder) + ':/in:ro']
        if Path('/etc/ssl/certs/ca-certificates.crt').is_file():
            argv += ['-v', '/etc/ssl/certs/ca-certificates.crt:/etc/ssl/certs/ca-certificates.crt:ro']
        run = ctx.host.run(argv + [image, 'bash', '-s', '--', mode], input=APT_SCRIPT.encode('ascii'), timeout=7200)
        if run.returncode:
            raise Refusal('apt ' + mode + ' in the throwaway container failed (exit ' + str(run.returncode) + '): ' +
                          tail(run.stderr))
        return image, run.stdout.decode('utf-8', 'replace')

    def resolve(self, ctx, doc):
        indexes, bases = load_indexes(ctx, doc)
        wanted = ctx.lead.get('package_versions') or {}
        request, basis = [], {}
        for _, _, name, current in direct_entries(doc):
            if not is_unresolved(current):
                request.append(name + '=' + current)
                basis[name] = 'already pinned'
            elif wanted.get(name):
                request.append(name + '=' + wanted[name])
                basis[name] = 'lead input package_versions'
            elif name in NVIDIA_TOOLKIT or name == 'tailscale':
                repo = home_repository(name)[0]
                versions = sorted({key[1] for key in indexes[repo] if key[0] == name}, key=_DebKey)
                if not versions:
                    raise Refusal(name + ' is absent from the signed ' + repo + ' index')
                request.append(name + '=' + versions[-1])
                basis[name] = 'highest version in the signed ' + repo + ' index retrieved ' + utcnow()
            else:
                request.append(name)
                basis[name] = 'apt candidate in the dated snapshot ' + (ctx.lead.get('ubuntu_snapshot') or '')
        result = Result()
        image, output = self.apt_container(ctx, result, doc, request, 'closure', bases)
        closure, kit_plan = [], []
        for line in output.splitlines():
            if line.startswith('D32-INST '):
                text = line[len('D32-INST '):]
                match = INST.match(text)
                if not match:
                    raise Refusal('unparsed apt line: ' + line[:200])
                if KIT_PLAN_REFUSAL.search(text):
                    raise Refusal('the closure pulls ' + text[:160] + '; DKMS or a driver metapackage is never accepted '
                                  '(the kit\'s install-packages.sh refuses it)')
                if profile(doc) == 'pack' and iso_package(match[1]):
                    raise Refusal('pack closure contains an ISO-only package: ' + match[1])
                closure.append(match.groups())
            elif line.startswith('D32-KIT '):
                kit_plan.append(line[len('D32-KIT '):])
        if not closure:
            raise Refusal('apt simulation returned no packages')
        if not kit_plan:
            raise Refusal('the install-time simulation with recommends (as the kit runs it) returned no plan')
        for text in kit_plan:
            match = INST.match(text)
            if profile(doc) == 'pack' and match and iso_package(match[1]):
                raise Refusal('pack install-time simulation contains an ISO-only package: ' + match[1])
            if KIT_PLAN_REFUSAL.search(text):
                raise Refusal('with recommends, as the kit\'s install-time simulation runs, apt adds ' + text[:160] +
                              '; install-packages.sh refuses a plan with that line (exit 3). This plan starts from an '
                              'empty dpkg status, so it can list more than the target would: review before any kit change')
        chosen = {}
        per_repo = {repo: [] for repo in REPOSITORIES}
        elements = {repo: [] for repo in REPOSITORIES}
        paths = set()
        for name, version, architecture in closure:
            hits =[(repo, indexes[repo][(name, version, architecture)]) for repo in REPOSITORIES
                    if (name, version, architecture) in indexes[repo]]
            if not hits:
                raise Refusal('closure member ' + name + '=' + version + ' [' + architecture +
                              '] is not covered by any signed Packages entry')
            if len({stanza.get('SHA256') for _, stanza in hits}) != 1:
                raise Refusal(name + '=' + version + ' has different hashes in different signed indexes')
            hits = [hit for hit in hits if hit[0] in home_repository(name)]
            if not hits:
                raise Refusal(name + '=' + version + ' does not come from its required repository ' + '/'.join(home_repository(name)))
            repo, stanza = hits[0]
            digest = (stanza.get('SHA256') or '').lower()
            path = stanza.get('Filename', '').removeprefix('./')
            if not re.fullmatch(HEX64, digest) or not relative_ok(path):
                raise Refusal(name + '=' + version + ': signed entry has no usable SHA256 or Filename')
            if path in paths or name in chosen:
                raise Refusal(name + ' appears twice in the closure')
            paths.add(path)
            chosen[name] = version
            per_repo[repo].append({'architecture': architecture, 'name': name, 'path': path, 'sha256': digest,
                                   'url': bases[repo] + path, 'version': version})
            elements[repo].append({'packages_index': stanza['_index'], 'size': stanza.get('Size'),
                                   'apt': 'Inst ' + name + ' ' + version + ' [' + architecture + ']'})
        abi = None
        if profile(doc) == 'iso':
            abis = sorted({match[1] for name in chosen for match in [re.fullmatch(r'linux-image-(\d+\.\d+\.\d+-\d+)-generic', name)] if match})
            if len(abis) != 1:
                raise Refusal('the closure holds ' + str(len(abis)) + ' generic kernel images; exactly one is required')
            abi = abis[0]
            if ctx.lead.get('kernel_abi') and ctx.lead['kernel_abi'] != abi:
                raise Refusal('the snapshot kernel ABI is ' + abi + ', the lab-checked one is ' + ctx.lead['kernel_abi'])
            module_package = 'linux-modules-nvidia-' + DRIVER_BRANCH + '-' + abi + '-generic'
            if module_package not in chosen:
                raise Refusal('the closure has no signed module package ' + module_package)
        stamp = utcnow()
        authentication = {'method': 'signed InRelease -> Release -> Packages entry', 'tool_image': image,
                          'closure': 'apt-get -s --no-install-recommends with an empty dpkg status', 'kernel_abi': abi}
        for repo in REPOSITORIES:
            order = sorted(range(len(per_repo[repo])), key=lambda i: (per_repo[repo][i]['name'], per_repo[repo][i]['version']))
            rows = [per_repo[repo][i] for i in order]
            extras = [dict(elements[repo][i], signer=doc['fetch']['repositories'][repo]['fingerprint']) for i in order]
            result.set(('fetch', 'repositories', repo, 'packages'), rows, bases[repo],
                       dict(authentication, repository=repo), retrieved_at=stamp, elements=extras)
        for path, position, name, _ in direct_entries(doc):
            if name not in chosen:
                raise Refusal('direct package ' + name + ' is missing from the closure')
        packages = [entry.split('=', 1)[0] + '=' + chosen[entry.split('=', 1)[0]]
                    if entry.split('=', 1)[0] in chosen else entry for entry in doc['packages']]
        basis_auth = dict(authentication, request=request)
        result.set(('packages',), packages, 'apt closure', basis_auth, retrieved_at=stamp,
                   elements=[{'selection': basis.get(value.split('=')[0], 'deferred: v1.1 ISO form')} for value in packages])
        if profile(doc) == 'pack':
            return result
        drivers = [entry.split('=', 1)[0] + '=' + chosen[entry.split('=', 1)[0]] for entry in doc['ubuntu']['driver_packages']]
        result.set(('ubuntu', 'driver_packages'), drivers, 'apt closure', basis_auth, retrieved_at=stamp,
                   elements=[{'selection': basis[value.split('=')[0]]} for value in drivers])
        for field in ('kernel_package', 'modules_package'):
            name = doc['ubuntu'][field].split('=', 1)[0]
            result.set(('ubuntu', field), name + '=' + chosen[name], 'apt closure', dict(basis_auth, selection=basis[name]),
                       retrieved_at=stamp)
        return result


class _DebKey:
    def __init__(self, value):
        self.value = value

    def __lt__(self, other):
        return deb_compare(self.value, other.value) < 0


class ModuleIdentity(Item):
    family, name = 'module', 'module'

    def paths(self, doc):
        return [('ubuntu', 'module_version'), ('ubuntu', 'module_signer')]

    def plan(self, ctx, doc):
        return ['install the pinned ABI-specific signed module package and kmod in the throwaway container (snapshot sources)',
                'select exactly one nvidia.ko or nvidia.ko.zst in the requested ABI kernel/nvidia-' + DRIVER_BRANCH.split('-')[0] + 'srv folder',
                'check modinfo version and signer and dpkg-query -W installed status and pinned package version',
                'the driver user-space version must match the module version']

    def resolve(self, ctx, doc):
        if is_unresolved(doc['ubuntu']['modules_package']) or is_unresolved(doc['fetch']['repositories']['ubuntu-noble']['packages']):
            raise Refusal('the apt closure is not resolved: run --family apt first')
        rows = [row for repo in REPOSITORIES[:3] for row in doc['fetch']['repositories'][repo]['packages']]
        matches = [row for row in rows if re.fullmatch(r'linux-modules-nvidia-' + DRIVER_BRANCH + r'-\d+\.\d+\.\d+-\d+-generic', row['name'])]
        if len(matches) != 1:
            raise Refusal('expected exactly one ABI-specific signed module package in the closure')
        request = [matches[0]['name'] + '=' + matches[0]['version']]
        configured = doc['ubuntu']['modules_package']
        configured_name, _, configured_version = configured.partition('=')
        allowed_names = (matches[0]['name'], 'linux-modules-nvidia-' + DRIVER_BRANCH + '-generic')
        if configured_name not in allowed_names or not any(
                row['name'] == configured_name and row['version'] == configured_version for row in rows):
            raise Refusal('the signed module package pin does not match the apt closure: ' + configured)
        indexes, bases = load_indexes(ctx, doc)
        result = Result()
        image, output = AptClosure().apt_container(ctx, result, doc, request, 'module', bases)
        modules, installed = [], []
        current = None
        for line in output.splitlines():
            if line.startswith('D32-MOD '):
                key, _, value = line[len('D32-MOD '):].partition('=')
                if key == 'file':
                    current = dict(file=value.strip(), owner='', version='', signer='')
                    modules.append(current)
                elif current is not None and key in ('version', 'signer', 'owner'):
                    current[key] = value.strip()
            elif line.startswith('D32-PKG '):
                fields = line[len('D32-PKG '):].split('\t')
                installed.append(dict(zip(('name', 'status', 'version'), fields)) if len(fields) == 3
                                 else {'invalid': line[len('D32-PKG '):]})
        modules.sort(key=lambda row: (row['file'], row['version'], row['signer']))

        def refuse(message):
            raise Refusal(message + '; matches: ' + json.dumps(modules, sort_keys=True, ensure_ascii=True))

        package = matches[0]['name']
        abi = re.fullmatch(r'linux-modules-nvidia-' + DRIVER_BRANCH + r'-(\d+\.\d+\.\d+-\d+)-generic', package).group(1)
        module_root = '/lib/modules/' + abi + '-generic/kernel/nvidia-' + DRIVER_BRANCH.split('-')[0] + 'srv/'
        module_paths = (module_root + 'nvidia.ko', module_root + 'nvidia.ko.zst')
        selected = [row for row in modules if row['file'] in module_paths]
        if len(selected) != 1:
            refuse('expected exactly one nvidia.ko or nvidia.ko.zst at ' + module_root +
                   ', found ' + str(len(selected)) + ' after installing ' + request[0])
        version, signer = selected[0]['version'], selected[0]['signer']
        installed_package = {'name': package, 'status': 'install ok installed', 'version': matches[0]['version']}
        if installed != [installed_package]:
            refuse('the requested module package is not installed at the pinned version: ' +
                   json.dumps(installed, sort_keys=True, ensure_ascii=True))
        if not re.fullmatch(r'\d+\.\d+(\.\d+)?', version) or not version.startswith(DRIVER_BRANCH.split('-')[0] + '.'):
            refuse('module version ' + version + ' is not on the ' + DRIVER_BRANCH + ' branch')
        if not signer:
            refuse('nvidia.ko carries no signer')
        if signer != 'Canonical Ltd. Kernel Module Signing':
            refuse('module_signer ' + signer + ' does not match Canonical Ltd. Kernel Module Signing')
        for key, value in (('module_version', version), ('module_signer', signer)):
            pinned = doc['ubuntu'].get(key, 'TO-PIN')
            if not is_unresolved(pinned) and value != pinned:
                refuse(key + ' ' + value + ' does not match pin ' + pinned)
        for entry in doc['ubuntu']['driver_packages']:
            upstream = entry.split('=', 1)[1].split(':')[-1].split('-')[0]
            if upstream != version:
                refuse(entry + ' does not match module version ' + version)
        authentication = {'method': 'modinfo on the module installed from the signed snapshot in a throwaway container',
                          'tool_image': image, 'package': request[0], 'installed_package': installed_package,
                          'file': selected[0]['file'],
                          'files': [row['file'] for row in selected], 'matches': modules}
        result.set(('ubuntu', 'module_version'), version, 'modinfo -F version', authentication, retrieved_at=utcnow())
        result.set(('ubuntu', 'module_signer'), signer, 'modinfo -F signer', authentication, retrieved_at=utcnow())
        return result


def parse_direct(path, pymupdf):
    lines = []
    for raw in path.read_text(encoding='ascii').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if 'TO-PIN' in line:
            if is_unresolved(pymupdf):
                raise Refusal('runtime.pymupdf_version is TO-PIN: run --family lead with pymupdf_version first')
            line = line.replace('TO-PIN', pymupdf)
        if not re.fullmatch(r'[A-Za-z0-9_.-]+==[A-Za-z0-9.+_-]+', line):
            raise Refusal('direct requirement is not an exact pin: ' + line)
        lines.append(line)
    return lines


def report_hash(item):
    info = item.get('download_info') or {}
    archive = info.get('archive_info') or {}
    digest = (archive.get('hashes') or {}).get('sha256')
    if not digest and str(archive.get('hash', '')).startswith('sha256='):
        digest = archive['hash'][len('sha256='):]
    return (info.get('url') or '').split('#')[0], (digest or '').lower()


class LockItem(Item):
    family = 'locks'

    def __init__(self, name):
        self.name = name

    def paths(self, doc):
        return [('fetch', 'locks', self.name, 'url'), ('fetch', 'locks', self.name, 'sha256')]

    def direct_file(self, ctx):
        return ctx.kit / 'containers' / ('converter-agpl' if self.name == 'converter' else self.name) / 'requirements-direct.txt'

    def plan(self, ctx, doc):
        lock = doc['fetch']['locks'][self.name]
        return ['inside runtime.base_image (' + doc['runtime']['base_image'] + '), as this user, network on:',
                '  lockgen.py pick-torch: pip install --dry-run --no-deps --report for the torch pin from ' + lock['torch_index'],
                '  lockgen.py resolve: pip install --dry-run --ignore-installed --only-binary=:all: --no-binary=jieba --report from ' + lock['index'] + ' with torch as a hashed direct URL',
                'host: torch hash checked on the cu130 simple page; every other archive hash checked on the PyPI JSON API; only the lead-reviewed jieba 0.42.1 sdist is allowed',
                'write store/locks/' + self.name + '.lock: one "name==version --hash=sha256:..." line per distribution',
                '  lockgen.py verify: pip download --require-hashes --no-deps into the container\'s /tmp; wheels use --only-binary=:all:, jieba alone uses --no-binary=jieba',
                'pin fetch.locks.' + self.name + '.url (file:) and .sha256']

    def container(self, ctx, image, work, args):
        argv = ['docker', 'run', '--rm', '--network', 'bridge', '--platform', 'linux/amd64'] + ctx.mount_user() + \
            ['-e', 'HOME=/tmp', '-e', 'PIP_NO_INPUT=1', '-e', 'PYTHONDONTWRITEBYTECODE=1', '-v', str(work) + ':/work',
             '--workdir', '/work', image, 'python', '-I', '-B', '/work/lockgen.py'] + args
        run = ctx.host.run(argv, timeout=6 * 3600)
        if run.returncode:
            raise Refusal('lock step ' + args[0] + ' failed (exit ' + str(run.returncode) + '): ' + tail(run.stderr))
        for line in run.stdout.decode('utf-8', 'replace').splitlines():
            if line.startswith('D32-JSON '):
                return json.loads(line[len('D32-JSON '):])
        raise Refusal('lock step ' + args[0] + ' printed no result')

    def resolve(self, ctx, doc):
        image = doc['runtime']['base_image']
        if is_unresolved(image):
            raise Refusal('runtime.base_image is TO-PIN: run --family base first')
        lock = doc['fetch']['locks'][self.name]
        index, torch_index = lock['index'], lock['torch_index']
        if index.rstrip('/') != 'https://pypi.org/simple':
            raise Refusal('only the PyPI index can be cross-checked by this resolver')
        direct = parse_direct(self.direct_file(ctx), doc['runtime']['pymupdf_version'])
        pins = {normalise_name(line.split('==')[0]): line.split('==')[1] for line in direct}
        torch_lines = [line for line in direct if normalise_name(line.split('==')[0]) == 'torch']
        if len(torch_lines) != 1:
            raise Refusal('the direct requirements need exactly one torch pin')
        work = ctx.work / 'locks' / self.name
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        shutil.copyfile(HERE / 'lockgen.py', work / 'lockgen.py')
        ctx.docker_pull(image)
        torch = self.container(ctx, image, work, ['pick-torch', '--spec', torch_lines[0], '--index', torch_index])
        environment = torch['env']
        if environment.get('python') != doc['runtime']['python']:
            raise Refusal('the lock container runs Python ' + str(environment.get('python')) + ', not ' + doc['runtime']['python'])
        torch_url, torch_sha = torch['url'].split('#')[0], torch['sha256'].lower()
        index_parts = urllib.parse.urlsplit(torch_index)
        if index_parts.scheme != 'https' or index_parts.netloc != 'download.pytorch.org' or \
                index_parts.query or index_parts.fragment:
            raise Refusal('torch index is not an official HTTPS download.pytorch.org index')
        wheel_parts = urllib.parse.urlsplit(torch_url)
        prefix = index_parts.path.rstrip('/') + '/'
        if wheel_parts.scheme != 'https' or \
                wheel_parts.netloc not in (index_parts.netloc, 'download-r2.pytorch.org') or \
                not wheel_parts.path.startswith(prefix) or \
                any(part in ('.', '..') for part in urllib.parse.unquote(wheel_parts.path).split('/')):
            raise Refusal('torch did not come from ' + torch_index)
        filename = urllib.parse.unquote(PurePosixPath(urllib.parse.urlsplit(torch_url).path).name)
        index_url = torch_index.rstrip('/') + '/torch/'
        index_page = ctx.net.get_ok(index_url, redirect_hosts=('download.pytorch.org',)).body
        page = index_page.decode('utf-8', 'replace')
        listed = {}
        for href in re.findall(r'href="([^"]+)"', page):
            name = urllib.parse.unquote(PurePosixPath(urllib.parse.urlsplit(href).path).name)
            fragment = urllib.parse.urlsplit(href).fragment
            if fragment.startswith('sha256='):
                listed[name] = fragment[len('sha256='):].lower()
        if listed.get(filename) != torch_sha:
            raise Refusal('torch wheel ' + filename + ' hash differs from the ' + torch_index + ' listing')
        requirements = [line for line in direct if line not in torch_lines] + ['torch @ ' + torch_url + '#sha256=' + torch_sha]
        (work / 'resolve.txt').write_text('\n'.join(requirements) + '\n', encoding='ascii')
        report = self.container(ctx, image, work, ['resolve', '--requirements', '/work/resolve.txt', '--index', index])['report']
        entries, wheels = {}, []
        for item in report.get('install', []):
            name, version = item['metadata']['name'], item['metadata']['version']
            url, digest = report_hash(item)
            wheel = urllib.parse.unquote(PurePosixPath(urllib.parse.urlsplit(url).path).name)
            key = normalise_name(name)
            sdist = not url.endswith('.whl')
            if sdist:
                if (key, version) not in SDIST_ALLOWED or wheel != key + '-' + version + '.tar.gz':
                    raise Refusal('sdist not allowed: ' + name + ' ' + version + ' (' + url + ')')
                wanted = SDIST_ALLOWED[(key, version)]
                if digest != wanted:
                    raise Refusal('sdist hash differs: ' + name + ' ' + version + ': report ' + digest + ', allowed ' + wanted)
            if not re.fullmatch(HEX64, digest):
                raise Refusal(name + ' ' + version + ' resolved to a non-wheel or unhashed file')
            if key == 'torch':
                if url != torch_url or digest != torch_sha:
                    raise Refusal('the resolution changed the torch wheel')
                source = torch_index
                licence = {}
            else:
                metadata = ctx.net.get_ok(PYPI_JSON.format(name=name, version=version)).json()
                published = {row['filename']: row['digests']['sha256'].lower() for row in metadata.get('urls', [])}
                if published.get(wheel) != digest:
                    raise Refusal(name + ' ' + version + ': wheel ' + wheel + ' hash differs from the PyPI JSON API, '
                                  'or the wheel is not on PyPI (the kit fetches every non-torch wheel from PyPI)')
                source = index
                info = metadata.get('info') or {}
                licence = {'license_expression': info.get('license_expression'),
                           'license': (info.get('license') or '')[:80],
                           'classifiers': [value for value in info.get('classifiers') or [] if value.startswith('License ::')]}
            if key in entries:
                raise Refusal(name + ' appears twice in the resolution')
            if sdist:
                entries[key] = {'name': name, 'version': version, 'kind': 'sdist', 'filename': wheel, 'url': url,
                                'sha256': digest, 'index': source, 'licence': licence, 'allowed_by': SDIST_ALLOWED_BY}
            else:
                entries[key] = {'name': name, 'version': version, 'wheel': wheel, 'url': url, 'sha256': digest, 'index': source,
                                'licence': licence}
        for key, version in pins.items():
            entry = entries.get(key)
            if entry is None:
                raise Refusal('direct requirement ' + key + ' is missing from the resolution')
            if entry['version'] != version and ('+' in version or entry['version'].split('+')[0] != version):
                raise Refusal('direct requirement ' + key + '==' + version + ' resolved to ' + entry['version'])
        lines = []
        for key in sorted(entries):
            entry = entries[key]
            version = pins.get(key, entry['version'])
            line = entry['name'] + '==' + version + ' --hash=sha256:' + entry['sha256']
            if not re.fullmatch(r'[A-Za-z0-9_.-]+==[A-Za-z0-9.+_-]+(?: --hash=sha256:[0-9a-f]{64})+', line):
                raise Refusal('lock line does not meet the kit\'s format: ' + line)
            lines.append(line)
            wheels.append(dict(entry, line_version=version))
        text = ('# ' + self.name + ' lock written by resolve-pins.py inside ' + image + '\n'
                '# Python ' + environment.get('python', '') + ', ' + environment.get('platform', '') + ', glibc ' +
                environment.get('glibc', '') + '. Do not edit; re-run the resolver.\n' + '\n'.join(lines) + '\n')
        data = text.encode('ascii')
        (work / (self.name + '.lock')).write_bytes(data)
        verify = self.container(ctx, image, work, ['verify', '--lock', '/work/' + self.name + '.lock', '--index', index,
                                                   '--torch-index', torch_index])
        downloaded = {row['filename']: row['sha256'].lower() for row in verify['files']}
        if len(downloaded) != len(lines) or any(downloaded.get(row.get('filename', row.get('wheel'))) != row['sha256'] for row in wheels):
            raise Refusal('pip download --require-hashes did not return exactly the locked wheels')
        target = ctx.store / 'locks' / (self.name + '.lock')
        write_atomic(target, data)
        authentication = {'method': 'pip download --require-hashes in the pinned runtime base; wheel groups mirror the kit; '
                                    'the sdist bytes also match the lead table (the kit fetches its fixed URL)',
                          'base_image': image, 'environment': environment, 'host_glibc': os.confstr('CS_GNU_LIBC_VERSION')
                          if hasattr(os, 'confstr') and 'CS_GNU_LIBC_VERSION' in os.confstr_names else '',
                          'wheels': wheels, 'torch_index_listing': index_url,
                          'index_url': index_url, 'wheel_url': torch_url,
                          'index_page_sha256': sha256_bytes(index_page), 'report_sha256': torch_sha}
        result = Result()
        stamp = utcnow()
        result.set(('fetch', 'locks', self.name, 'url'), ctx.store_uri(target), 'resolver output', authentication, retrieved_at=stamp)
        result.set(('fetch', 'locks', self.name, 'sha256'), sha256_bytes(data), 'sha256 of the lock', authentication, retrieved_at=stamp)
        return result


def validate_agpl_text(data):
    head = ' '.join(data[:400].decode('ascii', 'replace').split())
    if not head.startswith('GNU AFFERO GENERAL PUBLIC LICENSE Version 3, 19 November 2007'):
        raise Refusal('the lead-supplied text is not the GNU AGPL version 3')


class AgplText(Item):
    family, name = 'licences', 'agpl'

    def paths(self, doc):
        return files_paths(doc, 'payload/licences/AGPL-3.0.txt')

    def plan(self, ctx, doc):
        return ['copy lead input agpl_path; require agpl_sha256, check the GNU AGPL version 3 header, '
                'pin the local store copy at payload/licences/AGPL-3.0.txt; no network fetch']

    def resolve(self, ctx, doc):
        source = ctx.lead.get('agpl_path', '')
        if not isinstance(source, str) or not source:
            raise Refusal('lead input agpl_path is not given as a file path')
        expected = ctx.lead.get('agpl_sha256', '')
        if not isinstance(expected, str) or not re.fullmatch(HEX64, expected):
            raise Refusal('lead input agpl_sha256 must be a lowercase 64-hex SHA256')
        path = Path(source)
        if path.is_symlink() or not path.is_file() or not path.stat().st_size:
            raise Refusal('agpl_path is not a regular, non-empty file')
        data = path.read_bytes()
        digest = sha256_bytes(data)
        if expected != digest:
            raise Refusal('AGPL text hash ' + digest + ' differs from the lead record ' + expected)
        validate_agpl_text(data)
        target = ctx.store / 'licences' / 'AGPL-3.0.txt'
        write_atomic(target, data)
        result = Result()
        result.upsert_file('payload/licences/AGPL-3.0.txt', ctx.store_uri(target), digest,
                           'file copy of ' + source,
                           {'method': 'lead-supplied local file with a content check', 'original': source,
                            'lead_sha256': expected, 'lead_hash_matched': True}, retrieved_at=utcnow())
        return result


class CorrespondingSource(Item):
    family, name = 'licences', 'sources'

    def paths(self, doc):
        return [('fetch', 'files', index, field) for index, row in enumerate(doc['fetch']['files'])
                if row['path'].startswith(LICENCE_SOURCE_PREFIX) for field in ('url', 'sha256', 'kind') if field in row]

    def pending(self, ctx, doc):
        names = ctx.lead.get('corresponding_source') or []
        have = {row['path'] for row in doc['fetch']['files'] if row['path'].startswith(LICENCE_SOURCE_PREFIX)}
        return bool(names) and (not have or super().pending(ctx, doc))

    def plan(self, ctx, doc):
        return ['for each lead input corresponding_source name: its version in the converter lock, its sdist on the PyPI JSON API',
                'with no sdist: download the hash-locked py3-none-any wheel, check its hash, tags and every member for compiled files',
                'add one fetch.files row per accepted source archive under ' + LICENCE_SOURCE_PREFIX]

    def wheel_source(self, ctx, key, name, version, hashes, metadata, pypi_json):
        """Use the exact locked wheel, never a source repository or an unpinned archive."""
        lock_record = getattr(ctx, 'records', {}).get('fetch.locks.converter.sha256', {})
        locked = [row for row in lock_record.get('authentication', {}).get('wheels', [])
                  if normalise_name(row.get('name', '')) == key and
                  row.get('line_version', row.get('version')) == version]
        if locked and any(row.get('url') for row in locked):
            if len(locked) != 1 or not locked[0].get('url') or locked[0].get('sha256') not in hashes:
                raise Refusal(name + ' ' + version + ': recorded wheel URL/hash does not identify exactly one locked wheel')
            row = locked[0]
            filename = row.get('wheel', '')
            wheel_url, digest = row['url'], row['sha256']
            basis = 'converter lock evidence wheel URL'
        else:
            matches = [row for row in metadata.get('urls', []) if row.get('packagetype') == 'bdist_wheel' and
                       (row.get('digests') or {}).get('sha256', '').lower() in hashes]
            if len(matches) != 1:
                raise Refusal(name + ' ' + version + ': hash-only converter lock must identify exactly one PyPI wheel')
            row = matches[0]
            filename, wheel_url, digest = row['filename'], row['url'], row['digests']['sha256'].lower()
            basis = 'PyPI wheel URL matched to hash-only converter lock'
        if not relative_ok(filename) or '/' in filename or not filename.endswith('-py3-none-any.whl'):
            raise Refusal(name + ' ' + version + ': corresponding-source wheel must be tagged py3-none-any')
        if urllib.parse.unquote(PurePosixPath(urllib.parse.urlsplit(wheel_url).path).name) != filename:
            raise Refusal(name + ' ' + version + ': locked wheel URL and filename differ')
        data = ctx.net.get_ok(wheel_url).body
        if sha256_bytes(data) != digest:
            raise Refusal(name + ' ' + version + ': corresponding-source wheel sha256 differs from the converter lock')
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = sorted(archive.namelist())
                compiled = [member for member in members
                            if member.lower().endswith(('.so', '.pyd', '.dll', '.dylib', '.a', '.o', '.exe', '.bin'))]
                if compiled:
                    raise Refusal(name + ' ' + version + ': corresponding-source wheel contains compiled members: ' +
                                  ', '.join(compiled))
                descriptors = [member for member in members if member.endswith('.dist-info/WHEEL')]
                if len(descriptors) != 1:
                    raise Refusal(name + ' ' + version + ': corresponding-source wheel needs exactly one WHEEL descriptor')
                tags = re.findall(r'^Tag:\s*(\S+)\s*$', archive.read(descriptors[0]).decode('ascii'), re.M)
                if set(tags) != {'py3-none-any'}:
                    raise Refusal(name + ' ' + version + ': internal wheel tags must be py3-none-any')
        except (zipfile.BadZipFile, zipfile.LargeZipFile, UnicodeError, RuntimeError, OSError) as exc:
            raise Refusal(name + ' ' + version + ': cannot inspect corresponding-source wheel: ' + str(exc))
        project_urls = (metadata.get('info') or {}).get('project_urls') or {}
        documentary = {label: value for label, value in sorted(project_urls.items())
                       if label.lower() in ('source', 'homepage')}
        authentication = {'method': 'converter lock sha256 and inspected pure-Python wheel; PyPI has no sdist',
                          'package': name + '==' + version, 'pypi_json': pypi_json, 'locked_url_basis': basis,
                          'wheel_url': wheel_url, 'wheel_sha256': digest, 'wheel_members': members,
                          'wheel_tags': sorted(set(tags)), 'project_urls_documentary_only': documentary}
        return {'path': LICENCE_SOURCE_PREFIX + filename, 'url': wheel_url, 'sha256': digest}, authentication

    def resolve(self, ctx, doc):
        names = [normalise_name(value) for value in ctx.lead.get('corresponding_source') or []]
        if not names:
            raise Refusal('lead input corresponding_source lists no packages')
        lock = doc['fetch']['locks']['converter']
        if is_unresolved(lock['sha256']):
            raise Refusal('the converter lock is not resolved: run --family locks first')
        path = ctx.store / 'locks' / 'converter.lock'
        if not path.is_file() or sha256_file(path) != lock['sha256']:
            raise Refusal('the stored converter lock differs from its pin')
        versions, hashes = {}, {}
        for line in path.read_text(encoding='ascii').splitlines():
            if line and not line.startswith('#'):
                name, _, rest = line.partition('==')
                versions[normalise_name(name)] = (name, rest.split(' ', 1)[0])
                hashes[normalise_name(name)] = re.findall(r'--hash=sha256:([0-9a-f]{64})(?=\s|$)', rest)
        added, elements = [], []
        for key in names:
            if key not in versions:
                raise Refusal(key + ' is not in the converter lock')
            name, version = versions[key]
            public = version.split('+')[0]
            url = PYPI_JSON.format(name=name, version=public)
            metadata = ctx.net.get_ok(url).json()
            sdists = [row for row in metadata.get('urls', []) if row.get('packagetype') == 'sdist']
            if not sdists:
                row, authentication = self.wheel_source(ctx, key, name, version, hashes[key], metadata, url)
                added.append(row)
                elements.append({'pypi_json': url, 'authentication': authentication,
                                 'kind': 'wheel-as-source: pure Python, PyPI has no sdist'})
                continue
            if len(sdists) != 1:
                raise Refusal(name + ' ' + public + ' has ' + str(len(sdists)) + ' source distributions on PyPI; '
                              'supply its corresponding source another way')
            row = sdists[0]
            if not relative_ok(row['filename']):
                raise Refusal('unsafe source file name ' + row['filename'])
            added.append({'path': LICENCE_SOURCE_PREFIX + row['filename'], 'url': row['url'],
                          'sha256': row['digests']['sha256'].lower()})
            elements.append({'package': name + '==' + version, 'pypi_json': url})
        result = Result()
        stamp = utcnow()
        for row, extra in sorted(zip(added, elements), key=lambda pair: pair[0]['path']):
            result.upsert_file(row['path'], row['url'], row['sha256'], extra['pypi_json'],
                               extra['authentication'] if 'authentication' in extra else
                               {'method': 'PyPI-published sha256 over HTTPS', 'package': extra['package']},
                               retrieved_at=stamp, **({'kind': extra['kind']} if 'kind' in extra else {}))
        return result


def all_items(doc):
    items = [LeadMineruConfig(), LeadConverterSource(), LeadPymupdf()]
    # New kits ship the reviewed pairing package as runtime source. Retain the
    # old resolver only for an explicitly supplied legacy payload register.
    if 'pairing_helper_sha256' in doc['runtime']:
        items.insert(0, LeadPairingHelper())
    if any(field in doc['runtime'] for field in ('cuda_minimum_driver', 'tei_minimum_driver')):
        items += [LeadDriverRequirements()]
    names = ('ubuntu', 'nvidia', 'tailscale') if profile(doc) == 'pack' else ('ubuntu', 'ubuntu-release', 'nvidia', 'tailscale')
    items += [KeyItem(name) for name in names]
    if profile(doc) == 'iso':
        items += [IsoSums()]
    items += [TeiVariant(name) for name in sorted(doc['tei']['variants'])]
    items += [BaseImage()]
    items += [ModelItem(name) for name in ('tei', 'reranker', 'mineru_vlm', 'mineru_small') if name in doc['models']]
    items += [BuildTools()]
    items += [AptMetadata(repo) for repo in REPOSITORIES] + [AptClosure()]
    if profile(doc) == 'iso':
        items += [ModuleIdentity()]
    items += [LockItem(name) for name in ('reranker', 'converter')]
    items += [AgplText(), CorrespondingSource()]
    return items


# ---------------------------------------------------------------- persistent state and engine

class State:
    def __init__(self, out, template_path, template, log):
        self.out, self.template_path, self.template, self.log = out, template_path, template, log
        self.template_sha = sha256_file(template_path)
        self.filled = out / 'filled-pins.json'
        self.evidence_path = out / 'EVIDENCE.json'
        if self.filled.exists():
            self.doc = json.loads(self.filled.read_text(encoding='ascii'))
        else:
            self.doc = copy.deepcopy(template)
        self.records, self.decisions, self.tools = {}, [], []
        if self.evidence_path.exists():
            evidence = json.loads(self.evidence_path.read_text(encoding='ascii'))
            if evidence.get('template', {}).get('sha256') != self.template_sha:
                raise UsageError('the template changed since this output folder was started; use a new --out folder')
            self.records = {row['path']: row for row in evidence.get('records', [])}
            self.decisions = evidence.get('decisions', [])
            self.tools = evidence.get('tools', [])
        elif self.filled.exists():
            raise UsageError('filled-pins.json exists without EVIDENCE.json; use a new --out folder')

    def evidence(self, items, never, reasons=None):
        reasons = reasons or {}
        records = {}
        for path, row in self.records.items():
            if row.get('status') == 'dropped':
                records[path] = row
        owned = []
        for item in items:
            for path in item.paths(self.doc):
                owned.append((item, path))
        for path in never:
            owned.append((None, tuple(path.split('.'))))
        for item, path in owned:
            value = get_optional(self.doc, path)
            if value is None:
                continue
            units = [(path + (index,), element) for index, element in enumerate(value)] if isinstance(value, list) else [(path, value)]
            if isinstance(value, list) and not value:
                continue
            for unit, element in units:
                key = dotted(unit)
                old = self.records.get(key)
                if item is None and profile(self.doc) == 'pack' and not key.startswith('runtime.'):
                    records[key] = {'path': key, 'value': element, 'status': 'deferred', 'family': 'iso',
                                    'item': 'iso', 'reason': 'deferred: v1.1 ISO form'}
                elif is_unresolved(element):
                    if item is None:
                        reason = ('produced later (BUILD-DAY step 5 or lab runs); fetch-inputs.py LATER_STEP_PINS'
                                  if key not in MEASURED_BASE else 'measured by the first real fetch (record_base); keep the value it records')
                        family = 'later'
                    else:
                        reason = reasons.get(item.key) or (old or {}).get('reason') or ('not resolved yet: run --family ' + item.family)
                        family = item.family
                    records[key] = {'path': key, 'value': element, 'status': 'to-pin', 'family': family,
                                    'item': item.key if item else family, 'reason': reason,
                                    'retrieved_at': (old or {}).get('retrieved_at') if old and old.get('status') == 'to-pin' else utcnow()}
                elif old and old.get('value') == element and old.get('status') in ('resolved', 'conflict'):
                    records[key] = old
                else:
                    records[key] = {'path': key, 'value': element, 'status': 'template', 'family': item.family if item else 'later',
                                    'item': item.key if item else 'later',
                                    'reason': 'value already present in the template'}
        return records

    def save(self, items, never, reasons=None):
        self.records = self.evidence(items, never, reasons)
        data = canonical(self.doc).encode('ascii')
        write_atomic(self.filled, data)
        evidence = {'format': EVIDENCE_FORMAT, 'template': {'path': str(self.template_path), 'sha256': self.template_sha},
                    'filled': {'path': str(self.filled), 'sha256': sha256_bytes(data)}, 'updated_at': utcnow(),
                    'decisions': self.decisions, 'tools': self.tools,
                    'records': [self.records[key] for key in sorted(self.records)]}
        write_atomic(self.evidence_path, canonical(evidence).encode('ascii'))


def expand_file_rows(doc, result):
    """Turn row upserts into ordinary field updates; returns the rows to append."""
    rows = doc['fetch']['files']
    appended = []
    for stage_path, url, digest, source, authentication, extra in result.file_rows:
        if not relative_ok(stage_path):
            raise AssertionError('unsafe stage path ' + stage_path)
        index = next((i for i, row in enumerate(rows) if row['path'] == stage_path), None)
        if index is None:
            index = len(rows) + len(appended)
            appended.append({'path': stage_path, 'url': 'TO-PIN', 'sha256': 'TO-PIN'})
        fields = [('url', url), ('sha256', digest)]
        if 'kind' in extra:
            fields.append(('kind', extra['kind']))
        for field, value in fields:
            result.set(('fetch', 'files', index, field), value, source, authentication, **extra)
    result.file_rows = []
    return appended


def template_fixed_changes(template, path, new):
    """(name, template value, proposed) for every value the template already fixes that `new` would change.

    The template's own values (a weight hash recorded from production, a vendor URL) are the
    reference, not resolver output: --force-family never replaces them.
    """
    old = get_optional(template, path)
    if old is None:
        return []
    if isinstance(old, list):
        changes = []
        rows = new if isinstance(new, list) else []
        for index, element in enumerate(old):
            if is_unresolved(element):
                continue
            if isinstance(element, dict) and 'path' in element:
                same = [row for row in rows if isinstance(row, dict) and row.get('path') == element['path']]
                proposed = same[0] if same else None
            else:
                proposed = rows[index] if index < len(rows) else None
            if proposed != element:
                changes.append((dotted(tuple(path) + (index,)), element, proposed))
        return changes
    if not is_unresolved(old) and old != new:
        return [(dotted(path), old, new)]
    return []


def apply_result(ctx, state, item, result, forced):
    doc = state.doc
    appended = expand_file_rows(doc, result)
    for path in result.updates:
        if dotted(path) in ctx.never:
            raise AssertionError('resolver attempted to set a later-step or measured pin: ' + dotted(path))
    fixed = [change for path, new in result.updates.items() for change in template_fixed_changes(state.template, path, new)]
    if fixed:
        ctx.log(item.key, 'CONFLICT', '; '.join(name + ': template fixes ' + json.dumps(old)[:120] + ', upstream now ' +
                                               json.dumps(new)[:120] for name, old, new in fixed))
        for name, old, new in fixed:
            row = dict(state.records.get(name) or {'path': name, 'value': old, 'family': item.family, 'item': item.key})
            row.update(status='conflict', reason='upstream differs from a value the template fixes; the template value is '
                       'kept and --force-family never replaces it (a reviewed kit change is needed)', proposed=new,
                       checked_at=utcnow())
            state.records[name] = row
        return 'conflict'
    conflicts = []
    for path, new in result.updates.items():
        current = get_optional(doc, path)
        if current is None or current == []:
            continue  # nothing pinned yet (an absent value or an empty template list)
        if not is_unresolved(current) and current != new:
            if isinstance(current, list):
                for index, element in enumerate(current):
                    if index >= len(new) or new[index] != element:
                        conflicts.append((dotted(path + (index,)), element, new[index] if index < len(new) else None))
                if not conflicts:
                    conflicts.append((dotted(path), current, new))
            else:
                conflicts.append((dotted(path), current, new))
        # A partly pinned list (a model file list with a known weight hash) is pinned only by the
        # template; template_fixed_changes above keeps those elements, with or without --force-family.
    if conflicts and not forced:
        ctx.log(item.key, 'CONFLICT', '; '.join(name + ': kept ' + json.dumps(old)[:120] + ', upstream now ' +
                                               json.dumps(new)[:120] for name, old, new in conflicts))
        for name, old, new in conflicts:
            row = dict(state.records.get(name) or {'path': name, 'value': old, 'family': item.family, 'item': item.key})
            row.update(status='conflict', reason='upstream value changed; the pinned value is kept. Refused without '
                       '--force-family ' + item.family, proposed=new, checked_at=utcnow())
            state.records[name] = row
        return 'conflict'
    doc['fetch']['files'].extend(appended)
    for path in result.deletes:
        get(doc, path[:-1]).pop(path[-1], None)
    for path, value in result.updates.items():
        prefix = dotted(path)
        for key in [key for key in state.records if key == prefix or key.startswith(prefix + '.')]:
            del state.records[key]
        put(doc, path, value)
    for record in result.records:
        record = dict(record)
        record.setdefault('status', 'resolved')
        record.update(family=item.family, item=item.key)
        if conflicts:
            record['replaced_by_force'] = True
        state.records[record['path']] = record
    known = {row['id'] for row in state.decisions}
    state.decisions += [row for row in result.decisions if row['id'] not in known]
    seen = {(row['name'], row['pinned']) for row in state.tools}
    state.tools += [row for row in result.tools if (row['name'], row['pinned']) not in seen]
    ctx.log(item.key, 'OK', str(len(result.records)) + ' evidence records' +
            (' (forced over ' + str(len(conflicts)) + ' changed values)' if conflicts else ''))
    return 'ok'


def run(ctx, state, families, recheck, force):
    reasons = {}
    outcome = {}
    for item in [item for item in all_items(state.doc) if item.family in families]:
        ctx.records = state.records
        doc = state.doc
        forced = item.family in force
        pending = item.pending(ctx, doc)
        if not pending and not (recheck or forced):
            ctx.log(item.key, 'KEPT', 'already resolved')
            outcome[item.key] = 'kept'
            continue
        ctx.log(item.key, 'START', 'resolve' if pending else 'recheck')
        try:
            result = item.resolve(ctx, doc)
        except Refusal as exc:
            ctx.log(item.key, 'REFUSED', str(exc))
            reasons[item.key] = str(exc)
            outcome[item.key] = 'refused'
            if not pending:
                for path in item.paths(doc):
                    key = dotted(path)
                    if key in state.records:
                        state.records[key] = dict(state.records[key], recheck_failed=str(exc), checked_at=utcnow())
        else:
            outcome[item.key] = apply_result(ctx, state, item, result, forced)
        state.save(all_items(state.doc), ctx.never, reasons)
    return outcome


# ---------------------------------------------------------------- check and dry run

def structure_problems(template, doc, dropped, decisions):
    """Filled pins keep the template's shape and every value the template already fixed."""
    problems = []
    replaced = {row['what'].split(' replaced by ')[0] for row in decisions if ' replaced by ' in row.get('what', '')}

    def walk(left, right, path):
        if isinstance(left, dict):
            if not isinstance(right, dict):
                problems.append(dotted(path) + ': not an object')
                return
            expected = set(left) - (set(dropped) if path == ('tei', 'variants') else set())
            if set(right) != expected:
                problems.append(dotted(path) + ': keys differ (' + ', '.join(sorted(set(right) ^ expected)) + ')')
            for key in sorted(expected & set(right)):
                walk(left[key], right[key], path + (key,))
        elif isinstance(left, list):
            if not isinstance(right, list):
                problems.append(dotted(path) + ': not a list')
            elif path in (('packages',), ('ubuntu', 'driver_packages')):
                if [value.split('=')[0] for value in left] != [value.split('=')[0] for value in right]:
                    problems.append(dotted(path) + ': package names or order differ from the template')
            elif path == ('fetch', 'files'):
                if [row['path'] for row in right[:len(left)]] != [row['path'] for row in left]:
                    problems.append('fetch.files: template rows changed or reordered')
                for left_row, right_row in zip(left, right):
                    for field in ('url', 'sha256'):
                        if not is_unresolved(left_row[field]) and left_row[field] != right_row[field]:
                            problems.append('fetch.files[' + left_row['path'] + '].' + field + ': template value changed')
                for row in right[len(left):]:
                    if not row['path'].startswith((CONVERTER_SOURCE_PREFIX, LICENCE_SOURCE_PREFIX)):
                        problems.append('fetch.files: unexpected extra row ' + row['path'])
            elif len(path) == 4 and path[:2] == ('fetch', 'repositories') and path[3] == 'metadata':
                if len(left) != len(right):
                    problems.append(dotted(path) + ': metadata rows added or removed')
                for left_row, right_row in zip(left, right):
                    if left_row['path'] in replaced:
                        continue
                    if left_row['path'] != right_row['path'] or \
                            (not is_unresolved(left_row['url']) and left_row['url'] != right_row['url']):
                        problems.append(dotted(path) + '[' + left_row['path'] + ']: template path or URL changed')
            else:
                # Other lists (model file lists, package rows): every element the template fixes stays.
                for name, _, _ in template_fixed_changes(template, path, right):
                    problems.append(name + ': template value changed')
        elif not is_unresolved(left) and left != right:
            problems.append(dotted(path) + ': template value changed')
    walk(template, doc, ())
    return problems


def check_lead_payloads(ctx, state, report, blocking):
    """Check the exact local bytes that the fetch will stage, without network access."""
    for stage_path in (LEAD_FILES['mineru_config'], 'payload/licences/AGPL-3.0.txt'):
        index = file_row(state.doc, stage_path)
        row = state.doc['fetch']['files'][index]
        if is_unresolved(row):
            continue
        try:
            data = read_file_url(row['url'])
            if sha256_bytes(data) != row['sha256']:
                raise Refusal('stored file differs from its pinned SHA256')
            if stage_path == LEAD_FILES['mineru_config']:
                ctx.fetch.validate_mineru_config(data)
            else:
                record = state.records.get(dotted(('fetch', 'files', index, 'sha256')), {})
                auth = record.get('authentication', {})
                expected = auth.get('lead_sha256', '')
                if not isinstance(expected, str) or not re.fullmatch(HEX64, expected) or expected != row['sha256']:
                    raise Refusal('AGPL pin differs from the required agpl_sha256 lead record')
                if ctx.lead.get('agpl_sha256', expected) != expected:
                    raise Refusal('AGPL pin differs from the current agpl_sha256 lead input')
                validate_agpl_text(data)
        except (OSError, NetError, Refusal, ValueError, TypeError) as exc:
            blocking.append('LOCAL ' + stage_path + ': ' + str(exc))
        else:
            report.append('LOCAL ' + stage_path + ': pinned local bytes and content pass')


def check(ctx, state):
    doc, template = state.doc, state.template
    report, blocking = [], []
    dropped = [row['path'].split('.')[-1] for row in state.records.values() if row.get('status') == 'dropped']
    for problem in structure_problems(template, doc, dropped, state.decisions):
        blocking.append('STRUCTURE ' + problem)
    for key, row in sorted(state.records.items()):
        if row.get('status') in ('resolved', 'conflict'):
            value = get_optional(doc, tuple(int(part) if part.isdigit() else part for part in key.split('.')))
            if value != row.get('value'):
                blocking.append('EVIDENCE ' + key + ': evidence value differs from filled-pins.json')
        if row.get('status') == 'conflict':
            report.append('WARN ' + key + ': upstream now differs from the kept pin (' + json.dumps(row.get('proposed'))[:100] +
                          '); the pinned value stays authenticated, decide with --force-family ' + str(row.get('family')))
    check_lead_payloads(ctx, state, report, blocking)
    unresolved = ctx.fetch.release_profile.unresolved(doc)
    allowed = set(ctx.fetch.LATER_STEP_PINS) | set(MEASURED_BASE)
    remaining = [key for key in unresolved if key not in allowed]
    report.append('UNRESOLVED allowed (later step or measured by the fetch): ' + ', '.join(key for key in unresolved if key in allowed))
    for key in remaining:
        row = state.records.get(key) or {}
        report.append('UNRESOLVED ' + key + ': ' + str(row.get('reason', 'no evidence record')))
    # The kit's own validate.py pin rules, on a copy where unresolved markers are neutral words.
    neutral = json.loads(json.dumps(doc).replace('TO-PIN', 'UNRESOLVED'))
    if profile(doc) == 'iso':
        neutral = json.loads(json.dumps(neutral).replace('deferred: v1.1 ISO form', 'UNRESOLVED'))
    sys.path.insert(0, str(ctx.kit / 'build'))
    try:
        validator = load_module('kit_validate', ctx.kit / 'build' / 'validate.py')
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            (stage / 'pins.json').write_text(canonical(neutral), encoding='ascii')
            try:
                validator.validate(stage, ctx.kit)
                blocking.append('VALIDATE unexpected pass without a payload')
            except (OSError,) as exc:
                if 'PAYLOAD.json' in str(exc):
                    report.append('VALIDATE pin rules of kit/build/validate.py: pass (stage checks belong to step 7)')
                else:
                    blocking.append('VALIDATE ' + str(exc))
            except (ValueError, KeyError, TypeError) as exc:
                blocking.append('VALIDATE ' + str(exc))
    finally:
        sys.path.remove(str(ctx.kit / 'build'))
    # The kit's own fetch preflight, in process. Off Linux the platform rule is shimmed and said so.
    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory) / 'never-created'
        fetch = ctx.fetch
        saved = (fetch.sys, fetch.platform)
        try:
            if not (sys.platform == 'linux' and sys.version_info[:2] == (3, 12)):
                import types
                fetch.sys = types.SimpleNamespace(platform='linux', version_info=(3, 12, 0))
                fetch.platform = types.SimpleNamespace(machine=lambda: 'x86_64')
                report.append('PREFLIGHT platform rule shimmed on this host; it runs for real on the Linux CPython 3.12 build host')
            fetch.preflight(doc, stage)
            report.append('PREFLIGHT kit fetch-inputs.py preflight: pass')
        except (ValueError, KeyError, TypeError) as exc:
            (report if remaining else blocking).append('PREFLIGHT refused: ' + str(exc)[:500])
        finally:
            fetch.sys, fetch.platform = saved
        if stage.exists():
            blocking.append('PREFLIGHT created the stage folder')
    # The kit's own dry run, as BUILD-DAY step 4 runs it.
    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory) / 'stage-dry-run'
        script = ctx.kit / 'build' / 'fetch-inputs.sh'
        if shutil.which('bash') and shutil.which('python3') and sys.platform != 'win32':
            argv = ['bash', str(script), '--pins', str(state.filled), '--stage', str(stage), '--dry-run']
        else:
            argv = [sys.executable, '-I', '-B', str(ctx.kit / 'build' / 'fetch-inputs.py'), '--pins', str(state.filled),
                    '--stage', str(stage), '--dry-run']
        run_dry = subprocess.run(argv, capture_output=True, text=True)
        lines = run_dry.stdout.splitlines()
        pins = [line for line in lines if line.startswith('PIN ')]
        open_pins = [line.split(' = ')[0][4:] for line in pins if 'TO-PIN' in line]
        if run_dry.returncode or stage.exists():
            blocking.append('DRYRUN exit ' + str(run_dry.returncode) + ': ' + tail(run_dry.stderr))
        else:
            report.append('DRYRUN ' + ' '.join(argv[:2]) + ' ... --dry-run: exit 0, ' + str(len(pins)) + ' PIN lines, ' +
                          str(len(lines) - len(pins)) + ' plan lines, ' + str(len(open_pins)) + ' PIN lines still TO-PIN')
            unexpected = [key for key in open_pins if key not in allowed and key not in remaining]
            if unexpected:
                blocking.append('DRYRUN TO-PIN lines not explained: ' + ', '.join(unexpected))
    for line in report + blocking:
        print(line)
    if blocking:
        print('CHECK FAILED: ' + str(len(blocking)) + ' problem(s)')
        return 3
    if remaining:
        print('CHECK INCOMPLETE: ' + str(len(remaining)) + ' resolvable pins are still TO-PIN')
        return 3
    print('CHECK PASSED: only the later-step and fetch-measured pins remain; BUILD-DAY step 4 may run')
    return 0


def dry_run(ctx, state, families):
    print('DRY RUN: no network, no container, no file written. Template ' + str(state.template_path))
    for item in all_items(state.doc):
        if item.family not in families:
            continue
        try:
            pending = item.pending(ctx, state.doc)
            count = sum(count_unresolved(get_optional(state.doc, path)) for path in item.paths(state.doc))
            print('PLAN ' + item.key + ': ' + ('resolve (' + str(count) + ' unresolved fields)' if pending else 'kept (already resolved)'))
            for line in item.plan(ctx, state.doc):
                print('  ' + line)
        except (Refusal, UsageError) as exc:
            print('PLAN ' + item.key + ': cannot plan: ' + str(exc))
    print('UNTOUCHED: ' + ', '.join(sorted(ctx.never)) + ' (later steps and the fetch\'s own measurement)')


# ---------------------------------------------------------------- main

HOST_FACTORY = Host  # replaced by the offline tests with their stand-in host


def count_unresolved(value):
    if isinstance(value, dict):
        return sum(count_unresolved(child) for child in value.values())
    if isinstance(value, list):
        return sum(count_unresolved(child) for child in value)
    return 1 if is_unresolved(value) else 0

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--kit', type=Path, required=True, help='the kit folder holding pins.json, build/ and containers/')
    parser.add_argument('--template', type=Path, help='default: KIT/pins.json')
    parser.add_argument('--out', type=Path, required=True, help='output folder (made on the first real run)')
    parser.add_argument('--lead-inputs', type=Path, help='JSON of lead-supplied, reviewed inputs (see README.md)')
    parser.add_argument('--family', action='append', choices=FAMILIES, default=[])
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--recheck', action='store_true')
    parser.add_argument('--force-family', action='append', choices=FAMILIES, default=[])
    parser.add_argument('--gpg')
    parser.add_argument('--gpgv')
    parser.add_argument('--test-web', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        modes = sum(bool(value) for value in (args.dry_run, args.check, args.family or args.all))
        if modes != 1 and not (args.dry_run and (args.family or args.all)):
            raise UsageError('choose exactly one of --dry-run, --check, --family NAME or --all')
        if args.force_family and not (args.family or args.all):
            raise UsageError('--force-family needs --family or --all')
        kit = args.kit.resolve()
        template_path = (args.template or kit / 'pins.json').resolve()
        template = json.loads(template_path.read_text(encoding='ascii'))
        if template.get('format') != 'deep32-forge-pins/1':
            raise UsageError('the template is not a deep32-forge-pins/1 file')
        out = args.out.resolve()
        if out == kit or out.is_relative_to(kit):
            raise UsageError('the output folder must be outside the kit')
        lead = {}
        if args.lead_inputs:
            lead = json.loads(args.lead_inputs.read_text(encoding='utf-8'))
            if lead.get('format') != LEAD_FORMAT:
                raise UsageError('lead inputs must say "format": "' + LEAD_FORMAT + '"')
        offline = args.dry_run or args.check
        log_path = out / 'RESOLVE.log'

        def log(line):
            text = utcnow() + ' ' + line
            if offline:
                return
            out.mkdir(parents=True, exist_ok=True)
            with log_path.open('a', encoding='ascii', errors='replace') as stream:
                stream.write(text + '\n')
            print(text, flush=True)
        net = Net(log, rewrite_base=args.test_web, offline=offline, retries=1 if args.test_web else 3)
        host = HOST_FACTORY(log, offline=args.dry_run)
        gpg = Gpg(host, out / 'work' / 'gnupg', args.gpg, args.gpgv)
        fetch_module = load_module('kit_fetch_inputs', kit / 'build' / 'fetch-inputs.py')
        ctx = Context(args, log, net, host, gpg, lead, kit, out, fetch_module, fetch_module.prov)
        state = State(out, template_path, template, log)
        ctx.never.update(ctx.fetch.release_profile.deferred_paths(state.doc))
        families = FAMILIES if args.all or not args.family else tuple(args.family)
        if args.dry_run:
            dry_run(ctx, state, families)
            return 0
        if args.check:
            if not state.filled.exists():
                raise UsageError('no filled-pins.json in ' + str(out))
            return check(ctx, state)
        out.mkdir(parents=True, exist_ok=True)
        log('RUN families=' + ','.join(families) + (' recheck' if args.recheck else '') +
            (' force=' + ','.join(args.force_family) if args.force_family else ''))
        outcome = run(ctx, state, families, args.recheck, set(args.force_family))
        state.save(all_items(state.doc), ctx.never)
        counts = {}
        for value in outcome.values():
            counts[value] = counts.get(value, 0) + 1
        open_pins = [key for key in ctx.fetch.release_profile.unresolved(state.doc) if key not in ctx.never]
        log('DONE ' + ', '.join(key + '=' + str(value) for key, value in sorted(counts.items())) +
            '; resolvable pins still TO-PIN: ' + str(len(open_pins)))
        if 'conflict' in outcome.values():
            return 4
        if 'refused' in outcome.values():
            return 3
        return 0
    except UsageError as exc:
        print('resolve-pins: ' + str(exc), file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print('resolve-pins: input error: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
