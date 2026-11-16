'use strict';
// Deep32 Library's front-panel control (position 9) and its subpanel: Library Search, Reader, Acquisition,
// Lookup Desk, Manual Pages, In-Tray (DISTRO-DESIGN 6.4; LIBRARY-DESIGN 2.2, 3.6; LOOK-SOUND 6.2 as revised by
// DISTRO-DESIGN 6.9 / fidelity 23).
const { dropShadow, block, page, grid } = require('../../lib/kit');
const { F3x5, F4x5, F5x5 } = require('../../lib/font');

const navy = (x, y) => (((x + y) & 1) ? 'b' : 'k');      // blue/black checker reads as navy
const sky = (x, y) => (((x + y) & 1) ? 'b' : 'w');       // blue/white checker reads as light blue
const maroon = (x, y) => (((x + y) & 1) ? 'r' : 'k');
const all = () => true;

// ---------- D32lib: Deep32 Library, an open reference volume with "26" on its spine ----------
// The volume lies open; its cover shows round the pages and its spine (navy, gold bands, "26") hangs below the gutter.
function openBook(c, o) {
  const { x0, x1, top, bot, arch, gut, rim } = o;               // page box and arch height
  const mid = Math.floor((x0 + x1) / 2);
  const wing = (a, b, flip) => {                               // one page: arched top and bottom edges
    const w = b - a;
    const yAt = (x, base) => { const t = flip ? (b - x) / w : (x - a) / w; return base - Math.round(arch * Math.sin(Math.PI * Math.min(1, t * 1.15))); };
    return { yAt };
  };
  const L = wing(x0, mid, false), R = wing(mid + 1, x1, true);
  // cover rim (navy) behind: page outline grown by rim px, dropped by rim px
  for (let x = x0 - rim; x <= x1 + rim; x++) {
    const xx = Math.max(x0, Math.min(x1, x)); const W = xx <= mid ? L : R;
    c.shape(x, W.yAt(xx, top) + rim, x, W.yAt(xx, bot) + rim + 1, all, navy);
  }
  // page stack edges (the thickness under the open pages)
  for (let x = x0; x <= x1; x++) {
    const W = x <= mid ? L : R; const yb = W.yAt(x, bot);
    for (let k = 1; k <= rim; k++) c.set(x, yb + k, k % 2 ? 'g3' : 'g1');
  }
  // pages
  for (let x = x0; x <= x1; x++) { const W = x <= mid ? L : R; c.vl(x, W.yAt(x, top), W.yAt(x, bot), 'w'); }
  // outline of the pages
  for (let x = x0; x <= x1; x++) { const W = x <= mid ? L : R; c.set(x, W.yAt(x, top), 'g8'); c.set(x, W.yAt(x, bot) + rim + 1, 'g8'); }
  c.vl(x0 - rim, L.yAt(x0, top) + rim, L.yAt(x0, bot) + rim + 1, 'g8');
  c.vl(x1 + rim, R.yAt(x1, top) + rim, R.yAt(x1, bot) + rim + 1, 'g8');
  c.vl(x0, L.yAt(x0, top), L.yAt(x0, bot), 'g8'); c.vl(x1, R.yAt(x1, top), R.yAt(x1, bot), 'g8');
  // gutter shading
  if (gut) { c.vl(mid, L.yAt(mid, top) + 1, L.yAt(mid, bot), 'g4'); c.vl(mid + 1, R.yAt(mid + 1, top) + 1, R.yAt(mid + 1, bot), 'g2'); c.vl(mid - 1, L.yAt(mid - 1, top) + 1, L.yAt(mid - 1, bot), 'g2'); }
  return { L, R, mid };
}
function lib48(c) {
  openBook(c, { x0: 4, x1: 43, top: 12, bot: 32, arch: 4, gut: true, rim: 3 });
  // left page: a picture plate (sky over a hill) and a caption; right page: text with a bold lead word
  c.shape(8, 15, 19, 23, all, sky); c.shape(8, 20, 19, 23, (x, y) => y >= 20 + Math.round(2 * Math.sin((x - 8) / 3.5)), 'g');
  c.box(7, 14, 20, 24, 'g6');
  c.hl(8, 19, 27, 'g4'); c.hl(8, 16, 29, 'g4');
  for (let y = 15, n = 0; y <= 31; y += 2, n++) c.hl(28, 40 - (n % 3 === 2 ? 5 : 0), y, 'g4');
  c.hl(28, 33, 15, 'g7');
  // spine plate under the gutter: navy, gold bands, a bold "26"
  c.rect(16, 36, 31, 46, 'k'); c.box(16, 36, 31, 46, 'g8');
  c.hl(17, 30, 37, 'y'); c.hl(17, 30, 45, 'y'); c.vl(17, 38, 44, 'g7'); c.vl(30, 38, 44, 'g7');
  c.text(F5x5, '26', 19, 39, 'y');
  dropShadow(c, 2, 2);
}
function lib32(c) {
  openBook(c, { x0: 2, x1: 29, top: 9, bot: 24, arch: 3, gut: true, rim: 2 });
  c.shape(5, 11, 12, 16, all, sky); c.shape(5, 14, 12, 16, (x, y) => y >= 14 + ((x - 5) % 5 === 2 ? 0 : 1), 'g');
  c.box(4, 10, 13, 17, 'g6');
  c.hl(5, 12, 20, 'g4');
  for (const y of [11, 13, 15, 17, 19]) c.hl(18, y === 19 ? 23 : 26, y, 'g4');
  c.hl(18, 22, 11, 'g7');
  c.shape(12, 25, 20, 31, (x, y) => !((x === 12 || x === 20) && y === 31), navy);
  c.vl(12, 25, 30, 'g8'); c.vl(20, 25, 30, 'g8'); c.hl(13, 19, 31, 'g8');
  c.text(F3x5, '26', 13, 26, 'y');
  dropShadow(c, 1, 1);
}
function lib16(c) {
  grid(c, [
    '..88|88..|..88|88..',
    '.8WW|WW88|88WW|WW8.',
    '.8WB|BBW4|2WWW|WW8.',
    '.8WB|BBW4|2W77|7W8.',
    '.8WG|GGW4|2WWW|WW8.',
    '.8WW|WWW4|2W44|4W8.',
    '.8W4|44W4|2WWW|WW8.',
    '.8WW|WWW4|2W44|WW8.',
    '.8WW|WWWW|WWWW|WW8.',
    '.8NN|NNNN|NNNN|NN8.',
    '.888|YYYN|YYY8|888.',
    '...8|NNYN|YNN8|....',
    '...8|YYYN|YYY8|....',
    '...8|YNNN|YNY8|....',
    '...8|YYYN|YYY8|....',
    '...8|8888|8888|....',
  ], { N: navy });
}

