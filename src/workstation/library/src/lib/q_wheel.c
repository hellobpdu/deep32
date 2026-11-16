/* q_wheel.c: the title wheel (W1-DATA; LIB-PROTOCOL.md "title_prefix and wheel_page", "random_title"). Wheel rows
 * sort by their folded sort key, so the rows a prefix matches are one run of wheel ordinals, found through the
 * title_prefix index; the per-row article ordinals and flags live in memory (db.c), so counting a bitmap-filtered
 * wheel of 400,000 rows is a loop, not a query. start and next are global wheel ordinals. */
#include "lib.h"
#include "../app/registry.h"
#include "../common/helper.h"
#include <stdlib.h>
#include <string.h>
#include <time.h>

void e26_q_wheel(void);

#if defined(E26_LIB_SQLITE)
#ifdef __OpenBSD__
uint32_t arc4random_uniform(uint32_t);
#endif

static const char *const ROW =
	"SELECT t.wheel_ordinal,t.title,t.id,t.article_ordinal,"
	"CASE WHEN t.catalogue_only THEN 'catalogue' ELSE a.class END AS class,t.alias,t.catalogue_only "
	"FROM title t LEFT JOIN article a ON a.ordinal=t.article_ordinal WHERE t.wheel_ordinal=?1";

static int title_row(e26j_buf *b, uint32_t w)
{
	sqlite3_stmt *st = lib_stmt(LIB_CATALOGUE, ROW);
	int rc;
	if (!st) return -1;
	sqlite3_bind_int64(st, 1, w);
	rc = sqlite3_step(st) == SQLITE_ROW ? lib_row(b, st, NULL) : -1;
	sqlite3_reset(st);
	return rc;
}

/* the first wheel ordinal whose sort key is >= key (len bytes) */
static int first_at(const char *key, size_t len, uint32_t *w)
{
	static const char *const q = "SELECT wheel_ordinal FROM title WHERE sort_key>=?1 ORDER BY sort_key,wheel_ordinal LIMIT 1";
	sqlite3_stmt *st = lib_stmt(LIB_CATALOGUE, q);
	int rc;
	if (!st) return -1;
	sqlite3_bind_text(st, 1, key, (int)len, SQLITE_STATIC);
	rc = sqlite3_step(st);
	*w = rc == SQLITE_ROW ? (uint32_t)sqlite3_column_int64(st, 0) : lib_nwheel();
	sqlite3_reset(st);
	return rc == SQLITE_ROW || rc == SQLITE_DONE ? 0 : -1;
}

static int eligible(uint32_t w, const unsigned char *bits)
{
	int32_t art;
	unsigned fl;
	lib_wheel_row(w, &art, &fl);
	return !bits || (art >= 0 && lib_bit(bits, (uint32_t)art));
}

/* one page of the wheel rows in [lo, hi) from start */
static const char *page(uint32_t lo, uint32_t hi, int64_t start, int64_t limit, const unsigned char *bits, char **result)
{
	e26j_buf b;
	uint32_t w, total = 0, from = start > (int64_t)lo ? (start < (int64_t)hi ? (uint32_t)start : hi) : lo;
	int64_t k = 0;
	int64_t next = -1;
	for (w = lo; w < hi; w++) total += (uint32_t)eligible(w, bits);
	e26j_init(&b);
	e26j_lit(&b, "{\"rows\":[");
	for (w = from; w < hi; w++) {
		if (!eligible(w, bits)) continue;
		if (k == limit) { next = w; break; }
		if (k++) e26j_lit(&b, ",");
		if (title_row(&b, w)) { e26j_free(&b); return lib_sqlerr(result); }
	}
	e26j_lit(&b, "],\"start\":");
	e26j_i(&b, start);
	e26j_lit(&b, ",\"next\":");
	if (next >= 0) e26j_i(&b, next); else e26j_lit(&b, "null");
	e26j_lit(&b, ",\"total\":");
	e26j_u(&b, total);
	e26j_lit(&b, "}");
	return lib_ok(&b, result);
}

