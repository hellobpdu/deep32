'use strict';
// Desktop Style subpanel (front panel position 10): Sound, Audio Control, Look Profile, Deep32 Settings,
// System Administration (SAM), Administration Menus (smitty). Style Manager keeps CDE's own icon.
const { dropShadow, block, monitor, grid, checker } = require('../../lib/kit');
const { F3x5 } = require('../../lib/font');

// ---------- D32snd: Sound, a speaker with sound arcs and a small bell ----------
function snd48(c) {
  c.rect(2, 19, 7, 29, 'g7'); c.box(2, 19, 7, 29, 'g8');
  c.poly([[7, 19], [20, 9], [20, 39], [7, 29]], 'g5');
  c.line(7, 19, 20, 9, 'g2'); c.line(7, 29, 20, 39, 'g8'); c.vl(20, 9, 39, 'g8'); c.line(8, 20, 19, 12, 'g4');
  c.vl(21, 11, 37, 'g3');
  for (const r of [6, 11, 16]) c.ring(21 - r, 24 - r * 1.2, 21 + r, 24 + r * 1.2, 2, 'g4', (dx) => dx > 2);
  for (const r of [6, 11, 16]) c.ring(21 - r, 24 - r * 1.2, 21 + r, 24 + r * 1.2, 1, 'g8', (dx) => dx > 2);
  // bell
  c.poly([[37, 31], [40, 31], [43, 41], [34, 41]], 'y'); c.oval(33, 39, 44, 43, 'y');
  c.line(37, 31, 34, 41, 'g8'); c.line(40, 31, 43, 41, 'g8'); c.ring(33, 39, 44, 43, 1, 'g8', (dx, dy) => dy > -1);
  c.rect(37, 29, 40, 31, 'g8'); c.vl(36, 33, 39, 'w'); c.oval(37, 43, 40, 46, 'g7');
  dropShadow(c, 2, 2);
}
function snd32(c) {
  c.rect(1, 12, 4, 19, 'g7'); c.box(1, 12, 4, 19, 'g8');
  c.poly([[4, 12], [13, 5], [13, 26], [4, 19]], 'g5'); c.line(4, 12, 13, 5, 'g2'); c.line(4, 19, 13, 26, 'g8'); c.vl(13, 5, 26, 'g8');
  for (const r of [4, 8, 12]) c.ring(14 - r, 16 - r * 1.2, 14 + r, 16 + r * 1.2, 1, 'g8', (dx) => dx > 1);
  c.poly([[24, 21], [26, 21], [29, 28], [21, 28]], 'y'); c.line(24, 21, 21, 28, 'g8'); c.line(26, 21, 29, 28, 'g8'); c.hl(21, 29, 28, 'g8'); c.rect(24, 29, 26, 30, 'g7');
  dropShadow(c, 1, 1);
}
function snd16(c) {
  grid(c, [
    '....|....|....|....',
    '....|.8..|..8.|....',
    '....|88.8|...8|....',
    '...8|58..|8..8|....',
    '8888|58.8|.8..|8...',
    '8758|58.8|.8..|8...',
    '8758|58.8|.8..|8...',
    '8888|58.8|.8..|8...',
    '...8|58..|8..8|....',
    '....|88.8|.88.|....',
    '....|.8..|8YY8|....',
    '....|....|8YY8|....',
    '....|...8|YYYY|8...',
    '....|...8|8888|8...',
    '....|....|.77.|....',
    '....|....|....|....',
  ]);
}