// ---------- D32srch: Library Search, a card-catalogue drawer with one raised card ----------
function srch48(c) {
  // cards standing in the drawer, back to front; the third one raised with a blue tab
  const tabs = [[11, 16], [19, 24], [27, 32], [33, 38], [14, 19]];
  for (let i = 0; i < 5; i++) {
    const raised = i === 2; const y = 10 + 3 * i - (raised ? 7 : 0);
    c.rect(9, y, 38, 27, 'w'); c.box(9, y, 38, 28, 'g8');
    c.hl(10, 37, y + 1, 'g1');
    const [tx0, tx1] = tabs[i];
    c.rect(tx0, y - 2, tx1, y, raised ? 'b' : 'g4'); c.box(tx0, y - 3, tx1, y, 'g8');
    if (raised) { c.hl(tx0 + 1, tx1 - 1, y - 2, 'c'); c.hl(12, 30, y + 3, 'r'); c.hl(12, 34, y + 5, 'g4'); c.hl(12, 26, y + 7, 'g4'); }
  }
  block(c, 6, 26, 41, 43, 'g3', 'g1', 'g6', 'g8');
  c.bevel(7, 27, 40, 42, 1, 'g1', 'g5');
  c.rect(17, 29, 30, 32, 'w'); c.box(17, 29, 30, 32, 'g7'); c.hl(19, 28, 30, 'g4');
  c.rect(20, 35, 27, 39, 'y'); c.box(20, 35, 27, 39, 'g8'); c.hl(21, 26, 36, 'w'); c.hl(21, 26, 38, 'g5');
  dropShadow(c, 2, 2);
}
function srch32(c) {
  const tabs = [[7, 11], [17, 21], [10, 14]];
  for (let i = 0; i < 3; i++) {
    const raised = i === 1; const y = 6 + 3 * i - (raised ? 4 : 0);
    c.rect(6, y, 25, 18, 'w'); c.box(6, y, 25, 19, 'g8');
    const [a, b] = tabs[i];
    c.rect(a, y - 2, b, y, raised ? 'b' : 'g4'); c.box(a, y - 2, b, y, 'g8');
    if (raised) { c.hl(8, 20, y + 2, 'r'); c.hl(8, 22, y + 4, 'g4'); }
  }
  block(c, 3, 17, 28, 29, 'g3', 'g1', 'g6', 'g8');
  c.rect(11, 19, 20, 21, 'w'); c.box(11, 19, 20, 21, 'g7');
  c.rect(13, 23, 18, 26, 'y'); c.box(13, 23, 18, 26, 'g8');
  dropShadow(c, 1, 1);
}
function srch16(c) {
  grid(c, [
    '....|..88|88..|....',
    '....|..8B|B8..|....',
    '...8|8888|8888|8...',
    '...8|WRRR|RRWW|8...',
    '...8|W444|44WW|8...',
    '...8|WWWW|WWWW|8...',
    '.888|8888|8888|888.',
    '.811|1111|1111|118.',
    '.813|8WWW|WWW8|368.',
    '.813|8888|8888|368.',
    '.813|3388|8833|368.',
    '.813|338Y|Y833|368.',
    '.813|3388|8833|368.',
    '.866|6666|6666|668.',
    '.888|8888|8888|888.',
    '....|....|....|....',
  ]);
}

