/* q_finder.c: the FINDER-only composition, without changing protocol 1's frozen queries.
 * Registered facets and search supply canonical article bitmaps. The wheel in memory (wheel_mem) counts and pages
 * the filtered title list; for a non-canonical wheel SQLite counts and pages the indexed title
 * range; only the requested (at most 64) title records become JSON. start/next are filtered row offsets.
 * Optional seek=true selects the nearest prefix on the complete filtered wheel; the normal prefix query narrows.
 * The aggregate keeps only ceil(N/8) bytes, so aliases count as wheel rows but occur once in the last-list bitmap.
 * DATA currently opens one pack. Pack/marking are copied from provenance when present; no cross-pack merge or
 * compact H2 storage adapter is claimed here. */
#include "lib.h"
#include "q_search.h"
#include "../app/registry.h"
#include "../common/helper.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

void e26_q_finder(void);

#if defined(E26_LIB_SQLITE)
#include "query.h"
#include "search.h"

#define FINDER_WHERE " FROM title t WHERE t.sort_key>=?1 AND t.sort_key<?2 " \
	"AND (?3 OR t.catalogue_only=0) AND e26_finder_allowed(t.article_ordinal,?4)"
static const char *const COUNT = "SELECT count(*),e26_finder_bitmap(t.article_ordinal)" FINDER_WHERE;
static const char *const RANK = "SELECT count(*) FROM title t WHERE t.sort_key<?1 "
	"AND (?2 OR t.catalogue_only=0) AND e26_finder_allowed(t.article_ordinal,?3)";
static const char *const PAGE =
	"SELECT t.wheel_ordinal,t.title,t.id,t.article_ordinal,t.alias,t.catalogue_only,"
	"CASE WHEN t.catalogue_only THEN 'catalogue' ELSE a.class END,e26_unzstd(a.meta_json) "
	"FROM title t LEFT JOIN article a ON a.ordinal=t.article_ordinal "
	"WHERE t.sort_key>=?1 AND t.sort_key<?2 AND (?3 OR t.catalogue_only=0) "
	"AND e26_finder_allowed(t.article_ordinal,?4) ORDER BY t.sort_key,t.wheel_ordinal LIMIT ?5 OFFSET ?6";
static const char *const SNIPPET =
	"SELECT snippet(docs,1,'','',' ... ',24) FROM docs WHERE rowid=?1 AND docs MATCH ?2";

/* NULL bitmap means unrestricted, including catalogue-only titles. An active filter never grants those titles
 * a made-up article ordinal. The bitmap is bound, rather than kept in a request-lifetime global pointer. */
static void allowed(sqlite3_context *ctx, int argc, sqlite3_value **v)
{
	const unsigned char *bits;
	sqlite3_int64 ord;
	(void)argc;
	if (sqlite3_value_type(v[1]) == SQLITE_NULL) { sqlite3_result_int(ctx, 1); return; }
	if (sqlite3_value_type(v[0]) == SQLITE_NULL) { sqlite3_result_int(ctx, 0); return; }
	ord = sqlite3_value_int64(v[0]);
	bits = (const unsigned char *)sqlite3_value_blob(v[1]);
	if (!bits || ord < 0 || ord >= (sqlite3_int64)lib_ndocs() ||
	    sqlite3_value_bytes(v[1]) != (int)lib_bitmap_bytes()) { sqlite3_result_error(ctx, "invalid Finder bitmap", -1); return; }
	sqlite3_result_int(ctx, lib_bit(bits, (uint32_t)ord));
}

static void bitmap_step(sqlite3_context *ctx, int argc, sqlite3_value **v)
{
	unsigned char *bits;
	sqlite3_int64 ord;
	(void)argc;
	if (sqlite3_value_type(v[0]) == SQLITE_NULL) return;
	ord = sqlite3_value_int64(v[0]);
	if (ord < 0 || ord >= (sqlite3_int64)lib_ndocs()) { sqlite3_result_error(ctx, "invalid Finder ordinal", -1); return; }
	bits = (unsigned char *)sqlite3_aggregate_context(ctx, (int)lib_bitmap_bytes() + 1);
	if (!bits) { sqlite3_result_error_nomem(ctx); return; }
	lib_setbit(bits, (uint32_t)ord);
}

static void bitmap_final(sqlite3_context *ctx)
{
	const void *bits = sqlite3_aggregate_context(ctx, 0);
	if (bits) sqlite3_result_blob(ctx, bits, (int)lib_bitmap_bytes(), SQLITE_TRANSIENT);
	else sqlite3_result_zeroblob(ctx, (int)lib_bitmap_bytes());
}

static const char *compose(const char *name, const jv *args, unsigned char **bits, char **result)
{
	const e26_helper_op *op = (const e26_helper_op *)reg_impl(REG_QUERY, reg_find(REG_QUERY, name));
	char *out = NULL;
	const char *code;
	jv *r;
	if (!op || !op->run) return lib_err(result, "E_UNSUPPORTED", NULL);
	code = op->run(args, &out);
	if (code) { *result = out; return code; }
	r = out ? e26j_parse(out, strlen(out), NULL, 0) : NULL;
	free(out);
	*bits = r ? lib_bitmap_arg(r, "bitmap") : NULL;
	json_free(r);
	return *bits ? NULL : lib_err(result, "E_PACK", NULL);
}

static int bind_range(sqlite3_stmt *st, const char *lo, const char *hi, int catalogue, const unsigned char *bits)
{
	return sqlite3_bind_text(st, 1, lo, -1, SQLITE_STATIC) != SQLITE_OK ||
	    sqlite3_bind_text(st, 2, hi, -1, SQLITE_STATIC) != SQLITE_OK ||
	    sqlite3_bind_int(st, 3, catalogue) != SQLITE_OK ||
	    (bits ? sqlite3_bind_blob(st, 4, bits, (int)lib_bitmap_bytes(), SQLITE_STATIC) : sqlite3_bind_null(st, 4)) != SQLITE_OK;
}

/* Bound on UTF-8 bytes at a character boundary. Snippets are plain text, never markup. The body joins its blocks
 * with line breaks, which a one-line snippet would lose (the words ran together): every control character
 * becomes a space and runs of spaces become one. */
static void short_text(e26j_buf *b, const char *text)
{
	char out[324];
	size_t i, n = 0;
	for (i = 0; text[i] && n < 320; i++) {
		unsigned char c = (unsigned char)text[i];
		if (c < 0x20 || c == 0x7F) c = ' ';
		if (c == ' ' && (n == 0 || out[n - 1] == ' ')) continue;
		out[n++] = (char)c;
	}
	if (((unsigned char)text[i] & 0xC0) == 0x80) { /* cut inside a character: drop its first bytes too */
		while (n && ((unsigned char)out[n - 1] & 0xC0) == 0x80) n--;
		if (n) n--;
	}
	while (n && out[n - 1] == ' ') n--;
	e26j_str(b, out, n);
}

/* Snippets read each article's text through e26_body on a storage revision 2 pack: a page spends at most this long
 * on them (a cold disk must not push the page past the 5 s deadline); the rows after it list without one. */
