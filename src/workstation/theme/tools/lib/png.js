'use strict';
// Minimal PNG writer/reader (8-bit RGB, no interlace), node's zlib only. Used by the preview renderer and by the
// validator, which re-reads every PNG (signature, chunk CRCs, IHDR, IDAT inflate, filters) and probes colours.
const zlib = require('zlib');
const fs = require('fs');

const CRC = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
  return t;
})();
function crc32(buf) { let c = 0xffffffff; for (let i = 0; i < buf.length; i++) c = CRC[(c ^ buf[i]) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; }
function chunk(type, data) {
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
}
const SIG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

// img: { w, h, p: Buffer(w*h*3) }
function encode(img) {
  const { w, h, p } = img;
  const raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y++) { raw[y * (w * 3 + 1)] = 0; p.copy(raw, y * (w * 3 + 1) + 1, y * w * 3, (y + 1) * w * 3); }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  return Buffer.concat([SIG, chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw, { level: 9 })), chunk('IEND', Buffer.alloc(0))]);
}
function write(file, img) { fs.writeFileSync(file, encode(img)); }

// Strict-ish reader: checks signature, every chunk CRC, IHDR rules, filters 0-4. Reads what our own encoder writes
// (RGB 8-bit) and what netpbm's pnmtopng writes for the VM renders (palette at 1/2/4/8 bits, grey, RGB, RGBA).
function decode(buf) {
  if (!buf.subarray(0, 8).equals(SIG)) throw new Error('bad PNG signature');
  let off = 8, ihdr = null, plte = null; const idat = []; let sawEnd = false;
  while (off < buf.length) {
    const len = buf.readUInt32BE(off); const type = buf.toString('ascii', off + 4, off + 8);
    const data = buf.subarray(off + 8, off + 8 + len);
    const crc = buf.readUInt32BE(off + 8 + len);
    if (crc32(buf.subarray(off + 4, off + 8 + len)) !== crc) throw new Error('CRC mismatch in ' + type);
    if (type === 'IHDR') ihdr = { w: data.readUInt32BE(0), h: data.readUInt32BE(4), depth: data[8], ct: data[9], il: data[12] };
    else if (type === 'PLTE') plte = Buffer.from(data);
    else if (type === 'IDAT') idat.push(data);
    else if (type === 'IEND') { sawEnd = true; break; }
    off += 12 + len;
  }
  if (!ihdr || !sawEnd) throw new Error('missing IHDR or IEND');
  const { w, h, depth, ct } = ihdr;
  const chans = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 }[ct];
  if (!chans || ihdr.il !== 0) throw new Error('unsupported PNG form');
  if (ct === 3 && !plte) throw new Error('palette PNG without PLTE');
  if (![1, 2, 4, 8].includes(depth) || (depth < 8 && ct !== 0 && ct !== 3)) throw new Error('unsupported bit depth ' + depth);
  const bitsPP = chans * depth, bpp = Math.max(1, bitsPP >> 3), stride = Math.ceil(w * bitsPP / 8);
  const raw = zlib.inflateSync(Buffer.concat(idat));
  if (raw.length !== (stride + 1) * h) throw new Error('IDAT size mismatch');
  const out = Buffer.alloc(w * h * 3), prev = Buffer.alloc(stride), cur = Buffer.alloc(stride);
  for (let y = 0; y < h; y++) {
    const ft = raw[y * (stride + 1)];
    if (ft > 4) throw new Error('bad filter type ' + ft);
    for (let i = 0; i < stride; i++) {
      const x = raw[y * (stride + 1) + 1 + i], a = i >= bpp ? cur[i - bpp] : 0, b = prev[i], c = i >= bpp ? prev[i - bpp] : 0;
      let v;
      if (ft === 0) v = x; else if (ft === 1) v = x + a; else if (ft === 2) v = x + b; else if (ft === 3) v = x + ((a + b) >> 1);
      else { const pp = a + b - c, pa = Math.abs(pp - a), pb = Math.abs(pp - b), pc = Math.abs(pp - c); v = x + (pa <= pb && pa <= pc ? a : pb <= pc ? b : c); }
      cur[i] = v & 0xff;
    }
    for (let x = 0; x < w; x++) {
      const o = (y * w + x) * 3;
      if (ct === 2 || ct === 6) { for (let k = 0; k < 3; k++) out[o + k] = cur[x * bpp + k]; continue; }
      let v;
      if (depth === 8) v = cur[x * bpp];
      else { const bit = x * depth; v = (cur[bit >> 3] >> (8 - depth - (bit & 7))) & ((1 << depth) - 1); }
      if (ct === 3) { if (v * 3 + 2 >= plte.length) throw new Error('palette index out of range'); out[o] = plte[v * 3]; out[o + 1] = plte[v * 3 + 1]; out[o + 2] = plte[v * 3 + 2]; }
      else { const g = depth === 8 ? v : Math.round(v * 255 / ((1 << depth) - 1)); out[o] = out[o + 1] = out[o + 2] = g; }
    }
    cur.copy(prev);
  }
  return { w, h, p: out };
}
function read(file) { return decode(fs.readFileSync(file)); }
function pixel(img, x, y) { const i = (y * img.w + x) * 3; return '#' + [0, 1, 2].map(k => img.p[i + k].toString(16).padStart(2, '0').toUpperCase()).join(''); }

module.exports = { encode, write, decode, read, pixel, crc32 };
