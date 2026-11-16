/* search.c: runs a parsed Words request over the FTS5 table in one of the three scopes and returns the allowed-set
 * bitmap and the current article's hit offsets (W1-SEARCH; docs/SEARCH-LANGUAGE.md "Title mode and scopes"). Codex
 * T-0018 (e26-search 3e82b3b), brought in unchanged but for this note and the HAVE_SQLITE3 guard. */
#ifdef HAVE_SQLITE3
#include "search.h"

#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* No state survives a call: each connection owns its auxiliary registration. */
typedef struct {
    unsigned (*ranges)[2];
    size_t count, capacity;
    int text_bytes;
} token_ranges;

static int
set_error(e26q_err *err, int code)
{
    const char *message;

    if (err == NULL)
        return -1;
    if (code == 20100)
        message = "The Find request failed due to an out-of-memory error.";
    else if (code == 26222)
        message = "You typed an invalid search expression.  Please correct it and try again.";
    else if (code == 20102)
        message = "The Find request is too long or too complex.";
    else {
        code = 2001;
        message = "The search failed.";
    }
    err->code = code;
    (void)snprintf(err->message, sizeof(err->message), "%s", message);
    return -1;
}

static int
valid_table(const char *table)
{
    const unsigned char *p = (const unsigned char *)table;

    if (p == NULL || !((*p >= 'a' && *p <= 'z') ||
        (*p >= 'A' && *p <= 'Z') || *p == '_'))
        return 0;
    for (++p; *p != '\0'; ++p) {
        if (!((*p >= 'a' && *p <= 'z') ||
            (*p >= 'A' && *p <= 'Z') ||
            (*p >= '0' && *p <= '9') || *p == '_'))
            return 0;
    }
    return 1;
}

static int
collect_token(void *context, int flags, const char *token, int token_bytes,
    int start, int end)
{
    token_ranges *r = (token_ranges *)context;
    size_t capacity;
    void *grown;

    (void)token;
    (void)token_bytes;
    /* unicode61 has no synonyms; retain its token-position convention. */
    if ((flags & FTS5_TOKEN_COLOCATED) != 0)
        return SQLITE_OK;
    if (start < 0 || end < start || end > r->text_bytes)
        return SQLITE_ERROR;
    if (r->count == r->capacity) {
        if (r->capacity == 0)
            capacity = 32;
        else {
            if (r->capacity > SIZE_MAX / 2)
                return SQLITE_NOMEM;
            capacity = r->capacity * 2;
        }
        if (capacity > SIZE_MAX / sizeof(*r->ranges))
            return SQLITE_NOMEM;
        grown = realloc(r->ranges, capacity * sizeof(*r->ranges));
        if (grown == NULL)
            return SQLITE_NOMEM;
        r->ranges = grown;
        r->capacity = capacity;
    }
    r->ranges[r->count][0] = (unsigned)start;
    r->ranges[r->count][1] = (unsigned)end;
    ++r->count;
    return SQLITE_OK;
}

