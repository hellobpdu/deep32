#include "ask_core.h"

#include <string.h>

static bool utf8(const unsigned char *s, size_t n, bool escaped_controls)
{
	for (size_t i = 0; i < n; ) {
		unsigned c = s[i++];
		if (c < 0x80) {
			if ((c < 0x20 && !(escaped_controls && (c == '\n' || c == '\t'))) || c == 0x7f)
				return false;
			continue;
		}
		unsigned count = c < 0xe0 ? 1 : c < 0xf0 ? 2 : 3;
		if (c < 0xc2 || c > 0xf4 || n - i < count ||
		    (c == 0xe0 && s[i] < 0xa0) || (c == 0xed && s[i] >= 0xa0) ||
		    (c == 0xf0 && s[i] < 0x90) || (c == 0xf4 && s[i] >= 0x90))
			return false;
		while (count--)
			if ((s[i++] & 0xc0) != 0x80)
				return false;
	}
	return true;
}

static bool unescape(char *s)
{
	char *out = s;
	for (char *p = s; *p; p++) {
		if (*p == '\\') {
			switch (*++p) {
			case 'n': *out++ = '\n'; break;
			case 't': *out++ = '\t'; break;
			case '\\': *out++ = '\\'; break;
			default: return false;
			}
		} else {
			*out++ = *p;
		}
	}
	*out = '\0';
	return utf8((const unsigned char *)s, (size_t)(out - s), true);
}

static bool decimal(const char *s, uint32_t *out)
{
	uint32_t n = 0;
	if (!*s)
		return false;
	for (; *s; s++) {
		unsigned digit = (unsigned char)*s - (unsigned)'0';
		if (digit > 9 || n > (UINT32_MAX - digit) / 10)
			return false;
		n = n * 10 + digit;
	}
	*out = n;
	return true;
}

static bool enum_field(const char *s, const char *values)
{
	size_t n = strlen(s);
	while (*values) {
		size_t len = strcspn(values, "|");
		if (len == n && !memcmp(values, s, n))
			return true;
		values += len;
		if (*values)
			values++;
	}
	return false;
}

static bool valid(AskEvent *e)
{
	char **f = e->field;
	uint32_t *n = e->number;
	switch (e->tag) {
	case 'V': return n[0] == 1;
	case 'H': return n[2] <= 1;
	case 'S': return n[0] <= 1;
	case 'M': return n[4] <= 1;
	case 'N': return enum_field(f[0], "info|error|cmd");
	case 'Q': case 'P': return n[0] != 0;
	case 'B':
		if (!n[0])
			return false;
		if (!strcmp(f[1], "li"))
			return n[2] >= 1 && n[2] <= 4 && *f[3];
		if (enum_field(f[1], "h|quote"))
			return n[2] >= 1 && n[2] <= 3 && !*f[3];
		if (!strcmp(f[1], "code"))
			return n[2] == 0;
		return n[2] == 0 && !*f[3] && enum_field(f[1], "p|pre|hr|blank");
	case 'R': {
		const char *order = "bic";
		for (const char *s = f[0]; *s; s++) {
			while (*order && *order != *s)
				order++;
			if (!*order)
				return false;
			n[0] |= *order == 'b' ? ASK_BOLD : *order == 'i' ? ASK_ITALIC : ASK_MONO;
			order++;
		}
		return true;
	}
	case 'C':
		return n[0] && n[1] && n[3] <= 1 &&
		    enum_field(f[2], "library|absent") &&
		    (!*f[4] || !strcmp(f[4], "RESTRICTED"));
	case 'F':
		return n[0] && enum_field(f[1], "completed|interrupted|failed|error|closed");
	default:
		return true;
	}
}

static bool receive(AskReader *r, AskReceive handle, void *context)
{
	static const struct {
		char tag;
		const char *fields;
	} spec[] = {
		{'V',"n"}, {'H',"ttnttt"}, {'S',"n"}, {'N',"tt"}, {'Q',"nt"},
		{'P',"nt"}, {'B',"ntnt"}, {'R',"tt"}, {'E',""}, {'C',"nntntt"},
		{'F',"nt"}, {'X',"t"}, {'K',"tttt"}, {'M',"ttttn"}, {'J',""},
		{'Y',"tt"}, {'Z',"t"}
	};
	AskEvent event = {0};
	char *p = r->line;
	if (!r->used || (r->used > 1 && p[1] != '\t') ||
	    memchr(p, '\0', r->used))
		return false;
	p[r->used] = '\0';
	event.tag = *p++;
	const char *fields = NULL;
	for (size_t i = 0; i < sizeof spec / sizeof spec[0]; i++)
		if (spec[i].tag == event.tag)
			fields = spec[i].fields;
	if (!fields)
		return event.tag >= 'A' && event.tag <= 'Z';
	if ((event.tag == 'V') == r->version_seen)
		return false;
	bool more = *p == '\t';
	for (size_t i = 0; fields[i]; i++) {
		if (!more)
			return false;
		event.field[i] = ++p;
		p += strcspn(p, "\t");
		more = *p == '\t';
		*p = '\0';
		bool ok = fields[i] == 'n' ? decimal(event.field[i], &event.number[i]) :
		    unescape(event.field[i]);
		if (!ok)
			return false;
	}
	if (more || !valid(&event) || !handle(context, &event))
		return false;
	if (event.tag == 'V')
		r->version_seen = true;
	return true;
}

void ask_reader_feed(AskReader *r, const void *bytes, size_t size,
    AskReceive handle, void *context)
{
	const unsigned char *data = bytes;
	for (size_t i = 0; i < size; i++) {
		if (data[i] == '\n') {
			if (!r->discard && !receive(r, handle, context))
				r->malformed++;
			r->used = 0;
			r->discard = false;
		} else if (!r->discard) {
			if (r->used == ASK_RECORD_MAX - 1) {
				r->malformed++;
				r->discard = true;
			} else {
				r->line[r->used++] = (char)data[i];
			}
		}
	}
}

void ask_reader_end(AskReader *r)
{
	if (r->used && !r->discard)
		r->malformed++;
	r->used = 0;
	r->discard = false;
}

size_t ask_line_request(char *out, size_t capacity, const char *text, size_t length)
{
	size_t used = 2;
	if (capacity < 3 || !utf8((const unsigned char *)text, length, true))
		return 0;
	out[0] = 'L';
	out[1] = '\t';
	for (size_t i = 0; i < length; i++) {
		char c = text[i];
		size_t need = c == '\t' || c == '\\' ? 2 : 1;
		if (need + 1 > capacity - used || used + need + 1 > ASK_RECORD_MAX)
			return 0;
		if (need == 2) {
			out[used++] = '\\';
			c = c == '\t' ? 't' : '\\';
		}
		out[used++] = c == '\n' ? ' ' : c;
	}
	out[used++] = '\n';
	return used;
}
