'use strict';
// Network Services. Emblem: a hotel desk bell. Watermark: a telephone exchange switchboard.
const { S, T, place, F, INK, H, WF, BF, WS, range } = require('./kit');

function emblem() {
  const dome = S.inter(S.ellipse(32, 44, 24, 26), S.halfY(44, false));
  return [
    BF(S.ellipse(32, 50, 30, 7), { tone: T.lin(0, 44, 0, 57, 0, 0.9), hatch: H(0, 2), shade: null }),
    BF(S.box(4, 44, 60, 50)),
    BF(dome, { tone: T.sphere(32, 40, 24, 0, 1), hatch: { ring: [22, 30], p: 2 }, shade: null }),
    { shape: S.rot(S.ellipse(22, 28, 3, 7), 35, 22, 28), kind: 'paper' },
    BF(S.box(29, 9, 35, 19)),
    BF(S.ellipse(32, 9, 7, 3)),
    INK(S.union(S.seg(14, 8, 9, 3, 1.4), S.seg(50, 8, 55, 3, 1.4), S.seg(11, 14, 4, 12, 1.4), S.seg(53, 14, 60, 12, 1.4))),
  ];
}

function watermark() {
  const L = [];
  const x0 = 104, x1 = 340, y0 = 36, y1 = 296;
  // the cabinet's left side, white against the band
  L.push(WF(S.poly([[x0, y0], [x0 - 34, y0 + 22], [x0 - 34, 412], [x0, 412]]), { shade: null }));
  L.push(INK(S.union(S.seg(x0 - 30, 60, x0 - 4, 44, 1.2), S.seg(x0 - 30, 300, x0 - 4, 300, 1.2), S.seg(x0 - 30, 330, x0 - 4, 330, 1.2))));
  // the board's face: carved cornice, the jack field, the lamp strips
  L.push(WF(S.box(x0, y0, x1, 412), { shade: null }));
  L.push(WF(S.box(x0 - 4, y0 - 14, x1 + 4, y0 + 2), { tone: 0.4, hatch: H(0, 3), shade: null }));
  L.push(F(S.box(x0 + 12, y0 + 16, x1, y1 - 30), 0.15, H(0, 3), { line: 1.6 }));
  const jacks = [];
  for (let r = 0; r < 12; r++) for (let c = 0; c < 15; c++) {
    const x = x0 + 24 + c * 15, y = y0 + 28 + r * 17;
    if (x > x1 - 6) continue;
    jacks.push(S.circle(x, y, 3.2));
  }
  L.push(F(S.union(jacks), 1));
  L.push(INK(S.union(range(12, r => S.seg(x0 + 16, y0 + 37 + r * 17, x1, y0 + 37 + r * 17, 1)))));
  // the keyshelf with rows of plugs and cords running up into the jacks
  L.push(F(S.poly([[x0 - 10, y1], [x1 + 10, y1], [x1 + 10, y1 + 26], [x0 - 18, y1 + 30]]), 0.0, null, { line: 2, shade: { ...WS, dir: [0, 1], w: 8 } }));
  L.push(WF(S.box(x0 - 18, y1 + 30, x1 + 10, y1 + 40), { tone: 0.7, hatch: H(0, 2.5), shade: null }));
  const cords = [[130, 232, 2, 3], [160, 172, 5, 8], [190, 290, 9, 1], [222, 140, 1, 6], [252, 318, 3, 11], [284, 196, 7, 2]];
  for (const [px, jx, jr, k] of cords) {
    const jy = y0 + 28 + jr * 17;
    L.push(WF(S.stroke(S.path([[px, y1 + 8], ['c', [px - 30 + k * 4, y1 - 60], [jx + 10, jy + 80], [jx, jy + 6]]], 18), 3.6), { line: 1.2, shade: null }));
    L.push(WF(S.box(jx - 3, jy + 4, jx + 3, jy + 16), { line: 1.2, shade: null }));
  }
  for (let i = 0; i < 8; i++) { const x = 124 + i * 28; L.push(WF(S.box(x - 3.5, y1 + 2, x + 3.5, y1 + 18, 2), { shade: null, line: 1.4 })); }
  // the cabinet's lower doors
  for (const [a, b] of [[x0 + 12, x0 + 112], [x0 + 124, x1 - 6]]) { L.push(WF(S.box(a, y1 + 54, b, 412), { shade: { ...WS, w: 6 } })); L.push(INK(S.circle(b - 10, y1 + 80, 2.5))); }
  // a headset hanging from a hook on the side
  L.push(INK(S.seg(x0 - 20, 138, x0 - 20, 150, 2)));
  L.push(WF(S.stroke(S.arcPts(x0 - 20, 176, 18, 22, 180, 360, 20), 3), { shade: null, line: 1 }));
  L.push(WF(S.ellipse(x0 - 38, 182, 7, 10), { shade: { ...WS, w: 4 } }));
  L.push(WF(S.stroke(S.path([[x0 - 2, 182], ['c', [x0 + 6, 210], [x0 - 40, 230], [x0 - 26, 262]]], 14), 2.4), { shade: null, line: 1 }));
  L.push(WF(S.ellipse(x0 - 26, 270, 9, 12), { shade: { ...WS, w: 4 } }));
  return L;
}

module.exports = {
  id: 'netsvc',
  emblem,
  button: () => place(emblem(), 0.8, 116, -4),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 64, ox: 0, oy: 0 } }),
  watermark,
};
