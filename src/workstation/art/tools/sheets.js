'use strict';
// Contact sheets (PNG previews only; the art itself is the XPM/XBM). render(built) -> [[relPath, Buffer], ...]
// built: [{ it, c }] from tools/build.js.
const { Img } = require('../lib/rgba');
const { F5x7 } = require('../lib/font');
const { colourSet } = require('../lib/cde');

const INK = '#000000', PAPER = '#BDBDBD', HEAD = '#1A1A1A';
function rgba(b, set) { return b.c.toRGBA(set); }
function setFor(b, pal = ['Default', 8]) { return b.it.preview === null ? null : colourSet(...(b.it.rule === 'cde' ? pal : (b.it.preview || pal))); }

// Grid of cells, each with the art at 1x and at `scale`x on the colour set's background, labelled.
function grid(title, list, { scale = 3, perRow = 8, pal = ['Default', 8], showOne = true } = {}) {
  if (!list.length) return null;
  const maxW = Math.max(...list.map(b => b.it.w)), maxH = Math.max(...list.map(b => b.it.h));
  const cellW = Math.max(maxW * scale + (showOne ? maxW + 18 : 12), 6 * 14 + 8), cellH = maxH * scale + 26;
  const cols = Math.min(perRow, list.length), rows = Math.ceil(list.length / cols);
  const img = new Img(cols * cellW + 10, rows * cellH + 26, PAPER);
  img.text(F5x7, title, 6, 6, HEAD);
  list.forEach((b, i) => {
    const x = 5 + (i % cols) * cellW, y = 20 + Math.floor(i / cols) * cellH, set = setFor(b, pal);
    img.fill(x, y, cellW - 4, cellH - 4, set ? set.background : '#C6C6C6');
    img.bevel(x, y, cellW - 4, cellH - 4, 1, set ? set.topShadowColor : '#FFFFFF', set ? set.bottomShadowColor : '#808080');
    const px = rgba(b, set);
    img.paste(px, b.it.w, b.it.h, x + 4, y + 14, scale);
    if (showOne) img.paste(px, b.it.w, b.it.h, x + 8 + b.it.w * scale, y + 14, 1);
    img.text(F5x7, b.it.file.slice(0, Math.floor((cellW - 8) / 6)), x + 3, y + 3, set && set.foreground === '#000000' ? '#000000' : '#FFFFFF');
  });
  return img;
}
// Every item at 1x on several palettes (does it read on light, grey and dark sets?).
function palettes(title, list, pals) {
  if (!list.length) return null;
  const maxW = Math.max(...list.map(b => b.it.w)), maxH = Math.max(...list.map(b => b.it.h));
  const cw = maxW + 6, perRow = Math.max(1, Math.min(list.length, Math.floor(1500 / cw)));
  const rowsPer = Math.ceil(list.length / perRow), bandH = rowsPer * (maxH + 6) + 14;
  const img = new Img(perRow * cw + 16, pals.length * bandH + 22, PAPER);
  img.text(F5x7, title, 6, 6, HEAD);
  pals.forEach((p, k) => {
    const set = colourSet(...p), y0 = 20 + k * bandH;
    img.fill(4, y0, perRow * cw + 8, bandH - 4, set.background);
    img.text(F5x7, `${p[0]} set ${p[1]}`, 8, y0 + 2, set.foreground);
    list.forEach((b, i) => img.paste(rgba(b, b.it.rule === 'cde' ? set : setFor(b)), b.it.w, b.it.h, 8 + (i % perRow) * cw, y0 + 12 + Math.floor(i / perRow) * (maxH + 6), 1));
  });
  return img;
}
// Backdrops tiled the way dtwm tiles them, in the four workspace colour sets.
function backdrops(list) {
  if (!list.length) return null;
  const sets = [3, 5, 6, 7], sw = 200, sh = 132;
  const img = new Img(sets.length * (sw + 8) + 150, list.length * (sh + 8) + 30, PAPER);
  img.text(F5x7, 'Deep32/UX backdrops, tiled, Default palette workspace sets 3 5 6 7 (Library, Lab, Study, Work)', 6, 6, HEAD);
  list.forEach((b, j) => {
    const y = 22 + j * (sh + 8);
    img.text(F5x7, b.it.file, 6, y + 2, HEAD); img.text(F5x7, `${b.it.w}x${b.it.h}`, 6, y + 12, HEAD);
    sets.forEach((s, i) => {
      const set = colourSet('Default', s), x = 146 + i * (sw + 8);
      // tall gradients: show a vertical slice (rows 0..1439 squeezed by sampling every 11th row) so the ramp shows
      if (b.it.h > 400) {
        const px = rgba(b, set), step = Math.ceil(b.it.h / sh);
        for (let yy = 0; yy < sh; yy++) for (let xx = 0; xx < sw; xx++) {
          const o = (Math.min(b.it.h - 1, yy * step) * b.it.w + (xx % b.it.w)) * 4; img.px(x + xx, y + yy, px[o], px[o + 1], px[o + 2]);
        }
      } else img.tile(rgba(b, set), b.it.w, b.it.h, x, y, sw, sh);
      img.frame(x - 1, y - 1, sw + 2, sh + 2, '#000000');
    });
  });
  return img;
}
// Deep32 Library new-room art at 1x and 3x (transparent pieces shown over the UI sky colour and over a checker).
function library(list) {
  if (!list.length) return null;
  const S = 3, W = Math.max(...list.map(b => b.it.w)) * (S + 1) + 40;
  const H = list.reduce((a, b) => a + b.it.h * S + 30, 30) + list.filter(b => b.it.file === 'ask-room').length * (67 * S + 30);
  const img = new Img(W, H, '#5A5A5A');
  img.text(F5x7, 'Deep32 Library new-room art (Ask): drawn as code in the disc\'s interface style; no disc pixels', 6, 6, '#FFFFFF');
  let y = 24;
  for (const b of list) {
    const px = rgba(b, null), grounds = b.it.file === 'ask-room' ? ['#E7E7EF', 'checker'] : [null];
    for (const g of grounds) {
      img.text(F5x7, `${b.it.file}  ${b.it.w}x${b.it.h}${b.it.frames ? `  (${b.it.frames[2]} frames of ${b.it.frames[0]}x${b.it.frames[1]})` : ''}${g ? `  over ${g}` : ''}`, 10, y, '#FFFFFF');
      const paint = (x0, s) => {
        if (g === 'checker') for (let j = 0; j < b.it.h * s; j++) for (let i = 0; i < b.it.w * s; i++) { const v = ((i >> 3) + (j >> 3)) & 1 ? 200 : 150; img.px(x0 + i, y + 10 + j, v, v, v); }
        else if (g) img.fill(x0, y + 10, b.it.w * s, b.it.h * s, g);
        img.paste(px, b.it.w, b.it.h, x0, y + 10, s);
      };
      paint(10, 1); paint(20 + b.it.w, S);
      y += b.it.h * S + 30;
    }
  }
  return img;
}
// One page with everything (icons 2x, the rest 1x), for a quick look.
function overview(built) {
  const parts = [];
  const pick = f => built.filter(f);
  const add = (title, list, opts) => { const g = grid(title, list, opts); if (g) parts.push(g); };
  add('Front panel and subpanel icons, 48x48 (.l), Default palette set 8', pick(b => b.it.group === 'icons' && b.it.sizeLetter === 'l'), { scale: 2, perRow: 12 });
  add('Icons 32x32 (.m)', pick(b => b.it.group.startsWith('icons') && b.it.sizeLetter === 'm'), { scale: 2, perRow: 16 });
  add('Icons 16x16 (.t)', pick(b => b.it.group.startsWith('icons') && b.it.sizeLetter === 't'), { scale: 3, perRow: 18 });
  add('Deep32/UX logo shortlist (A Strata, B Bezel Badge, C Root Bridge, D Operator Panel): CDE symbols on set 8', pick(b => b.it.group === 'logos' && b.it.sizeLetter), { scale: 2, perRow: 12 });
  add('Logo shortlist, brand palettes', pick(b => b.it.group === 'logos/brand' && b.it.sizeLetter), { scale: 2, perRow: 12 });
  add('dtlogin emblems 224x224 (brand)', pick(b => /\.login$/.test(b.it.file)), { scale: 1, perRow: 4, showOne: false });
  add('Wordmark and 1-bit boot emblems', pick(b => /\.(word|boot)$/.test(b.it.file)), { scale: 1, perRow: 4, showOne: false });
  const bd = backdrops(pick(b => b.it.backdrop)); if (bd) parts.push(bd);
  const en = library(pick(b => b.it.library)); if (en) parts.push(en);
  const W = Math.max(...parts.map(p => p.w)) + 10, H = parts.reduce((a, p) => a + p.h + 10, 40);
  const img = new Img(W, H, '#3A3A3A');
  img.text(F5x7, `Deep32/UX art lane: contact sheet of every piece (${built.length} pieces), generated by tools/build.js`, 6, 8, '#FFFFFF');
  let y = 26; for (const p of parts) { img.pasteImg(p, 5, y); y += p.h + 10; }
  return img;
}

