/* lib.h: e26-lib's inside (W1-DATA): the opened pack, its databases and reader store, and the helpers every named
 * query uses. A query is one function in a src/lib/q_<name>.c file, registered in REG_QUERY with an
 * e26_helper_op (helper.h); proto.c runs it with the request's "args" object and wraps what it returns in
 * LIB-PROTOCOL's envelope. The seam with W1-SEARCH is section "SEARCH" below and docs/rooms/DATA.md.
 *
 * Conventions for a query's run(args, &result):
 *   - check the arguments first: lib_args(args, "id", "start", "limit", NULL) refuses unlisted keys, then the
 *     lib_arg_* readers check each one; any failure is return lib_err(result, "E_ARGUMENT", NULL);
 *   - need the pack: if (!lib_ready(result)) return lib_code();   (E_PACK, E_VERSION or E_STALE with its message)
 *   - build the result with an e26j_buf, then return lib_ok(&b, result) (NULL, or E_PACK when the text is bad);
 *   - SQLite failures are lib_sqlerr(result): E_CANCELLED or E_TIMEOUT when proto.c interrupted the statement
 *     (a cancellation or the 5 s deadline), else E_PACK.
 * Search errors are numeric (T-0018): return "2200" and the exact message; proto.c writes the code as a number. */
#ifndef E26_LIB_H
#define E26_LIB_H
#include <stddef.h>
#include <stdint.h>
#include "../app/json.h"
#include "../common/e26j.h"

#if defined(E26_LIB_SQLITE)
#include <sqlite3.h>
#endif

/* ---- the pack (db.c) */
enum { LIB_CATALOGUE, LIB_SEARCH, LIB_FACETS, LIB_LINKS, LIB_DICTIONARY, LIB_NDB };

/* opens the generation behind packdir (resolved once) and the encyclopedia directory's ACTIVE layers; never fails:
 * a pack that cannot be used leaves the helper answering every query with the reason (lib_ready) */
void lib_open(const char *packdir, const char *encdir);
void lib_close(void);
/* the paths lib_open resolved (for unveil), "" when none */
const char *lib_pack_path(void);
const char *lib_enc_path(void);
/* 1 when the pack is usable; else 0 with *result the user message, and lib_code() the error code */
int lib_ready(char **result);
const char *lib_code(void);
const char *lib_generation(void);  /* NULL without a pack */
uint32_t lib_ndocs(void);          /* N: canonical articles, the bitmap universe */
uint32_t lib_nwheel(void);         /* wheel rows */
/* wheel row w (< lib_nwheel()): its article ordinal (-1 for a catalogue-only row) and flags (1 alias, 2 catalogue-only) */
void lib_wheel_row(uint32_t w, int32_t *article, unsigned *flags);
const char *lib_pack_json(void);   /* PACK.json's text */
/* "sqlite zim icu": what this e26-lib was built with (e26-lib -features) */
const char *lib_features(void);

#if defined(E26_LIB_SQLITE)
sqlite3 *lib_db(int which);
/* prepares sql on database which (cached by the sql text's address: pass string literals); NULL on failure */
sqlite3_stmt *lib_stmt(int which, const char *sql);
#endif

/* ---- errors: the code to return, with *result the exact user message of LIB-PROTOCOL (or msg when given) */
const char *lib_err(char **result, const char *code, const char *msg);
const char *lib_sqlerr(char **result);
const char *lib_ok(e26j_buf *b, char **result);

/* ---- arguments */
int lib_args(const jv *args, ...);                        /* 0 when every key of args is in the NULL-ended list */
const jv *lib_arg(const jv *args, const char *key);       /* NULL when absent */
/* 0 and *out (default when absent) in [lo, hi]; -1 when present and not an integer in range */
int lib_arg_int(const jv *args, const char *key, int64_t lo, int64_t hi, int64_t def, int64_t *out);
/* a string of 1..max bytes (0..max when empty_ok); -1 when absent or invalid */
int lib_arg_str(const jv *args, const char *key, size_t max, int empty_ok, const char **out);
int lib_arg_bool(const jv *args, const char *key, int def, int *out);
/* start (default 0) and limit (1..max, default def) */
int lib_arg_page(const jv *args, int64_t def, int64_t max, int64_t *start, int64_t *limit);

/* ---- bitmaps (LIB-PROTOCOL "Bitmap"): ceil(N/8) bytes, ordinal i at bit i%8 of byte i/8 */
size_t lib_bitmap_bytes(void);
/* the canonical base64 bitmap at args[key] decoded (malloc'd); NULL when invalid */
unsigned char *lib_bitmap_arg(const jv *args, const char *key);
static inline int lib_bit(const unsigned char *bits, uint32_t i) { return bits[i >> 3] >> (i & 7) & 1; }
static inline void lib_setbit(unsigned char *bits, uint32_t i) { bits[i >> 3] = (unsigned char)(bits[i >> 3] | 1u << (i & 7)); }
void lib_bitmap_out(e26j_buf *b, const unsigned char *bits);   /* the base64 string */
uint32_t lib_popcount(const unsigned char *bits);

