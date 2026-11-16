/* article2.c: the article format 2 reader (article2.h). A strict JSON parser (UTF-8 kept, duplicate keys refused,
 * integers exact) feeds a checker that follows tools/validate_v2.py in the same order with the same codes, and builds
 * the typed model as it checks. Every allocation of a document lives in one arena; each record's JSON tree lives in a
 * second arena that is emptied after the record, so a large article costs about its own size. Errors unwind with
 * longjmp to a2_parse, which frees both arenas. */
#include "article2.h"
#include "../common/sha256.h"
#include "../common/text8.h"
#include <setjmp.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_ARTICLE (32L * 1024 * 1024)
#define MAX_LINE (1024L * 1024)
#define MAX_BLOCKS 65536
#define JSON_DEPTH 64

/* ---------------------------------------------------------------- arenas */

typedef struct chunk {
	struct chunk *next;
	size_t used, cap;
	/* the bytes follow */
} chunk;

typedef struct {
	chunk *head;
} arena;

static void *ar_alloc(arena *ar, size_t n, jmp_buf *oom)
{
	chunk *c = ar->head;
	void *p;
	n = (n + 7) & ~(size_t)7;
	if (!c || c->cap - c->used < n) {
		size_t cap = n > 65536 - sizeof(chunk) ? n : 65536 - sizeof(chunk);
		c = (chunk *)malloc(sizeof(chunk) + cap);
		if (!c) longjmp(*oom, 2);
		c->cap = cap;
		c->used = 0;
		c->next = ar->head;
		ar->head = c;
	}
	p = (char *)(c + 1) + c->used;
	c->used += n;
	return p;
}

static void ar_free(arena *ar)
{
	chunk *c = ar->head, *n;
	while (c) { n = c->next; free(c); c = n; }
	ar->head = NULL;
}

/* ---------------------------------------------------------------- the parser's state */

enum { J_NULL, J_BOOL, J_INT, J_NUM, J_STR, J_ARR, J_OBJ };

typedef struct jv {
	int t;
	int b;                /* J_BOOL */
	long long i;          /* J_INT */
	char *s;              /* J_STR: UTF-8 (NUL-terminated; a NUL or a surrogate is flagged and stored as U+FFFD) */
	size_t slen;
	int bad;              /* J_STR: it held a NUL or a lone surrogate */
	long nscal;           /* J_STR: its Unicode scalars */
	struct jv *v;         /* J_ARR and J_OBJ: the values */
	char **k;             /* J_OBJ: the keys */
	int n;
} jv;

typedef struct {
	char *name;
	int line;
} ent;

typedef struct {
	jmp_buf jb;
	arena doc, rec;       /* the document's arena; the current record's JSON tree */
	int line;
	char code[32];
	char msg[300];
	const char *p, *end;  /* JSON being read */
	int depth;
	a2_doc *d;
	/* local targets to resolve after the blocks */
	ent *anchors, *fnrefs, *mediarefs, *anchor_refs, *footnotes, *refids;
	int nanchors, nfnrefs, nmediarefs, nanchor_refs, nfootnotes, nrefids;
	int canchors, cfnrefs, cmediarefs, canchor_refs, cfootnotes, crefids;
	int corpus_source;     /* provenance.source_kind is corpus (PACK-CORPUS corpus-1, A5) */
} ck;

static void fail(ck *c, const char *code, const char *fmt, ...)
{
	va_list ap;
	char m[240];
	va_start(ap, fmt);
	vsnprintf(m, sizeof m, fmt, ap);
	va_end(ap);
	snprintf(c->code, sizeof c->code, "%s", code);
	snprintf(c->msg, sizeof c->msg, "line %d: %s", c->line, m);
	longjmp(c->jb, 1);
}

static void *dalloc(ck *c, size_t n) { void *p = ar_alloc(&c->doc, n, &c->jb); memset(p, 0, n); return p; }
static void *ralloc(ck *c, size_t n) { return ar_alloc(&c->rec, n, &c->jb); }

static char *ddup(ck *c, const char *s, size_t n)
{
	char *p = (char *)ar_alloc(&c->doc, n + 1, &c->jb);
	memcpy(p, s, n);
	p[n] = 0;
	return p;
}

/* a name list for the end-of-file resolution */
static void note(ck *c, ent **list, int *n, int *cap, const char *name)
{
	if (*n == *cap) {
		int nc = *cap ? *cap * 2 : 64;
		ent *nl = (ent *)realloc(*list, (size_t)nc * sizeof(ent));
		if (!nl) longjmp(c->jb, 2);
		*list = nl;
		*cap = nc;
	}
	(*list)[*n].name = ddup(c, name, strlen(name));
	(*list)[*n].line = c->line;
	(*n)++;
}

static int has(ent *const *listp, int n, const char *name)
{
	const ent *list = *listp;
	int i;
	for (i = 0; i < n; i++)
		if (!strcmp(list[i].name, name)) return 1;
	return 0;
}

/* ---------------------------------------------------------------- JSON */

static void jws(ck *c) { while (c->p < c->end && (*c->p == ' ' || *c->p == '\t' || *c->p == '\n' || *c->p == '\r')) c->p++; }

static void jfail(ck *c, const char *what) { fail(c, "E_JSON", "%s", what); }

static int hexv(int ch)
{
	if (ch >= '0' && ch <= '9') return ch - '0';
	if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
	if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
	return -1;
}

static long hex4(ck *c)
{
	long v = 0;
	int i, h;
	if (c->end - c->p < 4) fail(c, "E_JSON", "invalid \\u escape");
	for (i = 0; i < 4; i++) {
		if ((h = hexv((unsigned char)c->p[i])) < 0) fail(c, "E_JSON", "invalid \\u escape");
		v = v * 16 + h;
	}
	c->p += 4;
	return v;
}

static void jstring(ck *c, jv *v)
{
	const char *q = ++c->p;
	size_t cap = 0, n = 0;
	char *out;
	/* the decoded string is never longer than the source */
	while (q < c->end && *q != '"') { if (*q == '\\') q++; q++; }
	if (q >= c->end) fail(c, "E_JSON", "unterminated string");
	cap = (size_t)(q - c->p) + 1;
	out = (char *)ralloc(c, cap + 4);
	v->t = J_STR;
	v->bad = 0;
	v->nscal = 0;
	while (c->p < c->end && *c->p != '"') {
		unsigned char ch = (unsigned char)*c->p;
		if (ch < 0x20) fail(c, "E_JSON", "invalid control character in string");
		if (ch != '\\') {
			uint32_t cp;
			int k = t8_decode(c->p, (size_t)(c->end - c->p), &cp);
			memcpy(out + n, c->p, (size_t)k);
			n += (size_t)k;
			c->p += k;
			v->nscal++;
			continue;
		}
		c->p++;
		if (c->p >= c->end) fail(c, "E_JSON", "invalid escape");
		ch = (unsigned char)*c->p++;
		switch (ch) {
		case '"': out[n++] = '"'; break;
		case '\\': out[n++] = '\\'; break;
		case '/': out[n++] = '/'; break;
		case 'b': out[n++] = '\b'; break;
		case 'f': out[n++] = '\f'; break;
		case 'n': out[n++] = '\n'; break;
		case 'r': out[n++] = '\r'; break;
		case 't': out[n++] = '\t'; break;
		case 'u': {
			long u = hex4(c);
			if (u >= 0xD800 && u <= 0xDBFF && c->end - c->p >= 6 && c->p[0] == '\\' && c->p[1] == 'u') {
				const char *save = c->p;
				long lo;
				c->p += 2;
				lo = hex4(c);
				if (lo >= 0xDC00 && lo <= 0xDFFF) u = 0x10000 + ((u - 0xD800) << 10) + (lo - 0xDC00);
				else c->p = save;
			}
			if (u == 0 || (u >= 0xD800 && u <= 0xDFFF)) { v->bad = 1; u = 0xFFFD; }
			n += (size_t)t8_encode((uint32_t)u, out + n);
			break;
		}
		default: fail(c, "E_JSON", "invalid \\escape");
		}
		v->nscal++;
	}
	c->p++; /* the closing quote */
	out[n] = 0;
	v->s = out;
	v->slen = n;
}

static void jvalue(ck *c, jv *v);

