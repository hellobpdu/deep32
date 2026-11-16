'use strict';
// A small, deterministic 2.5D renderer for Deep32 Library-style illustrations (the disc's interface art is rendered
// 3-D clip art reduced to a small palette with dithering). Shapes are signed distance functions in final-pixel
// units; each gets a height field (a quarter-round bevel of radius r, plus an optional side wall extruded towards the
// lower right), is lit from the top left (Lambert + Blinn-Phong), supersampled ss x ss per pixel, then reduced to a
// fixed palette with serpentine Floyd-Steinberg (objects) and a deterministic noise threshold (shadow and glow).
// Pure JavaScript arithmetic only; same input, same bytes.

const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const mix = (a, b, t) => a + (b - a) * t;

// ---------- signed distance primitives (negative inside) ----------
function sdEllipse(px, py, cx, cy, rx, ry) { // Quilez's approximation; exact on the boundary
  const x = px - cx, y = py - cy;
  const k0 = Math.hypot(x / rx, y / ry), k1 = Math.hypot(x / (rx * rx), y / (ry * ry));
  return k1 < 1e-9 ? -Math.min(rx, ry) : (k0 * (k0 - 1)) / k1;
}
function sdCircle(px, py, cx, cy, r) { return Math.hypot(px - cx, py - cy) - r; }
function sdSegment(px, py, ax, ay, bx, by) {
  const pax = px - ax, pay = py - ay, bax = bx - ax, bay = by - ay;
  const h = clamp((pax * bax + pay * bay) / (bax * bax + bay * bay), 0, 1);
  return Math.hypot(pax - bax * h, pay - bay * h);
}
function sdTriangle(px, py, p0, p1, p2) {
  const e0 = [p1[0] - p0[0], p1[1] - p0[1]], e1 = [p2[0] - p1[0], p2[1] - p1[1]], e2 = [p0[0] - p2[0], p0[1] - p2[1]];
  const v0 = [px - p0[0], py - p0[1]], v1 = [px - p1[0], py - p1[1]], v2 = [px - p2[0], py - p2[1]];
  const pq = (v, e) => { const t = clamp((v[0] * e[0] + v[1] * e[1]) / (e[0] * e[0] + e[1] * e[1]), 0, 1); return [v[0] - e[0] * t, v[1] - e[1] * t]; };
  const q0 = pq(v0, e0), q1 = pq(v1, e1), q2 = pq(v2, e2);
  const s = Math.sign(e0[0] * e2[1] - e0[1] * e2[0]);
  const c = (q, v, e) => [q[0] * q[0] + q[1] * q[1], s * (v[0] * e[1] - v[1] * e[0])];
  const a = c(q0, v0, e0), b = c(q1, v1, e1), d = c(q2, v2, e2);
  const dx = Math.min(a[0], b[0], d[0]), dy = Math.min(a[1], b[1], d[1]);
  return -Math.sqrt(dx) * Math.sign(dy);
}
function smin(a, b, k) { const h = clamp(0.5 + 0.5 * (b - a) / k, 0, 1); return mix(b, a, h) - k * h * (1 - h); }

// ---------- colour ----------
function hex(h) { const s = h.replace('#', ''); return [0, 2, 4].map(i => parseInt(s.slice(i, i + 2), 16) / 255); }
function toHex(c) { return '#' + c.map(v => Math.round(clamp(v, 0, 1) * 255).toString(16).padStart(2, '0')).join('').toUpperCase(); }
function dist2(a, b) { // "redmean" weighted RGB distance, inputs 0..1
  const rm = (a[0] + b[0]) * 0.5, dr = a[0] - b[0], dg = a[1] - b[1], db = a[2] - b[2];
  return (2 + rm) * dr * dr + 4 * dg * dg + (3 - rm) * db * db;
}
// A ramp of n shades from dark to light through base (index of base = round(n * 0.62)).
function ramp(baseHex, n, darkHex = '#000000', lightHex = '#FFFFFF', dark = 0.78, light = 0.85) {
  const b = hex(baseHex), d = hex(darkHex), l = hex(lightHex), mid = Math.round((n - 1) * 0.62), out = [];
  for (let i = 0; i < n; i++) {
    let c;
    if (i <= mid) { const t = (mid - i) / Math.max(1, mid) * dark; c = b.map((v, k) => mix(v, d[k], t)); }
    else { const t = (i - mid) / Math.max(1, n - 1 - mid) * light; c = b.map((v, k) => mix(v, l[k], t)); }
    out.push(toHex(c));
  }
  return out;
}

