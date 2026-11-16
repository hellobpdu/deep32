#!/usr/bin/env python3
"""Public input preparation for a lab Linux host. Dry run is read-only and offline.
Usage: fetch-inputs.py --stage NEW_DIR [--pins FILE] [--dry-run]. Exit 2 on refusal.
The download manifest lives in pins.json; this program never resolves floating pins.
"""
import argparse
import datetime as dt
import email.utils
import gzip
import hashlib
import json
import lzma
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

# -I does not add this script's directory to sys.path. Load the sibling explicitly.
import importlib.util
_spec = importlib.util.spec_from_file_location('fetch_provenance', Path(__file__).with_name('provenance.py'))
prov = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prov)
# Load shared distribution checks under -I without changing the pins template.
_spec = importlib.util.spec_from_file_location('fetch_distributions', Path(__file__).with_name('distributions.py'))
distributions = importlib.util.module_from_spec(_spec)
# The shared module imports provenance; keep its sibling explicit under -I.
sys.modules.setdefault('provenance', prov)
_spec.loader.exec_module(distributions)
_spec = importlib.util.spec_from_file_location('fetch_mineru_config', Path(__file__).with_name('pdf_model_config.py'))
pdf_model_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pdf_model_config)
validate_mineru_config = pdf_model_config.validate_mineru_config
_spec = importlib.util.spec_from_file_location('fetch_mineru_layout', Path(__file__).with_name('pdf_model_layout.py'))
pdf_model_layout = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pdf_model_layout)
_profile_spec = importlib.util.spec_from_file_location('forge_release_profile', Path(__file__).with_name('release_profile.py'))
release_profile = importlib.util.module_from_spec(_profile_spec)
_profile_spec.loader.exec_module(release_profile)
_binding_spec = importlib.util.spec_from_file_location('fetch_image_binding', Path(__file__).with_name('image_binding.py'))
image_binding = importlib.util.module_from_spec(_binding_spec)
_binding_spec.loader.exec_module(image_binding)
TEI = 'ghcr.io/huggingface/text-embeddings-inference'
HEX = r'[0-9a-f]{64}'
PIPELINE_BUNDLE_ROLES = frozenset({'mineru_small'})
# Values that exist only after this fetch: application image IDs come from the
# step 5 offline build; the MinerU CUDA facts and Windows driver build come from
# lab runs on the fetched wheels. A real fetch leaves them TO-PIN in
# stage/pins.json and validate.py refuses them until they are filled.
LATER_STEP_PINS = ('runtime.converter_image_id', 'runtime.door_image_id', 'runtime.reranker_image_id',
                   'runtime.mineru_cuda_version', 'runtime.mineru_minimum_driver',
                   'runtime.windows_cuda13_driver_build')


def leaves(value, prefix=''):
    if isinstance(value, dict):
        for key, child in sorted(value.items()):
            yield from leaves(child, (prefix + '.' + key).lstrip('.'))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from leaves(child, prefix + '.' + str(index))
    else:
        yield prefix, value


def source_for(key):
    if key.startswith('models.'):
        return 'https://huggingface.co/ (immutable snapshot, including remote code)'
    if key.startswith('tei.'):
        return TEI + ' (linux/amd64 manifest)'
    if key.startswith(('ubuntu.', 'packages.')):
        return 'signed Ubuntu/NVIDIA/Tailscale snapshots in fetch.repositories'
    if key.startswith('fetch.'):
        return 'exact public URL or reviewed local file URL in this fetch record'
    if key.startswith('runtime.'):
        return 'runtime base registry; reviewed source/locks; signed lab build evidence in PINS.md'
    return 'kit input register PINS.md'


