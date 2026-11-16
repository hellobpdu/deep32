/* dib.c: strict DIB/RLE4/RLE8 decoder. Logic follows the private-analysis readers (tools/neres.py and
 * Codex ref/images.py), re-written in C99 for the importer. */
#include "dib.h"
#include "../common/le.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define FAIL(...) do { snprintf(err, errlen, __VA_ARGS__); goto fail; } while (0)

static int mask_shift(unsigned long m, int *shift, unsigned long *max)
{
	int s = 0;
	if (!m) { *shift = 0; *max = 0; return 0; }
	while (!(m & 1)) { m >>= 1; s++; }
	*shift = s;
	*max = m;
	return 0;
}

/* The twenty static colours of the Windows palette manager (the system palette's entries 0-9 and 246-255 on a
 * 256-colour display, Windows 3.1 and later; the same values on the S3 driver of the capture lane: ENCSMALL's index
 * 7 shows as #C0C0C0 on capture256 and its index 248 as #808080, AUTHENTICITY 16.4). Windows' defaults, not disc
 * data. */
const unsigned char dib_sys_static[20][3] = {
	{ 0x00, 0x00, 0x00 }, { 0x80, 0x00, 0x00 }, { 0x00, 0x80, 0x00 }, { 0x80, 0x80, 0x00 }, { 0x00, 0x00, 0x80 },
	{ 0x80, 0x00, 0x80 }, { 0x00, 0x80, 0x80 }, { 0xC0, 0xC0, 0xC0 }, { 0xC0, 0xDC, 0xC0 }, { 0xA6, 0xCA, 0xF0 },
	{ 0xFF, 0xFB, 0xF0 }, { 0xA0, 0xA0, 0xA4 }, { 0x80, 0x80, 0x80 }, { 0xFF, 0x00, 0x00 }, { 0x00, 0xFF, 0x00 },
	{ 0xFF, 0xFF, 0x00 }, { 0x00, 0x00, 0xFF }, { 0xFF, 0x00, 0xFF }, { 0x00, 0xFF, 0xFF }, { 0xFF, 0xFF, 0xFF },
};

int dib_sys_index(int k)
{
	return k < 10 ? k : 236 + k;
}

static int decode(const unsigned char *p, size_t n, int icon, int sys, e26_image *out, dib_info *info, char *err, size_t errlen);

int dib_decode(const unsigned char *p, size_t n, int icon, e26_image *out, dib_info *info, char *err, size_t errlen)
{
	return decode(p, n, icon, 0, out, info, err, errlen);
}

int dib_decode_sys(const unsigned char *p, size_t n, int icon, e26_image *out, dib_info *info, char *err, size_t errlen)
{
	return decode(p, n, icon, 1, out, info, err, errlen);
}

