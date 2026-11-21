"""R16 observed Claude ordering, using task-owned scripts and no sockets."""
import json
import os
from pathlib import Path
import sys
import time
import unittest

from support import OfflineCase, rpc
from test_native_r15 import MemoryProxy, claude_control, claude_init, claude_stage1
from deep32_relay import engine, native, process, proxy, selftest


def encoded(value):
    return json.dumps(value).encode()


def initialisation():
    return dict(claude_code_version='synthetic-999.1', current_permission_mode='dontAsk',
                models=[dict(value='synthetic-model')], account=dict(uuid='synthetic-account'))


class Controls(OfflineCase):
    def test_user_and_init_are_refused_before_control_attestation(self):
        for event in (claude_init(self.root), dict(type='assistant',message={}),
                      claude_control('relay-mcp-status',dict(mcpServers=[]))):
            protocol=native.ClaudeProtocol(self.root,'synthetic-model','')
            with self.subTest(event=event),self.assertRaises(native.NativeError):
                protocol.attest(encoded(event))
            with self.assertRaises(native.NativeError):protocol.turn('synthetic question',())

    def test_initialise_requires_success_version_permission_and_signed_in_account(self):
        for field,replacement,reason in (
            ('claude_code_version','','native-startup-unverified'),
            ('claude_code_version','bad\nversion','native-startup-unverified'),
            ('current_permission_mode','default','native-startup-unverified'),
            ('account',None,'claude-sign-in-required'),
            ('account',{},'claude-sign-in-required'),
            ('account','synthetic','claude-sign-in-required'),
            ('models',[],'native-startup-unverified'),
            ('models',[dict(value='synthetic-model'),dict(value='synthetic-model')],'native-startup-unverified')):
            event=initialisation();event[field]=replacement
            protocol=native.ClaudeProtocol(self.root,'synthetic-model','')
            with self.subTest(field=field,replacement=replacement),self.assertRaisesRegex(native.NativeError,'^'+reason+'$'):
                protocol.attest(encoded(claude_control('relay-init',event)))
            self.assertEqual(protocol.replies(),[])
            with self.assertRaises(native.NativeError):protocol.turn('synthetic question',())
        for event in (dict(type='control_response',response=dict(subtype='error',request_id='relay-init')),
                      claude_control('foreign-init',initialisation()),
                      dict(type='control_response',response=dict(subtype='success',request_id='relay-init'))):
            with self.subTest(event=event),self.assertRaises(native.NativeError):
                native.ClaudeProtocol(self.root,None,'').attest(encoded(event))

    def test_initialize_then_mcp_status_is_the_complete_no_model_gate(self):
        protocol=native.ClaudeProtocol(self.root,None,'',install_check=True)
        self.assertEqual(json.loads(protocol.startup())['request']['subtype'],'initialize')
        self.assertIsNone(protocol.attest(encoded(claude_control('relay-init',initialisation()))))
        self.assertEqual([json.loads(v)['request']['subtype'] for v in protocol.replies()],['mcp_status'])
        self.assertEqual(protocol.replies(),[])
        self.assertTrue(protocol.attest(encoded(claude_control('relay-mcp-status',dict(
            mcpServers=[dict(name='deep32',status='failed',config={})])))))
        self.assertEqual((protocol.version,protocol.model,protocol.effort),
                         ('synthetic-999.1','synthetic-model','default'))
        with self.assertRaises(native.NativeError):protocol.turn('synthetic question',())
        self.assertNotIn('synthetic-account',repr(vars(protocol)))

    def test_session_requires_one_connected_deep32_server(self):
        for servers in ([],[dict(name='deep32',status='failed')],
                        [dict(name='other',status='connected')],
                        [dict(name='deep32',status='connected')]*2,
                        [dict(name='deep32')]):
            protocol=native.ClaudeProtocol(self.root,'synthetic-model','')
            protocol.attest(encoded(claude_control('relay-init',initialisation())))
            with self.subTest(servers=servers),self.assertRaises(native.NativeError):
                protocol.attest(encoded(claude_control('relay-mcp-status',dict(mcpServers=servers))))
        protocol=native.ClaudeProtocol(self.root,'synthetic-model','')
        self.assertTrue(claude_stage1(protocol))
        with self.assertRaises(native.NativeError):
            protocol.attest(encoded(claude_control('relay-mcp-status',dict(mcpServers=[]))))

    def test_stage_two_requires_exact_inventory_server_model_folder_and_order(self):
        for mutation in ('extra','missing','duplicate','server','disconnected','model','folder','premature-result'):
            protocol=native.ClaudeProtocol(self.root,'synthetic-model','')
            self.assertTrue(claude_stage1(protocol))
            protocol.turn('synthetic question',())
            event=claude_init(self.root)
            if mutation=='extra':event['tools'].append('Bash')
            elif mutation=='missing':event['tools'].pop()
            elif mutation=='duplicate':event['tools'][-1]=event['tools'][0]
            elif mutation=='server':event['mcp_servers'].append(dict(name='other',status='connected'))
            elif mutation=='disconnected':event['mcp_servers'][0]['status']='failed'
            elif mutation=='model':event['model']='synthetic-other'
            elif mutation=='folder':event['cwd']=str(self.root.parent)
            elif mutation=='premature-result':event=dict(type='result',subtype='success')
            with self.subTest(mutation=mutation),self.assertRaises(native.NativeError):protocol.attest_turn(encoded(event))

    def test_second_init_or_output_before_stage_two_is_refused(self):
        protocol=native.ClaudeProtocol(self.root,'synthetic-model','')
        claude_stage1(protocol)
        with self.assertRaises(native.NativeError):protocol.events(encoded(dict(type='result')))
        protocol.turn('synthetic question',())
        protocol.attest_turn(encoded(claude_init(self.root)))
        with self.assertRaises(native.NativeError):protocol.events(encoded(claude_init(self.root)))


