#!/usr/bin/env python3
"""emblem.py: redraw the gold relief "A" on the title card's ribbon end as a "D", stdlib only.

The emblem is a small gold letter in relief (about 30x35 px), rotated and sheared: left foot P1, right foot P2,
apex P0 (measured on title1.dib; identical on title1-26). The D takes the same letter frame (baseline P1->P2,
"up" from the baseline's midpoint to the apex), Times New Roman Bold's outline, and the A's own colours: an
extruded dark side down and to the right, a gold face, a pale rim on the edges facing the top-left light.
Every pixel is snapped to a colour the card already uses.

  emblem.py CARD.ppm TIMESBD.ttf OUT.ppm [REPORT.json]
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import title26 as t  # noqa: E402
from ttf import Font  # noqa: E402

P0, P1, P2 = (171.0, 342.5), (176.0, 375.5), (201.0, 359.0)   # apex, left foot, right foot (pixel centres)
REGION = (150, 326, 215, 392)                                 # x0, y0, x1, y1 searched and redrawn
EXTRUDE = (1.6, 1.2)                                          # the A's dark side: down and right
SS = 4                                                        # supersampling per axis


def inside(polys, x, y):
    c = False
    for p in polys:
        n = len(p)
        for i in range(n):
            x1, y1 = p[i]
            x2, y2 = p[(i + 1) % n]
            if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                c = not c
    return c


def frame():
    m = ((P1[0] + P2[0]) / 2, (P1[1] + P2[1]) / 2)
    bx = (P2[0] - P1[0], P2[1] - P1[1])
    up = (P0[0] - m[0], P0[1] - m[1])
    return bx, up


def coverage(polys, gb, dx=0.0, dy=0.0, scale=1.0):
    """Coverage of the glyph mapped into the letter frame: glyph box (x0, top, x1, base) -> unit square ->
    P1 + fx * baseline + fy * up. Inverse-mapped per subsample."""
    bx, up = frame()
    det = bx[0] * up[1] - bx[1] * up[0]
    gx0, gtop, gx1, gbase = gb
    cov = {}
    x0, y0, x1, y1 = REGION
    for y in range(y0, y1):
        for x in range(x0, x1):
            n = 0
            for sy in range(SS):
                for sx in range(SS):
                    px = x + (sx + 0.5) / SS - 0.5 - dx
                    py = y + (sy + 0.5) / SS - 0.5 - dy
                    rx, ry = px - P1[0], py - P1[1]
                    fx = (rx * up[1] - ry * up[0]) / det
                    fy = (bx[0] * ry - bx[1] * rx) / det
                    # shrink about the frame's centre (scale < 1 narrows the letter)
                    fx = 0.5 + (fx - 0.5) / scale
                    fy = 0.45 + (fy - 0.45) / scale
                    if not (0 <= fx <= 1 and 0 <= fy <= 1):
                        continue
                    gx = gx0 + fx * (gx1 - gx0)
                    gy = gbase - fy * (gbase - gtop)
                    if inside(polys, gx, gy):
                        n += 1
            if n:
                cov[(x, y)] = n / (SS * SS)
    return cov


def main(card_path, ttf, out, report=None):
    w, h, px = t.read_pnm(card_path)
    pal = sorted(set(px))
    at = lambda x, y: px[y * w + x]
    font = Font(ttf)
    polys = font.polygons('D', 400, 0.0, 0.0)
    xs = [p[0] for pl in polys for p in pl]
    ys = [p[1] for pl in polys for p in pl]
    gb = (min(xs), min(ys), max(xs), max(ys))
    # the A's colours: face (strong yellow), rim (pale yellow), side (dark red-brown), from the emblem area
    x0, y0, x1, y1 = REGION
    area = [at(x, y) for y in range(338, 380) for x in range(166, 204)]
    face = [c for c in area if c[0] > 225 and c[1] > 170 and c[2] < 90]
    rim = [c for c in area if c[0] > 235 and c[1] > 215 and c[2] > 90]
    side = [c for c in area if c[0] > 140 and c[1] < 80 and c[2] < 60]
    med = lambda cs: tuple(sorted(c[i] for c in cs)[len(cs) // 2] for i in range(3))
    cface, crim, cside = med(face), med(rim), med(side)
    scale = 0.76
    fcov = coverage(polys, gb, 0.0, 0.0, scale)
    # the extruded side: the letter swept along the extrusion vector
    scov = {}
    for k in range(1, 5):
        f = k / 4
        for p, a in coverage(polys, gb, EXTRUDE[0] * f, EXTRUDE[1] * f, scale).items():
            scov[p] = max(scov.get(p, 0.0), a)
    # rim: face pixels whose up-left neighbour is outside the face
    lit = coverage(polys, gb, 1.0, 1.0, scale)
    # the old A: emblem-coloured pixels in its footprint; any the D does not cover get the ground beside them
    # only inside the A's own triangle (apex, feet), grown by 2.5 px: the ribbon beside it is gold too
    def near_triangle(x, y, grow=2.5):
        pts = (P0, P1, P2)
        for i in range(3):
            (ax, ay), (bx_, by_) = pts[i], pts[(i + 1) % 3]
            cx, cy = pts[(i + 2) % 3]
            ex, ey = bx_ - ax, by_ - ay
            ln = (ex * ex + ey * ey) ** 0.5
            side_c = (ex * (cy - ay) - ey * (cx - ax)) / ln
            side_p = (ex * (y - ay) - ey * (x - ax)) / ln
            if side_p * (1 if side_c > 0 else -1) < -grow:
                return False
        return True
    tile_early, _ = t.row_ground(t.Card(card_path))
    old = set()
    for y in range(336, 382):
        for x in range(164, 206):
            c = at(x, y)
            bar_blue = c[2] > c[0] + 20
            if bar_blue and t.BANNER_TOP <= y <= t.BANNER_BOT and t.lum(c) > max(t.lum(q) for q in tile_early[y]) + 6:
                bar_blue = False   # the A's pale glow on the bar, not the bar
            sky = y < t.BANNER_TOP and min(c) > 170 and c[2] >= c[0]
            if near_triangle(x, y) and not bar_blue and not sky:
                old.add((x, y))
    newpx = list(px)
    cache, changed, refilled = {}, 0, 0
    tile, gmean = t.row_ground(t.Card(card_path))
    for y in range(y0, y1):
        for x in range(x0, x1):
            a, s = fcov.get((x, y), 0.0), scov.get((x, y), 0.0)
            ground = at(x, y)
            if (x, y) in old and a < 0.5 and s < 0.5:
                # what the A hid: decided by the nearest ring of pixels outside the A: bar blue (keep the bar's own
                # dither from its clean strip) or not (the ribbon or the sky: take the nearest such pixel)
                ring = []
                for d in range(1, 14):
                    ring = [(x + dx, y + dy) for dx in range(-d, d + 1) for dy in range(-d, d + 1)
                            if max(abs(dx), abs(dy)) == d and (x + dx, y + dy) not in old
                            and REGION[0] <= x + dx < REGION[2] and REGION[1] <= y + dy < REGION[3]]
                    if ring:
                        break
                blue = [q for q in ring if at(*q)[2] > at(*q)[0] + 20]
                if t.BANNER_TOP <= y <= t.BANNER_BOT and len(blue) * 2 >= len(ring):
                    row = tile[y]
                    ground = row[(x - t.CLEAN_X0) % len(row)]
                else:
                    other = [q for q in ring if q not in blue] or ring
                    ground = at(*min(other, key=lambda q: abs(q[0] - x) + abs(q[1] - y)))
                refilled += 1
            if a == 0 and s == 0 and ground == at(x, y):
                continue
            c = tuple(s * cside[i] + (1 - s) * ground[i] for i in range(3))
            if a:
                rimk = max(0.0, a - lit.get((x, y), 0.0))
                fc = tuple(rimk * crim[i] + (1 - rimk) * cface[i] for i in range(3))
                c = tuple(a * fc[i] + (1 - a) * c[i] for i in range(3))
            q = t.nearest(pal, tuple(int(round(v)) for v in c), cache)
            if q != at(x, y):
                changed += 1
            newpx[y * w + x] = q
    t.write_ppm(out, w, h, newpx)
    rep = {'colours': {'face': cface, 'rim': crim, 'side': cside}, 'samples': [len(face), len(rim), len(side)],
           'changed_pixels': changed, 'refilled_old_pixels': refilled, 'scale': scale}
    print(json.dumps(rep))
    if report:
        json.dump(rep, open(report, 'w'), indent=1)


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(2)
    main(*sys.argv[1:5])
