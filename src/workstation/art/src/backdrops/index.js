'use strict';
// Deep32/UX backdrops (ASSETS 4, LOOK-SOUND 7.1, DISTRO-DESIGN 6.6). CDE pattern style: small tiles whose colours are
// the palette-following symbols only (topShadowColor, background, selectColor, bottomShadowColor), so dtwm recolours
// each one from the workspace's colour set. Every generator is deterministic (xorshift32 with fixed seeds, integer
// frequencies, so each tile wraps seamlessly). Ported from ASSETS' reference generator (deep32-cde research
// tools/preview.js) with the 1440-row versions LOOK-SOUND 7.1 asks for.
const { BAYER8 } = require('../../lib/canvas');

function rng(seed) {
  let s = seed >>> 0 || 1;
  return () => { s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0; return s / 4294967296; };
}
function fromSym(c, sym) { for (let i = 0; i < sym.length; i++) c.set(i % c.w, Math.floor(i / c.w), sym[i]); }

const B = [];
function def(name, w, h, title, design, ref, gen, preview = ['Default', 3]) {
  B.push({ group: 'backdrops', file: name, w, h, title: `${title} backdrop (${w}x${h})`, ref, design, rule: 'cde',
    formats: ['pm'], preview, draw: c => fromSym(c, gen(w, h)), backdrop: true });
}

// 1 Strata: four sediment boundaries (Motif etched grooves), stipple on odd layers, rare selectColor specks.
def('Strata', 128, 64, 'Strata', 'four etched sediment boundaries, stipple on odd layers', 'ASSETS 4 #1', (W, H) => {
  const sym = new Array(W * H).fill('bg'), put = (x, y, v) => { sym[((y % H + H) % H) * W + ((x % W) + W) % W] = v; };
  const base = [4, 20, 36, 52], amp = [2, 3, 2, 3], f = [1, 2, 1, 3], ph = [0, 1.3, 2.6, 4.1];
  const bnd = (k, x) => base[k] + Math.round(amp[k] * Math.sin(2 * Math.PI * f[k] * x / W + ph[k]));
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    let layer = 0; for (let k = 0; k < 4; k++) if (y > bnd(k, x)) layer++;
    if (layer % 2 && y % 2 === 0 && (x + y / 2) % 4 === 0) put(x, y, 'sc');
  }
  for (let k = 0; k < 4; k++) for (let x = 0; x < W; x++) {
    const y = bnd(k, x), yp = bnd(k, (x + W - 1) % W);
    for (let yy = Math.min(y, yp); yy <= Math.max(y, yp); yy++) put(x, yy, 'bs');
    put(x, y + 1, 'ts');
  }
  const r = rng(1101);
  for (let i = 0; i < sym.length; i++) if (sym[i] === 'bg' && r() < 1 / 90) sym[i] = 'sc';
  return sym;
});

// 2 Herringbone: a 2/2 twill, mirrored at the seam (KnitLight's spirit).
def('Herringbone', 16, 16, 'Herringbone', '2/2 twill, mirrored halves', 'ASSETS 4 #2', (W, H) => {
  const sym = [];
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const d = x < 8 ? (x + y) & 3 : (x - y + 64) & 3;
    const v = ['ts', 'bg', 'bg', 'sc'][d];
    sym.push(v === 'ts' && (x === 7 || x === 15) ? 'bs' : v);
  }
  return sym;
}, ['Default', 5]);

// 3 Backplane: printed-circuit traces with engraved vias on a 12x12 grid of 8 px cells, toroidal.
def('Backplane', 96, 96, 'Backplane', 'circuit traces and vias, toroidal random walks', 'ASSETS 4 #3', (W, H) => {
  const sym = new Array(W * H).fill('bg'), put = (x, y, v) => { sym[((y % H + H) % H) * W + ((x % W) + W) % W] = v; };
  const occ = new Set(), r = rng(32), dirs = [[1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1]];
  const via = (cx, cy) => {
    put(cx - 1, cy - 1, 'bs'); put(cx, cy - 1, 'bs'); put(cx - 1, cy, 'bs');
    put(cx + 1, cy + 1, 'ts'); put(cx, cy + 1, 'ts'); put(cx + 1, cy, 'ts');
    put(cx, cy, 'sc'); put(cx + 1, cy - 1, 'bg'); put(cx - 1, cy + 1, 'bg');
  };
  for (let t = 0; t < 16; t++) {
    let i = Math.floor(r() * 12), j = Math.floor(r() * 12), d = Math.floor(r() * 4) * 2;
    if (occ.has(i + ',' + j)) continue;
    const start = [i, j]; occ.add(i + ',' + j);
    const len = 3 + Math.floor(r() * 7);
    for (let s = 0; s < len; s++) {
      if (r() > 0.62) d = (d + (r() < 0.5 ? 1 : 7)) & 7;
      const ni = (i + dirs[d][0] + 12) % 12, nj = (j + dirs[d][1] + 12) % 12;
      if (occ.has(ni + ',' + nj)) break;
      for (let k = 0; k <= 8; k++) { const x = i * 8 + 4 + dirs[d][0] * k, y = j * 8 + 4 + dirs[d][1] * k; put(x, y, 'sc'); put(x + 1, y + 1, 'ts'); }
      i = ni; j = nj; occ.add(i + ',' + j);
    }
    via(start[0] * 8 + 4, start[1] * 8 + 4); via(i * 8 + 4, j * 8 + 4);
  }
  return sym;
}, ['Default', 7]);

