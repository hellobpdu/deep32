/* render_client.c: the interface's side of e26-render (render_client.h). */
#include "library_app.h"
#include "render_client.h"
#include "json.h"
#include "spawn.h"
#include "../common/e26p.h"
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/uio.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

typedef struct {
	long id;
	rc_done_fn fn;
	void *ud;
	unsigned long long deadline;
	int forgotten;
} pend;

typedef struct ofr {
	unsigned char *buf;
	size_t len, off;
	int fd;                     /* sent with the frame's first byte, then closed; -1 none */
	struct ofr *next;
} ofr;

static struct {
	e26_spawn sp;
	int live;
	unsigned gen;               /* bumped whenever the helper goes: a callback that replaced it ends the read loop */
	int watch;                  /* io_watch id, -1 */
	XtInputId wid;              /* the write watch while the socket is full */
	int wid_on;
	unsigned char *in;
	size_t inlen, incap;
	pend *p;
	int np, capp;
	ofr *qh, *qt;
	long next_id;
	char path[600];
	unsigned deadline_ms;
	unsigned starts;
	int timer_armed;
	e26_app *a;
} C = { .watch = -1, .sp = { .fd = -1 } };

static unsigned long long now_ms(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (unsigned long long)ts.tv_sec * 1000ull + (unsigned long long)ts.tv_nsec / 1000000ull;
}

static int xt(e26_app *a) { return a && a->ctx; }

static void unwatch_write(void)
{
	if (C.wid_on) { XtRemoveInput(C.wid); C.wid_on = 0; }
}

/* the helper goes: every pending request fails (overdue gets ocode, the rest code), and the queue is dropped */
static void drop_helper(e26_app *a, const char *code, long overdue, const char *ocode)
{
	pend *list;
	int n, i;
	ofr *f;
	if (C.watch >= 0) io_unwatch(C.watch);
	C.watch = -1;
	unwatch_write();
	if (C.sp.pid > 0) {
		pid_t pid = C.sp.pid;
		kill(pid, SIGKILL);
		(void)waitpid(pid, NULL, WNOHANG);   /* d32library ignores SIGCHLD (sound.c): the system reaps it */
	}
	spawn_close(&C.sp);
	C.live = 0;
	C.gen++;
	free(C.in);
	C.in = NULL;
	C.inlen = C.incap = 0;
	while ((f = C.qh)) {
		C.qh = f->next;
		if (f->fd >= 0) close(f->fd);
		free(f->buf);
		free(f);
	}
	C.qt = NULL;
	list = C.p;
	n = C.np;
	C.p = NULL;
	C.np = C.capp = 0;
	for (i = 0; i < n; i++)                   /* the callbacks may start new requests: they go to a new helper */
		if (!list[i].forgotten) list[i].fn(a, list[i].id == overdue ? ocode : code, NULL, list[i].ud);
	free(list);
}

void rc_stop(void) { if (C.live) drop_helper(C.a, "E_PACK", 0, NULL); }

static int flush(e26_app *a);

static void on_writable(XtPointer cd, int *fd, XtInputId *id)
{
	(void)cd; (void)fd; (void)id;
	unwatch_write();
	flush(C.a);
}

/* writes queued frames while the socket takes them; 0, or -1 when the helper went */
static int flush(e26_app *a)
{
	while (C.qh) {
		ofr *f = C.qh;
		ssize_t w;
		if (f->off == 0 && f->fd >= 0) {
			struct msghdr m;
			struct iovec iov;
			union { struct cmsghdr h; char buf[CMSG_SPACE(sizeof(int))]; } cb;
			struct cmsghdr *c;
			memset(&m, 0, sizeof m);
			memset(&cb, 0, sizeof cb);
			iov.iov_base = f->buf;
			iov.iov_len = f->len;
			m.msg_iov = &iov;
			m.msg_iovlen = 1;
			m.msg_control = cb.buf;
			m.msg_controllen = sizeof cb.buf;
			c = CMSG_FIRSTHDR(&m);
			c->cmsg_level = SOL_SOCKET;
			c->cmsg_type = SCM_RIGHTS;
			c->cmsg_len = CMSG_LEN(sizeof(int));
			memcpy(CMSG_DATA(c), &f->fd, sizeof(int));
			w = sendmsg(C.sp.fd, &m, MSG_NOSIGNAL);
			if (w > 0) { close(f->fd); f->fd = -1; }
		} else w = send(C.sp.fd, f->buf + f->off, f->len - f->off, MSG_NOSIGNAL);
		if (w < 0 && errno == EINTR) continue;
		if (w < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
			if (xt(a) && !C.wid_on) {
				C.wid = XtAppAddInput(a->ctx, C.sp.fd, (XtPointer)XtInputWriteMask, on_writable, NULL);
				C.wid_on = 1;
			}
			return 0;
		}
		if (w <= 0) { drop_helper(a, "E_PACK", 0, NULL); return -1; }
		f->off += (size_t)w;
		if (f->off == f->len) {
			C.qh = f->next;
			if (!C.qh) C.qt = NULL;
			free(f->buf);
			free(f);
		}
	}
	return 0;
}

