/* q_links.c: an article's neighbours and the dictionary (W1-DATA; LIB-PROTOCOL.md "related, references and
 * backlinks", "updates", "facts and moreinfo", "dictionary"). Unheld targets stay visible with available=false;
 * status chains are walked at query time with cycle and depth reporting, never bridged. */
#include "lib.h"
#include "../app/registry.h"
#include "../common/helper.h"
#include <stdlib.h>
#include <string.h>

void e26_q_links(void);

#if defined(E26_LIB_SQLITE)
#define MAXCHAINS 4096

/* ,"available":true when column col (a target ordinal) is not null */
static const char *avail_col(sqlite3_stmt *st, int col)
{
	return sqlite3_column_type(st, col) == SQLITE_NULL ? ",\"available\":false" : ",\"available\":true";
}
static const char *avail2(sqlite3_stmt *st) { return avail_col(st, 2); }
static const char *avail7(sqlite3_stmt *st) { return avail_col(st, 7); }

/* id, start and limit; then the canonical ordinal and id */
static const char *head(const jv *args, char **result, uint32_t *ord, char **id, int64_t *start, int64_t *limit, const char *extra_key)
{
	if (lib_args(args, "id", "start", "limit", extra_key, NULL) || lib_arg_page(args, 32, 256, start, limit)) return lib_err(result, "E_ARGUMENT", NULL);
	return lib_resolve(args, "id", ord, id, result);
}

static const char *paged(lib_list *l, int64_t start, int64_t limit, char **result)
{
	e26j_buf b;
	e26j_init(&b);
	e26j_lit(&b, "{");
	lib_list_page(&b, l, start, limit);
	e26j_lit(&b, "}");
	lib_list_free(l);
	return lib_ok(&b, result);
}

static const char *q_related(const jv *args, char **result)
{
	static const char *const q0 = "SELECT source_ordinal,target_id,target_ordinal,reason,weight FROM related WHERE source_ordinal=?1 "
	                              "ORDER BY weight DESC,target_id,reason";
	/* storage revision 2 (h2-index SIZE-PASS "Related query"): the explicit rows and the other primary members of the
	 * source's primary subcategory at weight 10; the highest row per target, by weight then target id */
	static const char *const q1 =
		"WITH candidates AS (SELECT source_ordinal,target_id,target_ordinal,reason,weight FROM related WHERE source_ordinal=?1 "
		"UNION ALL SELECT ?1,a.id,a.ordinal,'same_category',10 FROM fac.article_category ac JOIN cat.article a ON a.ordinal=ac.article_ordinal "
		"WHERE ac.category_id=(SELECT primary_category FROM cat.article WHERE ordinal=?1) AND ac.is_primary=1 AND ac.article_ordinal<>?1), "
		"ranked AS (SELECT *,row_number() OVER (PARTITION BY target_id ORDER BY weight DESC,reason) AS rank FROM candidates) "
		/* u14 M13: the target's title as the row's label (the reader's list shows titles, not ids); without the
		 * catalogue attached (q0) the reader humanises the id */
		"SELECT source_ordinal,target_id,target_ordinal,reason,weight,"
		"(SELECT t.title FROM cat.article t WHERE t.id=ranked.target_id) AS label "
		"FROM ranked WHERE rank=1 ORDER BY weight DESC,target_id";
	const char *const q = lib_lazy_related() ? q1 : q0;
	uint32_t ord;
	char *id = NULL, *last = NULL;
	const char *code;
	int64_t start, limit;
	sqlite3_stmt *st;
	lib_list l;
	int rc;
	if ((code = head(args, result, &ord, &id, &start, &limit, NULL))) return code;
	free(id);
	if (!(st = lib_stmt(LIB_LINKS, q))) return lib_err(result, "E_PACK", NULL);
	sqlite3_bind_int64(st, 1, ord);
	memset(&l, 0, sizeof l);
	while ((rc = sqlite3_step(st)) == SQLITE_ROW) {
		const char *t = (const char *)sqlite3_column_text(st, 1);
		if (!t) { rc = SQLITE_CORRUPT; break; }
		if (last && !strcmp(last, t)) continue;       /* the highest-ranked row per target only */
		free(last);
		last = strdup(t);
		if (!last || lib_list_add(&l, st, avail2(st))) { rc = SQLITE_CORRUPT; break; }
	}
	sqlite3_reset(st);
	free(last);
	if (rc != SQLITE_DONE) { lib_list_free(&l); return rc == SQLITE_CORRUPT ? lib_err(result, "E_PACK", NULL) : lib_sqlerr(result); }
	return paged(&l, start, limit, result);
}

