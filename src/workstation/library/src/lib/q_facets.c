/* q_facets.c: filters and curated lists (W1-DATA; LIB-PROTOCOL.md "facets", "facts and moreinfo", "study_lists and
 * tours"). A facet answer is a bitmap over the article ordinals: values OR within a dimension, dimensions AND;
 * categories and places include their descendants through the stored hierarchy; years intersect inclusively. */
#include "lib.h"
#include "../app/registry.h"
#include "../common/helper.h"
#include <stdlib.h>
#include <string.h>

void e26_q_facets(void);

#if defined(E26_LIB_SQLITE)
typedef struct {
	const char *key;
	const char *check;          /* SQL proving a value exists, or NULL for a fixed vocabulary */
	const char *const *vocab;
	const char *members;        /* SQL giving the article ordinals of one value */
} dim;

static const char *const KINDS[] = { "image", "maths", "pdf", "cli", "table", "audio", "video", "interactivity", NULL };
static const char *const FLAGS[] = { "updated", "has_errata", "obsoleted", "has_references", "factbox", NULL };

static const dim DIMS[] = {
	{ "categories", "SELECT 1 FROM category WHERE id=?1", NULL,
	  "WITH RECURSIVE chosen(id) AS (SELECT ?1 UNION SELECT v.id FROM category v JOIN chosen c ON v.parent_id=c.id) "
	  "SELECT a.article_ordinal FROM article_category a JOIN chosen c ON a.category_id=c.id" },
	{ "media_kinds", NULL, KINDS, "SELECT article_ordinal FROM article_media WHERE kind=?1" },
	{ "places", "SELECT 1 FROM place WHERE id=?1", NULL,
	  "WITH RECURSIVE chosen(id) AS (SELECT ?1 UNION SELECT v.id FROM place v JOIN chosen c ON v.parent_id=c.id) "
	  "SELECT a.article_ordinal FROM article_place a JOIN chosen c ON a.place_id=c.id" },
	{ "flags", NULL, FLAGS, "SELECT article_ordinal FROM article_flag WHERE flag=?1" },
};

/* ORs the ordinals sql gives (one text parameter, or two integers) into bits; 0, or an error code */
static const char *collect(const char *sql, const char *text, int64_t a, int64_t b, unsigned char *bits, char **result)
{
	sqlite3_stmt *st = lib_stmt(LIB_FACETS, sql);
	int rc;
	if (!st) return lib_err(result, "E_PACK", NULL);
	if (text) sqlite3_bind_text(st, 1, text, -1, SQLITE_STATIC);
	else { sqlite3_bind_int64(st, 1, a); sqlite3_bind_int64(st, 2, b); }
	while ((rc = sqlite3_step(st)) == SQLITE_ROW) {
		int64_t o = sqlite3_column_int64(st, 0);
		if (o < 0 || o >= (int64_t)lib_ndocs()) { sqlite3_reset(st); return lib_err(result, "E_PACK", NULL); }
		lib_setbit(bits, (uint32_t)o);
	}
	sqlite3_reset(st);
	return rc == SQLITE_DONE ? NULL : lib_sqlerr(result);
}

static int exists(int db, const char *sql, const char *text)
{
	sqlite3_stmt *st = lib_stmt(db, sql);
	int rc;
	if (!st) return -1;
	sqlite3_bind_text(st, 1, text, -1, SQLITE_STATIC);
	rc = sqlite3_step(st);
	sqlite3_reset(st);
	return rc == SQLITE_ROW ? 1 : rc == SQLITE_DONE ? 0 : -1;
}

