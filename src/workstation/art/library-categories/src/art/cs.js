'use strict';
// Computer Science. Emblem: an abacus with a pair of compasses. Watermark: Babbage-style gear columns.
const { S, T, place, F, INK, H, WF, BF, WS, range } = require('./kit');

function emblem() {
  const L = [];
  L.push(BF(S.sub(S.box(2, 8, 50, 52, 1), S.box(6, 12, 46, 48)), { tone: 0.55, hatch: H(0, 2), shade: null }));
  for (let r = 0; r < 5; r++) {
    const y = 16 + r * 8;
    L.push(INK(S.seg(6, y, 46, y, 1)));
    const n = [3, 5, 2, 4, 1][r];
    for (let b = 0; b < n; b++) L.push(BF(S.ellipse(10 + b * 5.4, y, 2.7, 3.3), { shade: null }));
    for (let b = 0; b < 6 - n; b++) L.push(BF(S.ellipse(42 - b * 5.4, y, 2.7, 3.3), { shade: null }));
  }
  // compasses in front: hinge at the top, two legs splayed
  L.push(BF(S.seg(46, 6, 30, 64, 3.2, 1.6), { shade: null }));
  L.push(BF(S.seg(46, 6, 62, 62, 3.2, 1.6), { shade: null }));
  L.push(BF(S.circle(46, 7, 4), { tone: 0.6, hatch: { ring: [46, 7], p: 2 } }));
  L.push(INK(S.seg(46, 0, 46, 3, 1.6)));
  return L;
}

// one column of figure wheels: short cylinders stacked on a shaft
function column(cx, y0, n, r, h, gap, L, seed) {
  L.push(WF(S.box(cx - 5, y0 - 30, cx + 5, y0 + n * (h + gap) + 30), { tone: T.cyl(cx - 5, cx + 5, 0, 0.9), hatch: H(90, 2), shade: null }));
  for (let i = 0; i < n; i++) {
    const y = y0 + i * (h + gap);
    const body = S.union(S.box(cx - r, y, cx + r, y + h), S.ellipse(cx, y + h, r, r * 0.22));
    L.push(WF(body, { tone: T.cyl(cx - r, cx + r, 0, 0.85, 0.3), hatch: H(90, 3), shade: null }));
    L.push(WF(S.ellipse(cx, y, r, r * 0.22), { tone: 0.05, hatch: H(0, 2.5), shade: null, line: 1.6 }));
    // engraved figures round the rim: short vertical marks at ten places, the front ones legible as numerals
    const marks = [];
    for (let k = 0; k < 10; k++) { const a = (k + (seed + i) * 3) / 10 * 2 * Math.PI; const x = cx + Math.sin(a) * (r - 3); if (Math.cos(a) > 0.1) marks.push(S.seg(x, y + h * 0.3, x, y + h * 0.75, 1.6)); }
    L.push(INK(S.union(marks)));
    // the carry teeth on the lower rim
    L.push(INK(S.inter(S.union(range(16, k => S.seg(cx - r + k * (2 * r / 15), y + h, cx - r + k * (2 * r / 15), y + h + 4, 1.2))), S.box(cx - r, y + h - 1, cx + r, y + h + 6))));
  }
}

function watermark() {
  const L = [];
  // the top and bottom plates spanning the frame
  for (const [y, t] of [[20, 0.2], [372, 0.5]]) {
    L.push(WF(S.box(-10, y, 340, y + 20), { tone: t, hatch: H(0, 3), shade: null }));
    L.push(INK(S.union(range(9, i => S.circle(10 + i * 40, y + 10, 3)))));
  }
  // four columns of figure wheels, the nearest largest
  column(64, 70, 6, 34, 24, 26, L, 1);
  column(160, 70, 6, 34, 24, 26, L, 4);
  column(256, 70, 6, 34, 24, 26, L, 7);
  // sector wheels between the columns
  for (let i = 0; i < 6; i++) for (const cx of [112, 208]) {
    const y = 70 + i * 50 + 12;
    L.push(WF(S.inter(S.gear(cx, y, 9, 10, 3), S.box(cx - 14, y - 4, cx + 14, y + 4)), { line: 1.2, shade: null }));
  }
  // the crank handle rising from the top plate
  L.push(WF(S.box(156, -10, 164, 22), { shade: null }));
  return L;
}

module.exports = {
  id: 'cs',
  emblem,
  button: () => place(emblem(), 0.78, 116, -3),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 64, ox: 0, oy: 0 } }),
  watermark,
};
