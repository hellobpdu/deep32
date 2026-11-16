'use strict';
// Lab subpanel (front panel position 8): Wireshark (the protocol analyser), Capture, Neighbours, OUI Lookup,
// Network Calculator (DISTRO-DESIGN 6.4).
const { dropShadow, block, grid, checker } = require('../../lib/kit');
const { F3x5 } = require('../../lib/font');

const forest = (x, y) => (((x + y) & 1) ? 'g' : 'k');
const teal = (x, y) => (((x + y) & 1) ? 'c' : 'g4');       // a muted console-cable blue

// 1U chassis with LEDs and ports
function chassis(c, x0, y0, x1, y1, leds, ports) {
  c.rect(x0, y0, x1, y1, 'g3'); c.hl(x0, x1, y0, 'g1'); c.hl(x0, x1, y1, 'g6'); c.box(x0 - 1, y0 - 1, x1 + 1, y1 + 1, 'g8');
  for (const x of leds) c.set(x, y0 + 2, 'g');
  for (const x of ports) { c.rect(x, y0 + 2, x + 1, y1 - 2, 'g8'); }
}

// ---------- D32shk: Wireshark, drawn as a period luggable protocol analyser (no vendor or project marks) ----------
function shk48(c) {
  // carrying handle
  c.rect(15, 3, 32, 5, 'g6'); c.box(15, 3, 32, 9, 'g8'); c.rect(18, 6, 29, 9, null); c.hl(16, 31, 4, 'g4');
  block(c, 2, 9, 45, 44, 'g4', 'g2', 'g6', 'g8');
  // screen: column header and packet rows in three colours, then a hex pane
  c.rect(6, 12, 41, 32, 'g8');
  c.hl(7, 40, 13, 'g4');
  const rows = ['g', 'g', 'c', 'y', 'g', 'c', 'c', 'r', 'g'];
  rows.forEach((col, i) => { const y = 15 + i * 2; c.hl(7, 10, y, 'g5'); c.hl(12, 22 - (i % 3) * 2, y, col); c.hl(25, 38 - (i % 4) * 3, y, 'g5'); });
  c.rect(7, 27, 40, 31, 'g7');
  for (let x = 8; x <= 38; x += 3) { c.set(x, 28, 'g3'); c.set(x + 1, 28, 'g3'); c.set(x, 30, 'g3'); }
  // keyboard strip and the probe socket
  for (let x = 6; x <= 41; x += 3) { c.rect(x, 35, x + 1, 36, 'g1'); c.rect(x + 1, 38, x + 2, 39, 'g1'); }
  c.rect(38, 40, 42, 42, 'g8');
  // probe lead with a clip
  c.line(42, 41, 46, 41, 'k');
  dropShadow(c, 2, 2);
}
function shk32(c) {
  c.rect(10, 2, 21, 3, 'g6'); c.box(10, 2, 21, 6, 'g8'); c.rect(12, 4, 19, 6, null);
  block(c, 1, 6, 30, 29, 'g4', 'g2', 'g6', 'g8');
  c.rect(4, 9, 27, 21, 'g8'); c.hl(5, 26, 10, 'g4');
  ['g', 'c', 'y', 'g', 'r'].forEach((col, i) => { const y = 12 + i * 2; c.hl(5, 7, y, 'g5'); c.hl(9, 16 - (i % 2) * 2, y, col); c.hl(18, 25 - i, y, 'g5'); });
  for (let x = 4; x <= 27; x += 3) c.rect(x, 24, x + 1, 25, 'g1');
  dropShadow(c, 1, 1);
}
function shk16(c) {
  grid(c, [
    '....|8888|8888|....',
    '....|8..8|8..8|....',
    '8888|8888|8888|8888',
    '8288|8888|8888|8868',
    '8288|GGG8|5558|8868',
    '8288|CC88|5588|8868',
    '8288|YYY8|5558|8868',
    '8288|RR88|5888|8868',
    '8288|8888|8888|8868',
    '8244|4444|4444|4468',
    '824W|4W4W|4W4W|4468',
    '8244|4444|4444|4468',
    '8666|6666|6666|6668',
    '8888|8888|8888|8888',
    '....|....|....|....',
    '....|....|....|....',
  ]);
}

