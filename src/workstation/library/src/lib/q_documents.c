/* The optional public Cisco catalogue, independent of the legacy Library operations. */
#include "document_route.h"
#include "../app/registry.h"
#include "../common/helper.h"

#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

void e26_q_cisco(void);

#if defined(E26_LIB_SQLITE)
#define MAX_CATALOGUE_BYTES (64u * 1024u * 1024u)
#define MAX_RECORD_BYTES 24576u

static const char *const STORE_NAMES[] = { "lib-e26", "lib-corpus" };
static struct cisco_catalogue pinned;
static char catalogue_path[PATH_MAX];
static int explicit_catalogue, tried;
static const char *load_error;

static int progress(void *arg)
{
	(void)arg;
	lib_poll();
	return lib_interrupted();
}

void lib_cisco_set_path(const char *path, int explicit_path)
{
	if (pinned.db) sqlite3_close(pinned.db);
	memset(&pinned, 0, sizeof pinned);
	snprintf(catalogue_path, sizeof catalogue_path, "%s", path ? path : "");
	explicit_catalogue = explicit_path;
	tried = 0;
	load_error = "E_PACK";
}

static const char *text_column(sqlite3_stmt *st, int col, size_t max, int empty_ok)
{
	const char *s;
	int bytes;
	if (sqlite3_column_type(st, col) != SQLITE_TEXT) return NULL;
	s = (const char *)sqlite3_column_text(st, col);
	bytes = sqlite3_column_bytes(st, col);
	return s && (bytes || empty_ok) && (size_t)bytes <= max &&
	    e26j_utf8(s, (size_t)bytes) ? s : NULL;
}

static int same(const char *a, const char *b)
{
	return a && b ? !strcmp(a, b) : a == b;
}

int cisco_store_number(const char *name)
{
	int i;
	for (i = 0; name && i < 2; i++) if (!strcmp(name, STORE_NAMES[i])) return i;
	return -1;
}

int cisco_store_available(const struct cisco_catalogue *cat, int n)
{
	const struct cisco_store *s = &cat->store[n];
	return s->index >= 0 && same(s->installed, s->expected) &&
	    same(s->installed_hash, s->expected_hash);
}

const char *cisco_store_ready(const struct cisco_catalogue *cat, int n, char **result)
{
	if (cisco_store_available(cat, n)) return NULL;
	if (cat->store[n].index < 0)
		return lib_err(result, "E_PACK", n ? "The public Corpus reader pack is unavailable." :
		    "The base Library reader pack is unavailable.");
	return lib_err(result, "E_STALE", n ? "The public Corpus reader pack does not match this Cisco catalogue." :
	    "The base Library reader pack does not match this Cisco catalogue.");
}

void cisco_stores_json(const struct cisco_catalogue *cat, e26j_buf *b)
{
	int i;
	e26j_lit(b, "[");
	for (i = 0; i < 2; i++) {
		const struct cisco_store *s = &cat->store[i];
		if (i) e26j_lit(b, ",");
		e26j_lit(b, "{\"reader_pack\":"); e26j_cstr(b, STORE_NAMES[i]);
		e26j_lit(b, ",\"reader_generation\":"); e26j_cstr(b, s->expected);
		e26j_lit(b, ",\"installed_generation\":"); e26j_cstr(b, s->installed);
		e26j_lit(b, ",\"state\":");
		e26j_cstr(b, s->index < 0 ? "missing" : cisco_store_available(cat, i) ? "ready" : "stale");
		e26j_lit(b, "}");
	}
	e26j_lit(b, "]");
}

static int fixture_reader(void)
{
	const char *raw = lib_pack_json();
	jv *pack = raw ? e26j_parse(raw, strlen(raw), NULL, 0) : NULL;
	const jv *fixture = json_get(pack, "fixture");
	int ok = fixture && fixture->type == J_BOOL && fixture->b;
	json_free(pack);
	return ok;
}

static void catalogue_uri(char uri[PATH_MAX * 3 + 32], const char *path)
{
	static const char hex[] = "0123456789ABCDEF";
	const unsigned char *s = (const unsigned char *)path;
	char *out = uri + 5;
	memcpy(uri, "file:", 5);
	for (; *s; s++) {
		*out++ = '%';
		*out++ = hex[*s >> 4];
		*out++ = hex[*s & 15];
	}
	memcpy(out, "?mode=ro&immutable=1", sizeof "?mode=ro&immutable=1");
}

