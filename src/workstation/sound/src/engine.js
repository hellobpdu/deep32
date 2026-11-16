'use strict';
// The approved v3 sound engine (reference/sound-cs.txt, the owner 11-03), ported line by line from C# to JavaScript.
//
// Parity rules kept here:
// - buffers are Float32Array, as C#'s float[]: every store rounds to float32 exactly as C#'s `(float)` casts and
//   float += float do (a double sum of two float32 values rounded once to float32 equals the float32 sum);
// - all other maths is double, as in C#; integer casts truncate toward zero ((int) in C#);
// - noise comes from a port of .NET Framework's System.Random (netrandom.js), consumed in the same order;
// - Wav() rounds with Math.Round's default, round-half-to-even.
// The sample rate is a parameter (the C# engine has `const int Rate = 44100`): 44100 for the parity test, 48000 for
// the shipped files (sndiod's native rate, LOOK-SOUND 8.7).
//
// Primitives below the line "Deep32 additions" are new (not in the v3 engine) and are used only by recipes that the
// v3 primitives cannot express (soft FM attacks, high-pass, the X bell's square, seamless noise loops).

const { NetRandom } = require('./netrandom');

const f32 = Math.fround;
const trunc = Math.trunc;

// C# Math.Round(double): MidpointRounding.ToEven
function roundHalfEven(x) {
  const f = Math.floor(x);
  const d = x - f;
  if (d < 0.5) return f;
  if (d > 0.5) return f + 1;
  return f % 2 === 0 ? f : f + 1;
}

class Engine {
  constructor(rate, seed = 1994) {
    this.rate = rate;
    this.rnd = new NetRandom(seed);
  }

  reseed(seed = 1994) {
    this.rnd = new NetRandom(seed);
  }

  buf(sec) {
    return new Float32Array(trunc(sec * this.rate));
  }

  static env(t, dur, atk, rel) {
    let e = 1;
    if (atk > 0 && t < atk) e = t / atk;
    const left = dur - t;
    if (rel > 0 && left < rel) e *= Math.max(0, left / rel);
    return e;
  }

  // a sine with optional 2nd and 3rd partials and an exponential sweep f0 -> f1
  Sine(b, at, dur, f0, f1, vol, atk, rel, h2, h3) {
    const R = this.rate;
    const s0 = trunc(at * R), n = trunc(dur * R);
    let ph = 0;
    for (let i = 0; i < n && s0 + i < b.length; i++) {
      const t = i / R, x = i / n;
      const f = f0 * Math.pow(f1 / f0, x);
      ph += f / R; ph -= Math.floor(ph);
      const v = Math.sin(2 * Math.PI * ph) + h2 * Math.sin(4 * Math.PI * ph) + h3 * Math.sin(6 * Math.PI * ph);
      b[s0 + i] += f32(v * vol * Engine.env(t, dur, atk, rel));
    }
  }

  // an FM bell / electric piano: the modulation index dies away faster than the tone
  Fm(b, at, dur, f, ratio, index, decay, vol) {
    const R = this.rate;
    const s0 = trunc(at * R), n = trunc(dur * R);
    for (let i = 0; i < n && s0 + i < b.length; i++) {
      const t = i / R;
      let a = Math.exp(-t / decay);
      if (t < 0.003) a *= t / 0.003;
      const left = dur - t;
      if (left < 0.08) a *= Math.max(0, left / 0.08);
      const ix = index * Math.exp(-t / (decay * 0.35));
      const v = Math.sin(2 * Math.PI * f * t + ix * Math.sin(2 * Math.PI * f * ratio * t));
      b[s0 + i] += f32(v * vol * a);
    }
  }

  // noise through a one-pole low-pass (lp near 0 = dark, near 1 = bright)
  Noise(b, at, dur, vol, lp, atk, rel) {
    const R = this.rate;
    const s0 = trunc(at * R), n = trunc(dur * R);
    let y = 0;
    for (let i = 0; i < n && s0 + i < b.length; i++) {
      const t = i / R;
      y += lp * ((this.rnd.nextDouble() * 2 - 1) - y);
      b[s0 + i] += f32(y * vol * Engine.env(t, dur, atk, rel));
    }
  }

  // a muffled disk-head tick, heard through the case
  Tick(b, at, vol) {
    const R = this.rate;
    const s0 = trunc(at * R), n = trunc(0.004 * R);
    let y = 0;
    for (let i = 0; i < n && s0 + i < b.length; i++) {
      y += 0.25 * ((this.rnd.nextDouble() * 2 - 1) - y);
      b[s0 + i] += f32(y * vol * Math.exp(-i / (0.0009 * R)));
    }
    this.Sine(b, at, 0.006, 1400, 1100, vol * 0.15, 0.0005, 0.004, 0, 0);
  }

  Seeks(b, at, count, spread, vol) {
    let t = at;
    for (let k = 0; k < count; k++) {
      const v = vol * (0.6 + 0.4 * this.rnd.nextDouble()); // C# evaluates the argument before Tick runs
      this.Tick(b, t, v);
      t += spread * (0.4 + this.rnd.nextDouble());
    }
  }

  LowPass(b, a) {
    let y = 0;
    for (let i = 0; i < b.length; i++) { y += a * (b[i] - y); b[i] = y; }
  }

