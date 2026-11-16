'use strict';
// RGBA sheet for contact sheets and previews: paste indexed images at integer scales, captions in the label font.
const { hexToRGB, pngRGBA } = require('./image');
const font = require('./font');

class Sheet {
  constructor(w, h, bg = '#5A5A5A') {
    this.w = w; this.h = h; this.rgba = Buffer.alloc(w * h * 4);
    const [r, g, b] = hexToRGB(bg);
    for (let i = 0; i < w * h; i++) { this.rgba[i * 4] = r; this.rgba[i * 4 + 1] = g; this.rgba[i * 4 + 2] = b; this.rgba[i * 4 + 3] = 255; }
  }
  dot(x, y, rgb) { if (x < 0 || y < 0 || x >= this.w || y >= this.h) return; const o = (y * this.w + x) * 4; this.rgba[o] = rgb[0]; this.rgba[o + 1] = rgb[1]; this.rgba[o + 2] = rgb[2]; this.rgba[o + 3] = 255; }
  fill(x, y, w, h, hex) { const c = hexToRGB(hex); for (let j = y; j < y + h; j++) for (let i = x; i < x + w; i++) this.dot(i, j, c); }
  // img: Indexed (pal + px) or { w, h, rgba }
  paste(img, x, y, s = 1, crop = null) {
    const [cx, cy, cw, ch] = crop || [0, 0, img.w, img.h];
    const pal = img.pal ? img.pal.map(hexToRGB) : null;
    for (let j = 0; j < ch * s; j++) for (let i = 0; i < cw * s; i++) {
      const sx = cx + Math.floor(i / s), sy = cy + Math.floor(j / s);
      if (pal) this.dot(x + i, y + j, pal[img.px[sy * img.w + sx]]);
      else { const o = (sy * img.w + sx) * 4; this.dot(x + i, y + j, [img.rgba[o], img.rgba[o + 1], img.rgba[o + 2]]); }
    }
    return { w: cw * s, h: ch * s };
  }
  text(t, x, y, hex = '#FFFFFF') { const c = hexToRGB(hex); return font.draw(t, x, y + 9, (px, py) => this.dot(px, py, c)); }
  png() { return pngRGBA(this.w, this.h, this.rgba); }
}
module.exports = { Sheet };
