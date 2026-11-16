'use strict';
// System controls: Event Log (front panel position 5, replaces Mail), the status/station tile glyphs (positions 6
// and 8; DISTRO-DESIGN 6.5), Isolate, the status window, Information Manager (Help subpanel), System Load, and the
// AIR-GAPPED badge glyph (LOOK-SOUND 6.5).
const { dropShadow, block, grid, checker } = require('../../lib/kit');

// DISTRO-DESIGN 6.5: tile colours are fixed, never palette-derived.
const TILE = { ok: '#7FBF7F', amber: '#D9B04A', red: '#D46A6A' };

// ---------- D32log / D32logN: Event Log, a ledger with a hash chain; a flag raised when there is something new ----------
function ledger48(c, flag) {
  // book block (pages show at the right and bottom), dark cover, spine with chain links, label
  c.rect(8, 10, 42, 42, 'w'); for (let y = 12; y <= 41; y += 2) c.hl(40, 42, y, 'g3'); c.hl(9, 42, 41, 'g3');
  c.box(8, 10, 42, 42, 'g8');
  c.rect(5, 8, 39, 40, 'g6'); c.box(5, 8, 39, 40, 'g8'); c.hl(6, 38, 9, 'g4'); c.vl(10, 9, 39, 'g5');
  c.rect(5, 8, 10, 40, 'g8');
  for (let i = 0; i < 6; i++) { const y = 11 + i * 5; c.box(6, y, 9, y + 3, 'g2'); if (i < 5) c.vl(8, y + 3, y + 5, 'g3'); }
  c.rect(16, 15, 33, 23, 'w'); c.box(16, 15, 33, 23, 'g8'); c.hl(18, 31, 18, 'g5'); c.hl(18, 27, 20, 'g5');
  c.hl(14, 36, 33, 'g4'); c.hl(14, 36, 35, 'g4');
  if (flag) {
    c.vl(41, 3, 26, 'g7'); c.vl(42, 3, 26, 'g4');
    c.rect(33, 3, 40, 10, 'r'); c.box(33, 3, 40, 10, 'g8'); c.hl(34, 39, 4, 'w');
  }
  dropShadow(c, 2, 2);
}
function ledger32(c, flag) {
  c.rect(6, 7, 28, 28, 'w'); c.hl(7, 28, 27, 'g3'); c.box(6, 7, 28, 28, 'g8');
  c.rect(3, 5, 26, 26, 'g6'); c.box(3, 5, 26, 26, 'g8'); c.hl(4, 25, 6, 'g4');
  c.rect(3, 5, 7, 26, 'g8');
  for (let i = 0; i < 4; i++) c.box(4, 8 + i * 5, 6, 10 + i * 5, 'g2');
  c.rect(11, 10, 22, 15, 'w'); c.box(11, 10, 22, 15, 'g8'); c.hl(13, 20, 12, 'g5');
  c.hl(10, 23, 21, 'g4');
  if (flag) { c.vl(27, 1, 17, 'g7'); c.rect(21, 1, 26, 6, 'r'); c.box(21, 1, 26, 6, 'g8'); }
  dropShadow(c, 1, 1);
}
function ledger16(c, flag) {
  grid(c, [
    '....|....|....|....',
    '....|....|....|....',
    '8888|8888|8888|8...',
    '8828|6666|6666|8...',
    '8888|6888|8886|88..',
    '8828|68WW|WW86|8W8.',
    '8888|6888|8886|8W8.',
    '8828|6666|6666|8W8.',
    '8888|6444|4446|8W8.',
    '8828|6666|6666|8W8.',
    '8888|6444|4446|8W8.',
    '8828|6666|6666|8W8.',
    '8888|8888|8888|8W8.',
    '.888|8888|8888|888.',
    '....|....|....|....',
    '....|....|....|....',
  ]);
  if (flag) grid(c, [
    '....|....|..88|88..',
    '....|....|..8R|R87.',
    '....|....|..88|887.',
    '....|....|....|.7..',
    '....|....|....|.7..',
    '....|....|....|.7..',
    '....|....|....|....',
    '....|....|....|....',
    '....|....|....|....',
    '....|....|....|....',
    '....|....|....|....',
    '....|....|....|....',
    '....|....|....|....',
    '....|....|....|....',
    '....|....|....|....',
    '....|....|....|....',
  ]);
}