def plan(config, stage):
    # Every leaf, including metadata and output pins, has a named source. Missing
    # pins remain visible in a dry run, while a real run refuses them all.
    excluded = release_profile.deferred_paths(config)
    for key, value in leaves(config):
        if key in excluded:
            print('DEFERRED ' + key + '; v1.1 ISO form')
            continue
        print('PIN ' + key + ' = ' + json.dumps(value, ensure_ascii=True) + '; source=' + source_for(key))
    for name, variant in sorted(config['tei']['variants'].items()):
        image = TEI + '@' + variant['digest']
        print(shlex.join(['docker', 'pull', '--platform', 'linux/amd64', image]))
        print(shlex.join(['docker', 'image', 'save', '--output', str(stage / 'payload/images' / ('tei-' + name + '.tar')), image]))
    base = config['runtime']['base_image']
    print(shlex.join(['docker', 'pull', '--platform', 'linux/amd64', base]))
    print(shlex.join(['docker', 'image', 'save', '--output', str(stage / 'base/runtime.tar'), base]))
    for name, model in sorted(config['models'].items()):
        snapshot = model_snapshot(name, model)
        print('SNAPSHOT https://huggingface.co/' + model['id'] + '/tree/' + model['revision'] +
              ' revision=' + model['revision'] + ' ALL files, remote code included -> ' + str(stage / snapshot))
        print('GET https://huggingface.co/api/models/' + model['id'] + '/revision/' + model['revision'] + '?blobs=true; require exact revision and complete pinned file list')
        for record in model['files']:
            print('FETCH https://huggingface.co/' + model['id'] + '/resolve/' + model['revision'] + '/' +
                  urllib.parse.quote(record['path'], safe='/') + ' sha256=' + record['sha256'] + ' -> ' + str(stage / snapshot / record['path']))
    for record in release_profile.files(config):
        print('FETCH ' + record['url'] + ' sha256=' + record['sha256'] + ' -> ' + str(stage / record['path']))
    for role, record in sorted(config['fetch']['locks'].items()):
        print('FETCH ' + record['url'] + ' sha256=' + record['sha256'] + ' -> ' + str(stage / 'payload/locks' / (role + '.lock')))
        for part, index in [('torch', record['torch_index']), ('other', record['index'])]:
            print(shlex.join(['python3', '-m', 'pip', 'download', '--require-hashes', '--no-deps', '--only-binary=:all:',
                              '--index-url', index, '--requirement', '<' + role + '.' + part + '.lock>',
                              '--dest', str(stage / 'payload/wheelhouse' / role)]))
    print('CONDITIONAL converter lock: fetch only jieba==0.42.1 from ' + distributions.SDIST_URL +
          ' sha256=' + distributions.SDIST_ALLOWED[('jieba', '0.42.1')] +
          '; exclude it from the binary-only pip groups; refuse every other source archive.')
    for name, repo in sorted(config['fetch']['repositories'].items()):
        print('REPOSITORY ' + name + ' suite=' + repo['suite'] + ' signer=' + repo['fingerprint'] + '; keep original signed metadata')
        for record in repo['metadata'] + repo['packages']:
            print('FETCH ' + record['url'] + ' sha256=' + record['sha256'] + ' -> ' + str(stage / repo['target'] / record['path']))
    print('VERIFY original InRelease or Release + Release.gpg, signer fingerprints, expiry, signed index hashes and exact package closure.')
    print('WRITE FETCHED.json: SHA-256 and size for every staged file; payload inventory is sealed after the offline image build.')
    print('LATER ' + ', '.join(LATER_STEP_PINS) + ': produced after this fetch (step 5 build, lab runs); '
          'a real fetch leaves them unresolved and validate.py refuses them until they are filled.')
    print('Dry run: no directory creation, downloads, registry, pip, GPU or service commands.')


def relative(value):
    path = PurePosixPath(value)
    if not value or value.startswith('/') or '\\' in value or ':' in value or '..' in path.parts or str(path) != value:
        raise ValueError('unsafe relative input path')
    return Path(*path.parts)


def check_url(value):
    url = urllib.parse.urlsplit(value)
    if url.scheme not in {'https', 'file'} or url.username or url.password or (url.scheme == 'https' and not url.netloc):
        raise ValueError('input must use public HTTPS or a reviewed local file URL')


def check_record(record):
    relative(record['path'])
    check_url(record['url'])
    if not re.fullmatch(HEX, record['sha256']):
        raise ValueError('input needs a full SHA-256')


