#!/usr/bin/env node
'use strict';
// u14: the Category menu strip for "Unclassified" (TAXONOMY order 21, added by T-0061 with no art), drawn with the
// category-art lane's own generator (this tree's art/library-categories, used read-only) and written in
// the store format Library loads: <store>/e26/cat/<id>.ppm, P6 336x41 (normal and selected frames).
//   node build-unclassified.js OUT_DIR [check-id]
// With check-id (e.g. routing) it also rebuilds that existing category's strip, to compare with the installed file.
const fs = require('fs'), path = require('path');
const LANE = path.join(__dirname, '..', '..', '..', 'art', 'library-categories');
const strips = require(path.join(LANE, 'src/strips'));
const categories = require(path.join(LANE, 'src/categories'));
const { pngIndexed, hexToRGB } = require(path.join(LANE, 'lib/image'));
const { S, place, BF, INK, PAPER, H, lettering } = require(path.join(LANE, 'src/art/kit'));

// Emblem: a bundle of loose, unsorted papers tied with string, a blank tag hanging from the knot (local 64 box).
function emblem() {
  return [
    BF(S.poly([[14, 14], [50, 10], [54, 46], [18, 52]]), { tone: 0.35, hatch: H(-8, 2), shade: null }),
    BF(S.poly([[8, 20], [44, 22], [42, 58], [6, 54]]), { tone: 0.15, hatch: H(4, 2), shade: null }),
    BF(S.poly([[12, 24], [52, 24], [52, 62], [12, 62]])),
    INK(S.inter(lettering(16, 30, 48, 58, 5, 0.8, 21), S.box(15, 27, 49, 59))),
    INK(S.union(S.seg(32, 23, 32, 63, 1.1), S.seg(11, 43, 53, 43, 1.1))),
    INK(S.circle(32, 43, 2.2)),
    INK(S.seg(32, 43, 41, 53, 0.8)),
    BF(S.poly([[38, 51], [52, 55], [50, 64], [36, 60]]), { shade: null }),
    PAPER(S.circle(40.5, 54.5, 1.3)),
  ];
}

const art = {
  id: 'unclassified',
  emblem,
  button: () => place(emblem(), 0.72, 122, -4),
  icon: size => ({ layers: emblem(), view: { s: (size - 2) / 66, ox: 0, oy: 0 } }),
};

// No colours in the taxonomy for this entry: the stone face of the Library's own controls (entry 14) and a neutral grey ink
// (entry 29), so it reads as "no subject" next to the twenty coloured categories.
const cat = { id: 'unclassified', label: ['Unclassified'], face: '#D9D6C5', ink: '#969696' };

function ppm(img) {
  const head = Buffer.from(`P6\n${img.w} ${img.h}\n255\n`, 'ascii');
  const body = Buffer.alloc(img.w * img.h * 3);
  for (let i = 0; i < img.w * img.h; i++) {
    const [r, g, b] = hexToRGB(img.pal[img.px[i]]);
    body[3 * i] = r; body[3 * i + 1] = g; body[3 * i + 2] = b;
  }
  return Buffer.concat([head, body]);
}

function main() {
  const out = process.argv[2], check = process.argv[3];
  fs.mkdirSync(out, { recursive: true });
  const strip = strips.catStrip(cat, strips.motifMask(art, cat));
  fs.writeFileSync(path.join(out, 'unclassified.ppm'), ppm(strip));
  fs.writeFileSync(path.join(out, 'unclassified-preview.png'), pngIndexed(strip));
  if (check) {
    const c = categories.all().find(x => x.id === check);
    const built = categories.build(c);
    fs.writeFileSync(path.join(out, `${check}.check.ppm`), ppm(built.cat_strip));
  }
  console.log('wrote', out);
}
main();
