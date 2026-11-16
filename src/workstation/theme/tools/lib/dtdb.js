'use strict';
// CDE "Dt database" reader for .dt (actions, data types) and .fp (front panel) files.
// Mirrors lib/DtSvc/DtUtil1/DbReader.c of CDE 2.5.3:
//   - leading white space ignored; empty lines and lines starting '#' are comments (also inside records);
//   - "set NAME=value" defines a variable; "set DtDbVersion=1.0" is allowed only before anything else;
//   - a record is "KEYWORD name", then "{" on its own line (text after it is ignored), fields, "}";
//   - a field line ending in "\" continues on the next line;
//   - "$NAME" / "${NAME}" expand from the file's set variables first, then the environment.
// Errors it reports are the ones DbReader.c reports (missing/unexpected '{', '}' without '{', incomplete record
// at EOF, DtDbVersion redefined) plus the field and value checks dtwm and DtSvc apply when converting records.

// keywords: the record keywords this consumer reads (DbReader's MatchKeyword); a line outside a record whose first word
// is not one of them is skipped silently by CDE, and reported here as a warning (our own files must have none).
const KEYWORDS = { fp: ['PANEL', 'BOX', 'SUBPANEL', 'SWITCH', 'CONTROL', 'ANIMATION'], dt: ['ACTION', 'DATA_ATTRIBUTES', 'DATA_CRITERIA'] };
function parse(src, file = '(dt)', kind = 'dt') {
  const recognised = KEYWORDS[kind];
  const lines = src.split('\n');
  const errors = [], warnings = [], records = [], vars = {};
  let versionCanBeSet = true, version = null;
  let i = 0;
  const err = (n, msg) => errors.push(`${file}:${n + 1}: ${msg}`);
  const strip = s => s.replace(/\r$/, '');
  while (i < lines.length) {
    const raw = strip(lines[i]); const t = raw.trim();
    if (t === '' || t[0] === '#') { i++; continue; }
    if (t.startsWith('set ')) {
      const m = /^set\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(t);
      if (!m) err(i, 'bad set line: ' + t);
      else if (m[1] === 'DtDbVersion') {
        if (!versionCanBeSet) err(i, 'DtDbVersion can only be set at the beginning of the file');
        else { version = m[2].trim(); if (version !== '1.0') err(i, 'invalid DtDbVersion ' + version); }
      } else vars[m[1]] = m[2].trim();
      versionCanBeSet = false; i++; continue;
    }
    versionCanBeSet = false;
    const hm = /^([A-Z_]+)(?:\s+(\S.*))?$/.exec(t);
    if (!hm || !recognised.includes(hm[1])) { warnings.push(`${file}:${i + 1}: line outside a record, skipped by CDE: ${t}`); i++; continue; }
    const rec = { type: hm[1], name: (hm[2] || '').trim(), line: i + 1, fields: [], file };
    i++;
    let state = 'AT_START', cont = null, contLine = 0, ok = true, closed = false;
    for (; i < lines.length; i++) {
      const r = strip(lines[i]); const s = r.trim();
      if (s === '' || s[0] === '#') continue;
      if (s.startsWith('set ')) { err(i, 'set inside a record'); continue; }
      if (s[0] === '{') {
        if (state !== 'AT_START') { err(i, `unexpected '{' in ${rec.type} ${rec.name}`); ok = false; }
        state = 'START'; continue;
      }
      if (state === 'AT_START') { err(i, `${rec.type} ${rec.name} is missing its '{'`); ok = false; break; }
      if (s[0] === '}') {
        if (cont !== null) { err(i, `${rec.type} ${rec.name}: continued field runs into '}'`); ok = false; }
        closed = true; i++; break;
      }
      // field line, possibly continued
      let body = s, continued = false;
      if (/\\\s*$/.test(body) && !/\\\\\s*$/.test(body)) { body = body.replace(/\\\s*$/, ''); continued = true; }
      if (cont !== null) { cont += body; } else { cont = body; contLine = i + 1; }
      if (!continued) {
        const fm = /^(\S+)(?:\s+([\s\S]*))?$/.exec(cont);
        rec.fields.push({ key: fm[1], value: (fm[2] || '').trim(), line: contLine });
        cont = null;
      }
    }
    if (!closed && ok) { err(rec.line - 1, `${rec.type} ${rec.name} is incomplete at end of file`); ok = false; }
    if (ok) records.push(rec);
  }
  return { file, version, vars, records, errors, warnings };
}

