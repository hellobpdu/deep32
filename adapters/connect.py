#!/usr/bin/env python3
"""Direct MCP enrolment and safe per-user agent configuration (Python 3.11+).

No secret command arguments or environment inputs. The existing C8 Joiner owns
the exchange; this module adapts only its received-credential storage seam.
"""
import argparse
import codecs
import contextlib
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
if sys.version_info < (3,11):
    print('Connect needs Python 3.11 or newer; install it and run Connect again.',file=sys.stderr)
    raise SystemExit(10)
import tomllib
import urllib.error
import urllib.parse
import urllib.request

KIT = Path(__file__).resolve().parent
CORE = KIT.parent / 'src/workstation/distrib/site/usr/local/d32'
TOOLS = frozenset(('rag_status','search_rag','search_many','get_section','get_outline',
                  'lookup_id','lookup_rfc','find_command','grep_corpus','list_documents','verify_quote'))
MAX_FILE = 4 * 1024 * 1024


class Refusal(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs)


def safe(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise Refusal('A configuration path is a link; choose a private local home and run Connect again.')
    return path


def read(path, private=False):
    path = safe(path)
    if not path.exists():
        return b''
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0) | getattr(os,'O_NONBLOCK',0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE or info.st_nlink != 1:
            raise Refusal('A configuration file is unsafe or too large; repair it and run Connect again.')
        if private and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
            raise Refusal('The private credential permissions are unsafe; restore mode 0600 and run Connect again.')
        return stream.read(MAX_FILE + 1)


def atomic(path, raw, mode=0o600):
    path = safe(path)
    safe(path.parent).mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.deep32-stage-' + secrets.token_hex(8))
    try:
        with os.fdopen(os.open(safe(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                               getattr(os,'O_BINARY',0), mode), 'wb') as stream:
            if os.name == 'posix':
                os.fchmod(stream.fileno(), mode)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        safe(path)
        os.replace(temporary, path)
        if os.name == 'posix':
            fd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def json_bytes(obj):
    return (json.dumps(obj, indent=2, ensure_ascii=True) + '\n').encode('utf-8')


class PrivateStore:
    """CurrentUser DPAPI on Windows; private regular files on POSIX."""
    def __init__(self, windows=None):
        self.windows = os.name == 'nt' if windows is None else windows

    def protect_directory(self, path):
        path = safe(path)
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.windows:
            # The script carries a path only; no credential crosses a process boundary.
            script = ("$ErrorActionPreference='Stop';$p=" + "'" + str(path).replace("'", "''") + "';"
                      "$u=[Security.Principal.WindowsIdentity]::GetCurrent().User;"
                      "$a=New-Object Security.AccessControl.DirectorySecurity;"
                      "$a.SetOwner($u);$a.SetAccessRuleProtection($true,$false);"
                      "$r=New-Object Security.AccessControl.FileSystemAccessRule($u,'FullControl','ContainerInherit,ObjectInherit','None','Allow');"
                      "$a.AddAccessRule($r);[IO.Directory]::SetAccessControl($p,$a)")
            import base64
            try:
                subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',
                                base64.b64encode(script.encode('utf-16le')).decode('ascii')],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, check=True, timeout=15)
            except (OSError, subprocess.SubprocessError):
                raise Refusal('The private store could not be restricted to this user; repair its permissions and run Connect again.') from None
        else:
            info = path.stat()
            if info.st_uid != os.geteuid():
                raise Refusal('The private store belongs to another user; choose a private local home and run Connect again.')
            os.chmod(path, 0o700)

    def crypt(self, raw, decrypt=False):
        if not self.windows:
            return raw
        from ctypes import wintypes
        class Blob(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
        buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        source, result = Blob(len(raw), buffer), Blob()
        crypt32, kernel32 = ctypes.WinDLL('crypt32', use_last_error=True), ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        function.restype = wintypes.BOOL
        # CRYPTPROTECT_UI_FORBIDDEN; no machine-wide scope or recovery prompt.
        ok = function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
        if not ok:
            raise Refusal('The private credential could not be opened for this user; pair this agent again.')
        try:
            return ctypes.string_at(result.data, result.size)
        finally:
            ctypes.memset(result.data, 0, result.size)
            kernel32.LocalFree(result.data)

    def put(self, path, token):
        if not re.fullmatch('[0-9a-f]{64}', token):
            raise Refusal('Enrolment returned an invalid credential; open a new Workstation pairing window.')
        self.protect_directory(Path(path).parent)
        atomic(path, self.crypt((token + '\n').encode('ascii')))

    def get(self, path):
        path = safe(path)
        raw = self.crypt(read(path,private=not self.windows), decrypt=True)
        if not re.fullmatch(rb'[0-9a-f]{64}\n', raw):
            raise Refusal('The private credential is damaged; pair this agent again.')
        return raw.decode('ascii').strip()


def endpoint(text):
    if any(ord(char)<=32 or ord(char)>=127 for char in text):
        raise Refusal('Workstation must be an HTTPS /mcp URL printed by publication; copy that URL and run Connect again.')
    url = urllib.parse.urlsplit(text)
    try:
        port = url.port
    except ValueError:
        port = -1
    if (url.scheme != 'https' or not url.hostname or url.username or url.password or
            url.path != '/mcp' or url.query or url.fragment or port == -1 or
            not re.fullmatch('[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', url.hostname)):
        raise Refusal('Workstation must be an HTTPS /mcp URL printed by publication; copy that URL and run Connect again.')
    return text.rstrip('/'), url.hostname


def codex_merge(raw, name, url, variable):
    try:
        text = raw.decode('utf-8')
        obj = tomllib.loads(text)
    except (ValueError, UnicodeError):
        raise Refusal('Codex config.toml is invalid or has duplicate tables; repair it and run Connect again.') from None
    servers = obj.get('mcp_servers', {})
    if type(servers) is not dict:
        raise Refusal('Codex mcp_servers is ambiguous; repair config.toml and run Connect again.')
    value = {'url':url, 'bearer_token_env_var':variable, 'startup_timeout_sec':30, 'tool_timeout_sec':120}
    if name in servers:
        if servers[name] != value:
            raise Refusal('The scoped Codex server name belongs to another configuration; restore its owned entry before reconnecting.')
        return raw
    entry = '\n[mcp_servers.' + name + ']\n' + ''.join(key + ' = ' + json.dumps(value) + '\n' for key,value in value.items())
    result = (text + ('' if not text or text.endswith('\n') else '\n') + entry).encode('utf-8')
    # Parse the staged result too: catches dotted and inline-table ambiguities.
    try:
        staged = tomllib.loads(result.decode('utf-8'))
        if staged['mcp_servers'][name] != value:
            raise ValueError()
    except ValueError:
        raise Refusal('Codex mcp_servers cannot be extended safely; expand its inline table and run Connect again.') from None
    return result


def claude_merge(raw, name, url, variable):
    try:
        obj = strict_json(raw) if raw else {}
        if type(obj) is not dict or type(obj.get('mcpServers', {})) is not dict:
            raise ValueError()
        servers = obj.setdefault('mcpServers', {})
        value = {'type':'http','url':url,'headers':{'Authorization':'Bearer ${' + variable + '}'}}
        if name in servers and servers[name] != value:
            raise ValueError()
        servers[name] = value
        return json_bytes(obj)
    except (ValueError, UnicodeError):
        raise Refusal('Claude configuration is invalid, duplicated or has a conflicting scoped server; repair it and run Connect again.') from None


def merge_block(raw, block, ident):
    try:
        text = raw.decode('utf-8')
    except UnicodeError:
        raise Refusal('The agent instructions are not UTF-8; repair the file and run Connect again.') from None
    begin, end = '<!-- deep32:' + ident + ':begin -->', '<!-- deep32:' + ident + ':end -->'
    if text.count(begin) > 1 or text.count(end) > 1 or text.count(begin) != text.count(end):
        raise Refusal('The owned Deep32 instruction block is ambiguous; repair its markers and run Connect again.')
    wrapped = begin + '\n' + block.strip() + '\n' + end + '\n'
    if begin in text:
        start, finish = text.index(begin), text.index(end) + len(end)
        if finish < start:
            raise Refusal('The owned Deep32 instruction block is reversed; repair its markers and run Connect again.')
        if text[finish:finish+1] == '\n':
            finish += 1
        return (text[:start] + wrapped + text[finish:]).encode('utf-8')
    return (text + ('\n' if text and not text.endswith('\n') else '') + wrapped).encode('utf-8')


def install_files(agent, home, state):
    """Build a complete nonsecret plan; no mutation until every parser accepts."""
    name, variable, url = state['server_name'], state['variable'], state['url']
    config = home / ('config.toml' if agent == 'codex' else '../.claude.json')
    if agent == 'claude':
        # CLAUDE_CONFIG_DIR is the user scope, whose .claude.json lives within it.
        config = home / '.claude.json' if state.get('profile_dir') or os.environ.get('CLAUDE_CONFIG_DIR') else home.parent / '.claude.json'
    raw = read(config)
    planned = {safe(config): codex_merge(raw,name,url,variable) if agent == 'codex' else claude_merge(raw,name,url,variable)}
    source = KIT / ('codex' if agent == 'codex' else 'claude-code') / 'skills'
    skill_names = {p.name:p.name + '_' + state['install_id'][:8] for p in source.iterdir() if p.is_dir()}
    def rewritten(text,skill='deep32'):
        text = text.replace('${CODEX_HOME}',home.as_posix()).replace('${CLAUDE_HOME}',home.as_posix())
        text = text.replace('${CLAUDE_SKILL_DIR}',(home/'skills'/skill_names[skill]).as_posix())
        for before,after in sorted(skill_names.items(), key=lambda item:-len(item[0])):
            text = re.sub('/skills/' + re.escape(before) + r'(?![A-Za-z0-9_-])', '/skills/' + after, text)
            text = re.sub(r'\$' + re.escape(before) + r'(?![A-Za-z0-9_-])', '$' + after, text)
            text = text.replace('name: ' + before + '\n', 'name: ' + after + '\n')
            text = text.replace('display_name: "' + before + '"','display_name: "' + after + '"')
        return text.replace('mcp__deep32__','mcp__' + name + '__').replace('`deep32` prefix','`' + name + '` prefix').replace('DEEP32_MCP_TOKEN',variable)
    for src in sorted(source.rglob('*')):
        if src.is_file():
            rel = src.relative_to(source)
            dest = home / 'skills' / skill_names[rel.parts[0]] / Path(*rel.parts[1:])
            content = src.read_bytes()
            planned[safe(dest)] = rewritten(content.decode('utf-8'),rel.parts[0]).encode('utf-8') if src.suffix in ('.md','.yaml','.yml') else content
    # each agent's own standing block, the one the skills-only setup installs too (setup_skills.py)
    source_block = 'codex/AGENTS.deep32.md' if agent == 'codex' else 'claude-code/CLAUDE.deep32.md'
    block = rewritten((KIT/source_block).read_text(encoding='utf-8'))
    block = re.sub(r'<!-- deep32:begin.*?-->\n|<!-- deep32:end -->\n?', '', block)
    instructions = home / ('AGENTS.md' if agent == 'codex' else 'CLAUDE.md')
    planned[safe(instructions)] = merge_block(read(instructions), block, state['install_id'])
    if agent == 'claude':
        settings = home/'settings.json'
        try:
            options = strict_json(read(settings)) if settings.exists() else {}
            if type(options) is not dict or type(options.get('permissions',{})) is not dict:
                raise ValueError()
            permissions = options.setdefault('permissions',{})
            if type(permissions.get('allow',[])) is not list or any(type(v) is not str for v in permissions.get('allow',[])):
                raise ValueError()
            rules = permissions.setdefault('allow',[])
            for tool in sorted(TOOLS):
                rule = 'mcp__' + name + '__' + tool
                if rule not in rules:
                    rules.append(rule)
            planned[safe(settings)] = json_bytes(options)
        except (ValueError,UnicodeError):
            raise Refusal('Claude settings.json has ambiguous permission rules; repair it and run Connect again.') from None
    return planned


def apply_plan(planned, state, consent):
    changed = [(path, read(path), raw) for path,raw in planned.items() if read(path) != raw]
    existing = [path for path,old,_ in changed if path.exists()]
    if existing and not consent(existing):
        raise Refusal('Configuration merge was declined; run Connect again when the listed backup and merge are approved.',10)
    # Every staged file is public configuration/skill text; secrets never enter this plan.
    stamp = str(time.time_ns()) + '-' + secrets.token_hex(4)
    committed = []
    try:
        for path, old, raw in changed:
            if read(path)!=old:
                raise Refusal('An agent file changed during the merge; close its settings writer and run Connect again.')
            if path.exists():
                atomic(path.with_name(path.name + '.deep32-backup-' + stamp), old)
            atomic(path, raw)
            committed.append((path,old,path in existing))
    except BaseException:
        for path,old,existed in reversed(committed):
            if existed:
                atomic(path,old)
            else:
                path.unlink(missing_ok=True)
        raise
    return len(changed)


def import_core():
    if not (CORE/'deep32net/pairing.py').is_file():
        raise Refusal('The accepted C8 pairing core is missing; use Connect from the complete verified source kit.')
    sys.path.insert(0,str(CORE))
    from deep32net import pairing, store, wire
    return pairing, store, wire


def enrol(root, host, code, vault, resolve=socket.getaddrinfo, joiner_class=None):
    pairing, store, wire = import_core()
    from deep32net.errors import NetError, StateError
    from deep32net.transport import tailnet_ipv4
    try:
        addresses = sorted({row[4][0] for row in resolve(host,7454,socket.AF_INET,socket.SOCK_STREAM)})
        if len(addresses) != 1 or not tailnet_ipv4(addresses[0]):
            raise ValueError()
    except (OSError,ValueError):
        raise Refusal('The Workstation has no unique tailnet IPv4 address; connect Tailscale and check its published name.',14) from None
    received = []
    original = store.save_received
    def protected_received(state, kind, peer_id, handover):
        wire.validate_handover(handover,kind)
        if kind != 'gateway':
            raise Refusal('The peer did not offer gateway enrolment; open the Workstation headend pairing action.',13)
        path = store.received_dir(state,kind,peer_id)/'mcp-token'
        try:
            vault.put(path,handover['mcp_token'])
        except (Refusal,OSError):
            # Keep the accepted core's failure/rollback transaction in charge.
            raise StateError('credential-store') from None
        received.append(path)
        return ['mcp-token']
    store.save_received = protected_received
    try:
        joiner = (joiner_class or pairing.Joiner)(kind='gateway',state=str(root),code_text=code,
            transport='tailnet',address=addresses[0],peer_dns_name=host)
        result = joiner.join()
        path = store.received_dir(root,'gateway',result['peer_id'])/'mcp-token'
        if result.get('received') != ['mcp-token'] or not store.has_record(root,'gateway','joiner',result['peer_id']):
            raise Refusal('Enrolment did not commit a gateway peer; open a new Workstation pairing window.',13)
        vault.get(path)  # validate protected credential before recording the connector commit.
        return result['peer_id'], path
    except NetError as exc:
        if exc.reason == 'openssl-missing':
            raise Refusal('OpenSSL was not found; install OpenSSL on this agent PC and run Connect again.',10) from None
        number = 12 if exc.exit_code == 3 else 11 if exc.reason in ('expired','timeout') else 14 if exc.reason in ('unreachable','tls-failed','connection') else 13
        raise Refusal('Gateway enrolment failed; open the Workstation headend pairing action and run Connect again.',number) from None
    finally:
        store.save_received = original


class Gateway:
    def __init__(self, url):
        self.url = url
        # No environment proxy can receive the bearer, even with a misconfigured proxy.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def rpc(self, method, params=None, token=None, ident=1):
        obj = {'jsonrpc':'2.0','id':ident,'method':method}
        if params is not None:
            obj['params'] = params
        headers = {'Content-Type':'application/json','Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2024-11-05'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        request = urllib.request.Request(self.url,json.dumps(obj).encode('utf-8'),headers,method='POST')
        try:
            with self.opener.open(request,timeout=10) as response:
                raw = response.read(MAX_FILE+1)
                if len(raw)>MAX_FILE:
                    raise ValueError()
                result = strict_json(raw)
                if result.get('id') != ident or 'error' in result or 'result' not in result:
                    raise ValueError()
                return result['result']
        except urllib.error.HTTPError as exc:
            raise Refusal('The gateway refused authentication; reopen Workstation enrolment for this agent.',13 if exc.code in (401,403) else 14) from None
        except (OSError,ValueError):
            raise Refusal('The gateway did not answer a valid MCP request; check Workstation publication and run Connect again.',14) from None

    def probe(self):
        try:
            self.rpc('initialize',{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'deep32-connect','version':'1'}})
        except Refusal as exc:
            if exc.code == 13:
                return
            raise
        raise Refusal('The gateway accepted an unauthenticated MCP request; repair its bearer protection before connecting.',13)

    def verify(self, token):
        self.rpc('initialize',{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'deep32-connect','version':'1'}},token)
        tools = self.rpc('tools/list',{},token,2)
        if {row.get('name') for row in tools.get('tools',[])} != TOOLS:
            raise Refusal('The gateway tool set differs from the eleven-tool contract; repair the Workstation release.',13)
        status = self.rpc('tools/call',{'name':'rag_status','arguments':{'format':'json'}},token,3)
        if status.get('isError'):
            raise Refusal('The gateway status check failed; restore the Workstation retrieval services and reconnect.',13)
        # The content stays private: this connector reports only readiness, never corpus text.
        try:
            value = strict_json(next(item['text'] for item in status['content'] if item.get('type')=='text'))
        except (ValueError,KeyError,StopIteration):
            raise Refusal('The gateway returned no structured readiness result; update the Workstation gateway.',13) from None
        if value.get('ok') is not True:
            raise Refusal('Gateway authentication and eleven tools passed but retrieval is not ready; repair the reported Workstation services and reconnect.',13)
        return 'degraded' if value.get('degraded') else 'ready'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Refusal('The gateway redirected its MCP URL; use the exact HTTPS URL printed by Workstation publication.',14)


