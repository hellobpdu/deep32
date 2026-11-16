#include "d32_json.h"

#include <stdlib.h>
#include <string.h>

struct json_node {
	size_t start, length;
	size_t key, data;
	uint32_t key_length, data_length;
	d32_json_id first, next;
	uint32_t count;
	enum d32_json_type type;
};

struct d32_json {
	const unsigned char *input;
	struct json_node *nodes;
	char *strings;
	size_t nodes_used, nodes_capacity;
	size_t strings_used, strings_capacity;
	d32_json_id root;
};

struct parser {
	d32_json *doc;
	size_t length, pos;
	uint32_t tokens;
};

static const struct json_node *
get_node(const d32_json *doc, d32_json_id id)
{
	if (doc == NULL || id == 0 || id > doc->nodes_used)
		return NULL;
	return &doc->nodes[id - 1];
}

/* Internal IDs are always allocated and remain stable across arena growth. */
static struct json_node *
node(d32_json *doc, d32_json_id id)
{
	return &doc->nodes[id - 1];
}

static void *
grow(void *ptr, size_t *capacity, size_t need, size_t limit, size_t width)
{
	size_t cap = *capacity;
	void *next;

	if (need <= cap)
		return ptr;
	if (need > limit || need > SIZE_MAX / width)
		return NULL;
	if (cap == 0)
		cap = limit < 32 ? limit : 32;
	while (cap < need)
		cap = cap > limit / 2 ? limit : cap * 2;
	if (cap > SIZE_MAX / width)
		cap = need;
	next = realloc(ptr, cap * width);
	if (next != NULL)
		*capacity = cap;
	return next;
}

static enum d32_error
new_node(struct parser *p, d32_json_id *id)
{
	d32_json *doc = p->doc;
	struct json_node *items, *n;

	if (doc->nodes_used == D32_JSON_MAX_TOKENS)
		return D32E_LIMIT;
	items = grow(doc->nodes, &doc->nodes_capacity, doc->nodes_used + 1,
	    D32_JSON_MAX_TOKENS, sizeof(*items));
	if (items == NULL)
		return D32E_NOMEM;
	doc->nodes = items;
	n = &items[doc->nodes_used++];
	memset(n, 0, sizeof(*n));
	n->key = SIZE_MAX;
	n->start = p->pos;
	*id = (d32_json_id)doc->nodes_used;
	return D32E_OK;
}

static enum d32_error
append_string(struct parser *p, const void *bytes, size_t n)
{
	d32_json *doc = p->doc;
	char *strings;

	/* The arena stores at most the input bytes, including terminators. */
	if (n > p->length - doc->strings_used)
		return D32E_LIMIT;
	strings = grow(doc->strings, &doc->strings_capacity, doc->strings_used + n,
	    p->length, sizeof(*strings));
	if (strings == NULL)
		return D32E_NOMEM;
	doc->strings = strings;
	memcpy(strings + doc->strings_used, bytes, n);
	doc->strings_used += n;
	return D32E_OK;
}

static int
peek(const struct parser *p)
{
	return p->pos == p->length ? -1 : p->doc->input[p->pos];
}

static void
space(struct parser *p)
{
	int c;

	while ((c = peek(p)) == ' ' || c == '\t' || c == '\r' || c == '\n')
		p->pos++;
}

static enum d32_error
token(struct parser *p)
{
	if (p->tokens == D32_JSON_MAX_TOKENS)
		return D32E_LIMIT;
	p->tokens++;
	return D32E_OK;
}

static enum d32_error
take(struct parser *p, int c)
{
	enum d32_error e;

	if (peek(p) != c)
		return D32E_FORMAT;
	e = token(p);
	if (e == D32E_OK)
		p->pos++;
	return e;
}

