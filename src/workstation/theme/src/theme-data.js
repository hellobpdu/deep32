'use strict';
// Deep32/UX theme: the one source of truth the generator (tools/gen.js) reads.
// Every value cites the design note it comes from (the Deep32 design notes, which are not part of this tree).
// Strings a person reads obey DISTRO 6.14's forbidden-string gate (checked by validate.js).

const release = 'D.32.12';            // First light (REPLAN 1); the update step passes --release for later trains.

const identity = {
  os: 'Deep32/UX', node: 'deep32', model: 'D32/732', maker: 'Jeffrey A. Cummings', contact: 'https://github.com/hellobpdu', year: 2026,
  // SYSV-FLAVOUR 2.5: the locked footer, verbatim, broken after "content only ·" when narrower than 88 columns.
  notice: 'Deep32 Workstation · reference library and research desk',
  noticeLines: ['Deep32 Workstation ·', 'reference library and research desk'],
  notices: '/usr/local/share/doc/d32/NOTICE',
};

// Trains (REPLAN 1, ISO 9.4, DECISIONS C "25 Train: core (F0-F4) in D.32.14"). A control or action whose train is
// later than the release is not emitted; a main-panel position whose control is not yet due gets a blank cell, so
// the 13-position silhouette never changes and no control is dead (DISTRO 6.4 "No dead controls").
const TRAINS = ['D.32.12', 'D.32.13', 'D.32.14', 'D.32.15', 'D.32.16', 'D.32.17'];

// Look profiles (DISTRO 6.2; CDE-SPEC 2.0). cde10 is the build default (DECISIONS C, "Look for building").
const profiles = {
  cde10: {
    shownAs: 'Classic (10.x look)', thickness: 2,
    // the Motif 1.2 look on Motif 2.3.8: every CDE enable* off (sys.resources and dtlogin alike)
    enable: { enableThinThickness: 'False', enableEtchedInMenu: 'False', enableToggleVisual: 'False', enableToggleColor: 'False',
      enableDefaultButton: 'False', enableButtonTab: 'False', enableBtn1Transfer: 'False' },
    panelColorSet: 8,          // [I] measured only on 11i (LOOK-SOUND 2.1); DISTRO 6.1: per-profile once 10.20 is measured
    radio: 'diamond', check: 'filled',
  },
  cde21: {
    shownAs: 'Thin (11.x look)', thickness: 1,
    enable: { enableThinThickness: 'True', enableEtchedInMenu: 'True', enableToggleVisual: 'True', enableToggleColor: 'True',
      enableDefaultButton: 'True', enableButtonTab: 'True', enableBtn1Transfer: 'button2_transfer' },
    panelColorSet: 8, radio: 'round', check: 'check',
  },
  // ---- the genuine HP looks, update item 43 ("use anything and everything we can"): built only with --private, from
  // HP's own files on the owner's discs (src/originals.js; never in a git repository, a pack or anything shared).
  // hp: HP CDE exactly as HP-UX 10.20 and 11.00 ship it. Every value is HP's: the eleven enable switches of HP's
  // sys.resources (identical in 10.20 and 11.00, and live in the 11.00 session: hpux/captures/hpux1100/live-xrdb-query.txt),
  // the 1-px thickness of HP's dtlogin Xresources, HP's Dtwm workspace backdrops (SkyLight, WaterDrops, SkyDark,
  // Paver), the greeter as captured on 11.00 (01-dtlogin.png: #a8a8a8 dialog, #ef506f highlight, #7e7e7e fields, HP's
  // Dtlogo). Deep32's own controls, workspace names, keys and session values are kept.
  hp: {
    shownAs: 'HP CDE (genuine)', thickness: 1, private: true,
    lookBlurb: 'HP CDE as HP-UX 10.20 and 11.00 ship it: Default palette, thin bevels, etched menus, HP backdrops.',
    enable: { enableThinThickness: 'True', enableEtchedInMenu: 'True', enableToggleVisual: 'True', enableToggleColor: 'True',
      enableMenuInCascade: 'True', enableDefaultButton: 'True', enableButtonTab: 'True', enableBtn1Transfer: 'button2_transfer',
      enableMultiKeyBindings: 'True', enableDragIcon: 'True', enableCDEColorFactors: 'True' },
    panelColorSet: 8, radio: 'round', check: 'check', backdropDefault: 'SkyLight',
    greeter: { logo: '/usr/local/share/d32/theme/login/hp-cde-logo.pm', background: '#a8a8a8', foreground: 'black',
      highlightColor: '#ef506f', textBackground: '#7e7e7e', textForeground: 'black', logoBackground: '#a8a8a8', screenBackground: 'black',
      // HP's dtlogin Xresources (10.20 and 11.00) set these 1-px shadows explicitly
      thinWidgets: ['XmCascadeButton', 'XmDrawnButton', 'XmPushButton', 'XmPushButtonGadget', 'XmText', 'XmTextField'] },
  },
  // vue: "Classic VUE", HP VUE 3.0 as HP-UX 9.05 ships it, on dtwm. Matched to the genuine desktop: the vue lane booted
  // HP-UX 9.05 from the owner's discs on the keithgh1/hpux905-qemu build (2026-11-06) and captured the live session at
  // 1280x1024 (VUE-REFERENCE.md). Live facts used here: the palette
  // CoralReef (Style Manager's selection on a fresh system), the front panel on colour set 7 (Vuewm), the workspace
  // colour sets One to Four = 8, 7, 6, 5 (the switch buttons), the backdrop vuewm draws in every workspace (nested
  // stippled frames: tools/gen/vuedeep.js, fitted pixel for pixel), the faces of the live RESOURCE_MANAGER (sys.font's
  // Y_RESOLUTION > 3582 step: HP Swiss 742 Medium 19 and Prestige 19, here from HP's ISO 8859-1 set), the front panel
  // and icon faces of Vuewm's app-defaults (Swiss 742 Bold 17/16/13, Bold 16), the Motif 1.2 look (no CDE enable switch,
  // diamond toggles: every Style Manager dialog), the VUE login colours. Not reproduced: VUE's own front panel art
  // (the HP badge strip), six workspaces (Deep32 keeps four), the login's logo-above-the-box layout (dtgreet's is fixed:
  // the wordmark is re-set in two rows to fit its 245 px logo frame, HP's pixels unchanged).
  vue: {
    shownAs: 'Classic VUE', thickness: 2, private: true, palette: 'CoralReef',
    lookBlurb: 'HP VUE 3.0 as HP-UX 9.05 ships it: CoralReef, Swiss 742 and Prestige, Motif 1.2 bevels, the VUE backdrop.',
    enable: { enableThinThickness: 'False', enableEtchedInMenu: 'False', enableToggleVisual: 'False', enableToggleColor: 'False',
      enableMenuInCascade: 'False', enableDefaultButton: 'False', enableButtonTab: 'False', enableBtn1Transfer: 'False', enableDragIcon: 'False' },
    panelColorSet: 7, radio: 'diamond', check: 'filled', backdropDefault: 'VueDeep1280x1024', deepBackdrop: true,
    wsColorSets: [8, 7, 6, 5],
    fonts: {
      system: '-bitstream-swiss 742-medium-r-normal--19-140-85-85-p-110-iso8859-1',
      user: '-bitstream-prestige-medium-r-normal--19-160-72-72-m-110-iso8859-1',
      panelHigh: '-bitstream-swiss 742-bold-r-normal--17-140-85-85-p-100-iso8859-1',
      panelMedium: '-bitstream-swiss 742-bold-r-normal--16-120-72-72-p-94-iso8859-1',
      panelLow: '-bitstream-swiss 742-medium-r-normal--13-100-72-72-p-75-iso8859-1',
      icon: '-bitstream-swiss 742-bold-r-normal--16-120-72-72-p-94-iso8859-1',
      dir: 'hpvue',
    },
    // the wordmark re-set in two rows (tools/gen/vuelogo.js): dtgreet's logo frame is a fixed 245 x 245 and cut the 482-px original
    greeter: { logo: '/usr/local/share/d32/theme/login/hp-vue-logo-stacked.bm', background: '#a8a8a8', foreground: 'black',
      highlightColor: '#ef506f', textBackground: '#7e7e7e', textForeground: 'black', logoBackground: '#ef506f', screenBackground: 'black',
      greetingFont: '-adobe-times-medium-i-normal--18-*-*-*-p-*-iso8859-1',
      labelFont: '-bitstream-swiss 742-bold-r-normal--16-120-72-72-p-94-iso8859-1',
      textFont: '-bitstream-prestige-medium-r-normal--16-120-72-72-m-80-iso8859-1' },
  },
};
const defaultProfile = 'cde10';
// The private build (gen.js --private, update item 43) boots the genuine HP look. DECIDED by the owner 2026-11-06
// (update answers 2 and 5: "can we have the ability to pick themes between the hp-ux and then vue? If so, go", "hp look
// as default, VUE selectable"): HP-UX CDE is the default, HP VUE is chosen per user with Desktop Look (below).
const privateProfile = 'hp';