static const char *q_title_prefix(const jv *args, char **result)
{
	const char *prefix, *code;
	char *fold, *hi;
	int64_t start, limit;
	unsigned char *bits = NULL;
	uint32_t lo, up;
	size_t n;
	if (lib_args(args, "prefix", "start", "limit", "bitmap", NULL) || lib_arg_str(args, "prefix", 4096, 1, &prefix) ||
	    lib_arg_page(args, 32, 256, &start, &limit))
		return lib_err(result, "E_ARGUMENT", NULL);
	if (!lib_ready(result)) return lib_code();
	if (lib_arg(args, "bitmap") && !(bits = lib_bitmap_arg(args, "bitmap"))) return lib_err(result, "E_ARGUMENT", NULL);
	if (!(fold = lib_fold(prefix))) { free(bits); return lib_err(result, "E_ARGUMENT", NULL); }
	n = strlen(fold);
	if (!n) { lo = 0; up = lib_nwheel(); }
	else {
		/* every key with this prefix sorts below prefix + 0xFF (no UTF-8 byte is 0xFF) */
		if (!(hi = (char *)malloc(n + 2))) { free(fold); free(bits); return lib_err(result, "E_PACK", NULL); }
		memcpy(hi, fold, n);
		hi[n] = (char)0xFF;
		hi[n + 1] = 0;
		if (first_at(fold, n, &lo) || first_at(hi, n + 1, &up)) { free(hi); free(fold); free(bits); return lib_sqlerr(result); }
		free(hi);
		if (up < lo) up = lo;
	}
	free(fold);
	code = page(lo, up, start, limit, bits, result);
	free(bits);
	return code;
}

static const char *q_wheel_page(const jv *args, char **result)
{
	int64_t start, limit;
	unsigned char *bits = NULL;
	const char *code;
	if (lib_args(args, "start", "limit", "bitmap", NULL) || lib_arg_page(args, 32, 256, &start, &limit)) return lib_err(result, "E_ARGUMENT", NULL);
	if (!lib_ready(result)) return lib_code();
	if (lib_arg(args, "bitmap") && !(bits = lib_bitmap_arg(args, "bitmap"))) return lib_err(result, "E_ARGUMENT", NULL);
	code = page(0, lib_nwheel(), start, limit, bits, result);
	free(bits);
	return code;
}

/* splitmix64: the seeded, deterministic choice (tests); unseeded choices use arc4random */
static uint64_t mix(uint64_t *s)
{
	uint64_t z = (*s += 0x9E3779B97F4A7C15ull);
	z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
	z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
	return z ^ (z >> 31);
}

static const char *q_random_title(const jv *args, char **result)
{
	unsigned char *bits = NULL;
	const jv *seedv = lib_arg(args, "seed");
	uint64_t seed = 0, pick, c = 0;
	uint32_t w, nw;
	e26j_buf b;
	if (lib_args(args, "bitmap", "seed", NULL) || (seedv && e26j_u64(seedv, &seed))) return lib_err(result, "E_ARGUMENT", NULL);
	if (!lib_ready(result)) return lib_code();
	if (lib_arg(args, "bitmap") && !(bits = lib_bitmap_arg(args, "bitmap"))) return lib_err(result, "E_ARGUMENT", NULL);
	nw = lib_nwheel();
	for (w = 0; w < nw; w++) {
		int32_t art;
		unsigned fl;
		lib_wheel_row(w, &art, &fl);
		if (!fl && eligible(w, bits)) c++;
	}
	e26j_init(&b);
	e26j_lit(&b, "{\"row\":");
	if (!c) e26j_lit(&b, "null");
	else {
		if (seedv) {
			uint64_t s = seed, lim = UINT64_MAX - UINT64_MAX % c, r;
			do r = mix(&s); while (r >= lim);   /* no modulo bias */
			pick = r % c;
		} else {
#ifdef __OpenBSD__
			pick = arc4random_uniform((uint32_t)c);
#else
			uint64_t s = (uint64_t)time(NULL) ^ (uint64_t)(size_t)&b;
			pick = mix(&s) % c;
#endif
		}
		for (w = 0; w < nw; w++) {
			int32_t art;
			unsigned fl;
			lib_wheel_row(w, &art, &fl);
			if (fl || !eligible(w, bits)) continue;
			if (!pick--) break;
		}
		if (title_row(&b, w)) { free(bits); e26j_free(&b); return lib_sqlerr(result); }
	}
	free(bits);
	e26j_lit(&b, "}");
	return lib_ok(&b, result);
}
#endif

void e26_q_wheel(void)
{
#if defined(E26_LIB_SQLITE)
	static const e26_helper_op tp = { q_title_prefix }, wp = { q_wheel_page }, rt = { q_random_title };
	reg_add(REG_QUERY, "title_prefix", &tp, "q_wheel", 0);
	reg_add(REG_QUERY, "wheel_page", &wp, "q_wheel", 0);
	reg_add(REG_QUERY, "random_title", &rt, "q_wheel", 0);
#endif
}
