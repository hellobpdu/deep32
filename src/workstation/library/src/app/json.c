/* json.c: small strict JSON reader for the taxonomy file. */
#include "json.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
	const char *p, *end;
	char *err;
	size_t errlen;
	int depth;
} jp;

static void ws(jp *j) { while (j->p < j->end && (*j->p == ' ' || *j->p == '\t' || *j->p == '\n' || *j->p == '\r')) j->p++; }

static jv *fail(jp *j, const char *msg)
{
	if (!j->err[0]) snprintf(j->err, j->errlen, "%s at byte %ld", msg, (long)(j->end - j->p));
	return NULL;
}

static jv *mk(int type)
{
	jv *v = (jv *)calloc(1, sizeof(jv));
	if (v) v->type = type;
	return v;
}

static int push(jv *v, char *key, jv *item)
{
	jv **ni = (jv **)realloc(v->items, (v->n + 1) * sizeof(jv *));
	if (!ni) return -1;
	v->items = ni;
	if (v->type == J_OBJ) {
		char **nk = (char **)realloc(v->keys, (v->n + 1) * sizeof(char *));
		if (!nk) return -1;
		v->keys = nk;
		v->keys[v->n] = key;
	}
	v->items[v->n++] = item;
	return 0;
}

static char *str(jp *j)
{
	size_t cap = 32, n = 0;
	char *o = (char *)malloc(cap);
	if (!o) return NULL;
	j->p++; /* opening quote */
	while (j->p < j->end && *j->p != '"') {
		unsigned c = (unsigned char)*j->p++;
		if (c < 0x20) { free(o); fail(j, "control character in string"); return NULL; }
		if (c == '\\') {
			if (j->p >= j->end) break;
			c = (unsigned char)*j->p++;
			switch (c) {
			case '"': case '\\': case '/': break;
			case 'b': c = 8; break; case 'f': c = 12; break; case 'n': c = 10; break;
			case 'r': c = 13; break; case 't': c = 9; break;
			case 'u': {
				unsigned v = 0, k;
				if (j->end - j->p < 4) { free(o); fail(j, "short \\u escape"); return NULL; }
				for (k = 0; k < 4; k++) {
					char h = *j->p++;
					v <<= 4;
					if (h >= '0' && h <= '9') v |= (unsigned)(h - '0');
					else if (h >= 'a' && h <= 'f') v |= (unsigned)(h - 'a' + 10);
					else if (h >= 'A' && h <= 'F') v |= (unsigned)(h - 'A' + 10);
					else { free(o); fail(j, "bad \\u escape"); return NULL; }
				}
				c = v < 256 ? v : '?';
				break;
			}
			default: free(o); fail(j, "bad escape"); return NULL;
			}
		} else if (c >= 0x80) {
			/* UTF-8 to Latin-1 where possible */
			unsigned cp = 0, more = 0;
			if ((c & 0xE0) == 0xC0) { cp = c & 0x1F; more = 1; }
			else if ((c & 0xF0) == 0xE0) { cp = c & 0x0F; more = 2; }
			else if ((c & 0xF8) == 0xF0) { cp = c & 0x07; more = 3; }
			else { free(o); fail(j, "bad UTF-8"); return NULL; }
			while (more--) {
				if (j->p >= j->end || ((unsigned char)*j->p & 0xC0) != 0x80) { free(o); fail(j, "bad UTF-8"); return NULL; }
				cp = (cp << 6) | ((unsigned char)*j->p++ & 0x3F);
			}
			c = cp < 256 ? cp : '?';
		}
		if (n + 2 > cap) {
			char *no = (char *)realloc(o, cap *= 2);
			if (!no) { free(o); return NULL; }
			o = no;
		}
		o[n++] = (char)c;
	}
	if (j->p >= j->end) { free(o); fail(j, "unterminated string"); return NULL; }
	j->p++;
	o[n] = 0;
	return o;
}

static jv *value(jp *j)
{
	jv *v;
	ws(j);
	if (j->p >= j->end) return fail(j, "unexpected end");
	if (++j->depth > 32) return fail(j, "nesting too deep");
	if (*j->p == '{' || *j->p == '[') {
		int obj = *j->p == '{';
		v = mk(obj ? J_OBJ : J_ARR);
		if (!v) return NULL;
		j->p++;
		ws(j);
		if (j->p < j->end && *j->p == (obj ? '}' : ']')) { j->p++; j->depth--; return v; }
		for (;;) {
			char *key = NULL;
			jv *item;
			ws(j);
			if (obj) {
				if (j->p >= j->end || *j->p != '"') { json_free(v); return fail(j, "expected key"); }
				key = str(j);
				if (!key) { json_free(v); return NULL; }
				ws(j);
				if (j->p >= j->end || *j->p != ':') { free(key); json_free(v); return fail(j, "expected ':'"); }
				j->p++;
			}
			item = value(j);
			if (!item || push(v, key, item)) { free(key); json_free(item); json_free(v); return NULL; }
			ws(j);
			if (j->p < j->end && *j->p == ',') { j->p++; continue; }
			if (j->p < j->end && *j->p == (obj ? '}' : ']')) { j->p++; break; }
			json_free(v);
			return fail(j, "expected ',' or close");
		}
	} else if (*j->p == '"') {
		v = mk(J_STR);
		if (!v) return NULL;
		v->str = str(j);
		if (!v->str) { free(v); return NULL; }
	} else if (!strncmp(j->p, "true", 4) || !strncmp(j->p, "false", 5)) {
		v = mk(J_BOOL);
		if (!v) return NULL;
		v->b = *j->p == 't';
		j->p += v->b ? 4 : 5;
	} else if (!strncmp(j->p, "null", 4)) {
		v = mk(J_NULL);
		j->p += 4;
	} else {
		char *e;
		char buf[64];
		size_t k = 0;
		while (j->p + k < j->end && k < sizeof buf - 1 && strchr("+-0123456789.eE", j->p[k])) { buf[k] = j->p[k]; k++; }
		buf[k] = 0;
		if (!k) return fail(j, "unexpected character");
		v = mk(J_NUM);
		if (!v) return NULL;
		v->num = strtod(buf, &e);
		if (*e) { free(v); return fail(j, "bad number"); }
		j->p += k;
	}
	j->depth--;
	return v;
}

jv *json_parse(const char *text, size_t len, char *err, size_t errlen)
{
	jp j;
	jv *v;
	j.p = text; j.end = text + len; j.err = err; j.errlen = errlen; j.depth = 0;
	err[0] = 0;
	v = value(&j);
	if (!v) return NULL;
	ws(&j);
	if (j.p != j.end) { json_free(v); fail(&j, "trailing data"); return NULL; }
	return v;
}

void json_free(jv *v)
{
	size_t i;
	if (!v) return;
	for (i = 0; i < v->n; i++) {
		json_free(v->items[i]);
		if (v->keys) free(v->keys[i]);
	}
	free(v->items);
	free(v->keys);
	free(v->str);
	free(v);
}

const jv *json_get(const jv *obj, const char *key)
{
	size_t i;
	if (!obj || obj->type != J_OBJ) return NULL;
	for (i = 0; i < obj->n; i++)
		if (!strcmp(obj->keys[i], key)) return obj->items[i];
	return NULL;
}

const char *json_str(const jv *obj, const char *key)
{
	const jv *v = json_get(obj, key);
	return v && v->type == J_STR ? v->str : NULL;
}