// Desktop Look (d32-look, THEME B8): the per-user chooser, reached from the Style Manager control's subpanel, the
// Workspace Menu and Application Manager. It offers the looks of the build it ships in: the private build the two
// genuine HP looks, the public build the two reconstructions. The build's own profile is the default and is labelled
// so. Each look carries its palette, faces, backdrops, Dtwm resources, front-panel colour set and login greeter.
const looks = {
  public: [{ id: 'cde10', label: 'Classic (10.x look)' }, { id: 'cde21', label: 'Thin (11.x look)' }],
  private: [{ id: 'hp', label: 'HP-UX CDE' }, { id: 'vue', label: 'HP VUE (Classic)' }],
};

// Palettes. Default (= Broica) at HIGH_COLOR (DISTRO 6.1). Slate is ISO's "Deep32VUE" renamed (DISTRO 6.1), eight
// backgrounds from the VUE colours the owner approved for the Windows Codex Console; derived colours by Motif's algorithm.
const palettes = {
  default: 'Default', colourUse: 'HIGH_COLOR',
  originals: { Slate: ['#6B7A8F', '#49535E', '#49535E', '#1C1F24', '#BDB8AC', '#BDB8AC', '#6B5B3E', '#34495E'] },
  bwOnly: ['Black', 'White', 'BlackWhite', 'WhiteBlack'],      // CDE-SPEC 1.1: refused outside B_W
  previewSet: ['Default', 'Slate', 'Crimson'],                 // goldens: Default, Slate, Crimson (DISTRO 6.13)
};

// Workspaces (DISTRO 6.6; DECISIONS C "workspaces Library, Lab, Study, Work"). Colour sets are dtwm's fixed map.
// hp: HP's Dtwm app-defaults (10.20 and 11.00); vue: the picture vuewm draws in every workspace (tools/gen/vuedeep.js),
// at the size of the screen (the 1280x1024 names stand for the set; the resources choose by WIDTH). Four names for two
// copies of the picture: dtwm caches a backdrop by its name with the first workspace's colours (tools/gen/vuedeep.js).
const workspaces = [
  { id: 'ws0', title: 'Library', cs: 3, backdrop: { cde10: 'KnitLight', cde21: 'Horizon', hp: 'SkyLight', vue: 'VueDeep1280x1024' } },
  { id: 'ws1', title: 'Lab', cs: 5, backdrop: { cde10: 'WaterDrops', cde21: 'WaterDrops', hp: 'WaterDrops', vue: 'VueDeep1280x1024.pm' } },
  { id: 'ws2', title: 'Study', cs: 6, backdrop: { cde10: 'Abyss1440', cde21: 'KnitLight', hp: 'SkyDark', vue: 'VueDeepB1280x1024' } },
  { id: 'ws3', title: 'Work', cs: 7, backdrop: { cde10: 'Paver', cde21: 'Paver', hp: 'Paver', vue: 'VueDeepB1280x1024.pm' } },
];

// Fixed safety colours (ISO 9.5, DISTRO 6.5, 6.9): never palette-derived.
const fixedColours = {
  tileGreen: '#7FBF7F', tileAmber: '#D9B04A', tileRed: '#D46A6A',
};

// Paths the actions call. Slot apps ride in /d32/active (ISO 4.1); packaged helpers in /usr/local/d32 (DISTRO 6.11);
// doas targets in /usr/local/d32/sbin (DISTRO 3.5). Written as DtDb variables at the top of each .dt file.
const paths = {
  D32BIN: '/d32/active/bin',
  D32LBIN: '/usr/local/d32/bin',
  D32SBIN: '/usr/local/d32/sbin',
  DTBIN: '/usr/local/dt/bin',
  DOAS: '/usr/bin/doas',
};

