#!/usr/bin/env python3
"""Collect verified Linux Forge prerequisite observations for C10."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
METADATA_LIMIT = 2 * 1024 * 1024
GPU_CLASSES = {
    '7.5': 'turing', '8.0': 'ampere80', '8.6': 'ampere86', '8.7': 'ampere86',
    '8.9': 'ada', '9.0': 'hopper', '10.0': 'blackwell100', '12.0': 'blackwell120',
}
GPU_QUERY = ['nvidia-smi', '--query-gpu=driver_version,compute_cap,memory.total',
             '--format=csv,noheader,nounits']


def module(name):
    spec = importlib.util.spec_from_file_location('_forge_preflight_' + name, ROOT / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


getter = module('deep32_get_whole')
settings = module('deep32_settings')
requirements = module('forge_requirements')
ports = module('forge_ports')


def signed_evidence(args, identity, *, backend='docker-engine'):
    with getter.metadata(args) as (folder, index, source):
        if source is None or index is None or index.get('format_version') != 2:
            raise ValueError('Supply local capability release metadata.')
        if Path(identity['download_directory']) != source:
            raise ValueError('The accepted plan names a different local media folder.')
        cap = getter.manifest.read_mirror_capabilities(index, folder / 'SHA256SUMS')
        # The Linux check passes --ingestion as the getter does; the Windows getter always installs the add-on.
        chosen = getter.select_bundles(cap, args.gpu_class, ingestion=getattr(args, 'ingestion', True))
        by_id = {row['bundle_id']: row for row in index['bundles']}
        budget = getter.forge_budget_document(
            chosen, [by_id[row['id']] for row in chosen], args.gpu_class, backend=backend)
        if identity.get('media_components') != budget['media_components']:
            raise ValueError('The accepted plan differs from the signed Forge media selection.')
        digest = getter.manifest.read_sums(folder / 'SHA256SUMS').get(requirements.ASSET)
        if digest is None:
            return budget, None
        try:
            raw = getter.read_checked(source / requirements.ASSET, max_bytes=65536)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError('Forge prerequisites differ from the release signature.')
            envelope = requirements.validate(raw, capability_sha256=index['capability_manifest']['sha256'],
                                             pins_sha256=cap['pins_sha256'], release=cap['release'],
                                             storage_evidence_sha256=cap.get('storage_evidence', {}).get('sha256'))
        except (OSError, ValueError, getter.transfer.DownloadError, getter.manifest.Refusal):
            return budget, False
        return budget, envelope


def gpu_checks(raw, gpu_class, envelope):
    rows = []
    for line in raw.splitlines():
        fields = [field.strip() for field in line.split(',')]
        if len(fields) != 3 or not re.fullmatch(r'[0-9]{1,7}', fields[2]):
            return {}
        try:
            driver = requirements.version_tuple(fields[0])
        except ValueError:
            return {}
        rows.append((driver, GPU_CLASSES.get(fields[1]), int(fields[2])))
    if not rows or len(rows) > 128:
        return {}
    result = {'gpu.capability': all(card == gpu_class for _, card, _ in rows)}
    if envelope:
        floor = max(requirements.version_tuple(value) for value in envelope['driver_floors'].values())
        result['gpu.driver'] = all(driver >= floor for driver, _, _ in rows)
        result['gpu.memory'] = all(memory >= envelope['minimum_vram_mib'] for _, _, memory in rows)
    return result


def storage_checks(info, path, budget, observe, components=None):
    docker_root = getter.forge_docker_root(info)
    rows = []
    for loc, domain in ((path, 'data'), (docker_root, 'docker')):
        device, free = observe(loc, existing=domain == 'docker')
        needed = budget[domain + '_bytes']
        if domain == 'data':
            # The second pass runs after the getter wrote the joined copy beside the data root; the data budget
            # already includes it, so a complete copy on the same filesystem is not asked for twice.
            credit = getter.joined_cache_bytes(path, components)
            if credit:
                cache_device, _ = observe(str(Path(path).with_name(Path(path).name + '.deep32-media')), existing=True)
                if cache_device == device:
                    needed = max(0, needed - credit)
        rows.append(getter.storage_row(device, needed, free, budget))
    return rows


def collect(args, observed, *, run=subprocess.run, observe=getter.observed_volume):
    """Return only evidence produced by the local probe and verified release."""
    if not isinstance(observed, dict) or set(observed) != {'docker', 'tailscale'}:
        raise ValueError('Expected local Docker and Tailscale observations.')
    accepted = settings.check_plan(settings.read_json(Path(args.plan)), args.accept_plan)
    identity = accepted['identity']
    if ((identity['journey'], identity['form'], identity['platform'], identity['docker_role'])
            != (args.journey, 'docker', 'linux', 'forge')
            or Path(identity['data_root']) != Path(args.forge_data_root).absolute()):
        raise ValueError('The consented Linux Forge target or journey differs from this check.')
    budget, envelope = signed_evidence(args, identity)
    checks, detail, notice = {}, {}, None
    if envelope is None:
        detail['gpu.release-envelope'] = 'Obtain the signed FORGE-PREREQUISITES.json from this release.'
        detail['platform.recipe'] = 'Obtain the accepted Linux host recipe in the signed Forge prerequisites.'
    elif envelope is False:
        checks['gpu.release-envelope'] = False
        detail['gpu.release-envelope'] = 'Restore the exact signed Forge prerequisite asset for this release.'
    else:
        checks['gpu.release-envelope'] = True
        host_arch = 'amd64' if args.architecture in ('x86_64', 'x64', 'amd64') else args.architecture
        if args.os_id and args.os_version:
            recipe = next((row for row in envelope['recipes']
                           if (row['os_id'], row['os_version'], row['architecture'], row['journey'])
                           == (args.os_id, args.os_version, host_arch, args.journey)), None)
            checks['platform.recipe'] = recipe is not None
            if recipe is None:
                detail['platform.recipe'] = 'Use an OS, architecture and journey with accepted signed Linux host preparation.'
            else:
                # A waived recipe passes this one check; every other check below is unchanged.
                notice = requirements.waiver_notice(recipe)

    store_issue = None
    try:
        rows = storage_checks(observed['docker'], identity['data_root'], budget, observe,
                              identity.get('media_components'))
    except getter.DockerImageStoreRefusal as error:
        rows = None
        if error.code == 'docker-store-containerd':
            checks['docker.storage'] = False
        detail['docker.storage'] = str(error)
        store_issue = {'code': error.code, 'next_step': error.next_step}
    except (OSError, ValueError, getter.manifest.Refusal):
        rows = None
    if rows is not None:
        checks['docker.storage'] = True

    try:
        sample = run(GPU_QUERY, capture_output=True, text=True, timeout=4)
        if sample.returncode == 0 and len(sample.stdout) <= 16384:
            checks.update(gpu_checks(sample.stdout, args.gpu_class, envelope))
    except (OSError, subprocess.SubprocessError):
        pass
    if envelope and checks.get('gpu.driver') is False:
        floor = max(envelope['driver_floors'].values(), key=requirements.version_tuple)
        detail['gpu.driver'] = 'Use NVIDIA driver ' + floor + ' or later on every visible GPU.'
    if envelope and checks.get('gpu.memory') is False:
        detail['gpu.memory'] = 'Use at least ' + str(envelope['minimum_vram_mib']) + ' MiB on every selected GPU.'

    try:
        suffix, hostname = settings.tailnet_identity(observed['tailscale'])
    except (ValueError, TypeError):
        hostname = None
    if hostname and identity.get('tailnet_sha256') is not None:
        checks['host.membership-confirmed'] = settings.tailnet_digest(suffix) == identity['tailnet_sha256']
        if not checks['host.membership-confirmed']:
            detail['host.membership-confirmed'] = 'The live tailnet changed. Confirm the intended tailnet and review a new plan.'
        else:
            try:
                ports.check_linux_ports(Path(identity['data_root']), accepted['settings'], hostname, run=run)
                checks['gpu.ports'] = True
            except ports.PortConflict as error:
                checks['gpu.ports'] = False
                detail['gpu.ports'] = str(error)
            except ports.PortUnavailable as error:
                detail['gpu.ports'] = str(error)
    result = {'prerequisites': checks, 'details': detail, 'storage_plan': rows or []}
    if notice is not None:
        result['notice'] = notice
    if store_issue is not None:
        result['docker_image_store'] = store_issue
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ('source', 'gpu-class', 'forge-data-root', 'key', 'fingerprint', 'plan', 'accept-plan',
                 'journey', 'os-id', 'os-version', 'architecture'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--gpg', default='')
    parser.add_argument('--ingestion', action='store_true')
    args = parser.parse_args(argv)
    args.release = 'v0.1.0-alpha.3'
    try:
        raw = sys.stdin.buffer.read(METADATA_LIMIT + 1)
        if len(raw) > METADATA_LIMIT:
            raise ValueError('Local observations exceed their limit.')
        observed = json.loads(raw, object_pairs_hook=getter.manifest.unique_json)
        print(json.dumps(collect(args, observed), sort_keys=True, separators=(',', ':')))
    except (OSError, ValueError, TypeError, KeyError, getter.manifest.Refusal, getter.transfer.DownloadError):
        parser.exit(1, 'The verified Forge release or reviewed plan could not be checked. Review both inputs.\n')


if __name__ == '__main__':
    main()