// ---------- D32mix: Audio Control (sndioctl), three faders and a speaker ----------
function mix48(c) {
  block(c, 4, 2, 43, 45, 'g3', 'g1', 'g6', 'g8');
  // speaker badge
  c.rect(7, 5, 10, 10, 'g6'); c.poly([[10, 5], [15, 2], [15, 13], [10, 10]], 'g6'); c.ring(13, 4, 19, 11, 1, 'g8', (dx) => dx > 1);
  for (const [i, x] of [[0, 14], [1, 25], [2, 36]].map(([i, x]) => [i, x])) {
    c.rect(x - 1, 14, x + 1, 42, 'g8'); c.vl(x + 2, 14, 42, 'g1');
    for (let y = 16; y <= 40; y += 4) { c.set(x - 4, y, 'g6'); c.set(x + 4, y, 'g6'); }
    const ky = [20, 31, 25][i];
    c.rect(x - 4, ky, x + 4, ky + 4, 'g2'); c.bevel(x - 4, ky, x + 4, ky + 4, 1, 'w', 'g6'); c.hl(x - 3, x + 3, ky + 2, 'g8');
  }
  dropShadow(c, 2, 2);
}
function mix32(c) {
  block(c, 2, 1, 29, 30, 'g3', 'g1', 'g6', 'g8');
  for (const [i, x] of [[0, 9], [1, 16], [2, 23]]) {
    c.rect(x, 5, x + 1, 27, 'g8');
    const ky = [10, 18, 13][i];
    c.rect(x - 2, ky, x + 3, ky + 3, 'g2'); c.bevel(x - 2, ky, x + 3, ky + 3, 1, 'w', 'g6'); c.hl(x - 1, x + 2, ky + 1, 'g8');
  }
  c.rect(4, 3, 6, 6, 'g6');
  dropShadow(c, 1, 1);
}
function mix16(c) {
  grid(c, [
    '8888|8888|8888|8888',
    '8111|1111|1111|1168',
    '8138|3383|3383|3368',
    '8WWW|W383|3383|3368',
    '8666|6383|3WWW|W368',
    '8138|3383|3666|6368',
    '8138|3WWW|W383|3368',
    '8138|3666|6383|3368',
    '8138|3383|3383|3368',
    '8138|3383|3383|3368',
    '8138|3383|3383|3368',
    '8666|6666|6666|6668',
    '8888|8888|8888|8888',
    '....|....|....|....',
    '....|....|....|....',
    '....|....|....|....',
  ]);
}

// ---------- D32prof: Look Profile (bevel and line weight of the whole desktop), a window and a pair of calipers ----------
function prof48(c) {
  // a miniature Motif window with a thick bevel
  c.rect(3, 9, 34, 42, 'g2'); c.bevel(3, 9, 34, 42, 2, 'w', 'g6'); c.box(2, 8, 35, 43, 'g8');
  c.rect(6, 12, 31, 16, 'g5'); c.bevel(6, 12, 31, 16, 1, 'g3', 'g7'); c.hl(12, 24, 14, 'w');
  c.rect(6, 18, 31, 20, 'g4'); c.hl(8, 11, 19, 'g8'); c.hl(14, 17, 19, 'g8');
  c.rect(7, 23, 30, 39, 'w'); c.bevel(6, 22, 31, 40, 1, 'g6', 'g1');
  c.hl(9, 24, 26, 'g4'); c.hl(9, 20, 29, 'g4'); c.hl(9, 26, 32, 'g4');
  // vernier calipers measuring the frame: beam, fixed jaw, sliding jaw
  c.rect(39, 2, 42, 45, 'g2'); c.vl(39, 2, 45, 'w'); c.vl(42, 2, 45, 'g6'); c.box(38, 1, 43, 46, 'g8');
  for (let y = 4; y <= 44; y += 2) c.set(41, y, y % 8 === 0 ? 'g8' : 'g6');
  c.rect(30, 1, 38, 4, 'g2'); c.box(29, 0, 38, 5, 'g8'); c.hl(30, 37, 1, 'w');
  c.rect(30, 9, 38, 12, 'g2'); c.box(29, 8, 38, 13, 'g8'); c.hl(30, 37, 9, 'w');
  c.rect(43, 7, 45, 14, 'g4'); c.box(43, 7, 46, 14, 'g8');
  dropShadow(c, 1, 1);
}
function prof32(c) {
  c.rect(2, 6, 22, 29, 'g2'); c.bevel(2, 6, 22, 29, 1, 'w', 'g6'); c.box(1, 5, 23, 30, 'g8');
  c.rect(4, 8, 20, 10, 'g5'); c.rect(4, 13, 20, 27, 'w'); c.box(4, 13, 20, 27, 'g6');
  c.hl(6, 16, 16, 'g4'); c.hl(6, 14, 19, 'g4');
  c.rect(26, 1, 28, 30, 'g2'); c.box(25, 0, 29, 31, 'g8'); for (let y = 3; y <= 29; y += 3) c.set(27, y, 'g6');
  c.rect(20, 1, 25, 2, 'g2'); c.box(19, 0, 25, 3, 'g8'); c.rect(20, 6, 25, 7, 'g2'); c.box(19, 5, 25, 8, 'g8');
  dropShadow(c, 1, 1);
}
function prof16(c) {
  grid(c, [
    '....|....|8888|888.',
    '....|....|8222|888.',
    '8888|8888|8888|828.',
    '8WWW|WWWW|W888|828.',
    '8W55|5555|5868|828.',
    '8W5W|WW55|5888|828.',
    '8W44|4444|4868|828.',
    '8W88|8888|8868|828.',
    '8W8W|WWWW|W868|868.',
    '8W8W|4444|W868|828.',
    '8W8W|WWWW|W868|828.',
    '8W8W|444W|W868|868.',
    '8W8W|WWWW|W868|828.',
    '8W66|6666|6668|828.',
    '8888|8888|8888|888.',
    '....|....|....|....',
  ]);
}

