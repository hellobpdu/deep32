'use strict';
// Resolves src/originals.js against the owner's private extraction (update item 43) for the PRIVATE build only:
// returns every file to install, with its bytes, source path, SHA-256 of the source, install path (system and user)
// and licence, plus the palettes it adds (so the toolkit bridge can theme them). Never called by the public build.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const colour = require('../lib/colour');
const audio = require('../lib/audio');
const O = require('../../src/originals');

const sha = b => crypto.createHash('sha256').update(b).digest('hex');
function resolveDir(spec, roots) {
  const m = /^([A-Z]+):(.*)$/.exec(spec);
  if (!m || !roots[m[1]]) throw new Error('originals: bad root in ' + spec);
  return path.join(roots[m[1]], m[2]);
}
const parseDp = buf => buf.toString('latin1').split(/\r?\n/).map(s => s.trim()).filter(s => s && !s.startsWith('!'));
// the same picture: XBM by size and bits, XPM with comments, whitespace and the array name ignored
function sameImage(a, b, ext) {
  const A = a.toString('latin1'), B = b.toString('latin1');
  if (ext === '.bm') {
    const k = s => { const w = /_width\s+(\d+)/.exec(s), h = /_height\s+(\d+)/.exec(s); return w && h ? `${w[1]}x${h[1]}:` + (s.slice(s.indexOf('{')).match(/0x[0-9a-fA-F]+/g) || []).map(x => parseInt(x, 16)).join(',') : s; };
    return k(A) === k(B);
  }
  const k = s => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\s+/g, '').replace(/staticchar\*+[A-Za-z0-9_]*(\[\])?=/, '');
  return k(A) === k(B);
}

// names: the CDE palettes and backdrops a VUE file must not shadow, with their bytes or values to compare.
function collect(opts = {}) {
  const roots = Object.assign({}, O.roots, opts.roots || {});
  const golden = opts.golden || {};
  const files = []; const missing = []; const skipped = []; const addedPalettes = {};
  // CDE backdrops by base name (HP 11.00's set, which equals the package's), and our own
  const hpBd = resolveDir('HP:hpux1100/desktop/usr/dt/share/backdrops', roots);
  const cdeBackdrops = new Map();
  if (fs.existsSync(hpBd)) for (const f of fs.readdirSync(hpBd)) if (/\.(pm|bm)$/.test(f)) cdeBackdrops.set(f.replace(/\.(pm|bm)$/, ''), fs.readFileSync(path.join(hpBd, f)));
  for (const n of ['Horizon', 'Abyss1440', 'SunLogo', 'CobbleRound', 'DarkPaper', 'Distortion', 'Fleecy', 'LatticeLite', 'Leaf', 'LeafBright']) if (!cdeBackdrops.has(n)) cdeBackdrops.set(n, null);
  for (const set of O.sets) {
    const dir = resolveDir(set.from, roots);
    if (!fs.existsSync(dir)) { missing.push(`${set.id}: ${dir}`); continue; }
    const names = fs.readdirSync(dir).filter(f => set.match.test(f) && fs.statSync(path.join(dir, f)).isFile()).sort();
    if (!names.length) { missing.push(`${set.id}: no file matching ${set.match} in ${dir}`); continue; }
    for (const f of names) {
      if (set.skip && set.skip.test(f)) continue;
      const src = path.join(dir, f);
      const buf = fs.readFileSync(src);
      // an empty file draws nothing (the HP-UX 11.00 extraction has the six DtdirBO files empty): left out
      if (!buf.length) { skipped.push(`${set.id}: ${f} is empty`); continue; }
      let name = set.rename ? set.rename(f) : f;
      if (set.skipIfColour && fs.existsSync(path.join(dir, 'Color', f.replace(/\.bm$/, '.pm')))) continue;
      if (set.vueNames === 'palettes') {
        const base = name.replace(/\.dp$/, '');
        if (golden[base]) {
          const same = parseDp(buf).map(colour.parseX11Colour).every((c, i) => golden[base][i] && c.every((v, k) => v === colour.parseX11Colour(golden[base][i].source)[k]));
          if (same) continue;                                    // CDE's own copy has the same values
          name = base + O.VUE_RENAME_SUFFIX + '.dp';
        }
      }
      if (set.vueNames === 'backdrops') {
        const base = name.replace(/\.(pm|bm)$/, ''), ext = name.slice(base.length);
        if (cdeBackdrops.has(base)) {
          const other = cdeBackdrops.get(base);
          if (other && sameImage(other, buf, ext)) continue;    // CDE's copy draws the same picture
          name = base + O.VUE_RENAME_SUFFIX + ext;
        }
      }
      const rec = { set: set.id, src, srcSha256: sha(buf), name, licence: set.licence, why: set.why };
      if (set.convert === 'wav48k') {
        files.push(Object.assign({}, rec, { sys: `${set.to}/${name}`, user: `${set.user}/${name}`, buf }));
        const w = audio.auToWav48k(buf);
        const wn = name.replace(/\.au$/, '.wav');
        files.push(Object.assign({}, rec, { name: wn, sys: `${set.to}/${wn}`, user: `${set.user}/${wn}`, buf: w.wav, derived: `48 kHz 16-bit mono WAV from ${name} (${w.from.rate} Hz, ${w.from.channels} ch)` }));
        continue;
      }
      files.push(Object.assign({}, rec, { sys: `${set.to}/${name}`, user: `${set.user}/${name}`, buf }));
      if (/\.dp$/.test(name) && set.id !== 'hp-palettes') addedPalettes[name.replace(/\.dp$/, '')] = parseDp(buf);
    }
  }
  return { files, missing, skipped, addedPalettes, roots };
}

module.exports = { collect, resolveDir };
