/* e26j.h: strict JSON for the Library protocol (LIB-PROTOCOL.md, W1-DATA), shared by e26-lib and the UI's client.
 *
 * The reader enforces what the contract asks of every E26P body and of every JSON value e26-lib splices from the
 * pack: strict UTF-8 (no overlong forms, no surrogates, nothing above U+10FFFF), no BOM, no NUL (raw or escaped), no
 * lone surrogate escapes, RFC 8259 numbers only (and finite), no duplicate keys, containers nested at most 64 deep
 * (the root object is level 1), nothing but white space after the value. Unlike src/app/json.c it keeps every
 * string as exact UTF-8, so it is the parser for article format 2 and the protocol.
 *
 * Trees use json.h's jv (so json_free, json_get and json_str work on them). A number keeps its double in num and its
 * exact source text in str, so 64-bit integers (request ids, seeds) are read without loss through e26j_u64/e26j_i64.
 *
 * The writer (e26j_buf) builds JSON text: strings are checked UTF-8 and escaped; raw fragments are appended as
 * they are (the caller validates them first with e26j_valid). Base64 is the canonical alphabet with padding. */
#ifndef E26_E26J_H
#define E26_E26J_H
#include <stddef.h>
#include <stdint.h>
#include "../app/json.h"

#define E26J_MAX_DEPTH 64

/* parses len bytes; the tree (root of any type), or NULL with a reason in err */
jv *e26j_parse(const char *text, size_t len, char *err, size_t errlen);
/* the same checks without building a tree: 0 valid, -1 not (reason in err when err is not NULL) */
int e26j_valid(const char *text, size_t len, char *err, size_t errlen);
/* 1 when s (len bytes) is strict UTF-8 without NUL */
int e26j_utf8(const char *s, size_t len);

/* exact integers: 0 and *out when v is a JSON number written as an integer (no fraction or exponent) in range;
 * -1 otherwise (a boolean is not an integer) */
int e26j_u64(const jv *v, uint64_t *out);
int e26j_i64(const jv *v, int64_t lo, int64_t hi, int64_t *out);

/* ---- the writer */
typedef struct {
	char *p;
	size_t n, cap;
	int bad;              /* an allocation failed or a string was not UTF-8: the text is not usable */
} e26j_buf;

void e26j_init(e26j_buf *b);
void e26j_free(e26j_buf *b);
void e26j_raw(e26j_buf *b, const char *s, size_t len);   /* s as it is */
void e26j_lit(e26j_buf *b, const char *s);               /* a NUL-terminated literal as it is */
void e26j_str(e26j_buf *b, const char *s, size_t len);   /* a JSON string of UTF-8 s (marks bad when not UTF-8) */
void e26j_cstr(e26j_buf *b, const char *s);              /* the same for a NUL-terminated s; NULL gives null */
void e26j_u(e26j_buf *b, uint64_t v);
void e26j_i(e26j_buf *b, int64_t v);
char *e26j_take(e26j_buf *b);                            /* the NUL-terminated text (the caller frees), or NULL when bad */

/* ---- base64 (RFC 4648, padded) */
void e26j_b64(e26j_buf *b, const unsigned char *data, size_t len);    /* as a JSON string, quotes included */
/* decodes canonical base64 only (length a multiple of 4, padding where due, unused bits zero); the bytes malloc'd,
 * or NULL */
unsigned char *e26j_unb64(const char *s, size_t len, size_t *outlen);

#endif
