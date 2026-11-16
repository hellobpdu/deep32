/* e26j.c: strict JSON reader, writer and base64 for the Library protocol (e26j.h). */
#include "e26j.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
	const unsigned char *s, *p, *e;
	char *err;
	size_t errlen;
	int build;
} jp;

static int fail(jp *j, const char *m)
{
	if (j->err && j->errlen) snprintf(j->err, j->errlen, "%s at byte %lu", m, (unsigned long)(j->p - j->s));
	return -1;
}

static void ws(jp *j)
{
	while (j->p < j->e && (*j->p == ' ' || *j->p == '\t' || *j->p == '\n' || *j->p == '\r')) j->p++;
}

/* the length of the strict UTF-8 sequence at p (lead byte >= 0x80), or 0 */
static size_t u8len(const unsigned char *p, const unsigned char *e)
{
	unsigned c = p[0], lo = 0x80, hi = 0xBF;
	size_t n, i;
	if (c >= 0xC2 && c <= 0xDF) n = 2;
	else if (c >= 0xE0 && c <= 0xEF) { n = 3; if (c == 0xE0) lo = 0xA0; else if (c == 0xED) hi = 0x9F; }
	else if (c >= 0xF0 && c <= 0xF4) { n = 4; if (c == 0xF0) lo = 0x90; else if (c == 0xF4) hi = 0x8F; }
	else return 0;
	if ((size_t)(e - p) < n) return 0;
	if (p[1] < lo || p[1] > hi) return 0;
	for (i = 2; i < n; i++) if ((p[i] & 0xC0) != 0x80) return 0;
	return n;
}

int e26j_utf8(const char *s, size_t len)
{
	const unsigned char *p = (const unsigned char *)s, *e = p + len;
	while (p < e) {
		size_t n;
		if (*p == 0) return 0;
		if (*p < 0x80) { p++; continue; }
		if (!(n = u8len(p, e))) return 0;
		p += n;
	}
	return 1;
}

typedef struct { char *p; size_t n, cap; } sbuf;

static int sput(sbuf *b, const void *d, size_t n)
{
	if (b->n + n + 1 > b->cap) {
		size_t cap = b->cap ? b->cap : 32;
		char *np;
		while (cap < b->n + n + 1) cap *= 2;
		if (!(np = (char *)realloc(b->p, cap))) return -1;
		b->p = np;
		b->cap = cap;
	}
	memcpy(b->p + b->n, d, n);
	b->n += n;
	b->p[b->n] = 0;
	return 0;
}

static int hex4(const unsigned char *p, unsigned *v)
{
	int i;
	*v = 0;
	for (i = 0; i < 4; i++) {
		unsigned c = p[i], d;
		if (c >= '0' && c <= '9') d = c - '0';
		else if (c >= 'a' && c <= 'f') d = c - 'a' + 10;
		else if (c >= 'A' && c <= 'F') d = c - 'A' + 10;
		else return -1;
		*v = *v << 4 | d;
	}
	return 0;
}

static size_t put8(unsigned char *o, unsigned cp)
{
	if (cp < 0x80) { o[0] = (unsigned char)cp; return 1; }
	if (cp < 0x800) { o[0] = (unsigned char)(0xC0 | cp >> 6); o[1] = (unsigned char)(0x80 | (cp & 63)); return 2; }
	if (cp < 0x10000) {
		o[0] = (unsigned char)(0xE0 | cp >> 12); o[1] = (unsigned char)(0x80 | (cp >> 6 & 63));
		o[2] = (unsigned char)(0x80 | (cp & 63)); return 3;
	}
	o[0] = (unsigned char)(0xF0 | cp >> 18); o[1] = (unsigned char)(0x80 | (cp >> 12 & 63));
	o[2] = (unsigned char)(0x80 | (cp >> 6 & 63)); o[3] = (unsigned char)(0x80 | (cp & 63));
	return 4;
}

