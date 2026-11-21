"""Synthetic launch protocols, measured effects and fail-closed qualification.

The native CLI protocols remain unqualified. The Windows rows exercise the
actual OS Job mechanism with task-created Python fakes, never an agent/model.
HTTP rows exercise the actual HTTP handler with memory-only request streams.
"""
import io
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest import mock

from support import OfflineCase, rpc
from deep32_relay import engine, native, process, proxy, selftest
from deep32_relay.state import StateError


class MemorySocket:
    def __init__(self, raw):
        self.input = io.BytesIO(raw)
        self.output = bytearray()
    def makefile(self, *_):
        return self.input
    def sendall(self, raw):
        self.output.extend(raw)
    def settimeout(self, value):
        self.timeout = value


class HTTP(OfflineCase):
    def exchange(self, body, *, headers=(), method="POST", path="/mcp", defaults=True):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        values = [("Host", "127.0.0.1:23456"), ("Authorization", "Bearer " + self.cap.bearer),
                  ("Content-Type", "application/json"), ("Content-Length", str(len(raw)))] if defaults else []
        request = (method + " " + path + " HTTP/1.1\r\n" + "".join(
            k + ": " + v + "\r\n" for k, v in values + list(headers)) + "\r\n").encode() + raw
        connection = MemorySocket(request)
        self.endpoint.handler()(connection, ("127.0.0.1", 45678),
                                type("Server", (), {"server_address": ("127.0.0.1", 23456)})())
        return bytes(connection.output)

    def setUp(self):
        super().setUp()
        self.cap = self.active_cap()
        self.endpoint = proxy.HTTPProxy(self.gateway, self.cap)

    def test_http_actual_handler_initialization_notification_and_tool_call(self):
        for value in (rpc("initialize"), rpc("notifications/initialized"), rpc("tools/list", ident=2),
                      rpc("tools/call", ident=3, name="rag_status", arguments={})):
            response = self.exchange(value)
            self.assertIn(b"HTTP/1.1 202" if "id" not in value else b"HTTP/1.1 200", response)
            self.assertIn(b"Connection: close", response)
            self.assertIn(b"Mcp-Session-Id: synthetic-mcp-session", response)
        self.assertEqual(len(self.dispatch.calls), 4)

    def test_http_smuggling_duplicate_headers_and_unowned_host_never_dispatch(self):
        for headers in ((('Host', 'evil.invalid'),), (('Authorization', 'Bearer wrong'),),
                        (('Content-Length', '2'),), (('Transfer-Encoding', 'chunked'),),
                        (('Expect', '100-continue'),), (('Upgrade', 'websocket'),),
                        (('Mcp-Session-Id', 'other'),), (('MCP-Protocol-Version', 'other'),)):
            with self.subTest(headers=headers):
                response = self.exchange(rpc("initialize"), headers=headers)
                self.assertIn(b"HTTP/1.1 400", response)
        self.assertEqual(self.dispatch.calls, [])

    def test_http_bad_bearer_path_content_type_and_length_are_fixed_refusals(self):
        raw = json.dumps(rpc("initialize")).encode()
        basic = [('Host', '127.0.0.1:23456'), ('Authorization', 'Bearer wrong'),
                 ('Content-Type', 'application/json'), ('Content-Length', str(len(raw)))]
        self.assertIn(b"HTTP/1.1 401", self.exchange(raw, headers=basic, defaults=False))
        for path in ("http://evil.invalid/mcp", "/mcp?x=1", "/other"):
            self.assertIn(b"HTTP/1.1 400", self.exchange(raw, path=path))
        for length in ('-1', '99999999999999', '65537', 'abc'):
            headers = basic[:-1] + [('Content-Length', length)]
            self.assertIn(b"HTTP/1.1 400", self.exchange(raw, headers=headers, defaults=False))
        self.assertEqual(self.dispatch.calls, [])

    def test_http_revoke_and_forbidden_tool_prevent_late_effects(self):
        self.initialise(self.cap)
        baseline = len(self.dispatch.calls)
        for name in ('shell', 'Write', 'WebFetch', 'mcp__other__rag_status'):
            self.assertIn(b"HTTP/1.1 400", self.exchange(rpc("tools/call", name=name)))
        self.gateway.revoke(self.cap.session)
        self.assertIn(b"HTTP/1.1 400", self.exchange(rpc("tools/call", name='rag_status')))
        self.assertEqual(len(self.dispatch.calls), baseline)

    def test_http_does_not_offer_sse_get_delete_or_reflect_paths(self):
        for method in ('GET', 'DELETE', 'PUT', 'HEAD', 'OPTIONS', 'PATCH'):
            response = self.exchange(b'', method=method, path='/SYNTHETIC-PRIVATE-CANARY')
            self.assertIn(b"HTTP/1.1 405", response)
            self.assertNotIn(b'SYNTHETIC-PRIVATE-CANARY', response)
        self.assertEqual(self.dispatch.calls, [])

    def test_close_failure_still_closes_server_and_joins_listener(self):
        events=[]
        class BrokenServer:
            def shutdown(inner):events.append('shutdown');raise RuntimeError('synthetic')
            def server_close(inner):events.append('close')
            def active_workers(inner):return []
        class Listener:
            def join(inner,**_):events.append('join')
            def is_alive(inner):return False
        self.endpoint.server=BrokenServer()
        self.endpoint.thread=Listener()
        self.assertFalse(self.endpoint.close())
        self.assertEqual(events,['shutdown','close','join'])
        self.assertFalse(self.endpoint.close())
        self.assertFalse(self.endpoint.cleanup_complete)
        self.assertFalse(self.cap.active)

    def test_revocation_failure_is_retried_and_blocks_reusing_capability(self):
        def fail(_):raise RuntimeError('SYNTHETIC-PRIVATE-CANARY')
        self.gateway.on_revoke=fail
        self.assertFalse(self.endpoint.close())
        self.assertFalse(self.endpoint.close())
        with self.assertRaises(proxy.ProxyError):self.gateway.grant(self.cap.session,self.clock()+170)
        self.gateway.on_revoke=lambda _:None
        self.assertTrue(self.endpoint.close())
        cap=self.gateway.grant(self.cap.session,self.clock()+170)
        self.assertFalse(cap.active)


