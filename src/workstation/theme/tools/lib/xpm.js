'use strict';
// XPM3 reader and writer, enough of libXpm's grammar for CDE icons and backdrops:
//   /* XPM */ static char *name[] = { "w h ncolors cpp [xhot yhot]", colour lines, pixel rows };
// Colour lines carry keys s (symbolic), m (mono), g4, g, c; CDE resolves s first (CDE-SPEC 1.5).
// parse() is strict: it throws on anything libXpm would reject or that CDE could not resolve.
const fs = require('fs');

// The symbolic names CDE's colour server and DtSvc resolve (CDE-SPEC 1.5), plus "none" for transparency.
const CDE_SYMBOLS = new Set([
  'background', 'foreground', 'topShadowColor', 'bottomShadowColor', 'selectColor',
  'iconGray1', 'iconGray2', 'iconGray3', 'iconGray4', 'iconGray5', 'iconGray6', 'iconGray7', 'iconGray8',
  'iconColor1', 'iconColor2', 'iconColor3', 'iconColor4', 'iconColor5', 'iconColor6', 'iconColor7', 'iconColor8',
  'none', 'None',
]);
// Default values of the 16 fixed icon colours (CDE-SPEC 1.5, from the c fields of CDE's own .pm files).
const ICON_DEFAULTS = {
  iconGray1: '#DEDEDE', iconGray2: '#BDBDBD', iconGray3: '#ADADAD', iconGray4: '#949494',
  iconGray5: '#737373', iconGray6: '#636363', iconGray7: '#424242', iconGray8: '#212121',
  iconColor1: '#000000', iconColor2: '#FFFFFF', iconColor3: '#FF0000', iconColor4: '#00FF00',
  iconColor5: '#0000FF', iconColor6: '#FFFF00', iconColor7: '#00FFFF', iconColor8: '#FF00FF',
};

// Extract the C string literals in order, honouring \" and \\ escapes and skipping /* comments */.
// Comments and strings are tokenised together because a pixel row may itself contain "/*".
// literal: read strings as libXpm does (no escape processing; a string ends at the next quote). Genuine vendor
// files need it: HP CDE's SkyDark.pm and SkyLight.pm use a bare backslash as a pixel key.
function cStrings(src, literal) {
  const out = []; let i = 0;
  while (i < src.length) {
    if (src[i] === '/' && src[i + 1] === '*') { const e = src.indexOf('*/', i + 2); if (e < 0) throw new Error('unterminated comment'); i = e + 2; continue; }
    if (src[i] === '"') {
      let s = ''; i++;
      while (i < src.length && src[i] !== '"') {
        if (src[i] === '\\' && !literal) { const n = src[i + 1]; s += n === 'n' ? '\n' : n === 't' ? '\t' : n; i += 2; }
        else if (src[i] === '\n') throw new Error('newline inside string literal');
        else s += src[i++];
      }
      if (src[i] !== '"') throw new Error('unterminated string literal');
      i++; out.push(s);
    } else i++;
  }
  return out;
}

