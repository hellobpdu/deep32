"""Operator settings of the relay service: read from the environment at start, never from the install receipt.

Fake clock and synthetic sessions; no socket, no agent."""
import hashlib
import unittest

from support import OfflineCase, Agent, Scratch, request
from deep32_relay import connector, messages, settings
from deep32_relay.daemon import RelayReactor
from deep32_relay.link import Link
from deep32_relay.service import make_bridge
from deep32_relay.sessions import SessionError, Supervisor
from deep32_relay.state import StateError
from deep32_relay.wire import Frame, Type


class Loading(unittest.TestCase):
    def test_absent_and_empty_mean_the_defaults(self):
        self.assertEqual(settings.load({}), settings.Settings())
        self.assertEqual(settings.load({'DEEP32_RELAY_IDLE_SECONDS': ''}), settings.Settings())
        self.assertEqual(settings.load({'DEEP32_RELAY_IDLE_SECONDS': '   '}).idle_seconds, 900)
        self.assertEqual(settings.Settings().idle_seconds, 900)

    def test_idle_seconds_from_sixty_to_nine_hundred(self):
        for text, value in (('60', 60), ('900', 900), (' 300 ', 300), ('0090', 90)):
            with self.subTest(text=text):
                self.assertEqual(settings.load({'DEEP32_RELAY_IDLE_SECONDS': text}).idle_seconds, value)

    def test_a_value_that_is_not_valid_stops_the_start(self):
        for text in ('59', '901', '0', '-60', '6e2', '60.0', 'soon', '\uff11\uff12\uff10', '00060', '1' * 40):
            with self.subTest(text=text), self.assertRaisesRegex(StateError, '^invalid-relay-setting$'):
                settings.load({'DEEP32_RELAY_IDLE_SECONDS': text})

    def test_other_names_with_the_same_prefix_are_not_settings(self):
        environ = {'DEEP32_RELAY_TEST_ROOT': 'x', 'DEEP32_RELAY_SESSION_BEARER': 'y', 'DEEP32_RELAY_UNKNOWN': 'z'}
        self.assertEqual(settings.load(environ), settings.Settings())

    def test_the_recorded_configuration_is_what_alpha_2_recorded(self):
        # Install receipts store this configuration and every start compares it whole (validate_receipt): one changed
        # or added field and every installed relay stops with invalid-install-receipt. No setting lives here.
        self.assertEqual(connector._config('a' * 32, 'claude', 'loopback', 7452), {
            'schema': 'deep32.relay-config.v1', 'install_id': 'a' * 32, 'agent': 'claude',
            'profile_alias': 'relay-claude', 'listener': '127.0.0.1', 'port': 7452, 'transport': 'loopback',
            'intake_enabled': True, 'max_active_sessions': 1, 'max_queue': 2, 'queue_timeout_seconds': 30,
            'turn_timeout_seconds': 170, 'idle_timeout_seconds': 60, 'session_timeout_seconds': 3600,
            'turns_per_hour': 30})


