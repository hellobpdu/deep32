"""Internal, offline implementation of the adapter setup entrypoints.

Only shipped skill files and marked guidance are managed. The manifest records
their hashes so an update cannot silently replace local edits. No credential
store, agent process, service or network endpoint is opened here.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tempfile


class Refusal(Exception):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def claude_launch_command(config_path, shell):
    """Quote one literal path for the named shell; this function executes nothing."""
    value = str(config_path)
    if shell == 'powershell':
        argument = "'" + value.replace("'", "''") + "'"
    elif shell == 'posix':
        argument = shlex.quote(value)
    else:
        raise Refusal('Unknown launcher shell.')
    return 'claude --mcp-config ' + argument


def safe_path(path):
    """Refuse links/reparse points before traversing any managed path."""
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise Refusal('A setup path is a link or reparse point; choose a plain directory.')


def read_file(path):
    safe_path(path)
    if path.exists() and not path.is_file():
        raise Refusal('A managed file path is not a regular file.')
    return path.read_bytes() if path.exists() else None


def json_object(data):
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise Refusal('A JSON configuration has duplicate fields.')
            obj[key] = value
        return obj
    try:
        value = json.loads(data.decode('utf-8-sig'), object_pairs_hook=unique)
    except (ValueError, UnicodeError):
        raise Refusal('A JSON configuration is invalid; no files were changed.') from None
    if not isinstance(value, dict):
        raise Refusal('A JSON configuration must be an object.')
    return value


def encoded_json(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode('utf-8')


def marked_merge(current, block, previous):
    text = (current or b'').decode('utf-8')
    begin, end = '<!-- deep32:begin', '<!-- deep32:end -->'
    if text.count(begin) != text.count(end) or text.count(begin) > 1:
        raise Refusal('Deep32 guidance markers conflict; repair them before setup.')
    if begin in text:
        match = re.search(r'<!-- deep32:begin.*?<!-- deep32:end -->(?:\r?\n)?', text, re.S)
        if not match:
            raise Refusal('Deep32 guidance markers conflict; repair them before setup.')
        old = match.group().encode('utf-8')
        if old != block and digest(old) != previous:
            raise Refusal('Deep32 guidance has local changes; back it up and resolve them before setup.')
        return (text[:match.start()] + block.decode('utf-8') + text[match.end():]).encode('utf-8')
    separator = b'' if not current else (b'\n' if current.endswith(b'\n') else b'\n\n')
    return (current or b'') + separator + block


def codex_config(current, entry, url):
    text = (current or b'').decode('utf-8')
    headers = list(re.finditer(r'(?m)^\s*\[([^\]\r\n]+)\]\s*(?:#[^\r\n]*)?$', text))
    owned = [i for i, m in enumerate(headers)
             if re.sub(r'[\s\'"]', '', m.group(1)) == 'mcp_servers.deep32']
    nested = [m for m in headers if re.sub(r'[\s\'"]', '', m.group(1)).startswith('mcp_servers.deep32.')]
    ancestor = any(re.sub(r'[\s\'"]', '', m.group(1)) == 'mcp_servers' for m in headers)
    if nested or ancestor or len(owned) > 1:
        raise Refusal('The deep32 MCP configuration conflicts; no files were changed.')
    if owned:
        i = owned[0]
        section = text[headers[i].end():headers[i + 1].start() if i + 1 < len(headers) else len(text)]
        values = {}
        for line in section.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            m = re.fullmatch(r'([a-z_]+)\s*=\s*("[^"\r\n]*"|[0-9]+)\s*(?:#.*)?', line)
            if not m or m.group(1) in values:
                raise Refusal('The deep32 MCP configuration conflicts; no files were changed.')
            values[m.group(1)] = m.group(2)
        expected = {'url': json.dumps(url), 'bearer_token_env_var': '"DEEP32_MCP_TOKEN"',
                    'startup_timeout_sec': '30', 'tool_timeout_sec': '120'}
        if values != expected:
            raise Refusal('The deep32 MCP entry already has different settings; keep it or resolve the conflict.')
        return current
    # A dotted assignment can also define the same table without a table header.
    if re.search(r'(?m)^\s*[\'"]?mcp_servers[\'"]?\s*(?:\.\s*[\'"]?deep32|=)', text):
        raise Refusal('The deep32 MCP configuration conflicts; no files were changed.')
    return (current or b'') + (b'\n' if current else b'') + entry


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_path(path)
    fd, name = tempfile.mkstemp(prefix='.deep32-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)



def prepare(agent, home, url):
    kit = Path(__file__).parent / ('codex' if agent == 'codex' else 'claude-code')
    safe_path(home)
    if home.exists() and not home.is_dir():
        raise Refusal('The agent home must be a directory.')
    state_path = home / '.deep32-setup.json'
    state_data = read_file(state_path)
    state = json_object(state_data) if state_data else {}
    if state and (state.get('version') != 1 or state.get('agent') != agent):
        raise Refusal('This setup manifest belongs to a different adapter or version.')
    previous = state.get('files', {})
    if not isinstance(previous, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                             for k, v in previous.items()):
        raise Refusal('The setup manifest is invalid.')
    planned, hashes = {}, {}
    names = sorted(p.name for p in (kit / 'skills').iterdir() if p.is_dir())
    expected_names = ['deep32', 'deep32-configure', 'deep32-describe', 'deep32-design',
                      'deep32-troubleshoot', 'deep32-verify'] if agent == 'codex' else ['deep32']
    if names != expected_names:
        raise Refusal('The adapter skill set is incomplete.')
    for source in sorted((kit / 'skills').rglob('*')):
        safe_path(source)
        if not source.is_file():
            continue
        rel = source.relative_to(kit).as_posix()
        data = source.read_bytes()
        if source.suffix in ('.md', '.yaml'):
            data = data.decode('utf-8').replace('${CODEX_HOME}', home.as_posix()).encode('utf-8')
        current = read_file(home / rel)
        if current is not None and current != data and digest(current) != previous.get(rel):
            raise Refusal('An installed Deep32 skill has local changes; back it up and resolve them before setup.')
        planned[rel], hashes[rel] = data, digest(data)
    # Unknown files in owned skill folders may be local work: never remove them.
    for name in names:
        folder = home / 'skills' / name
        safe_path(folder)
        if folder.exists():
            if not folder.is_dir():
                raise Refusal('A skill directory path is not a directory.')
            for path in sorted(folder.rglob('*')):
                safe_path(path)
                if path.is_file():
                    rel = path.relative_to(home).as_posix()
                    if rel not in planned:
                        raise Refusal('An owned skill folder contains extra files; preserve them outside it before setup.')
    entrypoint = 'AGENTS.md' if agent == 'codex' else 'CLAUDE.md'
    source = kit / ('AGENTS.deep32.md' if agent == 'codex' else 'CLAUDE.deep32.md')
    block = source.read_text(encoding='utf-8').replace('${CODEX_HOME}', home.as_posix())
    block = block.replace('${CLAUDE_HOME}', home.as_posix()).rstrip() + '\n'
    block = block.encode('utf-8')
    planned[entrypoint] = marked_merge(read_file(home / entrypoint), block, state.get('guidance_sha256'))
    if url:
        if agent == 'codex':
            rel = 'config.toml'
            entry = (kit / 'config.deep32.toml').read_bytes().replace(b'{{WORKSTATION_MCP_URL}}', url.encode('ascii'))
            planned[rel] = codex_config(read_file(home / rel), entry, url)
        else:
            rel = 'deep32.mcp.json'
            current = read_file(home / rel)
            config = json_object(current) if current else {}
            servers = config.setdefault('mcpServers', {})
            if not isinstance(servers, dict):
                raise Refusal('mcpServers must be an object; no files were changed.')
            server = {'type': 'http', 'url': url,
                      'headers': {'Authorization': 'Bearer ${DEEP32_MCP_TOKEN}'}}
            if 'deep32' in servers and servers['deep32'] != server:
                raise Refusal('The deep32 MCP entry already has different settings; keep it or resolve the conflict.')
            if servers.get('deep32') == server:
                planned[rel] = current
            else:
                servers['deep32'] = server
                planned[rel] = encoded_json(config)
    planned['.deep32-setup.json'] = encoded_json({'version': 1, 'agent': agent, 'files': hashes,
                                               'guidance_sha256': digest(block)})
    changes = {}
    for rel, data in planned.items():
        old = read_file(home / rel)
        if old != data:
            changes[rel] = (old, data)
    return changes, len(names)


def install(home, changes):
    if not changes:
        return None
    home.mkdir(parents=True, exist_ok=True)
    backup = None
    if any(old is not None for old, _ in changes.values()):
        backup_root = home / '.deep32-backups'
        safe_path(backup_root)
        backup_root.mkdir(exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix='setup-', dir=backup_root))
        os.chmod(backup, 0o700)
        for rel, (old, _) in sorted(changes.items()):
            if old is not None:
                atomic_write(backup / rel, old)
    written = []
    try:
        for rel, (old, data) in sorted(changes.items()):
            path = home / rel
            if read_file(path) != old:
                raise Refusal('A setup target changed during planning; run setup again.')
            atomic_write(path, data)
            written.append((path, old))
        for rel, (_, data) in sorted(changes.items()):
            if read_file(home / rel) != data:
                raise Refusal('Copied-file verification failed.')
    except Exception:
        for path, old in reversed(written):
            if old is None:
                path.unlink()
            else:
                atomic_write(path, old)
        raise
    return backup


def main():
    ap = argparse.ArgumentParser(description='Install checked Deep32 skill files; no agent or service is contacted.')
    ap.add_argument('--agent', required=True, choices=('codex', 'claude'))
    ap.add_argument('--home', required=True)
    ap.add_argument('--workstation-url', default='')
    args = ap.parse_args()
    if sys.version_info < (3, 10):
        ap.error('Python 3.10 or newer is required.')
    if not Path(args.home).is_absolute():
        ap.error('The agent home must be an absolute directory.')
    if args.workstation_url:
        match = re.fullmatch(r'https://([A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)(?::([0-9]{1,5}))?/mcp',
                             args.workstation_url)
        if not match or (match.group(2) and not 1 <= int(match.group(2)) <= 65535):
            ap.error('The workstation URL must be https://host[:port]/mcp.')
    home = Path(os.path.abspath(args.home))
    # Inspect marker names only. Never open original-install private files.
    appdata = os.environ.get('LOCALAPPDATA')
    if appdata and any((Path(appdata) / p).exists() for p in
                       ('Programs/Deep32/Deep32-package.json', 'Programs/Deep32/state/private/relay')):
        real = Path(os.environ.get('CODEX_HOME' if args.agent == 'codex' else 'CLAUDE_CONFIG_DIR',
                                   str(Path.home() / ('.codex' if args.agent == 'codex' else '.claude'))))
        if home == Path(os.path.abspath(real)):
            print('Refused: the original Deep32 installation uses this agent home.')
            return 3
    try:
        changes, count = prepare(args.agent, home, args.workstation_url)
        backup = install(home, changes)
    except (Refusal, OSError, UnicodeError) as exc:
        # OSError text can contain arbitrary user values. Keep diagnostics sanitised.
        print(str(exc) if isinstance(exc, Refusal) else 'Setup could not read or write its managed files; no completed install was recorded.')
        return 1
    print('Copied-file check passed: %d skill directories; %d changed files.' % (count, len(changes)))
    if backup:
        print('Previous managed files were backed up under the agent home .deep32-backups directory.')
    print('Service, authentication and credential-mode compatibility have not been checked.')
    if args.agent == 'claude' and args.workstation_url:
        shell = 'powershell' if os.name == 'nt' else 'posix'
        label = 'PowerShell' if shell == 'powershell' else 'a POSIX shell'
        print('Launch in %s after credential-mode acceptance: %s' % (
            label, claude_launch_command(home / 'deep32.mcp.json', shell)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
