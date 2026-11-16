'use strict';
// Personal Applications subpanel (front panel position 4): Sessions, Codex Console, NEdit, Notes.
// Terminal and Icon Editor keep CDE's own icons (LOOK-SOUND 6.1).
const { dropShadow, block, monitor, page, grid } = require('../../lib/kit');
const { F3x5 } = require('../../lib/font');

// ---------- D32sess: Sessions (SecureCRT-class manager): a console plug over a tab strip ----------
function sess48(c) {
  // tabs
  c.rect(4, 3, 15, 9, 'w'); c.box(4, 3, 15, 9, 'g8'); c.hl(6, 13, 6, 'g4');
  for (const x of [16, 28]) { c.rect(x, 5, x + 11, 9, 'g3'); c.box(x, 5, x + 11, 9, 'g8'); c.hl(x + 1, x + 10, 6, 'g1'); }
  // window frame and screen
  block(c, 3, 9, 44, 41, 'g5', 'g3', 'g7', 'g8');
  c.rect(4, 9, 14, 9, 'w');
  c.rect(5, 12, 42, 39, 'g8');
  c.hl(8, 16, 16, 'g'); c.hl(18, 22, 16, 'g4');
  c.hl(8, 13, 20, 'g4'); c.hl(15, 30, 20, 'y');
  c.hl(8, 24, 24, 'g');
  c.hl(8, 20, 28, 'r');
  c.hl(8, 11, 32, 'g'); c.rect(13, 31, 15, 33, 'w');
  // DB-9 plug and its cable
  c.rect(31, 36, 33, 45, 'g6'); c.vl(31, 36, 45, 'g4');
  c.poly([[33, 37], [46, 37], [44, 46], [35, 46]], 'g4');
  c.line(33, 37, 46, 37, 'g8'); c.line(46, 37, 44, 46, 'g8'); c.line(33, 37, 35, 46, 'g8'); c.hl(35, 44, 46, 'g8');
  c.hl(35, 44, 38, 'g2');
  for (const x of [36, 38, 40, 42]) c.set(x, 40, 'g8');
  for (const x of [37, 39, 41]) c.set(x, 43, 'g8');
  dropShadow(c, 2, 1);
}
function sess32(c) {
  c.rect(2, 2, 10, 6, 'w'); c.box(2, 2, 10, 6, 'g8');
  c.rect(11, 3, 19, 6, 'g3'); c.box(11, 3, 19, 6, 'g8');
  block(c, 1, 6, 29, 27, 'g5', 'g3', 'g7', 'g8'); c.hl(2, 9, 6, 'w');
  c.rect(3, 9, 27, 25, 'g8');
  c.hl(5, 10, 12, 'g'); c.hl(5, 8, 15, 'g4'); c.hl(10, 19, 15, 'y'); c.hl(5, 13, 18, 'r'); c.hl(5, 6, 21, 'g'); c.rect(8, 21, 9, 22, 'w');
  c.rect(20, 23, 21, 30, 'g6');
  c.poly([[21, 24], [31, 24], [30, 30], [23, 30]], 'g4');
  c.line(21, 24, 31, 24, 'g8'); c.line(31, 24, 30, 30, 'g8'); c.line(21, 24, 23, 30, 'g8'); c.hl(23, 30, 30, 'g8');
  for (const x of [24, 26, 28]) c.set(x, 26, 'g8');
  dropShadow(c, 1, 1);
}
function sess16(c) {
  grid(c, [
    '8888|8888|....|....',
    '8WW8|3338|....|....',
    '8888|8888|8888|888.',
    '8888|8888|8888|888.',
    '8GGG|8888|8888|888.',
    '8888|YYYY|8888|888.',
    '8RRR|R888|8888|888.',
    '8G8W|8888|8888|888.',
    '8888|8888|8888|888.',
    '8888|8888|8888|888.',
    '8888|8888|8888|888.',
    '....|....|6888|8888',
    '....|....|6844|4448',
    '....|....|.884|8488',
    '....|....|..84|4448',
    '....|....|..88|888.',
  ]);
  // grey frame round the screen (rows 2-10) so it reads as a window, not a black block
  c.vl(0, 2, 10, 'g5'); c.vl(14, 2, 10, 'g5'); c.hl(0, 14, 10, 'g5'); c.hl(0, 14, 2, 'g5');
  c.box(0, 2, 14, 10, 'g8');
}

