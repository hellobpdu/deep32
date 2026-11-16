'use strict';
// WAN, VPN & SD-WAN. Emblem: a suspension bridge between two towers. Watermark: a stagecoach on a long road.
const { S, T, place, F, INK, H, WF, BF, WS, wheel, range, lerp } = require('./kit');

function emblem() {
  const L = []; const deck = 44;
  const sag = x => { const t = (x - 14) / 36; return 9 + 30 * 4 * t * (1 - t); };
  const main = range(19, i => { const x = 14 + i * 2; return [x, sag(x)]; });
  L.push(INK(S.union(range(17, i => { const x = 16 + i * 2; return S.seg(x, sag(x), x, deck, 0.8); }))));
  L.push(INK(S.union(range(6, i => S.seg(0, 52 + i * 3.4, 64, 52 + i * 3.4, 0.9)).filter((_, i) => i % 2 === 0))));
  L.push(BF(S.box(-4, deck, 68, deck + 4)));
  for (const tx of [14, 50]) {
    L.push(BF(S.union(S.box(tx - 4, 4, tx - 1.5, 58), S.box(tx + 1.5, 4, tx + 4, 58), S.box(tx - 4, 4, tx + 4, 8), S.box(tx - 4, 22, tx + 4, 25))));
  }
  L.push(BF(S.stroke(main, 1.8), { shade: null }));
  L.push(BF(S.stroke([[14, 9], [8, 22], [0, 40]], 1.8), { shade: null }));
  L.push(BF(S.stroke([[50, 9], [56, 22], [64, 40]], 1.8), { shade: null }));
  return L;
}

function watermark() {
  const L = []; const hz = 168, vp = [56, hz];
  // the long road to the horizon, with ruts, and hills on the horizon
  L.push(INK(S.stroke(S.path([[0, hz - 6], ['c', [40, hz - 30], [90, hz - 18], [130, hz - 4]], ['c', [170, hz - 22], [240, hz - 40], [323, hz - 14]]], 18), 1.6)));
  L.push(INK(S.seg(0, hz, 323, hz, 1)));
  // the road surface: white, so it shows across the band too
  L.push(WF(S.poly([[vp[0] - 2, hz], [vp[0] + 4, hz], [330, 400], [-60, 400]]), { line: 2, shade: null }));
  for (const k of [0.3, 0.55, 0.7]) L.push(INK(S.seg(vp[0] + 1, hz + 2, -60 + (390) * k, 400, 1)));
  // fence posts along the road shrinking with distance
  for (let n = 0; n < 9; n++) { const t = 1 / (1 + n * 0.55), x = vp[0] + 8 + (300 - vp[0]) * t, y = hz + (400 - hz) * t; L.push(WF(S.box(x, y - 46 * t, x + 6 * t + 1.5, y), { line: 1.2, shade: null })); }
  // the coach (drawn at its own place, then moved left so its front and team pole cross the band)
  const C = [];
  const body = S.poly(S.path([[150, 196], [296, 196], ['c', [306, 240], [304, 280], [290, 306]], [168, 306], ['c', [150, 280], [142, 236], [150, 196]]], 14));
  C.push(F(S.move(body, 6, 6), 0.9, H(-40, 3)));
  C.push(WF(body, { shade: { ...WS, w: 10 } }));
  C.push(WF(S.box(198, 204, 252, 300, 4), { shade: { ...WS, w: 4 } }));
  C.push(F(S.box(206, 212, 244, 244, 3), 0.7, H(90, 3), { line: 1.6 }));
  C.push(F(S.box(160, 212, 190, 244, 3), 0.7, H(90, 3), { line: 1.6 }));
  C.push(F(S.box(260, 212, 290, 244, 3), 0.7, H(90, 3), { line: 1.6 }));
  C.push(INK(S.union(S.seg(150, 256, 296, 256, 1.6), S.seg(155, 270, 296, 270, 1))));
  C.push(WF(S.box(146, 186, 302, 197, 2), { shade: null }));
  C.push(WF(S.box(170, 160, 230, 186, 3), { tone: T.lin(170, 0, 230, 0, 0, 0.6), hatch: H(90, 3), shade: null }));
  C.push(WF(S.box(232, 168, 280, 186, 3), { shade: { ...WS, w: 4 } }));
  C.push(INK(S.union(range(6, i => S.seg(150 + i * 30, 176, 150 + i * 30, 186, 1.4)), S.seg(148, 176, 300, 176, 1.4))));
  // driver's box and footboard at the front, the pole reaching for the team
  C.push(WF(S.poly([[112, 222], [150, 214], [152, 262], [118, 252]]), { tone: 0.4, hatch: H(30, 3) }));
  C.push(WF(S.box(104, 214, 126, 222, 2), { shade: null }));
  C.push(WF(S.seg(150, 318, 20, 300, 6, 4), { shade: null }));
  // springs and the perch
  C.push(WF(S.ellipse(186, 314, 22, 5), { shade: null, line: 1.4 }));
  C.push(WF(S.ellipse(268, 314, 24, 5), { shade: null, line: 1.4 }));
  // wheels: the rear larger
  C.push(...wheel(276, 330, 50, 14, 7, WF, { hub: 9, spokeW: 3.4 }));
  C.push(...wheel(174, 344, 36, 12, 6, WF, { hub: 7, spokeW: 3 }));
  L.push(...place(C, 1, -48, 0));
  return L;
}

module.exports = {
  id: 'wan',
  emblem,
  button: () => place(emblem(), 0.8, 114, -4),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 64, ox: 0, oy: 0 } }),
  watermark,
};
