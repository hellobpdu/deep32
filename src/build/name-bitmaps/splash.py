#!/usr/bin/env python3
"""splash.py: the startup splash (320x224) with the program's name in place of the publisher's logo, stdlib only.

SPLASH.ppm is two colours (navy ground, white lettering, no anti-aliasing); SPLASHVGA.ppm is the same picture for
16-colour VGA, its navy made of a blue/black dither. The new lettering is a two-line wordmark set in Times New Roman,
the face of the title card's banner, hard-edged (coverage >= 0.5) like the original. Each output keeps its source's
own ground (solid navy, or the dither in its own phase) and its own white.

  splash.py SOURCE.ppm TIMES.ttf OUT.ppm
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import title26 as t  # noqa: E402
from ttf import Font  # noqa: E402

UPEM = 2048
LINES = (('Deep32', 82.0, 0.0), ('Encyclopedia', 36.0, 0.02))   # text, size px, tracking (fraction of size)
GAP = 10


def width(font, s, px, tr):
    return sum(font.metrics(c)[0] * px / UPEM for c in s) + tr * px * (len(s) - 1)


def main(src, ttf, out):
    w, h, px = t.read_pnm(src)
    cols = sorted(set(px), key=t.lum)
    white = cols[-1]
    ground = [c for c in cols if c != white]
    font = Font(ttf)
    # cap heights and block height for vertical centring
    caps = []
    for s, size, tr in LINES:
        ib = t.ink_box(font.raster('D', size, 0.0, 0.0))
        caps.append(ib[3] - ib[1] + 1)
    # the second line has descenders (y, p): leave room for them below its baseline
    desc = round(LINES[1][1] * 0.22)
    block = caps[0] + GAP + caps[1] + desc
    top = (h - block) / 2
    cov = {}
    base = top + caps[0] - 1
    for i, (s, size, tr) in enumerate(LINES):
        if i == 1:
            base = base + GAP + caps[1]
        x = (w - width(font, s, size, tr)) / 2
        for c in s:
            for p, a in font.raster(c, size, x, base).items():
                cov[p] = min(1.0, cov.get(p, 0.0) + a)
            x += font.metrics(c)[0] * size / UPEM + tr * size
    # a text-free band at the top of the source (rows above its first white pixel) supplies the ground under
    # old lettering: the original's own pixels where it was ground, the band's pixels (same column) where it was text
    first = min(i // w for i, c in enumerate(px) if c == white)
    band = max(1, first - 2)
    newpx = []
    for y in range(h):
        for x in range(w):
            if cov.get((x, y), 0.0) >= 0.5:
                newpx.append(white)
            elif px[y * w + x] != white:
                newpx.append(px[y * w + x])
            else:
                newpx.append(px[(y % band) * w + x])
    t.write_ppm(out, w, h, newpx)
    print(out, w, h, 'white px', sum(1 for c in newpx if c == white), 'colours', len(set(newpx)))


_phase = {}


def dither_at(px, w, h, white, x, y):
    key = (x + y) % 2
    if key not in _phase:
        for yy in range(h):
            for xx in range(w):
                c = px[yy * w + xx]
                if c != white and (xx + yy) % 2 == key:
                    _phase[key] = c
                    break
            if key in _phase:
                break
    return _phase[key]


if __name__ == '__main__':
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(2)
    main(*sys.argv[1:])