SCRIPT = '''import json,sys,time
from pathlib import Path
mode=MODE
def send(value):print(json.dumps(value),flush=True)
def record(value):
    with Path('synthetic-order').open('a') as stream:stream.write(value+'\\n')
# Deliberately silent until stdin, matching the observed native CLI.
value=json.loads(sys.stdin.readline());record(value['request']['subtype'])
assert value['type']=='control_request' and value['request']['subtype']=='initialize'
if mode=='silent':time.sleep(60)
init=INITIALISATION
if mode=='missing-account':init.pop('account')
send(dict(type='control_response',response=dict(subtype='success',request_id=value['request_id'],response=init)))
value=json.loads(sys.stdin.readline());record(value['request']['subtype'])
assert value['type']=='control_request' and value['request']['subtype']=='mcp_status'
servers=[dict(name='deep32',status='failed' if mode=='disconnected' else 'connected',config={})]
if mode=='other-server':servers.append(dict(name='other',status='connected',config={}))
send(dict(type='control_response',response=dict(subtype='success',request_id=value['request_id'],response=dict(mcpServers=servers))))
line=sys.stdin.readline()
if not line:sys.exit(0)
value=json.loads(line);record(value['type'])
assert value['type']=='user'
Path('synthetic-model-turn').write_text('sent')
event=INIT
event['cwd']=str(Path.cwd())
if mode=='bad-tools':event['tools'].append('Bash')
if mode=='bad-model':event['model']='synthetic-other'
if mode=='bad-folder':event['cwd']=str(Path.cwd().parent)
if mode=='bad-server':event['mcp_servers'].append(dict(name='other',status='connected'))
if mode=='no-init':send(dict(type='assistant',message=dict(content=[])))
else:send(event)
if mode.startswith('bad-') or mode=='no-init':time.sleep(60)
send(dict(type='result',subtype='success',is_error=False,result='synthetic answer',usage=dict(input_tokens=2,output_tokens=3)))
'''


