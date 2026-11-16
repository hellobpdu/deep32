/* q_core.c: e26-lib's status, article, section and media queries (W1-DATA; LIB-PROTOCOL.md "status", "article and
 * section", "media"). Article pages hold the largest prefix of whole blocks whose complete response fits 8 MiB. */
#include "lib.h"
#include "../app/registry.h"
#include "../common/helper.h"
#include <stdlib.h>
#include <string.h>

void e26_q_core(void);

#if defined(E26_LIB_SQLITE)
#define MAXBODY 8388608u

static int64_t count(int db, const char *sql)
{
	sqlite3_stmt *st = lib_stmt(db, sql);
	int64_t n = -1;
	if (st && sqlite3_step(st) == SQLITE_ROW) n = sqlite3_column_int64(st, 0);
	if (st) sqlite3_reset(st);
	return n;
}

static const char *q_status(const jv *args, char **result)
{
	static const char *const qa = "SELECT count(*) FROM article", *const qt = "SELECT count(*) FROM title",
	                         *const qm = "SELECT count(*) FROM media", *const qd = "SELECT count(*) FROM term";
	e26j_buf b;
	const char *p;
	size_t n;
	int64_t a, t, m, d;
	if (lib_args(args, NULL)) return lib_err(result, "E_ARGUMENT", NULL);
	if (!lib_ready(result)) return lib_code();
	a = count(LIB_CATALOGUE, qa); t = count(LIB_CATALOGUE, qt); m = count(LIB_CATALOGUE, qm); d = count(LIB_DICTIONARY, qd);
	if (a < 0 || t < 0 || m < 0 || d < 0) return lib_sqlerr(result);
	p = lib_pack_json();
	for (n = strlen(p); n && (p[n - 1] == '\n' || p[n - 1] == ' '); n--) ;
	e26j_init(&b);
	e26j_lit(&b, "{\"pack\":");
	e26j_raw(&b, p, n);
	e26j_lit(&b, ",\"counts\":{\"articles\":"); e26j_i(&b, a);
	e26j_lit(&b, ",\"titles\":"); e26j_i(&b, t);
	e26j_lit(&b, ",\"media\":"); e26j_i(&b, m);
	e26j_lit(&b, ",\"dictionary\":"); e26j_i(&b, d);
	e26j_lit(&b, "},\"versions\":{\"protocol\":1,\"article\":2,\"schema\":1}");
	/* with corpus packs open beside it: each pack's name, generation, presence, handling and counts */
	if (lib_npacks() > 1) { e26j_lit(&b, ","); lib_packs_json(&b); }
	e26j_lit(&b, "}");
	return lib_ok(&b, result);
}

static size_t digits(uint64_t v) { size_t n = 1; while (v >= 10) { v /= 10; n++; } return n; }

/* {<head>,"blocks":[...],"start":S,"next":N,"total":T}: the largest whole-block prefix of [start, start+limit) whose
 * complete response envelope fits the frame */
static const char *blocks_page(const char *head, char **blk, const size_t *blen, size_t total, int64_t start, int64_t limit,
                               char **result)
{
	e26j_buf b, g;
	size_t s = (size_t)start, e = start + limit < (int64_t)total ? (size_t)(start + limit) : total, k, base, sum = 0;
	if (s > total) s = total;
	if (e < s) e = s;
	e26j_init(&g);
	e26j_cstr(&g, lib_generation());
	/* {"ok":true,"result":{HEAD,"blocks":[],"start":S,"next":,"total":T}},"generation":G} */
	base = strlen("{\"ok\":true,\"result\":{") + strlen(head) + strlen(",\"blocks\":[],\"start\":") + digits((uint64_t)start) +
	       strlen(",\"next\":,\"total\":") + digits(total) + strlen("},\"generation\":") + g.n + 1;
	e26j_free(&g);
	for (k = 0; s + k < e; k++) {
		size_t next = s + k + 1, need = base + sum + blen[s + k] + k + (next < total ? digits(next) : 4);
		if (need > MAXBODY) break;
		sum += blen[s + k];
	}
	if (!k && s < e) return lib_err(result, "E_TOO_LARGE", NULL);
	e26j_init(&b);
	e26j_lit(&b, "{");
	e26j_lit(&b, head);
	e26j_lit(&b, ",\"blocks\":[");
	for (e = 0; e < k; e++) { if (e) e26j_lit(&b, ","); e26j_raw(&b, blk[s + e], blen[s + e]); }
	e26j_lit(&b, "],\"start\":");
	e26j_i(&b, start);
	e26j_lit(&b, ",\"next\":");
	if (s + k < total) e26j_u(&b, s + k); else e26j_lit(&b, "null");
	e26j_lit(&b, ",\"total\":");
	e26j_u(&b, total);
	e26j_lit(&b, "}");
	return lib_ok(&b, result);
}

