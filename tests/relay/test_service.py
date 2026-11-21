import hashlib
from support import OfflineCase, Agent, Scratch, request
from deep32_relay.service import make_bridge
from deep32_relay.wire import Frame, Type, END_MESSAGE, u32, Error
from deep32_relay import messages
from deep32_relay.link import Link


class ServiceTests(OfflineCase):
    def setUp(self):
        super().setUp()
        der = b'synthetic peer DER: not a real certificate'
        self.agent = Agent()
        self.link = Link('synthetic-relay', 'synthetic-node', hashlib.sha256(der).digest(), der,
                         authenticated=True, clock=self.clock)
        self.bridge = make_bridge(self.link, self.agent,
            lambda ident: Scratch(self.root, ident), clock=self.clock)
        self.supervisor = self.bridge.supervisor
        hello = dict(proto='D32U/1', id='synthetic-node', version=1, versions=[1],
            role='node', features=['research-v1'], nonce='11'*32, sessions=[])
        self.feed(Type.HELLO, hello)
        self.feed(Type.HELLO_ACK, dict(version=1, features=['research-v1'], paired=True))
        self.link.take_output()

    def feed(self, kind, value=None, stream=0, channel=0):
        payload = messages.dumps(value) if value is not None else b''
        frame = Frame(kind, stream, channel, payload, END_MESSAGE if kind == Type.DATA else 0)
        self.link.feed(frame.encode())

    def open(self, stream, ident):
        self.feed(Type.OPEN, {'session': ident}, stream, 1)
        self.feed(Type.DATA, request(), stream, 1)

    def test_question_is_the_only_agent_trigger(self):
        self.open(1, 'synthetic-session')
        self.assertEqual(len(self.agent.starts), 0)
        self.feed(Type.DATA, {'op':'turn.start','text':'synthetic question'}, 1, 1)
        self.assertEqual(len(self.agent.starts), 1)
        session = self.supervisor.sessions[1]
        self.supervisor.output(1, session.epoch, {'type':'delta','text':'synthetic answer'})
        self.supervisor.output(1, session.epoch, {'type':'completed','usage':{'input_tokens':3,'output_tokens':4}})
        self.assertEqual(self.supervisor.last_result['status'], 'completed')
        self.assertEqual(self.agent.handles[0].cancelled, 1)

    def test_reset_cancels_active_tree_without_writing_retired_stream(self):
        self.open(1, 'synthetic-session')
        self.feed(Type.DATA, {'op':'turn.start','text':'synthetic question'}, 1, 1)
        self.link.feed(Frame(Type.RESET, 1, 1, u32(Error.CANCELLED)).encode())
        self.assertFalse(self.supervisor.sessions)
        self.assertEqual(self.agent.handles[0].cancelled, 1)
        self.assertFalse(self.supervisor.gateway.capabilities)

    def test_disconnect_clears_queue_before_cleanup(self):
        self.open(1, 'synthetic-first')
        self.open(3, 'synthetic-queued')
        self.feed(Type.DATA, {'op':'turn.start','text':'synthetic question'}, 1, 1)
        self.link.disconnect()
        self.assertFalse(self.supervisor.sessions)
        self.assertFalse(self.supervisor.queue)
        self.assertFalse(self.bridge.pending)
        self.assertEqual(self.agent.handles[0].cancelled, 1)
        self.assertEqual(len(self.agent.starts), 1)

    def test_idle_timeout_closes_wire_stream(self):
        self.open(1, 'synthetic-session')
        self.clock.advance(60)
        self.supervisor.tick()
        self.assertFalse(self.supervisor.sessions)
        self.assertTrue(self.link.streams[1].closing)
