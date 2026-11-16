'use strict';
// Procedural engraving: a painter's list of signed-distance layers rendered to a 1-bit mask (1 = ink), the way a
// woodcut or a steel engraving is cut: an outline, then parallel hatching whose line width follows the tone.
//
// Layer fields (all optional except shape):
//   shape  : from lib/sdf.js (design units)
//   kind   : 'fill' (default: outline + hatched tone), 'ink' (solid ink), 'paper' (solid paper, cuts white lines)
//   tone   : 0..1 darkness, number or fn(x, y, px) (x, y design units; px = pixel helpers), default 0
//   hatch  : { a: degrees, p: period px, ph: phase } straight lines, { ring: [cx, cy], p } concentric,
//            { contour: true, p } lines parallel to the outline; default { a: -30, p: 3 }
//   line   : outline width in px, inside the shape (default 1 for fills, 0 otherwise)
//   shade  : { dir: [dx, dy], w: px, k: 0..1, contour: bool } darken the band along edges facing dir (the shade side)
//   lit    : { dir: [dx, dy], w: px } leave a paper band (no outline) along edges facing dir (the lit side)
//   only   : fn(x, y) -> bool, restrict the layer to where it returns true (e.g. a clip)
// render() returns { w, h, m: Uint8Array } with m = 1 for ink.
const DEF_HATCH = { a: -30, p: 3 };

function hatchU(h, px, py, d, X, Y, s) {
  if (h.contour) return -d;
  if (h.ring) return Math.hypot(X - h.ring[0], Y - h.ring[1]) * s;
  const r = (h.a || 0) * Math.PI / 180;
  return -px * Math.sin(r) + py * Math.cos(r);
}
const inkAt = (u, p, t, ph = 0) => { if (t <= 0) return 0; if (t >= 1) return 1; const f = ((u / p + ph) % 1 + 1) % 1; return f < t ? 1 : 0; };

function render({ w, h, layers, view = { s: 1, ox: 0, oy: 0 }, ground = () => 0, flat = false, flatCut = 0.5 }) {
  const m = new Uint8Array(w * h);
  const { s, ox, oy } = view;
  const L = layers.filter(Boolean).map(l => ({ kind: 'fill', tone: 0, ...l, hatch: l.hatch || DEF_HATCH, line: l.line ?? ((l.kind || 'fill') === 'fill' ? 1 : 0) }));
  for (let py = 0; py < h; py++) for (let px = 0; px < w; px++) {
    // sample a hair off the pixel centre so a 1 px line on an integer coordinate lands on exactly one row
    const X = (px + 0.5 + 0.013 - ox) / s, Y = (py + 0.5 + 0.017 - oy) / s;
    let v = ground(px, py);
    for (let i = L.length - 1; i >= 0; i--) {
      const l = L[i], b = l.shape.bb;
      if (X < b[0] || X > b[2] || Y < b[1] || Y > b[3]) continue;
      if (l.only && !l.only(X, Y)) continue;
      const d = l.shape.d(X, Y) * s; // px
      if (d >= 0) continue;
      if (l.kind === 'ink') { v = 1; break; }
      if (l.kind === 'paper') { v = 0; break; }
      // edge normal, only when needed
      let nx = 0, ny = 0;
      if (l.shade || l.lit) {
        const e = 0.5 / s, gx = l.shape.d(X + e, Y) - l.shape.d(X - e, Y), gy = l.shape.d(X, Y + e) - l.shape.d(X, Y - e), gl = Math.hypot(gx, gy) || 1;
        nx = gx / gl; ny = gy / gl;
      }
      const depth = -d;
      if (l.lit) {
        const [lx, ly] = l.lit.dir, ll = Math.hypot(lx, ly), f = (nx * lx + ny * ly) / ll;
        if (f > 0.35 && depth < l.lit.w) { v = 0; break; }
      }
      if (depth < l.line) { v = 1; break; }
      let t = typeof l.tone === 'function' ? l.tone(X, Y, { px, py, d }) : l.tone;
      let hh = l.hatch;
      if (l.shade) {
        const [sx, sy] = l.shade.dir, sl = Math.hypot(sx, sy), f = Math.max(0, (nx * sx + ny * sy) / sl);
        const band = Math.max(0, 1 - depth / l.shade.w) * f * (l.shade.k ?? 0.8);
        if (band > 0.05 && band > t * 0.5) { t = Math.min(1, t + band); if (l.shade.contour !== false) hh = { contour: true, p: l.shade.p || hh.p || 3 }; }
      }
      if (flat) v = t >= flatCut ? 1 : 0;
      else v = inkAt(hatchU(hh, px, py, d, X, Y, s), hh.p || 3, t, hh.ph || 0);
      break;
    }
    m[py * w + px] = v;
  }
  return { w, h, m };
}

