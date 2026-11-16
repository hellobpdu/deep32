/* assetlru.h: the bookkeeping of the picture and asset cache (W1-IMAGE, plan E6), X-free so the tests drive it.
 * Slots hold what xassets.c uploaded (store bitmaps, decoded pictures); each has a size in bytes and the time of its
 * last use on a counter. A cache over its byte budget, or out of slots, gives up its least recently used slot first,
 * except the most recent few ("keep"): a caller that took two assets one after the other still holds both. */
#ifndef E26_ASSETLRU_H
#define E26_ASSETLRU_H
#include <stddef.h>

typedef struct {
	unsigned long long use;   /* the counter at its last use */
	size_t bytes;
	int live;
} alru_slot;

typedef struct {
	alru_slot *s;
	int n;                    /* slots */
	size_t used, budget;      /* bytes held, bytes allowed */
	unsigned long long clock;
	unsigned keep;            /* the most recently used slots never given up */
	unsigned long evictions;
} alru;

void alru_init(alru *l, alru_slot *slots, int n, size_t budget, unsigned keep);
void alru_use(alru *l, int i);                   /* slot i was used now */
void alru_fill(alru *l, int i, size_t bytes);    /* slot i now holds bytes (and was used now) */
void alru_empty(alru *l, int i);                 /* slot i was given up */
int alru_over(const alru *l, size_t add);        /* 1 when adding add bytes passes the budget */
/* the slot to give up: the least recently used live slot outside the "keep" most recent ones, not except; -1 none */
int alru_victim(const alru *l, int except);
/* a slot that holds nothing, below hw (the slots handed out so far); -1 none */
int alru_free_slot(const alru *l, int hw);

#endif
