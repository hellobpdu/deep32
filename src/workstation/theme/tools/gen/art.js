'use strict';
// Deep32/UX art, generated deterministically as XPM with CDE's symbolic colours (CDE-SPEC 1.5), so it recolours
// with the palette like CDE's own art. Everything here is DRAFT art: shapes from LOOK-SOUND 6.2-6.4 and 7.1 where
// the design gives coordinates, a consistent placeholder family where it does not. The art lane (REPLAN B9,
// hand-drawn per size) replaces the icons; the backdrops and the emblem construction are final in method.
const xpm = require('../lib/xpm');

// Legend: one character per colour. Symbols resolve per colour set; c values are CDE's defaults (fallback).
const LEGEND = {
  ' ': { s: 'none', m: 'none', c: 'none' },
  b: { s: 'background', m: 'black', c: '#949494949494' },
  f: { s: 'foreground', m: 'white', c: '#000000000000' },
  T: { s: 'topShadowColor', m: 'white', c: '#bdbdbdbdbdbd' },
  B: { s: 'bottomShadowColor', m: 'black', c: '#636363636363' },
  s: { s: 'selectColor', m: 'white', c: '#737373737373' },
  1: { s: 'iconGray1', m: 'white', c: '#dededededede' }, 2: { s: 'iconGray2', m: 'white', c: '#bdbdbdbdbdbd' },
  3: { s: 'iconGray3', m: 'white', c: '#adadadadadad' }, 4: { s: 'iconGray4', m: 'white', c: '#949494949494' },
  5: { s: 'iconGray5', m: 'black', c: '#737373737373' }, 6: { s: 'iconGray6', m: 'black', c: '#636363636363' },
  7: { s: 'iconGray7', m: 'black', c: '#424242424242' }, 8: { s: 'iconGray8', m: 'black', c: '#212121212121' },
  k: { s: 'iconColor1', m: 'black', c: 'black' }, w: { s: 'iconColor2', m: 'white', c: 'white' },
  r: { s: 'iconColor3', m: 'black', c: 'red' }, g: { s: 'iconColor4', m: 'white', c: 'green' },
  u: { s: 'iconColor5', m: 'black', c: 'blue' }, y: { s: 'iconColor6', m: 'white', c: 'yellow' },
  c: { s: 'iconColor7', m: 'white', c: 'cyan' }, m: { s: 'iconColor8', m: 'black', c: 'magenta' },
  // fixed safety colours (DISTRO-DESIGN 6.5): never palette-derived, so no symbol
  A: { m: 'white', c: '#D9B04A' }, G: { m: 'white', c: '#7FBF7F' }, R: { m: 'black', c: '#D46A6A' },
};

// 3x5 and 5x7 engraving fonts (original pixel art).
const F3 = { A: '010101111101101', B: '110101110101110', C: '011100100100011', D: '110101101101110', E: '111100110100111',
  F: '111100110100100', G: '011100101101011', H: '101101111101101', I: '111010010010111', K: '101101110101101',
  L: '100100100100111', M: '101111111101101', N: '110101101101101', O: '010101101101010', P: '110101110100100',
  Q: '010101101110011', R: '110101110101101', S: '011100010001110', T: '111010010010010', U: '101101101101111',
  V: '101101101101010', W: '101101111111101', X: '101101010101101', Y: '101101010010010', Z: '111001010100111',
  0: '111101101101111', 1: '010110010010111', 2: '110001010100111', 3: '110001010001110', 4: '101101111001001',
  5: '111100110001110', 6: '011100111101111', 7: '111001010010010', 8: '111101111101111', 9: '111101111001110',
  '/': '001001010100100', '?': '110001010000010', '!': '010010010000010', '.': '000000000000010', '-': '000000111000000' };
