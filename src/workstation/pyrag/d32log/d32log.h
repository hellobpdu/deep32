/* d32log.h (Deep32, PYTHON-ON-OPENBSD 5.4): every log() call goes to d32_log, the double log of ARM
 * optimized-routines (the code glibc's log is built from) on the non-FMA path that glibc 2.43 runs on deep32-engine's
 * Ivy Bridge. Used as `cc -include d32log.h` for the SQLite amalgamation (FTS5 bm25's IDF, the SQL ln/log
 * functions) and by one #include in CPython 3.12.14's Modules/mathmodule.c (math.log, lgamma's logs).
 *
 * A function-like macro on purpose: only calls are rerouted. `FUNCTION(log, ...)` (the SQL function's name),
 * the `void (*log)(...)` member of sqlite3_api_routines and the strings stay as they are.
 * <math.h> comes first, so its own declaration of log() is read with the real name. */
#ifndef D32LOG_H
#define D32LOG_H
#include <math.h>
__attribute__((__visibility__("hidden"))) double d32_log(double);
#define log(x) d32_log(x)
#endif