static void jnumber(ck *c, jv *v)
{
	const char *s = c->p;
	int isint = 1, digits = 0;
	if (*c->p == '-') c->p++;
	if (c->end - c->p >= 8 && !strncmp(c->p, "Infinity", 8)) fail(c, "E_JSON", "non-finite JSON number");
	if (c->p >= c->end || *c->p < '0' || *c->p > '9') jfail(c, "expecting value");
	if (*c->p == '0') { c->p++; digits = 1; }
	else while (c->p < c->end && *c->p >= '0' && *c->p <= '9') { c->p++; digits++; }
	if (c->p < c->end && *c->p == '.') {
		isint = 0;
		c->p++;
		if (c->p >= c->end || *c->p < '0' || *c->p > '9') jfail(c, "invalid number");
		while (c->p < c->end && *c->p >= '0' && *c->p <= '9') c->p++;
	}
	if (c->p < c->end && (*c->p == 'e' || *c->p == 'E')) {
		isint = 0;
		c->p++;
		if (c->p < c->end && (*c->p == '+' || *c->p == '-')) c->p++;
		if (c->p >= c->end || *c->p < '0' || *c->p > '9') jfail(c, "invalid number");
		while (c->p < c->end && *c->p >= '0' && *c->p <= '9') c->p++;
	}
	v->t = J_NUM;
	if (isint && digits <= 15) {
		char buf[24];
		size_t n = (size_t)(c->p - s);
		memcpy(buf, s, n);
		buf[n] = 0;
		v->t = J_INT;
		v->i = strtoll(buf, NULL, 10);
	}
}

static void jcontainer(ck *c, jv *v, int obj)
{
	struct node { jv v; char *k; struct node *next; } *head = NULL, *tail = NULL, *nd;
	int n = 0, i;
	if (++c->depth > JSON_DEPTH) fail(c, "E_DEPTH", "JSON nesting too deep");
	c->p++;
	jws(c);
	if (c->p < c->end && *c->p == (obj ? '}' : ']')) c->p++;
	else for (;;) {
		nd = (struct node *)ralloc(c, sizeof *nd);
		nd->next = NULL;
		nd->k = NULL;
		jws(c);
		if (obj) {
			jv key;
			if (c->p >= c->end || *c->p != '"') jfail(c, "expecting property name enclosed in double quotes");
			jstring(c, &key);
			nd->k = key.s;
			jws(c);
			if (c->p >= c->end || *c->p != ':') jfail(c, "expecting ':' delimiter");
			c->p++;
		}
		jvalue(c, &nd->v);
		if (tail) tail->next = nd; else head = nd;
		tail = nd;
		n++;
		jws(c);
		if (c->p < c->end && *c->p == ',') { c->p++; continue; }
		if (c->p < c->end && *c->p == (obj ? '}' : ']')) { c->p++; break; }
		jfail(c, obj ? "expecting ',' delimiter" : "expecting ',' delimiter");
	}
	v->t = obj ? J_OBJ : J_ARR;
	v->n = n;
	v->v = n ? (jv *)ralloc(c, (size_t)n * sizeof(jv)) : NULL;
	v->k = obj && n ? (char **)ralloc(c, (size_t)n * sizeof(char *)) : NULL;
	for (i = 0, nd = head; nd; nd = nd->next, i++) {
		v->v[i] = nd->v;
		if (obj) {
			int j;
			for (j = 0; j < i; j++)
				if (!strcmp(v->k[j], nd->k)) fail(c, "E_JSON_DUPLICATE", "duplicate JSON key %s", nd->k);
			v->k[i] = nd->k;
		}
	}
	c->depth--;
}

static void jvalue(ck *c, jv *v)
{
	memset(v, 0, sizeof *v);
	jws(c);
	if (c->p >= c->end) jfail(c, "expecting value");
	switch (*c->p) {
	case '{': jcontainer(c, v, 1); return;
	case '[': jcontainer(c, v, 0); return;
	case '"': jstring(c, v); return;
	case 't': if (c->end - c->p >= 4 && !strncmp(c->p, "true", 4)) { c->p += 4; v->t = J_BOOL; v->b = 1; return; } break;
	case 'f': if (c->end - c->p >= 5 && !strncmp(c->p, "false", 5)) { c->p += 5; v->t = J_BOOL; return; } break;
	case 'n': if (c->end - c->p >= 4 && !strncmp(c->p, "null", 4)) { c->p += 4; v->t = J_NULL; return; } break;
	case 'N': if (c->end - c->p >= 3 && !strncmp(c->p, "NaN", 3)) fail(c, "E_JSON", "non-finite JSON number NaN"); break;
	case 'I': if (c->end - c->p >= 8 && !strncmp(c->p, "Infinity", 8)) fail(c, "E_JSON", "non-finite JSON number Infinity"); break;
	default: if (*c->p == '-' || (*c->p >= '0' && *c->p <= '9')) { jnumber(c, v); return; }
	}
	jfail(c, "expecting value");
}

/* ---------------------------------------------------------------- checks (validate_v2.py's Checker) */

static const jv *get(const jv *o, const char *k)
{
	int i;
	if (!o || o->t != J_OBJ) return NULL;
	for (i = 0; i < o->n; i++)
		if (!strcmp(o->k[i], k)) return &o->v[i];
	return NULL;
}

static int in_list(const char *s, const char *const *list)
{
	for (; *list; list++)
		if (!strcmp(s, *list)) return 1;
	return 0;
}

/* obj: an object with exactly the required fields and any of the optional ones; the first missing (or unknown) in
 * sorted order is reported, as the reference parser does */
static void obj(ck *c, const jv *v, const char *const *req, const char *const *opt)
{
	const char *const *r;
	const char *first = NULL;
	int i;
	if (!v || v->t != J_OBJ) fail(c, "E_TYPE", "expected object");
	for (r = req; *r; r++)
		if (!get(v, *r) && (!first || strcmp(*r, first) < 0)) first = *r;
	if (first) fail(c, "E_FIELD", "missing field %s", first);
	for (i = 0; i < v->n; i++)
		if (!in_list(v->k[i], req) && !(opt && in_list(v->k[i], opt)) && (!first || strcmp(v->k[i], first) < 0)) first = v->k[i];
	if (first) fail(c, "E_FIELD", "unknown field %s", first);
}

#define F(...) (const char *const[]){ __VA_ARGS__, NULL }

static const char *str(ck *c, const jv *v, const char *key, int empty, int raw)
{
	size_t i;
	if (!v || v->t != J_STR || (!empty && !v->slen)) fail(c, "E_TYPE", "%s must be %s", key, empty ? "a string" : "a non-empty string");
	if ((long)v->slen > MAX_LINE) fail(c, "E_LIMIT", "%s exceeds string limit", key);
	if (v->bad) fail(c, "E_UNICODE", "NUL and surrogate scalars forbidden");
	if (!raw) {
		for (i = 0; i < v->slen; i++) {
			unsigned char ch = (unsigned char)v->s[i];
			if (ch < 32 || ch == 127) fail(c, "E_CONTROL", "%s contains a control character", key);
			if (ch == 0xC2 && i + 1 < v->slen && (unsigned char)v->s[i + 1] == 0xAD) fail(c, "E_SOFT_HYPHEN", "use a shy run, not U+00AD");
		}
	}
	return ddup(c, v->s, v->slen);
}

static long integer(ck *c, const jv *v, const char *key, long lo, long hi)
{
	if (!v || v->t != J_INT || v->i < lo || v->i > hi) fail(c, "E_RANGE", "%s must be an integer in %ld..%ld", key, lo, hi);
	return (long)v->i;
}

static void array(ck *c, const jv *v, const char *key, int nonempty, int max)
{
	if (!v || v->t != J_ARR || v->n > max || (nonempty && !v->n))
		fail(c, "E_TYPE", "%s must be %s array", key, nonempty ? "a non-empty" : "a bounded");
}

typedef int (*pattern_fn)(const char *s);

