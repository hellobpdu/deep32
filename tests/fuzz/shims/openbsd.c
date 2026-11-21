/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * openbsd.c: portable versions of recallocarray(3), freezero(3) and timingsafe_bcmp(3) for the Linux fuzz build,
 * with the semantics the OpenBSD manual pages give. recallocarray copies exactly the old size, so AddressSanitizer
 * reports a caller that passes an old element count larger than its allocation, as OpenBSD's malloc would fault.
 */
#include "openbsd.h"
#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static void wipe(void *p, size_t n)
{
	volatile unsigned char *v = (volatile unsigned char *)p;
	while (n--) *v++ = 0;
}

void *recallocarray(void *ptr, size_t oldnmemb, size_t newnmemb, size_t size)
{
	size_t oldsize, newsize;
	void *newptr;

	if (ptr == NULL)
		return calloc(newnmemb, size);
	if (size != 0 && (newnmemb > SIZE_MAX / size || oldnmemb > SIZE_MAX / size)) {
		errno = ENOMEM;
		return NULL;
	}
	newsize = newnmemb * size;
	oldsize = oldnmemb * size;
	newptr = malloc(newsize ? newsize : 1);
	if (newptr == NULL)
		return NULL;
	if (newsize > oldsize) {
		memcpy(newptr, ptr, oldsize);
		memset((unsigned char *)newptr + oldsize, 0, newsize - oldsize);
	} else
		memcpy(newptr, ptr, newsize);
	wipe(ptr, oldsize);
	free(ptr);
	return newptr;
}

void freezero(void *ptr, size_t size)
{
	if (ptr == NULL)
		return;
	wipe(ptr, size);
	free(ptr);
}

int timingsafe_bcmp(const void *b1, const void *b2, size_t n)
{
	const unsigned char *p1 = b1, *p2 = b2;
	int ret = 0;

	for (; n > 0; n--)
		ret |= *p1++ ^ *p2++;
	return ret != 0;
}
