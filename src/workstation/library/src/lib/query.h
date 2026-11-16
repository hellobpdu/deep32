/* query.h: the Words language parser (W1-SEARCH, Codex T-0018; docs/SEARCH-LANGUAGE.md). e26q_parse gives an AST or
 * one numbered error with its exact message; e26q_to_fts5 writes the canonical MATCH string. */
#ifndef E26_QUERY_H
#define E26_QUERY_H

#include <stddef.h>

typedef struct e26q_ast e26q_ast;
typedef struct { int code; char message[256]; } e26q_err;

int e26q_parse(const char *utf8, int title_mode, int near_default,
    e26q_ast **out, e26q_err *err);
int e26q_to_fts5(const e26q_ast *ast, char *buf, size_t bufsz);
void e26q_free(e26q_ast *ast);

#endif
