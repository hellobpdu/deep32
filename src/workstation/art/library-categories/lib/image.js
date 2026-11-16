'use strict';
// Palette-indexed images and their file formats, dependency-free (node's zlib only), byte-stable.
//   Indexed: { w, h, pal: ['#RRGGBB', ...], px: Uint8Array(w*h) of palette indices }
//   PNG  : colour type 3 (PLTE), bit depth 1/2/4/8 chosen from the palette size, filter 0, zlib level 9.
//   BMP  : Windows DIB (BITMAPINFOHEADER), 4 bpp up to 16 colours, else 8 bpp, uncompressed, bottom-up,
//          the same container the period design resources use (BI_RGB; the disc's RLE8 is a storage detail).
//   XPM  : XPM 3, one char per pixel, fixed #RRGGBB colours ("c" visual), as libXpm and XmGetPixmap read them.
const zlib = require('zlib');

const hexToRGB = h => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
const rgbToHex = (r, g, b) => '#' + [r, g, b].map(v => v.toString(16).padStart(2, '0')).join('').toUpperCase();

class Indexed {
  constructor(w, h, pal, fill = 0) {
    this.w = w; this.h = h; this.pal = pal.map(c => c.toUpperCase());
    this.px = new Uint8Array(w * h).fill(fill);
  }
  idx(c) { const i = this.pal.indexOf(c.toUpperCase()); if (i < 0) throw new Error(`colour ${c} not in palette ${this.pal}`); return i; }
  set(x, y, i) { if (x >= 0 && y >= 0 && x < this.w && y < this.h) this.px[y * this.w + x] = i; }
  get(x, y) { return this.px[y * this.w + x]; }
  colour(x, y) { return this.pal[this.get(x, y)]; }
  fillRect(x, y, w, h, i) { for (let j = y; j < y + h; j++) for (let k = x; k < x + w; k++) this.set(k, j, i); }
  blit(src, sx, sy, w, h, dx, dy, map = null) {
    for (let j = 0; j < h; j++) for (let k = 0; k < w; k++) {
      const v = src.get(sx + k, sy + j);
      this.set(dx + k, dy + j, map ? map[v] : v);
    }
  }
  clone() { const o = new Indexed(this.w, this.h, this.pal); o.px.set(this.px); return o; }
  usedColours() { const s = new Set(this.px); return [...s].sort((a, b) => a - b).map(i => this.pal[i]); }
  toRGBA() {
    const out = Buffer.alloc(this.w * this.h * 4); const rgb = this.pal.map(hexToRGB);
    for (let i = 0; i < this.px.length; i++) { const c = rgb[this.px[i]]; out[i * 4] = c[0]; out[i * 4 + 1] = c[1]; out[i * 4 + 2] = c[2]; out[i * 4 + 3] = 255; }
    return out;
  }
}

// ---------- PNG ----------
const CRC = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1); t[n] = c >>> 0; } return t; })();
const crc32 = b => { let c = 0xFFFFFFFF; for (let i = 0; i < b.length; i++) c = CRC[(c ^ b[i]) & 0xFF] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; };
function chunk(type, data) {
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length, 0);
  const td = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(td), 0);
  return Buffer.concat([len, td, crc]);
}
const SIG = Buffer.from([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]);
function depthFor(n) { return n <= 2 ? 1 : n <= 4 ? 2 : n <= 16 ? 4 : 8; }

function pngIndexed(img) {
  const d = depthFor(img.pal.length), per = 8 / d, stride = Math.ceil(img.w / per);
  const raw = Buffer.alloc((stride + 1) * img.h);
  for (let y = 0; y < img.h; y++) {
    const o = y * (stride + 1) + 1;
    for (let x = 0; x < img.w; x++) raw[o + Math.floor(x / per)] |= img.px[y * img.w + x] << (8 - d - (x % per) * d);
  }
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(img.w, 0); ihdr.writeUInt32BE(img.h, 4); ihdr[8] = d; ihdr[9] = 3;
  const plte = Buffer.from(img.pal.flatMap(hexToRGB));
  return Buffer.concat([SIG, chunk('IHDR', ihdr), chunk('PLTE', plte), chunk('IDAT', zlib.deflateSync(raw, { level: 9 })), chunk('IEND', Buffer.alloc(0))]);
}

function pngRGBA(w, h, rgba) {
  const raw = Buffer.alloc((w * 4 + 1) * h);
  for (let y = 0; y < h; y++) Buffer.from(rgba.buffer, rgba.byteOffset + y * w * 4, w * 4).copy(raw, y * (w * 4 + 1) + 1);
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 6;
  return Buffer.concat([SIG, chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw, { level: 9 })), chunk('IEND', Buffer.alloc(0))]);
}

