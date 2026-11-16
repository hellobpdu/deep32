#!/usr/bin/env node
'use strict';
// Deep32/UX theme generator (d32-theme, d32-fonts-hp). Reads src/theme-data.js and the golden palette JSON and writes
//   out/<release>/root/   the system install tree, rooted at / (what the packages and install.site place), + MANIFEST.tsv
//   out/<release>/user/   the same look as a USER-level CDE configuration ($HOME/.dt, ~/.Xdefaults, app-defaults for
//                         XUSERFILESEARCHPATH, a private font directory): what the VM proof installs for d32build
//   out/<release>/test/   text goldens and shell fixtures for validate.js
// Usage: node tools/gen.js [--release D.32.12|...|all] [--profile cde10|cde21|hp|vue] [--palette Default] [--out DIR]
//                           [--private [DIR]]
// --private (update item 43): the PRIVATE build. It adds the genuine HP and IBM originals named in src/originals.js
// (HP's palettes, backdrops, icons, login logos, VUE fonts and sounds; the best of AIX), offers the hp and vue profiles
// and boots the hp profile (theme-data privateProfile). It writes OUTSIDE this tree, to
// <D32_PRIVATE_OUT>/<release> or --private DIR (there is no default), and refuses any path inside it: the originals
// never enter a git repository, a pack or anything shared.
// Deterministic: the same inputs give byte-identical output (no clocks, no randomness).
const fs = require('fs');
const path = require('path');
const { ROOT, Tree, due, loadPalettes, sets } = require('./gen/common');
const cde = require('./gen/cde');
const dt = require('./gen/dtfiles');
const art = require('./gen/art');
const fonts = require('./gen/fonts');
const bridge = require('./gen/bridge');
const sysv = require('./gen/sysv');
const look = require('./gen/look');
const vuedeep = require('./gen/vuedeep');
const colour = require('./lib/colour');
const PRIVATE_OUT = process.env.D32_PRIVATE_OUT || '';   // the private build's output folder: no default
const inside = (p, dir) => { const r = path.relative(path.resolve(dir), path.resolve(p)); return !r.startsWith('..') && !path.isAbsolute(r); };

function args(argv) {
  const a = { release: 'all', profile: null, palette: null, out: null, private: false };
  for (let i = 2; i < argv.length; i++) {
    const k = argv[i];
    if (k === '--release') a.release = argv[++i];
    else if (k === '--profile') a.profile = argv[++i];
    else if (k === '--palette') a.palette = argv[++i];
    else if (k === '--out') a.out = argv[++i];
    else if (k === '--private') { a.private = true; if (argv[i + 1] && !argv[i + 1].startsWith('--')) a.out = argv[++i]; }
    else throw new Error('unknown argument ' + k);
  }
  return a;
}

const ICON_DIR = 'etc/dt/appconfig/icons/C';
const TYPES_DIR = 'etc/dt/appconfig/types/C';
const APPMGR_DIR = 'etc/dt/appconfig/appmanager/C';
const FONT_LICENCE = 'LGPL-2.1 code / CC BY-SA 3.0 media (The Open Group)';
const DTWMRC_LICENCE = 'LGPL-2.1 (CDE 2.5.3 sys.dtwmrc, The Open Group) with AGPL-3.0-or-later (Deep32) additions';
const THEME = 'usr/local/share/d32/theme';
const LOCALES = ['C', 'en_US.UTF-8'];