  // a small Schroeder room: four damped combs, two all-passes, mixed in
  Reverb(b, mix, fb) {
    const R = this.rate;
    const cs = [0.0297, 0.0371, 0.0411, 0.0437];
    const wet = new Float32Array(b.length);
    for (const c of cs) {
      const d = trunc(c * R); const line = new Float32Array(d); let p = 0; let damp = 0;
      for (let i = 0; i < b.length; i++) {
        const o = line[p];
        damp = o * 0.55 + damp * 0.45;
        line[p] = b[i] + damp * fb;
        p = (p + 1) % d;
        wet[i] += o * 0.25;
      }
    }
    const aps = [0.005, 0.0017];
    for (const c of aps) {
      const d = trunc(c * R); const line = new Float32Array(d); let p = 0;
      for (let i = 0; i < wet.length; i++) {
        const bo = line[p], x = wet[i];
        wet[i] = -0.5 * x + bo;
        line[p] = x + 0.5 * bo;
        p = (p + 1) % d;
      }
    }
    for (let i = 0; i < b.length; i++) b[i] = b[i] * (1 - mix) + wet[i] * mix;
  }

  // ---------------------------------------------------------------- Deep32 additions (not in the v3 engine)

  // Fm with a chosen linear attack instead of the fixed 3 ms (Deep32 Library's theme: "soft 30 ms attacks")
  FmSoft(b, at, dur, f, ratio, index, decay, vol, atk) {
    const R = this.rate;
    const s0 = trunc(at * R), n = trunc(dur * R);
    for (let i = 0; i < n && s0 + i < b.length; i++) {
      const t = i / R;
      let a = Math.exp(-t / decay);
      if (t < atk) a *= t / atk;
      const left = dur - t;
      if (left < 0.08) a *= Math.max(0, left / 0.08);
      const ix = index * Math.exp(-t / (decay * 0.35));
      const v = Math.sin(2 * Math.PI * f * t + ix * Math.sin(2 * Math.PI * f * ratio * t));
      b[s0 + i] += f32(v * vol * a);
    }
  }

  // one-pole RC high-pass at fc Hz over the whole buffer
  HighPass(b, fc) {
    const dt = 1 / this.rate, rc = 1 / (2 * Math.PI * fc), k = rc / (rc + dt);
    let y = 0, xPrev = 0;
    for (let i = 0; i < b.length; i++) {
      const x = b[i];
      y = k * (y + x - xPrev);
      xPrev = x;
      b[i] = y;
    }
  }

  // the X bell's tone (LOOK-SOUND 8.5): a band-limited square (odd harmonics below 16 kHz, the 4/pi sum) with a
  // linear attack and release
  Square(b, at, dur, f, vol, atk, rel) {
    const R = this.rate;
    const s0 = trunc(at * R), n = trunc(dur * R);
    const top = Math.min(16000, R / 2 - 1);
    const ks = [];
    for (let k = 1; k * f < top; k += 2) ks.push(k);
    for (let i = 0; i < n && s0 + i < b.length; i++) {
      const t = i / R;
      let v = 0;
      for (const k of ks) v += Math.sin(2 * Math.PI * k * f * t) / k;
      b[s0 + i] += f32(v * (4 / Math.PI) * vol * Engine.env(t, dur, atk, rel));
    }
  }

  // noise that repeats seamlessly every buffer length: white noise drawn once, the one-pole low-pass run around the
  // ring twice so the filter state at the end equals the state at the start (for loops only)
  CyclicNoise(b, vol, lp) {
    const n = b.length;
    const w = new Float64Array(n);
    for (let i = 0; i < n; i++) w[i] = this.rnd.nextDouble() * 2 - 1;
    let y = 0;
    for (let i = 0; i < n; i++) y += lp * (w[i] - y);
    for (let i = 0; i < n; i++) { y += lp * (w[i] - y); b[i] += f32(y * vol); }
  }

  // Loop effects: tile the dry period twice, run the effects over both, keep the second copy, so the reverb and
  // filter tails of the end of the loop are heard at its start, as they are when it repeats.
  LoopEffects(period, effects) {
    const n = period.length;
    const two = new Float32Array(2 * n);
    two.set(period, 0); two.set(period, n);
    effects(two);
    period.set(two.subarray(n));
  }

  // A Deep32 layer over the engine's output (not a recipe change): a raised-cosine fade over the last `sec` seconds,
  // so a reverb tail that the buffer cuts off ends without a step. Applied to one-shots before normalisation; it
  // never touches the peak (render-lib checks), and outside the last `sec` the samples are the engine's own.
  static declick(b, rate, sec = 0.010) {
    const n = Math.min(b.length, trunc(sec * rate));
    const start = b.length - n;
    for (let i = 0; i < n; i++) b[start + i] *= 0.5 * (1 + Math.cos(Math.PI * (i + 1) / n));
  }

  // ---------------------------------------------------------------- normalisation and files

  // C#'s Wav(): scale so the loudest point sits at `level`, round half to even, clamp to 16 bits
  static quantise(s, level) {
    let peak = 0;
    for (let i = 0; i < s.length; i++) { const a = Math.abs(s[i]); if (a > peak) peak = a; }
    const g = peak > 0 ? level / peak : 1;
    const out = new Int16Array(s.length);
    for (let i = 0; i < s.length; i++) {
      let q = roundHalfEven(s[i] * g * 32767);
      if (q > 32767) q = 32767; if (q < -32768) q = -32768;
      out[i] = q;
    }
    return out;
  }

  // Run a recipe given as data: [['Fm', at, dur, ...], ['Reverb', mix, fb], ...]
  runOps(b, ops) {
    for (const [op, ...args] of ops) {
      if (typeof this[op] !== 'function' || op === 'constructor') throw new Error(`unknown primitive ${op}`);
      this[op](b, ...args);
    }
  }
}

// The primitives the C# engine has, so a data recipe made only of them can be rendered by the approved engine itself
// (parity/render-ref.ps1 calls them through reflection).
const V3_PRIMITIVES = new Set(['Sine', 'Fm', 'Noise', 'Tick', 'Seeks', 'LowPass', 'Reverb']);

module.exports = { Engine, roundHalfEven, V3_PRIMITIVES };
