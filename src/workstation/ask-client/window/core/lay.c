#include "doc.h"

#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { MEASURE_BYTES = 512 };

typedef struct Spacing {
	int margin, paragraph, role, heading_top, heading_bottom, list, code_y, code_x, citation;
} Spacing;

static const Spacing spacing[] = {
	[ASK_VIEW_DEFAULT] = {8, 14, 22, 18, 8, 6, 8, 10, 12},
	[ASK_VIEW_COMPACT] = {6, 10, 16, 12, 6, 4, 6, 6, 8},
	[ASK_VIEW_WIDE] = {14, 20, 32, 26, 12, 8, 12, 14, 16},
};

typedef struct Span {
	const char *text;
	size_t n;
	unsigned style, flags;
	uint32_t ref;
} Span;

typedef struct Position { size_t span, offset; } Position;

typedef struct Flow {
	AskLayout *layout;
	const AskMetrics *metrics;
	Span *spans;
	size_t count, capacity;
	const Spacing *spacing;
	int y, limit, pitch, ascent, em, blank;
	int right; /* how far right a code well may reach, in column coordinates */
} Flow;

static size_t char_bytes(unsigned char c)
{
	return c < 0x80 ? 1 : c < 0xe0 ? 2 : c < 0xf0 ? 3 : 4;
}

static size_t safe_chunk(const char *text, size_t len)
{
	if (len <= MEASURE_BYTES)
		return len;
	size_t n = MEASURE_BYTES;
	while (((unsigned char)text[n] & 0xc0) == 0x80)
		n--;
	return n;
}

static int width(Flow *f, unsigned style, const char *text, size_t n)
{
	int total = 0;
	while (n) {
		size_t chunk = safe_chunk(text, n);
		int measure = f->metrics->width(f->metrics->context, style, text, chunk);
		if (measure > INT_MAX - total)
			return INT_MAX;
		if (measure > 0)
			total += measure;
		text += chunk;
		n -= chunk;
	}
	return total;
}

static bool box(Flow *f, const AskItem *item, AskBoxKind kind, int x, int y,
    int w, int h, unsigned style, unsigned flags, uint32_t ref, const char *s, size_t n)
{
	AskLayout *l = f->layout;
	if (!ask_grow((void **)&l->boxes, &l->capacity, l->count + 1, sizeof *l->boxes))
		return false;
	char *text = s ? ask_dup(s, n) : NULL;
	if (s && !text)
		return false;
	l->boxes[l->count++] = (AskBox){kind, x, y, w, h, y + f->ascent,
	    style, flags, item->turn, ref, item->id, text, n};
	return true;
}

static bool span(Flow *f, const char *s, size_t n, unsigned style, unsigned flags, uint32_t ref)
{
	if (!n)
		return true;
	if (!ask_grow((void **)&f->spans, &f->capacity, f->count + 1, sizeof *f->spans))
		return false;
	f->spans[f->count++] = (Span){s, n, style, flags, ref};
	return true;
}

static bool equal(Position a, Position b)
{
	return a.span == b.span && a.offset == b.offset;
}

static unsigned char peek(const Flow *f, Position p)
{
	return p.span < f->count ? (unsigned char)f->spans[p.span].text[p.offset] : 0;
}

static Position advance(const Flow *f, Position p)
{
	p.offset += char_bytes(peek(f, p));
	if (p.offset == f->spans[p.span].n)
		return (Position){p.span + 1, 0};
	return p;
}

static bool space(unsigned char c) { return c == ' ' || c == '\t'; }

static int range_width(Flow *f, Position a, Position b)
{
	int size = 0;
	for (size_t i = a.span; i < f->count && i <= b.span; i++) {
		Span s = f->spans[i];
		size_t start = i == a.span ? a.offset : 0;
		size_t end = i == b.span ? b.offset : s.n;
		int w = width(f, s.style, s.text + start, end - start);
		if (w > INT_MAX - size)
			return INT_MAX;
		size += w;
	}
	return size;
}