// ---------------------------------------------------------------------------------------------------------------
// Actions. owner = the lane that delivers the program the action runs (the contract the VM's "no dead controls"
// test checks). icon = Deep32 icon base name (drawn by tools/art.js as DRAFT art until the art lane, REPLAN B9).
// file = which .dt file carries it. exec uses $D32BIN etc. (DtDb set variables).
const A = (o) => Object.assign({ type: 'COMMAND', window: 'NO_STDIO', file: 'deep32.dt', train: 'D.32.12' }, o);
const actions = [
  // --- front-panel main controls and their pushes
  A({ name: 'D32Plan', label: 'Calendar', icon: 'FpCM', exec: '/usr/local/bin/plan', owner: 'APPS (pkg plan)',
      desc: 'Plan, the calendar. CDE\'s own calendar needs rpc.cmsd, which this system does not run.' }),
  A({ name: 'D32Status', label: 'Deep32 Status', icon: 'D32stat', exec: '$D32BIN/d32status', owner: 'SERVICES (status window)', train: 'D.32.13',
      desc: 'The state behind every status tile, with the time of its last change.' }),
  A({ name: 'Deep32Library', label: 'Deep32 Encyclopedia', icon: 'D32lib', exec: '$D32BIN/e26', owner: 'LIBRARY', train: 'D.32.14', file: 'library.dt',
      desc: 'Deep32 Encyclopedia, the reference library: opens at the Title or Home screen, or where you left it.' }),
  // --- Personal Applications (Text Editor subpanel)
  // Update item 41 (proposed, his to veto): the Deep32 chat is Deep32 Library's Ask room; one program to him, separate
  // processes underneath. The control and Mod4+A open the room; d32ask stays the broker binary behind it.
  A({ name: 'Deep32LibraryAsk', label: 'Ask', icon: 'D32ask', exec: '$D32BIN/e26 -ask', owner: 'LIBRARY (Ask room, item 41)', train: 'D.32.14', file: 'library.dt',
      // items 29 and 41: chat grounding is the RAG corpus only; Library-only content is never used (unless sent to RAG)
      desc: "Deep32 Encyclopedia's Ask room: questions to Codex, grounded on the RAG corpus only, with every cite verified." }),
  A({ name: 'D32NEdit', label: 'NEdit', icon: 'D32nedit', exec: '/usr/local/bin/nedit', owner: 'APPS (pkg nedit)',
      desc: 'The NEdit programmer\'s editor.' }),
  // --- Lab subpanel (position 8)
  A({ name: 'D32Wireshark', label: 'Wireshark', icon: 'D32wshk', exec: '$DOAS -u d32net $D32SBIN/wireshark-run', owner: 'APPS (pkg wireshark + wrapper)', train: 'D.32.13',
      desc: 'Wireshark in the lab network domain.' }),
  A({ name: 'D32WiresharkOpen', label: 'Open in Wireshark', icon: 'D32wshk', exec: '$DOAS -u d32net $D32SBIN/wireshark-run "%Arg_1%"', owner: 'APPS', train: 'D.32.13', argType: 'PCAP,PCAPNG',
      desc: 'Open this capture in Wireshark.' }),
  A({ name: 'D32Capture', label: 'Capture', icon: 'D32cap', exec: '$D32LBIN/d32-capture-dialog', owner: 'APPS (front end to d32-capture)', train: 'D.32.13',
      desc: 'Capture packets on a lab or bench interface into the Lab tray.' }),
  A({ name: 'OuiLookup', label: 'OUI Lookup', icon: 'D32oui', exec: '$D32BIN/d32oui', owner: 'APPS (oui)', train: 'D.32.15',
      desc: 'Find the maker of a MAC address.' }),
  A({ name: 'D32NetCalc', label: 'Network Calculator', icon: 'D32calc', exec: '$D32BIN/d32calc', owner: 'FEATURES R4', train: 'D.32.15',
      desc: 'Subnets, wildcards, summaries and VLSM.' }),
  // --- Deep32 Library subpanel (position 9)
  A({ name: 'D32Search', label: 'Library Search', icon: 'D32srch', exec: '$D32BIN/d32lib', owner: 'SEARCH (d32lib)',
      desc: 'Faceted search over the whole library, offline.' }),
  A({ name: 'D32SearchSelection', label: 'Search the Selection', icon: 'D32srch', exec: '$D32BIN/d32lib -primary', owner: 'SEARCH (d32lib)',
      desc: 'Library Search for the text you selected.' }),
  A({ name: 'D32Read', label: 'Reader', icon: 'D32read', exec: '$D32BIN/d32read', owner: 'LIBRARY (Reader)', train: 'D.32.14', file: 'library.dt',
      desc: 'Read PDF, EPUB, HTML and RFC text, with outline, search and cites.' }),
  A({ name: 'D32ReadFile', label: 'Open in Reader', icon: 'D32read', exec: '$D32BIN/d32read "%Arg_1%"', owner: 'LIBRARY (Reader)', train: 'D.32.14', file: 'library.dt',
      argType: 'PDF,EPUB,XHTML,HTML,RFCTXT', desc: 'Open this document in the Reader.' }),
  A({ name: 'Deep32LibraryAcquisitions', label: 'Acquisition', icon: 'D32acq', exec: '$D32BIN/e26 -acquisitions', owner: 'LIBRARY', train: 'D.32.14', file: 'library.dt',
      desc: 'Downloads and Send to RAG requests, with their state.' }),
  A({ name: 'Deep32LibraryPinpointer', label: 'Pinpointer', icon: 'D32srch', exec: '$D32BIN/e26 -pinpointer', owner: 'LIBRARY', train: 'D.32.14', file: 'library.dt',
      desc: 'Deep32 Encyclopedia\'s Pinpointer.' }),
  A({ name: 'Deep32LibraryOpenFile', label: 'Open in Deep32 Encyclopedia', icon: 'D32lib', exec: '$D32BIN/e26 "%Arg_1%"', owner: 'LIBRARY', train: 'D.32.14', file: 'library.dt',
      argType: 'RFCTXT,D32LIST,D32ORG', desc: 'Open this file in Deep32 Encyclopedia.' }),
  A({ name: 'D32InTray', label: 'In-Tray', icon: 'D32tray', exec: '$DTBIN/dtfile -folder /home/d32web/Downloads', owner: 'WEB (In-Tray)', train: 'D.32.17',
      desc: 'Files from the web, opened in their own domain.' }),
  // --- Desktop Style subpanel (position 10)
  A({ name: 'D32Sound', label: 'Sound', icon: 'D32snd', exec: '$D32LBIN/d32-sound', owner: 'THEME B12', train: 'D.32.13',
      desc: 'The sound profile: Silent, Classic, Workstation or Full.' }),
  A({ name: 'AudioControl', label: 'Audio Control', icon: 'D32mix', exec: '$D32LBIN/audioctl-dialog', owner: 'WEB/MEDIA', train: 'D.32.17',
      desc: 'Volume, devices and the microphone and camera switches.' }),
  // Desktop Look (update answers 2 and 5, 2026-11-06): the dtksh dialog tools/gen/look.js writes; per user, applied at once
  A({ name: 'D32LookProfile', label: 'Desktop Look', icon: 'D32prof', exec: '$D32LBIN/d32-look', owner: 'THEME B8 (d32-look, this lane)',
      desc: 'Choose the look of your desktop: palette, faces, backdrops, window manager and front panel, and the login screen.' }),
  A({ name: 'D32Settings', label: 'Deep32 Settings', icon: 'D32set', exec: '$D32LBIN/sam -f settings', owner: 'THEME B13', train: 'D.32.14',
      desc: 'Retrieval mode, egress, pairing, Web switch and updates.' }),
  A({ name: 'D32SysAdmin', label: 'System Administration', icon: 'D32sam', exec: '$D32LBIN/sam', owner: 'THEME B13', train: 'D.32.14',
      desc: 'Administration areas that show the exact command each step runs.' }),
  A({ name: 'D32AdminMenus', label: 'Administration Menus', icon: 'D32smit', window: 'TERMINAL', exec: '$D32LBIN/smitty', owner: 'THEME B13', train: 'D.32.14',
      desc: 'The same administration menus in a terminal, with the command log.' }),
  // --- Applications subpanel (position 11)
  A({ name: 'D32Web', label: 'Web Browser', icon: 'D32web', exec: '$DOAS -u d32web $D32SBIN/fathom-run', owner: 'WEB (Fathom)', train: 'D.32.17',
      desc: 'Fathom, the web browser, in its own domain. Off unless the Web switch is on.' }),
  A({ name: 'D32ImageViewer', label: 'Image Viewer', icon: 'D32img', exec: '/usr/local/bin/xv', owner: 'APPS (pkg xv)',
      desc: 'View images.' }),
  A({ name: 'D32ScreenCapture', label: 'Screen Capture', icon: 'D32scap', exec: '$D32LBIN/d32-screencap', owner: 'WEB/MEDIA', train: 'D.32.17',
      desc: 'Capture a window, an area or the screen.' }),
  // --- Help subpanel (position 12)
  A({ name: 'D32Help', label: 'Deep32 Workstation', icon: 'D32help', exec: '$DTBIN/dthelpview -helpVolume Deep32', owner: 'THEME B18', train: 'D.32.14',
      desc: 'Help for this workstation: the key card, every mode, lamp and status word.' }),
  // --- keys only (DISTRO 6.6)
  // --- data-type actions
  A({ name: 'D32NEditFile', label: 'Open in NEdit', icon: 'D32nedit', exec: '/usr/local/bin/nedit "%Arg_1%"', owner: 'APPS (pkg nedit)',
      argType: 'CONFIG,CONSOLE_LOG,URL,YANG', desc: 'Open this file in NEdit.' }),
  A({ name: 'D32TranscriptOpen', label: 'Open Transcript', icon: 'D32ask', exec: '$D32BIN/e26 -ask -transcript "%Arg_1%"', owner: 'LIBRARY (Ask room, item 41)', train: 'D.32.14',
      argType: 'D32TRANSCRIPT', desc: 'Open this Ask transcript in Deep32 Encyclopedia.' }),
  // --- trays (DISTRO 3.7): path first, one action, the viewer in the tray's domain
  A({ name: 'D32TrayOpen', label: 'Open Safely', icon: 'D32tray', exec: '$D32SBIN/open-untrusted "%Arg_1%"', owner: 'BASE (open-untrusted)', train: 'D.32.13', file: 'deep32-trays.dt',
      argType: 'D32TRAYWEB', desc: 'Open this file in the domain it came from, without network or programs.' }),
];

