"""Float32 scores in the Workstation's four-accumulator SSE order.

Every product or sum of two float32 values is computed in binary64 and
rounded once to float32. Binary64 has 53 >= 2*24+2 significand bits, so
this gives the IEEE float32 result; double rounding is innocuous for
+,-,*,/ and sqrt in these operations.
"""
from __future__ import annotations
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from array import array
from fractions import Fraction
import math
import struct
from typing import Callable

_F32 = struct.Struct('<f')

def f32(x):
    return _F32.unpack(_F32.pack(x))[0]

F32_EPS = 2.0 ** -23
NORM_TOL = f32(1e-6)

def dot_sse(x, y):
    if len(x) != len(y):
        raise ValueError('lengths differ')
    n = len(x)
    m = n - n % 16
    acc = [[0.0] * 4 for _ in range(4)]
    for i in range(0, m, 16):
        for k in range(4):
            for l in range(4):
                j = i + 4 * k + l
                p = f32(x[j] * y[j])
                acc[k][l] = f32(p + acc[k][l])
    h = [f32(f32(a[0] + a[2]) + f32(a[1] + a[3])) for a in acc]
    r = f32(f32(f32(h[0] + h[1]) + h[2]) + h[3])
    for j in range(m, n):
        r = f32(r + f32(x[j] * y[j]))
    return r

def round_f32_exact(value):
    """Round an exact rational with integer arithmetic, including subnormals."""
    value = Fraction(value)
    negative = value < 0
    value = abs(value)
    if not value:
        return float.fromhex('0x0p0')
    numerator, denominator = value.numerator, value.denominator
    exponent = numerator.bit_length() - denominator.bit_length()
    if exponent >= 0:
        if numerator < denominator << exponent:
            exponent -= 1
    elif numerator << -exponent < denominator:
        exponent -= 1
    shift = max(exponent - 23, -149)
    if shift >= 0:
        denominator <<= shift
    else:
        numerator <<= -shift
    significand, remainder = divmod(numerator, denominator)
    twice = remainder * 2
    if twice > denominator or (twice == denominator and significand & 1):
        significand += 1
    if (significand << max(shift - 104, 0)) > (0xffffff << max(104 - shift, 0)):
        raise OverflowError('outside float32 range')
    prefix = '-' if negative else ''
    return float.fromhex(f'{prefix}0x{significand:x}p{shift}')

def dot_sse_exact(x, y):
    if len(x) != len(y):
        raise ValueError('lengths differ')
    xx, yy = list(map(Fraction, x)), list(map(Fraction, y))
    def rounded(v):
        return Fraction(round_f32_exact(v))
    n = len(x)
    m = n - n % 16
    acc = [[Fraction(0) for _ in range(4)] for _ in range(4)]
    for i in range(0, m, 16):
        for k in range(4):
            for l in range(4):
                j = i + 4 * k + l
                p = rounded(xx[j] * yy[j])
                acc[k][l] = rounded(p + acc[k][l])
    h = [rounded(rounded(a[0] + a[2]) + rounded(a[1] + a[3])) for a in acc]
    r = rounded(rounded(rounded(h[0] + h[1]) + h[2]) + h[3])
    for j in range(m, n):
        r = rounded(r + rounded(xx[j] * yy[j]))
    return round_f32_exact(r)

def cosine_preprocess(v):
    len2 = dot_sse(v, v)
    if len2 < F32_EPS or abs(f32(len2 - 1.0)) <= NORM_TOL:
        return list(v)
    length = f32(math.sqrt(len2))
    return [f32(c / length) for c in v]

def read_rows(path, dim, first, count):
    if dim <= 0 or first < 0 or count < 0:
        raise ValueError('invalid row range')
    with open(path, 'rb') as stream:
        stream.seek(first * dim * 4)
        raw = stream.read(count * dim * 4)
    if len(raw) != count * dim * 4:
        raise ValueError('short vector file')
    values = array('f')
    values.frombytes(raw)
    if sys.byteorder != 'little':
        values.byteswap()
    return [list(values[i:i + dim]) for i in range(0, len(values), dim)]