static bool draw_range(Flow *f, const AskItem *item, Position a, Position b, int x)
{
	for (size_t i = a.span; i < f->count && i <= b.span; i++) {
		Span s = f->spans[i];
		size_t start = i == a.span ? a.offset : 0;
		size_t end = i == b.span ? b.offset : s.n;
		if (end == start)
			continue;
		int w = width(f, s.style, s.text + start, end - start);
		if (!box(f, item, ASK_TEXT, x, f->y, w, f->pitch, s.style, s.flags,
		    s.ref, s.text + start, end - start))
			return false;
		x += w;
	}
	return true;
}

static bool wrap(Flow *f, const AskItem *item, int x)
{
	Position p = {0}, start = {0}, painted = {0};
	int used = 0, available = f->limit - x;
	if (available < 1)
		available = 1;
	while (p.span < f->count) {
		unsigned char c = peek(f, p);
		if (c == '\n') {
			if (!draw_range(f, item, start, p, x))
				return false;
			f->y += f->pitch;
			p = start = painted = advance(f, p);
			used = 0;
			continue;
		}
		Position end = advance(f, p);
		while ((c = peek(f, end)) != 0 && c != '\n' && space(c) == space(peek(f, p)))
			end = advance(f, end);
		int w = range_width(f, p, end);
		if (space(peek(f, p))) {
			used = w > INT_MAX - used ? INT_MAX : used + w;
		} else {
			if (w > available - used && !equal(start, painted)) {
				if (!draw_range(f, item, start, painted, x))
					return false;
				f->y += f->pitch;
				start = p;
				used = 0;
			}
			/* Ordinary overlong words can break at a Unicode character. */
			if (w > available && !(f->spans[p.span].flags & ASK_MARKING)) {
				while (!equal(p, end)) {
					Position ch = advance(f, p);
					int cw = range_width(f, p, ch);
					if (used && cw > available - used) {
						if (!draw_range(f, item, start, p, x))
							return false;
						f->y += f->pitch;
						start = p;
						used = 0;
					}
					used += cw;
					p = ch;
				}
			} else {
				used = w > INT_MAX - used ? INT_MAX : used + w;
			}
			painted = end;
		}
		p = end;
	}
	if (!draw_range(f, item, start, p, x))
		return false;
	f->y += f->pitch;
	return true;
}

static bool alnum(unsigned char c)
{
	return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9');
}

static bool id_char(unsigned char c) { return alnum(c) || (c && strchr("._:-", c)); }

static bool prefix(const char *s, size_t n, const char *word)
{
	for (size_t i = 0; word[i]; i++) {
		if (i == n)
			return false;
		unsigned char c = (unsigned char)s[i];
		if (c >= 'A' && c <= 'Z')
			c += 'a' - 'A';
		if (c != (unsigned char)word[i])
			return false;
	}
	return true;
}

static size_t citation(const char *s, size_t n)
{
	if (n > 4 && prefix(s, n, "rfc") && space((unsigned char)s[3])) {
		size_t i = 4;
		while (i < n && space((unsigned char)s[i])) i++;
		size_t start = i;
		while (i < n && s[i] >= '0' && s[i] <= '9') i++;
		if (i > start && i - start <= 6 && s[start] != '0') {
			size_t end = i;
			while (i < n && space((unsigned char)s[i])) i++;
			if (i == end) return end;
			if (n - i >= 2 && (unsigned char)s[i] == 0xc2 && (unsigned char)s[i+1] == 0xa7) {
				i += 2;
			} else if (prefix(s + i, n - i, "app")) {
				i += prefix(s + i, n - i, "appendix") ? 8 : 3;
				if (i < n && s[i] == '.') i++;
				if (i == n || !space((unsigned char)s[i])) return end;
			} else {
				return end;
			}
			while (i < n && space((unsigned char)s[i])) i++;
			size_t section = i;
			while (i < n && alnum((unsigned char)s[i])) i++;
			while (i + 1 < n && s[i] == '.' && s[i+1] >= '0' && s[i+1] <= '9') {
				i++;
				while (i < n && s[i] >= '0' && s[i] <= '9') i++;
			}
			if (i > section) end = i;
			return end;
		}
	}
	if (!n || !alnum((unsigned char)s[0]))
		return 0;
	size_t i = 1;
	while (i < n && id_char((unsigned char)s[i])) i++;
	if (i + 1 >= n || s[i] != '#' || !alnum((unsigned char)s[i+1]))
		return 0;
	i += 2;
	while (i < n && id_char((unsigned char)s[i])) i++;
	return i;
}

