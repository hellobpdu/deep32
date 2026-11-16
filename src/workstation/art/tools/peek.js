#!/usr/bin/env node
'use strict';
// Quick look while drawing: node tools/peek.js <out.png> <filter> [scale]
// Renders every item whose file name matches <filter> (a RegExp) at 1x and at <scale>x on three palettes.
const fs = require('fs');
const { Canvas } = require('../lib/canvas');
const { Img } = require('../lib/rgba');
const { colourSet } = require('../lib/cde');
const { F5x7 } = require('../lib/font');
const out = process.argv[2]; const re = new RegExp(process.argv[3] || '.'); const scale = +(process.argv[4] || 6);
const items = require('../src').items().filter(i => re.test(i.file));
const sets = process.argv[5] === 'all' ? [['Default', 8], ['Golden', 8], ['GrayScale', 5]] : [['Default', 8]];
let W = 10, H = 10; const rows = [];
for (const it of items) { const c = new Canvas(it.w, it.h); it.draw(c); rows.push([it, c]); }
const maxW = Math.max(...rows.map(([it]) => it.w)), cellW = maxW * scale + maxW + 30;
const perRow = Math.max(1, Math.floor(1800 / cellW));
const lines = Math.ceil(rows.length / perRow);
const rowH = Math.max(...rows.map(([it]) => it.h)) * scale + 24;
W = perRow * cellW + 10; H = lines * rowH * sets.length + 10;
const img = new Img(W, H, '#303030');
rows.forEach(([it, c], i) => {
  const col = i % perRow, line = Math.floor(i / perRow);
  sets.forEach((s, si) => {
    const set = it.preview === null ? null : colourSet(...(it.rule === 'cde' ? s : (it.preview || s)));
    const x = 5 + col * cellW, y = 5 + (line * sets.length + si) * rowH;
    img.fill(x, y, cellW - 6, rowH - 4, set ? set.background : '#C6B2A8');
    const rgba = c.toRGBA(set);
    img.paste(rgba, c.w, c.h, x + 4, y + 14, scale);
    img.paste(rgba, c.w, c.h, x + 8 + c.w * scale, y + 14, 1);
    if (si === 0) img.text(F5x7, it.file, x + 2, y + 2, '#000000');
  });
});
fs.writeFileSync(out, img.png());
console.log(`${rows.length} items -> ${out} (${W}x${H})`);
