'use strict';
// Identity & Access. Emblem: a key crossed with a signet ring. Watermark: a castle gatehouse with portcullis.
const { S, T, place, F, INK, H, WF, BF, WS, masonry, range } = require('./kit');

// a warded key lying along +x: trefoil bow at the left, bit at the right; rotated about (cx, cy)
function key(rot, cx, cy) {
  const bow = S.sub(S.union(S.circle(10, 32, 7), S.circle(5, 26, 4.5), S.circle(5, 38, 4.5)), S.circle(10, 32, 3.4));
  const k = S.union(bow, S.box(16, 30.2, 60, 33.8), S.box(17, 29, 20, 35), S.box(48, 33.5, 52, 42), S.box(55, 33.5, 59, 40), S.box(48, 39, 59, 42));
  return S.rot(k, rot, cx, cy);
}

function emblem() {
  // the signet ring stands upright behind; the key crosses it diagonally
  const ring = S.sub(S.ellipse(34, 38, 15, 17), S.ellipse(34, 39, 10, 12));
  return [
    BF(ring, { tone: T.lin(20, 0, 50, 0, 0, 0.75), hatch: H(90, 2), shade: null }),
    BF(S.box(25, 14, 43, 25, 3), { tone: T.lin(25, 0, 43, 0, 0, 0.8), hatch: H(90, 2), shade: null }),
    BF(S.ellipse(34, 17.5, 7, 3.6), { shade: null }),
    INK(S.union(S.seg(30, 17.5, 38, 17.5, 1), S.seg(34, 15, 34, 20, 1))),
    BF(key(-35, 32, 36), { shade: { dir: [0.7, 0.72], w: 1.6, k: 0.9, p: 2 } }),
  ];
}

function watermark() {
  const L = [];
  // the curtain wall and gate passage between two round towers
  const wall = S.box(78, 120, 272, 420);
  L.push(WF(wall, { shade: null }));
  L.push(masonry(S.offset(wall, -2), 78, 120, 272, 420, 13, 26, 1));
  // crenellations on the wall walk
  for (let i = 0; i < 7; i++) L.push(WF(S.box(80 + i * 28, 100, 98 + i * 28, 122), { shade: { ...WS, w: 4 } }));
  // the gate arch, dark, with the portcullis half raised
  const arch = S.union(S.box(132, 250, 218, 420), S.circle(175, 250, 43));
  L.push(WF(S.offset(arch, 10), { tone: 0.25, hatch: H(-35, 3), shade: null }));
  L.push(F(arch, 1));
  const bars = [];
  for (let i = 0; i < 7; i++) bars.push(S.box(138 + i * 12.5, 214, 143 + i * 12.5, 336));
  for (let j = 0; j < 7; j++) bars.push(S.box(134, 222 + j * 17, 216, 226 + j * 17));
  const grille = S.inter(S.union(bars), S.offset(arch, -2));
  L.push(WF(grille, { line: 1, shade: null }));
  for (let i = 0; i < 7; i++) L.push(WF(S.poly([[138 + i * 12.5, 336], [143 + i * 12.5, 336], [140.5 + i * 12.5, 350]]), { line: 1, shade: null }));
  // towers: cylinders with conical caps' bases (crenellated), arrow slits
  for (const [cx, r] of [[62, 64], [290, 60]]) {
    const body = S.box(cx - r, 80, cx + r, 420);
    L.push(WF(body, { tone: T.cyl(cx - r, cx + r, 0, 0.75, 0.32), hatch: H(90, 3.2), shade: null }));
    L.push(INK(S.inter(S.union(range(26, j => S.seg(cx - r, 92 + j * 13, cx + r, 92 + j * 13 + 2, 1))), S.offset(body, -2))));
    for (let i = 0; i < 5; i++) { const x = cx - r + 6 + i * (2 * r - 12) / 4.5; L.push(WF(S.box(x, 52, x + 16, 82), { tone: T.cyl(cx - r, cx + r, 0, 0.6, 0.32)(x + 8), hatch: H(90, 3) })); }
    L.push(WF(S.box(cx - r - 6, 78, cx + r + 6, 92), { tone: 0.15, hatch: H(0, 3), shade: null }));
    for (const y of [150, 250]) L.push(F(S.box(cx - 3, y, cx + 3, y + 30), 1));
  }
  // banners of the gate: a carved shield over the arch
  L.push(WF(S.poly(S.path([[160, 150], [190, 150], [190, 168], ['c', [190, 184], [176, 190], [175, 194]], ['c', [174, 190], [160, 184], [160, 168]]], 8)), { shade: { ...WS, w: 4 } }));
  L.push(INK(S.union(S.seg(175, 152, 175, 190, 1.4), S.seg(161, 166, 189, 166, 1.4))));
  return L;
}

module.exports = {
  id: 'identity',
  emblem,
  button: () => place(emblem(), 0.82, 112, -6),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 58, ox: -4 * (size - 2) / 58, oy: -2 * (size - 2) / 58 } }),
  watermark,
};