// ---------- D32read: Reader, a lectern holding an open document with a ribbon ----------
function read48(c) {
  // slanted desk board (wood grey), the document on it, the ledge, the column and the foot
  c.poly([[9, 7], [38, 7], [44, 28], [3, 28]], 'g5');
  c.line(9, 7, 38, 7, 'g2'); c.line(9, 7, 3, 28, 'g3'); c.line(38, 7, 44, 28, 'g7');
  c.poly([[12, 9], [35, 9], [39, 26], [8, 26]], 'w');
  c.line(12, 9, 35, 9, 'g8'); c.line(12, 9, 8, 26, 'g8'); c.line(35, 9, 39, 26, 'g8');
  c.line(23, 9, 23, 26, 'g4'); c.line(24, 9, 24, 26, 'g2');
  for (let y = 12, n = 0; y <= 24; y += 3, n++) {
    const ix = Math.round((y - 9) * 4 / 17);
    c.hl(13 - ix + 1, 21, y, 'g4'); c.hl(26, 34 + ix - (n % 2 ? 4 : 0), y, 'g4');
  }
  c.rect(14 - 1, 12, 18, 12, 'g7');
  // ribbon
  c.vl(30, 9, 33, 'r'); c.vl(31, 9, 32, 'r'); c.set(31, 33, 'k'); c.vl(32, 9, 33, 'r'); c.set(30, 34, 'r'); c.set(32, 34, 'r'); c.vl(29, 9, 26, 'g8');
  // ledge
  c.rect(2, 28, 45, 30, 'g6'); c.hl(2, 45, 28, 'g4'); c.hl(2, 45, 30, 'g8'); c.vl(2, 28, 30, 'g8'); c.vl(45, 28, 30, 'g8');
  // column and foot
  c.rect(21, 31, 26, 40, 'g4'); c.vl(21, 31, 40, 'g2'); c.vl(26, 31, 40, 'g7'); c.vl(20, 31, 40, 'g8'); c.vl(27, 31, 40, 'g8');
  block(c, 11, 41, 36, 44, 'g5', 'g3', 'g7', 'g8');
  dropShadow(c, 2, 2);
}
function read32(c) {
  c.poly([[6, 4], [25, 4], [29, 19], [2, 19]], 'g5');
  c.poly([[8, 6], [23, 6], [26, 17], [5, 17]], 'w');
  c.line(8, 6, 23, 6, 'g8'); c.line(8, 6, 5, 17, 'g8'); c.line(23, 6, 26, 17, 'g8');
  c.line(15, 6, 15, 17, 'g4');
  for (const y of [9, 12, 15]) { c.hl(9 - Math.round((y - 6) / 4), 13, y, 'g4'); c.hl(17, 22 + Math.round((y - 6) / 4), y, 'g4'); }
  c.vl(20, 6, 21, 'r'); c.vl(21, 6, 20, 'r');
  c.rect(1, 19, 30, 20, 'g6'); c.hl(1, 30, 21, 'g8'); c.vl(1, 19, 21, 'g8'); c.vl(30, 19, 21, 'g8');
  c.rect(14, 22, 17, 27, 'g4'); c.vl(13, 22, 27, 'g8'); c.vl(18, 22, 27, 'g8'); c.vl(14, 22, 27, 'g2');
  block(c, 7, 27, 24, 29, 'g5', 'g3', 'g7', 'g8');
  dropShadow(c, 1, 1);
}
function read16(c) {
  grid(c, [
    '....|....|....|....',
    '....|8888|8888|....',
    '...8|W44W|4WRW|8...',
    '...8|WWWW|4WRW|8...',
    '..8W|444W|44R4|W8..',
    '..8W|WWWW|4WRW|W8..',
    '.8WW|WWWW|4WRW|WW8.',
    '8888|8888|8888|8888',
    '8666|6666|6666|6668',
    '8888|8888|8888|8888',
    '....|..82|48..|....',
    '....|..82|48..|....',
    '....|..82|48..|....',
    '..88|8888|8888|88..',
    '..85|5555|5555|58..',
    '..88|8888|8888|88..',
  ]);
}

