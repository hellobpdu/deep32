'use strict';
// Cloud & IT Operations. Emblem: a cumulus cloud over a warehouse roof. Watermark: a lighthouse keeper's lamp room.
const { S, T, place, F, INK, H, WF, BF, WS, range } = require('./kit');

function emblem() {
  const roof = S.poly([[0, 46], [0, 40], [12, 30], [12, 40], [24, 30], [24, 40], [36, 30], [36, 40], [48, 30], [48, 40], [60, 30], [64, 34], [64, 46]]);
  const cloud = S.union(S.circle(18, 20, 9), S.circle(30, 14, 12), S.circle(44, 18, 10), S.circle(54, 23, 7), S.box(12, 20, 58, 30, 5));
  return [
    BF(S.box(0, 46, 64, 66), { tone: 0.0 }),
    INK(S.union(range(4, i => S.box(5 + i * 15, 52, 13 + i * 15, 66)))),
    BF(roof, { shade: null }),
    INK(S.union(range(5, i => S.seg(12 + i * 12, 30, 12 + i * 12, 40, 1.2)))),
    INK(S.inter(S.union(range(5, i => S.seg(i * 12 + 1, 40, i * 12 + 11, 31, 1))), roof)),
    BF(cloud, { shade: null }),
    INK(S.union(S.arc(30, 14, 7, 200, 290, 1), S.arc(44, 18, 5.5, 200, 290, 1), S.seg(16, 29, 56, 29, 1))),
  ];
}

function watermark() {
  const L = []; const cx = 186;
  // light beams thrown left across the band
  for (const [a, b] of [[-0.32, -0.18], [0.12, 0.26]]) L.push(F(S.poly([[cx - 20, 200], [-40, 200 + Math.tan(a) * 260], [-40, 200 + Math.tan(b) * 260]]), 0.18, H(0, 3), { line: 0 }));
  // the tower top below the gallery, masonry in courses
  const tower = S.poly([[cx - 74, 420], [cx + 74, 420], [cx + 66, 300], [cx - 66, 300]]);
  L.push(WF(tower, { tone: T.lin(cx - 74, 0, cx + 74, 0, 0, 0.75), hatch: H(90, 3.4), shade: null }));
  L.push(INK(S.inter(S.union(range(8, i => S.seg(cx - 80, 312 + i * 15, cx + 80, 312 + i * 15, 1))), S.offset(tower, -2))));
  L.push(F(S.union(S.box(cx - 10, 340, cx + 10, 380), S.circle(cx, 340, 10)), 1));
  // the gallery: corbelled deck and railing
  L.push(WF(S.box(cx - 100, 286, cx + 100, 300), { tone: 0.3, hatch: H(0, 3), shade: null }));
  L.push(INK(S.union(range(14, i => S.seg(cx - 96 + i * 14.5, 300, cx - 90 + i * 14.5, 308, 1.6)))));
  L.push(WF(S.box(cx - 100, 230, cx + 100, 236), { shade: null, line: 1.4 }));
  L.push(INK(S.union(range(15, i => S.seg(cx - 96 + i * 13.6, 236, cx - 96 + i * 13.6, 286, 2)))));
  // the lantern: glazed drum with mullions and diagonal astragals, the lens inside
  const lx0 = cx - 62, lx1 = cx + 62, ly0 = 116, ly1 = 228;
  L.push(WF(S.box(lx0, ly0, lx1, ly1), { tone: 0.0, shade: null }));
  // the lens: a beehive of prism rings around a bright bull's-eye
  const lens = S.union(S.box(cx - 40, 132, cx + 40, 214, 6));
  L.push(WF(lens, { tone: T.cyl(cx - 40, cx + 40, 0, 0.6, 0.4), hatch: H(90, 3), shade: null, line: 1.6 }));
  L.push(INK(S.inter(S.union(range(9, i => S.seg(cx - 40, 138 + i * 9, cx + 40, 138 + i * 9, 1.4))), lens)));
  L.push(WF(S.circle(cx, 172, 15), { shade: null }));
  L.push(WF(S.circle(cx, 172, 7), { tone: 0.0, shade: null, line: 1.2 }));
  // mullions and astragals over the glass
  L.push(INK(S.inter(S.union(range(5, i => S.seg(lx0 + i * 31, ly0, lx0 + i * 31, ly1, 3)), range(8, i => S.seg(lx0 - 60 + i * 31, ly1, lx0 + i * 31, ly0, 1.4))), S.box(lx0, ly0, lx1, ly1))));
  L.push(WF(S.box(lx0 - 6, ly1, lx1 + 6, ly1 + 6), { shade: null, line: 1.4 }));
  // the domed roof, ventilator ball and vane
  L.push(WF(S.box(lx0 - 12, ly0 - 8, lx1 + 12, ly0 + 2), { tone: 0.2, hatch: H(0, 2.5), shade: null }));
  const dome = S.inter(S.ellipse(cx, ly0 - 8, 70, 58), S.halfY(ly0 - 8, false));
  L.push(WF(dome, { tone: T.sphere(cx, ly0 - 8, 70, 0, 0.85), hatch: H(90, 3.2), shade: null }));
  L.push(INK(S.inter(S.union(range(6, i => { const x = cx - 50 + i * 20; return S.seg(cx, ly0 - 66, x, ly0 - 8, 1.2); })), dome)));
  L.push(WF(S.circle(cx, ly0 - 74, 11), { tone: T.sphere(cx, ly0 - 74, 11, 0, 0.9), hatch: { ring: [cx - 4, ly0 - 78], p: 2.5 }, shade: null }));
  L.push(WF(S.box(cx - 2, 10, cx + 2, ly0 - 84), { shade: null, line: 1.2 }));
  L.push(WF(S.poly([[cx + 2, 16], [cx + 34, 22], [cx + 26, 28], [cx + 2, 26]]), { shade: null, line: 1.2 }));
  L.push(WF(S.seg(cx - 18, 30, cx + 18, 30, 3), { shade: null, line: 1 }));
  return L;
}

module.exports = {
  id: 'itinfra',
  emblem,
  button: () => place(emblem(), 0.74, 122, -4),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 64, ox: 0, oy: 0 } }),
  watermark,
};
