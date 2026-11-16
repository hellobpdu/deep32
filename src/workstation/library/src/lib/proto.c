/* proto.c: e26-lib's side of LIB-PROTOCOL 1.0 (W1-DATA). One connection (the socketpair d32library made), read
 * without ever blocking on a partial frame; requests queue (at most 64 pending, then E_BUSY) and run one at a time
 * in arrival order; a cancellation or the 5 s deadline (queued time included) interrupts the running query between
 * SQLite steps (the progress handler calls lib_poll); each request gets exactly one terminal response.
 *
 * Malformed framing (magic, length, id, kind, an unsolicited response, a reused id, a body that is not strict
 * JSON: UTF-8, no BOM or NUL, no duplicate keys, nesting at most 64, finite numbers, an object) ends the
 * connection: an E_BAD_FRAME answer when the header was sound, then exit. A parseable other version gets E_VERSION,
 * then exit. Invalid arguments only get E_ARGUMENT. */
#include "lib.h"
#include "../app/registry.h"
#include "../common/helper.h"
#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define HDR 24
#define MAXBODY 8388608u
#define MAXPEND 64
#define DEADLINE 5.0

typedef struct { uint64_t id; jv *body; double t0; } req;

static struct {
	int fd, dead;
	unsigned char *in;            /* bytes read and not yet framed */
	size_t n, cap;
	req q[MAXPEND];
	int nq;
	uint64_t cur;                 /* the running request (0 none) */
	double deadline;
	int state;                    /* 0, 1 cancelled, 2 timed out */
	int eof;                      /* the peer closed its side: answer what is queued, then end */
	uint64_t *seen;               /* every id used on this connection (open addressing, 0 = empty) */
	size_t nseen, capseen;
	uint64_t acks[MAXPEND];       /* cancellations of the running request, answered when it ends */
	int nacks;
	double last_poll;
} S;

static double now(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static int seen_add(uint64_t id)   /* 1 when the id was new */
{
	size_t i, mask;
	if (S.nseen * 2 >= S.capseen) {
		size_t cap = S.capseen ? S.capseen * 2 : 1024, k;
		uint64_t *t = (uint64_t *)calloc(cap, sizeof *t);
		if (!t) return 0;
		for (k = 0; k < S.capseen; k++) if (S.seen[k]) {
			for (i = (size_t)(S.seen[k] * 0x9E3779B97F4A7C15ull) & (cap - 1); t[i]; i = (i + 1) & (cap - 1)) ;
			t[i] = S.seen[k];
		}
		free(S.seen);
		S.seen = t;
		S.capseen = cap;
	}
	mask = S.capseen - 1;
	for (i = (size_t)(id * 0x9E3779B97F4A7C15ull) & mask; S.seen[i]; i = (i + 1) & mask) if (S.seen[i] == id) return 0;
	S.seen[i] = id;
	S.nseen++;
	return 1;
}

static void put32(unsigned char *p, uint32_t v) { p[0] = (unsigned char)(v >> 24); p[1] = (unsigned char)(v >> 16); p[2] = (unsigned char)(v >> 8); p[3] = (unsigned char)v; }
static uint32_t get32(const unsigned char *p) { return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3]; }

static void send_frame(uint64_t id, const char *body, size_t len)
{
	unsigned char h[HDR];
	size_t off = 0;
	if (S.dead == 2) return;      /* the socket is gone */
	memcpy(h, "E26P", 4);
	h[4] = 0; h[5] = 1; h[6] = 0; h[7] = 0;
	put32(h + 8, (uint32_t)len);
	put32(h + 12, (uint32_t)(id >> 32));
	put32(h + 16, (uint32_t)id);
	put32(h + 20, 2);
	while (off < HDR + len) {
		const unsigned char *p = off < HDR ? h + off : (const unsigned char *)body + (off - HDR);
		size_t n = off < HDR ? HDR - off : len - (off - HDR);
		ssize_t w = write(S.fd, p, n);
		if (w < 0 && errno == EINTR) continue;
		if (w <= 0) { S.dead = 2; return; }
		off += (size_t)w;
	}
}