typedef struct ReadList {
	const char *line;
	size_t length, next, start, end;
} ReadList;

static void next_read_cite(ReadList *read)
{
	const char *s = read->line;
	size_t n = read->length;
	read->start = read->end = n;
	while (read->next < n) {
		size_t start = read->next, end = start;
		while (end < n && s[end] != ',' && s[end] != ';') {
			if (s[end++] == '(')
				while (end < n && s[end++] != ')') {}
		}
		read->next = end < n && s[end] == ',' ? end + 1 : n;
		while (start < end && (space((unsigned char)s[start]) || strchr("`[", s[start])))
			start++;
		if (start == end || !alnum((unsigned char)s[start]))
			continue;
		size_t token = start + 1;
		while (token < end && id_char((unsigned char)s[token]))
			token++;
		if (token + 1 < end && s[token] == '#' && alnum((unsigned char)s[token+1])) {
			token += 2;
			while (token < end && id_char((unsigned char)s[token]))
				token++;
		}
		size_t tail = token;
		while (tail < end) {
			if (space((unsigned char)s[tail]) || strchr("`]", s[tail])) {
				tail++;
			} else if (s[tail] == '(') {
				const char *close = memchr(s + tail + 1, ')', end - tail - 1);
				if (!close) break;
				tail = (size_t)(close - s) + 1;
			} else {
				break;
			}
		}
		if (tail == end) {
			read->start = start;
			read->end = token;
			return;
		}
	}
}

static ReadList read_list(const char *s, size_t n)
{
	ReadList read = {.line = s, .length = n, .next = n, .start = n, .end = n};
	for (size_t i = 0; i < n; i++) {
		if (i && s[i-1] != ';')
			continue;
		size_t p = i;
		while (p < n && space((unsigned char)s[p])) p++;
		if (p + 1 < n && strchr("-*", s[p]) && space((unsigned char)s[p+1])) {
			p++;
			while (p < n && space((unsigned char)s[p])) p++;
		}
		if (!prefix(s + p, n - p, "read") && !prefix(s + p, n - p, "docs"))
			continue;
		p += 4;
		if (p < n && (s[p] == ':' || s[p] == '.')) {
			read.next = p + 1;
			next_read_cite(&read);
			break;
		}
	}
	return read;
}

static bool run_spans(Flow *f, const AskRun *run, unsigned style, bool cite,
    ReadList *read, size_t offset)
{
	size_t start = 0;
	if (cite) {
		for (size_t i = 0; i < run->length; i += char_bytes((unsigned char)run->text[i])) {
			if (i && (id_char((unsigned char)run->text[i-1]) || run->text[i-1] == '/'))
				continue;
			size_t n = citation(run->text + i, run->length - i);
			while (offset + i > read->start && read->start < read->length)
				next_read_cite(read);
			if (!n && offset + i == read->start && read->end - read->start <= run->length - i)
				n = read->end - read->start;
			if (!n)
				continue;
			if (!span(f, run->text + start, i - start, style, 0, 0) ||
			    !span(f, run->text + i, n, style, ASK_ACCENT, 0))
				return false;
			start = i + n;
			i = start - 1;
		}
	}
	return span(f, run->text + start, run->length - start, style, 0, 0);
}