// ---------- D32tile*: the 16x16 tile glyph (menus, the status window, Fathom's status line) ----------
// One 14x9 bevelled rectangle with the state's fill and shape: solid = OK, hatched = degraded, X = failed,
// hollow = off or unknown (DISTRO-DESIGN 6.5). No glint.
function tile16(state) {
  return c => {
    const x0 = 1, y0 = 4, x1 = 14, y1 = 12;
    c.rect(x0, y0, x1, y1, 'bg');
    c.bevel(x0, y0, x1, y1, 1, 'ts', 'bs');
    const ix0 = x0 + 1, iy0 = y0 + 1, ix1 = x1 - 1, iy1 = y1 - 1;
    if (state === 'ok') c.rect(ix0, iy0, ix1, iy1, TILE.ok);
    if (state === 'degraded') c.shape(ix0, iy0, ix1, iy1, (x, y) => ((x + y) % 4) < 2, TILE.amber);
    if (state === 'failed') {
      for (let i = 0; i <= 6; i++) { const x = ix0 + Math.round(i * (ix1 - ix0) / 6); c.rect(x, iy0 + i, x + 1, iy0 + i, TILE.red); c.rect(ix1 - (x - ix0) - 1, iy0 + i, ix1 - (x - ix0), iy0 + i, TILE.red); }
    }
    if (state === 'off') c.box(ix0 + 1, iy0 + 1, ix1 - 1, iy1 - 1, 'g7');
  };
}

// ---------- D32iso: Isolate, a knife switch thrown open ----------
function iso48(c) {
  // slate base with two terminal blocks
  block(c, 4, 31, 43, 42, 'g6', 'g4', 'g7', 'g8');
  c.hl(5, 42, 41, 'g7');
  for (const x of [9, 33]) { c.rect(x, 33, x + 5, 38, 'g3'); c.box(x, 33, x + 5, 38, 'g8'); c.set(x + 2, 35, 'g8'); c.set(x + 3, 36, 'g8'); }
  // open jaw on the left (two clips), hinge on the right
  c.rect(10, 22, 11, 32, 'g2'); c.rect(14, 22, 15, 32, 'g2'); c.vl(10, 22, 32, 'g1'); c.vl(15, 22, 32, 'g5'); c.hl(10, 15, 32, 'g8');
  c.vl(9, 22, 32, 'g8'); c.vl(16, 22, 32, 'g8'); c.hl(9, 11, 21, 'g8'); c.hl(14, 16, 21, 'g8');
  c.rect(33, 24, 38, 32, 'g2'); c.box(33, 24, 38, 32, 'g8'); c.oval(34, 25, 37, 28, 'g6');
  // blade thrown up and to the left, insulated handle with a red knob
  for (let i = 0; i < 3; i++) c.line(35, 26 + i - 1, 17, 8 + i, i === 0 ? 'g1' : i === 1 ? 'g3' : 'g5');
  c.line(35, 29, 17, 11, 'g8'); c.line(36, 25, 18, 7, 'g8');
  c.line(17, 8, 12, 4, 'k'); c.line(17, 9, 12, 5, 'k'); c.line(18, 9, 13, 5, 'g7');
  c.oval(7, 1, 13, 7, 'r'); c.ring(7, 1, 13, 7, 1, 'g8'); c.set(9, 3, 'w');
  dropShadow(c, 2, 2);
}
function iso32(c) {
  block(c, 2, 21, 29, 28, 'g6', 'g4', 'g7', 'g8');
  for (const x of [5, 21]) { c.rect(x, 23, x + 3, 26, 'g3'); c.box(x, 23, x + 3, 26, 'g8'); }
  c.rect(6, 15, 7, 21, 'g2'); c.rect(9, 15, 10, 21, 'g2'); c.vl(5, 15, 21, 'g8'); c.vl(11, 15, 21, 'g8');
  c.rect(22, 16, 25, 21, 'g2'); c.box(22, 16, 25, 21, 'g8');
  c.line(23, 17, 11, 5, 'g2'); c.line(24, 17, 12, 5, 'g4'); c.line(23, 18, 11, 6, 'g8'); c.line(24, 16, 12, 4, 'g8');
  c.line(11, 5, 8, 2, 'k'); c.oval(4, 0, 8, 4, 'r'); c.ring(4, 0, 8, 4, 1, 'g8');
  dropShadow(c, 1, 1);
}
function iso16(c) {
  grid(c, [
    '.888|....|....|....',
    '8RWR|8...|....|....',
    '8RRR|8...|....|....',
    '.888|K...|....|....',
    '....|K8..|....|....',
    '....|.828|....|....',
    '....|..82|8...|....',
    '....|...8|28..|....',
    '.8.8|...8|328.|....',
    '.828|....|8328|....',
    '.828|....|.832|8...',
    '.828|....|.888|8...',
    '8888|8888|8888|888.',
    '8433|4444|4443|348.',
    '8777|7777|7777|778.',
    '8888|8888|8888|888.',
  ]);
}

