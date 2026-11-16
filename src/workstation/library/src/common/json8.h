/* json8.h: the strict JSON reader LIB-PROTOCOL.md asks of a helper (W1-IMAGE, for e26-render): strings stay UTF-8
 * (\u escapes, surrogate pairs included, become UTF-8), and a body is refused for invalid UTF-8, a NUL, a lone
 * surrogate, a duplicate key, nesting past 64 levels, a number JSON does not allow, or anything after the value.
 * It builds the same jv tree as the interface's reader (src/app/json.h, which turns strings into Latin-1 for the
 * period X fonts), so json_get, json_str and json_free work on it; arrays grow by doubling, so a long display list
 * parses in linear time. */
#ifndef E26_JSON8_H
#define E26_JSON8_H
#include <stddef.h>
#include "../app/json.h"

jv *json8_parse(const char *text, size_t len, char *err, size_t errlen);

#endif