#define SNIPPET_BUDGET 0.75
static double snippet_t0;
static double mono(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static int force_sql;

enum { DOC_CISCO = 4, DOC_UNCLASSIFIED = 6, DOC_CISCO_WHITE_PAPER,
       DOC_CISCO_CVD, DOC_CISCO_CONFIG, DOC_CISCO_OTHER, DOC_TYPES, DOC_EAGER_HITS = 64 };
static const char *const doc_types[DOC_TYPES] = {
	"rfc", "draft", "wiki", "nist", "cisco", "synthetic", "unclassified",
	"cisco-white-paper", "cisco-cvd", "cisco-configuration-guide", "cisco-other"
};
enum { SORT_TITLE, SORT_ID, SORT_DATE };

static const struct cisco_type { const char *id; int type; } cisco_types[] = {
#include "cisco_types.inc"
};

typedef struct { char *anchor, *title; int level; int64_t block; } document_hit;
typedef struct {
	uint32_t wheel, ordinal;
	char *id, *title, *key, *published, *updated;
	char topic[128];
	int bucket, cisco_type, duplicate;
	document_hit *hits;
	size_t nhits, caphits;
} document;

static const char *const CANONICAL =
	"SELECT wheel_ordinal,article_ordinal FROM title WHERE alias=0 AND catalogue_only=0 ORDER BY wheel_ordinal";
static const char *const DOC_INFO =
	"SELECT id,title,sort_key,source_kind,coalesce(published,''),coalesce(updated,'') FROM article WHERE ordinal=?1";
static const char *const DOC_ROW =
	"SELECT t.wheel_ordinal,a.title,a.id,a.ordinal,t.alias,t.catalogue_only,a.class,e26_unzstd(a.meta_json),"
	"EXISTS(SELECT 1 FROM anchor h WHERE h.article_ordinal=a.ordinal) "
	"FROM title t JOIN article a ON a.ordinal=t.article_ordinal WHERE t.wheel_ordinal=?1";
static const char *const DOC_TOPIC =
	"SELECT category_id FROM article_category WHERE article_ordinal=?1 "
	"ORDER BY (instr(category_id,'.')>0) DESC,category_id LIMIT 1";
static const char *const CISCO_ROSTER =
	"SELECT a.id FROM article a JOIN title t ON t.article_ordinal=a.ordinal "
	"WHERE a.source_kind='cisco' AND t.alias=0 AND t.catalogue_only=0 ORDER BY a.id";
static const char *const ALIAS_MATCH =
	"SELECT article_ordinal FROM title WHERE sort_key>=?1 AND sort_key<?2 AND catalogue_only=0";
static const char *const FTS_HITS =
	"SELECT rowid,e26_hits(docs) FROM docs WHERE docs MATCH ?1 "
	"AND e26_finder_allowed(rowid-1,?2) ORDER BY rowid";
static const char *const FTS_HIT_AT =
	"SELECT e26_hits(docs) FROM docs WHERE docs MATCH ?1 AND rowid=?2";
static const char *const HIT_BLOCK =
	"SELECT block_index,byte_end FROM body_map WHERE article_ordinal=?1 AND byte_start<=?2 AND byte_end>?2 "
	"ORDER BY block_index DESC LIMIT 1";
static const char *const HIT_ANCHOR =
	"SELECT anchor,title,level,block_index FROM anchor WHERE article_ordinal=?1 AND block_index<=?2 AND end_block>?2 "
	"ORDER BY block_index DESC,level DESC,anchor LIMIT 1";

static int doc_bucket(const char *kind)
{
	int i;
	for (i = 0; i < DOC_UNCLASSIFIED; i++) if (!strcmp(kind, doc_types[i])) return i;
	return DOC_UNCLASSIFIED;
}

static int compare_cisco_id(const void *key, const void *row)
{
	return strcmp(key, ((const struct cisco_type *)row)->id);
}

static int cisco_document_type(const char *id)
{
	const struct cisco_type *row = bsearch(id, cisco_types,
	    sizeof cisco_types / sizeof *cisco_types, sizeof *cisco_types, compare_cisco_id);
	return row ? row->type : DOC_CISCO_OTHER;
}

/* The additive roster is bound to the signed public U9 generation. 1 valid, 0 a mismatch, -1 no verdict: a
 * cancellation or the 5 s deadline stopped the scan (u14: these were reported as a roster mismatch, and a typed
 * Pinpointer query fell back to All types). The pack cannot change under a running helper, so a verdict is kept. */
static int cisco_roster_valid(const jv *pack)
{
	static int verdict = -1;
	const char *generation = json_str(pack, "generation"), *id;
	const size_t n = sizeof cisco_types / sizeof *cisco_types;
	unsigned counts[DOC_TYPES] = {0};
	sqlite3_stmt *st;
	size_t i = 0;
	int rc, type;
	if (!generation || strcmp(generation, "h2-4128ecf867cc63f27f06a92d")) return 1;
	if (verdict >= 0) return verdict;
	st = lib_stmt(LIB_CATALOGUE, CISCO_ROSTER);
	if (!st) return 0;
	while ((rc = sqlite3_step(st)) == SQLITE_ROW) {
		id = (const char *)sqlite3_column_text(st, 0);
		if (!id || i == n || strcmp(id, cisco_types[i].id)) break;
		type = cisco_types[i++].type;
		if (type < DOC_CISCO_WHITE_PAPER || type >= DOC_TYPES) break;
		counts[type]++;
	}
	sqlite3_reset(st);
	if (rc != SQLITE_ROW && rc != SQLITE_DONE) return lib_interrupted() ? -1 : 0;
	verdict = rc == SQLITE_DONE && i == n && n == 297 &&
	    counts[DOC_CISCO_WHITE_PAPER] == 2 && counts[DOC_CISCO_CVD] == 151 &&
	    counts[DOC_CISCO_CONFIG] == 0 && counts[DOC_CISCO_OTHER] == 144;
	return verdict;
}

static int public_pack(const jv *pack)
{
	const char *name = json_str(pack, "name"), *handling = json_str(pack, "handling");
	const char *marking = json_str(pack, "marking");
	return name && !strcmp(name, "lib-e26") && (!handling || !strcmp(handling, "PUBLIC")) &&
	       (!marking || !*marking || !strcmp(marking, "PUBLIC"));
}

static void doc_free(document *d)
{
	size_t i;
	free(d->id); free(d->title); free(d->key); free(d->published); free(d->updated);
	for (i = 0; i < d->nhits; i++) { free(d->hits[i].anchor); free(d->hits[i].title); }
	free(d->hits);
	memset(d, 0, sizeof *d);
}

static char *copy_column(sqlite3_stmt *st, int col)
{
	const char *s = (const char *)sqlite3_column_text(st, col);
	return s ? strdup(s) : NULL;
}

static int doc_load(sqlite3_stmt *st, document *d, uint32_t ord, uint32_t wheel)
{
	const char *kind;
	if (sqlite3_bind_int64(st, 1, ord) != SQLITE_OK || sqlite3_step(st) != SQLITE_ROW) return -1;
	d->id = copy_column(st, 0); d->title = copy_column(st, 1); d->key = copy_column(st, 2);
	kind = (const char *)sqlite3_column_text(st, 3);
	d->bucket = kind ? doc_bucket(kind) : DOC_UNCLASSIFIED;
	d->cisco_type = d->bucket == DOC_CISCO && d->id ? cisco_document_type(d->id) : -1;
	d->published = copy_column(st, 4); d->updated = copy_column(st, 5);
	d->wheel = wheel; d->ordinal = ord;
	sqlite3_reset(st);
	return d->id && d->title && d->key && d->published && d->updated ? 0 : -1;
}

static int doc_load_topic(sqlite3_stmt *st, document *d)
{
	const char *topic;
	int rc;
	if (sqlite3_bind_int64(st, 1, d->ordinal) != SQLITE_OK) return -1;
	rc = sqlite3_step(st);
	if (rc == SQLITE_ROW) {
		topic = (const char *)sqlite3_column_text(st, 0);
		if (topic && strlen(topic) < sizeof d->topic) snprintf(d->topic, sizeof d->topic, "%s", topic);
	}
	sqlite3_reset(st);
	return rc != SQLITE_ROW && rc != SQLITE_DONE ? -1 : 0;
}

/* Both paths use the first non-alias wheel row for an ordinal, then share
 * filtering, counting, rank and page code. H2 emits one such row per article. */
static int canonical_rows(int32_t *wheel, uint32_t n)
{
	uint32_t i, count = lib_nwheel();
	if (force_sql) {
		sqlite3_stmt *st = lib_stmt(LIB_CATALOGUE, CANONICAL);
		int rc;
		if (!st) return -1;
		while ((rc = sqlite3_step(st)) == SQLITE_ROW) {
			int64_t w = sqlite3_column_int64(st, 0), ord = sqlite3_column_int64(st, 1);
			if (w < 0 || w >= count || ord < 0 || ord >= n) { sqlite3_reset(st); return -1; }
			if (wheel[ord] < 0) wheel[ord] = (int32_t)w;
		}
		sqlite3_reset(st);
		return rc == SQLITE_DONE ? 0 : -1;
	}
	for (i = 0; i < count; i++) {
		int32_t ord;
		unsigned flags;
		if (!(i % 1024)) { lib_poll(); if (lib_interrupted()) return -1; }
		lib_wheel_row(i, &ord, &flags);
		if (!flags && ord >= 0 && (uint32_t)ord < n && wheel[ord] < 0) wheel[ord] = (int32_t)i;
	}
	return 0;
}

static int doc_title(const void *a, const void *b)
{
	const document *x = a, *y = b;
	int c = strcmp(x->key, y->key);
	return c ? c : strcmp(x->id, y->id);
}

static int doc_cisco_title(const void *a, const void *b)
{
	const document *x = a, *y = b;
	return x->cisco_type != y->cisco_type ? x->cisco_type - y->cisco_type : doc_title(a, b);
}

static int rfc_number(const char *id, const char **digits, size_t *length)
{
	const char *p;
	if (strncmp(id, "rfc", 3)) return 0;
	p = id + 3;
	if (*p < '1' || *p > '9') return 0;
	*digits = p;
	*length = strspn(p, "0123456789");
	return !p[*length];
}

static int doc_id(const void *a, const void *b)
{
	const document *x = a, *y = b;
	const char *nx = NULL, *ny = NULL;
	size_t lx = 0, ly = 0;
	int c;
	int gx = x->bucket == 0 && rfc_number(x->id, &nx, &lx) ? 0 : x->bucket == 1 ? 1 : 2;
	int gy = y->bucket == 0 && rfc_number(y->id, &ny, &ly) ? 0 : y->bucket == 1 ? 1 : 2;
	if (gx != gy) return gx - gy;
	if (gx == 0) {
		if (lx != ly) return lx < ly ? -1 : 1;
		return memcmp(nx, ny, lx);
	}
	if (gx == 1 && (c = strcmp(x->id, y->id))) return c;
	return doc_title(a, b);
}

static int doc_date(const void *a, const void *b)
{
	const document *x = a, *y = b;
	const char *dx = *x->published ? x->published : x->updated;
	const char *dy = *y->published ? y->published : y->updated;
	int c = strcmp(dy, dx);
	return c ? c : strcmp(x->id, y->id);
}

static int doc_aliases(const char *lo, unsigned char *aliases)
{
	sqlite3_stmt *st = lib_stmt(LIB_CATALOGUE, ALIAS_MATCH);
	size_t n = strlen(lo);
	char *hi = malloc(n + 2);
	int rc = SQLITE_ERROR;
	if (!st || !hi) { free(hi); return -1; }
	memcpy(hi, lo, n); hi[n] = (char)0xFF; hi[n + 1] = 0;
	if (sqlite3_bind_text(st, 1, lo, -1, SQLITE_STATIC) == SQLITE_OK &&
	    sqlite3_bind_text(st, 2, hi, -1, SQLITE_STATIC) == SQLITE_OK) {
		while ((rc = sqlite3_step(st)) == SQLITE_ROW) {
			int64_t ord = sqlite3_column_int64(st, 0);
			if (ord < 0 || ord >= lib_ndocs()) { rc = SQLITE_ERROR; break; }
			lib_setbit(aliases, (uint32_t)ord);
		}
	}
	sqlite3_reset(st); sqlite3_clear_bindings(st);
	free(hi);
	return rc == SQLITE_DONE ? 0 : -1;
}

static int doc_add_hit(document *d, sqlite3_stmt *st)
{
	const char *anchor = (const char *)sqlite3_column_text(st, 0);
	document_hit *hit;
	size_t i;
	if (!anchor) return -1;
	for (i = 0; i < d->nhits; i++) if (!strcmp(d->hits[i].anchor, anchor)) return 0;
	if (d->nhits == d->caphits) {
		size_t cap = d->caphits ? d->caphits * 2 : 8;
		document_hit *grown = realloc(d->hits, cap * sizeof *grown);
		if (!grown) return -1;
		d->hits = grown; d->caphits = cap;
	}
	hit = &d->hits[d->nhits];
	hit->anchor = copy_column(st, 0); hit->title = copy_column(st, 1);
	hit->level = sqlite3_column_int(st, 2); hit->block = sqlite3_column_int64(st, 3);
	if (!hit->anchor || !hit->title) { free(hit->anchor); free(hit->title); return -1; }
	d->nhits++;
	return 0;
}

/* Search's e26_hits auxiliary reports the UTF-8 offsets in body_map units.
 * Skip repeated offsets in the same block and emit each enclosing section once. */
static int doc_map_hits(document *d, const void *blob, size_t length, int first_only)
{
	sqlite3_stmt *block = lib_stmt(LIB_SEARCH, HIT_BLOCK), *anchor = lib_stmt(LIB_CATALOGUE, HIT_ANCHOR);
	const unsigned char *bytes = blob;
	int64_t end = 0, previous = -1;
	size_t at;
	int rc = -1;
	if (!block || !anchor || length % (2 * sizeof(unsigned)) || (length && !blob)) return -1;
	if (sqlite3_bind_int64(block, 1, d->ordinal) != SQLITE_OK ||
	    sqlite3_bind_int64(anchor, 1, d->ordinal) != SQLITE_OK) goto out;
	for (at = 0; at < length; at += 2 * sizeof(unsigned)) {
		unsigned offset;
		int64_t ordinal;
		int step;
		memcpy(&offset, bytes + at, sizeof offset);
		if (offset < end) continue;
		if (sqlite3_bind_int64(block, 2, offset) != SQLITE_OK) goto out;
		step = sqlite3_step(block);
		if (step == SQLITE_DONE) { sqlite3_reset(block); continue; }
		if (step != SQLITE_ROW) goto out;
		ordinal = sqlite3_column_int64(block, 0);
		end = sqlite3_column_int64(block, 1);
		sqlite3_reset(block);
		if (ordinal == previous) continue;
		previous = ordinal;
		if (sqlite3_bind_int64(anchor, 2, ordinal) != SQLITE_OK) goto out;
		step = sqlite3_step(anchor);
		if ((step == SQLITE_ROW && doc_add_hit(d, anchor)) ||
		    (step != SQLITE_ROW && step != SQLITE_DONE)) goto out;
		sqlite3_reset(anchor);
		if (first_only && d->nhits) break;
	}
	rc = 0;
out:
	sqlite3_reset(block); sqlite3_clear_bindings(block);
	sqlite3_reset(anchor); sqlite3_clear_bindings(anchor);
	return rc;
}

/* The canonical document array is still in ordinal order. Join it to one
 * rowid-ordered MATCH pass, with the scoped bitmap applied before e26_hits. */
static int doc_hits(document *docs, size_t count, const unsigned char *bits, const char *match)
{
	sqlite3 *db = lib_db(LIB_SEARCH);
	sqlite3_stmt *st;
	size_t i = 0;
	int step, rc = -1;
	if (!db || e26s_register_hits(db) != SQLITE_OK ||
	    sqlite3_create_function_v2(db, "e26_finder_allowed", 2, SQLITE_UTF8 | SQLITE_DETERMINISTIC,
	    NULL, allowed, NULL, NULL, NULL) != SQLITE_OK) return -1;
	st = lib_stmt(LIB_SEARCH, FTS_HITS);
	if (!st) return -1;
	if (sqlite3_bind_text(st, 1, match, -1, SQLITE_STATIC) != SQLITE_OK ||
	    sqlite3_bind_blob(st, 2, bits, (int)lib_bitmap_bytes(), SQLITE_STATIC) != SQLITE_OK) goto out;
	while ((step = sqlite3_step(st)) == SQLITE_ROW) {
		sqlite3_int64 rowid = sqlite3_column_int64(st, 0);
		while (i < count && (sqlite3_int64)docs[i].ordinal + 1 < rowid) i++;
		if (i == count || (sqlite3_int64)docs[i].ordinal + 1 != rowid ||
		    doc_map_hits(&docs[i], sqlite3_column_blob(st, 1), (size_t)sqlite3_column_bytes(st, 1), 0)) goto out;
	}
	if (step == SQLITE_DONE) rc = 0;
out:
	sqlite3_reset(st); sqlite3_clear_bindings(st);
	return rc;
}

/* A broad result already has a MATCH bitmap. Seek only to each visible rowid;
 * another unbounded MATCH pass would consume the cold-query budget itself. */
static int doc_hit_at(document *doc, const char *match, int first_only)
{
	sqlite3_stmt *st = lib_stmt(LIB_SEARCH, FTS_HIT_AT);
	int step, rc = -1;
	if (!st) return -1;
	if (sqlite3_bind_text(st, 1, match, -1, SQLITE_STATIC) == SQLITE_OK &&
	    sqlite3_bind_int64(st, 2, (sqlite3_int64)doc->ordinal + 1) == SQLITE_OK) {
		step = sqlite3_step(st);
		if (step == SQLITE_DONE) rc = 0;
		else if (step == SQLITE_ROW)
			rc = doc_map_hits(doc, sqlite3_column_blob(st, 0), (size_t)sqlite3_column_bytes(st, 0), first_only);
	}
	sqlite3_reset(st); sqlite3_clear_bindings(st);
	return rc;
}

static int row(e26j_buf *b, sqlite3_stmt *st, const char *match, const jv *pack,
               const document *doc, const document_hit *hit)
{
	static const char *const names[] = { "wheel_ordinal", "title", "id", "article_ordinal", "alias", "catalogue_only", "class" };
	/* Storage revision 2 (the compact lib-e26) keeps meta_json as a zstd frame: the SQL reads it through
	 * e26_unzstd (storage.c, CR-F2). Without a meta the pack's own name and marking stand in. */
	const char *raw = sqlite3_column_type(st, 7) == SQLITE_TEXT ? (const char *)sqlite3_column_text(st, 7) : NULL;
	const char *p, *mark, *snippet = "";
	jv *meta = raw ? e26j_parse(raw, strlen(raw), NULL, 0) : NULL;
	const jv *prov = json_get(meta, "provenance");
	sqlite3_stmt *sn = NULL;
	int i;
	if (raw && (!meta || meta->type != J_OBJ)) { json_free(meta); return -1; }
	p = json_str(prov, "pack");
	if (!p) p = json_str(pack, "name");
	mark = json_str(meta, "marking");
	if (!mark) mark = json_str(prov, "marking");
	if (!mark) mark = json_str(pack, "marking");
	if (!mark && (mark = json_str(pack, "handling")) && !strcmp(mark, "PUBLIC")) mark = NULL;
	/* A snippet is a courtesy: a pack whose FTS reads its text through an adapter this helper lacks gives none,
	 * and the row still lists. */
	if (match && sqlite3_column_type(st, 3) != SQLITE_NULL && (snippet_t0 ? mono() - snippet_t0 : (snippet_t0 = mono(), 0)) < SNIPPET_BUDGET && (sn = lib_stmt(LIB_SEARCH, SNIPPET)) &&
	    (sqlite3_bind_int64(sn, 1, sqlite3_column_int64(st, 3) + 1) != SQLITE_OK ||
	     sqlite3_bind_text(sn, 2, match, -1, SQLITE_STATIC) != SQLITE_OK || sqlite3_step(sn) != SQLITE_ROW ||
	     !(snippet = (const char *)sqlite3_column_text(sn, 0))))
		snippet = "";
	e26j_lit(b, "{");
	for (i = 0; i < 7; i++) {
		if (i) e26j_lit(b, ",");
		e26j_cstr(b, names[i]); e26j_lit(b, ":");
		if (sqlite3_column_type(st, i) == SQLITE_NULL) {
			if (i == 6) e26j_cstr(b, ""); else e26j_lit(b, "null");
		} else if (i == 4 || i == 5) e26j_lit(b, sqlite3_column_int(st, i) ? "true" : "false");
		else if (i == 0 || i == 3) e26j_i(b, sqlite3_column_int64(st, i));
		else e26j_cstr(b, (const char *)sqlite3_column_text(st, i));
	}
	e26j_lit(b, ",\"pack\":"); e26j_cstr(b, p ? p : "");
	e26j_lit(b, ",\"marking\":"); e26j_cstr(b, mark ? mark : "");
	e26j_lit(b, ",\"snippet\":"); short_text(b, snippet);
	if (doc) {
		const jv *obsolete = json_get(json_get(meta, "status"), "obsoleted_by");
		const char *revision = json_str(prov, "revision");
		e26j_lit(b, ",\"source_kind\":"); e26j_cstr(b, doc_types[doc->bucket]);
		if (doc->cisco_type >= DOC_CISCO_WHITE_PAPER) {
			e26j_lit(b, ",\"document_type\":"); e26j_cstr(b, doc_types[doc->cisco_type]);
		}
		if (doc->topic[0]) { e26j_lit(b, ",\"topic_id\":"); e26j_cstr(b, doc->topic); }
		e26j_lit(b, ",\"published\":"); e26j_cstr(b, doc->published);
		e26j_lit(b, ",\"updated\":"); e26j_cstr(b, doc->updated);
		e26j_lit(b, ",\"revision\":"); e26j_cstr(b, revision ? revision : "");
		e26j_lit(b, ",\"obsoleted\":"); e26j_lit(b, obsolete && obsolete->type == J_ARR && obsolete->n ? "true" : "false");
		e26j_lit(b, ",\"duplicate\":"); e26j_lit(b, doc->duplicate ? "true" : "false");
		e26j_lit(b, ",\"has_sections\":"); e26j_lit(b, sqlite3_column_int(st, 8) ? "true" : "false");
		e26j_lit(b, ",\"anchor\":"); e26j_cstr(b, hit ? hit->anchor : "");
		e26j_lit(b, ",\"section_title\":"); e26j_cstr(b, hit ? hit->title : "");
	}
	e26j_lit(b, "}");
	if (sn) sqlite3_reset(sn);
	json_free(meta);
	return b->bad ? -1 : 0;
}

/* The wheel in memory, in the helper (never the UI): one article ordinal per wheel ordinal (-1 for a catalogue-only
 * title), loaded once per pack generation. H2's wheel ordinals follow (sort_key, wheel_ordinal), which the load
 * checks, so a list position is a wheel ordinal and counting, ranking and paging a filtered list are walks over this
 * array: lib-e26's 805,266 titles in milliseconds, where the SQL below (a function call and a table row per title)
 * took 0.6 to 5 seconds. A pack that fails the check, or a helper short of memory, keeps the SQL path. */
static const char *const WHEEL_ALL = "SELECT wheel_ordinal,article_ordinal,sort_key FROM title ORDER BY wheel_ordinal";
static const char *const WHEEL_SEEK = "SELECT wheel_ordinal FROM title WHERE sort_key>=?1 ORDER BY sort_key,wheel_ordinal LIMIT 1";
static const char *const ROW =
	"SELECT t.wheel_ordinal,t.title,t.id,t.article_ordinal,t.alias,t.catalogue_only,"
	"CASE WHEN t.catalogue_only THEN 'catalogue' ELSE a.class END,e26_unzstd(a.meta_json) "
	"FROM title t LEFT JOIN article a ON a.ordinal=t.article_ordinal WHERE t.wheel_ordinal=?1";
static struct { char gen[160]; uint32_t n, ndocs; int32_t *art; int state; } WM; /* state: 0 none, 1 ready, -1 SQL */
#define ALLOWED(o, bits, cat) ((o) >= 0 ? (!(bits) || lib_bit((bits), (uint32_t)(o))) : ((cat) && !(bits)))

/* test hook: 1 makes finder.page take the SQL path (test_finder_query runs both and compares) */
void e26_q_finder_sql(int on);
void e26_q_finder_sql(int on) { force_sql = on; }

static int wheel_mem(const jv *pack)
{
	const char *gen = json_str(pack, "generation"), *k;
	sqlite3_stmt *st;
	uint32_t i = 0, n = (uint32_t)lib_nwheel();
	char *prev = NULL;
	size_t prevn = 0, cap = 0, kn, m;
	int rc = SQLITE_ERROR, ok = 1, c;
	if (force_sql || !gen) return 0;
	if (WM.state && WM.n == n && WM.ndocs == (uint32_t)lib_ndocs() && !strcmp(WM.gen, gen)) return WM.state == 1;
	free(WM.art);
	memset(&WM, 0, sizeof WM);
	snprintf(WM.gen, sizeof WM.gen, "%s", gen);
	WM.n = n; WM.ndocs = (uint32_t)lib_ndocs(); WM.state = -1;
	if (!n || !(WM.art = (int32_t *)malloc((size_t)n * sizeof *WM.art)) || !(st = lib_stmt(LIB_CATALOGUE, WHEEL_ALL))) return 0;
	while (ok && (rc = sqlite3_step(st)) == SQLITE_ROW) {
		k = (const char *)sqlite3_column_text(st, 2);
		kn = (size_t)sqlite3_column_bytes(st, 2);
		if (i >= n || sqlite3_column_int64(st, 0) != (sqlite3_int64)i || !k) { ok = 0; break; }
		if (sqlite3_column_type(st, 1) == SQLITE_NULL) WM.art[i] = -1;
		else {
			sqlite3_int64 o = sqlite3_column_int64(st, 1);
			if (o < 0 || o >= (sqlite3_int64)WM.ndocs) { ok = 0; break; }
			WM.art[i] = (int32_t)o;
		}
		/* SQLite's BINARY order: bytes, then length */
		m = prevn < kn ? prevn : kn;
		if (i && ((c = memcmp(prev, k, m)) > 0 || (c == 0 && prevn > kn))) { ok = 0; break; }
		if (kn + 1 > cap) {
			char *grown = (char *)realloc(prev, kn + 64);
			if (!grown) { ok = 0; break; }
			prev = grown; cap = kn + 64;
		}
		memcpy(prev, k, kn); prevn = kn;
		i++;
	}
	sqlite3_reset(st);
	free(prev);
	if (!ok || rc != SQLITE_DONE || i != n) { free(WM.art); WM.art = NULL; return 0; }
	WM.state = 1;
	return 1;
}

/* the first wheel ordinal whose sort key is at or after key: n when none, -1 on a failure */
static int64_t wheel_pos(const char *key)
{
	sqlite3_stmt *st = lib_stmt(LIB_CATALOGUE, WHEEL_SEEK);
	int64_t p = -1;
	if (!st || sqlite3_bind_text(st, 1, key, -1, SQLITE_STATIC) != SQLITE_OK) return -1;
	switch (sqlite3_step(st)) {
	case SQLITE_ROW: p = sqlite3_column_int64(st, 0); break;
	case SQLITE_DONE: p = WM.n; break;
	default: break;
	}
	sqlite3_reset(st); sqlite3_clear_bindings(st);
	return p;
}


static const char *q_documents(const jv *args, const jv *pack, const unsigned char *bits,
    const unsigned char *search, const char *match, const char *prefix,
    const char *query, const char *scope, const char *current, int whole, int seek, int64_t start, int64_t limit,
    char **result)
{
	const char *type = "all", *sort = "title", *code;
	uint32_t ndocs = lib_ndocs(), ord, counts[DOC_TYPES] = {0};
	int32_t *wheel = NULL;
	unsigned char *aliases = NULL, *found = NULL, *last = NULL, *hit_bits = NULL;
	document *docs = NULL;
	char *fold = NULL;
	size_t cap = 0, count = 0, i, total = 0, nrow = 0, offset = 0, hit_docs = 0;
	int bucket = -1, order = SORT_TITLE, grouped, error = 0, types_valid;
	sqlite3_stmt *st = NULL, *topic = NULL;
	e26j_buf b;
	if ((lib_arg(args, "type") && lib_arg_str(args, "type", 32, 0, &type)) ||
	    (lib_arg(args, "sort") && lib_arg_str(args, "sort", 16, 0, &sort))) return lib_err(result, "E_ARGUMENT", NULL);
	types_valid = cisco_roster_valid(pack);
	if (types_valid < 0) return lib_sqlerr(result);
	if (!types_valid) {
		static int reported;
		if (!reported) {
			fprintf(stderr, "e26-lib: signed Cisco type roster mismatch\n");
			reported = 1;
		}
		if (strcmp(type, "all"))
			return lib_err(result, "E_TYPES", "The Cisco type roster does not match this Library.");
	}
	if (strcmp(type, "all")) {
		for (bucket = 0; bucket < DOC_TYPES && strcmp(type, doc_types[bucket]); bucket++) ;
		if (bucket == DOC_TYPES) return lib_err(result, "E_ARGUMENT", NULL);
	}
	if (!strcmp(sort, "id")) order = SORT_ID;
	else if (!strcmp(sort, "date")) order = SORT_DATE;
	else if (strcmp(sort, "title")) return lib_err(result, "E_ARGUMENT", NULL);
	wheel = calloc((size_t)ndocs + 1, sizeof *wheel);
	found = calloc(lib_bitmap_bytes() + 1, 1);
	if (!wheel || !found) goto failed;
	if (search && !(hit_bits = calloc(lib_bitmap_bytes() + 1, 1))) goto failed;
	for (ord = 0; ord < ndocs; ord++) wheel[ord] = -1;
	if (canonical_rows(wheel, ndocs)) goto failed;
	if (*query || *prefix) {
		fold = lib_fold(*query ? query : prefix);
		if (!fold) goto failed;
		if (*fold && (*query || !seek)) {
			aliases = calloc(lib_bitmap_bytes() + 1, 1);
			if (!aliases || doc_aliases(fold, aliases)) goto failed;
			if (*query && !strcmp(scope, "last") && !(last = lib_bitmap_arg(args, "last"))) goto failed;
		}
	}
	st = lib_stmt(LIB_CATALOGUE, DOC_INFO);
	if (!st) goto failed;
	for (ord = 0; ord < ndocs; ord++) {
		document d = {0};
		int in_search = search && lib_bit(search, ord), alias_match = aliases && lib_bit(aliases, ord);
		if (!(ord % 1024)) { lib_poll(); if (lib_interrupted()) goto failed; }
		if (wheel[ord] < 0 || (bits && !lib_bit(bits, ord))) continue;
		if (doc_load(st, &d, ord, (uint32_t)wheel[ord])) { doc_free(&d); goto failed; }
		if (!types_valid) d.cisco_type = -1;
		if ((current && strcmp(current, d.id)) || (last && !lib_bit(last, ord))) alias_match = 0;
		if ((*query && !in_search && !alias_match) ||
		    (!*query && *prefix && !seek && !alias_match)) { doc_free(&d); continue; }
		counts[d.bucket]++;
		if (d.cisco_type >= DOC_CISCO_WHITE_PAPER) counts[d.cisco_type]++;
		if (bucket >= 0 && d.bucket != bucket && d.cisco_type != bucket) { doc_free(&d); continue; }
		if (in_search) { lib_setbit(hit_bits, ord); hit_docs++; }
		lib_setbit(found, ord);
		if (count == cap) {
			size_t nc = cap ? cap * 2 : 64;
			document *grown = realloc(docs, nc * sizeof *docs);
			if (!grown) { doc_free(&d); goto failed; }
			docs = grown; cap = nc;
		}
		docs[count++] = d;
	}
	/* Small results retain their section ranks. Broad results rank documents before reading bodies. */
	grouped = hit_docs > DOC_EAGER_HITS;
	if (grouped) {
		sqlite3 *db = lib_db(LIB_SEARCH);
		if (!db || e26s_register_hits(db) != SQLITE_OK) goto failed;
	} else if (hit_docs && doc_hits(docs, count, hit_bits, match)) goto failed;
	for (i = 0; i < count; i++) {
		size_t rows = !grouped && docs[i].nhits ? docs[i].nhits : 1;
		if (rows > (size_t)INT32_MAX - total) goto failed;
		total += rows;
	}
	if (count > 1) {
		qsort(docs, count, sizeof *docs, doc_title);
		/* Folded identical titles, including those on different pages,
		 * need identifiers to distinguish their canonical documents. */
		for (i = 1; i < count; i++) if (!strcmp(docs[i - 1].key, docs[i].key))
			docs[i - 1].duplicate = docs[i].duplicate = 1;
		if (order != SORT_TITLE) qsort(docs, count, sizeof *docs, order == SORT_ID ? doc_id : doc_date);
		else if (bucket == DOC_CISCO) qsort(docs, count, sizeof *docs, doc_cisco_title);
	}
	if (seek) {
		const document *nearest = NULL, *last_title = NULL;
		size_t nearest_at = 0, last_at = 0;
		for (i = 0; i < count; i++) {
			if (!last_title || doc_title(last_title, &docs[i]) < 0) { last_title = &docs[i]; last_at = offset; }
			if ((!fold || strcmp(docs[i].key, fold) >= 0) && (!nearest || doc_title(nearest, &docs[i]) > 0)) {
				nearest = &docs[i]; nearest_at = offset;
			}
			offset += !grouped && docs[i].nhits ? docs[i].nhits : 1;
		}
		start = (int64_t)(nearest ? nearest_at : last_at);
	}
	st = lib_stmt(LIB_CATALOGUE, DOC_ROW);
	if (!st) goto failed;
	if (whole && !(topic = lib_stmt(LIB_FACETS, DOC_TOPIC))) goto failed;
	e26j_init(&b); e26j_lit(&b, "{\"rows\":[");
	for (i = offset = 0; i < count && nrow < (size_t)limit; i++) {
		size_t h, rows = !grouped && docs[i].nhits ? docs[i].nhits : 1;
		if (offset + rows <= (size_t)start) { offset += rows; continue; }
		if (topic && doc_load_topic(topic, &docs[i])) { error = 1; break; }
		if (grouped && lib_bit(hit_bits, docs[i].ordinal) && doc_hit_at(&docs[i], match, 1)) { error = 1; break; }
		if (sqlite3_bind_int64(st, 1, docs[i].wheel) != SQLITE_OK || sqlite3_step(st) != SQLITE_ROW) { error = 1; break; }
		for (h = 0; h < rows && nrow < (size_t)limit; h++, offset++) {
			if (offset < (size_t)start) continue;
			if (nrow++) e26j_lit(&b, ",");
			if (row(&b, st, NULL, pack, &docs[i], docs[i].nhits ? &docs[i].hits[h] : NULL)) { error = 1; break; }
		}
		sqlite3_reset(st);
		if (error) break;
	}
	if (error) { e26j_free(&b); goto failed; }
	e26j_lit(&b, "],\"start\":"); e26j_i(&b, start);
	e26j_lit(&b, ",\"next\":");
	if ((size_t)start + nrow < total) e26j_u(&b, (size_t)start + nrow); else e26j_lit(&b, "null");
	e26j_lit(&b, ",\"total\":"); e26j_u(&b, total);
	e26j_lit(&b, ",\"universe\":"); e26j_u(&b, total > lib_nwheel() ? total : lib_nwheel());
	e26j_lit(&b, ",\"catalogue_id\":"); e26j_cstr(&b, json_str(pack, "name"));
	e26j_lit(&b, ",\"bitmap\":"); lib_bitmap_out(&b, found);
	e26j_lit(&b, ",\"doc_count\":"); e26j_u(&b, count);
	if (!types_valid) e26j_lit(&b, ",\"types_unavailable\":true");
	if (grouped) e26j_lit(&b, ",\"grouped\":true");
	e26j_lit(&b, ",\"type_counts\":{");
	for (i = 0, offset = 0; i < DOC_TYPES; i++) if (counts[i]) {
		if (offset++) e26j_lit(&b, ",");
		e26j_cstr(&b, doc_types[i]); e26j_lit(&b, ":"); e26j_u(&b, counts[i]);
	}
	e26j_lit(&b, "}}");
	code = lib_ok(&b, result);
	goto out;
failed:
	code = lib_sqlerr(result);
out:
	if (st) { sqlite3_reset(st); sqlite3_clear_bindings(st); }
	if (topic) { sqlite3_reset(topic); sqlite3_clear_bindings(topic); }
	for (i = 0; i < count; i++) doc_free(&docs[i]);
	free(docs); free(wheel); free(found); free(aliases); free(last); free(fold); free(hit_bits);
	return code;
}

static int hit_block_order(const void *a, const void *b)
{
	const document_hit *x = a, *y = b;
	if (x->block != y->block) return x->block < y->block ? -1 : 1;
	return strcmp(x->anchor, y->anchor);
}

static void section_out(e26j_buf *b, const char *anchor, const char *title, int level, int64_t block)
{
	e26j_lit(b, "{\"anchor\":"); e26j_cstr(b, anchor);
	e26j_lit(b, ",\"title\":"); e26j_cstr(b, title);
	e26j_lit(b, ",\"level\":"); e26j_i(b, level);
	e26j_lit(b, ",\"block_index\":"); e26j_i(b, block);
	e26j_lit(b, "}");
}

static const char *q_sections(const jv *args, char **result)
{
	static const char *const DOC =
		"SELECT a.ordinal,e26_unzstd(a.meta_json),"
		"(SELECT count(DISTINCT h.block_index) FROM anchor h WHERE h.article_ordinal=a.ordinal) "
		"FROM article a WHERE a.id=?1";
	/* Compatibility anchors can name the same heading. Keep the first by the existing section order. */
	static const char *const PAGE_SECTIONS =
		"SELECT anchor,title,level,block_index FROM ("
		"SELECT anchor,title,level,block_index,"
		"row_number() OVER (PARTITION BY block_index ORDER BY level,anchor) AS section_rank "
		"FROM anchor WHERE article_ordinal=?1) WHERE section_rank=1 ORDER BY block_index LIMIT ?2 OFFSET ?3";
	const char *id, *raw, *mark, *code, *query = NULL;
	char match[8192];
	int64_t start, limit, ord, total, near_value, n = 0;
	sqlite3_stmt *st = NULL;
	jv *pack = NULL, *meta = NULL;
	document hits = {0};
	e26q_ast *ast = NULL;
	e26q_err err;
	e26j_buf b;
	int rc, title_mode;
	if (lib_args(args, "id", "start", "limit", "query", "title_mode", "near", NULL) ||
	    lib_arg_str(args, "id", 4096, 0, &id) ||
	    (lib_arg(args, "query") && lib_arg_str(args, "query", 4096, 0, &query)) ||
	    (!query && (lib_arg(args, "title_mode") || lib_arg(args, "near"))) ||
	    lib_arg_bool(args, "title_mode", 0, &title_mode) || lib_arg_int(args, "near", 1, 9999, 8, &near_value) ||
	    lib_arg_page(args, 32, 64, &start, &limit)) return lib_err(result, "E_ARGUMENT", NULL);
	if (!lib_ready(result)) return lib_code();
	pack = e26j_parse(lib_pack_json(), strlen(lib_pack_json()), NULL, 0);
	if (!public_pack(pack)) { code = lib_err(result, "E_UNSUPPORTED", NULL); goto out; }
	st = lib_stmt(LIB_CATALOGUE, DOC);
	if (!st || sqlite3_bind_text(st, 1, id, -1, SQLITE_STATIC) != SQLITE_OK) goto failed;
	rc = sqlite3_step(st);
	if (rc == SQLITE_DONE) { code = lib_err(result, "E_NOT_FOUND", NULL); goto out; }
	if (rc != SQLITE_ROW) goto failed;
	ord = sqlite3_column_int64(st, 0); total = sqlite3_column_int64(st, 2);
	raw = (const char *)sqlite3_column_text(st, 1);
	meta = raw ? e26j_parse(raw, strlen(raw), NULL, 0) : NULL;
	if (!meta || meta->type != J_OBJ) goto failed;
	mark = json_str(meta, "marking");
	if (!mark) mark = json_str(json_get(meta, "provenance"), "marking");
	if (mark && *mark && strcmp(mark, "PUBLIC")) { code = lib_err(result, "E_UNSUPPORTED", NULL); goto out; }
	sqlite3_reset(st); sqlite3_clear_bindings(st);
	if (query) {
		sqlite3 *db = lib_db(LIB_SEARCH);
		size_t i, unique = 0;
		if (e26q_parse(query, title_mode, (int)near_value, &ast, &err) ||
		    e26q_to_fts5(ast, match, sizeof match) < 0) {
			code = lib_err(result, "E_ARGUMENT", NULL);
			goto out;
		}
		if (ord < 0 || ord >= lib_ndocs() || !db || e26s_register_hits(db) != SQLITE_OK) goto failed;
		hits.ordinal = (uint32_t)ord;
		if (doc_hit_at(&hits, match, 0)) goto failed;
		if (hits.nhits > 1) qsort(hits.hits, hits.nhits, sizeof *hits.hits, hit_block_order);
		for (i = 0; i < hits.nhits; i++) {
			document_hit *hit = &hits.hits[i];
			if (unique && hits.hits[unique - 1].block == hit->block) {
				free(hit->anchor); free(hit->title);
			} else hits.hits[unique++] = *hit;
		}
		hits.nhits = unique;
		total = (int64_t)unique;
	} else {
		st = lib_stmt(LIB_CATALOGUE, PAGE_SECTIONS);
		if (!st || sqlite3_bind_int64(st, 1, ord) != SQLITE_OK || sqlite3_bind_int64(st, 2, limit) != SQLITE_OK ||
		    sqlite3_bind_int64(st, 3, start) != SQLITE_OK) goto failed;
	}
	e26j_init(&b); e26j_lit(&b, "{\"id\":"); e26j_cstr(&b, id);
	e26j_lit(&b, ",\"catalogue_id\":\"lib-e26\",\"rows\":[");
	if (query) {
		for (; n < limit && start + n < total; n++) {
			const document_hit *hit = &hits.hits[start + n];
			if (n) e26j_lit(&b, ",");
			section_out(&b, hit->anchor, hit->title, hit->level, hit->block);
		}
	} else {
		while ((rc = sqlite3_step(st)) == SQLITE_ROW) {
			if (n++) e26j_lit(&b, ",");
			section_out(&b, (const char *)sqlite3_column_text(st, 0), (const char *)sqlite3_column_text(st, 1),
			            sqlite3_column_int(st, 2), sqlite3_column_int64(st, 3));
		}
		if (rc != SQLITE_DONE) { e26j_free(&b); goto failed; }
	}
	e26j_lit(&b, "],\"start\":"); e26j_i(&b, start);
	e26j_lit(&b, ",\"next\":");
	if (start + n < total) e26j_i(&b, start + n); else e26j_lit(&b, "null");
	e26j_lit(&b, ",\"total\":"); e26j_i(&b, total);
	e26j_lit(&b, "}"); code = lib_ok(&b, result);
	goto out;
failed:
	code = lib_sqlerr(result);
out:
	if (st) { sqlite3_reset(st); sqlite3_clear_bindings(st); }
	e26q_free(ast); doc_free(&hits);
	json_free(pack); json_free(meta);
	return code;
}

static const char *q_finder(const jv *args, char **result)
{
	const char *prefix = "", *query = "", *scope = "all", *current = NULL, *last = NULL, *code = NULL, *mode = NULL;
	const char *collection = NULL;
	const jv *filters;
	int64_t start, limit, near_value, viewport, total = 0, nrow = 0;
	int catalogue, title_mode, seek, filtered = 0, rc;
	size_t i, nb;
	unsigned char *bits = NULL, *search = NULL, *found = NULL;
	char *lo = NULL, *hi = NULL, *target = NULL, *text = NULL, match[8192];
	jv *search_args = NULL, *pack = NULL;
	sqlite3_stmt *st = NULL;
	e26q_ast *ast = NULL;
	e26q_err err;
	e26j_buf b;
	*result = NULL;
	if (lib_args(args, "prefix", "start", "limit", "catalogue", "filters", "query", "scope", "last", "current",
	             "title_mode", "near", "seek", "mode", "type", "sort", "viewport", "collection", NULL) ||
	    (lib_arg(args, "mode") && (lib_arg_str(args, "mode", 16, 0, &mode) || strcmp(mode, "documents"))) ||
	    (lib_arg(args, "collection") && (!mode || lib_arg_str(args, "collection", 16, 0, &collection) ||
	                                    strcmp(collection, "library"))) ||
	    (!mode && (lib_arg(args, "type") || lib_arg(args, "sort"))) ||
	    (lib_arg(args, "prefix") && lib_arg_str(args, "prefix", 4096, 1, &prefix)) ||
	    (lib_arg(args, "query") && lib_arg_str(args, "query", 4096, 1, &query)) ||
	    (lib_arg(args, "scope") && lib_arg_str(args, "scope", 16, 0, &scope)) ||
	    lib_arg_page(args, 32, 64, &start, &limit) || lib_arg_bool(args, "catalogue", 0, &catalogue) || lib_arg_bool(args, "seek", 0, &seek) ||
	    lib_arg_bool(args, "title_mode", 0, &title_mode) || lib_arg_int(args, "near", 1, 9999, 8, &near_value))
		return lib_err(result, "E_ARGUMENT", NULL);
	if (lib_arg(args, "viewport")) {
		if (!mode || !query[0] || lib_arg_int(args, "viewport", 1, 64, 64, &viewport))
			return lib_err(result, "E_ARGUMENT", NULL);
		if (viewport < limit) limit = viewport;
	}
	if (strcmp(scope, "all") && strcmp(scope, "current") && strcmp(scope, "last")) return lib_err(result, "E_ARGUMENT", NULL);
	if ((query[0] && ((!strcmp(scope, "current")) != (lib_arg(args, "current") != NULL) ||
	    (!strcmp(scope, "last")) != (lib_arg(args, "last") != NULL))) ||
	    (!query[0] && (lib_arg(args, "current") || lib_arg(args, "last"))) ||
	    (lib_arg(args, "current") && lib_arg_str(args, "current", 4096, 0, &current)) ||
	    (lib_arg(args, "last") && lib_arg_str(args, "last", 8388608, 1, &last))) return lib_err(result, "E_ARGUMENT", NULL);
	filters = lib_arg(args, "filters");
	if (filters && lib_args(filters, "categories", "media_kinds", "year_min", "year_max", "places", NULL)) return lib_err(result, "E_ARGUMENT", NULL);
	if (mode) {
		const jv *categories = lib_arg(filters, "categories");
		if ((categories && categories->type != J_ARR) ||
		    (collection ? categories && categories->n : !categories || !categories->n))
			return lib_err(result, "E_ARGUMENT", NULL);
	}
	if (!lib_ready(result)) return lib_code();
	if (mode) {
		pack = e26j_parse(lib_pack_json(), strlen(lib_pack_json()), NULL, 0);
		if (!public_pack(pack)) { code = lib_err(result, "E_UNSUPPORTED", NULL); goto out; }
	}
	nb = lib_bitmap_bytes();
	if (filters) {
		if ((code = compose("facets", filters, &bits, result))) goto out;
		for (i = 0; i < filters->n; i++) if (filters->items[i]->type != J_ARR || filters->items[i]->n) filtered = 1;
		if (!filtered) { free(bits); bits = NULL; }
	}
	if (query[0]) {
		e26j_init(&b);
		e26j_lit(&b, "{\"text\":"); e26j_cstr(&b, query);
		e26j_lit(&b, ",\"scope\":"); e26j_cstr(&b, scope);
		e26j_lit(&b, ",\"title_mode\":"); e26j_lit(&b, title_mode ? "true" : "false");
		e26j_lit(&b, ",\"near\":"); e26j_i(&b, near_value);
		if (current) { e26j_lit(&b, ",\"id\":"); e26j_cstr(&b, current); }
		if (last) { e26j_lit(&b, ",\"last\":"); e26j_cstr(&b, last); }
		e26j_lit(&b, "}");
		text = e26j_take(&b);
		search_args = text ? e26j_parse(text, strlen(text), NULL, 0) : NULL;
		if (!search_args) { code = lib_err(result, "E_PACK", NULL); goto out; }
		q_search_attach(lib_db(LIB_SEARCH), lib_db(LIB_CATALOGUE), lib_ndocs());
		if ((code = compose("search", search_args, &search, result))) goto out;
		if (!mode) {
			if (bits) { for (i = 0; i < nb; i++) bits[i] &= search[i]; }
			else { bits = search; search = NULL; }
		}
		/* search has already validated this expression and delivered its precise numbered failures; e26q_to_fts5
		 * returns the MATCH length, or a negative number when the buffer is too small. */
		if (e26q_parse(query, title_mode, (int)near_value, &ast, &err) || e26q_to_fts5(ast, match, sizeof match) < 0) {
			code = lib_err(result, "E_PACK", NULL); goto out;
		}
	}
	if (mode) {
		code = q_documents(args, pack, bits, search, query[0] ? match : NULL, prefix,
		                   query, scope, current, collection != NULL, seek, start, limit, result);
		goto out;
	}
	snippet_t0 = 0;   /* the budget starts at the page's first snippet */
	lo = lib_fold(seek ? "" : prefix);
	if (seek) target = lib_fold(prefix);
	if (!lo || (seek && !target) || !(hi = (char *)malloc(strlen(lo) + 2)) || !(found = (unsigned char *)calloc(nb + 1, 1))) { code = lib_err(result, "E_PACK", NULL); goto out; }
	memcpy(hi, lo, strlen(lo)); hi[strlen(lo)] = (char)0xFF; hi[strlen(lo) + 1] = 0;
	pack = e26j_parse(lib_pack_json(), strlen(lib_pack_json()), NULL, 0);
	if (pack && wheel_mem(pack)) {
		/* positions are wheel ordinals (wheel_mem): the prefix's range, then one walk for the count, the
		 * last-list bitmap and the seek's rank, and one to the page's rows, read by primary key */
		int64_t plo = wheel_pos(lo), phi = wheel_pos(hi), tpos = seek ? wheel_pos(target) : 0, k, rank = 0;
		if (plo < 0 || phi < 0 || tpos < 0) { code = lib_sqlerr(result); goto out; }
		for (k = plo; k < phi; k++) {
			int32_t o = WM.art[k];
			if (!ALLOWED(o, bits, catalogue)) continue;
			total++;
			if (k < tpos) rank++;
			if (o >= 0) lib_setbit(found, (uint32_t)o);
		}
		if (seek) { start = rank; if (total && start >= total) start = total - 1; }
		if (!(st = lib_stmt(LIB_CATALOGUE, ROW))) { code = lib_sqlerr(result); goto out; }
		e26j_init(&b); e26j_lit(&b, "{\"rows\":[");
		for (k = plo, rank = 0; k < phi && nrow < limit; k++) {
			if (!ALLOWED(WM.art[k], bits, catalogue) || rank++ < start) continue;
			if (sqlite3_bind_int64(st, 1, k) != SQLITE_OK || sqlite3_step(st) != SQLITE_ROW) { e26j_free(&b); code = lib_sqlerr(result); goto out; }
			if (nrow++) e26j_lit(&b, ",");
			if (row(&b, st, query[0] ? match : NULL, pack, NULL, NULL)) { e26j_free(&b); code = lib_sqlerr(result); goto out; }
			sqlite3_reset(st);
		}
		goto tail;
	}
	if (!pack || sqlite3_create_function_v2(lib_db(LIB_CATALOGUE), "e26_finder_allowed", 2, SQLITE_UTF8 | SQLITE_DETERMINISTIC,
	    NULL, allowed, NULL, NULL, NULL) != SQLITE_OK ||
	    sqlite3_create_function_v2(lib_db(LIB_CATALOGUE), "e26_finder_bitmap", 1, SQLITE_UTF8, NULL, NULL, bitmap_step, bitmap_final, NULL) != SQLITE_OK)
		{ code = lib_sqlerr(result); goto out; }
	st = lib_stmt(LIB_CATALOGUE, COUNT);
	if (!st || bind_range(st, lo, hi, catalogue, bits) || sqlite3_step(st) != SQLITE_ROW ||
	    sqlite3_column_bytes(st, 1) != (int)nb) { code = lib_sqlerr(result); goto out; }
	total = sqlite3_column_int64(st, 0);
	if (nb) {
		const void *blob = sqlite3_column_blob(st, 1);
		if (!blob) { code = lib_sqlerr(result); goto out; }
		memcpy(found, blob, nb);
	}
	sqlite3_reset(st);
	sqlite3_clear_bindings(st);
	if (seek) {
		/* Pinpointer type-ahead selects the nearest title without narrowing the list or its count. The rank
		 * is in the current filtered list, so sparse facets and aliases retain their logical row offsets. */
		st = lib_stmt(LIB_CATALOGUE, RANK);
		if (!st || sqlite3_bind_text(st, 1, target, -1, SQLITE_STATIC) != SQLITE_OK ||
		    sqlite3_bind_int(st, 2, catalogue) != SQLITE_OK ||
		    (bits ? sqlite3_bind_blob(st, 3, bits, (int)nb, SQLITE_STATIC) : sqlite3_bind_null(st, 3)) != SQLITE_OK ||
		    sqlite3_step(st) != SQLITE_ROW) { code = lib_sqlerr(result); goto out; }
		start = total ? sqlite3_column_int64(st, 0) : 0;
		if (total && start >= total) start = total - 1;
		sqlite3_reset(st); sqlite3_clear_bindings(st);
	}
	st = lib_stmt(LIB_CATALOGUE, PAGE);
	if (!st || bind_range(st, lo, hi, catalogue, bits) || sqlite3_bind_int64(st, 5, limit) != SQLITE_OK ||
	    sqlite3_bind_int64(st, 6, start) != SQLITE_OK) { code = lib_sqlerr(result); goto out; }
	e26j_init(&b); e26j_lit(&b, "{\"rows\":[");
	while ((rc = sqlite3_step(st)) == SQLITE_ROW) {
		if (nrow++) e26j_lit(&b, ",");
		if (nrow > limit || row(&b, st, query[0] ? match : NULL, pack, NULL, NULL)) { e26j_free(&b); code = lib_sqlerr(result); goto out; }
	}
	if (rc != SQLITE_DONE) { e26j_free(&b); code = lib_sqlerr(result); goto out; }
tail:
	e26j_lit(&b, "],\"start\":"); e26j_i(&b, start);
	e26j_lit(&b, ",\"next\":");
	if (start + nrow < total) e26j_i(&b, start + nrow); else e26j_lit(&b, "null");
	e26j_lit(&b, ",\"total\":"); e26j_i(&b, total);
	e26j_lit(&b, ",\"universe\":"); e26j_u(&b, lib_nwheel());
	e26j_lit(&b, ",\"catalogue_id\":"); e26j_cstr(&b, json_str(pack, "name"));
	e26j_lit(&b, ",\"bitmap\":"); lib_bitmap_out(&b, found);
	e26j_lit(&b, "}"); code = lib_ok(&b, result);
out:
	if (st) { sqlite3_reset(st); sqlite3_clear_bindings(st); }
	free(bits); free(search); free(found); free(lo); free(hi); free(target); free(text);
	json_free(search_args); json_free(pack); e26q_free(ast);
	return code;
}
#else
static const char *q_finder(const jv *args, char **result)
{
	(void)args;
	return lib_err(result, "E_UNSUPPORTED", NULL);
}
static const char *q_sections(const jv *args, char **result)
{
	(void)args;
	return lib_err(result, "E_UNSUPPORTED", NULL);
}
#endif

void e26_q_finder(void)
{
	static const e26_helper_op page = { q_finder }, sections = { q_sections };
	reg_add(REG_QUERY, "finder.page", &page, "q_finder", 0);
	reg_add(REG_QUERY, "finder.sections", &sections, "q_finder", 0);
}