static bool styled_item(Flow *f, const AskItem *item, int x)
{
	bool cite = item->kind == PARAGRAPH || item->kind == LIST || item->kind == QUOTE ||
	    item->kind == HEADING;
	char *combined = NULL;
	size_t length = 0, offset = 0;
	const char *line = "";
	if (cite && item->count == 1) {
		line = item->runs[0].text;
		length = item->runs[0].length;
	} else if (cite && item->count > 1) {
		combined = malloc(item->bytes + 1);
		if (!combined)
			return false;
		for (size_t i = 0; i < item->count; i++) {
			memcpy(combined + length, item->runs[i].text, item->runs[i].length);
			length += item->runs[i].length;
		}
		combined[length] = '\0';
		line = combined;
	}
	ReadList read = read_list(line, length);
	bool ok = true;
	for (size_t i = 0; ok && i < item->count; i++) {
		const AskRun *run = &item->runs[i];
		unsigned style = run->style;
		if (item->kind == HEADING)
			style = !style && item->level == 1 ? ASK_H1 : !style && item->level == 2 ? ASK_H2 : style | ASK_BOLD;
		ok = run_spans(f, run, style, cite, &read, offset);
		offset += run->length;
	}
	free(combined);
	return ok && wrap(f, item, x);
}

/* The line with its tabs set every eight columns; the caller frees it. */
static char *expand_tabs(const char *text, size_t n, size_t *out)
{
	char *expanded = malloc(n * 8 + 1);
	if (!expanded)
		return NULL;
	size_t len = 0, column = 0;
	for (size_t i = 0; i < n; ) {
		if (text[i] == '\t') {
			size_t spaces = 8 - column % 8;
			memset(expanded + len, ' ', spaces);
			column += spaces;
			len += spaces;
			i++;
		} else {
			size_t bytes = char_bytes((unsigned char)text[i]);
			memcpy(expanded + len, text + i, bytes);
			i += bytes;
			len += bytes;
			column++;
		}
	}
	*out = len;
	return expanded;
}

/* The width of the widest line in a code record, unwrapped; -1 without memory. */
static int code_need(Flow *f, const char *text)
{
	int need = 0;
	for (const char *p = text; p; ) {
		const char *nl = strchr(p, '\n');
		size_t len, n = nl ? (size_t)(nl - p) : strlen(p);
		char *expanded = expand_tabs(p, n, &len);
		if (!expanded)
			return -1;
		int w = width(f, ASK_MONO, expanded, len);
		free(expanded);
		if (w > need)
			need = w;
		p = nl ? nl + 1 : NULL;
	}
	return need;
}

static bool code_line(Flow *f, const AskItem *item, AskCode *code, const char *text, size_t n)
{
	size_t len;
	char *expanded = expand_tabs(text, n, &len);
	if (!expanded)
		return false;
	bool ok = true;
	int pad = f->spacing->code_x, cell = width(f, ASK_MONO, " ", 1);
	int room = code->width - 2 * pad;
	int indent = room > 5 * cell ? 4 * cell : 0;
	for (size_t at = 0; ok && at < len; ) {
		int shift = at ? indent : 0;
		int used = 0, break_width = 0, available = room - shift;
		size_t end = at, stop = at;
		while (end < len) {
			unsigned char ch = (unsigned char)expanded[end];
			size_t bytes = char_bytes(ch);
			int w = width(f, ASK_MONO, expanded + end, bytes);
			if (end != at && w > available - used) break;
			used += w;
			end += bytes;
			/* CLI wraps only after a space or comma, so keywords such as
			 * message-digest-key and names such as Gi1/0/1 stay whole. */
			if (ch == ' ' || ch == ',') {
				stop = end;
				break_width = used;
			}
		}
		if (end < len && stop > at) { end = stop; used = break_width; }
		ok = box(f, item, ASK_TEXT, code->x + pad + shift, f->y, used, f->pitch,
		    ASK_MONO, 0, 0, expanded + at, end - at);
		if (2 * pad + shift + used > code->content_width)
			code->content_width = 2 * pad + shift + used;
		f->y += f->pitch;
		at = end;
	}
	if (!len) f->y += f->pitch;
	free(expanded);
	return ok;
}