/* a string at j->p ('"'); decoded into *out (malloc'd) when out is not NULL */
static int str(jp *j, char **out)
{
	sbuf b = { NULL, 0, 0 };
	j->p++;
	for (;;) {
		const unsigned char *run = j->p;
		unsigned char u[4];
		unsigned cp;
		while (j->p < j->e && *j->p >= 0x20 && *j->p < 0x80 && *j->p != '"' && *j->p != '\\') j->p++;
		if (out && j->p > run && sput(&b, run, (size_t)(j->p - run))) goto oom;
		if (j->p >= j->e) { free(b.p); return fail(j, "unterminated string"); }
		if (*j->p == '"') { j->p++; break; }
		if (*j->p < 0x20) { free(b.p); return fail(j, "control character in string"); }
		if (*j->p >= 0x80) {
			size_t n = u8len(j->p, j->e);
			if (!n) { free(b.p); return fail(j, "invalid UTF-8"); }
			if (out && sput(&b, j->p, n)) goto oom;
			j->p += n;
			continue;
		}
		/* an escape */
		if (j->e - j->p < 2) { free(b.p); return fail(j, "short escape"); }
		j->p++;
		switch (*j->p) {
		case '"': case '\\': case '/': cp = *j->p; j->p++; break;
		case 'b': cp = 8; j->p++; break;
		case 'f': cp = 12; j->p++; break;
		case 'n': cp = 10; j->p++; break;
		case 'r': cp = 13; j->p++; break;
		case 't': cp = 9; j->p++; break;
		case 'u':
			if (j->e - j->p < 5 || hex4(j->p + 1, &cp)) { free(b.p); return fail(j, "bad \\u escape"); }
			j->p += 5;
			if (cp >= 0xDC00 && cp <= 0xDFFF) { free(b.p); return fail(j, "lone low surrogate"); }
			if (cp >= 0xD800 && cp <= 0xDBFF) {
				unsigned lo;
				if (j->e - j->p < 6 || j->p[0] != '\\' || j->p[1] != 'u' || hex4(j->p + 2, &lo) || lo < 0xDC00 || lo > 0xDFFF) {
					free(b.p);
					return fail(j, "lone high surrogate");
				}
				j->p += 6;
				cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
			}
			if (cp == 0) { free(b.p); return fail(j, "NUL in string"); }
			break;
		default: free(b.p); return fail(j, "bad escape");
		}
		if (out && sput(&b, u, put8(u, cp))) goto oom;
	}
	if (out) {
		if (!b.p && !(b.p = (char *)calloc(1, 1))) return fail(j, "out of memory");
		*out = b.p;
	}
	return 0;
oom:
	free(b.p);
	return fail(j, "out of memory");
}

static int num(jp *j, jv *v)
{
	const unsigned char *s = j->p;
	char tmp[64], *dyn = NULL, *t;
	size_t n;
	double d;
	if (j->p < j->e && *j->p == '-') j->p++;
	if (j->p >= j->e) return fail(j, "bad number");
	if (*j->p == '0') j->p++;
	else if (*j->p >= '1' && *j->p <= '9') while (j->p < j->e && *j->p >= '0' && *j->p <= '9') j->p++;
	else return fail(j, "bad number");
	if (j->p < j->e && *j->p == '.') {
		j->p++;
		if (j->p >= j->e || *j->p < '0' || *j->p > '9') return fail(j, "bad number");
		while (j->p < j->e && *j->p >= '0' && *j->p <= '9') j->p++;
	}
	if (j->p < j->e && (*j->p == 'e' || *j->p == 'E')) {
		j->p++;
		if (j->p < j->e && (*j->p == '+' || *j->p == '-')) j->p++;
		if (j->p >= j->e || *j->p < '0' || *j->p > '9') return fail(j, "bad number");
		while (j->p < j->e && *j->p >= '0' && *j->p <= '9') j->p++;
	}
	n = (size_t)(j->p - s);
	if (n < sizeof tmp) t = tmp;
	else if (!(t = dyn = (char *)malloc(n + 1))) return fail(j, "out of memory");
	memcpy(t, s, n);
	t[n] = 0;
	d = strtod(t, NULL);
	if (!isfinite(d)) { free(dyn); return fail(j, "non-finite number"); }
	if (v) {
		v->num = d;
		if (!(v->str = (char *)malloc(n + 1))) { free(dyn); return fail(j, "out of memory"); }
		memcpy(v->str, t, n + 1);
	}
	free(dyn);
	return 0;
}

static int lit(jp *j, const char *w)
{
	size_t n = strlen(w);
	if ((size_t)(j->e - j->p) < n || memcmp(j->p, w, n)) return fail(j, "bad literal");
	j->p += n;
	return 0;
}