#define REFCOLS "SELECT source_ordinal,reference_id,kind,position,label_json,target_json,target_id,target_ordinal FROM reference "

static const char *q_references(const jv *args, char **result)
{
	static const char *const all = REFCOLS "WHERE source_ordinal=?1 ORDER BY CASE kind WHEN 'normative' THEN 0 "
	                               "WHEN 'informative' THEN 1 ELSE 2 END,position,reference_id";
	static const char *const one = REFCOLS "WHERE source_ordinal=?1 AND kind=?2 ORDER BY position,reference_id";
	uint32_t ord;
	char *id = NULL;
	const char *code, *kind = NULL;
	int64_t start, limit;
	sqlite3_stmt *st;
	lib_list l;
	if (lib_arg(args, "kind") && (lib_arg_str(args, "kind", 32, 0, &kind) ||
	    (strcmp(kind, "normative") && strcmp(kind, "informative") && strcmp(kind, "further-reading"))))
		return lib_err(result, "E_ARGUMENT", NULL);
	if ((code = head(args, result, &ord, &id, &start, &limit, "kind"))) return code;
	free(id);
	if (!(st = lib_stmt(LIB_LINKS, kind ? one : all))) return lib_err(result, "E_PACK", NULL);
	sqlite3_bind_int64(st, 1, ord);
	if (kind) sqlite3_bind_text(st, 2, kind, -1, SQLITE_STATIC);
	memset(&l, 0, sizeof l);
	if ((code = lib_list_all(&l, st, avail7, result))) { lib_list_free(&l); return code; }
	return paged(&l, start, limit, result);
}

static const char *q_backlinks(const jv *args, char **result)
{
	static const char *const q = REFCOLS "WHERE target_id=?1 ORDER BY source_ordinal,position,reference_id";
	uint32_t ord;
	char *id = NULL;
	const char *code;
	int64_t start, limit;
	sqlite3_stmt *st;
	lib_list l;
	if ((code = head(args, result, &ord, &id, &start, &limit, NULL))) return code;
	if (!(st = lib_stmt(LIB_LINKS, q))) { free(id); return lib_err(result, "E_PACK", NULL); }
	sqlite3_bind_text(st, 1, id, -1, SQLITE_TRANSIENT);
	free(id);
	memset(&l, 0, sizeof l);
	if ((code = lib_list_all(&l, st, avail7, result))) { lib_list_free(&l); return code; }
	return paged(&l, start, limit, result);
}

/* ---- updates */
typedef struct { e26j_buf *b; int nchains, depth; const char *rel; const char *err; } walk;

/* the targets of id under relation, in target order (malloc'd array of malloc'd strings) */
static int edges(const char *id, const char *rel, char ***out, size_t *n)
{
	static const char *const qo = "SELECT ordinal FROM article WHERE id=?1";
	static const char *const qe = "SELECT target_id FROM status_edge WHERE source_ordinal=?1 AND relation=?2 ORDER BY target_id";
	sqlite3_stmt *st = lib_stmt(LIB_CATALOGUE, qo);
	int64_t ord;
	int rc;
	size_t cap = 0;
	*out = NULL;
	*n = 0;
	if (!st) return -1;
	sqlite3_bind_text(st, 1, id, -1, SQLITE_STATIC);
	rc = sqlite3_step(st);
	ord = sqlite3_column_int64(st, 0);
	sqlite3_reset(st);
	if (rc == SQLITE_DONE) return 0;                  /* an unheld id: a leaf */
	if (rc != SQLITE_ROW || !(st = lib_stmt(LIB_LINKS, qe))) return -1;
	sqlite3_bind_int64(st, 1, ord);
	sqlite3_bind_text(st, 2, rel, -1, SQLITE_STATIC);
	while ((rc = sqlite3_step(st)) == SQLITE_ROW) {
		const char *t = (const char *)sqlite3_column_text(st, 0);
		if (*n == cap) {
			char **np = (char **)realloc(*out, (cap = cap ? cap * 2 : 8) * sizeof *np);
			if (!np) { rc = SQLITE_NOMEM; break; }
			*out = np;
		}
		if (!t || !((*out)[*n] = strdup(t))) { rc = SQLITE_NOMEM; break; }
		(*n)++;
	}
	sqlite3_reset(st);
	return rc == SQLITE_DONE ? 0 : -1;
}

