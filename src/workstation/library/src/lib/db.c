/* db.c: e26-lib's pack (W1-DATA; lib.h, PACK-LAYOUT.md). Opens the generation behind /d32/libcontent/e26 once:
 * PACK.json (name, versions, generation, store), MANIFEST.tsv (every listed file present at its size, PACK.json at
 * its hash; d32-update checked every hash at install), the five SQLite files read-only and immutable with the same
 * generation, the title wheel's ordinals in memory, and the reader store (reader.zim, or reader/ in a fixture).
 * The encyclopedia's ACTIVE file names the layers media may come from; they open on first use.
 *
 * Without SQLite (the Makefile before docs/rooms/DATA-makefile.diff) the pack is never ready and no query is
 * registered, so every Library query is E_UNSUPPORTED, as before W1-DATA. */
#include "lib.h"
#include "q_search.h"
#include "zimshim.h"
#include "../common/sha256.h"
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define MAX_ARTICLE (32u * 1024u * 1024u)
#define MAX_RECORD (1024u * 1024u)
#define NCACHE 4
#define NSTMT 96
#define NLAYER 8

#define NREQ 8

typedef struct { int seq; char file[256], book[128]; void *zim; int tried; } layer;
/* a PACK.json requires entry: {pack, path (relative to the libcontent root), store zim|directory} */
typedef struct { char pack[64], path[256]; int dir; void *zim; int tried; } require;

struct pack {
	char pack[PATH_MAX], enc[PATH_MAX];
	int lazy_related;             /* Related adds the same-primary-subcategory members at query time */
	require req[NREQ];
	int nreq;
	int ready;
	const char *code;
	char msg[200];
	char *pack_json;
	jv *pk;                       /* PACK.json parsed */
	char generation[130];
	char catalogue_hash[65];      /* signed manifest's catalogue.sqlite digest, for Cisco routing */
	char name[64];
	int fixture, zimstore;
	void *zim;                    /* reader.zim */
	uint32_t ndocs, nwheel;
	int32_t *wart;                /* per wheel row: its article ordinal, -1 for a catalogue-only row */
	unsigned char *wflag;         /* per wheel row: 1 alias, 2 catalogue-only */
	layer layers[NLAYER];
	int nlayers;
	lib_article cache[NCACHE];
	uint32_t cache_ord[NCACHE];
	unsigned long cache_use[NCACHE], clock;
#if defined(E26_LIB_SQLITE)
	sqlite3 *db[LIB_NDB];
	struct { int which; const char *sql; sqlite3_stmt *st; } stmt[NSTMT];
	int nstmt;
#endif
};

/* pack 0 is lib_open's; 1 and 2 the corpus packs lib_open_extra adds. D is the selected pack. */
static struct pack PK[LIB_NPACK];
static struct pack *cur = PK;
static int npacks;
static char ROOT[PATH_MAX];
#define D (*cur)
static void lib_close_one(void);

void lib_set_root(const char *root) { snprintf(ROOT, sizeof ROOT, "%s", root ? root : ""); }
const char *lib_root_path(void) { return ROOT; }
int lib_npacks(void) { return npacks ? npacks : 1; }
int lib_lazy_related(void) { return D.lazy_related; }

static void unready(const char *code, const char *why)
{
	D.ready = 0;
	D.code = code;
	snprintf(D.msg, sizeof D.msg, "%s", why);
}

const char *lib_pack_path(void) { return D.pack; }
const char *lib_enc_path(void) { return D.enc; }
const char *lib_code(void) { return D.code ? D.code : "E_PACK"; }
const char *lib_generation(void) { return D.ready ? D.generation : NULL; }
uint32_t lib_ndocs(void) { return D.ndocs; }
uint32_t lib_nwheel(void) { return D.nwheel; }

void lib_wheel_row(uint32_t w, int32_t *article, unsigned *flags)
{
	if (w >= D.nwheel || !D.wart) { *article = -1; *flags = 2; return; }
	*article = D.wart[w];
	*flags = D.wflag[w];
}
const char *lib_pack_json(void) { return D.pack_json; }

const char *lib_features(void)
{
	return "protocol 1.0"
#if defined(E26_LIB_SQLITE)
	       " sqlite"
#endif
#if defined(E26_LIB_ZIM)
	       " zim icu"
#endif
	       ;
}

int lib_ready(char **result)
{
	if (D.ready) return 1;
	lib_err(result, lib_code(), NULL);
	return 0;
}

/* a whole small file (at most max bytes), NUL-terminated; NULL when unreadable or larger */
static char *slurp(const char *path, size_t max, size_t *len)
{
	int fd = open(path, O_RDONLY | O_CLOEXEC);
	struct stat st;
	char *p;
	size_t got = 0;
	if (fd < 0) return NULL;
	if (fstat(fd, &st) || !S_ISREG(st.st_mode) || (uintmax_t)st.st_size > max || !(p = (char *)malloc((size_t)st.st_size + 1))) {
		close(fd);
		return NULL;
	}
	while (got < (size_t)st.st_size) {
		ssize_t r = read(fd, p + got, (size_t)st.st_size - got);
		if (r < 0 && errno == EINTR) continue;
		if (r <= 0) { free(p); close(fd); return NULL; }
		got += (size_t)r;
	}
	close(fd);
	p[got] = 0;
	if (len) *len = got;
	return p;
}

/* a relative POSIX path with no empty, "." or ".." component, no backslash or control character */
static int safe_path(const char *p)
{
	const char *c = p;
	if (!*p || *p == '/') return 0;
	for (; *c; c++) if ((unsigned char)*c < 0x20 || *c == '\\') return 0;
	for (c = p; *c;) {
		size_t n = strcspn(c, "/");
		if (!n || (n == 1 && c[0] == '.') || (n == 2 && c[0] == '.' && c[1] == '.')) return 0;
		c += n;
		if (*c == '/') { c++; if (!*c) return 0; }
	}
	return 1;
}

static int join(char *out, size_t n, const char *dir, const char *rel)
{
	return snprintf(out, n, "%s/%s", dir, rel) < (int)n ? 0 : -1;
}

