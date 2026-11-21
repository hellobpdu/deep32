"""R15 native-shaped scripted executables. No real agents or network."""
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest import mock

from support import OfflineCase
from deep32_relay import engine, native, process, proxy, selftest
from deep32_relay.state import OwnedRoot, OWNER_SCHEMA, StateError


class MemoryProxy:
    url = 'http://127.0.0.1:34567/mcp'
    def __init__(self, *args):
        self.closed = False
    def start(self):
        return self
    def close(self):
        self.closed = True
        return True


def claude_init(folder):
    return dict(type='system', subtype='init', cwd=str(folder), model='synthetic-model',
        claude_code_version='synthetic-999.1', tools=['mcp__deep32__'+v for v in sorted(proxy.TOOLS)],
        mcp_servers=[dict(name='deep32', status='connected')])


def claude_control(ident, value):
    return dict(type='control_response', response=dict(subtype='success', request_id=ident, response=value))


def claude_stage1(protocol, *, status='connected'):
    protocol.attest(json.dumps(claude_control('relay-init', dict(
        claude_code_version='synthetic-999.1', current_permission_mode='dontAsk',
        account=dict(uuid='synthetic-account'), models=[dict(value='synthetic-model')]))).encode())
    protocol.replies()
    return protocol.attest(json.dumps(claude_control('relay-mcp-status',dict(
        mcpServers=[dict(name='deep32',status=status)]))).encode())


def codex_config():
    return dict(model='synthetic-model', model_reasoning_effort='default',
        approval_policy='never', sandbox_mode='read-only', web_search='disabled',
        project_doc_max_bytes=0, history=dict(persistence='none'),
        shell_environment_policy=dict(inherit='none'),
        features={name:False for name in native.CODEX_DISABLED},
        mcp_servers=dict(deep32=dict(enabled=True, enabled_tools=sorted(proxy.TOOLS))))


