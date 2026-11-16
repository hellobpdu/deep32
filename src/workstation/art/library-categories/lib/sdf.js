'use strict';
// Signed-distance shapes for the engraving renderer. A shape is { d(x, y) -> distance (negative inside), bb: [x0, y0, x1, y1] }
// in design units. All exact or close to exact (ellipses use Inigo Quilez's approximation), so outlines keep their width.
const { hypot, min, max, abs, cos, sin, sqrt, atan2, PI } = Math;
const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const BIG = 1e9;
const bbU = bbs => [min(...bbs.map(b => b[0])), min(...bbs.map(b => b[1])), max(...bbs.map(b => b[2])), max(...bbs.map(b => b[3]))];
const grow = (bb, r) => [bb[0] - r, bb[1] - r, bb[2] + r, bb[3] + r];

function circle(cx, cy, r) { return { d: (x, y) => hypot(x - cx, y - cy) - r, bb: [cx - r, cy - r, cx + r, cy + r] }; }
function ellipse(cx, cy, rx, ry) {
  return {
    d: (x, y) => {
      const px = x - cx, py = y - cy, k0 = hypot(px / rx, py / ry), k1 = hypot(px / (rx * rx), py / (ry * ry));
      return k1 === 0 ? -min(rx, ry) : k0 * (k0 - 1) / k1;
    }, bb: [cx - rx, cy - ry, cx + rx, cy + ry],
  };
}
function box(x0, y0, x1, y1, r = 0) {
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, hx = (x1 - x0) / 2 - r, hy = (y1 - y0) / 2 - r;
  return {
    d: (x, y) => { const qx = abs(x - cx) - hx, qy = abs(y - cy) - hy; return hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r; },
    bb: [x0, y0, x1, y1],
  };
}
function poly(pts) {
  const n = pts.length;
  return {
    d: (x, y) => {
      let d = BIG, s = 1;
      for (let i = 0, j = n - 1; i < n; j = i, i++) {
        const [xi, yi] = pts[i], [xj, yj] = pts[j];
        const ex = xj - xi, ey = yj - yi, wx = x - xi, wy = y - yi;
        const t = clamp((wx * ex + wy * ey) / (ex * ex + ey * ey || 1), 0, 1);
        const bx = wx - ex * t, by = wy - ey * t; d = min(d, bx * bx + by * by);
        const c1 = y >= yi, c2 = y < yj, c3 = ex * wy > ey * wx;
        if ((c1 && c2 && c3) || (!c1 && !c2 && !c3)) s = -s;
      }
      return s * sqrt(d);
    },
    bb: [min(...pts.map(p => p[0])), min(...pts.map(p => p[1])), max(...pts.map(p => p[0])), max(...pts.map(p => p[1]))],
  };
}
// capsule from (x1,y1) to (x2,y2), total width w (w2 = width at the far end, for tapering strokes)
function seg(x1, y1, x2, y2, w, w2 = w) {
  const ex = x2 - x1, ey = y2 - y1, ll = ex * ex + ey * ey || 1;
  return {
    d: (x, y) => { const t = clamp(((x - x1) * ex + (y - y1) * ey) / ll, 0, 1); return hypot(x - x1 - ex * t, y - y1 - ey * t) - (w + (w2 - w) * t) / 2; },
    bb: grow([min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)], max(w, w2) / 2),
  };
}
function stroke(pts, w, closed = false, w2 = w) {
  const segs = []; const n = pts.length - (closed ? 0 : 1);
  for (let i = 0; i < n; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    const wa = w + (w2 - w) * (i / n), wb = w + (w2 - w) * ((i + 1) / n);
    segs.push(seg(a[0], a[1], b[0], b[1], wa, wb));
  }
  return union(...segs);
}
function ring(cx, cy, r, w) { return { d: (x, y) => abs(hypot(x - cx, y - cy) - r) - w / 2, bb: [cx - r - w, cy - r - w, cx + r + w, cy + r + w] }; }
function arc(cx, cy, r, a0, a1, w, n = 24) { return stroke(arcPts(cx, cy, r, r, a0, a1, n), w); }
function arcPts(cx, cy, rx, ry, a0, a1, n = 24) { const p = []; for (let i = 0; i <= n; i++) { const a = (a0 + (a1 - a0) * i / n) * PI / 180; p.push([cx + rx * cos(a), cy + ry * sin(a)]); } return p; }
function bez(p0, p1, p2, p3, n = 16) {
  const out = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n, u = 1 - t;
    out.push([u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0], u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]]);
  }
  return out;
}
// a path of [x,y] points and ['c', c1, c2, end] cubic segments -> points
function path(spec, n = 12) {
  const out = [];
  for (const s of spec) {
    if (s[0] === 'c') { const p0 = out[out.length - 1]; out.push(...bez(p0, s[1], s[2], s[3], n).slice(1)); }
    else out.push(s);
  }
  return out;
}
function union(...s) {
  s = s.flat();
  return { d: (x, y) => { let d = BIG; for (const k of s) { const b = k.bb; if (x < b[0] - d || x > b[2] + d || y < b[1] - d || y > b[3] + d) continue; d = min(d, k.d(x, y)); } return d; }, bb: bbU(s.map(k => k.bb)) };
}
function inter(a, b) { return { d: (x, y) => max(a.d(x, y), b.d(x, y)), bb: [max(a.bb[0], b.bb[0]), max(a.bb[1], b.bb[1]), min(a.bb[2], b.bb[2]), min(a.bb[3], b.bb[3])] }; }
function sub(a, ...b) { const u = union(...b); return { d: (x, y) => max(a.d(x, y), -u.d(x, y)), bb: a.bb }; }
function offset(a, r) { return { d: (x, y) => a.d(x, y) - r, bb: grow(a.bb, max(r, 0)) }; }
// rotate a shape by deg about (cx, cy)
function rot(a, deg, cx, cy) {
  const r = deg * PI / 180, c = cos(r), s = sin(r);
  const corners = [[a.bb[0], a.bb[1]], [a.bb[2], a.bb[1]], [a.bb[0], a.bb[3]], [a.bb[2], a.bb[3]]].map(([x, y]) => [cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c]);
  return { d: (x, y) => { const dx = x - cx, dy = y - cy; return a.d(cx + dx * c + dy * s, cy - dx * s + dy * c); }, bb: [min(...corners.map(p => p[0])), min(...corners.map(p => p[1])), max(...corners.map(p => p[0])), max(...corners.map(p => p[1]))] };
}
function move(a, dx, dy) { return { d: (x, y) => a.d(x - dx, y - dy), bb: [a.bb[0] + dx, a.bb[1] + dy, a.bb[2] + dx, a.bb[3] + dy] }; }
// gear: radius r to the tooth root, n teeth of height th; duty = tip width as a share of the pitch.
// Built as an exact polygon (trapezoid teeth), so outlines keep their width along the flanks too.
function gear(cx, cy, r, n, th, duty = 0.45) {
  const pts = [], pitch = 2 * PI / n, tip = pitch * duty * 0.5, base = pitch * Math.min(0.48, duty * 0.5 + 0.12);
  for (let i = 0; i < n; i++) {
    const c = i * pitch;
    for (const [a, rr] of [[c - base, r], [c - tip, r + th], [c + tip, r + th], [c + base, r]]) pts.push([cx + cos(a) * rr, cy + sin(a) * rr]);
  }
  return poly(pts);
}
// regular polygon / star
function ngon(cx, cy, r, n, rotDeg = -90, inner = 0) {
  const pts = [];
  for (let i = 0; i < n * (inner ? 2 : 1); i++) {
    const a = (rotDeg + i * 360 / (n * (inner ? 2 : 1))) * PI / 180, rr = inner && (i & 1) ? inner : r;
    pts.push([cx + rr * cos(a), cy + rr * sin(a)]);
  }
  return poly(pts);
}
// half-plane clip helpers
function halfX(x0, keepRight = true) { return { d: (x) => (keepRight ? x0 - x : x - x0), bb: [-BIG, -BIG, BIG, BIG] }; }
function halfY(y0, keepBelow = true) { return { d: (x, y) => (keepBelow ? y0 - y : y - y0), bb: [-BIG, -BIG, BIG, BIG] }; }

module.exports = { circle, ellipse, box, poly, seg, stroke, ring, arc, arcPts, bez, path, union, inter, sub, offset, rot, move, gear, ngon, halfX, halfY, clamp };
