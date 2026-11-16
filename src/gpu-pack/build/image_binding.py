#!/usr/bin/env python3
"""Measure Docker-save images and bind classic and containerd identity forms.

No archive is extracted. OCI descriptors are measured before their contents are
used; an OCI index may omit other platforms, as Docker's platform-specific save
does. A pinned classic archive additionally needs the caller's repository-digest
inspection evidence. The caller must validate that evidence before passing its
immutable inspected Id here.
"""
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import zlib

ID = re.compile(r'sha256:[0-9a-f]{64}\Z')
INDEX_TYPES = {'application/vnd.oci.image.index.v1+json',
               'application/vnd.docker.distribution.manifest.list.v2+json'}
MANIFEST_TYPES = {'application/vnd.oci.image.manifest.v1+json',
                  'application/vnd.docker.distribution.manifest.v2+json'}
CONFIG_TYPES = {'application/vnd.oci.image.config.v1+json',
                'application/vnd.docker.container.image.v1+json'}
MAX_JSON = 8 * 1024 * 1024
MAX_GRAPH = 4096
MAX_DEPTH = 32


def _id(value, label):
    if not isinstance(value, str) or ID.fullmatch(value) is None:
        raise ValueError('invalid ' + label + ' digest')
    return value


def _regular(path):
    path = Path(path)
    if (path.is_symlink() or not path.is_file()
            or any(parent.is_symlink() for parent in path.parents)):
        raise ValueError('regular image archive required')
    if path.suffix == '.env' or path.name == 'permissions-evidence':
        raise ValueError('excluded image archive name')
    return path


def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise ValueError('duplicate image metadata key')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('invalid image metadata JSON constant')


def _json(data):
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid image metadata JSON') from exc


def _platform(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or not isinstance(value.get('os'), str)
            or not isinstance(value.get('architecture'), str)):
        raise ValueError('invalid OCI descriptor platform')
    return (value['os'], value['architecture'])


