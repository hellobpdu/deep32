/* d32log.c (Deep32, PYTHON-ON-OPENBSD 5.4): d32_log = ARM optimized-routines' math/log.c, release v26.07
 * (commit 4be260a5117480382690c6d8c300bc784e927d76, MIT OR Apache-2.0 WITH LLVM-exception, see LICENSE there),
 * the same routine as glibc's sysdeps/ieee754/dbl-64/e_log.c since 2.28.
 *
 * glibc 2.43 on x86-64 picks one of four builds of that one source by CPU (ifunc-avx-fma4.h): fma (FMA+AVX2),
 * fma4, avx, sse2. deep32-engine is an Ivy Bridge (AVX, no FMA, no AVX2): it runs __ieee754_log_avx, built with -mavx
 * and without FMA, so __FP_FAST_FMA is undefined and r = (z - T2[i].chi - T2[i].clo) * invc (the non-FMA path).
 * Here HAVE_FAST_FMA is forced to 0 for the same path, and the object is built with -O2 -ffp-contract=off and no
 * -march (baseline x86-64: no FMA instruction can be generated). Bit-exactness is checked, not assumed:
 * d32log-check (production's proof and an independent build of glibc 2.43's own e_log.c).
 *
 * Build (build-d32log.sh): cc -O2 -ffp-contract=off -fPIC -fvisibility=hidden -I$AOR/math -I$AOR/math/include \
 *                          -c d32log.c -o d32log.o
 */
#define HAVE_FAST_FMA 0      /* glibc's sse2 and avx builds: no __FP_FAST_FMA */
#define WANT_ROUNDING 1      /* as glibc's dbl-64 math_config.h */
#define WANT_ERRNO 1         /* as glibc: log(0) sets ERANGE, log(x < 0) EDOM */
#define USE_GLIBC_ABI 0      /* no __log_finite / __ieee754_log aliases */

#include <errno.h>
#include <float.h>
#include <math.h>            /* read with the real names first */
#include <stdint.h>

__attribute__((__visibility__("hidden"))) double d32_log(double);

#define log d32_log          /* this file only: the definition in log.c becomes d32_log */
#include "log.c"
#undef log
#undef T
#undef T2
#undef B
#undef A
#undef Ln2hi
#undef Ln2lo
#undef N
#undef OFF
#undef LO
#undef HI
#include "log_data.c"
#include "math_err.c"
