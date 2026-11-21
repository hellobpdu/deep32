"""State isolation and cold-start readiness, with no network or privileged calls."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit_support  # noqa: E402,F401  src/gpu-pack and the shared helpers (kit_support.py)

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

KIT = Path(__file__).resolve().parents[2] / 'src' / 'gpu-pack'


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, KIT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


state = load('builder_state_test', 'containers/builder-state.py')
ready = load('builder_ready_test', 'containers/builder/wait_services.py')


class StateTests(unittest.TestCase):
    def test_working_directories_only_are_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / 'private'
            private.mkdir()
            material = private / 'synthetic-marker.txt'
            material.write_text('synthetic non-secret marker', encoding='ascii')
            with mock.patch.object(state.os, 'geteuid', return_value=0, create=True), \
                 mock.patch.object(state.os, 'chown', create=True) as owner:
                state.prepare(root)
            self.assertEqual(owner.call_args_list, [mock.call(root / name, 10032, 10032)
                                                   for name in state.DIRECTORIES])
            self.assertEqual(material.read_text(encoding='ascii'), 'synthetic non-secret marker')
            self.assertNotIn(private, [call.args[0] for call in owner.call_args_list])

    def test_unprivileged_preparation_refused_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'not-created'
            with mock.patch.object(state.os, 'geteuid', return_value=1000, create=True):
                with self.assertRaises(PermissionError):
                    state.prepare(root)
            self.assertFalse(root.exists())


class ReadyTests(unittest.TestCase):
    def test_waits_for_each_existing_service(self):
        attempts = {}
        clock = [0]
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
        def open_url(url, timeout):
            attempts[url] = attempts.get(url, 0) + 1
            if attempts[url] == 1:
                raise OSError('synthetic cold start')
            return Response()
        def sleep(value): clock[0] += value
        ready.wait(10, opener=open_url, clock=lambda: clock[0], sleep=sleep)
        self.assertEqual(attempts, {url: 2 for url in ready.URLS})

    def test_readiness_timeout_is_bounded(self):
        clock = [0]
        def sleep(value): clock[0] += value
        def refuse(*args, **kwargs): raise OSError('synthetic unavailable')
        with self.assertRaisesRegex(TimeoutError, 'did not become ready'):
            ready.wait(3, opener=refuse, clock=lambda: clock[0], sleep=sleep)
        self.assertEqual(clock[0], 3)


if __name__ == '__main__':
    unittest.main()