def detect_agent(agent, env=None, require_login=True):
    executable = shutil.which(agent)
    if not executable:
        raise Refusal('The selected agent is not installed; install it, sign in and run Connect again.',10)
    try:
        print(agent + ': waiting for its version probe; repair the installed agent if it does not return.')
        version = subprocess.run([executable,'--version'],stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,check=True,timeout=10,
            **({'env':env} if env is not None else {})).stdout.decode('utf-8').strip()
        if not re.fullmatch(r'[A-Za-z0-9 ._()+:/-]{1,160}',version):
            raise ValueError()
        # A fresh isolated profile has no account state yet. Its own launcher is
        # used for native sign-in after pairing; never consult another profile.
        if not require_login:
            return str(Path(executable).resolve()), version
        # Only a documented status command is used; account files are never opened.
        login = ['login','status'] if agent == 'codex' else ['auth','status']
        print(agent + ': waiting for its sign-in check; sign in with the agent if it is not ready.')
        result = subprocess.run([executable,*login],stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,
            **({'env':env} if env is not None else {}))
        if result.returncode:
            raise Refusal('The selected agent needs sign-in; sign in with its own command and run Connect again.',10)
        return str(Path(executable).resolve()), version
    except (OSError,subprocess.SubprocessError,ValueError,KeyError,UnicodeError):
        raise Refusal('The selected agent could not report its version and sign-in state; update it, sign in and run Connect again.',10) from None