/* One UTF-8 scalar. Reject overlong encodings, surrogates and > U+10FFFF. */
static size_t
utf8_width(const unsigned char *s, size_t available)
{
	size_t n, i;

	if (s[0] < 0x80)
		return 1;
	if (s[0] >= 0xc2 && s[0] <= 0xdf)
		n = 2;
	else if (s[0] >= 0xe0 && s[0] <= 0xef)
		n = 3;
	else if (s[0] >= 0xf0 && s[0] <= 0xf4)
		n = 4;
	else
		return 0;
	if (n > available)
		return 0;
	if ((s[0] == 0xe0 && s[1] < 0xa0) || (s[0] == 0xed && s[1] >= 0xa0) ||
	    (s[0] == 0xf0 && s[1] < 0x90) || (s[0] == 0xf4 && s[1] >= 0x90))
		return 0;
	for (i = 1; i < n; i++)
		if (s[i] < 0x80 || s[i] > 0xbf)
			return 0;
	return n;
}

static size_t
utf8_encode(uint32_t value, unsigned char out[4])
{
	if (value < 0x80) {
		out[0] = (unsigned char)value;
		return 1;
	}
	if (value < 0x800) {
		out[0] = (unsigned char)(0xc0 | (value >> 6));
		out[1] = (unsigned char)(0x80 | (value & 0x3f));
		return 2;
	}
	if (value < 0x10000) {
		out[0] = (unsigned char)(0xe0 | (value >> 12));
		out[1] = (unsigned char)(0x80 | ((value >> 6) & 0x3f));
		out[2] = (unsigned char)(0x80 | (value & 0x3f));
		return 3;
	}
	out[0] = (unsigned char)(0xf0 | (value >> 18));
	out[1] = (unsigned char)(0x80 | ((value >> 12) & 0x3f));
	out[2] = (unsigned char)(0x80 | ((value >> 6) & 0x3f));
	out[3] = (unsigned char)(0x80 | (value & 0x3f));
	return 4;
}

static int
hex4(struct parser *p, uint32_t *out)
{
	uint32_t value = 0;
	int i, c, digit;

	for (i = 0; i < 4; i++) {
		c = peek(p);
		if (c >= '0' && c <= '9')
			digit = c - '0';
		else if (c >= 'a' && c <= 'f')
			digit = c - 'a' + 10;
		else if (c >= 'A' && c <= 'F')
			digit = c - 'A' + 10;
		else
			return 0;
		value = (value << 4) | (uint32_t)digit;
		p->pos++;
	}
	*out = value;
	return 1;
}

/* p is at the byte after the backslash. */
static size_t
escape(struct parser *p, unsigned char out[4])
{
	uint32_t value, low;
	int c = peek(p);

	if (c < 0)
		return 0;
	p->pos++;
	switch (c) {
	case '"': case '\\': case '/': out[0] = (unsigned char)c; return 1;
	case 'b': out[0] = '\b'; return 1;
	case 'f': out[0] = '\f'; return 1;
	case 'n': out[0] = '\n'; return 1;
	case 'r': out[0] = '\r'; return 1;
	case 't': out[0] = '\t'; return 1;
	case 'u':
		if (!hex4(p, &value))
			return 0;
		if (value >= 0xd800 && value <= 0xdbff) {
			if (p->length - p->pos < 2 || p->doc->input[p->pos] != '\\' ||
			    p->doc->input[p->pos + 1] != 'u')
				return 0;
			p->pos += 2;
			if (!hex4(p, &low) || low < 0xdc00 || low > 0xdfff)
				return 0;
			value = 0x10000 + ((value - 0xd800) << 10) + low - 0xdc00;
		} else if (value >= 0xdc00 && value <= 0xdfff)
			return 0;
		return utf8_encode(value, out);
	default:
		return 0;
	}
}