static const char *const MESSAGES[][2] = {
	{ "E_BAD_FRAME", "The helper sent an invalid message." },
	{ "E_ARGUMENT", "The request is not valid." },
	{ "E_NOT_FOUND", "The requested item is not in this Library." },
	{ "E_STALE", "The Library has changed. Please repeat the request." },
	{ "E_VERSION", "This Library version is not supported." },
	{ "E_TOO_LARGE", "The requested content is too large." },
	{ "E_PACK", "The Library pack is unavailable." },
	{ "E_UNSUPPORTED", "This operation is not available in this build." },
	{ "E_CANCELLED", "The request was cancelled." },
	{ "E_TIMEOUT", "The helper took too long. Please try again." },
	{ "E_BUSY", "The helper is busy. Please try again." },
};

const char *lib_err(char **result, const char *code, const char *msg)
{
	size_t i;
	if (!msg) for (i = 0; i < sizeof MESSAGES / sizeof MESSAGES[0]; i++) if (!strcmp(MESSAGES[i][0], code)) msg = MESSAGES[i][1];
	if (result) {
		*result = NULL;
		if (msg) {
			size_t n = strlen(msg) + 1;
			if ((*result = (char *)malloc(n))) memcpy(*result, msg, n);
		}
	}
	return code;
}

const char *lib_ok(e26j_buf *b, char **result)
{
	*result = e26j_take(b);
	return *result ? NULL : lib_err(result, "E_PACK", NULL);
}

static void gen_out(e26j_buf *b)
{
	const char *g = lib_generation();
	e26j_cstr(b, g);
}

static void send_error(uint64_t id, const char *code, const char *msg)
{
	e26j_buf b;
	char *m = NULL;
	size_t i;
	if (!msg) { lib_err(&m, code, NULL); msg = m; }
	e26j_init(&b);
	e26j_lit(&b, "{\"ok\":false,\"error\":{\"code\":");
	for (i = 0; code[i] >= '0' && code[i] <= '9'; i++) ;
	if (i && !code[i]) e26j_lit(&b, code);          /* a search error: its T-0018 number */
	else e26j_cstr(&b, code);
	e26j_lit(&b, ",\"message\":");
	e26j_cstr(&b, msg ? msg : "The request could not be answered.");
	e26j_lit(&b, "},\"generation\":");
	gen_out(&b);
	e26j_lit(&b, "}");
	if (b.bad) {   /* a message that was not UTF-8: the code's own message */
		e26j_free(&b);
		free(m);
		send_error(id, "E_PACK", NULL);
		return;
	}
	send_frame(id, b.p, b.n);
	e26j_free(&b);
	free(m);
}

static void send_result(uint64_t id, const char *result)
{
	e26j_buf b;
	e26j_init(&b);
	e26j_lit(&b, "{\"ok\":true,\"result\":");
	e26j_lit(&b, result ? result : "null");
	e26j_lit(&b, ",\"generation\":");
	gen_out(&b);
	e26j_lit(&b, "}");
	if (b.bad) send_error(id, "E_PACK", NULL);
	else if (b.n > MAXBODY) send_error(id, "E_TOO_LARGE", NULL);
	else send_frame(id, b.p, b.n);
	e26j_free(&b);
}

static void send_cancelled(uint64_t id, int yes)
{
	send_result(id, yes ? "{\"cancelled\":true}" : "{\"cancelled\":false}");
}