static const char *q_facets(const jv *args, char **result)
{
	size_t nb, i, d, k, m;
	unsigned char *sel, *one = NULL;
	const char *code = NULL;
	e26j_buf b;
	if (lib_args(args, "categories", "media_kinds", "year_min", "year_max", "places", "flags", NULL)) return lib_err(result, "E_ARGUMENT", NULL);
	if (!lib_ready(result)) return lib_code();
	nb = lib_bitmap_bytes();
	if (!(sel = (unsigned char *)malloc(nb + 1)) || !(one = (unsigned char *)malloc(nb + 1))) { free(sel); return lib_err(result, "E_PACK", NULL); }
	memset(sel, 0xFF, nb);
	if (lib_ndocs() % 8 && nb) sel[nb - 1] = (unsigned char)((1u << (lib_ndocs() % 8)) - 1);
	for (d = 0; d < sizeof DIMS / sizeof DIMS[0] && !code; d++) {
		const jv *v = lib_arg(args, DIMS[d].key);
		if (!v) continue;
		if (v->type != J_ARR || v->n > 256) { code = lib_err(result, "E_ARGUMENT", NULL); break; }
		for (k = 0; k < v->n && !code; k++) {
			const jv *x = v->items[k];
			if (x->type != J_STR || !x->str[0] || strlen(x->str) > 256) { code = lib_err(result, "E_ARGUMENT", NULL); break; }
			for (m = 0; m < k; m++) if (!strcmp(v->items[m]->str, x->str)) break;
			if (m < k) { code = lib_err(result, "E_ARGUMENT", NULL); break; }
			if (DIMS[d].vocab) {
				for (m = 0; DIMS[d].vocab[m] && strcmp(DIMS[d].vocab[m], x->str); m++) ;
				if (!DIMS[d].vocab[m]) code = lib_err(result, "E_ARGUMENT", NULL);
			} else {
				int e = exists(LIB_FACETS, DIMS[d].check, x->str);
				if (e < 0) code = lib_sqlerr(result);
				else if (!e) code = lib_err(result, "E_ARGUMENT", NULL);
			}
		}
		if (code || !v->n) continue;
		memset(one, 0, nb);
		for (k = 0; k < v->n && !code; k++) code = collect(DIMS[d].members, v->items[k]->str, 0, 0, one, result);
		for (i = 0; i < nb; i++) sel[i] &= one[i];
	}
	if (!code && (lib_arg(args, "year_min") || lib_arg(args, "year_max"))) {
		static const char *const q = "SELECT article_ordinal FROM article_year WHERE year_min<=?2 AND year_max>=?1";
		int64_t lo, hi;
		if (lib_arg_int(args, "year_min", -999999, 999999, -999999, &lo) || lib_arg_int(args, "year_max", -999999, 999999, 999999, &hi) || lo > hi)
			code = lib_err(result, "E_ARGUMENT", NULL);
		else {
			memset(one, 0, nb);
			if (!(code = collect(q, NULL, lo, hi, one, result))) for (i = 0; i < nb; i++) sel[i] &= one[i];
		}
	}
	free(one);
	if (code) { free(sel); return code; }
	e26j_init(&b);
	e26j_lit(&b, "{\"bitmap\":");
	lib_bitmap_out(&b, sel);
	e26j_lit(&b, ",\"count\":");
	e26j_u(&b, lib_popcount(sel));
	e26j_lit(&b, "}");
	free(sel);
	return lib_ok(&b, result);
}

static const char *q_facts(const jv *args, char **result)
{
	static const char *const q =
		"SELECT article_ordinal,fact_id,box,position,label,runs_json,cite_json FROM fact WHERE article_ordinal=?1 "
		"ORDER BY CAST(substr(fact_id,1,instr(fact_id,':')-1) AS INTEGER),CAST(substr(fact_id,instr(fact_id,':')+1) AS INTEGER),fact_id";
	uint32_t ord;
	char *id = NULL;
	const char *code;
	sqlite3_stmt *st;
	lib_list l;
	e26j_buf b;
	size_t i;
	if (lib_args(args, "id", NULL)) return lib_err(result, "E_ARGUMENT", NULL);
	if ((code = lib_resolve(args, "id", &ord, &id, result))) return code;
	if (!(st = lib_stmt(LIB_FACETS, q))) { free(id); return lib_err(result, "E_PACK", NULL); }
	sqlite3_bind_int64(st, 1, ord);
	memset(&l, 0, sizeof l);
	if ((code = lib_list_all(&l, st, NULL, result))) { free(id); lib_list_free(&l); return code; }
	e26j_init(&b);
	e26j_lit(&b, "{\"id\":");
	e26j_cstr(&b, id);
	e26j_lit(&b, ",\"rows\":[");
	for (i = 0; i < l.n; i++) { if (i) e26j_lit(&b, ","); e26j_lit(&b, l.row[i]); }
	e26j_lit(&b, "]}");
	free(id);
	lib_list_free(&l);
	return lib_ok(&b, result);
}

