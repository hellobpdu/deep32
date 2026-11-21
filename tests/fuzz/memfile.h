/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * memfile.h: in-memory files for harnesses whose target reads or writes a path or a descriptor. Linux only
 * (memfd_create); define _GNU_SOURCE before the first system header of the harness.
 */
#ifndef D32_FUZZ_MEMFILE_H
#define D32_FUZZ_MEMFILE_H
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>

typedef struct {
	int fd;
	char path[64]; /* /proc/self/fd/N: a path that opens the same in-memory file */
} memfile;

/* a new in-memory file holding n bytes of data (data may be NULL when n is 0), positioned at its start */
static inline void memfile_open(memfile *m, const void *data, size_t n)
{
	const unsigned char *p = (const unsigned char *)data;
	size_t off = 0;
	m->fd = memfd_create("d32fuzz", 0);
	if (m->fd < 0) abort();
	while (off < n) {
		ssize_t w = write(m->fd, p + off, n - off);
		if (w <= 0) abort();
		off += (size_t)w;
	}
	if (lseek(m->fd, 0, SEEK_SET) != 0) abort();
	snprintf(m->path, sizeof m->path, "/proc/self/fd/%d", m->fd);
}

/* the whole file into a malloc'd buffer (*n bytes); the caller frees it */
static inline unsigned char *memfile_read(const memfile *m, size_t *n)
{
	off_t end = lseek(m->fd, 0, SEEK_END);
	unsigned char *b;
	size_t got = 0;
	if (end < 0 || lseek(m->fd, 0, SEEK_SET) != 0) abort();
	b = (unsigned char *)malloc((size_t)end + 1);
	if (!b) abort();
	while (got < (size_t)end) {
		ssize_t r = read(m->fd, b + got, (size_t)end - got);
		if (r <= 0) abort();
		got += (size_t)r;
	}
	*n = got;
	return b;
}

static inline void memfile_close(memfile *m)
{
	close(m->fd);
	m->fd = -1;
}

#endif