function build(opt = {}) {
  const d = require('../src/theme-data');
  const release = opt.release || 'all';
  if (release !== 'all' && !d.TRAINS.includes(release)) throw new Error('unknown release ' + release);
  const priv = !!opt.private;
  const prof = opt.profile || (priv ? d.privateProfile : d.defaultProfile);
  if (!d.profiles[prof]) throw new Error('unknown profile ' + prof);
  if (d.profiles[prof].private && !priv) throw new Error(`profile ${prof} uses HP's own files: build it with --private (update item 43)`);
  const palName = opt.palette || d.profiles[prof].palette || d.palettes.default;
  const dd = Object.assign({}, d, { release: release === 'all' ? d.release : release });
  const outDir = opt.out || (priv ? path.join(PRIVATE_OUT, release) : path.join(ROOT, 'out', release));
  // the originals (private build only): resolved before anything is written, so a missing source stops the build
  let orig = null;
  if (priv) {
    if (!opt.out && !PRIVATE_OUT) throw new Error('--private needs an output folder outside this tree: --private DIR or D32_PRIVATE_OUT');
    if (inside(outDir, ROOT)) throw new Error(`--private writes HP's own files: ${outDir} is inside the theme tree; use a folder outside it`);
    const golden = JSON.parse(fs.readFileSync(path.join(ROOT, 'src', 'palettes', 'cde-palettes.json'), 'utf8'));
    orig = require('./gen/originals').collect(Object.assign({ golden }, opt.roots ? { roots: opt.roots } : {}));
    if (orig.missing.length) throw new Error('private build: originals not found:\n  ' + orig.missing.join('\n  '));
  }
  const palettes = loadPalettes(d, orig ? orig.addedPalettes : null);
  if (!palettes[palName]) throw new Error('unknown palette ' + palName);
  const root = new Tree(path.join(outDir, 'root'), {});
  const user = new Tree(path.join(outDir, 'user'), {});
  const test = new Tree(path.join(outDir, 'test'), {});
  const S = (rel, txt, meta) => root.write(rel, txt, Object.assign({ install: 'sample' }, meta));
  const M = (rel, buf, meta) => root.write(rel, buf, Object.assign({ install: 'file' }, meta));

  // ---- palettes: stock palettes come from the package; originals are ours ---------------------------------
  for (const p of Object.values(palettes).filter(p => !p.stock && !p.original)) {
    M(`etc/dt/palettes/${p.name}.dp`, cde.dp(p), { note: 'original palette (DISTRO 6.1)' });
    user.write(`.dt/palettes/${p.name}.dp`, cde.dp(p));
  }

  // ---- resources, window manager, greeter, session scripts ------------------------------------------------
  const sysRes = cde.sysResources(dd, prof, palName);
  // dtsession_res reads /etc/dt/config/$LANG/sys.resources only (no fallback to C), and the session locale is decided
  // by ISO 9.2.2's P1 matrix (C or en_US.UTF-8), so the overlay and the dtwmrc are placed for both.
  for (const loc of LOCALES) S(`etc/dt/config/${loc}/sys.resources`, sysRes, { note: `overlay, profile ${prof}, palette ${palName}` });
  for (const p of Object.keys(d.profiles).filter(p => priv || !d.profiles[p].private)) {
    M(`${THEME}/profiles/${p}.res`, cde.profileRes(dd, p).join('\n') + '\n', { note: 'look profile overlay (d32-look)' });
    M(`${THEME}/profiles/sys.resources.${p}`, cde.sysResources(dd, p, palName), { note: 'full overlay per profile' });
  }
  // the dtwmrc is CDE 2.5.3's sys.dtwmrc with our additions (its header says so), so it keeps CDE's licence
  const WMRC_LIC = { licence: DTWMRC_LICENCE };
  for (const loc of LOCALES) S(`etc/dt/config/${loc}/sys.dtwmrc`, cde.dtwmrc(dd, release), Object.assign({ note: 'window manager' }, WMRC_LIC));
  M(`${THEME}/dtwm/sys.dtwmrc.vm`, cde.dtwmrc(dd, release, { vm: true }), Object.assign({ note: 'with Ctrl+Alt+1..4 for a VM console' }, WMRC_LIC));
  S(cde.GREETER_FILE, cde.greeter(dd, prof, palName, palettes), { note: 'dtlogin greeter' });
  S('etc/dt/config/Xsession.d/0010.d32hello', cde.hello(dd), { mode: '0555', note: 'dthello screen' });
  S('etc/dt/config/Xsession.d/0110.d32env', cde.env(dd), { mode: '0555', note: 'toolkit environment' });
  S('etc/dt/config/Xsession.d/0120.d32xsettings', cde.xsettingsStart(dd), { mode: '0555', note: 'xsettingsd start' });
  S('etc/dt/config/C/d32.copyright', cde.copyright(dd), { note: 'dthello text' });

  // ---- Desktop Look (d32-look): the looks this build offers, the dialog, the greeter helper (tools/gen/look.js) -------
  // The build's own profile is the default look; every look carries its resources, colour-server pixel sets and greeter.
  const lookList = look.looksFor(d, priv);
  if (!lookList.some(l => l.id === prof)) throw new Error(`profile ${prof} is not one of the looks this build offers (${lookList.map(l => l.id).join(', ')})`);
  const lookPal = id => id === prof ? palName : look.lookPalette(d, id);
  const lookTexts = {};
  for (const { id, label } of lookList) lookTexts[id] = look.lookRes(dd, id, label, id === prof, lookPal(id));
  const lookFiles = { 'looks.conf': look.looksConf(dd, priv, prof, lookPal), 'managed.keys': look.managedKeys(Object.values(lookTexts)).join('\n') + '\n' };
  for (const { id } of lookList) {
    const pal = palettes[lookPal(id)];
    if (!pal) throw new Error(`look ${id}: palette ${lookPal(id)} is not in this build`);
    lookFiles[`${id}/look.res`] = lookTexts[id];
    lookFiles[`${id}/pixelset`] = look.pixelSetFile(id, pal);
    lookFiles[`${id}/greeter`] = cde.greeter(dd, id, pal.name, palettes);
  }
  for (const [f, txt] of Object.entries(lookFiles)) M(`${THEME}/looks/${f}`, txt, { note: 'Desktop Look data (d32-look)' });
  M('usr/local/d32/bin/d32-look', look.SCRIPT, { mode: '0555', note: 'Desktop Look dialog (dtksh), the D32LookProfile action' });
  M('usr/local/d32/sbin/d32-look-greeter', look.GREETER_HELPER, { mode: '0555',
    note: 'root helper for the login screen look; doas rule for the BASE lane: permit nopass :wheel cmd /usr/local/d32/sbin/d32-look-greeter' });

  // ---- front panel and actions --------------------------------------------------------------------------------
  const problems = [];
  const fpText = dt.frontPanel(dd, release, problems);
  S(`${TYPES_DIR}/deep32.fp`, fpText, { note: 'front panel' });
  for (const f of dt.DT_FILES) S(`${TYPES_DIR}/${f}`, dt.dtFile(dd, release, f), { note: 'actions and data types' });
  const layouts = { single: ['+0-0', 'single head: centred at the bottom (dtwm default)'],
    docked: ['+1470-0', 'docked: the 34-inch head, bottom centre'] };
  for (const [k, [g, note]] of Object.entries(layouts)) M(`${THEME}/fp/geometry-${k}.fp`, dt.panelGeometry(dd, g, note), { note });
  // Application Manager groups: one action file per entry (DISTRO 6.8, EP9)
  const appGroups = dt.appManager(dd, release);
  for (const [g, entries] of Object.entries(appGroups)) for (const a of entries)
    S(`${APPMGR_DIR}/${g}/${a}`, dt.ACTION_FILE, { mode: '0555', note: `Application Manager entry, group ${g}` });

  // ---- icons, backdrops, login emblem ---------------------------------------------------------------------------
  const icons = art.icons();
  for (const [name, txt] of Object.entries(icons)) S(`${ICON_DIR}/${name}`, txt, { licence: 'CC-BY-NC-4.0 (Deep32), DRAFT art', note: 'draft icon' });
  const bds = art.backdrops();
  for (const [name, txt] of Object.entries(bds)) S(`etc/dt/backdrops/${name}`, txt, { note: 'original backdrop' });
  M(`${THEME}/login/emblem-224.xpm`, art.emblem(), { note: 'login emblem (draft)' });

  // ---- the genuine originals (private build only, update item 43) -------------------------------------------------
  // Each file is copied byte for byte from the owner's own discs (the HP-UX and AIX lanes' read-only extraction); the
  // manifest records its source path and the source's SHA-256. A .au also gets a 48 kHz WAV for d32sndd.
  const origCount = {};
  if (orig) {
    for (const f of orig.files) {
      M(f.sys, f.buf, { licence: f.licence, note: `original ${f.set}: ${f.src.replace(/\\/g, '/')} sha256 ${f.srcSha256}${f.derived ? '; ' + f.derived : ''}` });
      user.write(f.user, f.buf, { licence: f.licence });
      origCount[f.set] = (origCount[f.set] || 0) + 1;
    }
    S('etc/X11/xorg.conf.d/11-d32-hpvue-fonts.conf', HPVUE_XORG, { note: 'font path for the vue profile faces (private build)' });
    // the VUE login wordmark re-set for dtgreet's 245 px logo frame: HP's pixels moved, not redrawn (tools/gen/vuelogo.js)
    const vl = orig.files.find(f => f.set === 'vue-logo');
    if (vl) {
      const txt = require('./gen/vuelogo').stacked(vl.buf);
      M(`${THEME}/login/hp-vue-logo-stacked.bm`, txt, { licence: vl.licence, note: `derived from ${vl.src.replace(/\\/g, '/')} sha256 ${vl.srcSha256}: the wordmark in two rows for the 245 px greeter frame` });
      user.write('greeter/hp-vue-logo-stacked.bm', txt, { licence: vl.licence });
    }
    // VUE's own workspace backdrop, drawn from the model fitted to the genuine 9.05 screens (tools/gen/vuedeep.js)
    for (const [n, txt] of Object.entries(vuedeep.backdrops())) {
      M(`etc/dt/backdrops/${n}`, txt, { licence: 'private build only (HP VUE 3.0 design, item 43)', note: 'VUE deep backdrop at one screen size' });
      user.write(`.dt/backdrops/${n}`, txt, { licence: 'private build only' });
    }
    M(`${THEME}/sound/originals.conf`, SOUND_ORIGINALS, { note: 'HP audio files, not mapped to events' });
  }

  // ---- fonts ---------------------------------------------------------------------------------------------------------
  const F = fonts.build();
  for (const f of F.files) M(`usr/local/share/fonts/d32hp/src/${f.name}`, f.text,
    { licence: FONT_LICENCE, charset: 'latin1', note: 'compiled to PCF (bdftopcf) and OTB (fonttosfnt) when packaged; tools/vm/install-user.sh does the same for the proof' });
  M('usr/local/share/fonts/d32hp/fonts.alias', F.alias, { note: '-dt-interface aliases' });
  S('etc/X11/xorg.conf.d/10-d32-fonts.conf', F.xorg, { note: 'font path' });
  S('etc/fonts/conf.d/60-d32-bitmap.conf', F.fontconfig, { note: 'fontconfig' });

  // ---- toolkit bridge: GTK 3, GTK 2, Qt, Xt, xsettingsd -----------------------------------------------------------
  const P = d.profiles[prof]; const profObj = Object.assign({ id: prof }, P);
  const themeNames = [];
  for (const p of Object.values(palettes).filter(p => !p.bw)) {
    for (const use of ['HIGH_COLOR', 'MEDIUM_COLOR']) {
      const s = sets(p, use);
      const name = `Deep32-CDE-${p.name}-${use.replace('_COLOR', '')}`;
      themeNames.push(name);
      const td = `usr/local/share/themes/${name}`;
      M(`${td}/index.theme`, bridge.indexTheme(name, p.name, use, profObj));
      M(`${td}/gtk-3.0/gtk.css`, bridge.gtk3(name, s, profObj));
      for (const [f, buf] of Object.entries(bridge.assets(s, profObj))) M(`${td}/gtk-3.0/assets/${f}`, buf);
      M(`${td}/gtk-2.0/gtkrc`, bridge.gtk2(name, s, profObj));
      M(`${THEME}/qt/${name}.conf`, bridge.qtColours(name, s));
      M(`${THEME}/qt/${name}.qss`, bridge.qss(name, s, profObj, `/${td}/gtk-3.0/assets`));
      M(`${THEME}/xt/${name}.ad`, bridge.xt(name, s));
    }
  }
  const defTheme = `Deep32-CDE-${palName}-${d.palettes.colourUse.replace('_COLOR', '')}`;
  M(`${THEME}/xsettingsd.conf`, bridge.xsettings(defTheme), { note: 'build-time default theme' });

  // ---- System V face ---------------------------------------------------------------------------------------------
  M('usr/local/d32/lib/d32-banner.sh', sysv.bannerLib(dd), { mode: '0444', note: 'console banner functions' });
  S('etc/issue', sysv.issue(dd), { note: 'console issue' });
  M(`${THEME}/sysv/gettytab.default`, sysv.gettytab(dd), { note: 'default entry for /etc/gettytab' });
  M(`${THEME}/sysv/apply-gettytab.sh`, sysv.GETTY_APPLY, { mode: '0555', note: 'idempotent applier' });
  S('etc/login.conf.d/d32', sysv.loginClass(dd), { note: 'login class with the motd' });
  // goldens are byte-exact; the getty banner carries CR LF on purpose, so goldens are written as bytes
  for (const [k, txt] of Object.entries(sysv.goldens(dd))) test.write(`goldens/${k}`, Buffer.from(txt, 'utf8'));
  for (const [k, txt] of Object.entries(sysv.bannerFixture())) test.write(`fixtures/${k}.sh`, txt);

  // ---- the user-level configuration (the VM proof and any account that wants the look without /etc) -------------
  // dtwm reads ~/.dt/dtwmrc first; DtDbLoad reads ~/.dt/types before /etc/dt and the package (DtDbPathId order);
  // dtsession_res -load -system -xdefaults merges ~/.Xdefaults after sys.resources; the colour server, the icon
  // search path and the backdrop list look in ~/.dt/palettes, ~/.dt/icons and ~/.dt/backdrops first.
  user.write('.Xdefaults', sysRes);
  user.write('.dt/dtwmrc', cde.dtwmrc(dd, release, { vm: true }), WMRC_LIC);
  user.write('.dt/types/deep32.fp', fpText);
  // ~/.dt/appmanager is the first entry of DTAPPSEARCHPATH (dtsearchpath), gathered by dtappgather at login
  for (const [g, entries] of Object.entries(appGroups)) for (const a of entries) user.write(`.dt/appmanager/${g}/${a}`, dt.ACTION_FILE, { mode: '0555' });
  // a user-level install is self-contained: Desktop Look runs from ~/.dt/d32 (the action's $HOME is expanded by the
  // action service, as in any CDE action), with its data beside it
  for (const f of dt.DT_FILES) user.write(`.dt/types/${f}`, dt.dtFile(dd, release, f).replace(/^(\s+EXEC_STRING\s+)\$D32LBIN\/d32-look$/m, '$1$HOME/.dt/d32/bin/d32-look'));
  for (const [f, txt] of Object.entries(lookFiles)) user.write(`.dt/d32/looks/${f}`, txt);
  user.write('.dt/d32/bin/d32-look', look.SCRIPT, { mode: '0555' });
  for (const [name, txt] of Object.entries(icons)) user.write(`.dt/icons/${name}`, txt);
  for (const [name, txt] of Object.entries(bds)) user.write(`.dt/backdrops/${name}`, txt);
  // app-defaults through XUSERFILESEARCHPATH=$HOME/app-defaults/%N: the class resources of the Xt bridge, one file per
  // class, for the build palette (the palette follower merges the same text with xrdb on a palette change).
  const xtText = bridge.xt(defTheme, sets(palettes[palName], d.palettes.colourUse));
  const byClass = {};
  for (const line of xtText.split('\n')) { const m = /^([A-Za-z]+)\*/.exec(line); if (m) (byClass[m[1]] = byClass[m[1]] || []).push(line); }
  for (const [cls, lines] of Object.entries(byClass)) user.write(`app-defaults/${cls}`, `! ${cls}: Deep32/UX class resources (${defTheme}).\n${lines.join('\n')}\n`);
  user.write('app-defaults/Dtterm', ['! Dtterm: Deep32/UX terminal settings (DISTRO-DESIGN 6.7), user level.',
    ...d.session.filter(([k]) => k.startsWith('Dtterm*')).map(([k, v]) => `${k}:`.padEnd(32) + v), ''].join('\n'));
  for (const f of F.files) user.write(`fonts/d32hp/${f.name}`, f.text, { charset: 'latin1', licence: FONT_LICENCE });
  user.write('fonts/d32hp/fonts.alias', F.alias);
  user.write('greeter/9999.zz-d32', cde.greeter(dd, prof, palName, palettes));
  // the GTK bridge at user level: the build theme under ~/.themes, the faces for fontconfig (OTB built on the VM by
  // install-user.sh with fonttosfnt) and the same rendering rule as the system file
  {
    const s = sets(palettes[palName], d.palettes.colourUse);
    const td = `.themes/${defTheme}`;
    user.write(`${td}/index.theme`, bridge.indexTheme(defTheme, palName, d.palettes.colourUse, profObj));
    user.write(`${td}/gtk-3.0/gtk.css`, bridge.gtk3(defTheme, s, profObj));
    for (const [f, buf] of Object.entries(bridge.assets(s, profObj))) user.write(`${td}/gtk-3.0/assets/${f}`, buf);
    user.write(`${td}/gtk-2.0/gtkrc`, bridge.gtk2(defTheme, s, profObj));
    user.write('.config/fontconfig/fonts.conf', F.fontconfig.replace('<dir>/usr/local/share/fonts/d32hp</dir>', '<dir>~/fonts/d32hp-otb</dir>'));
    user.write('.config/gtk-3.0/settings.ini', `[Settings]
gtk-theme-name=${defTheme}
gtk-font-name=Deep32 System 13px
gtk-enable-animations=false
`);
  }
  user.write('sysv/d32-banner.sh', sysv.bannerLib(dd));

  require('./laptop-look').overlay(root);
  const hdr = `Deep32/UX theme ${release} (profile ${prof}, palette ${palName}, ${d.palettes.colourUse})${priv ? ', PRIVATE build with HP and IBM originals (item 43): never commit, pack or share' : ''}`;
  root.manifest(hdr); user.manifest(hdr + ', user level'); test.manifest(hdr + ', tests');
  // A small summary the validator and the VM scripts read.
  const summary = { release, profile: prof, palette: palName, colourUse: d.palettes.colourUse, defTheme,
    themes: themeNames.length, files: { root: root.files.length, user: user.files.length, test: test.files.length },
    icons: Object.keys(art.ICONS), sets: sets(palettes[palName], d.palettes.colourUse).slice(1), appGroups, problems,
    private: priv, panelColorSet: d.profiles[prof].panelColorSet, backdrops: d.workspaces.map(w => w.backdrop[prof]),
    fontsStock: !d.profiles[prof].fonts, originals: origCount, looks: lookList.map(l => l.id),
    wsColorSets: d.profiles[prof].wsColorSets || d.workspaces.map(w => w.cs) };
  if (priv) fs.writeFileSync(path.join(outDir, 'PRIVATE.txt'), 'PRIVATE (update item 43). This tree holds genuine HP and IBM files from the owner\'s own discs.\n' +
    'Never commit it to a git repository, put it in a pack, or send it anywhere. Generated by theme/tools/gen.js --private.\n');
  fs.writeFileSync(path.join(outDir, 'summary.json'), JSON.stringify(summary, null, 1) + '\n');
  return { outDir, summary, root, user, test, problems };
}