static int lc_digit(int ch) { return (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9'); }
static int all_of(const char *s, const char *set, int first_lc_digit)
{
	if (!*s) return 0;
	if (first_lc_digit && !lc_digit((unsigned char)*s)) return 0;
	for (; *s; s++)
		if (!lc_digit((unsigned char)*s) && !strchr(set, *s)) return 0;
	return 1;
}

/* ANCHOR_RE [a-z][a-z0-9._-]{0,127} */
static int pat_anchor(const char *s)
{
	size_t n = strlen(s);
	return n >= 1 && n <= 128 && *s >= 'a' && *s <= 'z' && all_of(s, "._-", 0);
}

/* KEY_RE [a-z0-9][a-z0-9:._/-]{0,127} */
static int pat_key(const char *s)
{
	size_t n = strlen(s);
	return n >= 1 && n <= 128 && all_of(s, ":._/-", 1);
}

static int digits(const char *s, const char *e) { if (s >= e) return 0; for (; s < e; s++) if (*s < '0' || *s > '9') return 0; return 1; }

static int unicode_space(const char *s)
{
	size_t i = 0, n = strlen(s);
	while (i < n) {
		uint32_t cp;
		i += (size_t)t8_decode(s + i, n - i, &cp);
		if (cp == ' ' || (cp >= 0x09 && cp <= 0x0D) || (cp >= 0x1C && cp <= 0x1F) || cp == 0x85 || cp == 0xA0 ||
		    cp == 0x1680 || (cp >= 0x2000 && cp <= 0x200A) || cp == 0x2028 || cp == 0x2029 || cp == 0x202F ||
		    cp == 0x205F || cp == 0x3000)
			return 1;
	}
	return 0;
}

/* ID_RE: rfcN[-sN.N...], wiki:<title key>, cisco:<key>, nist:<key>-<letters>-<n>[(n)], fx:<key>, draft:<key>-NN */
static int pat_id(const char *s)
{
	if (!strncmp(s, "rfc", 3)) {
		const char *p = s + 3;
		if (*p < '1' || *p > '9') return 0;
		while (*p >= '0' && *p <= '9') p++;
		if (!*p) return 1;
		if (strncmp(p, "-s", 2)) return 0;
		p += 2;
		for (;;) {
			const char *q = p;
			while (*p >= '0' && *p <= '9') p++;
			if (p == q) return 0;
			if (!*p) return 1;
			if (*p != '.') return 0;
			p++;
		}
	}
	if (!strncmp(s, "wiki:", 5)) return s[5] && !strpbrk(s + 5, "/#?\\") && !unicode_space(s + 5);
	if (!strncmp(s, "cisco:", 6)) return all_of(s + 6, "._-", 1);
	if (!strncmp(s, "fx:", 3)) return all_of(s + 3, "._-", 1);
	if (!strncmp(s, "corpus:", 7)) return all_of(s + 7, "._-", 1); /* corpus-1 A1: corpus:<doc_key> */
	if (!strncmp(s, "draft:", 6)) {
		size_t n = strlen(s + 6);
		const char *b = s + 6;
		if (n < 4 || b[n - 3] != '-' || !digits(b + n - 2, b + n)) return 0;
		{
			char pre[300];
			if (n - 3 >= sizeof pre) return 0;
			memcpy(pre, b, n - 3);
			pre[n - 3] = 0;
			return all_of(pre, "._-", 1);
		}
	}
	if (!strncmp(s, "nist:", 5)) {
		const char *b = s + 5, *e = b + strlen(b), *q;
		char pre[300];
		if (e > b && e[-1] == ')') { /* the optional "(n)" */
			q = e - 1;
			while (q > b && q[-1] >= '0' && q[-1] <= '9') q--;
			if (q == e - 1 || q == b || q[-1] != '(') return 0;
			e = q - 1;
		}
		q = e;
		while (q > b && q[-1] >= '0' && q[-1] <= '9') q--;
		if (q == e || q == b || q[-1] != '-') return 0;
		e = q - 1;
		q = e;
		while (q > b && q[-1] >= 'a' && q[-1] <= 'z') q--;
		if (q == e || q == b || q[-1] != '-') return 0;
		e = q - 1;
		if (e <= b || (size_t)(e - b) >= sizeof pre) return 0;
		memcpy(pre, b, (size_t)(e - b));
		pre[e - b] = 0;
		return all_of(pre, "._-", 1);
	}
	return 0;
}

static const char **unique_strings(ck *c, const jv *v, const char *key, pattern_fn pat, int nonempty, int *count)
{
	const char **out;
	int i, j;
	array(c, v, key, nonempty, 65536);
	out = (const char **)dalloc(c, (size_t)(v->n + 1) * sizeof(char *));
	for (i = 0; i < v->n; i++) {
		out[i] = str(c, &v->v[i], key, 0, 0);
		if (pat && !pat(out[i])) fail(c, "E_ID", "invalid %s: %s", key, out[i]);
	}
	for (i = 0; i < v->n; i++)
		for (j = 0; j < i; j++)
			if (!strcmp(out[i], out[j])) fail(c, "E_DUPLICATE", "duplicate %s", key);
	if (count) *count = v->n;
	return out;
}

/* one string checked as unique_strings([value], ...) does */
static const char *one(ck *c, const jv *v, const char *key, pattern_fn pat)
{
	const char *s = str(c, v, key, 0, 0);
	if (pat && !pat(s)) fail(c, "E_ID", "invalid %s: %s", key, s);
	return s;
}

static const char *url(ck *c, const jv *v, const char *key)
{
	const char *s = str(c, v, key, 0, 0), *h, *e;
	int ok = 0;
	if (!strncmp(s, "https://", 8) || !strncmp(s, "http://", 7) || !strncmp(s, "HTTP://", 7) || !strncmp(s, "HTTPS://", 8)) {
		h = strstr(s, "//") + 2;
		e = h + strcspn(h, "/?#");
		ok = e > h && !memchr(h, '@', (size_t)(e - h)) && *h != ':';
	}
	/* corpus-1 A5: a corpus source with no HTTP(S) URL names its RAG document, rag:<generation>/<doc_key> (Copy-only) */
	if (!ok && c->corpus_source && !strcmp(key, "source_url") && !strncmp(s, "rag:", 4)) {
		const char *slash = strchr(s + 4, '/');
		char gen[200];
		if (slash && (size_t)(slash - s - 4) < sizeof gen) {
			memcpy(gen, s + 4, (size_t)(slash - s - 4)); gen[slash - s - 4] = 0;
			ok = all_of(gen, "._-", 1) && all_of(slash + 1, "._-", 1);
		}
	}
	if (!ok || unicode_space(s)) fail(c, "E_URL", "%s must be an absolute HTTP(S) URL without credentials", key);
	return s;
}

static const char *date(ck *c, const jv *v, const char *key, int nullable)
{
	static const int mdays[12] = { 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31 };
	int y, m, d, i, leap;
	const char *s;
	if (v && v->t == J_NULL && nullable) return NULL;
	if (!v || v->t != J_STR || v->slen != 10) goto bad;
	s = v->s;
	for (i = 0; i < 10; i++)
		if (i == 4 || i == 7 ? s[i] != '-' : (s[i] < '0' || s[i] > '9')) goto bad;
	y = atoi(s);
	m = atoi(s + 5);
	d = atoi(s + 8);
	leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
	if (y < 1 || m < 1 || m > 12 || d < 1 || d > mdays[m - 1] + (m == 2 && leap)) goto bad;
	return ddup(c, s, 10);
bad:
	fail(c, "E_DATE", "%s must be YYYY-MM-DD%s", key, nullable ? " or null" : "");
	return NULL;
}

static int is_hex64(const jv *v)
{
	size_t i;
	if (!v || v->t != J_STR || v->slen != 64) return 0;
	for (i = 0; i < 64; i++)
		if (!((v->s[i] >= '0' && v->s[i] <= '9') || (v->s[i] >= 'a' && v->s[i] <= 'f'))) return 0;
	return 1;
}

/* (?:zim:[a-z0-9._-]+/|fixture:media/)[^\s\\]+, no empty, "." or ".." segment after the first, no '%' */
static int safe_uri(const char *u)
{
	const char *p, *seg;
	if (!strncmp(u, "zim:", 4)) {
		p = u + 4;
		seg = p;
		while (lc_digit((unsigned char)*p) || *p == '.' || *p == '_' || *p == '-') p++;
		if (p == seg || *p != '/') return 0;
		p++;
	} else if (!strncmp(u, "fixture:media/", 14)) p = u + 14;
	else return 0;
	if (!*p || strchr(p, '\\') || strchr(u, '%') || unicode_space(p)) return 0;
	/* the segments after the first '/' */
	seg = strchr(u, '/') + 1;
	for (;;) {
		size_t n = strcspn(seg, "/");
		if (n == 0 || (n == 1 && seg[0] == '.') || (n == 2 && seg[0] == '.' && seg[1] == '.')) return 0;
		if (!seg[n]) break;
		seg += n + 1;
	}
	return 1;
}

static int safe_mime(const char *m)
{
	const char *p = m;
	while (lc_digit((unsigned char)*p) || *p == '.' || *p == '+' || *p == '-') p++;
	if (p == m || *p != '/') return 0;
	m = ++p;
	while (lc_digit((unsigned char)*p) || (*p >= 'A' && *p <= 'Z') || *p == '.' || *p == '+' || *p == '-') p++;
	return p > m && !*p;
}

static const char *const media_kinds[] = { "image", "maths", "pdf", "cli", "table", "audio", "video", "interactivity", NULL };

static void metadata(ck *c, const jv *m)
{
	a2_doc *d = c->d;
	const jv *st, *p, *v;
	int i;
	obj(c, m, F("id", "class", "title", "sort_key", "aliases", "redirects", "categories", "primary_category", "places",
	            "years", "published", "updated", "status", "word_count", "media", "content_sha256", "provenance"), F("corpus"));
	d->id = one(c, get(m, "id"), "id", pat_id);
	v = get(m, "class");
	if (v->t != J_STR || (strcmp(v->s, "library") && strcmp(v->s, "corpus"))) fail(c, "E_CLASS", "class must be library or corpus");
	d->cls = ddup(c, v->s, v->slen);
	d->title = str(c, get(m, "title"), "title", 0, 0);
	d->sort_key = str(c, get(m, "sort_key"), "sort_key", 0, 0);
	{
		/* NFKC(title).casefold(): checked exactly for an ASCII title (the builder checks the rest) */
		const char *t = d->title;
		int ascii = 1;
		for (; *t; t++) if ((unsigned char)*t >= 0x80) ascii = 0;
		if (ascii) {
			size_t n = strlen(d->title);
			if (strlen(d->sort_key) != n) fail(c, "E_SORT", "sort_key must be NFKC(title).casefold()");
			for (i = 0; i < (int)n; i++) {
				char ch = d->title[i];
				if (ch >= 'A' && ch <= 'Z') ch = (char)(ch - 'A' + 'a');
				if (d->sort_key[i] != ch) fail(c, "E_SORT", "sort_key must be NFKC(title).casefold()");
			}
		}
	}
	d->aliases = unique_strings(c, get(m, "aliases"), "aliases", NULL, 0, &d->naliases);
	d->redirects = unique_strings(c, get(m, "redirects"), "redirects", pat_id, 0, &d->nredirects);
	for (i = 0; i < d->nredirects; i++)
		if (!strcmp(d->redirects[i], d->id)) fail(c, "E_ID", "article cannot redirect to itself");
	d->categories = unique_strings(c, get(m, "categories"), "categories", pat_key, 1, &d->ncategories);
	v = get(m, "primary_category");
	{
		int found = 0;
		for (i = 0; v->t == J_STR && i < d->ncategories; i++) if (!strcmp(d->categories[i], v->s)) found = 1;
		if (!found) fail(c, "E_CATEGORY", "primary_category must be in categories");
		d->primary_category = ddup(c, v->s, v->slen);
	}
	d->places = unique_strings(c, get(m, "places"), "places", pat_key, 0, &d->nplaces);
	v = get(m, "years");
	array(c, v, "years", 0, 65536);
	d->years = (long *)dalloc(c, (size_t)(v->n + 1) * sizeof(long));
	for (i = 0; i < v->n; i++) d->years[i] = integer(c, &v->v[i], "years", -999999, 999999);
	d->nyears = v->n;
	for (i = 0; i < d->nyears; i++) {
		int j;
		for (j = 0; j < i; j++) if (d->years[i] == d->years[j]) fail(c, "E_DUPLICATE", "duplicate year");
	}
	d->published = date(c, get(m, "published"), "published", 1);
	d->updated = date(c, get(m, "updated"), "updated", 1);
	if (d->published && d->updated && strcmp(d->updated, d->published) < 0) fail(c, "E_DATE", "updated precedes published");
	st = get(m, "status");
	obj(c, st, F("obsoletes", "obsoleted_by", "updates", "updated_by", "errata"), NULL);
	d->obsoletes = unique_strings(c, get(st, "obsoletes"), "obsoletes", pat_id, 0, &d->nobsoletes);
	d->obsoleted_by = unique_strings(c, get(st, "obsoleted_by"), "obsoleted_by", pat_id, 0, &d->nobsoleted_by);
	d->updates = unique_strings(c, get(st, "updates"), "updates", pat_id, 0, &d->nupdates);
	d->updated_by = unique_strings(c, get(st, "updated_by"), "updated_by", pat_id, 0, &d->nupdated_by);
	d->errata = unique_strings(c, get(st, "errata"), "errata", NULL, 0, &d->nerrata);
	d->word_count = integer(c, get(m, "word_count"), "word_count", 0, 10000000);
	if (!is_hex64(get(m, "content_sha256"))) fail(c, "E_HASH", "content_sha256 must be 64 lower-case hex digits");
	d->content_sha256 = ddup(c, get(m, "content_sha256")->s, 64);
	p = get(m, "provenance");
	obj(c, p, F("source_kind", "source_url", "licence", "authors", "retrieved", "source_sha256", "revision", "pack"), NULL);
	v = get(p, "source_kind");
	if (v->t != J_STR || !in_list(v->s, F("rfc", "wiki", "cisco", "nist", "synthetic", "draft", "corpus"))) fail(c, "E_PROVENANCE", "unknown source_kind");
	c->corpus_source = !strcmp(v->s, "corpus"); /* corpus-1 A2 */
	d->source_kind = ddup(c, v->s, v->slen);
	d->licence = str(c, get(p, "licence"), "licence", 0, 0);
	d->pack = str(c, get(p, "pack"), "pack", 0, 0);
	d->revision = str(c, get(p, "revision"), "revision", 1, 0);
	d->authors = unique_strings(c, get(p, "authors"), "authors", NULL, 1, &d->nauthors);
	d->source_url = url(c, get(p, "source_url"), "source_url");
	d->retrieved = date(c, get(p, "retrieved"), "retrieved", 0);
	if (!is_hex64(get(p, "source_sha256"))) fail(c, "E_HASH", "invalid source_sha256");
	d->source_sha256 = ddup(c, get(p, "source_sha256")->s, 64);
	{
		static const char *const kinds[] = { "rfc", "wiki", "cisco", "nist", "synthetic", "draft", "corpus", NULL };
		static const char *const prefix[] = { "rfc", "wiki:", "cisco:", "nist:", "fx:", "draft:", "corpus:" }; /* corpus-1 A3 */
		for (i = 0; kinds[i]; i++)
			if (!strcmp(d->source_kind, kinds[i]) && strncmp(d->id, prefix[i], strlen(prefix[i])))
				fail(c, "E_PROVENANCE", "source_kind and id prefix disagree");
	}
	if (c->corpus_source && strcmp(d->cls, "corpus")) fail(c, "E_PROVENANCE", "corpus source requires corpus class"); /* corpus-1 A4 */
	v = get(m, "corpus");
	if (!strcmp(d->cls, "corpus")) {
		if (!v) fail(c, "E_PROVENANCE", "corpus class requires corpus provenance");
		obj(c, v, F("doc_id", "generation", "section", "cite", "handling"), NULL);
		d->corpus_doc_id = str(c, get(v, "doc_id"), "doc_id", 0, 0);
		d->corpus_generation = str(c, get(v, "generation"), "generation", 0, 0);
		d->corpus_section = str(c, get(v, "section"), "section", 0, 0);
		d->corpus_cite = str(c, get(v, "cite"), "cite", 0, 0);
		d->corpus_handling = str(c, get(v, "handling"), "handling", 0, 0);
	} else if (v) fail(c, "E_PROVENANCE", "library class cannot claim corpus provenance");
	v = get(m, "media");
	array(c, v, "media", 0, 4096);
	d->media = (a2_media *)dalloc(c, (size_t)(v->n + 1) * sizeof(a2_media));
	for (i = 0; i < v->n; i++) {
		const jv *e = &v->v[i], *k;
		a2_media *md = &d->media[i];
		int j;
		obj(c, e, F("id", "kind", "uri", "mime", "alt", "credit"), NULL);
		md->id = one(c, get(e, "id"), "id", pat_anchor);
		for (j = 0; j < i; j++) if (!strcmp(d->media[j].id, md->id)) fail(c, "E_DUPLICATE", "duplicate media id");
		k = get(e, "kind");
		if (k->t != J_STR || !in_list(k->s, media_kinds)) fail(c, "E_MEDIA", "unknown media kind");
		md->kind = ddup(c, k->s, k->slen);
		md->uri = str(c, get(e, "uri"), "uri", 0, 0);
		md->mime = str(c, get(e, "mime"), "mime", 0, 0);
		md->alt = str(c, get(e, "alt"), "alt", 0, 0);
		md->credit = str(c, get(e, "credit"), "credit", 0, 0);
		if (!safe_uri(md->uri)) fail(c, "E_MEDIA", "unsafe media URI");
		if (!safe_mime(md->mime)) fail(c, "E_MEDIA", "invalid MIME type");
		d->nmedia = i + 1;
	}
}

static void target(ck *c, const jv *t, a2_target *out)
{
	const jv *k = get(t, "kind");
	memset(out, 0, sizeof *out);
	if (!t || t->t != J_OBJ || !k) fail(c, "E_TARGET", "target must be a typed object");
	if (k->t == J_STR && !strcmp(k->s, "article")) {
		obj(c, t, F("kind", "id"), F("anchor"));
		out->kind = T_ARTICLE;
		out->id = one(c, get(t, "id"), "id", pat_id);
		if (get(t, "anchor")) out->anchor = one(c, get(t, "anchor"), "anchor", pat_anchor);
	} else if (k->t == J_STR && !strcmp(k->s, "anchor")) {
		obj(c, t, F("kind", "anchor"), NULL);
		out->kind = T_ANCHOR;
		out->anchor = one(c, get(t, "anchor"), "anchor", pat_anchor);
		note(c, &c->anchor_refs, &c->nanchor_refs, &c->canchor_refs, out->anchor);
	} else if (k->t == J_STR && !strcmp(k->s, "rfc")) {
		obj(c, t, F("kind", "number"), F("section"));
		out->kind = T_RFC;
		out->number = integer(c, get(t, "number"), "number", 1, 999999);
		if (get(t, "section")) {
			const char *s = str(c, get(t, "section"), "section", 0, 0), *p = s;
			int ok = 1;
			if (*p >= 'A' && *p <= 'Z') p++;
			else if (*p >= '0' && *p <= '9') while (*p >= '0' && *p <= '9') p++;
			else ok = 0;
			while (ok && *p) {
				const char *q;
				if (*p != '.') { ok = 0; break; }
				q = ++p;
				while (*p >= '0' && *p <= '9') p++;
				if (p == q) ok = 0;
			}
			if (!ok) fail(c, "E_TARGET", "invalid RFC section");
			out->section = s;
		}
	} else if (k->t == J_STR && !strcmp(k->s, "url")) {
		obj(c, t, F("kind", "url"), NULL);
		out->kind = T_URL;
		out->url = url(c, get(t, "url"), "url");
	} else if (k->t == J_STR && !strcmp(k->s, "media")) {
		const jv *id;
		obj(c, t, F("kind", "id"), NULL);
		out->kind = T_MEDIA;
		id = get(t, "id");
		out->id = id->t == J_STR ? ddup(c, id->s, id->slen) : "";
		/* a non-string id never resolves (validate_v2: E_MEDIA at the end) */
		note(c, &c->mediarefs, &c->nmediarefs, &c->cmediarefs, id->t == J_STR && !id->bad ? out->id : "\x01");
	} else fail(c, "E_TARGET", "unknown target kind");
}

static void runs(ck *c, const jv *v, a2_runs *out, int depth)
{
	static const char *const styles[] = { "bold", "italic", "mono", "sub", "sup", "smallcaps",
	                                      "underline", "strike", "small", "big", NULL };
	static const char *const keywords[] = { "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT",
	                                        "RECOMMENDED", "MAY", "OPTIONAL", NULL };
	int i;
	if (depth > 8) fail(c, "E_DEPTH", "inline nesting exceeds eight");
	array(c, v, "runs", 1, 4096);
	out->n = v->n;
	out->r = (a2_run *)dalloc(c, (size_t)v->n * sizeof(a2_run));
	for (i = 0; i < v->n; i++) {
		const jv *r = &v->v[i], *t = get(r, "t");
		a2_run *o = &out->r[i];
		int k;
		if (r->t == J_STR) { o->t = R_TEXT; o->text = str(c, r, "runs", 1, 0); continue; }
		if (r->t != J_OBJ || !t || t->t != J_STR) fail(c, "E_INLINE", "unknown inline type");
		for (k = 0; styles[k]; k++)
			if (!strcmp(t->s, styles[k])) break;
		if (styles[k]) {
			obj(c, r, F("t", "runs"), NULL);
			o->t = R_BOLD + k;
			runs(c, get(r, "runs"), &o->kids, depth + 1);
		} else if (!strcmp(t->s, "keyword")) {
			const jv *x;
			obj(c, r, F("t", "text"), NULL);
			x = get(r, "text");
			if (x->t != J_STR || !in_list(x->s, keywords)) fail(c, "E_KEYWORD", "invalid RFC keyword");
			o->t = R_KEYWORD;
			o->text = ddup(c, x->s, x->slen);
		} else if (!strcmp(t->s, "link")) {
			obj(c, r, F("t", "target", "label"), NULL);
			o->t = R_LINK;
			target(c, get(r, "target"), &o->target);
			runs(c, get(r, "label"), &o->kids, depth + 1);
		} else if (!strcmp(t->s, "footnote_ref")) {
			obj(c, r, F("t", "id"), NULL);
			o->t = R_FOOTREF;
			o->text = one(c, get(r, "id"), "id", pat_anchor);
			note(c, &c->fnrefs, &c->nfnrefs, &c->cfnrefs, o->text);
		} else if (!strcmp(t->s, "glossary")) {
			obj(c, r, F("t", "term", "label", "definition"), NULL);
			o->t = R_GLOSSARY;
			o->text = str(c, get(r, "term"), "term", 0, 0);
			o->definition = str(c, get(r, "definition"), "definition", 0, 0);
			runs(c, get(r, "label"), &o->kids, depth + 1);
		} else if (!strcmp(t->s, "shy")) {
			obj(c, r, F("t"), NULL);
			o->t = R_SHY;
		} else fail(c, "E_INLINE", "unknown inline type");
	}
}

static void list_block(ck *c, const jv *b, a2_list *out, int depth)
{
	const jv *o, *items;
	int i, j;
	if (depth > 8) fail(c, "E_DEPTH", "list nesting exceeds eight");
	obj(c, b, F("ordered", "start", "items"), NULL);
	o = get(b, "ordered");
	if (o->t != J_BOOL) fail(c, "E_TYPE", "ordered must be boolean");
	out->ordered = o->b;
	out->start = integer(c, get(b, "start"), "start", 1, 999999);
	if (!out->ordered && out->start != 1) fail(c, "E_RANGE", "unordered lists start at one");
	items = get(b, "items");
	array(c, items, "items", 1, 4096);
	out->nitems = items->n;
	out->items = (a2_item *)dalloc(c, (size_t)items->n * sizeof(a2_item));
	for (i = 0; i < items->n; i++) {
		const jv *it = &items->v[i], *ch;
		obj(c, it, F("runs", "children"), NULL);
		runs(c, get(it, "runs"), &out->items[i].runs, 1);
		ch = get(it, "children");
		array(c, ch, "children", 0, 64);
		out->items[i].nkids = ch->n;
		out->items[i].kids = (a2_list *)dalloc(c, (size_t)(ch->n + 1) * sizeof(a2_list));
		for (j = 0; j < ch->n; j++) list_block(c, &ch->v[j], &out->items[i].kids[j], depth + 1);
	}
}

static const char *const block_names[A2_NKINDS + 1] = { "title", "subtitle", "byline", "heading", "paragraph", "list",
	"definitions", "pre", "code", "note", "caution", "warning", "quote", "table", "figure", "maths", "factbox", "infobox",
	"quick_facts", "references", "see_also", "timeline_year", "footnote", "attribution", NULL };

const char *a2_kind_name(int k) { return k >= 0 && k < A2_NKINDS ? block_names[k] : "?"; }

static void block(ck *c, const char *kind, const jv *b, a2_block *o)
{
	int k, i;
	for (k = 0; k < A2_NKINDS; k++)
		if (!strcmp(kind, block_names[k])) break;
	if (k == A2_NKINDS) fail(c, "E_BLOCK", "unknown block %s", kind);
	memset(o, 0, sizeof *o);
	o->kind = k;
	o->line = c->line;
	switch (k) {
	case A2_SUBTITLE: case A2_BYLINE: case A2_PARAGRAPH: case A2_NOTE: case A2_CAUTION: case A2_WARNING: case A2_QUOTE:
		obj(c, b, F("runs"), NULL);
		runs(c, get(b, "runs"), &o->runs, 1);
		break;
	case A2_TITLE:
		obj(c, b, F("text"), NULL);
		o->text = str(c, get(b, "text"), "text", 0, 0);
		break;
	case A2_HEADING:
		obj(c, b, F("level", "anchor", "runs"), NULL);
		o->level = (int)integer(c, get(b, "level"), "level", 1, 6);
		o->anchor = one(c, get(b, "anchor"), "anchor", pat_anchor);
		if (has(&c->anchors, c->nanchors, o->anchor)) fail(c, "E_DUPLICATE", "duplicate heading anchor");
		note(c, &c->anchors, &c->nanchors, &c->canchors, o->anchor);
		runs(c, get(b, "runs"), &o->runs, 1);
		break;
	case A2_LIST:
		list_block(c, b, &o->list, 1);
		break;
	case A2_DEFINITIONS: {
		const jv *it = get(b, "items");
		obj(c, b, F("items"), NULL);
		it = get(b, "items");
		array(c, it, "items", 1, 4096);
		o->ndefs = it->n;
		o->defs = (a2_def *)dalloc(c, (size_t)it->n * sizeof(a2_def));
		for (i = 0; i < it->n; i++) {
			obj(c, &it->v[i], F("term", "definition"), NULL);
			runs(c, get(&it->v[i], "term"), &o->defs[i].term, 1);
			runs(c, get(&it->v[i], "definition"), &o->defs[i].def, 1);
		}
		break;
	}
	case A2_PRE:
		obj(c, b, F("text"), NULL);
		o->text = str(c, get(b, "text"), "text", 1, 1);
		break;
	case A2_CODE: {
		const jv *t, *vars;
		long len, end = 0;
		obj(c, b, F("language", "text", "prompt", "variables"), NULL);
		o->language = str(c, get(b, "language"), "language", 0, 0);
		t = get(b, "text");
		o->text = str(c, t, "text", 0, 1);
		o->prompt = str(c, get(b, "prompt"), "prompt", 1, 0);
		if (o->prompt[0] && strncmp(o->text, o->prompt, strlen(o->prompt))) fail(c, "E_CODE", "text does not start with prompt");
		vars = get(b, "variables");
		array(c, vars, "variables", 0, 4096);
		len = t->nscal;
		o->nvar = vars->n;
		o->var = (int *)dalloc(c, (size_t)(2 * vars->n + 1) * sizeof(int));
		for (i = 0; i < vars->n; i++) {
			obj(c, &vars->v[i], F("start", "end"), NULL);
			o->var[2 * i] = (int)integer(c, get(&vars->v[i], "start"), "start", end, len - 1);
			o->var[2 * i + 1] = (int)integer(c, get(&vars->v[i], "end"), "end", o->var[2 * i] + 1, len);
			end = o->var[2 * i + 1];
		}
		break;
	}
	case A2_TABLE: {
		static const char *const shades[] = { "yes", "no", "partial", "neutral", "header", "highlight", NULL };
		const jv *cols, *rows;
		unsigned char *occ;
		int w, h, r, n = 0, filled = 0;
		obj(c, b, F("columns", "header_rows", "rows"), NULL);
		cols = get(b, "columns");
		array(c, cols, "columns", 1, 64);
		rows = get(b, "rows");
		array(c, rows, "rows", 1, 1024);
		w = cols->n;
		h = rows->n;
		o->ncols = w;
		o->nrows = h;
		o->align = (int *)dalloc(c, (size_t)w * sizeof(int));
		for (i = 0; i < w; i++) {
			const jv *a = &cols->v[i];
			if (a->t != J_STR || !in_list(a->s, F("left", "centre", "right"))) fail(c, "E_TABLE", "invalid column alignment");
			o->align[i] = !strcmp(a->s, "left") ? COL_LEFT : !strcmp(a->s, "centre") ? COL_CENTRE : COL_RIGHT;
		}
		o->header_rows = (int)integer(c, get(b, "header_rows"), "header_rows", 0, h);
		for (r = 0; r < h; r++) {
			array(c, &rows->v[r], "rows", 0, 64);
			n += rows->v[r].n;
		}
		o->cells = (a2_cell *)dalloc(c, (size_t)(n + 1) * sizeof(a2_cell));
		occ = (unsigned char *)ralloc(c, (size_t)w * (size_t)h);
		memset(occ, 0, (size_t)w * (size_t)h);
		for (r = 0; r < h; r++) {
			const jv *row = &rows->v[r];
			int col = 0, ci;
			for (ci = 0; ci < row->n; ci++) {
				const jv *cell = &row->v[ci], *shade;
				a2_cell *oc = &o->cells[o->ncells];
				int rr, cc;
				obj(c, cell, F("runs", "rowspan", "colspan"), F("shade"));
				runs(c, get(cell, "runs"), &oc->runs, 1);
				shade = get(cell, "shade");
				oc->shade = SHADE_NONE;
				if (shade) {
					int k;
					if (shade->t != J_STR) fail(c, "E_TABLE", "invalid cell shade");
					for (k = 0; shades[k] && strcmp(shade->s, shades[k]); k++) {}
					if (!shades[k]) fail(c, "E_TABLE", "invalid cell shade");
					oc->shade = SHADE_YES + k;
				}
				oc->rowspan = (int)integer(c, get(cell, "rowspan"), "rowspan", 1, h);
				oc->colspan = (int)integer(c, get(cell, "colspan"), "colspan", 1, w);
				while (col < w && occ[r * w + col]) col++;
				if (r + oc->rowspan > h || col + oc->colspan > w) fail(c, "E_TABLE", "span outside table");
				if (r < o->header_rows && o->header_rows < r + oc->rowspan) fail(c, "E_TABLE", "span crosses header boundary");
				for (rr = r; rr < r + oc->rowspan; rr++)
					for (cc = col; cc < col + oc->colspan; cc++) {
						if (occ[rr * w + cc]) fail(c, "E_TABLE", "overlapping span");
						occ[rr * w + cc] = 1;
						filled++;
					}
				oc->row = r;
				oc->col = col;
				col += oc->colspan;
				o->ncells++;
			}
		}
		if (filled != w * h) fail(c, "E_TABLE", "table has uncovered cells");
		break;
	}
	case A2_FIGURE: case A2_MATHS: {
		const jv *mv;
		if (k == A2_FIGURE) obj(c, b, F("media", "caption", "credit"), NULL);
		else obj(c, b, F("media", "alt"), NULL);
		mv = get(b, "media");
		o->media = str(c, mv, "media", 0, 0);
		note(c, &c->mediarefs, &c->nmediarefs, &c->cmediarefs, o->media);
		if (k == A2_FIGURE) {
			runs(c, get(b, "caption"), &o->runs, 1);
			o->credit = str(c, get(b, "credit"), "credit", 0, 0);
		} else {
			const a2_media *md = a2_media_find(c->d, o->media);
			o->alt = str(c, get(b, "alt"), "alt", 0, 0);
			if (md && (strcmp(md->kind, "maths") || strcmp(md->mime, "image/svg+xml"))) fail(c, "E_MEDIA", "maths must reference an SVG maths item");
		}
		break;
	}
	case A2_FACTBOX: case A2_INFOBOX: case A2_QUICK_FACTS: {
		const jv *rows;
		obj(c, b, F("title", "rows"), NULL);
		o->title = str(c, get(b, "title"), "title", 0, 0);
		rows = get(b, "rows");
		array(c, rows, "rows", 1, 1024);
		o->nfacts = rows->n;
		o->facts = (a2_fact *)dalloc(c, (size_t)rows->n * sizeof(a2_fact));
		for (i = 0; i < rows->n; i++) {
			obj(c, &rows->v[i], F("key", "runs"), NULL);
			o->facts[i].key = str(c, get(&rows->v[i], "key"), "key", 0, 0);
			runs(c, get(&rows->v[i], "runs"), &o->facts[i].runs, 1);
		}
		break;
	}
	case A2_REFERENCES: case A2_SEE_ALSO: {
		const jv *items;
		obj(c, b, F("items"), NULL);
		items = get(b, "items");
		array(c, items, "items", 1, 4096);
		o->nrefs = items->n;
		o->refs = (a2_ref *)dalloc(c, (size_t)items->n * sizeof(a2_ref));
		for (i = 0; i < items->n; i++) {
			const jv *it = &items->v[i], *kv;
			a2_ref *rf = &o->refs[i];
			if (k == A2_REFERENCES) obj(c, it, F("id", "kind", "target", "label"), NULL);
			else obj(c, it, F("target", "label"), NULL);
			target(c, get(it, "target"), &rf->target);
			runs(c, get(it, "label"), &rf->label, 1);
			if (k == A2_REFERENCES) {
				rf->id = one(c, get(it, "id"), "id", pat_anchor);
				if (has(&c->refids, c->nrefids, rf->id)) fail(c, "E_DUPLICATE", "duplicate reference id");
				note(c, &c->refids, &c->nrefids, &c->crefids, rf->id);
				kv = get(it, "kind");
				if (kv->t != J_STR || !in_list(kv->s, F("normative", "informative", "further-reading"))) fail(c, "E_REFERENCE", "invalid reference kind");
				rf->kind = !strcmp(kv->s, "normative") ? REF_NORMATIVE : !strcmp(kv->s, "informative") ? REF_INFORMATIVE : REF_FURTHER;
			}
		}
		break;
	}
	case A2_TIMELINE_YEAR:
		obj(c, b, F("year", "runs"), NULL);
		o->year = integer(c, get(b, "year"), "year", -999999, 999999);
		runs(c, get(b, "runs"), &o->runs, 1);
		break;
	case A2_FOOTNOTE:
		obj(c, b, F("id", "runs"), NULL);
		o->anchor = one(c, get(b, "id"), "id", pat_anchor);
		if (has(&c->footnotes, c->nfootnotes, o->anchor)) fail(c, "E_DUPLICATE", "duplicate footnote id");
		note(c, &c->footnotes, &c->nfootnotes, &c->cfootnotes, o->anchor);
		runs(c, get(b, "runs"), &o->runs, 1);
		break;
	case A2_ATTRIBUTION:
		obj(c, b, F("licence", "authors", "source_url", "retrieved", "revision"), NULL);
		o->licence = str(c, get(b, "licence"), "licence", 0, 0);
		o->authors = unique_strings(c, get(b, "authors"), "authors", NULL, 1, &o->nauthors);
		o->source_url = url(c, get(b, "source_url"), "source_url");
		o->retrieved = date(c, get(b, "retrieved"), "retrieved", 0);
		o->revision = str(c, get(b, "revision"), "revision", 1, 0);
		break;
	default: break;
	}
}

const a2_media *a2_media_find(const a2_doc *d, const char *id)
{
	int i;
	for (i = 0; d->media && i < d->nmedia; i++)
		if (!strcmp(d->media[i].id, id)) return &d->media[i];
	return NULL;
}

/* ---------------------------------------------------------------- plain text (validate_v2.py block_text) */

int a2_buf_add(a2_buf *b, const char *s, size_t n)
{
	if (b->n + n + 1 > b->cap) {
		size_t cap = b->cap ? b->cap : 4096;
		char *ns;
		while (cap < b->n + n + 1) cap *= 2;
		if (!(ns = (char *)realloc(b->s, cap))) return -1;
		b->s = ns;
		b->cap = cap;
	}
	memcpy(b->s + b->n, s, n);
	b->n += n;
	b->s[b->n] = 0;
	return 0;
}

static int adds(a2_buf *b, const char *s) { return a2_buf_add(b, s, strlen(s)); }

int a2_runs_text(const a2_runs *r, a2_buf *out)
{
	int i, rc = 0;
	for (i = 0; i < r->n && !rc; i++) {
		const a2_run *x = &r->r[i];
		switch (x->t) {
		case R_TEXT: rc = adds(out, x->text); break;
		case R_SHY: break;
		case R_FOOTREF: rc = adds(out, "[") || adds(out, x->text) || adds(out, "]"); break;
		case R_KEYWORD: rc = adds(out, x->text); break;
		default: rc = a2_runs_text(&x->kids, out); break;
		}
	}
	return rc;
}

static int list_text(const a2_list *l, int depth, a2_buf *out)
{
	int i, j, d, rc = 0;
	for (i = 0; i < l->nitems && !rc; i++) {
		char num[24];
		if (i) rc |= adds(out, "\n");
		for (d = 0; d < depth; d++) rc |= adds(out, "  ");
		if (l->ordered) { snprintf(num, sizeof num, "%ld. ", l->start + i); rc |= adds(out, num); }
		else rc |= adds(out, "* ");
		rc |= a2_runs_text(&l->items[i].runs, out);
		for (j = 0; j < l->items[i].nkids; j++) {
			rc |= adds(out, "\n");
			rc |= list_text(&l->items[i].kids[j], depth + 1, out);
		}
	}
	return rc;
}

int a2_block_text(const a2_block *b, a2_buf *out)
{
	int i, rc = 0;
	switch (b->kind) {
	case A2_TITLE: case A2_PRE: case A2_CODE: return adds(out, b->text);
	case A2_LIST: return list_text(&b->list, 0, out);
	case A2_DEFINITIONS:
		for (i = 0; i < b->ndefs && !rc; i++) {
			if (i) rc |= adds(out, "\n");
			rc |= a2_runs_text(&b->defs[i].term, out) || adds(out, ": ") || a2_runs_text(&b->defs[i].def, out);
		}
		return rc;
	case A2_TABLE: {
		int r = -1;
		for (i = 0; i < b->ncells && !rc; i++) {
			if (b->cells[i].row != r) {
				/* rows with no cells of their own (covered by spans) still add their line break */
				while (r < b->cells[i].row) { if (r >= 0) rc |= adds(out, "\n"); r++; }
			} else rc |= adds(out, " | ");
			rc |= a2_runs_text(&b->cells[i].runs, out);
		}
		while (r < b->nrows - 1) { rc |= adds(out, "\n"); r++; }
		return rc;
	}
	case A2_FIGURE: return a2_runs_text(&b->runs, out) || adds(out, "\n") || adds(out, b->credit);
	case A2_MATHS: return adds(out, b->alt);
	case A2_FACTBOX: case A2_INFOBOX: case A2_QUICK_FACTS:
		rc = adds(out, b->title) || adds(out, "\n");
		for (i = 0; i < b->nfacts && !rc; i++) {
			if (i) rc |= adds(out, "\n");
			rc |= adds(out, b->facts[i].key) || adds(out, ": ") || a2_runs_text(&b->facts[i].runs, out);
		}
		return rc;
	case A2_REFERENCES: case A2_SEE_ALSO:
		for (i = 0; i < b->nrefs && !rc; i++) {
			if (i) rc |= adds(out, "\n");
			rc |= a2_runs_text(&b->refs[i].label, out);
		}
		return rc;
	case A2_ATTRIBUTION:
		for (i = 0; i < b->nauthors && !rc; i++) rc |= (i ? adds(out, "; ") : 0) || adds(out, b->authors[i]);
		return rc || adds(out, "\n") || adds(out, b->licence) || adds(out, "\n") || adds(out, b->source_url) ||
		       adds(out, "\nRetrieved ") || adds(out, b->retrieved);
	default: return a2_runs_text(&b->runs, out);
	}
}

char *a2_plain_text(const a2_doc *d, size_t *len)
{
	a2_buf b;
	int i;
	memset(&b, 0, sizeof b);
	if (a2_buf_add(&b, "", 0)) return NULL;
	for (i = 0; i < d->nb; i++) {
		if ((i && adds(&b, "\n\n")) || a2_block_text(&d->b[i], &b)) { free(b.s); return NULL; }
	}
	if (len) *len = b.n;
	return b.s;
}

/* ---------------------------------------------------------------- the file */

int a2_is_v2(const char *buf, size_t n) { return n >= 9 && !memcmp(buf, "e26art 2\n", 9); }

static long utf8_bad_at(const char *s, size_t n)
{
	size_t i = 0;
	while (i < n) {
		unsigned char ch = (unsigned char)s[i];
		uint32_t cp;
		int k;
		if (ch < 0x80) { i++; continue; }
		k = t8_decode(s + i, n - i, &cp);
		if (cp == 0xFFFD && !(k == 3 && (unsigned char)s[i] == 0xEF && (unsigned char)s[i + 1] == 0xBF && (unsigned char)s[i + 2] == 0xBD))
			return (long)i;
		i += (size_t)k;
	}
	return -1;
}

static int ascii_words(const char *s, size_t n, long *count)
{
	size_t i;
	int in = 0;
	*count = 0;
	for (i = 0; i < n; i++) {
		unsigned char ch = (unsigned char)s[i];
		if (ch >= 0x80) return 0;
		if (t8_is_word(ch)) { if (!in) (*count)++; in = 1; } else in = 0;
	}
	return 1;
}

int a2_parse(const char *buf, size_t n, a2_doc *d, char *code, size_t codelen, char *err, size_t errlen)
{
	ck *c;
	size_t i, nlines = 0, ls;
	int rc, bcap = 0, ln;
	const char *body = NULL;
	memset(d, 0, sizeof *d);
	if (!(c = (ck *)calloc(1, sizeof *c))) { snprintf(code, codelen, "E_MEMORY"); snprintf(err, errlen, "out of memory"); return -1; }
	c->d = d;
	c->line = 1;
	if ((rc = setjmp(c->jb)) != 0) {
		if (rc == 2) { snprintf(c->code, sizeof c->code, "E_MEMORY"); snprintf(c->msg, sizeof c->msg, "out of memory"); }
		snprintf(code, codelen, "%s", c->code);
		snprintf(err, errlen, "%s", c->msg);
		ar_free(&c->doc);
		ar_free(&c->rec);
		goto out;
	}
	if ((long)n > MAX_ARTICLE) fail(c, "E_LIMIT", "article exceeds 32 MiB");
	if (n >= 3 && !memcmp(buf, "\xef\xbb\xbf", 3)) fail(c, "E_BOM", "UTF-8 BOM forbidden");
	{
		long bad = utf8_bad_at(buf, n);
		if (bad >= 0) {
			for (i = 0; i < (size_t)bad; i++) if (buf[i] == '\n') c->line++;
			fail(c, "E_UTF8", "invalid UTF-8");
		}
	}
	{
		const char *cr = (const char *)memchr(buf, '\r', n);
		if (cr) { for (i = 0; i < (size_t)(cr - buf); i++) if (buf[i] == '\n') c->line++; fail(c, "E_NEWLINE", "only LF record endings allowed"); }
	}
	for (i = 0; i < n; i++) if (buf[i] == '\n') nlines++;
	if (!n || buf[n - 1] != '\n') { c->line = (int)nlines + 1; fail(c, "E_NEWLINE", "final LF required"); }
	if (!a2_is_v2(buf, n)) fail(c, "E_MAGIC", "expected e26art 2");
	if (nlines < 4) fail(c, "E_STRUCTURE", "meta, title and attribution required");
	ls = 9; /* after the magic line */
	for (ln = 2; ls < n; ln++) {
		const char *lp = buf + ls, *nl = (const char *)memchr(lp, '\n', n - ls), *tab, *t2;
		size_t len = (size_t)(nl - lp), klen;
		char kind[32];
		jv v;
		c->line = ln;
		if ((long)len > MAX_LINE) fail(c, "E_LIMIT", "record exceeds 1 MiB");
		tab = (const char *)memchr(lp, '\t', len);
		t2 = tab ? (const char *)memchr(tab + 1, '\t', len - (size_t)(tab + 1 - lp)) : NULL;
		if (!tab || t2) fail(c, "E_RECORD", "expected exactly one command separator TAB");
		klen = (size_t)(tab - lp);
		if (!klen || klen >= sizeof kind) fail(c, "E_RECORD", "invalid command");
		for (i = 0; i < klen; i++) if (!((lp[i] >= 'a' && lp[i] <= 'z') || lp[i] == '_')) fail(c, "E_RECORD", "invalid command");
		memcpy(kind, lp, klen);
		kind[klen] = 0;
		c->p = tab + 1;
		c->end = nl;
		c->depth = 0;
		jvalue(c, &v);
		jws(c);
		if (c->p != c->end) fail(c, "E_JSON", "extra data");
		if (ln == 2) {
			if (strcmp(kind, "meta")) fail(c, "E_STRUCTURE", "meta must follow magic");
			metadata(c, &v);
			body = nl + 1;
		} else {
			if (!strcmp(kind, "meta")) fail(c, "E_STRUCTURE", "only one meta permitted");
			if (d->nb == bcap) {
				int nc = bcap ? bcap * 2 : 64;
				a2_block *nb = (a2_block *)ar_alloc(&c->doc, (size_t)nc * sizeof(a2_block), &c->jb);
				if (d->nb) memcpy(nb, d->b, (size_t)d->nb * sizeof(a2_block));
				d->b = nb;
				bcap = nc;
			}
			block(c, kind, &v, &d->b[d->nb]);
			d->nb++;
			if (d->nb > MAX_BLOCKS) fail(c, "E_LIMIT", "too many blocks");
		}
		ar_free(&c->rec);
		ls = (size_t)(nl - buf) + 1;
	}
	c->line = 3;
	{
		int titles = 0, attrs = 0, subs = 0, bys = 0;
		for (i = 0; i < (size_t)d->nb; i++) {
			titles += d->b[i].kind == A2_TITLE;
			attrs += d->b[i].kind == A2_ATTRIBUTION;
			subs += d->b[i].kind == A2_SUBTITLE;
			bys += d->b[i].kind == A2_BYLINE;
		}
		if (d->b[0].kind != A2_TITLE || titles != 1 || strcmp(d->b[0].text, d->title)) fail(c, "E_TITLE", "exactly one first title must equal meta.title");
		if (d->b[d->nb - 1].kind != A2_ATTRIBUTION || attrs != 1) fail(c, "E_ATTRIBUTION", "exactly one final attribution required");
		{
			const a2_block *at = &d->b[d->nb - 1];
			int same = at->nauthors == d->nauthors;
			for (i = 0; same && i < (size_t)at->nauthors; i++) same = !strcmp(at->authors[i], d->authors[i]);
			c->line = (int)nlines;
			if (strcmp(at->licence, d->licence)) fail(c, "E_ATTRIBUTION", "attribution disagrees with provenance: licence");
			if (!same) fail(c, "E_ATTRIBUTION", "attribution disagrees with provenance: authors");
			if (strcmp(at->source_url, d->source_url)) fail(c, "E_ATTRIBUTION", "attribution disagrees with provenance: source_url");
			if (strcmp(at->retrieved, d->retrieved)) fail(c, "E_ATTRIBUTION", "attribution disagrees with provenance: retrieved");
			if (strcmp(at->revision, d->revision)) fail(c, "E_ATTRIBUTION", "attribution disagrees with provenance: revision");
		}
		c->line = 3;
		if (subs > 1) fail(c, "E_STRUCTURE", "at most one subtitle");
		if (bys > 1) fail(c, "E_STRUCTURE", "at most one byline");
	}
	for (i = 0; i < (size_t)c->nanchor_refs; i++)
		if (!has(&c->anchors, c->nanchors, c->anchor_refs[i].name)) { c->line = c->anchor_refs[i].line; fail(c, "E_ANCHOR", "unresolved local target %s", c->anchor_refs[i].name); }
	for (i = 0; i < (size_t)c->nfnrefs; i++)
		if (!has(&c->footnotes, c->nfootnotes, c->fnrefs[i].name)) { c->line = c->fnrefs[i].line; fail(c, "E_FOOTNOTE", "unresolved local target %s", c->fnrefs[i].name); }
	for (i = 0; i < (size_t)c->nmediarefs; i++)
		if (!a2_media_find(d, c->mediarefs[i].name)) { c->line = c->mediarefs[i].line; fail(c, "E_MEDIA", "unresolved local target %s", c->mediarefs[i].name); }
	{
		char h[65];
		sha256_hex(body, (size_t)(buf + n - body), h);
		c->line = 2;
		if (strcmp(h, d->content_sha256)) fail(c, "E_HASH", "content hash mismatch");
	}
	{
		size_t plen;
		long words;
		char *pt = a2_plain_text(d, &plen);
		if (!pt) longjmp(c->jb, 2);
		if (ascii_words(pt, plen, &words) && words != d->word_count) { free(pt); fail(c, "E_WORD_COUNT", "word count mismatch: %ld words in the body, %ld in meta", words, d->word_count); }
		free(pt);
	}
	d->arena = malloc(sizeof(arena));
	if (!d->arena) longjmp(c->jb, 2);
	*(arena *)d->arena = c->doc;
	rc = 0;
out:
	free(c->anchors); free(c->fnrefs); free(c->mediarefs); free(c->anchor_refs); free(c->footnotes); free(c->refids);
	free(c);
	if (rc) { memset(d, 0, sizeof *d); return -1; }
	return 0;
}

int a2_load(const char *path, a2_doc *d, char *code, size_t codelen, char *err, size_t errlen)
{
	FILE *f = fopen(path, "rb");
	char *buf;
	long n;
	int rc;
	memset(d, 0, sizeof *d);
	if (!f) { snprintf(code, codelen, "E_INPUT"); snprintf(err, errlen, "cannot open %s", path); return -1; }
	if (fseek(f, 0, SEEK_END) || (n = ftell(f)) < 0 || fseek(f, 0, SEEK_SET)) { fclose(f); snprintf(code, codelen, "E_INPUT"); snprintf(err, errlen, "cannot read %s", path); return -1; }
	if (n > MAX_ARTICLE) { fclose(f); snprintf(code, codelen, "E_LIMIT"); snprintf(err, errlen, "line 1: article exceeds 32 MiB"); return -1; }
	if (!(buf = (char *)malloc((size_t)n + 1))) { fclose(f); snprintf(code, codelen, "E_MEMORY"); snprintf(err, errlen, "out of memory"); return -1; }
	if (fread(buf, 1, (size_t)n, f) != (size_t)n) { fclose(f); free(buf); snprintf(code, codelen, "E_INPUT"); snprintf(err, errlen, "cannot read %s", path); return -1; }
	fclose(f);
	rc = a2_parse(buf, (size_t)n, d, code, codelen, err, errlen);
	free(buf);
	return rc;
}

void a2_free(a2_doc *d)
{
	if (d->arena) { ar_free((arena *)d->arena); free(d->arena); }
	memset(d, 0, sizeof *d);
}