// ---------- D32acq: Acquisition, a wire in-tray with a document in it and a library card clipped to its edge ----------
function acq48(c) {
  // document lying in the tray (behind the front wires)
  c.poly([[11, 17], [32, 17], [36, 32], [8, 32]], 'w');
  c.line(11, 17, 32, 17, 'g8'); c.line(11, 17, 8, 32, 'g8'); c.line(32, 17, 36, 32, 'g8');
  for (let y = 20; y <= 29; y += 3) c.hl(12 - Math.round((y - 17) / 5), 30 + Math.round((y - 17) / 5) - (y === 26 ? 6 : 0), y, 'g4');
  c.hl(13, 22, 20, 'g7');
  // the tray: back rail, side rails, front wire grid, rim
  const wire = 'g7', hi = 'g3';
  c.hl(9, 38, 22, wire); c.line(9, 22, 4, 31, wire); c.line(38, 22, 43, 31, wire);
  c.hl(4, 43, 31, wire); c.hl(4, 43, 32, hi);
  c.hl(4, 43, 42, wire); c.hl(5, 42, 43, 'g8');
  for (let x = 4; x <= 43; x += 4) { c.vl(x, 31, 42, wire); c.vl(x + 1, 33, 41, hi); }
  c.hl(4, 43, 37, wire);
  c.vl(43, 31, 42, wire);
  // library card clipped to the right front edge (buff card, red top rule, a few lines)
  c.rect(33, 12, 44, 30, 'g1'); c.box(33, 12, 44, 30, 'g8');
  c.hl(34, 43, 15, 'r'); for (const y of [18, 21, 24, 27]) c.hl(35, 42, y, 'b');
  c.vl(38, 13, 29, 'r');
  // paper clip over the card's top edge
  c.box(35, 9, 37, 17, 'g6'); c.vl(36, 10, 15, 'g2'); c.set(36, 9, 'g3');
  dropShadow(c, 2, 2);
}
function acq32(c) {
  c.poly([[7, 11], [21, 11], [24, 21], [5, 21]], 'w');
  c.line(7, 11, 21, 11, 'g8'); c.line(7, 11, 5, 21, 'g8'); c.line(21, 11, 24, 21, 'g8');
  c.hl(8, 19, 14, 'g4'); c.hl(8, 17, 17, 'g4');
  c.hl(6, 25, 15, 'g7'); c.line(6, 15, 2, 21, 'g7'); c.line(25, 15, 29, 21, 'g7');
  c.hl(2, 29, 21, 'g7'); c.hl(2, 29, 28, 'g7'); c.hl(3, 28, 29, 'g8');
  for (let x = 2; x <= 29; x += 3) c.vl(x, 21, 28, 'g7');
  c.hl(2, 29, 25, 'g7');
  c.rect(22, 7, 30, 20, 'g1'); c.box(22, 7, 30, 20, 'g8'); c.hl(23, 29, 9, 'r'); c.hl(24, 28, 12, 'b'); c.hl(24, 28, 15, 'b');
  c.box(24, 5, 25, 11, 'g6');
  dropShadow(c, 1, 1);
}
function acq16(c) {
  grid(c, [
    '....|....|..88|888.',
    '....|....|..8R|RR8.',
    '....|....|..8W|WW8.',
    '..88|8888|888B|BB8.',
    '..8W|4444|4W8W|WW8.',
    '.8WW|WWWW|WW8B|BB8.',
    '.8W4|4444|4W8W|WW8.',
    '7777|7777|7777|7777',
    '7..7|..7.|.7..|7..7',
    '7..7|..7.|.7..|7..7',
    '7777|7777|7777|7777',
    '7..7|..7.|.7..|7..7',
    '7..7|..7.|.7..|7..7',
    '7777|7777|7777|7777',
    '.888|8888|8888|888.',
    '....|....|....|....',
  ]);
}

