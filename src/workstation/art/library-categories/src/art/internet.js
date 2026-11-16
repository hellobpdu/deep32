'use strict';
// Internet & the Web. Emblem: a globe girdled by a cable. Watermark: a globe on a stand wrapped in submarine cable.
const { S, T, place, F, INK, H, WF, BF, WS, range } = require('./kit');

// stylised land masses on a globe facing the Atlantic (centre cx, cy, radius r)
function lands(cx, cy, r) {
  const P = pts => S.poly(S.path(pts.map(p => (p[0] === 'c' ? ['c', ...p.slice(1).map(q => [cx + q[0] * r, cy + q[1] * r])] : [cx + p[0] * r, cy + p[1] * r])), 8));
  return S.union(
    // the Americas on the left limb
    P([[-0.95, -0.35], [-0.55, -0.62], [-0.4, -0.45], ['c', [-0.5, -0.25], [-0.62, -0.1], [-0.55, 0.05]], ['c', [-0.45, 0.1], [-0.3, 0.18], [-0.32, 0.35]], ['c', [-0.38, 0.55], [-0.5, 0.7], [-0.56, 0.82]], [-0.66, 0.7], ['c', [-0.7, 0.45], [-0.8, 0.25], [-0.9, 0.12]], [-0.99, -0.05]]),
    // Europe and Africa
    P([[0.05, -0.72], [0.3, -0.8], [0.5, -0.62], [0.4, -0.45], ['c', [0.3, -0.38], [0.18, -0.4], [0.12, -0.3]], ['c', [0.0, -0.22], [-0.08, -0.05], [0.02, 0.08]], ['c', [0.1, 0.18], [0.25, 0.15], [0.3, 0.3]], ['c', [0.38, 0.5], [0.3, 0.68], [0.22, 0.8]], ['c', [0.12, 0.65], [0.08, 0.45], [-0.02, 0.32]], ['c', [-0.15, 0.2], [-0.2, 0.0], [-0.12, -0.18]], ['c', [-0.05, -0.3], [-0.05, -0.5], [0.05, -0.72]]]),
    P([[0.62, -0.5], [0.85, -0.42], [0.95, -0.15], [0.8, 0.05], [0.66, -0.18]]),
  );
}

// a tilted ellipse band (the cable's path) as a shape, and its front or back half
function tilted(cx, cy, rx, ry, tilt) {
  const c = Math.cos(-tilt), sn = Math.sin(-tilt), e = S.ellipse(0, 0, rx, ry);
  return { d: (x, y) => e.d((x - cx) * c - (y - cy) * sn, (x - cx) * sn + (y - cy) * c), bb: [cx - rx, cy - rx, cx + rx, cy + rx] };
}
function halfOf(shape, cx, cy, tilt, front) {
  const c = Math.cos(-tilt), sn = Math.sin(-tilt);
  return S.inter(shape, { d: (x, y) => { const Y = (x - cx) * sn + (y - cy) * c; return front ? -Y : Y; }, bb: shape.bb });
}
// graticule lines clipped to a globe
function graticule(cx, cy, r, w) {
  const g = S.circle(cx, cy, r);
  return S.inter(S.union(
    ...[0.42, 0.8].map(k => ({ d: (x, y) => Math.abs(S.ellipse(cx, cy, r * k, r).d(x, y)) - w / 2, bb: g.bb })),
    S.seg(cx, cy - r, cx, cy + r, w),
    ...[-0.5, 0, 0.5].map(k => { const yy = cy + k * r, hw = Math.sqrt(1 - k * k) * r; return S.seg(cx - hw, yy, cx + hw, yy, w); }),
  ), g);
}

