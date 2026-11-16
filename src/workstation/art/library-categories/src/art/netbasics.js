'use strict';
// Networking Fundamentals. Emblem: seven stacked stone tablets, the top one lettered (the seven layers).
// Watermark: a pagoda of seven storeys.
const { S, T, place, F, INK, H, WF, BF, WS, rough, subdiv, eaveRoof, lettering } = require('./kit');

// a stone slab in oblique view with chipped edges; returns [side, top, front] layers
function stone(x, y, w, h, dep, seed, o = {}) {
  const dx = dep * 0.62, dy = dep * 0.42, j = o.j ?? 0.7;
  const front = S.poly(rough(subdiv([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], 4), j, seed));
  const top = S.poly(rough(subdiv([[x, y], [x + dx, y - dy], [x + w + dx, y - dy], [x + w, y]], 3), j * 0.6, seed + 7));
  const side = S.poly([[x + w, y], [x + w + dx, y - dy], [x + w + dx, y + h - dy], [x + w, y + h]]);
  const mk = o.mk || BF;
  return [mk(side, { tone: o.side ?? 0.55, hatch: H(0, o.p || 2) }), mk(top, { shade: null }), mk(front)];
}

// local box 0..64; n tablets (7 on the button, fewer on small icons): irregular widths, dark sides, pitted faces
function emblem(n = 7) {
  const L = []; const off = [0, 3, -2, 2, -1, 1, 0], wid = [42, 38, 41, 36, 40, 37, 39]; const th = 52 / (n + 1.4), dep = 16;
  for (let i = 0; i < n; i++) {
    const x = 4 + off[i % 7] * (7 / n), y = 63 - th * (i + 1), w = wid[i % 7];
    L.push(...stone(x, y, w, th, dep, 11 + i * 5, { side: 0.85, j: 0.9 }));
    if (n > 4) L.push(INK(S.union(S.circle(x + 6 + (i * 7) % 20, y + th * 0.5, 0.7), S.circle(x + 24 + (i * 5) % 12, y + th * 0.45, 0.7))));
  }
  const yt = 63 - th * n, x = 4, w = wid[(n - 1) % 7], dx = dep * 0.62, dy = dep * 0.42;
  L.push(INK(S.inter(lettering(x + 5, yt - dy + 1.6, x + w + dx - 4, yt - 1.4, 2, 1.1, 7), S.poly([[x, yt], [x + w, yt], [x + w + dx, yt - dy], [x + dx, yt - dy]]))));
  return L;
}

function watermark() {
  const L = []; const cx = 190;
  // stone podium and steps, cropped by the bottom edge
  L.push(...stone(40, 374, 320, 40, 22, 5, { mk: WF, side: 0.9, p: 3, j: 1.2 }));
  L.push(...stone(84, 356, 246, 18, 16, 9, { mk: WF, side: 0.9, p: 3, j: 1.2 }));
  L.push(INK(S.union([0, 1, 2].map(i => S.seg(40, 386 + i * 6, 360, 386 + i * 6, 1.6)))));
  // seven storeys, narrowing upwards: a wall in the eave's shadow, then the roof
  let y = 356;
  for (let i = 0; i < 7; i++) {
    const k = 1 - i / 8.2;
    const ww = 66 * k + 8, wh = 24 * k + 9, rw = 168 * k + 22, rh = 15 * k + 7, wy = y - wh;
    // wall: cast shadow from the eave fading downwards in horizontal lines, the right end in shade
    L.push(WF(S.box(cx - ww, wy, cx + ww, y), { tone: (x, yy) => Math.max(1.15 - (yy - wy) / (wh * 0.75), x > cx + ww * 0.5 ? 0.5 : 0), hatch: H(0, 3), shade: null }));
    for (let p = -2; p <= 2; p++) L.push(INK(S.seg(cx + p * ww * 0.46, wy + wh * 0.35, cx + p * ww * 0.46, y, 1.6)));
    if (i === 0) L.push(F(S.box(cx - 12, y - 20, cx + 12, y), 1));
    else L.push(F(S.box(cx - 5, wy + wh * 0.45, cx + 5, y - 2), 0.75, H(90, 2.5), { line: 1.5 }));
    if (i > 0) L.push(WF(S.box(cx - ww - 6, y - 3, cx + ww + 6, y), { line: 1.4, shade: null }));
    const ry = wy - rh;
    const roof = eaveRoof(cx, ry, ww * 0.5, rw, rh, 6 + 5 * k);
    L.push(F(S.move(roof, 0, 4), 1));                                                   // the eave's dark underside
    L.push(WF(roof, { tone: T.lin(cx + rw * 0.3, 0, cx + rw, 0, 0, 0.6), hatch: H(60, 3.4), shade: { ...WS, dir: [0.3, 1], w: 6 } }));
    const ridges = [];
    for (let t = -8; t <= 8; t += 2) ridges.push(S.seg(cx + t * ww * 0.06, ry + 2, cx + t * rw / 8.6, ry + rh - 1, 1.2));
    L.push({ shape: S.inter(S.union(ridges), S.offset(roof, -2)), kind: 'ink', only: x => x < cx + rw * 0.3 });
    y = ry + 1;
  }
  // finial: a mast with rings and a jewel
  L.push(WF(S.box(cx - 4, y - 76, cx + 4, y + 2), { line: 1.6, tone: T.cyl(cx - 4, cx + 4, 0, 0.9), hatch: H(90, 2), shade: null }));
  for (let r = 0; r < 8; r++) L.push(WF(S.ellipse(cx, y - 12 - r * 7.2, 11 - r * 0.7, 2.8), { line: 1.5, shade: { ...WS, w: 3 } }));
  L.push(WF(S.circle(cx, y - 84, 7.5), { line: 1.6, shade: { ...WS, w: 5 } }));
  // a pine bough crossing the band from the left: white on the cream
  const needles = [];
  for (let i = 0; i < 9; i++) { const bx = 2 + i * 12, by = 250 - i * 5; needles.push(S.seg(bx, by, bx + 10, by - 20, 4, 1.2), S.seg(bx + 3, by, bx + 17, by + 10, 4, 1.2)); }
  L.push(WF(S.union(needles), { line: 1.2, shade: null }));
  L.push(WF(S.seg(-6, 254, 122, 200, 7, 3), { line: 1.6, shade: { ...WS, w: 4 } }));
  return L;
}

module.exports = {
  id: 'netbasics',
  emblem,
  button: () => place(emblem(7), 0.98, 117, -14),
  icon: size => ({ layers: emblem(size <= 16 ? 3 : 4), view: { s: (size - 2) / 58, ox: -3 * (size - 2) / 58, oy: -7 * (size - 2) / 58 } }),
  watermark,
};
