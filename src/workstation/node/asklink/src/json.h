/*
 * Strict JSON (d32u1.md "Establishment"): invalid UTF-8, a BOM, duplicate
 * keys, nesting deeper than 32, invalid escapes or lone surrogates, raw
 * control characters in strings and trailing bytes are all refused.
 * Integer literals that fit int64 are J_INT; any other number is J_NUM
 * (raw lexeme kept), so schema checks can refuse floats for integers.
 */
#ifndef D32U_JSON_H
#define D32U_JSON_H

#include <stddef.h>
#include <stdint.h>

#define J_NULL	0
#define J_FALSE	1
#define J_TRUE	2
#define J_INT	3
#define J_NUM	4
#define J_STR	5
#define J_ARR	6
#define J_OBJ	7

#define JSON_MAX_DEPTH	32

struct jval {
	int		 type;
	int64_t		 i;
	char		*s;		/* J_STR text or J_NUM lexeme, NUL ended */
	size_t		 n;
	char		*key;		/* member name inside an object */
	size_t		 keylen;
	struct jval	*first;
	struct jval	*last;
	struct jval	*next;
	size_t		 count;
};

struct dbuf {
	uint8_t		*b;
	size_t		 len;
	size_t		 cap;
};

#define DBUF_HARD_MAX	(8u * 1024 * 1024)

int		 dbuf_add(struct dbuf *, const void *, size_t);
int		 dbuf_addstr(struct dbuf *, const char *);
int		 dbuf_printf(struct dbuf *, const char *, ...)
		    __attribute__((__format__(__printf__, 2, 3)));
void		 dbuf_drop(struct dbuf *, size_t);
void		 dbuf_reset(struct dbuf *);
void		 dbuf_free(struct dbuf *);

int		 utf8_valid(const uint8_t *, size_t);
int		 json_parse(const uint8_t *, size_t, struct jval **);
void		 json_free(struct jval *);
const struct jval *json_get(const struct jval *, const char *);
int		 json_only_keys(const struct jval *, const char *const *);
int		 json_has_keys(const struct jval *, const char *const *);
int		 json_str_is(const struct jval *, const char *);
int		 json_write(struct dbuf *, const struct jval *);
int		 json_put_str(struct dbuf *, const char *, size_t);
size_t		 text_sanitise(char *, size_t);

#endif /* D32U_JSON_H */
