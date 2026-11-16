'use strict';
// Switching & LANs. Emblem: a patch panel with looped cords. Watermark: a railway switchyard seen from a signal box.
const { S, T, place, F, INK, PAPER, H, WF, BF, WS, range } = require('./kit');

function emblem() {
  const L = [];
  L.push(BF(S.box(0, 6, 64, 28, 1)));
  L.push(INK(S.seg(1, 9, 63, 9, 1)));
  for (let r = 0; r < 2; r++) for (let c = 0; c < 6; c++) L.push(F(S.box(6 + c * 9.5, 12 + r * 8, 11 + c * 9.5, 17 + r * 8), 1));
  // cords: from lower-row ports, looping down and back up
  const cord = (x0, x1, depth, w = 3.4) => S.stroke(S.path([[x0, 25], ['c', [x0, 25 + depth], [x1, 25 + depth], [x1, 25]]], 14), w);
  for (const [a, b, d] of [[8.5, 37, 30], [18, 46.5, 40], [27.5, 56, 22]]) L.push(BF(cord(a, b, d), { shade: { dir: [0.7, 0.72], w: 1.4, k: 0.9, p: 2 } }));
  L.push(BF(S.stroke(S.path([[8.5, 17], ['c', [-4, 22], [-6, 50], [2, 66]]], 12), 3.4)));
  return L;
}

function watermark() {
  const L = []; const V = [236, 36], B = 400;
  // ballast between the outermost rails, lightly hatched
  const bot = range(6, i => -70 + i * 88);
  // sleepers in perspective
  const sleepers = [];
  for (let n = 0; n < 40; n++) {
    const f = 1 / (1 + n * 0.16), y = V[1] + (B + 40 - V[1]) * f; if (y < V[1] + 18) break;
    for (const bx of bot) {
      const cxp = V[0] + (bx - V[0]) * (y - V[1]) / (B - V[1]), half = 22 * (y - V[1]) / (B - V[1]);
      sleepers.push(S.seg(cxp - half, y, cxp + half, y, Math.max(1, 5 * f)));
    }
  }
  L.push(INK(S.union(sleepers)));
  // rails, white with ink edges, converging on the vanishing point
  for (const bx of bot) for (const g of [-14, 14]) {
    const x1 = bx + g; L.push(WF(S.poly([[V[0], V[1]], [x1 - 3, B + 20], [x1 + 3, B + 20]]), { line: 1.2, shade: null }));
  }
  // a crossover between tracks 2 and 3, and a turnout from 4
  for (const [a, b] of [[bot[2], bot[3]], [bot[4], bot[5]]]) {
    const p = (bx, y) => V[0] + (bx - V[0]) * (y - V[1]) / (B - V[1]);
    L.push(WF(S.stroke(S.path([[p(a, 360), 360], ['c', [p(a, 290), 290], [p(b, 280), 280], [p(b, 220), 220]]], 16), 4), { line: 1.2, shade: null }));
  }
  // signal gantry across the yard with semaphore arms
  L.push(WF(S.box(96, 150, 340, 162), { shade: { ...WS, w: 5 } }));
  L.push(INK(S.union(range(16, i => S.seg(100 + i * 15, 152, 108 + i * 15, 160, 1.2)))));
  for (const x of [128, 300]) L.push(WF(S.box(x - 5, 160, x + 5, 330), { tone: T.cyl(x - 5, x + 5, 0, 0.9), hatch: H(90, 2.5), shade: null }));
  for (const x of [170, 220, 270]) {
    L.push(WF(S.box(x - 2.5, 96, x + 2.5, 150), { shade: null, line: 1.4 }));
    L.push(WF(S.rot(S.box(x, 104, x + 34, 113), 20, x, 108), { shade: null }));
    L.push(F(S.rot(S.box(x + 22, 104, x + 28, 113), 20, x, 108), 1));
    L.push(WF(S.circle(x, 108, 5), { shade: null, line: 1.4 }));
  }
  // the signal box: the lever frame in the foreground, polished handles white against the band
  L.push(WF(S.box(-10, 352, 150, 410), { tone: 0.55, hatch: H(0, 3), shade: null }));
  for (let i = 0; i < 6; i++) {
    const x = 6 + i * 22, top = 216 + (i % 3) * 22;
    L.push(WF(S.poly([[x - 4, 360], [x + 4, 360], [x + 12, top + 10], [x + 6, top + 8]]), { shade: { ...WS, w: 4 } }));
    L.push(WF(S.rot(S.box(x + 3, top - 22, x + 13, top + 10, 3), 12, x + 8, top), { shade: { ...WS, w: 3 } }));
    L.push(WF(S.rot(S.box(x - 2, top + 26, x + 14, top + 34, 1), 12, x + 6, top + 30), { shade: null, line: 1.4 }));
  }
  L.push(WF(S.box(-10, 344, 150, 356), { shade: null }));
  return L;
}

module.exports = {
  id: 'switching',
  emblem,
  button: () => place(emblem(), 0.78, 116, -1),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 64, ox: 0, oy: -2 * (size - 2) / 64 } }),
  watermark,
};