// ---------- D32look: Lookup Desk, a thumb-indexed reference book ----------
function look48(c) {
  // a closed reference book standing in 3/4 view: maroon front cover, page tops, the fore-edge with thumb tabs
  c.poly([[6, 9], [14, 3], [39, 3], [31, 9]], 'w');
  for (let y = 4; y <= 8; y += 2) c.hl(15 - (y - 3), 38 - (y - 3), y, 'g2');
  c.poly([[31, 9], [39, 3], [39, 38], [31, 44]], 'w');
  for (let x = 32; x <= 38; x += 2) c.line(x, 9 - Math.round((x - 31) * 0.75), x, 44 - Math.round((x - 31) * 0.75), 'g2');
  c.line(6, 9, 14, 3, 'g8'); c.hl(14, 39, 3, 'g8'); c.line(39, 3, 39, 38, 'g8'); c.line(39, 38, 31, 44, 'g8');
  c.rect(6, 9, 31, 44, maroon); c.box(6, 9, 31, 44, 'g8');
  c.vl(7, 10, 43, 'r'); c.hl(7, 30, 10, 'r');
  c.hl(7, 30, 14, 'y'); c.hl(7, 30, 15, 'y'); c.hl(7, 30, 38, 'y'); c.hl(7, 30, 39, 'y');
  c.rect(11, 20, 26, 28, 'y'); c.box(11, 20, 26, 28, 'g8'); c.hl(13, 24, 23, 'g7'); c.hl(15, 22, 25, 'g7');
  const tabs = [['A', 9], ['G', 17], ['N', 25], ['T', 33]];
  for (const [ch, y] of tabs) {
    const x = 36 - Math.round((y - 9) * 0.1);
    c.rect(x, y, x + 6, y + 6, 'k'); c.box(x, y, x + 6, y + 6, 'g8');
    c.text(F4x5, ch, x + 2 - (ch === 'T' ? 0 : 0), y + 1, 'y');
  }
  dropShadow(c, 2, 2);
}
function look32(c) {
  c.poly([[4, 6], [9, 2], [26, 2], [21, 6]], 'w'); c.hl(9, 25, 4, 'g2');
  c.poly([[21, 6], [26, 2], [26, 25], [21, 29]], 'w');
  c.line(4, 6, 9, 2, 'g8'); c.hl(9, 26, 2, 'g8'); c.line(26, 2, 26, 25, 'g8'); c.line(26, 25, 21, 29, 'g8');
  c.rect(4, 6, 21, 29, maroon); c.box(4, 6, 21, 29, 'g8'); c.vl(5, 7, 28, 'r');
  c.hl(5, 20, 9, 'y'); c.hl(5, 20, 26, 'y');
  c.rect(8, 13, 17, 18, 'y'); c.box(8, 13, 17, 18, 'g8');
  for (const y of [7, 13, 19, 24]) { c.rect(24, y, 28, y + 3, 'k'); c.box(24, y, 28, y + 3, 'g8'); c.hl(25, 27, y + 1, 'y'); c.set(26, y + 2, 'y'); }
  dropShadow(c, 1, 1);
}
function look16(c) {
  grid(c, [
    '....|....|....|....',
    '..88|8888|8888|8...',
    '..8R|mmmm|mmm8|W8..',
    '..8R|mmmm|mmm8|WKK.',
    '..8R|YYYY|YYY8|WKY.',
    '..8R|mmmm|mmm8|WKK.',
    '..8R|mYYY|Ymm8|W8..',
    '..8R|mY77|Ymm8|WKK.',
    '..8R|mYYY|Ymm8|WKY.',
    '..8R|mmmm|mmm8|WKK.',
    '..8R|mmmm|mmm8|W8..',
    '..8R|YYYY|YYY8|WKK.',
    '..8R|mmmm|mmm8|WKY.',
    '..8R|mmmm|mmm8|WKK.',
    '..88|8888|8888|88..',
    '....|....|....|....',
  ], { m: maroon });
}

