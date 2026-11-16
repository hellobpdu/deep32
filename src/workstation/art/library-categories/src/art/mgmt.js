'use strict';
// Management & Automation. Emblem: a ship's engine-room telegraph. Watermark: a player-piano roll unspooling.
const { S, T, place, F, INK, H, WF, BF, WS, range } = require('./kit');

function emblem() {
  const cx = 30, cy = 24, r = 19;
  const ticks = range(9, i => { const a = Math.PI * (1.1 + i * 0.1); return S.seg(cx + Math.cos(a) * (r - 7), cy + Math.sin(a) * (r - 7), cx + Math.cos(a) * (r - 2.5), cy + Math.sin(a) * (r - 2.5), 1); });
  const ticks2 = range(9, i => { const a = Math.PI * (0.1 + i * 0.1); return S.seg(cx + Math.cos(a) * (r - 7), cy + Math.sin(a) * (r - 7), cx + Math.cos(a) * (r - 2.5), cy + Math.sin(a) * (r - 2.5), 1); });
  return [
    BF(S.poly([[22, 40], [38, 40], [42, 66], [18, 66]]), { tone: T.cyl(18, 42, 0, 0.9), hatch: H(90, 2), shade: null }),
    BF(S.box(16, 60, 44, 66)),
    BF(S.circle(cx, cy, r + 3), { tone: 0.55, hatch: { ring: [cx, cy], p: 2 }, shade: null }),
    BF(S.circle(cx, cy, r), { shade: null }),
    INK(S.union(ticks, ticks2, S.seg(cx - r + 1, cy, cx + r - 1, cy, 1.2))),
    INK(S.seg(cx, cy, cx + 12, cy - 10, 2.2)),
    BF(S.circle(cx, cy, 3)),
    BF(S.rot(S.box(cx + r - 2, cy - 2.2, cx + r + 22, cy + 2.2, 2), -38, cx + r, cy), { shade: null }),
    BF(S.circle(cx + r + 17, cy - 15, 3.6)),
  ];
}

function watermark() {
  const L = [];
  // the spool at the top right: a cylinder lying across, flanges at both ends
  const sy = 56, sx0 = 150, sx1 = 330;
  L.push(WF(S.ellipse(sx0, sy, 12, 34), { tone: 0.2, hatch: H(90, 3), shade: { ...WS, w: 5 } }));
  L.push(WF(S.box(sx0, sy - 30, sx1, sy + 30), { tone: T.cylY(sy - 30, sy + 30, 0, 0.9, 0.25), hatch: H(0, 3), shade: null }));
  L.push(INK(S.union(range(8, i => S.seg(sx0 + 4, sy - 26 + i * 7.5, sx1, sy - 26 + i * 7.5, 1)))));
  L.push(WF(S.ellipse(sx0 - 4, sy, 13, 38), { shade: { ...WS, w: 5 } }));
  L.push(WF(S.ellipse(sx0 - 4, sy, 4, 8), { tone: 0.8, hatch: H(0, 2) }));
  // the paper sheet unspooling in an S towards the lower left: centre line and width taper (perspective)
  const C = S.path([[240, sy + 28], ['c', [250, 150], [330, 190], [250, 250]], ['c', [190, 296], [60, 260], [40, 330]], ['c', [30, 366], [60, 392], [90, 420]]], 40);
  const half = i => 80 - i * 0.45;
  const n = C.length, left = [], right = [], holes = [];
  for (let i = 0; i < n; i++) {
    const p = C[i], q = C[Math.min(n - 1, i + 1)], o = C[Math.max(0, i - 1)];
    let tx = q[0] - o[0], ty = q[1] - o[1]; const tl = Math.hypot(tx, ty) || 1; tx /= tl; ty /= tl;
    // the sheet twists: its visible width follows a cosine along the length
    const w = half(i) * (0.35 + 0.65 * Math.abs(Math.cos(i / n * Math.PI * 1.15)));
    left.push([p[0] - ty * w, p[1] + tx * w]); right.push([p[0] + ty * w, p[1] - tx * w]);
    if (i % 2 === 0 && i > 2) for (let k = -3; k <= 3; k++) {
      if ((i * 7 + k * 13) % 5 < 2) continue;
      const f = k / 4.2, hx = p[0] - ty * w * f, hy = p[1] + tx * w * f;
      holes.push(S.rot(S.box(hx - 2.2, hy - 4.5, hx + 2.2, hy + 4.5), Math.atan2(ty, tx) * 180 / Math.PI, hx, hy));
    }
  }
  const sheet = S.poly([...left, ...right.reverse()]);
  L.push(F(S.move(sheet, 6, 8), 0.9, H(-30, 3)));
  L.push(WF(sheet, { tone: (x, y) => 0, shade: { ...WS, w: 6 } }));
  L.push(F(S.inter(S.union(holes), S.offset(sheet, -3)), 1));
  // the sheet's edges drawn again on top so they stay crisp across the holes
  L.push(INK(S.union(S.stroke(left.slice(0), 1.6), S.stroke(right, 1.6))));
  return L;
}

module.exports = {
  id: 'mgmt',
  emblem,
  button: () => place(emblem(), 0.78, 118, -2),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 64, ox: 0, oy: 0 } }),
  watermark,
};
