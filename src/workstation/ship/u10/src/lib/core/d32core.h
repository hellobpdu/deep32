#ifndef D32CORE_H
#define D32CORE_H

#include <stddef.h>
#include <sys/types.h>

#include "d32_error.h"
#include "d32_io.h"
#include "d32_json.h"
#include "d32_prefs.h"

/*
 * Execute a caller-selected absolute program with fixed argv and no shell
 * expansion or fallback. Each stream fd is borrowed, or -1 to inherit it.
 * Other descriptors are closed in the child. The child receives only PATH,
 * HOME, DISPLAY and LANG. The caller must serialize environment changes.
 * Success returns a child pid which the caller must reap. On failure there
 * is no child left to reap and *pid is -1.
 */
enum d32_error d32_exec_fixed(const char *abs, char *const argv[], int stdin_fd,
    int stdout_fd, int stderr_fd, pid_t *pid);

/*
 * Send fixed audit fields to LOG_DAEMON at LOG_INFO. Format is a list of
 * allowed field names followed by =%d or =%s, separated by one space.
 * A %s argument must match a built-in token. d32_log_allowed also permits
 * exact matches from a caller's approved operation or policy token table.
 * Never put untrusted input into the format, tag or allowlist.
 */
enum d32_error d32_log(const char *tag, const char *fmt, ...)
    __attribute__((__format__(__printf__, 2, 3)));
enum d32_error d32_log_allowed(const char *tag, const char *const *allow,
    size_t nallow, const char *fmt, ...)
    __attribute__((__format__(__printf__, 4, 5)));

#endif
