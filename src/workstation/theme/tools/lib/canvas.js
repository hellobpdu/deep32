'use strict';
// Software raster with the CDE/Motif drawing primitives of CDE-SPEC 2.1-2.3 (no anti-aliasing anywhere).
const bdf = require('./bdf');

const hx = h => [parseInt(h.substr(1, 2), 16), parseInt(h.substr(3, 2), 16), parseInt(h.substr(5, 2), 16)];

class Canvas {
  constructor(w, h, bg) { this.w = w; this.h = h; this.p = Buffer.alloc(w * h * 3); if (bg) this.fill(0, 0, w, h, bg); this.clip = null; }
  px(x, y, c) {
    if (x < 0 || y < 0 || x >= this.w || y >= this.h) return;
    if (this.clip && (x < this.clip[0] || y < this.clip[1] || x >= this.clip[2] || y >= this.clip[3])) return;
    const v = typeof c === 'string' ? hx(c) : c; const i = (y * this.w + x) * 3; this.p[i] = v[0]; this.p[i + 1] = v[1]; this.p[i + 2] = v[2];
  }
  get(x, y) { const i = (y * this.w + x) * 3; return '#' + [0, 1, 2].map(k => this.p[i + k].toString(16).padStart(2, '0').toUpperCase()).join(''); }
  fill(x, y, w, h, c) { const v = hx(c); for (let j = y; j < y + h; j++) for (let i = x; i < x + w; i++) this.px(i, j, v); }
  hline(x0, x1, y, c) { for (let x = x0; x <= x1; x++) this.px(x, y, c); }
  vline(x, y0, y1, c) { for (let y = y0; y <= y1; y++) this.px(x, y, c); }
  // CDE-SPEC 2.1 BevelRectangle: t nested rings, ts top/left, bs bottom/right, 45-degree joins at TR and BL.
  bevel(x, y, w, h, t, ts, bs) {
    for (let i = 0; i < t; i++) {
      this.hline(x + i, x + w - 1 - i, y + i, ts);            // top
      this.vline(x + i, y + i + 1, y + h - 1 - i, ts);        // left
      this.hline(x + i + 1, x + w - 1 - i, y + h - 1 - i, bs); // bottom
      this.vline(x + w - 1 - i, y + i + 1, y + h - 2 - i, bs); // right
    }
  }
  raised(x, y, w, h, t, s) { this.bevel(x, y, w, h, t, s.ts, s.bs); }
  sunken(x, y, w, h, t, s) { this.bevel(x, y, w, h, t, s.bs, s.ts); }
  // Etched in: outer half IN, inner half OUT (CDE-SPEC 2.1); t=2 gives bs ring then ts ring.
  etchedIn(x, y, w, h, t, s) { const a = Math.max(1, t >> 1); this.bevel(x, y, w, h, a, s.bs, s.ts); if (t > 1) this.bevel(x + a, y + a, w - 2 * a, h - 2 * a, a, s.ts, s.bs); }
  // Etched separator: bs row over ts row (CDE-SPEC 2.3.3).
  separator(x, y, w, s) { this.hline(x, x + w - 1, y, s.bs); this.hline(x, x + w - 1, y + 1, s.ts); }
  // Motif arrow (XmeDrawArrow, CDE-SPEC 2.3.5): 1:2 slope triangle, ts on the lit edges, bs on the others.
  // The up arrow reproduces the [M-2012] 11x11 golden exactly; other directions rotate the shape and colour each
  // edge by its outward normal (light from the top left: ts when nx + ny < 0, else bs).
  arrow(x, y, size, dir, s, fillBg = true) {
    const n = size, c = Math.floor(n / 2);
    const cell = [];                                  // up arrow: per pixel null | 'b' | normal [nx, ny]
    for (let r = 0; r < n; r++) {
      cell.push(new Array(n).fill(null));
      const half = (r + 1) >> 1, l = c - half, rr = c + half;
      for (let k = l; k <= rr; k++) cell[r][k] = 'b';
      if (r === 0) cell[r][c] = [-2, -1];
      else if (r % 2 === 1) { cell[r][l] = [-2, -1]; cell[r][rr] = [2, -1]; }
      if (r === n - 1) { for (let k = l + 1; k <= rr; k++) cell[r][k] = [0, 1]; cell[r][l] = [-2, -1]; }
    }
    const rot = { up: (i, j) => [i, j, v => v], down: (i, j) => [i, n - 1 - j, v => [v[0], -v[1]]],
      left: (i, j) => [j, i, v => [v[1], v[0]]], right: (i, j) => [n - 1 - j, i, v => [-v[1], v[0]]] }[dir];
    for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
      const v = cell[j][i]; if (!v) continue;
      const [X, Y, tf] = rot(i, j);
      if (v === 'b') { if (fillBg) this.px(x + X, y + Y, s.bg); continue; }
      const nn = tf(v); this.px(x + X, y + Y, nn[0] + nn[1] < 0 ? s.ts : s.bs);
    }
  }
  // cde10 one-of-many: a diamond with a t-px shadow; raised: upper-left edges ts, lower-right bs; set = sunken + sc.
  diamond(x, y, size, t, s, set) {
    const c = (size - 1) / 2, top = set ? s.bs : s.ts, bot = set ? s.ts : s.bs, fillc = set ? s.sc : s.bg;
    for (let j = 0; j < size; j++) for (let i = 0; i < size; i++) {
      const d = Math.abs(i - c) + Math.abs(j - c);
      if (d > c + 0.01) continue;
      if (d > c - t + 0.01) this.px(x + i, y + j, (j < c || (j === c && i < c)) ? top : bot);
      else this.px(x + i, y + j, fillc);
    }
  }
  // cde21 one-of-many: a 1-px ring (CDE-SPEC 2.3.2), set = sunken ring with highlightColor disc.
  circle(x, y, size, s, set, hl) {
    const c = (size - 1) / 2, R = size / 2;
    for (let j = 0; j < size; j++) for (let i = 0; i < size; i++) {
      const d = Math.hypot(i - c, j - c);
      if (d > R - 0.2) continue;
      if (d > R - 1.3) { const ul = (i - c) + (j - c) < 0; this.px(x + i, y + j, (ul !== !!set) ? s.ts : s.bs); }
      else if (set && d < R - 2.3) this.px(x + i, y + j, hl);
      else this.px(x + i, y + j, s.bg);
    }
  }
  // cde21 check mark (CDE-SPEC 2.3.2, 8x8 golden for a 10-px indicator), scaled by nearest neighbour.
  check(x, y, size, col) {
    const G = ['.......X', '......XX', '.X....X.', 'XXX..XX.', '.XXXXX..', '..XXXX..', '...XX...', '....X...'];
    for (let j = 0; j < size; j++) for (let i = 0; i < size; i++) if (G[Math.floor(j * 8 / size)][Math.floor(i * 8 / size)] === 'X') this.px(x + i, y + j, col);
  }
  // 50 % stipple fill, phase anchored to the screen (CDE-SPEC 1.2.4).
  stipple(x, y, w, h, c) { for (let j = y; j < y + h; j++) for (let i = x; i < x + w; i++) if (((i + j) & 1) === 0) this.px(i, j, c); }
  // Draw an XPM raster (from xpm.toRaster) with transparency.
  blit(r, x, y) { for (let j = 0; j < r.h; j++) for (let i = 0; i < r.w; i++) { const c = r.p[j * r.w + i]; if (c) this.px(x + i, y + j, c); } }
  tile(r, x0, y0, w, h, ox = 0, oy = 0) {
    for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
      const c = r.p[(((y0 + j - oy) % r.h + r.h) % r.h) * r.w + (((x0 + i - ox) % r.w + r.w) % r.w)];
      if (c) this.px(x0 + i, y0 + j, c);
    }
  }
  // BDF text: baseline at y. Returns the advance.
  text(font, str, x, y, col, opts = {}) {
    let pen = x;
    for (const ch of str) {
      const g = font.glyphs.get(ch.codePointAt(0)) || font.glyphs.get(32);
      if (!g) continue;
      const [bw, bh, bx, by] = g.bbx;
      for (let r = 0; r < bh; r++) {
        const row = parseInt(g.rows[r] || '0', 16), bits = g.rows[r].length * 4;
        for (let c = 0; c < bw; c++) if (row & (1 << (bits - 1 - c))) {
          const X = pen + bx + c, Y = y - (by + bh) + r;
          if (!opts.stipple || ((X + Y) & 1) === 0) this.px(X, Y, col);
        }
      }
      pen += g.dw;
    }
    return pen - x;
  }
  crop(x, y, w, h) { const c = new Canvas(w, h); for (let j = 0; j < h; j++) this.p.copy(c.p, j * w * 3, ((y + j) * this.w + x) * 3, ((y + j) * this.w + x + w) * 3); return c; }
  scale(k) { const c = new Canvas(this.w * k, this.h * k); for (let j = 0; j < c.h; j++) for (let i = 0; i < c.w; i++) { const s = ((j / k | 0) * this.w + (i / k | 0)) * 3, d = (j * c.w + i) * 3; c.p[d] = this.p[s]; c.p[d + 1] = this.p[s + 1]; c.p[d + 2] = this.p[s + 2]; } return c; }
  paste(src, x, y) { for (let j = 0; j < src.h; j++) for (let i = 0; i < src.w; i++) { const s = (j * src.w + i) * 3; this.px(x + i, y + j, [src.p[s], src.p[s + 1], src.p[s + 2]]); } }
}
function textWidth(font, str) { let w = 0; for (const ch of str) { const g = font.glyphs.get(ch.codePointAt(0)) || font.glyphs.get(32); if (g) w += g.dw; } return w; }
function loadFont(file) { return bdf.read(file); }

module.exports = { Canvas, textWidth, loadFont, hx };
