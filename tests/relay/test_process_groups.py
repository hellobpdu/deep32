"""R15 POSIX process/session-group boundary with fake children only."""
import io
import os
import signal
import subprocess
import unittest
from unittest.mock import Mock, patch

import test_install
from deep32_relay import process


class ProcessGroups(unittest.TestCase):
    def setUp(self):
        # The fake POSIX boundary is also tested on Windows, whose signal
        # module does not export SIGKILL. No native signal is sent here.
        kill_signal = patch.object(signal, 'SIGKILL', 9, create=True)
        kill_signal.start()
        self.addCleanup(kill_signal.stop)

    def child(self):
        child = Mock(pid=12345)
        child.stdin, child.stdout, child.stderr = (io.BytesIO() for _ in range(3))
        child.poll.return_value = None
        child.wait.return_value = -9
        return child

    def start(self, child):
        tree = process.PosixGroup()
        executable = str(test_install.SCRATCH / 'synthetic-agent')
        with patch.object(process.subprocess, 'Popen', return_value=child) as launch:
            self.assertIs(tree.start((executable, '--synthetic'), cwd=test_install.SCRATCH,
                                     env={'SYNTHETIC': 'test'}), tree)
        args, options = launch.call_args
        self.assertEqual(args[0], [executable, '--synthetic'])
        self.assertTrue(options['start_new_session'])
        self.assertTrue(options['close_fds'])
        self.assertFalse(options['shell'])
        self.assertEqual(options['env'], {'SYNTHETIC': 'test'})
        return tree

    def test_cancel_kills_owned_group_then_waits_and_closes(self):
        child = self.child()
        tree = self.start(child)
        calls = []
        def kill(group, sig):
            calls.append(('kill', group, sig))
            self.assertFalse(child.stdout.closed)
        def wait(**options):
            calls.append(('wait', options['timeout']))
            self.assertFalse(child.stdout.closed)
            return -9
        child.wait.side_effect = wait
        with patch.object(process.os, 'killpg', side_effect=kill, create=True):
            self.assertTrue(tree.cancel())
            self.assertTrue(tree.close())
        self.assertEqual(calls, [('kill', 12345, signal.SIGKILL), ('wait', 2.0)])
        self.assertTrue(tree.closed)
        self.assertTrue(all(stream.closed for stream in (tree.stdin, tree.stdout, tree.stderr)))

    def test_exited_leader_does_not_skip_group_kill(self):
        child = self.child()
        child.poll.return_value = 0
        tree = self.start(child)
        self.assertEqual(tree.poll(), 0)
        with patch.object(process.os, 'killpg', create=True) as kill:
            self.assertTrue(tree.cancel())
        kill.assert_called_once_with(12345, signal.SIGKILL)
        child.wait.assert_called_once()

    def test_wait_failure_retains_open_tree_for_retry(self):
        child = self.child()
        tree = self.start(child)
        child.wait.side_effect = subprocess.TimeoutExpired('synthetic', 0)
        with patch.object(process.os, 'killpg', create=True):
            self.assertFalse(tree.cancel(grace=0))
            self.assertFalse(tree.closed)
            self.assertFalse(tree.stdout.closed)
            child.wait.side_effect = None
            self.assertTrue(tree.cancel())

    def test_absent_group_is_still_waited(self):
        child = self.child()
        tree = self.start(child)
        with patch.object(process.os, 'killpg', side_effect=ProcessLookupError, create=True):
            self.assertTrue(tree.cancel())
        child.wait.assert_called_once()

    def test_permission_failure_never_claims_cleanup(self):
        child = self.child()
        tree = self.start(child)
        with patch.object(process.os, 'killpg', side_effect=PermissionError, create=True):
            self.assertFalse(tree.cancel())
        self.assertFalse(tree.closed)
        child.wait.assert_not_called()

    def test_native_selection_is_limited_to_matching_host_family(self):
        with patch.object(process.os, 'name', 'posix'):
            for platform in ('linux', 'macos'):
                self.assertIsInstance(process.native_tree(platform), process.PosixGroup)
        with patch.object(process.os, 'name', 'nt'):
            with self.assertRaises(process.ProcessError):
                process.native_tree('linux')


if __name__ == '__main__':
    unittest.main()
