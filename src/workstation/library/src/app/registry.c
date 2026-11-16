/* registry.c: the named tables of registry.h. No caps: every table grows as modules register. */
#include "registry.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
	reg_entry *e;
	int n, cap;
} reg_tab;

static reg_tab tabs[REG_NTABLES];
static int inited;
static char conflicts[1024];

/* build 2's kinds and actions, at the numbers of scr.h's enums */
static const char *const builtin_kinds[] = { "button", "toggle", "radio", "text", "label", "bar", "panel", "article", "outline", "list",
                                             "image", "scrollbar", "wheel", "edit", "count", "catpanel" };
static const char *const builtin_actions[] = { "none", "goto", "close", "menu", "open-article", "toggle", "select", "back", "quit",
                                               "iconify", "scroll-up", "scroll-down", "section-prev", "section-next", "notice", "reset",
                                               "maximize" };

static reg_entry *slot(int t, const char *name)
{
	reg_tab *tb = &tabs[t];
	reg_entry *e;
	if (strlen(name) >= REG_NAME) return NULL;
	if (tb->n == tb->cap) {
		int cap = tb->cap ? tb->cap * 2 : 32;
		reg_entry *ne = (reg_entry *)realloc(tb->e, (size_t)cap * sizeof(reg_entry));
		if (!ne) return NULL;
		tb->e = ne;
		tb->cap = cap;
	}
	e = &tb->e[tb->n];
	memset(e, 0, sizeof(*e));
	snprintf(e->name, sizeof e->name, "%s", name);
	e->id = tb->n++;
	e->owner = "";
	return e;
}

void reg_builtins(void)
{
	size_t i;
	if (inited) return;
	inited = 1;
	for (i = 0; i < sizeof builtin_kinds / sizeof builtin_kinds[0]; i++) slot(REG_KIND, builtin_kinds[i]);
	for (i = 0; i < sizeof builtin_actions / sizeof builtin_actions[0]; i++) {
		reg_entry *e = slot(REG_ACTION, builtin_actions[i]);
		/* goto, menu and notice need their argument (scr.c refused them without one in build 2) */
		if (e && (!strcmp(e->name, "goto") || !strcmp(e->name, "menu") || !strcmp(e->name, "notice"))) e->flags |= REG_A_ARG;
	}
}

static int named(const char *name) { return strchr(name, '.') != NULL; }

int reg_find(int t, const char *name)
{
	int i;
	reg_builtins();
	if (t < 0 || t >= REG_NTABLES || !name) return -1;
	for (i = 0; i < tabs[t].n; i++)
		if (!strcmp(tabs[t].e[i].name, name)) return i;
	return -1;
}

int reg_add(int t, const char *name, const void *impl, const char *owner, unsigned flags)
{
	int id = reg_find(t, name);
	reg_entry *e;
	if (t < 0 || t >= REG_NTABLES) return -1;
	if (id < 0) {
		if (!(e = slot(t, name))) { fprintf(stderr, "d32library: registry: cannot register %s '%s'\n", reg_table_name(t), name); return -1; }
	} else {
		e = &tabs[t].e[id];
		if (e->impl && impl && e->impl != impl) {
			size_t k = strlen(conflicts);
			fprintf(stderr, "d32library: registry: %s '%s' registered by %s and again by %s (the last one wins)\n",
			        reg_table_name(t), name, e->owner, owner ? owner : "?");
			snprintf(conflicts + k, sizeof conflicts - k, "%s%s %s (%s, %s)", k ? "; " : "", reg_table_name(t), name, e->owner,
			         owner ? owner : "?");
		}
	}
	e->impl = impl;
	e->owner = owner ? owner : "";
	e->declared = 0;
	e->flags |= flags;
	if (t == REG_ACTION && named(name)) e->flags |= REG_A_NAMED;
	return e->id;
}

int reg_declare(int t, const char *name)
{
	int id = reg_find(t, name);
	reg_entry *e;
	if (id >= 0) return id;
	if (t != REG_ACTION || !named(name) || !(e = slot(t, name))) return -1;
	e->declared = 1;
	e->flags |= REG_A_NAMED;
	return e->id;
}

const reg_entry *reg_get(int t, int id)
{
	reg_builtins();
	if (t < 0 || t >= REG_NTABLES || id < 0 || id >= tabs[t].n) return NULL;
	return &tabs[t].e[id];
}

int reg_count(int t)
{
	reg_builtins();
	return t >= 0 && t < REG_NTABLES ? tabs[t].n : 0;
}

const char *reg_name(int t, int id)
{
	const reg_entry *e = reg_get(t, id);
	return e ? e->name : "?";
}

const void *reg_impl(int t, int id)
{
	const reg_entry *e = reg_get(t, id);
	return e ? e->impl : NULL;
}

int reg_conflicts(char *out, size_t n)
{
	if (out && n) snprintf(out, n, "%s", conflicts);
	return conflicts[0] != 0;
}

const char *reg_table_name(int t)
{
	static const char *const nm[REG_NTABLES] = { "kind", "action", "source", "setting", "query", "renderop" };
	return t >= 0 && t < REG_NTABLES ? nm[t] : "?";
}