const F5 = { 2: ['01110', '10001', '00001', '00110', '01000', '10000', '11111'], 6: ['00110', '01000', '10000', '11110', '10001', '10001', '01110'],
  M: ['10001', '11011', '10101', '10101', '10001', '10001', '10001'], O: ['01110', '10001', '10001', '10001', '10001', '10001', '01110'],
  G: ['01110', '10001', '10000', '10111', '10001', '10001', '01111'], P: ['11110', '10001', '10001', '11110', '10000', '10000', '10000'],
  R: ['11110', '10001', '10001', '11110', '10100', '10010', '10001'], L: ['10000', '10000', '10000', '10000', '10000', '10000', '11111'],
  C: ['01110', '10001', '10000', '10000', '10000', '10001', '01110'], N: ['10001', '11001', '10101', '10011', '10001', '10001', '10001'],
  X: ['10001', '10001', '01010', '00100', '01010', '10001', '10001'], W: ['10001', '10001', '10001', '10101', '10101', '11011', '10001'],
  B: ['11110', '10001', '10001', '11110', '10001', '10001', '11110'], I: ['01110', '00100', '00100', '00100', '00100', '00100', '01110'],
  A: ['01110', '10001', '10001', '11111', '10001', '10001', '10001'], E: ['11111', '10000', '10000', '11110', '10000', '10000', '11111'] };

// A symbol grid drawn in 48-unit design space and scaled to n pixels (48, 32 or 16), lines at least 1 px.
class Grid {
  constructor(n) { this.n = n; this.g = Array.from({ length: n }, () => new Array(n).fill(' ')); }
  sc(v) { return Math.floor(v * this.n / 48); }
  set(x, y, c) { if (x >= 0 && y >= 0 && x < this.n && y < this.n) this.g[y][x] = c; }
  // inclusive design-space box
  box(x0, y0, x1, y1) { const a = this.sc(x0), b = this.sc(y0); return [a, b, Math.max(a, this.sc(x1 + 1) - 1), Math.max(b, this.sc(y1 + 1) - 1)]; }
  rect(x0, y0, x1, y1, c) { const [a, b, e, f] = this.box(x0, y0, x1, y1); for (let y = b; y <= f; y++) for (let x = a; x <= e; x++) this.set(x, y, c); }
  frame(x0, y0, x1, y1, c) { const [a, b, e, f] = this.box(x0, y0, x1, y1); for (let x = a; x <= e; x++) { this.set(x, b, c); this.set(x, f, c); } for (let y = b; y <= f; y++) { this.set(a, y, c); this.set(e, y, c); } }
  bevel(x0, y0, x1, y1, tl = 'T', br = 'B') { const [a, b, e, f] = this.box(x0, y0, x1, y1); for (let x = a; x <= e; x++) { this.set(x, b, tl); this.set(x, f, br); } for (let y = b + 1; y <= f; y++) { this.set(a, y, tl); this.set(e, y, br); } }
  plate(x0, y0, x1, y1, fill = 'b', tl = 'T', br = 'B') { this.rect(x0, y0, x1, y1, fill); this.bevel(x0, y0, x1, y1, tl, br); }
  hline(x0, x1, y, c) { const [a, b, e] = this.box(x0, y, x1, y); for (let x = a; x <= e; x++) this.set(x, b, c); }
  vline(x, y0, y1, c) { const [a, b, , f] = this.box(x, y0, x, y1); for (let y = b; y <= f; y++) this.set(a, y, c); }
  line(x0, y0, x1, y1, c) { let [a, b, e, f] = [this.sc(x0), this.sc(y0), this.sc(x1), this.sc(y1)]; const dx = Math.abs(e - a), dy = -Math.abs(f - b), sx = a < e ? 1 : -1, sy = b < f ? 1 : -1; let err = dx + dy; for (;;) { this.set(a, b, c); if (a === e && b === f) break; const e2 = 2 * err; if (e2 >= dy) { err += dy; a += sx; } if (e2 <= dx) { err += dx; b += sy; } } }
  disc(cx, cy, r, c, ring) { const C = [this.sc(cx), this.sc(cy)], R = Math.max(1, r * this.n / 48); for (let y = -Math.ceil(R); y <= Math.ceil(R); y++) for (let x = -Math.ceil(R); x <= Math.ceil(R); x++) { const d = Math.hypot(x, y); if (d <= R && (!ring || d > R - Math.max(1, ring * this.n / 48))) this.set(C[0] + x, C[1] + y, c); } }
  // text at pixel scale (not scaled), font 3 (3x5) or 5 (5x7); x,y design-space origin (top-left)
  text(x, y, str, c, font = 3) {
    let px = this.sc(x); const py = this.sc(y);
    for (const ch of str) {
      if (ch === ' ') { px += font === 3 ? 2 : 3; continue; }
      if (font === 3) { const b = F3[ch]; if (b) for (let i = 0; i < 15; i++) if (b[i] === '1') this.set(px + i % 3, py + (i / 3 | 0), c); px += 4; }
      else { const b = F5[ch]; if (b) for (let j = 0; j < 7; j++) for (let i = 0; i < 5; i++) if (b[j][i] === '1') this.set(px + i, py + j, c); px += 6; }
    }
  }
  rows() { return this.g.map(r => r.join('')); }
}
const textW = (s, font) => [...s].reduce((w, ch) => w + (ch === ' ' ? (font === 3 ? 2 : 3) : (font === 3 ? 4 : 6)), 0) - 1;