class Runner:
    requires_model = False
    def __init__(self, effects=0):
        self.effects = effects
        self.calls = []
    def run(self, case):
        self.calls.append(case)
        if case in selftest.DENIED:
            return dict(refused=True, effects=self.effects)
        return dict(passed=True, permitted_calls=1 if case == 'deep32_allowed' else 0)


class Qualification(OfflineCase):
    def test_owned_receipt_binds_release_profile_instructions_and_binary(self):
        from test_install import FakeSecurity
        from deep32_relay.state import OwnedRoot, OWNER_SCHEMA
        owner = OwnedRoot(self.root/'synthetic-owned', security=FakeSecurity())
        owner.create()
        owner.write_json('owner.json', dict(schema=OWNER_SCHEMA, install_id='1'*32))
        executable = self.root/'synthetic-agent.bin'
        executable.write_bytes(b'synthetic version one')
        install = dict(agent='codex', install_id='1'*32, release_id='2'*64, profile_alias='relay-codex',
            owned_files={'packages/'+'2'*64+'/RESEARCH.md':'3'*64,
                         'packages/'+'2'*64+'/deep32_relay/engine.py':'4'*64})
        result = selftest.qualify_install(owner, install, executable=executable, version='synthetic-1')
        self.assertEqual(result['binding_sha256'], selftest.install_binding(install))
        self.assertEqual(selftest.qualification_status(owner,install,executable=executable)['reason'], 'native-agent-unqualified')
        executable.write_bytes(b'synthetic version two')
        self.assertEqual(selftest.qualification_status(owner,install,executable=executable)['reason'], 'binary-changed')
        self.assertEqual(selftest.qualification_status(owner,install,version='synthetic-2')['reason'], 'binding-changed')
        changed = dict(install, profile_alias='relay-other')
        self.assertEqual(selftest.qualification_status(owner,changed)['state'], 'unqualified')
        agent = engine.native_from_owned(owner,install,self.gateway)
        self.assertFalse(agent.ready)
        self.assertIs(agent.gateway,self.gateway)

    def test_synthetic_success_records_all_attempts_but_never_native_qualification(self):
        executable = self.root / 'synthetic-agent.bin'
        executable.write_bytes(b'synthetic exact version one')
        runner = Runner()
        receipt = selftest.run('claude', executable=executable, version='synthetic-1',
            platform='windows', binding_sha256='a'*64, runner=runner, evidence_kind='synthetic')
        self.assertEqual(runner.calls, list(selftest.CASES))
        self.assertEqual(receipt['state'], 'unqualified')
        self.assertEqual(receipt['reason'], 'synthetic-evidence')
        self.assertTrue(all(item['pass'] for item in receipt['cases']))
        self.assertEqual(receipt['model_usage'], 'none')
        self.assertEqual(receipt['binary_sha256'], selftest.binary_digest(executable))

    def test_refusal_after_effect_is_failed_and_cannot_be_qualified(self):
        receipt = selftest.run('codex', runner=Runner(effects=1), evidence_kind='synthetic')
        self.assertEqual(receipt['reason'], 'box-selftest-failed')
        self.assertFalse(any(item['pass'] for item in receipt['cases'] if item['case'] in selftest.DENIED))

    def test_model_qualification_requires_explicit_opt_in(self):
        runner = Runner()
        runner.requires_model = True
        receipt = selftest.run('claude', runner=runner, evidence_kind='native')
        self.assertEqual(receipt['reason'], 'box-selftest-unavailable')
        self.assertEqual(runner.calls, [])
        receipt = selftest.run('claude', runner=runner, evidence_kind='native', allow_model_usage=True)
        self.assertEqual(receipt['state'], 'unqualified')
        self.assertEqual(receipt['reason'], 'native-contract-unqualified')
        self.assertEqual(receipt['model_usage'], 'opt-in')

    def test_binary_change_during_attempts_is_refused(self):
        executable = self.root / 'synthetic-agent.bin'
        executable.write_bytes(b'synthetic version one')
        class Changing(Runner):
            def run(inner, case):
                executable.write_bytes(b'synthetic version two')
                return super().run(case)
        receipt = selftest.run('codex', executable=executable, runner=Changing(), evidence_kind='synthetic')
        self.assertEqual(receipt['state'], 'unqualified')
        self.assertEqual(receipt['reason'], 'box-selftest-failed')

    def test_malformed_success_unknown_or_duplicate_case_fails_closed(self):
        value = selftest.run('codex', runner=Runner(), evidence_kind='synthetic')
        bad = dict(value, state='qualified', reason='none')
        with self.assertRaises(StateError): selftest.validate(bad)
        for field, replacement in (('extra', 'SYNTHETIC-PRIVATE-CANARY'), ('version', 'bad\nvalue')):
            bad = dict(value)
            bad[field] = replacement
            with self.assertRaises(StateError): selftest.validate(bad)
        bad = dict(value, cases=value['cases'] + [value['cases'][0]])
        with self.assertRaises(StateError): selftest.validate(bad)
        for key in value:
            bad = dict(value)
            bad[key] = []
            if key != 'cases':
                with self.subTest(key=key),self.assertRaises(StateError): selftest.validate(bad)

    def test_source_control_specs_do_not_invent_native_protocols(self):
        for adapter in (native.ClaudeCodeLauncher(), native.CodexLauncher()):
            with self.assertRaises(native.NativeError): adapter.prepare()
        self.assertIn(('--tools', ''), native.ClaudeCodeLauncher.source_flags)
        self.assertEqual(native.CodexLauncher.source_flags, ())
        for name in ('claude', 'codex'):
            agent = engine.NativeAgent(name, self.root)
            self.assertFalse(agent.ready)
            self.assertIs(agent.bind_gateway(self.gateway).gateway, self.gateway)


