/* Bounded Cisco catalogue browsing and probes of the installed reader FTS. */
#include "document_route.h"

#if defined(E26_LIB_SQLITE)
#include "query.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* u14 (readability spec M17): a page holds up to 100 publications or parts (was 50); the client asks for 100 */
enum { PAGE_MAX = 100, SEARCH_MAX = 200, RESULT_MAX = 8388608 - 1024 };
enum cisco_order { ORDER_TITLE, ORDER_ARTICLE_ID, ORDER_CATALOGUE_ID };

#define TOP_HEAD(key) "SELECT " CISCO_COLUMNS " FROM catalogue WHERE parts_of IS NULL " \
	"AND (" key ",catalogue_id)>(?1,?2) AND (?4 IS NULL OR reader_pack=?4)"
#define TOP_TAIL(key) " ORDER BY " key ",catalogue_id LIMIT ?3"
#define TOP_QUERIES(key) { \
	TOP_HEAD(key) TOP_TAIL(key), \
	TOP_HEAD(key) " AND subtype=?5" TOP_TAIL(key), \
	TOP_HEAD(key) " AND area_id=?6 AND (?5 IS NULL OR subtype=?5)" TOP_TAIL(key), \
	TOP_HEAD(key) " AND topic_id=?6 AND (?5 IS NULL OR subtype=?5)" TOP_TAIL(key) \
}

static const char *const top_sql[][4] = {
	[ORDER_TITLE] = TOP_QUERIES("sort_key"),
	[ORDER_ARTICLE_ID] = TOP_QUERIES("article_id")
};

static const char child_sql[] = "SELECT " CISCO_COLUMNS " FROM catalogue "
	"WHERE reader_pack=?1 AND reader_generation=?2 AND parts_of=?3 "
	"AND (part,catalogue_id)>(?4,?5) AND (?7 IS NULL OR subtype=?7) "
	"AND (?8 IS NULL OR area_id=?8) AND (?9 IS NULL OR topic_id=?9) "
	"ORDER BY part,catalogue_id LIMIT ?6";

static const char search_sql[] = "SELECT " CISCO_COLUMNS " FROM catalogue "
	"WHERE (?1 IS NULL OR reader_pack=?1) AND (parts_of IS NOT NULL OR "
	"((?2 IS NULL OR subtype=?2) AND (?3 IS NULL OR area_id=?3) AND (?4 IS NULL OR topic_id=?4))) "
	"ORDER BY catalogue_id LIMIT ?5";

#define RANKED "SELECT " CISCO_COLUMNS " FROM catalogue WHERE parts_of IS NULL ORDER BY "
static const char *const ranked_sql[] = {
	[ORDER_TITLE] = RANKED "sort_key,catalogue_id LIMIT ?1",
	[ORDER_ARTICLE_ID] = RANKED "article_id,catalogue_id LIMIT ?1",
	[ORDER_CATALOGUE_ID] = RANKED "catalogue_id LIMIT ?1"
};

static const char contents_sql[] = "SELECT " CISCO_COLUMNS ","
	"(?4 IS NULL OR subtype=?4) AND (?5 IS NULL OR area_id=?5) AND (?6 IS NULL OR topic_id=?6) "
	"FROM catalogue "
	"WHERE reader_pack=?1 AND reader_generation=?2 AND doc_key=?3 "
	"AND part=0 AND parts_of IS NULL LIMIT 2";

static const char *const store_names[] = { "lib-e26", "lib-corpus" };

struct scope {
	const char *pack, *subtype, *area, *topic;
	int store;
};

static int valid_subtype(const char *value)
{
	return !value || !strcmp(value, "ConfigGuide") || !strcmp(value, "WhitePaper") ||
	    !strcmp(value, "TechNote") || !strcmp(value, "ValidatedDesign") || !strcmp(value, "Other");
}

static int bind_text(sqlite3_stmt *st, int pos, const char *value)
{
	return value ? sqlite3_bind_text(st, pos, value, -1, SQLITE_STATIC) : sqlite3_bind_null(st, pos);
}

