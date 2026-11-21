"""Three-sample peer clock interval policy, with no transport or clock writes.

Only a qualified existing service boundary can provide origin_verified/cache
evidence.  Cached status dates and HTTP Date never enter this algorithm.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import math
import re

from .errors import DiagError, bounded
from .models import State
from .probes import _peer_alias
from .report import Reason, row


@dataclass(frozen=True)
class ClockSample:
    wall_send: float
    wall_receive: float
    monotonic_send: float
    monotonic_receive: float
    peer_time: str | None = field(default=None, repr=False)
    nonce: str | None = field(default=None, repr=False)
    echoed_nonce: str | None = field(default=None, repr=False)
    origin_verified: bool = False
    cache_free: bool = False


@dataclass(frozen=True)
class ClockObservation:
    state: State
    lower_ms: float | None = None
    upper_ms: float | None = None
    rtt_ms: float | None = None


def _interval(sample, maximum_rtt, jump_tolerance):
    if type(sample) is not ClockSample:
        return None
    numbers = (sample.wall_send, sample.wall_receive, sample.monotonic_send, sample.monotonic_receive)
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in numbers):
        return None
    if (any(value < 0 for value in numbers) or
            any(value > 253402300799.999 for value in (sample.wall_send, sample.wall_receive))):
        return None
    rtt = sample.monotonic_receive - sample.monotonic_send
    wall_elapsed = sample.wall_receive - sample.wall_send
    if (not 0 <= rtt <= maximum_rtt or abs(wall_elapsed - rtt) > jump_tolerance
            or sample.origin_verified is not True or sample.cache_free is not True
            or type(sample.nonce) is not str or re.fullmatch('[0-9a-f]{32}', sample.nonce) is None
            or sample.echoed_nonce != sample.nonce
            or type(sample.peer_time) is not str
            or re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z', sample.peer_time) is None):
        return None
    try:
        peer = datetime.fromisoformat(sample.peer_time.replace('Z', '+00:00'))
        delta = peer - datetime(1970, 1, 1, tzinfo=timezone.utc)
        peer_ms = delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000
    except (ValueError, OverflowError, OSError):
        return None
    # Decimal subtraction preserves the specified millisecond allowance at
    # exact +/-1 and +/-5 boundaries despite large epoch float magnitudes.
    stamp = Decimal(peer_ms) / 1000
    lower = float(stamp - Decimal(str(sample.wall_receive)) - Decimal('.001'))
    upper = float(stamp - Decimal(str(sample.wall_send)) + Decimal('.001'))
    if upper < lower or upper - lower > 2.0:
        return None
    return (lower, upper, rtt)


@bounded('invalid-observation')
def evaluate_samples(samples, *, maximum_rtt=2.0, jump_tolerance=.002):
    """Narrowest qualified interval; insufficient evidence remains WARN.

    A 2 ms elapsed-clock tolerance admits sampling resolution, never an NTP
    step. Three no-retry samples are mandatory; repeated nonces are rejected.
    """
    if (type(samples) not in (tuple, list) or len(samples) != 3
            or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                   for value in (maximum_rtt, jump_tolerance))
            or maximum_rtt > 2.0 or jump_tolerance > .002):
        raise DiagError('invalid-observation')
    nonces = [sample.nonce for sample in samples if type(sample) is ClockSample and type(sample.nonce) is str]
    if len(set(nonces)) != len(nonces):
        return ClockObservation(State.WARN)
    # An NTP/manual step between requests is just as unsafe as one in flight.
    for left, right in zip(samples, samples[1:]):
        if type(left) is ClockSample and type(right) is ClockSample:
            clocks = (left.wall_receive, right.wall_send, left.monotonic_receive, right.monotonic_send)
            if all(type(value) in (int, float) and math.isfinite(value) for value in clocks):
                elapsed = right.monotonic_send - left.monotonic_receive
                if elapsed < 0 or abs(right.wall_send - left.wall_receive - elapsed) > jump_tolerance:
                    return ClockObservation(State.WARN)
    intervals = [value for sample in samples
                 if (value := _interval(sample, maximum_rtt, jump_tolerance)) is not None]
    if not intervals:
        return ClockObservation(State.WARN)
    lower, upper, rtt = min(intervals, key=lambda item: (item[1] - item[0], item[2], item[0]))
    state = (State.OK if lower >= -1 and upper <= 1 else
             State.FAIL if lower > 5 or upper < -5 else State.WARN)
    return ClockObservation(state, lower * 1000, upper * 1000, rtt * 1000)


@bounded('invalid-observation')
def observe_clock(sample_once, *, reachable, role, peer_role, alias):
    """Collect exactly three bounded calls, never retries or arbitrary URLs.

    `sample_once(index)` is a qualified enum-peer/route adapter which creates a
    fresh nonce and captures local wall/monotonic times around the response.
    """
    if (role not in ('workstation', 'forge', 'headend') or peer_role not in ('workstation', 'forge', 'headend')
            or type(reachable) is not bool or not _peer_alias(alias, peer_role)):
        raise DiagError('invalid-observation')
    check = 'peer-clock.' + alias
    if not reachable:
        return row(check, State.SKIP, Reason.BLOCKED, {'peer_role': peer_role},
                   blocked_by=('peer-reachability.' + alias,), policy=role + '.peer-clock')
    samples = []
    for index in range(3):
        try:
            samples.append(sample_once(index))
        except Exception:
            samples.append(None)
    observation = evaluate_samples(samples)
    values = {'peer_role': peer_role}
    if observation.lower_ms is not None:
        values.update(offset_lower_ms=observation.lower_ms, offset_upper_ms=observation.upper_ms,
                      rtt_ms=observation.rtt_ms)
    return row(check, observation.state, Reason.PASSED if observation.state is State.OK else Reason.UNAVAILABLE,
               values, policy=role + '.peer-clock')
