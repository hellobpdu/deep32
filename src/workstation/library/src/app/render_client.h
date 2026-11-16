/* render_client.h: the interface's side of e26-render (W1-IMAGE; LIB-PROTOCOL.md). One helper per Deep32 Library,
 * started on first use (spawn.h) and spoken to in E26P frames on its socket: requests are queued and written as the
 * socket takes them, a descriptor travels with its request (SCM_RIGHTS on the frame's first byte), answers are read
 * when the socket is readable (io.c through Xt) and handed to the request's callback. Nothing here waits: the
 * interface never blocks on the helper.
 *
 * Kill and replace: a helper that ends, breaks the framing or answers what nobody asked fails every pending request
 * (E_PACK, or E_BAD_FRAME) and is replaced on the next request. A request older than its deadline (30 s,
 * LIB-PROTOCOL) fails E_TIMEOUT, and the helper, which serves one request at a time and so is stuck in it, is killed
 * and replaced the same way; the requests queued behind it fail E_PACK and their callers ask again.
 *
 * The helper is $E26_RENDER, else /d32/active/bin/e26-render (beside d32library on the node). */
#ifndef E26_RENDER_CLIENT_H
#define E26_RENDER_CLIENT_H
#include <stddef.h>
#include <sys/types.h>

struct e26_app;
struct jv;

#define RC_DEFAULT_PATH "/d32/active/bin/e26-render"
#define RC_DEADLINE_MS 30000u

/* the answer to a request: code NULL and result the envelope's "result", or code ("E_TIMEOUT", ...) and result the
 * error object (or NULL); both are only valid during the call */
typedef void (*rc_done_fn)(struct e26_app *a, const char *code, const struct jv *result, void *ud);

/* sends op with args (a JSON object's text) and, when fd >= 0, that descriptor (taken: closed once sent, or on
 * failure). Returns the request's id (> 0), or -1 when the helper cannot be started (fd closed; fn not called). */
long rc_call(struct e26_app *a, const char *op, const char *args, int fd, rc_done_fn fn, void *ud);
/* the caller no longer wants request id's answer: its callback will not run (the helper is told; LIB-PROTOCOL
 * cancellation) */
void rc_forget(long id);
int rc_pending(void);                    /* requests not yet answered */
/* tests and tools: serves the socket and the deadlines for up to ms without Xt; returns rc_pending() */
int rc_wait(struct e26_app *a, int ms);
/* the helper's path (NULL: $E26_RENDER or the default) and the deadline (0: RC_DEADLINE_MS), for the tests */
void rc_config(const char *path, unsigned deadline_ms);
pid_t rc_helper_pid(void);               /* 0 when none runs */
unsigned rc_starts(void);                /* helpers started so far (a replacement counts) */
void rc_stop(void);                      /* ends the helper; pending requests fail E_PACK */

#endif