class Plans(OfflineCase):
    def prepare(self, agent):
        profile = self.root/'profile'
        profile.mkdir(exist_ok=True)
        (self.root/'synthetic-agent.exe').write_bytes(b'synthetic executable')
        launcher = native.ClaudeCodeLauncher() if agent == 'claude' else native.CodexLauncher()
        return launcher.prepare(self.root/'synthetic-agent.exe', self.root, profile,
            endpoint=MemoryProxy.url, bearer='SYNTHETIC-BEARER', model='synthetic-model',
            platform='windows')

    def test_claude_captured_flags_and_no_secret_prompt_in_arguments(self):
        plan, protocol = self.prepare('claude')
        for option, value in (('--tools',''),('--setting-sources',''),('--permission-mode','dontAsk'),
                              ('--permission-prompts','none'),('--input-format','stream-json')):
            self.assertEqual(plan.argv[plan.argv.index(option)+1], value)
        for option in ('--strict-mcp-config','--restricted','--no-session-persistence'):
            self.assertIn(option,plan.argv)
        self.assertNotIn('SYNTHETIC-BEARER',' '.join(plan.argv))
        self.assertNotIn('SYNTHETIC-BEARER',(self.root/'relay-mcp.json').read_text())
        self.assertNotIn('user', json.loads(plan.startup_input).get('type',''))
        self.assertNotIn('PATH',plan.environment)

    def test_codex_flags_features_and_no_user_rules(self):
        plan, protocol = self.prepare('codex')
        self.assertEqual(plan.argv[-1],'app-server')
        self.assertIn('--strict-config',plan.argv)
        self.assertIn('--no-daemon',plan.argv)
        disabled = [plan.argv[i+1] for i,v in enumerate(plan.argv) if v == '--disable']
        self.assertEqual(disabled,list(native.CODEX_DISABLED))
        self.assertIn('approval_policy="never"',plan.argv)
        self.assertIn('sandbox_mode="read-only"',plan.argv)
        self.assertIn('web_search="disabled"',plan.argv)
        self.assertNotIn('SYNTHETIC-BEARER',' '.join(plan.argv))

    def test_relay_profile_config_or_foreign_proxy_refused(self):
        profile=self.root/'profile';profile.mkdir()
        (profile/'config.toml').write_text('synthetic=true')
        with self.assertRaises(native.NativeError): self.prepare('codex')
        with self.assertRaises(native.NativeError):
            native.ClaudeCodeLauncher().prepare(self.root/'agent.exe',self.root,profile,
                endpoint='http://example.invalid/mcp',bearer='synthetic')

    def test_claude_exact_eleven_server_model_and_folder(self):
        for mutation in ('extra','duplicate','server','model','folder'):
            value=claude_init(self.root)
            if mutation=='extra': value['tools'].append('Bash')
            if mutation=='duplicate': value['tools'][-1]=value['tools'][0]
            if mutation=='server': value['mcp_servers'].append(dict(name='other',status='connected'))
            if mutation=='model': value['model']='other'
            if mutation=='folder': value['cwd']=str(self.root.parent)
            with self.subTest(mutation=mutation),self.assertRaises(native.NativeError):
                protocol=native.ClaudeProtocol(self.root,'synthetic-model','')
                claude_stage1(protocol)
                protocol.turn('synthetic question',())
                protocol.attest_turn(json.dumps(value).encode())
        protocol=native.ClaudeProtocol(self.root,'synthetic-model','')
        self.assertTrue(claude_stage1(protocol))
        protocol.turn('synthetic question',())
        self.assertTrue(protocol.attest_turn(json.dumps(claude_init(self.root)).encode()))

    def test_codex_effective_settings_and_feature_failures(self):
        for field in ('approval_policy','sandbox_mode','web_search','features','mcp_servers','history'):
            protocol=native.CodexProtocol(self.root,None,None,'')
            config=codex_config()
            config[field]={} if isinstance(config[field],dict) else 'unsafe'
            with self.subTest(field=field),self.assertRaises(native.NativeError):
                protocol._config(dict(config=config,layers=[]))
        for kind in ('user','project','managed'):
            with self.subTest(layer=kind),self.assertRaises(native.NativeError):
                native.CodexProtocol(self.root,None,None,'')._config(dict(config=codex_config(),layers=[dict(name=dict(type=kind))]))

    def test_codex_order_model_turn_and_other_server_fail_before_prompt(self):
        protocol=native.CodexProtocol(self.root,None,None,'')
        for event in (dict(id=5,result=dict(data=[])),dict(method='turn/started',params={}),
                      dict(id=2,result=dict(config=codex_config(),layers=[]))):
            with self.subTest(event=event), self.assertRaises(native.NativeError):
                protocol.attest(json.dumps(event).encode())

    def test_metadata_install_gateway_can_never_call_a_tool(self):
        metadata=selftest.MetadataGateway()
        with self.assertRaises(StateError):
            metadata.dispatch('check',dict(body=dict(method='tools/call',id=1)),100)

    def test_optional_live_runner_needs_explicit_opt_in_before_any_effect(self):
        self.assertEqual(selftest.live_selftest(None,dict(agent='claude'))['reason'],
                         'model-usage-opt-in-required')

    def test_optional_live_runner_measures_allowed_call_and_records_no_content(self):
        from test_install import FakeSecurity
        from support import rpc
        owner=OwnedRoot(self.root/'owned',security=FakeSecurity());owner.create()
        owner.write_json('owner.json',dict(schema=OWNER_SCHEMA,install_id='1'*32))
        install=dict(agent='claude',install_id='1'*32)
        attempts=[]
        class FakeLive:
            instruction=''
            def __init__(inner,gateway):inner.gateway=gateway
            def verify(inner,*args):return True
            def start(inner,question,context,scratch,cap):
                attempts.append(question)
                allowed=question.startswith('Call the permitted Deep32')
                if allowed:
                    for request in (rpc('initialize',protocolVersion='2025-03-26',capabilities={},
                                        clientInfo=dict(name='synthetic',version='1')),
                                    rpc('notifications/initialized'),rpc('tools/list',ident=2),
                                    rpc('tools/call',ident=3,name='rag_status',arguments={})):
                        inner.gateway.call(cap,request)
                class Handle:
                    def poll(self):return [dict(type='final',text=json.dumps({'allowed':True} if allowed else {'refused':True})),
                        dict(type='completed',usage=dict(input_tokens=1,output_tokens=1))]
                    def cancel(self):return True
                return Handle()
        with mock.patch.object(selftest,'qualification_status',return_value=dict(state='qualified',reason='none')):
            result=selftest.live_selftest(owner,install,allow_model_usage=True,
                agent_factory=lambda owner,install,gateway:FakeLive(gateway))
        self.assertEqual(len(attempts),6)
        self.assertEqual(result['state'],'passed')
        self.assertEqual(result['model_usage'],'opt-in')
        self.assertTrue(all(case['pass'] for case in result['cases']))
        self.assertEqual(result['cases'][-1]['permitted_calls'],1)
        self.assertNotIn('private self-test fixture',json.dumps(owner.read_json('private/qualification/claude-live.json')))

    def test_failed_startup_retains_cleanup_ownership(self):
        class Endpoint:
            def close(self):return False
        class Tree:
            def __init__(self):self.count=0
            def cancel(self):self.count+=1;return self.count>1
        tree=Tree();handle=engine.FailedStartup(Endpoint(),tree)
        with self.assertRaises(engine.EngineError):handle.poll()
        self.assertFalse(handle.cancel())
        self.assertFalse(handle.cancel())
        handle.endpoint.close=lambda:True
        self.assertTrue(handle.cancel())

    def test_native_proxy_discovery_cannot_call_tools_before_start_attestation(self):
        cap=self.active_cap()
        gate=engine.StartupGateway(self.gateway)
        body=json.dumps(dict(jsonrpc='2.0',id=1,method='tools/call',params=dict(name='rag_status'))).encode()
        with self.assertRaises(proxy.ProxyError):
            gate.http_request(cap,method='POST',path='/mcp',host='127.0.0.1:34567',
                authorisation='Bearer '+cap.bearer,content_type='application/json',body=body)
        self.assertEqual(self.dispatch.calls,[])

    def test_no_model_receipt_no_version_allowlist_binding_and_upgrade(self):
        from test_install import FakeSecurity
        owner=OwnedRoot(self.root/'owned',security=FakeSecurity());owner.create()
        owner.write_json('owner.json',dict(schema=OWNER_SCHEMA,install_id='1'*32))
        binary=self.root/'synthetic-agent.bin';binary.write_bytes(b'synthetic binary')
        release='2'*64
        install=dict(agent='claude',agent_binary=str(binary),install_id='1'*32,release_id=release,
            profile_alias='relay-claude',owned_files={'packages/'+release+'/RESEARCH.md':'3'*64,
                'packages/'+release+'/deep32_relay/native.py':'4'*64})
        calls=[]
        def checker(*args,**kwargs):
            calls.append((args,kwargs))
            return dict(version='synthetic-new-999',model='synthetic-model',effort='default')
        result=selftest.qualify_install(owner,install,executable=binary,checker=checker)
        self.assertEqual(result['state'],'qualified')
        self.assertEqual(result['model_usage'],'none')
        self.assertEqual(len(calls),1)
        self.assertEqual(selftest.qualification_status(owner,install,executable=binary),dict(state='qualified',reason='none'))
        binary.write_bytes(b'synthetic upgraded binary')
        self.assertEqual(selftest.qualification_status(owner,install,executable=binary)['reason'],'binary-changed')
        result=selftest.qualify_install(owner,install,executable=binary,checker=checker)
        self.assertEqual(result['state'],'qualified')
        self.assertEqual(len(calls),2)