def scores_sse(path, n, dim, q, block=256):
    if len(q) != dim or block <= 0:
        raise ValueError('invalid score dimensions')
    scores = array('f')
    # Each array conversion rounds every lane at exactly dot_sse's boundary.
    for first in range(0, n, block):
        rows = read_rows(path, dim, first, min(block, n - first))
        size = len(rows)
        columns = list(zip(*rows))
        acc = [array('f', [0.0]) * size for _ in range(16)]
        m = dim - dim % 16
        for j in range(m):
            p = array('f', (v * q[j] for v in columns[j]))
            lane = j % 16
            acc[lane] = array('f', (v + a for v, a in zip(p, acc[lane])))
        h = []
        for k in range(4):
            a = acc[4*k:4*k+4]
            left = array('f', (x + y for x, y in zip(a[0], a[2])))
            right = array('f', (x + y for x, y in zip(a[1], a[3])))
            h.append(array('f', (x + y for x, y in zip(left, right))))
        r = h[0]
        for k in range(1, 4):
            r = array('f', (x + y for x, y in zip(r, h[k])))
        for j in range(m, dim):
            p = array('f', (v * q[j] for v in columns[j]))
            r = array('f', (x + y for x, y in zip(r, p)))
        scores.extend(r)
    return scores

def top_rows(scores, mask, limit):
    if limit <= 0:
        return []
    if mask is not None and len(mask) != len(scores):
        raise ValueError('mask length differs')
    rows = [i for i in range(len(scores)) if mask is None or mask[i]]
    rows.sort(key=lambda i: (-scores[i], i))
    return [(i, scores[i]) for i in rows[:limit]]

def shortest_f32_text(x):
    if not math.isfinite(x):
        raise ValueError('non-finite value')
    for p in range(1, 10):
        text = format(x, '.%dg' % p)
        if f32(float(text)) == x:
            return text
    raise ValueError('value is not float32')

# The unchanged decimal parser follows, copied from export_generation.py,
# sha256 dcb00608cf53441b6e85ab087e734c925da55f5c32aa863b0dcd36e00a1bbb3e.  # sha256 or public signing fingerprint


LOW28 = (1 << 28) - 1      # a float32 midpoint has at most 25 significant bits: a double whose low 28 bits are 0


def f32_step(x: float, up: bool) -> float:
    """The float32 next to the float32 x, upwards or downwards."""
    if x == 0:
        bits = 0x00000001 if up else 0x80000001
    else:
        bits = struct.unpack("<I", struct.pack("<f", x))[0]
        bits = bits + 1 if (x > 0) == up else bits - 1
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def exact_f32(doubles: array, texts: Callable[[], list[str]]) -> tuple[array, int, int]:
    """float32 values nearest (ties to even) to the decimal texts whose correctly rounded doubles are `doubles`.
    The C cast (array 'f') is right except where the double is exactly halfway between two float32: such a
    midpoint is itself a double, so rounding the text to a double can land on it, never cross it. Those doubles
    have their low 28 mantissa bits zero; each is decided again from the exact decimal value. texts() is called
    only when there is one. Returns (values, midpoints met, values corrected)."""
    values = array("f", doubles)
    if not len(doubles):
        return values, 0, 0
    bits = memoryview(doubles).cast("B").cast("Q")
    candidates = [i for i, b in enumerate(bits) if not b & LOW28]
    midpoints = corrected = 0
    decimal: list[str] | None = None
    for i in candidates:
        x, cast = doubles[i], values[i]
        if x == cast or math.isinf(cast) or math.isnan(x):
            continue
        other = f32_step(cast, up=x > cast)
        mid = (cast + other) / 2
        if x != mid:
            continue
        midpoints += 1
        if decimal is None:
            decimal = texts()
        exact, half = Fraction(decimal[i]), Fraction(mid)
        if exact == half:
            continue                                  # a true tie: the cast's half-to-even is the rule
        want = max(cast, other) if exact > half else min(cast, other)
        if want != cast:
            values[i] = want
            corrected += 1
    return values, midpoints, corrected


def parse_f32_texts(texts):
    texts = list(texts)
    return exact_f32(array('d', map(float, texts)), lambda: texts)[0]
