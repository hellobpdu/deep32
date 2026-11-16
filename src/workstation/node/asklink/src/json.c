/*
 * Strict JSON parser and writer for D32U/1 messages, and a bounded
 * dynamic byte buffer.
 */
#include <errno.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "json.h"

/* ---- dbuf ---- */

int
dbuf_add(struct dbuf *d, const void *p, size_t n)
{
	size_t	 ncap;
	uint8_t	*nb;

	if (n == 0)
		return 0;
	if (n > DBUF_HARD_MAX || d->len > DBUF_HARD_MAX - n)
		return -1;
	if (d->len + n > d->cap) {
		ncap = d->cap ? d->cap : 256;
		while (ncap < d->len + n)
			ncap *= 2;
		if ((nb = recallocarray(d->b, d->cap, ncap, 1)) == NULL)
			return -1;
		d->b = nb;
		d->cap = ncap;
	}
	memcpy(d->b + d->len, p, n);
	d->len += n;
	return 0;
}

int
dbuf_addstr(struct dbuf *d, const char *s)
{
	return dbuf_add(d, s, strlen(s));
}

int
dbuf_printf(struct dbuf *d, const char *fmt, ...)
{
	char	 tmp[512];
	va_list	 ap;
	int	 n;

	va_start(ap, fmt);
	n = vsnprintf(tmp, sizeof(tmp), fmt, ap);
	va_end(ap);
	if (n < 0 || (size_t)n >= sizeof(tmp))
		return -1;
	return dbuf_add(d, tmp, n);
}

void
dbuf_drop(struct dbuf *d, size_t n)
{
	if (n >= d->len) {
		d->len = 0;
		return;
	}
	memmove(d->b, d->b + n, d->len - n);
	d->len -= n;
}

void
dbuf_reset(struct dbuf *d)
{
	d->len = 0;
}

void
dbuf_free(struct dbuf *d)
{
	freezero(d->b, d->cap);
	d->b = NULL;
	d->len = d->cap = 0;
}

/* ---- UTF-8 ---- */

int
utf8_valid(const uint8_t *s, size_t n)
{
	size_t	 i = 0;

	while (i < n) {
		uint8_t c = s[i];

		if (c < 0x80) {
			i++;
		} else if (c >= 0xc2 && c <= 0xdf) {
			if (i + 1 >= n || (s[i + 1] & 0xc0) != 0x80)
				return 0;
			i += 2;
		} else if (c >= 0xe0 && c <= 0xef) {
			if (i + 2 >= n || (s[i + 1] & 0xc0) != 0x80 ||
			    (s[i + 2] & 0xc0) != 0x80)
				return 0;
			if (c == 0xe0 && s[i + 1] < 0xa0)
				return 0;	/* overlong */
			if (c == 0xed && s[i + 1] >= 0xa0)
				return 0;	/* surrogate */
			i += 3;
		} else if (c >= 0xf0 && c <= 0xf4) {
			if (i + 3 >= n || (s[i + 1] & 0xc0) != 0x80 ||
			    (s[i + 2] & 0xc0) != 0x80 ||
			    (s[i + 3] & 0xc0) != 0x80)
				return 0;
			if (c == 0xf0 && s[i + 1] < 0x90)
				return 0;	/* overlong */
			if (c == 0xf4 && s[i + 1] >= 0x90)
				return 0;	/* > U+10FFFF */
			i += 4;
		} else
			return 0;
	}
	return 1;
}

/* ---- parser ---- */

struct jparser {
	const uint8_t	*s;
	size_t		 n;
	size_t		 p;
};

static struct jval *jvalue(struct jparser *, int);

static void
jws(struct jparser *jp)
{
	while (jp->p < jp->n && (jp->s[jp->p] == ' ' || jp->s[jp->p] == '\t' ||
	    jp->s[jp->p] == '\n' || jp->s[jp->p] == '\r'))
		jp->p++;
}

