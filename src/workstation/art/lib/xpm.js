'use strict';
// XPM3 (CDE's icon and backdrop format) and XBM (CDE's 1-bit .bm and _m.bm) writers, plus a parser for the tests.
// Layout follows cde-2.5.3/programs/icons/*.pm: a header comment, "static char * name[] = {", the values line,
// colour lines "<chars> s <symbol> m <mono> c <colour>", then the pixel rows.
const { FIXED, DYNAMIC_DEFAULT, MONO, brightness } = require('./cde');

// libXpm's own character order (minus '"' and '\\').
const CHARS = " .XoO+@#$%&*=-;:>,<1234567890qwertyuipasdfghjklzxcvbnmMNBVCZASDFGHJKLPIUYTREWQ!~^/()_`'][{}|";

function hex16(hex) {
  const s = hex.replace('#', '').toLowerCase();
  return '#' + [0, 2, 4].map(i => s.slice(i, i + 2).repeat(2)).join('');
}
function monoOf(tok) {
  if (tok in MONO) return MONO[tok];
  return brightness(tok) > 127 ? 'white' : 'black';
}
function cIdent(s) { return s.replace(/[^A-Za-z0-9_]/g, '_'); }

// canvas: lib/canvas Canvas. opts: { name (C identifier base), comment (one line), forceCpp }
function toXPM(canvas, opts = {}) {
  const name = cIdent(opts.name || 'image');
  // order: transparent first if present, then tokens in raster order of first use
  const order = []; const seen = new Set(); let hasNone = false;
  for (let i = 0; i < canvas.px.length; i++) {
    const v = canvas.px[i];
    if (v < 0) { hasNone = true; continue; }
    const t = canvas.toks[v];
    if (!seen.has(t)) { seen.add(t); order.push(t); }
  }
  const n = order.length + (hasNone ? 1 : 0);
  const cpp = opts.forceCpp || (n <= CHARS.length ? 1 : 2);
  const code = k => cpp === 1 ? CHARS[k] : CHARS[Math.floor(k / CHARS.length)] + CHARS[k % CHARS.length];
  const map = new Map(); let k = 0;
  const lines = [];
  if (hasNone) { map.set(null, code(k++)); lines.push(`"${map.get(null)}\ts none\tm none\tc none",`); }
  for (const t of order) {
    const c = code(k++); map.set(t, c);
    if (t[0] === '#') lines.push(`"${c}\tm ${monoOf(t)}\tc ${hex16(t)}",`);
    else {
      const cv = FIXED[t] ? hex16(FIXED[t]) : hex16(DYNAMIC_DEFAULT[t]);
      lines.push(`"${c}\ts ${t}\tm ${MONO[t]}\tc ${cv}",`);
    }
  }
  const rows = [];
  for (let y = 0; y < canvas.h; y++) {
    let r = '';
    for (let x = 0; x < canvas.w; x++) { const v = canvas.px[y * canvas.w + x]; r += map.get(v < 0 ? null : canvas.toks[v]); }
    rows.push(`"${r}"${y === canvas.h - 1 ? '' : ','}`);
  }
  return [
    '/* XPM */',
    `/* ${opts.file || name}: ${opts.comment || 'Deep32/UX art'} */`,
    '/* Original art for Deep32/UX, drawn as code by art/tools/build.js. Copyright (C) 2026 Jeffrey A. Cummings (https://github.com/hellobpdu). Licence: CC BY-NC 4.0. */',
    `static char * ${name}[] = {`,
    '/* width height ncolors cpp [x_hot y_hot] */',
    `"${canvas.w} ${canvas.h} ${n} ${cpp} 0 0",`,
    '/* colors */',
    ...lines,
    '/* pixels */',
    ...rows,
    '};',
    '',
  ].join('\n');
}

// XBM from a predicate bit(x, y) -> 0/1.
function toXBM(w, h, bit, name, comment) {
  const id = cIdent(name);
  const bytes = [];
  for (let y = 0; y < h; y++) for (let bx = 0; bx < w; bx += 8) {
    let b = 0;
    for (let i = 0; i < 8; i++) if (bx + i < w && bit(bx + i, y)) b |= 1 << i;
    bytes.push('0x' + b.toString(16).padStart(2, '0'));
  }
  const out = [`/* ${comment || name} */`, `#define ${id}_width ${w}`, `#define ${id}_height ${h}`, `static unsigned char ${id}_bits[] = {`];
  for (let i = 0; i < bytes.length; i += 12) out.push('   ' + bytes.slice(i, i + 12).join(', ') + (i + 12 < bytes.length ? ',' : '};'));
  out.push('');
  return out.join('\n');
}
// CDE .bm (1 = drawn in the foreground: every pixel whose mono key is black) and _m.bm (1 = opaque).
function monoBits(canvas) {
  return (x, y) => { const t = canvas.get(x, y); return t !== null && monoOf(t) === 'black' ? 1 : 0; };
}
function maskBits(canvas) { return (x, y) => (canvas.get(x, y) !== null ? 1 : 0); }

// ---- parsers (tests) ----
function parseXPM(text) {
  const strs = [];
  const body = text.replace(/\/\*[\s\S]*?\*\//g, '');
  const re = /"((?:[^"\\]|\\.)*)"/g; let m;
  while ((m = re.exec(body))) strs.push(m[1]);
  if (!/static\s+(const\s+)?char\s*\*\s*\*?\s*[A-Za-z_][A-Za-z0-9_]*\s*\[\]\s*=\s*\{/.test(body)) throw new Error('xpm: no C array header');
  const [w, h, nc, cpp] = strs[0].trim().split(/\s+/).map(Number);
  const colors = {};
  for (let i = 1; i <= nc; i++) {
    const s = strs[i]; const key = s.slice(0, cpp); const rest = s.slice(cpp).trim().split(/\s+/);
    const c = {};
    for (let j = 0; j + 1 < rest.length; j += 2) c[rest[j]] = rest[j + 1];
    if (colors[key]) throw new Error(`xpm: duplicate colour key '${key}'`);
    colors[key] = c;
  }
  const rows = strs.slice(1 + nc, 1 + nc + h);
  if (rows.length !== h) throw new Error(`xpm: ${rows.length} rows, header says ${h}`);
  rows.forEach((r, y) => {
    if (r.length !== w * cpp) throw new Error(`xpm: row ${y} has ${r.length / cpp} px, header says ${w}`);
    for (let x = 0; x < w; x++) if (!colors[r.slice(x * cpp, x * cpp + cpp)]) throw new Error(`xpm: row ${y} uses an undefined key`);
  });
  if (strs.length !== 1 + nc + h) throw new Error('xpm: trailing strings');
  return { w, h, nc, cpp, colors, rows, pixel: (x, y) => colors[rows[y].slice(x * cpp, x * cpp + cpp)] };
}
function parseXBM(text) {
  const w = +(/#define\s+\S+_width\s+(\d+)/.exec(text) || [])[1];
  const h = +(/#define\s+\S+_height\s+(\d+)/.exec(text) || [])[1];
  const bytes = (text.split('{')[1] || '').match(/0x[0-9a-fA-F]{2}/g).map(b => parseInt(b, 16));
  const bpr = Math.ceil(w / 8);
  if (bytes.length !== bpr * h) throw new Error(`xbm: ${bytes.length} bytes for ${w}x${h}`);
  return { w, h, bit: (x, y) => (bytes[y * bpr + (x >> 3)] >> (x & 7)) & 1 };
}

module.exports = { toXPM, toXBM, monoBits, maskBits, parseXPM, parseXBM, hex16, monoOf, cIdent, CHARS };