static int parse_scope(const jv *args, struct scope *scope)
{
	scope->store = -1;
	return (lib_arg(args, "reader_pack") && (lib_arg_str(args, "reader_pack", 32, 0, &scope->pack) ||
	    (scope->store = cisco_store_number(scope->pack)) < 0)) ||
	    (lib_arg(args, "subtype") && lib_arg_str(args, "subtype", 32, 0, &scope->subtype)) ||
	    !valid_subtype(scope->subtype) ||
	    (lib_arg(args, "area_id") && lib_arg_str(args, "area_id", CISCO_SORT_MAX, 0, &scope->area)) ||
	    (lib_arg(args, "topic_id") && lib_arg_str(args, "topic_id", CISCO_SORT_MAX, 0, &scope->topic)) ||
	    (scope->area && scope->topic);
}

static const char *parse_order(const jv *args, enum cisco_order *order, char **result)
{
	const char *name;
	if (!lib_arg(args, "sort")) return NULL;
	if (lib_arg_str(args, "sort", 16, 0, &name)) return lib_err(result, "E_ARGUMENT", NULL);
	if (!strcmp(name, "title")) *order = ORDER_TITLE;
	else if (!strcmp(name, "id")) *order = ORDER_ARTICLE_ID;
	else if (!strcmp(name, "date"))
		return lib_err(result, "E_ARGUMENT", "Publication dates are not available in the verified Cisco catalogue.");
	else return lib_err(result, "E_ARGUMENT", NULL);
	return NULL;
}

static const char *order_key(const struct cisco_row *row, enum cisco_order order)
{
	return order == ORDER_ARTICLE_ID ? row->article_id : row->sort_key;
}

static int narrow_scope(const struct cisco_catalogue *cat, struct scope *scope)
{
	int e26, corpus;
	if (scope->pack) return 1;
	e26 = cisco_store_available(cat, 0);
	corpus = cisco_store_available(cat, 1);
	if (!e26 && !corpus) return 0;
	if (e26 != corpus) {
		scope->store = corpus ? 1 : 0;
		scope->pack = store_names[scope->store];
	}
	return 1;
}

static int same_parent(const char *a, const char *b)
{
	return a && b ? !strcmp(a, b) : a == b;
}

static const char *check_cursor_taxonomy(const struct cisco_catalogue *cat, const struct scope *scope,
	const char *id, char **result)
{
	static const char sql[] = "SELECT 1 FROM catalogue WHERE catalogue_id=?1 "
		"AND (?2 IS NULL OR area_id=?2) AND (?3 IS NULL OR topic_id=?3)";
	sqlite3_stmt *st = NULL;
	int rc;

	if (!scope->area && !scope->topic) return NULL;
	rc = sqlite3_prepare_v2(cat->db, sql, -1, &st, NULL);
	if (rc == SQLITE_OK && bind_text(st, 1, id) == SQLITE_OK &&
	    bind_text(st, 2, scope->area) == SQLITE_OK && bind_text(st, 3, scope->topic) == SQLITE_OK)
		rc = sqlite3_step(st);
	sqlite3_finalize(st);
	if (rc == SQLITE_ROW) return NULL;
	if (rc == SQLITE_DONE) return lib_err(result, "E_ARGUMENT", "The cursor is outside the requested catalogue scope.");
	return lib_sqlerr(result);
}

static const char *finish(e26j_buf *b, const char *code, char **result)
{
	if (!code && b->n > RESULT_MAX) code = lib_err(result, "E_TOO_LARGE", NULL);
	if (!code) return lib_ok(b, result);
	e26j_free(b);
	return code;
}

static const char *append_row(e26j_buf *b, const struct cisco_row *row, int64_t *count, char **result)
{
	if (*count) e26j_lit(b, ",");
	e26j_lit(b, row->record_json);
	if (b->bad) return lib_sqlerr(result);
	++*count;
	return b->n > RESULT_MAX ? lib_err(result, "E_TOO_LARGE", NULL) : NULL;
}