// ---------- the scene ----------
// object: { sdf(x, y), r (bevel px), z, albedo: [r,g,b] | (x, y) => [r,g,b], ks, shin, side: [dx, dy] | null,
//           sideShade, emboss: (x, y) => height offset (grooves), noShadow }
const LIGHT = (() => { const v = [-0.5, -0.72, 0.62], n = Math.hypot(...v); return v.map(x => x / n); })();
const HALF = (() => { const v = [LIGHT[0], LIGHT[1], LIGHT[2] + 1], n = Math.hypot(...v); return v.map(x => x / n); })();

function heightAt(o, x, y) {
  const d = o.sdf(x, y);
  if (d >= 0) return 0;
  const t = clamp(-d / o.r, 0, 1);
  let h = o.r * Math.sqrt(1 - (1 - t) * (1 - t));
  if (o.emboss) h += o.emboss(x, y);
  return h;
}
function shadeFace(o, x, y) {
  const e = 0.35;
  const hx = (heightAt(o, x + e, y) - heightAt(o, x - e, y)) / (2 * e);
  const hy = (heightAt(o, x, y + e) - heightAt(o, x, y - e)) / (2 * e);
  let nx = -hx, ny = -hy, nz = 1; const nl = Math.hypot(nx, ny, nz); nx /= nl; ny /= nl; nz /= nl;
  const ndl = Math.max(0, nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]);
  const ndh = Math.max(0, nx * HALF[0] + ny * HALF[1] + nz * HALF[2]);
  const amb = o.ambient ?? 0.36, kd = 0.74, flat = amb + kd * LIGHT[2];
  const a = typeof o.albedo === 'function' ? o.albedo(x, y) : o.albedo;
  const lit = (amb + kd * ndl) / flat;
  const sp = (o.ks ?? 0.35) * Math.pow(ndh, o.shin ?? 24);
  return a.map(v => v * lit + sp);
}
function insideSide(o, x, y) { // on the extruded side wall (behind the front face, towards the lower right)
  if (!o.side) return -1;
  const steps = 8;
  for (let i = 1; i <= steps; i++) { const t = i / steps; if (o.sdf(x - o.side[0] * t, y - o.side[1] * t) < 0) return t; }
  return -1;
}

// Render objects into float buffers: col (w*h*3, premultiplied by coverage), cov (w*h coverage), shadow-casting cover.
function render(w, h, objects, opt = {}) {
  const ss = opt.ss || 4, ox = opt.ox || 0, oy = opt.oy || 0;
  const objs = [...objects].sort((a, b) => b.z - a.z); // front first
  const col = new Float64Array(w * h * 3), cov = new Float64Array(w * h), cast = new Float64Array(w * h);
  for (let py = 0; py < h; py++) for (let px = 0; px < w; px++) {
    let r = 0, g = 0, b = 0, n = 0, nc = 0;
    for (let sy = 0; sy < ss; sy++) for (let sx = 0; sx < ss; sx++) {
      const x = px + (sx + 0.5) / ss - ox, y = py + (sy + 0.5) / ss - oy;
      let hit = null;
      for (const o of objs) {
        if (o.sdf(x, y) < 0) { hit = shadeFace(o, x, y); if (!o.noShadow) nc++; break; }
        const t = insideSide(o, x, y);
        if (t >= 0) {
          const a = typeof o.albedo === 'function' ? o.albedo(x - o.side[0] * t, y - o.side[1] * t) : o.albedo;
          const k = (o.sideShade ?? 0.46) * (1 - 0.25 * t);
          hit = a.map(v => v * k); if (!o.noShadow) nc++; break;
        }
      }
      if (hit) { r += hit[0]; g += hit[1]; b += hit[2]; n++; }
    }
    const i = py * w + px, k = ss * ss;
    col[i * 3] = r / k; col[i * 3 + 1] = g / k; col[i * 3 + 2] = b / k; cov[i] = n / k; cast[i] = nc / k;
  }
  return { w, h, col, cov, cast };
}

