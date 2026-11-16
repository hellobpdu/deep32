'use strict';
// Rendering shared by render.js, the parity runner and the tests.

const crypto = require('crypto');
const { Engine } = require('./engine');
const { V3, v3Level } = require('./v3');
const { CATALOGUE, BUILDERS, parityKind } = require('./catalogue');

const SEED = 1994;

const DECLICK_S = 0.010;

// The engine's float buffer for one catalogue entry, from a fresh generator (every sound starts from
// new Random(1994), so no sound depends on which were made before it). This is what the parity test compares.
function renderEngineFloat(entry, rate) {
  const e = new Engine(rate, SEED);
  if (entry.v3) return V3[entry.name](e);
  if (entry.build) return BUILDERS[entry.build](e, entry);
  const b = e.buf(entry.buffer);
  e.runOps(b, entry.ops);
  return b;
}

const declicked = (entry) => !entry.loop && !entry.noDeclick;

// What ships: the engine's buffer plus the declick layer on one-shots (Engine.declick). The layer must never reach
// the peak, so the gain, and every sample before the last 10 ms, stay the engine's own.
function renderFloat(entry, rate) {
  const b = renderEngineFloat(entry, rate);
  if (!declicked(entry)) return b;
  let peakAt = 0, peak = 0;
  for (let i = 0; i < b.length; i++) { const a = Math.abs(b[i]); if (a > peak) { peak = a; peakAt = i; } }
  const start = b.length - Math.trunc(DECLICK_S * rate);
  if (peakAt >= start) throw new Error(`${entry.name}: the peak lies in the declick region`);
  Engine.declick(b, rate, DECLICK_S);
  return b;
}

function renderSamples(entry, rate, level = entry.tier) {
  return Engine.quantise(renderFloat(entry, rate), level);
}

function renderEngineSamples(entry, rate, level = entry.tier) {
  return Engine.quantise(renderEngineFloat(entry, rate), level);
}

// the v3 engine's own output for a v3 sound (at its v3 Level), for the parity test
function renderV3AtEngineLevel(name, rate) {
  const e = new Engine(rate, SEED);
  return Engine.quantise(V3[name](e), v3Level(name));
}

function stats(samples) {
  let peak = 0, sumSq = 0, sum = 0;
  for (let i = 0; i < samples.length; i++) {
    const v = samples[i]; const a = Math.abs(v);
    if (a > peak) peak = a;
    sumSq += v * v; sum += v;
  }
  const rms = Math.sqrt(sumSq / samples.length) / 32767;
  return {
    peakSample: peak,
    peak: peak / 32767,
    peakDbfs: peak > 0 ? 20 * Math.log10(peak / 32767) : -Infinity,
    rmsDbfs: rms > 0 ? 20 * Math.log10(rms) : -Infinity,
    dc: sum / samples.length / 32767,
  };
}

const sha256 = (buf) => crypto.createHash('sha256').update(buf).digest('hex');
const dbfs = (x) => 20 * Math.log10(x);
const round = (x, n) => Math.round(x * 10 ** n) / 10 ** n;

// the parity list for parity/render-ref.ps1: every data recipe made only of v3 primitives, at its shipped tier
function parityRecipes() {
  return CATALOGUE.filter((c) => parityKind(c) === 'prim').map((c) => ({ name: c.name, buffer: c.buffer, level: c.tier, ops: c.ops }));
}

module.exports = {
  renderFloat, renderSamples, renderEngineFloat, renderEngineSamples, renderV3AtEngineLevel, declicked, DECLICK_S,
  stats, sha256, dbfs, round, parityRecipes, CATALOGUE, SEED,
};
