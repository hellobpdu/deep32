/* lib_client.c: the UI side of the Library helper (W1-DATA; lib_client.h, LIB-PROTOCOL.md). */
#include "library_app.h"
#include "lib_client.h"
#include "spawn.h"
#include "../common/e26j.h"
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#define HDR 24
#define MAXBODY 8388608u
#define MAXRESTARTS 5           /* within RESTART_WINDOW: then the helper stays down for the window */
#define RESTART_WINDOW 60000.0

typedef struct {
	uint64_t id;
	lib_cb cb;
	void *ud;
	double deadline;
	int internal;               /* 1: the client's own status; 2: a cancellation (no callback) */
	uint64_t target;            /* a cancellation: what it cancels */
	int abandoned;              /* timed out: a late answer is discarded */
	double ack_by;              /* abandoned: its cancellation must be answered by then */
} pend;

struct libconn {
	char helper[512];
	char *argv[8];
	e26_spawn sp;
	int up;
	uint64_t next_id;
	pend *p;
	int np, cap;
	unsigned char *in;          /* a frame being read */
	size_t nin, capin;
	unsigned char *out;         /* bytes not yet written */
	size_t nout, capout;
	char generation[130];
	int pinned;
	int starts;
	double start_times[MAXRESTARTS];
	double worst;
};

double libc_now_ms(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (double)ts.tv_sec * 1000.0 + (double)ts.tv_nsec / 1e6;
}

libconn *libc_new(const char *helper, char *const *argv)
{
	libconn *c = (libconn *)calloc(1, sizeof *c);
	const char *h = helper ? helper : getenv("E26_LIB_HELPER");
	int i;
	if (!c) return NULL;
	snprintf(c->helper, sizeof c->helper, "%s", h && *h ? h : "/d32/a/libexec/e26-lib");
	c->argv[0] = (char *)"e26-lib";
	for (i = 0; argv && argv[i] && i < 6; i++) c->argv[i + 1] = argv[i];
	c->sp.fd = -1;
	c->next_id = 1;
	return c;
}

static pend *find(libconn *c, uint64_t id)
{
	int i;
	for (i = 0; i < c->np; i++) if (c->p[i].id == id) return &c->p[i];
	return NULL;
}

static void drop(libconn *c, pend *e)
{
	int i = (int)(e - c->p);
	memmove(&c->p[i], &c->p[i + 1], (size_t)(c->np - i - 1) * sizeof *c->p);
	c->np--;
}

static const char *message_of(const char *code)
{
	static const char *const m[][2] = {
		{ "E_BAD_FRAME", "The helper sent an invalid message." }, { "E_PACK", "The Library pack is unavailable." },
		{ "E_TIMEOUT", "The helper took too long. Please try again." }, { "E_CANCELLED", "The request was cancelled." },
	};
	size_t i;
	for (i = 0; i < sizeof m / sizeof m[0]; i++) if (!strcmp(m[i][0], code)) return m[i][1];
	return "The request could not be answered.";
}

/* the caller's callback with an outcome the client made itself */
static void fail_one(libconn *c, pend e, const char *code)
{
	lib_reply r;
	if (e.internal || e.abandoned || !e.cb) return;
	memset(&r, 0, sizeof r);
	r.id = e.id;
	r.code = code;
	r.message = message_of(code);
	r.generation = c->pinned ? c->generation : NULL;
	e.cb(&r, e.ud);
}

/* the helper is gone or misbehaved: every request in flight fails (the first with first_code), the socket closes */
static void down(libconn *c, const char *code)
{
	if (c->up) spawn_close(&c->sp);
	c->up = 0;
	c->sp.fd = -1;
	c->nin = c->nout = 0;
	c->pinned = 0;
	while (c->np) {
		pend e = c->p[0];
		drop(c, &c->p[0]);
		fail_one(c, e, code);
		code = "E_PACK";
	}
}

static void put32(unsigned char *p, uint32_t v) { p[0] = (unsigned char)(v >> 24); p[1] = (unsigned char)(v >> 16); p[2] = (unsigned char)(v >> 8); p[3] = (unsigned char)v; }
static uint32_t get32(const unsigned char *p) { return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3]; }