// ---------- D32capt: Capture (d32-capture), a reel-to-reel recorder taking packets to tape ----------
function reel(c, cx, cy, r) {
  c.oval(cx - r, cy - r, cx + r, cy + r, 'g6'); c.ring(cx - r, cy - r, cx + r, cy + r, 1, 'g8');
  c.oval(cx - r + 2, cy - r + 2, cx + r - 2, cy + r - 2, 'g5');
  c.ring(cx - r + 1, cy - r + 1, cx + r - 1, cy + r - 1, 1, 'g4', (dx, dy) => dx + dy < 0);
  for (const [dx, dy] of [[0, -1], [1, 1], [-1, 1]]) c.line(cx, cy, cx + Math.round(dx * (r - 3)), cy + Math.round(dy * (r - 3) * (dy < 0 ? 1 : 0.8)), 'g2');
  c.oval(cx - 2, cy - 2, cx + 2, cy + 2, 'g2'); c.set(cx, cy, 'g8');
}
function capt48(c) {
  block(c, 2, 4, 45, 44, 'g3', 'g1', 'g6', 'g8');
  reel(c, 13, 15, 9); reel(c, 34, 15, 9);
  // tape path round the heads
  c.line(5, 17, 13, 30, 'g8'); c.line(42, 17, 34, 30, 'g8'); c.hl(13, 34, 30, 'g8');
  c.rect(20, 28, 27, 32, 'g7'); c.box(20, 28, 27, 32, 'g8'); c.hl(21, 26, 29, 'g4');
  // transport keys and the record lamp
  for (let i = 0; i < 5; i++) { const x = 7 + i * 7; c.rect(x, 36, x + 5, 40, 'g2'); c.bevel(x, 36, x + 5, 40, 1, 'w', 'g6'); }
  c.rect(30, 37, 32, 39, 'r');
  // the packet stream coming in from the left (a cable)
  dropShadow(c, 2, 2);
}
function capt32(c) {
  block(c, 1, 3, 30, 29, 'g3', 'g1', 'g6', 'g8');
  reel(c, 9, 10, 6); reel(c, 22, 10, 6);
  c.line(3, 11, 9, 19, 'g8'); c.line(28, 11, 22, 19, 'g8'); c.hl(9, 22, 19, 'g8');
  c.rect(13, 18, 18, 21, 'g7');
  for (let i = 0; i < 4; i++) { const x = 4 + i * 6; c.rect(x, 23, x + 4, 26, 'g2'); c.bevel(x, 23, x + 4, 26, 1, 'w', 'g6'); }
  c.rect(20, 24, 21, 25, 'r');
  dropShadow(c, 1, 1);
}
function capt16(c) {
  grid(c, [
    '8888|8888|8888|8888',
    '8118|8811|1188|8168',
    '8186|6681|1866|6868',
    '8186|2681|1862|6868',
    '8186|6681|1866|6868',
    '8118|8811|1188|8168',
    '8118|1111|1111|8168',
    '8111|8888|8888|1168',
    '8111|1177|7711|1168',
    '8111|1111|1111|1168',
    '8WW2|WW2W|W2WR|2168',
    '8662|6626|6266|2668',
    '8666|6666|6666|6668',
    '8888|8888|8888|8888',
    '....|....|....|....',
    '....|....|....|....',
  ]);
}

