/* assetlru.c: the asset cache's least-recently-used bookkeeping (assetlru.h). */
#include "assetlru.h"
#include <string.h>

void alru_init(alru *l, alru_slot *slots, int n, size_t budget, unsigned keep)
{
	memset(l, 0, sizeof *l);
	memset(slots, 0, (size_t)n * sizeof *slots);
	l->s = slots;
	l->n = n;
	l->budget = budget;
	l->keep = keep;
}

void alru_use(alru *l, int i)
{
	if (i >= 0 && i < l->n) l->s[i].use = ++l->clock;
}

void alru_fill(alru *l, int i, size_t bytes)
{
	if (i < 0 || i >= l->n) return;
	if (l->s[i].live) l->used -= l->s[i].bytes;
	l->s[i].live = 1;
	l->s[i].bytes = bytes;
	l->used += bytes;
	alru_use(l, i);
}

void alru_empty(alru *l, int i)
{
	if (i < 0 || i >= l->n || !l->s[i].live) return;
	l->used -= l->s[i].bytes;
	l->s[i].live = 0;
	l->s[i].bytes = 0;
	l->evictions++;
}

int alru_over(const alru *l, size_t add)
{
	return l->used + add > l->budget;
}

int alru_victim(const alru *l, int except)
{
	int i, best = -1;
	for (i = 0; i < l->n; i++) {
		const alru_slot *s = &l->s[i];
		if (!s->live || i == except) continue;
		if (l->clock - s->use < l->keep) continue;   /* one of the most recent uses */
		if (best < 0 || s->use < l->s[best].use) best = i;
	}
	return best;
}

int alru_free_slot(const alru *l, int hw)
{
	int i;
	for (i = 0; i < hw && i < l->n; i++)
		if (!l->s[i].live) return i;
	return -1;
}
