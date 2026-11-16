/* q_search.c: the e26-lib named query "search" (W1-SEARCH, plan F4's engine half; docs/rooms/SEARCH.md).
 *
 * docs/LIB-PROTOCOL.md "search": args {text, scope 'all'|'current'|'last', title_mode?, near?, id?, last?}, result
 * {bitmap, count, hits, id}. The Words language is query.c (docs/SEARCH-LANGUAGE.md) and the FTS5 run is search.c,
 * both Codex's T-0018 port (e26-search 3e82b3b). The bitmap is the Pinpointer's allowed set over article ordinals;
 * hits are the current article's matched body tokens as half-open UTF-8 byte ranges, for highlighting.
 *
 * Search errors keep their numeric code and exact message: the code goes back as a string of digits (helper.c sends
 * it as a JSON number, LIB-PROTOCOL "error.code is an integer for these conditions"). Everything else is one of the
 * protocol's E_ codes with its exact user message. The connections come from the Library service (q_search.h). */
#include "../app/registry.h"
#include "../app/json.h"
#include "../common/helper.h"
#include "q_search.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char *dupmsg(const char *s)
{
	size_t n = strlen(s) + 1;
	char *m = (char *)malloc(n);
	if (m) memcpy(m, s, n);
	return m;
}

static const char *bad(const char *code, const char *message, char **result)
{
	*result = dupmsg(message);
	return code;
}

#ifdef HAVE_SQLITE3
#include <sqlite3.h>
#include "query.h"
#include "search.h"

#define RESULT_MAX (8388608u - 1024u) /* the E26P body cap, less room for the envelope and the generation */

static sqlite3 *sdb, *cdb;
static unsigned ndocs;

void q_search_attach(sqlite3 *search_db, sqlite3 *catalogue_db, unsigned n)
{
	if (search_db && catalogue_db) { sdb = search_db; cdb = catalogue_db; ndocs = n; }
	else { sdb = cdb = NULL; ndocs = 0; }
}

#define ARGUMENT(r) bad("E_ARGUMENT", "The request is not valid.", r)
#define PACK(r)     bad("E_PACK", "The Library pack is unavailable.", r)

/* a search error: its code as digits (a static buffer: the helper answers one request at a time) and its message */
static const char *numeric(const e26q_err *err, char **result)
{
	static char code[16];
	snprintf(code, sizeof code, "%d", err->code);
	*result = dupmsg(err->message);
	return code;
}

/* ---- canonical base64 (RFC 4648 with padding): the protocol's Bitmap */
static const char B64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static size_t b64_len(size_t n) { return (n + 2) / 3 * 4; }

static void b64_put(char *out, const unsigned char *in, size_t n)
{
	size_t i, k = 0;
	for (i = 0; i < n; i += 3) {
		unsigned long w = (unsigned long)in[i] << 16;
		if (i + 1 < n) w |= (unsigned long)in[i + 1] << 8;
		if (i + 2 < n) w |= in[i + 2];
		out[k++] = B64[w >> 18 & 63];
		out[k++] = B64[w >> 12 & 63];
		out[k++] = i + 1 < n ? B64[w >> 6 & 63] : '=';
		out[k++] = i + 2 < n ? B64[w & 63] : '=';
	}
	out[k] = 0;
}

static int b64_val(int c)
{
	const char *p = c ? strchr(B64, c) : NULL;
	return p ? (int)(p - B64) : -1;
}

/* exactly n bytes from canonical base64 s: 0; -1 when s is anything else (length, alphabet, padding, spare bits);
 * -2 when out of memory. Canonical means encoding the bytes again gives s back. */
static int b64_get(const char *s, unsigned char *out, size_t n)
{
	size_t len = strlen(s), i, k = 0;
	char *again;
	int same;
	if (len != b64_len(n)) return -1;
	for (i = 0; i < len; i += 4) {
		int a = b64_val((unsigned char)s[i]), b = b64_val((unsigned char)s[i + 1]);
		int c = s[i + 2] == '=' ? 0 : b64_val((unsigned char)s[i + 2]);
		int d = s[i + 3] == '=' ? 0 : b64_val((unsigned char)s[i + 3]);
		unsigned long w;
		if (a < 0 || b < 0 || c < 0 || d < 0) return -1;
		w = (unsigned long)a << 18 | (unsigned long)b << 12 | (unsigned long)c << 6 | (unsigned long)d;
		if (k < n) out[k++] = (unsigned char)(w >> 16);
		if (k < n) out[k++] = (unsigned char)(w >> 8 & 255);
		if (k < n) out[k++] = (unsigned char)(w & 255);
	}
	if (k != n) return -1;
	if (!(again = (char *)malloc(len + 1))) return -2;
	b64_put(again, out, n);
	same = strcmp(again, s) == 0;
	free(again);
	return same ? 0 : -1;
}