static bool code_item(Flow *f, const AskItem *item, bool merge)
{
	AskLayout *l = f->layout;
	const Spacing *space = f->spacing;
	int pitch = f->pitch, ascent = f->ascent;
	char *text = malloc(item->bytes + 1);
	if (!text)
		return false;
	size_t used = 0;
	for (size_t i = 0; i < item->count; i++) {
		memcpy(text + used, item->runs[i].text, item->runs[i].length);
		used += item->runs[i].length;
	}
	text[used] = '\0';
	/* A well is as wide as its widest line: never narrower than the column, and growing to
	 * the right only, up to the pane's edge, so the column keeps one left edge. A line wider
	 * than that wraps as before. A continued well grows the same way. */
	int need = code_need(f, text), reach = f->right - space->margin;
	if (need < 0) {
		free(text);
		return false;
	}
	need = need > INT_MAX - 2 * space->code_x ? INT_MAX : need + 2 * space->code_x;
	if (need > reach)
		need = reach;
	if (!merge) {
		int w = f->limit - space->margin;
		if (need > w)
			w = need;
		if (!ask_grow((void **)&l->code, &l->code_capacity, l->code_count + 1, sizeof *l->code) ||
		    !box(f, item, ASK_CODE_RULE, space->margin, f->y, w, 1, 0, 0, 0, NULL, 0)) {
			free(text);
			return false;
		}
		l->code[l->code_count++] = (AskCode){space->margin, f->y++, w, 0, 0, item->id};
		f->y += space->code_y;
	} else {
		/* Continue the same inset in the new record, replacing its closing rule. */
		l->count--;
		f->y -= space->code_y + 1;
		if (need > l->code[l->code_count - 1].width)
			l->code[l->code_count - 1].width = need;
	}
	AskCode *code = &l->code[l->code_count - 1];
	bool ok = true;
	f->ascent = f->metrics->ascent[ASK_MONO];
	f->pitch = f->ascent + f->metrics->descent[ASK_MONO] + 2;
	for (const char *p = text; p; ) {
		const char *nl = strchr(p, '\n');
		ok = code_line(f, item, code, p, nl ? (size_t)(nl - p) : strlen(p));
		if (!ok)
			break;
		p = nl ? nl + 1 : NULL;
	}
	free(text);
	f->pitch = pitch;
	f->ascent = ascent;
	f->y += space->code_y;
	if (ok) ok = box(f, item, ASK_CODE_RULE, code->x, f->y++, code->width, 1, 0, 0, 0, NULL, 0);
	code->height = f->y - code->y;
	return ok;
}

static bool grouped(ItemKind previous, ItemKind now)
{
	return (previous == now && (now == CODE || now == PRE || now == QUOTE || now == REFERENCE)) ||
	    (previous == REF_TITLE && now == REFERENCE);
}

static bool text_item(Flow *f, const AskItem *item)
{
	int margin = f->spacing->margin, x = margin, top = f->y;
	char ref[24];
	f->count = 0;
	if (item->kind == LIST) {
		int indent = (int)(item->level - 1) * 2 * f->em;
		const char *marker = !strcmp(item->marker, "*") || !strcmp(item->marker, "+") ? "-" : item->marker;
		size_t len = strlen(marker);
		int mark = width(f, 0, marker, len);
		if (!box(f, item, ASK_TEXT, x + indent, f->y, mark, f->pitch, 0, 0, 0, marker, len))
			return false;
		x += indent + (mark + f->em > 2 * f->em ? mark + f->em : 2 * f->em);
	} else if (item->kind == QUOTE) {
		x += (int)item->level * (3 * f->em / 2);
	}
	if (item->kind == REFERENCE) {
		snprintf(ref, sizeof ref, "[%" PRIu32 "] ", item->ref);
		unsigned linked = (item->flags & REF_OPEN) ? ASK_UNDERLINE : 0;
		uint32_t number = linked ? item->ref : 0;
		if (!span(f, "Source: ", 8, ASK_ITALIC, 0, 0) ||
		    !span(f, ref, strlen(ref), ASK_ITALIC, ASK_ACCENT | linked, number) ||
		    !span(f, item->runs[0].text, item->runs[0].length, ASK_ITALIC, linked, number) ||
		    (item->mark && (!span(f, "  ", 2, 0, 0, 0) ||
		    !span(f, item->mark, strlen(item->mark), 0, ASK_MARKING, 0))))
			return false;
		const char fallback[] = " (document; section not mapped)";
		if ((item->flags & REF_FALLBACK) && !span(f, fallback, sizeof fallback - 1, 0, 0, 0))
			return false;
		if (!wrap(f, item, x))
			return false;
	} else if (!styled_item(f, item, x)) {
		return false;
	}
	if (item->kind == QUOTE) {
		for (unsigned i = 0; i < item->level; i++)
			if (!box(f, item, ASK_QUOTE_BAR, margin + (int)i * (3 * f->em / 2),
			    top, 2, f->y - top, 0, 0, 0, NULL, 0))
				return false;
	}
	return true;
}

