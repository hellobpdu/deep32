'use strict';
// The 16 approved v3 sounds (reference/sound-cs.txt, Make()), ported statement by statement. Comments are the
// engine's own. Each builder takes an Engine (engine.js) and returns the float buffer before normalisation.

const f32 = Math.fround;
const trunc = Math.trunc;

const V3 = {
  boot(e) {
    // the PC powering on: a soft degauss thump, the POST beep, the disk spinning up and seeking, a warm chime
    const R = e.rate;
    const b = e.buf(6.2);
    e.Sine(b, 0, 0.5, 62, 48, 0.32, 0.004, 0.45, 0.3, 0);
    for (let i = 0; i < trunc(0.8 * R); i++) {
      const t = i / R;
      b[i] += f32(Math.sin(2 * Math.PI * 120 * t + 1.5 * Math.sin(2 * Math.PI * 8 * t)) * Math.exp(-t * 4) * 0.07);
    }
    e.Noise(b, 0, 0.05, 0.22, 0.2, 0.001, 0.04);
    e.Sine(b, 0.9, 0.12, 1000, 1000, 0.13, 0.002, 0.012, 0, 0.3);
    e.Sine(b, 1.0, 2.2, 70, 520, 0.045, 0.5, 0.6, 0.25, 0.1);
    e.Noise(b, 1.0, 2.2, 0.02, 0.05, 0.5, 0.6);
    e.Seeks(b, 1.5, 7, 0.16, 0.2);
    e.Seeks(b, 2.6, 4, 0.12, 0.16);
    const ch = [196.00, 293.66, 392.00, 493.88, 739.99];
    for (let k = 0; k < ch.length; k++) e.Fm(b, 3.4 + k * 0.11, 2.6, ch[k], 1.0, 1.8, 0.9, 0.085);
    e.LowPass(b, 0.55);
    e.Reverb(b, 0.22, 0.72);
    return b;
  },
  rom(e) {
    // the workstation's power-on: the fans swell, one clean system beep, a few faint disk seeks
    const b = e.buf(2.6);
    e.Noise(b, 0, 2.5, 0.035, 0.03, 0.9, 0.7);
    e.Sine(b, 0.35, 0.16, 1046.5, 1046.5, 0.15, 0.006, 0.06, 0.15, 0.05);
    e.Seeks(b, 0.9, 4, 0.2, 0.11);
    e.Reverb(b, 0.18, 0.7);
    return b;
  },
  login(e) {
    // the session starting: three soft FM bells, rising
    const b = e.buf(2.8);
    e.Fm(b, 0, 2.0, 659.25, 3.5, 1.2, 0.6, 0.1);
    e.Fm(b, 0.14, 2.0, 987.77, 3.5, 1.0, 0.65, 0.085);
    e.Fm(b, 0.28, 2.1, 1318.51, 3.5, 0.8, 0.7, 0.06);
    e.Reverb(b, 0.3, 0.75);
    return b;
  },
  connect(e) {
    // a new conversation: two quiet blips, the second higher
    const b = e.buf(0.9);
    e.Sine(b, 0, 0.07, 660, 660, 0.07, 0.004, 0.03, 0.1, 0);
    e.Sine(b, 0.09, 0.09, 990, 990, 0.06, 0.004, 0.05, 0.1, 0);
    e.Reverb(b, 0.35, 0.7);
    return b;
  },
  think(e) {
    // while Codex works (it loops seamlessly every 32 s): a soft workstation hum that breathes slowly (two
    // near-unison tones, 110 and 110.25 Hz, swelling every 8 s), a faint fan, now and then a muffled disk access
    // and twice a distant ping; every period divides 32 s, and no event crosses the loop's end
    const R = e.rate;
    const b = e.buf(32.0);
    for (let i = 0; i < b.length; i++) {
      const t = i / R;
      const breath = 0.55 + 0.45 * Math.sin(2 * Math.PI * t / 8.0);
      b[i] += f32((Math.sin(2 * Math.PI * 110 * t) + Math.sin(2 * Math.PI * 110.25 * t) + 0.35 * Math.sin(2 * Math.PI * 165 * t)) * 0.0075 * breath);
    }
    e.Noise(b, 0, 32.0, 0.012, 0.03, 0, 0);
    const disk = [2.1, 7.4, 11.8, 17.3, 21.0, 26.6];
    for (const t of disk) e.Seeks(b, t, 2 + e.rnd.nextInt(3), 0.05, 0.08);
    e.Fm(b, 9.6, 1.6, 1318.51, 3.5, 0.6, 0.5, 0.025);
    e.Fm(b, 24.2, 1.6, 1174.66, 3.5, 0.6, 0.5, 0.022);
    e.Reverb(b, 0.35, 0.7);
    return b;
  },
  output(e) {
    // the machine writing its answer (it loops while the lines print): a soft, quick terminal murmur, the
    // ticks a little uneven, 36 per 1.2 s
    const R = e.rate;
    const b = e.buf(1.2);
    for (let k = 0; k < 36; k++) {
      const t = k / 30.0 + e.rnd.nextDouble() * 0.008;
      const s0 = trunc(t * R), n = trunc(0.0025 * R);
      let y = 0;
      const vol = 0.035 * (0.7 + 0.3 * e.rnd.nextDouble());
      for (let i = 0; i < n && s0 + i < b.length; i++) {
        y += 0.3 * ((e.rnd.nextDouble() * 2 - 1) - y);
        b[s0 + i] += f32(y * vol * Math.exp(-i / (0.0007 * R)));
      }
    }
    e.Noise(b, 0, 1.2, 0.004, 0.2, 0, 0);
    return b;
  },
  enter(e) {
    // the Return key: a deeper thock
    const b = e.buf(0.05);
    e.Noise(b, 0, 0.005, 0.12, 0.3, 0.0005, 0.004);
    e.Sine(b, 0, 0.022, 150, 115, 0.08, 0.001, 0.018, 0, 0);
    return b;
  },
  page(e) {
    // a page of the manual or the scrollback: a soft swish
    const b = e.buf(0.2);
    e.Noise(b, 0, 0.14, 0.05, 0.08, 0.04, 0.09);
    return b;
  },
  disk(e) {
    // a file written: the disk works for a moment
    const b = e.buf(0.9);
    e.Noise(b, 0, 0.8, 0.02, 0.04, 0.1, 0.3);
    e.Seeks(b, 0.05, 6, 0.07, 0.15);
    e.Reverb(b, 0.15, 0.6);
    return b;
  },
  ok(e) {
    const b = e.buf(0.5);
    e.Fm(b, 0, 0.3, 783.99, 3.5, 0.7, 0.12, 0.07);
    e.Reverb(b, 0.25, 0.65);
    return b;
  },
  down(e) {
    // shutting the RAG down: two soft notes, falling
    const b = e.buf(1.0);
    e.Fm(b, 0, 0.5, 783.99, 3.5, 0.8, 0.2, 0.07);
    e.Fm(b, 0.16, 0.7, 523.25, 3.5, 0.8, 0.3, 0.07);
    e.Reverb(b, 0.3, 0.7);
    return b;
  },
  window(e) {
    // a window opening or closing: a quiet rising sweep
    const b = e.buf(0.6);
    e.Sine(b, 0, 0.18, 300, 700, 0.05, 0.03, 0.12, 0.2, 0);
    e.Noise(b, 0, 0.18, 0.015, 0.1, 0.03, 0.12);
    e.Reverb(b, 0.35, 0.7);
    return b;
  },
  done(e) {
    const b = e.buf(1.1);
    e.Fm(b, 0, 0.8, 1046.5, 3.5, 1.0, 0.25, 0.08);
    e.Fm(b, 0.12, 0.9, 1568.0, 3.5, 0.8, 0.3, 0.07);
    e.Reverb(b, 0.3, 0.7);
    return b;
  },
  bell(e) {
    // the system beep: rounded, short
    const b = e.buf(0.35);
    e.Sine(b, 0, 0.11, 880, 880, 0.15, 0.005, 0.05, 0.2, 0.05);
    e.Reverb(b, 0.2, 0.65);
    return b;
  },
  click(e) {
    // a key: a soft thock
    const b = e.buf(0.03);
    e.Noise(b, 0, 0.004, 0.1, 0.35, 0.0005, 0.003);
    e.Sine(b, 0, 0.012, 190, 160, 0.06, 0.001, 0.009, 0, 0);
    return b;
  },
  crtoff(e) {
    const b = e.buf(1.0);
    e.Sine(b, 0, 0.35, 1800, 90, 0.1, 0.003, 0.15, 0, 0);
    e.Noise(b, 0.03, 0.45, 0.08, 0.15, 0.003, 0.4);
    e.Sine(b, 0.3, 0.22, 58, 44, 0.16, 0.006, 0.18, 0.2, 0);
    e.LowPass(b, 0.5);
    e.Reverb(b, 0.15, 0.65);
    return b;
  },
};

// the engine's Level(): each sound's loudest point as a share of full scale
function v3Level(name) {
  switch (name) {
    case 'boot': return 0.36;
    case 'rom': case 'login': case 'crtoff': return 0.21;
    case 'done': case 'down': return 0.16;
    case 'bell': case 'connect': case 'ok': return 0.14;
    case 'enter': case 'disk': return 0.13;
    case 'click': case 'window': return 0.12;
    case 'page': return 0.1;
    case 'think': return 0.07;
    case 'output': return 0.06;
    default: return 0.15;
  }
}

const V3_NAMES = Object.keys(V3);

module.exports = { V3, V3_NAMES, v3Level };
