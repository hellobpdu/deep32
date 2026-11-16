'use strict';
// HP VUE 3.0's default workspace backdrop, for the Classic VUE look (private build only).
// On HP-UX 9.05, Vuewm*backdrop*image names "@/usr/lib/X11/bitmaps/Vuebackdrops/deepBackground", a file the release does
// not ship (the vue lane looked on the installed system and in the 9.05 catalogue); what vuewm draws instead, in every
// workspace, is a full-screen picture of nested frames: a flat outer band, five stippled bands that darken inwards, and
// a solid centre, in two colours of the workspace's colour set (background and bottom shadow). This module draws that
// picture from a model fitted to the genuine screens: the vue lane booted HP-UX 9.05 on the keithgh1/hpux905-qemu
// build and captured workspaces Two to Six at 1280x1024 (the vue lane's captures 15 to 19). Every one
// of the 1,221,601 pixels those captures show outside windows and the front panel is reproduced (0 differ):
//   band = min(6, floor(x / 24), floor((W - 1 - x) / 24), floor(y / 16), floor((H - 1 - y) / 24))
//   pixel = TILES[band][(y mod 4) * 4 + (x mod 4)]  (1 = bottomShadowColor, 0 = background; screen-absolute phase)
// The top bands are 16 rows, the others 24 columns or rows, so the centre sits higher, above the front panel. On other
// screen sizes the bands keep their widths and the centre grows: VUE drew 1280x1024 here, the rest is inferred [I].
// Written as XPM with CDE's symbolic colours, so dtwm paints it in each workspace's own colour set, as vuewm did.
const xpm = require('../lib/xpm');

const BANDS = { left: 24, right: 24, top: 16, bottom: 24 };
const TILES = ['0000000000000000', '0000001000001000', '0000101000001010', '0101101001011010', '0101111101011111',
  '0111111111011111', '1111111111111111'];
// the screens Deep32 drives (DISTRO-DESIGN 6.13: the 34-inch head, the laptop panel, the VM and the 1280x800 fallback)
const SIZES = [[1280, 1024], [1280, 800], [1920, 1080], [3440, 1440]];
const name = (w, h, copy = '') => `VueDeep${copy}${w}x${h}`;
// dtwm caches a backdrop by the name in the resource, with the colours of the first workspace that loads it (the VM proof:
// four workspaces naming one file all drew in workspace One's colours). Four distinct names, two files: NAME and NAME.pm
// both resolve through the backdrop search path to the same file, so two copies give four names and every workspace is
// drawn in its own colour set, as vuewm did.
const wsName = (i, w, h) => [name(w, h), name(w, h) + '.pm', name(w, h, 'B'), name(w, h, 'B') + '.pm'][i % 4];

function bandAt(x, y, W, H) {
  return Math.min(6, Math.floor(x / BANDS.left), Math.floor((W - 1 - x) / BANDS.right), Math.floor(y / BANDS.top),
    Math.floor((H - 1 - y) / BANDS.bottom));
}
function rows(W, H) {
  const out = [];
  for (let y = 0; y < H; y++) {
    let r = '';
    for (let x = 0; x < W; x++) r += TILES[bandAt(x, y, W, H)][(y % 4) * 4 + (x % 4)] === '1' ? 'B' : 'b';
    out.push(r);
  }
  return out;
}
function backdrops() {
  const L = { b: { s: 'background', m: 'white' }, B: { s: 'bottomShadowColor', m: 'black' } };
  const out = {};
  for (const [W, H] of SIZES) for (const copy of ['', 'B']) out[`${name(W, H, copy)}.pm`] = xpm.write(name(W, H, copy), rows(W, H), L,
    `${name(W, H, copy)} (d32-theme, private build): HP VUE 3.0's default workspace backdrop as vuewm drew it on HP-UX 9.05,\n` +
    'nested stippled frames in the workspace colour set. Drawn from a model fitted pixel for pixel to the genuine screens\n' +
    '(tools/gen/vuedeep.js). PRIVATE (update item 43): never commit, pack or share.');
  return out;
}
// xrdb lines choosing the size by the screen width (cpp WIDTH, as the package sys.resources steps)
function resourceSteps(key, i = 0) {
  return ['# if WIDTH > 2560', `${key}: `.padEnd(34) + wsName(i, 3440, 1440), '# else', '# if WIDTH > 1600', `${key}: `.padEnd(34) + wsName(i, 1920, 1080),
    '# else', '# if HEIGHT < 900', `${key}: `.padEnd(34) + wsName(i, 1280, 800), '# else', `${key}: `.padEnd(34) + wsName(i, 1280, 1024),
    '# endif', '# endif', '# endif'];
}

module.exports = { backdrops, resourceSteps, bandAt, rows, TILES, BANDS, SIZES, name, wsName };