static int
hex4(struct jparser *jp, unsigned int *out)
{
	unsigned int	 v = 0;
	int		 k;

	if (jp->n - jp->p < 4)
		return -1;
	for (k = 0; k < 4; k++) {
		uint8_t c = jp->s[jp->p++];
		v <<= 4;
		if (c >= '0' && c <= '9')
			v |= c - '0';
		else if (c >= 'a' && c <= 'f')
			v |= c - 'a' + 10;
		else if (c >= 'A' && c <= 'F')
			v |= c - 'A' + 10;
		else
			return -1;
	}
	*out = v;
	return 0;
}

static int
put_utf8(struct dbuf *d, unsigned int cp)
{
	uint8_t	 b[4];
	size_t	 n;

	if (cp < 0x80) {
		b[0] = cp;
		n = 1;
	} else if (cp < 0x800) {
		b[0] = 0xc0 | (cp >> 6);
		b[1] = 0x80 | (cp & 0x3f);
		n = 2;
	} else if (cp < 0x10000) {
		b[0] = 0xe0 | (cp >> 12);
		b[1] = 0x80 | ((cp >> 6) & 0x3f);
		b[2] = 0x80 | (cp & 0x3f);
		n = 3;
	} else {
		b[0] = 0xf0 | (cp >> 18);
		b[1] = 0x80 | ((cp >> 12) & 0x3f);
		b[2] = 0x80 | ((cp >> 6) & 0x3f);
		b[3] = 0x80 | (cp & 0x3f);
		n = 4;
	}
	return dbuf_add(d, b, n);
}

/* Parses a string at jp->p (which must be '"'); result NUL-terminated. */
static int
jstring(struct jparser *jp, char **out, size_t *outlen)
{
	struct dbuf	 d = { 0 };
	unsigned int	 cp, lo;
	uint8_t		 c, z = 0;

	if (jp->p >= jp->n || jp->s[jp->p] != '"')
		return -1;
	jp->p++;
	for (;;) {
		if (jp->p >= jp->n)
			goto fail;
		c = jp->s[jp->p++];
		if (c == '"')
			break;
		if (c < 0x20)
			goto fail;
		if (c != '\\') {
			if (dbuf_add(&d, &c, 1) == -1)
				goto fail;
			continue;
		}
		if (jp->p >= jp->n)
			goto fail;
		c = jp->s[jp->p++];
		switch (c) {
		case '"': case '\\': case '/':
			break;
		case 'b': c = '\b'; break;
		case 'f': c = '\f'; break;
		case 'n': c = '\n'; break;
		case 'r': c = '\r'; break;
		case 't': c = '\t'; break;
		case 'u':
			if (hex4(jp, &cp) == -1)
				goto fail;
			if (cp >= 0xdc00 && cp <= 0xdfff)
				goto fail;
			if (cp >= 0xd800 && cp <= 0xdbff) {
				if (jp->n - jp->p < 2 || jp->s[jp->p] != '\\' ||
				    jp->s[jp->p + 1] != 'u')
					goto fail;
				jp->p += 2;
				if (hex4(jp, &lo) == -1 || lo < 0xdc00 ||
				    lo > 0xdfff)
					goto fail;
				cp = 0x10000 + ((cp - 0xd800) << 10) +
				    (lo - 0xdc00);
			}
			if (put_utf8(&d, cp) == -1)
				goto fail;
			continue;
		default:
			goto fail;
		}
		if (dbuf_add(&d, &c, 1) == -1)
			goto fail;
	}
	*outlen = d.len;
	if (dbuf_add(&d, &z, 1) == -1)
		goto fail;
	*out = (char *)d.b;
	return 0;
 fail:
	dbuf_free(&d);
	return -1;
}

static struct jval *
jnew(int type)
{
	struct jval *v = calloc(1, sizeof(*v));

	if (v)
		v->type = type;
	return v;
}

static void
jappend(struct jval *parent, struct jval *child)
{
	if (parent->last)
		parent->last->next = child;
	else
		parent->first = child;
	parent->last = child;
	parent->count++;
}