// ---------- D32nbr: Neighbours (LLDP and receive-only CDP), two devices that know each other's name ----------
function nbr48(c) {
  // the near device (lower left) and the neighbour (upper right), each with a name tag
  chassis(c, 3, 33, 26, 40, [5, 8], [14, 17, 20, 23]);
  chassis(c, 22, 13, 44, 20, [24, 27], [33, 36, 39, 42]);
  // the link between them with two hello pulses
  c.line(20, 32, 30, 22, 'g8'); c.line(21, 32, 31, 22, teal); c.line(22, 32, 32, 22, 'g8');
  c.rect(24, 26, 26, 28, 'y'); c.rect(27, 23, 28, 24, 'y');
  // name tags hanging from each device
  const tag = (x, y) => { c.rect(x, y, x + 11, y + 7, 'w'); c.box(x, y, x + 11, y + 7, 'g8'); c.hl(x + 2, x + 9, y + 2, 'g5'); c.hl(x + 2, x + 6, y + 4, 'g5'); c.set(x + 1, y + 1, 'g8'); };
  tag(30, 2); c.line(31, 3, 27, 12, 'g7');
  tag(5, 21); c.line(9, 29, 9, 32, 'g7');
  dropShadow(c, 2, 2);
}
function nbr32(c) {
  chassis(c, 2, 23, 17, 27, [4], [10, 13]);
  chassis(c, 15, 9, 29, 13, [17], [23, 26]);
  c.line(14, 22, 20, 15, 'g8'); c.line(15, 22, 21, 15, teal);
  c.rect(20, 1, 28, 6, 'w'); c.box(20, 1, 28, 6, 'g8'); c.hl(22, 26, 3, 'g5'); c.line(21, 6, 19, 8, 'g7');
  c.rect(3, 14, 11, 19, 'w'); c.box(3, 14, 11, 19, 'g8'); c.hl(5, 9, 16, 'g5'); c.vl(6, 20, 22, 'g7');
  dropShadow(c, 1, 1);
}
function nbr16(c) {
  grid(c, [
    '....|....|8888|8...',
    '....|....|8558|8...',
    '....|....|8888|8...',
    '....|....|.8..|....',
    '....|...8|8888|8888',
    '....|...8|1G11|8188',
    '....|...8|8888|8888',
    '....|...C|....|....',
    '8888|8.C.|....|....',
    '8558|8C..|....|....',
    '8888|C...|....|....',
    '..8C|....|....|....',
    '8888|8888|8...|....',
    '81G1|1818|8...|....',
    '8888|8888|8...|....',
    '....|....|....|....',
  ]);
}

// ---------- D32oui: OUI Lookup, a network card with its address label ----------
function oui48(c) {
  // board
  c.rect(10, 8, 44, 34, forest); c.box(10, 8, 44, 34, 'g8');
  // gold fingers along the bottom edge
  c.rect(16, 35, 40, 38, 'g8');
  for (let x = 17; x <= 39; x += 2) c.vl(x, 35, 38, 'y');
  // chips and traces
  c.rect(14, 12, 24, 20, 'k'); c.hl(15, 23, 13, 'g6'); for (let x = 15; x <= 23; x += 2) { c.set(x, 11, 'g2'); c.set(x, 21, 'g2'); }
  c.rect(28, 13, 33, 18, 'k'); c.hl(29, 32, 14, 'g6');
  for (let i = 0; i < 4; i++) c.hl(25, 27, 14 + i * 2, 'y');
  // the address label
  c.rect(14, 24, 40, 31, 'w'); c.box(14, 24, 40, 31, 'g7');
  for (let i = 0; i < 6; i++) { const x = 16 + i * 4; c.hl(x, x + 2, 26, 'g8'); }
  for (let x = 16; x <= 38; x += 2) c.vl(x, 28, 29, x % 4 === 0 ? 'g8' : 'g6');
  // mounting bracket with an RJ-45 jack
  c.rect(3, 4, 9, 42, 'g3'); c.vl(3, 4, 42, 'g1'); c.vl(9, 4, 42, 'g6'); c.box(2, 3, 9, 43, 'g8');
  c.rect(4, 17, 8, 23, 'g8'); c.rect(5, 21, 7, 23, 'g5'); c.set(5, 18, 'g'); c.set(7, 18, 'y');
  dropShadow(c, 2, 2);
}
function oui32(c) {
  c.rect(7, 5, 29, 22, forest); c.box(7, 5, 29, 22, 'g8');
  c.rect(11, 23, 26, 25, 'g8'); for (let x = 12; x <= 25; x += 2) c.vl(x, 23, 25, 'y');
  c.rect(10, 8, 16, 13, 'k'); c.rect(19, 8, 23, 12, 'k');
  c.rect(10, 15, 27, 20, 'w'); c.box(10, 15, 27, 20, 'g7');
  for (let i = 0; i < 6; i++) c.hl(11 + i * 3, 12 + i * 3, 17, 'g8');
  c.rect(2, 2, 6, 28, 'g3'); c.box(2, 2, 6, 28, 'g8'); c.rect(3, 11, 5, 15, 'g8');
  dropShadow(c, 1, 1);
}
function oui16(c) {
  grid(c, [
    '....|....|....|....',
    '888.|....|....|....',
    '838F|FFFF|FFFF|FFF8',
    '838F|KKKF|KKFF|FFF8',
    '838F|KKKF|KKFF|FFF8',
    '888F|FFFF|FFFF|FFF8',
    '858F|8888|8888|88F8',
    '858F|8W88|W88W|88F8',
    '888F|8888|8888|88F8',
    '838F|FFFF|FFFF|FFF8',
    '838F|8888|8888|8888',
    '838.|.8YY|YYYY|8...',
    '888.|.888|8888|8...',
    '....|....|....|....',
    '....|....|....|....',
    '....|....|....|....',
  ], { F: forest });
}

