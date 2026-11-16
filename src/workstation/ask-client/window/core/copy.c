#include "doc.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct Plain {
	char *data;
	size_t used, capacity;
} Plain;

static bool add(Plain *p, const char *s)
{
	size_t n = strlen(s);
	if (!ask_grow((void **)&p->data, &p->capacity, p->used + n + 1, 1))
		return false;
	memcpy(p->data + p->used, s, n + 1);
	p->used += n;
	return true;
}

static bool item_text(Plain *p, const AskItem *item)
{
	if (item->kind == LIST) {
		for (unsigned i = 1; i < item->level; i++)
			if (!add(p, "  "))
				return false;
		if (!add(p, item->marker) || !add(p, " "))
			return false;
	} else if (item->kind == QUOTE) {
		for (unsigned i = 0; i < item->level; i++)
			if (!add(p, "> "))
				return false;
	} else if (item->kind == RULE) {
		if (!add(p, "---"))
			return false;
	} else if (item->kind == REFERENCE) {
		char number[24];
		snprintf(number, sizeof number, "[%" PRIu32 "] ", item->ref);
		if (!add(p, number))
			return false;
	}
	for (size_t i = 0; i < item->count; i++)
		if (!add(p, item->runs[i].text))
			return false;
	if (item->mark && (!add(p, "  ") || !add(p, item->mark)))
		return false;
	if ((item->flags & REF_FALLBACK) && !add(p, " (document; section not mapped)"))
		return false;
	return add(p, "\n");
}

char *ask_copy_turn(const AskDoc *d, uint32_t turn)
{
	Plain plain = {0};
	bool found = false;
	for (size_t i = 0; i < d->count; i++) {
		const AskItem *item = &d->items[i];
		if (item->turn != turn || item->kind == NOTICE)
			continue;
		found = true;
		if (!item_text(&plain, item)) {
			free(plain.data);
			return NULL;
		}
	}
	return found ? plain.data : NULL;
}
