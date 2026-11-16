'use strict';
// Shared plumbing for the generator: an output tree with a manifest (install path, mode, owner, SHA-256, licence,
// how it installs), train gating, and palette loading.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const colour = require('../lib/colour');

const ROOT = path.resolve(__dirname, '..', '..');
const CDE_SRC = process.env.D32_CDE_SRC || '';   // the extracted CDE 2.5.3 sources: set D32_CDE_SRC (no default)

class Tree {
  constructor(dir, opts) {
    this.dir = dir; this.files = []; this.charset = opts.charset || 'utf8';
    fs.rmSync(dir, { recursive: true, force: true });
    fs.mkdirSync(dir, { recursive: true });
  }
  // rel: path inside the tree (also the install path, rooted at /). meta: { mode, owner, install, licence, note, text }
  write(rel, content, meta = {}) {
    const abs = path.join(this.dir, rel);
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    let buf;
    if (Buffer.isBuffer(content)) buf = content;
    else {
      if (/\r/.test(content)) throw new Error(rel + ': carriage return in generated text');
      const enc = meta.charset || (meta.ascii ? 'ascii' : this.charset);
      if (enc === 'ascii' && /[^\x00-\x7f]/.test(content)) throw new Error(rel + ': non-ASCII text in an ASCII file');
      buf = enc === 'latin1' ? Buffer.from(content, 'latin1') : Buffer.from(content, 'utf8');
      meta = Object.assign({ encoding: /[^\x00-\x7f]/.test(content) ? enc : 'ascii' }, meta);
    }
    fs.writeFileSync(abs, buf);
    this.files.push(Object.assign({ rel: rel.replace(/\\/g, '/'), sha256: crypto.createHash('sha256').update(buf).digest('hex'),
      size: buf.length, mode: '0444', owner: 'root:wheel', install: 'file', licence: 'AGPL-3.0-or-later (Deep32)', encoding: 'binary' }, meta));
    return abs;
  }
  manifest(extraHeader) {
    const cols = ['rel', 'mode', 'owner', 'install', 'encoding', 'size', 'sha256', 'licence', 'note'];
    const lines = ['# ' + (extraHeader || 'Deep32/UX theme output'), '# install: file = copy to /<path>; sample = @sample (kept if edited); master = package copy under /usr/local',
      '# path (relative to /)\tmode\towner\tinstall\tencoding\tbytes\tsha256\tlicence\tnote'];
    for (const f of this.files.sort((a, b) => a.rel.localeCompare(b.rel))) lines.push(cols.map(c => String(f[c] === undefined ? '' : f[c])).join('\t'));
    fs.writeFileSync(path.join(this.dir, 'MANIFEST.tsv'), lines.join('\n') + '\n');
  }
}

// Train gating: 'all' emits everything; otherwise only items whose train is at or before the release.
function due(train, release) {
  if (release === 'all' || !train) return true;
  // 'D.32.12' -> 32012 (the validator's T-DT-4 caught the old form, which read the leading D as NaN and so emitted
  // nothing with a train in a numbered release)
  const n = s => s.replace(/^D\./, '').split('.').map(Number).reduce((a, v) => a * 1000 + v, 0);
  return n(train) <= n(release);
}

// The form of a record at a release. A record may carry `early: { until: TRAIN, ...fields }`: before TRAIN the early
// fields replace the record's own (a null removes the field), from TRAIN on, and in 'all' (the full product), the record
// is used as written. Used for a front-panel cell that serves First light and becomes another program later, keeping
// its control and subpanel names so the user's own subpanel additions (fp_dynamic) survive the update.
function form(item, release) {
  if (!item.early || release === 'all' || due(item.early.until, release)) return item;
  const out = Object.assign({}, item, item.early);
  delete out.early; delete out.until;
  for (const k of Object.keys(out)) if (out[k] === null) delete out[k];
  return out;
}

// Palettes: the 41 stock palettes from the golden JSON (16-bit sources), plus Deep32 originals (8-bit, written as
// #RR00GG00BB00 like CDE's own 8-bit palette lines).
// extra (private build): palettes added from genuine vendor files (VUE, AIX), { name: [8 source lines] }. They are
// installed by the originals copy, never rewritten here (original: true), and the toolkit bridge themes them too.
function loadPalettes(data, extra) {
  const golden = JSON.parse(fs.readFileSync(path.join(ROOT, 'src', 'palettes', 'cde-palettes.json'), 'utf8'));
  const out = {};
  for (const [name, sets] of Object.entries(golden)) {
    const lines = sets.map(s => s.source);
    out[name] = { name, stock: true, lines, bw: data.palettes.bwOnly.includes(name),
      bgs16: lines.map(l => colour.parseX11Colour(l)) };
  }
  for (const [name, hex] of Object.entries(data.palettes.originals)) {
    const bgs16 = hex.map(colour.from8);
    out[name] = { name, stock: false, lines: bgs16.map(colour.x11hex16), bw: false, bgs16 };
  }
  for (const [name, lines] of Object.entries(extra || {})) {
    if (out[name]) throw new Error(`palette ${name} from the originals would shadow an existing palette`);
    const bgs16 = lines.map(l => colour.parseX11Colour(l));
    if (bgs16.length !== 8 || bgs16.some(c => !c)) throw new Error(`palette ${name}: expected 8 colours`);
    // a palette whose eight sets are black and white only is a B_W palette (CDE-SPEC 1.1)
    const bw = bgs16.every(c => c.every(v => v === 0 || v === colour.MAX));
    out[name] = { name, stock: false, original: true, lines, bw, bgs16 };
  }
  return out;
}
const sets = (pal, use) => colour.paletteSets(pal.bgs16, use);

module.exports = { ROOT, CDE_SRC, Tree, due, form, loadPalettes, sets };
