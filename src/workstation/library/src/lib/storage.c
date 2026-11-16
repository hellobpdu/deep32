/* storage.c: e26-lib's storage revision 2 adapter (h2-index docs/SIZE-PASS.md, libbuild/index/storage.py and
 * reader.py; change requests CR-2 and CR-F2). A revision 2 pack (STORAGE.json storage_revision 2, every database at
 * PRAGMA user_version 2) keeps its logical schema 1 behind views that call three SQL functions, registered here on
 * every connection of the pack:
 *   e26_unzstd(x)          a zstd frame (BLOB) to its UTF-8 text; TEXT and NULL pass through unchanged
 *   e26_cell(blob,row,col) the zstd-compressed JSON array of arrays, cell [row][col] (strings as text, numbers as
 *                          numbers, null as NULL, arrays and objects as their JSON text)
 *   e26_body(reader_path)  the article's canonical FTS body: ARTICLE-FORMAT-2's plain text projection of the reader
 *                          entry (reader.py plain_text), read through the pack's own reader store
 * Decompression is bounded (64 MiB a frame); nothing is ever written. */
#include "lib.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(E26_LIB_SQLITE)
#if defined(E26_LIB_ZSTD)
#include <zstd.h>
#endif

#define MAX_FRAME (64u * 1024u * 1024u)
#define NCELL 8

/* ---- e26_unzstd (without libzstd a frame is unreadable: a revision 2 pack then answers E_PACK where it needs one) */
#if !defined(E26_LIB_ZSTD)
static char *unzstd(const void *src, size_t n, size_t *outlen) { (void)src; (void)n; (void)outlen; return NULL; }
#else
static char *unzstd(const void *src, size_t n, size_t *outlen)
{
	unsigned long long size = ZSTD_getFrameContentSize(src, n);
	char *out;
	if (size == ZSTD_CONTENTSIZE_ERROR) return NULL;
	if (size != ZSTD_CONTENTSIZE_UNKNOWN) {
		size_t got;
		if (size > MAX_FRAME || !(out = (char *)malloc((size_t)size + 1))) return NULL;
		got = ZSTD_decompress(out, (size_t)size, src, n);
		if (ZSTD_isError(got) || got != size) { free(out); return NULL; }
		out[got] = 0;
		*outlen = got;
		return out;
	}
	{
		/* no content size in the frame: stream it, still bounded */
		ZSTD_DStream *ds = ZSTD_createDStream();
		size_t cap = 1u << 16, len = 0, r = 1;
		ZSTD_inBuffer in = { src, n, 0 };
		if (!ds || !(out = (char *)malloc(cap + 1))) { ZSTD_freeDStream(ds); return NULL; }
		ZSTD_initDStream(ds);
		while (r) {
			ZSTD_outBuffer o;
			if (len == cap) {
				char *nb;
				if (cap >= MAX_FRAME || !(nb = (char *)realloc(out, cap * 2 + 1))) { free(out); ZSTD_freeDStream(ds); return NULL; }
				out = nb;
				cap *= 2;
			}
			o.dst = out + len; o.size = cap - len; o.pos = 0;
			r = ZSTD_decompressStream(ds, &o, &in);
			if (ZSTD_isError(r) || (!o.pos && in.pos == in.size && r)) { free(out); ZSTD_freeDStream(ds); return NULL; }
			len += o.pos;
		}
		ZSTD_freeDStream(ds);
		out[len] = 0;
		*outlen = len;
		return out;
	}
}
#endif

static void f_unzstd(sqlite3_context *ctx, int argc, sqlite3_value **argv)
{
	char *t;
	size_t n;
	(void)argc;
	switch (sqlite3_value_type(argv[0])) {
	case SQLITE_NULL: sqlite3_result_null(ctx); return;
	case SQLITE_BLOB: break;
	default: sqlite3_result_value(ctx, argv[0]); return;
	}
	if (!(t = unzstd(sqlite3_value_blob(argv[0]), (size_t)sqlite3_value_bytes(argv[0]), &n))) {
		sqlite3_result_error(ctx, "e26_unzstd: not a zstd frame", -1);
		return;
	}
	sqlite3_result_text(ctx, t, (int)n, free);
}

