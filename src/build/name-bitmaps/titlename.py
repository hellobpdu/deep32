#!/usr/bin/env python3
"""titlename.py: re-letter the legacy title card's banner as the program's name, stdlib only.

Reuses title26.py's model of the banner (face colour by row, clean ground tiled from the strip right of the '5',
a soft dark shadow, every pixel snapped to a colour the card already uses). Differences from title26: the whole
lettering is replaced, so the shadow is fitted on the banner's own observed lettering (no 96 lockup needed), and
the new lettering's face gradient is the old one mapped onto the new cap height.

  titlename.py build CARD.ppm TIMES.ttf OUT.ppm LAYOUT [PATCH_OUT.ppm]
  LAYOUT: one   = "DEEP32 ENCYCLOPEDIA" on one line
          two   = "DEEP32" over "ENCYCLOPEDIA"
"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, sys.argv[-1] if os.path.isdir(sys.argv[-1]) else HERE)
import title26 as t  # noqa: E402
from ttf import Font  # noqa: E402

UPEM = 2048
ERASE_X = (288, 582)          # the lettering's run: right of the ribbon to the band's dark right edge
OLD_X = (290, 570)            # the old lettering and its shadow
RIGHT = 568                   # the old lettering's right edge (the '5'), kept as the new right edge
CAP_OLD = (367, 393)          # cap line and baseline of the old letters (the 'E')
TRACK = 4.52 / 40             # the banner's letter spacing, as a fraction of the size (measured on the source banner)


def adv(font, s, px, track):
    return sum(font.metrics(c)[0] * px / UPEM for c in s) + track * px * (len(s) - 1)


def set_line(font, s, px, track, x_right, base, centre=None):
    """Coverage of one line set right-aligned at x_right (or centred on centre) on baseline base."""
    cov = {}
    x = x_right - adv(font, s, px, track) if centre is None else centre - adv(font, s, px, track) / 2
    for c in s:
        if c != ' ':
            for p, a in font.raster(c, px, x, base).items():
                cov[p] = min(1.0, cov.get(p, 0.0) + a)
        x += font.metrics(c)[0] * px / UPEM + track * px
    return cov


def cap_px(font, px):
    ib = t.ink_box(font.raster('E', px, 0.0, 0.0))
    return ib[3] - ib[1] + 1


def size_for_cap(font, cap):
    return min((abs(cap_px(font, 10 + 0.25 * i) - cap), 10 + 0.25 * i) for i in range(200))[1]


def layout(font, kind):
    if kind == 'one':
        s = 'DEEP32 ENCYCLOPEDIA'
        px = max(10 + 0.25 * i for i in range(200) if adv(font, s, 10 + 0.25 * i, TRACK) <= RIGHT - ERASE_X[0] - 6)
        c = cap_px(font, px)
        mid = (CAP_OLD[0] + CAP_OLD[1]) / 2
        base = mid + c / 2
        return [(s, px, TRACK, base)], [(int(round(base - c + 1)), int(round(base)))]
    if kind == 'two':
        p1 = size_for_cap(font, 24)
        p2 = size_for_cap(font, 11)
        w1 = adv(font, 'DEEP32', p1, TRACK)
        n2 = 'ENCYCLOPEDIA'
        # the small line spans the big one's width
        tr2 = (w1 - adv(font, n2, p2, 0.0)) / (p2 * (len(n2) - 1))
        c1, c2, gap = cap_px(font, p1), cap_px(font, p2), 3
        top = (357 + 401) / 2 - (c1 + gap + c2) / 2
        b1 = top + c1 - 1
        b2 = b1 + gap + c2
        return [('DEEP32', p1, TRACK, b1), (n2, p2, tr2, b2)], [(int(round(b1 - c1 + 1)), int(round(b1))), (int(round(b2 - c2 + 1)), int(round(b2)))]
    raise ValueError(kind)


def build(card_path, ttf, out, kind, patch_out=None):
    card = t.Card(card_path)
    font = Font(ttf)
    cs = t.glyph_masks(card)
    face_old = t.row_face(card, cs)
    tile, gmean = t.row_ground(card)
    obs = t.observed_alpha(card, face_old, gmean, OLD_X[0], OLD_X[1])
    # shadow fitted on the ground round the old lettering, as title26 fits it round the old digits
    pts = [(x, y) for y in range(t.BANNER_TOP + 2, t.BANNER_BOT - 1) for x in range(OLD_X[0], OLD_X[1])
           if obs.get((x, y), 0.0) < 0.02]
    best = None
    for ox in range(-6, 1):
        for oy in range(0, 6):
            for r in (1, 2, 3):
                sh = t.blur_shift(obs, ox, oy, r)
                num = den = 0.0
                for x, y in pts:
                    s = min(1.0, sh.get((x, y), 0.0))
                    if s < 0.01:
                        continue
                    lo = t.lum(card.at(x, y)) / max(1.0, t.lum(gmean[y]))
                    num += s * (1 - lo)
                    den += s * s
                k = num / den if den else 0.0
                err = sum((t.lum(card.at(x, y)) / max(1.0, t.lum(gmean[y])) - (1 - k * min(1.0, sh.get((x, y), 0.0)))) ** 2 for x, y in pts)
                if best is None or err < best[0]:
                    best = (err, ox, oy, r, k)
    _, ox, oy, r, k = best
    lines, caps = layout(font, kind)
    centre = None if kind == 'one' else (ERASE_X[0] + 4 + RIGHT) / 2
    new = {}
    face = {}
    sh_new = {}
    cap_old = CAP_OLD[1] - CAP_OLD[0] + 1
    for (s, px, tr, base), (c0, c1) in zip(lines, caps):
        line = set_line(font, s, px, tr, RIGHT, base, centre)
        for p, a in line.items():
            new[p] = min(1.0, new.get(p, 0.0) + a)
        # the shadow scales with the letters: offset and blur in proportion to the cap height
        f = (c1 - c0 + 1) / cap_old
        for p, a in t.blur_shift(line, int(round(ox * f)), int(round(oy * f)), max(1, int(round(r * f)))).items():
            sh_new[p] = min(1.0, sh_new.get(p, 0.0) + a)
        # the old face gradient (white, cream, gold from cap line to baseline) mapped onto this line's caps
        ys = sorted(y for y in face_old if CAP_OLD[0] <= y <= CAP_OLD[1])
        for y in range(c0 - 2, c1 + 3):
            f = (y - c0) / max(1, c1 - c0)
            src = CAP_OLD[0] + f * (CAP_OLD[1] - CAP_OLD[0])
            face[y] = face_old[min(ys, key=lambda yy: abs(yy - src))]
    sh_old = t.blur_shift(obs, ox, oy, r)
    # every pixel of the old lettering, its anti-aliased rim included: brighter or darker than its row's clean
    # ground by more than the ground's own dither, grown by one pixel
    dev = set()
    for y in range(t.BANNER_TOP + 1, t.BANNER_BOT):
        row = tile[y]
        lo, hi = min(t.lum(c) for c in row), max(t.lum(c) for c in row)
        for x in range(ERASE_X[0], ERASE_X[1]):
            v = t.lum(card.at(x, y))
            if v > hi + 3 or v < lo - 3:
                dev.update((x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1))
    px_out = list(card.px)
    cache, changed = {}, 0
    for y in range(t.BANNER_TOP, t.BANNER_BOT + 1):
        for x in range(ERASE_X[0], ERASE_X[1]):
            infl = max(obs.get((x, y), 0.0), sh_old.get((x, y), 0.0), new.get((x, y), 0.0), sh_new.get((x, y), 0.0))
            if infl < 0.01 and (x, y) not in dev:
                continue
            row = tile[y]
            gb = row[(x - t.CLEAN_X0) % len(row)]
            s = min(1.0, sh_new.get((x, y), 0.0))
            g = tuple(v * (1 - k * s) for v in gb)
            a = new.get((x, y), 0.0)
            f = face.get(y, gb)
            c = tuple(a * f[i] + (1 - a) * g[i] for i in range(3))
            q = t.nearest(card.palette, tuple(int(round(v)) for v in c), cache)
            if q != card.at(x, y):
                changed += 1
            px_out[y * 640 + x] = q
    t.write_ppm(out, 640, 480, px_out)
    rep = {'layout': kind, 'lines': [(s, round(px, 2), round(tr, 4), round(b, 2)) for s, px, tr, b in lines],
           'caps': caps, 'shadow': {'offset': (ox, oy), 'radius': r, 'strength': round(k, 3)},
           'changed_pixels': changed, 'palette_colours': len(card.palette)}
    if patch_out:
        x0, y0, w, h = t.PATCH
        t.write_ppm(patch_out, w, h, [px_out[(y0 + yy) * 640 + x0 + xx] for yy in range(h) for xx in range(w)])
        rep['patch'] = t.PATCH
    print(json.dumps(rep))


if __name__ == '__main__':
    a = sys.argv[1:]
    if len(a) >= 5 and a[0] == 'build':
        build(a[1], a[2], a[3], a[4], a[5] if len(a) > 5 and not os.path.isdir(a[5]) else None)
    else:
        print(__doc__)
        sys.exit(2)
