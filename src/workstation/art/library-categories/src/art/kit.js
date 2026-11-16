'use strict';
// Drawing kit shared by the twenty category drawings: layer shorthands and a few solids in oblique projection.
// Light comes from the upper left, as in the 1995 engravings; shade faces and cast shadows go lower right.
const S = require('../../lib/sdf');
const { T, place } = require('../../lib/engrave');

const SHADE = { dir: [0.75, 0.66], w: 5, k: 0.85, p: 3 };      // watermark scale: shade band along lower-right edges
const SHADE_S = { dir: [0.75, 0.66], w: 2.2, k: 0.7, p: 2 };   // button scale
const LIT = { dir: [-0.7, -0.7], w: 1.5 };

// layer shorthands
const F = (shape, tone = 0, hatch, extra = {}) => ({ shape, tone, ...(hatch ? { hatch } : {}), ...extra });
const INK = shape => ({ shape, kind: 'ink' });
const PAPER = shape => ({ shape, kind: 'paper' });
const H = (a, p = 3, ph = 0) => ({ a, p, ph });
const RING = (cx, cy, p = 3) => ({ ring: [cx, cy], p });
const CONTOUR = (p = 3) => ({ contour: true, p });
// watermark-scale options for the solids: 1.6 px outlines, 3 px hatching
const W = (o = {}) => ({ p: 3, ...o, extra: { line: 1.6, ...(o.extra || {}) } });

// oblique box: front face (x, y, w, h), depth `dep` going up-right
function slab(x, y, w, h, dep, o = {}) {
  const dx = dep * (o.kx ?? 0.62), dy = dep * (o.ky ?? 0.42);
  const top = S.poly([[x, y], [x + w, y], [x + w + dx, y - dy], [x + dx, y - dy]]);
  const side = S.poly([[x + w, y], [x + w + dx, y - dy], [x + w + dx, y + h - dy], [x + w, y + h]]);
  const front = S.box(x, y, x + w, y + h);
  const hp = o.p || 3;
  return [
    F(side, o.side ?? 0.72, H(o.sideA ?? 60, hp), o.extra),
    F(top, o.top ?? 0.08, H(o.topA ?? 0, hp), o.extra),
    F(front, o.front ?? 0.3, H(o.frontA ?? 0, hp), o.extra),
  ];
}

// vertical cylinder seen slightly from above: body from y0 to y1, centre cx, radius r, ellipse ratio e
function cylinder(cx, y0, y1, r, e = 0.3, o = {}) {
  const body = S.union(S.box(cx - r, y0, cx + r, y1), S.ellipse(cx, y1, r, r * e));
  const top = S.ellipse(cx, y0, r, r * e);
  return [
    F(body, T.cyl(cx - r, cx + r, o.lo ?? 0.02, o.hi ?? 0.85, o.lightAt ?? 0.3), H(o.a ?? 90, o.p || 3)),
    F(top, o.top ?? 0.05, H(0, o.p || 3)),
  ];
}

// a pitched roof with up-turned eaves (pagoda, signal box): ridge at (cx, y), eaves at y + h, half-width hw
function eaveRoof(cx, y, ridgeHalf, hw, h, curl) {
  return S.poly(S.path([
    [cx - ridgeHalf, y], [cx + ridgeHalf, y],
    ['c', [cx + ridgeHalf + (hw - ridgeHalf) * 0.45, y + h * 0.55], [cx + hw * 0.92, y + h * 1.0], [cx + hw, y + h - curl]],
    [cx + hw * 0.9, y + h + 1.2],
    ['c', [cx + hw * 0.5, y + h + 1.8], [cx - hw * 0.5, y + h + 1.8], [cx - hw * 0.9, y + h + 1.2]],
    [cx - hw, y + h - curl],
    ['c', [cx - hw * 0.92, y + h * 1.0], [cx - ridgeHalf - (hw - ridgeHalf) * 0.45, y + h * 0.55], [cx - ridgeHalf, y]],
  ], 10));
}

// masonry joints as ink lines clipped to a region: courses of height ch, stones of width sw, joint width jw
function masonry(region, x0, y0, x1, y1, ch, sw, jw = 1) {
  const segs = [];
  let row = 0;
  for (let y = y0 + ch; y < y1; y += ch, row++) segs.push(S.seg(x0, y, x1, y, jw));
  row = 0;
  for (let y = y0; y < y1; y += ch, row++) for (let x = x0 + (row % 2 ? sw / 2 : 0); x < x1; x += sw) segs.push(S.seg(x, y, x, Math.min(y + ch, y1), jw));
  return { shape: S.inter(S.union(segs), region), kind: 'ink' };
}