// "Open" bindings: ACTION Open, TYPE MAP, per data type (the CDE way to rebind; /etc is read before the package).
const openMaps = [
  { argType: 'PDF', to: 'D32ReadFile', file: 'library.dt', train: 'D.32.14' },       // stock PDF type, re-bound to the Reader
  { argType: 'HTML', to: 'D32ReadFile', file: 'library.dt', train: 'D.32.14' },      // DISTRO 3.7: HTML goes to the Reader
  { argType: 'EPUB', to: 'D32ReadFile', file: 'library.dt', train: 'D.32.14' },
  { argType: 'XHTML', to: 'D32ReadFile', file: 'library.dt', train: 'D.32.14' },
  { argType: 'RFCTXT', to: 'Deep32LibraryOpenFile', file: 'library.dt', train: 'D.32.14' },
  { argType: 'D32LIST', to: 'Deep32LibraryOpenFile', file: 'library.dt', train: 'D.32.14' },
  { argType: 'D32ORG', to: 'Deep32LibraryOpenFile', file: 'library.dt', train: 'D.32.14' },
  { argType: 'PCAP', to: 'D32WiresharkOpen', file: 'deep32-apps.dt', train: 'D.32.13' },
  { argType: 'PCAPNG', to: 'D32WiresharkOpen', file: 'deep32-apps.dt', train: 'D.32.13' },
  { argType: 'URL', to: 'D32NEditFile', file: 'fathom.dt', train: 'D.32.17' },
  { argType: 'CONFIG', to: 'D32NEditFile', file: 'deep32-apps.dt', train: 'D.32.16' },
  { argType: 'CONSOLE_LOG', to: 'D32NEditFile', file: 'deep32-apps.dt', train: 'D.32.16' },
  { argType: 'YANG', to: 'D32NEditFile', file: 'deep32-apps.dt', train: 'D.32.12' },
  { argType: 'D32TRANSCRIPT', to: 'D32TranscriptOpen', file: 'deep32.dt', train: 'D.32.14' },
  { argType: 'D32TRAYWEB', to: 'D32TrayOpen', file: 'deep32-trays.dt', train: 'D.32.13' },
];