static int queue_out(libconn *c, const void *d, size_t n)
{
	if (c->nout + n > c->capout) {
		size_t cap = c->capout ? c->capout : 16384;
		unsigned char *np;
		while (cap < c->nout + n) cap *= 2;
		if (!(np = (unsigned char *)realloc(c->out, cap))) return -1;
		c->out = np;
		c->capout = cap;
	}
	memcpy(c->out + c->nout, d, n);
	c->nout += n;
	return 0;
}

int libc_flush(libconn *c)
{
	size_t off = 0;
	while (c->up && off < c->nout) {
		ssize_t w = send(c->sp.fd, c->out + off, c->nout - off, MSG_NOSIGNAL);   /* never SIGPIPE the UI */
		if (w < 0 && errno == EINTR) continue;
		if (w < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
		if (w <= 0) { down(c, "E_PACK"); return 0; }
		off += (size_t)w;
	}
	if (off) { memmove(c->out, c->out + off, c->nout - off); c->nout -= off; }
	return c->nout > 0;
}

static int frame(libconn *c, uint64_t id, uint32_t kind, const char *body, size_t len)
{
	unsigned char h[HDR];
	memcpy(h, "E26P", 4);
	h[4] = 0; h[5] = 1; h[6] = 0; h[7] = 0;
	put32(h + 8, (uint32_t)len);
	put32(h + 12, (uint32_t)(id >> 32));
	put32(h + 16, (uint32_t)id);
	put32(h + 20, kind);
	if (queue_out(c, h, HDR) || queue_out(c, body, len)) return -1;
	libc_flush(c);
	return 0;
}

static uint64_t add(libconn *c, lib_cb cb, void *ud, int internal, uint64_t target)
{
	pend *e;
	if (c->np == c->cap) {
		int cap = c->cap ? c->cap * 2 : 16;
		pend *np = (pend *)realloc(c->p, (size_t)cap * sizeof *np);
		if (!np) return 0;
		c->p = np;
		c->cap = cap;
	}
	e = &c->p[c->np++];
	memset(e, 0, sizeof *e);
	e->id = c->next_id++;
	e->cb = cb;
	e->ud = ud;
	e->internal = internal;
	e->target = target;
	e->deadline = libc_now_ms() + LIBC_DEADLINE_MS;
	return e->id;
}

static uint64_t send_req(libconn *c, const char *op, const char *args, lib_cb cb, void *ud, int internal)
{
	e26j_buf b;
	uint64_t id;
	e26j_init(&b);
	e26j_lit(&b, "{\"op\":");
	e26j_cstr(&b, op);
	e26j_lit(&b, ",\"args\":");
	e26j_lit(&b, args);
	if (c->pinned) { e26j_lit(&b, ",\"generation\":"); e26j_cstr(&b, c->generation); }
	e26j_lit(&b, "}");
	if (b.bad || b.n > MAXBODY || !(id = add(c, cb, ud, internal, 0))) { e26j_free(&b); return 0; }
	if (frame(c, id, 1, b.p, b.n)) { drop(c, find(c, id)); id = 0; }
	e26j_free(&b);
	return id;
}

static void on_status(const lib_reply *r, void *ud)
{
	libconn *c = (libconn *)ud;
	if (r->ok && r->generation && strlen(r->generation) < sizeof c->generation) {
		snprintf(c->generation, sizeof c->generation, "%s", r->generation);
		c->pinned = 1;
	}
}

static int start(libconn *c)
{
	double now = libc_now_ms();
	int i, recent = 0;
	for (i = 0; i < MAXRESTARTS; i++) if (c->start_times[i] && now - c->start_times[i] < RESTART_WINDOW) recent++;
	if (recent >= MAXRESTARTS) return -1;        /* a crash loop: stay down for the window */
	if (spawn_exec(&c->sp, c->helper, c->argv)) { c->sp.fd = -1; return -1; }
	fcntl(c->sp.fd, F_SETFL, fcntl(c->sp.fd, F_GETFL) | O_NONBLOCK);
	c->start_times[c->starts % MAXRESTARTS] = now;
	c->starts++;
	c->up = 1;
	c->pinned = 0;
	send_req(c, "status", "{}", on_status, c, 1);
	return 0;
}

int libc_fd(libconn *c)
{
	if (!c->up && start(c)) return -1;
	return c->sp.fd;
}

uint64_t libc_request(libconn *c, const char *op, const char *args, lib_cb cb, void *ud)
{
	jv *v;
	int obj;
	if (!op || !args || libc_fd(c) < 0) return 0;
	v = e26j_parse(args, strlen(args), NULL, 0);
	obj = v && v->type == J_OBJ;
	json_free(v);
	if (!obj) return 0;
	return send_req(c, op, args, cb, ud, 0);
}

int libc_cancel(libconn *c, uint64_t id)
{
	pend *e = find(c, id);
	char body[48];
	uint64_t cid;
	if (!e || e->internal || !c->up) return -1;
	snprintf(body, sizeof body, "{\"target_id\":%llu}", (unsigned long long)id);
	if (!(cid = add(c, NULL, NULL, 2, id))) return -1;
	return frame(c, cid, 4, body, strlen(body));
}

/* one complete response frame */
static int deliver(libconn *c, uint64_t id, const char *body, size_t len)
{
	jv *v = e26j_parse(body, len, NULL, 0);
	const jv *ok = v ? json_get(v, "ok") : NULL, *g = v ? json_get(v, "generation") : NULL, *err, *code, *msg;
	pend *e = find(c, id);
	pend copy;
	lib_reply r;
	char num[32];
	if (!v || v->type != J_OBJ || !ok || ok->type != J_BOOL || !g || (g->type != J_STR && g->type != J_NULL)) { json_free(v); return -1; }
	memset(&r, 0, sizeof r);
	r.id = id;
	r.ok = ok->b;
	r.generation = g->type == J_STR ? g->str : NULL;
	if (r.ok) {
		if (!(r.result = json_get(v, "result"))) { json_free(v); return -1; }
	} else {
		err = json_get(v, "error");
		code = err ? json_get(err, "code") : NULL;
		msg = err ? json_get(err, "message") : NULL;
		if (!code || !msg || msg->type != J_STR || (code->type != J_STR && code->type != J_NUM)) { json_free(v); return -1; }
		if (code->type == J_STR) r.code = code->str;
		else { snprintf(num, sizeof num, "%s", code->str ? code->str : "0"); r.code = num; }
		r.message = msg->str;
	}
	if (!e) { json_free(v); return 0; }               /* a late answer to a request already given up: discarded */
	copy = *e;
	drop(c, e);
	if (copy.internal == 2) {                         /* a cancellation's answer: the abandoned target is settled */
		pend *t = find(c, copy.target);
		if (t && t->abandoned) t->ack_by = 0;
	} else if (!copy.abandoned && copy.cb) copy.cb(&r, copy.ud);
	json_free(v);
	return 0;
}

int libc_readable(libconn *c)
{
	double t0 = libc_now_ms(), t;
	size_t got = 0;
	if (!c->up) return -1;
	while (got < LIBC_SLICE) {
		ssize_t r;
		size_t want;
		if (c->nin < HDR) want = HDR - c->nin;
		else {
			uint32_t len = get32(c->in + 8);
			want = HDR + len - c->nin;
		}
		if (c->capin < c->nin + want) {
			size_t cap = c->capin ? c->capin : 65536;
			unsigned char *np;
			while (cap < c->nin + want) cap *= 2;
			if (!(np = (unsigned char *)realloc(c->in, cap))) { down(c, "E_PACK"); return -1; }
			c->in = np;
			c->capin = cap;
		}
		if (want > LIBC_SLICE - got) want = LIBC_SLICE - got;
		r = read(c->sp.fd, c->in + c->nin, want);
		if (r < 0 && errno == EINTR) continue;
		if (r < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
		if (r <= 0) { down(c, "E_PACK"); return -1; }
		c->nin += (size_t)r;
		got += (size_t)r;
		if (c->nin == HDR) {
			const unsigned char *h = c->in;
			uint32_t len = get32(h + 8), kind = get32(h + 20);
			if (memcmp(h, "E26P", 4) || h[4] || h[5] != 1 || h[6] || h[7] || !len || len > MAXBODY || kind != 2 ||
			    !(get32(h + 12) | get32(h + 16))) {
				down(c, "E_BAD_FRAME");
				return -1;
			}
		}
		if (c->nin >= HDR && c->nin == HDR + (size_t)get32(c->in + 8)) {
			uint64_t id = (uint64_t)get32(c->in + 12) << 32 | get32(c->in + 16);
			size_t len = c->nin - HDR;
			c->nin = 0;
			if (deliver(c, id, (const char *)c->in + HDR, len)) {
				pend *e = find(c, id);
				if (e) { pend copy = *e; drop(c, e); fail_one(c, copy, "E_BAD_FRAME"); }
				down(c, "E_PACK");
				return -1;
			}
			if (!c->up) return -1;
		}
	}
	libc_flush(c);
	t = libc_now_ms() - t0;
	if (t > c->worst) c->worst = t;
	return 0;
}

void libc_tick(libconn *c, double now)
{
	int i;
	if (!now) now = libc_now_ms();
	libc_flush(c);
	for (i = 0; i < c->np; i++) {
		pend *e = &c->p[i];
		if (e->abandoned && e->ack_by && now > e->ack_by) {   /* the helper cannot even acknowledge: replace it */
			down(c, "E_TIMEOUT");
			return;
		}
		if (!e->abandoned && now > e->deadline) {
			pend copy = *e;
			if (e->internal) { e->abandoned = 1; e->ack_by = now + LIBC_ACK_MS; continue; }
			e->abandoned = 1;
			e->ack_by = now + LIBC_ACK_MS;
			if (!e->internal) libc_cancel(c, e->id);
			fail_one(c, copy, "E_TIMEOUT");
			if (!c->up) return;
			i = -1;                                 /* the callback may have changed the table */
		}
	}
	/* an abandoned request whose answer came is gone; one whose cancellation was answered no longer waits */
	for (i = 0; i < c->np; i++) if (c->p[i].abandoned && !c->p[i].ack_by && !c->p[i].internal) { drop(c, &c->p[i]); i--; }
}

int libc_pending(const libconn *c)
{
	int i, n = 0;
	for (i = 0; i < c->np; i++) n += !c->p[i].internal && !c->p[i].abandoned;
	return n;
}

const char *libc_generation(const libconn *c) { return c->pinned ? c->generation : NULL; }
int libc_restarts(const libconn *c) { return c->starts > 1 ? c->starts - 1 : 0; }
int libc_pid(const libconn *c) { return c->up ? (int)c->sp.pid : 0; }
double libc_worst_ms(const libconn *c) { return c->worst; }

void libc_free(libconn *c)
{
	if (!c) return;
	down(c, "E_PACK");
	free(c->p);
	free(c->in);
	free(c->out);
	free(c);
}

/* ---- the app's connection over Xt */
static libconn *C;
static e26_app *APP;
static int watch_id = -1, watch_fd = -1, ticking;

static void tick(e26_app *a, void *ud);

static void arm(e26_app *a)
{
	if (!ticking && C && (C->np || C->nout)) { ticking = 1; timer_after(a, C->nout ? 10 : 100, tick, NULL); }
}

static void rewatch(e26_app *a);

static void readable(e26_app *a, int fd, void *ud)
{
	(void)fd;
	(void)ud;
	if (libc_readable(C) && watch_id >= 0) { io_unwatch(watch_id); watch_id = -1; watch_fd = -1; }
	arm(a);
}

static void rewatch(e26_app *a)
{
	int fd = C && C->up ? C->sp.fd : -1;
	if (fd == watch_fd) return;
	if (watch_id >= 0) io_unwatch(watch_id);
	watch_id = fd >= 0 ? io_watch(a, fd, readable, NULL) : -1;
	watch_fd = watch_id >= 0 ? fd : -1;
}

static void tick(e26_app *a, void *ud)
{
	(void)ud;
	ticking = 0;
	if (!C) return;
	libc_tick(C, 0);
	rewatch(a);
	arm(a);
}

uint64_t lib_request(e26_app *a, const char *op, const char *args, lib_cb cb, void *ud)
{
	uint64_t id;
	if (!C && !(C = libc_new(NULL, NULL))) return 0;
	APP = a;
	id = libc_request(C, op, args, cb, ud);
	rewatch(a);
	arm(a);
	return id;
}

int lib_cancel(uint64_t id)
{
	int r = C ? libc_cancel(C, id) : -1;
	if (APP) arm(APP);
	return r;
}

void lib_shutdown(void)
{
	if (watch_id >= 0) io_unwatch(watch_id);
	watch_id = watch_fd = -1;
	libc_free(C);
	C = NULL;
}