// ---------- D32ask: Codex Console, a terminal with a question balloon ----------
function questionMark(c, x, y, tok) {
  c.grid(['.###.', '##.##', '...##', '..##.', '..##.', '.....', '..##.'], { '#': tok }, x, y);
}
function ask48(c) {
  const [sx0, sy0, sx1] = monitor(c, 4, 7, 37, 35, { t: 2, chin: 3 });
  for (const y of [sy0 + 3, sy0 + 8, sy0 + 13]) { c.set(sx0 + 2, y, 'g'); c.hl(sx0 + 4, sx1 - 6 - (y % 3) * 2, y, 'g'); }
  c.rect(sx0 + 4, sy0 + 17, sx0 + 5, sy0 + 18, 'g');
  c.rect(14, 36, 27, 38, 'g4'); c.hl(14, 27, 36, 'g2'); c.box(14, 36, 27, 38, 'g8');
  c.poly([[5, 40], [36, 40], [39, 45], [2, 45]], 'g2'); c.line(5, 40, 36, 40, 'g8'); c.line(36, 40, 39, 45, 'g8'); c.line(5, 40, 2, 45, 'g8'); c.hl(2, 39, 45, 'g8');
  for (let x = 6; x <= 35; x += 3) c.set(x, 42, 'g5');
  for (let x = 5; x <= 36; x += 3) c.set(x, 43, 'g5');
  // balloon
  c.oval(25, 1, 45, 16, 'w');
  c.poly([[29, 12], [34, 14], [24, 21]], 'w');
  c.ring(25, 1, 45, 16, 1, 'g8', (dx, dy) => !(dx < -1 && dy > 2));
  c.line(29, 13, 24, 21, 'g8'); c.line(24, 21, 33, 15, 'g8');
  questionMark(c, 33, 5, 'b');
  dropShadow(c, 2, 2);
}
function ask32(c) {
  const [sx0, sy0, sx1] = monitor(c, 2, 5, 24, 23, { t: 1, chin: 2 });
  for (const y of [sy0 + 2, sy0 + 6, sy0 + 10]) { c.set(sx0 + 1, y, 'g'); c.hl(sx0 + 3, sx1 - 3, y, 'g'); }
  c.rect(9, 24, 17, 25, 'g4'); c.box(9, 24, 17, 25, 'g8');
  c.rect(2, 27, 25, 29, 'g2'); c.box(2, 27, 25, 29, 'g8'); for (let x = 4; x <= 23; x += 2) c.set(x, 28, 'g5');
  c.oval(17, 0, 31, 11, 'w'); c.ring(17, 0, 31, 11, 1, 'g8', (dx, dy) => !(dx < -1 && dy > 2));
  c.poly([[19, 8], [22, 10], [16, 14]], 'w'); c.line(19, 9, 16, 14, 'g8'); c.line(16, 14, 22, 10, 'g8');
  c.grid(['.##.', '#..#', '..#.', '.#..', '....', '.#..'], { '#': 'b' }, 22, 2);
  dropShadow(c, 1, 1);
}
function ask16(c) {
  grid(c, [
    '....|....|.888|88..',
    '....|....|8WWW|WW8.',
    '....|....|8WBB|WW8.',
    '8888|8888|8WWB|WW8.',
    '8111|1111|8WBW|W8..',
    '81GG|GGG8|8WWW|8...',
    '8188|8888|8BW8|....',
    '81GG|GGGG|8888|....',
    '8188|8888|8838|....',
    '81G8|8888|8838|....',
    '8155|5555|5558|....',
    '8888|8888|8888|....',
    '....|8444|48..|....',
    '.888|8888|8888|8...',
    '8222|2222|2222|28..',
    '8888|8888|8888|88..',
  ]);
}