/* Return host unsigned pairs in a SQLite BLOB, consumed on this connection. */
static void
hits_aux(const Fts5ExtensionApi *api, Fts5Context *fts,
    sqlite3_context *context, int nargs, sqlite3_value **args)
{
    token_ranges tokens = { NULL, 0, 0, 0 };
    const char *text = NULL;
    unsigned char *selected = NULL;
    unsigned (*hits)[2] = NULL;
    size_t nhits = 0, i;
    int ninst = 0, rc, inst;

    (void)args;
    if (nargs != 0) {
        sqlite3_result_error(context, "The search failed.", -1);
        return;
    }
    rc = api->xInstCount(fts, &ninst);
    if (rc != SQLITE_OK)
        goto failed;
    rc = api->xColumnText(fts, 1, &text, &tokens.text_bytes);
    if (rc != SQLITE_OK)
        goto failed;
    if (tokens.text_bytes < 0 || (tokens.text_bytes > 0 && text == NULL)) {
        rc = SQLITE_ERROR;
        goto failed;
    }
    rc = api->xTokenize(fts, text == NULL ? "" : text,
        tokens.text_bytes, &tokens, collect_token);
    if (rc != SQLITE_OK)
        goto failed;
    if (tokens.count != 0) {
        selected = calloc(tokens.count, sizeof(*selected));
        if (selected == NULL) {
            rc = SQLITE_NOMEM;
            goto failed;
        }
    }
    for (inst = 0; inst < ninst; ++inst) {
        int phrase, column, position, size;
        size_t first, length;

        rc = api->xInst(fts, inst, &phrase, &column, &position);
        if (rc != SQLITE_OK)
            goto failed;
        if (column != 1)
            continue;
        size = api->xPhraseSize(fts, phrase);
        if (position < 0 || size < 1) {
            rc = SQLITE_ERROR;
            goto failed;
        }
        first = (size_t)position;
        length = (size_t)size;
        if (first > tokens.count || length > tokens.count - first) {
            rc = SQLITE_ERROR;
            goto failed;
        }
        for (i = first; i < first + length; ++i)
            selected[i] = 1;
    }
    for (i = 0; i < tokens.count; ++i)
        if (selected[i] != 0)
            ++nhits;
    if (nhits > UINT_MAX || nhits > SIZE_MAX / sizeof(*hits)) {
        rc = SQLITE_NOMEM;
        goto failed;
    }
    if (nhits != 0) {
        size_t at = 0;

        hits = malloc(nhits * sizeof(*hits));
        if (hits == NULL) {
            rc = SQLITE_NOMEM;
            goto failed;
        }
        for (i = 0; i < tokens.count; ++i) {
            if (selected[i] != 0) {
                hits[at][0] = tokens.ranges[i][0];
                hits[at][1] = tokens.ranges[i][1];
                ++at;
            }
        }
        sqlite3_result_blob64(context, hits,
            (sqlite3_uint64)(nhits * sizeof(*hits)), SQLITE_TRANSIENT);
    } else
        sqlite3_result_blob(context, "", 0, SQLITE_STATIC);
    free(hits);
    free(selected);
    free(tokens.ranges);
    return;

failed:
    free(hits);
    free(selected);
    free(tokens.ranges);
    if (rc == SQLITE_NOMEM)
        sqlite3_result_error_nomem(context);
    else
        sqlite3_result_error(context, "The search failed.", -1);
}

int
e26s_register_hits(sqlite3 *db)
{
    fts5_api *api = NULL;
    sqlite3_stmt *statement = NULL;
    int rc, step_rc, final_rc;

    rc = sqlite3_prepare_v2(db, "SELECT fts5(?1)", -1,
        &statement, NULL);
    if (rc != SQLITE_OK)
        return rc;
    rc = sqlite3_bind_pointer(statement, 1, &api, "fts5_api_ptr", NULL);
    if (rc == SQLITE_OK) {
        step_rc = sqlite3_step(statement);
        rc = step_rc == SQLITE_ROW ? SQLITE_OK : step_rc;
    }
    final_rc = sqlite3_finalize(statement);
    if (rc == SQLITE_OK && final_rc != SQLITE_OK)
        rc = final_rc;
    if (rc != SQLITE_OK)
        return rc;
    if (api == NULL || api->xCreateFunction == NULL)
        return SQLITE_ERROR;
    return api->xCreateFunction(api, "e26_hits", NULL, hits_aux, NULL);
}

void
e26s_result_free(e26s_result *r)
{
    if (r == NULL)
        return;
    free(r->bits);
    free(r->hits);
    memset(r, 0, sizeof(*r));
}