static struct jval *
jnumber(struct jparser *jp)
{
	size_t		 start = jp->p;
	int		 isint = 1;
	struct jval	*v;
	char		*lex, *ep;
	long long	 ll = 0;

	if (jp->p < jp->n && jp->s[jp->p] == '-')
		jp->p++;
	if (jp->p >= jp->n)
		return NULL;
	if (jp->s[jp->p] == '0')
		jp->p++;
	else if (jp->s[jp->p] >= '1' && jp->s[jp->p] <= '9') {
		while (jp->p < jp->n && jp->s[jp->p] >= '0' &&
		    jp->s[jp->p] <= '9')
			jp->p++;
	} else
		return NULL;
	if (jp->p < jp->n && jp->s[jp->p] == '.') {
		isint = 0;
		jp->p++;
		if (jp->p >= jp->n || jp->s[jp->p] < '0' || jp->s[jp->p] > '9')
			return NULL;
		while (jp->p < jp->n && jp->s[jp->p] >= '0' &&
		    jp->s[jp->p] <= '9')
			jp->p++;
	}
	if (jp->p < jp->n && (jp->s[jp->p] == 'e' || jp->s[jp->p] == 'E')) {
		isint = 0;
		jp->p++;
		if (jp->p < jp->n && (jp->s[jp->p] == '+' ||
		    jp->s[jp->p] == '-'))
			jp->p++;
		if (jp->p >= jp->n || jp->s[jp->p] < '0' || jp->s[jp->p] > '9')
			return NULL;
		while (jp->p < jp->n && jp->s[jp->p] >= '0' &&
		    jp->s[jp->p] <= '9')
			jp->p++;
	}
	if ((lex = strndup((const char *)jp->s + start, jp->p - start)) == NULL)
		return NULL;
	if (isint) {
		errno = 0;
		ll = strtoll(lex, &ep, 10);
		if (errno == ERANGE || *ep != '\0')
			isint = 0;
	}
	if ((v = jnew(isint ? J_INT : J_NUM)) == NULL) {
		free(lex);
		return NULL;
	}
	if (isint) {
		v->i = ll;
		free(lex);
	} else {
		v->s = lex;
		v->n = jp->p - start;
	}
	return v;
}

static int
jlit(struct jparser *jp, const char *lit)
{
	size_t	 l = strlen(lit);

	if (jp->n - jp->p < l || memcmp(jp->s + jp->p, lit, l) != 0)
		return 0;
	jp->p += l;
	return 1;
}

static int
jkeycmp(const void *a, const void *b)
{
	const struct jval	*x = *(const struct jval *const *)a;
	const struct jval	*y = *(const struct jval *const *)b;

	if (x->keylen != y->keylen)
		return x->keylen < y->keylen ? -1 : 1;
	return memcmp(x->key, y->key, x->keylen);
}

/*
 * Does a finished object repeat a member name?  Pairwise for small
 * objects, otherwise sort the members by name and compare neighbours:
 * O(n log n), so a peer cannot make one large message cost O(n^2).
 */
static int
jdupkeys(const struct jval *o)
{
	const struct jval	*m, *m2, **a;
	size_t			 i, n = 0;
	int			 dup = 0;

	if (o->count <= 8) {
		for (m = o->first; m; m = m->next)
			for (m2 = m->next; m2; m2 = m2->next)
				if (m->keylen == m2->keylen &&
				    memcmp(m->key, m2->key, m->keylen) == 0)
					return 1;
		return 0;
	}
	if ((a = reallocarray(NULL, o->count, sizeof(*a))) == NULL)
		return 1;	/* refuse rather than skip the check */
	for (m = o->first; m && n < o->count; m = m->next)
		a[n++] = m;
	qsort(a, n, sizeof(*a), jkeycmp);
	for (i = 1; i < n && !dup; i++)
		dup = jkeycmp(&a[i - 1], &a[i]) == 0;
	free(a);
	return dup;
}