// the vue profile's faces: compiled to PCF with a fonts.dir by the packaging step (and by tools/vm/install-user.sh)
const HPVUE_XORG = `# Deep32/UX private build: HP VUE 3.0's desktop faces (HP Swiss 742, Prestige; ISO 8859-1) for the Classic VUE
# look profile. After the Deep32 faces, before the X11 defaults.
Section "Files"
    FontPath "/usr/local/share/fonts/hpvue/"
EndSection
`;
const SOUND_ORIGINALS = `# Deep32/UX private build: HP's own audio files (update item 43), in /usr/local/share/d32/sounds/hp as the
# original .au and a 48 kHz 16-bit mono WAV for d32sndd.
# HP's CDE and VUE played no sampled desktop sounds (every desktop sound was the X bell, LOOK-SOUND 8.1); these are
# the HP audio subsystem's demo files. They are not mapped to any event: which event, if any, is the owner's call.
#   hp-welcome        HP-UX 10.20 and 11.00 /opt/audio/sounds/welcome.au (1.97 s)
#   hp-stereo-test    HP-UX 11.00 /opt/audio/sounds/stereo_test.au (1.92 s, stereo mixed to mono)
#   hp-welcome-1993   HP-UX 9.05 audio demo welcome.au (2.55 s)
# To play one at login in the Classic sound profile, uncomment:
# classic.login = hp-welcome
`;

module.exports = { build };

if (require.main === module) {
  const a = args(process.argv);
  const r = build(a);
  if (a.private) console.log('d32-theme: PRIVATE build (HP and IBM originals, item 43): ' + Object.entries(r.summary.originals).map(([k, v]) => `${k} ${v}`).join(', '));
  const s = r.summary;
  // a due control or entry that has no way in is an error, never a silent drop
  for (const p of r.problems) console.error('d32-theme: ' + p);
  if (r.problems.length) process.exitCode = 1;
  console.log(`d32-theme ${s.release}: ${s.files.root} system files, ${s.files.user} user files, ${s.files.test} test files, ` +
    `${s.themes} toolkit themes; profile ${s.profile}, palette ${s.palette} -> ${path.relative(process.cwd(), r.outDir) || '.'}`);
}
