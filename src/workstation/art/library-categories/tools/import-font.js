#!/usr/bin/env node
'use strict';
// One-off: turns an X11 PCF bitmap face (copied from the build VM's /usr/X11R6/lib/X11/fonts) into fonts/<name>.json,
// printable ASCII plus the Latin-1 letters, with the face's own COPYRIGHT and NOTICE properties kept beside the glyphs.
//   node tools/import-font.js fonts/src/helvB12-75dpi.pcf.gz fonts/helvB12-75dpi.json
const fs = require('fs');
const { parsePCF } = require('../lib/pcf');
const [src, dst] = process.argv.slice(2);
const p = parsePCF(fs.readFileSync(src));
const glyphs = {};
for (let c = 32; c <= 255; c++) {
  if (c > 126 && c < 160) continue;
  const i = p.map[c]; if (i === undefined) continue;
  const m = p.metrics[i];
  glyphs[c] = { lsb: m.lsb, width: m.width, ascent: m.ascent, rows: p.glyphs[i] };
}
const keep = ['FONT', 'FOUNDRY', 'FAMILY_NAME', 'WEIGHT_NAME', 'PIXEL_SIZE', 'POINT_SIZE', 'RESOLUTION_X', 'CAP_HEIGHT', 'X_HEIGHT', 'COPYRIGHT', 'NOTICE'];
const props = Object.fromEntries(keep.filter(k => p.props[k] !== undefined).map(k => [k, p.props[k]]));
fs.writeFileSync(dst, JSON.stringify({ source: require('path').basename(src), props, glyphs }, null, 0) + '\n');
console.log(`${dst}: ${Object.keys(glyphs).length} glyphs, ${props.FONT}`);
