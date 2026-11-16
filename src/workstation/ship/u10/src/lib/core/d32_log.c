#include "d32core.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <syslog.h>

static const char *const tags[] = {
	"d32-core", "d32-calc", "d32-oui", "d32-ouiq", "d32-wsopen",
	"d32-capdlg", "d32-capture", "d32-sound", "d32-sndd", "d32-mix",
	"d32-sam", "d32-screencap"
};
static const char *const fields[] = {
	"verb", "caller_uid", "target", "result", "bytes", "ms", "id", "detail"
};
static const char *const tokens[] = {
	"ok", "usage", "dataerr", "noinput", "unavailable", "cantcreat", "ioerr",
	"tempfail", "noperm", "file", "open", "start", "stop", "status", "apply",
	"rollback", "save", "load", "check", "lookup", "capture", "read", "write",
	"refused", "missing", "failed", "timeout", "invalid", "none", "local",
	"gated", "deferred", "cancelled", "complete"
};

#define NITEMS(a) (sizeof(a) / sizeof((a)[0]))

static int
table_index(const char *const *table, size_t n, const char *text, size_t len)
{
	size_t i;

	for (i = 0; i < n; i++)
		if (table[i] != NULL && strlen(table[i]) == len &&
		    memcmp(table[i], text, len) == 0)
			return (int)i;
	return -1;
}

static int
token_ok(const char *value, const char *const *allow, size_t nallow)
{
	size_t len;

	if (value == NULL)
		return 0;
	len = strnlen(value, 65);
	if (len == 0 || len > 64 ||
	    strspn(value, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-") != len)
		return 0;
	return table_index(tokens, NITEMS(tokens), value, len) >= 0 ||
	    table_index(allow, nallow, value, len) >= 0;
}

static enum d32_error
log_fields(const char *tag, const char *const *allow, size_t nallow,
    const char *fmt, va_list args)
{
	char message[640];
	const char *equal, *value;
	size_t used = 0;
	unsigned seen = 0;
	int field, n;

	if (tag == NULL || table_index(tags, NITEMS(tags), tag, strnlen(tag, 32)) < 0 ||
	    fmt == NULL || fmt[0] == '\0' || (allow == NULL && nallow != 0) ||
	    nallow > 4096 || strnlen(fmt, 128) >= 128)
		return D32E_INVAL;
	while (*fmt != '\0') {
		equal = strchr(fmt, '=');
		if (equal == NULL || equal[1] != '%' || equal[2] == '\0' ||
		    (equal[3] != '\0' && equal[3] != ' '))
			return D32E_INVAL;
		field = table_index(fields, NITEMS(fields), fmt, (size_t)(equal - fmt));
		if (field < 0 || (seen & (1U << field)) != 0)
			return D32E_INVAL;
		seen |= 1U << field;
		if (equal[2] == 'd') {
			n = snprintf(message + used, sizeof(message) - used, "%s%s=%d",
			    used != 0 ? " " : "", fields[field], va_arg(args, int));
		} else if (equal[2] == 's') {
			value = va_arg(args, const char *);
			if (!token_ok(value, allow, nallow))
				return D32E_INVAL;
			n = snprintf(message + used, sizeof(message) - used, "%s%s=%s",
			    used != 0 ? " " : "", fields[field], value);
		} else {
			return D32E_INVAL;
		}
		if (n < 0 || (size_t)n >= sizeof(message) - used)
			return D32E_LIMIT;
		used += (size_t)n;
		fmt = equal + 3;
		if (*fmt == ' ' && *++fmt == '\0')
			return D32E_INVAL;
	}
#ifdef __OpenBSD__
	/* The reentrant interface avoids changing the process-wide log identity. */
	struct syslog_data log = SYSLOG_DATA_INIT;

	openlog_r(tag, LOG_PID, LOG_DAEMON, &log);
	syslog_r(LOG_DAEMON | LOG_INFO, &log, "%s", message);
	closelog_r(&log);
#else
	syslog(LOG_DAEMON | LOG_INFO, "%s: %s", tag, message);
#endif
	return D32E_OK;
}

enum d32_error
d32_log(const char *tag, const char *fmt, ...)
{
	enum d32_error result;
	va_list args;

	va_start(args, fmt);
	result = log_fields(tag, NULL, 0, fmt, args);
	va_end(args);
	return result;
}

enum d32_error
d32_log_allowed(const char *tag, const char *const *allow, size_t nallow,
    const char *fmt, ...)
{
	enum d32_error result;
	va_list args;

	va_start(args, fmt);
	result = log_fields(tag, allow, nallow, fmt, args);
	va_end(args);
	return result;
}