/* ---- the result text */
typedef struct { char *p; size_t n, cap; int oom; } sbuf;

static void sb_put(sbuf *b, const char *s, size_t n)
{
	if (b->oom) return;
	if (b->n + n + 1 > b->cap) {
		size_t cap = b->cap ? b->cap : 256;
		char *np;
		while (b->n + n + 1 > cap) cap *= 2;
		if (!(np = (char *)realloc(b->p, cap))) { b->oom = 1; return; }
		b->p = np;
		b->cap = cap;
	}
	memcpy(b->p + b->n, s, n);
	b->n += n;
	b->p[b->n] = 0;
}

static void sb_s(sbuf *b, const char *s) { sb_put(b, s, strlen(s)); }

/* s as a JSON string: quote and backslash escaped, control characters as \u00XX, UTF-8 passed through */
static void sb_json(sbuf *b, const char *s)
{
	sb_s(b, "\"");
	for (; *s; s++) {
		unsigned char c = (unsigned char)*s;
		char e[8];
		if (c == '"' || c == '\\') { e[0] = '\\'; e[1] = (char)c; sb_put(b, e, 2); }
		else if (c < 0x20) { snprintf(e, sizeof e, "\\u%04x", c); sb_s(b, e); }
		else sb_put(b, (const char *)&c, 1);
	}
	sb_s(b, "\"");
}

/* the article's ordinal for id in catalogue.sqlite: 1 found, 0 not in this Library, -1 a database failure; *idout is
 * the catalogue's own spelling (malloc'd) */
static int ordinal_of(const char *id, long long *ord, char **idout)
{
	sqlite3_stmt *st = NULL;
	int rc, found = -1;
	if (sqlite3_prepare_v2(cdb, "SELECT ordinal, id FROM article WHERE id=?1", -1, &st, NULL) != SQLITE_OK) return -1;
	if (sqlite3_bind_text(st, 1, id, -1, SQLITE_STATIC) == SQLITE_OK) {
		rc = sqlite3_step(st);
		if (rc == SQLITE_ROW && sqlite3_column_type(st, 0) == SQLITE_INTEGER) {
			const unsigned char *t = sqlite3_column_text(st, 1);
			*ord = sqlite3_column_int64(st, 0);
			*idout = dupmsg(t ? (const char *)t : id);
			found = *idout ? 1 : -1;
		} else if (rc == SQLITE_DONE)
			found = 0;
	}
	sqlite3_finalize(st);
	return found;
}