/* study_lists and tours: every row, or one with its members in position order */
static const char *paths(const jv *args, char **result, const char *all, const char *one, const char *members, const char *member)
{
	const char *id = NULL, *code;
	sqlite3_stmt *st;
	lib_list rows, items;
	e26j_buf b;
	size_t i;
	if (lib_args(args, "id", NULL) || (lib_arg(args, "id") && lib_arg_str(args, "id", 4096, 0, &id))) return lib_err(result, "E_ARGUMENT", NULL);
	if (!lib_ready(result)) return lib_code();
	memset(&rows, 0, sizeof rows);
	memset(&items, 0, sizeof items);
	if (!(st = lib_stmt(LIB_FACETS, id ? one : all))) return lib_err(result, "E_PACK", NULL);
	if (id) sqlite3_bind_text(st, 1, id, -1, SQLITE_STATIC);
	if ((code = lib_list_all(&rows, st, NULL, result))) goto out;
	if (id && !rows.n) { code = lib_err(result, "E_NOT_FOUND", NULL); goto out; }
	if (id) {
		if (!(st = lib_stmt(LIB_FACETS, members))) { code = lib_err(result, "E_PACK", NULL); goto out; }
		sqlite3_bind_text(st, 1, id, -1, SQLITE_STATIC);
		if ((code = lib_list_all(&items, st, NULL, result))) goto out;
	}
	if (rows.n > 256 || items.n > 256) { code = lib_err(result, "E_TOO_LARGE", NULL); goto out; }
	e26j_init(&b);
	e26j_lit(&b, "{\"rows\":[");
	for (i = 0; i < rows.n; i++) { if (i) e26j_lit(&b, ","); e26j_lit(&b, rows.row[i]); }
	e26j_lit(&b, "],\"");
	e26j_lit(&b, member);
	e26j_lit(&b, "\":[");
	for (i = 0; i < items.n; i++) { if (i) e26j_lit(&b, ","); e26j_lit(&b, items.row[i]); }
	e26j_lit(&b, "],\"start\":0,\"next\":null,\"total\":");
	e26j_u(&b, rows.n);
	e26j_lit(&b, "}");
	code = lib_ok(&b, result);
out:
	lib_list_free(&rows);
	lib_list_free(&items);
	return code;
}

static const char *q_study_lists(const jv *args, char **result)
{
	return paths(args, result, "SELECT id,title,category_id,approved,source FROM study_list ORDER BY id",
	             "SELECT id,title,category_id,approved,source FROM study_list WHERE id=?1",
	             "SELECT list_id,position,article_id,anchor FROM study_list_item WHERE list_id=?1 ORDER BY position", "items");
}

static const char *q_tours(const jv *args, char **result)
{
	return paths(args, result, "SELECT id,title,track,approved,source FROM tour ORDER BY id",
	             "SELECT id,title,track,approved,source FROM tour WHERE id=?1",
	             "SELECT tour_id,position,article_id,anchor,instruction,model_step,tutor_topic FROM tour_stop WHERE tour_id=?1 ORDER BY position",
	             "stops");
}
#endif

void e26_q_facets(void)
{
#if defined(E26_LIB_SQLITE)
	static const e26_helper_op facets = { q_facets }, facts = { q_facts }, lists = { q_study_lists }, tours = { q_tours };
	reg_add(REG_QUERY, "facets", &facets, "q_facets", 0);
	reg_add(REG_QUERY, "facts", &facts, "q_facets", 0);
	reg_add(REG_QUERY, "study_lists", &lists, "q_facets", 0);
	reg_add(REG_QUERY, "tours", &tours, "q_facets", 0);
#endif
}
