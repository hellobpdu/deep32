'use strict';
// Policy & Compliance. Emblem: a bound regulation volume with ribbon and seal.
// Watermark: a clerk's desk with a stamped dossier.
const { S, T, place, F, INK, H, WF, BF, WS, lettering, range } = require('./kit');

function emblem() {
  return [
    BF(S.poly([[10, 12], [48, 6], [56, 12], [18, 18]]), { tone: 0.2, hatch: H(0, 2), shade: null }),
    BF(S.poly([[48, 6], [56, 12], [56, 58], [48, 52]]), { tone: 0.0, shade: null }),
    INK(S.union(range(5, i => S.seg(49 + i * 1.6, 8 + i * 1.2, 49 + i * 1.6, 53 + i * 1.2, 0.7)))),
    BF(S.poly([[10, 12], [18, 18], [18, 64], [10, 58]]), { tone: 0.6, hatch: H(90, 2) }),
    BF(S.box(18, 18, 56, 64)),
    INK(S.union(S.box(21, 21, 53, 22.2), S.box(21, 59.8, 53, 61))),
    BF(S.circle(37, 40, 9), { tone: T.sphere(37, 40, 9, 0.2, 1), hatch: { ring: [34, 37], p: 2 }, shade: null }),
    { shape: S.ngon(37, 40, 5, 6, -90, 2.4), kind: 'paper' },
    BF(S.poly([[44, 64], [48, 64], [49, 72], [46, 69], [43, 72]]), { tone: 0.6, hatch: H(90, 2), shade: null }),
  ];
}

function watermark() {
  const L = [];
  // pigeonholes at the back, stuffed with rolled papers
  L.push(WF(S.box(110, 20, 340, 160), { tone: 0.15, hatch: H(0, 3), shade: null }));
  for (let r = 0; r < 3; r++) for (let c = 0; c < 5; c++) {
    const x = 118 + c * 46, y = 28 + r * 44;
    L.push(F(S.box(x, y, x + 40, y + 38), 1, null, { line: 1.4 }));
    if ((r + c) % 2 === 0) L.push(WF(S.ellipse(x + 20, y + 30, 18, 8), { line: 1.2, shade: { ...WS, w: 4 } }));
    else L.push(WF(S.box(x + 4, y + 10, x + 36, y + 38), { line: 1.2, shade: null }));
  }
  // the desk top, a broad plane in perspective, its front edge and a drawer
  const top = S.poly([[-10, 210], [340, 180], [340, 300], [-10, 336]]);
  L.push(WF(top, { shade: null }));
  L.push(INK(S.inter(S.union(range(9, i => S.seg(-10, 230 + i * 13, 340, 200 + i * 13, 0.9))), S.offset(top, -3))));
  L.push(WF(S.poly([[-10, 336], [340, 300], [340, 314], [-10, 352]]), { tone: 0.7, hatch: H(0, 3) }));
  L.push(WF(S.poly([[-10, 352], [340, 314], [340, 420], [-10, 420]]), { tone: 0.15, hatch: H(90, 3.4), shade: null }));
  L.push(WF(S.poly([[40, 366], [200, 350], [200, 400], [40, 418]]), { shade: { ...WS, w: 6 } }));
  L.push(WF(S.ellipse(120, 382, 12, 6), { shade: null }));
  // the dossier: a thick folder, tied, with a big round stamp and a corner stamp
  const dos = S.poly([[36, 236], [200, 214], [236, 286], [62, 314]]);
  L.push(F(S.move(dos, 6, 8), 0.9, H(-20, 3)));
  L.push(WF(S.poly([[62, 314], [236, 286], [236, 294], [62, 322]]), { tone: 0.6, hatch: H(0, 2) }));
  L.push(WF(dos, { shade: { ...WS, w: 6 } }));
  L.push(INK(S.inter(lettering(70, 232, 170, 246, 1, 1.4, 4), S.offset(dos, -6))));
  const st = { d: (x, y) => { const e = S.ellipse(150, 268, 34, 22); return Math.abs(e.d(x, y)) - 2.2; }, bb: [110, 240, 190, 296] };
  L.push(INK(st));
  L.push(INK({ d: (x, y) => Math.abs(S.ellipse(150, 268, 26, 15).d(x, y)) - 1, bb: [110, 240, 190, 296] }));
  L.push(INK(lettering(132, 262, 170, 274, 1, 2, 9)));
  L.push(INK(S.union(S.seg(118, 236, 128, 300, 1.6), S.seg(132, 236, 140, 298, 1.6))));
  // the rubber stamp standing on the desk
  L.push(F(S.ellipse(266, 270, 30, 10), 0.9, H(0, 2)));
  L.push(WF(S.box(242, 238, 286, 262, 2), { tone: T.cyl(242, 286, 0, 0.8), hatch: H(90, 3), shade: null }));
  L.push(WF(S.box(258, 196, 270, 240), { tone: T.cyl(258, 270, 0, 0.9), hatch: H(90, 2), shade: null }));
  L.push(WF(S.circle(264, 188, 14), { tone: T.sphere(264, 188, 14, 0, 0.9), hatch: { ring: [258, 182], p: 3 }, shade: null }));
  // inkwell and quill
  L.push(WF(S.box(276, 214, 316, 250, 6), { tone: T.cyl(276, 316, 0, 0.8), hatch: H(90, 3), shade: null }));
  L.push(WF(S.ellipse(296, 214, 20, 6), { tone: 0.2, hatch: H(0, 2) }));
  L.push(WF(S.poly(S.path([[300, 214], ['c', [312, 160], [330, 110], [336, 70]], ['c', [324, 110], [316, 150], [304, 214]]], 12)), { tone: 0.2, hatch: H(60, 2.5), line: 1.4 }));
  return L;
}

module.exports = {
  id: 'policy',
  emblem,
  button: () => place(emblem(), 0.72, 122, -4),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 66, ox: -4 * (size - 2) / 66, oy: 0 } }),
  watermark,
};
