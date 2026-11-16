'use strict';
// Sun/NeXT .au reader and a 16-bit mono WAV writer with a windowed-sinc resampler, so a genuine .au plays through
// d32sndd at sndiod's native 48 kHz (LOOK-SOUND 8.6-8.7: 48 kHz, 16-bit signed, mono). Deterministic: no clocks,
// no randomness; the same input gives the same bytes.

// G.711 mu-law to linear (the ITU reference expansion), as 16-bit signed.
function ulaw(u) {
  u = ~u & 0xff;
  const sign = u & 0x80, exp = (u >> 4) & 7, man = u & 0x0f;
  let s = ((man << 3) + 0x84) << exp;
  s -= 0x84;
  return sign ? -s : s;
}

// .au: magic ".snd", data offset, size, encoding (1 mu-law, 2 linear 8, 3 linear 16 big-endian), rate, channels.
function readAu(buf) {
  if (buf.length < 24 || buf.readUInt32BE(0) !== 0x2e736e64) throw new Error('not a .au file');
  const off = buf.readUInt32BE(4), enc = buf.readUInt32BE(12), rate = buf.readUInt32BE(16), ch = buf.readUInt32BE(20);
  const data = buf.subarray(off);
  let pcm;
  if (enc === 1) pcm = Array.from(data, ulaw);
  else if (enc === 2) pcm = Array.from(data, b => ((b << 24) >> 24) * 256);
  else if (enc === 3) { pcm = []; for (let i = 0; i + 1 < data.length; i += 2) pcm.push(data.readInt16BE(i)); }
  else throw new Error('unsupported .au encoding ' + enc);
  const n = Math.floor(pcm.length / ch);
  const mono = new Float64Array(n);
  for (let i = 0; i < n; i++) { let s = 0; for (let c = 0; c < ch; c++) s += pcm[i * ch + c]; mono[i] = s / ch / 32768; }
  return { rate, channels: ch, encoding: enc, samples: mono };
}

// Windowed-sinc (Blackman, 32 taps a side) resampling; the cutoff follows the lower of the two Nyquist rates.
function resample(x, from, to) {
  if (from === to) return Float64Array.from(x);
  const r = to / from, fc = Math.min(1, r), W = 32;
  const n = Math.floor(x.length * r);
  const y = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const t = i / r, k0 = Math.floor(t);
    let s = 0, wsum = 0;
    for (let k = k0 - W + 1; k <= k0 + W; k++) {
      if (k < 0 || k >= x.length) continue;
      const d = t - k, a = Math.PI * d * fc;
      const sinc = d === 0 ? 1 : Math.sin(a) / a;
      const p = (d + W) / (2 * W);
      const win = p <= 0 || p >= 1 ? 0 : 0.42 - 0.5 * Math.cos(2 * Math.PI * p) + 0.08 * Math.cos(4 * Math.PI * p);
      const h = sinc * win * fc;
      s += x[k] * h; wsum += h;
    }
    y[i] = s;
  }
  return y;
}

function wav16(samples, rate) {
  const n = samples.length, buf = Buffer.alloc(44 + n * 2);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVE', 8); buf.write('fmt ', 12);
  buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22); buf.writeUInt32LE(rate, 24);
  buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34); buf.write('data', 36); buf.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) buf.writeInt16LE(Math.max(-32768, Math.min(32767, Math.round(samples[i] * 32768))), 44 + i * 2);
  return buf;
}

// .au bytes -> 48 kHz 16-bit mono WAV bytes (the level is kept: no normalisation, the recording as HP shipped it).
function auToWav48k(buf) {
  const a = readAu(buf);
  return { wav: wav16(resample(a.samples, a.rate, 48000), 48000), from: a };
}

module.exports = { readAu, resample, wav16, auToWav48k, ulaw };
