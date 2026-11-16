'use strict';
// Deep32 Library: NEW-ROOM art the period design disc does not have (00-UPDATE item 40: the engine shows the disc's own title
// screen, buttons and icons, imported at install time; item 41: the Deep32 chat becomes the "Ask" room).
// Drawn in the disc's interface style, at the disc's sizes and frame layouts (SCREENS.md section 4):
//   ask-room     294x67  = 3 frames of 98x67, like RESEARCH: normal, pressed (art +2,+2, square pressed in),
//                          hot (yellow halo instead of the shadow, square raised); label static; navy button square
//                          at (41,27)-(61,47). Background transparent (the engine composites it on the title screen it
//                          imports from his disc); ask-room-opaque carries an original cloud-texture background.
//   ask-icon     126x61  = 2 frames of 63x61, like ATLAS_ICON: normal on the dark UI ground with a light label,
//                          selected on the taupe plate with a black label; row 59 dark, row 60 the taupe rule.
//   fw-ask       127x161 like FW_CAT_BMP / FW_WORD_BMP: the Find Wizard left picture with a letter-spaced caps label.
// The illustration (an Deep32 Library original): a pearl speech balloon with an embossed blue "?" in front of a gold
// answer balloon with engraved text lines; rendered by lib/render.js and reduced to a small fixed palette.
// No pixel of the disc is used: the art is drawn as code; only the frame geometry and a handful of interface colours
// (the dark ground, taupe, label tones, the button navy ramp) were measured from the reference bitmaps.
const R = require('../../lib/render');
const { G57 } = require('../../lib/font');

// ---------- the interface colours measured from the reference bitmaps (the period design UI) ----------
const UI = {
  ground: '#34322D', taupe: '#A09683', labelLight: '#D9D6C5', black: '#000000', white: '#FFFFFF',
  navyEdge: '#00105A', navyRamp: ['#29396B', '#2B3F75', '#314A84', '#4A5A94', '#526BAD', '#6571B4'], navyHi: '#739CB5', navyHi2: '#A6CAF0',
  titleLabel: '#103963', sky: '#E7E7EF', skyGrey: '#D6D6DE', skyBlue: '#CEDEE7', skyWarm: '#FFFBF0',
  shadowLight: ['#C0C0C0', '#9C9C9C', '#808080'], shadowDark: ['#000000'], shadowTaupe: ['#907949', '#674F39'],
  glow: ['#FFFAB8', '#F7EFAD', '#EFD684'],
};
// ---------- materials (Deep32 Library originals) ----------
const PEARL = R.ramp('#E4E4EA', 9, '#1E1E2A', '#FFFFFF', 0.8, 1.0);
const BLUE = R.ramp('#2E4AD0', 7, '#06103E', '#C8D4FF', 0.8, 0.85);
const GOLD = R.ramp('#F2BC2E', 9, '#3A2204', '#FFF8D8', 0.82, 0.9);
const PALETTE = [...new Set([...PEARL, ...BLUE, ...GOLD, UI.ground, UI.taupe, UI.black, UI.white, UI.sky, UI.skyGrey, UI.skyBlue])];