// ---------- D32edit: NEdit, the programmer's editor: a source window with a line-number gutter ----------
function edit48(c) {
  block(c, 4, 4, 43, 43, 'g3', 'g1', 'g6', 'g8');
  c.rect(5, 5, 42, 9, 'g5'); c.hl(7, 14, 7, 'g1'); c.hl(17, 21, 7, 'g1'); c.hl(24, 29, 7, 'g1');
  c.rect(6, 11, 41, 41, 'w'); c.box(6, 11, 41, 41, 'g6');
  c.rect(7, 12, 11, 40, 'g2'); c.vl(12, 12, 40, 'g4');
  const code = [[0, 'b', 6, 'k', 10], [1, 'k', 14], [1, 'b', 4, 'r', 9], [2, 'k', 12], [2, 'k', 8, 'g', 6], [1, 'k', 3], [1, 'b', 5, 'k', 9], [2, 'r', 13], [1, 'k', 3], [0, 'k', 2]];
  code.forEach((ln, i) => {
    const y = 14 + i * 3; c.set(9, y, 'g5'); if (i % 2) c.set(10, y, 'g5');
    let x = 15 + ln[0] * 4;
    for (let k = 1; k < ln.length; k += 2) { c.hl(x, Math.min(39, x + ln[k + 1] - 1), y, ln[k]); x += ln[k + 1] + 2; }
  });
  // text cursor (I-beam)
  c.vl(31, 31, 37, 'k'); c.hl(30, 32, 31, 'k'); c.hl(30, 32, 37, 'k');
  dropShadow(c, 2, 2);
}
function edit32(c) {
  block(c, 2, 2, 29, 29, 'g3', 'g1', 'g6', 'g8');
  c.rect(3, 3, 28, 6, 'g5'); c.hl(5, 9, 4, 'g1'); c.hl(12, 15, 4, 'g1');
  c.rect(4, 8, 27, 27, 'w'); c.box(4, 8, 27, 27, 'g6');
  c.rect(5, 9, 7, 26, 'g2'); c.vl(8, 9, 26, 'g4');
  const code = [[0, 'b', 4, 'k', 6], [1, 'k', 9], [1, 'b', 3, 'r', 6], [2, 'k', 8], [2, 'g', 7], [1, 'k', 2], [0, 'k', 2]];
  code.forEach((ln, i) => { const y = 10 + i * 2 + (i > 3 ? 1 : 0) + (i > 5 ? 1 : 0); let x = 10 + ln[0] * 3; for (let k = 1; k < ln.length; k += 2) { c.hl(x, Math.min(26, x + ln[k + 1] - 1), y, ln[k]); x += ln[k + 1] + 1; } });
  c.vl(21, 20, 25, 'k'); c.set(20, 20, 'k'); c.set(22, 20, 'k'); c.set(20, 25, 'k'); c.set(22, 25, 'k');
  dropShadow(c, 1, 1);
}
function edit16(c) {
  grid(c, [
    '8888|8888|8888|888.',
    '8555|5555|5555|558.',
    '8222|WWWW|WWWW|WW8.',
    '8224|WBBW|KKKK|WW8.',
    '8222|WWWK|KKKK|KW8.',
    '8224|WWWB|BWRR|RW8.',
    '8222|WWWW|WKKK|KW8.',
    '8224|WWWW|WGGG|GW8.',
    '8222|WWWK|KWWW|WW8.',
    '8224|WWWW|WWWK|WW8.',
    '8222|WKKW|WWWK|WW8.',
    '8222|WWWW|WWWK|WW8.',
    '8222|WWWW|WWKK|KW8.',
    '8666|6666|6666|668.',
    '8888|8888|8888|888.',
    '....|....|....|....',
  ]);
}