def verify_client(agent, executable, root, state, token):
    if agent != 'claude':
        return
    # Probe the installed CLI's real HTTP expansion, rather than assuming a minimum
    # version. No model turn or account credential is needed for `mcp list`.
    # Captured output may include resolved headers: it is never forwarded or saved.
    with tempfile.TemporaryDirectory(prefix='client-probe-', dir=root) as temporary:
        folder = Path(temporary)
        atomic(folder/'.claude.json',claude_merge(b'',state['server_name'],state['url'],state['variable']))
        env = os.environ.copy()
        env.update(CLAUDE_CONFIG_DIR=str(folder))
        env[state['variable']] = token
        try:
            result = subprocess.run([executable,'mcp','list'],cwd=folder,env=env,
                stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
            output = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',result.stdout.decode('utf-8',errors='replace'))
            lines = [line for line in output.splitlines() if state['server_name'] in line and state['url'] in line]
            if result.returncode or len(lines)!=1 or not re.search(r'\bConnected\b',lines[0]) or re.search(r'\b(?:Failed|Disconnected)\b',lines[0]):
                raise ValueError()
        except (OSError,subprocess.SubprocessError,ValueError):
            raise Refusal('agent-credential-mode-unsupported: Claude Code could not confirm the scoped HTTP connection; update it and run Connect again.',10) from None


@contextlib.contextmanager
def waiting(message):
    stopped = threading.Event()
    def heartbeat():
        while not stopped.wait(10):
            print(message,flush=True)
    thread = threading.Thread(target=heartbeat,daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join()


def recovered_enrolment(root, vault):
    # A crash after the C8 durable peer commit but before our public marker must
    # resume the committed enrolment instead of burning another one-time window.
    _, store, _ = import_core()
    rows = [row for row in store.list_peers(root) if row['kind']=='gateway' and row['side']=='joiner']
    if not rows:
        return None
    if len(rows)!=1:
        raise Refusal('The headend has several gateway peer records; resolve its enrolment state before reconnecting.')
    row = rows[0]
    path = store.received_dir(root,'gateway',row['peer_id'])/'mcp-token'
    vault.get(path)
    return row['peer_id'],path


@contextlib.contextmanager
def connection_lock(root, vault):
    vault.protect_directory(root)
    path = safe(root/'connect.lock')
    if not path.exists():
        try:
            # O_EXCL makes concurrent first-use creation harmless.
            fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0),0o600)
            with os.fdopen(fd,'wb') as stream:
                stream.write(b'\0')
        except FileExistsError:
            pass
    fd = os.open(path,os.O_RDWR|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    locked = False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_size!=1:
            raise OSError()
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        locked = True
    except OSError:
        os.close(fd)
        raise Refusal('Another Connect operation owns this headend; let it finish and run Connect again.',10) from None
    try:
        yield
    finally:
        if locked:
            if os.name=='nt':
                import msvcrt
                os.lseek(fd,0,os.SEEK_SET)
                msvcrt.locking(fd,msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_UN)
        os.close(fd)


def refuse_original(home):
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        return
    markers = (Path(local)/'Programs/Deep32/Deep32-package.json',Path(local)/'Programs/Deep32/state/private/relay')
    if not any(marker.exists() for marker in markers):
        return
    default = (Path.home()/'.codex',Path.home()/'.claude')
    defaults = (*default,*(Path(value) for key in ('CODEX_HOME','CLAUDE_CONFIG_DIR') if (value:=os.environ.get(key))))
    if any(os.path.normcase(os.path.abspath(home))==os.path.normcase(os.path.abspath(target)) for target in defaults):
        raise Refusal('This user already has the original Deep32 install; use the distribution on a separate agent PC.',10)


def consent(paths):
    print('Back up and merge these existing files:')
    for path in paths:
        print('  ' + str(path))
    if not sys.stdin.isatty():
        return False
    return input('Type merge to approve this file plan: ').strip() == 'merge'


CODE_INPUT_MAX = 64  # the C8 core refuses longer code text


class Terminal:
    """The console itself, as getpass uses it, with typed keys not echoed.

    Windows: msvcrt.getwch/putwch. POSIX: /dev/tty (else stdin and stderr) in
    non-canonical mode with echo off and ISIG kept, so Ctrl+C still stops Connect.
    The previous terminal settings return on every exit path.
    """
    def __init__(self, windows=None):
        self.windows = os.name == 'nt' if windows is None else windows
        self.saved = self.termios = None
        self.fd = self.stream = None
        self.owned = False
        self.decoder = codecs.getincrementaldecoder('utf-8')('replace')
        self.dot = '\u2022'

    def __enter__(self):
        if self.windows:
            import msvcrt
            self.msvcrt = msvcrt
            return self
        import termios
        self.termios = termios
        try:
            encoding = codecs.lookup(getattr(sys.stdin,'encoding',None) or 'ascii').name
        except LookupError:
            encoding = 'ascii'
        if encoding != 'utf-8':
            self.dot = '.'
        try:
            self.fd = os.open('/dev/tty',os.O_RDWR|getattr(os,'O_NOCTTY',0))
            self.owned = True
        except OSError:
            self.fd, self.stream = sys.stdin.fileno(), sys.stderr
        try:
            saved = termios.tcgetattr(self.fd)
            keys = list(saved)
            keys[6] = list(saved[6])
            keys[3] &= ~(termios.ECHO|termios.ICANON)
            keys[6][termios.VMIN], keys[6][termios.VTIME] = 1, 0
            self.saved = saved
            termios.tcsetattr(self.fd,termios.TCSAFLUSH,keys)
        except BaseException:
            self.__exit__()
            raise
        return self

    def read(self):
        if self.windows:
            return self.msvcrt.getwch()
        while True:
            data = os.read(self.fd,1)
            if not data:
                raise EOFError()
            char = self.decoder.decode(data)
            if char:
                return char

    def write(self, text):
        if self.windows:
            for char in text:
                self.msvcrt.putwch(char)
        elif self.stream is not None:
            self.stream.write(text)
            self.stream.flush()
        else:
            os.write(self.fd,text.encode('utf-8','replace'))

    def __exit__(self, *unused):
        try:
            if self.saved is not None:
                saved, self.saved = self.saved, None
                self.termios.tcsetattr(self.fd,self.termios.TCSAFLUSH,saved)
        finally:
            if self.owned:
                self.owned = False
                os.close(self.fd)


def masked_input(prompt, terminal=None):
    """Read a one-time secret showing one dot per character; the value is never shown.

    Backspace removes the last character, Ctrl+U clears the line, Enter ends it.
    Cursor and function keys are ignored. Ctrl+C and end of input with nothing
    typed raise as getpass does, after the terminal is restored.
    """
    terminal = terminal or Terminal()
    typed = []
    with terminal:
        windows = terminal.windows
        dot = terminal.dot
        terminal.write(prompt)
        try:
            while True:
                char = terminal.read()
                if char in ('\r','\n'):
                    return ''.join(typed)
                if char == '\x03':
                    raise KeyboardInterrupt()
                if char in ('\x04','\x1a'):
                    if not typed:
                        raise EOFError()
                elif windows and char in ('\x00','\xe0'):
                    terminal.read()
                elif char == '\x1b' and not windows:
                    # ESC [ ... final byte, ESC O x or ESC x: a cursor or function key.
                    char = terminal.read()
                    if char == '[':
                        while not '\x40' <= terminal.read() <= '\x7e':
                            continue
                    elif char == 'O':
                        terminal.read()
                elif char in ('\b','\x7f'):
                    if typed:
                        typed.pop()
                        terminal.write('\b \b')
                elif char == '\x15':
                    terminal.write('\b \b'*len(typed))
                    typed.clear()
                elif char.isprintable() and len(typed) < CODE_INPUT_MAX:
                    typed.append(char)
                    terminal.write(dot)
        finally:
            typed.clear()
            terminal.write('\n')


def hidden_code(agent, terminal=None):
    if not sys.stdin.isatty():
        raise Refusal('Pairing needs a private interactive terminal; open the Workstation headend window and run Connect there.',10)
    return masked_input('Workstation one-time code for ' + agent + ' (hidden, 10 characters): ',terminal)


def state_root(home, url, agent):
    return home/'deep32-connect'/hashlib.sha256((url+'\n'+agent).encode('ascii')).hexdigest()[:16]


def profile_environment(agent, home):
    env = os.environ.copy()
    env['CODEX_HOME' if agent == 'codex' else 'CLAUDE_CONFIG_DIR'] = str(home)
    return env


def isolated_profile(agent, directory, url, check=False):
    """Validate only the explicitly selected path, never an original agent home."""
    home = Path(directory)
    if not home.is_absolute():
        raise Refusal('The isolated profile directory must be absolute; create a new empty directory and run Connect again.',10)
    home = safe(home)
    if not home.is_dir():
        raise Refusal('Create the new empty profile directory yourself before running Connect with --profile-dir.',10)
    if check:
        try:
            state = strict_json(read(state_root(home,url,agent)/'state.json'))
        except (ValueError,UnicodeError):
            raise Refusal('The isolated profile has no valid connector state; use the profile and URL printed by Connect.',10) from None
        if (type(state) is not dict or state.get('agent') != agent or state.get('url') != url or
                state.get('profile_dir') != str(home)):
            raise Refusal('This is not the isolated profile enrolled for this agent and Workstation; use its original --profile-dir and URL.',10)
    elif next(home.iterdir(),None) is not None:
        raise Refusal('The isolated profile directory is not empty; create a new empty directory and run Connect again.',10)
    return home


def launcher_text(state_path):
    if os.name == 'nt':
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"
        return ('#requires -Version 5.1\n& '+quote(sys.executable)+' -I -B '+quote(KIT/'connect.py')+' --launch '+quote(state_path)+' @args\nexit $LASTEXITCODE\n').encode('utf-8')
    import shlex
    return ('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' -I -B '+shlex.quote(str(KIT/'connect.py'))+' --launch '+shlex.quote(str(state_path))+' "$@"\n').encode('utf-8')


def _connect_one(agent, url, host, home, name=None, check=False, vault=None,
                detect=detect_agent, gateway_factory=Gateway, prompt=hidden_code,
                enroller=enrol, approve=consent, client_check=verify_client, profile=False):
    executable, version = (detect(agent,env=profile_environment(agent,home),require_login=check)
                           if profile else detect(agent))
    gateway = gateway_factory(url)
    print(agent + ': waiting for the published gateway; keep the Workstation on and connected to Tailscale.')
    gateway.probe()
    root = safe(state_root(home,url,agent))
    state_path = root/'state.json'
    state = strict_json(read(state_path)) if state_path.exists() else None
    if state is not None and (state.get('agent') != agent or state.get('url') != url):
        raise Refusal('The connector state belongs to another endpoint; use its original Workstation URL.')
    if check:
        if state is None or not state.get('credential'):
            raise Refusal('Agent and endpoint checks passed; open a Workstation headend pairing window and run Connect without --check.',10)
        private = (vault or PrivateStore()).get(state['credential'])
        gateway.verify(private)
        if agent == 'claude':
            # --check is strictly read-only: do not create the isolated CLI probe.
            # The existing config is checked by MCP internally; no pairing begins.
            print('Claude HTTP expansion was checked during connection; rerun Connect to repeat its isolated client probe.')
        print(agent + ': read-only authentication and eleven-tool checks passed; client version ' + version + '.')
        return
    vault = vault or PrivateStore()
    if state is None:
        ident = secrets.token_hex(16)
        state = {'schema':'deep32.connector.v1','agent':agent,'url':url,'install_id':ident,
                 'server_name':name or 'deep32_alpha_'+ident[:8],
                 'variable':'DEEP32_ALPHA_'+ident[:8].upper()+'_MCP_TOKEN'}
        if profile:
            state['profile_dir'] = str(home)
        atomic(state_path,json_bytes(state))
    if name and state['server_name'] != name:
        raise Refusal('This agent already has a stable server name; reconnect without --name.')
    # Parse all existing config before opening a one-attempt pairing window.
    planned = install_files(agent,home,state)
    if not state.get('credential'):
        prior = recovered_enrolment(root,vault)
        if prior:
            peer, credential = prior
        else:
            print(agent + ': waiting for headend enrolment; open its five-minute Workstation pairing window.')
            # The heartbeat starts after the code prompt, so it never prints into the masked line.
            code = prompt(agent)
            with waiting(agent + ': still waiting for gateway enrolment; keep its pairing window open and Tailscale connected.'):
                peer, credential = enroller(root,host,code,vault)
            code = None
        state.update(peer_id=peer,credential=str(credential))
        atomic(state_path,json_bytes(state))
        fresh = True
    else:
        fresh = False
    token = vault.get(state['credential'])
    print(agent + ': waiting for authenticated MCP checks; restore the Workstation services if this fails.')
    # Right after enrolment the Workstation restarts its gateway to load the new credential, which takes a few
    # seconds; until then the old gateway answers 401. Only a fresh enrolment waits, and for at most 45 seconds.
    deadline = time.monotonic() + (45 if fresh else 0)
    while True:
        try:
            readiness = gateway.verify(token)
            break
        except Refusal as exc:
            if not str(exc).startswith('The gateway refused authentication') or time.monotonic() >= deadline:
                raise
            time.sleep(3)
    print(agent + ': waiting for the installed client connection check; update the agent if its credential mode is unsupported.')
    with waiting(agent + ': still waiting for the client connection check; keep Workstation publication available.'):
        client_check(agent,executable,root,state,token)
    launcher = root/('launch.ps1' if os.name=='nt' else 'launch.sh')
    state.update(executable=executable,version=version,launcher=str(launcher))
    planned[launcher] = launcher_text(state_path)
    count = apply_plan(planned,state,approve)
    if os.name == 'posix':
        os.chmod(launcher,0o700)
    atomic(state_path,json_bytes(state))
    # Fresh connection proves the persisted credential survived the file transaction.
    gateway_factory(url).verify(vault.get(state['credential']))
    print(agent + ': paired; config checked ('+str(count)+' files); gateway authentication passed; eleven tools passed; retrieval '+readiness+'.')
    print('URL: '+url+'; install ID: '+state['install_id']+'.')
    if os.name == 'nt':
        print('Launch: powershell -NoProfile -ExecutionPolicy Bypass -File "'+str(launcher)+'"')
    else:
        import shlex
        print('Launch: '+shlex.quote(str(launcher)))
    print('Actual agent restart/reconnect and revocation acceptance remain required; this result verifies MCP internally.')
    if profile:
        print('This fresh profile needs its own agent sign-in; use this launcher for the agent\'s native sign-in command, then launch again.')


def connect_one(agent,url,host,home,name=None,check=False,**options):
    if check:
        return _connect_one(agent,url,host,home,name,check,**options)
    vault = options.pop('vault',None) or PrivateStore()
    with connection_lock(safe(state_root(home,url,agent)),vault):
        return _connect_one(agent,url,host,home,name,check,vault=vault,**options)


def launch(state_path, args):
    state = strict_json(read(state_path))
    token = PrivateStore().get(state['credential'])
    env = (profile_environment(state['agent'],state['profile_dir'])
           if state.get('profile_dir') else os.environ.copy())
    # Only this child receives the bearer; never persist it in the user environment.
    env[state['variable']] = token
    return subprocess.run([state['executable'],*args],env=env,check=False).returncode


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv[:1] in (['relay'], ['--relay']):
            # The same connector owns relay lifecycle; no direct-MCP credential,
            # ordinary agent home, network enrolment or unrestricted launcher is
            # consulted by the relay branch.
            sys.path.insert(0, str(KIT / 'relay'))
            from deep32_relay.connector import main as relay_main
            return relay_main(argv[1:])
        if argv[:1] == ['--launch']:
            if len(argv)<2:
                raise Refusal('A launcher state path is required; use the launch command printed by Connect.')
            return launch(argv[1],argv[2:])
        parser = argparse.ArgumentParser(description='Pair direct MCP without a token argument.')
        parser.add_argument('--agent',required=True,choices=('codex','claude','both'))
        parser.add_argument('--workstation',required=True)
        parser.add_argument('--name')
        parser.add_argument('--check',action='store_true')
        parser.add_argument('--profile-dir',help='An absolute, user-created empty directory for one isolated agent profile.')
        args = parser.parse_args(argv)
        if args.name and not re.fullmatch('[a-z][a-z0-9_]{2,63}',args.name):
            raise Refusal('The server name must use lowercase letters, digits and underscores; choose a scoped name and run Connect again.')
        if args.name and args.agent=='both':
            raise Refusal('A custom name selects one logical headend; connect each agent separately with a distinct name.')
        if args.profile_dir and args.agent=='both':
            raise Refusal('An isolated profile belongs to one agent; connect each agent separately with a different --profile-dir.',10)
        url,host = endpoint(args.workstation)
        agents = ('codex','claude') if args.agent=='both' else (args.agent,)
        for agent in agents:
            if args.profile_dir:
                home = isolated_profile(agent,args.profile_dir,url,args.check)
                connect_one(agent,url,host,home,args.name,args.check,profile=True)
                continue
            home = Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))) if agent=='codex' else Path(os.environ.get('CLAUDE_CONFIG_DIR',str(Path.home()/'.claude')))
            if not home.is_absolute():
                raise Refusal('The agent home must be absolute; fix its home variable and run Connect again.')
            refuse_original(home)
            connect_one(agent,url,host,safe(home),args.name,args.check)
        return 0
    except Refusal as exc:
        print(str(exc),file=sys.stderr)
        return exc.code
    except (OSError,ValueError,KeyError,UnicodeError):
        print('Connect could not read or commit its local files; repair the agent configuration and run it again.',file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