@unittest.skipUnless(os.name=='nt','scripted native processes use the owned Windows Job')
class Executables(OfflineCase):
    def fake(self,mode='normal',*,before_user=None):
        script=self.root/'synthetic-observed-claude.py'
        script.write_text(SCRIPT.replace('MODE',repr(mode)).replace('INITIALISATION',repr(initialisation())).replace('INIT',repr(claude_init(self.root))),encoding='utf-8')
        binary=Path(sys.executable).resolve(strict=True)
        trees=[]
        def factory(platform):
            tree=process.WindowsJob();trees.append(tree)
            start=tree.start
            def launch(argv,**kwargs):
                result=start((str(binary),'-I','-B',str(script),*argv[1:]),**kwargs)
                if before_user:
                    target=result.stdin
                    class Input:
                        def write(inner,raw):
                            if json.loads(raw).get('type')=='user':before_user()
                            return target.write(raw)
                        def flush(inner):return target.flush()
                        def close(inner):return target.close()
                    result.stdin=Input()
                return result
            tree.start=launch
            return tree
        profile=self.root/'profile';profile.mkdir(exist_ok=True)
        return binary,profile,factory,trees

    def session(self,mode='normal'):
        binary,profile,factory,trees=self.fake(mode)
        endpoint=MemoryProxy()
        plan,protocol=native.ClaudeCodeLauncher().prepare(binary,self.root,profile,
            endpoint=endpoint.url,bearer='SYNTHETIC-BEARER',model='synthetic-model',platform='windows')
        session=native.NativeSession(plan,protocol,factory('windows'),endpoint,live=lambda:True)
        self.addCleanup(session.cancel)
        return session

    def test_observed_silence_control_response_then_init_after_first_user(self):
        session=self.session()
        self.assertTrue(session.initialise())
        self.assertEqual((self.root/'synthetic-order').read_text().splitlines(),['initialize','mcp_status'])
        self.assertFalse((self.root/'synthetic-model-turn').exists())
        session.release('synthetic question',())
        self.assertEqual((self.root/'synthetic-order').read_text().splitlines(),['initialize','mcp_status','user'])
        self.assertEqual(session.protocol.stage,'running')
        events=[];end=time.monotonic()+5
        while not session.completed and time.monotonic()<end:
            events+=session.poll();time.sleep(.01)
        self.assertEqual(events[-1],dict(type='completed',usage=dict(input_tokens=2,output_tokens=3)))
        self.assertTrue(session.cancel())

    def test_install_stops_after_stage_one_and_accepts_present_unconnected_server(self):
        binary,profile,factory,trees=self.fake('disconnected')
        result=selftest.check_startup('claude',binary,profile,self.root,tree_factory=factory,proxy_factory=MemoryProxy)
        self.assertEqual(result,dict(version='synthetic-999.1',model='synthetic-model',effort='default'))
        self.assertEqual((self.root/'synthetic-order').read_text().splitlines(),['initialize','mcp_status'])
        self.assertFalse((self.root/'synthetic-model-turn').exists())
        self.assertTrue(trees[0].closed)

    def test_missing_account_is_refused_with_sign_in_diagnostic_before_user(self):
        session=self.session('missing-account')
        with self.assertRaisesRegex(native.NativeError,'^claude-sign-in-required$'):session.initialise()
        self.assertEqual((self.root/'synthetic-order').read_text().splitlines(),['initialize'])
        self.assertFalse((self.root/'synthetic-model-turn').exists())
        self.assertTrue(session.tree.closed)

    def test_missing_account_is_recorded_as_sign_in_required_without_account_data(self):
        from test_install import FakeSecurity
        from deep32_relay.state import OwnedRoot, OWNER_SCHEMA
        binary,profile,factory,trees=self.fake('missing-account')
        owner=OwnedRoot(self.root/'owned',security=FakeSecurity());owner.create()
        owner.write_json('owner.json',dict(schema=OWNER_SCHEMA,install_id='1'*32))
        owner.mkdir('profiles/claude')
        release='2'*64
        install=dict(agent='claude',install_id='1'*32,release_id=release,profile_alias='relay-claude',
            owned_files={'packages/'+release+'/RESEARCH.md':'3'*64,
                         'packages/'+release+'/deep32_relay/native.py':'4'*64})
        def check(*args,**kwargs):
            return selftest.check_startup(*args,**kwargs,tree_factory=factory,proxy_factory=MemoryProxy)
        result=selftest.qualify_install(owner,install,executable=binary,checker=check)
        self.assertEqual((result['state'],result['reason']),('unqualified','claude-sign-in-required'))
        self.assertEqual(owner.read_json('private/qualification/claude.json'),result)
        self.assertEqual((result['model'],result['version']),('',''))
        self.assertNotIn('synthetic-account',json.dumps(result))
        self.assertTrue(trees[0].closed)

    def test_silent_agent_is_killed_without_user_input(self):
        session=self.session('silent')
        with self.assertRaisesRegex(native.NativeError,'^native-startup-timeout$'):session.initialise(timeout=.2)
        self.assertFalse((self.root/'synthetic-model-turn').exists())
        self.assertTrue(session.tree.closed)

    def test_stage_one_mismatch_never_releases_user(self):
        for mode in ('disconnected','other-server'):
            with self.subTest(mode=mode):
                scratch=self.root/mode;scratch.mkdir()
                # Each test plan needs a fresh private scratch configuration.
                binary,profile,factory,trees=self.fake(mode)
                plan,protocol=native.ClaudeCodeLauncher().prepare(binary,scratch,profile,
                    endpoint=MemoryProxy.url,bearer='SYNTHETIC-BEARER',model='synthetic-model',platform='windows')
                session=native.NativeSession(plan,protocol,factory('windows'),MemoryProxy(),live=lambda:True)
                self.addCleanup(session.cancel)
                with self.assertRaises(native.NativeError):session.initialise()
                self.assertFalse((scratch/'synthetic-model-turn').exists())
                self.assertTrue(trees[0].closed)

    def test_bad_stage_two_kills_process_before_any_tool_can_be_forwarded(self):
        cap=self.active_cap();self.initialise(cap)
        endpoints=[];blocked=[]
        class Endpoint(MemoryProxy):
            def __init__(inner,gateway,capability):
                super().__init__();inner.gateway=gateway;endpoints.append(inner)
            def attempt(inner):
                return inner.gateway.http_request(cap,method='POST',path='/mcp',host='127.0.0.1:34567',
                    authorisation='Bearer '+cap.bearer,content_type='application/json',
                    body=encoded(rpc('tools/call',ident=3,name='rag_status',arguments={})))
        def premature():
            try:endpoints[0].attempt()
            except proxy.ProxyError as error:blocked.append(str(error))
        binary,profile,factory,trees=self.fake('bad-tools',before_user=premature)
        qualification=dict(schema=selftest.ATTEST_SCHEMA,state='qualified',model='synthetic-model',
            effort='default',binary_sha256=selftest.binary_digest(binary))
        agent=engine.NativeAgent('claude',profile,executable=binary,qualification=qualification,
            gateway=self.gateway,tree_factory=factory,proxy_factory=Endpoint)
        with self.assertRaisesRegex(engine.EngineError,'^native-startup-unverified$'):
            agent.start('synthetic question',(),self.root,cap)
        self.assertEqual(blocked,['native-startup-unverified'])
        self.assertFalse(endpoints[0].gateway.verified)
        self.assertTrue(endpoints[0].closed)
        self.assertTrue(trees[0].closed)
        self.assertEqual([call[1]['body']['method'] for call in self.dispatch.calls],
                         ['initialize','notifications/initialized','tools/list'])

    def test_tool_forwarding_opens_only_after_valid_second_stage(self):
        cap=self.active_cap();self.initialise(cap)
        endpoints=[];blocked=[]
        class Endpoint(MemoryProxy):
            def __init__(inner,gateway,capability):
                super().__init__();inner.gateway=gateway;endpoints.append(inner)
            def attempt(inner):
                return inner.gateway.http_request(cap,method='POST',path='/mcp',host='127.0.0.1:34567',
                    authorisation='Bearer '+cap.bearer,content_type='application/json',
                    body=encoded(rpc('tools/call',ident=3,name='rag_status',arguments={})))
        def premature():
            try:endpoints[0].attempt()
            except proxy.ProxyError as error:blocked.append(str(error))
        binary,profile,factory,trees=self.fake(before_user=premature)
        qualification=dict(schema=selftest.ATTEST_SCHEMA,state='qualified',model='synthetic-model',
            effort='default',binary_sha256=selftest.binary_digest(binary))
        agent=engine.NativeAgent('claude',profile,executable=binary,qualification=qualification,
            gateway=self.gateway,tree_factory=factory,proxy_factory=Endpoint)
        session=agent.start('synthetic question',(),self.root,cap)
        self.addCleanup(session.cancel)
        self.assertEqual(blocked,['native-startup-unverified'])
        self.assertTrue(endpoints[0].gateway.verified)
        self.assertEqual(session.protocol.stage,'running')
        endpoints[0].attempt()
        self.assertEqual([call[1]['body']['method'] for call in self.dispatch.calls],
                         ['initialize','notifications/initialized','tools/list','tools/call'])
        self.assertTrue(session.cancel())
        self.assertTrue(trees[0].closed)

    def test_each_stage_two_mismatch_kills_before_release_returns(self):
        for mode in ('bad-model','bad-folder','bad-server','no-init'):
            with self.subTest(mode=mode):
                scratch=self.root/mode;scratch.mkdir()
                binary,profile,factory,trees=self.fake(mode)
                plan,protocol=native.ClaudeCodeLauncher().prepare(binary,scratch,profile,
                    endpoint=MemoryProxy.url,bearer='SYNTHETIC-BEARER',model='synthetic-model',platform='windows')
                session=native.NativeSession(plan,protocol,factory('windows'),MemoryProxy(),live=lambda:True)
                self.addCleanup(session.cancel)
                self.assertTrue(session.initialise())
                with self.assertRaises(native.NativeError):session.release('synthetic question',())
                self.assertTrue(trees[0].closed)
                self.assertTrue(session.cleanup_complete)


if __name__=='__main__':unittest.main()
