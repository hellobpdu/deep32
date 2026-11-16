/* query.c: the Deep32 Library Words language to an FTS5 MATCH expression (W1-SEARCH; docs/SEARCH-LANGUAGE.md). Codex
 * T-0018 (e26-search 3e82b3b), brought in unchanged but for this note and the HAVE_SQLITE3 guard: the parser uses
 * SQLite's own unicode61 tokenizer, so without SQLite (Makefile HAVE_SQLITE3) this file is empty. */
#ifdef HAVE_SQLITE3
#include "query.h"

#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <sqlite3.h>

enum node_kind { NODE_QUERY, NODE_TERM, NODE_AND, NODE_OR, NODE_NOT, NODE_NEAR };

struct e26q_ast {
    enum node_kind kind;
    int title_mode;
    int prefix;
    int distance;
    char *text;
    struct e26q_ast *left;
    struct e26q_ast *right;
};

enum lex_kind { L_END, L_TERM, L_OPEN, L_CLOSE, L_AND, L_OR, L_NOT, L_NEAR };

struct lexeme {
    enum lex_kind kind;
    e26q_ast *node;
};

struct parser {
    /* Each lexeme consumes at least one of the at most 127 input scalars. */
    struct lexeme lex[128];
    size_t nlex;
    size_t at;
    int saw_stop;
    int near_default;
    e26q_err *err;
    sqlite3 *db;
    fts5_tokenizer tokenizer;
    Fts5Tokenizer *tokenizer_instance;
};

struct token_text {
    char *text;
    size_t len;
    size_t count;
    int only_stop;
};

static const char *
error_message(int code)
{
    switch (code) {
    case 2001: return "The search failed.";
    case 2018: return "Cannot use quotes or NEAR to search this title.";
    case 2200: return "The query is empty or has no searchable words. You cannot search for words marked as stop words (for example, \"the\", \"that\", or \"a\").";
    case 2201: return "The query or a subexpression within parentheses begins or ends with an operator.";
    case 2213: return "The query contains a word consisting only of combination of wildcard characters (* and ?).";
    case 20100: return "The Find request failed due to an out-of-memory error.";
    case 20101: return "Your Find request is too long.  You can have a maximum of 127 characters.";
    case 20102: return "The Find request is too long or too complex.";
    case 26212: return "The NEAR value must be a number between 1 and 9999.";
    case 26221: return "You used an '*' as a search word by itself.  To use the '*' wildcard, put one or more characters in front of it.";
    case 26223: return "You have mismatched parenthesis in your search expression.  Please correct it and try again.";
    case 26224: return "You have mismatched quotes in your search expression.  Please correct it and try again.";
    case 26225: return "You cannot have an expression that consists only of stop words such as 'the', 'that', or 'a'.";
    default: return "You typed an invalid search expression.  Please correct it and try again.";
    }
}

static int
fail(e26q_err *err, int code)
{
    if (err != NULL) {
        err->code = code;
        (void)snprintf(err->message, sizeof(err->message), "%s", error_message(code));
    }
    return -1;
}

static int
sql_fail(e26q_err *err, int rc)
{
    return fail(err, rc == SQLITE_NOMEM ? 20100 : 2001);
}

/* Decode precisely one non-NUL Unicode scalar. The caller supplies a C string. */
static int
scalar(const unsigned char *p, unsigned *value, size_t *width)
{
    unsigned c = p[0];
    unsigned v;
    size_t n;
    size_t i;

    if (c < 0x80U) {
        *value = c;
        *width = 1;
        return c != 0U ? 0 : -1;
    }
    if (c >= 0xc2U && c <= 0xdfU) {
        v = c & 0x1fU;
        n = 2;
    } else if (c >= 0xe0U && c <= 0xefU) {
        v = c & 0x0fU;
        n = 3;
    } else if (c >= 0xf0U && c <= 0xf4U) {
        v = c & 0x07U;
        n = 4;
    } else {
        return -1;
    }
    for (i = 1; i < n; ++i) {
        if (p[i] < 0x80U || p[i] > 0xbfU)
            return -1;
        v = (v << 6) | (p[i] & 0x3fU);
    }
    if ((n == 3 && v < 0x800U) || (n == 4 && v < 0x10000U) ||
        (v >= 0xd800U && v <= 0xdfffU) || v > 0x10ffffU)
        return -1;
    *value = v;
    *width = n;
    return 0;
}