const char *lib_cisco_page(const jv *args, char **result)
{
	struct cisco_catalogue cat = {0};
	struct scope scope = {0};
	struct cisco_row row;
	sqlite3_stmt *st = NULL, *parent_st = NULL, *cursor_st = NULL;
	const jv *cursor_arg = lib_arg(args, "cursor");
	const char *parent = NULL, *cursor = "", *sort = "", *parts_of = NULL, *parent_gen = NULL, *code;
	char next_id[CISCO_ID_MAX + 1] = "", next_sort[CISCO_SORT_MAX + 1] = "";
	enum cisco_order order = ORDER_TITLE;
	e26j_buf b;
	int64_t limit, count = 0;
	int part = -1, more = 0, rc, filter;

	if (lib_args(args, "catalogue_generation", "reader_pack", "subtype", "area_id", "topic_id", "parent", "cursor", "limit", "sort", NULL) ||
	    parse_scope(args, &scope) ||
	    (lib_arg(args, "parent") && lib_arg_str(args, "parent", CISCO_ID_MAX, 0, &parent)) ||
	    (cursor_arg && (lib_args(cursor_arg, "sort_key", "catalogue_id", NULL) ||
	    lib_arg_str(cursor_arg, "sort_key", CISCO_SORT_MAX, 1, &sort) ||
	    lib_arg_str(cursor_arg, "catalogue_id", CISCO_ID_MAX, 0, &cursor))) ||
	    lib_arg_int(args, "limit", 1, PAGE_MAX, PAGE_MAX, &limit)) return lib_err(result, "E_ARGUMENT", NULL);
	if ((code = parse_order(args, &order, result))) return code;
	/* The Contents' children always follow part order and keep the original cursor key. */
	if (parent) order = ORDER_TITLE;

	e26j_init(&b);
	if ((code = cisco_catalogue_open(&cat, args, 1, result))) goto out;
	if (scope.store >= 0 && (code = cisco_store_ready(&cat, scope.store, result))) goto out;
	if (parent) {
		if ((code = cisco_row_get(&cat, parent, &parent_st, &row, result))) goto out;
		if (row.parts_of || (scope.store >= 0 && scope.store != row.store)) {
			code = lib_err(result, "E_ARGUMENT", "The parent is outside the requested catalogue scope.");
			goto out;
		}
		scope.store = row.store;
		scope.pack = row.reader_pack;
		parts_of = row.doc_key;
		parent_gen = row.reader_generation;
	}
	if (cursor_arg) {
		if ((code = cisco_row_get(&cat, cursor, &cursor_st, &row, result))) goto out;
		if ((scope.store >= 0 && scope.store != row.store) || !same_parent(parts_of, row.parts_of) ||
		    (parent && row.part < 0) || strcmp(sort, order_key(&row, order)) ||
		    (scope.subtype && strcmp(scope.subtype, row.subtype))) {
			code = lib_err(result, "E_ARGUMENT", "The cursor is outside the requested catalogue scope.");
			goto out;
		}
		part = row.part;
		if ((code = check_cursor_taxonomy(&cat, &scope, cursor, result))) goto out;
	}
	e26j_lit(&b, "{\"rows\":[");
	if (!narrow_scope(&cat, &scope)) goto reply;
	if (parent) {
		if (sqlite3_prepare_v2(cat.db, child_sql, -1, &st, NULL) != SQLITE_OK ||
		    bind_text(st, 1, scope.pack) != SQLITE_OK || bind_text(st, 2, parent_gen) != SQLITE_OK ||
		    bind_text(st, 3, parts_of) != SQLITE_OK || sqlite3_bind_int(st, 4, part) != SQLITE_OK ||
		    bind_text(st, 5, cursor) != SQLITE_OK || sqlite3_bind_int64(st, 6, limit + 1) != SQLITE_OK ||
		    bind_text(st, 7, scope.subtype) != SQLITE_OK || bind_text(st, 8, scope.area) != SQLITE_OK ||
		    bind_text(st, 9, scope.topic) != SQLITE_OK) goto failed;
	} else {
		filter = scope.topic ? 3 : scope.area ? 2 : scope.subtype ? 1 : 0;
		if (sqlite3_prepare_v2(cat.db, top_sql[order][filter], -1, &st, NULL) != SQLITE_OK ||
		    bind_text(st, 1, sort) != SQLITE_OK || bind_text(st, 2, cursor) != SQLITE_OK ||
		    sqlite3_bind_int64(st, 3, limit + 1) != SQLITE_OK || bind_text(st, 4, scope.pack) != SQLITE_OK ||
		    (filter && bind_text(st, 5, scope.subtype) != SQLITE_OK) ||
		    (filter >= 2 && bind_text(st, 6, scope.area ? scope.area : scope.topic) != SQLITE_OK)) goto failed;
	}
	while ((rc = sqlite3_step(st)) == SQLITE_ROW) {
		const char *key;
		size_t id_len, sort_len;
		lib_poll();
		if (lib_interrupted()) goto failed;
		if ((code = cisco_row_verify(&cat, st, &row, result))) goto out;
		if (count == limit) { more = 1; break; }
		key = order_key(&row, order);
		id_len = strlen(row.catalogue_id);
		sort_len = strlen(key);
		if (id_len > CISCO_ID_MAX || sort_len > CISCO_SORT_MAX) goto failed;
		memcpy(next_id, row.catalogue_id, id_len + 1);
		memcpy(next_sort, key, sort_len + 1);
		if ((code = append_row(&b, &row, &count, result))) goto out;
	}
	if (!more && rc != SQLITE_DONE) goto failed;

reply:
	e26j_lit(&b, "],\"catalogue_generation\":"); e26j_cstr(&b, cat.generation);
	e26j_lit(&b, ",\"next\":");
	if (more) {
		e26j_lit(&b, "{\"sort_key\":"); e26j_cstr(&b, next_sort);
		e26j_lit(&b, ",\"catalogue_id\":"); e26j_cstr(&b, next_id); e26j_lit(&b, "}");
	} else e26j_lit(&b, "null");
	e26j_lit(&b, ",\"stores\":"); cisco_stores_json(&cat, &b); e26j_lit(&b, "}");
	goto out;
failed:
	code = lib_sqlerr(result);
out:
	sqlite3_finalize(st);
	sqlite3_finalize(cursor_st);
	sqlite3_finalize(parent_st);
	cisco_catalogue_close(&cat);
	lib_select(0);
	return finish(&b, code, result);
}