class IdlePolicy(OfflineCase):
    def supervisor(self, **policy):
        self.agent, self.events = Agent(), []
        return Supervisor(self.agent, self.gateway, lambda stream, event: self.events.append((stream, event)),
                          lambda ident: Scratch(self.root, ident), clock=self.clock, **policy)

    def test_the_policy_allows_fifteen_minutes_and_no_more(self):
        self.assertEqual(self.supervisor(idle_limit=900).idle_limit, 900)
        self.assertEqual(self.supervisor().idle_limit, 60)
        for value in (901, 3600, 0, -1):
            with self.subTest(value=value), self.assertRaisesRegex(SessionError, '^invalid-policy$'):
                self.supervisor(idle_limit=value)

    def test_an_idle_session_keeps_its_history_for_the_limit(self):
        supervisor = self.supervisor(idle_limit=900)
        supervisor.open(1, 'synthetic-1', request())
        supervisor.start(1, 'first question')
        session = supervisor.sessions[1]
        supervisor.output(1, session.epoch, {'type': 'delta', 'text': 'first answer'})
        supervisor.output(1, session.epoch, {'type': 'completed', 'usage': {'input_tokens': 1, 'output_tokens': 1}})
        self.clock.advance(899)
        supervisor.tick()
        self.assertIn(1, supervisor.sessions)
        supervisor.start(1, 'second question')
        self.assertEqual(self.agent.starts[1][1], ('first question', 'first answer'))
        supervisor.output(1, session.epoch, {'type': 'delta', 'text': 'second answer'})
        supervisor.output(1, session.epoch, {'type': 'completed', 'usage': {'input_tokens': 1, 'output_tokens': 1}})
        self.clock.advance(900)
        supervisor.tick()
        self.assertNotIn(1, supervisor.sessions)
        self.assertEqual(supervisor.last_result['status'], 'completed')

    def test_a_question_after_the_limit_starts_no_agent(self):
        supervisor = self.supervisor(idle_limit=900)
        supervisor.open(1, 'synthetic-1', request())
        self.clock.advance(900)
        supervisor.start(1, 'too late')
        self.assertEqual((self.agent.starts, supervisor.sessions), ([], {}))

    def test_a_waiting_session_gets_the_slot_after_a_minute_of_idleness(self):
        supervisor = self.supervisor(idle_limit=900)
        supervisor.open(1, 'synthetic-1', request())
        self.clock.advance(40)
        supervisor.open(3, 'synthetic-3', request())          # queued behind the idle session
        self.clock.advance(19)
        supervisor.tick()
        self.assertEqual((supervisor.admitted, list(supervisor.queue)), (1, [3]))
        self.clock.advance(1)                                   # the admitted session has now been idle for 60 s
        supervisor.tick()
        self.assertNotIn(1, supervisor.sessions)
        self.assertEqual((supervisor.admitted, supervisor.sessions[3].state), (3, 'idle'))

    def test_with_nobody_waiting_the_slot_is_kept(self):
        supervisor = self.supervisor(idle_limit=900)
        supervisor.open(1, 'synthetic-1', request())
        self.clock.advance(600)
        supervisor.tick()
        self.assertEqual(supervisor.admitted, 1)


class Listener:
    peer = object()
    def start(self):
        pass
    def poll(self, timeout):
        pass
    def close(self):
        pass


class Wiring(OfflineCase):
    def link(self):
        der = b'synthetic certificate, no TLS socket'
        return Link('synthetic-relay', 'synthetic-node', hashlib.sha256(der).digest(), der, authenticated=True,
                    clock=self.clock)

    def test_the_bridge_takes_the_idle_limit_from_the_settings(self):
        bridge = make_bridge(self.link(), Agent(), lambda ident: Scratch(self.root, ident), clock=self.clock,
                             settings=settings.Settings(idle_seconds=300))
        self.assertEqual(bridge.supervisor.idle_limit, 300)
        bridge = make_bridge(self.link(), Agent(), lambda ident: Scratch(self.root, ident), clock=self.clock)
        self.assertEqual(bridge.supervisor.idle_limit, 60)

    def test_the_reactor_hands_its_settings_to_every_link(self):
        reactor = RelayReactor(Listener(), Agent(), lambda ident: Scratch(self.root, ident), clock=self.clock,
                               wall_clock=self.clock, identity_observer=lambda pid: 'windows-' + '1' * 16,
                               settings=settings.load({}))
        self.addCleanup(reactor.close)
        reactor.start()
        for _ in range(2):
            link = self.link()
            reactor.listener.on_link(link)
            payload = messages.dumps(dict(proto='D32U/1', id='synthetic-node', version=1, versions=[1], role='node',
                                          features=['research-v1'], nonce='11' * 32, sessions=[]))
            link.feed(Frame(Type.HELLO, 0, 0, payload, 0).encode())
            self.assertEqual(reactor.bridge.supervisor.idle_limit, 900)


if __name__ == '__main__':
    unittest.main()