// ---------- D32set: Deep32 Settings, a slider panel with a "32" plate ----------
function set48(c) {
  c.rect(4, 6, 43, 43, 'bg'); c.bevel(4, 6, 43, 43, 2, 'ts', 'bs'); c.box(3, 5, 44, 44, 'g8');
  for (const [y, kx] of [[12, 14], [22, 30], [32, 22]]) {
    c.rect(8, y, 39, y + 2, 'g8'); c.hl(8, 39, y + 3, 'ts');
    c.rect(kx - 2, y - 3, kx + 2, y + 5, 'g2'); c.bevel(kx - 2, y - 3, kx + 2, y + 5, 1, 'w', 'g6'); c.vl(kx, y - 1, y + 3, 'g7');
  }
  c.rect(30, 37, 41, 42, 'g3'); c.bevel(30, 37, 41, 42, 1, 'g1', 'g6'); c.text(F3x5, '32', 33, 37, 'g8');
  dropShadow(c, 1, 1);
}
function set32(c) {
  c.rect(2, 3, 29, 29, 'bg'); c.bevel(2, 3, 29, 29, 1, 'ts', 'bs'); c.box(1, 2, 30, 30, 'g8');
  for (const [y, kx] of [[8, 9], [15, 20], [22, 14]]) {
    c.rect(5, y, 26, y + 1, 'g8');
    c.rect(kx - 2, y - 2, kx + 2, y + 3, 'g2'); c.bevel(kx - 2, y - 2, kx + 2, y + 3, 1, 'w', 'g6');
  }
  c.rect(20, 24, 27, 28, 'g3'); c.box(20, 24, 27, 28, 'g6');
  c.grid(['##.##', '.#..#', '##.##', '.#.#.', '##.##'].map(r => r), { '#': 'g8' }, 21, 24);
  dropShadow(c, 1, 1);
}
function set16(c) {
  grid(c, [
    '8888|8888|8888|8888',
    '8ttt|tttt|tttt|tts8',
    '8tbb|b2bb|bbbb|bbs8',
    '8t88|8288|8888|88s8',
    '8tbb|b6bb|bbbb|bbs8',
    '8tbb|bbbb|bb2b|bbs8',
    '8t88|8888|8828|88s8',
    '8tbb|bbbb|bb6b|bbs8',
    '8tbb|bbb2|bbbb|bbs8',
    '8t88|8882|8888|88s8',
    '8tbb|bbb6|bbbb|bbs8',
    '8tbb|bbbb|8888|88s8',
    '8tbb|bbbb|8838|38s8',
    '8sss|ssss|ssss|sss8',
    '8888|8888|8888|8888',
    '....|....|....|....',
  ]);
}