def preflight(config, stage):
    if stage.exists() or stage.is_symlink():
        raise ValueError('stage target already exists; use a new directory')
    # These two TO-PIN values are measured during this fetch, not in a later step.
    measured_base = {'runtime.' + field for field in ('base_image_id', 'base_archive_sha256')
                     if isinstance(config['runtime'][field], str) and 'TO-PIN' in config['runtime'][field]}
    missing = [key for key in release_profile.unresolved(config) if key not in LATER_STEP_PINS and key not in measured_base]
    if missing:
        raise ValueError('TO-PIN: ' + ', '.join(missing))
    if sys.platform != 'linux' or sys.version_info[:2] != (3, 12) or platform.machine() not in {'x86_64', 'amd64'}:
        raise ValueError('fetch on Linux amd64 with CPython 3.12 so wheel tags match the runtime base')
    for variant in config['tei']['variants'].values():
        if not re.fullmatch('sha256:' + HEX, variant['digest']):
            raise ValueError('TEI needs immutable amd64 digests')
    if not re.fullmatch(r'[^\s@]+@sha256:' + HEX, config['runtime']['base_image']):
        raise ValueError('runtime base must be a repository digest')
    for role, model in config['models'].items():
        if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', model['id'])
                or any(part in {'.', '..'} for part in model['id'].split('/'))
                or not re.fullmatch('[0-9a-f]{40}', model['revision'])):
            raise ValueError('model needs a public owner/name and 40-hex revision')
        pipeline_bundle = role in PIPELINE_BUNDLE_ROLES and not any(
            record['path'] == 'config.json' for record in model['files'])
        paths = set()
        for record in model['files']:
            relative(record['path'])
            if role in pdf_model_layout.MINERU_ROLES:
                pdf_model_layout.refuse_upstream_markers([record['path']])
            if pipeline_bundle and (not isinstance(record.get('sha256'), str) or
                                    not re.fullmatch(HEX, record['sha256'])):
                raise ValueError('pipeline bundle ' + role + ' files need unique paths and full SHA-256 pins')
            if not re.fullmatch(HEX, record['sha256']) or record['path'] in paths:
                if pipeline_bundle:
                    raise ValueError('pipeline bundle ' + role + ' files need unique paths and full SHA-256 pins')
                raise ValueError('model files need unique paths and full SHA-256 pins')
            paths.add(record['path'])
        if pipeline_bundle:
            if not paths:
                raise ValueError('pipeline bundle ' + role + ' needs a non-empty complete snapshot file list')
            if not any(path.endswith(('.safetensors', '.pth', '.pt', '.onnx')) for path in paths):
                raise ValueError('pipeline bundle ' + role + ' has no weight file (.safetensors, .pth, .pt, .onnx)')
            python_files = sorted(path for path in paths if path.endswith('.py'))
            if python_files:
                raise ValueError('pipeline bundle ' + role + ' carries Python code: ' + python_files[0])
        elif not paths or 'config.json' not in paths:
            raise ValueError('complete model snapshot file list is required: model ' + role + ' has no root config.json')
    fetch = config['fetch']
    destinations = set()
    for record in release_profile.files(config):
        check_record(record)
        if record['path'] in destinations:
            raise ValueError('duplicate file destination')
        destinations.add(record['path'])
    required = {'payload/keyrings/ubuntu.gpg',
                'payload/keyrings/nvidia.gpg', 'payload/keyrings/tailscale.gpg', 'payload/licences/AGPL-3.0.txt',
                'payload/bin/pairing-helper', 'payload/models/mineru-home/config.yaml'}
    if not release_profile.is_pack(config):
        required.add('payload/keyrings/ubuntu-release.gpg')
    if not required <= destinations:
        raise ValueError('missing keyring, licence, helper or MinerU configuration input')
    licence = next(row for row in fetch['files'] if row['path'] == 'payload/licences/AGPL-3.0.txt')
    licence_url = urllib.parse.urlsplit(licence['url'])
    if licence_url.scheme != 'file' or licence_url.netloc not in {'', 'localhost'}:
        raise ValueError('AGPL text must come from a reviewed local file, never the network')
    if not release_profile.is_pack(config) and not re.fullmatch(r'[0-9A-F]{40,64}', fetch['iso_signing_fingerprint']):
        raise ValueError('ISO signing fingerprint is required')
    if set(fetch['locks']) != {'reranker', 'converter'}:
        raise ValueError('both complete wheel locks are required')
    for role, record in fetch['locks'].items():
        check_record(dict(record, path='payload/locks/' + role + '.lock'))
        check_url(record['index'])
        check_url(record['torch_index'])
    if set(fetch['repositories']) != {'ubuntu-noble', 'ubuntu-updates', 'ubuntu-security', 'nvidia', 'tailscale'}:
        raise ValueError('all five signed repository snapshots are required')
    closure = set()
    for name, repo in fetch['repositories'].items():
        relative(repo['target'])
        relative(repo['keyring'])
        relative(repo['release_dir']) if repo['release_dir'] else None
        if not re.fullmatch(r'[0-9A-F]{40,64}', repo['fingerprint']):
            raise ValueError('repository needs an independently verified signing fingerprint')
        if name in {'nvidia', 'tailscale'} and repo['fingerprint'] != config['runtime'][name + '_repository_key_fingerprint']:
            raise ValueError('repository signing fingerprint differs from runtime pin')
        for record in repo['metadata'] + repo['packages']:
            check_record(record)
            destination = repo['target'] + '/' + record['path']
            if destination in destinations:
                raise ValueError('duplicate repository destination')
            destinations.add(destination)
        metadata = {record['path'] for record in repo['metadata']}
        prefix = repo['release_dir'] + '/' if repo['release_dir'] not in {'', '.'} else ''
        if not ({prefix + 'InRelease'} <= metadata or {prefix + 'Release', prefix + 'Release.gpg'} <= metadata):
            raise ValueError('original signed release metadata is required')
        for record in repo['packages']:
            item = record['name'] + '=' + record['version']
            if item in closure or record['architecture'] not in {'amd64', 'all'} or 'dkms' in record['name']:
                raise ValueError('duplicate, foreign architecture or DKMS package in closure')
            if record['name'].startswith(('linux-', 'nvidia-utils-', 'libnvidia-compute-')) and not name.startswith('ubuntu-'):
                raise ValueError('kernel and driver packages must come from Ubuntu')
            closure.add(item)
    release_profile.require_package_closure(config)
    wanted = set(release_profile.packages(config))
    if not release_profile.is_pack(config):
        wanted.update(config['ubuntu']['driver_packages'] + [config['ubuntu']['kernel_package'], config['ubuntu']['modules_package']])
    if not wanted <= closure:
        raise ValueError('repository closure is missing a pinned package')