// Data types. criteria: list of { name|path|content|mode }. MODE f = plain file (dtdtsfile(4)).
const datatypes = [
  { name: 'RFCTXT', file: 'library.dt', icon: 'D32rfc', actions: 'Open,D32ReadFile', train: 'D.32.14', mime: 'text/plain',
    desc: 'An RFC in plain text: opens at its article in Deep32 Encyclopedia.', criteria: [{ name: 'rfc[0-9]*.txt', mode: 'f' }] },
  { name: 'EPUB', file: 'library.dt', icon: 'D32read', actions: 'Open', train: 'D.32.14', mime: 'application/epub+zip',
    desc: 'An EPUB book: opens in the Reader.', criteria: [{ name: '*.epub', mode: 'f' }] },
  { name: 'XHTML', file: 'library.dt', icon: 'D32read', actions: 'Open', train: 'D.32.14', mime: 'application/xhtml+xml',
    desc: 'An XHTML document: opens in the Reader.', criteria: [{ name: '*.xhtml', mode: 'f' }] },
  { name: 'D32LIST', file: 'library.dt', icon: 'D32srch', actions: 'Open', train: 'D.32.14',
    desc: 'A saved Pinpointer list.', criteria: [{ name: '*.d32list', mode: 'f' }] },
  { name: 'D32ORG', file: 'library.dt', icon: 'D32note', actions: 'Open', train: 'D.32.14',
    desc: 'A Research Organizer file. From outside your library folder it opens read-only.', criteria: [{ name: '*.d32org', mode: 'f' }] },
  { name: 'PCAP', file: 'deep32-apps.dt', icon: 'D32pcap', actions: 'Open,D32WiresharkOpen', train: 'D.32.13', mime: 'application/vnd.tcpdump.pcap',
    desc: 'A packet capture: opens in Wireshark in the lab domain.', criteria: [{ name: '*.pcap', mode: 'f' }, { name: '*.cap', mode: 'f' }] },
  { name: 'PCAPNG', file: 'deep32-apps.dt', icon: 'D32pcap', actions: 'Open,D32WiresharkOpen', train: 'D.32.13',
    desc: 'A packet capture (pcapng): opens in Wireshark in the lab domain.', criteria: [{ name: '*.pcapng', mode: 'f' }] },
  { name: 'URL', file: 'fathom.dt', icon: 'D32web', actions: 'Open', train: 'D.32.17',
    desc: 'Web link', criteria: [{ name: '*.url', mode: 'f' }] },
  { name: 'CONFIG', file: 'deep32-apps.dt', icon: 'D32cfg', actions: 'Open,D32NEditFile', train: 'D.32.16',
    desc: 'Device configuration file',
    criteria: [{ name: '*.cfg', mode: 'f' }, { name: '*-confg', mode: 'f' }] },
  { name: 'CONSOLE_LOG', file: 'deep32-apps.dt', icon: 'D32cfg', actions: 'Open,D32NEditFile', train: 'D.32.16',
    desc: 'Console log', criteria: [{ name: '*.session.log', mode: 'f' }] },
  { name: 'YANG', file: 'deep32-apps.dt', icon: 'D32yang', actions: 'Open,D32NEditFile', train: 'D.32.12', mime: 'application/yang',
    desc: 'A YANG module.', criteria: [{ name: '*.yang', mode: 'f' }] },
  { name: 'D32TRANSCRIPT', file: 'deep32.dt', icon: 'D32ask', actions: 'Open', train: 'D.32.14',
    desc: "A transcript from Deep32 Encyclopedia's Ask room.", criteria: [{ name: '*.d32t', mode: 'f' }] },
  // Trays (DISTRO 3.7): matched on the path.
  { name: 'D32TRAYWEB', file: 'deep32-trays.dt', icon: 'D32tray', actions: 'Open', train: 'D.32.13',
    desc: 'A file in the In-Tray, from the web. It opens in the web domain until you move it to your files.',
    criteria: [{ path: '/home/d32web/Downloads/*', mode: 'f' }] },
];