// ---------- the scene: unit composition scaled by S around (cx, cy) ----------
function scene(S, cx, cy, d = {}) {
  const u = (x, y) => [cx + x * S, cy + y * S];
  const [fx, fy] = u(0, 0), frx = 0.5 * S, fry = 0.36 * S;
  const tail = [u(-0.30, 0.20), u(-0.06, 0.33), u(-0.44, 0.62)];
  const front = (x, y) => R.smin(R.sdEllipse(x, y, fx, fy, frx, fry), R.sdTriangle(x, y, ...tail), 0.06 * S);
  const [gx, gy] = u(0.37, -0.33), grx = 0.37 * S, gry = 0.26 * S;
  const gtail = [u(0.50, -0.16), u(0.66, -0.24), u(0.86, 0.02)];
  const gold = (x, y) => R.smin(R.sdEllipse(x, y, gx, gy, grx, gry), R.sdTriangle(x, y, ...gtail), 0.05 * S);
  // engraved answer lines on the gold balloon
  const lines = [[-0.13, -0.18, 0.24], [-0.02, -0.22, 0.26], [0.09, -0.16, 0.12]].map(([dy, a, b]) => [gy + dy * S, gx + a * S, gx + b * S]);
  const groove = (x, y) => { for (const [ly, a, b] of lines) if (Math.abs(y - ly) < 0.026 * S + 0.35 && x > a && x < b) return -0.06 * S; return 0; };
  // the question mark: a hook of capsules, a stem and a dot
  const qc = u(0.0, -0.10), QR = 0.135 * S, hw = 0.056 * S + 0.25, pts = [];
  for (let a = 205; a >= -48; a -= 9) pts.push([qc[0] + QR * Math.cos(a * Math.PI / 180), qc[1] - QR * Math.sin(a * Math.PI / 180)]);
  pts.push([qc[0] + 0.02 * S, qc[1] + 1.15 * QR], [qc[0] + 0.02 * S, qc[1] + 1.55 * QR]);
  const dot = [qc[0] + 0.02 * S, qc[1] + 2.25 * QR], dotR = hw * 1.25;
  const qmark = (x, y) => {
    let d = R.sdCircle(x, y, dot[0], dot[1], dotR);
    for (let i = 0; i + 1 < pts.length; i++) d = Math.min(d, R.sdSegment(x, y, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]) - hw);
    return d;
  };
  const side = [0.035 * S, 0.055 * S];
  const pearlBase = R.hex('#E8E8EE'), blueBase = R.hex('#2E4AD0'), goldBase = R.hex('#F2BC2E');
  return [
    { name: 'gold', z: 1, sdf: gold, r: 0.085 * S, albedo: (x, y) => (groove(x, y) ? R.hex('#B47A14') : goldBase), emboss: groove, ks: 0.5, shin: 30, side, sideShade: 0.5 },
    { name: 'pearl', z: 2, sdf: front, r: 0.1 * S, albedo: pearlBase, ks: 0.55, shin: 36, side, sideShade: 0.5 },
    { name: 'qmark', z: 3, sdf: qmark, r: 0.04 * S + 0.4, albedo: blueBase, ks: 0.6, shin: 40, ambient: 0.42, noShadow: true },
  ].map(o => ({ ...o, ...(d[o.name] || {}) }));
}

// ---------- labels ----------
function glyphTrim(ch) { // proportional cut of the 5x7 face (empty columns trimmed; space 3 px)
  if (ch === ' ') return { w: 3, rows: [] };
  const rows = G57[ch]; let a = 5, b = -1;
  for (const r of rows) for (let x = 0; x < r.length; x++) if (r[x] === '#') { a = Math.min(a, x); b = Math.max(b, x); }
  return { w: b - a + 1, rows: rows.map(r => r.slice(a, b + 1)) };
}
function labelWidth(s) { let w = 0; for (const ch of s) w += glyphTrim(ch).w + 1; return w - 1; }
function label(c, s, x, capTop, tok) {
  for (const ch of s) { const g = glyphTrim(ch); g.rows.forEach((row, j) => { for (let i = 0; i < row.length; i++) if (row[i] === '#') c.set(x + i, capTop + j, tok); }); x += g.w + 1; }
}
// Letter-spaced 2-px capitals, 9 rows (the Find Wizard picture captions). Drawn for Deep32 Library.
const CAPS9 = {
  A: ['..###..', '..###..', '.##.##.', '.##.##.', '.##.##.', '##...##', '#######', '##...##', '##...##'],
  S: ['.#####', '######', '##....', '###...', '.####.', '...###', '....##', '######', '#####.'],
  K: ['##...##', '##..##.', '##.##..', '####...', '###....', '####...', '##.##..', '##..##.', '##...##'],
};
function spaced(c, word, centreX, top, pitch, tok) {
  const n = word.length;
  [...word].forEach((ch, i) => {
    const g = CAPS9[ch], gx = Math.round(centreX + (i - (n - 1) / 2) * pitch - g[0].length / 2);
    g.forEach((row, j) => { for (let k = 0; k < row.length; k++) if (row[k] === '#') c.set(gx + k, top + j, tok); });
  });
}

