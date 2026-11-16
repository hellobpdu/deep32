'use strict';
// Wireless & Mobile. Emblem: a lattice mast throwing concentric waves. Watermark: a wireless telegraph cabin with an aerial.
const { S, T, place, F, INK, PAPER, LINE, H, WF, BF, WS, lattice, range, lerp } = require('./kit');

function emblem() {
  const top = [32, 10];
  const A = range(7, i => lerp([22, 64], top, i / 6)), B = range(7, i => lerp([42, 64], top, i / 6));
  const L = [];
  for (const r of [9, 16, 23]) {
    L.push(INK(S.arc(top[0], top[1], r, -48, 48, 2.2)));
    L.push(INK(S.arc(top[0], top[1], r, 132, 228, 2.2)));
  }
  L.push(BF(lattice(A, B, 2.4, 1.4), { shade: null }));
  L.push(BF(S.circle(top[0], top[1] - 2, 3.2)));
  return L;
}

function watermark() {
  const L = [];
  // ground line and grass
  L.push(INK(S.union(S.seg(0, 372, 323, 368, 1.4), range(30, i => S.seg(4 + i * 11, 372, 8 + i * 11, 364 - (i % 3) * 3, 1.2)))));
  // the mast in the band: a tall lattice with guys
  const mx = 62, top = 18;
  const A = range(15, i => lerp([mx - 26, 380], [mx - 4, top], i / 14)), B = range(15, i => lerp([mx + 26, 380], [mx + 4, top], i / 14));
  L.push(...LINE(S.union(S.seg(mx, top + 6, -10, 240, 1.4), S.seg(mx, top + 6, 160, 372, 1.4), S.seg(mx, 150, -10, 330, 1.4), S.seg(mx, 150, 130, 372, 1.4))));
  L.push(PAPER(lattice(A, B, 4.5, 2.6)));
  L.push(PAPER(S.box(mx - 9, top - 8, mx + 9, top + 2)));
  L.push(INK(S.box(mx - 5, top - 5, mx + 5, top - 1)));
  // the cabin: plank front, side in shade, roof
  const fx0 = 136, fx1 = 262, fy0 = 262, fy1 = 382, sx1 = 318, rise = 22;
  L.push(WF(S.poly([[fx1, fy0], [sx1, fy0 - rise + 6], [sx1, fy1 - 12], [fx1, fy1]]), { tone: 0.62, hatch: H(90, 3), shade: null }));
  L.push(WF(S.box(fx0, fy0, fx1, fy1), { shade: { ...WS, w: 10 } }));
  L.push(INK(S.inter(S.union(range(12, i => S.seg(fx0 + 10 * (i + 1), fy0, fx0 + 10 * (i + 1), fy1, 1))), S.box(fx0, fy0, fx1, fy1))));
  // gable front and roof slope
  L.push(WF(S.poly([[fx0 - 10, fy0 + 2], [(fx0 + fx1) / 2, fy0 - 52], [fx1 + 10, fy0 + 2]]), { shade: { ...WS, w: 6 } }));
  L.push(WF(S.poly([[(fx0 + fx1) / 2, fy0 - 52], [sx1 + 14, fy0 - rise - 44], [sx1 + 22, fy0 - rise + 8], [fx1 + 10, fy0 + 2]]), { tone: 0.45, hatch: H(-18, 3) }));
  L.push(INK(S.inter(S.union(range(6, i => S.seg(fx0 - 10, fy0 - 2 - i * 9, fx1 + 10, fy0 - 2 - i * 9, 1))), S.poly([[fx0 - 6, fy0], [(fx0 + fx1) / 2, fy0 - 48], [fx1 + 6, fy0]]))));
  // door and window
  L.push(WF(S.box(150, 300, 184, 382), { tone: T.lin(150, 0, 184, 0, 0.1, 0.6), hatch: H(90, 3), shade: null }));
  L.push(INK(S.circle(178, 342, 2.4)));
  L.push(F(S.box(204, 292, 248, 330), 1));
  L.push({ shape: S.union(S.box(205, 293, 225, 310), S.box(228, 293, 247, 310), S.box(205, 313, 225, 329), S.box(228, 313, 247, 329)), kind: 'fill', tone: 0.25, hatch: H(45, 3), line: 0 });
  // stove pipe
  L.push(WF(S.box(280, 196, 290, 246), { tone: T.cyl(280, 290, 0, 0.9), hatch: H(90, 2), shade: null }));
  L.push(WF(S.box(276, 190, 294, 198), { shade: null }));
  // the aerial: wire from the mast head to a pole on the roof, insulators, the lead-in down to the cabin
  const pole = [300, 120];
  L.push(WF(S.box(pole[0] - 3, pole[1], pole[0] + 3, fy0 - rise - 30), { shade: null, line: 1.4 }));
  for (const dy of [0, 8]) L.push(INK(S.stroke(S.path([[mx + 6, top + 4 + dy], ['c', [150, top + 40 + dy], [240, pole[1] - 30 + dy], [pole[0] - 10, pole[1] + 4 + dy]]], 18), 1.4)));
  for (const [x, y] of [[mx + 16, top + 9], [pole[0] - 16, pole[1] + 2], [mx + 16, top + 17], [pole[0] - 16, pole[1] + 10]]) L.push(WF(S.ellipse(x, y, 5, 2.6), { shade: null, line: 1.2 }));
  L.push(INK(S.stroke([[230, 92], [226, 170], [222, 214]], 1.4)));
  L.push(WF(S.ellipse(222, 218, 3, 5), { shade: null, line: 1.2 }));
  return L;
}

module.exports = {
  id: 'wireless',
  emblem,
  button: () => place(emblem(), 0.8, 112, -3),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 64, ox: 0, oy: 0 } }),
  watermark,
};