FAKE = '''import json,os,sys,time
from pathlib import Path
assert sys.argv[1:]==["--tools","","--strict-mcp-config"]
startup=json.loads(sys.stdin.readline())
print(json.dumps(startup["attestation"]),flush=True)
turn=json.loads(sys.stdin.readline())
Path("synthetic-question-released").write_text("yes")
if turn["question"]=="wait":time.sleep(60)
print(json.dumps({"type":"final","text":turn["question"]}),flush=True)
print(json.dumps({"type":"completed","usage":{"input_tokens":1,"output_tokens":2}}),flush=True)
'''


class SyntheticProtocol:
    """Test-only protocol; not a claimed Claude/Codex native event schema."""
    def attest(self, line):
        return json.loads(line) == {'synthetic_startup': 'verified'}
    def turn(self, question, context):
        return (json.dumps(dict(question=question, context=context))+'\n').encode()
    def events(self, line):
        return [json.loads(line)]


class ProxyHandle:
    def __init__(self): self.closes = 0
    def close(self):
        self.closes += 1
        return True


class Cleanup(OfflineCase):
    def test_proxy_exception_still_kills_tree_and_failed_cleanup_never_reports_true(self):
        plan=native.LaunchPlan((str(self.root/'synthetic-agent.bin'),),self.root,{})
        class BrokenProxy:
            def close(inner):raise RuntimeError('SYNTHETIC-PRIVATE-CANARY')
        class Tree:
            cancels=0
            def cancel(inner):inner.cancels+=1;return True
        tree=Tree()
        session=native.NativeSession(plan,SyntheticProtocol(),tree,BrokenProxy(),live=lambda:True)
        self.assertFalse(session.cancel())
        self.assertFalse(session.cancel())
        self.assertEqual(tree.cancels,2)
        self.assertFalse(session.cleanup_complete)

    def test_reader_cleanup_failure_is_retained_and_can_be_retried(self):
        plan=native.LaunchPlan((str(self.root/'synthetic-agent.bin'),),self.root,{})
        class Tree:
            def cancel(inner):return True
        class Reader:
            alive=True
            def join(inner,**_):pass
            def is_alive(inner):return inner.alive
        reader=Reader()
        session=native.NativeSession(plan,SyntheticProtocol(),Tree(),ProxyHandle(),live=lambda:True)
        session.readers=[reader]
        self.assertFalse(session.cancel())
        self.assertFalse(session.cancel())
        reader.alive=False
        self.assertTrue(session.cancel())


