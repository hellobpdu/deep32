'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const [themeRoot, shipOverride, output, ...options] = process.argv.slice(2);
if (!themeRoot || !shipOverride || !output ||
    options.some((option) => !['--fathom', '--screen', '--wireshark'].includes(option)) ||
    new Set(options).size !== options.length) {
  throw new Error('Usage: node render-actions.cjs THEME-ROOT SHIP-OVERRIDE NEW-OUTPUT [--fathom] [--screen] [--wireshark]');
}
const fathom = options.includes('--fathom');

const theme = require(path.resolve(themeRoot, 'src/theme-data.js'));
const dt = require(path.resolve(themeRoot, 'tools/gen/dtfiles.js'));
const release = 'all';

const oldOverride = fs.readFileSync(shipOverride, 'utf8');
const oldPanel = fs.readFileSync(path.join(path.dirname(shipOverride), 'd32-ship.fp'), 'utf8');

const beforeGroups = dt.appManager(theme, release);
const desktopInventory = fs.readFileSync(path.join(__dirname, 'installed-desktop-tools.tsv'));
const desktopTools = desktopInventory.toString('utf8').trimEnd().split('\n').slice(5, -1).sort();
if (JSON.stringify(beforeGroups.Desktop_Tools) !== JSON.stringify(desktopTools)) {
  throw new Error('The U9 Desktop Tools roster differs from the installed inventory.');
}
const beforeMain = JSON.stringify(theme.frontpanel.main);
const changed = new Map([
  ['D32Capture', ['$D32BIN/d32capdlg', 'Show the Lab Capture setup status.']],
  ['D32Sound', ['$D32BIN/d32sound', 'Choose and preview desktop sound cues.']],
  ['AudioControl', ['$D32BIN/d32mix', 'Adjust the local audio device controls.']],
  ['D32Settings', ['$D32BIN/sam -f settings', 'Desktop appearance, sound and update status.']],
  ['D32SysAdmin', ['$D32BIN/sam', 'Read system information and preview update commands.']],
  ['D32AdminMenus', ['$DTBIN/dtterm -e $D32BIN/sam -t', 'Open the terminal administration menus.']],
  ['D32ScreenCapture', options.includes('--screen')
    ? ['$D32BIN/d32-screencap', 'Capture the desktop after checking window handling.']
    : ['/usr/local/d32/ship/libexec/d32-notyet "Screen Capture" "Desktop capture privacy checks are unavailable in this release."',
    'Screen Capture is held until the desktop handling policy is verified.']],
]);

const actions = theme.actions.map((action) => {
  if (!changed.has(action.name)) return action;
  const [exec, desc] = changed.get(action.name);
  return { ...action, exec, desc, window: 'NO_STDIO' };
});
for (const name of changed.keys()) {
  if (actions.filter((action) => action.name === name).length !== 1) {
    throw new Error('Action missing or ambiguous: ' + name);
  }
}

const clockTemplate = {
  type: 'COMMAND', window: 'NO_STDIO', file: 'deep32.dt', train: 'D.32.12',
  owner: 'U10 Desktop Tools', icon: 'Dtdgclk',
};
actions.push({
  ...clockTemplate, name: 'D32Clock', label: 'Clock (Analog)', icon: 'Dtclock',
  exec: '/usr/X11R6/bin/xclock -analog -update 60 -name d32clock',
  desc: 'Show the local time on an analog clock.',
}, {
  ...clockTemplate, name: 'D32ClockUTC', label: 'Clock (UTC)',
  exec: '/usr/bin/env TZ=UTC /usr/X11R6/bin/xclock -digital -strftime "%H:%M UTC  %a %d %b" -update 60 -name d32clockutc',
  desc: 'Show Coordinated Universal Time.',
}, {
  ...clockTemplate, name: 'Dtcalc', label: 'Calculator', icon: 'Dtcalc',
  owner: 'U10 stock Calculator catalog repair',
  exec: '/usr/local/d32/bin/d32-dtcalc',
  desc: 'Open the desktop Calculator.',
});
if (fathom) actions.push({
  ...clockTemplate, name: 'D32WebAccess', label: 'Fathom Web Access', icon: 'D32web',
  exec: '$DTBIN/dtterm -e $D32BIN/d32-web-access',
  desc: 'Show or change network access for Fathom.',
});

