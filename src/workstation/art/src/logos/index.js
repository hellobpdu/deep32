'use strict';
// Deep32/UX operating-system logo shortlist (LOOK-SOUND 5, DISTRO-DESIGN 6.9 revision 2, logo-grids.json).
// Four directions, each at 16/32/48 in two colourings:
//   cde   - CDE symbols (frame, plate and field follow the palette), rule 'cde'
//   brand - the direction's fixed brand palette (max 16 colours), rule 'brand'
// plus a 224x224 dtlogin emblem per direction (brand), the Bezel Badge wordmark "DEEP32/UX" (cap 32, <= 360 wide) and
// 64x64 1-bit boot emblems for C and D. The recommendation (DECISIONS.md, DISTRO-DESIGN D-table): C as the emblem,
// B's lettering as the wordmark. Everything is drawn natively at each size (no scaling). the owner picks at P1-G10.
const { F3x5, F5x7 } = require('../../lib/font');
const { bayer } = require('../../lib/canvas');
const { canon } = require('../../lib/cde');

const L = [];
function add(o) { L.push({ group: 'logos', rule: 'cde', formats: ['pm'], ...o }); }

// ---------- palettes per direction: role -> token ----------
const PAL = {
  A: {
    cde: { W: 'ts', K: 'bs', 1: 'g1', 2: 'g2', 3: 'g3', 4: 'g4', 5: 'g5', 6: 'g6', 7: 'g7', 8: 'g8', L: 'w', M: 'y', Ms: 'g6', Mh: 'w', R: 'sc' },
    brand: { W: '#E7EFF7', K: '#08183A', 1: '#B5C7DE', 2: '#94ADCE', 3: '#7394BD', 4: '#5A7BA5', 5: '#426394', 6: '#29497B', 7: '#18315A', 8: '#102142', L: '#FFFFFF', M: '#E7B552', Ms: '#A57B29', Mh: '#FFF1C8', R: '#B5C7DE' },
  },
  B: {
    cde: { W: 'ts', P: 'bg', Pb: 'bg', K: 'bs', e: 'g8', el: 'g1', s1: '#8998AA', s2: '#C6B2A8', s3: '#4992A7', s4: '#B7878D', sw: 'g3', sk: 'g7', S: 'bs' },
    brand: { W: '#EFEFEF', P: '#B5B5BD', Pb: '#C6C6CE', K: '#424A52', e: '#29313F', el: '#E7E7EF', s1: '#8998AA', s2: '#C6B2A8', s3: '#4992A7', s4: '#B7878D', sw: '#DEDEE6', sk: '#5A5F66', S: '#5A5F66' },
  },
  C: {
    cde: { W: 'ts', K: 'bs', d: 'g7', k: 'g8', A: 'y', Ah: 'w', n: 'g3', nh: 'g1', l: 'g1', s: 'g5', G: 'g4', x: 'r', glow: 'y' },
    brand: { W: '#C6CED6', K: '#08183A', d: '#29313F', k: '#101418', A: '#FFB531', Ah: '#FFE7A5', n: '#ADB5C6', nh: '#E7EFF7', l: '#E7EFF7', s: '#1C222C', G: '#39A54A', x: '#D84A3A', glow: '#FFE7A5' },
  },
  D: {
    cde: { W: 'ts', P: 'bg', K: 'bs', d: 'g8', A: 'r', o: 'g7', g: 'g', r: 'g5', v: 'g7', e: 'g8', el: 'g1', glow: 'r' },
    brand: { W: '#EFEFEF', P: '#B5B5BD', K: '#424A52', d: '#1A1A12', A: '#FFB531', o: '#3A3424', g: '#39A54A', r: '#4A4A3A', v: '#5A5F66', e: '#29313F', el: '#E7E7EF', glow: '#FFE7A5' },
  },
};
const B_STRIPE = ['#8998AA', '#C6B2A8', '#4992A7', '#B7878D'];

function stamp(c, rows, P, map) {
  rows.forEach((row, y) => { for (let x = 0; x < row.length; x++) { const ch = row[x]; if (ch === '.') continue; c.set(x, y, P[map[ch] || ch]); } });
}