// 4 Granite: two-octave value noise on toroidal lattices; selectColor fleck field and rare light/dark flecks.
def('Granite', 128, 128, 'Granite', 'two-octave value noise flecks', 'ASSETS 4 #4', (W, H) => {
  const r = rng(1994), lat = n => Array.from({ length: n * n }, () => r());
  const L1 = lat(8), L2 = lat(16), sm = t => t * t * (3 - 2 * t);
  const vn = (L, n, x, y) => {
    const cs = W / n, gx = x / cs, gy = y / cs, x0 = Math.floor(gx), y0 = Math.floor(gy), fx = sm(gx - x0), fy = sm(gy - y0);
    const v = (a, b) => L[((b % n) + n) % n * n + ((a % n) + n) % n];
    return (v(x0, y0) * (1 - fx) + v(x0 + 1, y0) * fx) * (1 - fy) + (v(x0, y0 + 1) * (1 - fx) + v(x0 + 1, y0 + 1) * fx) * fy;
  };
  const sym = [];
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const v = 0.65 * vn(L1, 8, x, y) + 0.35 * vn(L2, 16, x, y), q = r();
    sym.push(q < 1 / 29 ? (v > 0.5 ? 'ts' : 'bs') : v < 0.36 ? (q < 0.5 ? 'sc' : 'bg') : 'bg');
  }
  return sym;
}, ['Default', 6]);

// 5 Contour: a bathymetric chart from six integer-frequency sines, 14 levels, every 4th an index contour.
def('Contour', 192, 192, 'Contour', 'bathymetric contour chart, index contours every 4th level', 'ASSETS 4 #5', (W, H) => {
  const T = [[1, 0, 1.0, 0.3], [0, 1, 0.9, 1.1], [1, 1, 0.6, 2.0], [2, -1, 0.4, 0.7], [1, 2, 0.35, 2.9], [3, 1, 0.2, 4.0]];
  const Hh = new Float64Array(W * H); let mn = 1e9, mx = -1e9;
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    let h = 0; for (const [fx, fy, a, p] of T) h += a * Math.sin(2 * Math.PI * (fx * x + fy * y) / W + p);
    Hh[y * W + x] = h; mn = Math.min(mn, h); mx = Math.max(mx, h);
  }
  const lev = (x, y) => Math.floor((Hh[((y + H) % H) * W + (x + W) % W] - mn) / (mx - mn + 1e-9) * 14);
  const sym = [];
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const l = lev(x, y), edge = l !== lev(x + 1, y) || l !== lev(x, y + 1);
    sym.push(edge ? (Math.max(l, lev(x + 1, y), lev(x, y + 1)) % 4 === 0 ? 'bs' : 'sc') : 'bg');
  }
  return sym;
}, ['Default', 3]);

// Vertical gradient strips with 8x8 Bayer ordered dither between neighbouring symbols (CDE's SkyDark technique).
function gradient(W, H, stops) {
  // stops: [[row, symbol], ...] ascending; between two stops the lower symbol fades in.
  const sym = [];
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    let k = 0; while (k + 1 < stops.length && y >= stops[k + 1][0]) k++;
    if (k + 1 >= stops.length) { sym.push(stops[k][1]); continue; }
    const [y0, a] = stops[k], [y1, b] = stops[k + 1];
    const f = (y - y0) / (y1 - y0);
    sym.push(f * 64 > BAYER8[y & 7][x & 7] + 0.5 ? b : a);
  }
  return sym;
}
// 6 Abyss1440: ASSETS' Abyss (surface light to the deep) regenerated at 1440 rows for the 34" head.
def('Abyss1440', 8, 1440, 'Abyss (1440 rows)', 'depth gradient ts -> bg -> sc -> bs, Bayer 8x8', 'ASSETS 4 #6 at LOOK-SOUND 7.1 height',
  (W, H) => gradient(W, H, [[0, 'ts'], [480, 'bg'], [960, 'sc'], [1439, 'bs']]), ['Default', 6]);
// 7 Horizon: the measured HP-UX 11i sky (light at the top), long flat middle band so both heads look right.
def('Horizon', 8, 1440, 'Horizon', 'sky gradient ts -> bg (rows 0-479), bg (480-1199), bg -> sc (1200-1439)', 'LOOK-SOUND 7.1',
  (W, H) => gradient(W, H, [[0, 'ts'], [480, 'bg'], [1200, 'bg'], [1439, 'sc']]), ['Default', 3]);

module.exports = { items: () => B };