/* Python str.isspace(), including U+001C..U+001F rather than just White_Space. */
static int
is_space(unsigned c)
{
    return (c >= 0x09U && c <= 0x0dU) || (c >= 0x1cU && c <= 0x20U) ||
        c == 0x85U || c == 0xa0U || c == 0x1680U ||
        (c >= 0x2000U && c <= 0x200aU) || c == 0x2028U ||
        c == 0x2029U || c == 0x202fU || c == 0x205fU || c == 0x3000U;
}

static int
delimiter(const char *text)
{
    unsigned c;
    size_t width;

    if (*text == '\0')
        return 1;
    /* The full input has already passed scalar validation. */
    if (scalar((const unsigned char *)text, &c, &width) != 0)
        return 0;
    return is_space(c) || c == '(' || c == ')' || c == '"';
}

static int
stop_word(const char *word, int n)
{
    return (n == 1 && memcmp(word, "a", 1) == 0) ||
        (n == 2 && (memcmp(word, "an", 2) == 0 || memcmp(word, "of", 2) == 0)) ||
        (n == 3 && memcmp(word, "the", 3) == 0) ||
        (n == 4 && memcmp(word, "that", 4) == 0);
}

static int
append_token(void *context, int flags, const char *word, int n,
    int start, int end)
{
    struct token_text *tokens = context;
    size_t sep = tokens->count != 0 ? 1U : 0U;
    size_t added;
    char *grown;

    (void)flags;
    (void)start;
    (void)end;
    if (n < 0)
        return SQLITE_ERROR;
    added = (size_t)n + sep;
    if (added > (size_t)-1 - tokens->len - 1U)
        return SQLITE_NOMEM;
    grown = realloc(tokens->text, tokens->len + added + 1U);
    if (grown == NULL)
        return SQLITE_NOMEM;
    tokens->text = grown;
    if (sep != 0)
        tokens->text[tokens->len++] = ' ';
    memcpy(tokens->text + tokens->len, word, (size_t)n);
    tokens->len += (size_t)n;
    tokens->text[tokens->len] = '\0';
    ++tokens->count;
    if (!stop_word(word, n))
        tokens->only_stop = 0;
    return SQLITE_OK;
}

static int
open_tokenizer(struct parser *p)
{
    fts5_api *api = NULL;
    sqlite3_stmt *stmt = NULL;
    void *context = NULL;
    const char *args[2] = { "remove_diacritics", "2" };
    int rc;

    if (p->tokenizer_instance != NULL)
        return 0;
    rc = sqlite3_open(":memory:", &p->db);
    if (rc != SQLITE_OK)
        return sql_fail(p->err, rc);
    rc = sqlite3_prepare_v2(p->db, "SELECT fts5(?1)", -1, &stmt, NULL);
    if (rc == SQLITE_OK)
        rc = sqlite3_bind_pointer(stmt, 1, &api, "fts5_api_ptr", NULL);
    if (rc == SQLITE_OK) {
        rc = sqlite3_step(stmt);
        if (rc == SQLITE_ROW && api != NULL)
            rc = SQLITE_OK;
        else if (rc == SQLITE_ROW || rc == SQLITE_DONE)
            rc = SQLITE_ERROR;
    }
    {
        int final_rc = sqlite3_finalize(stmt);
        if (rc == SQLITE_OK && final_rc != SQLITE_OK)
            rc = final_rc;
    }
    if (rc != SQLITE_OK)
        return sql_fail(p->err, rc);
    rc = api->xFindTokenizer(api, "unicode61", &context, &p->tokenizer);
    if (rc == SQLITE_OK)
        rc = p->tokenizer.xCreate(context, args, 2, &p->tokenizer_instance);
    if (rc != SQLITE_OK)
        return sql_fail(p->err, rc);
    return 0;
}

void
e26q_free(e26q_ast *ast)
{
    if (ast == NULL)
        return;
    e26q_free(ast->left);
    e26q_free(ast->right);
    free(ast->text);
    free(ast);
}