static void load_catalogue(void)
{
	static const char META[] =
		"SELECT (SELECT application_id FROM pragma_application_id),"
		"(SELECT user_version FROM pragma_user_version),"
		"(SELECT value FROM meta WHERE key='schema_version'),"
		"(SELECT value FROM meta WHERE key='catalogue_generation'),"
		"(SELECT value FROM meta WHERE key='scope')";
	sqlite3_stmt *st = NULL;
	char uri[PATH_MAX * 3 + 32];
	struct stat info;
	const char *s, *scope;
	int i, rc, mask = 0;

	tried = 1;
	load_error = "E_PACK";
	if (!catalogue_path[0] || stat(catalogue_path, &info) || !S_ISREG(info.st_mode) ||
	    info.st_size <= 0 || (uint64_t)info.st_size > MAX_CATALOGUE_BYTES ||
	    (!explicit_catalogue && (info.st_uid != 0 || (info.st_mode & 0777) != 0440))) return;
	catalogue_uri(uri, catalogue_path);
	if (sqlite3_open_v2(uri, &pinned.db, SQLITE_OPEN_READONLY | SQLITE_OPEN_URI |
	    SQLITE_OPEN_NOMUTEX, NULL) != SQLITE_OK) goto done;
	sqlite3_limit(pinned.db, SQLITE_LIMIT_LENGTH, 1 << 20);
	if (sqlite3_exec(pinned.db, "PRAGMA trusted_schema=OFF; PRAGMA query_only=1; PRAGMA temp_store=MEMORY",
	    NULL, NULL, NULL) != SQLITE_OK ||
	    sqlite3_prepare_v2(pinned.db, META, -1, &st, NULL) != SQLITE_OK ||
	    sqlite3_step(st) != SQLITE_ROW) goto done;
	if (sqlite3_column_int(st, 0) != 0x44333243 || sqlite3_column_int(st, 1) != 1 ||
	    !same(text_column(st, 2, 4, 0), "1")) {
		load_error = "E_VERSION";
		goto done;
	}
	s = text_column(st, 3, 128, 0);
	scope = text_column(st, 4, 8, 0);
	if (!s) goto done;
	if (same(scope, "verified")) {
		if (info.st_uid != 0 || (info.st_mode & 0777) != 0440) goto done;
	} else if (!same(scope, "fixture") || !explicit_catalogue || !fixture_reader()) {
		goto done;
	}
	memcpy(pinned.generation, s, strlen(s) + 1);
	sqlite3_finalize(st); st = NULL;
	if (sqlite3_prepare_v2(pinned.db, "SELECT name,generation,catalogue_sha256 FROM reader_pack",
	    -1, &st, NULL) != SQLITE_OK) goto done;
	while ((rc = sqlite3_step(st)) == SQLITE_ROW) {
		const char *generation = text_column(st, 1, 128, 0);
		const char *hash = text_column(st, 2, 64, 0);
		i = cisco_store_number(text_column(st, 0, 10, 0));
		if (i < 0 || (mask & (1 << i)) || !generation || !hash ||
		    strspn(hash, "0123456789abcdef") != 64) goto done;
		memcpy(pinned.store[i].expected, generation, strlen(generation) + 1);
		memcpy(pinned.store[i].expected_hash, hash, 65);
		mask |= 1 << i;
	}
	if (rc != SQLITE_DONE || mask != 3) goto done;
	sqlite3_finalize(st); st = NULL;
	if (sqlite3_prepare_v2(pinned.db, "SELECT count(*) FROM (SELECT 1 FROM catalogue LIMIT 4097)",
	    -1, &st, NULL) != SQLITE_OK || sqlite3_step(st) != SQLITE_ROW ||
	    sqlite3_column_int(st, 0) > CISCO_MAX_ROWS) goto done;
	for (i = 0; i < 2; i++)
		pinned.store[i].index = lib_public_pack(STORE_NAMES[i],
		    &pinned.store[i].installed, &pinned.store[i].installed_hash);
	sqlite3_progress_handler(pinned.db, 1000, progress, NULL);
	load_error = NULL;
done:
	sqlite3_finalize(st);
	if (load_error && pinned.db) {
		sqlite3_close(pinned.db);
		pinned.db = NULL;
	}
}

