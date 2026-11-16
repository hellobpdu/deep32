'use strict';
// Truecolour image used only for previews and the contact sheet (the art itself lives in Canvas/XPM).
const { hexToRGB } = require('./cde');
const { encodeRGBA } = require('./png');

class Img {
  constructor(w, h, bg = '#000000') {
    this.w = w; this.h = h; this.data = Buffer.alloc(w * h * 4);
    if (bg) this.fill(0, 0, w, h, bg);
  }
  static of(w, h, rgba) { const i = new Img(w, h, null); rgba.copy ? rgba.copy(i.data) : i.data.set(rgba); return i; }
  px(x, y, r, g, b, a = 255) {
    if (x < 0 || y < 0 || x >= this.w || y >= this.h) return;
    const o = (y * this.w + x) * 4;
    if (a === 255) { this.data[o] = r; this.data[o + 1] = g; this.data[o + 2] = b; this.data[o + 3] = 255; return; }
    if (a === 0) return;
    const ia = 255 - a;
    this.data[o] = (r * a + this.data[o] * ia) / 255; this.data[o + 1] = (g * a + this.data[o + 1] * ia) / 255;
    this.data[o + 2] = (b * a + this.data[o + 2] * ia) / 255; this.data[o + 3] = 255;
  }
  fill(x, y, w, h, hex) { const [r, g, b] = hexToRGB(hex); for (let j = y; j < y + h; j++) for (let i = x; i < x + w; i++) this.px(i, j, r, g, b); return this; }
  frame(x, y, w, h, hex) { this.fill(x, y, w, 1, hex); this.fill(x, y + h - 1, w, 1, hex); this.fill(x, y, 1, h, hex); this.fill(x + w - 1, y, 1, h, hex); return this; }
  bevel(x, y, w, h, t, light, dark) {
    const [lr, lg, lb] = hexToRGB(light), [dr, dg, db] = hexToRGB(dark);
    for (let i = 0; i < t; i++) {
      for (let k = x; k <= x + w - 2 - i; k++) this.px(k, y + i, lr, lg, lb);
      for (let k = y; k <= y + h - 2 - i; k++) this.px(x + i, k, lr, lg, lb);
      for (let k = x + i; k < x + w; k++) this.px(k, y + h - 1 - i, dr, dg, db);
      for (let k = y + i; k < y + h; k++) this.px(x + w - 1 - i, k, dr, dg, db);
    }
    return this;
  }
  // Paste an RGBA buffer (w x h) at (x, y) scaled by an integer factor, nearest neighbour, alpha respected.
  paste(rgba, w, h, x, y, scale = 1) {
    for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
      const o = (j * w + i) * 4; const a = rgba[o + 3];
      if (!a) continue;
      for (let sy = 0; sy < scale; sy++) for (let sx = 0; sx < scale; sx++) this.px(x + i * scale + sx, y + j * scale + sy, rgba[o], rgba[o + 1], rgba[o + 2], a);
    }
    return this;
  }
  pasteImg(img, x, y, scale = 1) { return this.paste(img.data, img.w, img.h, x, y, scale); }
  // Tile an RGBA buffer over a box.
  tile(rgba, w, h, x, y, bw, bh) {
    for (let j = 0; j < bh; j++) for (let i = 0; i < bw; i++) {
      const o = (((j % h) * w) + (i % w)) * 4;
      this.px(x + i, y + j, rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3]);
    }
    return this;
  }
  text(font, str, x, y, hex, scale = 1, gap = 1) {
    const [r, g, b] = hexToRGB(hex);
    let cx = x;
    for (const ch of str) {
      const gl = font.glyph(ch);
      gl.rows.forEach((row, ry) => { for (let rx = 0; rx < row.length; rx++) if (row[rx] === '#') for (let sy = 0; sy < scale; sy++) for (let sx = 0; sx < scale; sx++) this.px(cx + rx * scale + sx, y + ry * scale + sy, r, g, b); });
      cx += (gl.w + gap) * scale;
    }
    return cx;
  }
  png() { return encodeRGBA(this.w, this.h, this.data); }
}

module.exports = { Img };