static bool role(Flow *f, const AskItem *it, const char *label, bool separator)
{
	int margin = f->spacing->margin;
	if (separator && !box(f, it, ASK_RULE, margin, f->y++, f->limit - margin, 1, 0, 0, 0, NULL, 0))
		return false;
	if (separator) f->y += f->spacing->heading_bottom;
	size_t len = strlen(label);
	if (!box(f, it, ASK_TEXT, margin, f->y, width(f, ASK_BOLD, label, len),
	    f->pitch, ASK_BOLD, ASK_ACCENT, 0, label, len)) return false;
	f->y += f->pitch + f->spacing->list / 2;
	return true;
}

static bool item(Flow *f, const AskItem *it, const AskItem *previous)
{
	const Spacing *s = f->spacing;
	bool merge = previous && !f->blank && grouped(previous->kind, it->kind);
	bool answer = previous && previous->kind == QUESTION && it->turn && it->turn == previous->turn;
	int gap = 0;
	if (it->kind == BLANK) {
		f->blank = 1;
		return true;
	}
	if (merge && it->kind == CODE && (previous->marker || it->marker))
		merge = previous->marker && it->marker && !strcmp(previous->marker, it->marker);
	if (previous && !merge) {
		if (it->kind == QUESTION || answer) gap = s->role;
		else if (it->kind == HEADING) gap = s->heading_top;
		else if (it->kind == REF_TITLE || it->kind == REFERENCE) gap = s->citation;
		else if (it->kind == LIST && (previous->kind == LIST || previous->kind == PARAGRAPH)) gap = s->list;
		else if (f->blank || (it->kind == PARAGRAPH && (previous->kind == PARAGRAPH || previous->kind == LIST)) ||
		    it->kind == NOTICE || it->kind == STATUS || previous->kind == NOTICE || previous->kind == STATUS)
			gap = s->paragraph;
		else if (it->kind == CODE || it->kind == PRE || it->kind == QUOTE ||
		    previous->kind == CODE || previous->kind == PRE || previous->kind == QUOTE) gap = s->list;
	}
	f->blank = 0;
	f->y += gap;
	if (it->kind == QUESTION || answer)
		if (!role(f, it, answer ? "Answer" : "You", previous != NULL)) return false;
	if (it->kind == RULE) {
		int y = f->y + f->pitch / 2;
		f->y += f->pitch;
		return box(f, it, ASK_RULE, s->margin, y, f->limit - s->margin, 1, 0, 0, 0, NULL, 0);
	}
	if (it->kind == CODE || it->kind == PRE ||
	    (it->kind == NOTICE && it->count && (it->runs[0].style & ASK_MONO)))
		return code_item(f, it, merge);
	if (it->kind == HEADING && it->level <= 2) {
		int ascent = f->ascent, pitch = f->pitch;
		unsigned style = it->level == 1 ? ASK_H1 : ASK_H2;
		if (f->metrics->ascent[style] > f->ascent) f->ascent = f->metrics->ascent[style];
		int height = f->ascent + f->metrics->descent[style] + 2;
		if (height > f->pitch) f->pitch = height;
		bool ok = text_item(f, it);
		f->ascent = ascent;
		f->pitch = pitch;
		if (ok) ok = box(f, it, ASK_RULE, s->margin, f->y++, f->limit - s->margin, 1, 0, 0, 0, NULL, 0);
		f->y += s->heading_bottom;
		return ok;
	}
	return text_item(f, it);
}

