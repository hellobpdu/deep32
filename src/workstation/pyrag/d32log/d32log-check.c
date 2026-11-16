/* d32log-check (Deep32, PYTHON-ON-OPENBSD 5.4, G-libm): is d32_log glibc 2.43's log, bit for bit?
 *
 *   d32log-check proof FILE N1 N2 P [d32|sys]   recompute production's libm proof (d32_ref_record.py libm: the
 *                                               FTS5 IDF domains of the two BM25 tables, N1 and N2 rows, then
 *                                               best_line's log(1 + p/d) for 1 <= d <= p <= P) with d32_log or the
 *                                               system libm, against the recorded IEEE 754 values (little-endian)
 *   d32log-check sweep [LOG2_RANDOM]            d32_log against glibc_log (glibc 2.43's own e_log.c and
 *                                               e_log_data.c, compiled here from its source) on the proof domain,
 *                                               random positive doubles (normal and subnormal), the near-1 window,
 *                                               1 + p/d up to 4096, integers and the special values; errno too
 * Prints counts and indexes only. Exit 0 when nothing differs. */
#include <errno.h>
#include <float.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

double d32_log (double);
double glibc_log (double);
static double sys_log (double x) { return log (x); }

static uint64_t bits (double x) { uint64_t u; memcpy (&u, &x, 8); return u; }
static double dbl (uint64_t u) { double x; memcpy (&x, &u, 8); return x; }
static uint64_t rng = 0x9e3779b97f4a7c15ULL;
static uint64_t next (void) { rng ^= rng >> 12; rng ^= rng << 25; rng ^= rng >> 27; return rng * 2685821657736338717ULL; }

static long ndiff;
static long nseen;

static void same (const char *what, double x)
{
  int e1, e2;
  errno = 0;
  double a = d32_log (x);
  e1 = errno;
  errno = 0;
  double b = glibc_log (x);
  e2 = errno;
  nseen++;
  if ((bits (a) != bits (b) && !(isnan (a) && isnan (b))) || e1 != e2) {
    if (ndiff < 20)
      printf ("DIFF %s x=%016llx d32=%016llx glibc=%016llx errno %d/%d\n", what, (unsigned long long) bits (x),
              (unsigned long long) bits (a), (unsigned long long) bits (b), e1, e2);
    ndiff++;
  }
}

static int sweep (int lg)
{
  long n, i, p, d;
  /* the proof domain (both BM25 tables of p9er-20261102), then 1 + p/d well past best_line's p_max 816 */
  const long tabs[2] = {66717, 11558};
  for (i = 0; i < 2; i++)
    for (n = 0; n <= tabs[i]; n++)
      same ("bm25", ((double) (tabs[i] - n) + 0.5) / ((double) n + 0.5));
  for (p = 1; p <= 4096; p++)
    for (d = 1; d <= p; d++)
      same ("1+p/d", 1.0 + (double) p / (double) d);
  printf ("sweep: bm25 domain + 1+p/d up to 4096: %ld values, %ld differ\n", nseen, ndiff);
  long before = nseen, bd = ndiff;
  for (n = 0; n < (1L << lg); n++) {
    uint64_t u = next () & 0x7fffffffffffffffULL;
    if ((u >> 52) == 0x7ff)
      continue;
    same ("random", dbl (u));
  }
  printf ("sweep: random positive (normal and subnormal): %ld values, %ld differ\n", nseen - before, ndiff - bd);
  before = nseen;
  bd = ndiff;
  const uint64_t lo = bits (1.0 - 0x1p-4), hi = bits (1.0 + 0x1.09p-4);
  for (n = 0; n < (1L << (lg - 3)); n++)
    same ("near1", dbl (lo + next () % (hi - lo)));
  for (n = -100000; n <= 100000; n++)
    same ("near1-ulps", dbl (bits (1.0) + (uint64_t) n));
  printf ("sweep: near 1 (the poly1 window, and every one of the 200,001 ulps around 1): %ld values, %ld differ\n",
          nseen - before, ndiff - bd);
  before = nseen;
  bd = ndiff;
  for (n = 1; n <= 20000000; n++)
    same ("int", (double) n);
  printf ("sweep: integers 1..2e7: %ld values, %ld differ\n", nseen - before, ndiff - bd);
  before = nseen;
  bd = ndiff;
  const double sp[] = {0.0, -0.0, -1.0, INFINITY, -INFINITY, NAN, -NAN, 0x1p-1074, 0x1p-1022, DBL_MAX, 1.0,
                       0x1.fffffffffffffp-1, 0x1.0000000000001p+0, 2.0, 0.5, M_E, 10.0};
  for (i = 0; i < (long) (sizeof sp / sizeof sp[0]); i++)
    same ("special", sp[i]);
  printf ("sweep: special values (errno too): %ld values, %ld differ\n", nseen - before, ndiff - bd);
  printf ("sweep: TOTAL %ld values, %ld differ: %s\n", nseen, ndiff, ndiff ? "FAIL" : "PASS");
  return ndiff != 0;
}

static int proof (const char *file, long n1, long n2, long pmax, const char *which)
{
  double (*f) (double) = strcmp (which, "sys") == 0 ? sys_log : d32_log;
  FILE *fp = fopen (file, "rb");
  if (!fp) {
    perror (file);
    return 2;
  }
  long count = 0, diffs = 0, n, d, t;
  const long tabs[2] = {n1, n2};
  unsigned char buf[8];
  for (t = 0; t < 3; t++) {
    long lim = t < 2 ? tabs[t] : pmax;
    for (n = (t < 2 ? 0 : 1); n <= lim; n++) {
      for (d = 1; d <= (t < 2 ? 1 : n); d++) {
        double x = t < 2 ? ((double) (tabs[t] - n) + 0.5) / ((double) n + 0.5) : 1.0 + (double) n / (double) d;
        if (fread (buf, 1, 8, fp) != 8) {
          printf ("proof: short file at value %ld\n", count);
          return 2;
        }
        uint64_t want = 0;
        for (int b = 7; b >= 0; b--)
          want = (want << 8) | buf[b];
        uint64_t got = bits (f (x));
        if (got != want) {
          if (diffs < 400)
            printf ("DIFF value %ld: proof %016llx here %016llx\n", count, (unsigned long long) want,
                    (unsigned long long) got);
          diffs++;
        }
        count++;
      }
    }
  }
  if (fread (buf, 1, 1, fp) == 1) {
    printf ("proof: the file holds more values than the domain\n");
    return 2;
  }
  fclose (fp);
  printf ("proof (%s): %ld values, %ld differ: %s\n", which, count, diffs, diffs ? "FAIL" : "PASS");
  return diffs != 0;
}

int main (int argc, char **argv)
{
  if (argc >= 6 && strcmp (argv[1], "proof") == 0)
    return proof (argv[2], atol (argv[3]), atol (argv[4]), atol (argv[5]), argc > 6 ? argv[6] : "d32");
  if (argc >= 2 && strcmp (argv[1], "sweep") == 0)
    return sweep (argc > 2 ? atoi (argv[2]) : 28);
  fprintf (stderr, "usage: d32log-check proof FILE N1 N2 P [d32|sys] | sweep [LOG2_RANDOM]\n");
  return 2;
}
