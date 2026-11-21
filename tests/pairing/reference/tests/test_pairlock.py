import os
import unittest
from unittest.mock import patch
from support import Case, fixture, Clock
from deep32net import pairlock
from deep32net.errors import StateError
from deep32net.files import atomic_write
from deep32net.window import PairingWindow

class TestPairlock(Case):
    def test_six_hand_computed_cases_and_exact_text(self):
        rows = fixture('pairlock-v1-vectors.json')['cases']
        self.assertGreaterEqual(len(rows),6)
        for row in rows:
            state = pairlock.State(*row['before'])
            self.assertEqual(pairlock.failed(state,row['time']),row['remaining'])
            self.assertEqual(state.values(),row['after'])
            self.assertEqual(pairlock.text(state),row['file_text'])
            path = self.root/'lockstate'
            pairlock.save(path,state)
            self.assertEqual(path.read_bytes(),row['file_text'].encode())
            self.assertEqual(pairlock.load(path),state)

    def test_every_load_refusal(self):
        for text in fixture('pairlock-v1-vectors.json')['load_refusals']:
            path = self.root/'lockstate'
            atomic_write(path,bytes.fromhex(text))
            before = path.read_bytes()
            with self.assertRaises(StateError):
                pairlock.load(path)
            self.assertEqual(path.read_bytes(),before)
        self.assertEqual(pairlock.load(self.root/'missing'),pairlock.State())
        self.assertEqual(pairlock.parse(b'D32PAIR2 0 0 0 0 0'),pairlock.State())
        self.assertEqual(pairlock.parse(b'D32PAIR2 00 00 00 00 00'),pairlock.State())
        with self.assertRaises(StateError):
            pairlock.load(self.root)

    def test_interrupted_claim_counted_once(self):
        w = PairingWindow(self.root/'state','forge-joiner',clock=Clock())
        w.open()
        w.claim()
        w.close()
        for _ in range(2):
            with PairingWindow(self.root/'state','forge-joiner',clock=Clock()) as fresh:
                self.assertEqual(fresh.state.values(),[0,0,1700000000,0,0])

    @unittest.skipUnless(os.name == 'posix','POSIX-only pairlock permissions and ownership')
    def test_posix_file_safety(self):
        path = self.root/'state'
        pairlock.save(path,pairlock.State())
        os.chmod(path,0o644)
        with self.assertRaises(StateError):
            pairlock.load(path)
        os.chmod(path,0o600)
        with patch('os.geteuid',return_value=os.getuid()+1):
            with self.assertRaises(StateError):
                pairlock.load(path)
        os.link(path,self.root/'hardlink')
        with self.assertRaises(StateError):
            pairlock.load(path)

    def test_clock_edges(self):
        state = pairlock.State(0,0,1000,0,1)
        pairlock.failed(state,2800)
        self.assertEqual(state.values()[:3],[0,1000,2800])
        self.assertEqual(pairlock.remaining(pairlock.State(until=2**63-1),0),2**31-1)
        with self.assertRaises(StateError):
            pairlock.failed(state,-1)
