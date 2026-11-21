import hashlib
import threading

from support import OfflineCase, Agent, Scratch, request
from deep32_relay.daemon import RelayReactor, stop_requested
from deep32_relay.link import Link
from deep32_relay import messages
from deep32_relay.state import StateError
from deep32_relay.wire import Frame, Type, END_MESSAGE


class Listener:
    peer = object()
    def __init__(self):
        self.starts = self.closes = self.polls = 0
    def start(self):
        self.starts += 1
    def poll(self, timeout):
        self.polls += 1
    def close(self):
        self.closes += 1


class ReactorTests(OfflineCase):
    def setUp(self):
        super().setUp()
        self.listener = Listener()
        self.agent = Agent()
        self.snapshots = []
        self.reactor = RelayReactor(self.listener, self.agent,
            lambda ident: Scratch(self.root, ident), clock=self.clock,
            wall_clock=self.clock, publish=self.snapshots.append,
            identity_observer=lambda pid: 'windows-' + '1'*16,
            install=dict(install_id='a'*32, agent='codex', release_id='b'*64,
                         capability_id='unqualified'))
        self.addCleanup(self.reactor.close)
        self.reactor.start()
        self.link = self.new_link()

    def new_link(self):
        der = b'synthetic certificate, no TLS socket'
        link = Link('synthetic-relay', 'synthetic-node', hashlib.sha256(der).digest(), der,
                    authenticated=True, clock=self.clock)
        self.listener.on_link(link)
        self.feed(link, Type.HELLO, dict(proto='D32U/1', id='synthetic-node', version=1,
            versions=[1], role='node', features=['research-v1'], nonce='11'*32, sessions=[]))
        self.feed(link, Type.HELLO_ACK, dict(version=1, features=['research-v1'], paired=True))
        link.take_output()
        return link

    def feed(self, link, kind, value=None, stream=0, channel=0):
        payload = messages.dumps(value) if value is not None else b''
        link.feed(Frame(kind, stream, channel, payload,
                        END_MESSAGE if kind == Type.DATA else 0).encode())

    def open(self):
        self.feed(self.link, Type.OPEN, {'session':'synthetic-session'}, 1, 1)
        self.feed(self.link, Type.DATA, request(), 1, 1)

    def test_listener_and_open_never_launch_an_agent(self):
        self.open()
        self.reactor.step(0)
        self.assertEqual(self.listener.starts, 1)
        self.assertEqual(self.agent.starts, [])
        self.assertEqual(self.agent.verifies, [])

    def test_native_startup_does_not_block_transport_or_cancel(self):
        self.open()
        entered, release = threading.Event(), threading.Event()
        def verify(scratch, cap):
            entered.set()
            return release.wait(2)
        self.agent.verify_hook = verify
        self.addCleanup(release.set)
        self.feed(self.link, Type.DATA, {'op':'turn.start','text':'synthetic question'}, 1, 1)
        self.assertTrue(entered.wait(2))
        self.reactor.step(0)
        self.assertEqual(self.listener.polls, 1)
        self.feed(self.link, Type.DATA, {'op':'turn.interrupt'}, 1, 1)
        self.assertFalse(self.reactor.bridge.supervisor.gateway.capabilities)
        release.set()
        self.reactor.jobs.join()
        self.assertEqual(self.agent.starts, [])

    def test_cancelled_queued_ticket_never_starts(self):
        self.open()
        # Hold the dispatch lock so the worker cannot accept its queued ticket.
        with self.reactor.lock:
            self.feed(self.link, Type.DATA, {'op':'turn.start','text':'synthetic question'}, 1, 1)
            self.feed(self.link, Type.DATA, {'op':'turn.interrupt'}, 1, 1)
        self.reactor.jobs.join()
        self.assertEqual(self.agent.starts, [])
        self.assertEqual(self.agent.verifies, [])

    def test_turn_completes_and_stop_cancels_tree(self):
        self.open()
        self.feed(self.link, Type.DATA, {'op':'turn.start','text':'synthetic question'}, 1, 1)
        self.reactor.jobs.join()
        self.assertEqual(len(self.agent.starts), 1)
        self.agent.handles[0].events = [dict(type='delta', text='synthetic answer'),
            dict(type='completed', usage=dict(input_tokens=2, output_tokens=3))]
        self.reactor.step(0)
        self.assertEqual(self.reactor.last_result['state'], 'completed')
        self.assertTrue(self.reactor.close())
        self.assertEqual(self.agent.handles[0].cancelled, 1)
        self.assertFalse(self.snapshots[-1]['running'])
        self.assertEqual(self.snapshots[-1]['pid'], 0)
        self.assertEqual(self.listener.closes, 1)

    def test_reconnect_preserves_hourly_quota(self):
        self.reactor.turns.extend([self.clock()] * 30)
        self.link.disconnect()
        self.link = self.new_link()
        self.open()
        self.feed(self.link, Type.DATA, {'op':'turn.start','text':'synthetic question'}, 1, 1)
        self.reactor.jobs.join()
        self.assertEqual(self.agent.starts, [])
        self.assertEqual(len(self.reactor.bridge.supervisor.turns), 30)

    def test_status_changes_are_throttled_and_heartbeat_is_five_seconds(self):
        self.reactor.step(0)
        self.assertEqual(len(self.snapshots), 1)
        self.clock.advance(1)
        self.reactor.step(0)
        self.assertEqual(len(self.snapshots), 2)
        self.assertEqual(self.snapshots[-1]['link_state'], 'connected')
        self.clock.advance(4)
        self.reactor.step(0)
        self.assertEqual(len(self.snapshots), 2)
        self.clock.advance(1)
        self.reactor.step(0)
        self.assertEqual(len(self.snapshots), 3)

    def test_cleanup_failure_disables_listener(self):
        self.open()
        self.feed(self.link, Type.DATA, {'op':'turn.start','text':'synthetic question'}, 1, 1)
        self.reactor.jobs.join()
        self.agent.handles[0].tree_empty = False
        self.assertFalse(self.reactor.close())
        self.assertTrue(self.reactor.cleanup_failed)
        self.assertEqual(self.snapshots[-1]['link_state'], 'error')
        self.assertNotEqual(self.snapshots[-1]['pid'], 0)

    def test_disconnect_error_still_closes_listener(self):
        def broken():
            raise RuntimeError('synthetic private diagnostic')
        self.reactor.bridge.disconnect = broken
        self.assertFalse(self.reactor.close())
        self.assertEqual(self.listener.closes, 1)
        self.assertNotEqual(self.snapshots[-1]['pid'], 0)

    def test_unknown_process_identity_cannot_publish_running(self):
        self.reactor.identity_observer = lambda pid: None
        self.assertFalse(self.reactor.snapshot()['running'])


class StopTests(OfflineCase):
    def test_stop_request_requires_exact_owned_install(self):
        class Owner:
            def read_json(self, path, limit):
                return dict(schema='deep32.relay-stop.v1', install_id='a'*32)
        self.assertTrue(stop_requested(Owner(), 'a'*32))
        with self.assertRaises(StateError):
            stop_requested(Owner(), 'b'*32)

    def test_missing_stop_request_is_passive(self):
        class Owner:
            def read_json(self, path, limit):
                raise FileNotFoundError
        self.assertFalse(stop_requested(Owner(), 'a'*32))