function expand(value, vars, env = {}) {
  return value.replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)/g, (m, a, b) => {
    const k = a || b; return vars[k] !== undefined ? vars[k] : env[k] !== undefined ? env[k] : m;
  });
}

// ---- field schemas -------------------------------------------------------------------------------------
// Front panel (programs/dtwm/DataBaseLoad.c keyword tables, Parse.c converters).
const BOOL = v => /^(true|false)$/i.test(v);                       // StringToBoolean lower-cases
const POS = v => /^(first|last)$/i.test(v) || /^\d+$/.test(v);     // StringToPositionHints
const INT = v => /^\d+$/.test(v);
const ANY = v => v.length > 0;
const GEOM = v => /^(\d+x\d+)?([+-]-?\d+[+-]-?\d+)?$/.test(v) && v.length > 0;   // XParseGeometry forms
const FP = {
  PANEL: { PANEL_GEOMETRY: GEOM, DISPLAY_HANDLES: BOOL, DISPLAY_MENU: BOOL, DISPLAY_MINIMIZE: BOOL,
    RESOLUTION: v => /^(high|medium|low|match_display)$/i.test(v), CONTROL_BEHAVIOR: v => /^(single_click|double_click)$/i.test(v),
    DISPLAY_CONTROL_LABELS: BOOL, SUBPANEL_UNPOST: BOOL, LOCKED: BOOL, HELP_STRING: ANY, HELP_VOLUME: ANY, HELP_TOPIC: ANY },
  BOX: { CONTAINER_NAME: ANY, POSITION_HINTS: POS, LOCKED: BOOL, DELETE: BOOL, HELP_STRING: ANY, HELP_VOLUME: ANY, HELP_TOPIC: ANY },
  SUBPANEL: { CONTAINER_NAME: ANY, CONTROL_INSTALL: BOOL, TITLE: ANY, LOCKED: BOOL, DELETE: BOOL, HELP_STRING: ANY, HELP_VOLUME: ANY, HELP_TOPIC: ANY },
  SWITCH: { CONTAINER_NAME: ANY, POSITION_HINTS: POS, NUMBER_OF_ROWS: INT, LOCKED: BOOL, DELETE: BOOL, HELP_STRING: ANY, HELP_VOLUME: ANY, HELP_TOPIC: ANY },
  CONTROL: { TYPE: v => /^(blank|busy|icon|client|clock|date|file)$/i.test(v),
    CONTAINER_TYPE: v => /^(BOX|SUBPANEL|SWITCH)$/.test(v),      // StringToControlContainerType: case-sensitive
    CONTAINER_NAME: ANY, POSITION_HINTS: POS, ICON: ANY, ALTERNATE_ICON: ANY, LABEL: ANY, PUSH_ACTION: ANY,
    PUSH_ANIMATION: ANY, DROP_ACTION: ANY, DROP_ANIMATION: ANY, PUSH_RECALL: BOOL,
    MONITOR_TYPE: v => /^(none|mail|file)$/i.test(v), CLIENT_NAME: ANY, CLIENT_GEOMETRY: GEOM, FILE_NAME: ANY,
    DATE_FORMAT: ANY, LOCKED: BOOL, DELETE: BOOL, HELP_STRING: ANY, HELP_VOLUME: ANY, HELP_TOPIC: ANY },
  ANIMATION: { ANIMATION: v => /^\S+(\s+\d+)?$/.test(v) },
};
// Actions (include/Dt/ActionDb.h) and data types (include/Dt/Dts.h).
const ACTION = { TYPE: v => /^(COMMAND|MAP|TT_MSG|DT_REQUEST|DT_NOTIFY)$/.test(v), LABEL: ANY, ARG_CLASS: ANY, ARG_MODE: ANY,
  ARG_TYPE: ANY, ARG_COUNT: v => /^([<>]?\d+|\*)$/.test(v), DESCRIPTION: ANY, ICON: ANY, INSTANCE_ICON: ANY, MAP_ACTION: ANY,
  EXEC_STRING: ANY, EXEC_HOST: ANY, CWD: ANY, WINDOW_TYPE: v => /^(TERMINAL|PERM_TERMINAL|NO_STDIO)$/.test(v), TERM_OPTS: ANY,
  TT_CLASS: ANY, TT_SCOPE: ANY, TT_OPERATION: ANY, TT_FILE: ANY };
