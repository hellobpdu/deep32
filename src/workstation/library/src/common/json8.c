/* json8.c: a strict, UTF-8 preserving JSON reader (json8.h). */
#include "json8.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
	const unsigned char *p, *end;
	char *err;
	size_t errlen;
	int depth;
} jp;

static jv *fail(jp *j, const char *msg)
{
	if (!j->err[0]) snprintf(j->err, j->errlen, "%s at byte %ld", msg, (long)(j->end - j->p));
	return NULL;
}

static void ws(jp *j) { while (j->p < j->end && (*j->p == ' ' || *j->p == '\t' || *j->p == '\n' || *j->p == '\r')) j->p++; }

static jv *mk(int type)
{
	jv *v = (jv *)calloc(1, sizeof(jv));
	if (v) v->type = type;
	return v;
}

/* appends to an array or object, doubling its storage (json_free only frees the pointers) */
static int push(jv *v, size_t *cap, char *key, jv *item)
{
	if (v->n == *cap) {
		size_t nc = *cap ? *cap * 2 : 8;
		jv **ni = (jv **)realloc(v->items, nc * sizeof(jv *));
		if (!ni) return -1;
		v->items = ni;
		if (v->type == J_OBJ) {
			char **nk = (char **)realloc(v->keys, nc * sizeof(char *));
			if (!nk) return -1;
			v->keys = nk;
		}
		*cap = nc;
	}
	if (v->type == J_OBJ) v->keys[v->n] = key;
	v->items[v->n++] = item;
	return 0;
}

static int put(char **o, size_t *n, size_t *cap, const unsigned char *s, size_t k)
{
	if (*n + k + 1 > *cap) {
		size_t nc = *cap * 2 + k;
		char *no = (char *)realloc(*o, nc);
		if (!no) return -1;
		*o = no;
		*cap = nc;
	}
	memcpy(*o + *n, s, k);
	*n += k;
	return 0;
}

static size_t utf8(unsigned cp, unsigned char *u)
{
	if (cp < 0x80) { u[0] = (unsigned char)cp; return 1; }
	if (cp < 0x800) { u[0] = (unsigned char)(0xc0 | cp >> 6); u[1] = (unsigned char)(0x80 | (cp & 63)); return 2; }
	if (cp < 0x10000) { u[0] = (unsigned char)(0xe0 | cp >> 12); u[1] = (unsigned char)(0x80 | (cp >> 6 & 63)); u[2] = (unsigned char)(0x80 | (cp & 63)); return 3; }
	u[0] = (unsigned char)(0xf0 | cp >> 18); u[1] = (unsigned char)(0x80 | (cp >> 12 & 63));
	u[2] = (unsigned char)(0x80 | (cp >> 6 & 63)); u[3] = (unsigned char)(0x80 | (cp & 63));
	return 4;
}

static int hex4(jp *j, unsigned *v)
{
	int k;
	*v = 0;
	if (j->end - j->p < 4) return -1;
	for (k = 0; k < 4; k++) {
		unsigned char h = *j->p++;
		*v <<= 4;
		if (h >= '0' && h <= '9') *v |= (unsigned)(h - '0');
		else if (h >= 'a' && h <= 'f') *v |= (unsigned)(h - 'a' + 10);
		else if (h >= 'A' && h <= 'F') *v |= (unsigned)(h - 'A' + 10);
		else return -1;
	}
	return 0;
}

/* the length of a valid UTF-8 sequence at p (shortest form, no surrogates, at most U+10FFFF), 0 when invalid */
static size_t valid8(const unsigned char *p, const unsigned char *end)
{
	unsigned c = p[0], cp;
	size_t n, i;
	if (c < 0x80) return 1;
	if (c >= 0xc2 && c <= 0xdf) n = 2;
	else if (c >= 0xe0 && c <= 0xef) n = 3;
	else if (c >= 0xf0 && c <= 0xf4) n = 4;
	else return 0;
	if ((size_t)(end - p) < n) return 0;
	cp = c & (0x7f >> n);
	for (i = 1; i < n; i++) {
		if ((p[i] & 0xc0) != 0x80) return 0;
		cp = cp << 6 | (p[i] & 63);
	}
	if ((n == 3 && cp < 0x800) || (n == 4 && (cp < 0x10000 || cp > 0x10ffff)) || (cp >= 0xd800 && cp <= 0xdfff)) return 0;
	return n;
}

static char *str(jp *j)
{
	size_t cap = 32, n = 0;
	char *o = (char *)malloc(cap);
	if (!o) return NULL;
	j->p++; /* the opening quote */
	while (j->p < j->end && *j->p != '"') {
		unsigned c = *j->p;
		unsigned char u[4];
		size_t k;
		if (c < 0x20) { free(o); fail(j, "control character in a string"); return NULL; }
		if (c == '\\') {
			unsigned v;
			j->p++;
			if (j->p >= j->end) break;
			c = *j->p++;
			switch (c) {
			case '"': case '\\': case '/': u[0] = (unsigned char)c; k = 1; break;
			case 'b': u[0] = 8; k = 1; break;
			case 'f': u[0] = 12; k = 1; break;
			case 'n': u[0] = 10; k = 1; break;
			case 'r': u[0] = 13; k = 1; break;
			case 't': u[0] = 9; k = 1; break;
			case 'u':
				if (hex4(j, &v)) { free(o); fail(j, "bad \\u escape"); return NULL; }
				if (v >= 0xd800 && v <= 0xdbff) {            /* a surrogate pair */
					unsigned lo;
					if (j->end - j->p < 6 || j->p[0] != '\\' || j->p[1] != 'u') { free(o); fail(j, "a lone surrogate"); return NULL; }
					j->p += 2;
					if (hex4(j, &lo) || lo < 0xdc00 || lo > 0xdfff) { free(o); fail(j, "a lone surrogate"); return NULL; }
					v = 0x10000 + ((v - 0xd800) << 10) + (lo - 0xdc00);
				} else if (v >= 0xdc00 && v <= 0xdfff) { free(o); fail(j, "a lone surrogate"); return NULL; }
				if (!v) { free(o); fail(j, "a NUL in a string"); return NULL; }
				k = utf8(v, u);
				break;
			default: free(o); fail(j, "bad escape"); return NULL;
			}
			if (put(&o, &n, &cap, u, k)) { free(o); return NULL; }
			continue;
		}
		if (!(k = valid8(j->p, j->end))) { free(o); fail(j, "invalid UTF-8"); return NULL; }
		if (put(&o, &n, &cap, j->p, k)) { free(o); return NULL; }
		j->p += k;
	}
	if (j->p >= j->end) { free(o); fail(j, "unterminated string"); return NULL; }
	j->p++;
	o[n] = 0;
	return o;
}