function render(built) {
  const out = [];
  const put = (rel, img) => { if (img) out.push([rel, img.png()]); };
  const icons = built.filter(b => b.it.group.startsWith('icons'));
  put('sheets/icons-l.png', grid('Deep32 icons 48x48 (.l): front panel and subpanels, Default set 8, 1x and 3x', icons.filter(b => b.it.sizeLetter === 'l'), { scale: 3, perRow: 8 }));
  put('sheets/icons-m.png', grid('Deep32 icons 32x32 (.m), Default set 8, 1x and 4x', icons.filter(b => b.it.sizeLetter === 'm'), { scale: 4, perRow: 9 }));
  put('sheets/icons-t.png', grid('Deep32 icons 16x16 (.t), Default set 8, 1x and 6x', icons.filter(b => b.it.sizeLetter === 't'), { scale: 6, perRow: 10 }));
  put('sheets/icons-palettes.png', palettes('Every 32x32 icon at 1:1 on light, grey and dark colour sets', icons.filter(b => b.it.sizeLetter === 'm'),
    [['Default', 8], ['Default', 5], ['Crimson', 8], ['GrayScale', 5], ['Golden', 8], ['Charcoal', 8]]));
  const logos = built.filter(b => b.it.group.startsWith('logos'));
  put('sheets/logos.png', grid('Deep32/UX logo shortlist 16/32/48 (CDE symbols and brand), 1x and 4x', logos.filter(b => b.it.sizeLetter), { scale: 4, perRow: 6 }));
  put('sheets/logos-large.png', grid('Deep32/UX dtlogin emblems, wordmark, boot emblems (1x)', logos.filter(b => !b.it.sizeLetter), { scale: 1, perRow: 4, showOne: false }));
  put('sheets/logos-palettes.png', palettes('Logo shortlist (CDE symbol versions) on several palettes', logos.filter(b => b.it.group === 'logos' && b.it.sizeLetter), [['Default', 8], ['Crimson', 8], ['GrayScale', 8], ['Charcoal', 8]]));
  put('sheets/backdrops.png', backdrops(built.filter(b => b.it.backdrop)));
  put('sheets/d32library-ask.png', library(built.filter(b => b.it.library)));
  put('CONTACT-SHEET.png', overview(built));
  return out;
}
module.exports = { render };
