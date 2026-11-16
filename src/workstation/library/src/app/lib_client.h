/* lib_client.h: d32library's side of the Library helper (W1-DATA; LIB-PROTOCOL.md). The UI process never blocks on
 * e26-lib and links neither SQLite nor libzim: requests go out on a non-blocking socket, responses are read in
 * bounded slices when the socket is readable, each request has a 5 s deadline, and a helper that dies, sends a bad
 * frame or cannot acknowledge a cancellation is replaced (every request in flight fails E_PACK, E_BAD_FRAME or
 * E_TIMEOUT; nothing is retried behind the caller's back).
 *
 * Rooms use the app-level calls: lib_request(a, "title_prefix", "{\"prefix\":\"bgp\"}", done, ud) starts the helper
 * on first use, pins every request to the generation the first status reported (so a stale bitmap is refused with
 * E_STALE, never mixed), and calls done once with the outcome. args is the request's "args" object as JSON text.
 * The X-free core (libc_*) is what the tests drive with poll(). */
#ifndef E26_LIB_CLIENT_H
#define E26_LIB_CLIENT_H
#include <stddef.h>
#include <stdint.h>
#include "json.h"

typedef struct {
	uint64_t id;
	int ok;                     /* 1: result holds the envelope's result */
	const jv *result;
	const char *code;           /* !ok: "E_NOT_FOUND" ..., or a search error's number as text ("2200") */
	const char *message;        /* !ok: the exact user message */
	const char *generation;     /* the helper's generation, NULL without a pack */
} lib_reply;

typedef void (*lib_cb)(const lib_reply *r, void *ud);

#define LIBC_DEADLINE_MS 5000     /* LIB-PROTOCOL: e26-lib's deadline, queued time included */
#define LIBC_ACK_MS 1500          /* a timed-out request's cancellation must be answered within this, or the helper goes */
#define LIBC_SLICE (256u * 1024u) /* bytes read per readable event: the event loop turns between slices */

typedef struct libconn libconn;

/* helper: the e26-lib program (NULL: $E26_LIB_HELPER, else /d32/a/libexec/e26-lib); argv: extra arguments (NULL) */
libconn *libc_new(const char *helper, char *const *argv);
void libc_free(libconn *c);
/* the socket to watch (starts the helper when it is not running): -1 when it cannot start */
int libc_fd(libconn *c);
/* sends a request; its id, or 0 (args not a JSON object, or no helper). pin: add the pinned generation. */
uint64_t libc_request(libconn *c, const char *op, const char *args, lib_cb cb, void *ud);
/* asks the helper to cancel id (its callback still runs once, with E_CANCELLED or its result) */
int libc_cancel(libconn *c, uint64_t id);
/* reads at most one slice and delivers complete responses: 0, or -1 when the helper went (it restarts on use) */
int libc_readable(libconn *c);
/* writes queued bytes; 1 when some remain */
int libc_flush(libconn *c);
/* deadlines (now in ms of CLOCK_MONOTONIC; 0 reads the clock): overdue requests fail E_TIMEOUT */
void libc_tick(libconn *c, double now_ms);
int libc_pending(const libconn *c);         /* requests waiting for an answer */
const char *libc_generation(const libconn *c);
int libc_restarts(const libconn *c);        /* helpers started after the first */
int libc_pid(const libconn *c);            /* the running helper's process id, 0 when none (tests) */
double libc_now_ms(void);
/* the longest single libc_readable call so far, in ms (the UI's 16 ms budget is checked against it) */
double libc_worst_ms(const libconn *c);

/* ---- the app's connection, over Xt (io_watch and timers) */
struct e26_app;
uint64_t lib_request(struct e26_app *a, const char *op, const char *args, lib_cb cb, void *ud);
int lib_cancel(uint64_t id);
void lib_shutdown(void);

#endif
