'use strict';
// Indexed pixel canvas for hand-authored art. Every pixel holds a colour token: a CDE symbol (iconGray3,
// topShadowColor ...), a fixed colour '#RRGGBB', or transparent. Coordinates are inclusive pixel boxes, as in
// LOOK-SOUND 6.1 ("(x0,y0)-(x1,y1)"). No anti-aliasing anywhere; dithering is ordered (Bayer) only.
const { canon, resolver } = require('./cde');

const BAYER4 = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]];
const BAYER8 = (() => {
  const m = [];
  for (let y = 0; y < 8; y++) { m.push([]); for (let x = 0; x < 8; x++) {
    const b4 = BAYER4[y % 4][x % 4];
    const q = [[0, 2], [3, 1]][(y >> 2) & 1][(x >> 2) & 1];
    m[y].push(4 * b4 + q);
  } }
  return m;
})();
// Threshold in [0,1): bayer(x,y,n) for n = 4 or 8.
function bayer(x, y, n = 4) {
  if (n === 8) return (BAYER8[y & 7][x & 7] + 0.5) / 64;
  return (BAYER4[y & 3][x & 3] + 0.5) / 16;
}

class Canvas {
  constructor(w, h, fill = null) {
    this.w = w; this.h = h;
    this.toks = [];
    this.index = new Map();
    this.px = new Int16Array(w * h).fill(-1);
    if (fill) this.rect(0, 0, w - 1, h - 1, fill);
  }
  ti(tok) {
    const t = canon(tok);
    if (t === null) return -1;
    let i = this.index.get(t);
    if (i === undefined) { i = this.toks.length; this.toks.push(t); this.index.set(t, i); }
    return i;
  }
  in(x, y) { return x >= 0 && y >= 0 && x < this.w && y < this.h; }
  set(x, y, tok) { x = Math.round(x); y = Math.round(y); if (typeof tok === "function") tok = tok(x, y); if (this.in(x, y)) this.px[y * this.w + x] = this.ti(tok); return this; }
  get(x, y) { if (!this.in(x, y)) return null; const i = this.px[y * this.w + x]; return i < 0 ? null : this.toks[i]; }
  opaque(x, y) { return this.in(x, y) && this.px[y * this.w + x] >= 0; }
  clear(x, y) { if (this.in(x, y)) this.px[y * this.w + x] = -1; return this; }