// ---------- D32man: Manual Pages, a manual page in a ring binder ----------
function man48(c) {
  page(c, 10, 4, 41, 44, { fold: 0 });
  // header line: name(section) at both ends
  c.hl(13, 18, 7, 'g8'); c.hl(33, 38, 7, 'g8'); c.hl(22, 29, 7, 'g4');
  c.text(F4x5, 'NAME', 14, 11, 'g8');
  c.hl(17, 36, 18, 'g4');
  c.hl(13, 20, 22, 'g8'); c.hl(13, 21, 23, 'g8');
  for (const [y, e] of [[26, 37], [28, 35], [30, 37], [32, 30]]) c.hl(17, e, y, 'g4');
  c.hl(13, 22, 36, 'g8'); c.hl(13, 22, 37, 'g8');
  c.hl(17, 34, 40, 'g4');
  // binder rings on the left edge
  for (const y of [9, 23, 37]) {
    c.rect(6, y, 13, y + 3, 'g4'); c.box(6, y, 13, y + 3, 'g8'); c.hl(7, 12, y + 1, 'g1'); c.set(11, y + 2, 'g6');
    c.rect(11, y + 1, 12, y + 2, 'g6');
  }
  dropShadow(c, 2, 2);
}
function man32(c) {
  page(c, 7, 2, 28, 29, {});
  c.hl(9, 12, 4, 'g8'); c.hl(23, 26, 4, 'g8');
  c.hl(9, 15, 8, 'g8'); c.hl(9, 15, 9, 'g8');
  for (const [y, e] of [[12, 25], [14, 23], [16, 25]]) c.hl(12, e, y, 'g4');
  c.hl(9, 15, 20, 'g8'); c.hl(9, 15, 21, 'g8');
  c.hl(12, 24, 24, 'g4'); c.hl(12, 20, 26, 'g4');
  for (const y of [6, 15, 24]) { c.rect(4, y, 9, y + 2, 'g4'); c.box(4, y, 9, y + 2, 'g8'); c.hl(5, 8, y + 1, 'g1'); }
  dropShadow(c, 1, 1);
}
function man16(c) {
  grid(c, [
    '....|8888|8888|88..',
    '....|888W|44W8|88..',
    '.888|8WWW|WWWW|W8..',
    '.844|8W88|8WWW|W8..',
    '.888|8WWW|WWWW|W8..',
    '....|8WW4|4444|48..',
    '....|8WW4|444W|W8..',
    '.888|8WWW|WWWW|W8..',
    '.844|8W88|8WWW|W8..',
    '.888|8WWW|WWWW|W8..',
    '....|8WW4|4444|48..',
    '....|8WW4|4WWW|W8..',
    '.888|8WWW|WWWW|W8..',
    '.844|8WW4|444W|W8..',
    '.888|8WWW|WWWW|W8..',
    '....|8888|8888|88..',
  ]);
}

