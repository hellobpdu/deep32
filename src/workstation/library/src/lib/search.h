/* search.h: the FTS5 run (W1-SEARCH, Codex T-0018). Bit (rowid-1) of the result is the article ordinal; hits are
 * the current article's matched body tokens, half-open UTF-8 byte ranges. Free a result with e26s_result_free. */
#ifndef E26_SEARCH_H
#define E26_SEARCH_H

#include <sqlite3.h>
#include "query.h"

typedef enum { E26S_ALL, E26S_CURRENT, E26S_LAST } e26s_scope;
typedef struct {
    unsigned char *bits;
    unsigned ndocs, count;
    unsigned (*hits)[2];
    unsigned nhits;
} e26s_result;

int e26s_run(sqlite3 *db, const char *table, const e26q_ast *ast,
    e26s_scope scope, unsigned ndocs, const unsigned char *last_bits,
    long long doc_rowid, e26s_result *out, e26q_err *err);
void e26s_result_free(e26s_result *r);
int e26s_register_hits(sqlite3 *db);

#endif