static e26q_ast *
new_node(struct parser *p, enum node_kind kind)
{
    e26q_ast *node = calloc(1, sizeof(*node));

    if (node == NULL)
        (void)fail(p->err, 20100);
    else
        node->kind = kind;
    return node;
}

static int
lex_term(struct parser *p, const char *raw, size_t len, int prefix)
{
    struct token_text tokens;
    e26q_ast *node = NULL;
    size_t i;
    int only_wild = len != 0;
    int has_star = 0;
    int has_question = 0;
    int rc;

    if (len == 1 && raw[0] == '*')
        return fail(p->err, 26221);
    for (i = 0; i < len; ++i) {
        if (raw[i] != '*' && raw[i] != '?')
            only_wild = 0;
        if (raw[i] == '*')
            has_star = 1;
        if (raw[i] == '?')
            has_question = 1;
    }
    if (only_wild)
        return fail(p->err, 2213);
    if (has_star)
        return fail(p->err, 26221);
    if (has_question)
        return fail(p->err, 26222);
    if (open_tokenizer(p) != 0)
        return -1;
    memset(&tokens, 0, sizeof(tokens));
    tokens.only_stop = 1;
    rc = p->tokenizer.xTokenize(p->tokenizer_instance, &tokens,
        FTS5_TOKENIZE_DOCUMENT, raw, (int)len, append_token);
    if (rc != SQLITE_OK) {
        free(tokens.text);
        return sql_fail(p->err, rc);
    }
    if (prefix && tokens.count == 0) {
        free(tokens.text);
        return fail(p->err, 26221);
    }
    if (tokens.count != 0 && !prefix && tokens.only_stop) {
        p->saw_stop = 1;
        free(tokens.text);
    } else if (tokens.count != 0) {
        node = new_node(p, NODE_TERM);
        if (node == NULL) {
            free(tokens.text);
            return -1;
        }
        node->text = tokens.text;
        node->prefix = prefix;
    } else {
        free(tokens.text);
    }
    p->lex[p->nlex].kind = L_TERM;
    p->lex[p->nlex++].node = node;
    return 0;
}

static int
word_is(const char *raw, size_t len, const char *op)
{
    size_t i;

    if (strlen(op) != len)
        return 0;
    for (i = 0; i < len; ++i) {
        unsigned char c = (unsigned char)raw[i];
        if (c >= 'A' && c <= 'Z')
            c = (unsigned char)(c + ('a' - 'A'));
        if (c != (unsigned char)op[i])
            return 0;
    }
    return 1;
}

static int
lex_input(struct parser *p, const char *text, int title_mode)
{
    const char *at = text;
    int depth = 0;

    while (*at != '\0') {
        unsigned c;
        size_t width;
        const char *start;
        size_t len;
        enum lex_kind kind;
        int prefix;
        int only_wild;
        size_t i;

        if (scalar((const unsigned char *)at, &c, &width) != 0)
            return fail(p->err, 26222);
        if (is_space(c)) {
            at += width;
            continue;
        }
        if (c == '(' || c == ')') {
            depth += c == '(' ? 1 : -1;
            if (depth > 5)
                return fail(p->err, 20102);
            if (depth < 0)
                return fail(p->err, 26223);
            p->lex[p->nlex++].kind = c == '(' ? L_OPEN : L_CLOSE;
            ++at;
            continue;
        }
        if (c == '"') {
            start = at + 1;
            at = strchr(start, '"');
            if (at == NULL)
                return fail(p->err, 26224);
            len = (size_t)(at - start);
            ++at;
            prefix = *at == '*';
            if (prefix) {
                ++at;
                if (!delimiter(at))
                    return fail(p->err, 26221);
            }
            if (lex_term(p, start, len, prefix) != 0)
                return -1;
            continue;
        }
        start = at;
        while (*at != '\0' && !delimiter(at)) {
            if (scalar((const unsigned char *)at, &c, &width) != 0)
                return fail(p->err, 26222);
            at += width;
        }
        len = (size_t)(at - start);
        if (word_is(start, len, "and"))
            kind = L_AND;
        else if (word_is(start, len, "or"))
            kind = L_OR;
        else if (word_is(start, len, "not"))
            kind = L_NOT;
        else if (word_is(start, len, "near"))
            kind = L_NEAR;
        else
            kind = L_TERM;
        if (kind != L_TERM) {
            if (title_mode && kind == L_NEAR)
                return fail(p->err, 2018);
            p->lex[p->nlex++].kind = kind;
            continue;
        }
        only_wild = len != 0;
        for (i = 0; i < len; ++i)
            if (start[i] != '*' && start[i] != '?')
                only_wild = 0;
        prefix = !only_wild && len != 0 && start[len - 1] == '*';
        if (lex_term(p, start, len - (prefix ? 1U : 0U), prefix) != 0)
            return -1;
    }
    return depth != 0 ? fail(p->err, 26223) : 0;
}

