'use strict';
// Cyber Defence. Emblem: a round shield with a sword behind. Watermark: a watchtower with a beacon fire.
const { S, T, place, F, INK, H, WF, BF, WS, masonry, range } = require('./kit');

function emblem() {
  const blade = S.rot(S.poly([[30, -4], [34, -4], [34, 50], [32, 56], [30, 50]]), 40, 32, 32);
  const guard = S.rot(S.box(22, 46, 42, 50, 1), 40, 32, 32);
  const grip = S.rot(S.box(30, 50, 34, 62), 40, 32, 32);
  const pommel = S.rot(S.circle(32, 64, 3), 40, 32, 32);
  const cx = 30, cy = 32, r = 22;
  return [
    BF(blade, { shade: { dir: [0.7, 0.72], w: 1.5, k: 0.9, p: 2 } }), BF(guard), BF(grip, { tone: 0.6, hatch: H(0, 2) }), BF(pommel),
    BF(S.circle(cx, cy, r), { tone: 0.55, hatch: { ring: [cx, cy], p: 2 }, shade: null }),
    BF(S.circle(cx, cy, r - 4), { tone: T.sphere(cx, cy, r - 4, 0, 0.7), hatch: H(-45, 2), shade: null }),
    BF(S.circle(cx, cy, 6), { tone: T.sphere(cx, cy, 6, 0, 1), hatch: { ring: [cx - 2, cy - 2], p: 2 }, shade: null }),
    INK(S.union(range(8, i => { const a = i * Math.PI / 4; return S.circle(cx + Math.cos(a) * (r - 2), cy + Math.sin(a) * (r - 2), 1.2); }))),
  ];
}

function flame(x, y, h, w, lean, seed) {
  return S.poly(S.path([[x - w, y], ['c', [x - w * 1.2, y - h * 0.4], [x - w * 0.2 + lean * 0.4, y - h * 0.55], [x + lean, y - h]], ['c', [x + w * 0.3 + lean * 0.5, y - h * 0.6], [x + w * 1.3, y - h * 0.4], [x + w, y]]], 10));
}

function watermark() {
  const L = [];
  // the hill
  L.push(WF(S.poly(S.path([[-10, 330], ['c', [60, 300], [140, 296], [200, 300]], ['c', [260, 304], [300, 320], [340, 330]], [340, 420], [-10, 420]], 16)), { tone: T.lin(0, 300, 0, 420, 0.05, 0.5), hatch: H(-8, 3.4), shade: null }));
  // the tower: square, battered base, crenellated top
  const tw = S.poly([[126, 156], [266, 156], [276, 340], [116, 340]]);
  L.push(WF(tw, { tone: T.lin(126, 0, 276, 0, 0, 0.7), hatch: H(90, 3.4), shade: null }));
  L.push(masonry(S.offset(tw, -2), 110, 156, 280, 340, 15, 30, 1));
  L.push(WF(S.box(116, 142, 276, 160), { tone: 0.2, hatch: H(0, 3), shade: null }));
  for (let i = 0; i < 6; i++) L.push(WF(S.box(116 + i * 29, 116, 134 + i * 29, 144), { tone: i > 3 ? 0.5 : 0, hatch: H(90, 3) }));
  L.push(F(S.union(S.box(186, 206, 198, 236), S.circle(192, 206, 6)), 1));
  L.push(F(S.union(S.box(184, 284, 208, 340), S.circle(196, 284, 12)), 1));
  // the brazier on its pole and the beacon fire
  L.push(WF(S.box(190, 80, 202, 118), { tone: T.cyl(190, 202, 0, 0.9), hatch: H(90, 2), shade: null }));
  L.push(WF(S.poly([[158, 64], [234, 64], [222, 84], [170, 84]]), { tone: 0.35, hatch: H(90, 3) }));
  L.push(INK(S.union(range(7, i => S.seg(166 + i * 10, 66, 170 + i * 8.5, 82, 1.2)))));
  const fl = [flame(196, 68, 62, 24, -10, 1), flame(176, 68, 40, 13, -16, 2), flame(218, 68, 46, 13, 6, 3), flame(196, 68, 34, 10, -2, 4)];
  // smoke streaming away to the left over the band: tapering wisps
  for (const [y, k] of [[4, 0], [18, 1], [30, 2]]) L.push(WF(S.stroke(S.path([[180, y + 6], ['c', [140, y - 6 + k * 6], [100, y + 22], [60, y + 8]], ['c', [30, y - 2], [10, y + 12], [-20, y + 4]]], 20), 9 - k * 2, false, 2), { line: 1.4, shade: null }));
  for (const f of fl.slice(0, 3)) L.push(WF(f, { shade: { ...WS, dir: [0.9, 0.3], w: 6 } }));
  L.push(F(fl[3], 0.55, H(90, 3), { line: 1.4 }));
  // a distant answering beacon on the far hill
  L.push(WF(S.box(36, 286, 50, 316), { tone: 0.4, hatch: H(90, 2) }));
  L.push(WF(flame(43, 286, 18, 7, -2, 5), { line: 1.2, shade: null }));
  return L;
}

module.exports = {
  id: 'defence',
  emblem,
  button: () => place(emblem(), 0.8, 116, -5),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 60, ox: -1 * (size - 2) / 60, oy: 0 } }),
  watermark,
};
