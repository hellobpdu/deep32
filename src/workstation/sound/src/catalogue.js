'use strict';
// Every sound Deep32 Workstation ships, with its tier (the peak it is normalised to, LOOK-SOUND 8.3), where the design
// asks for it, what triggers it, which session profiles play it (DISTRO-DESIGN 6.10, newest) and its rate limit.
//
// Sources, newest wins where they differ:
//   v3      reference/sound-cs.txt, the approved engine (the owner 11-03); rendered by its own code (v3.js)
//   LS      distro/look-sound/LOOK-SOUND.md 8.3 (tiers, triggers, limits) and 8.4 (recipes, verbatim)
//   LS8.5   LOOK-SOUND 8.5, the X bell
//   D6.10   distro/DISTRO-DESIGN.md 6.10 (profiles: window and workspace sounds only in Full)
//   A5.5    deep32-cde/research/assets/ASSETS.md 5.5 (installer sounds; the `complete` chime is the machine's first
//           sound, DISTRO 4.3); parameters ASSETS does not give are ours, marked "ours" in `derived`
//   L6.2    library/LIBRARY-DESIGN.md 6.2 (Deep32 Library); sound atoms use the short form `e26-` (LIBRARY 3850)
//
// A recipe is either a v3 builder (`v3: true`) or data (`buffer` seconds + `ops`). Data recipes made only of the v3
// primitives are also rendered by the C# engine itself in the parity test (`parity: 'prim'`).

const { V3_PRIMITIVES } = require('./engine');

const WS = 'workstation', FULL = 'full', CLASSIC = 'classic';
const DESKTOP = [WS, FULL];

// LOOK-SOUND 8.4: the v3 primitives, unchanged; buffers and recipes verbatim
const ws = (n, f) => ({
  name: `ws${n}`, group: 'desktop', tier: 0.10, source: 'LS 8.3-8.4 (ws, pitch per workspace)',
  trigger: `workspace switch to workspace ${n}`, profiles: [FULL], rateLimit: '1 per 120 ms', minIntervalMs: 120,
  player: 'd32sndd (v1b)', buffer: 0.3, ops: [['Fm', 0, 0.25, f, 2.0, 0.8, 0.06, 0.07], ['Reverb', 0.2, 0.6]],
});