// ---------- A Strata ----------
const A16 = [
  'WWWWWWWWWWWWWWWK', 'W111111L1111111K', 'W111111L1111111K', 'W222222L2222222K', 'W222222L2222222K', 'W333333L3333333K',
  'W333333L3333333K', 'W444444L4444444K', 'W444444L4444444K', 'W555555L5555555K',
  'W555555M5555555K', 'W66666MMM666666K', 'W66666MMM666666K', 'W7777MMMMM77777K', // revision 2: a tapered sounding lead
  'W77777777777777K', 'KKKKKKKKKKKKKKKK'];
function lead(c, P, cx, top, widths) { // tapered lead, flat bottom; centre column cx (half-pixel if even widths)
  widths.forEach((w, i) => {
    const x0 = Math.ceil(cx - w / 2), y = top + i;
    c.hl(x0, x0 + w - 1, y, P.M);
    c.set(x0, y, i === 0 ? P.Mh : P.Mh); if (w > 2) c.set(x0 + w - 1, y, P.Ms);
  });
  const last = widths.length - 1, w = widths[last], x0 = Math.ceil(cx - w / 2);
  c.hl(x0, x0 + w - 1, top + last, P.Ms);
}
function strata(c, P, n) {
  const b = n >= 48 ? 2 : n >= 32 ? 2 : 1;
  c.panel(0, 0, n - 1, n - 1, b, P[8], P.W, P.K);
  if (n === 32) {
    for (let k = 0; k < 8; k++) c.rect(b, 2 + 3 * k, n - 1 - b, 4 + 3 * k, P[k + 1]);
    c.rect(b, 26, n - 1 - b, 29, P[8]); c.hl(b, n - 1 - b, 26, P[7]);
    for (let y = 2; y <= 25; y += 4) c.set(2, y, P.W); c.hl(2, 3, 2, P.W); c.hl(2, 3, 18, P.W);
    c.rect(13, 2, 14, 11, P.L); c.vl(14, 12, 21, P.L);
    lead(c, P, 14.5, 21, [1, 3, 3, 5, 5]);
  } else if (n === 48) {
    c.rect(b, 2, n - 1 - b, 3, P[1]);
    for (let k = 0; k < 8; k++) { c.rect(b, 4 + 4 * k, n - 1 - b, 7 + 4 * k, P[k + 1]); if (k) c.hl(b, n - 1 - b, 4 + 4 * k, P[k]); }
    c.rect(b, 36, n - 1 - b, 45, P[8]);
    for (let x = b; x <= n - 1 - b; x++) for (let y = 38; y <= 45; y += 3) if (((x + (y >> 1) * 3) % 6) === 0) c.set(x, y, P[6]);
    for (let y = 4; y <= 35; y += 4) c.set(2, y, P.W); for (let y = 4; y <= 35; y += 16) c.hl(2, 4, y, P.W);
    c.rect(21, 2, 22, 20, P.L); c.vl(22, 21, 30, P.L);
    lead(c, P, 22.5, 30, [1, 3, 3, 5, 5, 7, 7]);
  } else { // 224 login: bands with a Bayer 4x4 transition, sonar arcs round the lead, a long line
    c.panel(0, 0, n - 1, n - 1, 6, P[8], P.W, P.K);
    const top = 10, bandH = 22;
    for (let y = 6; y < n - 6; y++) for (let x = 6; x < n - 6; x++) {
      const t = (y - top) / bandH; let k = Math.max(0, Math.min(7, Math.floor(t)));
      const f = t - Math.floor(t);
      if (y >= top && t < 8 && f > 0.75 && k < 7 && (f - 0.75) * 4 > bayer(x, y, 4)) k++;
      c.set(x, y, y < top ? P[1] : t >= 8 ? P[8] : P[k + 1]);
    }
    for (let y = top; y < top + 8 * bandH; y += 11) c.hl(6, y % 44 === top % 44 ? 14 : 10, y, P.W);
    const cx = 112; c.rect(cx - 2, 6, cx + 1, 110, P.L); c.rect(cx - 1, 111, cx, 150, P.L);
    const ws = [3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 21, 23, 23, 25, 25, 27, 27, 27];
    ws.forEach((w, i) => { const y = 150 + i, x0 = cx - Math.floor(w / 2) - 1; c.hl(x0, x0 + w, y, P.M); c.hl(x0, x0 + 2, y, P.Mh); c.hl(x0 + w - 2, x0 + w, y, P.Ms); });
    c.hl(cx - 15, cx + 13, 168, P.Ms); c.hl(cx - 15, cx + 13, 169, P.Ms);
    for (const r of [26, 38, 50]) c.ring(cx - r, 160 - r, cx + r - 1, 160 + r - 1, 1, P.R, (dx, dy) => dy > -r * 0.2 && Math.abs(dx) > 16);
  }
}