/* a cancellation frame: its own id, body {target_id} */
static void cancel(uint64_t id, const jv *body)
{
	const jv *t = body->n == 1 ? json_get(body, "target_id") : NULL;
	uint64_t target;
	int i;
	if (!t || e26j_u64(t, &target) || !target) { send_error(id, "E_ARGUMENT", NULL); return; }
	if (target == S.cur && S.cur) {
		S.state = 1;
		if (S.nacks < MAXPEND) S.acks[S.nacks++] = id;
		else send_cancelled(id, 0);
		return;
	}
	for (i = 0; i < S.nq; i++) if (S.q[i].id == target) {
		json_free(S.q[i].body);
		memmove(&S.q[i], &S.q[i + 1], (size_t)(S.nq - i - 1) * sizeof S.q[0]);
		S.nq--;
		send_error(target, "E_CANCELLED", NULL);
		send_cancelled(id, 1);
		return;
	}
	send_cancelled(id, 0);
}

/* frames whatever is buffered; marks the connection dead on malformed input */
static void frames(void)
{
	size_t at = 0;
	while (!S.dead && S.n - at >= HDR) {
		const unsigned char *h = S.in + at;
		uint32_t major = (uint32_t)h[4] << 8 | h[5], minor = (uint32_t)h[6] << 8 | h[7], len = get32(h + 8), kind = get32(h + 20);
		uint64_t id = (uint64_t)get32(h + 12) << 32 | get32(h + 16);
		int sound = len >= 1 && len <= MAXBODY && id && (kind == 1 || kind == 2 || kind == 4);
		jv *body;
		char err[160];
		if (memcmp(h, "E26P", 4)) { S.dead = 1; break; }
		if (major != 1 || minor != 0) { if (sound) send_error(id, "E_VERSION", NULL); S.dead = 1; break; }
		if (!sound) { S.dead = 1; break; }
		if (S.n - at < HDR + (size_t)len) break;
		if (kind == 2 || !seen_add(id)) { send_error(id, "E_BAD_FRAME", NULL); S.dead = 1; break; }
		body = e26j_parse((const char *)h + HDR, len, err, sizeof err);
		if (!body || body->type != J_OBJ) { json_free(body); send_error(id, "E_BAD_FRAME", NULL); S.dead = 1; break; }
		at += HDR + (size_t)len;
		if (kind == 4) { cancel(id, body); json_free(body); continue; }
		if (S.nq >= MAXPEND) { json_free(body); send_error(id, "E_BUSY", NULL); continue; }
		S.q[S.nq].id = id;
		S.q[S.nq].body = body;
		S.q[S.nq].t0 = now();
		S.nq++;
	}
	if (at) { memmove(S.in, S.in + at, S.n - at); S.n -= at; }
}

/* reads what is there (wait: block until something comes); 0, or -1 when the connection is malformed (dead) or the
 * peer has closed its side (eof: the queued requests are still answered, a half-closed peer reads them) */
static int ingest(int wait)
{
	struct pollfd p;
	int any = 0;
	for (;;) {
		ssize_t r;
		p.fd = S.fd;
		p.events = POLLIN;
		p.revents = 0;
		if (S.eof) return -1;
		if (poll(&p, 1, wait && !any ? -1 : 0) < 0) { if (errno == EINTR) continue; S.eof = 1; return -1; }
		if (!(p.revents & (POLLIN | POLLHUP | POLLERR))) break;
		if (S.cap - S.n < 65536) {
			/* a header plus the largest body; never more than one frame's worth ahead of the parser */
			size_t cap = S.cap ? S.cap * 2 : 131072;
			unsigned char *np;
			if (cap > HDR + MAXBODY + 65536) cap = HDR + MAXBODY + 65536;
			if (cap <= S.cap) { frames(); if (S.dead) return -1; if (S.cap - S.n < 4096) break; continue; }
			if (!(np = (unsigned char *)realloc(S.in, cap))) { S.dead = 1; return -1; }
			S.in = np;
			S.cap = cap;
		}
		r = read(S.fd, S.in + S.n, S.cap - S.n);
		if (r < 0 && errno == EINTR) continue;
		if (r <= 0) { S.eof = 1; return -1; }
		S.n += (size_t)r;
		any = 1;
		frames();
		if (S.dead) return -1;
	}
	return 0;
}

