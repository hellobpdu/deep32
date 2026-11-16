#ifndef D32_JSON_H
#define D32_JSON_H

#include <stddef.h>
#include <stdint.h>

#include "d32_error.h"

#define D32_JSON_MAX_DEPTH 64u
#define D32_JSON_MAX_STRING (1024u * 1024u)
#define D32_JSON_MAX_TOKENS 4000000u

typedef struct d32_json d32_json;
typedef uint32_t d32_json_id;

enum d32_json_type {
	D32_JSON_INVALID = 0,
	D32_JSON_NULL,
	D32_JSON_BOOL,
	D32_JSON_NUMBER,
	D32_JSON_STRING,
	D32_JSON_ARRAY,
	D32_JSON_OBJECT
};

/*
 * Parse one RFC 8259 value. Keep input alive and unchanged until d32_json_free.
 * Callers bound input length before reading (for example, 64 MiB for a CKLB
 * import). Depth counts open containers. The string limit measures decoded
 * UTF-8 bytes. Tokens include keys, scalar values and punctuation.
 *
 * Output is NULL on failure. The optional error offset is a byte position
 * where the parser detected the failure, possibly the end of input.
 * Duplicate decoded object keys and invalid Unicode scalar sequences are
 * format errors. Limits use D32E_LIMIT and allocation failures use D32E_NOMEM.
 */
enum d32_error d32_json_parse(const void *, size_t, d32_json **, size_t *);
void d32_json_free(d32_json *);

/*
 * IDs are local to one document. Zero denotes an absent or invalid value.
 * Iterate a container with first/next. Arrays keep input order. Object keys
 * are in ascending decoded UTF-8 byte order. Lookup takes a byte length
 * because a JSON key can contain an escaped NUL.
 */
d32_json_id d32_json_root(const d32_json *);
enum d32_json_type d32_json_typeof(const d32_json *, d32_json_id);
size_t d32_json_count(const d32_json *, d32_json_id);
d32_json_id d32_json_first(const d32_json *, d32_json_id);
d32_json_id d32_json_next(const d32_json *, d32_json_id);
d32_json_id d32_json_get(const d32_json *, d32_json_id, const void *, size_t);

/*
 * Returned views are borrowed for the document lifetime. Key and string views
 * have a trailing NUL outside their reported length and can contain NUL bytes.
 * Number and raw views are exact input spans with no terminator guarantee.
 * Raw excludes surrounding whitespace and, for a member, excludes its key.
 * A wrong type or invalid ID returns NULL and sets the optional length to zero.
 * d32_json_bool returns 0/1 or -1 for a wrong type or invalid ID.
 */
const char *d32_json_key(const d32_json *, d32_json_id, size_t *);
const char *d32_json_string(const d32_json *, d32_json_id, size_t *);
const char *d32_json_number(const d32_json *, d32_json_id, size_t *);
const char *d32_json_raw(const d32_json *, d32_json_id, size_t *);
int d32_json_bool(const d32_json *, d32_json_id);

/*
 * Emit compact UTF-8 JSON with sorted object keys and exact number lexemes.
 * A sink consumes the whole span on D32E_OK. On a sink error it may have
 * consumed a prefix. For atomic output use write or stage the emitted bytes.
 * write allocates a NUL-terminated result which the caller frees with free.
 * quote validates a UTF-8 string before emitting its quoted representation.
 */
typedef enum d32_error (*d32_json_sink)(void *, const char *, size_t);
enum d32_error d32_json_emit(const d32_json *, d32_json_id, d32_json_sink, void *);
enum d32_error d32_json_write(const d32_json *, d32_json_id, char **, size_t *);
enum d32_error d32_json_quote(const void *, size_t, d32_json_sink, void *);

#endif
