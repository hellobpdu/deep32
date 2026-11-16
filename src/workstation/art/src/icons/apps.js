'use strict';
// Applications subpanel (front panel position 11): Web Browser (Fathom),
// Media Player, Image Viewer, Screen Capture; plus the section viewer of ISO-DESIGN 3.2.
const { dropShadow, block, monitor, page, grid, checker } = require('../../lib/kit');
const { F3x5 } = require('../../lib/font');

const forest = (x, y) => (((x + y) & 1) ? 'g' : 'k');
const sky = (x, y) => (((x + y) & 1) ? 'b' : 'w');
const navy = (x, y) => (((x + y) & 1) ? 'b' : 'k');
const chartBlue = (x, y) => (((x + y) & 1) ? 'b' : 'g1');

function tick(c, x, y, col) { c.set(x, y + 1, col); c.set(x + 1, y + 2, col); c.set(x + 2, y + 1, col); c.set(x + 3, y, col); }
function cross(c, x, y, col) { for (let i = 0; i < 3; i++) { c.set(x + i, y + i, col); c.set(x + 2 - i, y + i, col); } }

// ---------- D32web: Web Browser (Fathom), the sounding lead over chart grid lines, a page corner at 48 px ----------
function lead(c, cx, top, h, w) {
  // a sounding lead: a long, slightly tapered cylinder with a flat base, lit from the left, hung by its eye
  for (let i = 0; i < h; i++) {
    const half = Math.floor((w / 2) * (0.55 + 0.45 * i / (h - 1)));
    c.hl(cx - half, cx + half, top + i, 'g5');
    c.set(cx - half + 1, top + i, 'g3'); if (half > 2) c.set(cx - half + 2, top + i, 'g4');
    c.set(cx + half - 1, top + i, 'g7');
    c.set(cx - half, top + i, 'g8'); c.set(cx + half, top + i, 'g8');
  }
  const hb = Math.floor(w / 2);
  c.hl(cx - hb, cx + hb, top + h, 'g8'); c.hl(cx - Math.floor(hb * 0.55), cx + Math.floor(hb * 0.55), top, 'g8');
  c.ring(cx - 2, top - 4, cx + 2, top, 1, 'g8');
}
function web48(c) {
  page(c, 3, 4, 44, 44, { paper: 'g1', fold: 0, shade: false });
  for (let x = 8; x <= 40; x += 8) c.vl(x, 5, 43, chartBlue);
  for (let y = 10; y <= 42; y += 8) c.hl(4, 43, y, chartBlue);
  // a coastline and soundings
  c.shape(4, 30, 20, 43, (x, y) => y > 30 + Math.round(5 * Math.sin((x - 4) / 4)) + (x > 14 ? (x - 14) * 2 : 0), 'g3');
  for (const [d, x, y] of [['7', 26, 33], ['12', 32, 22], ['9', 12, 15], ['21', 34, 37]]) c.text(F3x5, d, x, y, 'g5');
  // page corner folded at the top left
  for (let i = 0; i < 8; i++) for (let j = 0; j < 8 - i; j++) c.clear(3 + i, 4 + j);
  c.poly([[3, 12], [11, 4], [11, 12]], 'w'); c.line(3, 12, 11, 4, 'g8'); c.hl(3, 11, 12, 'g7'); c.vl(11, 4, 12, 'g7');
  // the line and the lead
  c.vl(30, 0, 14, 'g8'); c.vl(31, 1, 14, 'g4');
  lead(c, 30, 19, 21, 9);
  dropShadow(c, 2, 2);
}
function web32(c) {
  page(c, 2, 3, 29, 29, { paper: 'g1', shade: false });
  for (let x = 7; x <= 27; x += 6) c.vl(x, 4, 28, chartBlue);
  for (let y = 9; y <= 27; y += 6) c.hl(3, 28, y, chartBlue);
  c.shape(3, 20, 13, 28, (x, y) => y > 21 + Math.round(3 * Math.sin((x - 3) / 3)) + (x > 9 ? (x - 9) * 2 : 0), 'g3');
  c.vl(20, 0, 9, 'g8');
  lead(c, 20, 13, 13, 7);
  dropShadow(c, 1, 1);
}
function web16(c) {
  grid(c, [
    '....|....|8...|....',
    '8888|8888|8888|8888',
    '81B1|1B11|8B11|1B18',
    '8BBB|BBBB|8BBB|BBB8',
    '81B1|1B18|881B|1B18',
    '81B1|1B18|681B|1B18',
    '8BBB|BBB8|668B|BBB8',
    '81B1|1B86|3668|1B18',
    '833B|1B86|3668|1B18',
    '8333|BBB8|8888|8BB8',
    '8333|31B1|1B11|1B18',
    '8333|33B1|1B11|1B18',
    '8333|333B|BBBB|BBB8',
    '8888|8888|8888|8888',
    '....|....|....|....',
    '....|....|....|....',
  ], { B: chartBlue });
}