const ACTION_PATTERN = /^(TT_ARG\d+_(MODE|VTYPE|REP_TYPE|VALUE)|DT_ARG\d+_VALUE|DT_REQUEST_NAME|DT_SVC|DT_NOTIFY_NAME|DT_NGROUP)$/;
const DATA_CRITERIA = { DATA_ATTRIBUTES_NAME: ANY, NAME_PATTERN: ANY, PATH_PATTERN: ANY, CONTENT: ANY, MODE: v => /^[!&|fdlrwx]+$/.test(v),
  LINK_PATH: ANY, LINK_NAME: ANY };
// DATA_ATTRIBUTES is an open set (vendors add attributes such as SUNV3_TYPE); the known ones are checked.
const DATA_ATTRIBUTES_KNOWN = { IS_SYNTHETIC: BOOL, LABEL: ANY, DESCRIPTION: ANY, DATA_HOST: ANY, ICON: ANY, INSTANCE_ICON: ANY,
  PROPERTIES: ANY, ACTIONS: v => /^[A-Za-z0-9_]+(,[A-Za-z0-9_]+)*$/.test(v), NAME_TEMPLATE: ANY, MODE_TEMPLATE: ANY,
  MOVE_TO_ACTION: ANY, COPY_TO_ACTION: ANY, LINK_TO_ACTION: ANY, IS_TEXT: BOOL, MEDIA: ANY, MIME_TYPE: v => /^[a-z]+\/[A-Za-z0-9.+-]+$/.test(v),
  MIME_TO_MEDIA_FILTER: ANY, MEDIA_TO_MIME_FILTER: ANY, IS_ACTION: BOOL, IS_EXECUTABLE: BOOL, TYPE_LABEL: ANY, SUNV3_TYPE: ANY };

function checkFields(db, kind) {
  const errs = [];
  for (const r of db.records) {
    let schema = null, open = false;
    if (kind === 'fp') schema = FP[r.type];
    else if (r.type === 'ACTION') schema = ACTION;
    else if (r.type === 'DATA_CRITERIA') schema = DATA_CRITERIA;
    else if (r.type === 'DATA_ATTRIBUTES') { schema = DATA_ATTRIBUTES_KNOWN; open = true; }
    if (!schema) { errs.push(`${db.file}:${r.line}: record type ${r.type} is not read by ${kind === 'fp' ? 'dtwm' : 'DtSvc'}`); continue; }
    if (!r.name) errs.push(`${db.file}:${r.line}: ${r.type} without a name`);
    else if (!/^[A-Za-z0-9_]+$/.test(r.name)) errs.push(`${db.file}:${r.line}: ${r.type} name "${r.name}" has characters outside [A-Za-z0-9_]`);
    const seen = new Set();
    for (const f of r.fields) {
      const chk = schema[f.key] || (r.type === 'ACTION' && ACTION_PATTERN.test(f.key) ? ANY : null);
      if (!chk) { if (!open || !/^[A-Z][A-Z0-9_]*$/.test(f.key)) errs.push(`${db.file}:${f.line}: ${r.type} ${r.name}: unknown field ${f.key}`); continue; }
      if (seen.has(f.key) && !(kind === 'fp' && r.type === 'ANIMATION')) errs.push(`${db.file}:${f.line}: ${r.type} ${r.name}: ${f.key} given twice`);
      seen.add(f.key);
      if (!chk(f.value)) errs.push(`${db.file}:${f.line}: ${r.type} ${r.name}: bad ${f.key} value "${f.value}"`);
    }
  }
  return errs;
}
const field = (r, k) => { const f = r.fields.find(x => x.key === k); return f ? f.value : undefined; };

module.exports = { KEYWORDS, parse, expand, checkFields, field, FP, ACTION };