// ---------- B Bezel Badge ----------
const B16 = ['................', '................', '................', 'WWWWWWWWWWWWWWWK', 'WPPPPPPPPPPPPPPK',
  'WPPeePPeeePeeePK', 'WPPePePPPePPPePK', 'WPPePePPeePeeePK', 'WPPePePPPePePPPK', 'WPPeePPeeePeeePK', 'WPPPPPPPPPPPPPPK',
  'W11112223333444K', 'KKKKKKKKKKKKKKKK', '................', '................', '................'];
const G59 = { // engraved 5x9 figures for the 32 px plate
  D: ['####.', '#...#', '#...#', '#...#', '#...#', '#...#', '#...#', '#...#', '####.'],
  3: ['####.', '....#', '....#', '....#', '.###.', '....#', '....#', '....#', '####.'],
  2: ['####.', '....#', '....#', '....#', '.###.', '#....', '#....', '#....', '#####'],
};
const G713 = { // engraved 7x13 figures for the 48 px plate (2 px strokes)
  D: ['#####..', '######.', '##..###', '##...##', '##...##', '##...##', '##...##', '##...##', '##...##', '##...##', '##..###', '######.', '#####..'],
  3: ['######.', '#######', '.....##', '.....##', '.....##', '..####.', '..####.', '.....##', '.....##', '.....##', '.....##', '#######', '######.'],
  2: ['######.', '#######', '.....##', '.....##', '.....##', '.#####.', '######.', '##.....', '##.....', '##.....', '##.....', '#######', '#######'],
};
function engrave(c, P, glyph, ox, oy) {
  glyph.forEach((row, y) => { for (let x = 0; x < row.length; x++) if (row[x] === '#') c.set(ox + x, oy + y, P.e); });
  // light catches the far wall of the cut: a 1 px light line one pixel down and right of every stroke
  const ink = (x, y) => glyph[y] && glyph[y][x] === '#';
  glyph.forEach((row, y) => { for (let x = 0; x < row.length; x++) if (ink(x, y) && !ink(x + 1, y + 1)) c.set(ox + x + 1, oy + y + 1, P.el); });
  glyph.forEach((row, y) => { for (let x = 0; x < row.length; x++) if (ink(x, y)) c.set(ox + x, oy + y, P.e); });
}
function screw(c, P, x, y, size = 3) {
  c.oval(x, y, x + size - 1, y + size - 1, P.sw); c.set(x + size - 1, y + size - 1, P.sk); c.set(x + size - 1, y + size - 2, P.sk);
  c.line(x, y + size - 1, x + size - 1, y, P.sk);
}
function bezel(c, P, n) {
  if (n === 32) {
    c.panel(0, 6, 31, 25, 1, P.P, P.W, P.K);
    ['D', '3', '2'].forEach((g, i) => engrave(c, P, G59[g], 8 + i * 6, 9));
    [[1, 7], [8, 15], [16, 23], [24, 30]].forEach(([a, b], i) => c.rect(a, 22, b, 24, P[`s${i + 1}`]));
    screw(c, P, 2, 13); screw(c, P, 27, 13);
  } else if (n === 48) {
    c.panel(0, 10, 47, 37, 2, P.P, P.W, P.K);
    for (let y = 12; y <= 35; y += 2) c.hl(2, 45, y, P.Pb);
    ['D', '3', '2'].forEach((g, i) => engrave(c, P, G713[g], 11 + i * 9, 13));
    [[2, 11], [12, 23], [24, 35], [36, 45]].forEach(([a, b], i) => c.rect(a, 28, b, 30, P[`s${i + 1}`]));
    c.text(F3x5, 'UX', 38, 31, P.e);
    screw(c, P, 3, 17, 4); screw(c, P, 41, 17, 4);
  }
}

// ---------- C Root Bridge ----------
const C16 = ['WWWWWWWWWWWWWWWK', 'WddddddddddddddK', 'WddddkkkkkkddddK', 'WddddkAAAAkddddK', 'WddddkAAAAkddddK',
  'WddddkkkkkkddddK', 'WddddddllddddddK', 'WddddddllddddddK', 'WddllllllllllddK', 'WddllddddddllddK',
  'WkkkkkkddkkkkkkK', 'WknnnnkddknnnnkK', 'WknnnnkddknnnnkK', 'WkkkkkkddkkkkkkK',
  'WddldldldldldddK', // revision 2: a dotted blocked link under both leaves
  'KKKKKKKKKKKKKKKK'];
