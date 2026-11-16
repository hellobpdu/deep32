#ifndef E26_CISCO_ROUTE_H
#define E26_CISCO_ROUTE_H
#include "lib.h"

#define CISCO_CATALOGUE_PATH "/d32/libcontent/cisco/cisco-public.sqlite"

#ifdef E26_LIB_SQLITE
#define CISCO_MAX_ROWS 4096
#define CISCO_ID_MAX 256
#define CISCO_SORT_MAX 4096
#define CISCO_COLUMNS "catalogue_id,reader_pack,reader_generation,article_id,article_ordinal,doc_key,subtype,parts_of,row_json,sort_key,part,title"

struct cisco_store {
	int index;
	const char *installed;
	const char *installed_hash;
	char expected[129], expected_hash[65];
};

struct cisco_catalogue {
	sqlite3 *db;
	char generation[129];
	struct cisco_store store[2];
};

/* The strings borrow the stepped statement's row, until it is reset or finalized. */
struct cisco_row {
	const char *catalogue_id, *reader_pack, *reader_generation, *article_id;
	const char *doc_key, *subtype, *parts_of, *record_json, *sort_key, *title;
	int ordinal, store, part;   /* part is -1 for SQL NULL */
};

const char *cisco_catalogue_open(struct cisco_catalogue *, const jv *, int, char **);
void cisco_catalogue_close(struct cisco_catalogue *);
int cisco_store_number(const char *);
int cisco_store_available(const struct cisco_catalogue *, int);
const char *cisco_store_ready(const struct cisco_catalogue *, int, char **);
void cisco_stores_json(const struct cisco_catalogue *, e26j_buf *);
const char *cisco_row_verify(const struct cisco_catalogue *, sqlite3_stmt *, struct cisco_row *, char **);
const char *cisco_row_get(const struct cisco_catalogue *, const char *, sqlite3_stmt **, struct cisco_row *, char **);
const char *lib_cisco_page(const jv *, char **);
const char *lib_cisco_search(const jv *, char **);
#endif
#endif