CLAUDE_SCRIPT = '''import json,sys
from pathlib import Path
args=sys.argv[1:]
assert args[args.index('--tools')+1]==''
assert '--strict-mcp-config' in args and '--restricted' in args
startup=json.loads(sys.stdin.readline())
assert startup['type']=='control_request' and startup['request']['subtype']=='initialize'
print(json.dumps(dict(type='control_response',response=dict(subtype='success',request_id=startup['request_id'],response=dict(claude_code_version='synthetic-999.1',current_permission_mode='dontAsk',account=dict(uuid='synthetic-account'),models=[dict(value='synthetic-model')])))),flush=True)
status=json.loads(sys.stdin.readline())
assert status['type']=='control_request' and status['request']['subtype']=='mcp_status'
print(json.dumps(dict(type='control_response',response=dict(subtype='success',request_id=status['request_id'],response=dict(mcpServers=[dict(name='deep32',status=STATUS)])))),flush=True)
event=EVENT
event['cwd']=str(Path.cwd())
line=sys.stdin.readline()
if line:
    value=json.loads(line)
    assert value['type']=='user'
    Path('synthetic-model-turn').write_text('sent')
    print(json.dumps(event),flush=True)
    print(json.dumps(dict(type='result',subtype='success',is_error=False,result='synthetic answer',usage=dict(input_tokens=2,output_tokens=3))),flush=True)
'''

CODEX_SCRIPT = '''import json,sys
from pathlib import Path
assert sys.argv[-1]=='app-server'
for line in sys.stdin:
    value=json.loads(line)
    method=value['method']
    result=None
    if method=='initialize':result=dict(userAgent='synthetic-codex/999')
    elif method=='initialized':continue
    elif method=='config/read':
        config=CONFIG
        config['mcp_servers']['deep32'].update(url='http://127.0.0.1:34567/mcp',bearer_token_env_var='DEEP32_RELAY_SESSION_BEARER')
        result=dict(config=config,layers=[])
    elif method=='experimentalFeature/list':result=dict(data=[dict(name=k,enabled=False) for k in DISABLED])
    elif method=='thread/start':result=dict(thread=dict(id='synthetic-thread',cwd=str(Path.cwd())),model='synthetic-model')
    elif method=='mcpServerStatus/list':result=dict(data=[dict(name='deep32',tools={k:dict(name=k) for k in TOOLS})])
    elif method=='turn/start':
        Path('synthetic-model-turn').write_text('sent')
        print(json.dumps(dict(id=value['id'],result=dict(turn=dict(id='synthetic-turn')))),flush=True)
        print(json.dumps(dict(method='item/agentMessage/delta',params=dict(threadId='synthetic-thread',delta='synthetic answer'))),flush=True)
        print(json.dumps(dict(method='thread/tokenUsage/updated',params=dict(threadId='synthetic-thread',tokenUsage=dict(last=dict(inputTokens=2,outputTokens=3))))),flush=True)
        print(json.dumps(dict(method='turn/completed',params=dict(threadId='synthetic-thread',turn=dict(status='completed')))),flush=True)
        continue
    else:raise AssertionError('unexpected method')
    print(json.dumps(dict(id=value['id'],result=result)),flush=True)
'''


