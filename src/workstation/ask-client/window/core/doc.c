#include "doc.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { MAX_TURNS = 200, MAX_BYTES = 4 * 1024 * 1024 };

bool ask_grow(void **array, size_t *capacity, size_t need, size_t size)
{
	if (need <= *capacity)
		return true;
	size_t next = *capacity && *capacity <= SIZE_MAX / 2 ? *capacity * 2 : 16;
	if (next < need)
		next = need;
	if (next > SIZE_MAX / size)
		return false;
	void *data = realloc(*array, next * size);
	if (!data)
		return false;
	*array = data;
	*capacity = next;
	return true;
}

char *ask_dup(const char *s, size_t n)
{
	char *copy = malloc(n + 1);
	if (copy) {
		memcpy(copy, s, n);
		copy[n] = '\0';
	}
	return copy;
}

static bool string(char **out, const char *text, size_t *bytes)
{
	size_t length = strlen(text);
	*out = ask_dup(text, length);
	if (*out)
		*bytes += length + 1;
	return *out != NULL;
}

static void clear_item(AskItem *item)
{
	for (size_t i = 0; i < item->count; i++)
		free(item->runs[i].text);
	free(item->runs);
	free(item->marker);
	free(item->mark);
	*item = (AskItem){0};
}

static bool run(AskItem *item, unsigned style, const char *text)
{
	size_t n = strlen(text);
	if (!ask_grow((void **)&item->runs, &item->capacity, item->count + 1, sizeof *item->runs))
		return false;
	char *copy = ask_dup(text, n);
	if (!copy)
		return false;
	item->runs[item->count++] = (AskRun){copy, n, style};
	item->bytes += n + 1 + sizeof(AskRun);
	return true;
}

AskDoc *ask_doc_new(void)
{
	return calloc(1, sizeof(AskDoc));
}

void ask_doc_free(AskDoc *d)
{
	if (!d)
		return;
	for (size_t i = 0; i < d->count; i++)
		clear_item(&d->items[i]);
	clear_item(&d->preview);
	clear_item(&d->pending);
	for (size_t i = 0; i < 5; i++)
		free(d->header[i]);
	free(d->items);
	free(d);
}

static void trim(AskDoc *d)
{
	size_t n = 0;
	while (n < d->count && (d->turns > MAX_TURNS ||
	    d->bytes + d->pending.bytes + d->preview.bytes > MAX_BYTES)) {
		uint32_t turn = d->items[n].turn;
		do {
			AskItem *item = &d->items[n++];
			if (item->kind == QUESTION)
				d->turns--;
			d->bytes -= sizeof *item + item->bytes;
			clear_item(item);
		} while (turn && n < d->count && d->items[n].turn == turn);
		if (turn && turn == d->turn)
			d->hidden = true;
	}
	if (n) {
		d->truncated = true;
		d->count -= n;
		memmove(d->items, d->items + n, d->count * sizeof *d->items);
	}
}

static bool append(AskDoc *d, AskItem *item)
{
	if (!ask_grow((void **)&d->items, &d->capacity, d->count + 1, sizeof *d->items))
		return false;
	if (!item->id)
		item->id = ++d->serial;
	if (item->kind == QUESTION)
		d->turns++;
	d->bytes += sizeof *item + item->bytes;
	d->items[d->count++] = *item;
	*item = (AskItem){0};
	trim(d);
	return true;
}

static bool append_text(AskDoc *d, ItemKind kind, uint32_t turn, unsigned style, const char *text)
{
	AskItem item = {.kind = kind, .turn = turn};
	bool ok = run(&item, style, text) && append(d, &item);
	clear_item(&item);
	return ok;
}

bool ask_doc_notice(AskDoc *d, const char *kind, const char *text)
{
	unsigned style = !strcmp(kind, "error") ? ASK_BOLD : !strcmp(kind, "cmd") ? ASK_MONO : 0;
	return append_text(d, NOTICE, d->active && !d->hidden ? d->turn : 0, style, text);
}

static const char *nonnull(const char *s)
{
	return s ? s : "";
}

AskHeader ask_doc_header(const AskDoc *d)
{
	return (AskHeader){nonnull(d->header[0]), nonnull(d->header[1]), nonnull(d->header[2]),
	    nonnull(d->header[3]), nonnull(d->header[4]), d->paired};
}

bool ask_doc_busy(const AskDoc *d) { return d->busy; }
bool ask_doc_fatal(const AskDoc *d) { return d->fatal; }

uint32_t ask_doc_last_turn(const AskDoc *d)
{
	for (size_t i = d->count; i > 0; i--)
		if (d->items[i - 1].kind == QUESTION)
			return d->items[i - 1].turn;
	return 0;
}

static bool header(AskDoc *d, const AskEvent *e)
{
	const unsigned fields[] = {0, 1, 3, 4, 5};
	char *update[5] = {0};
	for (size_t i = 0; i < 5; i++) {
		const char *s = e->field[fields[i]];
		update[i] = ask_dup(s, strlen(s));
		if (!update[i]) {
			for (size_t j = 0; j < i; j++)
				free(update[j]);
			return false;
		}
	}
	for (size_t i = 0; i < 5; i++) {
		free(d->header[i]);
		d->header[i] = update[i];
	}
	d->paired = e->number[2] != 0;
	return true;
}