function node(c, P, x0, y0, x1, y1, face, hi, leds) {
  c.rect(x0, y0, x1, y1, face); c.box(x0, y0, x1, y1, P.k);
  c.hl(x0 + 1, x1 - 1, y0 + 1, hi); c.vl(x0 + 1, y0 + 1, y1 - 1, hi);
  for (const x of leds) c.set(x, y1 - 1, P.G);
}
function rootBridge(c, P, n) {
  if (n === 32) {
    c.panel(0, 0, 31, 31, 2, P.d, P.W, P.K);
    node(c, P, 10, 4, 21, 11, P.A, P.Ah, [12, 14, 17, 19]);
    c.rect(15, 12, 16, 15, P.l); c.rect(7, 16, 24, 17, P.l); c.rect(7, 18, 8, 19, P.l); c.rect(23, 18, 24, 19, P.l);
    node(c, P, 3, 20, 12, 26, P.n, P.nh, [5, 7]); node(c, P, 19, 20, 28, 26, P.n, P.nh, [21, 23]);
    c.vl(7, 27, 28, P.l); c.vl(24, 27, 28, P.l); c.dotted(8, 28, 23, 28, P.l);
  } else if (n === 48) {
    c.panel(0, 0, 47, 47, 2, P.d, P.W, P.K);
    node(c, P, 15, 5, 32, 17, P.A, P.Ah, [17, 19, 21, 26, 28, 30]);
    c.text(F3x5, 'R', 22, 8, P.k);
    // 3 px links with a 1 px cable shadow
    c.rect(23, 18, 25, 23, P.l); c.vl(26, 18, 23, P.s);
    c.rect(10, 24, 37, 26, P.l); c.hl(11, 38, 27, P.s);
    c.rect(10, 27, 12, 29, P.l); c.rect(35, 27, 37, 29, P.l); c.vl(13, 28, 29, P.s); c.vl(38, 28, 29, P.s);
    node(c, P, 4, 30, 18, 39, P.n, P.nh, [6, 8, 10]); node(c, P, 29, 30, 43, 39, P.n, P.nh, [31, 33, 35]);
    c.rect(10, 40, 11, 42, P.l); c.rect(36, 40, 37, 42, P.l);
    c.dotted(12, 42, 35, 42, P.l, 2, 2); c.dotted(12, 43, 35, 43, P.l, 2, 2);
  } else { // 224 login (brand): lit root with a Bayer glow, BPDU pulse arcs, the blocked bar across the dotted link
    c.panel(0, 0, n - 1, n - 1, 6, P.d, P.W, P.K);
    const glow = (x0, y0, x1, y1, r) => c.shape(x0 - r, y0 - r, x1 + r, y1 + r, (x, y) => {
      const dx = Math.max(x0 - x, 0, x - x1), dy = Math.max(y0 - y, 0, y - y1), d = Math.hypot(dx, dy);
      return d > 0 && d <= r && (1 - d / r) * 0.9 > bayer(x, y, 4);
    }, P.glow);
    glow(76, 24, 147, 71, 8);
    const box = (x0, y0, x1, y1, face, hi, leds) => {
      c.rect(x0, y0, x1, y1, face); c.box(x0, y0, x1, y1, P.k); c.box(x0 + 1, y0 + 1, x1 - 1, y1 - 1, P.k);
      c.rect(x0 + 2, y0 + 2, x1 - 2, y0 + 3, hi); c.rect(x0 + 2, y0 + 2, x0 + 3, y1 - 2, hi);
      for (const x of leds) c.rect(x, y1 - 7, x + 3, y1 - 4, P.G);
    };
    box(76, 24, 147, 71, P.A, P.Ah, [86, 98, 122, 134]);
    // links 8 px with a 2 px shadow
    c.rect(108, 72, 115, 95, P.l); c.rect(116, 74, 117, 95, P.s);
    c.rect(48, 96, 175, 103, P.l); c.rect(50, 104, 177, 105, P.s);
    c.rect(48, 104, 55, 127, P.l); c.rect(168, 104, 175, 127, P.l); c.rect(56, 106, 57, 127, P.s); c.rect(176, 106, 177, 127, P.s);
    box(24, 128, 79, 167, P.n, P.nh, [34, 44]); box(144, 128, 199, 167, P.n, P.nh, [154, 164]);
    // BPDU pulse: two concentric arcs from the root towards each leaf
    for (const r of [44, 54]) c.ring(112 - r, 72 - r, 111 + r, 71 + r, 1, P.glow, (dx, dy) => dy > 8 && Math.abs(dx) > 20 && Math.abs(dx) < r - 4 && !(Math.abs(dx) < 12));
    // blocked link: stubs, a dotted 4 px line under both leaves, and the blocked bar across it
    c.rect(48, 168, 51, 187, P.l); c.rect(172, 168, 175, 187, P.l);
    for (let x = 48; x <= 175; x += 8) c.rect(x, 184, Math.min(x + 3, 175), 187, P.l);
    c.rect(108, 176, 115, 195, P.x); c.box(108, 176, 115, 195, P.k);
  }
}