const CATALOGUE = [
  // ------------------------------------------------------------------ the 16 approved v3 sounds
  { name: 'rom', v3: true, group: 'desktop', tier: 0.21, source: 'v3; LS 8.3', trigger: 'd32ready starts (rc), unless /var/run/d32-firstchime exists', profiles: DESKTOP, rateLimit: 'once per boot', player: 'aucat (rc)' },
  { name: 'login', v3: true, group: 'desktop', tier: 0.21, source: 'v3; LS 8.3', trigger: 'session start (~/.dt/sessions/sessionetc)', profiles: DESKTOP, rateLimit: 'once per session', player: 'send_sound' },
  { name: 'window', v3: true, group: 'desktop', tier: 0.08, source: 'v3, re-tiered 0.12 -> 0.08 (LS 8.3)', trigger: 'a managed top-level window maps', profiles: [FULL], rateLimit: '1 per 150 ms; muted 500 ms after a workspace switch and 10 s after login', minIntervalMs: 150, player: 'd32sndd (v1b)' },
  { name: 'bell', v3: true, group: 'console', tier: 0.14, source: 'v3', trigger: 'the Codex Console\'s notification chime (the desktop bell is xbell)', profiles: DESKTOP, rateLimit: 'app', player: 'send_sound' },
  { name: 'connect', v3: true, group: 'console', tier: 0.14, source: 'v3; LS 8.3', trigger: 'Codex Console / Ask: a new conversation', profiles: DESKTOP, rateLimit: 'app', player: 'send_sound' },
  { name: 'think', v3: true, group: 'console', tier: 0.07, loop: true, source: 'v3; LS 8.3; L6.2', trigger: 'Ask / Deep32 Library Instant Answers: while Codex works (32 s loop)', profiles: DESKTOP, rateLimit: 'app (the single loop slot)', player: 'd32sndd (v1b)' },
  { name: 'output', v3: true, group: 'console', tier: 0.06, loop: true, source: 'v3; LS 8.3', trigger: 'Ask: while the answer prints (1.2 s loop)', profiles: DESKTOP, rateLimit: 'app (the single loop slot)', player: 'd32sndd (v1b)' },
  { name: 'done', v3: true, group: 'console', tier: 0.16, source: 'v3; LS 8.3; L6.2', trigger: 'Ask: answer done; Deep32 Library: a request reaches "in generation N" or "on this node"', profiles: DESKTOP, rateLimit: 'app', player: 'send_sound' },
  { name: 'ok', v3: true, group: 'console', tier: 0.14, source: 'v3; LS 8.3; L6.2 (asks 0.12, the shared v3 file keeps 0.14)', trigger: 'hand-off ok; Deep32 Library: Check for Updates finds changes', profiles: DESKTOP, rateLimit: 'app', player: 'send_sound' },
  { name: 'disk', v3: true, group: 'console', tier: 0.13, source: 'v3; LS 8.3; L6.2', trigger: 'In-Tray / a file written; Deep32 Library: a download arrives in your library', profiles: DESKTOP, rateLimit: 'app', player: 'send_sound' },
  { name: 'page', v3: true, group: 'console', tier: 0.10, source: 'v3; L6.2', trigger: 'a page of the manual or scrollback; Deep32 Library: next or previous section or page (the page turn)', profiles: DESKTOP, rateLimit: 'app', player: 'send_sound' },
  { name: 'click', v3: true, group: 'console', tier: 0.12, source: 'v3; L6.2', trigger: 'Deep32 Library: push button (the button click); Ask: key click (app option); never desktop-wide', profiles: DESKTOP, rateLimit: 'app', player: 'send_sound' },
  { name: 'enter', v3: true, group: 'console', tier: 0.13, source: 'v3; L6.2', trigger: 'Deep32 Library: open a title; Ask: Return (app option)', profiles: DESKTOP, rateLimit: 'app', player: 'send_sound' },
  { name: 'down', v3: true, group: 'console', tier: 0.16, source: 'v3', trigger: 'Codex Console: shutting the RAG down', profiles: DESKTOP, rateLimit: 'app', player: 'send_sound' },
  { name: 'boot', v3: true, group: 'console', tier: 0.36, source: 'v3', trigger: 'Codex Console port only (the Windows launcher\'s PC power-on); no desktop event plays it', profiles: DESKTOP, rateLimit: 'app', player: 'send_sound' },
  { name: 'crtoff', v3: true, group: 'console', tier: 0.21, source: 'v3', trigger: 'Codex Console port only (the Windows launcher\'s exit); no desktop event plays it', profiles: DESKTOP, rateLimit: 'app', player: 'send_sound' },

  // ------------------------------------------------------------------ LOOK-SOUND 8.4, the new desktop recipes
  { name: 'pdc', group: 'desktop', tier: 0.14, source: 'LS 8.3-8.4', trigger: 'BOOT_STYLE=full, first header line (rc)', profiles: [FULL], rateLimit: 'once', player: 'aucat (rc)',
    buffer: 0.25, ops: [['Sine', 0, 0.12, 1000, 1000, 0.15, 0.002, 0.012, 0, 0.3], ['LowPass', 0.6]] },
  { name: 'logout', group: 'desktop', tier: 0.18, source: 'LS 8.3-8.4 (login\'s bells, falling)', trigger: 'session end (~/.dt/sessions/sessionexit)', profiles: DESKTOP, rateLimit: 'once', player: 'send_sound',
    buffer: 2.6, ops: [['Fm', 0, 1.8, 1318.51, 3.5, 0.8, 0.6, 0.06], ['Fm', 0.14, 1.9, 987.77, 3.5, 1.0, 0.65, 0.085], ['Fm', 0.28, 2.1, 659.25, 3.5, 1.2, 0.7, 0.1], ['Reverb', 0.3, 0.75]] },
  { name: 'halt', group: 'desktop', tier: 0.16, source: 'LS 8.3-8.4 (fans winding down, two low notes)', trigger: '/etc/rc.shutdown, synchronous', profiles: DESKTOP, rateLimit: 'once', player: 'aucat (rc)',
    buffer: 1.6, ops: [['Noise', 0, 0.7, 0.035, 0.05, 0.02, 0.4], ['Noise', 0.5, 0.9, 0.03, 0.025, 0.1, 0.8], ['Fm', 0.2, 1.2, 392.00, 1.0, 1.4, 0.5, 0.08], ['Fm', 0.36, 1.2, 261.63, 1.0, 1.4, 0.6, 0.08], ['LowPass', 0.5], ['Reverb', 0.2, 0.7]] },
  { name: 'lock', group: 'desktop', tier: 0.12, source: 'LS 8.3-8.4 (ASSETS)', trigger: 'front-panel Lock (D32Lock) and idle lock', profiles: DESKTOP, rateLimit: '1 per 5 s', minIntervalMs: 5000, player: 'd32sndd (v1b)',
    buffer: 0.6, ops: [['Fm', 0, 0.3, 392.00, 3.5, 0.8, 0.12, 0.07], ['Fm', 0.14, 0.4, 293.66, 3.5, 0.8, 0.15, 0.07], ['Reverb', 0.25, 0.65]] },
  { name: 'unlock', group: 'desktop', tier: 0.12, source: 'LS 8.3-8.4', trigger: 'the lock window unmaps', profiles: DESKTOP, rateLimit: '1 per 5 s', minIntervalMs: 5000, player: 'd32sndd (v1b)',
    buffer: 0.6, ops: [['Fm', 0, 0.3, 293.66, 3.5, 0.8, 0.12, 0.07], ['Fm', 0.14, 0.4, 392.00, 3.5, 0.8, 0.15, 0.07], ['Reverb', 0.25, 0.65]] },
  { name: 'err', group: 'desktop', tier: 0.14, source: 'LS 8.3-8.4 (ASSETS)', trigger: 'Deep32 app error dialog (libd32xm)', profiles: DESKTOP, rateLimit: '1 per 2 s', minIntervalMs: 2000, player: 'send_sound',
    buffer: 0.7, ops: [['Fm', 0, 0.3, 329.63, 3.5, 1.0, 0.15, 0.08], ['Fm', 0.25, 0.4, 246.94, 3.5, 1.0, 0.18, 0.08], ['Reverb', 0.2, 0.65]] },
  { name: 'warn', group: 'desktop', tier: 0.14, source: 'LS 8.3-8.4 (ASSETS)', trigger: 'Deep32 app warning dialog', profiles: DESKTOP, rateLimit: '1 per 2 s', minIntervalMs: 2000, player: 'send_sound',
    buffer: 0.5, ops: [['Fm', 0, 0.4, 440.00, 3.5, 1.0, 0.18, 0.08], ['Reverb', 0.2, 0.65]] },
  { name: 'ask', group: 'desktop', tier: 0.12, source: 'LS 8.3-8.4 (ASSETS)', trigger: 'Deep32 app question dialog', profiles: DESKTOP, rateLimit: '1 per 2 s', minIntervalMs: 2000, player: 'send_sound',
    buffer: 0.6, ops: [['Fm', 0, 0.25, 392.00, 3.5, 0.8, 0.12, 0.07], ['Fm', 0.12, 0.35, 523.25, 3.5, 0.8, 0.15, 0.07], ['Reverb', 0.25, 0.65]] },
  { name: 'info', group: 'desktop', tier: 0.10, source: 'LS 8.3-8.4', trigger: 'Deep32 app information dialog', profiles: [FULL], rateLimit: '1 per 2 s', minIntervalMs: 2000, player: 'send_sound',
    buffer: 0.45, ops: [['Fm', 0, 0.35, 523.25, 3.5, 0.6, 0.12, 0.07], ['Reverb', 0.25, 0.65]] },
  { name: 'window-close', group: 'desktop', tier: 0.08, source: 'LS 8.3-8.4 (v3 window, falling)', trigger: 'a managed window unmaps or is destroyed', profiles: [FULL], rateLimit: 'as window', minIntervalMs: 150, player: 'd32sndd (v1b)',
    buffer: 0.6, ops: [['Sine', 0, 0.18, 700, 300, 0.05, 0.03, 0.12, 0.2, 0], ['Noise', 0, 0.18, 0.015, 0.1, 0.03, 0.12], ['Reverb', 0.35, 0.7]] },
  ws(1, 523.25), ws(2, 587.33), ws(3, 659.25), ws(4, 783.99),
  { name: 'menu', group: 'desktop', tier: 0.06, source: 'LS 8.3-8.4 (ASSETS; LS tier 0.06 over ASSETS 0.08)', trigger: 'an XmMenuShell maps', profiles: [FULL], rateLimit: '1 per 80 ms', minIntervalMs: 80, player: 'd32sndd (v1b)',
    buffer: 0.03, ops: [['Noise', 0, 0.003, 0.08, 0.4, 0.0005, 0.002], ['Sine', 0, 0.008, 1200, 1200, 0.05, 0.001, 0.006, 0, 0]] },
  { name: 'lamp-ok', group: 'desktop', tier: 0.10, source: 'LS 8.3-8.4', trigger: 'a RAG lamp turns green', profiles: DESKTOP, rateLimit: 'coalesced: within 2 s only the worst new state plays; none in the first 15 s of a session', minIntervalMs: 2000, player: 'send_sound',
    buffer: 0.6, ops: [['Fm', 0, 0.25, 587.33, 2.0, 0.9, 0.10, 0.07], ['Fm', 0.09, 0.35, 880.00, 2.0, 0.9, 0.12, 0.06], ['Reverb', 0.25, 0.65]] },
  { name: 'lamp-warn', group: 'desktop', tier: 0.12, source: 'LS 8.3-8.4', trigger: 'a RAG lamp turns amber', profiles: DESKTOP, rateLimit: 'as lamp-ok', minIntervalMs: 2000, player: 'send_sound',
    buffer: 0.6, ops: [['Fm', 0, 0.22, 659.25, 2.0, 0.9, 0.10, 0.07], ['Fm', 0.12, 0.30, 587.33, 2.0, 0.9, 0.12, 0.07], ['Reverb', 0.25, 0.65]] },
  { name: 'lamp-fail', group: 'desktop', tier: 0.14, source: 'LS 8.3-8.4', trigger: 'a RAG lamp turns red (also plays under the lock and in office quiet)', profiles: DESKTOP, rateLimit: 'as lamp-ok', minIntervalMs: 2000, player: 'send_sound',
    buffer: 0.9, ops: [['Fm', 0, 0.20, 659.25, 3.5, 1.0, 0.10, 0.08], ['Fm', 0.13, 0.20, 554.37, 3.5, 1.0, 0.10, 0.08], ['Fm', 0.26, 0.45, 440.00, 3.5, 1.0, 0.18, 0.08], ['Reverb', 0.3, 0.7]] },
  { name: 'lamp-off', group: 'desktop', tier: 0.10, source: 'LS 8.3-8.4', trigger: 'a RAG lamp turns grey (unknown or offline)', profiles: DESKTOP, rateLimit: 'as lamp-ok', minIntervalMs: 2000, player: 'send_sound',
    buffer: 0.5, ops: [['Fm', 0, 0.30, 392.00, 1.0, 0.6, 0.10, 0.07], ['Tick', 0, 0.08], ['Reverb', 0.2, 0.6]] },
  { name: 'airgap', group: 'desktop', tier: 0.12, source: 'LS 8.3-8.4 (relay contacts opening)', trigger: 'an air-gapped tool opens its config pane', profiles: DESKTOP, rateLimit: '1 per 5 s', minIntervalMs: 5000, player: 'send_sound',
    buffer: 0.4, ops: [['Tick', 0, 0.2], ['Tick', 0.035, 0.16], ['Sine', 0.01, 0.12, 90, 60, 0.12, 0.002, 0.09, 0.3, 0], ['Noise', 0, 0.03, 0.05, 0.3, 0.001, 0.02], ['LowPass', 0.6], ['Reverb', 0.12, 0.6]] },
  { name: 'trash', group: 'desktop', tier: 0.12, source: 'LS 8.3-8.4 (ASSETS)', trigger: 'drop on Trash (D32TrashDrop)', profiles: DESKTOP, rateLimit: '1 per 1 s', minIntervalMs: 1000, player: 'send_sound',
    buffer: 0.45, ops: [['Noise', 0, 0.12, 0.05, 0.05, 0.005, 0.08], ['Noise', 0.08, 0.17, 0.05, 0.3, 0.01, 0.12], ['Seeks', 0.1, 3, 0.04, 0.12]] },

  // ------------------------------------------------------------------ the X bell (LOOK-SOUND 8.5): dtterm ^G, Motif invalid input
  { name: 'xbell', group: 'bell', tier: 0.10, source: 'LS 8.5 at the X server defaults (50 %, 400 Hz, 100 ms)', trigger: 'XkbBellNotify; d32sndd synthesises it live from the event\'s percent, pitch and duration (this file is the default render and the reference for that code)', profiles: [CLASSIC, WS, FULL], rateLimit: 'coalesce within 50 ms', minIntervalMs: 50, player: 'nxbelld (v1), d32sndd (v1b)',
    noDeclick: true, build: 'xbell', xbell: { pitchHz: 400, durationMs: 100, percent: 50 },
    derived: 'amplitude = 0.20 x percent / 100, so the X default 50 % peaks at 0.10 (-20 dBFS)' },

  // ------------------------------------------------------------------ first start and the installer (ASSETS 5.5)
  { name: 'complete', group: 'installer', tier: 0.20, source: 'A5.5; DISTRO 4.3 (the machine\'s first sound)', trigger: 'set_parms ends at first start (aucat), then writes /var/run/d32-firstchime', profiles: DESKTOP, rateLimit: 'once ever', player: 'aucat (rc)',
    buffer: 2.4, ops: [['Fm', 0, 1.4, 523.25, 3.5, 1.0, 0.5, 0.09], ['Fm', 0.09, 1.4, 659.25, 3.5, 1.0, 0.5, 0.085], ['Fm', 0.18, 1.4, 783.99, 3.5, 0.9, 0.55, 0.08], ['Fm', 0.27, 1.5, 1046.50, 3.5, 0.8, 0.9, 0.08], ['Reverb', 0.3, 0.75]],
    derived: 'ours: durations, indices, decays and volumes; ASSETS gives the notes, the 0.09 s stagger, the held last note (about 1.2 s), reverb 0.3 and the tier' },
  { name: 'setup', group: 'installer', tier: 0.18, source: 'A5.5', trigger: 'Path B installer only [J]: Setup starts (RAMDISK_CD has no audio, INSTALLER 1.9)', profiles: ['installer-path-b'], rateLimit: 'once', player: 'aucat (Path B only)',
    buffer: 2.0, ops: [['Fm', 0, 1.4, 329.63, 3.5, 1.2, 0.5, 0.08], ['Fm', 0.11, 1.4, 415.30, 3.5, 1.2, 0.5, 0.08], ['Fm', 0.22, 1.4, 493.88, 3.5, 1.2, 0.5, 0.08], ['Fm', 0.33, 1.5, 659.25, 3.5, 1.2, 0.6, 0.08], ['Reverb', 0.3, 0.75]],
    derived: 'ours: durations, decays, volumes; ASSETS gives the notes, 0.11 s apart, ratio 3.5, index 1.2, reverb 0.3' },
  { name: 'diskswap', group: 'installer', tier: 0.16, source: 'A5.5', trigger: 'Path B installer only [J]: a stage prompt', profiles: ['installer-path-b'], rateLimit: 'once per prompt', player: 'aucat (Path B only)',
    buffer: 1.6, ops: [['Fm', 0, 0.6, 783.99, 3.5, 1.0, 0.3, 0.08], ['Fm', 0.35, 0.9, 523.25, 3.5, 1.0, 0.4, 0.08], ['Reverb', 0.3, 0.7]],
    derived: 'ours: durations, index, decays, volumes; ASSETS gives the two notes 0.35 s apart' },
  { name: 'copy', group: 'installer', tier: 0.08, loop: true, source: 'A5.5', trigger: 'Path B installer only [J]: copying files (2 s loop)', profiles: ['installer-path-b'], rateLimit: 'loop', player: 'aucat (Path B only)',
    loopPeriod: 2.0, build: 'copy',
    derived: 'ours: the seek times (seeded) and tick levels; ASSETS gives the 2 s loop, the 120 Hz hum at 0.004, seeks every 0.4-0.7 s and reverb 0.15' },

  // ------------------------------------------------------------------ Deep32 Library (LIBRARY-DESIGN 6.2)
  { name: 'e26-theme', group: 'library', tier: 0.16, source: 'L6.2 (theme)', trigger: 'the Title screen, once per session (Options > Start > Play the theme)', profiles: DESKTOP, rateLimit: 'once per session', player: 'd32sndd (v1b) or send_sound via e26-launch (HARDENING FIX-27)',
    buffer: 1.9, ops: [
      ['FmSoft', 0.00, 0.90, 523.25, 2.0, 1.0, 0.35, 0.08, 0.03],
      ['FmSoft', 0.16, 0.90, 659.25, 2.0, 1.0, 0.35, 0.075, 0.03],
      ['FmSoft', 0.32, 0.90, 783.99, 2.0, 0.9, 0.35, 0.07, 0.03],
      ['FmSoft', 0.48, 0.90, 1046.50, 2.0, 0.8, 0.40, 0.065, 0.03],
      ['FmSoft', 0.64, 0.78, 1318.51, 2.0, 0.8, 0.80, 0.07, 0.03],
      ['Reverb', 0.35, 0.75]],
    notes: [523.25, 659.25, 783.99, 1046.50, 1318.51],
    derived: 'LIBRARY gives the five rising notes C5 E5 G5 C6 E6, FM ratio 2, soft 30 ms attacks, the 0.16 s stagger, the last held 0.7 s, reverb 0.35 and the tier; ours: indices, decays, volumes. Nothing of Library\'s own theme is imitated' },
  { name: 'e26-count', group: 'library', tier: 0.06, source: 'L6.2 (count: an odometer stopping)', trigger: 'a Pinpointer filter changes the count (never after typing)', profiles: DESKTOP, rateLimit: 'app', player: 'd32sndd (v1b)',
    buffer: 0.05, ops: [
      ['Noise', 0, 0.002, 0.1, 0.5, 0.0002, 0.0015], ['Sine', 0, 0.003, 2400, 2000, 0.03, 0.0002, 0.002, 0, 0],
      ['Noise', 0.04, 0.002, 0.08, 0.5, 0.0002, 0.0015], ['Sine', 0.04, 0.003, 2400, 2000, 0.024, 0.0002, 0.002, 0, 0],
      ['LowPass', 0.4]],
    derived: 'LIBRARY gives two 2 ms ticks 40 ms apart and low-pass 0.4; ours: the faint 2.4 kHz detent under each tick, the second softer' },
  { name: 'e26-card', group: 'library', tier: 0.07, source: 'L6.2 (card: a slip of paper)', trigger: 'keep a note card or a Notemark', profiles: DESKTOP, rateLimit: 'app', player: 'd32sndd (v1b)',
    buffer: 0.07, ops: [['Noise', 0, 0.06, 0.1, 1.0, 0.004, 0.045], ['HighPass', 4000], ['HighPass', 4000]],
    derived: 'LIBRARY gives a 60 ms noise burst high-passed at 4 kHz; ours: two one-pole stages (12 dB per octave), 4 ms attack, 45 ms release' },
  { name: 'e26-slide', group: 'library', tier: 0.09, source: 'L6.2 (slide)', trigger: 'the Slide Show advances', profiles: DESKTOP, rateLimit: 'app', player: 'd32sndd (v1b)',
    buffer: 0.04, ops: [['Noise', 0, 0.012, 0.12, 0.45, 0.0005, 0.01], ['Sine', 0, 0.03, 90, 75, 0.1, 0.002, 0.025, 0.3, 0]],
    derived: 'LIBRARY gives a 12 ms noise clack and a 90 Hz thump of 30 ms; ours: levels and the slight fall to 75 Hz' },
  { name: 'e26-hum', group: 'library', tier: 0.03, loop: true, source: 'L6.2 (hum, default off)', trigger: 'the Slide Show\'s fan (Options; off by default); yields the loop slot to think', profiles: DESKTOP, rateLimit: 'loop', player: 'd32sndd (v1b)',
    loopPeriod: 2.0, build: 'hum',
    derived: 'LIBRARY gives the 2 s seamless loop, the 120 Hz sine at 0.004 and low-passed noise; ours: the 240 Hz partial and the noise level' },
  { name: 'e26-step', group: 'library', tier: 0.07, source: 'L6.2 (step)', trigger: 'InterActivities: a step', profiles: DESKTOP, rateLimit: 'app', player: 'd32sndd (v1b)',
    buffer: 0.06, ops: [['Noise', 0, 0.015, 0.1, 0.35, 0.0005, 0.012], ['Tick', 0, 0.15], ['Sine', 0.012, 0.04, 880, 880, 0.06, 0.003, 0.025, 0.1, 0]],
    derived: 'LIBRARY gives a 15 ms detent click and a 40 ms 880 Hz sine; ours: the v3 Tick inside the click, levels' },
  // the room stings (item 27): one original FM figure per room, 0.4-0.6 s, two or three notes, never falling
  { name: 'e26-room-gallery', group: 'library', tier: 0.10, source: 'L6.2 (room sting; item 27)', trigger: 'entering the Media Gallery (the dark theatre)', profiles: DESKTOP, rateLimit: 'app', player: 'd32sndd (v1b)',
    buffer: 0.6, ops: [['Fm', 0, 0.32, 392.00, 1.0, 1.2, 0.14, 0.08], ['Fm', 0.11, 0.36, 587.33, 1.0, 1.0, 0.15, 0.07], ['Reverb', 0.3, 0.7]],
    notes: [392.00, 587.33], derived: 'ours: a warm rising fifth (G4 D5), FM ratio 1 for the dark theatre' },
  { name: 'e26-room-timeline', group: 'library', tier: 0.10, source: 'L6.2 (room sting; item 27)', trigger: 'entering the Timeline (the white wall of numerals)', profiles: DESKTOP, rateLimit: 'app', player: 'd32sndd (v1b)',
    buffer: 0.45, ops: [['Fm', 0, 0.25, 659.25, 3.5, 0.8, 0.08, 0.06], ['Fm', 0.07, 0.25, 880.00, 3.5, 0.8, 0.08, 0.06], ['Fm', 0.14, 0.3, 987.77, 3.5, 0.8, 0.1, 0.065], ['Reverb', 0.2, 0.6]],
    notes: [659.25, 880.00, 987.77], derived: 'ours: three quick clock-like bells rising (E5 A5 B5), FM ratio 3.5' },
  { name: 'e26-room-atlas', group: 'library', tier: 0.10, source: 'L6.2 (room sting; item 27)', trigger: 'entering the Atlas (the map of the protocol world)', profiles: DESKTOP, rateLimit: 'app', player: 'd32sndd (v1b)',
    buffer: 0.6, ops: [['Fm', 0, 0.35, 587.33, 2.0, 1.0, 0.12, 0.06], ['Fm', 0.09, 0.35, 880.00, 2.0, 0.9, 0.12, 0.06], ['Fm', 0.18, 0.3, 1174.66, 2.0, 0.8, 0.12, 0.055], ['Reverb', 0.3, 0.7]],
    notes: [587.33, 880.00, 1174.66], derived: 'ours: open fifths rising to the octave (D5 A5 D6), FM ratio 2, hollow' },
  { name: 'e26-room-interactivities', group: 'library', tier: 0.10, source: 'L6.2 (room sting; item 27)', trigger: 'entering InterActivities (the museum hall)', profiles: DESKTOP, rateLimit: 'app', player: 'd32sndd (v1b)',
    buffer: 0.6, ops: [['Fm', 0, 0.3, 523.25, 4.0, 0.7, 0.07, 0.08], ['Fm', 0.12, 0.3, 783.99, 4.0, 0.7, 0.07, 0.075], ['Reverb', 0.4, 0.72]],
    notes: [523.25, 783.99], derived: 'ours: two wooden mallet notes rising a fifth (C5 G5), FM ratio 4, in a larger room' },
];

