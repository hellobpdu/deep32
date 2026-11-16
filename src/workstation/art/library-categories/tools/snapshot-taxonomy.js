#!/usr/bin/env node
'use strict';
// Copies the fields this lane draws from the taxonomy (final since 2026-11-06) into data/taxonomy-snapshot.json, with
// the source's SHA-256, so every build is reproducible. Re-run whenever TAXONOMY.json changes:
//   node tools/snapshot-taxonomy.js [path/to/TAXONOMY.json]
const fs = require('fs'), path = require('path'), crypto = require('crypto');
const src = process.argv[2] || '/d32/a/share/e26/TAXONOMY.json';
const raw = fs.readFileSync(src);
const t = JSON.parse(raw);
const snap = {
  schema: 'deep32-d32library-category-art-snapshot/1',
  source: src.split(path.sep).join('/'),
  source_sha256: crypto.createHash('sha256').update(raw).digest('hex'),
  source_schema: t.schema, source_status: t.status, source_date: t.date,
  colour_rules: t.colour_rules,
  categories: t.categories.map(c => ({
    id: c.id, order: c.order, menu: c.menu, label: c.label, bar_label: c.bar_label,
    face: c.colour.face, ink: c.colour.ink, face_entry: c.colour.face_entry, ink_entry: c.colour.ink_entry, emblem: c.emblem, watermark: c.watermark,
    subcategories: c.subcategories.length,
  })),
};
const out = path.join(__dirname, '..', 'data', 'taxonomy-snapshot.json');
fs.writeFileSync(out, JSON.stringify(snap, null, 1) + '\n');
console.log(`${out}: ${snap.categories.length} categories from ${snap.source} (${snap.source_sha256.slice(0, 12)})`);