static int cmpkey(const void *a, const void *b) { return strcmp(*(char *const *)a, *(char *const *)b); }

/* 1 when two of the n keys are equal */
static int dupkeys(char **k, size_t n)
{
	size_t a, b;
	char **s;
	int dup = 0;
	if (n < 2) return 0;
	if (n <= 16) {
		for (a = 0; a < n; a++) for (b = a + 1; b < n; b++) if (!strcmp(k[a], k[b])) return 1;
		return 0;
	}
	if (!(s = (char **)malloc(n * sizeof *s))) return 1;
	memcpy(s, k, n * sizeof *s);
	qsort(s, n, sizeof *s, cmpkey);
	for (a = 1; a < n && !dup; a++) dup = !strcmp(s[a - 1], s[a]);
	free(s);
	return dup;
}

static jv *mk(int type) { jv *v = (jv *)calloc(1, sizeof(jv)); if (v) v->type = type; return v; }

static int grow(void **p, size_t *cap, size_t n, size_t sz)
{
	if (n < *cap) return 0;
	{
		size_t c = *cap ? *cap * 2 : 4;
		void *np = realloc(*p, c * sz);
		if (!np) return -1;
		*p = np;
		*cap = c;
	}
	return 0;
}

static int value(jp *j, int depth, jv **out);

static int container(jp *j, int depth, jv **out, int obj)
{
	jv *v = NULL;
	char **keys = NULL;      /* validate-only: the keys, for the duplicate check */
	size_t nk = 0, ck = 0, ci = 0, ck2 = 0;
	int rc = -1;
	if (depth > E26J_MAX_DEPTH) return fail(j, "nesting deeper than 64");
	if (j->build && !(v = mk(obj ? J_OBJ : J_ARR))) return fail(j, "out of memory");
	j->p++;
	ws(j);
	if (j->p < j->e && *j->p == (obj ? '}' : ']')) { j->p++; rc = 0; goto done; }
	for (;;) {
		jv *item = NULL;
		char *key = NULL;
		ws(j);
		if (obj) {
			if (j->p >= j->e || *j->p != '"') { fail(j, "expected a key"); goto done; }
			if (str(j, &key)) goto done;
			if (grow((void **)&keys, &ck, nk, sizeof *keys)) { free(key); fail(j, "out of memory"); goto done; }
			keys[nk++] = key;
			ws(j);
			if (j->p >= j->e || *j->p != ':') { fail(j, "expected ':'"); goto done; }
			j->p++;
			ws(j);
		}
		if (value(j, depth, j->build ? &item : NULL)) goto done;
		if (v) {
			if (grow((void **)&v->items, &ci, v->n, sizeof *v->items)) { json_free(item); fail(j, "out of memory"); goto done; }
			if (obj && grow((void **)&v->keys, &ck2, v->n, sizeof *v->keys)) { json_free(item); fail(j, "out of memory"); goto done; }
			v->items[v->n] = item;
			if (obj) { v->keys[v->n] = keys[nk - 1]; keys[nk - 1] = NULL; }
			v->n++;
		}
		ws(j);
		if (j->p < j->e && *j->p == ',') { j->p++; continue; }
		if (j->p < j->e && *j->p == (obj ? '}' : ']')) { j->p++; break; }
		fail(j, obj ? "expected ',' or '}'" : "expected ',' or ']'");
		goto done;
	}
	if (obj) {
		int dup = v ? dupkeys(v->keys, v->n) : dupkeys(keys, nk);
		if (dup) { fail(j, "duplicate key"); goto done; }
	}
	rc = 0;
done:
	{
		size_t i;
		for (i = 0; i < nk; i++) free(keys[i]);
		free(keys);
	}
	if (rc) { json_free(v); return -1; }
	if (out) *out = v;
	return 0;
}