def fetch_file(url, path, expected=None):
    check_url(url)
    if path.exists() or path.is_symlink():
        raise ValueError('input target already exists')
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response, path.open('xb') as stream:
        while block := response.read(1024 * 1024):
            stream.write(block)
    if expected is not None and prov.digest(path) != expected:
        raise ValueError('download hash differs: ' + path.name)


def model_snapshot(name, model):
    if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', model['id'])
            or any(part in {'.', '..'} for part in model['id'].split('/'))):
        raise ValueError('model needs a safe public owner/name')
    if name in {'tei', 'reranker'}:
        return (Path('payload/models') / name / ('models--' + model['id'].replace('/', '--'))
                / 'snapshots' / model['revision'])
    return Path('payload/models/mineru-home/models') / model['id'].split('/')[1]


def fetch_model(name, model, stage):
    endpoint = 'https://huggingface.co/api/models/' + model['id'] + '/revision/' + model['revision'] + '?blobs=true'
    with urllib.request.urlopen(endpoint, timeout=120) as response:
        metadata = json.load(response)
    if metadata.get('sha') != model['revision'] or not metadata.get('siblings'):
        raise ValueError('Hugging Face revision or file inventory differs')
    pinned = {row['path']: row['sha256'] for row in model['files']}
    if len(pinned) != len(model['files']) or set(pinned) != {row['rfilename'] for row in metadata['siblings']}:
        raise ValueError('snapshot file inventory differs from the complete pinned manifest')
    if name in pdf_model_layout.MINERU_ROLES:
        pdf_model_layout.refuse_upstream_markers(pinned)
        pdf_model_layout.markers_for_repository(model['id'].split('/')[1])
    root = stage / model_snapshot(name, model)
    for item in sorted(metadata['siblings'], key=lambda row: row['rfilename']):
        filename = item['rfilename']
        path = root / relative(filename)
        url = 'https://huggingface.co/' + model['id'] + '/resolve/' + model['revision'] + '/' + urllib.parse.quote(filename, safe='/')
        expected = item.get('lfs', {}).get('sha256')
        blob = item.get('blobId')
        if expected is not None and not re.fullmatch(HEX, expected):
            raise ValueError('invalid LFS hash')
        if expected is None and (not isinstance(blob, str) or not re.fullmatch('[0-9a-f]{40}', blob)):
            raise ValueError('snapshot file has no immutable blob identity')
        if expected is not None and expected != pinned[filename]:
            raise ValueError('LFS hash differs from the snapshot manifest')
        fetch_file(url, path, pinned[filename])
        if expected is None:
            content = path.read_bytes()
            if hashlib.sha1(b'blob ' + str(len(content)).encode('ascii') + b'\0' + content).hexdigest() != blob:
                raise ValueError('Git snapshot blob hash differs')
    if name in pdf_model_layout.MINERU_ROLES:
        pdf_model_layout.finish_snapshot(stage / 'payload/models/mineru-home', model['id'].split('/')[1])
    evidence = stage / 'payload/evidence' / ('model-' + name + '.json')
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(prov.canonical(metadata), encoding='ascii')


