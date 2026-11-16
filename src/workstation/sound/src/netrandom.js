'use strict';
// .NET Framework's System.Random (the Knuth subtractive generator), as `new Random(seed)` builds it in
// Windows PowerShell 5.1. The v3 engine seeds it with 1994; porting it exactly gives sample parity for every
// noise burst, tick and seek in the approved sounds. Integer maths only: every value stays in [0, 2^31 - 1).

const MBIG = 2147483647; // Int32.MaxValue
const MSEED = 161803398;

class NetRandom {
  constructor(seed) {
    const a = new Array(56).fill(0);
    const subtraction = seed === -2147483648 ? MBIG : Math.abs(seed);
    let mj = MSEED - subtraction;
    a[55] = mj;
    let mk = 1;
    for (let i = 1; i < 55; i++) {
      const ii = (21 * i) % 55;
      a[ii] = mk;
      mk = mj - mk;
      if (mk < 0) mk += MBIG;
      mj = a[ii];
    }
    for (let k = 1; k < 5; k++) {
      for (let i = 1; i < 56; i++) {
        a[i] -= a[1 + ((i + 30) % 55)];
        if (a[i] < 0) a[i] += MBIG;
      }
    }
    this.a = a;
    this.inext = 0;
    this.inextp = 21;
  }

  internalSample() {
    let n = this.inext + 1;
    if (n >= 56) n = 1;
    let p = this.inextp + 1;
    if (p >= 56) p = 1;
    let r = this.a[n] - this.a[p];
    if (r === MBIG) r--;
    if (r < 0) r += MBIG;
    this.a[n] = r;
    this.inext = n;
    this.inextp = p;
    return r;
  }

  // Sample() / NextDouble(): InternalSample() * (1.0 / MBIG)
  nextDouble() {
    return this.internalSample() * (1.0 / MBIG);
  }

  // Next(maxValue): (int)(Sample() * maxValue)
  nextInt(maxValue) {
    return Math.trunc(this.nextDouble() * maxValue);
  }

  // Next(): InternalSample()
  next() {
    return this.internalSample();
  }
}

module.exports = { NetRandom };