const paeth = (a, b, c) => { const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c); return (pa <= pb && pa <= pc) ? a : (pb <= pc ? b : c); };
// Decodes colour types 0, 2, 3, 6 (8-bit, and 1/2/4-bit for type 3). Returns { w, h, rgba, pal|null, index|null }.
function decodePNG(buf) {
  if (!buf.subarray(0, 8).equals(SIG)) throw new Error('png: bad signature');
  let off = 8, w = 0, h = 0, depth = 0, ct = 0, plte = null; const idat = [];
  while (off < buf.length) {
    const len = buf.readUInt32BE(off), type = buf.toString('ascii', off + 4, off + 8), data = buf.subarray(off + 8, off + 8 + len);
    if (crc32(buf.subarray(off + 4, off + 8 + len)) !== buf.readUInt32BE(off + 8 + len)) throw new Error(`png: CRC ${type}`);
    if (type === 'IHDR') { w = data.readUInt32BE(0); h = data.readUInt32BE(4); depth = data[8]; ct = data[9]; if (data[12]) throw new Error('png: interlaced'); }
    else if (type === 'PLTE') plte = data;
    else if (type === 'IDAT') idat.push(data);
    else if (type === 'IEND') break;
    off += 12 + len;
  }
  const ch = { 0: 1, 2: 3, 3: 1, 6: 4 }[ct]; if (!ch) throw new Error(`png: colour type ${ct}`);
  const bpp = Math.max(1, (ch * depth) >> 3), stride = Math.ceil(w * ch * depth / 8);
  const raw = zlib.inflateSync(Buffer.concat(idat)); const cur = Buffer.alloc(stride), prev = Buffer.alloc(stride);
  const rgba = Buffer.alloc(w * h * 4); const index = ct === 3 ? new Uint8Array(w * h) : null;
  for (let y = 0; y < h; y++) {
    const f = raw[y * (stride + 1)]; raw.copy(cur, 0, y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let i = 0; i < stride; i++) {
      const a = i >= bpp ? cur[i - bpp] : 0, b = prev[i], c = i >= bpp ? prev[i - bpp] : 0;
      cur[i] = (cur[i] + [0, a, b, (a + b) >> 1, paeth(a, b, c)][f]) & 0xFF;
    }
    for (let x = 0; x < w; x++) {
      const o = (y * w + x) * 4;
      if (ct === 3) {
        const per = 8 / depth, v = (cur[Math.floor(x / per)] >> (8 - depth - (x % per) * depth)) & ((1 << depth) - 1);
        index[y * w + x] = v; rgba[o] = plte[v * 3]; rgba[o + 1] = plte[v * 3 + 1]; rgba[o + 2] = plte[v * 3 + 2]; rgba[o + 3] = 255;
      } else if (ct === 2) { rgba[o] = cur[x * 3]; rgba[o + 1] = cur[x * 3 + 1]; rgba[o + 2] = cur[x * 3 + 2]; rgba[o + 3] = 255; }
      else if (ct === 6) cur.copy(rgba, o, x * 4, x * 4 + 4);
      else { rgba[o] = rgba[o + 1] = rgba[o + 2] = cur[x]; rgba[o + 3] = 255; }
    }
    cur.copy(prev);
  }
  const pal = plte ? Array.from({ length: plte.length / 3 }, (_, i) => rgbToHex(plte[i * 3], plte[i * 3 + 1], plte[i * 3 + 2])) : null;
  return { w, h, rgba, pal, index };
}

