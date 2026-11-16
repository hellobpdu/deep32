#ifndef D32_WS41_H
#define D32_WS41_H

#include "d32core.h"

#include <signal.h>
#include <stdint.h>

#ifndef WS_MAX_BYTES
#define WS_MAX_BYTES (512U * 1024U * 1024U)
#endif
#define WS_SPOOL_NAME_CAP 43

struct ws_spool {
	int fd;
	char name[WS_SPOOL_NAME_CAP];
};

uint64_t ws_now_ms(void);
size_t ws_header_min(const unsigned char magic[4]);
enum d32_error ws_validate_fd(int, off_t);
enum d32_error ws_error_from_exit(int);
int ws_finish(const char *tool, const char *verb, enum d32_error,
    const char *detail, size_t bytes, uint64_t start_ms);

enum d32_error ws_spool_sweep(int dirfd);
enum d32_error ws_spool_copy(int stdin_fd, int dirfd, struct ws_spool *,
    size_t *, const volatile sig_atomic_t *cancelled);
enum d32_error ws_spool_remove(int dirfd, struct ws_spool *);

/* The backend gates before any input is consumed. A successful open transfers
 * an owned private spool dirfd. A NULL viewer name denotes an empty window. */
enum d32_error ws_backend_open(int *dirfd);
enum d32_error ws_backend_view(int dirfd, const char *name,
    const volatile sig_atomic_t *cancelled);

#endif