static int decode(const unsigned char *p, size_t n, int icon, int sys, e26_image *out, dib_info *info, char *err, size_t errlen)
{
	size_t base = 0, hsz, pal_at, start, stride;
	long w, h, height, x, y;
	int bpp, comp, planes, pal_entry, colours, i;
	unsigned char pal[256][4];
	unsigned char *idx = NULL; /* indexed pixels for RLE */
	const unsigned char *src;
	size_t srclen;

	memset(info, 0, sizeof(*info));
	out->rgba = NULL;
	out->w = out->h = 0;
	if (n >= 2 && p[0] == 'B' && p[1] == 'M') { base = 14; info->file_header = 1; }
	if (n < base + 12) FAIL("truncated header");
	hsz = le32(p + base);
	if (hsz == 12) {
		w = le16(p + base + 4); h = le16(p + base + 6);
		planes = le16(p + base + 8); bpp = le16(p + base + 10);
		comp = 0; colours = 0; pal_entry = 3;
	} else if (hsz == 40 || hsz == 52 || hsz == 56 || hsz == 108 || hsz == 124) {
		if (n < base + hsz) FAIL("truncated BITMAPINFOHEADER");
		w = le32s(p + base + 4); h = le32s(p + base + 8);
		planes = le16(p + base + 12); bpp = le16(p + base + 14);
		comp = (int)le32(p + base + 16);
		colours = (int)le32(p + base + 32);
		pal_entry = 4;
	} else FAIL("unknown header size %lu", (unsigned long)hsz);
	if (icon) h /= 2;
	height = h < 0 ? -h : h;
	if (w <= 0 || w > 32768 || height <= 0 || height > 32768 || w * height > E26_IMAGE_MAX_PIXELS)
		FAIL("implausible dimensions %ldx%ld", w, h);
	if (planes != 1) FAIL("planes %d", planes);
	if (bpp != 1 && bpp != 4 && bpp != 8 && bpp != 16 && bpp != 24 && bpp != 32) FAIL("bpp %d", bpp);
	if (comp < 0 || comp > 3 || (comp == 1 && bpp != 8) || (comp == 2 && bpp != 4) || (comp == 3 && bpp != 16 && bpp != 32))
		FAIL("unsupported compression %d with %d bpp", comp, bpp);
	if (colours < 0 || colours > 256) FAIL("palette count %d", colours);
	if (!colours && bpp <= 8) colours = 1 << bpp;
	if (bpp <= 8 && colours > (1 << bpp)) FAIL("palette %d exceeds bit depth %d", colours, bpp);
	pal_at = base + hsz + ((comp == 3 && hsz == 40) ? 12 : 0);
	if (pal_at + (size_t)colours * pal_entry > n) FAIL("truncated palette");
	for (i = 0; i < 256; i++) { pal[i][0] = pal[i][1] = pal[i][2] = 0; pal[i][3] = 0; }
	for (i = 0; i < colours && bpp <= 8; i++) {
		const unsigned char *q = p + pal_at + (size_t)i * pal_entry;
		pal[i][0] = q[2]; pal[i][1] = q[1]; pal[i][2] = q[0]; pal[i][3] = 255;
	}
	if (sys && bpp == 8 && colours == 256) /* drawn through the system palette: its static entries win */
		for (i = 0; i < 20; i++) {
			int k = dib_sys_index(i);
			pal[k][0] = dib_sys_static[i][0]; pal[k][1] = dib_sys_static[i][1]; pal[k][2] = dib_sys_static[i][2];
		}
	start = base ? le32(p + 10) : pal_at + (size_t)(bpp <= 8 ? colours : 0) * pal_entry;
	if (start > n || start < pal_at) FAIL("invalid pixel offset");
	src = p + start;
	srclen = n - start;
	if (e26_image_alloc(out, (int)w, (int)height)) FAIL("allocation");
	info->width = (int)w; info->height = (int)height; info->bpp = bpp; info->compression = comp;
	info->header_size = (int)hsz; info->palette_entries = bpp <= 8 ? colours : 0;

	if (comp == 1 || comp == 2) {
		size_t q = 0;
		int ended = 0;
		if (h < 0) FAIL("top-down RLE bitmap");
		idx = (unsigned char *)calloc((size_t)w * (size_t)height, 1);
		if (!idx) FAIL("allocation");
		x = y = 0;
		while (q + 2 <= srclen) {
			unsigned cnt = src[q], c = src[q + 1];
			q += 2;
			if (cnt) {
				unsigned k, len = cnt;
				if (comp == 2 && (w & 1) && x + (long)len == w + 1) { len--; info->rle4_padding++; }
				if (y >= height || x + (long)len > w) FAIL("RLE overrun at x=%ld y=%ld", x, y);
				for (k = 0; k < len; k++)
					idx[y * w + x + k] = (unsigned char)(comp == 1 ? c : ((k & 1) ? (c & 15) : (c >> 4)));
				x += len;
			} else if (c == 0) {
				x = 0; y++;
				if (y > height) FAIL("RLE row overrun");
			} else if (c == 1) {
				ended = 1;
				break;
			} else if (c == 2) {
				if (q + 2 > srclen) FAIL("truncated RLE delta");
				x += src[q]; y += src[q + 1]; q += 2;
				if (x > w || y >= height) FAIL("RLE delta overrun");
			} else {
				unsigned k, bytes = comp == 1 ? c : (c + 1) / 2, len = c;
				if (q + bytes + (bytes & 1) > srclen) FAIL("truncated RLE absolute run");
				if (comp == 2 && (w & 1) && x + (long)len == w + 1) { len--; info->rle4_padding++; }
				if (y >= height || x + (long)len > w) FAIL("RLE overrun at x=%ld y=%ld", x, y);
				for (k = 0; k < len; k++)
					idx[y * w + x + k] = (unsigned char)(comp == 1 ? src[q + k] : ((k & 1) ? (src[q + k / 2] & 15) : (src[q + k / 2] >> 4)));
				x += len;
				q += bytes + (bytes & 1);
			}
		}
		if (!ended) info->rle_no_eob = 1;
		for (y = 0; y < height; y++)
			for (x = 0; x < w; x++) {
				unsigned v = idx[y * w + x];
				unsigned char *d = out->rgba + 4 * ((height - 1 - y) * w + x);
				if ((int)v >= colours) info->undefined_pixels++;
				memcpy(d, pal[v], 4);
			}
		free(idx);
		return 0;
	}

	stride = (size_t)((w * bpp + 31) / 32) * 4;
	if (srclen < stride * (size_t)height) FAIL("pixel data truncated");
	{
		unsigned long masks[3] = { 0, 0, 0 }, mx[3];
		int sh[3];
		if (bpp == 16 || bpp == 32) {
			if (comp == 3) {
				if (hsz == 40) { masks[0] = le32(p + base + 40); masks[1] = le32(p + base + 44); masks[2] = le32(p + base + 48); }
				else { masks[0] = le32(p + base + 40); masks[1] = le32(p + base + 44); masks[2] = le32(p + base + 48); }
			} else if (bpp == 16) { masks[0] = 0x7c00; masks[1] = 0x3e0; masks[2] = 0x1f; }
			else { masks[0] = 0xff0000; masks[1] = 0xff00; masks[2] = 0xff; }
			for (i = 0; i < 3; i++) mask_shift(masks[i], &sh[i], &mx[i]);
		}
		for (y = 0; y < height; y++) {
			const unsigned char *line = src + (size_t)y * stride;
			long oy = h > 0 ? height - 1 - y : y;
			for (x = 0; x < w; x++) {
				unsigned char *d = out->rgba + 4 * (oy * w + x);
				if (bpp == 24) { d[0] = line[3 * x + 2]; d[1] = line[3 * x + 1]; d[2] = line[3 * x]; d[3] = 255; }
				else if (bpp == 16 || bpp == 32) {
					unsigned long v = bpp == 16 ? le16(line + 2 * x) : le32(line + 4 * x);
					for (i = 0; i < 3; i++)
						d[i] = (unsigned char)(mx[i] ? ((v & masks[i]) >> sh[i]) * 255 / mx[i] : 0);
					d[3] = 255;
				} else {
					unsigned v;
					if (bpp == 8) v = line[x];
					else if (bpp == 4) v = (line[x / 2] >> ((x & 1) ? 0 : 4)) & 15;
					else v = (line[x / 8] >> (7 - (x & 7))) & 1;
					if ((int)v >= colours) info->undefined_pixels++;
					memcpy(d, pal[v], 4);
				}
			}
		}
		if (icon) {
			/* AND mask: 1 bpp, same height, set bit = transparent */
			size_t mstride = (size_t)((w + 31) / 32) * 4;
			const unsigned char *m = src + stride * (size_t)height;
			if (srclen < stride * (size_t)height + mstride * (size_t)height) FAIL("icon mask truncated");
			for (y = 0; y < height; y++)
				for (x = 0; x < w; x++) {
					long oy = height - 1 - y;
					if ((m[(size_t)y * mstride + x / 8] >> (7 - (x & 7))) & 1) out->rgba[4 * (oy * w + x) + 3] = 0;
				}
		}
	}
	return 0;
fail:
	free(idx);
	e26_image_free(out);
	return -1;
}