// ---------- D32note: Notes, an index card with a pin and a cite mark ----------
function note48(c) {
  c.rect(5, 10, 42, 38, 'w'); c.box(5, 10, 42, 38, 'g8'); c.vl(41, 11, 37, 'g2'); c.hl(6, 41, 37, 'g2');
  c.hl(6, 41, 14, 'r');
  for (let y = 19; y <= 35; y += 4) c.hl(6, 41, y, 'b');
  c.hl(9, 30, 17, 'g6'); c.hl(9, 27, 21, 'g6'); c.hl(9, 33, 25, 'g6'); c.hl(9, 22, 29, 'g6');
  c.text(F3x5, '[1]', 33, 31, 'b');
  // pin
  c.oval(33, 5, 40, 12, 'r'); c.set(35, 7, 'w'); c.set(34, 8, 'w'); c.ring(33, 5, 40, 12, 1, 'g8');
  c.vl(36, 13, 15, 'g6');
  dropShadow(c, 2, 2);
}
function note32(c) {
  c.rect(3, 7, 28, 25, 'w'); c.box(3, 7, 28, 25, 'g8'); c.hl(4, 27, 24, 'g2');
  c.hl(4, 27, 10, 'r'); for (let y = 13; y <= 22; y += 3) c.hl(4, 27, y, 'b');
  c.hl(6, 19, 12, 'g6'); c.hl(6, 17, 15, 'g6'); c.hl(6, 21, 18, 'g6');
  c.text(F3x5, '[1]', 19, 19, 'b');
  c.oval(21, 3, 26, 8, 'r'); c.set(22, 4, 'w'); c.ring(21, 3, 26, 8, 1, 'g8');
  dropShadow(c, 1, 1);
}
function note16(c) {
  grid(c, [
    '....|....|..88|8...',
    '....|....|.8RW|R8..',
    '....|....|.8RR|R8..',
    '8888|8888|8888|8888',
    '8RRR|RRRR|RRRR|RRR8',
    '8W66|6666|66WW|WWW8',
    '8BBB|BBBB|BBBB|BBB8',
    '8W66|6666|WWWW|WWW8',
    '8BBB|BBBB|BBBB|BBB8',
    '8W66|6666|66BB|WBB8',
    '8BBB|BBBB|BBBW|BWB8',
    '8WWW|WWWW|WWBB|WBB8',
    '8888|8888|8888|8888',
    '....|....|....|....',
    '....|....|....|....',
    '....|....|....|....',
  ]);
}

module.exports = [
  { name: 'D32sess', title: 'Sessions (SecureCRT-class session manager)', ref: 'DISTRO-DESIGN 3.4, 6.4 (Personal Applications, Lab), 6.9', concept: 'a console (DB-9) plug over a tabbed terminal window', draw: { l: sess48, m: sess32, t: sess16 } },
  { name: 'D32ask', title: 'Codex Console', ref: 'LOOK-SOUND 6.2 (D32ask); DISTRO-DESIGN 6.4 (Personal Applications)', concept: 'a terminal with a question balloon', draw: { l: ask48, m: ask32, t: ask16 } },
  { name: 'D32edit', title: 'NEdit (programmer\'s editor)', ref: 'DISTRO-DESIGN 3.3, 6.4 (Personal Applications)', concept: 'a source window with a line-number gutter and coloured code', draw: { l: edit48, m: edit32, t: edit16 } },
  { name: 'D32note', title: 'Notes', ref: 'LOOK-SOUND 6.2 (D32note); DISTRO-DESIGN 6.4 (Personal Applications)', concept: 'an index card with a pin and a cite mark', draw: { l: note48, m: note32, t: note16 } },
];