static bool start_block(AskDoc *d, const AskEvent *e)
{
	const char *names[] = {"p", "h", "li", "quote", "code", "pre", "hr", "blank"};
	const ItemKind kinds[] = {PARAGRAPH, HEADING, LIST, QUOTE, CODE, PRE, RULE, BLANK};
	if (!d->active || d->in_block || e->number[0] != d->turn || d->references)
		return false;
	for (size_t i = 0; i < sizeof kinds / sizeof kinds[0]; i++) {
		if (strcmp(e->field[1], names[i]))
			continue;
		d->pending = (AskItem){.kind = kinds[i], .turn = d->turn,
		    .level = e->number[2], .id = d->preview.id};
		if (!d->hidden && *e->field[3] && !string(&d->pending.marker, e->field[3], &d->pending.bytes))
			return false;
		d->in_block = true;
		return true;
	}
	return false;
}

static bool preview(AskDoc *d, const AskEvent *e)
{
	if (!d->active || d->in_block || e->number[0] != d->turn || d->references)
		return false;
	if (d->hidden)
		return true;
	uint64_t id = d->preview.id ? d->preview.id : ++d->serial;
	clear_item(&d->preview);
	d->preview = (AskItem){.kind = PARAGRAPH, .turn = d->turn, .id = id};
	bool ok = run(&d->preview, 0, e->field[1]);
	trim(d);
	return ok;
}

static bool reference(AskDoc *d, const AskEvent *e)
{
	if (!d->active || d->in_block || e->number[0] != d->turn ||
	    e->number[1] != d->references + 1)
		return false;
	d->references++;
	if (d->hidden)
		return true;
	if (d->references == 1 && !append_text(d, REF_TITLE, d->turn, ASK_BOLD, "Library:"))
		return false;
	if (d->hidden)
		return true;
	AskItem item = {.kind = REFERENCE, .turn = d->turn, .ref = e->number[1],
	    .flags = (!strcmp(e->field[2], "library") ? REF_OPEN : 0) |
	        (e->number[3] ? REF_FALLBACK : 0)};
	bool ok = (!*e->field[4] || string(&item.mark, e->field[4], &item.bytes)) &&
	    run(&item, 0, e->field[5]) && append(d, &item);
	clear_item(&item);
	return ok;
}

static bool finish(AskDoc *d, const AskEvent *e)
{
	if (!d->active || d->in_block || e->number[0] != d->turn)
		return false;
	const char *status = e->field[1], *text = NULL;
	if (!strcmp(status, "interrupted")) text = "Turn interrupted.";
	else if (!strcmp(status, "failed")) text = "Turn failed.";
	else if (!strcmp(status, "error")) text = "Turn ended with an error.";
	else if (!strcmp(status, "closed")) text = "Turn closed.";
	clear_item(&d->preview);
	d->active = false;
	return !text || d->hidden || append_text(d, STATUS, d->turn, 0, text);
}

bool ask_doc_receive(void *context, const AskEvent *e)
{
	AskDoc *d = context;
	switch (e->tag) {
	case 'V': return true;
	case 'H': return header(d, e);
	case 'S': d->busy = e->number[0] != 0; return true;
	case 'N': return ask_doc_notice(d, e->field[0], e->field[1]);
	case 'X': d->fatal = true; d->busy = false; return ask_doc_notice(d, "error", e->field[0]);
	case 'Q':
		if (d->active || e->number[0] != d->turn + 1)
			return false;
		d->turn = e->number[0];
		d->active = true;
		d->hidden = false;
		d->references = 0;
		return append_text(d, QUESTION, d->turn, ASK_BOLD, e->field[1]);
	case 'P': return preview(d, e);
	case 'B': return start_block(d, e);
	case 'R':
		if (!d->in_block)
			return false;
		if (!d->hidden && !run(&d->pending, e->number[0], e->field[1]))
			return false;
		trim(d);
		if (d->hidden) {
			clear_item(&d->pending);
			clear_item(&d->preview);
		}
		return true;
	case 'E':
		if (!d->in_block)
			return false;
		d->in_block = false;
		clear_item(&d->preview);
		return d->hidden || append(d, &d->pending);
	case 'C': return reference(d, e);
	case 'F': return finish(d, e);
	default: return false;
	}
}

size_t ask_request_open(const AskDoc *d, uint32_t turn, uint32_t n, char *out, size_t size)
{
	for (size_t i = 0; i < d->count; i++) {
		const AskItem *item = &d->items[i];
		if (item->kind != REFERENCE || item->turn != turn || item->ref != n ||
		    !(item->flags & REF_OPEN))
			continue;
		int written = snprintf(out, size, "O\t%" PRIu32 "\t%" PRIu32 "\n", turn, n);
		return written > 0 && (size_t)written < size ? (size_t)written : 0;
	}
	return 0;
}
