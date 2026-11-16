"""Parse and enforce the reviewed MinerU 4 block-mapping YAML configuration.

Only the exact reviewed document is accepted; this deliberately small YAML
subset needs no third-party parser and never resolves tags, aliases or code.
"""
import re


CONFIG_YAML = (b'model:\n'
               b'  source: local\n'
               b'  base_dir: /models/models\n'
               b'  small_backend: torch\n'
               b'  vlm:\n'
               b'    engine: vllm\n')
EXPECTED = {'model': {'source': 'local', 'base_dir': '/models/models',
                      'small_backend': 'torch', 'vlm': {'engine': 'vllm'}}}


def validate_mineru_config(data):
    """Return the parsed mapping, or refuse any unreviewed YAML or setting."""
    if not isinstance(data, bytes):
        raise ValueError('MinerU config.yaml must be bytes')
    try:
        lines = data.decode('ascii').splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError('MinerU config.yaml must be ASCII YAML') from exc
    root = {}
    stack = [(-2, root)]
    for line in lines:
        match = re.fullmatch(r'( *)([a-z_]+):(?: ([A-Za-z0-9_/-]+))?', line)
        if match is None:
            raise ValueError('MinerU config.yaml has unsupported YAML syntax')
        indent, key, value = len(match[1]), match[2], match[3]
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if not stack or indent != stack[-1][0] + 2:
            raise ValueError('MinerU config.yaml has invalid YAML indentation')
        parent = stack[-1][1]
        if key in parent:
            raise ValueError('MinerU config.yaml has a duplicate YAML key')
        parent[key] = value if value is not None else {}
        if value is None:
            stack.append((indent, parent[key]))
    if root != EXPECTED or data != CONFIG_YAML:
        raise ValueError('MinerU config.yaml must match the reviewed local /models/models torch vllm configuration')
    return root
