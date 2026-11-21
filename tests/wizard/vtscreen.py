"""A small VT terminal emulator for the setup wizard's tests and screenshots (standard library only).

It replays the exact byte stream a wizard wrote (cursor moves, erases, SGR colours in 16, 256 and 24-bit form,
the alternate screen, synchronized output and OSC hyperlinks) into a grid of cells, and takes a snapshot at each
`ESC ] 7777 ; snap ; NAME BEL` marker the wizard writes when its DEEP32_WIZARD_KEYS script says `snap:NAME`.
Snapshots can be read as text (for assertions) or written as standalone HTML (for screenshots).

    python3 -I -B tests/wizard/vtscreen.py CAPTURE.ansi OUTDIR --cols 100 --rows 30 --theme dark --title TITLE
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

THEMES = {
    # Terminal default colours and the 16-colour palette (GitHub's terminal colours) for each background.
    'dark': {'fg': '#e6edf3', 'bg': '#0d1117', 'chrome': '#161b22', 'chrome_fg': '#8b949e', 'edge': '#30363d',
             'ansi': ['#484f58', '#ff7b72', '#3fb950', '#d29922', '#58a6ff', '#bc8cff', '#39c5cf', '#b1bac4',
                      '#6e7681', '#ffa198', '#56d364', '#e3b341', '#79c0ff', '#d2a8ff', '#56d4dd', '#ffffff']},
    'light': {'fg': '#1f2328', 'bg': '#ffffff', 'chrome': '#f6f8fa', 'chrome_fg': '#59636e', 'edge': '#d0d7de',
              'ansi': ['#24292f', '#cf222e', '#116329', '#4d2d00', '#0969da', '#8250df', '#1b7c83', '#6e7781',
                       '#57606a', '#a40e26', '#1a7f37', '#633c01', '#218bff', '#a475f9', '#3192aa', '#8c959f']},
}


def xterm256(n):
    if n < 16:
        return None
    if n < 232:
        n -= 16
        steps = (0, 95, 135, 175, 215, 255)
        return '#%02x%02x%02x' % (steps[n // 36], steps[(n // 6) % 6], steps[n % 6])
    v = 8 + 10 * (n - 232)
    return '#%02x%02x%02x' % (v, v, v)


class Cell:
    __slots__ = ('ch', 'fg', 'bg', 'bold', 'under', 'link')

    def __init__(self, ch=' ', fg=None, bg=None, bold=False, under=False, link=None):
        self.ch, self.fg, self.bg, self.bold, self.under, self.link = ch, fg, bg, bold, under, link

    def style(self):
        return (self.fg, self.bg, self.bold, self.under, self.link)


class Screen:
    def __init__(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.main = self._blank()
        self.alt = self._blank()
        self.grid = self.main
        self.x = self.y = 0
        self.pending_wrap = False
        self.fg = self.bg = None
        self.bold = self.under = False
        self.link = None
        self.cursor_visible = True
        self.in_alt = False
        self.snaps = []      # (name, text lines, cell grid copy)
        self.sync_depth = 0

    def _blank(self):
        return [[Cell() for _ in range(self.cols)] for _ in range(self.rows)]

    # -- writing -------------------------------------------------------------------------------------------------
    def put(self, ch):
        if self.pending_wrap:
            self.x = 0
            self.y = min(self.y + 1, self.rows - 1)
            self.pending_wrap = False
        if 0 <= self.y < self.rows and 0 <= self.x < self.cols:
            self.grid[self.y][self.x] = Cell(ch, self.fg, self.bg, self.bold, self.under, self.link)
        if self.x == self.cols - 1:
            self.pending_wrap = True
        else:
            self.x += 1

    def sgr(self, params):
        if not params:
            params = [0]
        i = 0
        while i < len(params):
            p = params[i]
            if p == 0:
                self.fg = self.bg = None
                self.bold = self.under = False
            elif p == 1:
                self.bold = True
            elif p == 22:
                self.bold = False
            elif p == 4:
                self.under = True
            elif p == 24:
                self.under = False
            elif 30 <= p <= 37:
                self.fg = ('ansi', p - 30)
            elif 90 <= p <= 97:
                self.fg = ('ansi', p - 90 + 8)
            elif 40 <= p <= 47:
                self.bg = ('ansi', p - 40)
            elif 100 <= p <= 107:
                self.bg = ('ansi', p - 100 + 8)
            elif p == 39:
                self.fg = None
            elif p == 49:
                self.bg = None
            elif p in (38, 48) and i + 1 < len(params):
                if params[i + 1] == 2 and i + 4 < len(params):
                    color = '#%02x%02x%02x' % tuple(params[i + 2:i + 5])
                    i += 4
                elif params[i + 1] == 5 and i + 2 < len(params):
                    n = params[i + 2]
                    color = ('ansi', n) if n < 16 else xterm256(n)
                    i += 2
                else:
                    color = None
                    i += 1
                if p == 38:
                    self.fg = color
                else:
                    self.bg = color
            i += 1

    def csi(self, private, params, final):
        n = params[0] if params and params[0] else 1
        if final in 'Hf':
            row = (params[0] if len(params) > 0 and params[0] else 1) - 1
            col = (params[1] if len(params) > 1 and params[1] else 1) - 1
            self.y, self.x = max(0, min(row, self.rows - 1)), max(0, min(col, self.cols - 1))
            self.pending_wrap = False
        elif final == 'J':
            mode = params[0] if params else 0
            if mode == 2 or mode == 3:
                for r in range(self.rows):
                    self.grid[r] = [Cell() for _ in range(self.cols)]
            elif mode == 0:
                for c in range(self.x, self.cols):
                    self.grid[self.y][c] = Cell()
                for r in range(self.y + 1, self.rows):
                    self.grid[r] = [Cell() for _ in range(self.cols)]
        elif final == 'K':
            mode = params[0] if params else 0
            rng = range(self.x, self.cols) if mode == 0 else range(0, self.x + 1) if mode == 1 else range(self.cols)
            for c in rng:
                self.grid[self.y][c] = Cell(' ', None, self.bg)
        elif final == 'm':
            self.sgr(params)
        elif final == 'A':
            self.y = max(0, self.y - n)
        elif final == 'B':
            self.y = min(self.rows - 1, self.y + n)
        elif final == 'C':
            self.x = min(self.cols - 1, self.x + n)
        elif final == 'D':
            self.x = max(0, self.x - n)
        elif final == 'G':
            self.x = max(0, min(self.cols - 1, n - 1))
        elif final in 'hl' and private == '?':
            on = final == 'h'
            for p in params:
                if p == 1049:
                    if on and not self.in_alt:
                        self.alt = self._blank()
                        self.grid, self.in_alt = self.alt, True
                    elif not on and self.in_alt:
                        self.grid, self.in_alt = self.main, False
                elif p == 25:
                    self.cursor_visible = on
                elif p == 2026:
                    self.sync_depth = 1 if on else 0

    def osc(self, body):
        if body.startswith('7777;snap;'):
            name = body[len('7777;snap;'):]
            self.snaps.append((name, self.text(), [[Cell(c.ch, c.fg, c.bg, c.bold, c.under, c.link) for c in row] for row in self.grid]))
        elif body.startswith('8;'):
            parts = body.split(';', 2)
            self.link = parts[2] if len(parts) > 2 and parts[2] else None

    def feed(self, data):
        i, n = 0, len(data)
        while i < n:
            ch = data[i]
            if ch == '\x1b':
                if i + 1 >= n:
                    break
                nxt = data[i + 1]
                if nxt == '[':
                    m = re.compile(r'\[([?>=]?)([0-9;:]*)([ -/]*)([@-~])').match(data, i + 1)
                    if not m:
                        i += 2
                        continue
                    params = [int(p) if p.isdigit() else 0 for p in m.group(2).replace(':', ';').split(';')] if m.group(2) else []
                    self.csi(m.group(1), params, m.group(4))
                    i = m.end()
                elif nxt == ']':
                    j = i + 2
                    end = None
                    while j < n:
                        if data[j] == '\x07':
                            end, after = j, j + 1
                            break
                        if data[j] == '\x1b' and j + 1 < n and data[j + 1] == '\\':
                            end, after = j, j + 2
                            break
                        j += 1
                    if end is None:
                        break
                    self.osc(data[i + 2:end])
                    i = after
                elif nxt in '()':
                    i += 3
                else:
                    i += 2
            elif ch == '\r':
                self.x = 0
                self.pending_wrap = False
                i += 1
            elif ch == '\n':
                self.y = min(self.y + 1, self.rows - 1)
                self.pending_wrap = False
                i += 1
            elif ch == '\b':
                self.x = max(0, self.x - 1)
                i += 1
            elif ch == '\t':
                self.x = min(self.cols - 1, (self.x // 8 + 1) * 8)
                i += 1
            elif ch < ' ' or ch == '\x7f':
                i += 1
            else:
                self.put(ch)
                i += 1

    def text(self, grid=None):
        grid = grid or self.grid
        return [''.join(c.ch for c in row) for row in grid]


def color_of(value, theme, default):
    if value is None:
        return default
    if isinstance(value, tuple):
        return THEMES[theme]['ansi'][value[1]] if value[1] < 16 else xterm256(value[1])
    return value


def to_html(grid, theme, title):
    t = THEMES[theme]
    rows = []
    for row in grid:
        spans, run, style = [], [], None
        for cell in row + [None]:
            st = cell.style() if cell else None
            if st != style or cell is None:
                if run:
                    fg, bg, bold, under, link = style
                    css = ['color:' + color_of(fg, theme, t['fg'])]
                    if bg is not None:
                        css.append('background:' + color_of(bg, theme, t['bg']))
                    if bold:
                        css.append('font-weight:700')
                    if under:
                        css.append('text-decoration:underline;text-underline-offset:3px')
                    text = ''.join(html.escape(c) for c in run)
                    spans.append('<span style="%s">%s</span>' % (';'.join(css), text))
                run, style = [], st
            if cell is not None:
                run.append(cell.ch)
        rows.append('<div class="r">%s</div>' % ''.join(spans))
    return """<!doctype html>