// ---- shape family -----------------------------------------------------------------------------------------
function page(G, band = 's', lines = '4') {   // a document with a folded corner
  G.rect(10, 4, 37, 43, 'w'); G.frame(10, 4, 37, 43, '8');
  G.rect(31, 4, 37, 10, ' '); G.line(31, 4, 37, 10, '8'); G.rect(31, 5, 31, 10, '8'); G.hline(31, 37, 10, '8'); G.rect(32, 6, 36, 9, '2');
  G.rect(13, 13, 34, 16, band);
  for (let y = 21; y <= 39; y += 4) G.hline(14, 33, y, lines);
}
function monitor(G, lineCols = ['g', 'g', 'g']) {
  G.plate(3, 5, 44, 35, '3'); G.rect(7, 9, 40, 31, '8');
  lineCols.forEach((c, k) => G.hline(10, 26 + 4 * k, 13 + 5 * k, c));
  G.rect(16, 36, 31, 39, '4'); G.plate(6, 40, 41, 44, '2');
}
function chassis(G, y, leds = 'g') {
  G.plate(3, y, 44, y + 8, '3', '1', '6');
  for (let k = 0; k < 4; k++) G.rect(7 + 4 * k, y + 3, 8 + 4 * k, y + 4, leds);
  for (let k = 0; k < 4; k++) G.rect(27 + 4 * k, y + 3, 29 + 4 * k, y + 5, '8');
}
function book(G, cover = 's', spine = 'B') {
  G.rect(8, 6, 39, 43, cover); G.frame(8, 6, 39, 43, '8'); G.rect(9, 6, 13, 43, spine);
  G.rect(36, 8, 38, 42, 'w'); G.hline(16, 33, 14, 'T'); G.hline(16, 33, 16, 'T');
}
function plateIcon(G, code, accent = 's') {   // generic tool: an engraved plate with a coloured bar
  G.plate(4, 8, 43, 39, 'b'); G.rect(7, 11, 40, 15, accent); G.bevel(7, 11, 40, 15, 'B', 'T');
  const f = G.n >= 32 ? 3 : 0;
  if (f) { const w = textW(code, 3); G.text(0, 0, '', 'k'); const x0 = Math.floor((G.n - w) / 2), y0 = G.sc(24); let px = x0;
    for (const ch of code) { const b = F3[ch]; if (b) for (let i = 0; i < 15; i++) if (b[i] === '1') { G.set(px + i % 3, y0 + (i / 3 | 0), 'B'); G.set(px + i % 3 + 1, y0 + (i / 3 | 0) + 1, 'T'); } px += 4; } }
  else G.rect(14, 24, 33, 30, 'B');
}