/* ---- articles */
/* resolves an id (or an alias title, as the reference reader does): 0 with the ordinal and canonical id
 * (malloc'd), or an error code with *result set */
const char *lib_resolve(const jv *args, const char *key, uint32_t *ordinal, char **id, char **result);
/* a reader article split into records: rec[0] is meta, rec[1..] the blocks as JSON objects ({"type":...}) */
typedef struct {
	char *id;
	char *meta;          /* the meta object's JSON text */
	char **block;        /* the blocks' JSON text */
	size_t *blen;
	size_t nblocks;
	size_t bytes;
} lib_article;
/* the article at ordinal (cached; do not free): NULL and an error code via *code */
const lib_article *lib_article_get(uint32_t ordinal, const char **code);
/* NFKC then full case folding (ICU when built with it; else ASCII lower case): malloc'd UTF-8, or NULL */
char *lib_fold(const char *s);
/* reads a media item: 1 the bytes (malloc'd, *len, *total), 0 the pack is absent (optional), -1 corrupt or failing */
int lib_media_read(const char *pack, const char *path, uint64_t offset, size_t limit, unsigned char **data, size_t *len,
                   uint64_t *total);

#if defined(E26_LIB_SQLITE)
/* writes one row of a stepped statement as an object: every column by name; "_json" columns validated and spliced
 * under the name without the suffix; alias, catalogue_only and approved as booleans; extra (NULL or ",\"k\":v")
 * appended before the closing brace. -1 when a value is not valid (the pack is corrupt). */
int lib_row(e26j_buf *b, sqlite3_stmt *st, const char *extra);
/* rows collected before paging (de-duplication or re-ordering first) */
typedef struct { char **row; size_t n, cap; } lib_list;
int lib_list_add(lib_list *l, sqlite3_stmt *st, const char *extra);   /* -1 when the row is not valid */
/* "rows":[...],"start":S,"next":N|null,"total":T (members, no braces) for rows [start, start+limit) */
void lib_list_page(e26j_buf *b, const lib_list *l, int64_t start, int64_t limit);
void lib_list_free(lib_list *l);
/* steps st to the end into l; 0, or an error code with *result set */
const char *lib_list_all(lib_list *l, sqlite3_stmt *st, const char *(*extra)(sqlite3_stmt *), char **result);
#endif

/* ---- storage revision 2 (storage.c; h2-index docs/SIZE-PASS.md): e26_unzstd, e26_cell and e26_body on a
 * connection; body(ud, reader_path, &len) gives the canonical FTS body (malloc'd) or NULL */
typedef char *(*lib_body_fn)(void *ud, const char *reader_path, size_t *len);
char *lib_body_text(const lib_article *a, size_t *len);   /* reader.py plain_text of an article (malloc'd) */
#if defined(E26_LIB_SQLITE)
int lib_storage_register(sqlite3 *db, lib_body_fn body, void *ud);
#endif
/* 1 when Related adds the indexed same-primary-subcategory members at query time (storage revision 2, or links
 * meta related_mode primary-category); links.sqlite then has facets attached as fac and the catalogue as cat */
int lib_lazy_related(void);

/* ---- several packs (PACK-CORPUS.md section 8, CR-CORPUS-2 as far as id-addressed queries go): pack 0 is the one
 * lib_open opened; lib_open_extra adds lib-corpus or lib-reserved behind their pointers. Queries run on the selected
 * pack; proto.c selects the pack holding a request's id for the id-addressed queries, then pack 0 again. */
#define LIB_NPACK 3
/* the libcontent root that PACK.json requires[].path is relative to (default: the encyclopedia directory's parent) */
void lib_set_root(const char *root);
const char *lib_root_path(void);
void lib_open_extra(const char *packdir);
int lib_npacks(void);
void lib_select(int i);
/* the pack for op's id argument (article, media or anchor holder), 0 when no other pack holds it */
int lib_route(const char *op, const jv *args);
/* "packs":[{name,generation,present,handling,counts}] (a member, no braces) for status */
void lib_packs_json(e26j_buf *b);

/* Optional signed public catalogue and exact reader identities for finder.cisco.* only. */
void lib_cisco_set_path(const char *path, int explicit_path);
#if defined(E26_LIB_SQLITE)
int lib_public_pack(const char *name, const char **generation, const char **catalogue_hash);
int lib_cisco_identity(int pack, uint32_t ordinal, const char *id, const char *doc_key,
                       const char *doc_id, int part);
#endif

/* ---- the running request (proto.c): the SQLite progress handler interrupts on a cancellation or the deadline */
int lib_interrupted(void);          /* 0, or 1 cancelled, 2 timed out */
void lib_poll(void);                /* reads waiting frames (cancellations) without blocking; long loops call it */

/* ---- SEARCH (W1-SEARCH's src/lib/q_search.c registers "search"; see docs/rooms/DATA.md):
 * lib_db(LIB_SEARCH) is search.sqlite with its docs FTS5 table (rowid = ordinal + 1), lib_ndocs() the universe,
 * lib_resolve the current article, lib_bitmap_arg the "last" bitmap and lib_bitmap_out the result. */

/* proto.c */
int lib_serve(int fd);

#endif
