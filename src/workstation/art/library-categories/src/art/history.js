'use strict';
// History of Computing. Emblem: an hourglass with a quill. Watermark: a difference engine on a table.
const { S, T, place, F, INK, H, WF, BF, WS, range } = require('./kit');

function emblem() {
  const glass = S.poly(S.path([[16, 10], [46, 10], ['c', [46, 24], [34, 30], [33, 32]], ['c', [34, 34], [46, 40], [46, 54]], [16, 54], ['c', [16, 40], [28, 34], [29, 32]], ['c', [28, 30], [16, 24], [16, 10]]], 10));
  const sandLow = S.inter(glass, S.poly([[10, 54], [52, 54], [44, 44], [31, 38], [18, 44]]));
  const sandTop = S.inter(glass, S.poly([[16, 18], [46, 18], [36, 30], [26, 30]]));
  return [
    // the quill behind, from lower left to upper right
    BF(S.poly(S.path([[6, 62], ['c', [20, 40], [40, 14], [62, 0]], ['c', [56, 16], [40, 34], [10, 62]]], 12)), { tone: 0.15, hatch: H(50, 2), shade: null }),
    INK(S.seg(7, 62, 54, 8, 1)),
    BF(glass, { shade: null }),
    F(sandTop, 0.6, H(0, 2), { line: 0 }), F(sandLow, 0.6, H(0, 2), { line: 0 }), INK(S.seg(31, 32, 31, 40, 1)),
    BF(S.box(10, 4, 52, 10, 1), { tone: 0.5, hatch: H(0, 2) }),
    BF(S.box(10, 54, 52, 60, 1), { tone: 0.5, hatch: H(0, 2) }),
    BF(S.box(11, 10, 14, 54), { tone: T.cyl(11, 14, 0, 0.9), hatch: H(90, 2), shade: null }),
    BF(S.box(48, 10, 51, 54), { tone: T.cyl(48, 51, 0, 0.9), hatch: H(90, 2), shade: null }),
  ];
}

function watermark() {
  const L = [];
  // the table: top in perspective, apron, turned legs
  const top = S.poly([[-10, 292], [300, 292], [340, 262], [30, 262]]);
  L.push(WF(top, { tone: 0.05, hatch: H(0, 3), shade: null }));
  L.push(WF(S.box(-10, 292, 300, 312), { tone: 0.35, hatch: H(0, 3), shade: null }));
  L.push(WF(S.poly([[300, 292], [340, 262], [340, 282], [300, 312]]), { tone: 0.8, hatch: H(90, 3) }));
  for (const x of [14, 270]) {
    const leg = S.union(S.box(x - 7, 312, x + 7, 420), S.ellipse(x, 330, 11, 7), S.ellipse(x, 372, 10, 6), S.ellipse(x, 404, 12, 5));
    L.push(WF(leg, { tone: T.cyl(x - 12, x + 12, 0, 0.85), hatch: H(90, 2.5), shade: null }));
  }
  // the engine: frame plates and three columns of figure wheels
  const bx0 = 70, bx1 = 260, by0 = 92, by1 = 270;
  L.push(WF(S.box(bx0 - 14, by1 - 12, bx1 + 14, by1 + 2), { tone: 0.4, hatch: H(0, 2.5), shade: null }));
  L.push(WF(S.box(bx0 - 14, by0 - 14, bx1 + 14, by0), { tone: 0.2, hatch: H(0, 2.5), shade: null }));
  for (const x of [bx0 - 10, bx1 + 4]) L.push(WF(S.box(x, by0, x + 6, by1 - 12), { tone: T.cyl(x, x + 6, 0, 0.9), hatch: H(90, 2), shade: null }));
  for (const cx of [104, 164, 224]) {
    L.push(WF(S.box(cx - 3, by0, cx + 3, by1 - 12), { tone: 0.5, hatch: H(90, 2), shade: null }));
    for (let i = 0; i < 6; i++) {
      const y = by0 + 8 + i * 27, r = 22, h = 13;
      L.push(WF(S.union(S.box(cx - r, y, cx + r, y + h), S.ellipse(cx, y + h, r, 5)), { tone: T.cyl(cx - r, cx + r, 0, 0.85, 0.3), hatch: H(90, 2.6), shade: null, line: 1.6 }));
      L.push(WF(S.ellipse(cx, y, r, 5), { line: 1.4, shade: null, tone: 0.05, hatch: H(0, 2) }));
      L.push(INK(S.union(range(4, k => { const x = cx - r + 6 + k * 10; return S.seg(x, y + 3, x, y + h - 2, 1.4); }))));
    }
  }
  // the crank on top, with its handle
  L.push(WF(S.box(160, 40, 168, by0 - 14), { shade: null }));
  L.push(WF(S.box(160, 40, 206, 48), { shade: null }));
  L.push(WF(S.box(198, 22, 206, 48, 2), { tone: 0.3, hatch: H(90, 2) }));
  // a ledger and a pen lying on the table in front
  L.push(WF(S.poly([[40, 286], [118, 274], [134, 286], [56, 298]]), { shade: null }));
  L.push(INK(S.inter(S.union(range(4, i => S.seg(50, 288 - i * 3, 124, 278 - i * 3, 0.9))), S.poly([[44, 286], [118, 276], [128, 285], [56, 296]]))));
  L.push(WF(S.seg(150, 290, 230, 274, 3), { shade: null, line: 1 }));
  return L;
}

module.exports = {
  id: 'history',
  emblem,
  button: () => place(emblem(), 0.76, 118, -4),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 64, ox: 0, oy: 0 } }),
  watermark,
};
