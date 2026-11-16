'use strict';
// The genuine originals the PRIVATE build adopts (update item 43).
// This file names WHERE the originals are and WHERE they install; it holds no vendor data. The files themselves come
// from the owner's own discs, extracted read-only, and live only in private folders outside this tree (D32_HP_FILES
// and D32_AIX_FILES name them; there is no default). tools/gen.js --private copies them into
// a private output tree OUTSIDE this directory (it refuses a path inside it); the public build never reads them, and
// validate.js T-ORIG checks that no original's bytes appear in the public output.
//
// Roots (override with the environment): HP = the HP-UX lane's files/ tree, AIX = the AIX lane's files/ tree.
// Each set: from (ROOT:relative dir), match (file name filter), to (system install dir), user (the same at user level,
// what the VM proof installs as $HOME), and per-file rename/skip rules. `why` is the evidence (hp-diff.js reports it).
const roots = {
  HP: process.env.D32_HP_FILES || '',
  AIX: process.env.D32_AIX_FILES || '',
};
const H11 = 'HP:hpux1100/desktop', H9 = 'HP:hpux905/desktop';
const THEME = 'usr/local/share/d32/theme';

// CDE names a VUE file must not shadow (the package's palettes and backdrops, matched without the extension). A VUE
// palette or backdrop whose name is taken is installed under NAME1993 (VUE 3.0's year), unless it is identical to
// the CDE file, in which case CDE's copy serves both.
const VUE_RENAME_SUFFIX = '1993';

