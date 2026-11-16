#!/usr/bin/env node
'use strict';
// The parity run (LOOK-SOUND 8.7 T-S1, DISTRO D42): renders reference WAVs with the approved C# engine under
// Windows PowerShell 5.1 at 44.1 and 48 kHz, then compares the node engine with them sample by sample, and the shipped
// files with the 48 kHz references. Writes out/parity-ref/<rate>/*.wav and out/parity-report.json.
//
//   node parity/run.js            (needs Windows PowerShell 5.1; run `node render.js` first)

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const lib = require('../src/render-lib');
const { engineParity, shippedVsReference, REF } = require('./compare');

fs.mkdirSync(REF, { recursive: true });
const recipes = path.join(REF, 'recipes.json');
fs.writeFileSync(recipes, JSON.stringify(lib.parityRecipes(), null, 1) + '\n');

for (const rate of [44100, 48000]) {
  const out = execFileSync('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
    path.join(__dirname, 'render-ref.ps1'), '-Rate', String(rate), '-OutDir', path.join(REF, String(rate)), '-Recipes', recipes],
  { encoding: 'utf8' });
  process.stdout.write(out);
}

const report = { engine: {}, shipped: null, verdict: 'PASS' };
for (const rate of [44100, 48000]) {
  const rows = engineParity(rate);
  const worst = Math.max(...rows.map((r) => r.max));
  report.engine[rate] = { sounds: rows.length, worstLsb: worst, differingSamples: rows.reduce((a, r) => a + r.count, 0), rows };
  if (!(worst <= 1)) report.verdict = 'FAIL';
  console.log(`engine parity at ${rate} Hz: ${rows.length} sounds, worst difference ${worst} LSB, ${report.engine[rate].differingSamples} differing samples`);
}
const shipped = shippedVsReference();
report.shipped = shipped;
const badShip = shipped.filter((r) => r.lengthMismatch || r.beforeFade.max !== 0 || r.fadeRegionMaxChange > r.fadeRegionBound);
if (badShip.length) report.verdict = 'FAIL';
console.log(`shipped vs C# 48 kHz: ${shipped.length} files identical before the 10 ms declick region: ${badShip.length ? 'NO: ' + badShip.map((r) => r.name).join(', ') : 'yes'}; largest change inside it ${Math.max(...shipped.map((r) => r.fadeRegionMaxChange))} LSB`);
fs.writeFileSync(path.join(__dirname, '..', 'out', 'parity-report.json'), JSON.stringify(report, null, 2) + '\n');
console.log(`parity: ${report.verdict}`);
process.exit(report.verdict === 'PASS' ? 0 : 1);