// ---- the icon set ---------------------------------------------------------------------------------------------
const ICONS = {
  // an empty cell: the front-panel placeholder for a position whose control is not yet due (dtfiles.js)
  D32blank: G => {},
  // main panel (designed at 48 from LOOK-SOUND 6.2 where it gives coordinates)
  D32log: G => { G.rect(5, 8, 40, 40, '6'); G.bevel(5, 8, 40, 40, '5', '8'); G.rect(5, 8, 9, 40, '8'); for (let k = 0; k < 5; k++) G.rect(6, 11 + 6 * k, 8, 13 + 6 * k, '2'); G.plate(16, 16, 33, 23, 'w', '1', '4'); G.hline(18, 31, 19, '5'); G.hline(18, 27, 21, '5'); },
  D32logN: G => { ICONS.D32log(G); G.vline(35, 4, 22, '7'); G.rect(36, 4, 45, 13, 'r'); G.frame(36, 4, 45, 13, '8'); },
  D32stat: G => { G.plate(2, 6, 45, 41, 'b', 'B', 'T'); for (let r = 0; r < 2; r++) for (let c = 0; c < 3; c++) { const x = 5 + 13 * c, y = 10 + 15 * r; G.plate(x, y, x + 11, y + 11, 'b'); G.frame(x + 2, y + 2, x + 9, y + 9, '7'); } },
  D32statA: G => { ICONS.D32stat(G); for (const [r, c] of [[0, 1], [1, 2]]) { const x = 5 + 13 * c, y = 10 + 15 * r; G.rect(x + 2, y + 2, x + 9, y + 9, 'A'); G.frame(x + 2, y + 2, x + 9, y + 9, '8'); } },
  D32stn: G => { chassis(G, 12); chassis(G, 25); G.line(40, 34, 36, 44, 'c'); G.line(36, 44, 26, 44, 'c'); },
  D32stnA: G => { ICONS.D32stn(G); G.disc(40, 6, 5, 'A'); G.disc(40, 6, 5, '8', 1); },
  D32lib: G => {   // Deep32 Library: an open volume, a standing volume behind it with 26 on the spine (LIBRARY 4.21)
    G.rect(30, 3, 41, 30, 'B'); G.frame(30, 3, 41, 30, '8'); G.hline(31, 40, 6, 'T'); G.hline(31, 40, 27, 'T');
    if (G.n >= 48) G.text(30.5, 10, '26', 'y', 5); else if (G.n >= 32) G.text(31, 12, '26', 'y', 3);
    G.rect(3, 26, 23, 42, 'w'); G.rect(24, 26, 44, 42, 'w'); G.line(3, 26, 23, 29, '8'); G.line(24, 29, 44, 26, '8');
    G.frame(3, 26, 44, 44, '8'); G.rect(3, 43, 44, 44, 's'); G.vline(23, 29, 43, '5'); G.vline(24, 29, 43, '4');
    for (let y = 32; y <= 40; y += 3) { G.hline(6, 20, y, '4'); G.hline(27, 41, y, '4'); }
  },
  // documents and data types
  D32rfc: G => { page(G, '6'); if (G.n >= 32) G.text(16, 26, 'RFC', '8'); },
  D32pcap: G => { page(G, 'c'); for (let k = 0; k < 6; k++) G.rect(14 + 3 * k, 36 - [4, 10, 6, 12, 3, 8][k], 15 + 3 * k, 36, '5'); },
  D32cfg: G => { page(G, '7'); G.rect(12, 20, 12, 38, 'r'); G.hline(14, 30, 24, 'k'); },
  D32yang: G => { page(G, 'g'); if (G.n >= 32) G.text(14, 26, 'YANG', '8'); },
  D32note: G => { G.rect(5, 10, 42, 38, 'w'); G.frame(5, 10, 42, 38, '8'); G.hline(6, 41, 14, 'r'); for (let y = 19; y <= 35; y += 4) G.hline(7, 40, y, 'u'); G.disc(37, 9, 3, 'r'); },
  D32read: G => { G.rect(3, 12, 44, 40, 'w'); G.frame(3, 12, 44, 40, '8'); G.vline(23, 12, 40, '4'); G.vline(24, 12, 40, '4'); for (let y = 18; y <= 36; y += 3) { G.hline(6, 20, y, '4'); G.hline(27, 41, y, '4'); } G.rect(25, 8, 27, 22, 'r'); },
  D32srch: G => { G.plate(6, 26, 41, 43, '3'); G.rect(20, 32, 27, 35, 'y'); G.frame(20, 32, 27, 35, '8'); for (let k = 0; k < 5; k++) G.rect(8 + 6 * k, (k === 2 ? 6 : 10), 12 + 6 * k, 25, 'w'); G.rect(20, 6, 24, 9, 'u'); },
  D32look: G => { G.rect(5, 12, 42, 37, 'w'); G.frame(5, 12, 42, 37, '8'); G.rect(8, 16, 19, 30, '3'); G.hline(23, 39, 18, '5'); G.hline(23, 35, 23, '5'); G.hline(23, 38, 28, '5'); G.rect(8, 32, 39, 34, 's'); },
  D32acq: G => { G.plate(6, 30, 41, 43, '3'); G.rect(14, 6, 33, 26, 'w'); G.frame(14, 6, 33, 26, '8'); for (let y = 10; y <= 22; y += 4) G.hline(17, 30, y, '4'); G.rect(31, 24, 38, 33, 'y'); G.frame(31, 24, 38, 33, '8'); },
  D32acqN: G => { ICONS.D32acq(G); G.disc(40, 8, 5, 'A'); G.disc(40, 8, 5, '8', 1); },
  D32tray: G => { G.plate(4, 26, 43, 43, '3'); G.rect(8, 30, 39, 33, '6'); G.rect(12, 12, 35, 29, 'w'); G.frame(12, 12, 35, 29, '8'); G.line(18, 18, 29, 18, '4'); G.line(18, 22, 26, 22, '4'); },
  D32man: G => { G.rect(8, 4, 39, 43, 'w'); G.frame(8, 4, 39, 43, '8'); if (G.n >= 32) G.text(11, 7, 'NAME', '8'); for (let y = 16; y <= 40; y += 4) G.hline(14, 36, y, '4'); },
  D32info: G => { G.rect(6, 6, 15, 42, 'u'); G.rect(16, 8, 25, 42, '5'); G.rect(26, 6, 35, 42, 'c'); for (const x of [6, 16, 26]) G.hline(x + 1, x + 8, 12, 'w'); G.frame(6, 6, 35, 42, '8'); G.rect(37, 4, 39, 30, 'r'); },
  D32help: G => { book(G, 's'); if (G.n >= 32) G.text(18, 24, '?', 'k'); },
  // terminals
  D32sess: G => { G.rect(4, 4, 15, 8, 'w'); G.rect(16, 5, 27, 8, '3'); G.rect(28, 5, 39, 8, '3'); G.plate(3, 9, 44, 41, '5'); G.rect(5, 14, 42, 39, '8'); G.hline(8, 16, 17, 'g'); G.hline(8, 30, 21, 'y'); G.hline(8, 24, 25, 'g'); G.hline(8, 20, 29, 'r'); G.rect(8, 33, 10, 35, 'w'); G.rect(34, 38, 45, 45, '4'); },
  D32ask: G => { monitor(G); G.rect(26, 2, 45, 17, 'w'); G.frame(26, 2, 45, 17, '8'); if (G.n >= 32) G.text(34, 6, '?', 'u'); },
  D32smit: G => { monitor(G, ['w', 'w', 'w']); for (let k = 0; k < 4; k++) G.rect(9 + 7 * k, 27, 14 + 7 * k, 29, '3'); },
  D32nedit: G => { page(G, 'u'); G.line(20, 40, 38, 22, '7'); G.line(21, 40, 39, 22, '7'); },
  // devices and lab tools
  D32nbr: G => { chassis(G, 6); chassis(G, 33); G.line(23, 15, 23, 33, '8'); G.line(24, 15, 24, 33, 'c'); },
  D32wshk: G => { G.plate(3, 6, 44, 41, '3'); for (let k = 0; k < 7; k++) G.hline(7, 40, 10 + 4 * k, k === 2 ? 's' : '5'); G.rect(6, 34, 41, 38, 'w'); },
  D32cap: G => { G.plate(3, 10, 44, 37, '3'); for (let k = 0; k < 8; k++) G.rect(7 + 4 * k, 30 - [6, 12, 4, 15, 8, 10, 3, 13][k], 8 + 4 * k, 30, 'g'); G.disc(39, 15, 3, 'r'); },
  D32oui: G => plateIcon(G, 'OUI', 'c'),
  D32calc: G => { G.plate(8, 4, 39, 44, '4'); G.rect(12, 8, 35, 15, '1'); G.frame(12, 8, 35, 15, '7'); if (G.n >= 32) G.text(18, 9, '/24', '8'); for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) G.plate(12 + 6 * c, 19 + 6 * r, 16 + 6 * c, 22 + 6 * r, '2', '1', '6'); },
  D32iso: G => { G.plate(4, 8, 43, 39, 'b'); G.line(10, 24, 20, 24, '7'); G.line(28, 24, 38, 24, '7'); G.rect(20, 21, 21, 27, '7'); G.rect(27, 21, 28, 27, '7'); G.rect(22, 23, 26, 25, 'A'); },
  // desktop style and admin
  D32snd: G => { G.rect(6, 18, 12, 30, '7'); G.line(12, 18, 22, 10, '5'); G.line(12, 30, 22, 38, '5'); G.vline(22, 10, 38, '5'); G.rect(13, 17, 21, 31, '5'); G.disc(24, 24, 9, '4', 1); G.disc(24, 24, 15, '4', 1); G.rect(34, 34, 44, 44, 'y'); },
  D32mix: G => { for (const [x, y] of [[11, 16], [23, 28], [35, 22]]) { G.rect(x, 8, x + 2, 43, '8'); G.plate(x - 3, y, x + 5, y + 4, '2'); } },
  D32prof: G => { G.plate(3, 8, 22, 40, 'b'); G.plate(25, 8, 44, 40, 's'); G.bevel(6, 12, 19, 20); G.bevel(28, 12, 41, 20, 'B', 'T'); },
  D32set: G => { G.plate(4, 6, 43, 43, 'b'); for (const [y, k] of [[12, 14], [22, 30], [32, 22]]) { G.rect(8, y, 39, y + 2, '8'); G.plate(k, y - 3, k + 4, y + 5, '2'); } },
  D32sam: G => { G.rect(4, 18, 43, 42, 'r'); G.rect(38, 18, 43, 42, '7'); G.hline(4, 43, 24, '8'); G.plate(16, 10, 31, 18, '4'); G.line(10, 36, 38, 28, '2'); G.line(10, 37, 38, 29, '7'); },
  // applications
  D32web: G => { page(G, 'b'); G.hline(14, 33, 20, 'u'); G.hline(14, 33, 21, 'u'); G.disc(37, 33, 8, '1'); G.disc(37, 33, 8, '7', 1); G.line(37, 27, 37, 33, 'r'); G.line(37, 33, 37, 39, '6'); },
  D32media: G => { G.plate(3, 8, 44, 37, '4'); G.rect(9, 12, 38, 33, '8'); for (let y = 16; y <= 29; y++) { const w = Math.round((y - 16) <= 6 ? (y - 16) * 0.9 : (29 - y) * 0.9); G.hline(19, 19 + w, y, 'w'); } G.plate(16, 38, 31, 43, '5'); },
  D32img: G => { G.plate(4, 8, 43, 39, 'w', '1', '6'); G.rect(8, 12, 39, 35, 'c'); G.line(8, 35, 20, 22, 'g'); G.line(20, 22, 30, 32, 'g'); G.line(26, 28, 32, 22, 'g'); G.line(32, 22, 39, 30, 'g'); G.disc(32, 17, 3, 'y'); },
  D32scap: G => { G.plate(4, 8, 43, 39, 'b'); G.frame(9, 13, 38, 34, 'k'); for (let x = 9; x <= 38; x += 4) G.rect(x, 13, x + 1, 13, 'w'); G.rect(30, 26, 44, 44, '6'); G.disc(37, 35, 5, '2'); },
};
// status tiles (subpanel monitor controls): 28x17 bevelled tile in a 32x32 cell, two-letter code (DISTRO 6.5)
function tile(code, attention, reverse) {
  return G => {
    const s = G.n / 32;
    if (G.n < 32) { G.rect(1, 14, 46, 33, attention ? 'A' : 'b'); G.frame(1, 14, 46, 33, attention ? '8' : '7'); return; }
    const x0 = Math.round(2 * s), y0 = Math.round(7 * s), w = Math.round(28 * s), h = Math.round(17 * s);
    for (let y = y0; y < y0 + h; y++) for (let x = x0; x < x0 + w; x++) G.set(x, y, attention ? 'A' : 'b');
    for (let x = x0; x < x0 + w; x++) { G.set(x, y0, 'T'); G.set(x, y0 + h - 1, 'B'); }
    for (let y = y0; y < y0 + h; y++) { G.set(x0, y, 'T'); G.set(x0 + w - 1, y, 'B'); }
    if (!attention) { for (let x = x0 + 2; x < x0 + w - 2; x++) { G.set(x, y0 + 2, '7'); G.set(x, y0 + h - 3, '7'); } for (let y = y0 + 2; y < y0 + h - 2; y++) { G.set(x0 + 2, y, '7'); G.set(x0 + w - 3, y, '7'); } }
    const tw = 11, tx = x0 + Math.floor((w - tw) / 2), ty = y0 + Math.floor((h - 7) / 2);
    if (reverse && attention) for (let y = ty - 1; y < ty + 8; y++) for (let x = tx - 1; x < tx + tw + 1; x++) G.set(x, y, 'k');
    let px = tx;
    for (const ch of code) { const b = F5[ch]; for (let j = 0; j < 7; j++) for (let i = 0; i < 5; i++) if (b[j][i] === '1') G.set(px + i, ty + j, reverse && attention ? 'A' : 'k'); px += 6; }
  };
}
for (const code of ['MO', 'GP', 'RL', 'GN', 'NX', 'WB', 'MI', 'CA', 'RC']) {
  const reverse = ['MI', 'CA', 'RC'].includes(code);
  ICONS['D32t' + code] = tile(code, false, reverse);
  ICONS['D32t' + code + 'a'] = tile(code, true, reverse);
}

