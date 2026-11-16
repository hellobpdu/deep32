'use strict';
// Data-type icons for deep32.dt (LOOK-SOUND 6.3): 32x32 (.m) for dtfile and the desktop, 16x16 (.t) for lists.
const { dropShadow, page, grid } = require('../../lib/kit');
const { F3x5 } = require('../../lib/font');

const navy = (x, y) => (((x + y) & 1) ? 'b' : 'k');
const forest = (x, y) => (((x + y) & 1) ? 'g' : 'k');

function p32(c, o = {}) { page(c, 6, 1, 26, 30, { fold: 6, ...o }); }
function p16(c) {
  grid(c, [
    '..88|8888|88..|....',
    '..8W|WWWW|W88.|....',
    '..8W|WWWW|W828|....',
    '..8W|WWWW|W888|8...',
    '..8W|WWWW|WWW2|8...',
    '..8W|WWWW|WWW2|8...',
    '..8W|WWWW|WWW2|8...',
    '..8W|WWWW|WWW2|8...',
    '..8W|WWWW|WWW2|8...',
    '..8W|WWWW|WWW2|8...',
    '..8W|WWWW|WWW2|8...',
    '..8W|WWWW|WWW2|8...',
    '..8W|WWWW|WWW2|8...',
    '..8W|WWWW|WWW2|8...',
    '..82|2222|2222|8...',
    '..88|8888|8888|8...',
  ]);
}
function lines32(c, x0, x1, ys, tok = 'g4') { ys.forEach((y, i) => c.hl(x0, x1 - (i % 3) * 3, y, tok)); }

const T = [];
function def(name, title, concept, m, t) { T.push({ name, title, ref: 'LOOK-SOUND 6.3 (deep32.dt data types)', concept, datatype: true, draw: { m, t } }); }

def('D32rfc', 'D32_RFC (RFC text)', 'a page with a grey header band engraved "RFC"',
  c => { p32(c); c.rect(7, 4, 20, 10, 'g4'); c.text(F3x5, 'RFC', 9, 5, 'g8'); lines32(c, 9, 23, [13, 15, 17, 19, 22, 24, 26]); c.hl(9, 14, 13, 'g7'); dropShadow(c, 1, 1); },
  c => { p16(c); c.rect(3, 2, 9, 4, 'g4'); c.hl(4, 8, 3, 'g8'); c.hl(4, 11, 6, 'g4'); c.hl(4, 10, 8, 'g4'); c.hl(4, 11, 10, 'g4'); c.hl(4, 8, 12, 'g4'); });
def('D32cgd', 'D32_CISCOPDF (a Cisco guide from the Library)', 'a blue-banded manual cover; no logo',
  c => { c.rect(6, 1, 26, 30, 'w'); c.box(6, 1, 26, 30, 'g8'); c.rect(7, 2, 25, 11, navy); c.hl(9, 20, 6, 'w'); c.hl(9, 16, 8, 'w'); c.rect(7, 27, 25, 29, 'g4'); c.vl(7, 2, 29, 'g6'); c.hl(10, 22, 15, 'g4'); c.hl(10, 18, 17, 'g4'); dropShadow(c, 1, 1); },
  c => { grid(c, [
    '..88|8888|8888|8...', '..86|NNNN|NNNN|8...', '..86|NWWW|WNNN|8...', '..86|NWWW|NNNN|8...', '..86|NNNN|NNNN|8...', '..86|WWWW|WWWW|8...', '..86|W444|44WW|8...', '..86|WWWW|WWWW|8...',
    '..86|W444|WWWW|8...', '..86|WWWW|WWWW|8...', '..86|WWWW|WWWW|8...', '..86|WWWW|WWWW|8...', '..86|4444|4444|8...', '..86|4444|4444|8...', '..88|8888|8888|8...', '....|....|....|....'], { N: navy }); });
def('D32pdf', 'PDF (rebound to the Reader)', 'a page with a curled corner and a red band',
  c => { page(c, 6, 1, 26, 30, { fold: 0 }); c.rect(7, 3, 25, 7, 'r'); lines32(c, 9, 23, [11, 13, 15, 17, 19, 21]);
    for (let i = 0; i < 6; i++) for (let j = 0; j < 6 - i; j++) c.clear(26 - i, 30 - j);
    c.poly([[20, 30], [26, 24], [21, 25]], 'g2'); c.line(20, 30, 26, 24, 'g8'); c.line(20, 30, 21, 25, 'g8'); c.line(21, 25, 26, 24, 'g8'); dropShadow(c, 1, 1); },
  c => { p16(c); c.rect(3, 2, 12, 3, 'r'); c.hl(4, 11, 6, 'g4'); c.hl(4, 9, 8, 'g4'); c.hl(4, 11, 10, 'g4'); c.clear(13, 14); c.clear(13, 15); c.clear(12, 15); c.set(12, 14, 'g8'); c.set(11, 15, 'g8'); c.set(13, 13, 'g8'); });

