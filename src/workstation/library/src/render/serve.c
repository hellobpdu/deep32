/* SPDX-License-Identifier: AGPL-3.0-or-later */
/* Copyright (C) 2026 Jeffrey A. Cummings */
/* serve.c: e26-render's request loop (render.h): E26P frames read with recvmsg so that a request's descriptors
 * (SCM_RIGHTS, close-on-exec at once) arrive with it; LIB-PROTOCOL's envelopes; the helpers' argument checks and
 * result builders. */
#include "render.h"
#include "../app/registry.h"
#include "../common/b64.h"
#include "../common/e26p.h"
#include "../common/helper.h"
#include "../common/json8.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/uio.h>
#include <unistd.h>

static int fds[RENDER_MAX_FDS], nfds, kept[RENDER_MAX_FDS], fd_overflow;

int render_fd(int slot) { return slot >= 0 && slot < nfds ? fds[slot] : -1; }
int render_take_fd(int slot)
{
	if (slot < 0 || slot >= nfds) return -1;
	kept[slot] = 1;
	return fds[slot];
}

static void drop_fds(void)
{
	int i;
	for (i = 0; i < nfds; i++)
		if (!kept[i]) close(fds[i]);
	nfds = 0;
	memset(kept, 0, sizeof kept);
	fd_overflow = 0;
}

static void collect(struct msghdr *m)
{
	struct cmsghdr *c;
	if (m->msg_flags & MSG_CTRUNC) fd_overflow = 1;
	for (c = CMSG_FIRSTHDR(m); c; c = CMSG_NXTHDR(m, c)) {
		int i, n, *p;
		if (c->cmsg_level != SOL_SOCKET || c->cmsg_type != SCM_RIGHTS) continue;
		n = (int)((c->cmsg_len - CMSG_LEN(0)) / sizeof(int));
		p = (int *)(void *)CMSG_DATA(c);
		for (i = 0; i < n; i++) {
			int d;
			memcpy(&d, p + i, sizeof d);
			if (nfds < RENDER_MAX_FDS) fds[nfds++] = d;
			else { close(d); fd_overflow = 1; }
		}
	}
}

/* n bytes with recvmsg, keeping any descriptors that come with them: 1, 0 end of file before the first byte, -1 */
static int recv_all(int fd, unsigned char *p, size_t n)
{
	size_t got = 0;
	while (got < n) {
		struct msghdr m;
		struct iovec iov;
		union { struct cmsghdr h; char buf[CMSG_SPACE(sizeof(int) * RENDER_MAX_FDS)]; } cb;
		ssize_t r;
		iov.iov_base = p + got;
		iov.iov_len = n - got;
		memset(&m, 0, sizeof m);
		m.msg_iov = &iov;
		m.msg_iovlen = 1;
		m.msg_control = cb.buf;
		m.msg_controllen = sizeof cb.buf;
		r = recvmsg(fd, &m, MSG_CMSG_CLOEXEC);
		if (r < 0 && errno == EINTR) continue;
		if (r < 0) return -1;
		collect(&m);
		if (r == 0) return got ? -1 : 0;
		got += (size_t)r;
	}
	return 1;
}

/* ---- envelopes */

