/* json.h: a small strict JSON reader (RFC 8259 subset: no surrogate pairs beyond the BMP are combined; \u
 * escapes outside Latin-1 become '?', since the period X fonts are ISO 8859-1). Depth and size bounded. */
#ifndef E26_JSON_H
#define E26_JSON_H
#include <stddef.h>

enum { J_NULL, J_BOOL, J_NUM, J_STR, J_ARR, J_OBJ };

typedef struct jv {
	int type;
	double num;
	int b;
	char *str;          /* J_STR */
	struct jv **items;  /* J_ARR values, J_OBJ values */
	char **keys;        /* J_OBJ keys */
	size_t n;
} jv;

jv *json_parse(const char *text, size_t len, char *err, size_t errlen);
void json_free(jv *v);
const jv *json_get(const jv *obj, const char *key);
const char *json_str(const jv *obj, const char *key); /* NULL unless a string */

#endif
