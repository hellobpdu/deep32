/* Test only: glibc 2.43's e_log.c and e_log_data.c, unchanged, compiled as glibc_log (the sse2/avx build: no
 * __FP_FAST_FMA). Build: cc -O2 -ffp-contract=off -I glibc-shim -c glibc_log.c, with the two glibc files copied
 * in as e_log.c and e_log_data.c. */
#define __log glibc_log
double glibc_log (double);
#include "e_log.c"
#include "e_log_data.c"