  rect(x0, y0, x1, y1, tok) {
    if (x1 < x0) [x0, x1] = [x1, x0]; if (y1 < y0) [y0, y1] = [y1, y0];
    if (typeof tok === "function") { for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) this.set(x, y, tok); return this; }
    const i = this.ti(tok);
    for (let y = Math.max(0, y0); y <= Math.min(this.h - 1, y1); y++)
      for (let x = Math.max(0, x0); x <= Math.min(this.w - 1, x1); x++) this.px[y * this.w + x] = i;
    return this;
  }
  hl(x0, x1, y, tok) { return this.rect(Math.min(x0, x1), y, Math.max(x0, x1), y, tok); }
  vl(x, y0, y1, tok) { return this.rect(x, Math.min(y0, y1), x, Math.max(y0, y1), tok); }
  box(x0, y0, x1, y1, tok) { this.hl(x0, x1, y0, tok); this.hl(x0, x1, y1, tok); this.vl(x0, y0, y1, tok); this.vl(x1, y0, y1, tok); return this; }
  // Motif bevel: t px, light top/left, dark bottom/right, diagonal corners.
  bevel(x0, y0, x1, y1, t, light, dark) {
    for (let i = 0; i < t; i++) {
      this.hl(x0, x1 - i - 1, y0 + i, light); this.vl(x0 + i, y0, y1 - i - 1, light);
      this.hl(x0 + i, x1, y1 - i, dark); this.vl(x1 - i, y0 + i, y1, dark);
    }
    return this;
  }
  panel(x0, y0, x1, y1, t, face, light, dark) { this.rect(x0, y0, x1, y1, face); return this.bevel(x0, y0, x1, y1, t, light, dark); }

  line(x0, y0, x1, y1, tok) {
    x0 = Math.round(x0); y0 = Math.round(y0); x1 = Math.round(x1); y1 = Math.round(y1);
    const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
    let err = dx + dy;
    for (;;) {
      this.set(x0, y0, tok);
      if (x0 === x1 && y0 === y1) break;
      const e2 = 2 * err;
      if (e2 >= dy) { err += dy; x0 += sx; }
      if (e2 <= dx) { err += dx; y0 += sy; }
    }
    return this;
  }
  // Thick line: a w x w square stamped along a Bresenham path (top-left anchored).
  lineW(x0, y0, x1, y1, w, tok) {
    const pts = [];
    const c = new Canvas(1, 1); // reuse Bresenham by collecting points
    x0 = Math.round(x0); y0 = Math.round(y0); x1 = Math.round(x1); y1 = Math.round(y1);
    const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
    let err = dx + dy, x = x0, y = y0;
    for (;;) { pts.push([x, y]); if (x === x1 && y === y1) break; const e2 = 2 * err; if (e2 >= dy) { err += dy; x += sx; } if (e2 <= dx) { err += dx; y += sy; } }
    for (const [px, py] of pts) this.rect(px, py, px + w - 1, py + w - 1, tok);
    void c;
    return this;
  }
  dotted(x0, y0, x1, y1, tok, on = 1, off = 1) {
    x0 = Math.round(x0); y0 = Math.round(y0); x1 = Math.round(x1); y1 = Math.round(y1);
    const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
    let err = dx + dy, x = x0, y = y0, n = 0;
    for (;;) { if (n % (on + off) < on) this.set(x, y, tok); n++; if (x === x1 && y === y1) break; const e2 = 2 * err; if (e2 >= dy) { err += dy; x += sx; } if (e2 <= dx) { err += dx; y += sy; } }
    return this;
  }
  // Set every pixel in the box where pred(x, y) holds (pixel centres at +0.5).
  shape(x0, y0, x1, y1, pred, tok) {
    for (let y = Math.max(0, y0); y <= Math.min(this.h - 1, y1); y++)
      for (let x = Math.max(0, x0); x <= Math.min(this.w - 1, x1); x++)
        if (pred(x, y)) this.set(x, y, typeof tok === 'function' ? tok(x, y) : tok);
    return this;
  }
  // Filled ellipse over the inclusive box (x0,y0)-(x1,y1): exact for even and odd sizes.
  oval(x0, y0, x1, y1, tok) {
    const cx = (x0 + x1 + 1) / 2, cy = (y0 + y1 + 1) / 2, rx = (x1 - x0 + 1) / 2, ry = (y1 - y0 + 1) / 2;
    return this.shape(x0, y0, x1, y1, (x, y) => {
      const u = (x + 0.5 - cx) / rx, v = (y + 0.5 - cy) / ry; return u * u + v * v <= 1.0001 + 0.35 / Math.max(rx, ry);
    }, tok);
  }
  // Ring: the oval box minus the oval inset by th px.
  ring(x0, y0, x1, y1, th, tok, pred) {
    const cx = (x0 + x1 + 1) / 2, cy = (y0 + y1 + 1) / 2, rx = (x1 - x0 + 1) / 2, ry = (y1 - y0 + 1) / 2;
    const irx = rx - th, iry = ry - th;
    return this.shape(x0, y0, x1, y1, (x, y) => {
      const u = (x + 0.5 - cx) / rx, v = (y + 0.5 - cy) / ry;
      if (u * u + v * v > 1.0001 + 0.35 / Math.max(rx, ry)) return false;
      if (irx > 0 && iry > 0) { const a = (x + 0.5 - cx) / irx, b = (y + 0.5 - cy) / iry; if (a * a + b * b <= 1.0001 + 0.35 / Math.max(irx, iry)) return false; }
      return pred ? pred(x + 0.5 - cx, y + 0.5 - cy) : true;
    }, tok);
  }
  // Even-odd polygon fill, pixel centres.
  poly(pts, tok) {
    let minY = Infinity, maxY = -Infinity;
    for (const [, y] of pts) { minY = Math.min(minY, y); maxY = Math.max(maxY, y); }
    for (let y = Math.max(0, Math.floor(minY)); y <= Math.min(this.h - 1, Math.ceil(maxY)); y++) {
      const yc = y + 0.5, xs = [];
      for (let i = 0; i < pts.length; i++) {
        const [ax, ay] = pts[i], [bx, by] = pts[(i + 1) % pts.length];
        if ((ay <= yc && by > yc) || (by <= yc && ay > yc)) xs.push(ax + (yc - ay) * (bx - ax) / (by - ay));
      }
      xs.sort((a, b) => a - b);
      for (let k = 0; k + 1 < xs.length; k += 2)
        for (let x = Math.ceil(xs[k] - 0.5); x <= Math.floor(xs[k + 1] - 0.5); x++) this.set(x, y, tok);
    }
    return this;
  }
  // Stamp a character grid; legend maps a char to a token; '.' and ' ' are skipped; '_' clears.
  grid(rows, legend, ox = 0, oy = 0, strict = false) {
    rows = rows.map(r => r.split('|').join(''));
    if (strict) {
      if (rows.length !== this.h) throw new Error(`grid: ${rows.length} rows for a ${this.w}x${this.h} canvas`);
      rows.forEach((r, y) => { if (r.length !== this.w) throw new Error(`grid: row ${y} has ${r.length} px, expected ${this.w}: "${r}"`); });
    }
    rows.forEach((row, y) => {
      for (let x = 0; x < row.length; x++) {
        const ch = row[x];
        if (ch === '.' || ch === ' ') continue;
        if (ch === '_') { this.clear(ox + x, oy + y); continue; }
        if (!(ch in legend)) throw new Error(`grid: '${ch}' not in legend (row ${y})`);
        this.set(ox + x, oy + y, legend[ch]);
      }
    });
    return this;
  }
  // 1 px outline (4-neighbour) around every opaque pixel, drawn on transparent pixels only.
  outline(tok, diagonal = false) {
    const add = [];
    for (let y = 0; y < this.h; y++) for (let x = 0; x < this.w; x++) {
      if (this.opaque(x, y)) continue;
      const n = [[1, 0], [-1, 0], [0, 1], [0, -1]];
      if (diagonal) n.push([1, 1], [-1, -1], [1, -1], [-1, 1]);
      if (n.some(([dx, dy]) => this.opaque(x + dx, y + dy))) add.push([x, y]);
    }
    for (const [x, y] of add) this.set(x, y, tok);
    return this;
  }
  replace(from, to, x0 = 0, y0 = 0, x1 = this.w - 1, y1 = this.h - 1) {
    const f = canon(from);
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) if (this.get(x, y) === f) this.set(x, y, to);
    return this;
  }
  blit(src, ox, oy) {
    for (let y = 0; y < src.h; y++) for (let x = 0; x < src.w; x++) { const t = src.get(x, y); if (t !== null) this.set(ox + x, oy + y, t); }
    return this;
  }
  text(font, str, x, y, tok, gap = 1) {
    let cx = x;
    for (const ch of str) {
      const g = font.glyph(ch);
      g.rows.forEach((row, ry) => { for (let rx = 0; rx < row.length; rx++) if (row[rx] === '#') this.set(cx + rx, y + ry + (g.dy || 0), tok); });
      cx += g.w + gap;
    }
    return cx - gap;
  }
  colours() { const used = new Set(); for (const i of this.px) if (i >= 0) used.add(this.toks[i]); return [...used]; }
  mask() { const m = new Uint8Array(this.w * this.h); for (let i = 0; i < m.length; i++) m[i] = this.px[i] >= 0 ? 1 : 0; return m; }
  toRGBA(set) {
    const res = resolver(set);
    const out = Buffer.alloc(this.w * this.h * 4);
    for (let i = 0; i < this.px.length; i++) {
      const t = this.px[i] < 0 ? null : this.toks[this.px[i]];
      const [r, g, b, a] = res(t);
      out[i * 4] = r; out[i * 4 + 1] = g; out[i * 4 + 2] = b; out[i * 4 + 3] = a;
    }
    return out;
  }
  clone() { const c = new Canvas(this.w, this.h); c.toks = this.toks.slice(); c.index = new Map(this.index); c.px = this.px.slice(); return c; }
}

module.exports = { Canvas, bayer, BAYER4, BAYER8 };