def('D32ntd', 'D32_NOTE (*.d32note)', 'an index card',
  c => { c.rect(2, 7, 29, 25, 'w'); c.box(2, 7, 29, 25, 'g8'); c.hl(3, 28, 10, 'r'); for (let y = 13; y <= 22; y += 3) c.hl(3, 28, y, 'b'); c.hl(5, 18, 12, 'g6'); c.hl(5, 15, 15, 'g6'); dropShadow(c, 1, 1); },
  c => { grid(c, ['....|....|....|....', '....|....|....|....', '8888|8888|8888|8888', '8RRR|RRRR|RRRR|RRR8', '8W66|6666|6WWW|WWW8', '8BBB|BBBB|BBBB|BBB8', '8W66|666W|WWWW|WWW8', '8BBB|BBBB|BBBB|BBB8', '8WWW|WWWW|WWWW|WWW8', '8BBB|BBBB|BBBB|BBB8', '8888|8888|8888|8888', '....|....|....|....', '....|....|....|....', '....|....|....|....', '....|....|....|....', '....|....|....|....']); });
def('D32cfg', 'D32_CONFIG (*.cfg device configurations)', 'a page with "!" margin marks and an interface line',
  c => { p32(c); for (const y of [6, 14, 22]) { c.vl(9, y, y + 2, 'g8'); c.set(9, y + 4, 'g8'); } c.hl(12, 22, 9, 'b'); c.hl(14, 21, 11, 'g4'); c.hl(14, 19, 17, 'g4'); c.hl(14, 22, 19, 'g4'); c.hl(12, 18, 25, 'g4'); dropShadow(c, 1, 1); },
  c => { p16(c); c.vl(4, 2, 3, 'g8'); c.set(4, 5, 'g8'); c.hl(6, 10, 3, 'b'); c.hl(7, 11, 7, 'g4'); c.vl(4, 9, 10, 'g8'); c.set(4, 12, 'g8'); c.hl(7, 10, 11, 'g4'); });
def('D32pcp', 'D32_PCAP (*.pcap, *.pcapng)', 'a page with a dotted timeline of packets',
  c => { p32(c); c.hl(8, 24, 20, 'g6'); const h = [3, 7, 2, 5, 9, 4, 6, 2]; h.forEach((v, i) => { const x = 9 + i * 2; for (let y = 19; y > 19 - v; y -= 2) c.set(x, y, i % 3 ? 'b' : 'r'); }); lines32(c, 9, 22, [24, 26]); c.hl(9, 15, 5, 'g7'); dropShadow(c, 1, 1); },
  c => { p16(c); c.hl(3, 12, 10, 'g6'); [2, 5, 3, 6, 1, 4, 2].forEach((v, i) => { for (let y = 9; y > 9 - v; y -= 2) c.set(4 + i, y, i % 3 ? 'b' : 'r'); }); c.hl(4, 9, 13, 'g4'); });
def('D32ses', 'D32_SESSION (*.d32s saved sessions)', 'a small terminal with a tab',
  c => { c.rect(4, 5, 13, 8, 'w'); c.box(4, 5, 13, 8, 'g8'); c.rect(3, 8, 28, 26, 'g5'); c.box(3, 8, 28, 26, 'g8'); c.rect(5, 10, 26, 24, 'g8'); c.hl(7, 12, 13, 'g'); c.hl(7, 18, 16, 'g4'); c.hl(7, 9, 19, 'g'); c.rect(11, 19, 12, 20, 'w'); dropShadow(c, 1, 1); },
  c => { grid(c, ['....|....|....|....', '.888|888.|....|....', '.8WW|WW8.|....|....', '8888|8888|8888|8888', '8588|8888|8888|8858', '858G|GG88|8888|8858', '8588|8888|8888|8858', '858G|8W88|8888|8858', '8588|8888|8888|8858', '8555|5555|5555|5558', '8888|8888|8888|8888', '....|....|....|....', '....|....|....|....', '....|....|....|....', '....|....|....|....', '....|....|....|....']); });