def pull_save(reference, archive):
    if not re.fullmatch(r'[^\s@]+@sha256:' + HEX, reference):
        raise ValueError('image reference must use a repository digest')
    subprocess.run(['docker', 'pull', '--platform', 'linux/amd64', reference], check=True)
    rows = json.loads(subprocess.check_output(['docker', 'image', 'inspect', reference], text=True))
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError('expected exactly one inspected image')
    row = rows[0]
    if (not isinstance(row, dict) or not isinstance(row.get('Id'), str)
            or not re.fullmatch('sha256:' + HEX, row['Id'])
            or row.get('Architecture') != 'amd64' or row.get('Os') != 'linux'):
        raise ValueError('image is not linux/amd64')
    repository, source_digest = reference.rsplit('@', 1)
    repository = repository_name(repository)
    if not isinstance(row.get('RepoDigests'), list) or not any(isinstance(item, str) and '@' in item
               and repository_name(item.rsplit('@', 1)[0]) == repository
               and item.rsplit('@', 1)[1] == source_digest for item in row.get('RepoDigests', [])):
        raise ValueError('inspected repository digest differs from pinned reference')
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists() or archive.is_symlink():
        raise ValueError('image archive already exists')
    subprocess.run(['docker', 'image', 'save', '--output', str(archive), reference], check=True)
    return image_binding.archive_binding(archive, source_digest, row['Id'])['config_digest']


def repository_name(value):
    """Normalise only Docker Hub aliases and remove an optional image tag."""
    last = value.rsplit('/', 1)[-1]
    if ':' in last:
        value = value.rsplit(':', 1)[0]
    parts = value.split('/')
    if parts[0] in {'docker.io', 'index.docker.io', 'registry-1.docker.io'}:
        parts = parts[1:]
    elif len(parts) > 1 and ('.' in parts[0] or ':' in parts[0] or parts[0] == 'localhost'):
        return '/'.join(parts)
    if len(parts) == 1:
        parts.insert(0, 'library')
    return 'docker.io/' + '/'.join(parts)


def fetch_distribution(row, dest):
    """Fetch the approved source row without invoking its build backend."""
    distributions.check_sdist_row(row)
    path = dest / relative(row['filename'])
    if path.exists() or path.is_symlink():
        raise ValueError('input target already exists')
    try:
        fetch_file(row['url'], path, row['sha256'])
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        raise
    return path


def fetch_wheels(role, record, stage):
    lock = stage / 'payload/locks' / (role + '.lock')
    fetch_file(record['url'], lock, record['sha256'])
    rows = distributions.lock_requirements(lock.read_text(encoding='ascii'))
    groups = {'torch': [], 'other': []}
    sdists = []
    for (name, version), row in rows.items():
        if name == 'jieba':
            if role != 'converter':
                raise ValueError('sdist is allowed only in the converter lock')
            sdists.append(distributions.sdist_row(name, version, row['hashes']))
        else:
            groups['torch' if name == 'torch' else 'other'].append(row['line'])
    if not groups['torch'] or not groups['other']:
        raise ValueError('lock must include torch and its complete dependency closure')
    dest = stage / 'payload/wheelhouse' / role
    dest.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='fetch-lock-', dir=stage) as directory:
        for group, lines in groups.items():
            requirement = Path(directory) / (group + '.lock')
            requirement.write_text('\n'.join(lines) + '\n', encoding='ascii')
            subprocess.run([sys.executable, '-m', 'pip', '--isolated', 'download', '--disable-pip-version-check',
                            '--require-hashes', '--no-deps', '--only-binary=:all:', '--no-cache-dir',
                            '--index-url', record['torch_index' if group == 'torch' else 'index'],
                            '--requirement', str(requirement), '--dest', str(dest)], check=True)
    for row in sdists:
        fetch_distribution(row, dest)
    distributions.validate_wheelhouse(dest, rows)



