/* e26p.c: E26P frames (e26p.h). */
#include "e26p.h"
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void put16(unsigned char *p, uint32_t v) { p[0] = (unsigned char)(v >> 8); p[1] = (unsigned char)v; }
static void put32(unsigned char *p, uint32_t v) { put16(p, v >> 16); put16(p + 2, v & 0xffff); }
static uint32_t get16(const unsigned char *p) { return (uint32_t)p[0] << 8 | p[1]; }
static uint32_t get32(const unsigned char *p) { return get16(p) << 16 | get16(p + 2); }

void e26p_pack(const e26p_header *h, unsigned char out[E26P_HEADER])
{
	memcpy(out, "E26P", 4);
	put16(out + 4, h->major);
	put16(out + 6, h->minor);
	put32(out + 8, h->length);
	put32(out + 12, (uint32_t)(h->id >> 32));
	put32(out + 16, (uint32_t)(h->id & 0xffffffffu));
	put32(out + 20, h->kind);
}

int e26p_unpack(const unsigned char in[E26P_HEADER], e26p_header *h)
{
	if (memcmp(in, "E26P", 4)) return -1;
	h->major = (uint16_t)get16(in + 4);
	h->minor = (uint16_t)get16(in + 6);
	h->length = get32(in + 8);
	h->id = (uint64_t)get32(in + 12) << 32 | get32(in + 16);
	h->kind = get32(in + 20);
	if (h->major != 1 || h->minor != 0) return -1;
	if (h->length < 1 || h->length > E26P_MAX_BODY || h->id == 0) return -1;
	if (h->kind != E26P_REQUEST && h->kind != E26P_RESPONSE && h->kind != E26P_CANCEL) return -1;
	return 0;
}

/* all of n bytes, or 0 at end of file before the first, -1 otherwise */
static int read_all(int fd, unsigned char *p, size_t n)
{
	size_t got = 0;
	while (got < n) {
		ssize_t r = read(fd, p + got, n - got);
		if (r < 0 && errno == EINTR) continue;
		if (r < 0) return -1;
		if (r == 0) return got ? -1 : 0;
		got += (size_t)r;
	}
	return 1;
}

int e26p_read(int fd, e26p_header *h, char **body)
{
	unsigned char hb[E26P_HEADER];
	int r = read_all(fd, hb, sizeof hb);
	*body = NULL;
	if (r <= 0) return r;
	if (e26p_unpack(hb, h)) return -1;
	if (!(*body = (char *)malloc((size_t)h->length + 1))) return -1;
	if (read_all(fd, (unsigned char *)*body, h->length) != 1) { free(*body); *body = NULL; return -1; }
	(*body)[h->length] = 0;
	return 1;
}

int e26p_write(int fd, uint64_t id, uint32_t kind, const char *body, size_t len)
{
	e26p_header h;
	unsigned char hb[E26P_HEADER];
	size_t off = 0;
	if (len < 1 || len > E26P_MAX_BODY) return -1;
	h.major = 1; h.minor = 0; h.length = (uint32_t)len; h.id = id; h.kind = kind;
	e26p_pack(&h, hb);
	while (off < sizeof hb + len) {
		const unsigned char *p = off < sizeof hb ? hb + off : (const unsigned char *)body + (off - sizeof hb);
		size_t n = off < sizeof hb ? sizeof hb - off : len - (off - sizeof hb);
		ssize_t w = write(fd, p, n);
		if (w < 0 && errno == EINTR) continue;
		if (w <= 0) return -1;
		off += (size_t)w;
	}
	return 0;
}