// ---------- D32media: Media Player (mpv with the CDE controller), a monitor with a play mark and speaker grilles ----------
function media48(c) {
  block(c, 2, 7, 45, 37, 'g4', 'g2', 'g6', 'g8');
  c.rect(9, 11, 38, 33, 'g8'); c.hl(9, 38, 34, 'w'); c.vl(39, 11, 34, 'w');
  c.poly([[19, 15], [31, 22], [19, 29]], 'w'); c.line(19, 15, 31, 22, 'g4'); c.line(19, 29, 31, 22, 'g4');
  for (let y = 13; y <= 31; y += 2) for (const x of [4, 6, 41, 43]) c.set(x, y, 'g6');
  c.rect(16, 38, 31, 42, 'g5'); c.box(16, 38, 31, 42, 'g8'); c.hl(12, 35, 43, 'g8'); c.rect(12, 43, 35, 44, 'g6'); c.box(11, 43, 36, 45, 'g8');
  dropShadow(c, 2, 1);
}
function media32(c) {
  block(c, 1, 4, 30, 24, 'g4', 'g2', 'g6', 'g8');
  c.rect(6, 7, 25, 21, 'g8');
  c.poly([[12, 10], [20, 14], [12, 18]], 'w');
  for (let y = 8; y <= 20; y += 2) for (const x of [3, 28]) c.set(x, y, 'g6');
  c.rect(11, 25, 20, 27, 'g5'); c.box(8, 27, 23, 29, 'g8');
  dropShadow(c, 1, 1);
}
function media16(c) {
  grid(c, [
    '....|....|....|....',
    '8888|8888|8888|8888',
    '8288|8888|8888|8828',
    '8686|8W88|8888|8668',
    '8288|8WW8|8888|8828',
    '8686|8WWW|W888|8668',
    '8288|8WWW|WW88|8828',
    '8686|8WWW|W888|8668',
    '8288|8WW8|8888|8828',
    '8686|8W88|8888|8668',
    '8288|8888|8888|8828',
    '8666|6666|6666|6668',
    '8888|8888|8888|8888',
    '....|8555|5558|....',
    '...8|8888|8888|8...',
    '....|....|....|....',
  ]);
}