static const char *q_article(const jv *args, char **result)
{
	uint32_t ord;
	char *id = NULL;
	const char *code;
	const lib_article *a;
	int64_t start, limit;
	e26j_buf h;
	char *head;
	if (lib_args(args, "id", "start", "limit", NULL) || lib_arg_page(args, 128, 256, &start, &limit)) return lib_err(result, "E_ARGUMENT", NULL);
	if ((code = lib_resolve(args, "id", &ord, &id, result))) return code;
	free(id);
	if (!(a = lib_article_get(ord, &code))) return lib_err(result, code, NULL);
	e26j_init(&h);
	e26j_lit(&h, "\"meta\":");
	e26j_lit(&h, a->meta);
	if (!(head = e26j_take(&h))) return lib_err(result, "E_PACK", NULL);
	code = blocks_page(head, a->block, a->blen, a->nblocks, start, limit, result);
	free(head);
	return code;
}

static const char *q_section(const jv *args, char **result)
{
	static const char *const q = "SELECT block_index,end_block FROM anchor WHERE article_ordinal=?1 AND anchor=?2";
	uint32_t ord;
	char *id = NULL, *head;
	const char *code, *anchor;
	const lib_article *a;
	int64_t start, limit, bi, eb;
	sqlite3_stmt *st;
	e26j_buf h;
	int rc;
	if (lib_args(args, "id", "anchor", "start", "limit", NULL) || lib_arg_page(args, 128, 256, &start, &limit) ||
	    lib_arg_str(args, "anchor", 128, 0, &anchor))
		return lib_err(result, "E_ARGUMENT", NULL);
	if ((code = lib_resolve(args, "id", &ord, &id, result))) return code;
	if (!(st = lib_stmt(LIB_CATALOGUE, q))) { free(id); return lib_err(result, "E_PACK", NULL); }
	sqlite3_bind_int64(st, 1, ord);
	sqlite3_bind_text(st, 2, anchor, -1, SQLITE_STATIC);
	rc = sqlite3_step(st);
	bi = sqlite3_column_int64(st, 0);
	eb = sqlite3_column_int64(st, 1);
	sqlite3_reset(st);
	if (rc != SQLITE_ROW) { free(id); return rc == SQLITE_DONE ? lib_err(result, "E_NOT_FOUND", NULL) : lib_sqlerr(result); }
	if (!(a = lib_article_get(ord, &code))) { free(id); return lib_err(result, code, NULL); }
	if (bi < 0 || eb <= bi || (size_t)eb > a->nblocks) { free(id); return lib_err(result, "E_PACK", NULL); }
	e26j_init(&h);
	e26j_lit(&h, "\"id\":");
	e26j_cstr(&h, id);
	e26j_lit(&h, ",\"anchor\":");
	e26j_cstr(&h, anchor);
	free(id);
	if (!(head = e26j_take(&h))) return lib_err(result, "E_PACK", NULL);
	code = blocks_page(head, a->block + bi, a->blen + bi, (size_t)(eb - bi), start, limit, result);
	free(head);
	return code;
}

