'use strict';
// CDE icon colour model: the 16 fixed icon colours, the 5 palette-following symbols, and how they resolve.
// Sources: ASSETS.md 3.1 (tally of cde-2.5.3/programs/icons, 1,677 files); CDE-SPEC 1.5; the mono ("m") keys are
// the majority mapping found in the same files (iconGray1-4 white, 5-8 black, and so on).
const path = require('path');
const fs = require('fs');

const FIXED = {
  iconColor1: '#000000', iconColor2: '#FFFFFF', iconColor3: '#FF0000', iconColor4: '#00FF00',
  iconColor5: '#0000FF', iconColor6: '#FFFF00', iconColor7: '#00FFFF', iconColor8: '#FF00FF',
  iconGray1: '#DEDEDE', iconGray2: '#BDBDBD', iconGray3: '#ADADAD', iconGray4: '#949494',
  iconGray5: '#737373', iconGray6: '#636363', iconGray7: '#424242', iconGray8: '#212121',
};
// Default "c" values CDE writes for the dynamic symbols (used only when no colour set is supplied).
const DYNAMIC_DEFAULT = {
  background: '#949494', foreground: '#FFFFFF', topShadowColor: '#BDBDBD',
  bottomShadowColor: '#636363', selectColor: '#737373',
};
const DYNAMIC = Object.keys(DYNAMIC_DEFAULT);
const MONO = {
  iconColor1: 'black', iconColor2: 'white', iconColor3: 'black', iconColor4: 'white', iconColor5: 'black',
  iconColor6: 'white', iconColor7: 'white', iconColor8: 'black',
  iconGray1: 'white', iconGray2: 'white', iconGray3: 'white', iconGray4: 'white',
  iconGray5: 'black', iconGray6: 'black', iconGray7: 'black', iconGray8: 'black',
  background: 'black', foreground: 'white', topShadowColor: 'white', bottomShadowColor: 'black', selectColor: 'white',
};
const SYMBOLS = new Set([...Object.keys(FIXED), ...DYNAMIC]);

// Short authoring aliases used by the drawing code (never written to files).
const ALIAS = {
  k: 'iconColor1', w: 'iconColor2', r: 'iconColor3', g: 'iconColor4', b: 'iconColor5', y: 'iconColor6', c: 'iconColor7', m: 'iconColor8',
  g1: 'iconGray1', g2: 'iconGray2', g3: 'iconGray3', g4: 'iconGray4', g5: 'iconGray5', g6: 'iconGray6', g7: 'iconGray7', g8: 'iconGray8',
  bg: 'background', fg: 'foreground', ts: 'topShadowColor', bs: 'bottomShadowColor', sc: 'selectColor',
};

function hexToRGB(h) {
  const s = h.replace('#', '');
  return [parseInt(s.slice(0, 2), 16), parseInt(s.slice(2, 4), 16), parseInt(s.slice(4, 6), 16)];
}
function rgbToHex(r, g, b) {
  return '#' + [r, g, b].map(v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('').toUpperCase();
}
// Motif-style brightness (ASSETS 2.1), on 0..255, used for the mono key of fixed brand colours.
function brightness(hex) {
  const [r, g, b] = hexToRGB(hex);
  const intensity = (r + g + b) / 3;
  const lum = 0.30 * r + 0.59 * g + 0.11 * b;
  return (intensity * 75 + lum * 25) / 100;
}

let PALETTES = null;
function palettes() {
  if (!PALETTES) PALETTES = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'data', 'cde-palettes-subset.json'), 'utf8')).palettes;
  return PALETTES;
}
// A colour set as { background, foreground, topShadowColor, bottomShadowColor, selectColor }.
function colourSet(paletteName, setId) {
  const p = palettes()[paletteName];
  if (!p) throw new Error(`unknown palette ${paletteName}`);
  const s = p.find(x => x.set === setId);
  return { background: s.bg, foreground: s.fg, topShadowColor: s.ts, bottomShadowColor: s.bs, selectColor: s.sc };
}

// Resolve a colour token to [r,g,b,a]. Tokens: null (transparent), a CDE symbol, or '#RRGGBB'.
function resolver(set) {
  const dyn = set || DYNAMIC_DEFAULT;
  const cache = new Map();
  return tok => {
    if (tok === null || tok === undefined || tok === 'none') return [0, 0, 0, 0];
    if (cache.has(tok)) return cache.get(tok);
    let hex;
    if (tok[0] === '#') hex = tok;
    else if (FIXED[tok]) hex = FIXED[tok];
    else if (dyn[tok]) hex = dyn[tok];
    else throw new Error(`unknown colour token ${tok}`);
    const v = [...hexToRGB(hex), 255];
    cache.set(tok, v);
    return v;
  };
}

function canon(tok) {
  if (tok === null || tok === undefined) return null;
  if (tok[0] === '#') return tok.toUpperCase();
  if (ALIAS[tok]) return ALIAS[tok];
  if (SYMBOLS.has(tok)) return tok;
  throw new Error(`unknown colour token '${tok}'`);
}

module.exports = { FIXED, DYNAMIC, DYNAMIC_DEFAULT, MONO, SYMBOLS, ALIAS, hexToRGB, rgbToHex, brightness, colourSet, resolver, canon, palettes };
