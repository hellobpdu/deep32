'use strict';
// IP Routing. Emblem: a crossroads signpost with three arms. Watermark: a mariner's compass rose over a chart.
const { S, T, place, F, INK, H, WF, BF, WS, lettering, range } = require('./kit');

function board(x0, x1, y0, y1, tipLeft, rotDeg, cx, cy) {
  const m = (y0 + y1) / 2, t = (y1 - y0) * 0.6;
  const pts = tipLeft ? [[x0, m], [x0 + t, y0], [x1, y0], [x1, y1], [x0 + t, y1]] : [[x0, y0], [x1 - t, y0], [x1, m], [x1 - t, y1], [x0, y1]];
  let b = S.poly(pts), txt = lettering(x0 + (tipLeft ? t + 1 : 2), m - 1.6, x1 - (tipLeft ? 2 : t + 1), m + 1.6, 1, 1.2, x0 | 0);
  if (rotDeg) { b = S.rot(b, rotDeg, cx, cy); txt = S.rot(txt, rotDeg, cx, cy); }
  return [BF(b), INK(S.inter(txt, S.offset(b, -1)))];
}

function emblem() {
  return [
    INK(S.union(range(7, i => S.seg(18 + i * 4, 64, 15 + i * 4 + (i % 3), 57 - (i % 2) * 3, 1.2)))),
    BF(S.box(27, 9, 34, 66), { tone: T.cyl(27, 34, 0, 0.9), hatch: H(90, 2), shade: null }),
    BF(S.poly([[25.5, 9.5], [30.5, 3], [35.5, 9.5]])),
    ...board(30, 62, 11, 21, false),
    ...board(1, 31, 23, 33, true),
    ...board(30, 60, 35, 44, false, 14, 31, 39),
  ];
}

function watermark() {
  const cx = 180, cy = 252, L = [];
  // the chart: a coast at the top right, land hatched, soundings rings offshore
  const land = S.poly(S.path([[96, -10], ['c', [140, 30], [150, 60], [196, 54]], ['c', [236, 48], [250, 96], [292, 104]], ['c', [312, 108], [322, 126], [340, 132]], [340, -10]], 14));
  for (const off of [7, 13, 20]) L.push(INK({ d: (x, y) => Math.abs(land.d(x, y) - off) - 0.7, bb: [80, -20, 340, 160] }));
  L.push(WF(land, { tone: 0.33, hatch: H(0, 3), shade: null }));
  // graticule
  L.push(INK(S.union([0, 1, 2, 3].map(i => S.seg(0, 40 + i * 110, 323, 40 + i * 110, 1)), [0, 1, 2].map(i => S.seg(60 + i * 120, 0, 60 + i * 120, 400, 1)))));
  // rhumb lines from the rose
  L.push(INK(S.union(range(32, i => { const a = i * Math.PI / 16; return S.seg(cx, cy, cx + Math.cos(a) * 420, cy + Math.sin(a) * 420, 1); }))));
  // the rose: outer ring with degree ticks
  L.push(WF(S.sub(S.circle(cx, cy, 120), S.circle(cx, cy, 103)), { shade: null }));
  L.push(INK(S.inter(S.union(range(72, i => { const a = i * Math.PI / 36, r0 = i % 2 ? 112 : 106; return S.seg(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0, cx + Math.cos(a) * 120, cy + Math.sin(a) * 120, 1.2); })), S.circle(cx, cy, 119))));
  L.push(WF(S.circle(cx, cy, 103), { shade: null, line: 1 }));
  // points: 16 minor, 8 intercardinal, 4 cardinal; each with one half dark
  const point = (a, r, w) => {
    const ca = Math.cos(a), sa = Math.sin(a), px = -sa, py = ca;
    const tip = [cx + ca * r, cy + sa * r], l = [cx + ca * w * 1.2 + px * w, cy + sa * w * 1.2 + py * w], rr = [cx + ca * w * 1.2 - px * w, cy + sa * w * 1.2 - py * w];
    return [WF(S.poly([[cx, cy], l, tip]), { shade: null }), F(S.poly([[cx, cy], tip, rr]), 1)];
  };
  for (let i = 0; i < 16; i++) L.push(...point((i + 0.5) * Math.PI / 8 - Math.PI / 2, 72, 9));
  for (let i = 0; i < 4; i++) L.push(...point((i + 0.5) * Math.PI / 2 - Math.PI / 2, 98, 15));
  for (let i = 0; i < 4; i++) L.push(...point(i * Math.PI / 2 - Math.PI / 2, 142, 21));
  L.push(WF(S.circle(cx, cy, 10), { shade: { ...WS, w: 4 } }));
  L.push(INK(S.circle(cx, cy, 2.5)));
  // the north mark: a lily above the north point
  const ny = cy - 150;
  L.push(WF(S.union(S.ellipse(cx, ny - 6, 5, 14), S.rot(S.ellipse(cx - 11, ny - 2, 4, 11), -35, cx - 11, ny - 2), S.rot(S.ellipse(cx + 11, ny - 2, 4, 11), 35, cx + 11, ny - 2)), { shade: { ...WS, w: 4 } }));
  L.push(WF(S.box(cx - 13, ny + 6, cx + 13, ny + 10), { shade: null }));
  return L;
}

module.exports = {
  id: 'routing',
  emblem,
  button: () => place(emblem(), 0.74, 120, -5),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 64, ox: 0, oy: 0 } }),
  watermark,
};