def('D32trn', 'D32_TRANSCRIPT (*.d32t Ask transcripts)', 'a page with a speech balloon',
  c => { p32(c); lines32(c, 9, 23, [17, 19, 21, 23, 25, 27]); c.oval(9, 4, 21, 13, 'g1'); c.ring(9, 4, 21, 13, 1, 'g8'); c.poly([[11, 11], [13, 12], [9, 16]], 'g1'); c.line(11, 12, 9, 16, 'g8'); c.line(9, 16, 14, 12, 'g8'); c.hl(12, 18, 8, 'b'); c.hl(12, 16, 10, 'b'); dropShadow(c, 1, 1); },
  c => { p16(c); c.box(4, 2, 10, 6, 'g8'); c.hl(5, 9, 4, 'b'); c.set(5, 7, 'g8'); c.hl(4, 11, 10, 'g4'); c.hl(4, 9, 12, 'g4'); });
def('D32htm', 'HTML and URL (to the browser)', 'a page with an underlined link',
  c => { p32(c); lines32(c, 9, 23, [6, 8, 10, 22, 24, 26]); c.hl(9, 20, 14, 'b'); c.hl(9, 20, 15, 'b'); c.hl(9, 18, 18, 'g4'); dropShadow(c, 1, 1); },
  c => { p16(c); c.hl(4, 11, 3, 'g4'); c.hl(4, 9, 6, 'b'); c.hl(4, 9, 7, 'b'); c.hl(4, 11, 10, 'g4'); c.hl(4, 10, 12, 'g4'); });
def('D32vid', 'VIDEO (to the Media Player)', 'a page with a film-strip band and a play mark',
  c => { p32(c); c.rect(7, 9, 25, 21, 'g8'); for (let x = 8; x <= 24; x += 3) { c.set(x, 10, 'w'); c.set(x, 20, 'w'); } c.poly([[13, 12], [19, 15], [13, 18]], 'w'); dropShadow(c, 1, 1); },
  c => { p16(c); c.rect(3, 4, 12, 10, 'g8'); for (let x = 3; x <= 12; x += 2) { c.set(x, 4, 'w'); c.set(x, 10, 'w'); } c.vl(6, 5, 9, 'w'); c.vl(7, 6, 8, 'w'); c.set(8, 7, 'w'); });
def('D32aud', 'AUDIO (to the Media Player)', 'a page with a speaker and arcs',
  c => { p32(c); c.rect(9, 13, 11, 18, 'g7'); c.poly([[11, 13], [16, 9], [16, 22], [11, 18]], 'g5'); c.ring(13, 10, 21, 21, 1, 'g8', dx => dx > 1); c.ring(11, 7, 24, 24, 1, 'g8', dx => dx > 2); dropShadow(c, 1, 1); },
  c => { p16(c); c.rect(4, 6, 5, 9, 'g7'); c.vl(6, 5, 10, 'g5'); c.vl(7, 4, 11, 'g5'); c.vl(9, 5, 10, 'g8'); c.vl(11, 4, 11, 'g8'); });
def('D32vol', 'D32_VOLUME (the mounted /deep32)', 'the Disk Pack: stacked platters, hub and an amber lamp (ASSETS 3.4 B)',
  c => {
    for (let k = 3; k >= 0; k--) { const y = 10 + k * 4; c.oval(3, y, 28, y + 10, k === 0 ? 'g2' : 'g4'); c.ring(3, y, 28, y + 10, 1, 'g8', (dx, dy) => dy > 0 || k === 0); }
    c.rect(3, 15, 28, 26, (x, y) => x < 10 ? 'g3' : x > 22 ? 'g6' : 'g5'); c.vl(3, 15, 26, 'g8'); c.vl(28, 15, 26, 'g8');
    for (const y of [18, 22]) c.shape(4, y - 1, 27, y + 2, (x, yy) => yy === y + Math.round(1.5 * Math.cos((x - 15.5) / 8)), 'g7');
    c.oval(3, 26 - 5, 28, 31 - 2, 'g5'); c.ring(3, 21, 28, 29, 1, 'g8', (dx, dy) => dy > 0);
    c.oval(3, 10, 28, 20, 'g2'); c.ring(3, 10, 28, 20, 1, 'g8');
    c.oval(12, 13, 19, 17, 'g5'); c.ring(12, 13, 19, 17, 1, 'g7'); c.text(F3x5, '32', 21, 12, 'g7');
    c.rect(23, 22, 24, 23, 'y'); dropShadow(c, 1, 1);
  },
  c => { grid(c, [
    '....|....|....|....', '....|....|....|....', '...8|8888|8888|....', '.88W|2222|2222|88..', '.822|2255|5222|228.', '.832|2222|2222|268.', '.833|8888|8888|668.', '.833|3555|5566|668.', '.873|3555|5566|678.', '.833|7777|7777|668.', '.833|3555|5Y66|668.', '.833|3555|5566|668.', '..88|8888|8888|88..', '....|....|....|....', '....|....|....|....', '....|....|....|....']); });

module.exports = T;