// ---------- D32sam: System Administration (SAM), a toolbox with a spanner laid across a small system unit ----------
const brick = (x, y) => (((x + y) & 1) ? 'r' : 'g7');
const redbox = (x, y) => (((x + y) & 1) ? 'r' : 'g5');
function sam48(c) {
  // the system unit behind
  block(c, 9, 4, 38, 22, 'g2', 'w', 'g5', 'g8');
  c.rect(13, 8, 24, 10, 'g8'); c.hl(14, 23, 9, 'g5');
  c.rect(30, 8, 33, 9, 'g'); c.rect(13, 14, 34, 15, 'g4');
  // the toolbox in front
  c.rect(3, 24, 44, 44, 'r'); c.rect(4, 33, 43, 43, redbox); c.rect(39, 25, 43, 43, brick);
  c.box(3, 24, 44, 44, 'g8'); c.hl(4, 43, 25, 'w'); c.hl(4, 43, 26, 'r');
  c.hl(4, 43, 31, 'g8'); c.hl(4, 43, 32, 'w');
  c.rect(19, 34, 28, 37, 'g3'); c.box(19, 34, 28, 37, 'g8');
  // handle
  c.rect(16, 19, 31, 21, 'g4'); c.box(15, 18, 32, 24, 'g8'); c.rect(17, 21, 30, 24, 'r'); c.hl(17, 30, 21, 'g8');
  // spanner across the system unit
  c.lineW(5, 13, 36, 2, 2, 'g4'); c.line(5, 12, 36, 1, 'w'); c.line(5, 15, 36, 4, 'g8');
  c.oval(1, 10, 8, 17, 'g4'); c.ring(1, 10, 8, 17, 1, 'g8'); c.rect(1, 12, 4, 15, null); c.vl(5, 12, 15, 'g8');
  c.oval(35, 0, 41, 6, 'g4'); c.ring(35, 0, 41, 6, 1, 'g8'); c.rect(37, 2, 39, 4, 'g8');
  dropShadow(c, 2, 2);
}
function sam32(c) {
  block(c, 6, 3, 25, 15, 'g2', 'w', 'g5', 'g8'); c.rect(9, 6, 16, 7, 'g8'); c.rect(20, 6, 22, 6, 'g');
  c.rect(2, 16, 29, 29, 'r'); c.box(2, 16, 29, 29, 'g8'); c.hl(3, 28, 17, 'w'); c.hl(3, 28, 21, 'g8'); c.rect(13, 23, 18, 25, 'g3'); c.box(13, 23, 18, 25, 'g8');
  c.box(10, 12, 21, 16, 'g8'); c.rect(11, 13, 20, 13, 'g4');
  c.lineW(3, 10, 24, 2, 2, 'g4'); c.line(3, 9, 24, 1, 'w'); c.line(3, 12, 24, 4, 'g8');
  c.oval(0, 7, 5, 12, 'g4'); c.ring(0, 7, 5, 12, 1, 'g8'); c.rect(0, 9, 2, 10, null);
  dropShadow(c, 1, 1);
}
function sam16(c) {
  grid(c, [
    '....|....|....|.888',
    '..88|8888|8888|8848',
    '..82|2222|2228|4488',
    '..82|8822|2448|8...',
    '.88.|2222|448.|....',
    '8.84|4444|882.|....',
    '8448|8222|2228|....',
    '.888|8888|8888|....',
    '8888|8888|8888|8888',
    '8WWW|WWWW|WWWW|WWW8',
    '8RRR|RRRR|RRRR|RR78',
    '8888|8888|8888|8888',
    '8RRR|RR88|88RR|RR78',
    '8RRR|RR83|38RR|RR78',
    '8RRR|RR88|88RR|RR78',
    '8888|8888|8888|8888',
  ]);
}