const char *cisco_catalogue_open(struct cisco_catalogue *cat, const jv *args, int need_generation, char **result)
{
	const char *generation = NULL;
	memset(cat, 0, sizeof *cat);
	if (need_generation && lib_arg_str(args, "catalogue_generation", 128, 0, &generation))
		return lib_err(result, "E_ARGUMENT", NULL);
	if (!tried) load_catalogue();
	if (load_error)
		return lib_err(result, load_error, "The public Cisco catalogue is unavailable or incompatible.");
	if (generation && strcmp(generation, pinned.generation))
		return lib_err(result, "E_STALE", "The Cisco catalogue has changed. Reopen the catalogue.");
	*cat = pinned;
	return NULL;
}

/* Requests borrow the immutable connection. The helper owns it until shutdown. */
void cisco_catalogue_close(struct cisco_catalogue *cat) { cat->db = NULL; }

static int key_valid(const char *s)
{
	return s && strspn(s, "abcdefghijklmnopqrstuvwxyz0123456789") &&
	    strspn(s, "abcdefghijklmnopqrstuvwxyz0123456789._:-") == strlen(s);
}

static int row_identity(const struct cisco_row *row, const jv *meta)
{
	const jv *parent = json_get(meta, "parts_of"), *part = json_get(meta, "part");
	int64_t ordinal, part_number;
	return meta && meta->n == 22 &&
	    !lib_args(meta, "catalogue_id", "reader_pack", "reader_generation", "article_id",
	        "article_ordinal", "doc_key", "doc_id", "doc_type", "title", "product",
	        "subtype", "subtype_evidence", "publisher_label", "release", "part", "parts",
	        "parts_of", "source_coverage", "taxonomy_path", "has_figures", "has_outline",
	        "gap_flags", NULL) &&
	    same(json_str(meta, "catalogue_id"), row->catalogue_id) &&
	    same(json_str(meta, "reader_pack"), row->reader_pack) &&
	    same(json_str(meta, "reader_generation"), row->reader_generation) &&
	    same(json_str(meta, "article_id"), row->article_id) &&
	    !e26j_i64(json_get(meta, "article_ordinal"), 0, INT32_MAX, &ordinal) &&
	    ordinal == row->ordinal && same(json_str(meta, "doc_key"), row->doc_key) &&
	    same(json_str(meta, "doc_type"), "cisco") && same(json_str(meta, "subtype"), row->subtype) &&
	    same(json_str(meta, "title"), row->title) &&
	    parent && (row->parts_of ? same(json_str(meta, "parts_of"), row->parts_of) : parent->type == J_NULL) &&
	    part && (row->part < 0 ? part->type == J_NULL :
	        !e26j_i64(part, 0, INT32_MAX, &part_number) && part_number == row->part);
}

const char *cisco_row_verify(const struct cisco_catalogue *cat, sqlite3_stmt *st,
                             struct cisco_row *row, char **result)
{
	static const char *const mismatch = "The Cisco item does not match its public reader identity.";
	const char *code, *doc_id;
	char id[CISCO_ID_MAX + 1];
	int64_t ordinal, part;
	jv *meta;
	int ok;

	row->catalogue_id = text_column(st, 0, CISCO_ID_MAX, 0);
	row->reader_pack = text_column(st, 1, 10, 0);
	row->reader_generation = text_column(st, 2, 128, 0);
	row->article_id = text_column(st, 3, CISCO_ID_MAX, 0);
	ordinal = sqlite3_column_int64(st, 4);
	row->doc_key = text_column(st, 5, 4096, 0);
	row->subtype = text_column(st, 6, 20, 0);
	row->parts_of = text_column(st, 7, 4096, 0);
	row->record_json = text_column(st, 8, MAX_RECORD_BYTES, 0);
	row->sort_key = text_column(st, 9, CISCO_SORT_MAX, 1);
	part = sqlite3_column_type(st, 10) == SQLITE_NULL ? -1 : sqlite3_column_int64(st, 10);
	row->title = text_column(st, 11, 4096, 0);
	if (sqlite3_column_type(st, 4) != SQLITE_INTEGER || ordinal < 0 || ordinal > INT32_MAX ||
	    (sqlite3_column_type(st, 10) != SQLITE_NULL &&
	     (sqlite3_column_type(st, 10) != SQLITE_INTEGER || part < 0 || part > INT32_MAX)) ||
	    (!row->parts_of && sqlite3_column_type(st, 7) != SQLITE_NULL) ||
	    !row->doc_key || !row->title || !row->sort_key || !row->subtype || !row->record_json)
		return lib_err(result, "E_PACK", "A Cisco catalogue row is invalid.");
	row->store = cisco_store_number(row->reader_pack);
	row->ordinal = (int)ordinal;
	row->part = (int)part;
	if (row->store < 0 || !key_valid(row->article_id) ||
	    snprintf(id, sizeof id, "%s:%s", row->reader_pack, row->article_id) >= (int)sizeof id ||
	    !same(id, row->catalogue_id) ||
	    (row->store == 1 && (row->part < 0 ||
	     !same(row->parts_of, row->part > 0 ? row->doc_key : NULL))) ||
	    !same(row->reader_generation, cat->store[row->store].expected))
		return lib_err(result, "E_STALE", mismatch);
	if ((code = cisco_store_ready(cat, row->store, result))) return code;
	meta = e26j_parse(row->record_json, strlen(row->record_json), NULL, 0);
	doc_id = json_str(meta, "doc_id");
	if (!doc_id || !*doc_id || strlen(doc_id) > 4096 || !row_identity(row, meta)) {
		json_free(meta);
		return lib_err(result, "E_STALE", mismatch);
	}
	ok = lib_cisco_identity(cat->store[row->store].index, (uint32_t)row->ordinal, row->article_id,
	    row->doc_key, doc_id, row->part);
	json_free(meta);
	return ok == 1 ? NULL : ok < 0 ? lib_sqlerr(result) : lib_err(result, "E_STALE", mismatch);
}