// ---------------------------------------------------------------------------------------------------------------
// Front panel (DISTRO 6.4: CDE's 13 positions, stock icons for stock controls, colour set 8; REPLAN B19: the status
// and station clusters are CDE monitor controls whose icons follow status files, not an embedded client).
// kind: 'delete' (stock record removed: TYPE must match the stock record, EliminateDeleted in DataBaseLoad.c),
//       'redefine' (stock name kept; /etc is read first and the first record read wins, ResolveDuplicates),
//       'new'.
const C = (o) => Object.assign({ type: 'icon', train: 'D.32.12' }, o);
const frontpanel = {
  stockDeletes: [
    { name: 'Mail', type: 'icon', container: 'Top', ctype: 'BOX', why: 'dtmail is disabled in the package; the cell becomes the Event Log' },
    { name: 'Blank1', type: 'blank', container: 'Top', ctype: 'BOX', why: 'position 6 becomes Deep32 Status' },
    { name: 'Blank2', type: 'blank', container: 'Top', ctype: 'BOX', why: 'position 8 becomes the Lab and station control' },
    { name: 'Printer', type: 'icon', container: 'Top', ctype: 'BOX', why: 'no printer; the cell becomes Deep32 Library' },
    { name: 'InfoManager', type: 'icon', container: 'Top', ctype: 'BOX', why: 'the dtinfo build of dtwm.fp puts it at 12; Help Manager takes 12' },
  ],
  main: [
    C({ name: 'Date', kind: 'redefine', type: 'date', pos: 2, icon: 'FpCM', label: 'Calendar', push: 'D32Plan', recall: true, client: 'plan',
        extra: { DATE_FORMAT: '%b%n%e' }, help: 'Today\'s date. Click for the calendar.' }),
    // position 5 stays an empty cell until an event log ships (its action is not in this release)
    C({ name: 'D32Blank5', kind: 'new', type: 'blank', pos: 5, icon: 'D32blank', helpTopic: 'FPOnItemFrontPanel', helpVolume: 'FPanel' }),
    C({ name: 'D32Status', kind: 'new', pos: 6, icon: 'D32stat', alt: 'D32statA', monitor: 'file', fileName: '$HOME/.dt/d32/status.attn',
        label: 'Deep32 Status', push: 'D32Status', train: 'D.32.13',
        help: 'Lit when any status tile needs attention. Open the panel above for the six tiles.', subpanel: 'D32StatusSubpanel' }),
    C({ name: 'D32Station', kind: 'new', pos: 8, icon: 'D32stn', alt: 'D32stnA', monitor: 'file', fileName: '$HOME/.dt/d32/station.attn',
        label: 'Lab', push: 'D32WebAccess', train: 'D.32.13',
        help: 'Lit while the Web switch, the microphone, the camera or a recording is on.', subpanel: 'D32LabSubpanel' }),
    // Position 9 is the library from First light on. REPLAN 3 puts Library Search, man pages and the Lookup desk in
    // D.32.12 and R5 makes them First light's quick tools, later absorbed by Deep32 Library (D.32.14). So until D.32.14 the
    // cell is Library Search with the quick tools in its subpanel; then it becomes Deep32 Library. Control and subpanel keep
    // their names across the change, so anything he installs in the subpanel (fp_dynamic) survives the update.
    C({ name: 'D32Library', kind: 'new', pos: 9, icon: 'D32lib', label: 'Deep32 Encyclopedia', push: 'Deep32Library', drop: 'D32ReadFile',
        help: 'Deep32 Encyclopedia, the reference library. Drop a document here to read it.', subpanel: 'D32LibrarySubpanel',
        early: { until: 'D.32.14', icon: 'D32srch', label: 'Library Search', push: 'D32Search', drop: null,
          help: 'Library Search, offline. The panel above holds the Lookup Desk and the manual pages.' } }),
    C({ name: 'Help', kind: 'new', pos: 12, icon: 'Fphelp', label: 'Help Manager', push: 'Dthelpview', drop: 'Dthelpview',
        helpTopic: 'FPOnItemHelpMgr', helpVolume: 'FPanel', subpanel: 'HelpSubpanel' }),
  ],
  // Subpanels: container = the control they hang from; stock = a stock subpanel we only add to.
  subpanels: [
    { name: 'PersAppsSubpanel', container: 'TextEditor', title: 'Personal Applications', stock: true, controls: [
      C({ name: 'Term', kind: 'redefine', pos: 2, icon: 'Fpterm', label: 'Terminal', push: 'Terminal', helpTopic: 'FPOnItemTerm', helpVolume: 'FPanel' }),
      C({ name: 'D32AskCtl', pos: 3, icon: 'D32ask', label: 'Ask', push: 'Deep32LibraryAsk', train: 'D.32.14' }),
      C({ name: 'D32NEditCtl', pos: 4, icon: 'D32nedit', label: 'NEdit', push: 'D32NEdit', drop: 'D32NEditFile' }),
      C({ name: 'IconEditor', kind: 'redefine', pos: 6, icon: 'Dtpaint', label: 'Icon Editor', push: 'Dticon', drop: 'Dticon', helpTopic: 'FPOnItemIconEditor', helpVolume: 'FPanel' }),
    ] },
    { name: 'D32StatusSubpanel', container: 'D32Status', title: 'Deep32 Status', train: 'D.32.13', controls: [
      // ISO 9.2.5 meanings; one monitor control per tile: alternate icon while its status file is non-empty
      ...[['MO', 'Retrieval mode'], ['GP', 'Home GPU'], ['RL', 'Codex relays'], ['GN', 'Library generation'], ['NX', 'Network exposure']]
        .map(([code, label], k) => C({ name: 'D32Tile' + code, pos: k + 1, icon: 'D32t' + code, alt: 'D32t' + code + 'a', monitor: 'file',
          fileName: '$HOME/.dt/d32/tile.' + code, label: `${code}: ${label}`, push: 'D32Status', train: 'D.32.13' })),
    ] },
    { name: 'D32LabSubpanel', container: 'D32Station', title: 'Lab', train: 'D.32.13', controls: [
      ...[['WB', 'Web switch'], ['MI', 'Microphone'], ['CA', 'Camera'], ['RC', 'Recording']]
        .map(([code, label], k) => C({ name: 'D32Tile' + code, pos: k + 1, icon: 'D32t' + code, alt: 'D32t' + code + 'a', monitor: 'file',
          fileName: '$HOME/.dt/d32/tile.' + code, label: `${code}: ${label}`, push: code === 'WB' ? 'D32WebAccess' : 'D32Settings', train: 'D.32.13' })),
      C({ name: 'D32WiresharkCtl', pos: 8, icon: 'D32wshk', label: 'Wireshark', push: 'D32Wireshark', drop: 'D32WiresharkOpen', train: 'D.32.13' }),
      C({ name: 'D32CaptureCtl', pos: 9, icon: 'D32cap', label: 'Capture', push: 'D32Capture', train: 'D.32.13' }),
      C({ name: 'D32OuiCtl', pos: 12, icon: 'D32oui', label: 'OUI Lookup', push: 'OuiLookup', train: 'D.32.15' }),
      C({ name: 'D32NetCalcCtl', pos: 13, icon: 'D32calc', label: 'Network Calculator', push: 'D32NetCalc', train: 'D.32.15' }),
    ] },
    // before D.32.14 titled "Library"; Library Search is then the main control itself, which dtwm copies to the top
    // of the subpanel, so its own entry is omitted until Deep32 Library takes the cell
    { name: 'D32LibrarySubpanel', container: 'D32Library', title: 'Deep32 Encyclopedia',
      early: { until: 'D.32.14', title: 'Library', omit: ['D32SearchCtl'] }, controls: [
      C({ name: 'D32SearchCtl', pos: 1, icon: 'D32srch', label: 'Library Search', push: 'D32Search' }),
      C({ name: 'D32LibAskCtl', pos: 2, icon: 'D32ask', label: 'Ask', push: 'Deep32LibraryAsk', train: 'D.32.14' }),
      C({ name: 'D32ReaderCtl', pos: 2, icon: 'D32read', label: 'Reader', push: 'D32Read', drop: 'D32ReadFile', train: 'D.32.14' }),
      C({ name: 'D32AcqCtl', pos: 3, icon: 'D32acq', alt: 'D32acqN', monitor: 'file', fileName: '$HOME/.d32/e26/requests.changed',
          label: 'Acquisition', push: 'Deep32LibraryAcquisitions', train: 'D.32.14' }),
      C({ name: 'D32ManCtl', pos: 5, icon: 'D32man', label: 'Manual Pages', push: 'Dtmanpageview' }),
      C({ name: 'D32InTrayCtl', pos: 6, icon: 'D32tray', label: 'In-Tray', push: 'D32InTray', train: 'D.32.17' }),
    ] },
    { name: 'D32StyleSubpanel', container: 'Style', title: 'Desktop Style', controls: [
      C({ name: 'D32StyleMgrCtl', pos: 1, icon: 'Fpstyle', label: 'Style Manager', push: 'Dtstyle' }),
      C({ name: 'D32SoundCtl', pos: 2, icon: 'D32snd', label: 'Sound', push: 'D32Sound', train: 'D.32.13' }),
      C({ name: 'D32AudioCtl', pos: 3, icon: 'D32mix', label: 'Audio Control', push: 'AudioControl', train: 'D.32.17' }),
      C({ name: 'D32LookCtl', pos: 4, icon: 'D32prof', label: 'Desktop Look', push: 'D32LookProfile' }),
      C({ name: 'D32SettingsCtl', pos: 5, icon: 'D32set', label: 'Deep32 Settings', push: 'D32Settings', train: 'D.32.14' }),
      C({ name: 'D32SysAdminCtl', pos: 6, icon: 'D32sam', label: 'System Administration', push: 'D32SysAdmin', train: 'D.32.14' }),
      C({ name: 'D32AdminMenusCtl', pos: 7, icon: 'D32smit', label: 'Administration Menus', push: 'D32AdminMenus', train: 'D.32.14' }),
    ] },
    { name: 'D32AppsSubpanel', container: 'Applications', title: 'Applications', controls: [
      C({ name: 'D32WebCtl', pos: 4, icon: 'D32web', label: 'Web Browser', push: 'D32Web', train: 'D.32.17' }),
      C({ name: 'D32ImageCtl', pos: 6, icon: 'D32img', label: 'Image Viewer', push: 'D32ImageViewer' }),
      C({ name: 'D32ScreenCapCtl', pos: 7, icon: 'D32scap', label: 'Screen Capture', push: 'D32ScreenCapture', train: 'D.32.17' }),
    ] },
    { name: 'HelpSubpanel', container: 'Help', title: 'Help', controls: [
      C({ name: 'D32HelpCtl', pos: 2, icon: 'D32help', label: 'Deep32 Workstation', push: 'D32Help', train: 'D.32.14' }),
      C({ name: 'HelpOverview', kind: 'redefine', pos: 3, icon: 'Dthover', label: 'Desktop Introduction', push: 'OpenDtIntro', helpTopic: 'FPOnItemDtIntro', helpVolume: 'FPanel' }),
      C({ name: 'D32ManViewerCtl', pos: 4, icon: 'D32man', label: 'Man Page Viewer', push: 'Dtmanpageview' }),
      C({ name: 'OnItem', kind: 'redefine', pos: 5, icon: 'DthonFP', label: 'On Item Help', push: 'FPOnItemHelp', helpTopic: 'FPOnItemOnItem', helpVolume: 'FPanel' }),
      C({ name: 'FPHelp', kind: 'redefine', pos: 6, icon: 'Fpfphlp', label: 'Front Panel Help', push: 'FPHelp', helpTopic: 'FPOnItemFPHelp', helpVolume: 'FPanel' }),
    ] },
  ],
};