const sets = [
  // ---- HP CDE (HP-UX 11.00, the release the captures show; 10.20 is pixel-identical apart from 9 palettes it lacks)
  { id: 'hp-palettes', from: `${H11}/usr/dt/share/palettes`, match: /\.dp$/, to: 'etc/dt/palettes', user: '.dt/palettes',
    licence: 'HP original (HP-UX 11.00 CDE), private build only', why: 'value-identical to the theme goldens; the genuine files' },
  { id: 'hp-backdrops', from: `${H11}/usr/dt/share/backdrops`, match: /\.(pm|bm)$/, to: 'etc/dt/backdrops', user: '.dt/backdrops',
    licence: 'HP original (HP-UX 11.00 CDE), private build only', why: 'HP\'s 27 backdrops; the hp profile uses SkyLight, WaterDrops, SkyDark, Paver as HP\'s Dtwm does' },
  { id: 'hp-icons', from: `${H11}/usr/dt/appconfig/icons/C`, match: /\.(pm|bm)$/, to: 'etc/dt/appconfig/icons/C', user: '.dt/icons',
    licence: 'HP original (HP-UX 11.00 CDE), private build only', why: 'HP\'s icon set: 1,066 identical to the package, Dtlogo and DtCMa_m differ, 68 HP-only (media, audio, hpterm, coloured folders, print layouts)' },
  { id: 'hp-logo', from: `${H11}/usr/dt/appconfig/icons/C`, match: /^Dtlogo\.pm$/, to: `${THEME}/login`, user: 'greeter',
    rename: () => 'hp-cde-logo.pm', licence: 'HP original (HP-UX 11.00 dtlogin logo), private build only', why: 'the HP CDE login logo (hp profile greeter)' },
  { id: 'hp-sounds', from: `${H11}/opt/audio/sounds`, match: /\.au$/, to: 'usr/local/share/d32/sounds/hp', user: 'sounds/hp',
    rename: n => 'hp-' + n.replace(/_/g, '-'), convert: 'wav48k', licence: 'HP original (HP-UX 11.00 audio demo), private build only',
    why: 'HP\'s own audio files (no HP desktop played event sounds: the X bell only); the .au and a 48 kHz WAV for d32sndd' },
  // ---- HP VUE 3.0 (HP-UX 9.05): the Classic VUE profile
  { id: 'vue-palettes', from: `${H9}/usr/vue/palettes`, match: /\.vp$/, to: 'etc/dt/palettes', user: '.dt/palettes', vueNames: 'palettes',
    rename: n => n.replace(/\.vp$/, '.dp'), licence: 'HP original (HP VUE 3.0, HP-UX 9.05), private build only', why: 'VUE\'s 28 palettes, same 8-line format as .dp' },
  { id: 'vue-backdrops-colour', from: `${H9}/usr/vue/icons/Vuebackdrops/Color`, match: /\.pm$/, to: 'etc/dt/backdrops', user: '.dt/backdrops', vueNames: 'backdrops',
    skip: /^NoBackdrop\.pm$/, licence: 'HP original (HP VUE 3.0), private build only', why: 'VUE\'s colour backdrops (VUE prefers them on a colour display)' },
  { id: 'vue-backdrops-bitmap', from: `${H9}/usr/vue/icons/Vuebackdrops`, match: /\.bm$/, to: 'etc/dt/backdrops', user: '.dt/backdrops', vueNames: 'backdrops',
    skipIfColour: true, licence: 'HP original (HP VUE 3.0), private build only', why: 'VUE\'s bitmap backdrops, deepBackgnd (VUE\'s default) among them' },
  { id: 'vue-logo', from: `${H9}/usr/vue/icons/Vuelogin`, match: /^HPlogo\.bm$/, to: `${THEME}/login`, user: 'greeter',
    rename: () => 'hp-vue-logo.bm', licence: 'HP original (HP VUE 3.0 login), private build only', why: 'the VUE login wordmark (vue profile greeter)' },
  { id: 'vue-fonts', from: 'HP:hpux905/bdf/usr/lib/X11/fonts/iso_8859.1/75dpi', match: /^(system|user)\d+(x\d+)?\.bdf$/,
    to: 'usr/local/share/fonts/hpvue/src', user: 'fonts/hpvue', licence: 'HP original (HP-UX 9.05 fonts, converted PCF to BDF), private build only',
    why: 'VUE\'s desktop faces: HP Swiss 742 (system) and Prestige (user), ISO 8859-1' },
  { id: 'vue-sound', from: `${H9}/usr/audio/examples/ademo.tar.Z.d/editor/src`, match: /^welcome\.au$/, to: 'usr/local/share/d32/sounds/hp', user: 'sounds/hp',
    rename: () => 'hp-welcome-1993.au', convert: 'wav48k', licence: 'HP original (HP-UX 9.05 audio demo), private build only', why: 'the 1993 recording' },
  // ---- best of AIX (AIX 4.3.3, IBM CDE 1.0; AIX-REFERENCE.md section 4)
  { id: 'aix-palettes', from: 'AIX:X11.Dt/usr/dt/palettes', match: /^(Cactus|Cardamon|Fennel|Sage)\.dp$/, to: 'etc/dt/palettes', user: '.dt/palettes',
    licence: 'IBM original (AIX 4.3.3 CDE), private build only', why: 'A6: the four IBM palettes with new values' },
  { id: 'aix-default-contrast', from: 'AIX:X11.Dt/usr/dt/palettes', match: /^Default\.dp$/, to: 'etc/dt/palettes', user: '.dt/palettes',
    rename: () => 'DefaultContrast.dp', licence: 'IBM original (AIX 4.3.3 Default), private build only',
    why: 'A1: HP Default with a darker text-area set (CS4 #505665, white text 7.34:1 against 5.02:1); offered, not the default' },
  { id: 'aix-backdrops', from: 'AIX:X11.Dt/usr/dt/backdrops', match: /^(CobbleRound|DarkPaper|Distortion|LatticeLite|LeafBright)\.pm$/,
    to: 'etc/dt/backdrops', user: '.dt/backdrops', licence: 'IBM original (AIX 4.3.3 CDE, (C) IBM 1995), private build only',
    why: 'A7: five IBM backdrops that recolour with the palette (Leaf and Fleecy use fixed colours and are left out)' },
];

module.exports = { roots, sets, VUE_RENAME_SUFFIX, THEME };