class _Archive:
    def __init__(self, archive):
        self.archive = archive
        self.members = {}
        self.measured = {}
        self.graph_count = 0
        locations = set()
        rows = archive.getmembers()
        if len(rows) > 100000:
            raise ValueError('too many image archive members')
        for member in rows:
            name = member.name
            pure = PurePosixPath(name)
            parts = name.rstrip('/').split('/')
            if (not name or pure.is_absolute() or '\\' in name
                    or any(part in ('', '.', '..') for part in parts)
                    or name in self.members
                    or not (member.isfile() or member.isdir())
                    or (name.endswith('/') and not member.isdir())):
                raise ValueError('unsafe image archive member')
            # A directory and a file cannot name the same archive location.
            key = name.rstrip('/')
            if key in locations:
                raise ValueError('duplicate image archive member')
            locations.add(key)
            self.members[name] = member

    def member(self, name, required=True):
        row = self.members.get(name)
        if row is None and not required:
            return None
        if row is None or not row.isfile():
            raise ValueError('image archive member missing or not regular')
        return row

    def data(self, name, limit=MAX_JSON):
        row = self.member(name)
        if row.size > limit:
            raise ValueError('image metadata exceeds size limit')
        with self.archive.extractfile(row) as stream:
            return stream.read()

    def measure(self, name):
        if name not in self.measured:
            row = self.member(name)
            result = hashlib.sha256()
            with self.archive.extractfile(row) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    result.update(chunk)
            self.measured[name] = ('sha256:' + result.hexdigest(), row.size)
        return self.measured[name]

    def descriptor(self, desc, required=True):
        if not isinstance(desc, dict):
            raise ValueError('invalid OCI descriptor')
        digest = _id(desc.get('digest'), 'OCI descriptor')
        size = desc.get('size')
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError('invalid OCI descriptor size')
        if not isinstance(desc.get('mediaType'), str):
            raise ValueError('invalid OCI descriptor media type')
        name = 'blobs/sha256/' + digest.split(':')[1]
        if self.member(name, required=required) is None:
            return None
        if self.measure(name) != (digest, size):
            raise ValueError('OCI descriptor digest or size differs')
        return name

    def graph(self, desc, inherited=None, ancestry=()):
        self.graph_count += 1
        if self.graph_count > MAX_GRAPH or len(ancestry) >= MAX_DEPTH:
            raise ValueError('OCI descriptor graph exceeds limit')
        # A build attestation (BuildKit provenance/SBOM, platform unknown/unknown) describes how an image was built;
        # it is not a runnable image and never a binding candidate. docker save keeps the amd64 image's own
        # attestation beside it (lead fix 2026-11-15: real python:3.12-slim-trixie and TEI archives carry them).
        if isinstance(desc, dict) and (desc.get('annotations') or {}).get('vnd.docker.reference.type') == 'attestation-manifest':
            return []
        platform = _platform(desc.get('platform')) if isinstance(desc, dict) else None
        if inherited is not None and platform is not None and platform != inherited:
            raise ValueError('OCI descriptor platform differs from parent')
        platform = platform or inherited
        # Only an explicitly different platform may be absent from a sparse save.
        required = platform in (None, ('linux', 'amd64'))
        name = self.descriptor(desc, required=required)
        if name is None:
            return []
        digest = desc['digest']
        if digest in ancestry:
            raise ValueError('cyclic OCI descriptor graph')
        value = _json(self.data(name))
        if not isinstance(value, dict) or value.get('schemaVersion') != 2:
            raise ValueError('invalid OCI image metadata')
        media_type = desc['mediaType']
        if value.get('mediaType', media_type) != media_type:
            raise ValueError('OCI metadata media type differs')
        if media_type in INDEX_TYPES:
            rows = value.get('manifests')
            if not isinstance(rows, list) or not rows:
                raise ValueError('OCI index has no manifests')
            found = []
            for row in rows:
                found.extend(self.graph(row, platform, ancestry + (digest,)))
            return found
        if media_type not in MANIFEST_TYPES:
            raise ValueError('unsupported OCI image manifest media type')
        config_desc = value.get('config')
        config_name = self.descriptor(config_desc, required=required)
        if config_desc['mediaType'] not in CONFIG_TYPES:
            raise ValueError('invalid OCI image config media type')
        config = None if config_name is None else _json(self.data(config_name))
        if config is not None and not isinstance(config, dict):
            raise ValueError('invalid image configuration')
        actual = None
        if config is not None:
            os_name, architecture = config.get('os'), config.get('architecture')
            if os_name is not None and not isinstance(os_name, str):
                raise ValueError('invalid image configuration platform')
            if architecture is not None and not isinstance(architecture, str):
                raise ValueError('invalid image configuration platform')
            if platform is not None:
                if ((os_name is not None and os_name != platform[0])
                        or (architecture is not None and architecture != platform[1])):
                    raise ValueError('image config and OCI platform differ')
            if os_name is not None and architecture is not None:
                actual = (os_name, architecture)
        selected = (platform or actual) == ('linux', 'amd64')
        if selected and actual != ('linux', 'amd64'):
            raise ValueError('selected image config must establish linux/amd64')
        layers = value.get('layers')
        if not isinstance(layers, list):
            raise ValueError('invalid OCI manifest layers')
        names = []
        for layer in layers:
            layer_name = self.descriptor(layer, required=selected)
            names.append('blobs/sha256/' + layer['digest'].split(':')[1])
            if layer_name is not None and not isinstance(layer.get('mediaType'), str):
                raise ValueError('invalid OCI layer media type')
        return [(digest, config_desc['digest'], names, layers)] if selected else []

    def diff_ids(self, config, names, descriptors=None):
        rootfs = config.get('rootfs')
        if rootfs is None:
            return
        if (not isinstance(rootfs, dict) or rootfs.get('type') != 'layers'
                or not isinstance(rootfs.get('diff_ids'), list)
                or len(rootfs['diff_ids']) != len(names)):
            raise ValueError('invalid image rootfs layer binding')
        cache = {}
        for position, (name, expected) in enumerate(zip(names, rootfs['diff_ids'])):
            _id(expected, 'rootfs layer')
            media_type = '' if descriptors is None else descriptors[position]['mediaType']
            # Python 3.12 has no stdlib zstd decoder. The OCI digest and size are
            # still checked above; leave this optional extra diff-id check out.
            if 'zstd' in media_type:
                continue
            key = (name, media_type)
            if key not in cache:
                result = hashlib.sha256()
                with self.archive.extractfile(self.member(name)) as stream:
                    compressed = ('gzip' in media_type)
                    if descriptors is None:
                        compressed = stream.read(2) == b'\x1f\x8b'
                        stream.seek(0)
                    reader = gzip.GzipFile(fileobj=stream) if compressed else stream
                    try:
                        for chunk in iter(lambda: reader.read(1024 * 1024), b''):
                            result.update(chunk)
                    except (OSError, EOFError, zlib.error) as exc:
                        raise ValueError('invalid compressed image layer') from exc
                    finally:
                        if reader is not stream:
                            reader.close()
                cache[key] = 'sha256:' + result.hexdigest()
            if cache[key] != expected:
                raise ValueError('image rootfs diff_id differs')


