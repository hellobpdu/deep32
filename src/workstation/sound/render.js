#!/usr/bin/env node
'use strict';
// Renders every Deep32 Workstation sound to 16-bit mono PCM WAV and Sun .au at 48 kHz, with a manifest and checksums.
//
//   node render.js [--out DIR] [--rate 48000]
//
// Output (DIR defaults to ./out):
//   wav/<name>.wav  au/<name>.au           the sounds (identical samples; .au is big-endian, encoding 3)
//   manifest.json                          per sound: frames, duration, tier, peak and RMS levels, SHA-256 of both
//                                          files, recipe, design source, trigger, profiles, rate limit, licence
//   manifest.tsv                           the same essentials, tab-separated, for ksh/awk on the VM (send_sound)
//   SHA256                                 OpenBSD format: `SHA256 (wav/rom.wav) = <hex>`  (sha256 -c SHA256)
//   SHA256SUMS                             GNU format:     `<hex>  wav/rom.wav`             (sha256sum -c)
//   vectors/                               X bell test vectors for d32sndd's live bell (not shipped)

const fs = require('fs');
const path = require('path');
const { wavBytes, auBytes } = require('./src/codecs');
const { xbellRender, parityKind } = require('./src/catalogue');
const { Engine } = require('./src/engine');
const lib = require('./src/render-lib');

function args() {
  const a = process.argv.slice(2), o = { out: path.join(__dirname, 'out'), rate: 48000 };
  for (let i = 0; i < a.length; i++) {
    if (a[i] === '--out') o.out = path.resolve(a[++i]);
    else if (a[i] === '--rate') o.rate = Number(a[++i]);
    else if (a[i] === '--help' || a[i] === '-h') { console.log(fs.readFileSync(__filename, 'utf8').split('\n').slice(2, 17).join('\n')); process.exit(0); }
    else throw new Error(`unknown argument ${a[i]}`);
  }
  if (![44100, 48000].includes(o.rate)) throw new Error('--rate must be 48000 (shipped) or 44100 (parity)');
  return o;
}

// X bell vectors: the default render, the T-S4 test tone, Style Manager's extremes
const XBELL_VECTORS = [
  [400, 100, 50], [1000, 250, 100], [82, 2500, 100], [9000, 100, 100], [440, 500, 0], [50, 100, 100], [12000, 100, 100],
];