static enum lex_kind
peek(const struct parser *p)
{
    return p->at < p->nlex ? p->lex[p->at].kind : L_END;
}

static int expression(struct parser *, int, e26q_ast **);

static int
atom(struct parser *p, e26q_ast **out)
{
    enum lex_kind kind = peek(p);

    *out = NULL;
    if (kind == L_TERM) {
        *out = p->lex[p->at].node;
        p->lex[p->at++].node = NULL;
        return 0;
    }
    if (kind == L_OPEN) {
        ++p->at;
        if (peek(p) == L_CLOSE)
            return fail(p->err, 26222);
        if (expression(p, 0, out) != 0)
            return -1;
        if (peek(p) != L_CLOSE) {
            e26q_free(*out);
            *out = NULL;
            return fail(p->err, 26223);
        }
        ++p->at;
        return 0;
    }
    return fail(p->err, 2201);
}

static int
priority(enum lex_kind kind)
{
    switch (kind) {
    case L_OR: return 1;
    case L_AND: return 2;
    case L_NOT: return 3;
    case L_NEAR: return 4;
    default: return 0;
    }
}

static int
expression(struct parser *p, int minimum, e26q_ast **out)
{
    e26q_ast *left = NULL;

    *out = NULL;
    if (atom(p, &left) != 0)
        return -1;
    for (;;) {
        enum lex_kind op = peek(p);
        int implicit = op == L_TERM || op == L_OPEN;
        int prec;
        e26q_ast *right = NULL;
        e26q_ast *node;
        enum node_kind kind;

        if (implicit)
            op = L_AND;
        prec = priority(op);
        if (prec == 0 || prec < minimum) {
            *out = left;
            return 0;
        }
        if (!implicit)
            ++p->at;
        if (expression(p, prec + 1, &right) != 0) {
            e26q_free(left);
            return -1;
        }
        if (op == L_AND || op == L_OR) {
            if (left == NULL) {
                left = right;
                continue;
            }
            if (right == NULL)
                continue;
            kind = op == L_AND ? NODE_AND : NODE_OR;
        } else {
            if (left == NULL || right == NULL) {
                e26q_free(left);
                e26q_free(right);
                return fail(p->err, 20102);
            }
            kind = op == L_NOT ? NODE_NOT : NODE_NEAR;
            if (kind == NODE_NEAR &&
                ((left->kind != NODE_TERM && left->kind != NODE_NEAR) ||
                (right->kind != NODE_TERM && right->kind != NODE_NEAR))) {
                e26q_free(left);
                e26q_free(right);
                return fail(p->err, 20102);
            }
        }
        node = new_node(p, kind);
        if (node == NULL) {
            e26q_free(left);
            e26q_free(right);
            return -1;
        }
        node->left = left;
        node->right = right;
        node->distance = p->near_default;
        left = node;
    }
}

