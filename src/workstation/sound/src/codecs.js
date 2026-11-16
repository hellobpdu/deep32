'use strict';
// 16-bit PCM mono files: RIFF WAVE (little-endian, the v3 engine's 44-byte header) and Sun/NeXT .au (big-endian,
// encoding 3 = 16-bit linear PCM), the two formats OpenBSD's aucat(1) reads besides AIFF and raw. Strict readers
// for the tests: they reject anything that is not exactly the format written here.

const AU_HEADER = 32; // 24-byte header + an 8-byte annotation of NULs (some readers want at least 4)

function wavBytes(samples, rate) {
  const bytes = samples.length * 2;
  const b = Buffer.alloc(44 + bytes);
  b.write('RIFF', 0, 'ascii'); b.writeUInt32LE(36 + bytes, 4); b.write('WAVE', 8, 'ascii');
  b.write('fmt ', 12, 'ascii'); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22);
  b.writeUInt32LE(rate, 24); b.writeUInt32LE(rate * 2, 28); b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34);
  b.write('data', 36, 'ascii'); b.writeUInt32LE(bytes, 40);
  for (let i = 0; i < samples.length; i++) b.writeInt16LE(samples[i], 44 + 2 * i);
  return b;
}

function auBytes(samples, rate) {
  const bytes = samples.length * 2;
  const b = Buffer.alloc(AU_HEADER + bytes);
  b.writeUInt32BE(0x2e736e64, 0); // ".snd"
  b.writeUInt32BE(AU_HEADER, 4);  // data offset
  b.writeUInt32BE(bytes, 8);      // data size (exact; aucat uses it for the end position)
  b.writeUInt32BE(3, 12);         // encoding: 16-bit linear PCM
  b.writeUInt32BE(rate, 16);
  b.writeUInt32BE(1, 20);         // channels
  for (let i = 0; i < samples.length; i++) b.writeInt16BE(samples[i], AU_HEADER + 2 * i);
  return b;
}

function fail(what, msg) { throw new Error(`${what}: ${msg}`); }

// Parses a canonical 44-byte-header mono 16-bit PCM WAV; walks the chunks properly so a stray chunk is reported.
function readWav(buf, what = 'wav') {
  if (buf.length < 44) fail(what, 'shorter than a WAV header');
  if (buf.toString('ascii', 0, 4) !== 'RIFF' || buf.toString('ascii', 8, 12) !== 'WAVE') fail(what, 'not RIFF/WAVE');
  if (buf.readUInt32LE(4) !== buf.length - 8) fail(what, `RIFF size ${buf.readUInt32LE(4)} != file size - 8 (${buf.length - 8})`);
  let off = 12, fmt = null, data = null;
  while (off + 8 <= buf.length) {
    const id = buf.toString('ascii', off, off + 4), size = buf.readUInt32LE(off + 4);
    if (off + 8 + size > buf.length) fail(what, `chunk ${id} runs past the end`);
    if (id === 'fmt ') fmt = { off: off + 8, size };
    else if (id === 'data') data = { off: off + 8, size };
    else fail(what, `unexpected chunk ${JSON.stringify(id)}`);
    off += 8 + size + (size & 1);
  }
  if (!fmt || !data) fail(what, 'missing fmt or data chunk');
  if (fmt.size !== 16) fail(what, `fmt size ${fmt.size}`);
  const f = fmt.off;
  const format = buf.readUInt16LE(f), channels = buf.readUInt16LE(f + 2), rate = buf.readUInt32LE(f + 4);
  const byteRate = buf.readUInt32LE(f + 8), align = buf.readUInt16LE(f + 12), bits = buf.readUInt16LE(f + 14);
  if (format !== 1) fail(what, `format ${format}, want 1 (PCM)`);
  if (channels !== 1) fail(what, `${channels} channels, want 1`);
  if (bits !== 16 || align !== 2 || byteRate !== rate * 2) fail(what, `bits ${bits} align ${align} byteRate ${byteRate}`);
  if (data.size % 2) fail(what, 'odd data size');
  const samples = new Int16Array(data.size / 2);
  for (let i = 0; i < samples.length; i++) samples[i] = buf.readInt16LE(data.off + 2 * i);
  return { rate, channels, bits, samples };
}

function readAu(buf, what = 'au') {
  if (buf.length < 24) fail(what, 'shorter than an .au header');
  if (buf.readUInt32BE(0) !== 0x2e736e64) fail(what, 'bad magic');
  const offset = buf.readUInt32BE(4), size = buf.readUInt32BE(8), enc = buf.readUInt32BE(12);
  const rate = buf.readUInt32BE(16), channels = buf.readUInt32BE(20);
  if (offset < 24 || offset > buf.length) fail(what, `bad data offset ${offset}`);
  if (offset + size !== buf.length) fail(what, `offset + size (${offset + size}) != file size (${buf.length})`);
  if (enc !== 3) fail(what, `encoding ${enc}, want 3 (16-bit linear PCM)`);
  if (channels !== 1) fail(what, `${channels} channels, want 1`);
  if (size % 2) fail(what, 'odd data size');
  const samples = new Int16Array(size / 2);
  for (let i = 0; i < samples.length; i++) samples[i] = buf.readInt16BE(offset + 2 * i);
  return { rate, channels, bits: 16, samples };
}

module.exports = { wavBytes, auBytes, readWav, readAu, AU_HEADER };