// ---------- D32stat: the Deep32 status window, a bezel with the six tiles ----------
function stat48(c) {
  block(c, 3, 9, 44, 38, 'g3', 'g1', 'g6', 'g8');
  c.rect(6, 12, 41, 35, 'g5'); c.hl(6, 41, 12, 'g7'); c.vl(6, 12, 35, 'g7'); c.hl(6, 41, 35, 'g1'); c.vl(41, 12, 35, 'g1');
  const states = ['ok', 'ok', 'degraded', 'ok', 'off', 'failed'];
  states.forEach((s, i) => {
    const x0 = 9 + (i % 3) * 11, y0 = 15 + Math.floor(i / 3) * 10, x1 = x0 + 8, y1 = y0 + 7;
    c.rect(x0, y0, x1, y1, 'g3'); c.bevel(x0, y0, x1, y1, 1, 'g1', 'g7');
    if (s === 'ok') c.rect(x0 + 1, y0 + 1, x1 - 1, y1 - 1, TILE.ok);
    if (s === 'degraded') c.shape(x0 + 1, y0 + 1, x1 - 1, y1 - 1, (x, y) => ((x + y) % 4) < 2, TILE.amber);
    if (s === 'failed') { c.line(x0 + 1, y0 + 1, x1 - 1, y1 - 1, TILE.red); c.line(x0 + 2, y0 + 1, x1 - 1, y1 - 2, TILE.red); c.line(x1 - 1, y0 + 1, x0 + 1, y1 - 1, TILE.red); c.line(x1 - 2, y0 + 1, x0 + 1, y1 - 2, TILE.red); }
    if (s === 'off') c.box(x0 + 2, y0 + 2, x1 - 2, y1 - 2, 'g7');
  });
  dropShadow(c, 2, 2);
}
function stat32(c) {
  block(c, 2, 6, 29, 25, 'g3', 'g1', 'g6', 'g8');
  c.rect(4, 8, 27, 23, 'g5');
  const states = ['ok', 'ok', 'degraded', 'ok', 'off', 'failed'];
  states.forEach((s, i) => {
    const x0 = 6 + (i % 3) * 7, y0 = 10 + Math.floor(i / 3) * 6, x1 = x0 + 5, y1 = y0 + 4;
    c.rect(x0, y0, x1, y1, 'g3'); c.bevel(x0, y0, x1, y1, 1, 'g1', 'g7');
    if (s === 'ok') c.rect(x0 + 1, y0 + 1, x1 - 1, y1 - 1, TILE.ok);
    if (s === 'degraded') c.shape(x0 + 1, y0 + 1, x1 - 1, y1 - 1, (x, y) => ((x + y) & 1) === 0, TILE.amber);
    if (s === 'failed') { c.line(x0 + 1, y0 + 1, x1 - 1, y1 - 1, TILE.red); c.line(x1 - 1, y0 + 1, x0 + 1, y1 - 1, TILE.red); }
    if (s === 'off') c.set(x0 + 2, y0 + 2, 'g7');
  });
  dropShadow(c, 1, 1);
}
function stat16(c) {
  grid(c, [
    '....|....|....|....',
    '....|....|....|....',
    '8888|8888|8888|888.',
    '8111|1111|1111|118.',
    '81GG|G5GG|G5AA|A68.',
    '81GG|G5GG|G5AA|A68.',
    '8155|5555|5555|568.',
    '81GG|G577|75RR|R68.',
    '81GG|G575|75RR|R68.',
    '81GG|G577|75RR|R68.',
    '8666|6666|6666|668.',
    '8888|8888|8888|888.',
    '....|....|....|....',
    '....|....|....|....',
    '....|....|....|....',
    '....|....|....|....',
  ], { G: TILE.ok, A: TILE.amber, R: TILE.red });
}