// ---------- D32smit: Administration Menus (smitty), a terminal with a menu and a softkey bar ----------
function smit48(c) {
  const [sx0, sy0, sx1, sy1] = monitor(c, 4, 4, 43, 35, { t: 2, chin: 3 });
  c.hl(sx0 + 2, sx0 + 18, sy0 + 2, 'w');
  for (const [i, y] of [sy0 + 6, sy0 + 10, sy0 + 14].entries()) {
    if (i === 1) { c.rect(sx0 + 1, y - 1, sx1 - 6, y + 1, 'w'); c.hl(sx0 + 3, sx0 + 20, y, 'g8'); } else c.hl(sx0 + 3, sx0 + 22 - i * 3, y, 'w');
  }
  for (let k = 0; k < 4; k++) { const x = sx0 + 1 + k * 8; c.rect(x, sy1 - 3, x + 6, sy1 - 1, 'g3'); c.hl(x + 1, x + 4, sy1 - 2, 'g8'); }
  c.rect(16, 36, 31, 38, 'g4'); c.box(16, 36, 31, 38, 'g8');
  c.poly([[6, 40], [41, 40], [44, 45], [3, 45]], 'g2'); c.line(6, 40, 41, 40, 'g8'); c.line(41, 40, 44, 45, 'g8'); c.line(6, 40, 3, 45, 'g8'); c.hl(3, 44, 45, 'g8');
  for (let x = 7; x <= 40; x += 3) c.set(x, 42, 'g5');
  dropShadow(c, 1, 1);
}
function smit32(c) {
  const [sx0, sy0, sx1, sy1] = monitor(c, 2, 2, 29, 23, { t: 1, chin: 2 });
  c.hl(sx0 + 1, sx0 + 10, sy0 + 1, 'w');
  c.hl(sx0 + 2, sx0 + 14, sy0 + 4, 'w'); c.rect(sx0 + 1, sy0 + 6, sx1 - 3, sy0 + 8, 'w'); c.hl(sx0 + 2, sx0 + 12, sy0 + 7, 'g8'); c.hl(sx0 + 2, sx0 + 11, sy0 + 10, 'w');
  for (let k = 0; k < 4; k++) { const x = sx0 + k * 5; c.rect(x, sy1 - 1, x + 3, sy1, 'g3'); }
  c.rect(10, 24, 21, 25, 'g4'); c.box(10, 24, 21, 25, 'g8');
  c.rect(2, 27, 29, 29, 'g2'); c.box(2, 27, 29, 29, 'g8');
  dropShadow(c, 1, 1);
}
function smit16(c) {
  grid(c, [
    '8888|8888|8888|8888',
    '8111|1111|1111|1168',
    '818W|WWW8|8888|8868',
    '8188|8888|8888|8868',
    '818W|WWWW|W888|8868',
    '81WW|WWWW|WWW8|8868',
    '81W8|8888|8WW8|8868',
    '81WW|WWWW|WWW8|8868',
    '818W|WWW8|8888|8868',
    '8133|8338|3383|3868',
    '8155|5555|5555|5568',
    '8888|8888|8888|8888',
    '....|8444|448.|....',
    '.888|8888|8888|888.',
    '8222|2222|2222|2228',
    '8888|8888|8888|8888',
  ]);
}

module.exports = [
  { name: 'D32snd', title: 'Sound', ref: 'LOOK-SOUND 6.2 (D32snd); DISTRO-DESIGN 6.4 (Style subpanel)', concept: 'a speaker with sound arcs and a small bell', draw: { l: snd48, m: snd32, t: snd16 } },
  { name: 'D32mix', title: 'Audio Control (sndioctl)', ref: 'LOOK-SOUND 6.2 (D32mix); DISTRO-DESIGN 3.3, 6.4', concept: 'three faders and a speaker', draw: { l: mix48, m: mix32, t: mix16 } },
  { name: 'D32prof', title: 'Look Profile (cde10 / cde21)', ref: 'DISTRO-DESIGN 6.2, 6.4 (Style subpanel)', concept: 'a Motif window measured by a pair of calipers', draw: { l: prof48, m: prof32, t: prof16 } },
  { name: 'D32set', title: 'Deep32 Settings', ref: 'LOOK-SOUND 6.2 (D32set); DISTRO-DESIGN 6.4', concept: 'a slider panel with a "32" plate; the panel face follows the palette', draw: { l: set48, m: set32, t: set16 } },
  { name: 'D32sam', title: 'System Administration (SAM)', ref: 'DISTRO-DESIGN 6.9 / fidelity 23 (revised brief), 6.4', concept: 'a toolbox with a spanner laid across a small system unit; no gear', draw: { l: sam48, m: sam32, t: sam16 } },
  { name: 'D32smit', title: 'Administration Menus (smitty, terminal)', ref: 'LOOK-SOUND 4.1, 6.2 (D32smit); DISTRO-DESIGN 6.4', concept: 'a terminal showing a menu with one row selected and a softkey bar', draw: { l: smit48, m: smit32, t: smit16 } },
];