const SIZES = { l: 48, m: 32, t: 16 };
function icons() {
  const out = {};
  for (const [name, draw] of Object.entries(ICONS)) {
    for (const [suf, n] of Object.entries(SIZES)) {
      const G = new Grid(n); draw(G);
      out[`${name}.${suf}.pm`] = xpm.write(`${name}_${suf}_pm`, G.rows(), LEGEND,
        `${name} ${n}x${n} (d32-theme). DRAFT art: generated placeholder until the art lane redraws it (REPLAN B9).\nCDE symbolic colours; original art, no vendor marks.`);
    }
  }
  return out;
}

// ---- backdrops (LOOK-SOUND 7.1) ----------------------------------------------------------------------------
const BAYER8 = (() => { let a = [[0]]; for (let n = 1; n < 8; n *= 2) { const b = []; for (let y = 0; y < 2 * n; y++) { b.push([]); for (let x = 0; x < 2 * n; x++) b[y].push(4 * a[y % n][x % n] + [0, 2, 3, 1][(y >= n) * 2 + (x >= n)]); } a = b; } return a; })();
// stops: list of [row, symbol]; between stops an 8x8 Bayer dither from one symbol to the next (integer maths only).
function gradient(h, stops) {
  const rows = [];
  for (let y = 0; y < h; y++) {
    let k = 0; while (k < stops.length - 1 && y >= stops[k + 1][0]) k++;
    let r = '';
    for (let x = 0; x < 8; x++) {
      if (k >= stops.length - 1 || stops[k][1] === stops[k + 1][1]) { r += stops[k][1]; continue; }
      const [y0, a] = stops[k], [y1, b] = stops[k + 1];
      const f64 = Math.floor((y - y0) * 64 / (y1 - y0));   // 0..63
      r += f64 > BAYER8[y % 8][x] ? b : a;
    }
    rows.push(r);
  }
  return rows;
}
function backdrops() {
  const L = { T: { s: 'topShadowColor', m: 'white' }, b: { s: 'background', m: 'black' }, s: { s: 'selectColor', m: 'white' }, B: { s: 'bottomShadowColor', m: 'black' } };
  return {
    // the measured HP-UX 11i sky (LOOK-SOUND 2.1): rows 0-479 ts to bg, 480-1199 bg, 1200-1439 bg to sc
    'Horizon.pm': xpm.write('Horizon', gradient(1440, [[0, 'T'], [480, 'b'], [1200, 'b'], [1439, 's']]), L,
      'Horizon 8x1440 (d32-theme): a vertical sky, light at the top, 8x8 Bayer dither. Original art (LOOK-SOUND 7.1).'),
    // ASSETS 4 Abyss regenerated for 1440 rows: ts -> bg -> sc -> bs
    'Abyss1440.pm': xpm.write('Abyss1440', gradient(1440, [[0, 'T'], [480, 'b'], [960, 's'], [1439, 'B']]), L,
      'Abyss1440 8x1440 (d32-theme): a depth gradient, surface to floor, 8x8 Bayer dither. Original art (ASSETS 4).'),
  };
}