// Separable box blur, repeated (approximates a Gaussian), zero outside.
function blur(src, w, h, radius, passes = 3) {
  let a = Float64Array.from(src), b = new Float64Array(w * h);
  const r = Math.max(0, Math.round(radius));
  if (!r) return a;
  for (let p = 0; p < passes; p++) {
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { let s = 0; for (let k = -r; k <= r; k++) { const xx = x + k; if (xx >= 0 && xx < w) s += a[y * w + xx]; } b[y * w + x] = s / (2 * r + 1); }
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { let s = 0; for (let k = -r; k <= r; k++) { const yy = y + k; if (yy >= 0 && yy < h) s += b[yy * w + x]; } a[y * w + x] = s / (2 * r + 1); }
  }
  return a;
}
function shift(src, w, h, dx, dy) {
  const out = new Float64Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { const sx = x - dx, sy = y - dy; if (sx >= 0 && sy >= 0 && sx < w && sy < h) out[y * w + x] = src[sy * w + sx]; }
  return out;
}
// Interleaved gradient noise: a deterministic, blue-ish threshold in [0,1).
function ign(x, y) { const f = v => v - Math.floor(v); return f(52.9829189 * f(0.06711056 * x + 0.00583715 * y)); }

function nearest(pal, c) { let best = 0, bd = Infinity; for (let i = 0; i < pal.length; i++) { const d = dist2(pal[i], c); if (d < bd) { bd = d; best = i; } } return best; }

// Compose one frame onto a token canvas.
//   bg(x, y) -> '#RRGGBB' | null (transparent)   matte: colour edges are blended with when bg is transparent
//   layers: [{ kind: 'shadow'|'glow', a: Float64Array, levels: ['#..', ...] }] applied under the object
//   R: render result; palette: ['#..']; dx, dy: frame offset of R inside the canvas
function compose(canvas, { R, x0 = 0, y0 = 0, bg, matte, layers = [], palette, cut = 0.5 }) {
  const pal = palette.map(hex), W = R.w, H = R.h;
  const base = new Array(W * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    let t = bg(x0 + x, y0 + y);
    for (const L of layers) {
      const a = L.a[y * W + x] * (L.gain ?? 1);
      if (a <= 0.02) continue;
      const lv = a * L.levels.length, k = Math.floor(lv), f = lv - k;
      const idx = Math.min(L.levels.length, k + (f > ign(x0 + x + (L.seed || 0), y0 + y) ? 1 : 0));
      if (idx > 0) t = L.levels[idx - 1];
    }
    base[y * W + x] = t;
  }
  // error diffusion over object pixels only (serpentine Floyd-Steinberg)
  const err = new Float64Array(W * H * 3);
  for (let y = 0; y < H; y++) {
    const ltr = (y & 1) === 0;
    for (let k = 0; k < W; k++) {
      const x = ltr ? k : W - 1 - k, i = y * W + x, a = R.cov[i];
      const under = base[i] ? hex(base[i]) : hex(matte);
      if (a < cut) { if (base[i]) canvas.set(x0 + x, y0 + y, base[i]); continue; }
      const want = [0, 1, 2].map(c => clamp(R.col[i * 3 + c] + under[c] * (1 - a) + err[i * 3 + c], 0, 1));
      const p = nearest(pal, want), got = pal[p];
      canvas.set(x0 + x, y0 + y, palette[p]);
      const e = want.map((v, c) => v - got[c]), dir = ltr ? 1 : -1;
      const push = (xx, yy, wgt) => {
        if (xx < 0 || xx >= W || yy >= H) return; const j = yy * W + xx; if (R.cov[j] < cut) return;
        for (let c = 0; c < 3; c++) err[j * 3 + c] += e[c] * wgt;
      };
      push(x + dir, y, 7 / 16); push(x - dir, y + 1, 3 / 16); push(x, y + 1, 5 / 16); push(x + dir, y + 1, 1 / 16);
    }
  }
}

module.exports = { sdEllipse, sdCircle, sdSegment, sdTriangle, smin, hex, toHex, ramp, render, blur, shift, ign, compose, clamp, mix, LIGHT };