static void chain(walk *w, char **path, size_t n, int cycle, int truncated)
{
	size_t i;
	if (++w->nchains > MAXCHAINS) { w->err = "E_TOO_LARGE"; return; }
	if (w->nchains > 1) e26j_lit(w->b, ",");
	e26j_lit(w->b, "{\"relation\":");
	e26j_cstr(w->b, w->rel);
	e26j_lit(w->b, ",\"path\":[");
	for (i = 0; i < n; i++) { if (i) e26j_lit(w->b, ","); e26j_cstr(w->b, path[i]); }
	e26j_lit(w->b, "],\"cycle\":");
	e26j_lit(w->b, cycle ? "true" : "false");
	e26j_lit(w->b, ",\"truncated\":");
	e26j_lit(w->b, truncated ? "true" : "false");
	e26j_lit(w->b, "}");
}

static void follow(walk *w, char **path, size_t n)
{
	char **t;
	size_t m, i, k;
	if (w->err) return;
	if (edges(path[n - 1], w->rel, &t, &m)) { w->err = lib_interrupted() == 1 ? "E_CANCELLED" : lib_interrupted() == 2 ? "E_TIMEOUT" : "E_PACK"; return; }
	if (!m) chain(w, path, n, 0, 0);
	else if ((int)n - 1 >= w->depth) chain(w, path, n, 0, 1);
	else for (i = 0; i < m && !w->err; i++) {
		path[n] = t[i];
		for (k = 0; k < n && strcmp(path[k], t[i]); k++) ;
		if (k < n) chain(w, path, n + 1, 1, 0);
		else follow(w, path, n + 1);
	}
	for (i = 0; i < m; i++) free(t[i]);
	free(t);
}

static const char *q_updates(const jv *args, char **result)
{
	static const char *const qs = "SELECT json_extract(e26_unzstd(meta_json),'$.status') FROM article WHERE ordinal=?1";
	static const char *const RELS[] = { "obsoleted_by", "obsoletes", "updated_by", "updates" };
	uint32_t ord;
	char *id = NULL, *path[66];
	const char *code, *status;
	int64_t depth;
	sqlite3_stmt *st;
	e26j_buf b;
	walk w;
	size_t r;
	if (lib_args(args, "id", "max_depth", NULL) || lib_arg_int(args, "max_depth", 1, 64, 32, &depth)) return lib_err(result, "E_ARGUMENT", NULL);
	if ((code = lib_resolve(args, "id", &ord, &id, result))) return code;
	if (!(st = lib_stmt(LIB_CATALOGUE, qs))) { free(id); return lib_err(result, "E_PACK", NULL); }
	sqlite3_bind_int64(st, 1, ord);
	if (sqlite3_step(st) != SQLITE_ROW || !(status = (const char *)sqlite3_column_text(st, 0)) ||
	    e26j_valid(status, strlen(status), NULL, 0) || *status != '{') {
		sqlite3_reset(st);
		free(id);
		return lib_err(result, "E_PACK", NULL);
	}
	e26j_init(&b);
	e26j_lit(&b, "{\"id\":");
	e26j_cstr(&b, id);
	e26j_lit(&b, ",\"status\":");
	e26j_lit(&b, status);
	sqlite3_reset(st);
	e26j_lit(&b, ",\"chains\":[");
	memset(&w, 0, sizeof w);
	w.b = &b;
	w.depth = (int)depth;
	path[0] = id;
	for (r = 0; r < 4 && !w.err; r++) {
		char **t;
		size_t m, i;
		w.rel = RELS[r];
		if (edges(id, w.rel, &t, &m)) { w.err = "E_PACK"; break; }
		for (i = 0; i < m; i++) free(t[i]);
		free(t);
		if (m) follow(&w, path, 1);
	}
	free(id);
	if (w.err) { e26j_free(&b); return lib_err(result, w.err, NULL); }
	e26j_lit(&b, "]}");
	return lib_ok(&b, result);
}

static int any(int db, const char *sql, int64_t ord)
{
	sqlite3_stmt *st = lib_stmt(db, sql);
	int rc;
	if (!st) return -1;
	sqlite3_bind_int64(st, 1, ord);
	rc = sqlite3_step(st);
	sqlite3_reset(st);
	return rc == SQLITE_ROW ? 1 : rc == SQLITE_DONE ? 0 : -1;
}

