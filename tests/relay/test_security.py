"""Synthetic effect/redaction checks; native six-lane qualification stays open."""
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import unittest
from unittest import mock
from support import OfflineCase, Agent, Scratch, REAL_POPEN, request
from deep32_relay import engine, status
from deep32_relay.sessions import Supervisor
from deep32_relay.proxy import ProxyError
from deep32_relay.state import StateError

CANARY = 'SYNTHETIC-PRIVATE-CANARY-9264'


class Security(OfflineCase):
    def supervisor(self, agent):
        self.events=[]
        return Supervisor(agent,self.gateway,lambda s,e:self.events.append((s,e)),
                          lambda ident:Scratch(self.root,ident),clock=self.clock)

    def test_both_native_agents_refuse_even_a_complete_descriptive_receipt(self):
        for platform in ('windows','linux','macos'):
            for name in ('claude','codex'):
                with self.subTest(platform=platform,agent=name):
                    receipt=engine.Qualification(name,platform,binary_sha256='a'*64,version='synthetic',
                        tool_policy=True,scratch_writes=True,credential_resolution=True,
                        descendant_containment=True,startup_attestation=True,usage_accounting=True,native_evidence=True)
                    self.assertTrue(receipt.qualified)
                    native=engine.NativeAgent(name,self.root/'private-profile')
                    self.assertFalse(native.ready)
                    for action,args in ((native.verify,(self.root,None)),(native.start,(CANARY,(),self.root,None))):
                        with self.assertRaises(engine.EngineError) as caught:action(*args)
                        self.assertEqual(str(caught.exception),'native-agent-unqualified')
                    supervisor=self.supervisor(native)
                    self.assertFalse(supervisor.open(1,'synthetic',request()))
                    self.assertEqual(self.dispatch.calls,[])

    def test_child_environment_starts_empty_and_all_mutable_roots_are_scratch(self):
        inherited={'CODEX_HOME':CANARY,'CLAUDE_CONFIG_DIR':CANARY,'ANTHROPIC_API_KEY':CANARY,
                   'OPENAI_API_KEY':CANARY,'PYTHONPATH':CANARY,'LD_PRELOAD':CANARY,
                   'HTTP_PROXY':CANARY,'HTTPS_PROXY':CANARY,'PATH':CANARY,
                   'DEEP32_MCP_TOKEN':CANARY,'NODE_OPTIONS':CANARY,'BASH_ENV':CANARY}
        with mock.patch.dict(os.environ,inherited):
            for platform in ('linux','macos','windows'):
                env=engine.clean_environment(self.root,platform=platform)
                self.assertNotIn(CANARY,json.dumps(env))
                self.assertTrue(set(env).isdisjoint(inherited))
                for name in ('HOME','USERPROFILE','TMP','TEMP','TMPDIR','XDG_CONFIG_HOME','XDG_CACHE_HOME',
                             'XDG_STATE_HOME','XDG_DATA_HOME','APPDATA','LOCALAPPDATA'):
                    self.assertEqual(Path(env[name]),self.root)
                self.assertEqual(env['PYTHONUTF8'],'1')

    def test_link_or_reparse_scratch_is_refused_before_environment_use(self):
        original=Path.lstat
        def linked(path,*args,**kwargs):
            actual=original(path,*args,**kwargs)
            if path==self.root:
                return mock.Mock(st_mode=stat.S_IFDIR|0o700,st_file_attributes=0x400)
            return actual
        with mock.patch.object(Path,'lstat',linked):
            with self.assertRaises((engine.EngineError,StateError)):
                engine.clean_environment(self.root,platform='linux')

    def test_agent_exceptions_and_unknown_output_do_not_enter_public_result(self):
        agent=Agent()
        def fail(*_):raise RuntimeError(CANARY)
        agent.verify_hook=fail
        supervisor=self.supervisor(agent)
        public=io.StringIO()
        with contextlib.redirect_stdout(public),contextlib.redirect_stderr(public):
            supervisor.open(1,'synthetic',request());supervisor.start(1,CANARY)
        self.assertNotIn(CANARY,public.getvalue()+json.dumps(supervisor.last_result)+json.dumps(self.events))
        self.assertEqual(agent.starts,[])
        self.assertFalse(self.gateway.capabilities)

    def test_revoke_callback_exception_still_reaps_and_blocks_replacement(self):
        agent=Agent();supervisor=self.supervisor(agent)
        scratches=[]
        def scratch(ident):
            result=Scratch(self.root,ident);scratches.append(result);return result
        supervisor.scratch_factory=scratch
        supervisor.open(1,'synthetic-first',request());supervisor.start(1,CANARY)
        supervisor.open(3,'synthetic-queued',request())
        cap=supervisor.sessions[1].capability
        def fail(_):raise RuntimeError(CANARY)
        self.gateway.on_revoke=fail
        public=io.StringIO()
        with contextlib.redirect_stdout(public),contextlib.redirect_stderr(public):
            supervisor.interrupt(1);supervisor.close(1)
        self.assertFalse(cap.active)
        self.assertEqual(agent.handles[0].cancelled,1)
        self.assertEqual(scratches[0].cleanups,1)
        self.assertTrue(supervisor.cleanup_failed)
        self.assertEqual(supervisor.sessions[3].state,'queued')
        self.assertNotIn(CANARY,public.getvalue()+json.dumps(supervisor.last_result)+json.dumps(self.events))
        self.assertEqual(len(agent.starts),1)

    def test_private_session_and_capability_repr_excludes_contents(self):
        agent=Agent();supervisor=self.supervisor(agent)
        supervisor.open(1,'synthetic',request());supervisor.start(1,CANARY)
        session=supervisor.sessions[1]; cap=session.capability
        public=repr(session)+repr(cap)+json.dumps(supervisor.last_result)
        self.assertNotIn(CANARY,public)
        self.assertNotIn(cap.bearer,public)
        supervisor.disconnect()

    def test_public_status_rejects_injected_secret_fields_or_values(self):
        snapshot=status.unavailable_snapshot('a'*32,'codex','a'*64,now=100)
        self.assertNotIn(CANARY,json.dumps(snapshot))
        for field in ('token','code','password','profile_path','prompt','answer','argv','environment','stderr'):
            value=dict(snapshot);value[field]=CANARY
            with self.subTest(field=field),self.assertRaises(StateError):status.validate_snapshot(value)
        for field in ('profile_alias','workstation_alias','link_state','box_state','agent','instance_id'):
            value=dict(snapshot);value[field]=CANARY
            with self.subTest(field=field),self.assertRaises(StateError):status.validate_snapshot(value)