/* ---- e26_cell: a few decoded payloads per connection (the views read several cells of one payload in a row) */
typedef struct { unsigned char *key; size_t klen; jv *v; unsigned long use; } cell_slot;
typedef struct { cell_slot s[NCELL]; unsigned long clock; } cell_cache;

static const jv *cell_payload(cell_cache *c, sqlite3_value *arg)
{
	const unsigned char *blob = (const unsigned char *)sqlite3_value_blob(arg);
	size_t n = (size_t)sqlite3_value_bytes(arg), len = 0;
	int i, victim = 0;
	char *text;
	const char *src;
	jv *v;
	for (i = 0; i < NCELL; i++)
		if (c->s[i].v && c->s[i].klen == n && !memcmp(c->s[i].key, blob, n)) { c->s[i].use = ++c->clock; return c->s[i].v; }
	if (sqlite3_value_type(arg) == SQLITE_BLOB) {
		if (!(text = unzstd(blob, n, &len))) return NULL;
		src = text;
	} else {
		text = NULL;
		src = (const char *)sqlite3_value_text(arg);
		len = src ? strlen(src) : 0;
		if (!src) return NULL;
	}
	v = e26j_parse(src, len, NULL, 0);
	free(text);
	if (!v) return NULL;
	for (i = 1; i < NCELL; i++) if (!c->s[i].v || c->s[i].use < c->s[victim].use) victim = i;
	json_free(c->s[victim].v);
	free(c->s[victim].key);
	c->s[victim].v = v;
	c->s[victim].key = (unsigned char *)malloc(n ? n : 1);
	if (!c->s[victim].key) { c->s[victim].v = NULL; c->s[victim].klen = 0; json_free(v); return NULL; }
	memcpy(c->s[victim].key, blob, n);
	c->s[victim].klen = n;
	c->s[victim].use = ++c->clock;
	return v;
}

static void jv_text(e26j_buf *b, const jv *v);

static void f_cell(sqlite3_context *ctx, int argc, sqlite3_value **argv)
{
	cell_cache *c = (cell_cache *)sqlite3_user_data(ctx);
	const jv *p, *row, *cell;
	sqlite3_int64 r = sqlite3_value_int64(argv[1]), k = sqlite3_value_int64(argv[2]);
	(void)argc;
	if (sqlite3_value_type(argv[0]) == SQLITE_NULL) { sqlite3_result_null(ctx); return; }
	if (!(p = cell_payload(c, argv[0])) || p->type != J_ARR || r < 0 || (size_t)r >= p->n || !(row = p->items[r]) ||
	    row->type != J_ARR || k < 0 || (size_t)k >= row->n) {
		sqlite3_result_error(ctx, "e26_cell: no such cell", -1);
		return;
	}
	cell = row->items[k];
	switch (cell->type) {
	case J_NULL: sqlite3_result_null(ctx); break;
	case J_STR: sqlite3_result_text(ctx, cell->str, -1, SQLITE_TRANSIENT); break;
	case J_BOOL: sqlite3_result_int(ctx, cell->b); break;
	case J_NUM:
		if (cell->num == (double)(sqlite3_int64)cell->num) sqlite3_result_int64(ctx, (sqlite3_int64)cell->num);
		else sqlite3_result_double(ctx, cell->num);
		break;
	default: {
		e26j_buf b;
		char *t;
		e26j_init(&b);
		jv_text(&b, cell);
		if (!(t = e26j_take(&b))) { sqlite3_result_error(ctx, "e26_cell: bad JSON", -1); return; }
		sqlite3_result_text(ctx, t, -1, free);
	}
	}
}

static void cell_cache_free(void *p)
{
	cell_cache *c = (cell_cache *)p;
	int i;
	for (i = 0; i < NCELL; i++) { json_free(c->s[i].v); free(c->s[i].key); }
	free(c);
}