// ---------- D Operator Panel ----------
const D16 = ['WWWWWWWWWWWWWWWK', 'WPPPPPPPPPPPPPPK', 'WPKKKKKKKKKKKKPK', 'WPKddddddddddWPK', 'WPKddddddddddWPK',
  'WPKdAAAddAAAdWPK', 'WPKdooAddooAdWPK', 'WPKdoAAddAAAdWPK', 'WPKdooAddAoodWPK', 'WPKdAAAddAAAdWPK',
  'WPKddddddddddWPK', 'WPKddddddddddWPK', 'WPKWWWWWWWWWWWPK', 'WPPPPPPPPPPPPgPK', 'WPPPPPPPPPPPPPPK', 'KKKKKKKKKKKKKKKK'];
const DOT57 = { // 5x7 dot-matrix characters (the period LED/VFD style)
  D: ['####.', '#...#', '#...#', '#...#', '#...#', '#...#', '####.'],
  3: ['#####', '....#', '...#.', '..##.', '....#', '#...#', '.###.'],
  2: ['.###.', '#...#', '....#', '...#.', '..#..', '.#...', '#####'],
  U: ['#...#', '#...#', '#...#', '#...#', '#...#', '#...#', '.###.'],
  X: ['#...#', '#...#', '.#.#.', '..#..', '.#.#.', '#...#', '#...#'],
};
function dots(c, P, str, ox, oy, pitch, dot, gapChars, glowR = 0) {
  let x = ox;
  for (const ch of str) {
    const g = DOT57[ch];
    g.forEach((row, j) => { for (let i = 0; i < 5; i++) {
      const px = x + i * pitch, py = oy + j * pitch, lit = row[i] === '#';
      if (lit && glowR) { const bgTok = canon(P.d); c.shape(px - glowR, py - glowR, px + dot - 1 + glowR, py + dot - 1 + glowR, (xx, yy) => c.get(xx, yy) === bgTok && ((xx + yy) & 1) === 0, P.glow); }
      c.rect(px, py, px + dot - 1, py + dot - 1, lit ? P.A : P.o);
    } });
    x += 5 * pitch + gapChars;
  }
}
function opPanel(c, P, n) {
  if (n === 32) {
    c.panel(0, 0, 31, 31, 1, P.P, P.W, P.K);
    c.rect(3, 5, 28, 21, P.d); c.hl(3, 28, 5, P.K); c.vl(3, 5, 21, P.K); c.hl(3, 28, 21, P.W); c.vl(28, 5, 21, P.W);
    c.hl(4, 27, 6, P.r);
    dots(c, P, '32', 6, 7, 2, 1, 3);
    c.rect(24, 25, 25, 26, P.g); c.set(26, 26, P.g);
    c.hl(4, 13, 25, P.v); c.hl(4, 13, 27, P.v);
  } else if (n === 48) {
    c.panel(0, 0, 47, 47, 2, P.P, P.W, P.K);
    c.rect(3, 6, 44, 27, P.d); c.hl(3, 44, 6, P.K); c.vl(3, 6, 27, P.K); c.hl(3, 44, 27, P.W); c.vl(44, 6, 27, P.W);
    c.hl(4, 43, 7, P.r);
    dots(c, P, 'D32', 7, 10, 2, 1, 4);
    c.hl(5, 14, 32, P.v); c.hl(5, 14, 34, P.v); c.hl(5, 14, 36, P.v);
    c.text(F3x5, 'UX', 37, 32, P.e);
    c.rect(20, 33, 21, 34, P.g);
  } else { // 224 login (brand)
    c.panel(0, 0, n - 1, n - 1, 6, P.P, P.W, P.K);
    for (let y = 8; y < n - 8; y += 2) c.hl(6, n - 7, y, P.W === '#EFEFEF' ? '#C6C6CE' : P.P);
    const wx0 = 22, wy0 = 30, wx1 = 201, wy1 = 151;
    c.rect(wx0, wy0, wx1, wy1, P.d);
    c.rect(wx0 - 3, wy0 - 3, wx1 + 3, wy0 - 1, P.K); c.rect(wx0 - 3, wy0 - 3, wx0 - 1, wy1 + 3, P.K);
    c.rect(wx0 - 3, wy1 + 1, wx1 + 3, wy1 + 3, P.W); c.rect(wx1 + 1, wy0 - 3, wx1 + 3, wy1 + 3, P.W);
    c.hl(wx0, wx1, wy0 + 1, P.r);
    dots(c, P, 'D32', 52, 42, 6, 4, 12, 1);
    dots(c, P, 'UX', 82, 100, 6, 4, 12, 1);
    for (let i = 0; i < 4; i++) c.rect(22, 172 + i * 8, 101, 174 + i * 8, P.v);
    c.rect(180, 176, 187, 183, P.g); c.box(179, 175, 188, 184, P.K);
    c.text(F5x7, 'POWER', 166, 190, P.e);
  }
}