<html><head><meta charset="utf-8"><title>%(title)s</title>
<style>
html,body{margin:0;background:%(page)s}
body{display:flex;justify-content:center;align-items:center;min-height:100vh}
.win{display:inline-block;margin:24px;border-radius:10px;overflow:hidden;border:1px solid %(edge)s;
     box-shadow:0 18px 50px rgba(0,0,0,.28),0 2px 6px rgba(0,0,0,.18);background:%(bg)s}
.bar{height:34px;background:%(chrome)s;border-bottom:1px solid %(edge)s;display:flex;align-items:center;
     font:12.5px 'Segoe UI',system-ui,sans-serif;color:%(chrome_fg)s;padding:0 14px}
.bar b{font-weight:600}
.term{padding:14px 16px 16px 16px;color:%(fg)s}
.r{white-space:pre;font-family:'Cascadia Mono','Cascadia Code',Consolas,'DejaVu Sans Mono',monospace;
   font-size:15px;line-height:19px;height:19px;font-variant-ligatures:none}
</style></head><body><div class="win"><div class="bar"><b>%(title)s</b></div><div class="term">%(rows)s</div></div></body></html>
""" % {'title': html.escape(title), 'page': '#e9edf2' if theme == 'light' else '#05070a', 'edge': t['edge'],
       'bg': t['bg'], 'chrome': t['chrome'], 'chrome_fg': t['chrome_fg'], 'fg': t['fg'], 'rows': '\n'.join(rows)}


def replay(data, cols, rows):
    screen = Screen(cols, rows)
    screen.feed(data)
    return screen


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('capture')
    ap.add_argument('outdir')
    ap.add_argument('--cols', type=int, default=100)
    ap.add_argument('--rows', type=int, default=30)
    ap.add_argument('--theme', choices=sorted(THEMES), default='dark')
    ap.add_argument('--title', default='deep32-wizard')
    ap.add_argument('--prefix', default='')
    args = ap.parse_args()
    data = Path(args.capture).read_bytes().decode('utf-8', errors='replace')
    screen = replay(data, args.cols, args.rows)
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    for name, text, grid in screen.snaps:
        (out / (args.prefix + name + '.html')).write_text(to_html(grid, args.theme, args.title), encoding='utf-8')
        (out / (args.prefix + name + '.txt')).write_text('\n'.join(text) + '\n', encoding='utf-8')
        print(args.prefix + name)
    return 0


if __name__ == '__main__':
    sys.exit(main())