static struct jval *
jvalue(struct jparser *jp, int depth)
{
	struct jval	*v, *c;
	char		*k;
	size_t		 kl;

	if (depth > JSON_MAX_DEPTH)
		return NULL;
	jws(jp);
	if (jp->p >= jp->n)
		return NULL;
	switch (jp->s[jp->p]) {
	case '"':
		if ((v = jnew(J_STR)) == NULL)
			return NULL;
		if (jstring(jp, &v->s, &v->n) == -1) {
			free(v);
			return NULL;
		}
		return v;
	case '{':
		jp->p++;
		if ((v = jnew(J_OBJ)) == NULL)
			return NULL;
		jws(jp);
		if (jp->p < jp->n && jp->s[jp->p] == '}') {
			jp->p++;
			return v;
		}
		for (;;) {
			jws(jp);
			if (jstring(jp, &k, &kl) == -1)
				goto fail;
			jws(jp);
			if (jp->p >= jp->n || jp->s[jp->p] != ':') {
				free(k);
				goto fail;
			}
			jp->p++;
			if ((c = jvalue(jp, depth + 1)) == NULL) {
				free(k);
				goto fail;
			}
			c->key = k;
			c->keylen = kl;
			jappend(v, c);
			jws(jp);
			if (jp->p < jp->n && jp->s[jp->p] == '}') {
				jp->p++;
				if (jdupkeys(v))
					goto fail;	/* duplicate */
				return v;
			}
			if (jp->p >= jp->n || jp->s[jp->p] != ',')
				goto fail;
			jp->p++;
		}
	case '[':
		jp->p++;
		if ((v = jnew(J_ARR)) == NULL)
			return NULL;
		jws(jp);
		if (jp->p < jp->n && jp->s[jp->p] == ']') {
			jp->p++;
			return v;
		}
		for (;;) {
			if ((c = jvalue(jp, depth + 1)) == NULL)
				goto fail;
			jappend(v, c);
			jws(jp);
			if (jp->p < jp->n && jp->s[jp->p] == ']') {
				jp->p++;
				return v;
			}
			if (jp->p >= jp->n || jp->s[jp->p] != ',')
				goto fail;
			jp->p++;
		}
	case 't':
		return jlit(jp, "true") ? jnew(J_TRUE) : NULL;
	case 'f':
		return jlit(jp, "false") ? jnew(J_FALSE) : NULL;
	case 'n':
		return jlit(jp, "null") ? jnew(J_NULL) : NULL;
	default:
		return jnumber(jp);
	}
 fail:
	json_free(v);
	return NULL;
}

int
json_parse(const uint8_t *s, size_t n, struct jval **out)
{
	struct jparser	 jp = { s, n, 0 };
	struct jval	*v;

	*out = NULL;
	if (n >= 3 && s[0] == 0xef && s[1] == 0xbb && s[2] == 0xbf)
		return -1;	/* BOM */
	if (!utf8_valid(s, n))
		return -1;
	if ((v = jvalue(&jp, 0)) == NULL)
		return -1;
	jws(&jp);
	if (jp.p != jp.n) {
		json_free(v);
		return -1;
	}
	*out = v;
	return 0;
}

void
json_free(struct jval *v)
{
	struct jval	*c, *nx;

	if (v == NULL)
		return;
	for (c = v->first; c; c = nx) {
		nx = c->next;
		json_free(c);
	}
	free(v->s);
	free(v->key);
	free(v);
}

const struct jval *
json_get(const struct jval *o, const char *key)
{
	const struct jval	*m;
	size_t			 l = strlen(key);

	if (o == NULL || o->type != J_OBJ)
		return NULL;
	for (m = o->first; m; m = m->next)
		if (m->keylen == l && memcmp(m->key, key, l) == 0)
			return m;
	return NULL;
}