// text-like engraved marks: rows of short dashes inside a box
function lettering(x0, y0, x1, y1, rows, w = 0.8, seed = 1) {
  const segs = []; let r = seed;
  const rnd = () => { r = (r * 16807) % 2147483647; return r / 2147483647; };
  const lh = (y1 - y0) / rows;
  for (let i = 0; i < rows; i++) {
    let x = x0; const y = y0 + lh * (i + 0.5);
    while (x < x1) { const len = 1.5 + rnd() * 3.5; segs.push(S.seg(x, y, Math.min(x1, x + len), y, w)); x += len + 1.6; }
  }
  return S.union(segs);
}

// pen-and-ink defaults: white (lit) fills with a bold outline, the lower-right edges shaded by tapering contour lines
const WS = { dir: [0.7, 0.72], w: 8, k: 0.95, p: 3.2 };
const BS = { dir: [0.7, 0.72], w: 3, k: 0.9, p: 2 };
const WF = (shape, extra = {}) => ({ shape, tone: 0, line: 2, shade: WS, ...extra });
const BF = (shape, extra = {}) => ({ shape, tone: 0, line: 1, shade: BS, ...extra });
// irregular polygon: jitter every vertex by up to j units (deterministic)
function rough(pts, j, seed = 3) { let r = seed; const rnd = () => { r = (r * 16807) % 2147483647; return r / 2147483647 - 0.5; }; return pts.map(([x, y]) => [x + rnd() * 2 * j, y + rnd() * 2 * j]); }
// subdivide a polygon's edges into n pieces each (for rough outlines)
function subdiv(pts, n) { const o = []; for (let i = 0; i < pts.length; i++) { const a = pts[i], b = pts[(i + 1) % pts.length]; for (let k = 0; k < n; k++) o.push([a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n]); } return o; }

// spoked wheel: rim ring, hub, n spokes; returns layers drawn with mk (WF or BF)
function wheel(cx, cy, r, n, rimW, mk, o = {}) {
  const spokes = []; for (let i = 0; i < n; i++) { const a = (i / n) * 2 * Math.PI + (o.rot || 0); spokes.push(S.seg(cx, cy, cx + Math.cos(a) * r, cy + Math.sin(a) * r, o.spokeW || rimW * 0.45)); }
  return [
    mk(S.sub(S.circle(cx, cy, r), S.circle(cx, cy, r - rimW)), { shade: { ...(o.shade || {}), dir: [0.7, 0.72], w: rimW, k: 0.9, p: 2 } }),
    mk(S.union(spokes), { shade: null, line: o.spokeLine ?? 1 }),
    mk(S.circle(cx, cy, o.hub || r * 0.18), {}),
  ];
}
// a wall pierced by a row of round-headed arches: x0..x1, y0 (top)..y1 (bottom), n bays, pier width pw, spring height
function arcade(x0, y0, x1, y1, n, pw, spring) {
  const bay = (x1 - x0) / n, holes = [];
  for (let i = 0; i < n; i++) {
    const a = x0 + i * bay + pw / 2, b = x0 + (i + 1) * bay - pw / 2, r = (b - a) / 2;
    holes.push(S.union(S.box(a, spring, b, y1 + 5), S.circle((a + b) / 2, spring, r)));
  }
  return { wall: S.sub(S.box(x0, y0, x1, y1), ...holes), holes, bay };
}
// a lattice between two rails (lists of points of equal length): rails and zig-zag bracing as one shape
function lattice(railA, railB, w, wb = w * 0.7) {
  const parts = [S.stroke(railA, w), S.stroke(railB, w)];
  for (let i = 0; i < railA.length - 1; i++) { parts.push(S.seg(railA[i][0], railA[i][1], railB[i + 1][0], railB[i + 1][1], wb), S.seg(railB[i][0], railB[i][1], railA[i + 1][0], railA[i + 1][1], wb), S.seg(railA[i][0], railA[i][1], railB[i][0], railB[i][1], wb)); }
  return S.union(parts);
}
const lerp = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
const range = (n, f) => Array.from({ length: n }, (_, i) => f(i));
// a thin line that stays visible across the watermark's cream band: ink on the white ground, white (paper) on the band
const BAND = 100;
const LINE = shape => [{ shape, kind: 'ink', only: x => x >= BAND }, { shape, kind: 'paper', only: x => x < BAND }];

module.exports = { LINE, BAND, wheel, arcade, lattice, lerp, range, WS, BS, WF, BF, rough, subdiv, S, T, place, F, INK, PAPER, H, RING, CONTOUR, W, SHADE, SHADE_S, LIT, slab, cylinder, eaveRoof, masonry, lettering };