/* MANIFEST.tsv: every row's file present at its byte count; PACK.json's hash too */
static int manifest(void)
{
	char path[PATH_MAX], *text, *line, *save = NULL;
	int rows = 0, ok = 1, packrow = 0, catrow = 0;
	if (join(path, sizeof path, D.pack, "MANIFEST.tsv") || !(text = slurp(path, 64u << 20, NULL))) return -1;
	for (line = strtok_r(text, "\n", &save); line && ok; line = strtok_r(NULL, "\n", &save)) {
		char *f[6], *s = line, fp[PATH_MAX];
		struct stat st;
		int k;
		if (line[0] == '#') {
			if (!rows && !strncmp(line, "# e26-content-manifest\t", 23) && strcmp(line + 23, "1")) ok = 0;
			continue;
		}
		for (k = 0; k < 6; k++) { f[k] = s; s = strchr(s, '\t'); if (s) *s++ = 0; else if (k < 5) { ok = 0; break; } }
		if (!ok || s || !safe_path(f[0]) || join(fp, sizeof fp, D.pack, f[0]) || stat(fp, &st) || !S_ISREG(st.st_mode) ||
		    strspn(f[1], "0123456789") != strlen(f[1]) || strtoull(f[1], NULL, 10) != (unsigned long long)st.st_size) {
			ok = 0;
			break;
		}
		if (!strcmp(f[0], "PACK.json")) {
			char hex[65];
			sha256_hex(D.pack_json, strlen(D.pack_json), hex);
			packrow = 1;
			if (strcmp(hex, f[2])) ok = 0;
		}
		if (!strcmp(f[0], "catalogue.sqlite")) {
			if (++catrow == 1 && strlen(f[2]) == 64 &&
			    strspn(f[2], "0123456789abcdef") == 64)
				memcpy(D.catalogue_hash, f[2], sizeof D.catalogue_hash);
			else D.catalogue_hash[0] = 0;
		}
		rows++;
	}
	free(text);
	return ok && rows && packrow ? 0 : -1;
}

/* PACK.json: the contract's keys and versions */
static int pack_json(void)
{
	char path[PATH_MAX], err[120];
	const jv *v;
	size_t len;
	if (join(path, sizeof path, D.pack, "PACK.json") || !(D.pack_json = slurp(path, 1u << 20, &len))) { unready("E_PACK", "PACK.json unreadable"); return -1; }
	if (!(D.pk = e26j_parse(D.pack_json, len, err, sizeof err)) || D.pk->type != J_OBJ) { unready("E_PACK", "PACK.json is not JSON"); return -1; }
	v = json_get(D.pk, "name");
	if (!v || v->type != J_STR || (strcmp(v->str, "lib-e26") && strcmp(v->str, "lib-corpus") &&
	    strcmp(v->str, "lib-reserved"))) { unready("E_PACK", "not an Library reader pack"); return -1; }
	snprintf(D.name, sizeof D.name, "%s", v->str);
	{
		static const char *const keys[3] = { "schema", "article_format", "protocol" };
		static const int want[3] = { 1, 2, 1 };
		int i;
		for (i = 0; i < 3; i++) {
			int64_t x;
			if (e26j_i64(json_get(D.pk, keys[i]), 0, 1000, &x) || x != want[i]) { unready("E_VERSION", "unsupported pack version"); return -1; }
		}
	}
	v = json_get(D.pk, "generation");
	if (!v || v->type != J_STR || !v->str[0] || strlen(v->str) > 128) { unready("E_PACK", "no generation"); return -1; }
	snprintf(D.generation, sizeof D.generation, "%s", v->str);
	v = json_get(D.pk, "fixture");
	D.fixture = v && v->type == J_BOOL && v->b;
	v = json_get(D.pk, "reader_store");
	if (!v || v->type != J_STR || (strcmp(v->str, "zim") && strcmp(v->str, "directory"))) { unready("E_PACK", "unknown reader store"); return -1; }
	D.zimstore = !strcmp(v->str, "zim");
	/* requires: the media packs, located by path under the libcontent root (CR-1, CR-1b) */
	v = json_get(D.pk, "requires");
	if (v && v->type == J_ARR) {
		size_t i;
		for (i = 0; i < v->n && D.nreq < NREQ; i++) {
			const char *p = json_str(v->items[i], "pack"), *path = json_str(v->items[i], "path"), *store = json_str(v->items[i], "store");
			require *r = &D.req[D.nreq];
			if (!p || strlen(p) >= sizeof r->pack) continue;
			memset(r, 0, sizeof *r);
			snprintf(r->pack, sizeof r->pack, "%s", p);
			if (path && safe_path(path) && strlen(path) < sizeof r->path) snprintf(r->path, sizeof r->path, "%s", path);
			r->dir = store && !strcmp(store, "directory");
			D.nreq++;
		}
	}
	return 0;
}

/* ACTIVE: "seq<TAB>zim<TAB>sidecar<TAB>book" per layer, newest first */
static void active(void)
{
	char path[PATH_MAX], *text, *line, *save = NULL;
	if (!D.enc[0] || join(path, sizeof path, D.enc, "ACTIVE") || !(text = slurp(path, 1u << 20, NULL))) return;
	for (line = strtok_r(text, "\n", &save); line && D.nlayers < NLAYER; line = strtok_r(NULL, "\n", &save)) {
		char *f[4], *s = line;
		int k;
		if (line[0] == '#') continue;
		for (k = 0; k < 4 && s; k++) { f[k] = s; s = strchr(s, '\t'); if (s) *s++ = 0; }
		if (k < 4 || !safe_path(f[1]) || strlen(f[1]) >= sizeof D.layers[0].file || strlen(f[3]) >= sizeof D.layers[0].book) continue;
		D.layers[D.nlayers].seq = atoi(f[0]);
		snprintf(D.layers[D.nlayers].file, sizeof D.layers[0].file, "%s", f[1]);
		snprintf(D.layers[D.nlayers].book, sizeof D.layers[0].book, "%s", f[3]);
		D.nlayers++;
	}
	free(text);
}

#if defined(E26_LIB_SQLITE)
static const char *const DBNAME[LIB_NDB] = { "catalogue", "search", "facets", "links", "dictionary" };

static int progress(void *ud) { (void)ud; lib_poll(); return lib_interrupted(); }

sqlite3 *lib_db(int which) { return which >= 0 && which < LIB_NDB ? D.db[which] : NULL; }

sqlite3_stmt *lib_stmt(int which, const char *sql)
{
	int i;
	for (i = 0; i < D.nstmt; i++) if (D.stmt[i].sql == sql && D.stmt[i].which == which) {
		sqlite3_reset(D.stmt[i].st);
		sqlite3_clear_bindings(D.stmt[i].st);
		return D.stmt[i].st;
	}
	if (!D.db[which] || D.nstmt == NSTMT) return NULL;
	if (sqlite3_prepare_v3(D.db[which], sql, -1, SQLITE_PREPARE_PERSISTENT, &D.stmt[D.nstmt].st, NULL) != SQLITE_OK) return NULL;
	D.stmt[D.nstmt].which = which;
	D.stmt[D.nstmt].sql = sql;
	return D.stmt[D.nstmt++].st;
}

/* a file: URI for path (percent-encoding what a URI path may not hold) */
static void uri(char *out, size_t n, const char *path)
{
	size_t k = (size_t)snprintf(out, n, "file:");
	for (; *path && k + 4 < n; path++) {
		unsigned char c = (unsigned char)*path;
		if (isalnum(c) || strchr("/-._~", c)) out[k++] = (char)c;
		else k += (size_t)snprintf(out + k, n - k, "%%%02X", c);
	}
	snprintf(out + k, n - k, "?mode=ro&immutable=1");
}

static char *reader_bytes(const char *rpath, size_t *len);
static int parse_article(char *t, size_t len, lib_article *a);
static void article_free(lib_article *a);