// ---------- the navy button square of the title-screen choices (normal / pressed / raised) ----------
function navySquare(c, x0, y0, state) {
  const x1 = x0 + 20, y1 = y0 + 20, ramp = UI.navyRamp;
  const inner = state === 'pressed' ? [x0 + 3, y0 + 3] : [x0 + 1, y0 + 1];
  for (let y = inner[1]; y < y1; y++) for (let x = inner[0]; x < x1; x++) {
    const t = (y - inner[1]) / Math.max(1, y1 - 1 - inner[1]) * (ramp.length - 1), k = Math.floor(t), f = t - k;
    c.set(x, y, ramp[Math.min(ramp.length - 1, k + (f > R.ign(x * 3, y) ? 1 : 0))]);
  }
  c.box(x0, y0, x1, y1, UI.navyEdge);
  if (state === 'pressed') { c.rect(x0, y0, x1, y0 + 2, UI.navyEdge); c.rect(x0, y0, x0 + 2, y1, UI.navyEdge); }
  if (state === 'hot') {
    c.hl(x0 + 1, x1 - 2, y0 + 1, UI.navyHi); c.vl(x0 + 1, y0 + 1, y1 - 2, UI.navyHi2);
    c.vl(x1 - 1, y0 + 1, y1, UI.navyEdge); c.hl(x0 + 1, x1, y1 - 1, UI.navyEdge);
  }
}

// ---------- original cloud texture for the opaque title tile (no disc pixels) ----------
function cloudBg(seed = 26) {
  let s = seed; const rnd = () => { s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0; return s / 4294967296; };
  const N = 12, lat = Array.from({ length: N * N }, () => rnd()), sm = t => t * t * (3 - 2 * t);
  const vn = (x, y, cell) => {
    const gx = x / cell, gy = y / cell, x0 = Math.floor(gx), y0 = Math.floor(gy), fx = sm(gx - x0), fy = sm(gy - y0);
    const v = (a, b) => lat[((b % N) + N) % N * N + ((a % N) + N) % N];
    return R.mix(R.mix(v(x0, y0), v(x0 + 1, y0), fx), R.mix(v(x0, y0 + 1), v(x0 + 1, y0 + 1), fx), fy);
  };
  return (x, y) => {
    const n = 0.6 * vn(x, y, 23) + 0.4 * vn(x + 51, y + 17, 9), t = R.ign(x, y);
    if (n > 0.66 && t < (n - 0.66) * 2.6) return t < 0.18 ? UI.skyWarm : UI.white;
    if (n < 0.42 && t < (0.42 - n) * 2.4) return t < 0.33 ? UI.skyGrey : UI.skyBlue;
    return UI.sky;
  };
}

// ---------- the three pieces ----------
const ITEMS = [];
function add(o) { ITEMS.push({ group: 'd32library', rule: 'brand', formats: ['pm'], preview: null, library: true, ...o }); }

function titleTile(c, opaque) {
  const FW = 98, bg = opaque ? cloudBg() : () => null, S = 34;
  const states = ['normal', 'pressed', 'hot'];
  states.forEach((st, f) => {
    const dx = st === 'pressed' ? 2 : 0, objs = scene(S, 30 + dx, 23 + dx);
    const Rr = R.render(FW, 67, objs, { ss: 4 });
    const layers = [];
    if (st === 'hot') {
      const g = R.blur(Rr.cast, FW, 67, 3, 3);
      layers.push({ kind: 'glow', a: g.map(v => Math.min(1, v * 2.2)), levels: UI.glow, gain: 0.95, seed: 7 });
    } else {
      const sh = R.blur(R.shift(Rr.cast, FW, 67, st === 'pressed' ? -1 : -2, st === 'pressed' ? 2 : 3), FW, 67, 2, 3);
      layers.push({ kind: 'shadow', a: sh, levels: UI.shadowLight, gain: 0.85 });
    }
    R.compose(c, { R: Rr, x0: f * FW, y0: 0, bg: (x, y) => bg(x, y), matte: UI.sky, layers, palette: PALETTE });
    navySquare(c, f * FW + 41, 27, st);
    const text = 'Ask Deep32', w = labelWidth(text);
    label(c, text, f * FW + Math.round((FW - w) / 2), 55, UI.titleLabel);
  });
}
add({ file: 'ask-room', w: 294, h: 67, title: 'Deep32 Library "Ask" room tile: 3 frames of 98x67 (normal, pressed, hot), transparent ground',
  ref: 'SCREENS.md 4 (RESEARCH 294x67 = 3 x 98x67); 00-UPDATE items 40-41', frames: [98, 67, 3], draw: c => titleTile(c, false) });
