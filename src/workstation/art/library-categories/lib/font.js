'use strict';
// Bitmap text from an imported X11 face (fonts/*.json, see tools/import-font.js). No anti-aliasing: one colour,
// exactly as the 1995 strips and as the X server draws a core bitmap font.
const path = require('path');
const cache = new Map();
function load(name = 'helvB12-75dpi') {
  if (!cache.has(name)) cache.set(name, require(path.join(__dirname, '..', 'fonts', `${name}.json`)));
  return cache.get(name);
}
function width(text, name) { const f = load(name); return [...text].reduce((a, c) => a + f.glyphs[c.charCodeAt(0)].width, 0); }
// Calls plot(x, y) for every set pixel of `text` with its origin at x (pen start) and the baseline at y
// (the row just under the capitals, as X's XDrawString takes it). Returns the pen advance.
function draw(text, x, y, plot, name) {
  const f = load(name); let pen = x;
  for (const ch of text) {
    const g = f.glyphs[ch.charCodeAt(0)]; if (!g) throw new Error(`font ${name}: no glyph for ${JSON.stringify(ch)}`);
    g.rows.forEach((row, j) => { for (let i = 0; i < row.length; i++) if (row[i] === '#') plot(pen + g.lsb + i, y - g.ascent + j); });
    pen += g.width;
  }
  return pen - x;
}
module.exports = { load, width, draw };
