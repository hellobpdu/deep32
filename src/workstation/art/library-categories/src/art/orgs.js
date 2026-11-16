'use strict';
// Standards & Organisations. Emblem: a charter with ribbon and wax seal.
// Watermark: a standard yard measure set into a wall.
const { S, T, place, F, INK, H, WF, BF, WS, lettering, range, rough, subdiv } = require('./kit');

function emblem() {
  return [
    BF(S.poly([[10, 8], [50, 8], [50, 52], [10, 52]]), { shade: null }),
    BF(S.ellipse(30, 7, 23, 4), { tone: T.lin(0, 3, 0, 11, 0, 0.8), hatch: H(0, 2), shade: null }),
    BF(S.ellipse(30, 53, 23, 4), { tone: T.lin(0, 49, 0, 57, 0.2, 0.9), hatch: H(0, 2), shade: null }),
    INK(lettering(14, 14, 46, 40, 7, 1.0, 5)),
    BF(S.poly([[30, 52], [24, 66], [28, 64], [30, 68], [32, 63], [34, 52]]), { tone: 0.6, hatch: H(80, 2), shade: null }),
    BF(S.poly([[34, 52], [42, 64], [38, 64], [37, 68], [33, 60]]), { tone: 0.4, hatch: H(80, 2), shade: null }),
    BF(S.circle(34, 54, 8), { tone: T.sphere(34, 54, 8, 0.3, 1), hatch: { ring: [31, 51], p: 2 }, shade: null }),
    { shape: S.ngon(34, 54, 4.2, 5, -90, 1.8), kind: 'paper' },
  ];
}

function watermark() {
  const L = [];
  // a stretch of wall with a stepped, broken top-left edge (the band stays open above it): joints, then big dressed
  // stones with rough arrises, a drafted margin and a lightly pecked centre
  const rows = [[64, 132, 40, 150], [132, 250, 0, 60], [250, 318, 60, 10], [318, 400, 20, -60], [400, 470, 0, -60]];
  const stones = []; let seed = 5;
  for (const [y0, y1, off, xmin] of rows) for (let x = -60 + off; x < 340; x += 92) {
    if (x + 46 < xmin) continue;
    stones.push(S.poly(rough(subdiv([[x + 1.5, y0 + 1.5], [x + 90.5, y0 + 1.5], [x + 90.5, y1 - 1.5], [x + 1.5, y1 - 1.5]], 4), 1.3, seed += 7)));
  }
  L.push(F(S.offset(S.union(stones), 2.5), 1));
  for (const st of stones) {
    L.push(WF(st, { tone: (xx, yy) => (st.d(xx, yy) < -10 ? 0.1 : 0), hatch: H(-50, 5), line: 1.6, shade: { ...WS, w: 7 } }));
    L.push(INK({ d: (xx, yy) => Math.abs(st.d(xx, yy) + 7) - 0.5, bb: st.bb }));
  }
  // the inscribed plaque above the measure
  L.push(WF(S.box(96, 150, 300, 196, 3), { shade: { ...WS, w: 5 } }));
  L.push(INK(lettering(110, 158, 286, 188, 3, 1.6, 11)));
  // the bronze bar set into a recess: end studs, foot divisions, inch ticks
  L.push(F(S.box(28, 212, 312, 240), 1));
  L.push(WF(S.box(34, 220, 306, 232), { tone: T.lin(0, 220, 0, 232, 0, 0.8), hatch: H(0, 2.5), shade: null }));
  for (const x of [46, 294]) { L.push(WF(S.box(x - 7, 204, x + 7, 248, 2), { shade: { ...WS, w: 4 } })); L.push(WF(S.circle(x, 226, 4.5), { shade: null })); }
  const ticks = [];
  for (let i = 0; i <= 36; i++) { const x = 46 + i * (248 / 36), h = i % 12 === 0 ? 14 : i % 3 === 0 ? 9 : 5; ticks.push(S.seg(x, 232 - h, x, 232, 1.2)); }
  L.push(INK(S.inter(S.union(ticks), S.box(34, 214, 306, 232))));
  // the foot and the shorter measures below
  L.push(F(S.box(60, 268, 200, 290), 1));
  L.push(WF(S.box(66, 274, 194, 284), { tone: T.lin(0, 274, 0, 284, 0, 0.8), hatch: H(0, 2.5), shade: null }));
  for (const x of [72, 188]) L.push(WF(S.box(x - 5, 262, x + 5, 296, 2), { shade: null }));
  L.push(INK(S.inter(S.union(range(13, i => S.seg(72 + i * (116 / 12), 276, 72 + i * (116 / 12), 284, 1.2))), S.box(66, 274, 194, 284))));
  return L;
}

module.exports = {
  id: 'orgs',
  emblem,
  button: () => place(emblem(), 0.78, 120, -3),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 64, ox: 0, oy: 0 } }),
  watermark,
};
