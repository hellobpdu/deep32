"""Offline Serve ownership, recovery and coexistence; no host commands run."""
import argparse
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('forge_publication_tested',
    Path(__file__).resolve().parents[1] / 'forms/forge_publication.py')
pub = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pub)
HOST = 'synthetic-forge.example.ts.net'
SCRATCH = None


class Refusal(Exception):
    pass


class FakeHost:
    def __init__(self, platform='linux', existing=None):
        self.platform, self.config = platform, deepcopy(existing or {})
        self.calls, self.fail_after = [], None

    def call(self, argv, *args):
        self.calls.append(argv)
        assert argv[0] == ('tailscale.exe' if self.platform == 'windows' else 'tailscale')
        if argv[1:] == ['serve', 'status', '--json']:
            return json.dumps(self.config)
        assert argv[1] == 'serve'
        switch = argv[2]
        kind, port = switch[2:].split('=')
        if argv[-1] == 'off':
            self.config.get('TCP', {}).pop(port, None)
            self.config.get('Web', {}).pop(HOST + ':' + port, None)
        else:
            assert argv[3] == '--bg'
            if kind == 'https':
                self.config.setdefault('TCP', {})[port] = {'HTTPS': True}
                self.config.setdefault('Web', {})[HOST + ':' + port] = {'Handlers': {'/': {'Proxy': argv[-1]}}}
            else:
                self.config.setdefault('TCP', {})[port] = {'TCPForward': argv[-1].removeprefix('tcp://')}
        if self.fail_after == port:
            self.fail_after = None
            raise Refusal('synthetic interruption after successful route effect')
        return ''


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH, prefix='synthetic-publication-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'config').mkdir()
        self.common = SimpleNamespace(Refusal=Refusal, safe_path=lambda p: p,
            read_json=lambda p: json.loads(p.read_text()), atomic_json=self.write,
            windows_security=lambda *a, **kw: None)

    def write(self, path, value):
        path.write_text(json.dumps(value, sort_keys=True) + '\n', encoding='ascii')

    def controller(self, host):
        return pub.Publication(self.common, self.root, host.platform, host.call)

    def settings(self, alternate=False):
        result = {'forge_accept_ct': True}
        if alternate:
            result['forge_ports'] = dict(embeddings=18081, reranker=18082,
                embeddings_https=18443, reranker_https=19443, pairing=17453, pairing_tcp=17453)
        return result

    def existing(self):
        config = {'TCP': {'7450': {'TCPForward': '127.0.0.1:7450'}}, 'Web': {}}
        for port, local in ((443, 8081), (8443, 8082), (9443, 8083), (10443, 8010), (11443, 19011)):
            config['TCP'][str(port)] = {'HTTPS': True}
            config['Web'][HOST + ':' + str(port)] = {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:' + str(local)}}}
        return config

    def test_existing_worked_example_refuses_without_even_writing_journal(self):
        for platform in ('linux', 'windows'):
            host = FakeHost(platform, self.existing())
            before = deepcopy(host.config)
            controller = self.controller(host)
            with self.assertRaisesRegex(Refusal, '443.*forge_ports.embeddings_https'):
                controller.publish('1' * 16, HOST, self.settings())
            self.assertEqual(host.config, before)
            self.assertFalse(controller.path.exists())
            self.assertTrue(all(argv[2] == 'status' for argv in host.calls))

    def test_alternate_ports_add_remove_preserve_every_existing_route_on_both_hosts(self):
        for platform in ('linux', 'windows'):
            host = FakeHost(platform, self.existing())
            before = deepcopy(host.config)
            controller = self.controller(host)
            actual = controller.publish('1' * 16, HOST, self.settings(True))
            self.assertEqual(actual, {'embeddings': 'https://' + HOST + ':18443',
                                      'reranker': 'https://' + HOST + ':19443'})
            self.assertEqual(controller.load()['added'], [0, 1, 2])
            self.assertEqual(host.config['TCP']['17453'], {'TCPForward': '127.0.0.1:17453'})
            count = len([argv for argv in host.calls if argv[2] != 'status'])
            controller.publish('1' * 16, HOST, self.settings(True))
            self.assertEqual(len([argv for argv in host.calls if argv[2] != 'status']), count)
            controller.remove()
            self.assertEqual(pub.normal(host.config), pub.normal(before))
            self.assertEqual(controller.load()['phase'], 'off')

    def test_same_target_preexisting_is_never_adopted(self):
        host = FakeHost()
        controller = self.controller(host)
        row = pub.plan(HOST, {})[0]
        host.config = controller.expected(HOST, row)
        with self.assertRaises(Refusal):
            controller.publish('1' * 16, HOST, self.settings())
        self.assertFalse(controller.path.exists())

    def test_interrupted_addition_with_uncertain_effect_is_never_adopted_or_removed(self):
        host = FakeHost()
        host.fail_after = '443'
        controller = self.controller(host)
        with self.assertRaises(Refusal):
            controller.publish('2' * 16, HOST, self.settings())
        self.assertEqual(controller.load()['added'], [])
        self.assertEqual(controller.load()['pending'], 0)
        before = deepcopy(host.config)
        with self.assertRaisesRegex(Refusal, 'uncertain ownership'):
            controller.publish('2' * 16, HOST, self.settings())
        with self.assertRaisesRegex(Refusal, 'uncertain ownership'):
            controller.remove()
        self.assertEqual(host.config, before)

    def test_interrupt_before_effect_then_identical_unowned_route_is_not_claimed(self):
        host = FakeHost()
        controller = self.controller(host)
        original = host.call
        def before_effect(argv, *args):
            if argv[2] != 'status':
                raise Refusal('synthetic interrupt before command effect')
            return original(argv, *args)
        controller.call = before_effect
        with self.assertRaises(Refusal):
            controller.publish('2' * 16, HOST, self.settings())
        self.assertEqual(controller.load()['pending'], 0)
        host.config = controller.expected(HOST, pub.plan(HOST, {})[0])
        controller.call = original
        before = deepcopy(host.config)
        with self.assertRaisesRegex(Refusal, 'uncertain ownership'):
            controller.remove()
        self.assertEqual(host.config, before)

    def test_interrupt_before_effect_can_retry_when_port_stays_empty(self):
        host = FakeHost()
        controller = self.controller(host)
        original = host.call
        def before_effect(argv, *args):
            if argv[2] != 'status':
                raise Refusal('synthetic interrupt before command effect')
            return original(argv, *args)
        controller.call = before_effect
        with self.assertRaises(Refusal):
            controller.publish('2' * 16, HOST, self.settings())
        controller.call = original
        controller.publish('2' * 16, HOST, self.settings())
        self.assertEqual(controller.load()['added'], [0, 1, 2])
        self.assertIsNone(controller.load()['pending'])

    def test_missing_interrupted_route_is_readded_and_still_owned_once(self):
        host = FakeHost()
        controller = self.controller(host)
        controller.publish('2' * 16, HOST, self.settings())
        host.config['Web'].pop(HOST + ':443')
        host.config['TCP'].pop('443')
        controller.publish('2' * 16, HOST, self.settings())
        self.assertEqual(controller.load()['added'], [0, 1, 2])

    def test_interrupted_removal_never_claims_a_later_identical_route(self):
        host = FakeHost()
        controller = self.controller(host)
        controller.publish('2' * 16, HOST, self.settings())
        host.fail_after = '7453'
        with self.assertRaises(Refusal):
            controller.remove()
        self.assertEqual(controller.load()['removing'], 2)
        host.config['TCP']['7453'] = {'TCPForward': '127.0.0.1:7453'}
        before = deepcopy(host.config)
        with self.assertRaisesRegex(Refusal, 'uncertain ownership'):
            controller.remove()
        self.assertEqual(host.config, before)

    def test_interrupted_readdition_relinquishes_the_missing_old_route(self):
        host = FakeHost()
        controller = self.controller(host)
        controller.publish('2' * 16, HOST, self.settings())
        host.config['TCP'].pop('443')
        host.config['Web'].pop(HOST + ':443')
        host.fail_after = '443'
        with self.assertRaises(Refusal):
            controller.publish('2' * 16, HOST, self.settings())
        self.assertNotIn(0, controller.load()['added'])
        self.assertEqual(controller.load()['pending'], 0)
        before = deepcopy(host.config)
        with self.assertRaisesRegex(Refusal, 'uncertain ownership'):
            controller.remove()
        self.assertEqual(host.config, before)

    def test_interrupted_removal_finishes_if_port_remains_empty(self):
        host = FakeHost()
        controller = self.controller(host)
        controller.publish('2' * 16, HOST, self.settings())
        host.fail_after = '7453'
        with self.assertRaises(Refusal):
            controller.remove()
        controller.remove()
        self.assertEqual(controller.load()['phase'], 'off')
        self.assertEqual(pub.normal(host.config), {})

    def test_changed_route_is_not_removed_and_journal_retained(self):
        host = FakeHost()
        controller = self.controller(host)
        controller.publish('2' * 16, HOST, self.settings())
        host.config['Web'][HOST + ':443']['Handlers']['/other'] = {'Text': 'unowned'}
        before = deepcopy(host.config)
        with self.assertRaises(Refusal):
            controller.remove()
        self.assertEqual(host.config, before)
        self.assertEqual(controller.load()['phase'], 'removing')

    def test_conflicting_funnel_tcp_and_foreground_refused(self):
        cases = [dict(AllowFunnel={HOST + ':443': True}),
                 dict(TCP={'443': {'TCPForward': '127.0.0.1:9999'}}),
                 dict(Foreground={'session': {'TCP': {'443': {'HTTPS': True}}}})]
        for config in cases:
            host = FakeHost(existing=config)
            with self.assertRaises(Refusal):
                self.controller(host).publish('2' * 16, HOST, self.settings())
            self.assertEqual(host.config, config)

    def test_changed_ports_require_stop_and_ct_requires_explicit_setting(self):
        host = FakeHost()
        controller = self.controller(host)
        with self.assertRaisesRegex(Refusal, 'forge_accept_ct'):
            controller.publish('2' * 16, HOST, {})
        controller.publish('2' * 16, HOST, self.settings())
        with self.assertRaisesRegex(Refusal, 'Stop the recorded Forge'):
            controller.publish('2' * 16, HOST, self.settings(True))
        controller.remove()
        controller.publish('2' * 16, HOST, self.settings(True))

    def test_damaged_journal_and_malformed_serve_never_mutate_host(self):
        host = FakeHost()
        controller = self.controller(host)
        self.write(controller.path, {'schema': 'invalid'})
        with self.assertRaises(Refusal):
            controller.publish('2' * 16, HOST, self.settings())
        controller.path.unlink()
        with patch.object(host, 'call', return_value='{"TCP":[]}'):
            with self.assertRaises(Refusal):
                self.controller(host).publish('2' * 16, HOST, self.settings())
        self.assertFalse(any(argv[2] != 'status' for argv in host.calls))

    def test_mocked_local_conflict_names_setting_and_alternates_succeed(self):
        sockets = []
        class Probe:
            def __init__(self, *args):
                sockets.append(self)
                self.closed = False
            def setsockopt(self, *args):
                pass
            def bind(self, address):
                if address[1] in (8081, 8082):
                    raise OSError('synthetic occupied port')
            def close(self):
                self.closed = True
        with self.assertRaisesRegex(ValueError, '8081.*forge_ports.embeddings'):
            pub.check_local({}, socket_factory=Probe)
        pub.check_local(self.settings(True), socket_factory=Probe)
        pub.check_local({}, owned=(8081, 8082), socket_factory=Probe)
        self.assertTrue(all(probe.closed for probe in sockets))

    def test_invalid_port_shapes_refused(self):
        for ports in ({'embeddings': True}, {'embeddings': 0}, {'pairing': 8081},
                      {'reranker_https': 443}, {'unknown': 7777}):
            with self.assertRaises(ValueError):
                pub.ports({'forge_ports': ports})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scratch', type=Path, required=True)
    args, rest = parser.parse_known_args()
    SCRATCH = args.scratch
    SCRATCH.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[__file__, *rest])
