"""Closed, non-secret Compose configuration for the co-located Linux role.

The lifecycle verifies and loads the image archives before calling this pure
renderer. No service has a build definition or permission to pull an image.
"""
import copy
import json
from pathlib import Path, PurePosixPath
import re


IMAGE_KEYS = ('TEI_IMAGE', 'RERANKER_IMAGE', 'DOOR_IMAGE', 'QDRANT_IMAGE',
              'FULL_IMAGE', 'MODEL_BRIDGE_IMAGE')
SERVICES = ('embeddings', 'reranker', 'door', 'qdrant', 'index-health',
            'model-bridge', 'engine', 'gateway')


def absolute_path(value, allow_media_root=False):
    text = str(value)
    windows = re.match(r'^[A-Za-z]:[\\/]', text) is not None
    if windows:
        text = text[0].upper() + text[1:].replace('\\', '/')
        members = [] if allow_media_root and len(text) == 3 else text[3:].split('/')
    else:
        members = text[1:].split('/')
    if (not (windows or text.startswith('/')) or text.startswith('//')
            or any(ord(x) < 32 for x in text) or '$' in text
            or '\\' in text or ':' in text[2 if windows else 0:]
            or any(x in ('', '.', '..') for x in members)
            or windows and any(x.endswith((' ', '.')) or re.search(r'[<>"|?*]', x)
                               or re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', x)
                               for x in members)):
        raise ValueError('Choose a canonical absolute local directory without interpolation or parent traversal.')
    return text if windows and len(text) == 3 else str(PurePosixPath(text))


def prepare_config(kit, payload, data_root, content, images, config):
    """Return a Compose object without writing files or invoking a platform tool.

config carries tei_revision, tailscale_dns_name and model_bridge; the latter is
the non-secret signed attestation configuration written by the lifecycle to
data_root/config/model-bridge.json. C7 settings never hold credentials.
"""
    paths = {name: absolute_path(value, allow_media_root=name in ('KIT', 'CONTENT')) for name, value in
             (('KIT', kit), ('PAYLOAD', payload), ('DATA_ROOT', data_root), ('CONTENT', content))}
    windows = re.match(r'^[A-Z]:/', paths['DATA_ROOT']) is not None
    if any((re.match(r'^[A-Z]:/', value) is not None) != windows for value in paths.values()):
        raise ValueError('Use paths belonging to the same Docker host.')
    # Windows filesystems compare drive paths without case. Keep their spelling
    # in Compose, while applying the same separation rules to either host.
    comparable = {key: value.casefold() if windows else value for key, value in paths.items()}
    root = PurePosixPath(comparable['DATA_ROOT'])
    for name in ('KIT', 'PAYLOAD', 'CONTENT'):
        other = PurePosixPath(comparable[name])
        if (root == other or other in root.parents or root in other.parents and not (name == 'KIT' and re.fullmatch(re.escape(str(root)) + r'/releases/[0-9a-f]{64}/kit', str(other)))):
            raise ValueError('Choose a data directory separate from the kit, payload and content medium.')
    for key in IMAGE_KEYS:
        if not isinstance(images.get(key), str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', images[key]):
            raise ValueError('Load and verify the pinned ' + key + ' archive before starting the full role.')
    revision = config.get('tei_revision') or config.get('models', {}).get('tei', {}).get('revision', '')
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Rebuild the pack with the exact signed embedding revision.')
    dns = config.get('tailscale_dns_name', '').rstrip('.')
    if not re.fullmatch(r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*\.ts\.net', dns):
        raise ValueError('Read the signed-in host Tailscale DNS name before configuring the gateway.')
    dtype = config.get('reranker_dtype') or config.get('runtime', {}).get('reranker_dtype', 'float16')
    if dtype not in ('float16', 'float32') or config.get('reranker_device', 'cuda') != 'cuda':
        raise ValueError('Use the pinned CUDA reranker settings for the full GPU role.')
    bridge = config.get('model_bridge') or config.get('full', {}).get('model_bridge')
    if not isinstance(bridge, dict) or bridge.get('schema') != 'deep32.model-bridge.v1':
        raise ValueError('Write the signed model attestation configuration before starting the full role.')
    for name in bridge:
        if name != 'schema' and not re.fullmatch(r'GPU_EXT_(?:EMBED_MODEL_ID|RERANK_MODEL_ID|EMBED_REVISION|RERANK_REVISION|EMBED_PROBE_INPUT|EMBED_PROBE_SHA256|EMBED_PROBE_MODEL|RERANK_PROBE|RECHECK_SECONDS|CONNECT_TIMEOUT|READ_TIMEOUT|PROBE_ALWAYS)', name):
            raise ValueError('Keep transport settings and credentials out of the model attestation configuration.')
    if any(not isinstance(value, str) for value in bridge.values()):
        raise ValueError('Use string values in the signed model attestation configuration.')
    if (not bridge.get('GPU_EXT_EMBED_MODEL_ID') or not bridge.get('GPU_EXT_RERANK_MODEL_ID')
            or not bridge.get('GPU_EXT_EMBED_REVISION') and not (
                bridge.get('GPU_EXT_EMBED_PROBE_INPUT') and re.fullmatch(r'[0-9a-f]{64}', bridge.get('GPU_EXT_EMBED_PROBE_SHA256', '')))
            or not bridge.get('GPU_EXT_RERANK_REVISION')):
        raise ValueError('Seal both model identities, an embedding revision or golden probe, and the reranker revision before starting.')
    for name in ('GPU_EXT_EMBED_REVISION', 'GPU_EXT_RERANK_REVISION'):
        if bridge.get(name) and not re.fullmatch(r'[0-9a-f]{40}', bridge[name]):
            raise ValueError('Use the complete signed model revisions in the bridge configuration.')
    if bridge.get('GPU_EXT_PROBE_ALWAYS', 'no') not in ('yes', 'no') or (
            bridge.get('GPU_EXT_PROBE_ALWAYS') == 'yes' and not bridge.get('GPU_EXT_RERANK_PROBE')):
        raise ValueError('Set GPU_EXT_PROBE_ALWAYS to yes or no, and name the probe file when it is yes.')
    import math
    for name in ('GPU_EXT_RECHECK_SECONDS', 'GPU_EXT_CONNECT_TIMEOUT', 'GPU_EXT_READ_TIMEOUT'):
        if name in bridge:
            try:
                number = float(bridge[name])
                if not math.isfinite(number) or not 0 < number <= 300:
                    raise ValueError()
            except ValueError:
                raise ValueError('Use bounded positive model bridge timeouts of at most 300 seconds.') from None
    variables = dict(paths, **images, TEI_REVISION=revision, TAILSCALE_DNS_NAME=dns,
                     RERANKER_DTYPE=dtype)
    template = Path(__file__).resolve().parent.parent / 'compose.full.json'
    text = template.read_text(encoding='ascii')
    # Replace JSON string values structurally, preserving spaces in bind paths.
    def replace(value):
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, str):
            for key, item in variables.items():
                value = value.replace('${' + key + '}/', item.rstrip('/') + '/')
                value = value.replace('${' + key + '}', item)
            if '${' in value:
                raise ValueError('Rebuild the kit with all full-role Compose inputs.')
        return value
    return replace(copy.deepcopy(json.loads(text)))