def stanzas(text):
    for stanza in text.strip().split('\n\n'):
        yield dict(line.split(': ', 1) for line in stanza.splitlines() if ': ' in line and not line.startswith(' '))


def release_hashes(text):
    """Select each path's strongest Release entry, retaining weak-only entries."""
    algorithms = {'MD5Sum': ('md5', 0), 'SHA1': ('sha1', 1),
                  'SHA256': ('sha256', 2), 'SHA512': ('sha512', 3)}
    hashes = {}
    section = None
    for line in text.splitlines():
        if not line.startswith((' ', '\t')):
            section = algorithms.get(line.removesuffix(':')) if line.endswith(':') else None
        elif section is not None:
            digest, size, path = line.split()
            algorithm, rank = section
            entry = dict(algorithm=algorithm, digest=digest, size=int(size))
            previous = hashes.get(path)
            if previous is None or rank > previous[0]:
                hashes[path] = (rank, entry)
            elif rank == previous[0] and entry != previous[1]:
                raise ValueError('conflicting signed Release entries: ' + path)
    return {path: entry for path, (_, entry) in hashes.items()}


def verify_release_index(path, signed_name, hashes):
    entry = hashes.get(signed_name)
    if entry is None:
        raise ValueError(signed_name + ' is not listed in the signed Release')
    algorithm = entry['algorithm']
    if algorithm not in {'sha256', 'sha512'}:
        raise ValueError(signed_name + ' has only weak MD5Sum/SHA1 hashes in the signed Release')
    if not re.fullmatch(r'[0-9a-f]{' + str(hashlib.new(algorithm).digest_size * 2) + '}', entry['digest']) or entry['size'] < 0:
        raise ValueError('invalid signed Release digest or size: ' + signed_name)
    data = path.read_bytes()
    if (hashlib.new(algorithm, data).hexdigest(), len(data)) != (entry['digest'], entry['size']):
        raise ValueError('package index differs from signed Release ' + algorithm + ' digest or size: ' + signed_name)
    # SHA512 authenticates the bytes; the build kit still pins their SHA256.
    evidence = dict(signed_release_entry=dict(path=signed_name, **entry),
                    sha256=hashlib.sha256(data).hexdigest(), size=len(data))
    return data, evidence


def package_entry(packages, record):
    matches = [item for item in packages if item.get('Package') == record['name'] and item.get('Version') == record['version'] and
               item.get('Architecture') == record['architecture'] and item.get('Filename', '').removeprefix('./') == record['path']]
    for item in matches:
        algorithm = 'sha256' if 'SHA256' in item else 'sha512' if 'SHA512' in item else None
        if algorithm is None:
            continue
        digest = item[algorithm.upper()]
        if not re.fullmatch(r'[0-9a-f]{' + str(hashlib.new(algorithm).digest_size * 2) + '}', digest):
            raise ValueError('invalid package ' + algorithm + ' digest in signed package index')
        if algorithm == 'sha256' and digest != record['sha256']:
            continue
        if 'Size' in item and (not item['Size'].isdigit()):
            raise ValueError('invalid package size in signed package index')
        return dict(algorithm=algorithm, digest=digest, size=int(item['Size']) if 'Size' in item else None)
    if matches and not any('SHA256' in item or 'SHA512' in item for item in matches):
        raise ValueError('pinned package has no SHA256 or SHA512 in the signed package index; MD5Sum/SHA1 alone are refused')
    raise ValueError('pinned package is not covered by a signed package index')


def verify_package(path, record, entry):
    data = path.read_bytes()
    if hashlib.new(entry['algorithm'], data).hexdigest() != entry['digest'] or (entry['size'] is not None and len(data) != entry['size']):
        raise ValueError('downloaded package differs from signed package index digest or size: ' + record['path'])
    if hashlib.sha256(data).hexdigest() != record['sha256']:
        raise ValueError('downloaded package differs from SHA256 pin: ' + record['path'])


