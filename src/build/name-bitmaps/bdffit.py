"""bdffit.py: which BDF font draws a button label pixel for pixel. Usage: bdffit.py WORLDOF.ppm LABEL BDF...

LABEL is the text the original button shows; pass it on the command line (it is not kept in this source).
"""
import sys
from PIL import Image


def load_bdf(path):
    glyphs, cur, bm, ascent = {}, None, None, 0
    for line in open(path, encoding='latin-1'):
        p = line.split()
        if not p:
            continue
        if p[0] == 'FONT_ASCENT':
            ascent = int(p[1])
        elif p[0] == 'ENCODING':
            cur = {'enc': int(p[1])}
        elif p[0] == 'DWIDTH' and cur is not None:
            cur['dw'] = int(p[1])
        elif p[0] == 'BBX' and cur is not None:
            cur['bbx'] = tuple(int(v) for v in p[1:5])
        elif p[0] == 'BITMAP':
            bm = []
        elif p[0] == 'ENDCHAR':
            cur['bm'] = bm
            glyphs[cur['enc']] = cur
            cur, bm = None, None
        elif bm is not None:
            bm.append(int(p[0], 16) << 0)
            cur.setdefault('hexw', len(p[0]) * 4)
    return glyphs, ascent


def render(glyphs, ascent, s):
    """Pixel set of s, origin at the pen start, y = 0 on the ascent line."""
    pts, x = set(), 0
    for ch in s:
        g = glyphs.get(ord(ch))
        if g is None:
            return None
        w, h, xo, yo = g['bbx']
        top = ascent - (yo + h)
        for j, row in enumerate(g['bm']):
            for i in range(w):
                if row >> (g['hexw'] - 1 - i) & 1:
                    pts.add((x + xo + i, top + j))
        x += g['dw']
    return pts


def main(ppm, label, *bdfs):
    im = Image.open(ppm).convert('RGB')
    orig = {(x, y) for y in range(50, 67) for x in range(84) if sum(im.getpixel((x, y))) < 200}
    best = []
    for b in bdfs:
        glyphs, asc = load_bdf(b)
        r = render(glyphs, asc, label)
        if not r:
            continue
        # best alignment by translation
        ox0 = min(p[0] for p in orig) - min(p[0] for p in r)
        oy0 = min(p[1] for p in orig) - min(p[1] for p in r)
        top = None
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                m = {(x + ox0 + dx, y + oy0 + dy) for x, y in r}
                d = len(m ^ orig)
                if top is None or d < top[0]:
                    top = (d, ox0 + dx, oy0 + dy)
        best.append((top[0], b.split('/')[-1], top[1], top[2], len(r)))
    best.sort()
    print('original pixels', len(orig))
    for row in best[:5]:
        print('diff %d  %s  offset (%d,%d)  glyph px %d' % row)


main(*sys.argv[1:])
