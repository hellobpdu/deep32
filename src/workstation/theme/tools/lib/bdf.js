'use strict';
// BDF 2.1 reader (strict enough to validate) and the XLFD helpers used by the font package and the renderer.
const fs = require('fs');

function parse(src, name = '(bdf)') {
  const lines = src.split(/\r?\n/);
  if (!/^STARTFONT 2\.[12]$/.test(lines[0])) throw new Error(name + ': not STARTFONT 2.1');
  const f = { name, props: {}, glyphs: new Map(), font: null, bbx: null, header: [] };
  let i = 1, inProps = false, nprops = 0, nchars = null;
  for (; i < lines.length; i++) {
    const L = lines[i];
    if (L.startsWith('STARTCHAR')) break;
    f.header.push(L);
    if (L.startsWith('FONT ')) f.font = L.slice(5);
    else if (L.startsWith('FONTBOUNDINGBOX')) f.bbx = L.split(/\s+/).slice(1).map(Number);
    else if (L.startsWith('STARTPROPERTIES')) { inProps = true; nprops = +L.split(/\s+/)[1]; }
    else if (L === 'ENDPROPERTIES') inProps = false;
    else if (L.startsWith('CHARS ')) nchars = +L.split(/\s+/)[1];
    else if (inProps) {
      const m = /^(\S+)\s+(.*)$/.exec(L); if (!m) continue;
      const v = m[2].startsWith('"') ? m[2].slice(1, -1).replace(/""/g, '"') : +m[2];
      if (m[1] === 'COMMENT') continue;
      if (f.props[m[1]] !== undefined) { if (!Array.isArray(f.props[m[1]])) f.props[m[1]] = [f.props[m[1]]]; f.props[m[1]].push(v); }
      else f.props[m[1]] = v;
    }
  }
  if (!f.font || !f.bbx) throw new Error(name + ': missing FONT or FONTBOUNDINGBOX');
  if (nchars === null) throw new Error(name + ': missing CHARS');
  while (i < lines.length && lines[i].startsWith('STARTCHAR')) {
    const g = { name: lines[i].slice(10), enc: null, dw: null, bbx: null, rows: [] };
    i++;
    for (; i < lines.length && lines[i] !== 'BITMAP'; i++) {
      const L = lines[i];
      if (L.startsWith('ENCODING')) g.enc = +L.split(/\s+/)[1];
      else if (L.startsWith('DWIDTH')) g.dw = +L.split(/\s+/)[1];
      else if (L.startsWith('BBX')) g.bbx = L.split(/\s+/).slice(1).map(Number);
    }
    if (lines[i] !== 'BITMAP') throw new Error(name + ': glyph ' + g.name + ' without BITMAP');
    i++;
    for (; i < lines.length && lines[i] !== 'ENDCHAR'; i++) g.rows.push(lines[i].trim());
    if (lines[i] !== 'ENDCHAR') throw new Error(name + ': glyph ' + g.name + ' without ENDCHAR');
    if (g.enc === null || g.dw === null || !g.bbx) throw new Error(name + ': glyph ' + g.name + ' missing ENCODING/DWIDTH/BBX');
    if (g.rows.length !== g.bbx[1]) throw new Error(`${name}: glyph ${g.name} has ${g.rows.length} rows, BBX says ${g.bbx[1]}`);
    const hexw = Math.ceil(g.bbx[0] / 8) * 2;
    // libXfont pads short rows with zeros; CDE's HP fonts have some ("00" in a 9-px-wide glyph). Count them,
    // reject only malformed hex or over-long rows.
    for (let k = 0; k < g.rows.length; k++) {
      const r = g.rows[k];
      if (r.length % 2 || !/^[0-9A-Fa-f]*$/.test(r)) throw new Error(`${name}: glyph ${g.name} bad bitmap row "${r}"`);
      if (r.length > hexw) {
        // over-long rows are accepted only when the extra bytes are zero (CDE's sys13 has "aa00" in 8-px glyphs)
        if (!/^0*$/.test(r.slice(hexw))) throw new Error(`${name}: glyph ${g.name} row "${r}" has ink beyond its BBX`);
        f.longRows = (f.longRows || 0) + 1; g.rows[k] = r.slice(0, hexw);
      } else if (r.length < hexw) { f.shortRows = (f.shortRows || 0) + 1; g.rows[k] = r.padEnd(hexw, '0'); }
    }
    if (g.enc >= 0) f.glyphs.set(g.enc, g);
    i++;
  }
  if (lines[i] !== 'ENDFONT') throw new Error(name + ': missing ENDFONT');
  let count = 0; for (const L of lines) if (L.startsWith('STARTCHAR')) count++;
  if (count !== nchars) throw new Error(`${name}: CHARS ${nchars} but ${count} glyphs`);
  f.ascent = f.props.FONT_ASCENT; f.descent = f.props.FONT_DESCENT;
  return f;
}
function read(file) { return parse(fs.readFileSync(file, 'latin1'), file); }

// XLFD: 14 fields after the leading '-'.
const XLFD_FIELDS = ['FOUNDRY', 'FAMILY_NAME', 'WEIGHT_NAME', 'SLANT', 'SETWIDTH_NAME', 'ADD_STYLE_NAME', 'PIXEL_SIZE',
  'POINT_SIZE', 'RESOLUTION_X', 'RESOLUTION_Y', 'SPACING', 'AVERAGE_WIDTH', 'CHARSET_REGISTRY', 'CHARSET_ENCODING'];
function splitXlfd(name) {
  if (name[0] !== '-') return null;
  const f = name.slice(1).split('-');
  return f.length === 14 ? f : null;
}
// X's pattern match for font names: * any run, ? one char, case-insensitive.
function xlfdMatch(pattern, name) {
  const re = new RegExp('^' + pattern.toLowerCase().replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.') + '$');
  return re.test(name.toLowerCase());
}

module.exports = { parse, read, splitXlfd, xlfdMatch, XLFD_FIELDS };
