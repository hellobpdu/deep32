'use strict';
// Deep32 application and control icons: 48x48 (.l, front panel), 32x32 (.m, desktop, subpanels, App Manager),
// 16x16 (.t, menus, window menu, lists). Each size is drawn by hand in code (no scaling), CDE symbols only.
const SIZE = { l: 48, m: 32, s: 24, t: 16 };
const MODULES = ['./library', './personal', './lab', './style', './apps', './system', './datatypes'];

function defs() {
  const all = [];
  for (const m of MODULES) all.push(...require(m));
  const seen = new Set();
  for (const d of all) { if (seen.has(d.name)) throw new Error(`icon ${d.name} defined twice`); seen.add(d.name); }
  return all;
}
function items() {
  const out = [];
  for (const d of defs()) for (const [sz, fn] of Object.entries(d.draw)) {
    out.push({
      group: d.datatype ? 'icons/types' : 'icons', file: `${d.name}.${sz}`, w: SIZE[sz], h: SIZE[sz], sizeLetter: sz,
      title: d.title, ref: d.ref, rule: 'cde', formats: ['pm', 'bm', 'mask'], preview: d.preview || ['Default', 8],
      draw: fn, meta: d, allow: d.allow || [],
    });
  }
  return out;
}
module.exports = { items, defs, SIZE };