/* e26_body's reader: the canonical body of the entry at reader_path in pack ud (any pack may be selected) */
static char *body_of(void *ud, const char *rpath, size_t *len)
{
	struct pack *was = cur;
	lib_article a;
	char *text, *body = NULL;
	size_t n;
	cur = (struct pack *)ud;
	memset(&a, 0, sizeof a);
	if ((text = reader_bytes(rpath, &n)) && !parse_article(text, n, &a)) body = lib_body_text(&a, len);
	free(text);
	article_free(&a);
	cur = was;
	return body;
}

/* Related at query time (h2-index SIZE-PASS "Related query"): links gets facets and the catalogue attached */
static int related_mode(void)
{
	sqlite3_stmt *st;
	int lazy = 0, i;
	if (sqlite3_prepare_v2(D.db[LIB_LINKS], "SELECT (SELECT value FROM meta WHERE key='related_mode'),(SELECT user_version FROM pragma_user_version)", -1, &st, NULL) != SQLITE_OK) return 0;
	if (sqlite3_step(st) == SQLITE_ROW)
		lazy = (sqlite3_column_text(st, 0) && !strcmp((const char *)sqlite3_column_text(st, 0), "primary-category")) || sqlite3_column_int(st, 1) >= 2;
	sqlite3_finalize(st);
	if (!lazy) return 0;
	for (i = 0; i < 2; i++) {
		char path[PATH_MAX], u[PATH_MAX * 3 + 32];
		if (snprintf(path, sizeof path, "%s/%s.sqlite", D.pack, i ? "catalogue" : "facets") >= (int)sizeof path) return -1;
		uri(u, sizeof u, path);
		if (sqlite3_prepare_v2(D.db[LIB_LINKS], i ? "ATTACH ?1 AS cat" : "ATTACH ?1 AS fac", -1, &st, NULL) != SQLITE_OK) return -1;
		sqlite3_bind_text(st, 1, u, -1, SQLITE_TRANSIENT);
		if (sqlite3_step(st) != SQLITE_DONE) { sqlite3_finalize(st); unready("E_PACK", "a database will not open"); return -1; }
		sqlite3_finalize(st);
	}
	D.lazy_related = 1;
	return 0;
}

static int open_dbs(void)
{
	int i;
	for (i = 0; i < LIB_NDB; i++) {
		char path[PATH_MAX], u[PATH_MAX * 3 + 32];
		sqlite3_stmt *st;
		int ok;
		if (snprintf(path, sizeof path, "%s/%s.sqlite", D.pack, DBNAME[i]) >= (int)sizeof path) return -1;
		uri(u, sizeof u, path);
		if (sqlite3_open_v2(u, &D.db[i], SQLITE_OPEN_READONLY | SQLITE_OPEN_URI | SQLITE_OPEN_NOMUTEX, NULL) != SQLITE_OK) {
			unready("E_PACK", "a database will not open");
			return -1;
		}
		sqlite3_exec(D.db[i], "PRAGMA temp_store=MEMORY; PRAGMA query_only=1;", NULL, NULL, NULL);
		if (sqlite3_prepare_v2(D.db[i], "SELECT (SELECT value FROM meta WHERE key='generation'),(SELECT value FROM meta WHERE key='schema')", -1, &st, NULL) != SQLITE_OK) {
			unready("E_PACK", "a database has no meta table");
			return -1;
		}
		ok = sqlite3_step(st) == SQLITE_ROW && sqlite3_column_text(st, 0) && !strcmp((const char *)sqlite3_column_text(st, 0), D.generation);
		if (ok && (!sqlite3_column_text(st, 1) || strcmp((const char *)sqlite3_column_text(st, 1), "1"))) { sqlite3_finalize(st); unready("E_VERSION", "unsupported schema"); return -1; }
		sqlite3_finalize(st);
		if (!ok) { unready("E_STALE", "a database belongs to another generation"); return -1; }
		sqlite3_progress_handler(D.db[i], 4000, progress, NULL);
		/* storage revision 2: the views call these; harmless on a revision 1 pack */
		if (lib_storage_register(D.db[i], body_of, cur)) { unready("E_PACK", "a database will not open"); return -1; }
	}
	return related_mode();
}

/* the article ordinals 0..N-1 and the wheel rows 0..W-1, contiguous; the wheel's ordinals into memory */
static int wheel(void)
{
	sqlite3_stmt *st;
	uint32_t i = 0;
	int ok = 1;
	if (sqlite3_prepare_v2(D.db[LIB_CATALOGUE], "SELECT count(*),coalesce(max(ordinal),-1) FROM article", -1, &st, NULL) != SQLITE_OK) return -1;
	if (sqlite3_step(st) != SQLITE_ROW || sqlite3_column_int64(st, 1) != sqlite3_column_int64(st, 0) - 1 || sqlite3_column_int64(st, 0) > INT32_MAX) ok = 0;
	D.ndocs = (uint32_t)sqlite3_column_int64(st, 0);
	sqlite3_finalize(st);
	if (!ok) return -1;
	if (sqlite3_prepare_v2(D.db[LIB_CATALOGUE], "SELECT count(*) FROM title", -1, &st, NULL) != SQLITE_OK) return -1;
	if (sqlite3_step(st) != SQLITE_ROW || sqlite3_column_int64(st, 0) > INT32_MAX) ok = 0;
	D.nwheel = (uint32_t)sqlite3_column_int64(st, 0);
	sqlite3_finalize(st);
	if (!ok) return -1;
	D.wart = (int32_t *)malloc((D.nwheel + 1) * sizeof *D.wart);
	D.wflag = (unsigned char *)malloc(D.nwheel + 1);
	if (!D.wart || !D.wflag) return -1;
	if (sqlite3_prepare_v2(D.db[LIB_CATALOGUE], "SELECT wheel_ordinal,article_ordinal,alias,catalogue_only FROM title ORDER BY wheel_ordinal", -1, &st, NULL) != SQLITE_OK) return -1;
	while (ok && sqlite3_step(st) == SQLITE_ROW) {
		int null = sqlite3_column_type(st, 1) == SQLITE_NULL;
		int64_t a = sqlite3_column_int64(st, 1);
		if (i >= D.nwheel || sqlite3_column_int64(st, 0) != (int64_t)i || (!null && (a < 0 || a >= (int64_t)D.ndocs))) { ok = 0; break; }
		D.wart[i] = null ? -1 : (int32_t)a;
		D.wflag[i] = (unsigned char)((sqlite3_column_int(st, 2) ? 1 : 0) | (sqlite3_column_int(st, 3) ? 2 : 0));
		if (null != !!(D.wflag[i] & 2)) ok = 0;
		i++;
	}
	sqlite3_finalize(st);
	return ok && i == D.nwheel ? 0 : -1;
}
#endif

static void open_into(const char *packdir, const char *encdir);