bool ask_layout_build(AskLayout *layout, const AskDoc *d, int pane_width, const AskMetrics *m)
{
	AskLayout next = {0};
	Spacing scaled = spacing[m->size == ASK_VIEW_COMPACT ? ASK_VIEW_COMPACT :
	    m->size == ASK_VIEW_WIDE ? ASK_VIEW_WIDE : ASK_VIEW_DEFAULT];
	if (m->scale > 0 && m->scale != 100 && m->scale <= 1000) {
		int *field[] = {&scaled.margin, &scaled.paragraph, &scaled.role, &scaled.heading_top,
		    &scaled.heading_bottom, &scaled.list, &scaled.code_y, &scaled.code_x, &scaled.citation};
		for (size_t i = 0; i < sizeof field / sizeof *field; i++)
			*field[i] = (*field[i] * m->scale + 50) / 100;
	}
	const Spacing *s = &scaled;
	/* The column: the whole pane, or the prose measure centred in a wider pane. */
	int column = pane_width > 1 ? pane_width : 1;
	if (m->measure > 0 && m->measure < column - 2 * s->margin)
		column = m->measure + 2 * s->margin;
	int column_x = (pane_width > column ? pane_width - column : 0) / 2;
	Flow flow = {.layout = &next, .metrics = m, .spacing = s, .y = s->margin,
	    .limit = (column > 2 * s->margin ? column : 2 * s->margin + 1) - s->margin};
	flow.right = pane_width - column_x - s->margin > flow.limit ? pane_width - column_x - s->margin : flow.limit;
	int descent = 0;
	for (size_t i = 0; i < ASK_STYLE_COUNT; i++) {
		if (m->ascent[i] > flow.ascent) flow.ascent = m->ascent[i];
		if (m->descent[i] > descent) descent = m->descent[i];
	}
	flow.pitch = flow.ascent + descent + 2;
	if (m->pitch > flow.pitch) {
		/* Extra leading goes half above and half below the text. */
		flow.ascent += (m->pitch - flow.pitch) / 2;
		flow.pitch = m->pitch;
	}
	flow.em = width(&flow, 0, "M", 1);
	if (flow.em < 1) flow.em = 1;
	const AskItem *previous = NULL;
	bool ok = true;
	if (d->truncated) {
		char message[] = "Earlier answers were removed from this window.";
		AskRun note = {message, sizeof message - 1, 0};
		AskItem earlier = {.kind = NOTICE, .runs = &note, .count = 1};
		ok = item(&flow, &earlier, NULL);
	}
	for (size_t i = 0; ok && i < d->count; i++) {
		ok = item(&flow, &d->items[i], previous);
		if (d->items[i].kind != BLANK) previous = &d->items[i];
	}
	if (ok && !d->hidden && d->preview.count && d->preview.runs[0].length)
		ok = item(&flow, &d->preview, previous);
	free(flow.spans);
	if (!ok) {
		ask_layout_free(&next);
		return false;
	}
	next.height = flow.y + s->margin;
	next.wrap_width = flow.limit - s->margin;
	next.line_pitch = flow.pitch;
	next.column_x = column_x;
	ask_layout_free(layout);
	*layout = next;
	return true;
}

void ask_layout_free(AskLayout *l)
{
	for (size_t i = 0; i < l->count; i++)
		free((void *)l->boxes[i].text);
	free(l->boxes);
	free(l->code);
	*l = (AskLayout){0};
}

const AskBox *ask_layout_hit(const AskLayout *l, int x, int y)
{
	for (size_t i = 0; i < l->count; i++) {
		const AskBox *b = &l->boxes[i];
		if (b->ref && x >= b->x && y >= b->y && x - b->x < b->width && y - b->y < b->height)
			return b;
	}
	return NULL;
}
