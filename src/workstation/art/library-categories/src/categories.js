'use strict';
// The twenty categories (from data/taxonomy-snapshot.json) joined with their drawings (src/art/<id>.js).
const fs = require('fs');
const path = require('path');
const strips = require('./strips');

const SNAP = path.join(__dirname, '..', 'data', 'taxonomy-snapshot.json');
function snapshot() { return JSON.parse(fs.readFileSync(SNAP, 'utf8')); }
function all() { return snapshot().categories; }
function art(id) {
  const f = path.join(__dirname, 'art', `${id}.js`);
  if (!fs.existsSync(f)) throw new Error(`no drawing for category ${id} (src/art/${id}.js)`);
  return require(f);
}
function build(cat) {
  const a = art(cat.id);
  const motif = strips.motifMask(a, cat);
  return {
    cat, motif,
    cat_strip: strips.catStrip(cat, motif),
    fw_strip: strips.fwStrip(cat, motif),
    watermark: strips.watermark(a),
    icon16: strips.icon(cat, a, 16),
    icon32: strips.icon(cat, a, 32),
  };
}
module.exports = { snapshot, all, art, build, SNAP };