static const char *q_moreinfo(const jv *args, char **result)
{
	static const char *const qm =
		"SELECT json_extract(m,'$.updated'),json_extract(m,'$.provenance.source_url'),"
		"json_array_length(m,'$.status.errata')+json_array_length(m,'$.status.obsoletes')+"
		"json_array_length(m,'$.status.obsoleted_by')+json_array_length(m,'$.status.updates')+"
		"json_array_length(m,'$.status.updated_by') FROM (SELECT e26_unzstd(meta_json) AS m FROM article WHERE ordinal=?1)";
	static const char *const qr0 = "SELECT 1 FROM related WHERE source_ordinal=?1 LIMIT 1";
	static const char *const qr1 = "SELECT 1 FROM related WHERE source_ordinal=?1 UNION ALL SELECT 1 FROM fac.article_category ac "
		"WHERE ac.category_id=(SELECT primary_category FROM cat.article WHERE ordinal=?1) AND ac.is_primary=1 AND ac.article_ordinal<>?1 LIMIT 1";
	const char *const qr = lib_lazy_related() ? qr1 : qr0;
	static const char *const qf = "SELECT 1 FROM reference WHERE source_ordinal=?1 LIMIT 1";
	static const char *const qp = "SELECT 1 FROM article_place WHERE article_ordinal=?1 LIMIT 1";
	uint32_t ord;
	char *id = NULL;
	const char *code, *updated, *url;
	sqlite3_stmt *st;
	e26j_buf b;
	int rel, ref, atlas, upd;
	if (lib_args(args, "id", NULL)) return lib_err(result, "E_ARGUMENT", NULL);
	if ((code = lib_resolve(args, "id", &ord, &id, result))) return code;
	rel = any(LIB_LINKS, qr, ord);
	ref = any(LIB_LINKS, qf, ord);
	atlas = any(LIB_FACETS, qp, ord);
	if (rel < 0 || ref < 0 || atlas < 0 || !(st = lib_stmt(LIB_CATALOGUE, qm))) { free(id); return lib_sqlerr(result); }
	sqlite3_bind_int64(st, 1, ord);
	if (sqlite3_step(st) != SQLITE_ROW || !(url = (const char *)sqlite3_column_text(st, 1))) { sqlite3_reset(st); free(id); return lib_err(result, "E_PACK", NULL); }
	updated = (const char *)sqlite3_column_text(st, 0);
	upd = updated || sqlite3_column_int64(st, 2) > 0;
	e26j_init(&b);
	e26j_lit(&b, "{\"id\":");
	e26j_cstr(&b, id);
	e26j_lit(&b, ",\"pages\":{\"related\":");
	e26j_lit(&b, rel ? "true" : "false");
	e26j_lit(&b, ",\"references\":");
	e26j_lit(&b, ref ? "true" : "false");
	e26j_lit(&b, ",\"updates\":");
	e26j_lit(&b, upd ? "true" : "false");
	e26j_lit(&b, ",\"web_links\":");
	e26j_lit(&b, *url ? "true" : "false");
	e26j_lit(&b, ",\"ask\":false,\"atlas\":");
	e26j_lit(&b, atlas ? "true" : "false");
	e26j_lit(&b, "},\"source_url\":");
	e26j_cstr(&b, url);
	e26j_lit(&b, ",\"updated\":");
	e26j_cstr(&b, updated);
	e26j_lit(&b, "}");
	sqlite3_reset(st);
	free(id);
	return lib_ok(&b, result);
}

/* ---- the dictionary */
#define TERMCOLS "SELECT term_id,term,sort_key,definition_json,article_ordinal,anchor,class FROM term "
#define TERMORDER " ORDER BY CASE class WHEN 'corpus' THEN 0 ELSE 1 END,sort_key,term_id"

