/* SPDX-License-Identifier: AGPL-3.0-or-later */
/* Copyright (C) 2026 Jeffrey A. Cummings */
/* mu.c: e26-render's MuPDF context (mu.h). */
#include "mu.h"
#ifdef HAVE_MUPDF
#include <stdlib.h>
#include <string.h>

static fz_context *ctx;
static size_t live;
static int over;

/* every block carries its size in front of it (16 bytes, so the alignment malloc gave is kept) */
#define HDR 16u

static void *a_malloc(void *u, size_t n)
{
	unsigned char *p;
	(void)u;
	if (n > MU_HEAP || live + n > MU_HEAP) { over = 1; return NULL; }
	if (!(p = (unsigned char *)malloc(n + HDR))) { over = 1; return NULL; }
	memcpy(p, &n, sizeof n);
	live += n;
	return p + HDR;
}

static void a_free(void *u, void *q)
{
	unsigned char *p = (unsigned char *)q;
	size_t n;
	(void)u;
	if (!p) return;
	p -= HDR;
	memcpy(&n, p, sizeof n);
	live -= n;
	free(p);
}

static void *a_realloc(void *u, void *q, size_t n)
{
	unsigned char *p = (unsigned char *)q, *np;
	size_t old;
	if (!p) return a_malloc(u, n);
	if (!n) { a_free(u, q); return NULL; }
	p -= HDR;
	memcpy(&old, p, sizeof old);
	if (n > old && (n - old > MU_HEAP || live + (n - old) > MU_HEAP)) { over = 1; return NULL; }
	if (!(np = (unsigned char *)realloc(p, n + HDR))) { over = 1; return NULL; }
	live = live - old + n;
	memcpy(np, &n, sizeof n);
	return np + HDR;
}

static void quiet(void *u, const char *m) { (void)u; (void)m; }

fz_context *mu_ctx(void)
{
	static const fz_alloc_context alloc = { NULL, a_malloc, a_realloc, a_free };
	if (ctx) return ctx;
	ctx = fz_new_context(&alloc, NULL, 32u * 1024u * 1024u);   /* a 32 MiB resource store */
	if (!ctx) return NULL;
	fz_set_warning_callback(ctx, quiet, NULL);
	fz_set_error_callback(ctx, quiet, NULL);
	fz_try(ctx) fz_register_document_handlers(ctx);
	fz_catch(ctx) { fz_drop_context(ctx); ctx = NULL; }
	return ctx;
}

const char *mu_code(void) { return over ? "E_TOO_LARGE" : "E_ARGUMENT"; }
void mu_reset_code(void) { over = 0; }

void mu_to_rgba(const unsigned char *s, int stride, int n, int alpha, int w, int h, unsigned char *out)
{
	int x, y;
	for (y = 0; y < h; y++) {
		const unsigned char *p = s + (size_t)y * stride;
		for (x = 0; x < w; x++, p += n, out += 4) {
			unsigned a = alpha ? p[n - 1] : 255, k;
			int nc = alpha ? n - 1 : n;
			for (k = 0; k < 3; k++) {
				unsigned c = nc >= 3 ? p[k] : p[0];          /* grey: the one channel */
				if (alpha) c = a ? (c * 255 + a / 2) / a : 0;  /* MuPDF keeps colour premultiplied */
				out[k] = (unsigned char)(c > 255 ? 255 : c);
			}
			out[3] = (unsigned char)a;
		}
	}
}
#else
typedef int mu_unused; /* MuPDF absent: nothing here (ISO C wants something in a file) */
#endif
