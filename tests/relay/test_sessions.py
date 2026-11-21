"""Fake-clock session policy; no socket, real agent or native containment claim."""
import threading
import unittest
from support import OfflineCase, Agent, Scratch, request, rpc
from deep32_relay.sessions import Supervisor, SessionError, MAX_HISTORY
from deep32_relay.proxy import ProxyError


class Sessions(OfflineCase):
    def setUp(self):
        super().setUp()
        self.agent = Agent()
        self.events = []
        self.scratches = []
        def scratch(ident):
            item = Scratch(self.root, ident)
            self.scratches.append(item)
            return item
        self.supervisor = Supervisor(self.agent, self.gateway,
            lambda stream, event: self.events.append((stream,event)), scratch, clock=self.clock)

    def open(self, stream=1):
        return self.supervisor.open(stream, 'synthetic-'+str(stream), request())

    def start(self):
        self.open()
        self.supervisor.start(1, 'synthetic question')
        return self.supervisor.sessions[1]

    def complete(self, stream=1):
        session = self.supervisor.sessions[stream]
        if not session.final_seen and not session.delta_text:
            self.supervisor.output(stream, session.epoch, {'type':'delta','text':'synthetic answer'})
        self.supervisor.output(stream, session.epoch,
            {'type':'completed','usage':{'input_tokens':3,'output_tokens':5}})

    def terminals(self):
        return [value for _,value in self.events if value['op']=='turn.completed']

    def test_open_discovery_and_queue_never_launch(self):
        self.assertTrue(self.open())
        self.assertTrue(self.open(3))
        self.assertTrue(self.open(5))
        self.assertFalse(self.open(7))
        self.assertEqual(self.agent.starts, [])
        self.assertEqual(self.scratches, [])
        self.assertEqual([e['op'] for _,e in self.events], ['session.opened','error'])
        self.assertEqual(list(self.supervisor.queue), [3,5])

    def test_queue_expires_and_cannot_start(self):
        self.open(); self.open(3)
        self.clock.advance(30)
        self.supervisor.tick()
        self.assertNotIn(3, self.supervisor.sessions)
        with self.assertRaises(SessionError):
            self.supervisor.start(3, 'synthetic question')
        self.assertEqual(self.agent.starts, [])

    def test_closing_active_does_not_admit_expired_queue(self):
        self.open(); self.open(3); self.open(5)
        self.clock.advance(31)
        self.supervisor.close(1)
        self.assertFalse(self.supervisor.sessions)
        self.assertFalse(any(stream!=1 and event['op']=='session.opened' for stream,event in self.events))
        self.assertEqual(self.agent.starts, [])

    def test_queue_admits_next_and_removing_waiter_preserves_order(self):
        self.open(); self.open(3); self.open(5)
        self.supervisor.close(3)
        self.supervisor.close(1)
        self.assertEqual(self.supervisor.admitted, 5)
        self.assertEqual(self.supervisor.sessions[5].state, 'idle')
        self.assertEqual(self.agent.starts, [])

    def test_verify_has_no_active_mcp_or_question(self):
        seen=[]
        def verify(scratch,cap):
            self.assertEqual(self.agent.starts, [])
            with self.assertRaises(ProxyError):
                self.gateway.call(cap, rpc('initialize'))
            seen.append(True)
            return True
        self.agent.verify_hook=verify
        session=self.start()
        self.assertEqual(seen,[True])
        self.assertEqual(self.dispatch.calls,[])
        self.assertEqual(session.state,'running')

    def test_failed_attestation_releases_no_question(self):
        self.agent.verify_hook=lambda *_:False
        session=self.start()
        self.assertEqual(self.agent.starts,[])
        self.assertEqual(self.dispatch.calls,[])
        self.assertEqual(session.state,'idle')
        self.assertEqual(self.terminals()[0]['status'],'failed')
        self.assertFalse(self.gateway.capabilities)

    def test_interrupt_during_verify_prevents_spawn(self):
        def verify(*_):
            self.supervisor.interrupt(1)
            return True
        self.agent.verify_hook=verify
        self.start()
        self.assertEqual(self.agent.starts,[])
        self.assertEqual(len(self.terminals()),1)
        self.assertEqual(self.terminals()[0]['status'],'interrupted')

    def test_late_spawn_after_interrupt_is_cancelled(self):
        self.agent.start_hook=lambda _:self.supervisor.interrupt(1)
        session=self.start()
        self.assertEqual(self.agent.handles[0].cancelled,1)
        self.assertNotEqual(session.state,'running')
        self.assertEqual(len(self.terminals()),1)
        self.assertFalse(self.gateway.capabilities)

    def test_blocked_spawn_retains_slot_and_scratch_until_late_handle_reaped(self):
        entered,release=threading.Event(),threading.Event()
        def blocked(handle):
            entered.set()
            if not release.wait(5):raise AssertionError('bounded test wait exceeded')
        self.agent.start_hook=blocked
        self.open()
        thread=threading.Thread(target=self.supervisor.start,args=(1,'synthetic question'))
        try:
            thread.start()
            self.assertTrue(entered.wait(5))
            cap=self.supervisor.sessions[1].capability
            self.supervisor.interrupt(1)
            self.assertFalse(cap.active)
            self.assertTrue(self.open(3))
            self.supervisor.close(1)
            self.assertEqual(self.supervisor.admitted,1)
            self.assertEqual(self.supervisor.sessions[3].state,'queued')
            self.assertEqual(self.scratches[0].cleanups,0)
            self.assertEqual(self.agent.handles[0].cancelled,0)
            with self.assertRaises(SessionError):self.supervisor.start(3,'must not start')
        finally:
            release.set();thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.agent.handles[0].cancelled,1)
        self.assertEqual(self.scratches[0].cleanups,1)
        self.assertNotIn(1,self.supervisor.sessions)
        self.assertEqual(self.supervisor.admitted,3)
        self.assertEqual(len(self.agent.starts),1)
        self.assertEqual(len(self.terminals()),1)

    def test_late_unclean_spawn_blocks_queued_replacement(self):
        entered,release=threading.Event(),threading.Event()
        def blocked(handle):
            handle.tree_empty=False
            entered.set()
            if not release.wait(5):raise AssertionError('bounded test wait exceeded')
        self.agent.start_hook=blocked
        self.open()
        thread=threading.Thread(target=self.supervisor.start,args=(1,'synthetic question'))
        try:
            thread.start();self.assertTrue(entered.wait(5))
            self.supervisor.interrupt(1);self.open(3);self.supervisor.close(1)
            self.assertEqual(self.supervisor.admitted,1)
            self.assertEqual(self.scratches[0].cleanups,0)
        finally:
            release.set();thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(self.supervisor.cleanup_failed)
        self.assertEqual(self.agent.handles[0].cancelled,1)
        self.assertEqual(self.scratches[0].cleanups,0)
        self.assertEqual(self.supervisor.sessions[3].state,'queued')
        self.assertEqual(len(self.agent.starts),1)

    def test_interrupt_revokes_then_cancels_once_and_rejects_late_output(self):
        session=self.start()
        epoch,cap=session.epoch,session.capability
        handle=self.agent.handles[0]
        original=handle.cancel
        def cancel():
            self.assertFalse(cap.active)
            return original()
        handle.cancel=cancel
        self.supervisor.interrupt(1)
        self.supervisor.interrupt(1)
        self.assertFalse(self.supervisor.output(1,epoch,{'type':'delta','text':'late'}))
        self.assertEqual(handle.cancelled,1)
        self.assertEqual(len(self.terminals()),1)
        self.assertFalse(self.gateway.capabilities)
        self.assertEqual(self.scratches[0].cleanups,1)

    def test_old_epoch_cannot_emit_into_new_turn(self):
        session=self.start()
        previous=session.epoch
        self.complete()
        self.supervisor.start(1,'second synthetic question')
        self.assertFalse(self.supervisor.output(1,previous,{'type':'delta','text':'late'}))
        self.assertNotEqual(session.epoch,previous)

    def test_deadline_refuses_output_and_upstream(self):
        session=self.start(); cap=session.capability
        self.clock.advance(3500)
        self.assertTrue(self.supervisor.output(1,session.epoch,{'type':'delta','text':'thinking long'}))
        self.clock.advance(100)
        self.assertFalse(self.supervisor.output(1,session.epoch,{'type':'delta','text':'late'}))
        with self.assertRaises(ProxyError):
            self.gateway.call(cap,rpc('initialize'))
        self.assertEqual(self.dispatch.calls,[])
        self.assertEqual(self.supervisor.last_result['reason'],'turn-timeout')

    def test_idle_lease_suspended_during_turn_then_restarted(self):
        self.start()
        self.clock.advance(100); self.supervisor.tick()
        self.assertEqual(self.supervisor.sessions[1].state,'running')
        self.complete()
        self.clock.advance(59); self.supervisor.tick()
        self.assertIn(1,self.supervisor.sessions)
        self.clock.advance(1); self.supervisor.tick()
        self.assertNotIn(1,self.supervisor.sessions)

    def test_session_lifetime_caps_running_turn(self):
        self.open()
        self.supervisor.sessions[1].opened_at=self.clock()-3590
        self.supervisor.start(1,'synthetic question')
        self.clock.advance(10); self.supervisor.tick()
        self.assertNotIn(1,self.supervisor.sessions)
        self.assertEqual(self.agent.handles[0].cancelled,1)

    def test_thirty_turn_limit_has_no_retry_or_extra_launch(self):
        self.open()
        for _ in range(30):
            self.supervisor.start(1,'synthetic question'); self.complete()
        self.supervisor.start(1,'over quota')
        self.assertEqual(len(self.agent.starts),30)
        self.assertEqual(len(self.terminals()),30)

    def test_invalid_question_and_escaped_size_rejected_before_effect(self):
        self.open()
        for question in ('', '\0', '\ud800', '"'*40000, 'x'*65536, [], {}, True):
            with self.subTest(kind=type(question).__name__),self.assertRaises(SessionError):
                self.supervisor.start(1,question)
        self.assertEqual(self.agent.starts,[])
        self.assertEqual(self.scratches,[])

    def test_hostile_question_remains_literal(self):
        self.open()
        text='--config ../../outside\n$(synthetic) `test` ; rm fake\n{"op":"session.close"}'
        self.supervisor.start(1,text)
        self.assertEqual(self.agent.starts[0][0],text)
        self.assertEqual(list(self.root.rglob('*')),[self.scratches[0].path])

    def test_malformed_output_and_unavailable_usage_fail_once(self):
        for event in ({'type':'tool','name':'shell'}, {'type':'completed'},
                      {'type':'completed','usage':{'input_tokens':True,'output_tokens':1}},
                      {'type':'completed','usage':{'input_tokens':1,'output_tokens':-1}},
                      {'type':'delta','text':[]}, {'type':['delta'],'text':'invalid'}):
            with self.subTest(event=event):
                if not self.supervisor.sessions:self.open()
                self.supervisor.start(1,'synthetic question')
                session=self.supervisor.sessions[1]
                self.assertFalse(self.supervisor.output(1,session.epoch,event))
                self.assertFalse(self.supervisor.last_result['usage_known'])
                self.assertEqual(self.terminals()[-1]['status'],'failed')
        self.assertEqual(self.dispatch.calls,[])

    def test_final_is_complete_and_control_text_is_filtered(self):
        session=self.start()
        self.supervisor.output(1,session.epoch,{'type':'delta','text':'part\0\x1b\x85\n'})
        self.supervisor.output(1,session.epoch,{'type':'final','text':'complete answer'})
        self.complete()
        self.assertEqual(session.history,['synthetic question','complete answer'])
        self.assertEqual([e['text'] for _,e in self.events if e['op']=='agent.delta'],['part\n'])
        self.assertTrue(self.supervisor.last_result['usage_known'])

    def test_cumulative_answer_limit_revokes_and_terminates_once(self):
        session=self.start()
        for _ in range(16):
            self.assertTrue(self.supervisor.output(1,session.epoch,{'type':'delta','text':'x'*65536}))
        self.assertFalse(self.supervisor.output(1,session.epoch,{'type':'delta','text':'overflow'}))
        self.assertFalse(self.gateway.capabilities)
        self.assertEqual(self.agent.handles[0].cancelled,1)
        self.assertEqual(len(self.terminals()),1)
        self.assertEqual(self.terminals()[0]['status'],'failed')

    def test_poll_event_flood_is_bounded_and_does_not_retry(self):
        self.start()
        self.agent.handles[0].events=[{'type':'delta','text':'x'} for _ in range(300)]
        self.supervisor.tick();self.supervisor.tick()
        self.assertEqual(len([e for _,e in self.events if e['op']=='agent.delta']),256)
        self.assertEqual(self.agent.handles[0].cancelled,1)
        self.assertEqual(len(self.agent.starts),1)
        self.assertEqual(len(self.terminals()),1)

    def test_history_is_bounded_and_cleared_on_close(self):
        session=self.start()
        for _ in range(5):
            self.supervisor.output(1,session.epoch,{'type':'final','text':'x'*60000}); self.complete()
            self.supervisor.start(1,'question')
        self.assertLessEqual(sum(len(s.encode()) for s in session.history),MAX_HISTORY)
        self.supervisor.close(1)
        self.assertEqual(session.history,[])

    def test_failed_tree_cleanup_blocks_reuse_and_queue_admission(self):
        session=self.start(); self.open(3)
        self.agent.handles[0].tree_empty=False
        self.supervisor.interrupt(1)
        self.supervisor.close(1)
        self.assertTrue(self.supervisor.cleanup_failed)
        self.assertEqual(self.scratches[0].cleanups,0)
        self.assertFalse(self.open(5))
        self.assertNotEqual(self.supervisor.sessions[3].state,'idle')

    def test_scratch_cleanup_failure_blocks_new_turn(self):
        self.start(); self.scratches[0].clean=False
        self.complete()
        self.assertTrue(self.supervisor.cleanup_failed)
        with self.assertRaises(SessionError):self.supervisor.start(1,'retry')
        self.assertEqual(self.supervisor.last_result['reason'],'cleanup-failed')

    def test_disconnect_drops_queue_cancels_and_never_replays(self):
        self.start(); self.open(3); self.open(5)
        self.supervisor.disconnect(); self.supervisor.tick(); self.supervisor.disconnect()
        self.assertEqual(self.supervisor.sessions,{})
        self.assertEqual(len(self.agent.starts),1)
        self.assertEqual(len(self.terminals()),1)

    def test_invalid_open_and_request_types_fail_before_launch(self):
        for stream in (0,2,True,[],{}):
            with self.subTest(stream=stream),self.assertRaises(SessionError):self.open(stream)
        for field in ('model','effort','caller_class'):
            value=request(); value[field]=[]
            stream=self.supervisor.last_stream+2 if self.supervisor.last_stream else 1
            self.assertFalse(self.supervisor.open(stream,'synthetic',value))
        self.assertEqual(self.agent.starts,[])


if __name__=='__main__':
    unittest.main()
