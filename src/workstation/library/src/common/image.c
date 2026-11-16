/* image.c: RGBA images, Netpbm P6/P7 writer and reader (strict, bounded). */
#include "image.h"
#include "sha256.h"
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int e26_image_alloc(e26_image *im, int w, int h)
{
	im->w = im->h = 0;
	im->rgba = NULL;
	if (w <= 0 || h <= 0 || (long)w * (long)h > E26_IMAGE_MAX_PIXELS) return -1;
	im->rgba = (unsigned char *)calloc((size_t)w * (size_t)h, 4);
	if (!im->rgba) return -1;
	im->w = w;
	im->h = h;
	return 0;
}

void e26_image_free(e26_image *im)
{
	free(im->rgba);
	im->rgba = NULL;
	im->w = im->h = 0;
}

int e26_image_opaque(const e26_image *im)
{
	size_t i, n = (size_t)im->w * (size_t)im->h;
	for (i = 0; i < n; i++)
		if (im->rgba[4 * i + 3] != 255) return 0;
	return 1;
}

void e26_image_sha256(const e26_image *im, char hex[65])
{
	sha256_hex(im->rgba, (size_t)im->w * (size_t)im->h * 4, hex);
}

int e26_image_write_pnm(const e26_image *im, const char *path)
{
	FILE *f = fopen(path, "wb");
	size_t i, n = (size_t)im->w * (size_t)im->h;
	int ok = 1;
	if (!f) return -1;
	if (e26_image_opaque(im)) {
		fprintf(f, "P6\n%d %d\n255\n", im->w, im->h);
		for (i = 0; i < n && ok; i++)
			ok = fwrite(im->rgba + 4 * i, 1, 3, f) == 3;
	} else {
		fprintf(f, "P7\nWIDTH %d\nHEIGHT %d\nDEPTH 4\nMAXVAL 255\nTUPLTYPE RGB_ALPHA\nENDHDR\n", im->w, im->h);
		ok = fwrite(im->rgba, 4, n, f) == n;
	}
	if (fclose(f) != 0) ok = 0;
	return ok ? 0 : -1;
}

/* P6 header token reader: skips whitespace and # comments. */
static int pnm_int(FILE *f, int *v)
{
	int c, n = 0, any = 0;
	for (;;) {
		c = fgetc(f);
		if (c == '#') {
			while ((c = fgetc(f)) != EOF && c != '\n') {
			}
			continue;
		}
		if (c == EOF) return -1;
		if (!isspace(c)) break;
	}
	while (c != EOF && isdigit(c)) {
		if (n > 100000) return -1;
		n = n * 10 + (c - '0');
		any = 1;
		c = fgetc(f);
	}
	if (!any) return -1;
	*v = n;
	return 0; /* the single whitespace after the token was consumed */
}

int e26_image_read_pnm(const char *path, e26_image *im, char *err, size_t errlen)
{
	FILE *f = fopen(path, "rb");
	char magic[3] = { 0 };
	int w = 0, h = 0, maxv = 0, depth = 0;
	size_t i, n;
	im->rgba = NULL;
	im->w = im->h = 0;
	if (!f) { snprintf(err, errlen, "cannot open %s", path); return -1; }
	if (fread(magic, 1, 2, f) != 2) goto bad;
	if (!strcmp(magic, "P6")) {
		if (pnm_int(f, &w) || pnm_int(f, &h) || pnm_int(f, &maxv) || maxv != 255) goto bad;
		depth = 3;
	} else if (!strcmp(magic, "P7")) {
		char line[128], key[32], val[64];
		while (fgets(line, sizeof line, f)) {
			if (line[0] == '#' || line[0] == '\n') continue;
			if (!strncmp(line, "ENDHDR", 6)) break;
			val[0] = 0;
			if (sscanf(line, "%31s %63s", key, val) < 1) goto bad;
			if (!strcmp(key, "WIDTH")) w = atoi(val);
			else if (!strcmp(key, "HEIGHT")) h = atoi(val);
			else if (!strcmp(key, "DEPTH")) depth = atoi(val);
			else if (!strcmp(key, "MAXVAL")) maxv = atoi(val);
		}
		if (maxv != 255 || (depth != 3 && depth != 4)) goto bad;
	} else goto bad;
	if (e26_image_alloc(im, w, h)) { snprintf(err, errlen, "%s: bad size %dx%d", path, w, h); fclose(f); return -1; }
	n = (size_t)w * (size_t)h;
	for (i = 0; i < n; i++) {
		unsigned char px[4] = { 0, 0, 0, 255 };
		if (fread(px, 1, (size_t)depth, f) != (size_t)depth) { e26_image_free(im); goto bad; }
		memcpy(im->rgba + 4 * i, px, 4);
	}
	fclose(f);
	return 0;
bad:
	snprintf(err, errlen, "%s: not a P6/P7 8-bit pixmap", path);
	fclose(f);
	return -1;
}
