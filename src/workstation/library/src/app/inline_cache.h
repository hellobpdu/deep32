/* inline_cache.h: viewport order and eviction for ARTICLE's inline picture cache. */
#ifndef E26_INLINE_CACHE_H
#define E26_INLINE_CACHE_H

#include "picture.h"
#include <limits.h>
#include <stdint.h>

typedef struct {
	enum picture_status status;
	size_t bytes;         /* reservation for a pending or cached device-size pixmap */
	unsigned long ticket; /* distinguishes old and new requests for the same media id */
	int w, h, distance;  /* logical size and distance of the nearest occurrence */
} inline_pic;

static inline int inline_distance(int y, int h, int scroll, int view)
{
	int64_t bottom = (int64_t)y + h, end = (int64_t)scroll + view;
	int64_t d = bottom <= scroll ? scroll - bottom + 1 : y >= end ? y - end + 1 : 0;
	return d > INT_MAX ? INT_MAX : (int)d;
}

static inline int inline_next(const inline_pic *p, int n, int near)
{
	int i, best = -1;
	for (i = 0; i < n; i++)
		if (p[i].status == PICTURE_IDLE && p[i].distance <= near &&
		    (best < 0 || p[i].distance < p[best].distance)) best = i;
	return best;
}

static inline int inline_victim(const inline_pic *p, int n, int incoming)
{
	int i, farthest = -1;
	for (i = 0; i < n; i++)
		if (p[i].status == PICTURE_READY && p[i].distance > incoming &&
		    (farthest < 0 || p[i].distance > p[farthest].distance)) farthest = i;
	return farthest;
}

#endif