// ---------- D32img: Image Viewer (xv), a framed photograph on an easel back ----------
function img48(c) {
  c.line(24, 36, 31, 46, 'g7'); c.line(25, 36, 32, 46, 'g6');
  c.rect(5, 5, 42, 37, 'g6'); c.bevel(5, 5, 42, 37, 3, 'g4', 'g7'); c.box(4, 4, 43, 38, 'g8');
  c.rect(9, 9, 38, 33, sky);
  c.oval(29, 11, 34, 16, 'y'); c.ring(29, 11, 34, 16, 1, 'g8', (dx, dy) => dx + dy > 1);
  c.shape(9, 15, 38, 33, (x, y) => y > 30 - Math.abs(x - 20) * 1.1, 'g5');
  c.shape(9, 15, 38, 33, (x, y) => y > 30 - Math.abs(x - 20) * 1.1 && y < 21 - Math.abs(x - 20) * 0.2, 'w');
  c.shape(9, 24, 38, 33, (x, y) => y > 27 + Math.round(2 * Math.sin(x / 3.5)), (x, y) => (((x + y) & 1) ? 'g' : 'k'));
  c.box(8, 8, 39, 34, 'g8');
  dropShadow(c, 2, 1);
}
function img32(c) {
  c.line(16, 24, 21, 30, 'g7');
  c.rect(3, 3, 28, 25, 'g6'); c.bevel(3, 3, 28, 25, 2, 'g4', 'g7'); c.box(2, 2, 29, 26, 'g8');
  c.rect(6, 6, 25, 22, sky);
  c.oval(19, 7, 22, 10, 'y');
  c.shape(6, 9, 25, 22, (x, y) => y > 20 - Math.abs(x - 13) * 1.2, 'g5');
  c.shape(6, 16, 25, 22, (x, y) => y > 19 + Math.round(Math.sin(x / 2.5)), (x, y) => (((x + y) & 1) ? 'g' : 'k'));
  c.box(5, 5, 26, 23, 'g8');
  dropShadow(c, 1, 1);
}
function img16(c) {
  grid(c, [
    '8888|8888|8888|8888',
    '8666|6666|6666|6668',
    '868B|WBWB|WBYY|B868',
    '86BW|BWBW|BWYY|W868',
    '86WB|W5BW|BWBW|B868',
    '86BW|555W|BWBW|B868',
    '86W5|5555|BWB5|W868',
    '8655|5555|55G5|5868',
    '86GK|GKGK|GKGK|G868',
    '868G|KGKG|KGKG|K868',
    '8666|6666|6666|6668',
    '8888|8888|8888|8888',
    '....|...7|7...|....',
    '....|....|77..|....',
    '....|....|.77.|....',
    '....|....|....|....',
  ]);
}

// ---------- D32scap: Screen Capture (xwd, netpbm, slop), a box camera in front of a monitor ----------
function scap48(c) {
  const [sx0, sy0, sx1, sy1] = monitor(c, 14, 2, 45, 30, { t: 2, chin: 3, screen: 'g7' });
  c.rect(sx0 + 2, sy0 + 2, sx1 - 2, sy0 + 4, 'g4'); c.rect(sx0 + 2, sy0 + 5, sx1 - 2, sy1 - 2, 'g2');
  // dashed capture rectangle on the screen
  c.dotted(sx0 + 4, sy0 + 7, sx1 - 4, sy0 + 7, 'k', 2, 1); c.dotted(sx0 + 4, sy1 - 4, sx1 - 4, sy1 - 4, 'k', 2, 1);
  c.dotted(sx0 + 4, sy0 + 7, sx0 + 4, sy1 - 4, 'k', 2, 1); c.dotted(sx1 - 4, sy0 + 7, sx1 - 4, sy1 - 4, 'k', 2, 1);
  // the camera
  c.rect(10, 22, 15, 25, 'g6'); c.box(10, 22, 15, 25, 'g8');
  block(c, 2, 25, 28, 43, 'g7', 'g5', 'g8', 'k');
  c.rect(4, 27, 9, 29, 'g4'); c.box(4, 27, 9, 29, 'g8');
  c.oval(11, 27, 25, 41, 'g8'); c.ring(12, 28, 24, 40, 2, 'g4'); c.oval(15, 31, 21, 37, 'g6'); c.oval(16, 32, 18, 34, 'w');
  c.rect(23, 26, 26, 28, 'w');
  dropShadow(c, 2, 2);
}
function scap32(c) {
  monitor(c, 10, 1, 30, 20, { t: 1, chin: 2, screen: 'g2' });
  c.dotted(15, 7, 25, 7, 'k', 2, 1); c.dotted(15, 15, 25, 15, 'k', 2, 1); c.dotted(15, 7, 15, 15, 'k', 2, 1); c.dotted(25, 7, 25, 15, 'k', 2, 1);
  block(c, 1, 16, 19, 29, 'g7', 'g5', 'g8', 'k');
  c.oval(7, 18, 17, 28, 'g8'); c.ring(8, 19, 16, 27, 1, 'g4'); c.oval(10, 21, 14, 25, 'g6'); c.set(11, 22, 'w');
  c.rect(3, 18, 5, 19, 'g4');
  dropShadow(c, 1, 1);
}
function scap16(c) {
  grid(c, [
    '....|.888|8888|8888',
    '....|.811|1111|1168',
    '....|.812|K2K2|K168',
    '....|.812|2222|2168',
    '....|.81K|2222|K168',
    '....|.812|2222|2168',
    '8888|8888|8888|K168',
    '8555|5555|5552|2168',
    '8544|5888|8858|8868',
    '8555|8844|4885|5888',
    '8558|8466|6488|5...',
    '8558|846W|6488|5...',
    '8558|8466|6488|5...',
    '8555|8844|4885|5...',
    '8888|8888|8888|8...',
    '....|....|....|....',
  ]);
}