void lib_open(const char *packdir, const char *encdir)
{
	cur = PK;
	npacks = 1;
	open_into(packdir, encdir);
	if (!ROOT[0] && D.enc[0]) {
		/* the libcontent root: the encyclopedia directory's parent (/d32/libcontent) */
		char *slash;
		snprintf(ROOT, sizeof ROOT, "%s", D.enc);
		if ((slash = strrchr(ROOT, '/')) && slash != ROOT) *slash = 0;
		else ROOT[0] = 0;
	}
	lib_select(0);
}

/* lib-corpus or lib-reserved behind packdir, as pack 1 or 2; a missing or failing pack never fails pack 0 */
void lib_open_extra(const char *packdir)
{
	char rp[PATH_MAX];
	int i;
	if (!packdir || !*packdir || npacks >= LIB_NPACK || !PK[0].ready || !realpath(packdir, rp)) return;
	/* pack 0 is itself a corpus pack (ART-CR1: one helper per pack), or this pack is already open */
	if (strcmp(PK[0].name, "lib-e26")) return;
	for (i = 0; i < npacks; i++) if (!strcmp(PK[i].pack, rp)) return;
	cur = &PK[npacks];
	open_into(rp, PK[0].enc[0] ? PK[0].enc : NULL);
	if (D.ready && (!strcmp(D.name, "lib-corpus") || !strcmp(D.name, "lib-reserved"))) {
		for (i = 1; i < npacks && strcmp(PK[i].name, D.name); i++) ;
		if (i == npacks) { npacks++; lib_select(0); return; }
	}
	lib_close_one();
	cur = PK;
	lib_select(0);
}

void lib_select(int i)
{
	if (i < 0 || i >= lib_npacks()) i = 0;
	cur = &PK[i];
#if defined(E26_LIB_SQLITE)
	if (D.ready) q_search_attach(D.db[LIB_SEARCH], D.db[LIB_CATALOGUE], D.ndocs);
	else q_search_attach(NULL, NULL, 0);
#endif
}

static void open_into(const char *packdir, const char *encdir)
{
	unready("E_PACK", "no pack");
	D.enc[0] = 0;
	if (encdir && !realpath(encdir, D.enc)) D.enc[0] = 0;
	active();
	if (!packdir || !realpath(packdir, D.pack)) { D.pack[0] = 0; return; }
	if (pack_json()) return;
	if (manifest()) { unready("E_PACK", "the pack's inventory does not match"); return; }
#if defined(E26_LIB_SQLITE)
	if (open_dbs()) return;
	if (wheel()) { unready("E_PACK", "the catalogue's ordinals are not contiguous"); return; }
	if (D.zimstore) {
		char path[PATH_MAX], err[160];
#if defined(E26_LIB_ZIM)
		if (join(path, sizeof path, D.pack, "reader.zim") || !(D.zim = lib_zim_open(path, err, sizeof err))) { unready("E_PACK", "reader.zim will not open"); return; }
#else
		(void)path;
		(void)err;
		unready("E_PACK", "this e26-lib has no ZIM reader");
		return;
#endif
	}
	D.ready = 1;
	D.code = NULL;
#else
	unready("E_UNSUPPORTED", "this e26-lib has no SQLite");
#endif
}

void lib_close(void)
{
	int i;
	for (i = lib_npacks() - 1; i >= 0; i--) { cur = &PK[i]; lib_close_one(); }
	cur = PK;
	npacks = 0;
#if defined(E26_LIB_SQLITE)
	q_search_attach(NULL, NULL, 0);
#endif
}

/* closes the selected pack */
static void lib_close_one(void)
{
	int i;
	for (i = 0; i < D.nreq; i++) if (D.req[i].zim && D.req[i].tried != 2) {   /* 2: an ACTIVE layer's, closed below */
#if defined(E26_LIB_ZIM)
		lib_zim_close(D.req[i].zim);
#endif
	}
	for (i = 0; i < NCACHE; i++) {
		size_t k;
		for (k = 0; k < D.cache[i].nblocks; k++) free(D.cache[i].block[k]);
		free(D.cache[i].block); free(D.cache[i].blen); free(D.cache[i].meta); free(D.cache[i].id);
	}
#if defined(E26_LIB_SQLITE)
	for (i = 0; i < D.nstmt; i++) sqlite3_finalize(D.stmt[i].st);
	for (i = 0; i < LIB_NDB; i++) if (D.db[i]) sqlite3_close(D.db[i]);
#endif
#if defined(E26_LIB_ZIM)
	if (D.zim) lib_zim_close(D.zim);
	for (i = 0; i < D.nlayers; i++) if (D.layers[i].zim) lib_zim_close(D.layers[i].zim);
#endif
	json_free(D.pk);
	free(D.pack_json); free(D.wart); free(D.wflag);
	memset(&D, 0, sizeof D);
}

/* ---- errors and arguments */
const char *lib_sqlerr(char **result)
{
	int s = lib_interrupted();
	return lib_err(result, s == 1 ? "E_CANCELLED" : s == 2 ? "E_TIMEOUT" : "E_PACK", NULL);
}

int lib_args(const jv *args, ...)
{
	size_t i;
	if (!args || args->type != J_OBJ) return -1;
	for (i = 0; i < args->n; i++) {
		va_list ap;
		const char *k;
		int found = 0;
		va_start(ap, args);
		while ((k = va_arg(ap, const char *))) if (!strcmp(k, args->keys[i])) { found = 1; break; }
		va_end(ap);
		if (!found) return -1;
	}
	return 0;
}

const jv *lib_arg(const jv *args, const char *key) { return json_get(args, key); }

int lib_arg_int(const jv *args, const char *key, int64_t lo, int64_t hi, int64_t def, int64_t *out)
{
	const jv *v = json_get(args, key);
	if (!v) { *out = def; return 0; }
	return e26j_i64(v, lo, hi, out);
}

int lib_arg_str(const jv *args, const char *key, size_t max, int empty_ok, const char **out)
{
	const jv *v = json_get(args, key);
	size_t n;
	if (!v || v->type != J_STR) return -1;
	n = strlen(v->str);
	if (n > max || (!n && !empty_ok)) return -1;
	*out = v->str;
	return 0;
}

int lib_arg_bool(const jv *args, const char *key, int def, int *out)
{
	const jv *v = json_get(args, key);
	if (!v) { *out = def; return 0; }
	if (v->type != J_BOOL) return -1;
	*out = v->b;
	return 0;
}

int lib_arg_page(const jv *args, int64_t def, int64_t max, int64_t *start, int64_t *limit)
{
	if (lib_arg_int(args, "start", 0, INT32_MAX, 0, start)) return -1;
	return lib_arg_int(args, "limit", 1, max, def, limit);
}

/* ---- bitmaps */
size_t lib_bitmap_bytes(void) { return (D.ndocs + 7) / 8; }

unsigned char *lib_bitmap_arg(const jv *args, const char *key)
{
	const jv *v = json_get(args, key);
	unsigned char *bits;
	size_t n;
	if (!v || v->type != J_STR || !D.ready) return NULL;
	if (!(bits = e26j_unb64(v->str, strlen(v->str), &n))) return NULL;
	if (n != lib_bitmap_bytes() || (D.ndocs % 8 && n && bits[n - 1] >> (D.ndocs % 8))) { free(bits); return NULL; }
	return bits;
}

