from support import Case, Clock
from deep32net.window import PairingWindow
from deep32net.errors import StateError, Locked
from deep32net.pairlock import load

class TestWindow(Case):
    def test_claim_once_and_marker_durable(self):
        with PairingWindow(self.root/'state','forge-joined',clock=Clock()) as w:
            w.claim()
            self.assertEqual(load(w.path).in_progress,1)
            with self.assertRaises(StateError):
                w.claim()
            w.succeed()
            self.assertEqual(load(w.path).in_progress,0)

    def test_unused_expiry_no_failure(self):
        with PairingWindow(self.root/'state','forge-joined',clock=Clock()) as w:
            w.deadline = w.clock.monotonic()-1
            self.assertFalse(w.can_claim())
            w.expire()
            self.assertEqual(load(w.path).values(),[0,0,0,0,0])

    def test_three_failures_lock_and_open_never_cancels(self):
        clock = Clock()
        for i in range(3):
            with PairingWindow(self.root/'state','forge-joined',clock=clock) as w:
                w.claim()
                w.fail('protocol')
        for _ in range(2):
            with self.assertRaises(Locked) as caught:
                PairingWindow(self.root/'state','forge-joined',clock=clock).open()
            self.assertEqual(caught.exception.fields['locked_seconds'],900)
        clock.now += 901
        with PairingWindow(self.root/'state','forge-joined',clock=clock) as w:
            self.assertTrue(w.can_claim())

    def test_success_keeps_failures_and_os_lock(self):
        clock = Clock()
        with PairingWindow(self.root/'state','forge-joined',clock=clock) as w:
            w.claim()
            w.fail('connection')
        with PairingWindow(self.root/'state','forge-joined',clock=clock) as w:
            with self.assertRaises(StateError):
                PairingWindow(self.root/'state','forge-joined',clock=clock).open()
            w.claim()
            w.succeed()
            self.assertEqual(load(w.path).f2,clock.now)