// ---------- wordmark: "DEEP32/UX" in engraved extended capitals (B's lettering, the recommended wordmark) ----------
function sdBox(px, py, cx, cy, hx, hy) { const dx = Math.abs(px - cx) - hx, dy = Math.abs(py - cy) - hy; return Math.hypot(Math.max(dx, 0), Math.max(dy, 0)) + Math.min(Math.max(dx, dy), 0); }
// rounded box with per-corner radii [tr, br, tl, bl] (Inigo Quilez's sdRoundBox)
function sdRBox(px, py, cx, cy, hx, hy, r) {
  const x = px - cx, y = py - cy;
  let rr = x > 0 ? (y > 0 ? r[1] : r[0]) : (y > 0 ? r[3] : r[2]);
  const qx = Math.abs(x) - hx + rr, qy = Math.abs(y) - hy + rr;
  return Math.min(Math.max(qx, qy), 0) + Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) - rr;
}
function outline(x0, y0, x1, y1, r, s) { // ink of a stroked rounded box (x1, y1 exclusive), stroke s inside
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, hx = (x1 - x0) / 2, hy = (y1 - y0) / 2;
  return (x, y) => { const d = sdRBox(x, y, cx, cy, hx, hy, r); return d <= 0 && d > -s; };
}
const rect = (x0, y0, x1, y1) => (x, y) => x >= x0 && x < x1 && y >= y0 && y < y1;
const seg = (ax, ay, bx, by, hw) => (x, y) => {
  const pax = x - ax, pay = y - ay, bax = bx - ax, bay = by - ay;
  const h = Math.max(0, Math.min(1, (pax * bax + pay * bay) / (bax * bax + bay * bay)));
  // square-ended stroke: perpendicular distance only, clipped to the segment's extent
  const t = (pax * bax + pay * bay) / (bax * bax + bay * bay);
  return t >= 0 && t <= 1 && Math.hypot(pax - bax * h, pay - bay * h) <= hw;
};
const or = (...f) => (x, y) => f.some(g => g(x, y));
const and = (a, b) => (x, y) => a(x, y) && b(x, y);
const not = a => (x, y) => !a(x, y);
function extGlyphs(H, s) { // H = cap height, s = stroke
  const W = Math.round(H * 1.06), R = Math.round(H * 0.34), m = Math.floor(H / 2) - Math.floor(s / 2);
  return {
    D: { w: W, ink: or(rect(0, 0, s, H), outline(0, 0, W, H, [R, R, 0, 0], s)) },
    E: { w: W - 4, ink: or(rect(0, 0, s, H), rect(0, 0, W - 4, s), rect(0, m, W - 9, m + s), rect(0, H - s, W - 4, H)) },
    P: { w: W, ink: or(rect(0, 0, s, H), outline(0, 0, W, m + s, [R * 0.7, R * 0.7, 0, 0], s)) },
    3: { w: W, ink: or(and(outline(0, 0, W, m + s, [R * 0.7, R * 0.7, 0, 0], s), not(rect(0, s, W / 2, m))),
      and(outline(0, m, W, H, [R * 0.75, R * 0.75, 0, 0], s), not(rect(0, m + s, W / 2, H - s))), rect(Math.round(W * 0.3), m, W / 2 + 1, m + s)) },
    2: { w: W, ink: or(and(outline(0, 0, W, m + s, [R * 0.7, 0, 0, 0], s), not(rect(0, s, W / 2, H))),
      and(outline(0, m, W, H, [0, 0, R * 0.7, 0], s), not(rect(W / 2, m + s, W, H - s))), rect(0, H - s, W, H)) },
    '/': { w: Math.round(H / Math.tan(Math.PI / 3)) + 2, ink: seg(1, H - 0.5, Math.round(H / Math.tan(Math.PI / 3)) + 1, 0.5, 1.05) },
    U: { w: W, ink: and(outline(0, -R - 2, W, H, [0, R, 0, R], s), rect(0, 0, W, H)) },
    X: { w: W, ink: and(or(seg(s / 2, 0, W - s / 2, H, s * 0.62), seg(W - s / 2, 0, s / 2, H, s * 0.62)), rect(0, 0, W, H)) },
  };
}
function wordmark(c, P, str, ox, oy, H, s, gap, slashTok) {
  const G = extGlyphs(H, s); let x = ox; const ink = new Set();
  for (const ch of str) {
    const g = G[ch];
    for (let j = 0; j < H; j++) for (let i = 0; i < g.w; i++) if (g.ink(i + 0.5, j + 0.5)) { c.set(x + i, oy + j, ch === '/' && slashTok ? slashTok : P.e); if (ch !== '/') ink.add((x + i) + ',' + (oy + j)); }
    x += g.w + gap;
  }
  for (const k of ink) { const [px, py] = k.split(',').map(Number); if (!ink.has((px + 1) + ',' + py) || !ink.has(px + ',' + (py + 1))) c.set(px, py, P.el); }
  return x - gap;
}
function wordWidth(str, H, s, gap) { const G = extGlyphs(H, s); let w = 0; for (const ch of str) w += G[ch].w + gap; return w - gap; }

