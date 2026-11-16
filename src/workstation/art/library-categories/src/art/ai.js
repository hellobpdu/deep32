'use strict';
// AI & Data. Emblem: an owl on a stack of books. Watermark: a chess-playing automaton.
const { S, T, place, F, INK, H, WF, BF, WS, range } = require('./kit');

function emblem() {
  const L = [];
  // books
  L.push(BF(S.box(6, 52, 58, 60), { shade: null }));
  L.push(INK(S.seg(8, 56, 56, 56, 0.8)));
  L.push(BF(S.box(10, 44, 54, 52), { tone: 0.5, hatch: H(90, 2), shade: null }));
  L.push(BF(S.box(4, 60, 60, 66), { tone: 0.25, hatch: H(90, 2), shade: null }));
  // owl: body, wings, head, ear tufts, eyes, beak, feet
  L.push(BF(S.ellipse(32, 32, 14, 15), { tone: 0.0, shade: { dir: [0.7, 0.72], w: 3, k: 0.9, p: 2 } }));
  L.push(INK(S.inter(S.union(range(4, i => S.arc(32, 26 + i * 4, 6, 30, 150, 0.9))), S.ellipse(32, 34, 10, 11))));
  L.push(BF(S.poly(S.path([[18, 26], ['c', [14, 34], [16, 42], [22, 46]], [24, 34]], 8)), { tone: 0.6, hatch: H(70, 2) }));
  L.push(BF(S.poly(S.path([[46, 26], ['c', [50, 34], [48, 42], [42, 46]], [40, 34]], 8)), { tone: 0.6, hatch: H(110, 2) }));
  L.push(BF(S.union(S.ellipse(32, 14, 14, 10), S.poly([[18, 10], [20, 2], [25, 7]]), S.poly([[46, 10], [44, 2], [39, 7]])), { shade: null }));
  for (const x of [26, 38]) { L.push(BF(S.circle(x, 14, 5), { shade: null })); L.push(INK(S.circle(x, 14, 2.2))); }
  L.push(INK(S.poly([[30.5, 18], [33.5, 18], [32, 22]])));
  L.push(INK(S.union(S.seg(27, 44, 27, 47, 1.2), S.seg(37, 44, 37, 47, 1.2))));
  return L;
}

function watermark() {
  const L = [];
  // the cabinet: front with doors open on its clockwork, side in shade
  const fx0 = 40, fx1 = 280, fy0 = 270, fy1 = 420;
  L.push(WF(S.poly([[fx1, fy0], [fx1 + 50, fy0 - 26], [fx1 + 50, fy1], [fx1, fy1]]), { tone: 0.75, hatch: H(90, 3) }));
  L.push(WF(S.box(fx0, fy0, fx1, fy1), { shade: null }));
  L.push(F(S.box(fx0 + 14, fy0 + 14, fx0 + 110, fy1), 1));
  for (const [x, y, r] of [[78, 320, 24], [116, 350, 18], [70, 380, 16]]) L.push(WF(S.gear(x, y, r, Math.round(r * 0.7), 4), { line: 1.4, shade: null }));
  L.push(WF(S.box(fx0 + 124, fy0 + 14, fx1 - 14, fy1), { tone: 0.0, shade: { ...WS, w: 6 } }));
  L.push(INK(S.union(S.box(fx0 + 132, fy0 + 22, fx1 - 22, fy0 + 23.6), S.box(fx0 + 132, fy0 + 22, fx0 + 133.6, fy1), S.box(fx1 - 23.6, fy0 + 22, fx1 - 22, fy1))));
  L.push(INK(S.circle(fx0 + 140, fy0 + 70, 3)));
  // the chessboard on the cabinet top, foreshortened, chequered
  const A = [fx0 - 6, fy0], B = [fx1 + 6, fy0], C = [fx1 + 50, fy0 - 26], D = [fx0 + 40, fy0 - 26];
  L.push(WF(S.poly([A, B, C, D]), { shade: null }));
  const sq = [];
  const P = (u, v) => [A[0] + (B[0] - A[0]) * u + (D[0] - A[0]) * v, A[1] + (D[1] - A[1]) * v];
  for (let i = 0; i < 8; i++) for (let j = 0; j < 8; j++) if ((i + j) % 2) sq.push(S.poly([P(i / 8, j / 8), P((i + 1) / 8, j / 8), P((i + 1) / 8, (j + 1) / 8), P(i / 8, (j + 1) / 8)]));
  L.push(F(S.union(sq), 0.7, H(0, 2), { line: 0 }));
  // pieces: a few pawns and a king
  for (const [u, v, h] of [[0.2, 0.3, 18], [0.45, 0.6, 16], [0.62, 0.25, 30], [0.8, 0.5, 18]]) {
    const [x, y] = P(u, v);
    L.push(WF(S.union(S.ellipse(x, y, 7, 3), S.poly([[x - 4, y], [x + 4, y], [x + 2, y - h + 6], [x - 2, y - h + 6]]), S.circle(x, y - h + 4, 4.5)), { line: 1.2, shade: null }));
  }
  // the turbaned player seated behind the board, reaching over it
  L.push(WF(S.poly(S.path([[150, 250], ['c', [150, 190], [170, 150], [200, 140]], ['c', [240, 140], [262, 170], [262, 244]]], 12)), { tone: T.lin(150, 0, 262, 0, 0, 0.7), hatch: H(80, 3.2) }));
  L.push(INK(S.inter(S.union(range(6, i => S.seg(160 + i * 18, 160, 150 + i * 20, 250, 1.2))), S.box(150, 150, 262, 250))));
  // the face, a moustache and a beard; the turban wound in diagonal folds, a jewel and a plume
  L.push(WF(S.ellipse(206, 116, 21, 25), { tone: T.sphere(206, 116, 21, 0, 0.75), hatch: H(-30, 3) }));
  L.push(INK(S.union(S.seg(196, 112, 202, 111, 1.8), S.seg(210, 111, 216, 112, 1.8), S.stroke([[194, 128], [206, 124], [218, 128]], 2.4), S.stroke([[200, 134], [206, 142], [212, 134]], 1.6))));
  const turban = S.union(S.ellipse(206, 88, 32, 20), S.ellipse(206, 72, 22, 18));
  L.push(WF(turban, { shade: { ...WS, w: 8 } }));
  L.push(INK(S.inter(S.union(range(6, k => S.arc(150 + k * 16, 140, 70, 250, 300, 1.4))), S.offset(turban, -2))));
  L.push(WF(S.ellipse(206, 82, 6, 7), { tone: 0.6, hatch: { ring: [204, 80], p: 2 } }));
  L.push(WF(S.poly(S.path([[204, 76], ['c', [196, 50], [214, 36], [228, 24]], ['c', [222, 44], [214, 58], [210, 76]]], 10)), { tone: 0.2, hatch: H(60, 2.5), line: 1.4 }));
  // the arm reaching left over the board, holding a long pipe
  L.push(WF(S.stroke([[160, 170], [110, 200], [92, 236]], 18), { tone: 0.15, hatch: H(30, 3), shade: { ...WS, w: 5 } }));
  L.push(WF(S.circle(90, 242, 9), { shade: null }));
  L.push(WF(S.seg(80, 250, 10, 300, 4), { shade: null, line: 1.2 }));
  return L;
}

module.exports = {
  id: 'ai',
  emblem,
  button: () => place(emblem(), 0.76, 120, -3),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 64, ox: 0, oy: 0 } }),
  watermark,
};
