'use strict';
// Shared CDE-style drawing helpers for the icon modules. Conventions (ASSETS 3.2, LOOK-SOUND 6.1):
// light from the top left, 1 px dark outline on objects, bevels 1 px at 16/32 and 2 px at 48,
// only the 16 fixed icon colours plus background/foreground/topShadowColor/bottomShadowColor/selectColor.
const { Canvas, bayer } = require('./canvas');

// Universal legend for hand-drawn character grids.
const L = {
  K: 'k', W: 'w', R: 'r', G: 'g', B: 'b', Y: 'y', C: 'c', M: 'm',
  1: 'g1', 2: 'g2', 3: 'g3', 4: 'g4', 5: 'g5', 6: 'g6', 7: 'g7', 8: 'g8',
  t: 'ts', s: 'bs', b: 'bg', f: 'fg', e: 'sc',
};

// Drop shadow: the current silhouette offset by (dx, dy) drawn on transparent pixels only.
function dropShadow(c, dx = 2, dy = 2, tok = 'bs') {
  const m = c.mask(); const add = [];
  for (let y = 0; y < c.h; y++) for (let x = 0; x < c.w; x++) {
    if (m[y * c.w + x]) continue;
    const sx = x - dx, sy = y - dy;
    if (sx >= 0 && sy >= 0 && sx < c.w && sy < c.h && m[sy * c.w + sx]) add.push([x, y]);
  }
  for (const [x, y] of add) c.set(x, y, tok);
  return c;
}
// Checker dither of two tokens over a box (phase 0 or 1).
function checker(c, x0, y0, x1, y1, a, b, phase = 0) {
  return c.shape(x0, y0, x1, y1, () => true, (x, y) => (((x + y + phase) & 1) ? b : a));
}
// Ordered dither between two tokens with a fraction f(x, y) in [0,1] of token b.
function ditherFill(c, x0, y0, x1, y1, a, b, f, n = 4, pred) {
  return c.shape(x0, y0, x1, y1, pred || (() => true), (x, y) => (f(x, y) > bayer(x, y, n) ? b : a));
}
// Sheet of paper with a dark outline, optional dog-ear (fold size px) at the top right and text lines.
function page(c, x0, y0, x1, y1, o = {}) {
  const paper = o.paper || 'w', edge = o.edge || 'g8', fold = o.fold || 0;
  c.rect(x0, y0, x1, y1, paper);
  c.box(x0, y0, x1, y1, edge);
  if (o.shade !== false) { c.vl(x1 - 1, y0 + 1, y1 - 1, 'g2'); c.hl(x0 + 1, x1 - 1, y1 - 1, 'g2'); }
  if (fold) {
    for (let i = 0; i < fold; i++) for (let j = 0; j <= i; j++) c.clear(x1 - fold + 1 + i, y0 + j);
    // fold flap
    for (let i = 0; i < fold; i++) for (let j = i; j < fold; j++) c.set(x1 - fold + i, y0 + j, j === fold - 1 || i === 0 ? edge : 'g2');
    c.line(x1 - fold + 1, y0, x1, y0 + fold - 1, edge);
    c.set(x1 - fold + 1, y0, edge);
  }
  if (o.lines) {
    const { from, to, step, x: lx0, x2: lx1, tok = 'g4', ragged = true } = o.lines;
    let n = 0;
    for (let y = from; y <= to; y += step) {
      const end = ragged ? lx1 - ((n * 7) % 5) * Math.max(1, Math.floor((lx1 - lx0) / 12)) : lx1;
      c.hl(lx0, end, y, tok); n++;
    }
  }
  return c;
}
// Raised Motif tile on the colour set (background face, ts/bs bevel).
function tile(c, x0, y0, x1, y1, t = 1, face = 'bg') { return c.panel(x0, y0, x1, y1, t, face, 'ts', 'bs'); }
// A grey object box with highlight top/left and a dark outline (the CDE "object" look).
function block(c, x0, y0, x1, y1, face = 'g3', hi = 'g1', lo = 'g6', edge = 'g8') {
  c.rect(x0, y0, x1, y1, face);
  c.hl(x0 + 1, x1 - 1, y0 + 1, hi); c.vl(x0 + 1, y0 + 1, y1 - 1, hi);
  c.hl(x0 + 1, x1 - 1, y1 - 1, lo); c.vl(x1 - 1, y0 + 1, y1 - 1, lo);
  c.box(x0, y0, x1, y1, edge);
  return c;
}
// A monitor: bezel with a sunken screen. Returns the screen box.
function monitor(c, x0, y0, x1, y1, o = {}) {
  const t = o.t || 2;
  block(c, x0, y0, x1, y1, o.face || 'g3', 'g1', 'g5', 'g8');
  const sx0 = x0 + t + 2, sy0 = y0 + t + 2, sx1 = x1 - t - 2, sy1 = y1 - t - (o.chin || 2);
  c.rect(sx0, sy0, sx1, sy1, o.screen || 'g8');
  c.hl(sx0 - 1, sx1 + 1, sy0 - 1, 'g6'); c.vl(sx0 - 1, sy0 - 1, sy1 + 1, 'g6');
  c.hl(sx0 - 1, sx1 + 1, sy1 + 1, 'w'); c.vl(sx1 + 1, sy0 - 1, sy1 + 1, 'w');
  return [sx0, sy0, sx1, sy1];
}
function newCanvas(n) { return new Canvas(n, n); }

module.exports = { L, dropShadow, checker, ditherFill, page, tile, block, monitor, newCanvas };

// Stamp a full-size hand-drawn grid (rows must be exactly the canvas size; '|' separators are ignored).
function grid(c, rows, extra = {}) { return c.grid(rows, { ...L, ...extra }, 0, 0, true); }
module.exports.grid = grid;