static enum d32_error
parse_string(struct parser *p, size_t *offset, uint32_t *length)
{
	const unsigned char *input = p->doc->input, *bytes;
	unsigned char decoded[4];
	size_t start = p->doc->strings_used, n;
	enum d32_error e;
	int c;

	e = take(p, '"');
	if (e != D32E_OK)
		return e;
	while ((c = peek(p)) != '"') {
		if (c < 0x20)
			return D32E_FORMAT;
		if (c == '\\') {
			p->pos++;
			n = escape(p, decoded);
			bytes = decoded;
		} else {
			bytes = input + p->pos;
			n = utf8_width(bytes, p->length - p->pos);
			p->pos += n;
		}
		if (n == 0)
			return D32E_FORMAT;
		if (n > D32_JSON_MAX_STRING - (p->doc->strings_used - start))
			return D32E_LIMIT;
		e = append_string(p, bytes, n);
		if (e != D32E_OK)
			return e;
	}
	p->pos++;
	*offset = start;
	*length = (uint32_t)(p->doc->strings_used - start);
	return append_string(p, "", 1);
}

static int
digit(int c)
{
	return c >= '0' && c <= '9';
}

static void
digits(struct parser *p)
{
	while (digit(peek(p)))
		p->pos++;
}

static enum d32_error
parse_number(struct parser *p)
{
	enum d32_error e = token(p);

	if (e != D32E_OK)
		return e;
	if (peek(p) == '-')
		p->pos++;
	if (!digit(peek(p)))
		return D32E_FORMAT;
	if (peek(p) == '0')
		p->pos++;
	else
		digits(p);
	if (peek(p) == '.') {
		p->pos++;
		if (!digit(peek(p)))
			return D32E_FORMAT;
		digits(p);
	}
	if (peek(p) == 'e' || peek(p) == 'E') {
		p->pos++;
		if (peek(p) == '+' || peek(p) == '-')
			p->pos++;
		if (!digit(peek(p)))
			return D32E_FORMAT;
		digits(p);
	}
	return D32E_OK;
}

static enum d32_error
literal(struct parser *p, const char *word, size_t n)
{
	enum d32_error e;

	if (n > p->length - p->pos || memcmp(p->doc->input + p->pos, word, n))
		return D32E_FORMAT;
	e = token(p);
	if (e == D32E_OK)
		p->pos += n;
	return e;
}

static int
key_compare(const void *a, size_t a_len, const void *b, size_t b_len)
{
	size_t n = a_len < b_len ? a_len : b_len;
	int cmp = n ? memcmp(a, b, n) : 0;

	if (cmp != 0)
		return cmp;
	return (a_len > b_len) - (a_len < b_len);
}

static int
member_compare(d32_json *doc, d32_json_id a, d32_json_id b)
{
	const struct json_node *x = node(doc, a), *y = node(doc, b);

	return key_compare(doc->strings + x->key, x->key_length,
	    doc->strings + y->key, y->key_length);
}

/* Mergesort bounds duplicate detection to O(n log n) comparisons. */
static d32_json_id
sort_members(d32_json *doc, d32_json_id first, uint32_t count)
{
	d32_json_id left, right, middle, head = 0, *tail = &head;
	uint32_t i, split = count / 2;

	if (count < 2)
		return first;
	middle = first;
	for (i = 1; i < split; i++)
		middle = node(doc, middle)->next;
	right = node(doc, middle)->next;
	node(doc, middle)->next = 0;
	left = sort_members(doc, first, split);
	right = sort_members(doc, right, count - split);
	while (left != 0 && right != 0) {
		d32_json_id *side = member_compare(doc, left, right) <= 0 ? &left : &right;

		*tail = *side;
		tail = &node(doc, *side)->next;
		*side = *tail;
	}
	*tail = left != 0 ? left : right;
	return head;
}

static enum d32_error parse_value(struct parser *, unsigned int, d32_json_id *);