/* compact JSON text of a value (separators ',' and ':', as storage.py's json.dumps) */
static void jv_text(e26j_buf *b, const jv *v)
{
	size_t i;
	char t[40];
	switch (v->type) {
	case J_NULL: e26j_lit(b, "null"); break;
	case J_BOOL: e26j_lit(b, v->b ? "true" : "false"); break;
	case J_NUM:
		if (v->num == (double)(long long)v->num) snprintf(t, sizeof t, "%lld", (long long)v->num);
		else snprintf(t, sizeof t, "%.17g", v->num);
		e26j_lit(b, t);
		break;
	case J_STR: e26j_cstr(b, v->str); break;
	case J_ARR:
		e26j_lit(b, "[");
		for (i = 0; i < v->n; i++) { if (i) e26j_lit(b, ","); jv_text(b, v->items[i]); }
		e26j_lit(b, "]");
		break;
	default:
		e26j_lit(b, "{");
		for (i = 0; i < v->n; i++) { if (i) e26j_lit(b, ","); e26j_cstr(b, v->keys[i]); e26j_lit(b, ":"); jv_text(b, v->items[i]); }
		e26j_lit(b, "}");
	}
}

/* ---- the canonical text projection (reader.py: inline, list_text, block_text, plain_text) */
static void put(e26j_buf *b, const char *s) { if (s) e26j_raw(b, s, strlen(s)); }
static const jv *arr(const jv *o, const char *k) { const jv *v = json_get(o, k); return v && v->type == J_ARR ? v : NULL; }

static void inl(e26j_buf *b, const jv *runs)
{
	size_t i;
	if (!runs || runs->type != J_ARR) return;
	for (i = 0; i < runs->n; i++) {
		const jv *r = runs->items[i];
		const char *t;
		if (r->type == J_STR) { put(b, r->str); continue; }
		if (r->type != J_OBJ || !(t = json_str(r, "t"))) continue;
		if (!strcmp(t, "shy")) continue;
		if (!strcmp(t, "footnote_ref")) { put(b, "["); put(b, json_str(r, "id")); put(b, "]"); }
		else if (!strcmp(t, "keyword")) put(b, json_str(r, "text"));
		else if (!strcmp(t, "link")) inl(b, json_get(r, "label"));
		else if (!strcmp(t, "glossary")) put(b, json_str(r, "term"));
		else inl(b, json_get(r, "runs"));
	}
}

static void num(e26j_buf *b, const jv *v)
{
	char t[40];
	if (!v || v->type != J_NUM) return;
	snprintf(t, sizeof t, "%lld", (long long)v->num);
	put(b, t);
}

static void list_text(e26j_buf *b, const jv *blk, int depth)
{
	const jv *items = arr(blk, "items"), *st = json_get(blk, "start"), *ord = json_get(blk, "ordered");
	long long n = st && st->type == J_NUM ? (long long)st->num : 1;
	size_t i, k;
	int ordered = ord && ord->type == J_BOOL && ord->b;
	if (!items) return;
	for (i = 0; i < items->n; i++, n++) {
		const jv *it = items->items[i], *ch = arr(it, "children");
		int d;
		if (i) put(b, "\n");
		for (d = 0; d < depth; d++) put(b, "  ");
		if (ordered) { char t[32]; snprintf(t, sizeof t, "%lld. ", n); put(b, t); } else put(b, "* ");
		inl(b, json_get(it, "runs"));
		if (ch) for (k = 0; k < ch->n; k++) { put(b, "\n"); list_text(b, ch->items[k], depth + 1); }
	}
}

