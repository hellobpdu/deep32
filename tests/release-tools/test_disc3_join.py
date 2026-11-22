#!/usr/bin/env python3
"""Exercise the split disc 3 producer, release join and sealed payload verifier."""

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
GPU = ROOT / 'src/gpu-pack'
TOOLS = ROOT / 'src/release-tools'
EPOCH = 1_760_000_000
GPU_CLASS = 'turing'
COMPONENTS = ('core', 'embedder-' + GPU_CLASS, 'ingestion')
ROLES = ('builder', 'converter', 'door', 'qdrant', 'reranker', 'tei-' + GPU_CLASS)
SCRATCH = None


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


producer = load_module('synthetic_alpha2_producer', GPU / 'build/alpha2_disc3.py')
provision = load_module('synthetic_sealed_provision', GPU / 'forms/provision.py')
forge = load_module('synthetic_sealed_forge', GPU / 'forms/forge_docker_role.py')


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + '\n').encode('ascii')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def put(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def files(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def inventory(contents):
    return [{'path': name, 'sha256': sha(contents[name]), 'size': len(contents[name])}
            for name in sorted(contents)]


def sums(rows):
    return ''.join(row['sha256'] + '  ' + row['path'] + '\n' for row in rows).encode('ascii')


def archive(payload, role):
    config = canonical({'architecture': 'amd64', 'os': 'linux',
                        'rootfs': {'type': 'layers', 'diff_ids': []},
                        'config': {'Labels': {'synthetic-role': role}}})
    config_id = 'sha256:' + sha(config)
    config_name = sha(config) + '.json'
    manifest = canonical([{'Config': config_name, 'Layers': [], 'RepoTags': ['synthetic:test']}])
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        for name, raw in ((config_name, config), ('manifest.json', manifest)):
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            tar.addfile(member, io.BytesIO(raw))
    name = f'images/{role}.tar'
    raw = stream.getvalue()
    put(payload / name, raw)
    source = 'sha256:' + sha(role.encode('ascii')) if role in ('qdrant', 'tei-turing') else None
    return {'role': role, 'path': name, 'sha256': sha(raw), 'source_digest': source,
            'image_id': config_id, 'config_digest': config_id, 'index_digest': source,
            'manifest_digest': None, 'archive_format': 'classic'}


def producer_inputs(root):
    stage, kit = root / 'source', root / 'kit'
    payload = stage / 'payload'
    images = [archive(payload, role) for role in ROLES]
    tei = next(row for row in images if row['role'] == 'tei-turing')
    qdrant = next(row for row in images if row['role'] == 'qdrant')
    qdrant['version'] = '1.19.1'
    pins = {'release_profile': 'pack', 'models': {},
            'tei': {'variants': {'turing': {'digest': tei['source_digest']}}},
            'runtime': {row['role'] + '_image_id': row['config_digest']
                        for row in images if row is not tei},
            'builder': {'qdrant': {**qdrant, 'image': 'synthetic/qdrant@' + qdrant['source_digest']}}}
    for role in ('tei', 'reranker'):
        revision = sha(role.encode('ascii'))[:40]
        raw = f'synthetic {role} model bytes\n'.encode('ascii')
        prefix = f'models/{role}/models--synthetic--{role}/snapshots/{revision}'
        put(payload / prefix / 'model.bin', raw)
        pins['models'][role] = {'id': f'synthetic/{role}', 'revision': revision,
                               'files': [{'path': 'model.bin', 'sha256': sha(raw)}]}
    put(payload / 'models/mineru-home/docs/model.bin', b'synthetic ingestion model\n')
    put(payload / 'source/production-converter/synthetic.tar', b'synthetic corresponding source\n')
    put(payload / 'licences/AGPL-3.0.txt', b'synthetic fixture licence text\n')
    put(payload / 'evidence/input-attestation.json', canonical({'records': ['evidence/synthetic.txt']}))
    put(payload / 'evidence/synthetic.txt', b'synthetic attested content\n')
    put(payload / 'images/index.json', canonical(images))
    put(stage / 'pins.json', canonical(pins))
    put(stage / 'PAYLOAD.json', canonical(inventory(files(payload))))
    put(kit / 'pins.json', canonical(pins))
    for name in ('LICENSE', 'NOTICE', 'licenses/MinerU-4.0.7-LICENSE.md',
                 'forms/load-docker.sh', 'forms/provision.py', 'build/provenance.py', 'build/thin_forms.py',
                 'pairing/bin/deep32-pair', 'pairing/bin/pairing-helper',
                 'release/addon-licenses/synthetic.txt'):
        put(kit / name, b'synthetic runtime fixture\n')
    for name in producer.pack._thin.PAIRING_MODULES:
        put(kit / f'pairing/deep32net/{name}.py', b'# synthetic fixture\n')
    return stage, kit, images, pins


BASH = (Path('C:/Program Files/Git/bin/bash.exe') if os.name == 'nt'
        else Path('/bin/bash'))

BASH_FIXTURE = r'''import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

root = Path(os.environ['SYNTHETIC_SPLIT_ROOT'])
args = sys.argv[1:]
phase = args[0]
records = root / 'events.jsonl'
def report(*values):
    with records.open('a', encoding='ascii') as stream:
        stream.write(json.dumps(values) + '\n')

def check_pending_mount(value):
    path = Path(value).resolve()
    assert path.is_relative_to(root / 'tmp') and path.name.startswith('mount.')
    return path

if phase == 'check-settings':
    # The getter names every missing non-interactive choice before it reads any media.
    assert args[1] == '--split' and '--linux-forge-tailnet' in args
    if '--confirm-tailnet' not in args:
        report('settings-refused')
        print('Settings refused: missing non-interactive choices: confirm-tailnet', file=sys.stderr)
        raise SystemExit(1)
elif phase == 'disc3':
    assert '--from' in args and '--ingestion' in args and '--full' not in args
    report('verified-media')
    sys.stdout.reconfigure(newline='\n')
    for role in ('core', 'embedder-turing', 'ingestion'):
        image = root / 'outer' / (role + '.iso')
        raw = image.read_bytes()
        print(f'{image}\t{image.name}\t{len(raw)}\t{hashlib.sha256(raw).hexdigest()}')
elif phase == 'plan':
    assert '--linux-forge-tailnet' in args
    assert args[args.index('--confirm-tailnet') + 1] == 'synthetic-tailnet.ts.net'
    rows = [args[i + 1:i + 4] for i, arg in enumerate(args) if arg == '--media-component']
    report('plan', rows)
    if os.environ.get('SYNTHETIC_SPLIT_CHANGE_INGESTION'):
        (root / 'outer/ingestion.iso').write_bytes(b'changed synthetic ISO bytes')
    print(json.dumps({'digest': 'a' * 64}))
elif phase == 'media-cache':
    assert args[-2:] == ['--accept-plan', 'a' * 64]
    report('consent')
    cache = root / 'cache'
    cache.mkdir(exist_ok=True)
    sys.stdout.reconfigure(newline='\n')
    joined = (cache / 'joined').as_posix()
    if len(joined) > 2 and joined[1:3] == ':/':
        joined = '/' + joined[0].lower() + joined[2:]
    print(joined)
elif phase == 'mount':
    assert args[1:4] == ['-o', 'loop,ro', '--']
    image = Path(args[4])
    assert image.parent == root / 'outer'
    target = check_pending_mount(args[5])
    if os.environ.get('SYNTHETIC_SPLIT_FAIL_MOUNT') == image.stem:
        report('mount-failed', image.stem)
        raise SystemExit(9)
    shutil.copytree(root.parent / 'media' / image.stem, target, dirs_exist_ok=True)
    report('mount', image.stem, target.name)
elif phase == 'umount':
    assert args[1] == '--'
    target = check_pending_mount(args[2])
    for entry in target.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    report('umount', target.name)
elif phase == 'prerequisites':
    assert all(option not in os.environ for option in
               ('DEEP32_INVENTORY_TEST_ROOT', 'DEEP32_INVENTORY_TEST_UNAME_S', 'DEEP32_INVENTORY_TEST_UNAME_M'))
    assert args[args.index('--accept-plan') + 1] == 'a' * 64
    selected_plan = Path(args[args.index('--forge-plan') + 1])
    assert selected_plan.parent.parent == root / 'tmp'
    assert json.loads(selected_plan.read_bytes())['digest'] == 'a' * 64
    passes = sum(json.loads(line)[0] == 'prerequisites'
                 for line in records.read_text(encoding='ascii').splitlines())
    report('prerequisites', args[1:])
    if os.environ.get('SYNTHETIC_SPLIT_FAIL_GATE') == str(passes + 1):
        raise SystemExit(10)
elif phase == 'stage':
    assert args[args.index('--accept-plan') + 1] == 'a' * 64
    Path(args[args.index('--directory') + 1], 'settings.json').write_bytes(b'{}')
    report('stage')
elif phase == 'run-loader':
    assert args[1:3] == ['--', 'bash']
    loader = Path(args[3])
    target = check_pending_mount(loader.parent.parent)
    assert loader == target / 'forms/load-docker.sh'
    assert not (root.parent / 'media/embedder-turing/forms').exists()
    assert Path(args[4]) == root / 'cache/joined/payload'
    assert Path(args[args.index('--settings') + 1]).read_bytes() == b'{}'
    assert Path(args[args.index('--data-root') + 1]) == root / 'install-data'
    assert args[args.index('--docker-role') + 1] == 'forge'
    assert args[args.index('--platform') + 1] == 'linux'
    report('loader', target.name)
    raise SystemExit(int(os.environ.get('SYNTHETIC_SPLIT_LOADER_STATUS', '0')))
else:
    raise AssertionError(phase)
'''


def msys(path):
    value = Path(path).as_posix()
    if len(value) > 2 and value[1:3] == ':/':
        return '/' + value[0].lower() + value[2:]
    return value


def run_bash_fixture(root, *arguments, confirm_tailnet=True, **overrides):
    tools = root / 'repo/src/release-tools'
    shims = root / 'shim'
    temp = root / 'tmp'
    for directory in (tools, shims, temp, root / 'outer'):
        directory.mkdir(parents=True)
    for name in ('deep32-get.sh', 'deep32_download.py', 'deep32_get_manifest.py', 'contract_validate.py'):
        shutil.copyfile(TOOLS / name, tools / name)
    for name in ('deep32_get_whole.py', 'deep32_settings.py'):
        put(tools / name, BASH_FIXTURE.encode('ascii'))
    driver = root / 'fixture_driver.py'
    put(driver, BASH_FIXTURE.encode('ascii'))
    inventory_script = root / 'repo/install/deep32-inventory.sh'
    put(inventory_script, b'python3 -IB "$SYNTHETIC_SPLIT_DRIVER" prerequisites "$@"\n')
    python_shim = shims / 'python3'
    put(python_shim, f'#!/bin/sh\nexec "{msys(sys.executable)}" "$@"\n'.encode('ascii'))
    for command in ('mount', 'umount'):
        body = f'#!/bin/sh\npython3 -IB "$SYNTHETIC_SPLIT_DRIVER" {command} "$@"\n'
        put(shims / command, body.encode('ascii'))
    put(shims / 'curl', b'#!/bin/sh\nexit 97\n')
    for file in shims.iterdir():
        file.chmod(0o700)
    expected = []
    for component in COMPONENTS:
        raw = f'synthetic verified outer image for {component}\n'.encode('ascii')
        put(root / 'outer' / (component + '.iso'), raw)
        expected.append([component, str(len(raw)), sha(raw)])
    env = dict(os.environ, SYNTHETIC_SPLIT_ROOT=str(root),
               DEEP32_INVENTORY_TEST_ROOT=msys(root / 'outer'),
               DEEP32_INVENTORY_TEST_UNAME_S='Linux', DEEP32_INVENTORY_TEST_UNAME_M='amd64',
               SYNTHETIC_SPLIT_DRIVER=msys(driver), SYNTHETIC_SPLIT_SHIMS=msys(shims),
               TEMP=str(temp), TMP=str(temp), TMPDIR=msys(temp), **overrides)
    startup = r'''
export PATH="$SYNTHETIC_SPLIT_SHIMS:$PATH"
for program in mount umount curl python3; do
  test "$(command -v "$program")" = "$SYNTHETIC_SPLIT_SHIMS/$program" || exit 97
done
id() { test "$1" = "-u" || return 97; printf '0\n'; }
source "$1" "${@:2}"
'''
    command = [str(BASH), '--noprofile', '--norc', '-c', startup, '--', msys(tools / 'deep32-get.sh'),
               'disc3', '--install', '--journey', 'J2', '--gpu-class', GPU_CLASS, '--ingestion',
               '--data-root', msys(root / 'install-data'), '--from', msys(root / 'outer'),
               '--non-interactive', *(('--confirm-tailnet', 'synthetic-tailnet.ts.net') if confirm_tailnet else ()),
               *arguments]
    completed = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=90)
    rows = [json.loads(line) for line in (root / 'events.jsonl').read_text(encoding='ascii').splitlines()]
    return completed, rows, expected


class JoinTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='disc3-join-', dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, kit, self.images, self.pins = producer_inputs(self.root)
        self.media = {}
        for component in COMPONENTS:
            kind = 'embedder' if component.startswith('embedder-') else component
            card = GPU_CLASS if kind == 'embedder' else None
            allowlist = self.root / (component + '.allowlist.json')
            allowlist.write_bytes(canonical(producer.selected_allowlist(self.source, component)))
            stage = self.root / 'stages' / component
            # The tiny fixture models a sealed payload. Only the separate
            # acquisition/package validation is stubbed; selection, image
            # bindings, model refs, receipts and media are production code.
            with mock.patch.object(producer.runtime, 'validate_source'):
                producer.runtime.stage_runtime(self.source, kit, allowlist, kind, stage, EPOCH, card)
            media = self.root / 'media' / component
            producer.prepare_media(stage, kit, component, media, EPOCH)
            self.media[component] = media
        self.joiner = load_module('synthetic_release_join', TOOLS / 'deep32_download.py')
        self.join = self.joiner.join
        self.output = self.root / 'joined'
        self.data_root = self.root / 'install-data'

    def run_join(self, *, output=None, embedder=None, ingestion=None, data_root=None):
        return self.join(self.media['core'], embedder or self.media['embedder-turing'],
                         ingestion or self.media['ingestion'], GPU_CLASS, output or self.output,
                         data_root or self.data_root)

    def reseal(self, component):
        media = self.media[component] / 'deep32'
        rows = inventory(files(media / 'payload'))
        (media / 'PAYLOAD.json').write_bytes(canonical(rows))
        (media / 'PAYLOAD.SHA256').write_bytes(sums(rows))
        receipt = json.loads((media / 'RUNTIME.json').read_bytes())
        receipt['payload_sha256'] = sha(canonical(rows))
        (media / 'RUNTIME.json').write_bytes(canonical(receipt))

    def expected(self):
        contents = files(self.source / 'payload')
        # The common source already contains the complete selected roles in
        # role order. Construct the producer-only cache pointers independently.
        contents['images/index.json'] = canonical(self.images)
        for role, model in self.pins['models'].items():
            base = f'models/{role}/models--synthetic--{role}/refs/'
            revision = model['revision']
            for key in (revision, 'main'):
                contents[base + key] = revision.encode('ascii')
        rows = inventory(contents)
        manifest = canonical(rows)
        inputs = []
        for component in COMPONENTS:
            raw = (self.media[component] / 'deep32/RUNTIME.json').read_bytes()
            inputs.append({'component': component, 'receipt_sha256': sha(raw), 'receipt': json.loads(raw)})
        receipt = {'format': 'deep32-joined-runtime/1', 'gpu_class': GPU_CLASS,
                   'source_date_epoch': EPOCH, 'pins_sha256': sha((self.source / 'pins.json').read_bytes()),
                   'source_payload_sha256': sha((self.source / 'PAYLOAD.json').read_bytes()),
                   'roles': list(ROLES), 'payload_sha256': sha(manifest), 'components': inputs}
        return {**{'payload/' + name: raw for name, raw in contents.items()},
                'PAYLOAD.json': manifest, 'PAYLOAD.SHA256': sums(rows), 'RUNTIME.json': canonical(receipt)}

    def test_join_without_ingestion_has_only_core_and_embedder_roles(self):
        # The Linux getter installs the ingestion add-on only with --ingestion (DR-64).
        self.assertEqual(self.output, self.join(self.media['core'], self.media['embedder-turing'], None, GPU_CLASS,
                                                self.output, self.data_root))
        runtime = json.loads((self.output / 'RUNTIME.json').read_bytes())
        self.assertEqual(runtime['roles'], ['door', 'reranker', 'tei-turing'])
        self.assertEqual([row['component'] for row in runtime['components']], ['core', 'embedder-turing'])
        provision.verified_payload(self.output / 'payload')

    def test_exact_producer_union_and_immutable_reuse(self):
        old_media = files(self.root / 'media')
        self.assertEqual(self.output, self.run_join())
        expected = self.expected()
        self.assertEqual(expected, files(self.output))
        self.assertEqual(sha(expected['PAYLOAD.json']) + ':' + sha(expected['PAYLOAD.SHA256']),
                         provision.verified_payload(self.output / 'payload'))
        verifier = object.__new__(forge.Lifecycle)
        self.assertEqual(set(ROLES), verifier.image_roles(GPU_CLASS, self.pins))
        same = self.root / 'second-join'
        self.assertEqual(same, self.run_join(output=same))
        self.assertEqual(expected, files(same))
        timestamps = {name: (self.output / name).stat().st_mtime_ns for name in expected}
        self.assertEqual(self.output, self.run_join())
        self.assertEqual(timestamps, {name: (self.output / name).stat().st_mtime_ns for name in expected})
        self.assertEqual(old_media, files(self.root / 'media'))
        self.assertFalse(self.data_root.exists())

    def test_conflicting_byte_of_separately_verified_media_is_refused(self):
        member = self.media['ingestion'] / 'deep32/payload/licences/AGPL-3.0.txt'
        member.write_bytes(b'other synthetic fixture licence text\n')
        self.reseal('ingestion')
        provision.verified_payload(self.media['ingestion'] / 'deep32/payload')
        with self.assertRaises(ValueError):
            self.run_join()
        self.assertFalse(self.output.exists())

    def test_missing_and_misplaced_components_are_refused(self):
        for name, kwargs in (
            ('missing embedder', {'embedder': self.root / 'missing'}),
            ('missing ingestion', {'ingestion': self.root / 'missing'}),
            ('core in embedder position', {'embedder': self.media['core']}),
        ):
            with self.subTest(name=name), self.assertRaises((OSError, ValueError)):
                self.run_join(**kwargs)
            self.assertFalse(self.output.exists())

    def test_image_index_cannot_point_at_another_roles_archive(self):
        index = self.media['ingestion'] / 'deep32/payload/images/index.json'
        rows = json.loads(index.read_bytes())
        rows[0]['path'] = rows[1]['path']
        rows[0]['sha256'] = rows[1]['sha256']
        index.write_bytes(canonical(rows))
        self.reseal('ingestion')
        with self.assertRaises(ValueError):
            self.run_join()
        self.assertFalse(self.output.exists())

    def test_cached_join_is_never_replaced_on_mismatch(self):
        self.run_join()
        member = self.output / 'payload/images/door.tar'
        member.write_bytes(b'modified synthetic cache entry\n')
        before = files(self.output)
        with self.assertRaises(ValueError):
            self.run_join()
        self.assertEqual(before, files(self.output))

    def test_insufficient_cache_bytes_refuses_before_copy(self):
        expected_size = sum(map(len, self.expected().values()))
        before = set(self.root.iterdir())
        with mock.patch.object(shutil, 'disk_usage', return_value=mock.Mock(free=0)):
            with self.assertRaisesRegex(ValueError, rf'needs {expected_size} bytes'):
                self.run_join()
        self.assertEqual(before, set(self.root.iterdir()))

    def test_install_root_must_not_contain_join(self):
        with self.assertRaises(ValueError):
            self.run_join(data_root=self.root)
        self.assertFalse(self.output.exists())

    def raw_parts(self, *, modify=None):
        files_by_iso = {}
        for name in COMPONENTS:
            iso = self.root / (name + '.iso')
            iso.write_bytes(b'synthetic ISO path fixture')
            contents = files(self.media[name])
            files_by_iso[iso] = contents
        workspace = self.root / 'raw-window'
        workspace.mkdir()

        @contextlib.contextmanager
        def raw_iso(path, include=()):
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode='w', format=tarfile.PAX_FORMAT) as archive:
                for name, raw in sorted(files_by_iso[path].items()):
                    if include and name not in include:
                        continue
                    member = tarfile.TarInfo(name)
                    if modify and not include:
                        raw = modify(path.name, member, raw)
                    member.size = len(raw)
                    archive.addfile(member, io.BytesIO(raw))
            data.seek(0)
            with tarfile.open(fileobj=data, mode='r|') as archive:
                yield archive

        return workspace, lambda: self.joiner.join_windows_iso(
            *files_by_iso, GPU_CLASS, self.output, self.data_root, workspace, None, open_iso=raw_iso)

    def test_raw_windows_stream_joins_directly_and_verifies_core_kit(self):
        workspace, join = self.raw_parts()
        self.assertEqual(join(), self.output)
        self.assertEqual(files(self.output), self.expected())
        kit = workspace / 'kit'
        self.assertEqual((kit / 'forms/provision.py').read_bytes(),
                         (self.media['core'] / 'forms/provision.py').read_bytes())
        # The Apache-2.0 terms and MinerU's license travel with the extracted runtime kit.
        for name in ('LICENSE', 'NOTICE', 'licenses/MinerU-4.0.7-LICENSE.md'):
            self.assertEqual((kit / name).read_bytes(), (self.media['core'] / name).read_bytes())
        self.assertFalse((kit / 'forms/forge-docker.tar').exists())
        self.assertFalse((workspace / 'joined').exists())
        self.assertFalse(self.data_root.exists())
        # A new private kit on each invocation, with existing validated cache.
        shutil.rmtree(kit)
        self.assertEqual(join(), self.output)

    def test_raw_windows_link_traversal_and_kit_hash_failure_remove_staging(self):
        for change, error in (('link', 'linked'), ('traverse', 'Unsafe'), ('digest', 'hash verification')):
            with self.subTest(change=change):
                def modify(iso, member, raw):
                    if iso == 'core.iso' and member.name == 'forms/provision.py':
                        if change == 'link':
                            member.type, member.linkname, raw = tarfile.LNKTYPE, 'pins.json', b''
                        elif change == 'traverse':
                            member.name = '../forbidden.py'
                        else:
                            raw = b'altered synthetic core'
                    return raw

                workspace, join = self.raw_parts(modify=modify)
                with self.assertRaisesRegex(ValueError, error):
                    join()
                self.assertFalse(self.output.exists())
                self.assertEqual(list(workspace.iterdir()), [])
                workspace.rmdir()
        self.assertFalse((self.root / 'forbidden.py').exists())

    def test_raw_windows_storage_checks_bounded_kit_before_writes(self):
        workspace, join = self.raw_parts()
        with mock.patch.object(shutil, 'disk_usage', return_value=mock.Mock(free=self.joiner.RAW_KIT_LIMIT - 1)):
            with self.assertRaisesRegex(ValueError, 'Insufficient local storage'):
                join()
        self.assertEqual(list(workspace.iterdir()), [])
        self.assertFalse(self.output.exists())

    @unittest.skipUnless(os.name == 'nt', 'requires the native Windows raw-mode CLI')
    def test_raw_windows_hard_link_refusal_has_no_traceback(self):
        workspace, _join = self.raw_parts()
        core = self.root / 'core.iso'
        os.link(core, self.root / 'alias.iso')
        command = [sys.executable, '-I', '-B', str(TOOLS / 'deep32_download.py'),
                   '--core', str(core), '--embedder', str(self.root / 'embedder-turing.iso'),
                   '--ingestion', str(self.root / 'ingestion.iso'), '--gpu-class', GPU_CLASS,
                   '--output', str(self.output), '--data-root', str(self.data_root),
                   '--windows-workspace', str(workspace), '--bsdtar', 'unused']
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertIn('unexpected hard links', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(list(workspace.iterdir()), [])

    @unittest.skipUnless(BASH.is_file(), 'requires the named Bash executable')
    def test_bash_split_missing_choices_are_refused_before_any_media_is_read(self):
        result, rows, _ = run_bash_fixture(self.root / 'missing-choices', '--accept-plan', 'a' * 64,
                                           confirm_tailnet=False)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertEqual([['settings-refused']], rows)   # no verified-media: the whole helper was never called
        self.assertIn('missing non-interactive choices', result.stderr)

    def test_bash_split_real_join_consent_and_mount_cleanup(self):
        for case, expected_code, args, overrides, events in (
            ('decline', 1, (), {}, ('verified-media', 'plan')),
            ('accept', 0, ('--accept-plan', 'a' * 64), {},
             ('verified-media', 'plan', 'prerequisites', 'consent', 'mount', 'mount', 'mount',
              'prerequisites', 'stage', 'loader', 'umount', 'umount', 'umount')),
            ('failed-mount', 9, ('--accept-plan', 'a' * 64), {'SYNTHETIC_SPLIT_FAIL_MOUNT': 'ingestion'},
             ('verified-media', 'plan', 'prerequisites', 'consent', 'mount', 'mount', 'mount-failed',
              'umount', 'umount')),
            ('changed-ingestion', 1, ('--accept-plan', 'a' * 64), {'SYNTHETIC_SPLIT_CHANGE_INGESTION': 'yes'},
             ('verified-media', 'plan', 'prerequisites', 'consent', 'mount', 'mount', 'umount', 'umount')),
            ('failed-loader', 9, ('--accept-plan', 'a' * 64), {'SYNTHETIC_SPLIT_LOADER_STATUS': '9'},
             ('verified-media', 'plan', 'prerequisites', 'consent', 'mount', 'mount', 'mount',
              'prerequisites', 'stage', 'loader', 'umount', 'umount', 'umount')),
            ('first-preflight-refusal', 10, ('--accept-plan', 'a' * 64), {'SYNTHETIC_SPLIT_FAIL_GATE': '1'},
             ('verified-media', 'plan', 'prerequisites')),
            ('final-preflight-refusal', 10, ('--accept-plan', 'a' * 64), {'SYNTHETIC_SPLIT_FAIL_GATE': '2'},
             ('verified-media', 'plan', 'prerequisites', 'consent', 'mount', 'mount', 'mount',
              'prerequisites', 'umount', 'umount', 'umount')),
        ):
            with self.subTest(case=case):
                root = self.root / case
                result, rows, expected_plan = run_bash_fixture(root, *args, **overrides)
                self.assertEqual(expected_code, result.returncode, result.stdout + result.stderr)
                self.assertEqual(events, tuple(row[0] for row in rows))
                mounted = [row[2] for row in rows if row[0] == 'mount']
                unmounted = [row[1] for row in rows if row[0] == 'umount']
                self.assertEqual(list(reversed(mounted)), unmounted)
                for row in rows:
                    if row[0] == 'loader':
                        self.assertEqual(mounted[0], row[1])
                self.assertEqual(['plan', expected_plan], rows[1])
                self.assertEqual([], list((root / 'tmp').iterdir()))
                joined = root / 'cache/joined'
                if len(mounted) == 3:
                    self.assertEqual(self.expected(), files(joined))
                    provision.verified_payload(joined / 'payload')
                else:
                    self.assertFalse(joined.exists())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scratch-root', type=Path, required=True)
    options, rest = parser.parse_known_args()
    SCRATCH = options.scratch_root.resolve()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0], *rest], verbosity=2)