// ---- login emblem: Direction C Root Bridge with DISTRO 6.9's row-14 fix, 16x16 x 14 = 224 -------------------------
function emblem() {
  const grid = ['WWWWWWWWWWWWWWWK', 'WddddddddddddddK', 'WddddkkkkkkddddK', 'WddddkAAAAkddddK', 'WddddkAAAAkddddK', 'WddddkkkkkkddddK',
    'WddddddllddddddK', 'WddddddllddddddK', 'WddllllllllllddK', 'WddllddddddllddK', 'WkkkkkkddkkkkkkK', 'WknnnnkddknnnnkK',
    'WknnnnkddknnnnkK', 'WkkkkkkddkkkkkkK', 'WddldldldldldddK', 'KKKKKKKKKKKKKKKK'];
  const legend = { W: { c: '#C6CED6' }, K: { c: '#08183A' }, d: { c: '#29313F' }, k: { c: '#101418' }, A: { c: '#FFB531' }, n: { c: '#ADB5C6' }, l: { c: '#E7EFF7' } };
  const rows = []; for (const r of grid) { const line = [...r].map(ch => ch.repeat(14)).join(''); for (let i = 0; i < 14; i++) rows.push(line); }
  return xpm.write('emblem_224', rows, legend,
    'Deep32/UX emblem, Direction C "Root Bridge" (DISTRO-DESIGN 6.9, row 14 fixed), 224x224 = the 16x16 grid x14.\nDRAFT: brand colours, nearest-neighbour scale; the final 224 construction is the art lane (LOOK-SOUND 5).');
}

module.exports = { icons, backdrops, emblem, ICONS, LEGEND, gradient };