static enum d32_error
parse_container(struct parser *p, unsigned int depth, d32_json_id id, int open)
{
	d32_json *doc = p->doc;
	enum d32_error e;
	int close = open == '{' ? '}' : ']';
	d32_json_id last = 0, child, current, next;
	size_t key;
	uint32_t key_length;

	if (depth == D32_JSON_MAX_DEPTH)
		return D32E_LIMIT;
	e = take(p, open);
	if (e != D32E_OK)
		return e;
	space(p);
	if (peek(p) == close)
		return take(p, close);
	for (;;) {
		key = SIZE_MAX;
		key_length = 0;
		if (open == '{') {
			e = parse_string(p, &key, &key_length);
			if (e != D32E_OK)
				return e;
			space(p);
			e = take(p, ':');
			if (e != D32E_OK)
				return e;
			space(p);
		}
		e = parse_value(p, depth + 1, &child);
		if (e != D32E_OK)
			return e;
		node(doc, child)->key = key;
		node(doc, child)->key_length = key_length;
		if (last == 0)
			node(doc, id)->first = child;
		else
			node(doc, last)->next = child;
		last = child;
		node(doc, id)->count++;
		space(p);
		if (peek(p) == close)
			break;
		e = take(p, ',');
		if (e != D32E_OK)
			return e;
		space(p);
	}
	e = take(p, close);
	if (e != D32E_OK || open != '{')
		return e;
	current = sort_members(doc, node(doc, id)->first, node(doc, id)->count);
	node(doc, id)->first = current;
	while ((next = node(doc, current)->next) != 0) {
		if (member_compare(doc, current, next) == 0) {
			p->pos = node(doc, next)->start;
			return D32E_FORMAT;
		}
		current = next;
	}
	return D32E_OK;
}

static enum d32_error
parse_value(struct parser *p, unsigned int depth, d32_json_id *id)
{
	d32_json *doc = p->doc;
	enum d32_error e;
	enum d32_json_type type;
	size_t offset = 0;
	uint32_t length = 0;
	int c = peek(p);

	if (c != '{' && c != '[' && c != '"' && c != 't' && c != 'f' &&
	    c != 'n' && c != '-' && !digit(c))
		return D32E_FORMAT;
	e = new_node(p, id);
	if (e != D32E_OK)
		return e;
	switch (c) {
	case '{': case '[':
		type = c == '{' ? D32_JSON_OBJECT : D32_JSON_ARRAY;
		e = parse_container(p, depth, *id, c);
		break;
	case '"':
		type = D32_JSON_STRING;
		e = parse_string(p, &offset, &length);
		break;
	case 't': case 'f':
		type = D32_JSON_BOOL;
		offset = c == 't';
		e = c == 't' ? literal(p, "true", 4) : literal(p, "false", 5);
		break;
	case 'n':
		type = D32_JSON_NULL;
		e = literal(p, "null", 4);
		break;
	default:
		type = D32_JSON_NUMBER;
		e = parse_number(p);
		break;
	}
	if (e != D32E_OK)
		return e;
	node(doc, *id)->type = type;
	node(doc, *id)->data = offset;
	node(doc, *id)->data_length = length;
	node(doc, *id)->length = p->pos - node(doc, *id)->start;
	return D32E_OK;
}

enum d32_error
d32_json_parse(const void *input, size_t length, d32_json **out, size_t *error_offset)
{
	struct parser p;
	enum d32_error e;

	if (error_offset != NULL)
		*error_offset = 0;
	if (out == NULL)
		return D32E_INVAL;
	*out = NULL;
	if (input == NULL)
		return D32E_INVAL;
	p.doc = calloc(1, sizeof(*p.doc));
	if (p.doc == NULL)
		return D32E_NOMEM;
	p.doc->input = input;
	p.length = length;
	p.pos = p.tokens = 0;
	space(&p);
	e = parse_value(&p, 0, &p.doc->root);
	if (e == D32E_OK) {
		space(&p);
		if (p.pos != length)
			e = D32E_FORMAT;
	}
	if (e != D32E_OK) {
		if (error_offset != NULL)
			*error_offset = p.pos;
		d32_json_free(p.doc);
		return e;
	}
	*out = p.doc;
	return D32E_OK;
}

void
d32_json_free(d32_json *doc)
{
	if (doc != NULL) {
		free(doc->nodes);
		free(doc->strings);
		free(doc);
	}
}

