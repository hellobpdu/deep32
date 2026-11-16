'use strict';
// Motif 2.3.8 default colour calculation (lib/Xm/Color.c CalculateColorsRGB), exactly as CDE-SPEC 1.3
// documents it: C int arithmetic on 16-bit channels, division truncating toward zero, the luminosity
// term computed in double and truncated. 16 -> 8 bit is the high byte (v >> 8), never rounded (CDE-SPEC 1.3.1).
// Golden-tested against all 1,640 values of cde-palettes.json by validate.js (test T-PAL-1).

const MAX = 65535;
const PCT = Math.trunc(MAX / 100);          // 655
const LITE_T = 93 * PCT;                    // XmDEFAULT_LIGHT_THRESHOLD
const DARK_T = 20 * PCT;                    // XmDEFAULT_DARK_THRESHOLD
const FG_T = 70 * PCT;                      // XmDEFAULT_FOREGROUND_THRESHOLD
const idiv = (a, b) => Math.trunc(a / b);   // C integer division

function brightness(r, g, b) {
  const intensity = idiv(r + g + b, 3);
  const luminosity = Math.trunc(0.30 * r + 0.59 * g + 0.11 * b);
  const light = idiv(Math.min(r, g, b) + Math.max(r, g, b), 2);
  return idiv(intensity * 75 + light * 0 + luminosity * 25, 100);
}

// bg: [r,g,b] 16-bit. Returns { model, brightness, bg, fg, ts, bs, sc } each [r,g,b] 16-bit.
function derive(bg) {
  const [r, g, b] = bg;
  const br = brightness(r, g, b);
  const fg = br > FG_T ? [0, 0, 0] : [MAX, MAX, MAX];
  let model, f;
  if (br < DARK_T) {
    model = 'dark';
    f = c => ({ sc: c + idiv(15 * (MAX - c), 100), bs: c + idiv(30 * (MAX - c), 100), ts: c + idiv(50 * (MAX - c), 100) });
  } else if (br > LITE_T) {
    model = 'lite';
    f = c => ({ sc: c - idiv(c * 15, 100), bs: c - idiv(c * 40, 100), ts: c - idiv(c * 20, 100) });
  } else {
    model = 'medium';
    const fs = 15;
    const fb = 60 + idiv(br * (40 - 60), MAX);
    const ft = 50 + idiv(br * (60 - 50), MAX);
    f = c => ({ sc: c - idiv(c * fs, 100), bs: c - idiv(c * fb, 100), ts: c + idiv(ft * (MAX - c), 100) });
  }
  const ch = [f(r), f(g), f(b)];
  const pick = k => [ch[0][k], ch[1][k], ch[2][k]];
  return { model, brightness: br, bg: [r, g, b], fg, ts: pick('ts'), bs: pick('bs'), sc: pick('sc') };
}

const hex2 = v => v.toString(16).padStart(2, '0').toUpperCase();
const hex4 = v => v.toString(16).padStart(4, '0');
const to8 = c16 => c16.map(v => v >> 8);
const rgb8hex = c16 => '#' + to8(c16).map(hex2).join('');
const x11hex16 = c16 => '#' + c16.map(hex4).join('');

// The XParseColor numeric forms (#RGB .. #RRRRGGGGBBBB: the given bits are the most significant bits)
// plus the few names CDE's palette files use. Returns [r,g,b] 16-bit or null.
const NAMES = { black: [0, 0, 0], white: [MAX, MAX, MAX] };
function parseX11Colour(s) {
  s = String(s).trim();
  if (s[0] === '#') {
    const h = s.slice(1);
    if (!/^[0-9a-fA-F]+$/.test(h) || ![3, 6, 9, 12].includes(h.length)) return null;
    const n = h.length / 3;
    return [0, 1, 2].map(i => parseInt(h.substr(i * n, n), 16) << (16 - 4 * n));
  }
  const k = s.toLowerCase().replace(/\s+/g, '');
  return NAMES[k] ? NAMES[k].slice() : null;
}

// 8-bit "#RRGGBB" -> the 16-bit form CDE's own palette files use for 8-bit sources ("#ed00a8007000").
function from8(hex) {
  const m = /^#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$/.exec(hex);
  if (!m) throw new Error('bad #RRGGBB: ' + hex);
  return [1, 2, 3].map(i => parseInt(m[i], 16) << 8);
}

// Relative luminance and WCAG contrast, for the safety-colour checks (ISO 9.5: 4.5:1 or better).
function relLum(rgb8) {
  const lin = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  return 0.2126 * lin(rgb8[0]) + 0.7152 * lin(rgb8[1]) + 0.0722 * lin(rgb8[2]);
}
function contrast(a8, b8) {
  const la = relLum(a8), lb = relLum(b8);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}
const hexTo8 = h => [1, 3, 5].map(i => parseInt(h.substr(i, 2), 16));

// dtsession's colour-use mapping (SrvPalette.c convert_pixel_set, CDE-SPEC 1.2.3): requested CS -> palette line.
const USE_MAP = {
  HIGH_COLOR:   [0, 1, 2, 3, 4, 5, 6, 7, 8],
  MEDIUM_COLOR: [0, 1, 2, 3, 4, 2, 2, 3, 2],
};

// Derive all sets of a palette (array of 8 [r,g,b] 16-bit backgrounds) for a colour use.
// Returns sets[1..8] of { bg, fg, ts, bs, sc } as 8-bit "#RRGGBB", plus the 16-bit forms.
function paletteSets(bgs16, use = 'HIGH_COLOR') {
  const map = USE_MAP[use];
  if (!map) throw new Error('unsupported colour use ' + use);
  const out = [null];
  for (let cs = 1; cs <= 8; cs++) {
    const line = map[cs];
    const d = derive(bgs16[line - 1]);
    out.push({
      cs, line, model: d.model,
      bg: rgb8hex(d.bg), fg: rgb8hex(d.fg), ts: rgb8hex(d.ts), bs: rgb8hex(d.bs), sc: rgb8hex(d.sc),
      bg16: x11hex16(d.bg), fg16: x11hex16(d.fg), ts16: x11hex16(d.ts), bs16: x11hex16(d.bs), sc16: x11hex16(d.sc),
    });
  }
  return out;
}

module.exports = { MAX, brightness, derive, to8, rgb8hex, x11hex16, parseX11Colour, from8, contrast, hexTo8, USE_MAP, paletteSets };
