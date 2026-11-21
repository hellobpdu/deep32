"""Offline connector tests; all homes and labelled credentials are synthetic."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

WORK = Path(os.environ['DEEP32_CONNECT_TEST_ROOT'])
WORK.mkdir(parents=True,exist_ok=True)
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('deep32_connect',HERE.parent/'connect.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
# Labelled test-only material, never a real credential or host.
SYNTHETIC_TOKEN = 'a5'*32
URL = 'https://synthetic-workstation.example.test/mcp'
HOST = 'synthetic-workstation.example.test'
PEER = 'workstation-123456789abc'


class FixtureVault(c.PrivateStore):
    """Platform stand-in: exercises Windows-shaped encrypted bytes on this host."""
    def __init__(self):
        super().__init__(windows=True)
    def protect_directory(self,path):
        c.safe(path).mkdir(parents=True,exist_ok=True)
    def crypt(self,raw,decrypt=False):
        if decrypt:
            if not raw.startswith(b'SYNTHETIC-DPAPI:'):
                raise c.Refusal('Synthetic protected-store failure.')
            return raw[len(b'SYNTHETIC-DPAPI:'):][::-1]
        return b'SYNTHETIC-DPAPI:'+raw[::-1]


class FakeGateway:
    calls = []
    fail = False
    def __init__(self,url):
        self.url=url
    def probe(self):
        self.calls.append(('probe',self.url))
    def verify(self,token):
        if token != SYNTHETIC_TOKEN:
            raise AssertionError('Wrong synthetic credential.')
        self.calls.append(('verify',self.url))
        if self.fail:
            raise c.Refusal('Synthetic readiness failure.',13)
        return 'ready'


def fixture_joiner(root,host,code,vault):
    """Use the accepted Joiner constructor, handover validator and commit store.

    Only network/TLS exchange is replaced: no sockets, OpenSSL or agent runs.
    The actual protected_received adapter is exercised by c.enrol below.
    """
    pairing,store,wire = c.import_core()
    class FixtureJoiner(pairing.Joiner):
        def join(self):
            handover = wire.handover('gateway',SYNTHETIC_TOKEN,None,'serve-https')
            names = store.save_received(self.state,'gateway',PEER,handover)
            row = store.record('gateway','joiner',PEER,'workstation','12'*32,'34'*32,
                'tailnet',123,peer_address=self.address,peer_port=self.port,
                peer_dns_name=self.peer_dns_name,publish='serve-https',received=names)
            store.save_record(self.state,row)
            return {'peer_id':PEER,'received':names}
    resolve = lambda *args:[(2,1,6,'',('100.64.0.9',7454))]
    return c.enrol(root,host,code,vault,resolve=resolve,joiner_class=FixtureJoiner)


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='synthetic-connect-',dir=WORK)
        self.root = Path(self.temporary.name)
        self.vault = FixtureVault()
        self.out = io.StringIO()
        self.capture = contextlib.redirect_stdout(self.out)
        self.capture.__enter__()
        FakeGateway.calls=[]
        FakeGateway.fail=False

    def tearDown(self):
        self.capture.__exit__(None,None,None)
        self.temporary.cleanup()

    def options(self,**updates):
        value = dict(vault=self.vault,detect=lambda agent,**kwargs:('/synthetic/bin/'+agent,'synthetic '+agent+' 1.0'),
            gateway_factory=FakeGateway,prompt=lambda agent:'ABCDE-FGHIJ',enroller=fixture_joiner,
            approve=lambda paths:True,client_check=lambda *args:None)
        value.update(updates)
        return value

    def connect(self,agent='codex',**updates):
        home = self.root/('.codex' if agent=='codex' else '.claude')
        c.connect_one(agent,URL,HOST,home,**self.options(**updates))
        statepath=c.state_root(home,URL,agent)/'state.json'
        return home,json.loads(statepath.read_bytes())

    def test_codex_merge_preserves_comments_and_other_entries(self):
        raw=b'# user comment\nmodel = "synthetic"\n[mcp_servers.other]\nurl = "https://elsewhere.example.test/mcp" # retained\n'
        result=c.codex_merge(raw,'deep32_alpha_12345678',URL,'DEEP32_ALPHA_12345678_MCP_TOKEN')
        self.assertTrue(result.startswith(raw))
        value=c.tomllib.loads(result.decode())
        self.assertEqual(value['mcp_servers']['other']['url'],'https://elsewhere.example.test/mcp')
        self.assertEqual(value['mcp_servers']['deep32_alpha_12345678']['bearer_token_env_var'],'DEEP32_ALPHA_12345678_MCP_TOKEN')
        self.assertEqual(c.codex_merge(result,'deep32_alpha_12345678',URL,'DEEP32_ALPHA_12345678_MCP_TOKEN'),result)

    def test_codex_duplicate_tables_and_conflicts_refused(self):
        for raw in (b'[mcp_servers.x]\nurl="one"\n[mcp_servers.x]\nurl="two"\n',
                    b'mcp_servers={other={url="https://example.test/mcp"}}\n',
                    b'[mcp_servers.deep32_alpha_12345678]\nurl="other"\n'):
            with self.subTest(raw=raw),self.assertRaises(c.Refusal):
                c.codex_merge(raw,'deep32_alpha_12345678',URL,'VARIABLE')

    def test_claude_literal_environment_reference_and_unrelated_entries(self):
        raw=b'{"userSetting":17,"mcpServers":{"other":{"type":"stdio","command":"synthetic"}}}'
        result=c.claude_merge(raw,'deep32_alpha_12345678',URL,'DEEP32_ALPHA_12345678_MCP_TOKEN')
        obj=json.loads(result)
        self.assertEqual(obj['userSetting'],17)
        self.assertEqual(obj['mcpServers']['other']['command'],'synthetic')
        self.assertEqual(obj['mcpServers']['deep32_alpha_12345678']['headers']['Authorization'],'Bearer ${DEEP32_ALPHA_12345678_MCP_TOKEN}')
        self.assertNotIn(SYNTHETIC_TOKEN.encode(),result)

    def test_claude_duplicate_keys_and_scoped_conflicts_refused(self):
        for raw in (b'{"mcpServers":{},"mcpServers":{}}',b'{"mcpServers":{"owned":{"url":"other"}}}',b'[]'):
            with self.subTest(raw=raw),self.assertRaises(c.Refusal):
                c.claude_merge(raw,'owned',URL,'VARIABLE')

    def test_url_refuses_injection_credentials_redirectish_and_plaintext(self):
        for value in ('http://host/mcp','https://user:password@host/mcp','https://host/mcp?token=no',
                      'https://host/mcp#fragment','https://host:99999/mcp','https://host/other','https://host/mcp\n'):
            with self.subTest(value=value),self.assertRaises(c.Refusal):
                c.endpoint(value)
        self.assertEqual(c.endpoint(URL),(URL,HOST))

    def test_instruction_markers_preserve_user_text_and_refuse_ambiguity(self):
        raw=b'user before\n'
        result=c.merge_block(raw,'owned','1234')
        result=c.merge_block(result,'updated','1234')
        self.assertTrue(result.startswith(raw))
        self.assertEqual(result.count(b'<!-- deep32:1234:begin -->'),1)
        self.assertIn(b'updated',result)
        with self.assertRaises(c.Refusal):
            c.merge_block(b'<!-- deep32:1234:begin -->','x','1234')

    def test_plan_decline_is_no_mutation(self):
        path=self.root/'config'
        path.write_bytes(b'old')
        with self.assertRaises(c.Refusal) as raised:
            c.apply_plan({path:b'new'}, {}, lambda paths:False)
        self.assertEqual(raised.exception.code,10)
        self.assertEqual(path.read_bytes(),b'old')
        self.assertEqual(list(self.root.glob('*deep32-backup*')),[])

    def test_plan_backs_up_and_rolls_back_completed_files(self):
        first,second=self.root/'a',self.root/'b'
        first.write_bytes(b'old')
        original=c.atomic
        def fail(path,raw,*args,**kwargs):
            if Path(path)==second:
                raise OSError('synthetic failure')
            return original(path,raw,*args,**kwargs)
        with mock.patch.object(c,'atomic',side_effect=fail),self.assertRaises(OSError):
            c.apply_plan({first:b'new',second:b'new'}, {},lambda paths:True)
        self.assertEqual(first.read_bytes(),b'old')
        self.assertFalse(second.exists())
        self.assertEqual(next(self.root.glob('a.deep32-backup-*')).read_bytes(),b'old')

    def test_accepted_joiner_seam_receives_protected_token_and_committed_peer(self):
        root=self.root/'enrol'
        self.vault.protect_directory(root)
        peer,path=fixture_joiner(root,HOST,'ABCDE-FGHIJ',self.vault)
        self.assertEqual(peer,PEER)
        self.assertEqual(self.vault.get(path),SYNTHETIC_TOKEN)
        self.assertNotIn(SYNTHETIC_TOKEN.encode(),path.read_bytes())
        _,store,_=c.import_core()
        rows=store.list_peers(root)
        self.assertEqual((rows[0]['kind'],rows[0]['side'],rows[0]['received']),('gateway','joiner',['mcp-token']))

    def test_enrolment_refuses_non_tailnet_or_ambiguous_resolution(self):
        for addresses in (['192.0.2.10'],['100.64.0.10','100.64.0.11']):
            resolve=lambda *args:[(2,1,6,'',(a,7454)) for a in addresses]
            with self.subTest(addresses=addresses),self.assertRaises(c.Refusal) as raised:
                c.enrol(self.root,HOST,'ABCDE-FGHIJ',self.vault,resolve=resolve)
            self.assertEqual(raised.exception.code,14)

    def test_private_store_failure_keeps_core_error_and_restores_storage_seam(self):
        _,store,_=c.import_core()
        original=store.save_received
        with mock.patch.object(self.vault,'put',side_effect=c.Refusal('Synthetic store failure.')):
            with self.assertRaises(c.Refusal) as raised:
                fixture_joiner(self.root,HOST,'ABCDE-FGHIJ',self.vault)
        self.assertEqual(raised.exception.code,13)
        self.assertIs(store.save_received,original)
        self.assertEqual(store.list_peers(self.root),[])

    def test_config_parse_failure_does_not_pair_or_change_existing(self):
        home=self.root/'.codex'
        home.mkdir()
        config=home/'config.toml'
        original=b'[mcp_servers.bad]\n[mcp_servers.bad]\n'
        config.write_bytes(original)
        enroller=mock.Mock(side_effect=AssertionError('must not pair'))
        with self.assertRaises(c.Refusal):
            c.connect_one('codex',URL,HOST,home,**self.options(enroller=enroller))
        self.assertEqual(config.read_bytes(),original)
        enroller.assert_not_called()

    def test_connect_writes_both_configs_distinct_ids_and_no_token_in_output(self):
        codex,one=self.connect()
        claude,two=self.connect('claude')
        self.assertNotEqual(one['install_id'],two['install_id'])
        self.assertNotEqual(one['variable'],two['variable'])
        self.assertNotEqual(one['credential'],two['credential'])
        self.assertEqual(len(FakeGateway.calls),6)
        self.assertNotIn(SYNTHETIC_TOKEN,self.out.getvalue())
        self.assertNotIn('ABCDE-FGHIJ',self.out.getvalue())
        for path in (codex/'config.toml',self.root/'.claude.json',Path(one['launcher']),Path(two['launcher'])):
            self.assertNotIn(SYNTHETIC_TOKEN.encode(),path.read_bytes())
        self.assertIn(one['variable'].encode(),(codex/'config.toml').read_bytes())

    def test_rerun_stable_and_modified_skill_requires_merge_consent(self):
        home,state=self.connect()
        skill=home/'skills'/('deep32_'+state['install_id'][:8])/'SKILL.md'
        self.assertIn(('mcp__'+state['server_name']+'__').encode(),skill.read_bytes())
        skill.write_bytes(skill.read_bytes()+b'\nuser edit\n')
        with self.assertRaises(c.Refusal):
            self.connect(approve=lambda paths:False,prompt=lambda agent:self.fail('must resume'))
        self.assertTrue(skill.read_bytes().endswith(b'user edit\n'))
        _,updated=self.connect(prompt=lambda agent:self.fail('must resume'))
        self.assertEqual(updated['install_id'],state['install_id'])
        self.assertEqual(updated['credential'],state['credential'])

    def test_all_six_codex_skills_and_claude_references_resolve_after_scoping(self):
        for agent in ('codex','claude'):
            home,state=self.connect(agent)
            skills=list((home/'skills').iterdir())
            self.assertEqual(len(skills),6 if agent=='codex' else 1)
            for path in (home/'skills').rglob('*'):
                if path.is_file() and path.suffix in ('.md','.yaml'):
                    text=path.read_text(encoding='utf-8')
                    self.assertNotIn('${CODEX_HOME}',text)
                    self.assertNotIn('${CLAUDE_HOME}',text)
                    self.assertNotIn('${CLAUDE_SKILL_DIR}',text)
                    self.assertNotIn('mcp__deep32__',text)
                    self.assertFalse(re.search(r'\$deep32(?:-(?:configure|describe|design|troubleshoot|verify))?(?![A-Za-z0-9_-])',text))
                    for target in re.findall('`('+re.escape(home.as_posix())+r'/skills/[^`\n]+)`',text):
                        self.assertTrue(Path(target).exists(),(path,target))
            if agent=='claude':
                settings=json.loads((home/'settings.json').read_bytes())
                self.assertEqual(set(settings['permissions']['allow']),{'mcp__'+state['server_name']+'__'+tool for tool in c.TOOLS})

    def test_second_agent_failure_keeps_first_complete_and_resumes(self):
        _,first=self.connect()
        def fail(*args):
            raise c.Refusal('Synthetic expired window.',11)
        with self.assertRaises(c.Refusal):
            self.connect('claude',enroller=fail)
        self.assertTrue(Path(first['launcher']).exists())
        self.assertEqual(self.vault.get(first['credential']),SYNTHETIC_TOKEN)
        _,second=self.connect('claude')
        self.assertNotEqual(first['install_id'],second['install_id'])

    def test_interrupted_after_core_commit_recovers_without_second_window(self):
        home=self.root/'.codex'
        root=c.state_root(home,URL,'codex')
        self.vault.protect_directory(root)
        fixture_joiner(root,HOST,'ABCDE-FGHIJ',self.vault)
        _,state=self.connect(prompt=lambda agent:self.fail('must recover'))
        self.assertEqual(state['peer_id'],PEER)

    def test_check_is_read_only_and_never_pairs(self):
        home,state=self.connect()
        before={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        c.connect_one('codex',URL,HOST,home,check=True,**self.options(prompt=lambda agent:self.fail('must not prompt'),enroller=lambda *args:self.fail('must not pair')))
        after={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before,after)
        empty=self.root/'empty'
        with self.assertRaises(c.Refusal):
            c.connect_one('codex',URL,HOST,empty,check=True,**self.options())
        self.assertFalse(empty.exists())

    def test_read_rejects_hardlinked_configuration(self):
        first,second=self.root/'a',self.root/'b'
        first.write_bytes(b'synthetic')
        os.link(first,second)
        with self.assertRaises(c.Refusal):
            c.read(second)

    def test_nested_connect_lock_refuses_second_operation(self):
        root=self.root/'locked'
        with c.connection_lock(root,self.vault):
            with self.assertRaises(c.Refusal) as raised:
                with c.connection_lock(root,self.vault):
                    self.fail('second lock was acquired')
            self.assertEqual(raised.exception.code,10)

    def test_claude_client_probe_uses_env_only_and_hides_all_output(self):
        root=self.root/'probe'
        root.mkdir()
        state={'server_name':'deep32_alpha_12345678','variable':'DEEP32_ALPHA_12345678_MCP_TOKEN','url':URL}
        def run(argv,**kwargs):
            self.assertEqual(argv,['synthetic-claude','mcp','list'])
            self.assertNotIn(SYNTHETIC_TOKEN,' '.join(argv))
            self.assertEqual(kwargs['env'][state['variable']],SYNTHETIC_TOKEN)
            config=Path(kwargs['env']['CLAUDE_CONFIG_DIR'])/'.claude.json'
            self.assertNotIn(SYNTHETIC_TOKEN.encode(),config.read_bytes())
            return subprocess.CompletedProcess(argv,0,(state['server_name']+': '+URL+' (HTTP) - Connected\n'+SYNTHETIC_TOKEN).encode(),b'')
        with mock.patch.object(c.subprocess,'run',side_effect=run):
            c.verify_client('claude','synthetic-claude',root,state,SYNTHETIC_TOKEN)
        self.assertNotIn(SYNTHETIC_TOKEN,self.out.getvalue())
        self.assertEqual(list(root.iterdir()),[])

    def test_claude_unconfirmed_connection_fails_closed(self):
        root=self.root/'probe'
        root.mkdir()
        state={'server_name':'owned','variable':'VARIABLE','url':URL}
        for output in (b'owned: failed',b'owned: Connected',('owned: '+URL+' - Failed').encode()):
            result=subprocess.CompletedProcess([],0,output,b'')
            with mock.patch.object(c.subprocess,'run',return_value=result),self.assertRaises(c.Refusal) as raised:
                c.verify_client('claude','synthetic',root,state,SYNTHETIC_TOKEN)
            self.assertIn('agent-credential-mode-unsupported',str(raised.exception))

    def test_gateway_verifies_initialize_tools_status_internally(self):
        gateway=c.Gateway(URL)
        calls=[]
        def rpc(method,params=None,token=None,ident=1):
            self.assertEqual(token,SYNTHETIC_TOKEN)
            calls.append(method)
            if method=='tools/list':
                return {'tools':[{'name':name} for name in c.TOOLS]}
            if method=='tools/call':
                return {'content':[{'type':'text','text':json.dumps({'ok':True,'degraded':[]})}]}
            return {}
        gateway.rpc=rpc
        self.assertEqual(gateway.verify(SYNTHETIC_TOKEN),'ready')
        self.assertEqual(calls,['initialize','tools/list','tools/call'])

    def test_gateway_rejects_authless_access_wrong_tools_and_not_ready(self):
        gateway=c.Gateway(URL)
        gateway.rpc=lambda *args,**kwargs:{}
        with self.assertRaises(c.Refusal):
            gateway.probe()
        with self.assertRaises(c.Refusal):
            gateway.verify(SYNTHETIC_TOKEN)
        def rpc(method,*args):
            return {'tools':[{'name':n} for n in c.TOOLS]} if method=='tools/list' else {'content':[{'type':'text','text':'{"ok":false}'}]}
        gateway.rpc=rpc
        with self.assertRaises(c.Refusal):
            gateway.verify(SYNTHETIC_TOKEN)

    def test_gateway_bearer_is_header_never_url_or_argv_and_redirect_refused(self):
        gateway=c.Gateway(URL)
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,size):return b'{"jsonrpc":"2.0","id":1,"result":{}}'
        def opening(request,timeout):
            self.assertEqual(request.full_url,URL)
            self.assertEqual(request.get_header('Authorization'),'Bearer '+SYNTHETIC_TOKEN)
            self.assertNotIn(SYNTHETIC_TOKEN.encode(),request.data)
            return Response()
        gateway.opener.open=opening
        gateway.rpc('initialize',{},SYNTHETIC_TOKEN)
        with self.assertRaises(c.Refusal):
            c.NoRedirect().redirect_request(None,None,302,'x',{},'https://elsewhere.example.test/mcp')

    def test_platform_dpapi_uses_current_user_not_machine_and_no_plain_file(self):
        path=self.root/'private'/'mcp-token'
        self.vault.put(path,SYNTHETIC_TOKEN)
        self.assertNotIn(SYNTHETIC_TOKEN.encode(),path.read_bytes())
        self.assertEqual(self.vault.get(path),SYNTHETIC_TOKEN)
        source=(HERE.parent/'connect.py').read_text(encoding='utf-8')
        self.assertIn('CRYPTPROTECT_UI_FORBIDDEN',source)
        self.assertNotIn('CRYPTPROTECT_LOCAL_MACHINE',source)

    def test_windows_acl_adapter_carries_only_path_and_protects_current_user(self):
        import base64
        def run(argv,**kwargs):
            self.assertEqual(argv[:4],['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand'])
            script=base64.b64decode(argv[4]).decode('utf-16le')
            self.assertIn('SetAccessRuleProtection($true,$false)',script)
            self.assertIn('WindowsIdentity]::GetCurrent().User',script)
            self.assertIn('SetOwner($u)',script)
            self.assertNotIn(SYNTHETIC_TOKEN,script)
            self.assertIs(kwargs['stdout'],subprocess.DEVNULL)
            return subprocess.CompletedProcess(argv,0)
        with mock.patch.object(c.subprocess,'run',side_effect=run):
            c.PrivateStore(windows=True).protect_directory(self.root/'acl')

    def test_windows_dpapi_api_uses_ui_forbidden_and_frees_result(self):
        allocated=[]
        calls=[]
        class Function:
            def __init__(self,name):self.name=name
            def __call__(self,*args):
                calls.append((self.name,args))
                if self.name=='LocalFree':return None
                selftest.assertEqual(args[5],1)
                source=args[0]._obj
                raw=c.ctypes.string_at(source.data,source.size)
                selftest.assertEqual(raw,b'synthetic')
                buffer=(c.ctypes.c_ubyte*len(raw)).from_buffer_copy(raw)
                allocated.append(buffer)
                result=args[6]._obj
                result.size=len(raw)
                result.data=c.ctypes.cast(buffer,c.ctypes.POINTER(c.ctypes.c_ubyte))
                return True
        selftest=self
        crypt=mock.Mock(CryptProtectData=Function('protect'),CryptUnprotectData=Function('unprotect'))
        kernel=mock.Mock(LocalFree=Function('LocalFree'))
        def library(name,**kwargs):return crypt if name=='crypt32' else kernel
        with mock.patch.object(c.ctypes,'WinDLL',side_effect=library,create=True):
            store=c.PrivateStore(windows=True)
            self.assertEqual(store.crypt(b'synthetic'),b'synthetic')
            self.assertEqual(store.crypt(b'synthetic',decrypt=True),b'synthetic')
        self.assertEqual([name for name,args in calls],['protect','LocalFree','unprotect','LocalFree'])
        self.assertTrue(all(bytes(buffer)==b'\0'*9 for buffer in allocated))

    def test_linux_directory_adapter_restricts_permissions(self):
        directory=self.root/'linux'
        directory.mkdir()
        with mock.patch.object(c.os,'geteuid',create=True,return_value=directory.stat().st_uid),mock.patch.object(c.os,'chmod') as chmod:
            c.PrivateStore(windows=False).protect_directory(directory)
        chmod.assert_called_once_with(directory,0o700)

    def test_posix_permissions_checked_on_open_descriptor(self):
        fake=mock.Mock(st_mode=stat.S_IFREG|0o644,st_uid=100,st_nlink=1,st_size=4)
        path=self.root/'synthetic-token'
        path.write_bytes(b'test')
        with mock.patch.object(c.os,'fstat',return_value=fake),mock.patch.object(c.os,'geteuid',create=True,return_value=100):
            with self.assertRaises(c.Refusal):
                c.read(path,private=True)

    def test_launcher_injects_only_child_environment_and_preserves_exit(self):
        home,state=self.connect()
        before=dict(os.environ)
        result=subprocess.CompletedProcess([],7)
        with mock.patch.object(c,'PrivateStore',return_value=self.vault),mock.patch.object(c.subprocess,'run',return_value=result) as run:
            status=c.launch(c.state_root(home,URL,'codex')/'state.json',['synthetic-argument'])
        self.assertEqual(status,7)
        self.assertEqual(dict(os.environ),before)
        self.assertEqual(run.call_args.args[0],[state['executable'],'synthetic-argument'])
        self.assertEqual(run.call_args.kwargs['env'][state['variable']],SYNTHETIC_TOKEN)
        self.assertNotIn(SYNTHETIC_TOKEN,' '.join(run.call_args.args[0]))

    def test_isolated_profile_requires_user_created_empty_absolute_directory(self):
        with self.assertRaises(c.Refusal):
            c.isolated_profile('codex','relative',URL)
        missing=self.root/'missing-profile'
        with self.assertRaises(c.Refusal):
            c.isolated_profile('codex',missing,URL)
        self.assertFalse(missing.exists())
        for name,contents in (('config.toml',b''),('.claude.json',b'{}'),
                              ('unrelated.txt',b'synthetic'),('.hidden',None)):
            profile=self.root/name.replace('.','-')
            profile.mkdir()
            if contents is None:
                (profile/name).mkdir()
            else:
                (profile/name).write_bytes(contents)
            with self.subTest(name=name),self.assertRaises(c.Refusal):
                c.isolated_profile('codex',profile,URL)
            self.assertEqual(list(profile.iterdir()),[profile/name])
        empty=self.root/'new empty profile'
        empty.mkdir()
        self.assertEqual(c.isolated_profile('codex',empty,URL),empty)
        self.assertEqual(list(empty.iterdir()),[])

    def test_isolated_main_does_not_inspect_or_modify_original_homes(self):
        original=self.root/'synthetic-original'
        original.mkdir()
        original_codex=original/'codex'
        original_claude=original/'claude'
        original_local=original/'local'
        for path in (original_codex,original_claude,original_local):
            path.mkdir()
            (path/'unchanged-synthetic.txt').write_bytes(b'SYNTHETIC ORIGINAL HOME: never open')
        before={str(p):p.read_bytes() for p in original.rglob('*') if p.is_file()}
        real_stat,real_iterdir,real_open=Path.stat,Path.iterdir,c.os.open
        def guard(path):
            candidate=Path(os.path.abspath(path))
            if candidate==original or original in candidate.parents:
                raise AssertionError('Original profile was inspected: '+str(path))
        def guarded_stat(path,*args,**kwargs):
            guard(path)
            return real_stat(path,*args,**kwargs)
        def guarded_iterdir(path):
            guard(path)
            return real_iterdir(path)
        def guarded_open(path,*args,**kwargs):
            guard(path)
            return real_open(path,*args,**kwargs)
        real_connect=c.connect_one
        def connect(agent,url,host,home,name,check,**kwargs):
            self.assertTrue(kwargs['profile'])
            return real_connect(agent,url,host,home,name,check,**kwargs,**self.options())
        for agent in ('codex','claude'):
            profile=self.root/('isolated '+agent)
            profile.mkdir()
            with mock.patch.dict(os.environ,{'CODEX_HOME':str(original_codex),
                    'CLAUDE_CONFIG_DIR':str(original_claude),'LOCALAPPDATA':str(original_local)}), \
                    mock.patch.object(Path,'home',side_effect=AssertionError('default home was requested')), \
                    mock.patch.object(c,'refuse_original',side_effect=AssertionError('original markers were requested')), \
                    mock.patch.object(Path,'stat',guarded_stat),mock.patch.object(Path,'iterdir',guarded_iterdir), \
                    mock.patch.object(c.os,'open',side_effect=guarded_open),mock.patch.object(c,'connect_one',side_effect=connect):
                self.assertEqual(c.main(['--agent',agent,'--workstation',URL,'--profile-dir',str(profile)]),0)
            state=json.loads((c.state_root(profile,URL,agent)/'state.json').read_bytes())
            self.assertEqual(state['profile_dir'],str(profile))
            self.assertTrue((profile/('config.toml' if agent=='codex' else '.claude.json')).is_file())
            self.assertFalse((self.root/'.claude.json').exists())
        self.assertEqual(before,{str(p):p.read_bytes() for p in original.rglob('*') if p.is_file()})

    def test_isolated_both_agents_refused_before_connect(self):
        profile=self.root/'empty-profile'
        profile.mkdir()
        with mock.patch.object(c,'connect_one') as connect,contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(c.main(['--agent','both','--workstation',URL,'--profile-dir',str(profile)]),10)
        connect.assert_not_called()
        self.assertEqual(list(profile.iterdir()),[])

    def test_isolated_version_probe_sets_profile_and_defers_native_sign_in(self):
        for agent in ('codex','claude'):
            env=c.profile_environment(agent,self.root/agent)
            result=subprocess.CompletedProcess([],0,b'synthetic agent 1.0',b'')
            with mock.patch.object(c.shutil,'which',return_value=str(self.root/'synthetic-agent')), \
                    mock.patch.object(c.subprocess,'run',return_value=result) as run:
                c.detect_agent(agent,env=env,require_login=False)
            self.assertEqual(run.call_count,1)
            self.assertEqual(run.call_args.args[0][-1],'--version')
            self.assertEqual(run.call_args.kwargs['env']['CODEX_HOME' if agent=='codex' else 'CLAUDE_CONFIG_DIR'],str(self.root/agent))

    def test_legacy_detection_still_requires_native_sign_in(self):
        with mock.patch.object(c.shutil,'which',return_value=str(self.root/'synthetic-agent')), \
                mock.patch.object(c.subprocess,'run',side_effect=[
                    subprocess.CompletedProcess([],0,b'synthetic agent 1.0',b''),
                    subprocess.CompletedProcess([],1)]) as run,self.assertRaises(c.Refusal):
            c.detect_agent('codex')
        self.assertEqual(run.call_args.args[0][-2:],['login','status'])

    def test_isolated_launcher_overrides_inherited_home_for_child_only(self):
        for agent in ('codex','claude'):
            profile=self.root/('isolated-'+agent)
            profile.mkdir()
            c.connect_one(agent,URL,HOST,profile,profile=True,**self.options())
            statepath=c.state_root(profile,URL,agent)/'state.json'
            variable='CODEX_HOME' if agent=='codex' else 'CLAUDE_CONFIG_DIR'
            with mock.patch.dict(os.environ,{variable:'SYNTHETIC ORIGINAL HOME'}):
                before=dict(os.environ)
                with mock.patch.object(c,'PrivateStore',return_value=self.vault), \
                        mock.patch.object(c.subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as run:
                    self.assertEqual(c.launch(statepath,['synthetic-native-sign-in']),0)
                self.assertEqual(dict(os.environ),before)
            self.assertEqual(run.call_args.kwargs['env'][variable],str(profile))
            self.assertNotIn(SYNTHETIC_TOKEN,' '.join(run.call_args.args[0]))

    def test_isolated_check_accepts_only_matching_owned_state_and_is_read_only(self):
        for agent in ('codex','claude'):
            profile=self.root/('check-'+agent)
            profile.mkdir()
            c.connect_one(agent,URL,HOST,profile,profile=True,**self.options())
            before={str(p):p.read_bytes() for p in profile.rglob('*') if p.is_file()}
            self.assertEqual(c.isolated_profile(agent,profile,URL,check=True),profile)
            detect=mock.Mock(return_value=('/synthetic/bin/'+agent,'synthetic 1.0'))
            c.connect_one(agent,URL,HOST,profile,check=True,profile=True,**self.options(detect=detect))
            self.assertTrue(detect.call_args.kwargs['require_login'])
            self.assertEqual(before,{str(p):p.read_bytes() for p in profile.rglob('*') if p.is_file()})
            with self.assertRaises(c.Refusal):
                c.isolated_profile(agent,profile,URL)
            with self.assertRaises(c.Refusal):
                c.isolated_profile(agent,profile,URL.replace('synthetic-workstation','other-synthetic'),check=True)
        unowned=self.root/'unowned-check'
        unowned.mkdir()
        (unowned/'config.toml').write_bytes(b'# synthetic existing config\n')
        with self.assertRaises(c.Refusal):
            c.isolated_profile('codex',unowned,URL,check=True)

    def test_isolated_profile_rejects_link_without_mutating_target(self):
        target=self.root/'synthetic-target'
        target.mkdir()
        link=self.root/'synthetic-link'
        # A stand-in covers platforms where creating a symlink requires privilege.
        original=Path.is_symlink
        with mock.patch.object(Path,'is_symlink',lambda path: path==link or original(path)):
            with self.assertRaises(c.Refusal):
                c.isolated_profile('codex',link,URL)
        self.assertEqual(list(target.iterdir()),[])


PROMPT='Workstation one-time code for claude (hidden, 10 characters): '
DOT='\u2022'
# Labelled synthetic one-time code in the C8 alphabet; never a real window's code.
SYNTHETIC_CODE='QZ7XK2PA5M'


class ScriptTerminal:
    """Keys come from a script; records what the console would show."""
    def __init__(self,keys,windows=False):
        self.keys,self.windows,self.dot=list(keys),windows,DOT
        self.shown=[]
        self.entered=self.restored=0
    def __enter__(self):
        self.entered+=1
        return self
    def __exit__(self,*unused):
        self.restored+=1
    def read(self):
        return self.keys.pop(0)
    def write(self,text):
        self.shown.append(text)


class Console:
    def __init__(self,encoding='utf-8'):
        self.encoding=encoding
    def isatty(self):
        return True
    def fileno(self):
        return 55


class FakeTermios:
    ECHO,ICANON,ISIG=0o10,0o2,0o1
    VMIN,VTIME=6,5
    TCSAFLUSH=2
    def __init__(self):
        self.original=[0o2400,0o5,0o277,self.ECHO|self.ICANON|self.ISIG|0o40000,0o17,0o17,[b'\x03']*32]
        self.mode=self.copy(self.original)
        self.sets=[]
    @staticmethod
    def copy(value):
        return list(value[:6])+[list(value[6])]
    def tcgetattr(self,fd):
        assert fd==55
        return self.copy(self.mode)
    def tcsetattr(self,fd,when,attributes):
        assert fd==55 and when==self.TCSAFLUSH
        self.sets.append(self.copy(attributes))
        self.mode=self.copy(attributes)


try:
    import msvcrt as REAL_MSVCRT
except ImportError:
    REAL_MSVCRT=None


class FakeMsvcrt:
    def __init__(self,keys):
        self.keys,self.shown=list(keys),[]
    def getwch(self):
        return self.keys.pop(0)
    def putwch(self,char):
        assert len(char)==1
        self.shown.append(char)
    def __getattr__(self,name):
        if REAL_MSVCRT is None:
            raise AttributeError(name)
        return getattr(REAL_MSVCRT,name)


class MaskedCodeTests(unittest.TestCase):
    setUp=ConnectorTests.setUp
    tearDown=ConnectorTests.tearDown
    options=ConnectorTests.options

    def masked(self,keys,windows=False):
        terminal=ScriptTerminal(keys,windows)
        with mock.patch.object(c.sys,'stdin',Console()):
            try:
                return c.hidden_code('claude',terminal),terminal
            finally:
                self.assertEqual((terminal.entered,terminal.restored),(1,1))

    def after_prompt(self,shown):
        text=''.join(shown)
        self.assertTrue(text.startswith(PROMPT))
        return text[len(PROMPT):]

    def test_prompt_says_hidden_and_shows_one_dot_per_character(self):
        value,terminal=self.masked(list(SYNTHETIC_CODE)+['\r'])
        self.assertEqual(value,SYNTHETIC_CODE)
        self.assertEqual(''.join(terminal.shown),PROMPT+DOT*10+'\n')

    def test_backspace_and_clear_edit_the_hidden_value(self):
        keys=list('QZ7XX')+['\x7f']+list('K2PA')+['\b','\b']+list('PA5M')+['\r']
        value,terminal=self.masked(keys)
        self.assertEqual(value,SYNTHETIC_CODE)
        rest=self.after_prompt(terminal.shown)
        self.assertEqual(rest.count(DOT),13)
        self.assertEqual(rest.count('\b \b'),3)
        self.assertEqual(rest.replace(DOT,'').replace('\b \b',''),'\n')
        value,terminal=self.masked(list('WRONG')+['\x15']+list(SYNTHETIC_CODE)+['\n'])
        self.assertEqual(value,SYNTHETIC_CODE)
        self.assertIn(DOT*5+'\b \b'*5+DOT*10,self.after_prompt(terminal.shown))

    def test_cursor_and_function_keys_are_ignored(self):
        posix=list('QZ')+['\x1b','[','1',';','5','D']+list('7X')+['\x1b','O','P']+['\r']
        self.assertEqual(self.masked(posix)[0],'QZ7X')
        windows=list('QZ')+['\xe0','K']+list('7X')+['\x00',';','\x1b']+['\r']
        self.assertEqual(self.masked(windows,windows=True)[0],'QZ7X')

    def test_ctrl_c_and_end_of_input_restore_the_terminal(self):
        with self.assertRaises(KeyboardInterrupt):
            self.masked(list('QZ')+['\x03'])
        with self.assertRaises(EOFError):
            self.masked(['\x04'])
        self.assertEqual(self.masked(list('QZ')+['\x04','\x1a']+list('7')+['\r'])[0],'QZ7')

    def test_overlong_input_is_capped(self):
        value,terminal=self.masked(['A']*80+['\r'])
        self.assertEqual(value,'A'*c.CODE_INPUT_MAX)
        self.assertEqual(self.after_prompt(terminal.shown),DOT*c.CODE_INPUT_MAX+'\n')

    def test_no_console_is_refused_before_any_prompt(self):
        terminal=ScriptTerminal(['\r'])
        class Pipe(Console):
            def isatty(self):
                return False
        with mock.patch.object(c.sys,'stdin',Pipe()), self.assertRaises(c.Refusal):
            c.hidden_code('claude',terminal)
        self.assertEqual((terminal.entered,terminal.shown),(0,[]))

    def test_connect_never_shows_prints_logs_or_stores_the_code(self):
        terminal=ScriptTerminal(list(SYNTHETIC_CODE)+['\r'])
        order,received,errors=[],[],io.StringIO()
        def prompt(agent):
            order.append('prompt')
            return c.hidden_code(agent,terminal)
        def enroller(root,host,code,vault):
            received.append(code)
            return fixture_joiner(root,host,code,vault)
        @contextlib.contextmanager
        def waiting(message):
            order.append('heartbeat')
            yield
        home=self.root/'.claude'
        with mock.patch.object(c.sys,'stdin',Console()),mock.patch.object(c,'waiting',waiting),\
                contextlib.redirect_stderr(errors),self.assertNoLogs(level='DEBUG'):
            c.connect_one('claude',URL,HOST,home,**self.options(prompt=prompt,enroller=enroller))
        self.assertEqual(received,[SYNTHETIC_CODE])
        # The heartbeat starts after the prompt, so it never prints into the masked line.
        self.assertEqual(order[0],'prompt')
        self.assertIn('heartbeat',order)
        self.assertEqual(''.join(terminal.shown),PROMPT+DOT*10+'\n')
        self.assertNotIn(SYNTHETIC_CODE,self.out.getvalue()+errors.getvalue())
        for path in self.root.rglob('*'):
            if path.is_file():
                self.assertNotIn(SYNTHETIC_CODE.encode('ascii'),path.read_bytes(),path.name)

    def posix(self,data,*,tty=True,encoding='utf-8'):
        termios,writes,closed,errors=FakeTermios(),[],[],io.StringIO()
        def opened(path,flags):
            self.assertEqual(path,'/dev/tty')
            if not tty:
                raise OSError('synthetic: no controlling terminal')
            return 55
        def read(fd,size):
            self.assertEqual((fd,size),(55,1))
            key=data.pop(0)
            if isinstance(key,type):
                raise key()
            return key
        def write(fd,raw):
            self.assertEqual(fd,55)
            writes.append(raw)
            return len(raw)
        patches=(mock.patch.dict(sys.modules,{'termios':termios}),mock.patch.object(c.os,'open',opened),
                 mock.patch.object(c.os,'read',read),mock.patch.object(c.os,'write',write),
                 mock.patch.object(c.os,'close',closed.append),mock.patch.object(c.sys,'stdin',Console(encoding)),
                 contextlib.redirect_stderr(errors))
        return termios,writes,closed,errors,patches

    def run_posix(self,patches):
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            return c.masked_input(PROMPT,c.Terminal(windows=False))

    def test_posix_terminal_hides_keys_and_restores_its_settings(self):
        termios,writes,closed,_,patches=self.posix([b'Q',b'Z',b'7',b'\x7f',b'X',b'\r'])
        self.assertEqual(self.run_posix(patches),'QZX')
        shown=b''.join(writes).decode('utf-8')
        self.assertEqual(shown,PROMPT+DOT*3+'\b \b'+DOT+'\n')
        changed=termios.sets[0]
        self.assertEqual(changed[3]&(FakeTermios.ECHO|FakeTermios.ICANON),0)
        self.assertTrue(changed[3]&FakeTermios.ISIG)
        self.assertEqual((changed[6][FakeTermios.VMIN],changed[6][FakeTermios.VTIME]),(1,0))
        self.assertEqual(termios.mode,termios.original)
        self.assertEqual(closed,[55])

    def test_posix_ctrl_c_signal_restores_its_settings(self):
        termios,writes,closed,_,patches=self.posix([b'Q',b'Z',KeyboardInterrupt])
        with self.assertRaises(KeyboardInterrupt):
            self.run_posix(patches)
        self.assertEqual(termios.mode,termios.original)
        self.assertEqual(closed,[55])
        self.assertEqual(b''.join(writes).decode('utf-8'),PROMPT+DOT*2+'\n')

    def test_posix_without_dev_tty_uses_stdin_and_stderr(self):
        termios,writes,closed,errors,patches=self.posix([b'Q',b'\r'],tty=False,encoding='ascii')
        self.assertEqual(self.run_posix(patches),'Q')
        self.assertEqual(errors.getvalue(),PROMPT+'.\n')
        self.assertEqual((writes,closed),([],[]))
        self.assertEqual(termios.mode,termios.original)

    def test_windows_console_masks_with_msvcrt(self):
        console=FakeMsvcrt(list('QZ')+['\b']+list('7')+['\xe0','H']+list('ab')+['\r'])
        with mock.patch.dict(sys.modules,{'msvcrt':console}):
            value=c.masked_input(PROMPT,c.Terminal(windows=True))
        self.assertEqual(value,'Q7ab')
        self.assertEqual(''.join(console.shown),PROMPT+DOT*2+'\b \b'+DOT*3+'\n')
        console=FakeMsvcrt(list('QZ')+['\x03'])
        with mock.patch.dict(sys.modules,{'msvcrt':console}),self.assertRaises(KeyboardInterrupt):
            c.masked_input(PROMPT,c.Terminal(windows=True))
        self.assertEqual(''.join(console.shown),PROMPT+DOT*2+'\n')


if __name__=='__main__':
    unittest.main()