const appmanager = { ...theme.appmanager, groups: theme.appmanager.groups.map((group) => (
  group.name === 'Desktop_Tools'
    ? { ...group, entries: [...group.entries, 'D32Clock', 'D32ClockUTC', 'AudioControl', 'D32SysAdmin',
      'D32AdminMenus', ...(fathom ? ['D32WebAccess'] : [])] }
    : group
)) };
const frontpanel = { ...theme.frontpanel, subpanels: theme.frontpanel.subpanels.map((panel) => {
  if (panel.name !== 'D32AppsSubpanel' || panel.controls.some((control) => control.push === 'Dtcalc')) {
    return panel;
  }
  const pos = Math.max(...panel.controls.map((control) => control.pos)) + 1;
  return { ...panel, controls: [...panel.controls, {
    name: 'D32CalcCtl', type: 'icon', pos, icon: 'Dtcalc', label: 'Calculator',
    push: 'Dtcalc', train: 'D.32.12',
  }] };
}) };
const candidate = { ...theme, actions, appmanager, frontpanel };

const afterGroups = dt.appManager(candidate, release);
if (JSON.stringify(afterGroups.Network_Tools) !== JSON.stringify(beforeGroups.Network_Tools) ||
    JSON.stringify(candidate.frontpanel.main) !== beforeMain) {
  throw new Error('An existing Network Tools entry or main panel position changed.');
}

const problems = [];
const panelText = dt.frontPanel(candidate, release, problems);
if (problems.length) throw new Error(problems.join('\n'));
const actionText = dt.dtFile(candidate, release, 'deep32.dt')
  .replace(/^(ACTION (?:D32Clock(?:UTC)?|Dtcalc|D32WebAccess)\n\{\n)/gm, '$1  ARG_COUNT            0\n');

function block(source, kind, name) {
  const marker = '\n' + kind + ' ' + name + '\n{\n';
  const start = source.indexOf(marker);
  const end = source.indexOf('\n}\n', start);
  if (start < 0 || end < 0 || source.indexOf(marker, start + marker.length) !== -1) {
    throw new Error('Missing or duplicate generated record: ' + name);
  }
  return source.slice(start + 1, end + 3);
}

const browserBefore = block(oldOverride, 'ACTION', 'D32Web');
const browserAfter = browserBefore
  .replace(/^(\s+)(LABEL +).+$/m, '$1$2Fathom (Web Browser)')
  .replace(/^(  TYPE +COMMAND)$/m, '$1\n  ARG_COUNT            0')
  .replace(/^(  EXEC_STRING +).+$/m, '$1/d32/active/bin/d32-fathom')
  .replace(/^(  DESCRIPTION +).+$/m, '$1Browse in an isolated desktop window.');
if (browserBefore === browserAfter) throw new Error('The browser action did not change.');
let newOverride = fathom ? oldOverride.replace(browserBefore, browserAfter) : oldOverride.replace(
  'Fathom, the web browser, is not in this release yet. This workstation has no route to the internet: the Library, the encyclopedia and the RAG work offline.',
  'General browsing is not enabled in this release.',
);
if (newOverride === oldOverride) throw new Error('The browser placeholder preimage was not found.');
if (options.includes('--wireshark')) {
  for (const [name, args, description] of [
    ['D32Wireshark', '--empty', 'Open the isolated Wireshark viewer.'],
    ['D32WiresharkOpen', '-- "%Arg_1%"', 'Open the selected capture in the isolated Wireshark viewer.'],
  ]) {
    const before = block(oldOverride, 'ACTION', name);
    const after = before
      .replace(/^(  TYPE +COMMAND)$/m, `$1\n  ARG_COUNT            ${name === 'D32Wireshark' ? 0 : 1}`)
      .replace(/^(  EXEC_STRING +).+$/m, `$1/d32/active/bin/d32-wsopen ${args}`)
      .replace(/^(  DESCRIPTION +).+$/m, '$1' + description);
    if (before === after) throw new Error('The Wireshark action did not change: ' + name);
    newOverride = newOverride.replace(before, after);
  }
}
const overlayNames = [...changed.keys(), 'D32Clock', 'D32ClockUTC', 'Dtcalc', ...(fathom ? ['D32WebAccess'] : [])];
for (const name of overlayNames) {
  if (newOverride.includes('\nACTION ' + name + '\n')) {
    throw new Error('A generated U10 action already exists in the shipped override: ' + name);
  }
}
const overlays = overlayNames.map((name) => block(actionText, 'ACTION', name));
const panelOverlay = block(panelText, 'CONTROL', 'D32CalcCtl');

fs.mkdirSync(output);
for (const [relative, contents] of [
  ['types/C/d32-ship.dt', newOverride.trimEnd() + '\n\n# U10 desktop actions\n\n' + overlays.join('\n')],
  ['types/C/d32-ship.fp', oldPanel.trimEnd() + '\n\n# U10 Calculator shortcut\n\n' + panelOverlay],
  ...afterGroups.Desktop_Tools.filter((name) => !beforeGroups.Desktop_Tools.includes(name))
    .map((name) => ['appmanager/C/Desktop_Tools/' + name, dt.ACTION_FILE]),
]) {
  const destination = path.join(output, relative);
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.writeFileSync(destination, contents, { flag: 'wx' });
}