// ---------- D32info: Information Manager, three upright manuals with coloured tab dividers and a ribbon ----------
const navy = (x, y) => (((x + y) & 1) ? 'b' : 'k');
const maroon = (x, y) => (((x + y) & 1) ? 'r' : 'k');
const forest = (x, y) => (((x + y) & 1) ? 'g' : 'k');
function info48(c) {
  // tab dividers standing proud of the page tops
  const tabs = [[8, 'r'], [19, 'y'], [30, 'g'], [13, 'b'], [24, 'c']];
  const spines = [[5, navy], [16, (x, y) => 'g5'], [27, maroon]];
  for (const [x, col] of tabs) { c.rect(x, 4, x + 4, 8, col); c.box(x, 4, x + 4, 9, 'g8'); }
  spines.forEach(([x, f], i) => {
    const top = 7 + (i === 1 ? 2 : 0);
    c.rect(x, top, x + 10, 42, f); c.box(x, top, x + 10, 42, 'g8');
    c.vl(x + 1, top + 1, 41, i === 1 ? 'g3' : 'g6');
    c.rect(x + 2, top + 5, x + 8, top + 9, 'w'); c.box(x + 2, top + 5, x + 8, top + 9, 'g8'); c.hl(x + 3, x + 7, top + 7, 'g5');
    c.hl(x + 1, x + 9, 37, 'y'); c.hl(x + 1, x + 9, 39, 'y');
  });
  // page-marker ribbon hanging from the third manual
  c.vl(34, 42, 46, 'r'); c.vl(35, 42, 45, 'r'); c.set(35, 46, 'r'); c.set(34, 47, null);
  // a shelf
  c.rect(2, 43, 41, 44, 'g4'); c.hl(2, 41, 45, 'g8');
  dropShadow(c, 2, 0);
}
function info32(c) {
  for (const [x, col] of [[5, 'r'], [12, 'y'], [19, 'g']]) { c.rect(x, 2, x + 3, 5, col); c.box(x, 2, x + 3, 5, 'g8'); }
  [[3, navy], [11, () => 'g5'], [19, maroon]].forEach(([x, f], i) => {
    c.rect(x, 4 + (i === 1 ? 1 : 0), x + 7, 27, f); c.box(x, 4 + (i === 1 ? 1 : 0), x + 7, 27, 'g8');
    c.rect(x + 2, 8, x + 5, 11, 'w'); c.box(x + 2, 8, x + 5, 11, 'g8');
    c.hl(x + 1, x + 6, 24, 'y');
  });
  c.vl(24, 27, 30, 'r'); c.rect(1, 28, 28, 28, 'g4'); c.hl(1, 28, 29, 'g8');
  dropShadow(c, 1, 0);
}
function info16(c) {
  grid(c, [
    '..RR|..YY|..GG|....',
    '8888|8888|8888|8...',
    '8NN8|5558|mmm8|....',
    '8NN8|5558|mmm8|....',
    '8WW8|WWW8|WWW8|....',
    '8NN8|5558|mmm8|....',
    '8NN8|5558|mmm8|....',
    '8NN8|5558|mmm8|....',
    '8NN8|5558|mmm8|....',
    '8NN8|5558|mmm8|....',
    '8YY8|YYY8|YYY8|....',
    '8NN8|5558|mmm8|....',
    '8888|8888|8888|8...',
    '4444|4444|4R44|4...',
    '8888|8888|8R88|8...',
    '....|....|.R..|....',
  ], { N: navy, m: maroon });
}