// opts.strict (our own files): every s value must be a symbol CDE resolves.
// opts.literal (genuine vendor files, read as libXpm reads them): strings without escapes, and the older
// "/* XPM2 C */ static char ** name = {" form that HP VUE 3.0's colour icons use.
function parse(src, name = '(xpm)', opts = {}) {
  if (opts.literal && /^\s*\/\*\s*XPM2 C\s*\*\//.test(src)) {
    src = src.replace(/^\s*\/\*\s*XPM2 C\s*\*\//, '/* XPM */').replace(/static\s+char\s*\*\s*\*\s*[^=\[]+(\[\s*\])?\s*=\s*\{/, 'static char * xpm2[] = {');
  }
  if (!/^\s*\/\*\s*XPM\s*\*\//.test(src)) throw new Error(name + ': missing /* XPM */ header');
  // libXpm only needs the array's strings; CDE's own files vary the declaration ("static char * *Fpapps [] = {").
  // (literal: libXpm skips the declaration up to its brace, so a name such as HP's "DtC++_m_pm" is accepted)
  const decl = (opts.literal ? /static\s+(const\s+)?char\s*\*[\s*]*[^\s[=]+\s*\[\s*\]\s*=\s*\{/
    : /static\s+(const\s+)?char\s*\*[\s*]*[A-Za-z_][A-Za-z0-9_]*\s*\[\s*\]\s*=\s*\{/).exec(src);
  if (!decl) throw new Error(name + ': missing static char *name[] = {');
  if (!/\}\s*;\s*$/.test(src.trim())) throw new Error(name + ': missing closing };');
  const strs = cStrings(src.slice(decl.index + decl[0].length - 1), !!opts.literal);
  if (!strs.length) throw new Error(name + ': no strings');
  const hdr = strs[0].trim().split(/\s+/).map(Number);
  if (hdr.length < 4 || hdr.slice(0, 4).some(v => !Number.isInteger(v) || v <= 0)) throw new Error(name + ': bad values line "' + strs[0] + '"');
  const [w, h, nc, cpp] = hdr;
  if (strs.length !== 1 + nc + h) throw new Error(`${name}: expected ${1 + nc + h} strings, found ${strs.length}`);
  const colours = new Map();
  for (let k = 0; k < nc; k++) {
    const line = strs[1 + k];
    const chars = line.substr(0, cpp);
    if (chars.length !== cpp) throw new Error(name + ': short colour line');
    if (colours.has(chars)) throw new Error(`${name}: duplicate colour key "${chars}"`);
    const toks = line.substr(cpp).trim().split(/\s+/);
    const keys = {};
    for (let t = 0; t < toks.length;) {
      const key = toks[t];
      if (!['s', 'm', 'g4', 'g', 'c'].includes(key)) throw new Error(`${name}: bad colour key "${key}" in "${line}"`);
      let v = toks[t + 1];
      if (v === undefined) throw new Error(`${name}: missing value for ${key}`);
      t += 2;
      // values may be multi-word colour names: absorb until the next key
      while (t < toks.length && !['s', 'm', 'g4', 'g', 'c'].includes(toks[t])) { v += ' ' + toks[t]; t++; }
      keys[key] = v;
    }
    if (!keys.s && !keys.c && !keys.m) throw new Error(`${name}: colour "${chars}" has no s, c or m`);
    if (opts.strict && keys.s && !CDE_SYMBOLS.has(keys.s)) throw new Error(`${name}: unknown CDE symbol "${keys.s}"`);
    colours.set(chars, keys);
  }
  const rows = [];
  for (let y = 0; y < h; y++) {
    const r = strs[1 + nc + y];
    if (r.length !== w * cpp) throw new Error(`${name}: row ${y} has ${r.length} chars, expected ${w * cpp}`);
    const row = [];
    for (let x = 0; x < w; x++) { const c = r.substr(x * cpp, cpp); if (!colours.has(c)) throw new Error(`${name}: row ${y} uses undefined colour "${c}"`); row.push(c); }
    rows.push(row);
  }
  return { w, h, cpp, colours, rows, hot: hdr.length >= 6 ? [hdr[4], hdr[5]] : null };
}
function read(file, opts) { return parse(fs.readFileSync(file, 'latin1'), file, opts); }

// Resolve to an RGB raster. resolve(sym) maps a CDE symbol to '#RRGGBB'; returns null for transparent.
function toRaster(x, resolve) {
  const p = []; // array of '#RRGGBB' or null
  const cache = new Map();
  for (const [k, keys] of x.colours) {
    let v = null;
    if (keys.s && /^none$/i.test(keys.s)) v = null;
    else if (keys.s && resolve && resolve(keys.s)) v = resolve(keys.s);
    else if (keys.c && /^none$/i.test(keys.c)) v = null;
    else if (keys.c) v = normColour(keys.c);
    else if (keys.s && ICON_DEFAULTS[keys.s]) v = ICON_DEFAULTS[keys.s];
    else if (keys.m) v = /^white$/i.test(keys.m) ? '#FFFFFF' : /^black$/i.test(keys.m) ? '#000000' : null;
    cache.set(k, v);
  }
  for (const row of x.rows) for (const c of row) p.push(cache.get(c));
  return { w: x.w, h: x.h, p };
}
const NAMED = { white: '#FFFFFF', black: '#000000', red: '#FF0000', green: '#00FF00', blue: '#0000FF', yellow: '#FFFF00', cyan: '#00FFFF', magenta: '#FF00FF' };
function normColour(c) {
  if (c[0] === '#') {
    const h = c.slice(1); const n = h.length / 3;
    if (![1, 2, 3, 4].includes(n) || !/^[0-9a-fA-F]+$/.test(h)) return null;
    return '#' + [0, 1, 2].map(i => { const v = h.substr(i * n, n); return (n === 1 ? v + v : v.substr(0, 2)).toUpperCase(); }).join('');
  }
  return NAMED[c.toLowerCase()] || null;
}

// Writer. grid: array of strings (one char per pixel, cpp 1); legend: { char: { s?, m?, c? } }.
function write(cname, grid, legend, comment) {
  const h = grid.length, w = grid[0].length;
  const used = [...new Set(grid.join(''))];
  for (const ch of used) if (!legend[ch]) throw new Error(cname + ': no legend for "' + ch + '"');
  const esc = s => s.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  let out = '/* XPM */\n';
  if (comment) out += comment.split('\n').map(l => '/* ' + l.replace(/\*\//g, '* /') + ' */').join('\n') + '\n';
  out += `static char * ${cname}[] = {\n/* width height ncolors cpp [x_hot y_hot] */\n"${w} ${h} ${used.length} 1 0 0",\n/* colors */\n`;
  for (const ch of used) {
    const L = legend[ch]; const parts = [];
    if (L.s) parts.push('s ' + L.s); if (L.m) parts.push('m ' + L.m); if (L.c) parts.push('c ' + L.c);
    out += `"${esc(ch)}\t${parts.join('\t')}",\n`;
  }
  out += '/* pixels */\n';
  grid.forEach((r, i) => { if (r.length !== w) throw new Error(cname + ': ragged row ' + i); out += `"${esc(r)}"${i < h - 1 ? ',' : ''}\n`; });
  return out + '};\n';
}

module.exports = { parse, read, toRaster, write, CDE_SYMBOLS, ICON_DEFAULTS, normColour };