void lib_bitmap_out(e26j_buf *b, const unsigned char *bits) { e26j_b64(b, bits, lib_bitmap_bytes()); }

uint32_t lib_popcount(const unsigned char *bits)
{
	size_t i, n = lib_bitmap_bytes();
	uint32_t c = 0;
	for (i = 0; i < n; i++) { unsigned x = bits[i]; while (x) { c += x & 1; x >>= 1; } }
	return c;
}

/* ---- folding */
char *lib_fold(const char *s)
{
#if defined(E26_LIB_ZIM)
	return lib_icu_fold(s);
#else
	size_t i, n = strlen(s);
	char *o = (char *)malloc(n + 1);
	if (!o) return NULL;
	for (i = 0; i <= n; i++) o[i] = (char)((s[i] >= 'A' && s[i] <= 'Z') ? s[i] + 32 : s[i]);
	return o;
#endif
}

#if defined(E26_LIB_SQLITE)
/* ---- articles */
static int is_text(sqlite3_stmt *st, int col) { return sqlite3_column_type(st, col) == SQLITE_TEXT; }

const char *lib_resolve(const jv *args, const char *key, uint32_t *ordinal, char **id, char **result)
{
	static const char *const by_id = "SELECT ordinal,id FROM article WHERE id=?1";
	static const char *const by_alias = "SELECT DISTINCT a.ordinal,a.id FROM title t JOIN article a ON a.ordinal=t.article_ordinal "
	                                    "WHERE t.sort_key=?1 AND t.title=?2 AND t.alias=1 ORDER BY a.ordinal LIMIT 2";
	static const char *const by_alias_scan = "SELECT DISTINCT a.ordinal,a.id FROM title t JOIN article a ON a.ordinal=t.article_ordinal "
	                                         "WHERE t.title=?2 AND t.alias=1 ORDER BY a.ordinal LIMIT 2";
	const char *s;
	sqlite3_stmt *st;
	char *fold;
	int rc, n = 0;
	*id = NULL;
	if (lib_arg_str(args, key, 4096, 0, &s)) return lib_err(result, "E_ARGUMENT", NULL);
	if (!lib_ready(result)) return lib_code();
	if (!(st = lib_stmt(LIB_CATALOGUE, by_id))) return lib_err(result, "E_PACK", NULL);
	sqlite3_bind_text(st, 1, s, -1, SQLITE_STATIC);
	if ((rc = sqlite3_step(st)) == SQLITE_ROW && is_text(st, 1)) {
		*ordinal = (uint32_t)sqlite3_column_int64(st, 0);
		*id = strdup((const char *)sqlite3_column_text(st, 1));
		sqlite3_reset(st);
		return *id ? NULL : lib_err(result, "E_PACK", NULL);
	}
	sqlite3_reset(st);
	if (rc != SQLITE_DONE && rc != SQLITE_ROW) return lib_sqlerr(result);
	/* an alias or redirect title: through the sort key's index when the fold is ICU's, else a scan */
	fold = lib_fold(s);
#if defined(E26_LIB_ZIM)
	st = lib_stmt(LIB_CATALOGUE, fold ? by_alias : by_alias_scan);
#else
	st = lib_stmt(LIB_CATALOGUE, by_alias_scan);
	(void)by_alias;
#endif
	if (!st) { free(fold); return lib_err(result, "E_PACK", NULL); }
	if (fold) sqlite3_bind_text(st, 1, fold, -1, SQLITE_TRANSIENT);
	sqlite3_bind_text(st, 2, s, -1, SQLITE_STATIC);
	free(fold);
	while ((rc = sqlite3_step(st)) == SQLITE_ROW) {
		if (n++) break;
		*ordinal = (uint32_t)sqlite3_column_int64(st, 0);
		*id = is_text(st, 1) ? strdup((const char *)sqlite3_column_text(st, 1)) : NULL;
	}
	sqlite3_reset(st);
	if (rc != SQLITE_DONE && rc != SQLITE_ROW) { free(*id); *id = NULL; return lib_sqlerr(result); }
	if (n > 1) { free(*id); *id = NULL; return lib_err(result, "E_ARGUMENT", NULL); }
	if (!n) return lib_err(result, "E_NOT_FOUND", NULL);
	return *id ? NULL : lib_err(result, "E_PACK", NULL);
}

static void article_free(lib_article *a)
{
	size_t k;
	for (k = 0; k < a->nblocks; k++) free(a->block[k]);
	free(a->block); free(a->blen); free(a->meta); free(a->id);
	memset(a, 0, sizeof *a);
}

/* the reader bytes for a reader path */
static char *reader_bytes(const char *rpath, size_t *len)
{
	char path[PATH_MAX];
	if (!safe_path(rpath)) return NULL;
	if (!D.zimstore) {
		if (snprintf(path, sizeof path, "%s/reader/%s", D.pack, rpath) >= (int)sizeof path) return NULL;
		return slurp(path, MAX_ARTICLE, len);
	}
#if defined(E26_LIB_ZIM)
	{
		unsigned char *d;
		uint64_t total;
		char *p;
		if (lib_zim_read(D.zim, rpath, 0, MAX_ARTICLE + 1, &d, len, &total) != 1) return NULL;
		if (total > MAX_ARTICLE || !(p = (char *)realloc(d, *len + 1))) { free(d); return NULL; }
		p[*len] = 0;
		return p;
	}
#else
	return NULL;
#endif
}