def verify_repository(name, repo, stage):
    root = stage / relative(repo['target'])
    release_dir = root / relative(repo['release_dir']) if repo['release_dir'] else root
    inline = release_dir / 'InRelease'
    signature = [str(inline)] if inline.is_file() else [str(release_dir / 'Release.gpg'), str(release_dir / 'Release')]
    result = subprocess.run(['gpgv', '--status-fd=1', '--keyring', str(stage / repo['keyring']), *signature],
                            capture_output=True, text=True, errors='replace', check=True)
    fingerprints = set()
    for line in result.stdout.splitlines():
        if line.startswith('[GNUPG:] VALIDSIG '):
            fields = line.split()
            fingerprints.update((fields[2], fields[-1]))
    if repo['fingerprint'] not in fingerprints:
        raise ValueError('repository signer differs from independently verified fingerprint')
    if inline.is_file():
        raw = inline.read_text(encoding='utf-8')
        body = raw.split('\n\n', 1)[1].split('-----BEGIN PGP SIGNATURE-----', 1)[0]
        body = '\n'.join(line[2:] if line.startswith('- ') else line for line in body.splitlines())
    else:
        body = (release_dir / 'Release').read_text(encoding='utf-8')
    expiry = re.search(r'^Valid-Until: (.+)$', body, re.MULTILINE)
    if expiry and email.utils.parsedate_to_datetime(expiry[1]) <= dt.datetime.now(dt.timezone.utc):
        raise ValueError('repository snapshot has expired')
    hashes = release_hashes(body)
    packages = []
    indexes = []
    for record in repo['metadata']:
        path = root / relative(record['path'])
        if 'Packages' not in path.name:
            continue
        signed_name = path.relative_to(release_dir).as_posix()
        data, index_evidence = verify_release_index(path, signed_name, hashes)
        indexes.append(index_evidence)
        data = lzma.decompress(data) if path.suffix == '.xz' else gzip.decompress(data) if path.suffix == '.gz' else data
        # The index bytes are already hash-checked against the signed Release above. Third-party indexes may carry
        # non-UTF-8 bytes in free-text fields; the fields used here (Package, Version, Filename, Size, SHA256) are
        # ASCII, so undecodable text bytes are replaced instead of refusing. (The lab error of 2026-11-15, 0xed at
        # offset 330, came from gpgv's status output: a signing key's user ID; see the gpgv calls.)
        packages.extend(stanzas(data.decode('utf-8', errors='replace')))
    entries = [package_entry(packages, record) for record in repo['packages']]
    evidence = stage / 'payload/evidence' / ('repository-' + name + '.txt')
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(result.stdout, encoding='ascii', errors='backslashreplace')
    evidence.with_name('repository-' + name + '-indexes.json').write_text(prov.canonical(indexes), encoding='ascii')
    return entries


def verify_iso(config, stage):
    result = subprocess.run(['gpgv', '--status-fd=1', '--keyring', str(stage / 'payload/keyrings/ubuntu-release.gpg'),
                             str(stage / 'iso/SHA256SUMS.gpg'), str(stage / 'iso/SHA256SUMS')],
                            capture_output=True, text=True, errors='replace', check=True)
    valid = [line.split() for line in result.stdout.splitlines() if line.startswith('[GNUPG:] VALIDSIG ')]
    fingerprint = config['fetch']['iso_signing_fingerprint']
    if not any(fingerprint in (fields[2], fields[-1]) for fields in valid):
        raise ValueError('ISO release signer differs')
    entries = dict((parts[1].lstrip('*'), parts[0]) for line in (stage / 'iso/SHA256SUMS').read_text(encoding='ascii').splitlines()
                   if len(parts := line.split()) == 2)
    iso = config['ubuntu']['iso']
    if entries.get(iso) != config['ubuntu']['sha256'] or prov.digest(stage / 'iso' / iso) != config['ubuntu']['sha256']:
        raise ValueError('ISO signed checksum differs')
    evidence = stage / 'payload/evidence/iso-signature.txt'
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(result.stdout, encoding='ascii', errors='backslashreplace')


