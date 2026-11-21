"""Tiny deterministic Docker-save fixtures and a strictly offline Docker stub."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit_support  # noqa: E402,F401  src/gpu-pack and the shared helpers (kit_support.py)

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile

KIT = Path(__file__).resolve().parents[2] / 'src' / 'gpu-pack'


def binding():
    spec = importlib.util.spec_from_file_location('runtime_fixture_binding', KIT / 'build/image_binding.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')


def sha(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def make_image(path, role=None, oci=False, source_digest=None):
    path = Path(path)
    role = role or path.stem
    layer = ('synthetic image layer ' + role + '\n').encode('ascii')
    config = encoded({'os': 'linux', 'architecture': 'amd64', 'role': role,
                      'rootfs': {'type': 'layers', 'diff_ids': [sha(layer)]}})
    config_id = sha(config)
    config_name = config_id.split(':')[1] + '.json'
    layer_name = 'layer/layer.tar'
    files = {config_name: config, layer_name: layer}
    if oci:
        def desc(data, kind):
            return {'digest': sha(data), 'size': len(data), 'mediaType': kind}
        config_name = 'blobs/sha256/' + config_id.split(':')[1]
        layer_name = 'blobs/sha256/' + sha(layer).split(':')[1]
        manifest = encoded({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                            'config': desc(config, 'application/vnd.oci.image.config.v1+json'),
                            'layers': [desc(layer, 'application/vnd.oci.image.layer.v1.tar')]})
        leaf = desc(manifest, 'application/vnd.oci.image.manifest.v1+json')
        leaf['platform'] = {'os': 'linux', 'architecture': 'amd64'}
        root = encoded({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.index.v1+json',
                        'manifests': [leaf]})
        index = encoded({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.index.v1+json',
                         'manifests': [desc(root, 'application/vnd.oci.image.index.v1+json')]})
        files = {config_name: config, layer_name: layer,
                 'blobs/sha256/' + sha(manifest).split(':')[1]: manifest,
                 'blobs/sha256/' + sha(root).split(':')[1]: root, 'index.json': index,
                 'oci-layout': encoded({'imageLayoutVersion': '1.0.0'})}
        if source_digest is not None and source_digest != sha(root):
            raise ValueError('synthetic OCI source digest must match its measured root')
        source_digest = sha(root)
    files['manifest.json'] = encoded([{'Config': config_name, 'RepoTags': [], 'Layers': [layer_name]}])
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, 'w', format=tarfile.USTAR_FORMAT) as archive:
        for name, data in sorted(files.items()):
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            entry.mode = 0o644
            entry.mtime = 0
            archive.addfile(entry, io.BytesIO(data))
    row = dict(role=role, path='images/' + path.name,
               **binding().archive_binding(path, source_digest, config_id),
               sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    if source_digest is not None:
        row['source_digest'] = source_digest
    return row


def inspect_row(entry, index=False):
    return {'Id': entry['index_digest'] if index and entry['index_digest'] else entry['config_digest'],
            'Architecture': 'amd64', 'Os': 'linux'}


class FakeDocker:
    def __init__(self, rows, index=False, present=()):
        self.rows = list(rows)
        self.index = index
        self.present = set(present)

    def run(self, argv, **kwargs):
        if argv[:3] == ['docker', 'image', 'load']:
            name = Path(argv[-1]).name
            matches = [row for row in self.rows if Path(row['path']).name == name]
            if len(matches) != 1:
                raise AssertionError('unknown synthetic archive: ' + name)
            self.present.add(matches[0]['image_id'])
            return subprocess.CompletedProcess(argv, 0, '', '')
        if argv[:3] == ['docker', 'image', 'inspect'] and len(argv) == 4:
            matches = [row for row in self.rows if argv[-1] in {row['config_digest'], row['index_digest']}
                       and row['image_id'] in self.present]
            if not matches:
                if kwargs.get('check'):
                    raise subprocess.CalledProcessError(1, argv, '', 'synthetic missing image')
                return subprocess.CompletedProcess(argv, 1, '', 'synthetic missing image')
            return subprocess.CompletedProcess(argv, 0, json.dumps([inspect_row(matches[0], self.index)]), '')
        raise AssertionError('unexpected synthetic Docker command: ' + repr(argv))

    def check_output(self, argv, **kwargs):
        return self.run(argv, check=True, **kwargs).stdout