function main() {
  const o = args();
  const t0 = Date.now();
  for (const d of ['wav', 'au', 'vectors']) fs.mkdirSync(path.join(o.out, d), { recursive: true });
  const engineRef = path.join(__dirname, 'reference', 'sound-cs.txt');

  const sounds = [];
  const names = new Set();
  for (const entry of lib.CATALOGUE) {
    if (names.has(entry.name)) throw new Error(`duplicate sound ${entry.name}`);
    names.add(entry.name);
    const samples = lib.renderSamples(entry, o.rate);
    const wav = wavBytes(samples, o.rate), au = auBytes(samples, o.rate);
    const rel = { wav: `wav/${entry.name}.wav`, au: `au/${entry.name}.au` };
    fs.writeFileSync(path.join(o.out, rel.wav), wav);
    fs.writeFileSync(path.join(o.out, rel.au), au);
    const s = lib.stats(samples);
    sounds.push({
      name: entry.name,
      group: entry.group,
      files: rel,
      bytes: { wav: wav.length, au: au.length },
      sha256: { wav: lib.sha256(wav), au: lib.sha256(au) },
      rate: o.rate, channels: 1, bits: 16,
      frames: samples.length,
      durationS: lib.round(samples.length / o.rate, 4),
      loop: !!entry.loop,
      tier: entry.tier,
      tierDbfs: lib.round(lib.dbfs(entry.tier), 2),
      capSample: Math.round(entry.tier * 32767),
      peakSample: s.peakSample,
      peak: lib.round(s.peak, 5),
      peakDbfs: lib.round(s.peakDbfs, 2),
      rmsDbfs: lib.round(s.rmsDbfs, 2),
      recipe: entry.v3 ? `v3 engine: ${entry.name}` : entry.build ? `builder: ${entry.build}` : entry.ops,
      parity: parityKind(entry),
      declickMs: lib.declicked(entry) ? lib.DECLICK_S * 1000 : 0,
      source: entry.source,
      derived: entry.derived || null,
      notes: entry.notes || null,
      trigger: entry.trigger,
      profiles: entry.profiles,
      rateLimit: entry.rateLimit,
      minIntervalMs: entry.minIntervalMs ?? null,
      player: entry.player,
      licence: 'CC-BY-NC-4.0 (Deep32): original synthesis from code; no samples, no recorded or recreated vendor sounds',
    });
  }

  const vectors = [];
  for (const [p, ms, pct] of XBELL_VECTORS) {
    const { buffer, tier } = xbellRender(new Engine(o.rate), p, ms, pct);
    const samples = Engine.quantise(buffer, tier);
    const file = `vectors/xbell-${p}hz-${ms}ms-${pct}pc.wav`;
    const wav = wavBytes(samples, o.rate);
    fs.writeFileSync(path.join(o.out, file), wav);
    vectors.push({ file, pitchHz: p, durationMs: ms, percent: pct, clampedHz: Math.min(9000, Math.max(82, p)), tier, frames: samples.length, peakSample: lib.stats(samples).peakSample, sha256: lib.sha256(wav) });
  }

  const manifest = {
    title: 'Deep32 Workstation sounds',
    generator: 'deep32-ws/sound render.js (node port of the approved v3 engine, bit-exact against it in parity/)',
    engineReference: { file: 'reference/sound-cs.txt', sha256: lib.sha256(fs.readFileSync(engineRef)), approved: 'the owner 2026-11-03 (v3)' },
    rate: o.rate, channels: 1, bits: 16, seed: `every sound starts from .NET System.Random(${lib.SEED})`,
    formats: { wav: 'RIFF WAVE, PCM, little-endian, 44-byte header', au: 'Sun/NeXT .au, encoding 3 (16-bit linear PCM), big-endian, 32-byte header' },
    levels: 'tier = the peak each sound is normalised to (LOOK-SOUND 8.3); dBFS = 20 log10(tier); capSample = round(tier x 32767)',
    count: sounds.length,
    totalDurationS: lib.round(sounds.reduce((a, s) => a + s.frames, 0) / o.rate, 3),
    totalBytes: sounds.reduce((a, s) => a + s.bytes.wav + s.bytes.au, 0),
    sounds,
    xbellVectors: vectors,
  };
  fs.writeFileSync(path.join(o.out, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');

  const tsv = ['# name\twav\tau\tduration_ms\ttier\tpeak_dbfs\tprofiles\tmin_interval_ms\tloop\tgroup\tplayer'];
  for (const s of sounds) {
    tsv.push([s.name, s.files.wav, s.files.au, Math.round(s.durationS * 1000), s.tier, s.peakDbfs, s.profiles.join(','), s.minIntervalMs ?? 0, s.loop ? 1 : 0, s.group, s.player].join('\t'));
  }
  fs.writeFileSync(path.join(o.out, 'manifest.tsv'), tsv.join('\n') + '\n');

  const bsd = [], gnu = [];
  for (const s of sounds) {
    for (const k of ['wav', 'au']) { bsd.push(`SHA256 (${s.files[k]}) = ${s.sha256[k]}`); gnu.push(`${s.sha256[k]}  ${s.files[k]}`); }
  }
  bsd.push(`SHA256 (manifest.json) = ${lib.sha256(fs.readFileSync(path.join(o.out, 'manifest.json')))}`);
  bsd.push(`SHA256 (manifest.tsv) = ${lib.sha256(fs.readFileSync(path.join(o.out, 'manifest.tsv')))}`);
  fs.writeFileSync(path.join(o.out, 'SHA256'), bsd.join('\n') + '\n');
  fs.writeFileSync(path.join(o.out, 'SHA256SUMS'), gnu.join('\n') + '\n');

  console.log(`rendered ${sounds.length} sounds (${sounds.length * 2} files, ${(manifest.totalBytes / 1e6).toFixed(1)} MB, ${manifest.totalDurationS} s of audio) at ${o.rate} Hz into ${o.out} in ${((Date.now() - t0) / 1000).toFixed(1)} s`);
}

main();