/* a JSON number exactly as RFC 8259 writes it */
static jv *num(jp *j)
{
	const unsigned char *s = j->p;
	char buf[400];
	jv *v;
	if (j->p < j->end && *j->p == '-') j->p++;
	if (j->p < j->end && *j->p == '0') j->p++;
	else if (j->p < j->end && *j->p >= '1' && *j->p <= '9') while (j->p < j->end && *j->p >= '0' && *j->p <= '9') j->p++;
	else return fail(j, "unexpected character");
	if (j->p < j->end && *j->p == '.') {
		j->p++;
		if (j->p >= j->end || *j->p < '0' || *j->p > '9') return fail(j, "bad number");
		while (j->p < j->end && *j->p >= '0' && *j->p <= '9') j->p++;
	}
	if (j->p < j->end && (*j->p == 'e' || *j->p == 'E')) {
		j->p++;
		if (j->p < j->end && (*j->p == '+' || *j->p == '-')) j->p++;
		if (j->p >= j->end || *j->p < '0' || *j->p > '9') return fail(j, "bad number");
		while (j->p < j->end && *j->p >= '0' && *j->p <= '9') j->p++;
	}
	if ((size_t)(j->p - s) >= sizeof buf) return fail(j, "number too long");
	memcpy(buf, s, (size_t)(j->p - s));
	buf[j->p - s] = 0;
	if (!(v = mk(J_NUM))) return NULL;
	v->num = strtod(buf, NULL);
	if (!isfinite(v->num)) { free(v); return fail(j, "a number out of range"); }
	return v;
}

static jv *value(jp *j)
{
	jv *v;
	ws(j);
	if (j->p >= j->end) return fail(j, "unexpected end");
	if (*j->p == '{' || *j->p == '[') {
		int obj = *j->p == '{';
		size_t cap = 0, i;
		if (++j->depth > 64) return fail(j, "nesting deeper than 64");
		if (!(v = mk(obj ? J_OBJ : J_ARR))) return NULL;
		j->p++;
		ws(j);
		if (j->p < j->end && *j->p == (obj ? '}' : ']')) { j->p++; j->depth--; return v; }
		for (;;) {
			char *key = NULL;
			jv *item;
			ws(j);
			if (obj) {
				if (j->p >= j->end || *j->p != '"') { json_free(v); return fail(j, "expected a key"); }
				if (!(key = str(j))) { json_free(v); return NULL; }
				for (i = 0; i < v->n; i++)
					if (!strcmp(v->keys[i], key)) { free(key); json_free(v); return fail(j, "a duplicate key"); }
				ws(j);
				if (j->p >= j->end || *j->p != ':') { free(key); json_free(v); return fail(j, "expected ':'"); }
				j->p++;
			}
			item = value(j);
			if (!item || push(v, &cap, key, item)) { free(key); json_free(item); json_free(v); return NULL; }
			ws(j);
			if (j->p < j->end && *j->p == ',') { j->p++; continue; }
			if (j->p < j->end && *j->p == (obj ? '}' : ']')) { j->p++; break; }
			json_free(v);
			return fail(j, "expected ',' or a close");
		}
		j->depth--;
		return v;
	}
	if (*j->p == '"') {
		if (!(v = mk(J_STR))) return NULL;
		if (!(v->str = str(j))) { free(v); return NULL; }
		return v;
	}
	if (j->end - j->p >= 4 && !memcmp(j->p, "true", 4)) { if ((v = mk(J_BOOL))) v->b = 1; j->p += 4; return v; }
	if (j->end - j->p >= 5 && !memcmp(j->p, "false", 5)) { v = mk(J_BOOL); j->p += 5; return v; }
	if (j->end - j->p >= 4 && !memcmp(j->p, "null", 4)) { v = mk(J_NULL); j->p += 4; return v; }
	return num(j);
}

jv *json8_parse(const char *text, size_t len, char *err, size_t errlen)
{
	jp j;
	jv *v;
	j.p = (const unsigned char *)text;
	j.end = j.p + len;
	j.err = err;
	j.errlen = errlen;
	j.depth = 0;
	err[0] = 0;
	if (len >= 3 && !memcmp(text, "\xef\xbb\xbf", 3)) { fail(&j, "a byte order mark"); return NULL; }
	if (!(v = value(&j))) return NULL;
	ws(&j);
	if (j.p != j.end) { json_free(v); fail(&j, "trailing data"); return NULL; }
	return v;
}