static const char *parse_match(const char *query, char *match, size_t size, char **result)
{
	/* e26-lib serializes requests, so the numeric protocol code can stay here. */
	static char numeric[16];
	e26q_ast *ast = NULL;
	e26q_err err = {0};
	int rc;

	if (e26q_parse(query, 0, 8, &ast, &err)) {
		e26q_free(ast);
		snprintf(numeric, sizeof numeric, "%d", err.code);
		return lib_err(result, numeric, err.message);
	}
	rc = e26q_to_fts5(ast, match, size);
	e26q_free(ast);
	return rc < 0 ? lib_err(result, "E_TOO_LARGE", NULL) : NULL;
}

struct search_hit { char id[CISCO_ID_MAX + 1]; };

static int compare_hit_id(const void *id, const void *hit)
{
	return strcmp(id, ((const struct search_hit *)hit)->id);
}

static int compare_hits(const void *a, const void *b)
{
	return compare_hit_id(((const struct search_hit *)a)->id, b);
}

static const char *verify_contents(const struct cisco_catalogue *cat, const struct cisco_row *parent,
	const struct cisco_row *part, char **result)
{
	static const char *const family =
		"SELECT 1 FROM corpus_doc p JOIN corpus_doc c ON c.article_ordinal=?2 "
		"WHERE p.article_ordinal=?1 AND p.contents=1 AND p.part=0 AND p.parts>1 "
		"AND c.contents=0 AND c.part=?3 AND c.part BETWEEN 1 AND p.parts "
		"AND c.parts=p.parts AND c.doc_key=p.doc_key AND c.doc_id=p.doc_id";
	sqlite3_stmt *st;
	int rc = SQLITE_ERROR;

	lib_select(cat->store[part->store].index);
	st = lib_stmt(LIB_CATALOGUE, family);
	if (st) {
		if (sqlite3_bind_int(st, 1, parent->ordinal) == SQLITE_OK &&
		    sqlite3_bind_int(st, 2, part->ordinal) == SQLITE_OK &&
		    sqlite3_bind_int(st, 3, part->part) == SQLITE_OK)
			rc = sqlite3_step(st);
		sqlite3_reset(st);
		sqlite3_clear_bindings(st);
	}
	lib_select(0);
	if (rc == SQLITE_ROW) return NULL;
	if (rc == SQLITE_DONE) return lib_err(result, "E_STALE",
	    "The Cisco part does not match its public Contents article.");
	return lib_sqlerr(result);
}