static void block_text(e26j_buf *b, const jv *blk)
{
	const char *k = json_str(blk, "type");
	const jv *a;
	size_t i, j;
	if (!k) return;
	if (!strcmp(k, "title") || !strcmp(k, "pre") || !strcmp(k, "code")) put(b, json_str(blk, "text"));
	else if (!strcmp(k, "list")) list_text(b, blk, 0);
	else if (!strcmp(k, "definitions")) {
		if ((a = arr(blk, "items"))) for (i = 0; i < a->n; i++) {
			if (i) put(b, "\n");
			inl(b, json_get(a->items[i], "term")); put(b, ": "); inl(b, json_get(a->items[i], "definition"));
		}
	} else if (!strcmp(k, "table")) {
		if ((a = arr(blk, "rows"))) for (i = 0; i < a->n; i++) {
			const jv *row = a->items[i];
			if (i) put(b, "\n");
			if (row->type == J_ARR) for (j = 0; j < row->n; j++) { if (j) put(b, " | "); inl(b, json_get(row->items[j], "runs")); }
		}
	} else if (!strcmp(k, "factbox") || !strcmp(k, "infobox") || !strcmp(k, "quick_facts")) {
		put(b, json_str(blk, "title")); put(b, "\n");
		if ((a = arr(blk, "rows"))) for (i = 0; i < a->n; i++) {
			if (i) put(b, "\n");
			put(b, json_str(a->items[i], "key")); put(b, ": "); inl(b, json_get(a->items[i], "runs"));
		}
	} else if (!strcmp(k, "references") || !strcmp(k, "see_also")) {
		if ((a = arr(blk, "items"))) for (i = 0; i < a->n; i++) { if (i) put(b, "\n"); inl(b, json_get(a->items[i], "label")); }
	} else if (!strcmp(k, "figure")) {
		inl(b, json_get(blk, "caption")); put(b, "\n"); put(b, json_str(blk, "credit"));
	} else if (!strcmp(k, "maths")) put(b, json_str(blk, "alt"));
	else if (!strcmp(k, "attribution")) {
		if ((a = arr(blk, "authors"))) for (i = 0; i < a->n; i++) { if (i) put(b, "; "); if (a->items[i]->type == J_STR) put(b, a->items[i]->str); }
		put(b, "\n"); put(b, json_str(blk, "licence"));
		put(b, "\n"); put(b, json_str(blk, "source_url"));
		put(b, "\nRetrieved "); put(b, json_str(blk, "retrieved"));
	} else if (!strcmp(k, "timeline_year")) {
		num(b, json_get(blk, "year")); put(b, " "); inl(b, json_get(blk, "runs"));
	} else inl(b, json_get(blk, "runs"));
}

char *lib_body_text(const lib_article *a, size_t *len)
{
	e26j_buf b;
	size_t i;
	char *t;
	e26j_init(&b);
	for (i = 0; i < a->nblocks; i++) {
		jv *v = e26j_parse(a->block[i], a->blen[i], NULL, 0);
		if (!v) { e26j_free(&b); return NULL; }
		if (i) put(&b, "\n\n");
		block_text(&b, v);
		json_free(v);
	}
	*len = b.n;
	t = e26j_take(&b);
	if (!t && !*len) t = strdup("");
	return t;
}

/* ---- e26_body */
typedef struct { lib_body_fn fn; void *ud; } body_ctx;

static void f_body(sqlite3_context *ctx, int argc, sqlite3_value **argv)
{
	body_ctx *c = (body_ctx *)sqlite3_user_data(ctx);
	const char *path = (const char *)sqlite3_value_text(argv[0]);
	size_t n = 0;
	char *t;
	(void)argc;
	if (!path) { sqlite3_result_null(ctx); return; }
	if (!(t = c->fn(c->ud, path, &n))) { sqlite3_result_error(ctx, "e26_body: reader entry unreadable", -1); return; }
	sqlite3_result_text(ctx, t, (int)n, free);
}

int lib_storage_register(sqlite3 *db, lib_body_fn body, void *ud)
{
	cell_cache *c = (cell_cache *)calloc(1, sizeof *c);
	body_ctx *bc = (body_ctx *)malloc(sizeof *bc);
	int rc;
	if (!c || !bc) { free(c); free(bc); return -1; }
	bc->fn = body;
	bc->ud = ud;
	rc = sqlite3_create_function_v2(db, "e26_unzstd", 1, SQLITE_UTF8 | SQLITE_DETERMINISTIC, NULL, f_unzstd, NULL, NULL, NULL);
	if (rc == SQLITE_OK)
		rc = sqlite3_create_function_v2(db, "e26_cell", 3, SQLITE_UTF8 | SQLITE_DETERMINISTIC, c, f_cell, NULL, NULL, cell_cache_free);
	else cell_cache_free(c);
	if (rc == SQLITE_OK)
		rc = sqlite3_create_function_v2(db, "e26_body", 1, SQLITE_UTF8 | SQLITE_DETERMINISTIC, bc, f_body, NULL, NULL, free);
	else free(bc);
	return rc == SQLITE_OK ? 0 : -1;
}
#endif