const char *q_search_run(const struct jv *args, char **result)
{
	static const char *const known[] = { "text", "scope", "title_mode", "near", "id", "last", NULL };
	const jv *text, *scope, *tm, *nv, *id, *last;
	e26s_scope sc;
	int near = 8, k;
	size_t i, nbytes = (ndocs + 7u) / 8u;
	long long ord = -1;
	unsigned char *bits = NULL, none = 0;
	char *idout = NULL, *b64 = NULL, num[48];
	e26q_ast *ast = NULL;
	e26q_err err;
	e26s_result r;
	sbuf out = { NULL, 0, 0, 0 };

	*result = NULL;
	if (!args || args->type != J_OBJ) return ARGUMENT(result);
	for (i = 0; i < args->n; i++) {
		for (k = 0; known[k] && strcmp(known[k], args->keys[i]); k++)
			;
		if (!known[k]) return ARGUMENT(result);
	}
	text = json_get(args, "text");
	scope = json_get(args, "scope");
	tm = json_get(args, "title_mode");
	nv = json_get(args, "near");
	id = json_get(args, "id");
	last = json_get(args, "last");
	if (!text || text->type != J_STR || !scope || scope->type != J_STR || (tm && tm->type != J_BOOL)) return ARGUMENT(result);
	if (!strcmp(scope->str, "all")) sc = E26S_ALL;
	else if (!strcmp(scope->str, "current")) sc = E26S_CURRENT;
	else if (!strcmp(scope->str, "last")) sc = E26S_LAST;
	else return ARGUMENT(result);
	/* current needs id and forbids last; last needs last and forbids id; all forbids both */
	if ((sc == E26S_CURRENT) != (id != NULL) || (sc == E26S_LAST) != (last != NULL)) return ARGUMENT(result);
	if (id && (id->type != J_STR || !id->str[0] || strlen(id->str) > 4096)) return ARGUMENT(result);
	if (last && last->type != J_STR) return ARGUMENT(result);
	/* the NEAR setting: an integer 1-9999, else the parser's own 26212 (as the reference's parse(near=...)) */
	if (nv) near = nv->type == J_NUM && nv->num >= 1 && nv->num <= 9999 && nv->num == (double)(int)nv->num ? (int)nv->num : 0;
	if (!sdb) return PACK(result);

	if (sc == E26S_CURRENT) {
		int f = ordinal_of(id->str, &ord, &idout);
		if (f < 0) return PACK(result);
		if (f == 0) return bad("E_NOT_FOUND", "The requested item is not in this Library.", result);
		if (ord < 0 || ord >= (long long)ndocs) { free(idout); return PACK(result); }
	}
	if (sc == E26S_LAST) {
		int g;
		if (!(bits = (unsigned char *)malloc(nbytes ? nbytes : 1))) { err.code = 20100; snprintf(err.message, sizeof err.message, "The Find request failed due to an out-of-memory error."); return numeric(&err, result); }
		g = b64_get(last->str, bits, nbytes);
		/* the unused high bits of the final byte must be zero */
		if (g == 0 && ndocs % 8u && (bits[nbytes - 1] >> (ndocs % 8u))) g = -1;
		if (g) {
			free(bits);
			if (g == -2) { err.code = 20100; snprintf(err.message, sizeof err.message, "The Find request failed due to an out-of-memory error."); return numeric(&err, result); }
			return ARGUMENT(result);
		}
	}

	memset(&err, 0, sizeof err);
	if (e26q_parse(text->str, tm ? tm->b : 0, near, &ast, &err)) { free(bits); free(idout); return numeric(&err, result); }
	k = e26s_run(sdb, "docs", ast, sc, ndocs, bits ? bits : &none, sc == E26S_CURRENT ? ord + 1 : 0, &r, &err);
	e26q_free(ast);
	free(bits);
	if (k) { free(idout); return numeric(&err, result); }

	if (!(b64 = (char *)malloc(b64_len(nbytes) + 1))) {
		e26s_result_free(&r);
		free(idout);
		err.code = 20100;
		snprintf(err.message, sizeof err.message, "The Find request failed due to an out-of-memory error.");
		return numeric(&err, result);
	}
	b64_put(b64, r.bits ? r.bits : &none, r.bits ? nbytes : 0);
	sb_s(&out, "{\"bitmap\":\"");
	sb_s(&out, b64);
	snprintf(num, sizeof num, "\",\"count\":%u,\"hits\":[", r.count);
	sb_s(&out, num);
	for (i = 0; i < r.nhits && !out.oom && out.n < RESULT_MAX; i++) {
		snprintf(num, sizeof num, "%s[%u,%u]", i ? "," : "", r.hits[i][0], r.hits[i][1]);
		sb_s(&out, num);
	}
	sb_s(&out, "],\"id\":");
	if (idout) sb_json(&out, idout); else sb_s(&out, "null");
	sb_s(&out, "}");
	free(b64);
	free(idout);
	e26s_result_free(&r);
	if (out.oom) {
		free(out.p);
		err.code = 20100;
		snprintf(err.message, sizeof err.message, "The Find request failed due to an out-of-memory error.");
		return numeric(&err, result);
	}
	if (out.n > RESULT_MAX) { free(out.p); return bad("E_TOO_LARGE", "The requested content is too large.", result); }
	*result = out.p;
	return NULL;
}

#else /* built without SQLite: the query is registered and says so */

void q_search_attach(struct sqlite3 *search_db, struct sqlite3 *catalogue_db, unsigned n)
{
	(void)search_db;
	(void)catalogue_db;
	(void)n;
}

const char *q_search_run(const struct jv *args, char **result)
{
	(void)args;
	return bad("E_UNSUPPORTED", "This operation is not available in this build.", result);
}

#endif

void e26_q_search(void);
void e26_q_search(void)
{
	static const e26_helper_op op = { q_search_run };
	reg_add(REG_QUERY, "search", &op, "q_search", 0);
}