static int value(jp *j, int depth, jv **out)
{
	jv *v = NULL;
	ws(j);
	if (j->p >= j->e) return fail(j, "expected a value");
	switch (*j->p) {
	case '{': return container(j, depth + 1, out, 1);
	case '[': return container(j, depth + 1, out, 0);
	case '"':
		if (j->build) {
			if (!(v = mk(J_STR))) return fail(j, "out of memory");
			if (str(j, &v->str)) { free(v); return -1; }
		} else if (str(j, NULL)) return -1;
		break;
	case 't': case 'f':
		if (lit(j, *j->p == 't' ? "true" : "false")) return -1;
		if (j->build) { if (!(v = mk(J_BOOL))) return fail(j, "out of memory"); v->b = j->p[-1] == 'e' && j->p[-2] == 'u'; }
		break;
	case 'n':
		if (lit(j, "null")) return -1;
		if (j->build && !(v = mk(J_NULL))) return fail(j, "out of memory");
		break;
	default:
		if (j->build && !(v = mk(J_NUM))) return fail(j, "out of memory");
		if (num(j, v)) { free(v); return -1; }
	}
	if (out) *out = v;
	return 0;
}

static int run(const char *text, size_t len, char *err, size_t errlen, jv **out)
{
	jp j;
	j.s = j.p = (const unsigned char *)text;
	j.e = j.s + len;
	j.err = err;
	j.errlen = errlen;
	j.build = out != NULL;
	if (out) *out = NULL;
	if (len >= 3 && !memcmp(text, "\xEF\xBB\xBF", 3)) return fail(&j, "byte order mark");
	if (value(&j, 0, out)) return -1;
	ws(&j);
	if (j.p != j.e) {
		if (out) { json_free(*out); *out = NULL; }
		return fail(&j, "trailing data");
	}
	return 0;
}

jv *e26j_parse(const char *text, size_t len, char *err, size_t errlen)
{
	jv *v;
	return run(text, len, err, errlen, &v) ? NULL : v;
}

int e26j_valid(const char *text, size_t len, char *err, size_t errlen)
{
	return run(text, len, err, errlen, NULL);
}

/* the integer text of a number (no fraction or exponent): its sign and digits */
static int intext(const jv *v, int *neg, const char **d)
{
	const char *s;
	if (!v || v->type != J_NUM || !v->str) return -1;
	s = v->str;
	*neg = *s == '-';
	if (*neg) s++;
	if (!*s || strspn(s, "0123456789") != strlen(s)) return -1;
	*d = s;
	return 0;
}

int e26j_u64(const jv *v, uint64_t *out)
{
	int neg;
	const char *d;
	uint64_t x = 0;
	if (intext(v, &neg, &d)) return -1;
	if (neg) { if (strcmp(d, "0")) return -1; *out = 0; return 0; }
	for (; *d; d++) {
		unsigned c = (unsigned)(*d - '0');
		if (x > (UINT64_MAX - c) / 10) return -1;
		x = x * 10 + c;
	}
	*out = x;
	return 0;
}

int e26j_i64(const jv *v, int64_t lo, int64_t hi, int64_t *out)
{
	int neg;
	const char *d;
	uint64_t x = 0;
	int64_t r;
	if (intext(v, &neg, &d)) return -1;
	for (; *d; d++) {
		unsigned c = (unsigned)(*d - '0');
		if (x > ((uint64_t)INT64_MAX + 1 - c) / 10) return -1;
		x = x * 10 + c;
	}
	if (!neg && x > (uint64_t)INT64_MAX) return -1;
	r = neg ? (x == (uint64_t)INT64_MAX + 1 ? INT64_MIN : -(int64_t)x) : (int64_t)x;
	if (r < lo || r > hi) return -1;
	*out = r;
	return 0;
}

/* ---- the writer */
void e26j_init(e26j_buf *b) { b->p = NULL; b->n = b->cap = 0; b->bad = 0; }
void e26j_free(e26j_buf *b) { free(b->p); e26j_init(b); }

void e26j_raw(e26j_buf *b, const char *s, size_t len)
{
	if (b->bad) return;
	if (b->n + len + 1 > b->cap) {
		size_t cap = b->cap ? b->cap : 256;
		char *np;
		while (cap < b->n + len + 1) cap *= 2;
		if (!(np = (char *)realloc(b->p, cap))) { b->bad = 1; return; }
		b->p = np;
		b->cap = cap;
	}
	memcpy(b->p + b->n, s, len);
	b->n += len;
	b->p[b->n] = 0;
}

void e26j_lit(e26j_buf *b, const char *s) { e26j_raw(b, s, strlen(s)); }