// ---------- BMP (Windows DIB) ----------
function bmp(img, want = 0) {
  if (img.pal.length > 256) throw new Error('bmp: > 256 colours');
  const bpp = want || (img.pal.length <= 16 ? 4 : 8), nc = 1 << bpp;
  if (img.pal.length > nc) throw new Error(`bmp: ${img.pal.length} colours do not fit ${bpp} bpp`);
  const stride = Math.ceil(img.w * bpp / 32) * 4, dataOff = 14 + 40 + nc * 4, size = dataOff + stride * img.h;
  const b = Buffer.alloc(size);
  b.write('BM', 0, 'ascii'); b.writeUInt32LE(size, 2); b.writeUInt32LE(dataOff, 10);
  b.writeUInt32LE(40, 14); b.writeInt32LE(img.w, 18); b.writeInt32LE(img.h, 22); b.writeUInt16LE(1, 26); b.writeUInt16LE(bpp, 28);
  b.writeUInt32LE(0, 30); b.writeUInt32LE(stride * img.h, 34); b.writeInt32LE(3780, 38); b.writeInt32LE(3780, 42);
  b.writeUInt32LE(img.pal.length, 46); b.writeUInt32LE(img.pal.length, 50);
  img.pal.forEach((c, i) => { const [r, g, bl] = hexToRGB(c); b[54 + i * 4] = bl; b[55 + i * 4] = g; b[56 + i * 4] = r; });
  for (let y = 0; y < img.h; y++) {
    const row = dataOff + (img.h - 1 - y) * stride;
    for (let x = 0; x < img.w; x++) {
      const v = img.px[y * img.w + x];
      if (bpp === 8) b[row + x] = v; else b[row + (x >> 1)] |= (x & 1) ? v : v << 4;
    }
  }
  return b;
}
function decodeBMP(b) {
  if (b.toString('ascii', 0, 2) !== 'BM') throw new Error('bmp: bad magic');
  const off = b.readUInt32LE(10), w = b.readInt32LE(18), hh = b.readInt32LE(22), bpp = b.readUInt16LE(28), comp = b.readUInt32LE(30);
  if (comp !== 0 || (bpp !== 4 && bpp !== 8)) throw new Error(`bmp: unsupported ${bpp} bpp comp ${comp}`);
  const n = b.readUInt32LE(46) || (1 << bpp), h = Math.abs(hh), stride = Math.ceil(w * bpp / 32) * 4;
  const pal = Array.from({ length: n }, (_, i) => rgbToHex(b[56 + i * 4], b[55 + i * 4], b[54 + i * 4]));
  const img = new Indexed(w, h, pal);
  for (let y = 0; y < h; y++) {
    const row = off + (hh > 0 ? h - 1 - y : y) * stride;
    for (let x = 0; x < w; x++) img.px[y * w + x] = bpp === 8 ? b[row + x] : (x & 1 ? b[row + (x >> 1)] & 15 : b[row + (x >> 1)] >> 4);
  }
  return img;
}

// ---------- XPM ----------
const XPM_CHARS = ' .XoO+@#$%&*=-;:>,<1234567890qwertyuipasdfghjklzxcvbnmMNBVCZASDFGHJKLPIUYTREWQ!~^/()_`\'][{}|';
function xpm(img, name) {
  const key = img.pal.map((_, i) => XPM_CHARS[i]);
  const lines = [`/* XPM */`, `static char * ${name.replace(/[^A-Za-z0-9_]/g, '_')}[] = {`, `"${img.w} ${img.h} ${img.pal.length} 1",`];
  img.pal.forEach((c, i) => lines.push(`"${key[i]}\tc ${c}",`));
  for (let y = 0; y < img.h; y++) {
    let s = ''; for (let x = 0; x < img.w; x++) s += key[img.px[y * img.w + x]];
    lines.push(`"${s}"${y === img.h - 1 ? '' : ','}`);
  }
  lines.push('};', '');
  return lines.join('\n');
}
function parseXPM(text) {
  const strs = [...text.matchAll(/"((?:[^"\\]|\\.)*)"/g)].map(m => m[1]);
  const [w, h, n, cpp] = strs[0].trim().split(/\s+/).map(Number);
  const map = new Map(); const pal = [];
  for (let i = 0; i < n; i++) {
    const s = strs[1 + i]; const k = s.slice(0, cpp); const m = s.slice(cpp).match(/\bc\s+(#[0-9A-Fa-f]{6}|None)/);
    if (!m) throw new Error(`xpm: no c colour in ${s}`);
    map.set(k, pal.length); pal.push(m[1].toUpperCase());
  }
  const img = new Indexed(w, h, pal);
  for (let y = 0; y < h; y++) {
    const row = strs[1 + n + y]; if (row.length !== w * cpp) throw new Error(`xpm: row ${y} length ${row.length}`);
    for (let x = 0; x < w; x++) img.px[y * w + x] = map.get(row.slice(x * cpp, x * cpp + cpp));
  }
  return img;
}

// ---------- colour science (for the taxonomy's colour tests) ----------
const lin = v => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
const luminance = h => { const [r, g, b] = hexToRGB(h).map(lin); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const contrast = (a, b) => { const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
function lab(h) {
  const [r, g, b] = hexToRGB(h).map(lin);
  const X = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047, Y = 0.2126 * r + 0.7152 * g + 0.0722 * b, Z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883;
  const f = t => (t > 216 / 24389 ? Math.cbrt(t) : (24389 / 27 * t + 16) / 116);
  return [116 * f(Y) - 16, 500 * (f(X) - f(Y)), 200 * (f(Y) - f(Z))];
}
const deltaE76 = (a, b) => { const p = lab(a), q = lab(b); return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]); };

module.exports = { Indexed, pngIndexed, pngRGBA, decodePNG, bmp, decodeBMP, xpm, parseXPM, hexToRGB, rgbToHex, contrast, deltaE76, luminance };
