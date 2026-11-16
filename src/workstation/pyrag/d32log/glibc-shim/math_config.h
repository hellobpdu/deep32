/* Test shim (not shipped): just enough of glibc's dbl-64 math_config.h to compile glibc 2.43's own e_log.c and
 * e_log_data.c, unchanged, as glibc_log for d32log-check's comparison. The struct is glibc 2.43's (math_config.h
 * lines 265-280 there) for a compiler without __FP_FAST_FMA. */
#ifndef GLIBC_SHIM_MATH_CONFIG_H
#define GLIBC_SHIM_MATH_CONFIG_H
#include <math.h>
#include <stdint.h>
#include <errno.h>
#define WANT_ROUNDING 1
#define WANT_ERRNO 1
#define __glibc_unlikely(x) __builtin_expect ((x), 0)
#define attribute_hidden __attribute__ ((visibility ("hidden")))
static inline uint64_t asuint64 (double f) { union { double f; uint64_t i; } u = {f}; return u.i; }
static inline double asdouble (uint64_t i) { union { uint64_t i; double f; } u = {i}; return u.f; }
/* glibc's math_err.c, the two used by log (WANT_ERRNO 1) */
static double glibc_shim_with_errno (double y, int e) { errno = e; return y; }
static inline double __math_divzero (uint32_t sign)
{ volatile double one = sign ? -1.0 : 1.0; return glibc_shim_with_errno (one / 0.0, ERANGE); }
static inline double __math_invalid (double x)
{ double y = (x - x) / (x - x); return isnan (x) ? y : glibc_shim_with_errno (y, EDOM); }
#define __log_data glibc_log_data
#define LOG_TABLE_BITS 7
#define LOG_POLY_ORDER 6
#define LOG_POLY1_ORDER 12
extern const struct log_data
{
  double ln2hi;
  double ln2lo;
  double poly[LOG_POLY_ORDER - 1]; /* First coefficient is 1.  */
  double poly1[LOG_POLY1_ORDER - 1];
  struct {double invc, logc;} tab[1 << LOG_TABLE_BITS];
#ifndef __FP_FAST_FMA
  struct {double chi, clo;} tab2[1 << LOG_TABLE_BITS];
#endif
} __log_data attribute_hidden;
#endif