// ---------- D32sect: section viewer (a RAG section with its cite), a page with a marked passage ----------
function sect48(c) {
  page(c, 8, 3, 39, 44, { fold: 7 });
  for (let y = 12; y <= 41; y += 3) c.hl(14, 35 - ((y * 7) % 6), y, 'g4');
  // the marked passage: margin bracket and highlight
  c.rect(13, 20, 36, 30, 'y'); for (const y of [21, 24, 27, 30]) c.hl(14, 35 - (y % 4) * 2, y, 'g7');
  c.vl(10, 19, 31, 'b'); c.hl(10, 11, 19, 'b'); c.hl(10, 11, 31, 'b');
  // the cite tab on the right edge
  c.rect(36, 33, 45, 39, 'b'); c.box(36, 33, 45, 39, 'g8'); c.text(F3x5, '[1]', 37, 34, 'w');
  dropShadow(c, 2, 2);
}
function sect32(c) {
  page(c, 5, 2, 26, 29, { fold: 5 });
  for (let y = 8; y <= 27; y += 2) c.hl(9, 23 - ((y * 5) % 4), y, 'g4');
  c.rect(8, 13, 24, 19, 'y'); c.hl(9, 22, 14, 'g7'); c.hl(9, 20, 16, 'g7'); c.hl(9, 21, 18, 'g7');
  c.vl(6, 12, 20, 'b');
  c.rect(23, 22, 30, 26, 'b'); c.box(23, 22, 30, 26, 'g8'); c.hl(25, 28, 24, 'w');
  dropShadow(c, 1, 1);
}
function sect16(c) {
  grid(c, [
    '..88|8888|888.|....',
    '..8W|WWWW|W8W8|....',
    '..8W|4444|W888|8...',
    '..8W|WWWW|WWWW|8...',
    '.B8Y|7777|77YY|8...',
    '.B8Y|YYYY|YYYY|8...',
    '.B8Y|7777|7YYY|8...',
    '.B8Y|YYYY|YYYY|8...',
    '.B8Y|777Y|YYYY|8...',
    '..8W|WWWW|WWWW|8...',
    '..8W|4444|4WW8|8888',
    '..8W|WWWW|WWW8|BWB8',
    '..8W|4444|WWW8|8888',
    '..8W|WWWW|WWWW|8...',
    '..88|8888|8888|8...',
    '....|....|....|....',
  ]);
}

module.exports = [
  { name: 'D32web', title: 'Web Browser (Fathom)', ref: 'DISTRO-DESIGN 6.9 / fidelity 23 (revised brief); BROWSER-MEDIA 6.5', concept: 'the sounding lead over chart grid lines, with a page corner at 48 px; no compass, globe or animal', draw: { l: web48, m: web32, t: web16 } },
  { name: 'D32media', title: 'Media Player (mpv with the CDE controller)', ref: 'LOOK-SOUND 6.2 (D32media); DISTRO-DESIGN 3.3, 6.4', concept: 'a monitor with a play mark and speaker grilles', draw: { l: media48, m: media32, t: media16 } },
  { name: 'D32img', title: 'Image Viewer (xv)', ref: 'DISTRO-DESIGN 3.3, 6.4 (Applications subpanel)', concept: 'a framed photograph on an easel back', draw: { l: img48, m: img32, t: img16 } },
  { name: 'D32scap', title: 'Screen Capture', ref: 'DISTRO-DESIGN 3.3, 6.4 (Applications subpanel)', concept: 'a box camera in front of a monitor with a dashed capture box', draw: { l: scap48, m: scap32, t: scap16 } },
  { name: 'D32sect', title: 'Section viewer (RAG section with its cite)', ref: 'ISO-DESIGN 3.2 (DISTRO-DESIGN 3.2 list)', concept: 'a page with a bracketed, highlighted passage and a cite tab', draw: { l: sect48, m: sect32, t: sect16 } },
];