// ---------- D32tray: In-Tray, a folder with an "IN" plate and papers in it ----------
function tray48(c) {
  // back of the folder with its tab
  c.rect(4, 12, 18, 15, 'g2'); c.box(4, 12, 18, 16, 'g8'); c.line(18, 12, 21, 16, 'g8');
  c.rect(4, 16, 43, 42, 'g3'); c.box(4, 16, 43, 42, 'g8');
  // papers
  c.rect(9, 9, 36, 30, 'w'); c.box(9, 9, 36, 30, 'g8'); for (let y = 12; y <= 20; y += 3) c.hl(12, 32, y, 'g4');
  c.rect(14, 13, 40, 30, 'w'); c.box(14, 13, 40, 30, 'g8'); for (let y = 16; y <= 24; y += 3) c.hl(17, 36 - (y === 22 ? 6 : 0), y, 'g4');
  // front flap
  c.poly([[4, 25], [43, 22], [43, 42], [4, 42]], 'g2');
  c.line(4, 25, 43, 22, 'g8'); c.line(4, 26, 43, 23, 'g1');
  c.vl(42, 24, 41, 'g5'); c.hl(5, 42, 41, 'g5');
  c.box(4, 22, 43, 42, 'g8'); c.vl(4, 22, 24, null); c.hl(5, 43, 22, null);
  c.line(4, 25, 43, 22, 'g8');
  // label plate with IN
  c.rect(17, 30, 30, 37, 'w'); c.box(17, 30, 30, 37, 'g7'); c.hl(18, 29, 31, 'g1');
  c.text(F4x5, 'IN', 20, 32, 'g8');
  dropShadow(c, 2, 2);
}
function tray32(c) {
  c.rect(2, 7, 11, 9, 'g2'); c.box(2, 7, 11, 10, 'g8');
  c.rect(2, 10, 29, 28, 'g3'); c.box(2, 10, 29, 28, 'g8');
  c.rect(6, 5, 24, 19, 'w'); c.box(6, 5, 24, 19, 'g8'); c.hl(8, 21, 8, 'g4'); c.hl(8, 19, 11, 'g4');
  c.rect(10, 8, 27, 19, 'w'); c.box(10, 8, 27, 19, 'g8'); c.hl(12, 24, 11, 'g4'); c.hl(12, 22, 14, 'g4');
  c.poly([[2, 17], [29, 15], [29, 28], [2, 28]], 'g2');
  c.line(2, 17, 29, 15, 'g8'); c.vl(2, 17, 28, 'g8'); c.vl(29, 15, 28, 'g8'); c.hl(2, 29, 28, 'g8');
  c.hl(3, 28, 27, 'g5');
  c.rect(10, 18, 21, 26, 'w'); c.box(10, 18, 21, 26, 'g7'); c.text(F4x5, 'IN', 12, 20, 'g8');
  dropShadow(c, 1, 1);
}
function tray16(c) {
  grid(c, [
    '....|....|....|....',
    '.888|88..|....|....',
    '.833|3888|8888|888.',
    '.838|8888|8888|838.',
    '.838|W444|444W|838.',
    '.838|WWWW|WWWW|838.',
    '.838|W444|44WW|838.',
    '.888|8888|8888|888.',
    '.812|2222|2222|228.',
    '.812|2888|2882|228.',
    '.812|2282|2828|228.',
    '.812|2282|2828|228.',
    '.812|2282|2828|228.',
    '.812|2888|2828|228.',
    '.866|6666|6666|668.',
    '.888|8888|8888|888.',
  ]);
}

module.exports = [
  { name: 'D32lib', title: 'Deep32 Library (front panel position 9)', ref: 'LIBRARY-DESIGN 2.2, 3.6; DISTRO-DESIGN 6.4', concept: 'an open reference volume with "26" on its spine', draw: { l: lib48, m: lib32, t: lib16 } },
  { name: 'D32srch', title: 'Library Search', ref: 'LOOK-SOUND 6.2; DISTRO-DESIGN 6.4 (Deep32 Library subpanel)', concept: 'card-catalogue drawer with one raised card', draw: { l: srch48, m: srch32, t: srch16 } },
  { name: 'D32read', title: 'Reader', ref: 'DISTRO-DESIGN 6.4 (Deep32 Library subpanel); concept changed from LOOK-SOUND 6.2 (an open book) because Deep32 Library now owns the open volume', concept: 'a lectern holding an open document, with a ribbon', draw: { l: read48, m: read32, t: read16 } },
  { name: 'D32acq', title: 'Acquisition', ref: 'DISTRO-DESIGN 6.9 / fidelity 23 (revised brief)', concept: 'a period wire in-tray with a document in it and a library card clipped to its edge; no arrow', draw: { l: acq48, m: acq32, t: acq16 } },
  { name: 'D32look', title: 'Lookup Desk', ref: 'DISTRO-DESIGN 6.4 (Deep32 Library subpanel); ISO-DESIGN 3.2', concept: 'a thumb-indexed reference book with lettered tabs', draw: { l: look48, m: look32, t: look16 } },
  { name: 'D32man', title: 'Manual Pages (Cisco commands and RFCs)', ref: 'DISTRO-DESIGN 6.4 (Deep32 Library subpanel)', concept: 'a manual page with NAME and section heads in a ring binder', draw: { l: man48, m: man32, t: man16 } },
  { name: 'D32tray', title: 'In-Tray', ref: 'DISTRO-DESIGN 6.4 (Deep32 Library subpanel), 5.2 (downloads land in the In-Tray)', concept: 'a folder holding papers, with an engraved "IN" plate', draw: { l: tray48, m: tray32, t: tray16 } },
];
