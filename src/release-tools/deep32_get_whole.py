#!/usr/bin/env python3
"""Authenticated whole-media getter used by both launchers (stdlib, Python 3.10+).

Downloads emit four-column verified-image records. The internal Forge storage
query emits anonymous per-filesystem JSON. Human reports go to stderr.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent


def module(name):
    spec = importlib.util.spec_from_file_location('_getter_' + name, ROOT / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


manifest = module('deep32_get_manifest')
transfer = module('deep32_download')
METADATA = ('SHA256SUMS', 'SHA256SUMS.sig', 'MIRRORS.json')
MAX_CAPABILITY = 8 * 1024 * 1024
FORGE_CLASSIC_DRIVERS = frozenset({'overlay2'})
DECLARED_BASIS = 'declared-upper-bound'


class DockerImageStoreRefusal(manifest.NeedsAction):
    def __init__(self, code, next_step):
        self.code = code
        self.next_step = next_step
        super().__init__(f'{code}: {next_step}')


def classic_docker_root(info):
    """Return the observed classic store root from the local Linux Engine JSON."""
    unknown = DockerImageStoreRefusal(
        'docker-store-unknown', 'Run the Forge check against the dedicated classic overlay2 engine with complete Docker info.')
    if not isinstance(info, dict) or info.get('OSType') != 'linux':
        raise unknown
    status = info.get('DriverStatus')
    if (type(status) is not list or any(type(row) is not list or len(row) != 2
            or any(type(value) is not str for value in row) for row in status)):
        raise unknown
    markers = [value for key, value in status if key.casefold() == 'driver-type']
    if markers == ['io.containerd.snapshotter.v1']:
        raise DockerImageStoreRefusal(
            'docker-store-containerd',
            'Use the classic overlay2 image store on the dedicated Forge engine. '
            'For Docker Desktop, clear Use containerd for pulling and storing images in General settings.')
    driver, root = info.get('Driver'), info.get('DockerRootDir')
    if (markers or type(driver) is not str or driver not in FORGE_CLASSIC_DRIVERS or type(root) is not str
            or root.startswith('//') or '\\' in root or not PurePosixPath(root).is_absolute()
            or '..' in PurePosixPath(root).parts or any(ord(char) < 32 or ord(char) == 127 for char in root)):
        raise unknown
    return root


CONTAINERD_SYSTEM_SOCKET = '/run/containerd/containerd.sock'
CONTAINERD_SYSTEM_ROOT = '/var/lib/containerd'
CONTAINERD_MANAGED_PREFIX = '/var/run/docker/containerd/'


def forge_docker_root(info):
    """The local Linux Engine root that receives the Forge's images, in either image store.

    The classic overlay2 store keeps them under DockerRootDir (classic_docker_root). With the containerd image
    store, containerd keeps them: the system containerd that Docker Engine's packages run beside dockerd
    (Containerd.Address /run/containerd/containerd.sock) under its default root /var/lib/containerd, and a
    dockerd-managed containerd (an address under /var/run/docker/containerd/) under DockerRootDir. Any other
    shape is refused as unknown. Docker Desktop keeps the classic-only rule (forge_preflight_windows).
    host-requirements.py applies the same rule on the Forge; test_whole_get keeps the two equal.
    """
    try:
        return classic_docker_root(info)
    except DockerImageStoreRefusal as error:
        if error.code != 'docker-store-containerd':
            raise
    unknown = DockerImageStoreRefusal(
        'docker-store-unknown', 'Run the Forge check against the dedicated classic overlay2 engine with complete Docker info.')
    root = info.get('DockerRootDir')
    containerd = info.get('Containerd')
    address = containerd.get('Address') if isinstance(containerd, dict) else None
    if (info.get('Driver') != 'overlayfs' or type(root) is not str or type(address) is not str
            or root.startswith('//') or '\\' in root or not PurePosixPath(root).is_absolute()
            or '..' in PurePosixPath(root).parts or any(ord(char) < 32 or ord(char) == 127 for char in root)):
        raise unknown
    if address == CONTAINERD_SYSTEM_SOCKET:
        return CONTAINERD_SYSTEM_ROOT
    if address.startswith(CONTAINERD_MANAGED_PREFIX):
        return root
    raise unknown


def protect_scratch(folder):
    """Restrict only a just-created host scratch directory, then inspect it."""
    module('deep32_settings').protect(folder, directory=True)
    transfer.FileSystem().private_directory(folder)


def read_checked(source, *, max_bytes=manifest.MAX_META):
    """Bounded read with an open-handle identity and change check."""
    fs = transfer.FileSystem()
    with fs.open_payload(source, writable=False) as stream:
        before = os.fstat(stream.fileno())
        data = stream.read(max_bytes + 1)
        after = fs.recheck(source, stream)
        if (len(data) > max_bytes or
                (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(data) != after.st_size):
            raise manifest.Refusal('Metadata is oversized or changed while reading: ' + Path(source).name)
        return data


def fetch_metadata(url, *, opener=None, max_bytes=manifest.MAX_META):
    """Fresh, bounded, direct HTTP(S); same redirect and header rules as payloads."""
    response = None
    try:
        response, _ = transfer._open_response({'url': url}, 0, opener or transfer.open_http)
        if response.status != 200:
            raise manifest.Refusal('Release metadata request failed')
        encoding = transfer._one(response, 'Content-Encoding')
        length = transfer._one(response, 'Content-Length')
        framing = transfer._one(response, 'Transfer-Encoding')
        if (encoding not in (None, 'identity') or framing not in (None, 'chunked') or
                (length is not None and framing is not None) or
                transfer._one(response, 'Content-Range') is not None):
            raise manifest.Refusal('Invalid release metadata response headers')
        if length is not None and (not re.fullmatch(r'[0-9]+', length) or int(length) > max_bytes):
            raise manifest.Refusal('Release metadata exceeds its size bound')
        # _Body handles strict chunk framing without another response file.
        body = transfer._Body(response, framing == 'chunked')
        data = bytearray()
        while True:
            block = body.read(min(transfer.BUFFER, max_bytes + 1 - len(data)))
            if not block:
                break
            data.extend(block)
            if len(data) > max_bytes:
                raise manifest.Refusal('Release metadata exceeds its size bound')
        if length is not None and len(data) != int(length):
            raise manifest.Refusal('Release metadata length differs from its response')
        return bytes(data)
    except (transfer._MirrorFailure, OSError) as error:
        raise manifest.Refusal('Release metadata transport failed; use a complete local --from folder to work offline') from None
    finally:
        if response is not None:
            response.close()


def authenticate(folder, args):
    manifest.authenticate(folder, args.key, args.fingerprint, args.gpg)
    return manifest.read_mirrors(folder / 'MIRRORS.json', folder / 'SHA256SUMS', args.release)


def capability_metadata(folder, index, *, source=None, base=None, opener=None):
    """Bind the capability and any declared storage evidence to the authenticated root."""
    if index['format_version'] != 2:
        return None
    name = index['capability_manifest']['file']
    target = folder / name
    if source is not None:
        target.write_bytes(read_checked(source / name, max_bytes=MAX_CAPABILITY))
    elif base is not None:
        target.write_bytes(fetch_metadata(base + name, opener=opener, max_bytes=MAX_CAPABILITY))
    sums_path = folder / 'SHA256SUMS'
    capability = manifest.read_mirror_capabilities(index, sums_path, target)
    capabilities = module('capabilities')
    storage_name = capabilities.STORAGE_ASSET
    root_digest = manifest.read_sums(sums_path).get(storage_name)
    binding = capability.get('storage_evidence')
    raw = None
    if binding is not None:
        if root_digest != binding['sha256']:
            raise manifest.Refusal('Detached storage measurement checksum is absent or differs in the release root.')
        if base is not None:
            raw = fetch_metadata(base + storage_name, opener=opener, max_bytes=capabilities.STORAGE_LIMIT)
        else:
            raw = read_checked((source or folder) / storage_name, max_bytes=capabilities.STORAGE_LIMIT)
    # The signed mirror index gives each ISO length a declared budget must state.
    observed = {row['bundle_id']: {'iso_bytes': row['size']} for row in index['bundles']}
    capabilities.verify_detached_storage(capability, raw, root_digest, observed=observed)
    if raw is not None and (source is not None or base is not None):
        (folder / storage_name).write_bytes(raw)
    return capability


def local_source(value):
    decoded = unquote(urlsplit(value).path) if value.startswith('file:') else value
    if any(ord(c) < 32 or ord(c) == 127 for c in decoded):
        raise manifest.Refusal('Control characters in local paths are refused')
    source = manifest.local_source(value)
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        if kernel.GetDriveTypeW(source.anchor) not in {2, 3, 6}:
            raise manifest.Refusal('Local media must be on a local fixed or removable filesystem')
    return source


def compare_revisions(previous, current, old_folder, new_folder):
    if current['revision'] < previous['revision']:
        raise manifest.Refusal('Mirror index rollback refused')
    if current['revision'] == previous['revision']:
        if any(read_checked(old_folder / name) != read_checked(new_folder / name)
               for name in ('SHA256SUMS', 'MIRRORS.json')):
            raise manifest.Refusal('Authenticated bytes changed at the same mirror revision')
        return
    def immutable(data):
        result = dict(data)
        result.pop('revision')
        result.pop('published_at')
        result['discs'] = [{k: v for k, v in disc.items() if k != 'mirrors'} for disc in data['discs']]
        if 'bundles' in data:
            result['bundles'] = [{k: v for k, v in bundle.items() if k != 'mirrors'} for bundle in data['bundles']]
        return result
    if immutable(previous) != immutable(current):
        raise manifest.Refusal('A mirror-only revision changed immutable release data')
    for old, new in zip(previous['discs'] + previous.get('bundles', []),
                        current['discs'] + current.get('bundles', [])):
        if new['mirrors'][:len(old['mirrors'])] != old['mirrors']:
            raise manifest.Refusal('Mirror removal or reordering has no approved policy')


@contextlib.contextmanager
def metadata(args, *, opener=None):
    """Yield an authenticated set, never mutating a local source or good snapshot."""
    if args.source:
        source = local_source(args.source)
        with tempfile.TemporaryDirectory(prefix='deep32-local-') as name:
            folder = Path(name)
            protect_scratch(folder)
            names = METADATA if args.release != 'v0.1.0-alpha.1' else ('SHA256SUMS', 'SHA256SUMS.sig', 'ASSEMBLY.json')
            for item in names:
                (folder / item).write_bytes(read_checked(source / item))
            if args.release == 'v0.1.0-alpha.1':
                manifest.authenticate(folder, args.key, args.fingerprint, args.gpg)
                yield folder, None, source
            else:
                data = authenticate(folder, args)
                capability_metadata(folder, data, source=source)
                yield folder, data, source
        return
    cache = transfer.FileSystem().private_directory(args.cache)
    with transfer.FileSystem().lock(cache / '.metadata.lock'):
        with tempfile.TemporaryDirectory(prefix='.fresh-', dir=cache) as name:
            fresh = Path(name)
            protect_scratch(fresh)
            base = 'https://github.com/%s/releases/download/%s/' % (args.repo, args.release)
            for item in METADATA:
                (fresh / item).write_bytes(fetch_metadata(base + item, opener=opener))
            data = authenticate(fresh, args)
            capability_metadata(fresh, data, base=base, opener=opener)
            # Retain every immutable authenticated snapshot. Scanning them rather
            # than trusting a mutable CURRENT pointer preserves the high-water mark.
            for old in sorted(cache.glob('revision-*')):
                transfer.FileSystem().path(old, directory=True)
                if not re.fullmatch(r'revision-[1-9][0-9]*-[0-9a-f]{64}', old.name):
                    raise manifest.Refusal('Unexpected mirror-cache snapshot name')
                previous = authenticate(old, args)
                capability_metadata(old, previous)
                compare_revisions(previous, data, old, fresh)
            identity = hashlib.sha256(read_checked(fresh / 'SHA256SUMS') + read_checked(fresh / 'MIRRORS.json')).hexdigest()
            target = cache / ('revision-%d-%s' % (data['revision'], identity))
            if not target.exists():
                # Rename the entire verified set on its own filesystem. A failed
                # refresh never replaces any earlier authenticated snapshot.
                os.rename(fresh, target)
            yield target, data, None


def select_bundles(capability, gpu_class, *, ingestion=False, full=False):
    """Pure exact-class selection; caller supplies an authenticated manifest."""
    capabilities = module('capabilities')
    try:
        return capabilities.select_runtime_bundles(capability, gpu_class,
            include_ingestion=ingestion, include_full=full)
    except ValueError as error:
        raise manifest.Refusal(str(error)) from None


def forge_budget_document(selected, discs, gpu_class, *, backend='docker-engine'):
    component_names = ['core', 'embedder-' + gpu_class]
    if len(selected) == 3:
        component_names.append('ingestion')
    if [bundle['id'] for bundle in selected] != component_names or len(discs) != len(component_names):
        raise manifest.Refusal('The authenticated Forge budget has the wrong component selection.')
    if backend not in ('docker-engine', 'docker-desktop'):
        raise ValueError('Unknown Forge storage backend.')
    values = {'data': 0, 'docker': 0}
    if backend == 'docker-desktop':
        values['desktop-backing'] = 0
    media, declared = [], False
    for bundle, disc in zip(selected, discs):
        budgets = [row for row in bundle.get('storage_budgets', ()) if row['backend'] == backend]
        domains = budgets[0]['domains'] if len(budgets) == 1 else ()
        declared = declared or (len(budgets) == 1 and budgets[0].get('method') == DECLARED_BASIS)
        for domain in values:
            matches = [row['peak_bytes'] for row in domains if row['domain'] == domain]
            if len(matches) != 1 or type(matches[0]) is not int or matches[0] < 0:
                raise manifest.Refusal('The selected signed media have no Forge budget for ' + domain + '.')
            values[domain] += matches[0]
        media.append({'component': bundle['id'], 'size': disc['size'], 'sha256': disc['sha256']})
    document = {'format': 'deep32-forge-budget/1', 'media_components': media,
                **{domain + '_bytes': value for domain, value in values.items()}}
    if declared:
        # A sum that contains one declared budget is a declared upper bound.
        document['basis'] = DECLARED_BASIS
    return document


def storage_row(filesystem_id, needed, free, budget):
    """One preflight storage row; a declared budget keeps its label."""
    row = {'filesystem_id': filesystem_id, 'needed_bytes': needed, 'free_bytes': free}
    if budget.get('basis') is not None:
        row['basis'] = budget['basis']
    return row


def joined_cache_bytes(data_root, components):
    """Bytes of a complete joined Disc 3 copy already beside the data root, else 0.

    The copy (deep32_settings.media_cache: <data root>.deep32-media/<sha256 of the component digests>) counts only
    when every PAYLOAD.json row is a regular file of exactly its recorded size (no link) and the three metadata files
    exist; the loader verifies every byte again before use (deep32_download.verify_output). The data root arrives as
    a native absolute path on the host that runs the check.
    """
    if not isinstance(components, list) or not components:
        return 0
    try:
        root = Path(data_root)
        key = hashlib.sha256(''.join(row['sha256'] for row in components).encode('ascii')).hexdigest()
        cache = root.with_name(root.name + '.deep32-media') / key
        total = 0
        for name in ('PAYLOAD.json', 'PAYLOAD.SHA256', 'RUNTIME.json'):
            info = (cache / name).lstat()
            if not stat.S_ISREG(info.st_mode):
                return 0
            total += info.st_size
        rows = json.loads(read_checked(cache / 'PAYLOAD.json'), object_pairs_hook=manifest.unique_json)
        for row in rows:
            relative = PurePosixPath(row['path'])
            if relative.is_absolute() or '..' in relative.parts or ':' in row['path'] or '\\' in row['path']:
                return 0
            info = (cache / 'payload' / relative).lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size != row['size']:
                return 0
            total += row['size']
        return total
    except (OSError, ValueError, TypeError, KeyError, manifest.Refusal, transfer.DownloadError):
        return 0


def observed_volume(value, *, existing):
    if (not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or value.startswith(("//", "\\\\")) or not Path(value).is_absolute()):
        raise manifest.Refusal('Forge storage requires absolute local filesystem paths.')
    path = Path(value)
    while True:
        try:
            info = path.stat()
        except FileNotFoundError:
            if existing or path == path.parent:
                raise manifest.Refusal('A Forge storage filesystem could not be observed.') from None
            path = path.parent
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise manifest.Refusal('A Forge storage filesystem could not be observed.')
        return str(info.st_dev), shutil.disk_usage(path).free


def linux_forge_storage(args, raw_docker_info, *, observe=observed_volume):
    """Bind current filesystem space to the signed, exact local Forge selection.

    The calling inventory obtains Docker info from its explicit local socket.
    The join checks the cache's exact unique bytes before writing it. The signed
    data and Docker peaks are checked independently and rechecked after joining.
    """
    if (args.release != 'v0.1.0-alpha.3' or args.disc != 'disc3' or not args.source
            or not args.gpu_class or args.full or not args.forge_data_root):
        raise manifest.Refusal('Forge storage requires local Disc 3 media, class and data root.')
    if len(raw_docker_info) > 1024 * 1024:
        raise manifest.Refusal('Local Docker info exceeds its size bound.')
    info = json.loads(raw_docker_info, object_pairs_hook=manifest.unique_json)
    docker_root = forge_docker_root(info)
    with metadata(args) as (folder, index, source):
        if source is None or index is None or index['format_version'] != 2:
            raise manifest.Refusal('Forge storage requires authenticated capability metadata.')
        capability = manifest.read_mirror_capabilities(index, folder / 'SHA256SUMS')
        selected = select_bundles(capability, args.gpu_class, ingestion=args.ingestion)
        by_id = {row['bundle_id']: row for row in index['bundles']}
        discs = [by_id[bundle['id']] for bundle in selected]
        budget = forge_budget_document(selected, discs, args.gpu_class)
    data_id, data_free = observe(args.forge_data_root, existing=False)
    docker_id, docker_free = observe(docker_root, existing=True)
    return [storage_row(data_id, budget['data_bytes'], data_free, budget),
            storage_row(docker_id, budget['docker_bytes'], docker_free, budget)]


def get(args, *, opener=None, filesystem=None):
    """Return all verified records; disc3 selects core and its exact embedder."""
    with metadata(args, opener=opener) as (folder, index, source):
        if (args.gpu_class or args.ingestion or args.full) and args.disc != 'disc3':
            raise manifest.Refusal('Capability pack selection requires disc3')
        if index is not None and args.disc == 'disc3':
            if index['format_version'] != 2:
                raise manifest.Refusal('GPU packs require mirrors-v2 capability transport metadata; no universal fallback is permitted')
            capability = manifest.read_mirror_capabilities(index, folder / 'SHA256SUMS')
            selected = select_bundles(capability, args.gpu_class,
                ingestion=args.ingestion, full=args.full)
            by_id = {row['bundle_id']: row for row in index['bundles']}
            discs = [by_id[row['id']] for row in selected]
        elif index is not None:
            discs = [next(row for row in index['discs'] if row['role'] == args.disc)]
        elif args.gpu_class or args.ingestion or args.full:
            raise manifest.Refusal('Capability selection requires mirrors-v2 release metadata')
        if index is None:
            image, _ = manifest.selected_records(folder / 'ASSEMBLY.json', folder / 'SHA256SUMS', args.disc, args.release)
            disc = {'file': image[1], 'size': int(image[2]), 'sha256': image[3]}
            local = source / disc['file']
            fs = filesystem or transfer.FileSystem()
            with fs.open_payload(local, writable=False) as stream:
                if not transfer._verify_handle(stream, disc):
                    raise manifest.Refusal('Local image size or SHA-256 failed: ' + disc['file'])
                fs.recheck(local, stream)
            image_path = str(local)
            message = 'Verified local image %s.' % image_path
            records = [(image_path, disc['file'], disc['size'], disc['sha256'])]
            print(message, file=sys.stderr)
        else:
            records = []
            for disc in discs:
                if source is not None:
                    image_path = transfer.verify_local_image(disc, source / disc['file'], filesystem=filesystem)
                    message = 'Verified local image %s.' % image_path
                else:
                    report = transfer.fetch_whole(disc, args.destination, release=args.release,
                        revision=index['revision'], fingerprint=args.fingerprint,
                        torrent_url=index['archive']['torrent_url'], opener=opener, filesystem=filesystem)
                    image_path, message = report['path'], report['message']
                records.append((image_path, disc['file'], disc['size'], disc['sha256']))
                print(message, file=sys.stderr)
            print('Optional manual torrent: ' + index['archive']['torrent_url'], file=sys.stderr)
        if any(any(ord(c) < 32 or ord(c) == 127 for c in record[0]) for record in records):
            raise manifest.Refusal('Control characters in local paths are refused')
        return records


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('disc', choices=sorted(manifest.ROLES))
    result.add_argument('--release', default='v0.1.0-alpha.3')
    result.add_argument('--repo', default='hellobpdu/deep32')
    result.add_argument('--key', default=str(ROOT.parents[1] / 'install/release-key.gpg'))
    result.add_argument('--fingerprint', required=True)
    result.add_argument('--gpg', default='')
    result.add_argument('--cache')
    result.add_argument('--to', dest='destination')
    result.add_argument('--from', dest='source')
    result.add_argument('--gpu-class')
    result.add_argument('--ingestion', action='store_true')
    result.add_argument('--full', action='store_true')
    result.add_argument('--forge-storage', action='store_true', help=argparse.SUPPRESS)
    result.add_argument('--forge-data-root', help=argparse.SUPPRESS)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if (not re.fullmatch(r'v[0-9a-z][0-9a-z.-]{0,79}', args.release) or '..' in args.release or
            not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', args.repo) or
            '..' in args.repo or not manifest.FINGERPRINT.fullmatch(args.fingerprint)):
        raise manifest.Refusal('Invalid release, repository or fingerprint')
    if any(any(ord(c) < 32 or ord(c) == 127 for c in value) for value in
           (args.source, args.destination, args.cache, args.key, args.gpg, args.forge_data_root) if value):
        raise manifest.Refusal('Control characters in local paths are refused')
    if args.source:
        if args.destination:
            raise manifest.Refusal('--from and explicit --to cannot be combined')
    elif not args.destination or not args.cache:
        raise manifest.Refusal('Online transfer requires --to and a private --cache')
    if (args.gpu_class or args.ingestion or args.full) and args.disc != 'disc3':
        raise manifest.Refusal('Capability pack selection requires disc3')
    if args.forge_storage:
        print(json.dumps(linux_forge_storage(args, sys.stdin.buffer.read(1024 * 1024 + 1)),
                         separators=(',', ':')))
        return
    if args.forge_data_root:
        raise manifest.Refusal('--forge-data-root requires --forge-storage')
    for record in get(args):
        print('\t'.join(map(str, record)))


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(newline='\n')
        sys.stderr.reconfigure(newline='\n')
        main()
    except manifest.PendingDisc as error:
        print(str(error), file=sys.stderr)
        sys.exit(3)
    except manifest.NeedsAction as error:
        print(str(error), file=sys.stderr)
        sys.exit(10)
    except KeyboardInterrupt:
        print('Download interrupted; verified chunks can be resumed.', file=sys.stderr)
        sys.exit(1)
    except (manifest.Refusal, transfer.DownloadError, OSError, ValueError, TypeError, KeyError) as error:
        print('Download refused: %s' % error, file=sys.stderr)
        sys.exit(1)