static const char *canonical_hit(const struct cisco_catalogue *cat, sqlite3_stmt **lookup,
	const struct scope *scope, const struct cisco_row *part, struct search_hit *hit, char **result)
{
	struct cisco_row parent;
	const char *code;
	int rc;

	hit->id[0] = '\0';
	if (!part->parts_of) {
		memcpy(hit->id, part->catalogue_id, strlen(part->catalogue_id) + 1);
		return NULL;
	}
	if (strcmp(part->reader_pack, "lib-corpus") || part->part < 1)
		return lib_err(result, "E_STALE", "The Cisco part has no verified public Contents article.");
	if (!*lookup && sqlite3_prepare_v2(cat->db, contents_sql, -1, lookup, NULL) != SQLITE_OK)
		return lib_sqlerr(result);
	if (bind_text(*lookup, 1, part->reader_pack) != SQLITE_OK ||
	    bind_text(*lookup, 2, part->reader_generation) != SQLITE_OK ||
	    bind_text(*lookup, 3, part->parts_of) != SQLITE_OK ||
	    bind_text(*lookup, 4, scope->subtype) != SQLITE_OK ||
	    bind_text(*lookup, 5, scope->area) != SQLITE_OK ||
	    bind_text(*lookup, 6, scope->topic) != SQLITE_OK) return lib_sqlerr(result);
	rc = sqlite3_step(*lookup);
	if (rc == SQLITE_ROW) {
		code = cisco_row_verify(cat, *lookup, &parent, result);
		if (!code) code = verify_contents(cat, &parent, part, result);
		if (!code) {
			if (sqlite3_column_int(*lookup, sqlite3_column_count(*lookup) - 1))
				memcpy(hit->id, parent.catalogue_id, strlen(parent.catalogue_id) + 1);
			rc = sqlite3_step(*lookup);
			if (rc == SQLITE_ROW)
				code = lib_err(result, "E_STALE", "The Cisco part has ambiguous Contents articles.");
			else if (rc != SQLITE_DONE) code = lib_sqlerr(result);
		}
	} else {
		code = rc == SQLITE_DONE ? lib_err(result, "E_STALE",
		    "The Cisco part has no verified public Contents article.") : lib_sqlerr(result);
	}
	sqlite3_reset(*lookup);
	sqlite3_clear_bindings(*lookup);
	return code;
}