// Loop builders: the dry period is drawn once, then the effects run over two copies (Engine.LoopEffects), so the loop
// repeats without a seam.
const BUILDERS = {
  copy(e, entry) {
    const period = entry.loopPeriod;
    const b = e.buf(period);
    e.Sine(b, 0, period, 120, 120, 0.004, 0, 0, 0.3, 0); // 240 whole cycles in 2 s
    let t = 0.15;
    while (t < period - 0.25) { // no seek crosses the loop's end
      e.Seeks(b, t, 2 + e.rnd.nextInt(2), 0.03, 0.12);
      t += 0.4 + 0.3 * e.rnd.nextDouble();
    }
    e.LoopEffects(b, (two) => e.Reverb(two, 0.15, 0.6));
    return b;
  },
  hum(e, entry) {
    const period = entry.loopPeriod;
    const b = e.buf(period);
    e.Sine(b, 0, period, 120, 120, 0.004, 0, 0, 0.25, 0); // 120 Hz with a 240 Hz partial: 240 whole cycles
    e.CyclicNoise(b, 0.02, 0.04);                         // a dark fan, periodic in the buffer
    return b;
  },
};

// The X bell as d32sndd will make it from an XkbBellNotify event (LOOK-SOUND 8.5): pitch clamped to 82-9000 Hz,
// amplitude 0.20 x percent / 100. Returns { buffer, tier } for Engine.quantise.
function xbellRender(e, pitchHz, durationMs, percent) {
  const f = Math.min(9000, Math.max(82, pitchHz));
  const dur = durationMs / 1000;
  const b = e.buf(dur + 0.002);
  e.Square(b, 0, dur, f, 1, 0.003, 0.005);
  e.LowPass(b, 0.6);
  return { buffer: b, tier: 0.20 * Math.max(0, Math.min(100, percent)) / 100 };
}

function isV3PrimOnly(entry) {
  return !entry.v3 && Array.isArray(entry.ops) && entry.ops.every(([op]) => V3_PRIMITIVES.has(op));
}

function parityKind(entry) {
  if (entry.v3) return 'v3';
  if (isV3PrimOnly(entry)) return 'prim';
  return 'none';
}

BUILDERS.xbell = (e, entry) => xbellRender(e, entry.xbell.pitchHz, entry.xbell.durationMs, entry.xbell.percent).buffer;

module.exports = { CATALOGUE, BUILDERS, xbellRender, parityKind, isV3PrimOnly };