def write_stage_pins(config, stage):
    """Write the stage copy of the pins using the canonical format."""
    (stage / 'pins.json').write_text(prov.canonical(config), encoding='ascii')


def record_base(config, stage, base_id):
    """Enforce pinned base values and record TO-PIN measurements in the stage only."""
    runtime = config['runtime']
    binding = image_binding.archive_binding(stage / 'base/runtime.tar', runtime['base_image'].rsplit('@', 1)[1], base_id)
    measured = {'base_image_id': binding['config_digest'], 'base_archive_sha256': prov.digest(stage / 'base/runtime.tar')}
    recorded = {}
    for field, value in measured.items():
        expected = config['runtime'][field]
        if isinstance(expected, str) and 'TO-PIN' in expected:
            recorded[field] = value
        elif expected != value and not (field == 'base_image_id' and expected in
                                       {binding['config_digest'], binding['index_digest']}):
            raise ValueError('runtime.' + field + ' differs: expected ' + str(expected) + '; measured ' + str(value))
    existing = runtime.get('base_image_binding')
    if existing is not None and existing != binding:
        raise ValueError('runtime.base_image_binding differs from the measured archive')
    if existing is None:
        recorded['base_image_binding'] = binding
    if recorded:
        staged = json.loads((stage / 'pins.json').read_text(encoding='ascii'))
        staged['runtime'].update(recorded)
        write_stage_pins(staged, stage)
        for field, value in recorded.items():
            print('Recorded runtime.' + field + ' = ' + (value if isinstance(value, str) else json.dumps(value, sort_keys=True)))


def execute(config, stage):
    preflight(config, stage)
    stage.mkdir(parents=True, exist_ok=False)
    write_stage_pins(config, stage)
    for record in release_profile.files(config):
        fetch_file(record['url'], stage / relative(record['path']), record['sha256'])
        if record['path'] == 'payload/models/mineru-home/config.yaml':
            validate_mineru_config((stage / record['path']).read_bytes())
    if not release_profile.is_pack(config):
        verify_iso(config, stage)
    for name, repo in sorted(config['fetch']['repositories'].items()):
        for record in repo['metadata']:
            fetch_file(record['url'], stage / repo['target'] / relative(record['path']), record['sha256'])
        # Signatures/index coverage are checked before fetching any package pool.
        entries = verify_repository(name, repo, stage)
        for record, entry in zip(repo['packages'], entries):
            path = stage / repo['target'] / relative(record['path'])
            fetch_file(record['url'], path, record['sha256'])
            verify_package(path, record, entry)
    record_base(config, stage, pull_save(config['runtime']['base_image'], stage / 'base/runtime.tar'))
    images = []
    for name, variant in sorted(config['tei']['variants'].items()):
        archive = stage / 'payload/images' / ('tei-' + name + '.tar')
        image_id = pull_save(TEI + '@' + variant['digest'], archive)
        binding = image_binding.archive_binding(archive, variant['digest'], image_id)
        images.append(dict(role='tei-' + name, path='images/' + archive.name,
                           **binding, source_digest=variant['digest'], sha256=prov.digest(archive)))
    (stage / 'payload/images/index.json').write_text(prov.canonical(images), encoding='ascii')
    for name, model in sorted(config['models'].items()):
        fetch_model(name, model, stage)
    for role, record in sorted(config['fetch']['locks'].items()):
        fetch_wheels(role, record, stage)
    if prov.digest(stage / 'payload/bin/pairing-helper') != config['runtime']['pairing_helper_sha256']:
        raise ValueError('pairing helper pin differs')
    # This is a fetch inventory, not an assertion that lab or release gates passed.
    (stage / 'FETCHED.json').write_text(prov.canonical(prov.members(stage)), encoding='ascii')
    print('Fetched and hashed files:', len(prov.members(stage)) - 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--stage', type=Path)
    ap.add_argument('--pins', type=Path, default=Path(__file__).parents[1] / 'pins.json')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    try:
        config = json.loads(args.pins.read_text(encoding='ascii'))
        if args.dry_run:
            plan(config, args.stage or Path('stage'))
        else:
            if args.stage is None:
                raise ValueError('--stage is required for a real run')
            execute(config, args.stage)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        ap.exit(2, 'Input preparation refused: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
