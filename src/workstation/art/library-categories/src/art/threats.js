'use strict';
// Threats & Attacks. Emblem: a Trojan horse on wheels. Watermark: a siege tower at a city wall.
const { S, T, place, F, INK, H, WF, BF, WS, wheel, masonry, range } = require('./kit');

function horse(mk, sh) {
  const body = S.poly([[14, 22], [46, 20], [50, 26], [48, 36], [14, 38], [10, 30]]);
  const neck = S.poly([[38, 22], [46, 6], [53, 6], [52, 14], [48, 26]]);
  const head = S.poly([[45, 4], [60, 10], [61, 15], [55, 16], [50, 12]]);
  const ear = S.poly([[47, 5], [48, -1], [51, 5]]);
  const legs = S.union(S.box(15, 36, 19, 52), S.box(23, 36, 27, 52), S.box(37, 36, 41, 52), S.box(43, 35, 47, 52));
  const tail = S.stroke([[11, 24], [5, 30], [4, 40]], 3);
  return [
    mk(tail, { shade: null }), mk(legs, { tone: 0.5, hatch: H(90, 2), shade: null }), mk(S.union(body, neck), { shade: sh }), mk(head, {}), mk(ear, {}),
    INK(S.inter(S.union(range(4, i => S.seg(8, 25 + i * 4, 52, 24 + i * 4, 0.8))), S.offset(body, -1.4))),
    INK(S.circle(53, 10, 1.1)),
  ];
}

function emblem() {
  return [
    ...horse(BF, { dir: [0.7, 0.72], w: 2.5, k: 0.9, p: 2 }),
    BF(S.box(6, 51, 56, 56)),
    ...[13, 31, 49].flatMap(x => [BF(S.circle(x, 59, 6), { tone: 0.0 }), INK(S.union(S.seg(x - 5, 59, x + 5, 59, 1), S.seg(x, 54, x, 64, 1))), BF(S.circle(x, 59, 1.6))]),
  ];
}

function watermark() {
  const L = [];
  // the city wall on the right, crenellated, with a round tower
  const wall = S.box(196, 150, 340, 420);
  L.push(WF(wall, { tone: T.lin(196, 0, 340, 0, 0.05, 0.4), hatch: H(90, 3.4), shade: null }));
  L.push(masonry(S.offset(wall, -2), 196, 150, 340, 420, 14, 28, 1));
  for (let i = 0; i < 6; i++) L.push(WF(S.box(198 + i * 26, 128, 214 + i * 26, 152), { shade: { ...WS, w: 4 } }));
  // the siege tower: tapering timber frame on wheels, hide-covered, with a ramp falling onto the wall
  const tw = S.poly([[34, 420], [196, 420], [178, 92], [52, 92]]);
  L.push(WF(tw, { tone: T.lin(34, 0, 196, 0, 0.0, 0.55), hatch: H(90, 3), shade: { ...WS, w: 10 } }));
  // planks and storeys
  L.push(INK(S.inter(S.union(range(12, i => S.seg(30, 112 + i * 26, 200, 112 + i * 26, 1.8)), range(18, i => S.seg(44 + i * 9, 90, 40 + i * 9.6, 420, 0.9))), S.offset(tw, -2))));
  // the open ramp on chains, falling onto the wall top
  L.push(WF(S.poly([[176, 96], [182, 92], [246, 120], [242, 128]]), { tone: 0.3, hatch: H(20, 3) }));
  L.push(INK(S.union(S.seg(150, 70, 240, 120, 1.4), S.seg(150, 70, 178, 94, 1.4))));
  // the fighting top: a hoarding with shields
  L.push(WF(S.poly([[44, 92], [186, 92], [182, 56], [48, 56]]), { shade: { ...WS, w: 5 } }));
  for (let i = 0; i < 4; i++) L.push(WF(S.circle(66 + i * 34, 74, 13), { tone: T.sphere(66 + i * 34, 74, 13, 0, 0.9), hatch: { ring: [62 + i * 34, 70], p: 3 } }));
  // loopholes
  for (const y of [170, 250, 330]) for (const x of [80, 130]) L.push(F(S.box(x, y, x + 8, y + 18), 1));
  // great wheels
  L.push(...wheel(70, 396, 34, 8, 6, WF, { hub: 7, spokeW: 4 }));
  L.push(...wheel(166, 396, 34, 8, 6, WF, { hub: 7, spokeW: 4, rot: 0.3 }));
  return L;
}

module.exports = {
  id: 'threats',
  emblem,
  button: () => place(emblem(), 0.74, 118, -2),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 64, ox: 0, oy: 0 } }),
  watermark,
};