/* "e26art 2", then kind TAB object per line: meta, title, blocks, attribution; each object strict JSON */
static int parse_article(char *t, size_t len, lib_article *a)
{
	char *p = t, *e = t + len;
	size_t cap = 0, rec = 0;
	if (len < 9 || memcmp(t, "e26art 2\n", 9) || t[len - 1] != '\n' || memchr(t, '\r', len)) return -1;
	p += 9;
	while (p < e) {
		char *nl = (char *)memchr(p, '\n', (size_t)(e - p)), *tab, *obj, *end;
		size_t klen, olen;
		if (!nl || (size_t)(nl - p) > MAX_RECORD) return -1;
		tab = (char *)memchr(p, '\t', (size_t)(nl - p));
		if (!tab || memchr(tab + 1, '\t', (size_t)(nl - tab - 1))) return -1;
		klen = (size_t)(tab - p);
		if (!klen || klen > 32 || strspn(p, "abcdefghijklmnopqrstuvwxyz_") < klen) return -1;
		obj = tab + 1;
		end = nl;
		while (obj < end && (*obj == ' ')) obj++;
		while (end > obj && end[-1] == ' ') end--;
		olen = (size_t)(end - obj);
		if (olen < 2 || *obj != '{' || e26j_valid(obj, olen, NULL, 0)) return -1;
		if (!rec) {
			if (klen != 4 || memcmp(p, "meta", 4) || !(a->meta = (char *)malloc(olen + 1))) return -1;
			memcpy(a->meta, obj, olen);
			a->meta[olen] = 0;
		} else {
			char *b;
			size_t n, k = 0, inner;
			if (rec == 1 && (klen != 5 || memcmp(p, "title", 5))) return -1;
			if (a->nblocks == cap) {
				size_t c = cap ? cap * 2 : 64;
				char **nb = (char **)realloc(a->block, c * sizeof *nb);
				size_t *nl2;
				if (!nb) return -1;
				a->block = nb;
				if (!(nl2 = (size_t *)realloc(a->blen, c * sizeof *nl2))) return -1;
				a->blen = nl2;
				cap = c;
			}
			/* {"type":"<kind>", then the payload's members (the payload has no "type": its keys are fixed) */
			for (inner = 1; inner < olen && (obj[inner] == ' ' || obj[inner] == '\t'); inner++) ;
			n = 9 + klen + 1 + (obj[inner] == '}' ? 1 : 1 + (olen - 1));
			if (!(b = (char *)malloc(n + 1))) return -1;
			memcpy(b + k, "{\"type\":\"", 9); k += 9;
			memcpy(b + k, p, klen); k += klen;
			b[k++] = '"';
			if (obj[inner] == '}') b[k++] = '}';
			else { b[k++] = ','; memcpy(b + k, obj + 1, olen - 1); k += olen - 1; }
			b[k] = 0;
			a->block[a->nblocks] = b;
			a->blen[a->nblocks++] = k;
			a->bytes += k;
		}
		rec++;
		p = nl + 1;
	}
	if (rec < 3 || !a->nblocks) return -1;
	{
		const char *last = a->block[a->nblocks - 1];
		if (strncmp(last, "{\"type\":\"attribution\"", 21)) return -1;
	}
	return 0;
}

const lib_article *lib_article_get(uint32_t ordinal, const char **code)
{
	static const char *const q = "SELECT id,reader_path,block_count,content_sha256 FROM article WHERE ordinal=?1";
	sqlite3_stmt *st;
	int i, victim = 0;
	char *text, *rp, *body, hex[65], want[65];
	size_t len;
	int64_t blocks;
	lib_article a;
	for (i = 0; i < NCACHE; i++) if (D.cache[i].id && D.cache_ord[i] == ordinal) { D.cache_use[i] = ++D.clock; return &D.cache[i]; }
	*code = "E_PACK";
	if (!(st = lib_stmt(LIB_CATALOGUE, q))) return NULL;
	sqlite3_bind_int64(st, 1, ordinal);
	if (sqlite3_step(st) != SQLITE_ROW || !is_text(st, 0) || !is_text(st, 1)) {
		*code = lib_interrupted() == 1 ? "E_CANCELLED" : lib_interrupted() == 2 ? "E_TIMEOUT" : "E_NOT_FOUND";
		sqlite3_reset(st);
		return NULL;
	}
	memset(&a, 0, sizeof a);
	a.id = strdup((const char *)sqlite3_column_text(st, 0));
	rp = strdup((const char *)sqlite3_column_text(st, 1));
	blocks = sqlite3_column_int64(st, 2);
	snprintf(want, sizeof want, "%s", is_text(st, 3) ? (const char *)sqlite3_column_text(st, 3) : "");
	sqlite3_reset(st);
	if (!a.id || !rp) { free(a.id); free(rp); return NULL; }
	text = reader_bytes(rp, &len);
	free(rp);
	/* content_sha256 covers every record after meta (ARTICLE-FORMAT-2): a damaged reader entry is E_PACK */
	body = text && len > 9 ? memchr(text + 9, '\n', len - 9) : NULL;
	if (body) sha256_hex(body + 1, len - (size_t)(body + 1 - text), hex);
	if (!body || strcmp(hex, want) || parse_article(text, len, &a) || (int64_t)a.nblocks != blocks) { free(text); article_free(&a); return NULL; }
	free(text);
	for (i = 1; i < NCACHE; i++) if (!D.cache[i].id || D.cache_use[i] < D.cache_use[victim]) victim = i;
	article_free(&D.cache[victim]);
	D.cache[victim] = a;
	D.cache_ord[victim] = ordinal;
	D.cache_use[victim] = ++D.clock;
	return &D.cache[victim];
}

/* ---- rows */
int lib_row(e26j_buf *b, sqlite3_stmt *st, const char *extra)
{
	int i, n = sqlite3_column_count(st);
	e26j_lit(b, "{");
	for (i = 0; i < n; i++) {
		const char *name = sqlite3_column_name(st, i);
		size_t nl = strlen(name);
		int json = nl > 5 && !strcmp(name + nl - 5, "_json");
		int boolean = !strcmp(name, "alias") || !strcmp(name, "catalogue_only") || !strcmp(name, "approved");
		if (i) e26j_lit(b, ",");
		e26j_str(b, name, json ? nl - 5 : nl);
		e26j_lit(b, ":");
		switch (sqlite3_column_type(st, i)) {
		case SQLITE_NULL: e26j_lit(b, "null"); break;
		case SQLITE_INTEGER:
			if (boolean) e26j_lit(b, sqlite3_column_int64(st, i) ? "true" : "false");
			else e26j_i(b, sqlite3_column_int64(st, i));
			break;
		case SQLITE_FLOAT: {
			char t[40];
			snprintf(t, sizeof t, "%.17g", sqlite3_column_double(st, i));
			e26j_lit(b, t);
			break;
		}
		default: {
			const char *s = (const char *)sqlite3_column_text(st, i);
			int len = sqlite3_column_bytes(st, i);
			if (!s) return -1;
			if (json) {
				if (e26j_valid(s, (size_t)len, NULL, 0)) return -1;
				e26j_raw(b, s, (size_t)len);
			} else e26j_str(b, s, (size_t)len);
		}
		}
	}
	if (extra) e26j_lit(b, extra);
	e26j_lit(b, "}");
	return b->bad ? -1 : 0;
}

int lib_list_add(lib_list *l, sqlite3_stmt *st, const char *extra)
{
	e26j_buf b;
	char *s;
	e26j_init(&b);
	if (lib_row(&b, st, extra)) { e26j_free(&b); return -1; }
	if (!(s = e26j_take(&b))) return -1;
	if (l->n == l->cap) {
		size_t c = l->cap ? l->cap * 2 : 32;
		char **nr = (char **)realloc(l->row, c * sizeof *nr);
		if (!nr) { free(s); return -1; }
		l->row = nr;
		l->cap = c;
	}
	l->row[l->n++] = s;
	return 0;
}

void lib_list_page(e26j_buf *b, const lib_list *l, int64_t start, int64_t limit)
{
	size_t i, s = (size_t)start, e = start + limit < (int64_t)l->n ? (size_t)(start + limit) : l->n;
	e26j_lit(b, "\"rows\":[");
	for (i = s; i < e; i++) { if (i > s) e26j_lit(b, ","); e26j_lit(b, l->row[i]); }
	e26j_lit(b, "],\"start\":");
	e26j_i(b, start);
	e26j_lit(b, ",\"next\":");
	if (e < l->n) e26j_u(b, e); else e26j_lit(b, "null");
	e26j_lit(b, ",\"total\":");
	e26j_u(b, l->n);
}