// ---------- registration ----------
const DIRS = [
  { id: 'A', name: 'Strata', g16: A16, map: {}, draw: strata, ref: 'LOOK-SOUND 5.2 + DISTRO-DESIGN 6.9 rev 2 (tapered lead)' },
  { id: 'B', name: 'Bezel Badge', g16: B16, map: { 1: 's1', 2: 's2', 3: 's3', 4: 's4' }, draw: bezel, ref: 'LOOK-SOUND 5.3' },
  { id: 'C', name: 'Root Bridge', g16: C16, map: {}, draw: rootBridge, ref: 'LOOK-SOUND 5.4 + DISTRO-DESIGN 6.9 rev 2 (dotted blocked link); recommended emblem' },
  { id: 'D', name: 'Operator Panel', g16: D16, map: {}, draw: opPanel, ref: 'LOOK-SOUND 5.5 + DISTRO-DESIGN 6.9 rev 2 (CDE lit dots iconColor3)' },
];
const SZ = { t: 16, m: 32, l: 48 };
for (const d of DIRS) for (const kind of ['cde', 'brand']) for (const [sz, n] of Object.entries(SZ)) {
  const P = PAL[d.id][kind];
  add({
    group: kind === 'cde' ? 'logos' : 'logos/brand', file: `D32ux${d.id}.${sz}`, w: n, h: n, sizeLetter: sz,
    title: `Deep32/UX logo ${d.id} ${d.name}, ${n}x${n} (${kind === 'cde' ? 'CDE symbols' : 'brand palette'})`, ref: d.ref,
    rule: kind === 'cde' ? 'cde' : 'brand', maxColours: kind === 'brand' ? 16 : undefined,
    allow: d.id === 'B' ? B_STRIPE.map(h => h.toUpperCase()) : [], preview: ['Default', 8], formats: kind === 'cde' ? ['pm', 'bm', 'mask'] : ['pm'],
    draw: c => (n === 16 ? stamp(c, d.g16, P, d.map) : d.draw(c, P, n)),
  });
}
// 224x224 dtlogin emblems (brand). B's plate at 224 carries the wordmark lettering.
for (const d of DIRS) {
  add({
    group: 'logos/brand', file: `D32ux${d.id}.login`, w: 224, h: 224, title: `Deep32/UX logo ${d.id} ${d.name}, dtlogin 224x224 (brand)`,
    ref: d.ref, rule: 'brand', maxColours: 16, preview: ['Default', 3],
    draw: c => {
      const P = PAL[d.id].brand;
      if (d.id !== 'B') return d.draw(c, P, 224);
      // B at 224: the plate, orthographic, with "DEEP32/UX" engraved, the stripe with RESEARCH WORKSTATION, 4 screws
      c.rect(10, 62, 219, 165, P.S);
      c.panel(6, 58, 215, 161, 3, P.P, P.W, P.K);
      for (let y = 61; y <= 158; y += 2) c.hl(9, 212, y, P.Pb);
      let H = 30; while (wordWidth('DEEP32/UX', H, 3, 3) > 192) H--;
      const w = wordWidth('DEEP32/UX', H, 3, 3);
      wordmark(c, P, 'DEEP32/UX', Math.round(111 - w / 2), Math.round(90 - H / 2), H, 3, 3, P.s3);
      [[9, 59], [60, 110], [111, 161], [162, 212]].forEach(([a, b], i) => c.rect(a, 120, b, 132, P[`s${i + 1}`]));
      const t = 'RESEARCH WORKSTATION', tw = F5x7.width(t);
      c.text(F5x7, t, Math.round(111 - tw / 2), 123, P.e);
      for (const [x, y] of [[12, 64], [204, 64], [12, 148], [204, 148]]) screw(c, P, x, y, 6);
    },
  });
}
// The wordmark: "DEEP32/UX", cap 32, stroke 4, letter gap 6, engraved; CDE (letters follow iconGray8) and brand.
for (const kind of ['cde', 'brand']) {
  const W = wordWidth('DEEP32/UX', 32, 4, 6);
  add({
    group: kind === 'cde' ? 'logos' : 'logos/brand', file: 'D32uxB.word', w: W + 2, h: 34,
    title: `Deep32/UX wordmark "DEEP32/UX" (B's engraved extended capitals, cap 32, ${kind})`, ref: 'LOOK-SOUND 5.3 wordmark; recommended wordmark',
    rule: kind === 'cde' ? 'cde' : 'brand', maxColours: 16, preview: ['Default', 8], allow: ['#4992A7'],
    draw: c => wordmark(c, PAL.B[kind], 'DEEP32/UX', 1, 1, 32, 4, 6, kind === 'cde' ? 'g6' : '#4992A7'),
  });
}
// 64x64 1-bit boot emblems (console): D's dot matrix with 2x2 dots, and C's tree.
add({
  group: 'logos', file: 'D32uxD.boot', w: 64, h: 64, title: 'Deep32/UX boot emblem D (1-bit, 64x64): "D32" dot matrix, 2x2 dots',
  ref: 'LOOK-SOUND 5.5', rule: 'cde', formats: ['bm1'], preview: ['Default', 8],
  draw: c => { c.rect(0, 0, 63, 63, 'w'); const P = { A: 'k', o: 'w' }; dots(c, P, 'D32', 7, 22, 3, 2, 4); },
});
add({
  group: 'logos', file: 'D32uxC.boot', w: 64, h: 64, title: 'Deep32/UX boot emblem C (1-bit, 64x64): root bridge, two leaves, blocked link',
  ref: 'LOOK-SOUND 5.4', rule: 'cde', formats: ['bm1'], preview: ['Default', 8],
  draw: c => {
    c.rect(0, 0, 63, 63, 'w');
    const nodeBox = (x0, y0, x1, y1, fill) => { c.box(x0, y0, x1, y1, 'k'); c.box(x0 + 1, y0 + 1, x1 - 1, y1 - 1, 'k'); if (fill) c.rect(x0 + 3, y0 + 3, x1 - 3, y1 - 3, 'k'); };
    nodeBox(20, 4, 43, 19, true);
    c.rect(30, 20, 33, 28, 'k'); c.rect(8, 29, 55, 32, 'k'); c.rect(8, 33, 11, 38, 'k'); c.rect(52, 33, 55, 38, 'k');
    nodeBox(2, 39, 19, 52, false); nodeBox(44, 39, 61, 52, false);
    c.rect(9, 53, 10, 58, 'k'); c.rect(53, 53, 54, 58, 'k');
    for (let x = 9; x <= 54; x += 4) c.rect(x, 57, Math.min(x + 1, 54), 58, 'k');
  },
});

module.exports = { items: () => L, PAL, DOT57, extGlyphs, wordWidth };