static const struct { const char *code, *message; } MESSAGES[] = {
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

char *rs_json_str(const char *s, size_t n)
{
	size_t i, k = 0;
	char *o = (char *)malloc(n * 6 + 3);
	if (!o) return NULL;
	o[k++] = '"';
	for (i = 0; i < n; i++) {
		unsigned char c = (unsigned char)s[i];
		if (c == '"' || c == '\\') { o[k++] = '\\'; o[k++] = (char)c; }
		else if (c == '\n') { o[k++] = '\\'; o[k++] = 'n'; }
		else if (c == '\t') { o[k++] = '\\'; o[k++] = 't'; }
		else if (c < 0x20) k += (size_t)snprintf(o + k, 7, "\\u%04x", c);
		else o[k++] = (char)c;
	}
	o[k++] = '"';
	o[k] = 0;
	return o;
}

char *rs_dup(const char *s)
{
	size_t n = strlen(s) + 1;
	char *o = (char *)malloc(n);
	if (o) memcpy(o, s, n);
	return o;
}

static int reply(int fd, unsigned long long id, const char *body)
{
	return e26p_write(fd, id, E26P_RESPONSE, body, strlen(body));
}

/* {"ok":false,...}: the code's stable user message; the operation's own words go in detail.reason */
static int fail(int fd, unsigned long long id, const char *code, const char *reason, const char *gen)
{
	const char *msg = "The request could not be answered.";
	char *m, *r = NULL, body[2048];
	size_t i;
	for (i = 0; i < sizeof MESSAGES / sizeof MESSAGES[0]; i++)
		if (!strcmp(MESSAGES[i].code, code)) msg = MESSAGES[i].message;
	m = rs_json_str(msg, strlen(msg));
	if (reason && reason[0]) r = rs_json_str(reason, strlen(reason) > 400 ? 400 : strlen(reason));
	snprintf(body, sizeof body, "{\"ok\":false,\"error\":{\"code\":\"%s\",\"message\":%s%s%s%s},\"generation\":%s}", code,
	         m ? m : "\"\"", r ? ",\"detail\":{\"reason\":" : "", r ? r : "", r ? "}" : "", gen ? gen : "null");
	free(m);
	free(r);
	return reply(fd, id, body);
}

/* the largest result body: the envelope round it and an echoed generation (4096 bytes, escaped) must fit too */
#define RS_ENVELOPE 512u
#define RS_GENERATION (4096u * 6u + 2u)

size_t rs_max_pixels(void)
{
	size_t room = E26P_MAX_BODY - RS_ENVELOPE - RS_GENERATION - 128u;   /* 128: the result object's own fields */
	return room / 4u * 3u / 4u;
}

char *rs_rgba_source(const unsigned char *rgba, int w, int h, int sw, int sh, const char **code)
{
	size_t n = (size_t)w * h * 4, bl = b64_len(n);
	char *o, *p;
	int k;
	if ((size_t)w * h > rs_max_pixels()) { *code = "E_TOO_LARGE"; return NULL; }
	o = (char *)malloc(bl + 128);
	if (!o) { *code = "E_PACK"; return NULL; }
	if (sw >= 0)
		k = snprintf(o, 128, "{\"width\":%d,\"height\":%d,\"stride\":%d,\"format\":\"rgba8\","
		             "\"source_width\":%d,\"source_height\":%d,\"data_b64\":\"", w, h, w * 4, sw, sh);
	else k = snprintf(o, 128, "{\"width\":%d,\"height\":%d,\"stride\":%d,\"format\":\"rgba8\",\"data_b64\":\"", w, h, w * 4);
	if (k < 0 || k > 125) { free(o); *code = "E_TOO_LARGE"; return NULL; }
	p = o + k;
	p += b64_encode(rgba, n, p);
	memcpy(p, "\"}", 3);
	return o;
}

char *rs_rgba(const unsigned char *rgba, int w, int h, const char **code)
{
	return rs_rgba_source(rgba, w, h, -1, -1, code);
}

/* ---- arguments */

int ra_only(const jv *args, const char *const *keys)
{
	size_t i;
	int k, ok;
	if (!args || args->type != J_OBJ) return 0;
	for (i = 0; i < args->n; i++) {
		for (ok = 0, k = 0; keys[k]; k++)
			if (!strcmp(args->keys[i], keys[k])) ok = 1;
		if (!ok) return 0;
		for (k = 0; (size_t)k < i; k++)            /* a key given twice */
			if (!strcmp(args->keys[i], args->keys[k])) return 0;
	}
	return 1;
}

int ra_num(const jv *v, double lo, double hi, double *out)
{
	if (!v || v->type != J_NUM || !(v->num >= lo && v->num <= hi)) return 0;   /* NaN fails too */
	*out = v->num;
	return 1;
}

int ra_int(const jv *args, const char *key, long lo, long hi, long def, long *out)
{
	const jv *v = json_get(args, key);
	*out = def;
	if (!v) return 0;
	if (v->type != J_NUM || v->num != (double)(long)v->num || v->num < (double)lo || v->num > (double)hi) return -1;
	*out = (long)v->num;
	return 1;
}

int ra_str(const jv *args, const char *key, size_t max, const char *def, const char **out)
{
	const jv *v = json_get(args, key);
	*out = def;
	if (!v) return 0;
	if (v->type != J_STR || strlen(v->str) > max) return -1;
	*out = v->str;
	return 1;
}

int ra_bool(const jv *args, const char *key, int def, int *out)
{
	const jv *v = json_get(args, key);
	*out = def;
	if (!v) return 0;
	if (v->type != J_BOOL) return -1;
	*out = v->b;
	return 1;
}

/* ---- input descriptors */

int rd_all(int fd, size_t max, unsigned char **buf, size_t *len)
{
	struct stat st;
	size_t cap = 65536, n = 0;
	unsigned char *b;
	*buf = NULL;
	*len = 0;
	if (fd < 0) return -1;
	(void)lseek(fd, 0, SEEK_SET);                    /* a shared descriptor read before (a pipe cannot seek: fine) */
	if (!fstat(fd, &st) && S_ISREG(st.st_mode)) {
		if ((unsigned long long)st.st_size > max) return -2;
		cap = (size_t)st.st_size + 1;
	}
	if (!(b = (unsigned char *)malloc(cap))) return -1;
	for (;;) {
		ssize_t r;
		if (n == cap) {
			unsigned char *nb;
			if (cap > max) { free(b); return -2; }
			cap = cap * 2 > max + 1 ? max + 1 : cap * 2;
			if (!(nb = (unsigned char *)realloc(b, cap))) { free(b); return -1; }
			b = nb;
		}
		r = read(fd, b + n, cap - n);
		if (r < 0 && errno == EINTR) continue;
		if (r < 0) { free(b); return -1; }
		if (r == 0) break;
		n += (size_t)r;
		if (n > max) { free(b); return -2; }
	}
	*buf = b;
	*len = n;
	return 0;
}

/* ---- the loop */

static const char *const ENVELOPE[] = { "op", "args", "generation", NULL };

int render_serve(int fd)
{
	for (;;) {
		unsigned char hb[E26P_HEADER];
		e26p_header h;
		char *body, err[200], *gen = NULL;
		jv *v;
		int r = recv_all(fd, hb, sizeof hb), id;
		const char *op;
		if (r <= 0) { drop_fds(); return r == 0 ? 0 : 1; }
		if (e26p_unpack(hb, &h)) { drop_fds(); return 1; }              /* malformed framing: close */
		if (!(body = (char *)malloc((size_t)h.length + 1))) { drop_fds(); return 1; }
		if (recv_all(fd, (unsigned char *)body, h.length) != 1) { free(body); drop_fds(); return 1; }
		body[h.length] = 0;
		if (h.kind == E26P_RESPONSE) { free(body); drop_fds(); return 1; } /* an unsolicited response */
		v = json8_parse(body, h.length, err, sizeof err);   /* UTF-8 kept; invalid bodies refused (LIB-PROTOCOL) */
		free(body);
		if (v && v->type == J_OBJ && json_str(v, "generation")) {
			const char *g = json_str(v, "generation");
			gen = rs_json_str(g, strlen(g) > 4096 ? 4096 : strlen(g));
		}
		if (h.kind == E26P_CANCEL) {
			/* one request at a time, run to its end: nothing queued or running can still be cancelled */
			char out[256];
			snprintf(out, sizeof out, "{\"ok\":true,\"result\":{\"cancelled\":false},\"generation\":%s}", gen ? gen : "null");
			r = reply(fd, h.id, out);
			free(gen); json_free(v); drop_fds();
			if (r) return 1;
			continue;
		}
		op = v && v->type == J_OBJ ? json_str(v, "op") : NULL;
		{
			const char *code = NULL, *reason = NULL;
			const e26_helper_op *o = NULL;
			if (fd_overflow) { code = "E_ARGUMENT"; reason = "too many descriptors"; }
			else if (!op || !json_get(v, "args") || json_get(v, "args")->type != J_OBJ || !ra_only(v, ENVELOPE) ||
			         (json_get(v, "generation") && (!json_str(v, "generation") || !json_str(v, "generation")[0]))) {
				code = "E_ARGUMENT"; reason = "not a named operation with arguments";
			} else if ((id = reg_find(REG_RENDEROP, op)) < 0 || !reg_impl(REG_RENDEROP, id)) {
				code = "E_UNSUPPORTED"; reason = "no such operation";
			} else if ((int)RENDER_FDS(reg_get(REG_RENDEROP, id)->flags) != nfds) {
				code = "E_ARGUMENT"; reason = nfds ? "the descriptors do not match the operation" : "the operation needs a descriptor";
			} else o = (const e26_helper_op *)reg_impl(REG_RENDEROP, id);
			if (o) {
				char *result = NULL;
				const char *c = o->run(json_get(v, "args"), &result);
				if (c) r = fail(fd, h.id, c, result, gen);
				else {
					size_t n = (result ? strlen(result) : 4) + (gen ? strlen(gen) : 4) + 64;
					char *out = (char *)malloc(n);
					if (!out) r = fail(fd, h.id, "E_TOO_LARGE", "no memory for the response", gen);
					else {
						snprintf(out, n, "{\"ok\":true,\"result\":%s,\"generation\":%s}", result ? result : "null", gen ? gen : "null");
						r = strlen(out) > E26P_MAX_BODY ? fail(fd, h.id, "E_TOO_LARGE", "the response does not fit a frame", gen)
						                                : reply(fd, h.id, out);
						free(out);
					}
				}
				free(result);
			} else r = fail(fd, h.id, code, reason, gen);
		}
		free(gen); json_free(v); drop_fds();
		if (r) return 1;
	}
}