// Application Manager groups (DISTRO 6.8 fid 16; APPS 4.1; EP9: THEME, as the /etc/dt registrar, owns them). Each
// group is a directory under /etc/dt/appconfig/appmanager/C holding action files (an executable file named after an
// action, which dtfile treats as that action, as the package's own entries do). An entry follows its action's train,
// so every release offers a second way into each program it ships. `stock` groups exist in the package; new groups
// get a data type with their icon (stock icons until the art lane). `pending`: entries the design lists whose action
// another lane has not registered yet; never emitted, because an action file with no action behind it is a dead icon.
// The VM proof found that dtappgather does not merge groups of the same name: the first directory of that name on
// DTAPPSEARCHPATH ($HOME/.dt/appmanager, then /etc/dt, then the package) replaces the package's group whole. So a
// stock group we touch is shipped whole and curated: `keep` lists the package entries it keeps (APPS 4.1's rules,
// checked against test/fixtures/stock-appmanager.txt). Whole stock groups the design drops (System, Internet,
// Education, Tex) and the third-party entries of Games and Office are install.site's d32-appmgr-curate (APPS 4.1).
const appmanager = {
  groups: [
    { name: 'Deep32', icon: 'Dtapps', entries: ['D32Search', 'Deep32Library', 'Deep32LibraryAsk', 'D32Read', 'Deep32LibraryPinpointer',
      'Deep32LibraryAcquisitions', 'D32Status'] },
    { name: 'Network_Tools', icon: 'Dtautil', entries: ['D32Wireshark', 'D32Capture', 'OuiLookup', 'D32NetCalc'], pending: ['Nmap', 'mtr', 'iperf3', 'Legacy SSH Terminal'] },
    { name: 'System_Admin', icon: 'Dtasys', entries: ['D32SysAdmin', 'D32AdminMenus', 'D32Settings'],
      pending: ['Software Management', 'Back Up Now', 'Make Recovery Media', 'System Load', 'KeePassXC (if installed)'] },
    // APPS 4.1: the stock set minus Mailer (Dtmail), Information Manager (DtLoadInfoLib) and Print Manager; plus NEdit
    // and Plan. Dtcm goes too (Plan replaces it: CDE's calendar needs rpc.cmsd, not run here); App Builder moves to
    // Development
    { name: 'Desktop_Apps', stock: true, keep: ['Dtcalc', 'Dtcreate', 'Dtfile', 'Dthelpview', 'Dticon', 'Dtmanpageview', 'Dtpad',
      'Dtstyle', 'Dtterm', 'Dttrash'], entries: ['D32NEdit', 'D32Plan', 'D32Web'] },
    // APPS 4.1: the stock set minus the dtspcd and rlogin terminals; Make moves to Development; Diff stays until mgdiff
    // registers its action
    { name: 'Desktop_Tools', stock: true, keep: ['Bitmap', 'CodeGen', 'Compress', 'Df', 'Diff', 'DtImsMode', 'DttermConsole',
      'DttermErrorlog', 'Dttypes', 'DtwmrcEdit', 'DuSort', 'Env', 'ExecuteCmd', 'File', 'Grep', 'Nm', 'ReloadActions', 'ReloadApps',
      'ReloadResources', 'RestorePanel', 'Rm', 'SaveSession', 'Spell', 'Tar', 'TarList', 'TarUnpack', 'Uncompress', 'Vi', 'Wc',
      'Xclipboard', 'XclockDig', 'Xdpyinfo', 'Xfd', 'Xload', 'Xlsfonts', 'Xprop', 'Xrefresh', 'Xterm', 'Xwd', 'Xwininfo', 'Xwud'],
      entries: ['D32LookProfile', 'D32Sound'], pending: ['Diff (mgdiff)', 'xlock', 'the X toys'] },
    { name: 'Information', stock: true, keep: ['OpenDtIntro'], entries: ['D32Help'], pending: ['Deep32/UX introduction'] },
    // APPS 4.1: Media Player, Audio Control, cmixer; the package's entries are programs this node does not carry
    { name: 'Media_Tools', stock: true, keep: [], entries: ['AudioControl'], pending: ['cmixer'] },
    // APPS 4.1: Image Viewer (xv, our D32ImageViewer replaces the package's xv entry), Dia, Render Graph
    { name: 'Graphics', stock: true, keep: [], entries: ['D32ImageViewer', 'D32ScreenCapture'], pending: ['Dia', 'Render Graph'] },
    { name: 'Development', icon: 'Dtagen', keep: ['Dtappbuild', 'Make'], entries: [], pending: ['Git', 'gdb, DDD, Grace (if installed)'] },
  ],
};

