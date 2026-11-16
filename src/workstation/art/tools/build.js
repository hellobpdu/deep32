#!/usr/bin/env node
'use strict';
// Deep32/UX art lane: builds every piece of art from code.
//   node tools/build.js            -> writes out/ (XPM, XBM, PNG previews, MANIFEST.json, INVENTORY.md, contact sheets)
//   node tools/build.js --check    -> builds in memory and compares with out/MANIFEST.json (determinism check)
// Nothing is downloaded and nothing outside out/ is written.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { Canvas } = require('../lib/canvas');
const { toXPM, toXBM, monoBits, maskBits } = require('../lib/xpm');
const { encodeRGBA } = require('../lib/png');
const { colourSet, SYMBOLS } = require('../lib/cde');
const items = require('../src').items;
const sheets = require('./sheets');

const ROOT = path.join(__dirname, '..');
const OUT = path.join(ROOT, 'out');
const CHECK = process.argv.includes('--check');

const files = new Map(); // rel -> Buffer
function emit(rel, data) {
  if (files.has(rel)) throw new Error(`duplicate output ${rel}`);
  files.set(rel, Buffer.isBuffer(data) ? data : Buffer.from(data, 'utf8'));
}

const SIZE = { l: 48, m: 32, s: 24, t: 16 };
const built = [];
for (const it of items()) {
  const c = new Canvas(it.w, it.h);
  it.draw(c);
  // rules
  const cols = c.colours();
  if (!cols.length) throw new Error(`${it.file}: empty`);
  if (it.rule === 'cde') {
    const bad = cols.filter(t => !SYMBOLS.has(t) && !(it.allow || []).includes(t));
    if (bad.length) throw new Error(`${it.file}: non-CDE colours ${bad.join(' ')}`);
  }
  if (it.maxColours && cols.length > it.maxColours) throw new Error(`${it.file}: ${cols.length} colours > ${it.maxColours}`);
  if (it.sizeLetter && SIZE[it.sizeLetter] !== it.w) throw new Error(`${it.file}: .${it.sizeLetter} must be ${SIZE[it.sizeLetter]} px`);
  const base = `${it.group}/${it.file}`;
  const comment = `${it.title}${it.ref ? ' [' + it.ref + ']' : ''}`;
  const fmts = it.formats || ['pm'];
  if (fmts.includes('pm')) emit(`${base}.pm`, toXPM(c, { name: it.file.replace(/\./g, '_') + '_pm', file: `${it.file}.pm`, comment }));
  if (fmts.includes('bm')) emit(`${base}.bm`, toXBM(c.w, c.h, monoBits(c), it.file.replace(/\./g, '_'), `${it.file}.bm: ${comment}`));
  if (fmts.includes('mask')) emit(`${base}_m.bm`, toXBM(c.w, c.h, maskBits(c), it.file.replace(/\./g, '_') + '_m', `${it.file}_m.bm: mask`));
  if (fmts.includes('bm1')) emit(`${base}.bm`, toXBM(c.w, c.h, (x, y) => (c.get(x, y) === 'iconColor1' ? 1 : 0), it.file.replace(/\./g, '_'), `${it.file}.bm: ${comment}`));
  const set = it.preview === null ? null : colourSet(...(it.preview || ['Default', 8]));
  emit(`png/${base}.png`, encodeRGBA(c.w, c.h, c.toRGBA(set)));
  built.push({ it, c });
}

// contact sheets and the home-screen composite
for (const [rel, buf] of sheets.render(built)) emit(rel, buf);

// inventory first, so the manifest covers it
emit('INVENTORY.md', require('./inventory').render(built));
// manifest
const manifest = { schema: 'deep32-art-manifest/1', generator: 'deep32-ws/art tools/build.js', files: [] };
const byFile = new Map(built.map(b => [`${b.it.group}/${b.it.file}`, b.it]));
for (const [rel, buf] of [...files.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
  const key = rel.replace(/^png\//, '').replace(/(_m)?\.(pm|bm|png)$/, '');
  const it = byFile.get(key);
  manifest.files.push({
    file: rel, bytes: buf.length, sha256: crypto.createHash('sha256').update(buf).digest('hex'),
    title: it ? it.title : 'contact sheet / composite preview',
    design_ref: it ? it.ref || '' : '',
    licence: it && it.licence ? it.licence : 'CC-BY-NC-4.0 (Deep32): original art drawn as code for Deep32/UX (see README "Licence")',
  });
}
const mbuf = Buffer.from(JSON.stringify(manifest, null, 1) + '\n');

if (CHECK) {
  const old = JSON.parse(fs.readFileSync(path.join(OUT, 'MANIFEST.json'), 'utf8'));
  const oldMap = new Map(old.files.map(f => [f.file, f.sha256]));
  let diff = 0;
  for (const f of manifest.files) if (oldMap.get(f.file) !== f.sha256) { console.log(`DIFF ${f.file}`); diff++; }
  for (const f of old.files) if (!manifest.files.find(g => g.file === f.file)) { console.log(`GONE ${f.file}`); diff++; }
  console.log(diff ? `${diff} differences` : `identical: ${manifest.files.length} files`);
  process.exit(diff ? 1 : 0);
}

// write: clear out/ first so removed art does not linger
fs.rmSync(OUT, { recursive: true, force: true });
for (const [rel, buf] of files) {
  const p = path.join(OUT, rel);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, buf);
}
fs.writeFileSync(path.join(OUT, 'MANIFEST.json'), mbuf);
const n = built.length;
console.log(`built ${n} pieces of art, ${files.size + 1} files in out/`);
