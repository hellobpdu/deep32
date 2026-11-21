"""Synthetic clock transport evidence, no actual wall clock or network calls."""
from dataclasses import replace
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src' / 'diagnostics'))
from deep32_diag import peer_time, report
from deep32_diag.errors import DiagError
from deep32_diag.models import State

BASE = datetime(2026, 11, 17, tzinfo=timezone.utc).timestamp()


def samples(offset=0, rtts=(.1, .1, .1)):
    result = []
    for index, rtt in enumerate(rtts):
        wall = BASE + index * 4
        stamp = datetime.fromtimestamp(wall + rtt / 2 + offset, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        result.append(peer_time.ClockSample(wall, wall + rtt, index * 4, index * 4 + rtt,
                      stamp, str(index + 1) * 32, str(index + 1) * 32, True, True))
    return result


class PeerClock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = Path(os.environ['DEEP32_DIAG_TEST_ROOT'])
        if not base.is_absolute() or not base.is_dir() or base.is_symlink():
            raise AssertionError('explicit existing synthetic test root required')

    def test_precise_interval_and_narrowest(self):
        observation = peer_time.evaluate_samples(samples(.25, (.4, .1, .2)))
        self.assertEqual(observation.state, State.OK)
        self.assertAlmostEqual(observation.lower_ms, 199, delta=.01)
        self.assertAlmostEqual(observation.upper_ms, 301, delta=.01)
        self.assertAlmostEqual(observation.rtt_ms, 100, delta=.01)

    def test_threshold_bands_positive_and_negative(self):
        for offset, state in ((.94, State.OK), (1.01, State.WARN), (4.99, State.WARN),
                              (5.01, State.WARN), (5.06, State.FAIL), (-5.06, State.FAIL),
                              (-4.99, State.WARN), (-.94, State.OK)):
            with self.subTest(offset=offset):
                self.assertEqual(peer_time.evaluate_samples(samples(offset)).state, state)

    def test_precision_allowance_at_one_second(self):
        self.assertEqual(peer_time.evaluate_samples(samples(.999, (0, 0, 0))).state, State.OK)
        self.assertEqual(peer_time.evaluate_samples(samples(1, (0, 0, 0))).state, State.WARN)

    def test_missing_cached_wrong_nonce_or_unverified_origin_warn(self):
        for change in ({'peer_time': None}, {'echoed_nonce': 'a' * 32}, {'cache_free': False},
                       {'origin_verified': False}, {'peer_time': '2026-11-17T00:00:00Z'},
                       {'peer_time': '2026-11-17T00:00:00.000+00:00'},
                       {'peer_time': '2026-99-17T00:00:00.000Z'}):
            value = [replace(item, **change) for item in samples()]
            self.assertEqual(peer_time.evaluate_samples(value).state, State.WARN)

    def test_one_valid_sample_can_supply_narrowest_interval(self):
        value = samples()
        value[0] = replace(value[0], cache_free=False)
        value[2] = replace(value[2], peer_time=None)
        self.assertEqual(peer_time.evaluate_samples(value).state, State.OK)

    def test_reused_nonce_rejected(self):
        value = [replace(item, nonce='a' * 32, echoed_nonce='a' * 32) for item in samples()]
        self.assertEqual(peer_time.evaluate_samples(value).state, State.WARN)

    def test_excessive_roundtrip_and_uncertainty_warn(self):
        for rtt in (1.999, 2, 3):
            self.assertEqual(peer_time.evaluate_samples(samples(0, (rtt,) * 3)).state, State.WARN)

    def test_wall_jump_in_flight_rejects_samples(self):
        value = [replace(item, wall_receive=item.wall_receive + 1) for item in samples()]
        self.assertEqual(peer_time.evaluate_samples(value).state, State.WARN)

    def test_wall_jump_between_samples_rejects_run(self):
        value = samples()
        value[1] = replace(value[1], wall_send=value[1].wall_send + 10, wall_receive=value[1].wall_receive + 10)
        self.assertEqual(peer_time.evaluate_samples(value).state, State.WARN)

    def test_nonfinite_negative_monotonic_and_invalid_samples(self):
        for change in ({'wall_send': float('nan')}, {'monotonic_receive': -1}, {'nonce': []},
                       {'wall_send': 1e308, 'wall_receive': 1e308},
                       {'monotonic_send': -10, 'monotonic_receive': -9.9}):
            self.assertEqual(peer_time.evaluate_samples([replace(item, **change) for item in samples()]).state,
                             State.WARN)
        self.assertEqual(peer_time.evaluate_samples([None] * 3).state, State.WARN)

    def test_exact_three_no_retries_bounded_exceptions_and_redaction(self):
        calls = []
        def sample_once(index):
            calls.append(index)
            raise RuntimeError('synthetic-private-clock-error')
        result = peer_time.observe_clock(sample_once, reachable=True,
                                        role='forge', peer_role='workstation', alias='workstation')
        self.assertEqual(calls, [0, 1, 2])
        self.assertEqual(result.state, State.WARN)
        value = report.encode_report('forge', 'linux', 'synthetic', '2026-11-17T00:00:00Z', (result,))
        self.assertNotIn('synthetic-private-clock-error', value)

    def test_reachability_failure_blocks_clock_without_call(self):
        def forbidden(index):
            raise AssertionError('must not call unreachable peer')
        result = peer_time.observe_clock(forbidden, reachable=False, role='forge',
                                        peer_role='workstation', alias='workstation')
        self.assertEqual(result.state, State.SKIP)
        self.assertEqual(result.blocked_by, ('peer-reachability.workstation',))

    def test_qualified_three_samples_report_only_interval(self):
        value = samples()
        result = peer_time.observe_clock(lambda index: value[index], reachable=True, role='workstation',
                                        peer_role='forge', alias='forge')
        self.assertEqual(result.state, State.OK)
        encoded = report.encode_report('workstation', 'openbsd', 'synthetic', '2026-11-17T00:00:00Z', (result,))
        self.assertNotIn('1' * 32, encoded)
        self.assertNotIn('X-Deep32', encoded)

    def test_policy_cannot_be_weakened_and_three_required(self):
        for kwargs in ({'maximum_rtt': 10}, {'jump_tolerance': 1}):
            with self.assertRaises(DiagError):
                peer_time.evaluate_samples(samples(), **kwargs)
        with self.assertRaises(DiagError):
            peer_time.evaluate_samples(samples()[:2])

    def test_clock_alias_cannot_print_peer_hostname_or_wrong_role(self):
        for alias in ('synthetic-hostname', 'workstation', 'forge-0', 'forge-01', 'forge-100'):
            with self.assertRaises(DiagError):
                peer_time.observe_clock(lambda index: samples()[index], reachable=True,
                                        role='workstation', peer_role='forge', alias=alias)
        result = peer_time.observe_clock(lambda index: samples()[index], reachable=True,
                                        role='workstation', peer_role='forge', alias='forge-99')
        self.assertEqual(result.id, 'peer-clock.forge-99')


if __name__ == '__main__':
    unittest.main()