int
e26s_run(sqlite3 *db, const char *table, const e26q_ast *ast,
    e26s_scope scope, unsigned ndocs, const unsigned char *last_bits,
    long long doc_rowid, e26s_result *out, e26q_err *err)
{
    e26s_result result = { NULL, 0, 0, NULL, 0 };
    sqlite3_stmt *statement = NULL;
    char *query = NULL, *sql = NULL;
    size_t bitmap_bytes, capacity = 1024, table_len, sql_bytes;
    int rc = SQLITE_OK, query_bytes, code = 2001;

    if (out == NULL)
        return set_error(err, 26222);
    memset(out, 0, sizeof(*out));
    if (err != NULL)
        memset(err, 0, sizeof(*err));
    if (db == NULL || ast == NULL || !valid_table(table) ||
        (scope != E26S_ALL && scope != E26S_CURRENT && scope != E26S_LAST) ||
        (scope == E26S_LAST && last_bits == NULL) ||
        (scope == E26S_CURRENT &&
        (doc_rowid < 1 || (sqlite3_uint64)doc_rowid > ndocs)))
        return set_error(err, 26222);
    bitmap_bytes = (size_t)(ndocs / 8U) + ((ndocs % 8U) != 0U ? 1U : 0U);
    result.ndocs = ndocs;
    if (bitmap_bytes != 0) {
        result.bits = calloc(bitmap_bytes, sizeof(*result.bits));
        if (result.bits == NULL) {
            code = 20100;
            goto failed;
        }
    }
    for (;;) {
        void *grown = realloc(query, capacity);

        if (grown == NULL) {
            code = 20100;
            goto failed;
        }
        query = grown;
        query_bytes = e26q_to_fts5(ast, query, capacity);
        if (query_bytes >= 0)
            break;
        if (capacity > (size_t)INT_MAX / 2) {
            code = 20102;
            goto failed;
        }
        capacity *= 2;
    }
    table_len = strlen(table);
    if (table_len > (SIZE_MAX - 256) / 4) {
        code = 20100;
        goto failed;
    }
    sql_bytes = table_len * 4 + 256;
    sql = malloc(sql_bytes);
    if (sql == NULL) {
        code = 20100;
        goto failed;
    }
    if (scope == E26S_CURRENT) {
        rc = e26s_register_hits(db);
        if (rc != SQLITE_OK)
            goto sqlite_failed;
        (void)snprintf(sql, sql_bytes,
            "SELECT rowid,e26_hits(\"%s\") FROM \"%s\" "
            "WHERE \"%s\" MATCH ?1 AND rowid=?2", table, table, table);
    } else {
        (void)snprintf(sql, sql_bytes,
            "SELECT rowid FROM \"%s\" WHERE \"%s\" MATCH ?1 "
            "AND rowid BETWEEN 1 AND ?2", table, table);
    }
    rc = sqlite3_prepare_v2(db, sql, -1, &statement, NULL);
    if (rc != SQLITE_OK)
        goto sqlite_failed;
    rc = sqlite3_bind_text(statement, 1, query, query_bytes, SQLITE_STATIC);
    if (rc != SQLITE_OK)
        goto sqlite_failed;
    rc = sqlite3_bind_int64(statement, 2,
        scope == E26S_CURRENT ? (sqlite3_int64)doc_rowid : (sqlite3_int64)ndocs);
    if (rc != SQLITE_OK)
        goto sqlite_failed;
    while ((rc = sqlite3_step(statement)) == SQLITE_ROW) {
        sqlite3_int64 rowid = sqlite3_column_int64(statement, 0);
        unsigned ordinal;

        if (rowid < 1 || (sqlite3_uint64)rowid > ndocs)
            continue;
        ordinal = (unsigned)(rowid - 1);
        if (scope == E26S_LAST &&
            (last_bits[ordinal / 8U] & (1U << (ordinal % 8U))) == 0)
            continue;
        result.bits[ordinal / 8U] |= (unsigned char)(1U << (ordinal % 8U));
        ++result.count;
        if (scope == E26S_CURRENT) {
            const void *blob = sqlite3_column_blob(statement, 1);
            int nbytes = sqlite3_column_bytes(statement, 1);
            size_t length;

            if (nbytes < 0 || (size_t)nbytes % sizeof(*result.hits) != 0 ||
                (nbytes != 0 && blob == NULL)) {
                rc = sqlite3_errcode(db) == SQLITE_NOMEM ? SQLITE_NOMEM : SQLITE_ERROR;
                goto sqlite_failed;
            }
            length = (size_t)nbytes / sizeof(*result.hits);
            if (length > UINT_MAX) {
                rc = SQLITE_NOMEM;
                goto sqlite_failed;
            }
            if (nbytes != 0) {
                result.hits = malloc((size_t)nbytes);
                if (result.hits == NULL) {
                    code = 20100;
                    goto failed;
                }
                memcpy(result.hits, blob, (size_t)nbytes);
            }
            result.nhits = (unsigned)length;
        }
    }
    if (rc != SQLITE_DONE)
        goto sqlite_failed;
    rc = sqlite3_finalize(statement);
    statement = NULL;
    if (rc != SQLITE_OK)
        goto sqlite_failed;
    free(sql);
    free(query);
    *out = result;
    return 0;

sqlite_failed:
    code = (rc & 0xff) == SQLITE_NOMEM ? 20100 : 2001;
failed:
    if (statement != NULL)
        (void)sqlite3_finalize(statement);
    free(sql);
    free(query);
    e26s_result_free(&result);
    return set_error(err, code);
}

#else
typedef int e26s_no_sqlite3; /* ISO C wants one declaration in every file */
#endif