void lib_list_free(lib_list *l)
{
	size_t i;
	for (i = 0; i < l->n; i++) free(l->row[i]);
	free(l->row);
	memset(l, 0, sizeof *l);
}

const char *lib_list_all(lib_list *l, sqlite3_stmt *st, const char *(*extra)(sqlite3_stmt *), char **result)
{
	int rc;
	while ((rc = sqlite3_step(st)) == SQLITE_ROW)
		if (lib_list_add(l, st, extra ? extra(st) : NULL)) { sqlite3_reset(st); return lib_err(result, "E_PACK", NULL); }
	sqlite3_reset(st);
	return rc == SQLITE_DONE ? NULL : lib_sqlerr(result);
}
#else
const char *lib_resolve(const jv *args, const char *key, uint32_t *ordinal, char **id, char **result)
{
	(void)args; (void)key; (void)ordinal;
	*id = NULL;
	return lib_err(result, "E_UNSUPPORTED", NULL);
}
const lib_article *lib_article_get(uint32_t ordinal, const char **code) { (void)ordinal; *code = "E_UNSUPPORTED"; return NULL; }
#endif

/* ---- media */
static int declared(const char *pack)
{
	const jv *r = D.pk ? json_get(D.pk, "requires") : NULL;
	size_t i;
	if (!r || r->type != J_ARR) return 0;
	for (i = 0; i < r->n; i++) {
		const char *p = json_str(r->items[i], "pack");
		if (p && !strcmp(p, pack)) return 1;
	}
	return 0;
}

#if defined(E26_LIB_ZIM)
static void *layer_zim(int i)
{
	layer *l = &D.layers[i];
	if (!l->tried) {
		char path[PATH_MAX];
		l->tried = 1;
		if (!join(path, sizeof path, D.enc, l->file)) l->zim = lib_zim_open(path, NULL, 0);
	}
	return l->zim;
}
#endif

/* a byte range of the regular file dir/path: 1, 0 when there is no such file, -1 when it will not read */
static int file_read(const char *dir, const char *path, uint64_t offset, size_t limit, unsigned char **data, size_t *len,
                     uint64_t *total)
{
	char fp[PATH_MAX];
	struct stat st;
	int fd;
	ssize_t r = 0;
	size_t n;
	if (join(fp, sizeof fp, dir, path)) return -1;
	if ((fd = open(fp, O_RDONLY | O_CLOEXEC)) < 0) return errno == ENOENT || errno == ENOTDIR ? 0 : -1;
	if (fstat(fd, &st) || !S_ISREG(st.st_mode)) { close(fd); return -1; }
	*total = (uint64_t)st.st_size;
	n = offset >= *total ? 0 : (size_t)(*total - offset < limit ? *total - offset : limit);
	if (!(*data = (unsigned char *)malloc(n ? n : 1))) { close(fd); return -1; }
	while (*len < n) {
		r = pread(fd, *data + *len, n - *len, (off_t)(offset + *len));
		if (r < 0 && errno == EINTR) continue;
		if (r <= 0) break;
		*len += (size_t)r;
	}
	close(fd);
	if (*len != n) { free(*data); *data = NULL; *len = 0; return -1; }
	return 1;
}

#if defined(E26_LIB_ZIM)
/* a requires entry's ZIM: the ACTIVE layer at the same file when the encyclopedia lists it (d32-update checked its
 * hash at install), else the file at root/path */
static void *require_zim(require *q)
{
	if (!q->tried) {
		char fp[PATH_MAX];
		const char *enc = "encyclopedia/";
		int i;
		q->tried = 1;
		if (!q->path[0]) return NULL;
		if (!strncmp(q->path, enc, strlen(enc)))
			for (i = 0; i < D.nlayers; i++) if (!strcmp(D.layers[i].file, q->path + strlen(enc))) {
				void *z = layer_zim(i);
				if (z) return q->zim = z, q->tried = 2, z;
			}
		if (ROOT[0] && !join(fp, sizeof fp, ROOT, q->path)) q->zim = lib_zim_open(fp, NULL, 0);
	}
	return q->zim;
}
#endif

int lib_media_read(const char *pack, const char *path, uint64_t offset, size_t limit, unsigned char **data, size_t *len,
                   uint64_t *total)
{
	int own = !strcmp(pack, D.name) || (D.fixture && !strcmp(pack, "fx-lib"));
	char inside[PATH_MAX];
	int i, r;
	*data = NULL;
	*len = 0;
	*total = 0;
	if (!safe_path(path)) return -1;
	if (own && !D.zimstore) return file_read(D.pack, path, offset, limit, data, len, total) == 1 ? 1 : -1;
#if defined(E26_LIB_ZIM)
	if (own) {
		r = lib_zim_read(D.zim, path, offset, limit, data, len, total);
		return r == 1 ? 1 : -1;
	}
#endif
	/* another pack's picture: first a copy inside this pack's own generation (update 6 puts every picture there, at
	 * M/<pack>/<path>), then the packs PACK.json requires, then (as before) an ACTIVE encyclopedia layer by name */
	if (snprintf(inside, sizeof inside, "M/%s/%s", pack, path) < (int)sizeof inside && safe_path(inside)) {
		if (!D.zimstore) r = file_read(D.pack, inside, offset, limit, data, len, total);
#if defined(E26_LIB_ZIM)
		else r = lib_zim_read(D.zim, inside, offset, limit, data, len, total);
#else
		else r = 0;
#endif
		if (r) return r;
	}
	for (i = 0; i < D.nreq; i++) {
		require *q = &D.req[i];
		if (strcmp(q->pack, pack) || !q->path[0]) continue;
		if (q->dir) {
			char dir[PATH_MAX];
			if (!ROOT[0] || join(dir, sizeof dir, ROOT, q->path)) continue;
			if ((r = file_read(dir, path, offset, limit, data, len, total))) return r;
			continue;
		}
#if defined(E26_LIB_ZIM)
		{
			void *z = require_zim(q);
			if (z && (r = lib_zim_read(z, path, offset, limit, data, len, total))) return r;
		}
#endif
	}
#if defined(E26_LIB_ZIM)
	if (!declared(pack) && !declared("lib-encyclopedia")) return 0;
	{
		int i;
		for (i = 0; i < D.nlayers; i++) {
			void *z;
			int r;
			if (strcmp(D.layers[i].book, pack) && strcmp(pack, "lib-encyclopedia")) continue;
			if (!(z = layer_zim(i))) continue;
			r = lib_zim_read(z, path, offset, limit, data, len, total);
			if (r) return r;
		}
	}
	return 0;
#else
	(void)declared;
	return own ? -1 : 0;
#endif
}

/* ---- several packs */
#if defined(E26_LIB_SQLITE)
static int public_marking(const char *mark)
{
	return !mark || !*mark || !strcmp(mark, "PUBLIC");
}