int
e26q_parse(const char *utf8, int title_mode, int near_default,
    e26q_ast **out, e26q_err *err)
{
    struct parser p;
    const unsigned char *at;
    size_t count = 0;
    size_t i;
    int has_quote = 0;
    int rc = -1;
    e26q_ast *expr = NULL;
    e26q_ast *root = NULL;

    if (out != NULL)
        *out = NULL;
    if (err != NULL) {
        err->code = 0;
        err->message[0] = '\0';
    }
    if (near_default < 1 || near_default > 9999)
        return fail(err, 26212);
    if (utf8 == NULL || out == NULL)
        return fail(err, 26222);
    at = (const unsigned char *)utf8;
    while (*at != '\0') {
        unsigned value;
        size_t width;

        if (scalar(at, &value, &width) != 0)
            return fail(err, 26222);
        if (value == '"')
            has_quote = 1;
        if (count <= 127)
            ++count;
        at += width;
    }
    if (count > 127)
        return fail(err, 20101);
    if (title_mode && has_quote)
        return fail(err, 2018);
    memset(&p, 0, sizeof(p));
    p.err = err;
    p.near_default = near_default;
    if (lex_input(&p, utf8, title_mode) != 0)
        goto done;
    if (p.nlex == 0) {
        (void)fail(err, 2200);
        goto done;
    }
    if (expression(&p, 0, &expr) != 0)
        goto done;
    if (peek(&p) != L_END) {
        (void)fail(err, 26222);
        goto done;
    }
    if (expr == NULL) {
        (void)fail(err, p.saw_stop ? 26225 : 2200);
        goto done;
    }
    root = new_node(&p, NODE_QUERY);
    if (root == NULL)
        goto done;
    root->title_mode = title_mode != 0;
    root->left = expr;
    expr = NULL;
    *out = root;
    rc = 0;
done:
    e26q_free(expr);
    for (i = 0; i < p.nlex; ++i)
        e26q_free(p.lex[i].node);
    if (p.tokenizer_instance != NULL)
        p.tokenizer.xDelete(p.tokenizer_instance);
    if (p.db != NULL)
        (void)sqlite3_close(p.db);
    return rc;
}

struct writer {
    char *buf;
    size_t capacity;
    size_t len;
    int failed;
};

static void
write_text(struct writer *w, const char *text)
{
    size_t n = strlen(text);

    if (w->failed)
        return;
    if (n > (size_t)INT_MAX - w->len) {
        w->failed = 1;
        return;
    }
    if (w->buf != NULL) {
        if (w->len >= w->capacity || n >= w->capacity - w->len) {
            w->failed = 1;
            return;
        }
        memcpy(w->buf + w->len, text, n);
    }
    w->len += n;
}

static void emit(struct writer *, const e26q_ast *);

static void
emit_near_terms(struct writer *w, const e26q_ast *node)
{
    if (node->kind == NODE_NEAR) {
        emit_near_terms(w, node->left);
        write_text(w, " ");
        emit_near_terms(w, node->right);
    } else {
        emit(w, node);
    }
}

static void
emit(struct writer *w, const e26q_ast *node)
{
    if (node->kind == NODE_TERM) {
        const char *at;
        char one[2];

        write_text(w, "\"");
        for (at = node->text; *at != '\0'; ++at) {
            one[0] = *at;
            one[1] = '\0';
            write_text(w, one);
            if (*at == '"')
                write_text(w, "\"");
        }
        write_text(w, node->prefix ? "\"*" : "\"");
    } else if (node->kind == NODE_NEAR) {
        char distance[16];

        write_text(w, "NEAR(");
        emit_near_terms(w, node);
        (void)snprintf(distance, sizeof(distance), ", %d)", node->distance);
        write_text(w, distance);
    } else {
        write_text(w, "(");
        emit(w, node->left);
        write_text(w, node->kind == NODE_AND ? " AND " :
            node->kind == NODE_OR ? " OR " : " NOT ");
        emit(w, node->right);
        write_text(w, ")");
    }
}

int
e26q_to_fts5(const e26q_ast *ast, char *buf, size_t bufsz)
{
    struct writer w;

    if (buf != NULL && bufsz != 0)
        buf[0] = '\0';
    if (ast == NULL || ast->kind != NODE_QUERY || buf == NULL)
        return -1;
    memset(&w, 0, sizeof(w));
    /* Count before writing, so insufficient capacity leaves an empty buffer. */
    write_text(&w, ast->title_mode ? "title : (" : "body : (");
    emit(&w, ast->left);
    write_text(&w, ")");
    if (w.failed || w.len >= bufsz)
        return -1;
    w.buf = buf;
    w.capacity = bufsz;
    w.len = 0;
    write_text(&w, ast->title_mode ? "title : (" : "body : (");
    emit(&w, ast->left);
    write_text(&w, ")");
    if (w.failed)
        return -1;
    buf[w.len] = '\0';
    return (int)w.len;
}

#else
typedef int e26q_no_sqlite3; /* ISO C wants one declaration in every file */
#endif
