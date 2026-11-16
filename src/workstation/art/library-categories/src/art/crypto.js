'use strict';
// Cryptography. Emblem: a cipher disc of two rings. Watermark: a rotor cipher machine with its lid open.
const { S, T, place, F, INK, H, WF, BF, WS, range, lettering } = require('./kit');

function emblem() {
  const cx = 32, cy = 32;
  const marks = (r0, r1, n, rot, w) => S.union(range(n, i => { const a = rot + i * 2 * Math.PI / n; return S.seg(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0, cx + Math.cos(a) * r1, cy + Math.sin(a) * r1, w); }));
  return [
    BF(S.circle(cx, cy, 30), { shade: { dir: [0.7, 0.72], w: 3, k: 0.9, p: 2 } }),
    INK(S.inter(marks(22, 27, 26, 0, 1.6), S.circle(cx, cy, 29))),
    BF(S.circle(cx, cy, 20), { tone: 0.0, shade: { dir: [-0.7, -0.72], w: 2.5, k: 0.9, p: 2 } }),
    INK(marks(12, 17, 26, 0.12, 1.4)),
    BF(S.circle(cx, cy, 8), { tone: T.sphere(cx, cy, 8, 0.2, 1), hatch: { ring: [cx - 3, cy - 3], p: 2 }, shade: null }),
    INK(S.seg(cx, cy - 30, cx, cy - 18, 1.4)),
    BF(S.circle(cx, cy, 2.5)),
  ];
}

function watermark() {
  const L = [];
  const dx = 0.62, dy = 0.42; // oblique depth vector per unit
  // the case: front (keyboard) panel sloping, the top deck with lamps and rotors, the lid hinged open behind
  const fx0 = 40, fx1 = 330, fyTop = 272, fyBot = 412, dep = 150;
  const D = [dep * dx, dep * dy];
  // the lid, open and leaning back: wooden rim, a recessed inner panel with an instruction plate, brass hinges
  const lb0 = [fx0 + D[0], fyTop - D[1]], lb1 = [fx1 + D[0], fyTop - D[1]], lt1 = [fx1 + D[0] + 26, 34], lt0 = [fx0 + D[0] + 26, 34];
  L.push(WF(S.poly([lb0, lb1, lt1, lt0]), { tone: 0.55, hatch: H(80, 3) }));
  const inner = S.poly([[lb0[0] + 12, lb0[1] - 10], [lb1[0] - 12, lb1[1] - 10], [lt1[0] - 10, lt1[1] + 10], [lt0[0] + 10, lt0[1] + 10]]);
  L.push(WF(inner, { shade: { ...WS, dir: [-0.7, -0.7], w: 7 } }));
  L.push(WF(S.poly([[lb0[0] + 50, 70], [lb0[0] + 150, 70], [lb0[0] + 154, 110], [lb0[0] + 54, 110]]), { shade: null, line: 1.6 }));
  L.push(INK(lettering(lb0[0] + 60, 76, lb0[0] + 146, 104, 4, 1.2, 3)));
  for (const t of [0.15, 0.85]) { const x = lb0[0] + (lb1[0] - lb0[0]) * t; L.push(WF(S.box(x - 12, lb0[1] - 6, x + 12, lb0[1] + 4, 2), { tone: 0.3, hatch: H(90, 2), shade: null })); }
  // the top deck
  const deck = S.poly([[fx0, fyTop], [fx1, fyTop], [fx1 + D[0], fyTop - D[1]], [fx0 + D[0], fyTop - D[1]]]);
  L.push(WF(deck, { shade: null }));
  // rotors standing up through the deck's slot, at the back: three toothed wheels
  for (let i = 0; i < 3; i++) {
    const cx = 168 + i * 46, cy = fyTop - D[1] + 6;
    L.push(F(S.box(cx - 16, cy - 2, cx + 16, cy + 12), 1));
    L.push(WF(S.inter(S.gear(cx, cy - 18, 26, 26, 3.5, 0.45), S.halfY(cy + 2, false)), { tone: T.cyl(cx - 29, cx + 29, 0, 0.85, 0.35), hatch: H(90, 3), shade: null }));
    L.push(INK(S.inter(S.union(range(9, k => S.seg(cx - 26 + k * 6.5, cy - 44, cx - 26 + k * 6.5, cy + 2, 1))), S.inter(S.circle(cx, cy - 18, 24), S.halfY(cy + 2, false)))));
    L.push(WF(S.box(cx - 6, cy - 2, cx + 6, cy + 10), { shade: null, line: 1.4 }));
  }
  // lampboard: rows of round windows on the deck, foreshortened
  const lampRow = (k, n, off) => range(n, i => { const t = (i + 0.5 + off) / 10; const x = fx0 + 30 + (fx1 - fx0 - 60) * t + D[0] * k, y = fyTop - D[1] * k; return S.ellipse(x, y, 8, 5); });
  for (const [k, n, off] of [[0.25, 9, 0], [0.42, 8, 0.5], [0.59, 9, 0]]) for (const e of lampRow(k, n, off)) { L.push(WF(e, { line: 1.4, shade: null, tone: 0.0 })); L.push(INK(S.offset(e, -4.2))); }
  // the sloping keyboard face with three rows of round keys on stalks
  L.push(WF(S.box(fx0, fyTop, fx1, fyBot), { tone: 0.15, hatch: H(0, 3), shade: null }));
  const keys = (row, n, off) => range(n, i => [fx0 + 28 + i * ((fx1 - fx0 - 50) / 9) + off, fyTop + 34 + row * 34]);
  for (const [row, n, off] of [[0, 9, 0], [1, 8, 14], [2, 9, 0]]) for (const [x, y] of keys(row, n, off)) {
    L.push(F(S.ellipse(x + 4, y + 8, 12, 6), 1));
    L.push(WF(S.circle(x, y, 11), { shade: { ...WS, w: 4 } }));
    L.push(INK(S.seg(x - 3, y, x + 3, y, 1.6)));
  }
  // the case's right side in shade, dovetail joints
  L.push(WF(S.poly([[fx1, fyTop], [fx1 + D[0], fyTop - D[1]], [fx1 + D[0], fyBot], [fx1, fyBot]]), { tone: 0.8, hatch: H(90, 3) }));
  return L;
}

module.exports = {
  id: 'crypto',
  emblem,
  button: () => place(emblem(), 0.78, 120, -5),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 64, ox: 0, oy: 0 } }),
  watermark,
};