def archive_binding(path, source_digest=None, inspected_id=None):
    """Return measured immutable image identities, refusing ambiguous archives.

    image_id remains the config digest for compatibility. index_digest records
    the immutable repository pin even for a classic save, where only validated
    inspect evidence can establish that pin. An unpinned local classic save has
    no index_digest. OCI archives always record their one root descriptor.
    """
    if source_digest is not None:
        _id(source_digest, 'source')
    if inspected_id is not None:
        _id(inspected_id, 'inspected image')
    with tarfile.open(_regular(path), mode='r:') as tar:
        archive = _Archive(tar)
        records = _json(archive.data('manifest.json', 1024 * 1024))
        if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
            raise ValueError('image archive must contain exactly one image')
        record = records[0]
        config_name, layers = record.get('Config'), record.get('Layers')
        if not isinstance(config_name, str) or not isinstance(layers, list):
            raise ValueError('invalid Docker save manifest')
        config_data = archive.data(config_name)
        config_digest = 'sha256:' + hashlib.sha256(config_data).hexdigest()
        if config_name not in (config_digest.split(':')[1] + '.json',
                               'blobs/sha256/' + config_digest.split(':')[1]):
            raise ValueError('image config name does not match its measured digest')
        config = _json(config_data)
        if not isinstance(config, dict):
            raise ValueError('invalid image configuration')
        if (config.get('os', 'linux') != 'linux'
                or config.get('architecture', 'amd64') != 'amd64'):
            raise ValueError('image configuration must be linux/amd64')
        for layer in layers:
            if not isinstance(layer, str):
                raise ValueError('invalid Docker save layer name')
            archive.member(layer)
        index_digest, manifest_digest, descriptors = source_digest, None, None
        archive_format = 'classic'
        if 'index.json' in archive.members:
            root = _json(archive.data('index.json'))
            if (not isinstance(root, dict) or root.get('schemaVersion') != 2
                    or root.get('mediaType') not in INDEX_TYPES
                    or not isinstance(root.get('manifests'), list)
                    or len(root['manifests']) != 1):
                raise ValueError('OCI archive must name exactly one root image')
            desc = root['manifests'][0]
            if not isinstance(desc, dict):
                raise ValueError('invalid OCI root descriptor')
            index_digest = _id(desc.get('digest'), 'OCI root')
            if source_digest is not None and index_digest != source_digest:
                raise ValueError('OCI archive root differs from pinned source digest')
            selected = archive.graph(desc)
            if len(selected) != 1:
                raise ValueError('OCI archive must resolve exactly one linux/amd64 image')
            manifest_digest, selected_config, selected_layers, descriptors = selected[0]
            if selected_config != config_digest:
                raise ValueError('Docker save config differs from OCI selected config')
            if selected_layers != layers:
                raise ValueError('Docker save layers differ from OCI selected layers')
            archive_format = 'oci'
        elif source_digest is not None and inspected_id is None:
            raise ValueError('pinned classic archive requires measured inspect identity')
        permitted = {config_digest, index_digest} if archive_format == 'oci' else {config_digest}
        if inspected_id is not None and inspected_id not in permitted:
            raise ValueError('archive and measured image id differ: inspect identity')
        archive.diff_ids(config, layers, descriptors)
        return {'image_id': config_digest, 'config_digest': config_digest,
                'index_digest': index_digest, 'manifest_digest': manifest_digest,
                'archive_format': archive_format}


def _entry(entry):
    if not isinstance(entry, dict):
        raise ValueError('image binding record required')
    required = {'image_id', 'config_digest', 'index_digest', 'manifest_digest', 'archive_format'}
    if not required <= set(entry):
        raise ValueError('complete image binding record required')
    config = _id(entry.get('config_digest'), 'recorded config')
    if entry.get('image_id') != config:
        raise ValueError('recorded image id differs from config digest')
    index = entry.get('index_digest')
    if index is not None:
        _id(index, 'recorded index')
    if entry.get('archive_format') == 'oci':
        _id(index, 'recorded OCI root')
        _id(entry.get('manifest_digest'), 'recorded OCI manifest')
    elif entry.get('archive_format') == 'classic':
        if entry.get('manifest_digest') is not None:
            raise ValueError('classic binding cannot name an OCI manifest')
    else:
        raise ValueError('invalid recorded archive format')
    return config, index


def check_archive(path, entry, source_digest=None):
    """Remeasure an archive against its sealed binding record."""
    config, index = _entry(entry)
    if source_digest is not None and source_digest != index:
        raise ValueError('recorded index differs from pinned source digest')
    measured = archive_binding(path, source_digest=index, inspected_id=config)
    if any(entry.get(key) != value for key, value in measured.items()):
        raise ValueError('image archive differs from recorded binding')
    return measured


def resolve_loaded(entry, docker='docker'):
    """Inspect immutable recorded ids after load and return a verified actual Id.

    No tag is queried. Store implementations may expose the index or the config
    as Id; each accepted record must still establish linux/amd64 independently.
    """
    config, index = _entry(entry)
    candidates = list(dict.fromkeys(value for value in (index, config) if value))
    allowed = set(candidates)
    for candidate in candidates:
        try:
            result = subprocess.run([docker, 'image', 'inspect', candidate], check=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except subprocess.CalledProcessError:
            continue
        value = _json(result.stdout.encode('utf-8'))
        if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
            raise ValueError('single loaded image inspect record required')
        image = value[0]
        if image.get('Os') != 'linux' or image.get('Architecture') != 'amd64':
            raise ValueError('loaded image must be linux/amd64')
        actual = _id(image.get('Id'), 'loaded image')
        if actual not in allowed:
            raise ValueError('loaded image differs from recorded immutable identities')
        return actual
    raise ValueError('loaded image not found by recorded immutable identities')