int lib_public_pack(const char *name, const char **generation, const char **catalogue_hash)
{
	int i;
	*generation = *catalogue_hash = NULL;
	if (strcmp(name, "lib-e26") && strcmp(name, "lib-corpus")) return -1;
	for (i = 0; i < lib_npacks(); i++) {
		const struct pack *p = &PK[i];
		if (!p->ready || strcmp(name, p->name) ||
		    !public_marking(json_str(p->pk, "handling")) ||
		    !public_marking(json_str(p->pk, "marking"))) continue;
		*generation = p->generation;
		*catalogue_hash = p->catalogue_hash;
		return i;
	}
	return -1;
}

int lib_cisco_identity(int pack, uint32_t ordinal, const char *id, const char *doc_key,
                       const char *doc_id, int part)
{
	static const char *const E26 =
		"SELECT e26_unzstd(a.meta_json) FROM article a WHERE a.ordinal=?1 AND a.id=?2 "
		"AND a.source_kind='cisco' AND EXISTS(SELECT 1 FROM title t "
		"WHERE t.article_ordinal=a.ordinal AND t.alias=0 AND t.catalogue_only=0)";
	static const char *const CORPUS =
		"SELECT 1 FROM article a JOIN corpus_doc d ON d.article_ordinal=a.ordinal "
		"WHERE a.ordinal=?1 AND a.id=?2 AND d.doc_key=?3 AND d.doc_id=?4 "
		"AND d.doc_type='cisco' AND d.handling='PUBLIC' AND d.opt_in=0 AND d.part=?5";
	struct pack *was = cur;
	sqlite3_stmt *st;
	jv *meta = NULL;
	int corpus, rc, ok = -1;

	if (pack < 0 || pack >= lib_npacks()) return 0;
	cur = &PK[pack];
	corpus = !strcmp(D.name, "lib-corpus");
	if (!D.ready || ordinal >= D.ndocs || (!corpus && strcmp(D.name, "lib-e26"))) {
		ok = 0;
		goto done;
	}
	st = lib_stmt(LIB_CATALOGUE, corpus ? CORPUS : E26);
	if (!st) goto done;
	if (sqlite3_bind_int64(st, 1, ordinal) != SQLITE_OK ||
	    sqlite3_bind_text(st, 2, id, -1, SQLITE_STATIC) != SQLITE_OK ||
	    (corpus && (sqlite3_bind_text(st, 3, doc_key, -1, SQLITE_STATIC) != SQLITE_OK ||
	                sqlite3_bind_text(st, 4, doc_id, -1, SQLITE_STATIC) != SQLITE_OK ||
	                sqlite3_bind_int(st, 5, part) != SQLITE_OK))) goto reset;
	rc = sqlite3_step(st);
	if (rc == SQLITE_DONE) ok = 0;
	if (rc == SQLITE_ROW) {
		if (corpus) ok = 1;
		else {
			const char *raw = (const char *)sqlite3_column_text(st, 0);
			const char *meta_id;
			meta = raw ? e26j_parse(raw, (size_t)sqlite3_column_bytes(st, 0), NULL, 0) : NULL;
			meta_id = json_str(meta, "id");
			ok = meta_id && !strcmp(meta_id, id) &&
			    public_marking(json_str(meta, "marking")) &&
			    public_marking(json_str(json_get(meta, "provenance"), "marking"));
		}
	}
reset:
	sqlite3_reset(st);
	sqlite3_clear_bindings(st);
	json_free(meta);
done:
	cur = was;
	return ok;
}

static int holds(int which, const char *sql, const char *id)
{
	sqlite3_stmt *st = lib_stmt(which, sql);
	int rc;
	if (!st) return 0;
	sqlite3_bind_text(st, 1, id, -1, SQLITE_STATIC);
	rc = sqlite3_step(st);
	sqlite3_reset(st);
	return rc == SQLITE_ROW;
}
#endif

int lib_route(const char *op, const jv *args)
{
#if defined(E26_LIB_SQLITE)
	static const char *const qa = "SELECT 1 FROM article WHERE id=?1",
	                  *const qt = "SELECT 1 FROM title WHERE title=?1 AND alias=1 AND article_ordinal IS NOT NULL",
	                  *const qm = "SELECT 1 FROM media WHERE id=?1";
	static const char *const OPS[] = { "article", "section", "media", "related", "references", "updates", "moreinfo",
	                                   "facts", "rag_ids", NULL };
	const char *id = json_str(args, "id");
	struct pack *was = cur;
	int i, k, media;
	if (lib_npacks() < 2 || !id || !op) return 0;
	for (k = 0; OPS[k] && strcmp(OPS[k], op); k++) ;
	if (!OPS[k]) return 0;
	media = !strcmp(op, "media");
	for (i = 0; i < lib_npacks(); i++) {
		int found;
		cur = &PK[i];
		found = D.ready && (media ? holds(LIB_CATALOGUE, qm, id) : holds(LIB_CATALOGUE, qa, id));
		cur = was;
		if (found) return i;
	}
	if (!media) for (i = 0; i < lib_npacks(); i++) {
		int found;
		cur = &PK[i];
		found = D.ready && holds(LIB_CATALOGUE, qt, id);
		cur = was;
		if (found) return i;
	}
#else
	(void)op; (void)args;
#endif
	return 0;
}

void lib_packs_json(e26j_buf *b)
{
	static const char *const NAMES[LIB_NPACK] = { "lib-e26", "lib-corpus", "lib-reserved" };
	int i, k, first = 1;
	e26j_lit(b, "\"packs\":[");
	for (k = 0; k < LIB_NPACK; k++) {
		struct pack *p = NULL;
		const char *h;
		for (i = 0; i < lib_npacks(); i++) if (PK[i].ready && !strcmp(PK[i].name, NAMES[k])) p = &PK[i];
		if (!first) e26j_lit(b, ",");
		first = 0;
		e26j_lit(b, "{\"name\":"); e26j_cstr(b, NAMES[k]);
		e26j_lit(b, ",\"generation\":"); e26j_cstr(b, p ? p->generation : NULL);
		e26j_lit(b, ",\"present\":"); e26j_lit(b, p ? "true" : "false");
		h = p ? json_str(p->pk, "handling") : NULL;
		e26j_lit(b, ",\"handling\":"); e26j_cstr(b, p ? (h ? h : "PUBLIC") : NULL);
		e26j_lit(b, ",\"counts\":");
		{
			const jv *c = p ? json_get(p->pk, "counts") : NULL;
			const char *keys[4] = { "articles", "titles", "media", "dictionary" };
			int j;
			e26j_lit(b, "{");
			for (j = 0; j < 4; j++) {
				int64_t x = 0;
				if (c) e26j_i64(json_get(c, keys[j]), 0, INT64_MAX, &x);
				if (j) e26j_lit(b, ",");
				e26j_cstr(b, keys[j]); e26j_lit(b, ":"); e26j_i(b, x);
			}
			e26j_lit(b, "}");
		}
		e26j_lit(b, "}");
	}
	e26j_lit(b, "]");
}