d32_json_id
d32_json_root(const d32_json *doc)
{
	return doc != NULL ? doc->root : 0;
}

enum d32_json_type
d32_json_typeof(const d32_json *doc, d32_json_id id)
{
	const struct json_node *n = get_node(doc, id);

	return n != NULL ? n->type : D32_JSON_INVALID;
}

size_t
d32_json_count(const d32_json *doc, d32_json_id id)
{
	const struct json_node *n = get_node(doc, id);

	return n != NULL ? n->count : 0;
}

d32_json_id
d32_json_first(const d32_json *doc, d32_json_id id)
{
	const struct json_node *n = get_node(doc, id);

	return n != NULL ? n->first : 0;
}

d32_json_id
d32_json_next(const d32_json *doc, d32_json_id id)
{
	const struct json_node *n = get_node(doc, id);

	return n != NULL ? n->next : 0;
}

d32_json_id
d32_json_get(const d32_json *doc, d32_json_id id, const void *key, size_t length)
{
	const struct json_node *n = get_node(doc, id);
	d32_json_id child;

	if (n == NULL || n->type != D32_JSON_OBJECT || (key == NULL && length != 0))
		return 0;
	for (child = n->first; child != 0; child = n->next) {
		int cmp;

		n = get_node(doc, child);
		cmp = key_compare(doc->strings + n->key, n->key_length, key, length);
		if (cmp >= 0)
			return cmp == 0 ? child : 0;
	}
	return 0;
}

static const char *
view(const char *bytes, size_t length, size_t *out_length)
{
	if (out_length != NULL)
		*out_length = length;
	return bytes;
}

const char *
d32_json_key(const d32_json *doc, d32_json_id id, size_t *length)
{
	const struct json_node *n = get_node(doc, id);

	if (n == NULL || n->key == SIZE_MAX)
		return view(NULL, 0, length);
	return view(doc->strings + n->key, n->key_length, length);
}

const char *
d32_json_string(const d32_json *doc, d32_json_id id, size_t *length)
{
	const struct json_node *n = get_node(doc, id);

	if (n == NULL || n->type != D32_JSON_STRING)
		return view(NULL, 0, length);
	return view(doc->strings + n->data, n->data_length, length);
}

const char *
d32_json_raw(const d32_json *doc, d32_json_id id, size_t *length)
{
	const struct json_node *n = get_node(doc, id);

	if (n == NULL)
		return view(NULL, 0, length);
	return view((const char *)doc->input + n->start, n->length, length);
}

const char *
d32_json_number(const d32_json *doc, d32_json_id id, size_t *length)
{
	if (d32_json_typeof(doc, id) != D32_JSON_NUMBER)
		return view(NULL, 0, length);
	return d32_json_raw(doc, id, length);
}

int
d32_json_bool(const d32_json *doc, d32_json_id id)
{
	const struct json_node *n = get_node(doc, id);

	return n != NULL && n->type == D32_JSON_BOOL ? (int)n->data : -1;
}

struct writer {
	d32_json_sink sink;
	void *context;
};

static enum d32_error
write_string(struct writer *w, const char *bytes, size_t length)
{
	static const char hex[] = "0123456789abcdef";
	enum d32_error e;
	size_t i, start = 0;

	e = w->sink(w->context, "\"", 1);
	if (e != D32E_OK)
		return e;
	for (i = 0; i < length; i++) {
		unsigned char c = (unsigned char)bytes[i];
		char escaped[6] = {'\\', 'u', '0', '0', '0', '0'};
		size_t n = 2;

		switch (c) {
		case '"': case '\\': escaped[1] = (char)c; break;
		case '\b': escaped[1] = 'b'; break;
		case '\f': escaped[1] = 'f'; break;
		case '\n': escaped[1] = 'n'; break;
		case '\r': escaped[1] = 'r'; break;
		case '\t': escaped[1] = 't'; break;
		default:
			if (c >= 0x20)
				continue;
			escaped[4] = hex[c >> 4];
			escaped[5] = hex[c & 0x0f];
			n = sizeof(escaped);
			break;
		}
		if (i > start) {
			e = w->sink(w->context, bytes + start, i - start);
			if (e != D32E_OK)
				return e;
		}
		e = w->sink(w->context, escaped, n);
		if (e != D32E_OK)
			return e;
		start = i + 1;
	}
	if (length > start) {
		e = w->sink(w->context, bytes + start, length - start);
		if (e != D32E_OK)
			return e;
	}
	return w->sink(w->context, "\"", 1);
}

