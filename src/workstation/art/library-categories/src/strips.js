'use strict';
// Composes the category button strips, the watermark and the icons from a category's colours, label and art.
// Geometry and colours are the measured numbers of the period design strips (README, "Measurements"); nothing else of
// the disc is used.
const { Indexed } = require('../lib/image');
const font = require('../lib/font');
const { render } = require('../lib/engrave');

// measured constants -------------------------------------------------------------------------------------------
const NORMAL = { ground: '#494339', label: '#D9D6C5', rule: '#A09683' };
const CAT = { w: 168, h: 41, frames: 2, labelX: 31, base: [17, 31], arrow: { x: 152, y: 23 }, rule: { y: 40, x0: 14, x1: 156 } };
const FW = { w: 170, h: 43, frames: 4, labelX: 31, base: [18, 32], box: { x: 10, y: 7, s: 13 }, motifDy: 1 };
const BOX = { edge: '#A09683', white: '#FFFFFF', down: '#D9D6C5', tick: '#000000', tickOff: '#A09683' };
const WM = { w: 323, h: 400, band: 100, paper: '#FFFFFF', cream: '#EBEAE1' };
const MOTIF = { w: 170, h: 41 }; // one motif canvas serves both strips: CAT (x, y) = motif (x, y); FW (x, y + 1)
const FONT = 'helvB12-75dpi';
// our own tick, drawn for this lane (8 x 7, origin at the box's inner top-left + (2, 2))
const TICK = ['.......#', '......##', '.....##.', '#...##..', '##.##...', '.###....', '..#.....'];

function labelLines(cat) { return cat.label; }

// The emblem's place on the strip (2026-11-06, the menu-arrow nit): the engraving ends 7 px before the arrow (x 152),
// as CAT_PHYS's does on the disc (CAT_RELIGION 8, CAT_HISTORY 9, CAT_LIFE 31 px), so the arrow stands on the plain
// face; and it starts at least 5 px after the label. Drawings that reach further right are moved left, and only if
// the label leaves too little room are they scaled down about their own right edge and middle.
const EMBLEM = { right: CAT.arrow.x - 8, gapAfterLabel: 5 };

function inkBox(r) {
  let x0 = Infinity, x1 = -1, y0 = Infinity, y1 = -1;
  for (let y = 0; y < r.h; y++) for (let x = 0; x < r.w; x++) if (r.m[y * r.w + x]) {
    if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
  }
  return x1 < 0 ? null : { x0, x1, y0, y1 };
}

function labelEnd(lines) {
  const f = font.load(FONT);
  const lsb = Math.min(...lines.map(t => f.glyphs[t.charCodeAt(0)].lsb));
  return CAT.labelX - lsb + Math.max(...lines.map(t => font.width(t, FONT)));
}

// motif: { w, h, m } 1-bit (1 = ink) at MOTIF size; with a category, placed clear of its label and of the arrow
function motifMask(art, cat = null) {
  const at = view => render({ w: MOTIF.w, h: MOTIF.h, layers: art.button(), view });
  const r = at({ s: 1, ox: 0, oy: 0 });
  const bb = inkBox(r);
  if (!cat || !bb || bb.x1 <= EMBLEM.right) return r;
  const left = labelEnd(cat.label) + EMBLEM.gapAfterLabel, shift = bb.x1 - EMBLEM.right;
  if (bb.x0 - shift >= left) return at({ s: 1, ox: -shift, oy: 0 });
  const cy = (bb.y0 + bb.y1 + 1) / 2;
  let k = (EMBLEM.right - left) / (bb.x1 - bb.x0 + 1), ox = EMBLEM.right + 1 - (bb.x1 + 1) * k, out = null;
  for (let tries = 0; tries < 24; tries++) {   // outlines and hatching can land a pixel either side: nudge until clear
    out = at({ s: k, ox, oy: cy * (1 - k) });
    const b = inkBox(out);
    if (!b || (b.x1 <= EMBLEM.right && b.x0 >= left)) break;
    if (b.x1 > EMBLEM.right) ox -= b.x1 - EMBLEM.right;
    else k -= 0.01;
  }
  return out;
}

// x is the label's left ink edge (the originals' lines start their ink at x 31), so the pen starts one bearing earlier
function drawLabel(img, lines, x, bases, colourIdx, haloIdx = null) {
  const pts = [], f = font.load(FONT);
  const pen = x - Math.min(...lines.map(t => f.glyphs[t.charCodeAt(0)].lsb));
  lines.forEach((t, i) => font.draw(t, pen, bases[i], (px, py) => pts.push([px, py]), FONT));
  if (haloIdx !== null) for (const [px, py] of pts) for (let j = -1; j <= 1; j++) for (let i = -1; i <= 1; i++) img.set(px + i, py + j, haloIdx);
  for (const [px, py] of pts) img.set(px, py, colourIdx);
  return pts;
}

