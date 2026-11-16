/* d32logtrace.so (Deep32 identity lane, diagnostic only; never shipped): LD_PRELOAD into the node's CURRENT
 * interpreter (OpenBSD libm log). Every log() call that reaches libm by its dynamic symbol (math.log in the math
 * extension, FTS5 bm25 and the SQL log functions in _sqlite3's static SQLite) returns the system libm value as
 * before, and when d32_log (glibc's log) would have given other bits, the argument is kept. d32trace_take() hands
 * the kept arguments to Python (ctypes) and forgets them, so a caller can say which differing libm values one
 * request evaluated.
 * Build: cc -O2 -ffp-contract=off -fPIC -shared d32logtrace.c d32log.o -o d32logtrace.so -lm -lpthread */
#include <dlfcn.h>
#include <pthread.h>
#include <stdint.h>
#include <string.h>

double d32_log (double);

#define KEEP 65536
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static double kept[KEEP];
static long nkept, ncalls, ndiff;
static double (*real_log) (double);

double log (double x)
{
  if (!real_log)
    real_log = (double (*) (double)) dlsym (RTLD_NEXT, "log");
  double a = real_log (x);
  double b = d32_log (x);
  uint64_t ua, ub;
  memcpy (&ua, &a, 8);
  memcpy (&ub, &b, 8);
  __atomic_add_fetch (&ncalls, 1, __ATOMIC_RELAXED);
  if (ua != ub) {
    pthread_mutex_lock (&mu);
    ndiff++;
    if (nkept < KEEP)
      kept[nkept++] = x;
    pthread_mutex_unlock (&mu);
  }
  return a;
}

/* copies at most max kept arguments into out, returns how many were kept (may exceed max), and forgets them */
long d32trace_take (double *out, long max)
{
  pthread_mutex_lock (&mu);
  long n = nkept, m = n < max ? n : max;
  memcpy (out, kept, (size_t) m * sizeof (double));
  nkept = 0;
  pthread_mutex_unlock (&mu);
  return n;
}

long d32trace_calls (void) { return __atomic_load_n (&ncalls, __ATOMIC_RELAXED); }
long d32trace_diffs (void) { return ndiff; }