static enum d32_error
write_value(struct writer *w, const d32_json *doc, const struct json_node *n)
{
	enum d32_error e;
	d32_json_id child;
	int object = n->type == D32_JSON_OBJECT;

	if (n->type == D32_JSON_STRING)
		return write_string(w, doc->strings + n->data, n->data_length);
	if (n->type != D32_JSON_OBJECT && n->type != D32_JSON_ARRAY)
		return w->sink(w->context, (const char *)doc->input + n->start, n->length);
	e = w->sink(w->context, object ? "{" : "[", 1);
	if (e != D32E_OK)
		return e;
	for (child = n->first; child != 0;) {
		const struct json_node *item = get_node(doc, child);

		if (object) {
			e = write_string(w, doc->strings + item->key, item->key_length);
			if (e != D32E_OK)
				return e;
			e = w->sink(w->context, ":", 1);
			if (e != D32E_OK)
				return e;
		}
		e = write_value(w, doc, item);
		if (e != D32E_OK)
			return e;
		child = item->next;
		if (child != 0) {
			e = w->sink(w->context, ",", 1);
			if (e != D32E_OK)
				return e;
		}
	}
	return w->sink(w->context, object ? "}" : "]", 1);
}

enum d32_error
d32_json_emit(const d32_json *doc, d32_json_id id, d32_json_sink sink, void *context)
{
	const struct json_node *n = get_node(doc, id);
	struct writer w = {sink, context};

	if (n == NULL || sink == NULL)
		return D32E_INVAL;
	return write_value(&w, doc, n);
}

struct buffer {
	char *bytes;
	size_t used, limit;
};

static enum d32_error
buffer_sink(void *context, const char *bytes, size_t n)
{
	struct buffer *b = context;

	if (n > b->limit - b->used)
		return D32E_LIMIT;
	memcpy(b->bytes + b->used, bytes, n);
	b->used += n;
	return D32E_OK;
}

enum d32_error
d32_json_write(const d32_json *doc, d32_json_id id, char **out, size_t *length)
{
	const struct json_node *n = get_node(doc, id);
	struct buffer b;
	enum d32_error e;

	if (length != NULL)
		*length = 0;
	if (out == NULL)
		return D32E_INVAL;
	*out = NULL;
	if (n == NULL)
		return D32E_INVAL;
	if (n->length == SIZE_MAX)
		return D32E_LIMIT;
	/* The compact escaping never expands a valid source JSON string. */
	b.bytes = malloc(n->length + 1);
	if (b.bytes == NULL)
		return D32E_NOMEM;
	b.limit = n->length;
	b.used = 0;
	e = d32_json_emit(doc, id, buffer_sink, &b);
	if (e != D32E_OK) {
		free(b.bytes);
		return e;
	}
	b.bytes[b.used] = '\0';
	*out = b.bytes;
	if (length != NULL)
		*length = b.used;
	return D32E_OK;
}

enum d32_error
d32_json_quote(const void *input, size_t length, d32_json_sink sink, void *context)
{
	const unsigned char *bytes = input;
	struct writer w = {sink, context};
	size_t i = 0, width;

	if (sink == NULL || (input == NULL && length != 0))
		return D32E_INVAL;
	if (length > D32_JSON_MAX_STRING)
		return D32E_LIMIT;
	while (i < length) {
		width = utf8_width(bytes + i, length - i);
		if (width == 0)
			return D32E_FORMAT;
		i += width;
	}
	return write_string(&w, (const char *)input, length);
}
