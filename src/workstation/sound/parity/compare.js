'use strict';
// Compares the node engine with the approved C# engine's reference renders (made by render-ref.ps1).
//   engine parity: the node engine's output (no declick) against the C# output, sample by sample, for the 16 v3
//                  sounds (at the engine's own Level) and every v3-primitive data recipe (at its tier). LOOK-SOUND
//                  T-S1 accepts 1 LSB; the result is reported as measured.
//   shipped:       the shipped 48 kHz files against the C# 48 kHz output: identical before the declick region;
//                  inside it, never further from the engine than the fade itself moves it.

const fs = require('fs');
const path = require('path');
const { readWav } = require('../src/codecs');
const lib = require('../src/render-lib');
const { parityKind } = require('../src/catalogue');
const { V3_NAMES, v3Level } = require('../src/v3');

const REF = path.join(__dirname, '..', 'out', 'parity-ref');

function refPath(rate, name) { return path.join(REF, String(rate), `${name}.wav`); }
function haveRefs(rate) { return fs.existsSync(path.join(REF, String(rate))); }

function diff(a, b) {
  if (a.length !== b.length) return { lengthMismatch: [a.length, b.length], max: Infinity, count: Infinity };
  let max = 0, count = 0;
  for (let i = 0; i < a.length; i++) { const d = Math.abs(a[i] - b[i]); if (d) { count++; if (d > max) max = d; } }
  return { max, count };
}

function engineParity(rate) {
  const rows = [];
  for (const name of V3_NAMES) {
    const ref = readWav(fs.readFileSync(refPath(rate, name)), name);
    const entry = lib.CATALOGUE.find((c) => c.name === name);
    const mine = lib.renderEngineSamples(entry, rate, v3Level(name));
    rows.push({ name, kind: 'v3', level: v3Level(name), refRate: ref.rate, ...diff(mine, ref.samples) });
  }
  for (const entry of lib.CATALOGUE.filter((c) => parityKind(c) === 'prim')) {
    const ref = readWav(fs.readFileSync(refPath(rate, entry.name)), entry.name);
    const mine = lib.renderEngineSamples(entry, rate);
    rows.push({ name: entry.name, kind: 'prim', level: entry.tier, refRate: ref.rate, ...diff(mine, ref.samples) });
  }
  return rows;
}

// shipped files (out/wav) against the C# 48 kHz renders
function shippedVsReference(outDir = path.join(__dirname, '..', 'out')) {
  const rate = 48000;
  const rows = [];
  for (const entry of lib.CATALOGUE) {
    const kind = parityKind(entry);
    if (kind === 'none') continue;
    if (kind === 'v3' && v3Level(entry.name) !== entry.tier) continue; // re-tiered (window): compared through the engine at its tier
    const shipped = readWav(fs.readFileSync(path.join(outDir, 'wav', `${entry.name}.wav`)), entry.name).samples;
    const ref = readWav(fs.readFileSync(refPath(rate, entry.name)), entry.name).samples;
    if (shipped.length !== ref.length) { rows.push({ name: entry.name, lengthMismatch: [shipped.length, ref.length] }); continue; }
    const fadeFrom = lib.declicked(entry) ? shipped.length - Math.trunc(lib.DECLICK_S * rate) : shipped.length;
    const before = diff(shipped.subarray(0, fadeFrom), ref.subarray(0, fadeFrom));
    let fadeMax = 0, fadeBound = 0;
    for (let i = fadeFrom; i < shipped.length; i++) {
      fadeMax = Math.max(fadeMax, Math.abs(shipped[i] - ref[i]));
      fadeBound = Math.max(fadeBound, Math.abs(ref[i]) + 1); // the fade can only move a sample toward zero
    }
    rows.push({ name: entry.name, kind, beforeFade: before, fadeRegionMaxChange: fadeMax, fadeRegionBound: fadeBound });
  }
  return rows;
}

module.exports = { engineParity, shippedVsReference, haveRefs, REF };
