/* openat_ipc.h: same-user, same-display local launch hand-off (T-0037). */
#ifndef E26_OPENAT_IPC_H
#define E26_OPENAT_IPC_H
#include <stddef.h>
#define OPENAT_IPC_DISPLAY_MAX 1024
#define OPENAT_IPC_DISPLAY_KEY_SIZE (OPENAT_IPC_DISPLAY_MAX + 8)

typedef struct openat_ipc openat_ipc;
typedef int (*openat_ipc_request)(void *arg, const char *uri);

enum {
	OPENAT_IPC_ERROR = -1,
	OPENAT_IPC_FORWARDED = 0,
	OPENAT_IPC_OWNER = 1,
	OPENAT_IPC_DISABLED = 2
};

/* No endpoint is created without a display. NULL/empty uri requests RAISE.
 * An existing lifetime lock permits only forwarding: any failure is explicit,
 * never permission to start a second UI. The client has a 3-second total limit.
 * The caller owns *out only on OWNER; all other outcomes set it to NULL. */
int openat_ipc_claim(openat_ipc **out, const char *display, const char *uri);

/* Drive from a UI timer at least every 100 ms (a listener watcher is optional).
 * Never waits. Up to 16 bounded connections are serviced serially. The callback
 * sees a revalidated URI, or "" for RAISE; 0 accepts, any other value rejects.
 * Acceptance acknowledges delivery to the UI, not asynchronous Library success.
 * Do not close ipc from the callback. */
void openat_ipc_step(openat_ipc *ipc, openat_ipc_request request, void *arg);
int openat_ipc_fd(const openat_ipc *ipc);
const char *openat_ipc_path(const openat_ipc *ipc);
/* Sandbox declaration only: an enclosing sandbox may unveil this verified
 * directory as "rwc" before its final lock. This module does not partly enable
 * unveil in an otherwise unsandboxed UI. Lifecycle needs unix, rpath, wpath,
 * cpath and flock promises; accepted I/O also needs stdio. */
const char *openat_ipc_directory(const openat_ipc *ipc);
void openat_ipc_close(openat_ipc *ipc);

/* No-I/O derivation except gethostname(), with no DNS/network resolution.
 * Local :N, unix:N, unix/:N, localhost:N and current-hostname:N spellings
 * (also hostname/unix:N and unix/hostname:N) share local:N, ignoring screen.
 * Decimal display/screen numbers must fit INT_MAX. Remote spellings become
 * remote:<original bytes>: remote aliases cannot be proven equivalent offline.
 * The prefixes prevent a remote spelling from colliding with a local key.
 * Input is bounded to OPENAT_IPC_DISPLAY_MAX bytes; a buffer of
 * OPENAT_IPC_DISPLAY_KEY_SIZE holds every key. Returns 0 or -1. */
int openat_ipc_display_key(const char *display, char *buf, size_t size);

/* Pure derivation, also useful for synthetic endpoint tests; creates nothing.
 * Returns 0 on success, -1 on invalid display/insufficient buffer. */
int openat_ipc_endpoint(const char *display, char *path, size_t size);

#endif
