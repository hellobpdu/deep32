'use strict';
// Operating Systems. Emblem: a clockwork gear train. Watermark: a ship's engine room with governor.
const { S, T, place, F, INK, H, WF, BF, WS, cylinder, range } = require('./kit');

function spokedGear(cx, cy, r, n, th, spokes, mk, o = {}) {
  const rim = S.sub(S.gear(cx, cy, r, n, th, 0.45), S.circle(cx, cy, r * 0.72));
  const arms = S.union(range(spokes, i => { const a = i * 2 * Math.PI / spokes + (o.rot || 0); return S.seg(cx, cy, cx + Math.cos(a) * r * 0.75, cy + Math.sin(a) * r * 0.75, r * 0.16); }));
  return [mk(rim, { shade: o.shade }), mk(arms, { shade: null }), mk(S.circle(cx, cy, r * 0.2), { tone: o.hubTone ?? 0.5, hatch: { ring: [cx, cy], p: 2 } })];
}

function emblem() {
  const sh = null;
  return [
    ...spokedGear(22, 38, 20, 16, 4, 5, BF, { shade: sh }),
    ...spokedGear(50, 18, 12, 10, 3.5, 4, BF, { shade: sh, rot: 0.4 }),
    ...spokedGear(52, 50, 9, 8, 3, 3, BF, { shade: sh, rot: 0.2 }),
  ];
}

function watermark() {
  const L = [];
  // behind: two great cylinders with their covers and studs, a steam pipe
  for (const [x0, x1] of [[196, 270], [280, 354]]) {
    L.push(...cylinder((x0 + x1) / 2, 150, 330, (x1 - x0) / 2, 0.24, { lo: 0, hi: 0.8, p: 3.2 }).map(l => ({ ...l, line: 2 })));
    L.push(INK(S.union(range(9, i => { const a = Math.PI * (0.05 + i * 0.1125); return S.circle((x0 + x1) / 2 + Math.cos(a) * (x1 - x0) * 0.42, 150 + Math.sin(a) * (x1 - x0) * 0.1, 2.4); }))));
  }
  L.push(WF(S.stroke(S.path([[150, 40], ['c', [230, 34], [233, 90], [233, 140]]], 16), 16), { tone: T.lin(0, 30, 0, 140, 0.0, 0.5), hatch: H(90, 3) }));
  L.push(WF(S.stroke([[233, 120], [233, 136]], 22), { shade: null }));
  // pressure gauge on the pipe
  L.push(WF(S.circle(286, 70, 30), { tone: 0.0, shade: { ...WS, w: 6 } }));
  L.push(WF(S.circle(286, 70, 23), { shade: null, line: 1.4 }));
  L.push(INK(S.union(range(11, i => { const a = Math.PI * (0.75 + i * 0.15); return S.seg(286 + Math.cos(a) * 17, 70 + Math.sin(a) * 17, 286 + Math.cos(a) * 22, 70 + Math.sin(a) * 22, 1.2); }), S.seg(286, 70, 300, 58, 2))));
  // the flyball governor in front: spindle, two balls on arms, sleeve and links, its stand
  const gx = 96, top = 70;
  L.push(WF(S.poly([[gx - 40, 420], [gx + 40, 420], [gx + 16, 330], [gx - 16, 330]]), { tone: T.lin(gx - 40, 0, gx + 40, 0, 0, 0.7), hatch: H(90, 3) }));
  L.push(WF(S.box(gx - 46, 324, gx + 46, 336), { shade: null }));
  L.push(WF(S.box(gx - 4, top, gx + 4, 330), { tone: T.cyl(gx - 4, gx + 4, 0, 0.9), hatch: H(90, 2), shade: null }));
  L.push(WF(S.box(gx - 10, top - 14, gx + 10, top + 6, 3), { shade: null }));
  const sleeveY = 226;
  L.push(WF(S.box(gx - 12, sleeveY - 10, gx + 12, sleeveY + 12, 2), { tone: 0.3, hatch: H(0, 2.5) }));
  for (const s of [-1, 1]) {
    const bx = gx + s * 58, by = 160;
    L.push(WF(S.seg(gx + s * 5, top + 2, bx, by, 6), { shade: null, line: 1.4 }));
    L.push(WF(S.seg((gx + bx) / 2, (top + by) / 2, gx + s * 10, sleeveY, 4.5), { shade: null, line: 1.2 }));
    L.push(WF(S.circle(bx, by, 24), { tone: T.sphere(bx, by, 24, 0, 0.95), hatch: { ring: [bx - 8, by - 8], p: 3.2 }, shade: null }));
    L.push({ shape: S.rot(S.ellipse(bx - 10, by - 12, 5, 8), 45, bx - 10, by - 12), kind: 'paper' });
  }
  // bevel gears at the foot of the spindle, a belt pulley
  L.push(WF(S.gear(gx, 300, 22, 18, 4), { tone: T.cyl(gx - 26, gx + 26, 0, 0.8), hatch: H(90, 2.5), shade: null }));
  L.push(WF(S.circle(gx + 54, 300, 20), { tone: T.sphere(gx + 54, 300, 20, 0, 0.8), hatch: { ring: [gx + 54, 300], p: 3 } }));
  // the engine-room railing across the foreground
  L.push(WF(S.box(-10, 352, 340, 360), { shade: null }));
  for (const x of [20, 170, 300]) L.push(WF(S.box(x - 3, 352, x + 3, 420), { shade: null }));
  L.push(WF(S.box(-10, 388, 340, 394), { shade: null }));
  return L;
}

module.exports = {
  id: 'os',
  emblem,
  button: () => place(emblem(), 0.82, 116, -6),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 64, ox: 0, oy: 0 } }),
  watermark,
};