// dtwm (DISTRO 6.6, LOOK-SOUND 2.4; CDE-SPEC 4). Keys: Mod4 only; Ctrl+Left/Right and Alt+letter stay with apps.
const dtwm = {
  resources: [
    ['Dtwm*keyboardFocusPolicy', 'explicit'],
    ['Dtwm*moveOpaque', 'False'],
    ['Dtwm*useIconBox', 'False'],
    ['Dtwm*resizeBorderWidth', '6'],      // CDE-SPEC 2.2.1: 6 at high resolution (stock leaves it to the dynamic default)
    ['Dtwm*frameBorderWidth', '5'],
    ['Dtwm*0*workspaceCount', '4'],
  ],
  keys: [
    ['Mod4<Key>1', 'root|icon|window', 'f.goto_workspace "ws0"'],
    ['Mod4<Key>2', 'root|icon|window', 'f.goto_workspace "ws1"'],
    ['Mod4<Key>3', 'root|icon|window', 'f.goto_workspace "ws2"'],
    ['Mod4<Key>4', 'root|icon|window', 'f.goto_workspace "ws3"'],
    ['Mod4<Key>Left', 'root|icon|window', 'f.prev_workspace'],
    ['Mod4<Key>Right', 'root|icon|window', 'f.next_workspace'],
    ['Mod4<Key>space', 'root|icon|window', 'f.action D32SearchSelection'],
    ['Mod4<Key>a', 'root|icon|window', 'f.action Deep32LibraryAsk', 'D.32.14'],
    ['Mod4<Key>l', 'root|icon|window', 'f.action LockDisplay'],
  ],
  // In the VM (DISTRO 6.6): Ctrl+Alt+1..4 as well, because a hypervisor console may swallow the Windows key.
  vmKeys: [1, 2, 3, 4].map(n => [`Ctrl Alt<Key>${n}`, 'root|icon|window', `f.goto_workspace "ws${n - 1}"`]),
  // LOOK-SOUND 9.3: the root menu gains "Desktop Style..." and "System Administration..."
  rootMenuExtra: [
    ['"Desktop Style..."', 'f.action Dtstyle'],
    ['"Desktop Look..."', 'f.action D32LookProfile'],
    ['"System Administration..."', 'f.action D32SysAdmin', 'D.32.14'],
  ],
};

// dtsession, dtterm and the rest of the sys.resources overlay (LOOK-SOUND 9.3, DISTRO 6.6-6.7).
const session = [
  ['dtsession*saverTimeout', '10'],
  ['dtsession*lockTimeout', '10'],        // DECISIONS C 26: lock at 10 minutes
  ['dtsession*cycleTimeout', '3'],
  ['dtsession*saverList', 'StartDtscreenBlank StartDtscreenSwarm StartDtscreenQix StartDtscreenLife'],
  ['Dtterm*scrollBar', 'True'],
  ['Dtterm*saveLines', '20s'],
  ['Dtterm*rows', '24'],
  ['Dtterm*columns', '80'],
  ['Dtterm*visualBell', 'False'],
  ['Dtterm*pointerShape', 'xterm'],
  ['Xft.dpi', '100'],                    // X server runs with -dpi 100 (ISO 9.1); Xft clients read this
];

// dtlogin (LOOK-SOUND 2.8.1; DISTRO 6.7). Colours written out: dtlogin has no colour server.
const dtlogin = {
  greeting: 'Welcome to deep32', persGreeting: 'Welcome %s',
  clearLabel: 'Start Over',
  logo: '/usr/local/share/d32/theme/login/emblem-224.xpm',
  fonts: { greeting: 'xxl', label: 'l', text: 'l' },
};

// Text console and period files (SYSV-FLAVOUR 2.6-2.7, 3.3, 5; LOOK-SOUND 3.3-3.5).
const sysv = {
  checklistTitle: 'Deep32 Start-up in progress',
  shutdownTitle: 'System shutdown in progress',
  readyLine: 'The system is ready.',
  statusWords: ['OK', 'N/A', 'FAIL *', 'WARN', 'STARTING'],
  failFootnotes: ['* - An error has occurred !', '* - Refer to the file /var/adm/rc.log for more information.'],
  dtloginBox: ['Starting the Deep32 Desktop Login on display deep32:0 ...', '', 'Wait for the login screen before typing.'],
  haltedLine: 'System halted.  It is now safe to switch off.',
};

module.exports = { release, identity, TRAINS, profiles, defaultProfile, privateProfile, looks, palettes, workspaces, fixedColours, paths,
  actions, openMaps, datatypes, frontpanel, appmanager, dtwm, session, dtlogin, sysv };
