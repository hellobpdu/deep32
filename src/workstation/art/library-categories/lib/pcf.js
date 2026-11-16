'use strict';
// Minimal PCF (X11 Portable Compiled Font) reader: properties, metrics, bitmaps, encodings.
// Used once, by tools/import-font.js, to turn the X11 Adobe Helvetica bitmap faces into fonts/*.json.
const zlib = require('zlib');

const T = { PROPERTIES: 1, METRICS: 4, BITMAPS: 8, BDF_ENCODINGS: 32 };

function reader(buf, off) {
  const fmt = buf.readInt32LE(off);
  const msb = (fmt & 4) !== 0;
  let p = off + 4;
  const r = {
    fmt, msb,
    i32() { const v = msb ? buf.readInt32BE(p) : buf.readInt32LE(p); p += 4; return v; },
    i16() { const v = msb ? buf.readInt16BE(p) : buf.readInt16LE(p); p += 2; return v; },
    u16() { const v = msb ? buf.readUInt16BE(p) : buf.readUInt16LE(p); p += 2; return v; },
    u8() { return buf[p++]; },
    skip(n) { p += n; },
    get pos() { return p; }, set pos(v) { p = v; },
  };
  return r;
}

function parsePCF(input) {
  const buf = input[0] === 0x1f && input[1] === 0x8b ? zlib.gunzipSync(input) : input;
  if (buf.toString('latin1', 0, 4) !== '\x01fcp') throw new Error('pcf: bad magic');
  const n = buf.readInt32LE(4);
  const toc = {};
  for (let i = 0; i < n; i++) {
    const o = 8 + i * 16;
    toc[buf.readInt32LE(o)] = { format: buf.readInt32LE(o + 4), size: buf.readInt32LE(o + 8), offset: buf.readInt32LE(o + 12) };
  }
  // properties
  const props = {};
  {
    const r = reader(buf, toc[T.PROPERTIES].offset);
    const np = r.i32(); const list = [];
    for (let i = 0; i < np; i++) list.push({ name: r.i32(), isStr: r.u8(), value: r.i32() });
    if (np & 3) r.skip(4 - (np & 3));
    const ssize = r.i32(); const sbase = r.pos;
    const str = o => { let e = sbase + o; while (buf[e]) e++; return buf.toString('latin1', sbase + o, e); };
    for (const pr of list) props[str(pr.name)] = pr.isStr ? str(pr.value) : pr.value;
    void ssize;
  }
  // metrics
  const metrics = [];
  {
    const r = reader(buf, toc[T.METRICS].offset);
    if (r.fmt & 0x100) {
      const c = r.i16();
      for (let i = 0; i < c; i++) metrics.push({ lsb: r.u8() - 0x80, rsb: r.u8() - 0x80, width: r.u8() - 0x80, ascent: r.u8() - 0x80, descent: r.u8() - 0x80 });
    } else {
      const c = r.i32();
      for (let i = 0; i < c; i++) { metrics.push({ lsb: r.i16(), rsb: r.i16(), width: r.i16(), ascent: r.i16(), descent: r.i16() }); r.i16(); }
    }
  }
  // bitmaps
  const glyphs = [];
  {
    const r = reader(buf, toc[T.BITMAPS].offset);
    const fmt = r.fmt; const pad = 1 << (fmt & 3); const bitMsb = (fmt & 8) !== 0; const unit = 1 << ((fmt >> 4) & 3);
    if (unit !== 1 && r.msb !== bitMsb) throw new Error('pcf: unsupported scan unit/byte order mix');
    const c = r.i32(); const offs = []; for (let i = 0; i < c; i++) offs.push(r.i32());
    const sizes = [r.i32(), r.i32(), r.i32(), r.i32()];
    const base = r.pos;
    void sizes;
    for (let i = 0; i < c; i++) {
      const m = metrics[i]; const w = m.rsb - m.lsb, h = m.ascent + m.descent;
      const stride = Math.ceil(Math.ceil(w / 8) / pad) * pad;
      const rows = [];
      for (let y = 0; y < h; y++) {
        let s = '';
        for (let x = 0; x < w; x++) {
          const byte = buf[base + offs[i] + y * stride + (x >> 3)];
          const bit = bitMsb ? (byte >> (7 - (x & 7))) & 1 : (byte >> (x & 7)) & 1;
          s += bit ? '#' : '.';
        }
        rows.push(s);
      }
      glyphs.push(rows);
    }
  }
  // encodings
  const map = {};
  {
    const r = reader(buf, toc[T.BDF_ENCODINGS].offset);
    const min2 = r.i16(), max2 = r.i16(), min1 = r.i16(), max1 = r.i16(); r.i16();
    for (let b1 = min1; b1 <= max1; b1++) for (let b2 = min2; b2 <= max2; b2++) {
      const idx = r.u16();
      if (idx !== 0xFFFF) map[(b1 << 8) | b2] = idx;
    }
  }
  return { props, metrics, glyphs, map };
}

module.exports = { parsePCF };
