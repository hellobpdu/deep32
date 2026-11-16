'use strict';
// Data Centre & Fabrics. Emblem: rows of cabinets in perspective. Watermark: a Roman aqueduct of two tiers.
const { S, T, place, F, INK, H, WF, BF, WS, arcade, masonry, range } = require('./kit');

// one-point perspective: cabinets on both sides of an aisle receding to (vx, vy)
function emblem() {
  const L = []; const vx = 34, vy = 22;
  const toV = (x, y, k) => [x + (vx - x) * k, y + (vy - y) * k];
  for (const side of [-1, 1]) {
    const xNear = side < 0 ? 0 : 64, xIn = side < 0 ? 14 : 50;
    for (let i = 3; i >= 0; i--) {
      const k0 = i * 0.2, k1 = k0 + 0.18;
      const a = toV(xIn, 2, k0), b = toV(xIn, 60, k0), c = toV(xIn, 60, k1), d = toV(xIn, 2, k1);
      const face = S.poly([a, d, c, b]);
      L.push(BF(face, { shade: null }));
      L.push(INK(S.inter(S.union(range(8, j => { const t = (j + 1) / 9; const p = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], q = [d[0] + (c[0] - d[0]) * t, d[1] + (c[1] - d[1]) * t]; return S.seg(p[0], p[1], q[0], q[1], 0.8); })), S.offset(face, -1.6))));
    }
    // the near cabinet's front end, dark in the aisle's shade
    L.push(BF(S.poly([[xNear, 0], [xIn, 2], [xIn, 60], [xNear, 64]]), { tone: side < 0 ? 0.0 : 0.55, hatch: H(90, 2) }));
  }
  L.push(INK(S.union(range(4, i => S.seg(14 + i * 2, 60 - i * 6, 50 - i * 2, 60 - i * 6, 0.8)))));
  return L;
}

function watermark() {
  const L = [];
  // hills behind
  L.push(INK(S.stroke(S.path([[0, 128], ['c', [60, 96], [120, 110], [170, 120]], ['c', [220, 100], [280, 90], [330, 112]]], 18), 1.4)));
  // lower tier: three great arches, piers in shade on their right faces
  const low = arcade(-30, 252, 352, 420, 3, 34, 300);
  L.push(WF(low.wall, { shade: { ...WS, w: 9 } }));
  L.push(masonry(S.offset(low.wall, -2), -30, 252, 352, 420, 14, 30, 1));
  for (const h of low.holes) L.push(F(S.sub(h, S.move(h, 9, 0)), 0.85, H(90, 3)));
  // upper tier: six arches
  const up = arcade(-30, 150, 352, 252, 6, 18, 186);
  L.push(WF(up.wall, { shade: { ...WS, w: 7 } }));
  L.push(masonry(S.offset(up.wall, -2), -30, 150, 352, 252, 12, 24, 1));
  for (const h of up.holes) L.push(F(S.sub(h, S.move(h, 6, 0)), 0.85, H(90, 3)));
  // string courses and the water channel with its coping
  L.push(WF(S.box(-30, 244, 352, 254), { tone: 0.3, hatch: H(0, 3), shade: null }));
  L.push(WF(S.box(-30, 126, 352, 150), { shade: { ...WS, dir: [0, 1], w: 6 } }));
  L.push(WF(S.box(-34, 118, 356, 128), { tone: 0.2, hatch: H(0, 3), shade: null }));
  L.push(INK(S.union(range(14, i => S.seg(-30 + i * 28, 128, -30 + i * 28, 150, 1)))));
  return L;
}

module.exports = {
  id: 'dc',
  emblem,
  button: () => place(emblem(), 0.78, 116, -3),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 64, ox: 0, oy: 0 } }),
  watermark,
};