@unittest.skipUnless(os.name == 'nt', 'Windows Job mechanism; other hosts remain unqualified')
def _wait_until_released(folder, seconds=10.0):
    """Wait, bounded, until no process holds folder open (Windows only matters).

    A Job reports itself empty as its last process exits, a moment before Windows
    closes that process's handles, its working folder among them. Renaming the
    folder fails while such a handle is open, so a successful rename and rename
    back shows it is free to remove.
    """
    folder = Path(folder)
    probe = folder.with_name(folder.name + '-released')
    end = time.monotonic() + seconds
    while True:
        try:
            os.rename(folder, probe)
        except PermissionError:
            if time.monotonic() >= end:
                return
            time.sleep(0.05)
        else:
            os.rename(probe, folder)
            return


class WindowsTree(OfflineCase):
    def test_failed_assignment_and_failed_root_termination_cannot_certify_empty_tree(self):
        # Exercise the real startup exception path against fabricated handles;
        # no detached or otherwise uncontained real process is created.
        tree=process.WindowsJob()
        kernel=mock.Mock()
        tree.k=kernel
        kernel.CreateJobObjectW.return_value=101
        kernel.SetInformationJobObject.return_value=True
        kernel.SetHandleInformation.return_value=True
        pipe_handle=[200]
        def pipe(read,write,*_):
            pipe_handle[0]+=1;read._obj.value=pipe_handle[0]
            pipe_handle[0]+=1;write._obj.value=pipe_handle[0]
            return True
        kernel.CreatePipe.side_effect=pipe
        def attributes(buffer,count,flags,length):
            length._obj.value=64
            return buffer is not None
        kernel.InitializeProcThreadAttributeList.side_effect=attributes
        kernel.UpdateProcThreadAttribute.return_value=True
        def create(*args):
            info=args[-1]._obj
            info.process,info.thread,info.pid,info.tid=501,502,503,504
            return True
        kernel.CreateProcessW.side_effect=create
        kernel.AssignProcessToJobObject.return_value=False
        kernel.TerminateProcess.return_value=False
        kernel.TerminateJobObject.return_value=True
        def accounting(job,kind,value,*_):
            value._obj.active=0
            return True
        kernel.QueryInformationJobObject.side_effect=accounting
        kernel.WaitForSingleObject.return_value=258  # WAIT_TIMEOUT
        with self.assertRaises(process.ProcessError):
            tree.start((str(self.root/'synthetic-agent.exe'),),cwd=self.root,env={})
        kernel.AssignProcessToJobObject.assert_called_once_with(101,501)
        kernel.ResumeThread.assert_not_called()
        kernel.TerminateProcess.assert_called_with(501,1)
        self.assertFalse(tree.closed)
        self.assertEqual((tree.process,tree.job),(501,101))
        closed=[call.args[0] for call in kernel.CloseHandle.call_args_list]
        self.assertNotIn(501,closed)
        self.assertNotIn(101,closed)
        self.assertFalse(tree.cancel(grace=0))
        self.assertFalse(tree.closed)
        # A later exact-handle signal is actual cleanup evidence. It permits
        # closure on retry; merely repeating a failed termination does not.
        kernel.WaitForSingleObject.return_value=0
        self.assertTrue(tree.cancel(grace=0))
        self.assertTrue(tree.closed)
        closed=[call.args[0] for call in kernel.CloseHandle.call_args_list]
        self.assertIn(501,closed)
        self.assertIn(101,closed)

    def session(self, startup=None):
        script = self.root / 'synthetic-native.py'
        script.write_text(FAKE, encoding='utf-8')
        plan = native.LaunchPlan((str(Path(sys.executable).resolve(strict=True)), '-I', '-B', str(script), '--tools', '', '--strict-mcp-config'),
            self.root, engine.clean_environment(self.root, platform='windows'),
            (json.dumps({'attestation': startup or {'synthetic_startup':'verified'}})+'\n').encode())
        endpoint = ProxyHandle()
        result = native.NativeSession(plan, SyntheticProtocol(), process.WindowsJob(), endpoint, live=lambda:True)
        self.addCleanup(result.cancel)
        return result

    def test_actual_job_launch_argv_startup_gate_stdin_output_and_cancel(self):
        session = self.session()
        self.assertTrue(session.initialise())
        self.assertFalse((self.root/'synthetic-question-released').exists())
        question = 'SYNTHETIC-QUESTION-$(`literal`) --flag'
        session.release(question, ('synthetic history',))
        events = []
        end = time.monotonic()+5
        while not session.completed and time.monotonic()<end:
            events.extend(session.poll())
            time.sleep(0.01)
        self.assertEqual(events, [dict(type='final',text=question),
            dict(type='completed',usage=dict(input_tokens=1,output_tokens=2))])
        self.assertTrue(session.cancel())
        self.assertTrue(session.tree.closed)
        self.assertNotIn(question, ' '.join(session.plan.argv)+json.dumps(session.plan.environment))

    def test_wrong_startup_never_receives_question_and_is_killed(self):
        session = self.session({'synthetic_startup':'wrong'})
        with self.assertRaises(native.NativeError): session.initialise()
        self.assertFalse((self.root/'synthetic-question-released').exists())
        self.assertTrue(session.tree.closed)

    def test_stuck_process_tree_is_reaped_without_question_replay(self):
        session = self.session()
        session.initialise()
        session.release('wait', ())
        self.assertTrue(session.cancel())
        self.assertTrue(session.tree.closed)
        self.assertTrue(session.closed)
        self.assertTrue(session.cancel())

    def test_detached_descendants_die_and_other_job_survives(self):
        executable = str(Path(sys.executable).resolve(strict=True))
        child = '''import os,subprocess,sys,time
from pathlib import Path
grandchild=subprocess.Popen([sys.executable,"-I","-c","import time;time.sleep(60)"])
try:
    escaped=subprocess.Popen([sys.executable,"-I","-c","import time;time.sleep(60)"],creationflags=0x01000000)
except OSError:
    Path("synthetic-breakaway-refused").write_text("yes")
else:
    Path("synthetic-breakaway-started").write_text(str(escaped.pid))
Path("synthetic-descendants-ready").write_text(str(grandchild.pid))
time.sleep(60)
'''
        parent = 'import subprocess,sys;subprocess.Popen([sys.executable,"-I","-c",'+repr(child)+'],creationflags=0x8|0x200)'
        owned=process.WindowsJob()
        unrelated=process.WindowsJob()
        # Cleanups run last-in, first-out: both Jobs end, then this waits for their
        # processes to release the working folder, then the folder is removed.
        self.addCleanup(_wait_until_released, self.root)
        self.addCleanup(owned.cancel)
        self.addCleanup(unrelated.cancel)
        env=engine.clean_environment(self.root,platform='windows')
        unrelated.start((executable,'-I','-c','import time;time.sleep(60)'),cwd=self.root,env=env)
        owned.start((executable,'-I','-c',parent),cwd=self.root,env=env)
        end=time.monotonic()+5
        while not (self.root/'synthetic-descendants-ready').exists() and time.monotonic()<end:
            time.sleep(0.01)
        self.assertTrue((self.root/'synthetic-descendants-ready').exists())
        if not (self.root/'synthetic-breakaway-refused').exists():
            # A venv redirector can own an inner Job. Breaking from that inner
            # Job may succeed while the process remains in the relay's outer
            # non-breakaway Job. Verify exact kernel membership, rather than
            # assuming that a successful CreateProcess escaped our Job.
            import ctypes
            class Pids(ctypes.Structure):
                _fields_=[('assigned',owned.w.DWORD),('count',owned.w.DWORD),
                          ('values',ctypes.c_size_t*128)]
            members=Pids()
            self.assertTrue(owned.k.QueryInformationJobObject(owned.job,3,ctypes.byref(members),
                                                             ctypes.sizeof(members),None))
            self.assertLessEqual(members.assigned,128)
            self.assertLessEqual(members.count,128)
            escaped_pid=int((self.root/'synthetic-breakaway-started').read_text())
            self.assertIn(escaped_pid,set(members.values[:members.count]))
        self.assertGreaterEqual(owned.active_count(),2)
        self.assertTrue(owned.cancel())
        self.assertIsNone(unrelated.poll())
        self.assertGreaterEqual(unrelated.active_count(),1)


if __name__ == '__main__':
    unittest.main()