static int find(long id)
{
	int i;
	for (i = 0; i < C.np; i++)
		if (C.p[i].id == id) return i;
	return -1;
}

/* one whole response frame: its request's callback (the entry is gone before the callback runs) */
static void answer(e26_app *a, const e26p_header *h, const char *body)
{
	char err[200];
	int i = find((long)h->id);
	pend p;
	jv *v;
	if (i < 0) return;                        /* a forgotten request's answer, or a cancellation's */
	p = C.p[i];
	memmove(&C.p[i], &C.p[i + 1], (size_t)(C.np - i - 1) * sizeof(pend));
	C.np--;
	if (p.forgotten) return;
	v = json_parse(body, h->length, err, sizeof err);
	if (!v || v->type != J_OBJ || !json_get(v, "ok") || json_get(v, "ok")->type != J_BOOL) p.fn(a, "E_BAD_FRAME", NULL, p.ud);
	else if (json_get(v, "ok")->b) p.fn(a, NULL, json_get(v, "result"), p.ud);
	else {
		const jv *e = json_get(v, "error");
		const char *code = json_str(e, "code");
		p.fn(a, code ? code : "E_BAD_FRAME", e, p.ud);
	}
	json_free(v);
}

static void on_readable(e26_app *a, int fd, void *ud)
{
	unsigned gen = C.gen;
	(void)ud;
	for (;;) {
		ssize_t r;
		if (C.incap - C.inlen < 65536) {
			size_t cap = C.incap ? C.incap * 2 : 131072;
			unsigned char *nb;
			if (cap > E26P_HEADER + E26P_MAX_BODY + 65536) cap = E26P_HEADER + E26P_MAX_BODY + 65536;
			if (cap <= C.incap) { drop_helper(a, "E_BAD_FRAME", 0, NULL); return; }
			if (!(nb = (unsigned char *)realloc(C.in, cap))) { drop_helper(a, "E_PACK", 0, NULL); return; }
			C.in = nb;
			C.incap = cap;
		}
		r = read(fd, C.in + C.inlen, C.incap - C.inlen);
		if (r < 0 && errno == EINTR) continue;
		if (r < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
		if (r <= 0) { drop_helper(a, "E_PACK", 0, NULL); return; }   /* the helper ended */
		C.inlen += (size_t)r;
		while (C.inlen >= E26P_HEADER) {
			e26p_header h;
			size_t need;
			char *body;
			if (e26p_unpack(C.in, &h) || h.kind != E26P_RESPONSE || (long)h.id > C.next_id) {
				drop_helper(a, "E_BAD_FRAME", 0, NULL);
				return;
			}
			need = E26P_HEADER + h.length;
			if (C.inlen < need) break;
			if (!(body = (char *)malloc(h.length + 1))) { drop_helper(a, "E_PACK", 0, NULL); return; }
			memcpy(body, C.in + E26P_HEADER, h.length);
			body[h.length] = 0;
			memmove(C.in, C.in + need, C.inlen - need);
			C.inlen -= need;
			answer(a, &h, body);
			free(body);
			if (C.gen != gen) return;         /* a callback stopped or replaced the helper */
		}
	}
}

static void watch_cb(e26_app *a, int fd, void *ud) { on_readable(a, fd, ud); }

static int start(e26_app *a)
{
	char *argv[2] = { "e26-render", NULL };
	const char *path = C.path[0] ? C.path : getenv("E26_RENDER");
	if (!path || !path[0]) path = RC_DEFAULT_PATH;
	if (spawn_exec(&C.sp, path, argv)) return -1;
	fcntl(C.sp.fd, F_SETFL, fcntl(C.sp.fd, F_GETFL) | O_NONBLOCK);
	C.live = 1;
	C.starts++;
	C.watch = xt(a) ? io_watch(a, C.sp.fd, watch_cb, NULL) : -1;
	if (a && a->verbose) fprintf(stderr, "d32library: e26-render started (%s, pid %ld)\n", path, (long)C.sp.pid);
	return 0;
}

static void check_deadlines(e26_app *a)
{
	unsigned long long t = now_ms();
	int i;
	for (i = 0; i < C.np; i++)
		if (C.p[i].deadline <= t) {
			if (a && a->verbose) fprintf(stderr, "d32library: e26-render took too long: replaced\n");
			drop_helper(a, "E_PACK", C.p[i].id, "E_TIMEOUT");
			return;
		}
}

static void on_tick(e26_app *a, void *ud)
{
	(void)ud;
	C.timer_armed = 0;
	check_deadlines(a);
	if (C.np && xt(a)) { C.timer_armed = 1; timer_after(a, 1000, on_tick, NULL); }
}

static int queue(e26_app *a, long id, uint32_t kind, const char *body, size_t n, int fd)
{
	ofr *f = (ofr *)calloc(1, sizeof *f);
	e26p_header h;
	if (!f || !(f->buf = (unsigned char *)malloc(E26P_HEADER + n))) { free(f); return -2; }   /* nothing sent */
	h.major = 1; h.minor = 0; h.length = (uint32_t)n; h.id = (uint64_t)id; h.kind = kind;
	e26p_pack(&h, f->buf);
	memcpy(f->buf + E26P_HEADER, body, n);
	f->len = E26P_HEADER + n;
	f->fd = fd;
	if (C.qt) C.qt->next = f; else C.qh = f;
	C.qt = f;
	return flush(a);
}

long rc_call(e26_app *a, const char *op, const char *args, int fd, rc_done_fn fn, void *ud)
{
	size_t n = strlen(op) + strlen(args) + 32;
	char *body;
	long id;
	int r;
	if (a) C.a = a;
	if (!C.live && start(a)) { if (fd >= 0) close(fd); return -1; }
	if (n > E26P_MAX_BODY || !(body = (char *)malloc(n))) { if (fd >= 0) close(fd); return -1; }
	snprintf(body, n, "{\"op\":\"%s\",\"args\":%s}", op, args);
	if (C.np == C.capp) {
		int cap = C.capp ? C.capp * 2 : 16;
		pend *np = (pend *)realloc(C.p, (size_t)cap * sizeof(pend));
		if (!np) { free(body); if (fd >= 0) close(fd); return -1; }
		C.p = np;
		C.capp = cap;
	}
	id = ++C.next_id;
	C.p[C.np].id = id;
	C.p[C.np].fn = fn;
	C.p[C.np].ud = ud;
	C.p[C.np].deadline = now_ms() + (C.deadline_ms ? C.deadline_ms : RC_DEADLINE_MS);
	C.p[C.np].forgotten = 0;
	C.np++;
	r = queue(a, id, E26P_REQUEST, body, strlen(body), fd);
	free(body);
	if (r == -2) {                            /* no memory for the frame: nothing was sent, no callback */
		C.np--;
		if (fd >= 0) close(fd);
		return -1;
	}
	if (r == -1) return id;                   /* the helper went while writing: its callback has run */
	if (xt(a) && !C.timer_armed) { C.timer_armed = 1; timer_after(a, 1000, on_tick, NULL); }
	return id;
}

void rc_forget(long id)
{
	int i = find(id);
	char body[64];
	if (i < 0 || C.p[i].forgotten) return;
	C.p[i].forgotten = 1;
	snprintf(body, sizeof body, "{\"target_id\":%ld}", id);
	if (C.live) queue(C.a, ++C.next_id, E26P_CANCEL, body, strlen(body), -1);
}

int rc_pending(void)
{
	int i, n = 0;
	for (i = 0; i < C.np; i++) n += !C.p[i].forgotten;
	return n;
}

int rc_wait(e26_app *a, int ms)
{
	unsigned long long end = now_ms() + (unsigned long long)(ms > 0 ? ms : 0);
	if (a) C.a = a;
	while (rc_pending()) {
		unsigned long long t = now_ms(), until = end;
		struct pollfd pf;
		int i, r;
		for (i = 0; i < C.np; i++)
			if (C.p[i].deadline < until) until = C.p[i].deadline;
		if (t >= end) break;
		if (!C.live) break;
		pf.fd = C.sp.fd;
		pf.events = POLLIN | (C.qh ? POLLOUT : 0);
		pf.revents = 0;
		r = poll(&pf, 1, until > t ? (int)(until - t) : 0);
		if (r > 0 && (pf.revents & POLLOUT)) flush(a);
		if (r > 0 && C.live && (pf.revents & (POLLIN | POLLHUP | POLLERR))) on_readable(a, C.sp.fd, NULL);
		check_deadlines(a);
	}
	return rc_pending();
}

void rc_config(const char *path, unsigned deadline_ms)
{
	snprintf(C.path, sizeof C.path, "%s", path ? path : "");
	C.deadline_ms = deadline_ms;
}

pid_t rc_helper_pid(void) { return C.live ? C.sp.pid : 0; }
unsigned rc_starts(void) { return C.starts; }