FAKE_SOURCE = '''import json,sys,time
request=json.load(sys.stdin)
if request["mode"]=="wait":
    time.sleep(30)
else:
    print(json.dumps({"type":"delta","text":request["question"]}),flush=True)
    print(json.dumps({"type":"completed","usage":{"input_tokens":1,"output_tokens":2}}),flush=True)
'''


class FakeExecutableHandle:
    """Owns exactly the scripted no-descendant fake, not a native process tree."""
    def __init__(self,script,question,scratch,mode):
        self.argv=[sys.executable,'-I','-B',str(script)]
        self.environment=engine.clean_environment(scratch,platform='windows' if os.name=='nt' else 'linux')
        self.process=REAL_POPEN(self.argv,cwd=scratch,env=self.environment,stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,shell=False)
        self.process.stdin.write(json.dumps({'question':question,'mode':mode}).encode())
        self.process.stdin.close();self.process.stdin=None
        self.read=False
    def poll(self):
        if self.process.poll() is None or self.read:return []
        self.read=True
        stdout,stderr=self.process.communicate(timeout=2)
        if stderr or len(stdout)>1048576:raise RuntimeError('synthetic-output-invalid')
        return [json.loads(line) for line in stdout.splitlines()]
    def cancel(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=2)
        for pipe in (self.process.stdin,self.process.stdout,self.process.stderr):
            if pipe:pipe.close()
        return self.process.poll() is not None


class FakeExecutableAgent(Agent):
    def __init__(self,script,mode='healthy'):
        super().__init__();self.script=script;self.mode=mode
    def start(self,question,history,scratch,cap,*,model=None,effort=None):
        self.starts.append((question,history,scratch,cap))
        handle=FakeExecutableHandle(self.script,question,scratch,self.mode)
        self.handles.append(handle)
        return handle


class FakeExecutable(OfflineCase):
    """The two extra cases exercise the supervisor executable seam only."""
    supervisor=Security.supervisor
    def test_scripted_executable_question_is_stdin_only(self):
        script=self.root/'synthetic-agent.py';script.write_text(FAKE_SOURCE,encoding='utf-8')
        agent=FakeExecutableAgent(script);supervisor=self.supervisor(agent)
        supervisor.open(1,'synthetic',request());supervisor.start(1,CANARY)
        handle=agent.handles[0]
        self.assertNotIn(CANARY,' '.join(handle.argv)+json.dumps(handle.environment))
        handle.process.wait(timeout=5)
        supervisor.tick()
        self.assertEqual(supervisor.last_result,{'status':'completed','reason':'none','usage_known':True,
                                                 'model':'synthetic-model','effort':'synthetic-effort'})
        self.assertEqual([e['text'] for _,e in self.events if e['op']=='agent.delta'],[CANARY])
        self.assertIsNotNone(handle.process.poll())
        self.assertFalse(self.gateway.capabilities)
        supervisor.disconnect()

    def test_stuck_script_is_reaped_on_interrupt_without_replay(self):
        script=self.root/'synthetic-agent.py';script.write_text(FAKE_SOURCE,encoding='utf-8')
        agent=FakeExecutableAgent(script,'wait');supervisor=self.supervisor(agent)
        supervisor.open(1,'synthetic',request());supervisor.start(1,CANARY)
        handle=agent.handles[0]
        supervisor.interrupt(1);supervisor.tick();supervisor.disconnect()
        self.assertIsNotNone(handle.process.poll())
        self.assertEqual(len(agent.starts),1)
        self.assertFalse(self.gateway.capabilities)


if __name__=='__main__':
    unittest.main()