const char *cisco_row_get(const struct cisco_catalogue *cat, const char *id, sqlite3_stmt **keep,
                         struct cisco_row *row, char **result)
{
	sqlite3_stmt *st = NULL;
	const char *code;
	int rc;
	*keep = NULL;
	if (!key_valid(id)) return lib_err(result, "E_ARGUMENT", NULL);
	if (sqlite3_prepare_v2(cat->db, "SELECT " CISCO_COLUMNS " FROM catalogue WHERE catalogue_id=?1",
	    -1, &st, NULL) != SQLITE_OK || sqlite3_bind_text(st, 1, id, -1, SQLITE_STATIC) != SQLITE_OK) {
		sqlite3_finalize(st);
		return lib_sqlerr(result);
	}
	rc = sqlite3_step(st);
	code = rc == SQLITE_ROW ? cisco_row_verify(cat, st, row, result) :
	    rc == SQLITE_DONE ? lib_err(result, "E_NOT_FOUND", NULL) : lib_sqlerr(result);
	if (code) sqlite3_finalize(st); else *keep = st;
	return code;
}

static const char *q_status(const jv *args, char **result)
{
	struct cisco_catalogue cat;
	const char *code;
	e26j_buf b;
	if (lib_args(args, NULL)) return lib_err(result, "E_ARGUMENT", NULL);
	if ((code = cisco_catalogue_open(&cat, args, 0, result))) return code;
	e26j_init(&b);
	e26j_lit(&b, "{\"catalogue_id\":\"cisco-public\",\"catalogue_generation\":");
	e26j_cstr(&b, cat.generation);
	e26j_lit(&b, ",\"stores\":"); cisco_stores_json(&cat, &b);
	e26j_lit(&b, "}");
	cisco_catalogue_close(&cat);
	return lib_ok(&b, result);
}

static const char *q_verify(const jv *args, char **result)
{
	struct cisco_catalogue cat;
	struct cisco_row row;
	sqlite3_stmt *st;
	const char *id, *code;
	e26j_buf b;
	if (lib_args(args, "catalogue_generation", "catalogue_id", NULL) ||
	    lib_arg_str(args, "catalogue_id", CISCO_ID_MAX, 0, &id))
		return lib_err(result, "E_ARGUMENT", NULL);
	if ((code = cisco_catalogue_open(&cat, args, 1, result))) return code;
	code = cisco_row_get(&cat, id, &st, &row, result);
	if (!code) {
		e26j_init(&b);
		e26j_lit(&b, "{\"row\":"); e26j_lit(&b, row.record_json);
		e26j_lit(&b, "}");
		code = lib_ok(&b, result);
	}
	sqlite3_finalize(st);
	cisco_catalogue_close(&cat);
	return code;
}

struct cisco_reader {
	struct cisco_catalogue cat;
	struct cisco_row row;
	sqlite3_stmt *entry;
};

static void reader_close(struct cisco_reader *r)
{
	sqlite3_finalize(r->entry);
	cisco_catalogue_close(&r->cat);
}

