#!/usr/bin/env node
'use strict';
// Quick look while drawing: node tools/preview.js <out.png> [id,id,...]
// Per category: CAT strip and Find Wizard strip at 2x, the watermark at 1x, the icons at 1x and 4x.
const fs = require('fs');
const { Sheet } = require('../lib/sheet');
const cats = require('../src/categories');
const out = process.argv[2] || 'preview.png';
const want = process.argv[3] ? process.argv[3].split(',') : null;
const list = cats.all().filter(c => !want || want.includes(c.id));
const built = list.map(c => cats.build(c));
const rowH = 420, W = 20 + 680 * 2 + 20 + 323 + 20 + 140;
const sh = new Sheet(W, list.length * rowH + 10);
built.forEach((b, i) => {
  const y = 10 + i * rowH;
  sh.text(`${b.cat.order}. ${b.cat.bar_label}  (${b.cat.id})  face ${b.cat.face} ink ${b.cat.ink}`, 20, y);
  sh.paste(b.cat_strip, 20, y + 20, 2);
  sh.paste(b.fw_strip, 20, y + 120, 2);
  sh.paste(b.cat_strip, 20, y + 230, 1);
  sh.paste(b.fw_strip, 20, y + 290, 1);
  sh.paste(b.watermark, 20 + 1360 + 20, y + 10, 1);
  sh.paste(b.icon16, 1420 + 323 + 20, y + 20, 1);
  sh.paste(b.icon32, 1420 + 323 + 50, y + 20, 1);
  sh.paste(b.icon16, 1420 + 323 + 20, y + 70, 4);
  sh.paste(b.icon32, 1420 + 323 + 20, y + 150, 4);
});
fs.writeFileSync(out, sh.png());
console.log(`${out} ${sh.w}x${sh.h}`);