static const char *q_media(const jv *args, char **result)
{
	static const char *const q = "SELECT id,article_ordinal,kind,pack,path,mime,alt,credit,caption_json,bytes,sha256 FROM media WHERE id=?1";
	const char *id, *mode = "bytes";
	int64_t offset, limit;
	sqlite3_stmt *st;
	e26j_buf b;
	char *pack, *path, *mime;
	int rc, null_bytes;
	int64_t bytes;
	if (lib_args(args, "id", "mode", "offset", "limit", NULL) || lib_arg_str(args, "id", 4096, 0, &id) ||
	    lib_arg_int(args, "offset", 0, INT64_MAX, 0, &offset) || lib_arg_int(args, "limit", 1, 49152, 49152, &limit) ||
	    (lib_arg(args, "mode") && lib_arg_str(args, "mode", 16, 0, &mode)) || (strcmp(mode, "bytes") && strcmp(mode, "render")))
		return lib_err(result, "E_ARGUMENT", NULL);
	if (!lib_ready(result)) return lib_code();
	if (!(st = lib_stmt(LIB_CATALOGUE, q))) return lib_err(result, "E_PACK", NULL);
	sqlite3_bind_text(st, 1, id, -1, SQLITE_STATIC);
	if ((rc = sqlite3_step(st)) != SQLITE_ROW) { sqlite3_reset(st); return rc == SQLITE_DONE ? lib_err(result, "E_NOT_FOUND", NULL) : lib_sqlerr(result); }
	e26j_init(&b);
	e26j_lit(&b, "{\"media\":");
	if (lib_row(&b, st, NULL) || !sqlite3_column_text(st, 3) || !sqlite3_column_text(st, 4) || !sqlite3_column_text(st, 5)) {
		sqlite3_reset(st);
		e26j_free(&b);
		return lib_err(result, "E_PACK", NULL);
	}
	pack = strdup((const char *)sqlite3_column_text(st, 3));
	path = strdup((const char *)sqlite3_column_text(st, 4));
	mime = strdup((const char *)sqlite3_column_text(st, 5));
	null_bytes = sqlite3_column_type(st, 9) == SQLITE_NULL;
	bytes = sqlite3_column_int64(st, 9);
	sqlite3_reset(st);
	if (!pack || !path || !mime) { free(pack); free(path); free(mime); e26j_free(&b); return lib_err(result, "E_PACK", NULL); }
	if (!strcmp(mode, "render")) {
		e26j_lit(&b, ",\"render\":{\"source\":{\"pack\":");
		e26j_cstr(&b, pack);
		e26j_lit(&b, ",\"path\":");
		e26j_cstr(&b, path);
		e26j_lit(&b, "},\"mime\":");
		e26j_cstr(&b, mime);
		e26j_lit(&b, "}}");
	} else {
		unsigned char *data;
		size_t len;
		uint64_t total;
		int r = lib_media_read(pack, path, (uint64_t)offset, (size_t)limit, &data, &len, &total);
		if (r < 0 || (r && !null_bytes && (uint64_t)bytes != total)) {
			free(data); free(pack); free(path); free(mime); e26j_free(&b);
			return lib_err(result, "E_PACK", NULL);
		}
		if (!r) e26j_lit(&b, ",\"available\":false,\"data_b64\":\"\",\"next\":null,\"total\":null}");
		else {
			e26j_lit(&b, ",\"available\":true,\"data_b64\":");
			e26j_b64(&b, data, len);
			e26j_lit(&b, ",\"next\":");
			if ((uint64_t)offset + len < total) e26j_u(&b, (uint64_t)offset + len); else e26j_lit(&b, "null");
			e26j_lit(&b, ",\"total\":");
			e26j_u(&b, total);
			e26j_lit(&b, "}");
		}
		free(data);
	}
	free(pack); free(path); free(mime);
	return lib_ok(&b, result);
}
#endif

void e26_q_core(void)
{
#if defined(E26_LIB_SQLITE)
	static const e26_helper_op status = { q_status }, article = { q_article }, section = { q_section }, media = { q_media };
	reg_add(REG_QUERY, "status", &status, "q_core", 0);
	reg_add(REG_QUERY, "article", &article, "q_core", 0);
	reg_add(REG_QUERY, "section", &section, "q_core", 0);
	reg_add(REG_QUERY, "media", &media, "q_core", 0);
#endif
}
