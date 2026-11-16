#!/usr/bin/env python3
"""worldof.py: re-letter the source button strip (252x67, 3 frames of 84) as "World of Deep32".

The label is hand-drawn pixel lettering (no installed font reproduces it): one navy, caps 7 px (rows 55-61),
x-height 5, every glyph 4 px wide with 1 px between. "World of " is kept exactly as drawn in each frame (frame 2
lightens part of it under the globe's glow); the former name is replaced by "Deep32" drawn on the same grid, and the
line is re-centred as the original was. Ground under removed lettering: the nearest non-text pixel on its row.

  worldof.py WORLDOF.ppm OUT.ppm
"""
import sys
from PIL import Image

INK = (16, 57, 99)
FRAME = 84
ROWS = range(53, 66)
KEEP_X = (0, 45)          # frame-local columns of "World of " (and its space)
GLYPHS = {
    'D': ['###.', '#..#', '#..#', '#..#', '#..#', '#..#', '###.'],
    'e': ['....', '....', '.##.', '#..#', '####', '#...', '.##.'],
    'p': ['....', '....', '###.', '#..#', '#..#', '#..#', '###.', '#...', '#...'],
    '3': ['###.', '...#', '...#', '.##.', '...#', '...#', '###.'],
    '2': ['.##.', '#..#', '...#', '..#.', '.#..', '#...', '####'],
}
CAP_TOP = 55


def is_text(c):
    return sum(c) < 200


def main(src, out):
    im = Image.open(src).convert('RGB')
    w, h = im.size
    px = im.load()
    res = im.copy()
    rp = res.load()
    for f in range(w // FRAME):
        x0 = f * FRAME
        text = {(x, y) for y in ROWS for x in range(x0, x0 + FRAME) if is_text(px[x, y])}
        keep = {(x, y) for (x, y) in text if KEEP_X[0] <= x - x0 < KEEP_X[1]}
        old_right = max(x for x, _ in text) - x0
        old_left = min(x for x, _ in text) - x0
        # the new line: kept part + "Deep32" from where the former name began
        e_left = min(x for x, _ in text if x - x0 >= KEEP_X[1]) - x0
        new = set((x - x0, y) for x, y in keep)
        cx = e_left
        for ch in 'Deep32':
            g = GLYPHS[ch]
            top = CAP_TOP
            for j, row in enumerate(g):
                for i, b in enumerate(row):
                    if b == '#':
                        new.add((cx + i, top + j))
            cx += len(g[0]) + 1
        new_right = cx - 2
        # re-centre with the original's own margins (left old_left, right FRAME-1-old_right): same bias
        slack_old = (FRAME - 1 - old_right) - old_left
        dx = 0   # "World of " stays where it is drawn: frame 2 lightens part of it under the glow, so it is never moved
        new = {(x + dx, y) for x, y in new}
        # clear all old lettering in this frame: nearest non-text pixel on the row
        for (x, y) in text:
            for d in range(1, 12):
                for q in (x - d, x + d):
                    if x0 <= q < x0 + FRAME and (q, y) not in text:
                        rp[x, y] = px[q, y]
                        break
                else:
                    continue
                break
        for (x, y) in new:
            if (x + x0, y) in keep and dx == 0:
                rp[x + x0, y] = px[x + x0, y]
            else:
                # the kept glyphs keep their own pixel colour (frame 2's glow); new glyphs take the ink
                src_px = (x - dx + x0, y)
                rp[x + x0, y] = px[src_px] if src_px in keep else INK
    res.save(out, format='PPM')
    print(out, res.size, 'dx', dx)


if __name__ == '__main__':
    main(*sys.argv[1:3])