add({ file: 'ask-room-opaque', w: 294, h: 67, title: 'Deep32 Library "Ask" room tile on an original cloud ground (standalone fallback)',
  ref: 'SCREENS.md 4; 00-UPDATE items 40-41', frames: [98, 67, 3], draw: c => titleTile(c, true) });

add({ file: 'ask-icon', w: 126, h: 61, title: 'Deep32 Library "Ask" feature icon: 2 frames of 63x61 (normal on the dark ground, selected on taupe)',
  ref: 'SCREENS.md 3 (Features menu icons, |Atlas_icon ...); ATLAS_ICON 126x61', frames: [63, 61, 2],
  draw: c => {
    const FW = 63;
    for (let f = 0; f < 2; f++) {
      const x0 = f * FW;
      c.rect(x0, 0, x0 + FW - 1, 60, UI.ground);
      if (f === 1) c.rect(x0, 1, x0 + FW - 1, 58, UI.taupe);
      const Rr = R.render(FW, 61, scene(31, 26, 21), { ss: 4 });
      const sh = R.blur(R.shift(Rr.cast, FW, 61, 0, 3), FW, 61, 2, 3);
      const ground = f === 1 ? UI.taupe : UI.ground;
      R.compose(c, { R: Rr, x0, y0: 0, bg: (x, y) => (y >= 1 && y <= 58 ? ground : UI.ground), matte: ground,
        layers: [{ kind: 'shadow', a: sh, levels: f === 1 ? UI.shadowTaupe : UI.shadowDark, gain: 0.9, seed: f * 5 }], palette: [...PALETTE, ...UI.shadowTaupe] });
      c.hl(x0, x0 + FW - 1, 59, UI.ground); c.hl(x0, x0 + FW - 1, 60, UI.ground); c.hl(x0 + 3, x0 + FW - 4, 60, UI.taupe);
      if (f === 1) c.hl(x0, x0 + FW - 1, 0, UI.ground);
      const w = labelWidth('Ask');
      label(c, 'Ask', x0 + Math.round((FW - w) / 2), 48, f === 1 ? UI.black : UI.labelLight);
    }
  } });

add({ file: 'fw-ask', w: 127, h: 161, title: 'Deep32 Library Find-Wizard-style "Ask" picture (127x161, the left panel of the Ask room)',
  ref: 'SCREENS.md 4-5 (FW_*_BMP 127x161, Find Wizard panel (127,0)); 00-UPDATE item 41 ("in the Find Wizard\'s visual language")',
  draw: c => {
    c.rect(0, 0, 126, 160, UI.ground);
    const Rr = R.render(127, 161, scene(52, 55, 57), { ss: 4 });
    const sh = R.blur(R.shift(Rr.cast, 127, 161, 1, 4), 127, 161, 3, 3);
    R.compose(c, { R: Rr, bg: () => UI.ground, matte: UI.ground, layers: [{ kind: 'shadow', a: sh, levels: UI.shadowDark, gain: 0.9 }], palette: PALETTE });
    spaced(c, 'ASK', 62, 100, 25, UI.taupe);
  } });

module.exports = { items: () => ITEMS, UI, PALETTE, scene, labelWidth, CAPS9 };