@unittest.skipUnless(os.name=='nt','native process mechanism needs Windows')
class Executables(OfflineCase):
    def fake(self,agent,*,bad=False,default_null=False):
        script=self.root/('synthetic-'+agent+'.py')
        if agent=='claude':
            event=claude_init(self.root)
            value=CLAUDE_SCRIPT.replace('EVENT',repr(event)).replace('STATUS',repr('connected'))
            if bad:value=value.replace("name='deep32'","name='other'")
        else:
            config=codex_config()
            if bad:config['features']['shell_tool']=True
            if default_null:config['model']=None
            value=CODEX_SCRIPT.replace('CONFIG',repr(config)).replace('DISABLED',repr(native.CODEX_DISABLED)).replace('TOOLS',repr(sorted(proxy.TOOLS)))
        script.write_text(value,encoding='utf-8')
        binary=Path(sys.executable).resolve(strict=True)
        def factory(platform):
            tree=process.WindowsJob()
            start=tree.start
            def launch(argv,**kwargs):
                return start((str(binary),'-I','-B',str(script),*argv[1:]),**kwargs)
            tree.start=launch
            return tree
        profile=self.root/'profile';profile.mkdir(exist_ok=True)
        return binary,profile,factory

    def test_install_stops_at_init_with_no_model_turn_for_both_agents(self):
        for agent in ('claude','codex'):
            with self.subTest(agent=agent):
                scratch=self.root/agent;scratch.mkdir()
                binary,profile,factory=self.fake(agent)
                result=selftest.check_startup(agent,binary,profile,scratch,
                    tree_factory=factory,proxy_factory=MemoryProxy)
                self.assertEqual(result['model'],'synthetic-model')
                self.assertFalse((scratch/'synthetic-model-turn').exists())

    def test_bad_startup_is_killed_without_model_turn(self):
        for agent in ('claude','codex'):
            with self.subTest(agent=agent):
                scratch=self.root/agent;scratch.mkdir()
                binary,profile,factory=self.fake(agent,bad=True)
                with self.assertRaises(native.NativeError):
                    selftest.check_startup(agent,binary,profile,scratch,
                        tree_factory=factory,proxy_factory=MemoryProxy)
                self.assertFalse((scratch/'synthetic-model-turn').exists())

    def test_codex_null_config_default_is_resolved_and_attested_by_thread_start(self):
        binary,profile,factory=self.fake('codex',default_null=True)
        result=selftest.check_startup('codex',binary,profile,self.root,
            tree_factory=factory,proxy_factory=MemoryProxy)
        self.assertEqual(result['model'],'synthetic-model')
        self.assertFalse((self.root/'synthetic-model-turn').exists())

    def test_per_turn_attestation_release_answer_usage_and_tree_cleanup(self):
        for agent_name in ('claude','codex'):
            with self.subTest(agent=agent_name):
                scratch=self.root/agent_name;scratch.mkdir()
                binary,profile,factory=self.fake(agent_name)
                cap=self.active_cap(agent_name)
                receipt=dict(schema=selftest.ATTEST_SCHEMA,state='qualified',model='synthetic-model',
                    effort='default',binary_sha256=selftest.binary_digest(binary))
                agent=engine.NativeAgent(agent_name,profile,executable=binary,qualification=receipt,
                    gateway=self.gateway,tree_factory=factory,proxy_factory=MemoryProxy)
                self.assertTrue(agent.verify(scratch,cap))
                handle=agent.start('synthetic question',(),scratch,cap)
                self.addCleanup(handle.cancel)
                events=[];end=time.monotonic()+5
                while not handle.completed and time.monotonic()<end:
                    events+=handle.poll();time.sleep(.01)
                self.assertTrue((scratch/'synthetic-model-turn').exists())
                self.assertEqual(events[-1],dict(type='completed',usage=dict(input_tokens=2,output_tokens=3)))
                self.assertTrue(handle.cancel())
                self.assertTrue(handle.cleanup_complete)


if __name__=='__main__':unittest.main()