// ---------- D32calc: Network Calculator, a calculator showing "/24" ----------
function calc48(c) {
  block(c, 8, 3, 39, 44, 'g4', 'g2', 'g6', 'g8');
  c.rect(12, 7, 35, 15, 'g1'); c.hl(12, 35, 7, 'g6'); c.vl(12, 7, 15, 'g6');
  c.text(F3x5, '/24', 24, 9, 'g8');
  for (let r = 0; r < 4; r++) for (let k = 0; k < 4; k++) {
    const x = 12 + k * 6, y = 19 + r * 6;
    c.rect(x, y, x + 4, y + 3, r === 3 && k === 3 ? 'g6' : 'g2'); c.bevel(x, y, x + 4, y + 3, 1, 'w', 'g6');
  }
  c.rect(12, 43 - 2, 35, 41, 'g4');
  dropShadow(c, 2, 2);
}
function calc32(c) {
  block(c, 5, 1, 26, 29, 'g4', 'g2', 'g6', 'g8');
  c.rect(8, 4, 23, 9, 'g1'); c.hl(8, 23, 4, 'g6'); c.text(F3x5, '/24', 13, 5, 'g8');
  for (let r = 0; r < 3; r++) for (let k = 0; k < 3; k++) { const x = 8 + k * 5, y = 12 + r * 5; c.rect(x, y, x + 3, y + 3, 'g2'); c.bevel(x, y, x + 3, y + 3, 1, 'w', 'g6'); }
  dropShadow(c, 1, 1);
}
function calc16(c) {
  grid(c, [
    '..88|8888|8888|8...',
    '..84|4444|4444|8...',
    '..81|111K|1KKK|8...',
    '..81|11K1|1KKK|8...',
    '..84|4444|4444|8...',
    '..8W|2W2W|2W24|8...',
    '..86|6666|6666|8...',
    '..8W|2W2W|2W24|8...',
    '..86|6666|6666|8...',
    '..8W|2W2W|2W24|8...',
    '..86|6666|6666|8...',
    '..8W|2W2W|2W64|8...',
    '..86|6666|6666|8...',
    '..88|8888|8888|8...',
    '....|....|....|....',
    '....|....|....|....',
  ]);
}

module.exports = [
  { name: 'D32shk', title: 'Wireshark (as a protocol analyser; no project marks)', ref: 'DISTRO-DESIGN 3.3, 6.4 (Lab subpanel)', concept: 'a period luggable protocol analyser with a packet list on its screen', draw: { l: shk48, m: shk32, t: shk16 } },
  { name: 'D32capt', title: 'Capture (d32-capture)', ref: 'DISTRO-DESIGN 6.4 (Lab subpanel)', concept: 'a reel-to-reel recorder', draw: { l: capt48, m: capt32, t: capt16 } },
  { name: 'D32nbr', title: 'Neighbours (LLDP, receive-only CDP)', ref: 'DISTRO-DESIGN 3.3, 6.4 (Lab subpanel)', concept: 'two devices on one link, each with a name tag', draw: { l: nbr48, m: nbr32, t: nbr16 } },
  { name: 'D32oui', title: 'OUI Lookup', ref: 'DISTRO-DESIGN 3.3, 6.4 (Lab subpanel)', concept: 'a network card with its address label', draw: { l: oui48, m: oui32, t: oui16 } },
  { name: 'D32calc', title: 'Network Calculator', ref: 'LOOK-SOUND 6.2 (D32calc); DISTRO-DESIGN 6.4 (Lab subpanel)', concept: 'a calculator showing "/24"', draw: { l: calc48, m: calc32, t: calc16 } },
];