static const char *q_dictionary(const jv *args, char **result)
{
	static const char *const exact = TERMCOLS "WHERE sort_key=?1" TERMORDER " LIMIT ?3 OFFSET ?4";
	static const char *const pre = TERMCOLS "WHERE sort_key>=?1 AND sort_key<?2" TERMORDER " LIMIT ?3 OFFSET ?4";
	static const char *const nexact = "SELECT count(*) FROM term WHERE sort_key=?1";
	static const char *const npre = "SELECT count(*) FROM term WHERE sort_key>=?1 AND sort_key<?2";
	static const char *const rel = "SELECT target_term,relation FROM term_relation WHERE term_id=?1 ORDER BY target_term,relation";
	const char *term;
	char *fold, *hi;
	int prefix, rc, k = 0;
	int64_t start, limit, total;
	sqlite3_stmt *st, *rs;
	e26j_buf b;
	size_t n;
	if (lib_args(args, "term", "prefix", "start", "limit", NULL) || lib_arg_bool(args, "prefix", 0, &prefix) ||
	    lib_arg_page(args, 32, 256, &start, &limit))
		return lib_err(result, "E_ARGUMENT", NULL);
	if (lib_arg_str(args, "term", 4096, prefix, &term)) return lib_err(result, "E_ARGUMENT", NULL);
	if (!lib_ready(result)) return lib_code();
	if (!(fold = lib_fold(term))) return lib_err(result, "E_ARGUMENT", NULL);
	n = strlen(fold);
	if (!(hi = (char *)malloc(n + 2))) { free(fold); return lib_err(result, "E_PACK", NULL); }
	memcpy(hi, fold, n);
	hi[n] = (char)0xFF;
	hi[n + 1] = 0;
	if (!(st = lib_stmt(LIB_DICTIONARY, prefix ? npre : nexact))) { free(fold); free(hi); return lib_err(result, "E_PACK", NULL); }
	sqlite3_bind_text(st, 1, fold, (int)n, SQLITE_TRANSIENT);
	if (prefix) sqlite3_bind_text(st, 2, hi, (int)n + 1, SQLITE_TRANSIENT);
	rc = sqlite3_step(st);
	total = sqlite3_column_int64(st, 0);
	sqlite3_reset(st);
	if (rc != SQLITE_ROW || !(st = lib_stmt(LIB_DICTIONARY, prefix ? pre : exact))) { free(fold); free(hi); return lib_sqlerr(result); }
	sqlite3_bind_text(st, 1, fold, (int)n, SQLITE_TRANSIENT);
	if (prefix) sqlite3_bind_text(st, 2, hi, (int)n + 1, SQLITE_TRANSIENT);
	free(fold);
	free(hi);
	sqlite3_bind_int64(st, 3, limit);
	sqlite3_bind_int64(st, 4, start);
	e26j_init(&b);
	e26j_lit(&b, "{\"rows\":[");
	while ((rc = sqlite3_step(st)) == SQLITE_ROW) {
		e26j_buf r;
		char *rj;
		int rr, nr = 0;
		if (!(rs = lib_stmt(LIB_DICTIONARY, rel))) { rc = SQLITE_ERROR; break; }
		sqlite3_bind_text(rs, 1, (const char *)sqlite3_column_text(st, 0), -1, SQLITE_TRANSIENT);
		e26j_init(&r);
		e26j_lit(&r, ",\"related_terms\":[");
		while ((rr = sqlite3_step(rs)) == SQLITE_ROW) {
			if (nr++) e26j_lit(&r, ",");
			if (lib_row(&r, rs, NULL)) { rr = SQLITE_CORRUPT; break; }
		}
		sqlite3_reset(rs);
		e26j_lit(&r, "]");
		if (rr != SQLITE_DONE || !(rj = e26j_take(&r))) { e26j_free(&r); rc = SQLITE_CORRUPT; break; }
		if (k++) e26j_lit(&b, ",");
		rr = lib_row(&b, st, rj);
		free(rj);
		if (rr) { rc = SQLITE_CORRUPT; break; }
	}
	sqlite3_reset(st);
	if (rc != SQLITE_DONE) { e26j_free(&b); return rc == SQLITE_CORRUPT ? lib_err(result, "E_PACK", NULL) : lib_sqlerr(result); }
	e26j_lit(&b, "],\"start\":");
	e26j_i(&b, start);
	e26j_lit(&b, ",\"next\":");
	if (start + k < total) e26j_i(&b, start + k); else e26j_lit(&b, "null");
	e26j_lit(&b, ",\"total\":");
	e26j_i(&b, total);
	e26j_lit(&b, "}");
	return lib_ok(&b, result);
}
#endif

void e26_q_links(void)
{
#if defined(E26_LIB_SQLITE)
	static const e26_helper_op related = { q_related }, references = { q_references }, backlinks = { q_backlinks },
	                           updates = { q_updates }, moreinfo = { q_moreinfo }, dictionary = { q_dictionary };
	reg_add(REG_QUERY, "related", &related, "q_links", 0);
	reg_add(REG_QUERY, "references", &references, "q_links", 0);
	reg_add(REG_QUERY, "backlinks", &backlinks, "q_links", 0);
	reg_add(REG_QUERY, "updates", &updates, "q_links", 0);
	reg_add(REG_QUERY, "moreinfo", &moreinfo, "q_links", 0);
	reg_add(REG_QUERY, "dictionary", &dictionary, "q_links", 0);
#endif
}