// ---------- D32load: System Load, a strip-chart recorder ----------
function load48(c) {
  block(c, 3, 6, 44, 40, 'g3', 'g1', 'g6', 'g8');
  c.rect(7, 10, 38, 31, 'w'); c.box(6, 9, 39, 32, 'g7');
  for (let x = 10; x <= 37; x += 5) c.vl(x, 10, 31, 'g2');
  for (let y = 13; y <= 30; y += 5) c.hl(7, 38, y, 'g2');
  const h = [4, 6, 5, 9, 12, 10, 8, 14, 17, 13, 11, 9, 12, 15, 19, 16, 12, 10, 8, 11, 13, 10, 7, 6, 9, 12, 14, 11, 9, 8, 10];
  h.forEach((v, i) => c.vl(7 + i, 31 - v, 31, i % 2 ? 'g5' : 'g4'));
  c.vl(38, 31 - 12, 31, 'g5');
  // pen arm
  c.line(39, 19, 43, 14, 'g7'); c.rect(37, 18, 39, 20, 'r');
  // paper spilling out of the bottom
  c.rect(10, 41, 36, 44, 'w'); c.box(10, 40, 36, 44, 'g8'); c.hl(11, 35, 42, 'g2');
  c.rect(8, 34, 39, 37, 'g5'); c.hl(8, 39, 34, 'g7');
  dropShadow(c, 2, 2);
}
function load32(c) {
  block(c, 2, 4, 29, 26, 'g3', 'g1', 'g6', 'g8');
  c.rect(5, 7, 25, 20, 'w'); c.box(4, 6, 26, 21, 'g7');
  for (let x = 8; x <= 24; x += 4) c.vl(x, 7, 20, 'g2');
  const h = [3, 4, 6, 8, 7, 5, 9, 11, 8, 6, 7, 9, 12, 10, 7, 5, 6, 8, 9, 7, 5];
  h.forEach((v, i) => c.vl(5 + i, 20 - v, 20, i % 2 ? 'g5' : 'g4'));
  c.rect(24, 11, 26, 13, 'r');
  c.rect(7, 27, 24, 29, 'w'); c.box(7, 26, 24, 29, 'g8');
  dropShadow(c, 1, 1);
}
function load16(c) {
  grid(c, [
    '....|....|....|....',
    '8888|8888|8888|888.',
    '81WW|WWWW|WWWW|W68.',
    '81WW|WWWW|WWW5|R68.',
    '81WW|WW5W|WWW5|568.',
    '81WW|W55W|W5W5|568.',
    '81W5|W555|W555|568.',
    '81W5|5555|5555|568.',
    '8155|5555|5555|568.',
    '8166|6666|6666|668.',
    '8888|8888|8888|888.',
    '..8W|WWWW|WWWW|8...',
    '..88|8888|8888|8...',
    '....|....|....|....',
    '....|....|....|....',
    '....|....|....|....',
  ]);
}

const TILE_HEX = Object.values(TILE);
module.exports = [
  { name: 'D32log', title: 'Event Log (front panel position 5; replaces Mail)', ref: 'LOOK-SOUND 6.2; DISTRO-DESIGN 6.4', concept: 'a ledger with a hash chain on its spine', draw: { l: c => ledger48(c, false), m: c => ledger32(c, false), t: c => ledger16(c, false) } },
  { name: 'D32logN', title: 'Event Log, new events (the raised flag)', ref: 'LOOK-SOUND 6.2; DISTRO-DESIGN 6.4', concept: 'the ledger with a red flag raised', draw: { l: c => ledger48(c, true), m: c => ledger32(c, true), t: c => ledger16(c, true) } },
  { name: 'D32tileOK', title: 'Tile glyph: OK (solid green)', ref: 'DISTRO-DESIGN 6.5', concept: '14x9 bevelled tile, solid', allow: TILE_HEX, draw: { t: tile16('ok') } },
  { name: 'D32tileDeg', title: 'Tile glyph: degraded (amber hatch)', ref: 'DISTRO-DESIGN 6.5', concept: '14x9 bevelled tile, diagonal hatch', allow: TILE_HEX, draw: { t: tile16('degraded') } },
  { name: 'D32tileFail', title: 'Tile glyph: failed (red X)', ref: 'DISTRO-DESIGN 6.5', concept: '14x9 bevelled tile, X', allow: TILE_HEX, draw: { t: tile16('failed') } },
  { name: 'D32tileOff', title: 'Tile glyph: off or unknown (hollow)', ref: 'DISTRO-DESIGN 6.5', concept: '14x9 bevelled tile, hollow', allow: TILE_HEX, draw: { t: tile16('off') } },
  { name: 'D32iso', title: 'Isolate (status subpanel, behind a confirm)', ref: 'DISTRO-DESIGN 6.4 (position 6 subpanel), 6.6 (Mod4+I)', concept: 'a knife switch thrown open', draw: { l: iso48, m: iso32, t: iso16 } },
  { name: 'D32stat', title: 'Deep32 status window', ref: 'DISTRO-DESIGN 6.4 (position 6 push), 6.5', concept: 'a bezel holding the six status tiles', allow: TILE_HEX, draw: { l: stat48, m: stat32, t: stat16 } },
  { name: 'D32info', title: 'Information Manager (Help subpanel, v1b)', ref: 'DISTRO-DESIGN 6.9 / fidelity 23 (revised brief), 6.4', concept: 'three upright manuals with coloured tab dividers and a page-marker ribbon; no chain link', draw: { l: info48, m: info32, t: info16 } },
  { name: 'D32load', title: 'System Load (xload + systat in System_Admin)', ref: 'DISTRO-DESIGN 3.1 (System Load, new v1)', concept: 'a strip-chart recorder drawing a load trace', draw: { l: load48, m: load32, t: load16 } },
];