// the menu arrow (4 x 7); with haloIdx every pixel round it is set to haloIdx first, so nothing of the emblem touches
// it. Seven of the Library's nine strips keep their engravings clear of the arrow (CAT_PHYS ends 7 px short of it,
// CAT_RELIGION 8, CAT_HISTORY 9, CAT_LIFE 31; CAT_ARTLANG, CAT_GEOG and CAT_SOCIAL engrave nothing near it), and two,
// CAT_PERFORM and CAT_SPORTS, draw it over a light, open texture. Our inks are darker and our emblems sit right where
// the arrow is, so the selected frame gives the arrow the same 1-px face halo the labels have (review nit,
// 2026-11-06: the arrow touched the emblem in 19 of the 20 selected frames).
function arrow(img, x, y, idx, haloIdx = null) {
  const pts = [];
  for (let c = 0; c < 4; c++) for (let r = c; r < 7 - c; r++) pts.push([x + c, y + r]);
  if (haloIdx !== null) for (const [px, py] of pts) for (let j = -1; j <= 1; j++) for (let i = -1; i <= 1; i++) img.set(px + i, py + j, haloIdx);
  for (const [px, py] of pts) img.set(px, py, idx);
}

// CAT strip: 336 x 41, frame 0 normal (shared dark ground), frame 1 selected (face, ink motif, black label)
function catStrip(cat, motif) {
  const pal = [NORMAL.ground, NORMAL.label, NORMAL.rule, cat.face, cat.ink, '#000000'];
  const img = new Indexed(CAT.w * 2, CAT.h, pal, 0);
  const [G, LB, RU, FA, IN, BK] = pal.map((_, i) => i);
  // frame 0
  drawLabel(img, labelLines(cat), CAT.labelX, CAT.base, LB);
  arrow(img, CAT.arrow.x, CAT.arrow.y, LB);
  for (let x = CAT.rule.x0; x <= CAT.rule.x1; x++) img.set(x, CAT.rule.y, RU);
  // frame 1
  const o = CAT.w;
  img.fillRect(o, 0, CAT.w, CAT.h, FA);
  for (let y = 0; y < CAT.h; y++) for (let x = 0; x < CAT.w; x++) if (motif.m[y * MOTIF.w + x]) img.set(o + x, y, IN);
  drawLabel(img, labelLines(cat), o + CAT.labelX, CAT.base, BK, FA);
  arrow(img, o + CAT.arrow.x, CAT.arrow.y, BK, FA);
  return img;
}

// Find Wizard strip: 680 x 43, frames normal, checked, pressed, disabled
function fwStrip(cat, motif) {
  const pal = [cat.face, cat.ink, '#000000', BOX.white, BOX.edge, BOX.down];
  const img = new Indexed(FW.w * 4, FW.h, pal, 0);
  const [FA, IN, BK, WH, ED, DN] = pal.map((_, i) => i);
  for (let f = 0; f < 4; f++) {
    const o = f * FW.w;
    for (let y = 0; y < MOTIF.h; y++) for (let x = 1; x < FW.w - 1; x++) if (x < MOTIF.w && motif.m[y * MOTIF.w + x]) img.set(o + x, y + FW.motifDy, IN);
    for (let x = 0; x < FW.w; x++) { img.set(o + x, 0, BK); img.set(o + x, FW.h - 1, BK); }
    for (let y = 0; y < FW.h; y++) { img.set(o, y, BK); img.set(o + FW.w - 1, y, BK); }
    drawLabel(img, labelLines(cat), o + FW.labelX, FW.base, BK, FA);
    const { x: bx, y: by, s } = FW.box;
    img.fillRect(o + bx, by, s, s, ED);
    img.fillRect(o + bx + 1, by + 1, s - 2, s - 2, f >= 2 ? DN : WH);
    if (f === 1 || f === 3) TICK.forEach((row, j) => [...row].forEach((ch, i) => { if (ch === '#') img.set(o + bx + 3 + i, by + 3 + j, f === 1 ? BK : ED); }));
  }
  return img;
}

// watermark: 323 x 400, two tones, the cream band down the left
function watermark(art) {
  const r = render({ w: WM.w, h: WM.h, layers: art.watermark(), ground: (x) => (x < WM.band ? 1 : 0) });
  const img = new Indexed(WM.w, WM.h, [WM.paper, WM.cream], 0);
  img.px.set(r.m);
  return img;
}

// icons: the emblem on the category's face, framed in the shared dark ground; size 16 or 32
function icon(cat, art, size) {
  const pal = [NORMAL.ground, cat.face, cat.ink, '#000000'];
  const img = new Indexed(size, size, pal, 1);
  const [G, FA, IN, BK] = [0, 1, 2, 3];
  const spec = art.icon(size);
  const r = render({ w: size - 2, h: size - 2, layers: spec.layers, view: spec.view, flat: size <= 16 || spec.flat, flatCut: spec.flatCut ?? 0.5 });
  for (let y = 0; y < size - 2; y++) for (let x = 0; x < size - 2; x++) if (r.m[y * (size - 2) + x]) img.set(x + 1, y + 1, IN);
  if (spec.dark) for (const [x, y] of spec.dark(size)) img.set(x, y, BK);
  for (let i = 0; i < size; i++) { img.set(i, 0, G); img.set(i, size - 1, G); img.set(0, i, G); img.set(size - 1, i, G); }
  return img;
}

module.exports = { NORMAL, CAT, FW, BOX, WM, MOTIF, FONT, TICK, EMBLEM, inkBox, labelEnd, motifMask, catStrip, fwStrip, watermark, icon };