static const char *reader_open(struct cisco_reader *r, const jv *args, char **result)
{
	const char *id, *pack, *generation, *article, *code;
	memset(r, 0, sizeof *r);
	if (lib_arg_str(args, "catalogue_id", CISCO_ID_MAX, 0, &id) ||
	    lib_arg_str(args, "reader_pack", 10, 0, &pack) || cisco_store_number(pack) < 0 ||
	    lib_arg_str(args, "reader_generation", 128, 0, &generation) ||
	    lib_arg_str(args, "article_id", CISCO_ID_MAX, 0, &article))
		return lib_err(result, "E_ARGUMENT", NULL);
	if ((code = cisco_catalogue_open(&r->cat, args, 1, result)) ||
	    (code = cisco_row_get(&r->cat, id, &r->entry, &r->row, result))) return code;
	if (!same(pack, r->row.reader_pack) || !same(generation, r->row.reader_generation) ||
	    !same(article, r->row.article_id)) {
		reader_close(r);
		return lib_err(result, "E_STALE", "The requested Cisco reader identity has changed.");
	}
	return NULL;
}

static const char *q_sections(const jv *args, char **result)
{
	enum { ANCHOR_MAX = 16384 };
	static const char *const COUNT =
		"SELECT count(*) FROM (SELECT 1 FROM anchor WHERE article_ordinal=?1 LIMIT ?2)";
	static const char *const PAGE =
		"SELECT anchor,title,level,block_index FROM ("
		"SELECT anchor,title,level,block_index,row_number() OVER "
		"(PARTITION BY block_index ORDER BY level,anchor) AS compatibility_rank "
		"FROM anchor WHERE article_ordinal=?1 AND block_index>?2) "
		"WHERE compatibility_rank=1 ORDER BY block_index LIMIT ?3";
	struct cisco_reader r;
	sqlite3_stmt *st = NULL;
	const char *code;
	int64_t cursor, limit, last = -1;
	int rc, count, n = 0;
	e26j_buf b;

	if (lib_args(args, "catalogue_generation", "catalogue_id", "reader_pack", "reader_generation",
	        "article_id", "cursor", "limit", NULL) ||
	    lib_arg_int(args, "cursor", 0, INT32_MAX, -1, &cursor) ||
	    lib_arg_int(args, "limit", 1, 100, 32, &limit)) /* u14 (M17): up to 100 sections a page (was 64) */
		return lib_err(result, "E_ARGUMENT", NULL);
	if ((code = reader_open(&r, args, result))) return code;
	lib_select(r.cat.store[r.row.store].index);
	st = lib_stmt(LIB_CATALOGUE, COUNT);
	if (!st || sqlite3_bind_int(st, 1, r.row.ordinal) != SQLITE_OK ||
	    sqlite3_bind_int(st, 2, ANCHOR_MAX + 1) != SQLITE_OK || sqlite3_step(st) != SQLITE_ROW)
		goto sql_failure;
	count = sqlite3_column_int(st, 0);
	sqlite3_reset(st);
	if (count > ANCHOR_MAX) {
		code = lib_err(result, "E_TOO_LARGE", "This Cisco article's outline exceeds the supported section limit.");
		goto done;
	}
	st = lib_stmt(LIB_CATALOGUE, PAGE);
	if (!st || sqlite3_bind_int(st, 1, r.row.ordinal) != SQLITE_OK ||
	    sqlite3_bind_int64(st, 2, cursor) != SQLITE_OK ||
	    sqlite3_bind_int64(st, 3, limit + 1) != SQLITE_OK) goto sql_failure;
	e26j_init(&b);
	e26j_lit(&b, "{\"rows\":[");
	while ((rc = sqlite3_step(st)) == SQLITE_ROW && n < limit) {
		const char *anchor = text_column(st, 0, 128, 0);
		const char *title = text_column(st, 1, 4096, 0);
		int level = sqlite3_column_int(st, 2);
		int64_t block = sqlite3_column_int64(st, 3);
		if (!anchor || !title || level < 1 || level > 6 || block <= cursor || block > INT32_MAX ||
		    (last >= 0 && block <= last)) {
			e26j_free(&b);
			code = lib_err(result, "E_PACK", "The Cisco article's outline is invalid.");
			goto done;
		}
		if (n++) e26j_lit(&b, ",");
		e26j_lit(&b, "{\"anchor\":"); e26j_cstr(&b, anchor);
		e26j_lit(&b, ",\"title\":"); e26j_cstr(&b, title);
		e26j_lit(&b, ",\"level\":"); e26j_i(&b, level);
		e26j_lit(&b, ",\"block_index\":"); e26j_i(&b, block);
		e26j_lit(&b, "}");
		last = block;
	}
	if (rc != SQLITE_ROW && rc != SQLITE_DONE) { e26j_free(&b); goto sql_failure; }
	e26j_lit(&b, "],\"next\":");
	if (rc == SQLITE_ROW) e26j_i(&b, last); else e26j_lit(&b, "null");
	e26j_lit(&b, "}");
	code = lib_ok(&b, result);
	goto done;
sql_failure:
	code = lib_sqlerr(result);
done:
	if (st) {
		sqlite3_reset(st);
		sqlite3_clear_bindings(st);
	}
	lib_select(0);
	reader_close(&r);
	return code;
}