void lib_poll(void)
{
	double t = now();
	if (S.cur && t > S.deadline && !S.state) S.state = 2;
	if (t - S.last_poll < 0.002) return;
	S.last_poll = t;
	if (!S.dead && !S.eof) ingest(0);
}

int lib_interrupted(void) { return S.state; }

static const char *const PROTOCOL_OPS[] = {
	"status", "title_prefix", "wheel_page", "article", "section", "media", "search", "facets", "related", "references",
	"backlinks", "updates", "dictionary", "random_title", "facts", "moreinfo", "study_lists", "tours", NULL
};

static void run(req *r)
{
	const jv *b = r->body, *op = json_get(b, "op"), *args = json_get(b, "args"), *g = json_get(b, "generation");
	size_t i;
	int id;
	const char *code;
	char *result = NULL;
	for (i = 0; i < b->n; i++)
		if (strcmp(b->keys[i], "op") && strcmp(b->keys[i], "args") && strcmp(b->keys[i], "generation")) break;
	if (i < b->n || !op || op->type != J_STR || !args || args->type != J_OBJ) { send_error(r->id, "E_ARGUMENT", NULL); return; }
	if (g && (g->type != J_STR || !g->str[0] || strlen(g->str) > 128)) { send_error(r->id, "E_ARGUMENT", NULL); return; }
	if (!g && (json_get(args, "bitmap") || json_get(args, "last"))) { send_error(r->id, "E_ARGUMENT", NULL); return; }
	if (g && lib_generation() && strcmp(g->str, lib_generation())) { send_error(r->id, "E_STALE", NULL); return; }
	if ((id = reg_find(REG_QUERY, op->str)) < 0 || !reg_impl(REG_QUERY, id)) {
		for (i = 0; PROTOCOL_OPS[i] && strcmp(PROTOCOL_OPS[i], op->str); i++) ;
		send_error(r->id, PROTOCOL_OPS[i] ? "E_UNSUPPORTED" : "E_ARGUMENT", NULL);
		return;
	}
	if (now() > r->t0 + DEADLINE) { send_error(r->id, "E_TIMEOUT", NULL); return; }
	S.cur = r->id;
	S.deadline = r->t0 + DEADLINE;
	S.state = 0;
	S.nacks = 0;
	/* an id-addressed query runs on the pack holding its id (PACK-CORPUS section 8), then pack 0 again */
	lib_select(lib_route(op->str, args));
	code = ((const e26_helper_op *)reg_impl(REG_QUERY, id))->run(args, &result);
	lib_select(0);
	if (!S.state && now() > S.deadline) S.state = 2;
	if (S.state == 1) { free(result); result = NULL; code = "E_CANCELLED"; }
	else if (S.state == 2) { free(result); result = NULL; code = "E_TIMEOUT"; }
	if (code) send_error(r->id, code, result);
	else send_result(r->id, result);
	free(result);
	for (i = 0; i < (size_t)S.nacks; i++) send_cancelled(S.acks[i], S.state == 1);
	S.cur = 0;
	S.state = 0;
	S.nacks = 0;
}

int lib_serve(int fd)
{
	memset(&S, 0, sizeof S);
	S.fd = fd;
	for (;;) {
		if (!S.nq) {
			if (S.eof || ingest(1)) { if (S.dead || !S.nq) break; }
		} else {
			req r = S.q[0];
			memmove(&S.q[0], &S.q[1], (size_t)(S.nq - 1) * sizeof S.q[0]);
			S.nq--;
			run(&r);
			json_free(r.body);
			if (S.dead) break;
			if (!S.eof) ingest(0);
			if (S.dead) break;
		}
	}
	while (S.nq) json_free(S.q[--S.nq].body);
	free(S.in);
	free(S.seen);
	return S.dead == 1 ? 1 : 0;
}