void e26j_str(e26j_buf *b, const char *s, size_t len)
{
	const unsigned char *p = (const unsigned char *)s, *e = p + len, *run;
	if (!e26j_utf8(s, len)) { b->bad = 1; return; }
	e26j_raw(b, "\"", 1);
	while (p < e) {
		run = p;
		while (p < e && *p >= 0x20 && *p != '"' && *p != '\\') p++;
		if (p > run) e26j_raw(b, (const char *)run, (size_t)(p - run));
		if (p < e) {
			char t[8];
			switch (*p) {
			case '"': e26j_raw(b, "\\\"", 2); break;
			case '\\': e26j_raw(b, "\\\\", 2); break;
			case '\n': e26j_raw(b, "\\n", 2); break;
			case '\r': e26j_raw(b, "\\r", 2); break;
			case '\t': e26j_raw(b, "\\t", 2); break;
			case '\b': e26j_raw(b, "\\b", 2); break;
			case '\f': e26j_raw(b, "\\f", 2); break;
			default: snprintf(t, sizeof t, "\\u%04x", *p); e26j_raw(b, t, 6);
			}
			p++;
		}
	}
	e26j_raw(b, "\"", 1);
}

void e26j_cstr(e26j_buf *b, const char *s)
{
	if (!s) e26j_raw(b, "null", 4);
	else e26j_str(b, s, strlen(s));
}

void e26j_u(e26j_buf *b, uint64_t v)
{
	char t[24];
	e26j_raw(b, t, (size_t)snprintf(t, sizeof t, "%llu", (unsigned long long)v));
}

void e26j_i(e26j_buf *b, int64_t v)
{
	char t[24];
	e26j_raw(b, t, (size_t)snprintf(t, sizeof t, "%lld", (long long)v));
}

char *e26j_take(e26j_buf *b)
{
	char *p;
	if (b->bad) { e26j_free(b); return NULL; }
	if (!b->p) e26j_raw(b, "", 0);
	if (b->bad) { e26j_free(b); return NULL; }
	p = b->p;
	e26j_init(b);
	return p;
}

/* ---- base64 */
static const char B64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

void e26j_b64(e26j_buf *b, const unsigned char *d, size_t len)
{
	char t[4 * 1024];
	size_t i = 0, k = 0;
	e26j_raw(b, "\"", 1);
	while (i < len) {
		unsigned v = (unsigned)d[i] << 16;
		size_t r = len - i;
		if (r > 1) v |= (unsigned)d[i + 1] << 8;
		if (r > 2) v |= d[i + 2];
		t[k++] = B64[v >> 18 & 63];
		t[k++] = B64[v >> 12 & 63];
		t[k++] = r > 1 ? B64[v >> 6 & 63] : '=';
		t[k++] = r > 2 ? B64[v & 63] : '=';
		i += r > 2 ? 3 : r;
		if (k == sizeof t) { e26j_raw(b, t, k); k = 0; }
	}
	e26j_raw(b, t, k);
	e26j_raw(b, "\"", 1);
}

static int b64v(int c)
{
	const char *p = c ? strchr(B64, c) : NULL;
	return p ? (int)(p - B64) : -1;
}

unsigned char *e26j_unb64(const char *s, size_t len, size_t *outlen)
{
	unsigned char *o;
	size_t i, k = 0, pad = 0;
	if (len % 4) return NULL;
	if (len && s[len - 1] == '=') pad++;
	if (len > 1 && s[len - 2] == '=') pad++;
	if (!(o = (unsigned char *)malloc(len / 4 * 3 + 1))) return NULL;
	for (i = 0; i < len; i += 4) {
		int a = b64v(s[i]), b = b64v(s[i + 1]), c, d;
		int last = i + 4 == len;
		c = last && pad == 2 ? 0 : b64v(s[i + 2]);
		d = last && pad >= 1 ? 0 : b64v(s[i + 3]);
		if (a < 0 || b < 0 || c < 0 || d < 0) { free(o); return NULL; }
		if (last && pad == 2 && (b & 15)) { free(o); return NULL; }  /* unused bits must be zero (canonical) */
		if (last && pad == 1 && (c & 3)) { free(o); return NULL; }
		o[k++] = (unsigned char)(a << 2 | b >> 4);
		if (!(last && pad == 2)) o[k++] = (unsigned char)((b & 15) << 4 | c >> 2);
		if (!(last && pad >= 1)) o[k++] = (unsigned char)((c & 3) << 6 | d);
	}
	*outlen = k;
	return o;
}
