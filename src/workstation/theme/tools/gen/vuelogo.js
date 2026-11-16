'use strict';
// The HP VUE 3.0 login wordmark (HPlogo.bm, 482x122, from the owner's 9.05 disc) re-set for CDE's greeter (private build).
// VUE's login draws the wordmark on a plate above the login box; CDE 2.5.4's dtgreet puts the logo beside the box in a
// fixed 245 x 245 frame (vglogo.c), which cuts the 482-pixel wordmark after "HE". This keeps HP's own pixels and only
// moves the two blocks: the emblem above, "HEWLETT PACKARD" below, on the bitmap's own checkerboard stipple, with each
// block moved by an even number of pixels so the stipple runs on unbroken. Nothing is redrawn or scaled.
// Blocks found in the bitmap (non-stipple columns 56-175 and 194-423, rows 22-95); checked when built.
const EMBLEM = { x: 56, y: 22, w: 120, h: 74 }, TEXT = { x: 194, y: 22, w: 230, h: 74 };
const SIZE = 245, GAP = 18;

function parseXbm(buf) {
  const t = buf.toString('latin1');
  const w = +/_width\s+(\d+)/.exec(t)[1], h = +/_height\s+(\d+)/.exec(t)[1];
  const bytes = t.slice(t.indexOf('{')).match(/0x[0-9a-fA-F]+/g).map(x => parseInt(x, 16));
  const bpr = Math.ceil(w / 8);
  if (bytes.length !== bpr * h) throw new Error(`xbm: ${bytes.length} bytes for ${w}x${h}`);
  return { w, h, px: (x, y) => (bytes[y * bpr + (x >> 3)] >> (x & 7)) & 1 };
}
function writeXbm(name, w, h, px, comment) {
  const bpr = Math.ceil(w / 8), out = [];
  for (let y = 0; y < h; y++) for (let b = 0; b < bpr; b++) { let v = 0; for (let i = 0; i < 8; i++) { const x = b * 8 + i; if (x < w && px(x, y)) v |= 1 << i; } out.push('0x' + v.toString(16).padStart(2, '0')); }
  const lines = []; for (let i = 0; i < out.length; i += 12) lines.push('   ' + out.slice(i, i + 12).join(', '));
  // no comment line: dtgreet reads the file with XReadBitmapFile; the provenance (comment) goes into MANIFEST.tsv
  void comment;
  return `#define ${name}_width ${w}\n#define ${name}_height ${h}\nstatic unsigned char ${name}_bits[] = {\n${lines.join(',\n')}};\n`;
}
function stacked(buf) {
  const src = parseXbm(buf);
  if (src.w !== 482 || src.h !== 122) throw new Error(`HPlogo.bm is ${src.w}x${src.h}, expected 482x122`);
  const stipple = (x, y) => ((x + y) & 1) ? src.px(1, 0) : src.px(0, 0);
  // the blocks must be where they were measured: nothing but stipple outside them
  for (let y = 0; y < src.h; y++) for (let x = 0; x < src.w; x++) {
    const inside = [EMBLEM, TEXT].some(b => x >= b.x && x < b.x + b.w && y >= b.y && y < b.y + b.h);
    if (!inside && src.px(x, y) !== stipple(x, y)) throw new Error(`HPlogo.bm: ink outside the measured blocks at ${x},${y}`);
  }
  const top = Math.floor((SIZE - EMBLEM.h - GAP - TEXT.h) / 2);
  const place = (b, y) => { const x = Math.floor((SIZE - b.w) / 2); let dx = x - b.x, dy = y - b.y; if ((dx + dy) % 2) dx++; return { dx, dy }; };
  const pe = place(EMBLEM, top), pt = place(TEXT, top + EMBLEM.h + GAP);
  const px = (x, y) => {
    for (const [b, p] of [[EMBLEM, pe], [TEXT, pt]]) { const sx = x - p.dx, sy = y - p.dy; if (sx >= b.x && sx < b.x + b.w && sy >= b.y && sy < b.y + b.h) return src.px(sx, sy); }
    return stipple(x, y);
  };
  return writeXbm('hp_vue_logo_stacked', SIZE, SIZE, px,
    'HP VUE 3.0 login wordmark (HPlogo.bm, HP-UX 9.05) re-set in two rows for dtgreet\'s 245 px logo frame: HP\'s pixels, moved, not redrawn (tools/gen/vuelogo.js). PRIVATE build only.');
}

module.exports = { stacked, parseXbm, writeXbm, EMBLEM, TEXT, SIZE };