const char *lib_cisco_search(const jv *args, char **result)
{
	static const char probe_sql[] = "SELECT rowid FROM docs WHERE docs MATCH ?1 AND rowid=?2";
	struct cisco_catalogue cat = {0};
	struct scope scope = {0};
	struct cisco_row row;
	sqlite3_stmt *st = NULL, *contents = NULL, *entry = NULL, *probes[2] = {NULL, NULL};
	struct search_hit *hits = NULL;
	const char *query, *code;
	char match[8192];
	enum cisco_order order = ORDER_CATALOGUE_ID;
	e26j_buf b;
	int64_t limit, count = 0;
	size_t n = 0;
	int store, candidates = 0, more = 0, rc, hit;

	if (lib_args(args, "catalogue_generation", "reader_pack", "query", "subtype", "area_id", "topic_id", "limit", "sort", NULL) ||
	    parse_scope(args, &scope) ||
	    lib_arg_str(args, "query", 4096, 0, &query) ||
	    lib_arg_int(args, "limit", 1, SEARCH_MAX, SEARCH_MAX, &limit)) return lib_err(result, "E_ARGUMENT", NULL);
	if ((code = parse_order(args, &order, result))) return code;

	e26j_init(&b);
	if ((code = cisco_catalogue_open(&cat, args, 1, result)) ||
	    (scope.store >= 0 && (code = cisco_store_ready(&cat, scope.store, result))) ||
	    (code = parse_match(query, match, sizeof match, result))) goto out;

	e26j_lit(&b, "{\"rows\":[");
	if (!narrow_scope(&cat, &scope)) goto reply;
	hits = malloc(CISCO_MAX_ROWS * sizeof *hits);
	if (!hits) goto failed;
	for (store = 0; store < 2; store++) {
		sqlite3 *search_db;
		if (scope.store >= 0 && scope.store != store) continue;
		lib_select(cat.store[store].index);
		search_db = lib_db(LIB_SEARCH);
		lib_select(0);
		if (!search_db || sqlite3_prepare_v2(search_db, probe_sql, -1, &probes[store], NULL) != SQLITE_OK ||
		    bind_text(probes[store], 1, match) != SQLITE_OK) goto failed;
	}
	/* The sidecar bounds the probes. Resolve all matching parts before sorting
	 * and capping, since a Contents ID can sort after a part that matched. */
	if (sqlite3_prepare_v2(cat.db, search_sql, -1, &st, NULL) != SQLITE_OK ||
	    bind_text(st, 1, scope.pack) != SQLITE_OK || bind_text(st, 2, scope.subtype) != SQLITE_OK ||
	    bind_text(st, 3, scope.area) != SQLITE_OK || bind_text(st, 4, scope.topic) != SQLITE_OK ||
	    sqlite3_bind_int(st, 5, CISCO_MAX_ROWS + 1) != SQLITE_OK) goto failed;
	while ((rc = sqlite3_step(st)) == SQLITE_ROW) {
		sqlite3_stmt *probe;
		lib_poll();
		if (lib_interrupted() || ++candidates > CISCO_MAX_ROWS) goto failed;
		if ((code = cisco_row_verify(&cat, st, &row, result))) goto out;
		if (row.store < 0 || row.store >= 2 || !(probe = probes[row.store]) ||
		    sqlite3_bind_int64(probe, 2, (sqlite3_int64)row.ordinal + 1) != SQLITE_OK)
			goto failed;
		hit = sqlite3_step(probe);
		if (sqlite3_reset(probe) != SQLITE_OK || (hit != SQLITE_ROW && hit != SQLITE_DONE)) goto failed;
		if (hit == SQLITE_DONE) continue;
		if ((code = canonical_hit(&cat, &contents, &scope, &row, &hits[n], result))) goto out;
		if (hits[n].id[0]) n++;
	}
	if (rc != SQLITE_DONE) goto failed;
	if (!n) goto reply;
	qsort(hits, n, sizeof *hits, compare_hits);
	/* Only the bounded sidecar is scanned in display order. Each canonical
	 * row is visited once, even if several matching parts promoted to it. */
	if (sqlite3_prepare_v2(cat.db, ranked_sql[order], -1, &entry, NULL) != SQLITE_OK ||
	    sqlite3_bind_int(entry, 1, CISCO_MAX_ROWS) != SQLITE_OK) goto failed;
	while ((rc = sqlite3_step(entry)) == SQLITE_ROW) {
		const char *id = (const char *)sqlite3_column_text(entry, 0);
		lib_poll();
		if (lib_interrupted() || !id) goto failed;
		if (!bsearch(id, hits, n, sizeof *hits, compare_hit_id)) continue;
		if (count == limit) { more = 1; break; }
		if ((code = cisco_row_verify(&cat, entry, &row, result))) goto out;
		if ((code = append_row(&b, &row, &count, result))) goto out;
	}
	if (!more && rc != SQLITE_DONE) goto failed;
reply:
	e26j_lit(&b, "],\"count\":"); e26j_i(&b, count);
	e26j_lit(&b, more ? ",\"refine\":true,\"stores\":" : ",\"refine\":false,\"stores\":");
	cisco_stores_json(&cat, &b);
	e26j_lit(&b, "}");
	goto out;
failed:
	code = lib_sqlerr(result);
out:
	free(hits);
	sqlite3_finalize(contents);
	sqlite3_finalize(entry);
	sqlite3_finalize(probes[0]);
	sqlite3_finalize(probes[1]);
	sqlite3_finalize(st);
	cisco_catalogue_close(&cat);
	lib_select(0);
	return finish(&b, code, result);
}
#endif