// tone helpers ------------------------------------------------------------------------------------------------
const T = {
  // linear ramp from t0 at (x0,y0) to t1 at (x1,y1)
  lin: (x0, y0, x1, y1, t0, t1) => (x, y) => {
    const ex = x1 - x0, ey = y1 - y0, k = Math.max(0, Math.min(1, ((x - x0) * ex + (y - y0) * ey) / (ex * ex + ey * ey)));
    return t0 + (t1 - t0) * k;
  },
  // sphere lit from the upper left: tone rises towards the lower right limb
  sphere: (cx, cy, r, lo = 0, hi = 0.9, L = [-0.55, -0.65, 0.52]) => (x, y) => {
    const nx = (x - cx) / r, ny = (y - cy) / r, nz = Math.sqrt(Math.max(0, 1 - nx * nx - ny * ny));
    const ln = Math.hypot(...L), dot = (nx * L[0] + ny * L[1] + nz * L[2]) / ln;
    return lo + (hi - lo) * Math.max(0, Math.min(1, (1 - dot) / 1.3));
  },
  // vertical cylinder between x0 and x1 lit from the left
  cyl: (x0, x1, lo = 0, hi = 0.85, lightAt = 0.3) => x => {
    const u = (x - x0) / (x1 - x0); const k = Math.abs(u - lightAt) / (u < lightAt ? lightAt : 1 - lightAt);
    return lo + (hi - lo) * Math.min(1, k * k);
  },
  // horizontal cylinder between y0 and y1 lit from above
  cylY: (y0, y1, lo = 0, hi = 0.85, lightAt = 0.3) => (x, y) => T.cyl(y0, y1, lo, hi, lightAt)(y),
};

// mask utilities ------------------------------------------------------------------------------------------------
function clean(r, minRun = 1) {
  // remove isolated single ink pixels (no 8-neighbour), which read as dirt in a 1-bit engraving
  const { w, h, m } = r; const o = new Uint8Array(m);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    if (!m[y * w + x]) continue;
    let n = 0;
    for (let j = -1; j <= 1; j++) for (let i = -1; i <= 1; i++) { if (!i && !j) continue; const X = x + i, Y = y + j; if (X >= 0 && Y >= 0 && X < w && Y < h && m[Y * w + X]) n++; }
    if (n < minRun) o[y * w + x] = 0;
  }
  return { w, h, m: o };
}

// place: scale a layer list by s and move it by (dx, dy), shapes, tones, clips and ring hatch centres alike
function place(layers, s, dx, dy) {
  return layers.filter(Boolean).map(l => {
    const sh = l.shape;
    const shape = { d: (x, y) => s * sh.d((x - dx) / s, (y - dy) / s), bb: [sh.bb[0] * s + dx, sh.bb[1] * s + dy, sh.bb[2] * s + dx, sh.bb[3] * s + dy] };
    const tone = typeof l.tone === 'function' ? (x, y, c) => l.tone((x - dx) / s, (y - dy) / s, c) : l.tone;
    const hatch = l.hatch && l.hatch.ring ? { ...l.hatch, ring: [l.hatch.ring[0] * s + dx, l.hatch.ring[1] * s + dy] } : l.hatch;
    const only = l.only ? (x, y) => l.only((x - dx) / s, (y - dy) / s) : undefined;
    return { ...l, shape, tone, hatch, only };
  });
}

module.exports = { render, T, clean, inkAt, place };