static int public_mark(const char *value) { return !value || !*value || !strcmp(value, "PUBLIC"); }

static int body_identity(const struct cisco_row *row)
{
	const char *code;
	const lib_article *a = lib_article_get((uint32_t)row->ordinal, &code);
	jv *meta = a && a->meta ? e26j_parse(a->meta, strlen(a->meta), NULL, 0) : NULL;
	const jv *corpus = json_get(meta, "corpus");
	int ok = same(json_str(meta, "id"), row->article_id) &&
	    public_mark(json_str(meta, "marking")) &&
	    public_mark(json_str(json_get(meta, "provenance"), "marking")) &&
	    public_mark(json_str(corpus, "handling"));
	json_free(meta);
	return ok;
}

/* Reuse the legacy reader result shape and limits on the already verified, explicitly selected pack. */
static const char *q_body(const jv *args, char **result, int section)
{
	struct cisco_reader r;
	const e26_helper_op *reader;
	const char *code, *anchor = NULL;
	int64_t start, limit;
	char *keys[] = { "id", NULL, NULL, NULL };
	jv id = { .type = J_STR };
	jv *values[] = { &id, NULL, NULL, NULL };
	jv narrowed = { .type = J_OBJ, .keys = keys, .items = values, .n = 1 };
	static const char *const optional[] = { "start", "limit", "anchor" };
	size_t i;

	if (lib_args(args, "catalogue_generation", "catalogue_id", "reader_pack", "reader_generation",
	        "article_id", "start", "limit", section ? "anchor" : NULL, NULL) ||
	    lib_arg_page(args, 128, 256, &start, &limit) ||
	    (section && lib_arg_str(args, "anchor", 128, 0, &anchor)))
		return lib_err(result, "E_ARGUMENT", NULL);
	if ((code = reader_open(&r, args, result))) return code;
	id.str = (char *)r.row.article_id;
	for (i = 0; i < sizeof optional / sizeof optional[0]; i++) {
		const jv *v = lib_arg(args, optional[i]);
		if (v) {
			keys[narrowed.n] = (char *)optional[i];
			values[narrowed.n++] = (jv *)v;
		}
	}
	reader = reg_impl(REG_QUERY, reg_find(REG_QUERY, section ? "section" : "article"));
	lib_select(r.cat.store[r.row.store].index);
	code = reader ? reader->run(&narrowed, result) : lib_err(result, "E_UNSUPPORTED", NULL);
	if (!code && !body_identity(&r.row)) {
		free(*result);
		code = lib_err(result, "E_STALE", "The Cisco article body does not match its public reader identity.");
	}
	lib_select(0);
	reader_close(&r);
	return code;
}

static const char *q_article(const jv *args, char **result) { return q_body(args, result, 0); }
static const char *q_section(const jv *args, char **result) { return q_body(args, result, 1); }
#else
void lib_cisco_set_path(const char *path, int explicit_path) { (void)path; (void)explicit_path; }
#endif

void e26_q_cisco(void)
{
#if defined(E26_LIB_SQLITE)
	static const e26_helper_op status = { q_status }, verify = { q_verify },
	    page = { lib_cisco_page }, search = { lib_cisco_search }, sections = { q_sections },
	    article = { q_article }, section = { q_section };
	reg_add(REG_QUERY, "finder.cisco.status", &status, "q_cisco", 0);
	reg_add(REG_QUERY, "finder.cisco.verify", &verify, "q_cisco", 0);
	reg_add(REG_QUERY, "finder.cisco.page", &page, "q_cisco", 0);
	reg_add(REG_QUERY, "finder.cisco.search", &search, "q_cisco", 0);
	reg_add(REG_QUERY, "finder.cisco.sections", &sections, "q_cisco", 0);
	reg_add(REG_QUERY, "finder.cisco.article", &article, "q_cisco", 0);
	reg_add(REG_QUERY, "finder.cisco.section", &section, "q_cisco", 0);
#endif
}