/* Every member of o is named in keys (NULL-terminated list). */
int
json_only_keys(const struct jval *o, const char *const *keys)
{
	const struct jval	*m;
	const char *const	*k;

	if (o == NULL || o->type != J_OBJ)
		return 0;
	for (m = o->first; m; m = m->next) {
		for (k = keys; *k; k++)
			if (strlen(*k) == m->keylen &&
			    memcmp(*k, m->key, m->keylen) == 0)
				break;
		if (*k == NULL)
			return 0;
	}
	return 1;
}

int
json_has_keys(const struct jval *o, const char *const *keys)
{
	const char *const	*k;

	for (k = keys; *k; k++)
		if (json_get(o, *k) == NULL)
			return 0;
	return 1;
}

int
json_str_is(const struct jval *v, const char *lit)
{
	return v != NULL && v->type == J_STR && v->n == strlen(lit) &&
	    memcmp(v->s, lit, v->n) == 0;
}

/* ---- writer ---- */

int
json_put_str(struct dbuf *d, const char *s, size_t n)
{
	size_t	 i, run = 0;
	char	 esc[8];

	if (dbuf_add(d, "\"", 1) == -1)
		return -1;
	for (i = 0; i < n; i++) {
		uint8_t c = s[i];
		const char *e = NULL;

		if (c == '"')
			e = "\\\"";
		else if (c == '\\')
			e = "\\\\";
		else if (c == '\n')
			e = "\\n";
		else if (c == '\t')
			e = "\\t";
		else if (c == '\r')
			e = "\\r";
		else if (c < 0x20) {
			(void)snprintf(esc, sizeof(esc), "\\u%04x", c);
			e = esc;
		}
		if (e == NULL)
			continue;
		if (dbuf_add(d, s + run, i - run) == -1 ||
		    dbuf_addstr(d, e) == -1)
			return -1;
		run = i + 1;
	}
	if (dbuf_add(d, s + run, n - run) == -1)
		return -1;
	return dbuf_add(d, "\"", 1);
}

int
json_write(struct dbuf *d, const struct jval *v)
{
	const struct jval	*c;

	switch (v->type) {
	case J_NULL:
		return dbuf_addstr(d, "null");
	case J_TRUE:
		return dbuf_addstr(d, "true");
	case J_FALSE:
		return dbuf_addstr(d, "false");
	case J_INT:
		return dbuf_printf(d, "%lld", (long long)v->i);
	case J_NUM:
		return dbuf_add(d, v->s, v->n);
	case J_STR:
		return json_put_str(d, v->s, v->n);
	case J_ARR:
		if (dbuf_addstr(d, "[") == -1)
			return -1;
		for (c = v->first; c; c = c->next)
			if ((c != v->first && dbuf_addstr(d, ",") == -1) ||
			    json_write(d, c) == -1)
				return -1;
		return dbuf_addstr(d, "]");
	case J_OBJ:
		if (dbuf_addstr(d, "{") == -1)
			return -1;
		for (c = v->first; c; c = c->next)
			if ((c != v->first && dbuf_addstr(d, ",") == -1) ||
			    json_put_str(d, c->key, c->keylen) == -1 ||
			    dbuf_addstr(d, ":") == -1 ||
			    json_write(d, c) == -1)
				return -1;
		return dbuf_addstr(d, "}");
	}
	return -1;
}

/*
 * Strip every C0 and C1 control except LF and TAB (and DEL) from UTF-8
 * text, in place, before any terminal or widget sees it.
 */
size_t
text_sanitise(char *s, size_t n)
{
	size_t	 i, o = 0;

	for (i = 0; i < n; i++) {
		uint8_t c = s[i];

		if ((c < 0x20 && c != '\n' && c != '\t') || c == 0x7f)
			continue;
		if (c == 0xc2 && i + 1 < n && (uint8_t)s[i + 1] >= 0x80 &&
		    (uint8_t)s[i + 1] <= 0x9f) {
			i++;
			continue;
		}
		s[o++] = s[i];
	}
	if (o < n)
		s[o] = '\0';
	return o;
}