function emblem() {
  const cx = 32, cy = 32, r = 24, tilt = -0.35;
  const ring = S.sub(tilted(cx, cy, 31, 9, tilt), tilted(cx, cy, 27, 5.5, tilt));
  return [
    BF(halfOf(ring, cx, cy, tilt, false), { tone: 0.6, hatch: H(60, 2), shade: null }),
    BF(S.circle(cx, cy, r), { tone: T.sphere(cx, cy, r, 0, 0.75), hatch: { ring: [cx - 8, cy - 8], p: 2 }, shade: null }),
    INK(graticule(cx, cy, r, 1)),
    BF(halfOf(ring, cx, cy, tilt, true), { tone: 0.15, hatch: H(60, 2), shade: null }),
  ];
}

function watermark() {
  const L = []; const cx = 178, cy = 178, r = 122;
  const globe = S.circle(cx, cy, r);
  // stand: foot, stem, the brass meridian half-ring
  L.push(WF(S.ellipse(cx, 384, 70, 16), { tone: T.lin(0, 372, 0, 400, 0, 0.9), hatch: H(0, 3), shade: null }));
  L.push(WF(S.poly(S.path([[cx - 16, 376], ['c', [cx - 8, 350], [cx - 10, 330], [cx - 6, 314]], [cx + 6, 314], ['c', [cx + 10, 330], [cx + 8, 350], [cx + 16, 376]]], 10)), { tone: T.cyl(cx - 16, cx + 16, 0, 0.9), hatch: H(90, 3), shade: null }));
  const mer = S.inter(S.sub(S.circle(cx, cy, r + 16), S.circle(cx, cy, r + 6)), S.halfX(cx - 30));
  L.push(WF(mer, { shade: { ...WS, w: 6 } }));
  // the globe: ocean white, land in solid tone with a lit edge, graticule
  L.push(WF(globe, { tone: (x, y) => Math.max(0, T.sphere(cx, cy, r, -0.4, 0.9)(x, y)), hatch: { contour: true, p: 3.4 }, shade: null }));
  L.push(INK(S.inter(S.union(
    ...[0.35, 0.7].map(k => ({ d: (x, y) => Math.abs(S.ellipse(cx, cy, r * k, r).d(x, y)) - 0.6, bb: globe.bb })),
    S.seg(cx, cy - r, cx, cy + r, 1.2),
    ...[-0.75, -0.4, 0, 0.4, 0.75].map(k => { const yy = cy + k * r * 0.9, hw = Math.sqrt(1 - (k * 0.9) ** 2) * r; return S.seg(cx - hw, yy, cx + hw, yy, 1.2); }),
  ), globe)));
  L.push(F(S.inter(lands(cx, cy, r), globe), (x, y) => Math.min(1, 0.55 + T.sphere(cx, cy, r, 0, 0.6)(x, y)), H(-40, 3), { line: 1.6 }));
  L.push(WF(S.circle(cx, cy - r - 10, 6), { shade: null }));
  L.push(WF(S.circle(cx, cy + r + 10, 6), { shade: null }));
  // the submarine cable spiralling round: front passes only (behind the globe they are hidden)
  for (const [tilt, a, b, ry] of [[-0.42, -0.9, 0.6, 0.32], [-0.15, -0.6, 0.9, 0.22], [0.3, -0.75, 0.2, 0.42]]) {
    // points round a tilted ellipse; keep the front pass only (sin t > -0.15: the part nearer the viewer)
    const front = range(40, i => Math.PI * (a + (b - a) * i / 39)).filter(t => Math.sin(t) > -0.15).map(t => {
      const X = Math.cos(t) * (r + 26), Y = Math.sin(t) * (r + 26) * ry;
      return [cx + X * Math.cos(tilt) - Y * Math.sin(tilt), cy + X * Math.sin(tilt) + Y * Math.cos(tilt)];
    });
    if (front.length > 1) L.push(WF(S.stroke(front, 9), { tone: 0.35, hatch: H(50, 3.2), line: 1.6, shade: null }));
  }
  return L;
}

module.exports = {
  id: 'internet',
  emblem,
  button: () => place(emblem(), 0.86, 112, -6),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 64, ox: 0, oy: 0 } }),
  watermark,
};
