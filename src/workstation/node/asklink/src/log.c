/*
 * Logging: compile-time fixed formats with numeric arguments only
 * (ISO 3.1 item 7).  No peer-derived text, query, answer or key material
 * is ever logged.
 */
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <syslog.h>
#include <time.h>
#include <unistd.h>

#include "chatd.h"

static int	 log_debugging;
static int	 log_verbose;
static const char *log_tag = "d32-chatd";

void
log_init(int debug, int verbose, const char *tag)
{
	log_debugging = debug;
	log_verbose = verbose;
	if (tag)
		log_tag = tag;
	if (!debug)
		openlog(log_tag, LOG_PID | LOG_NDELAY, LOG_DAEMON);
	tzset();
}

void
log_setverbose(int v)
{
	log_verbose = v;
}

static void	 vlog(int, const char *, va_list)
		    __attribute__((__format__(__printf__, 2, 0)));

static void
vlog(int pri, const char *fmt, va_list ap)
{
	struct timespec	 ts;

	if (log_debugging) {
		clock_gettime(CLOCK_REALTIME, &ts);
		fprintf(stderr, "%lld.%03ld %s[%d]: ", (long long)ts.tv_sec,
		    ts.tv_nsec / 1000000, log_tag, (int)getpid());
		vfprintf(stderr, fmt, ap);
		fputc('\n', stderr);
		fflush(stderr);
	} else
		vsyslog(pri, fmt, ap);
}

void
log_info(const char *fmt, ...)
{
	va_list	 ap;

	va_start(ap, fmt);
	vlog(LOG_INFO, fmt, ap);
	va_end(ap);
}

void
log_warnx(const char *fmt, ...)
{
	va_list	 ap;

	va_start(ap, fmt);
	vlog(LOG_WARNING, fmt, ap);
	va_end(ap);
}

void
log_debug(const char *fmt, ...)
{
	va_list	 ap;

	if (!log_verbose)
		return;
	va_start(ap, fmt);
	vlog(LOG_DEBUG, fmt, ap);
	va_end(ap);
}

void
fatalx(const char *fmt, ...)
{
	va_list	 ap;

	va_start(ap, fmt);
	vlog(LOG_CRIT, fmt, ap);
	va_end(ap);
	exit(1);
}
