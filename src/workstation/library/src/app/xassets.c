/* xassets.c: private asset store pixmaps -> X server pixmaps (TrueColor visuals), plus 1-bit masks for the
 * transparent pixels the importer kept. The original pixels are copied exactly: no scaling, no dithering. One
 * exception, asked for by name: "<asset>@ui" is the asset drawn through the Library's interface palette (below).
 *
 * W1-IMAGE (plan E6): the slots are a cache, least recently used first out, with a byte budget (xassets.h,
 * assetlru.h), so the slot count no longer limits anything; decoded pictures (kind_picture.c) are held here too,
 * under their own names (xa_put_rgba). */
#include "library_app.h"
#include "xassets.h"
#include "../common/assetlru.h"
#include "../common/image.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int shift_of(unsigned long m) { int s = 0; if (!m) return 0; while (!(m & 1)) { m >>= 1; s++; } return s; }
static int bits_of(unsigned long m) { int b = 0; while (m) { b += (int)(m & 1); m >>= 1; } return b; }

static unsigned long chan(unsigned v, unsigned long mask)
{
	int b = bits_of(mask), s = shift_of(mask);
	unsigned long x = b >= 8 ? (unsigned long)v << (b - 8) : (unsigned long)v >> (8 - b);
	return (x << s) & mask;
}

unsigned long xa_pixel(e26_app *a, long rgb)
{
	return chan((unsigned)(rgb >> 16) & 255, a->rmask) | chan((unsigned)(rgb >> 8) & 255, a->gmask) | chan((unsigned)rgb & 255, a->bmask);
}

/* "<asset>@ui": a bitmap with colours of its own (FW_CAT_BMP has 48) as the period design shows it on a 256-colour
 * display, where its 32-entry interface palette is realised and GDI draws the DIB through it: every colour becomes
 * the nearest table entry (least squared RGB distance, the first entry on a tie). Measured: on the 256-colour
 * capture 20 all 20,447 pixels of FW_CAT_BMP at (12,97) equal their nearest table entry (#303028 -> entry 28,
 * #E33030 -> entry 3, #FBD351 -> entry 16, ...); drawn as stored they differ by up to a few units a channel. */
static void through_ui_palette(e26_app *a, e26_image *im)
{
	const rec_palette *source = a->appearance_theme ? &a->classic_pal : &a->pal;
	size_t i, n = (size_t)im->w * im->h;
	for (i = 0; i < n; i++) {
		unsigned char *p = im->rgba + 4 * i;
		long bd = 0;
		int best = -1, k;
		for (k = 0; k < REC_PAL_N; k++) {
			long v = rec_pal(source, k), dr, dg, db, d;
			if (v < 0) continue;
			dr = ((v >> 16) & 255) - p[0]; dg = ((v >> 8) & 255) - p[1]; db = (v & 255) - p[2];
			d = dr * dr + dg * dg + db * db;
			if (best < 0 || d < bd) { best = k; bd = d; }
		}
		if (best >= 0) {
			long v = rec_pal(&a->pal, best);
			p[0] = (unsigned char)(v >> 16); p[1] = (unsigned char)(v >> 8); p[2] = (unsigned char)v;
		}
	}
}

long xa_sample(const xasset *x, int px, int py)
{
	const unsigned char *p;
	if (!x || !x->rgba || px < 0 || py < 0 || px >= x->w || py >= x->h) return -1;
	p = x->rgba + 4 * ((size_t)py * x->w + px);
	return ((long)p[0] << 16) | ((long)p[1] << 8) | p[2];
}

/* ---- the cache: one assetlru slot per e26_app slot */

static alru L;
static alru_slot S[E26_MAX_ASSETS];
static int ready;
static size_t want_budget;

static void cache_init(void)
{
	size_t b;
	if (ready) return;
	b = want_budget ? want_budget : (size_t)set_get_int("PictureCacheMB", XA_DEFAULT_MB) * 1024u * 1024u;
	if (b < 1024u * 1024u) b = (size_t)XA_DEFAULT_MB * 1024u * 1024u;
	alru_init(&L, S, E26_MAX_ASSETS, b, XA_KEEP);
	ready = 1;
}

void xa_set_budget(size_t bytes)
{
	want_budget = bytes;
	if (ready) L.budget = bytes ? bytes : (size_t)XA_DEFAULT_MB * 1024u * 1024u;
}
size_t xa_budget(void) { cache_init(); return L.budget; }
size_t xa_used(void) { return ready ? L.used : 0; }
unsigned long xa_evictions(void) { return ready ? L.evictions : 0; }
int xa_live(void)
{
	int i, n = 0;
	for (i = 0; ready && i < E26_MAX_ASSETS; i++) n += S[i].live;
	return n;
}

/* what an entry holds: the server's pixmap (4 bytes a pixel at depth 24), the pixels kept for sampling, the mask */
static size_t cost(int w, int h, int kept, int masked)
{
	size_t px = (size_t)w * (size_t)h;
	return px * 4 + (kept ? px * 4 : 0) + (masked ? (size_t)((w + 7) / 8) * (size_t)h : 0);
}

static void release(e26_app *a, int i)
{
	xasset *x = &a->asset[i];
	/* W1-SHARP keeps this pixmap's k-times copies by its XID, which the server may give a new pixmap once it is freed */
	if (x->pm) { gfx_forget_pixmap(a, x->pm); XFreePixmap(a->dpy, x->pm); }
	if (x->mask) XFreePixmap(a->dpy, x->mask);
	free(x->rgba);
	if (a->verbose && x->name[0]) fprintf(stderr, "d32library: asset %s given up (cache)\n", x->name);
	memset(x, 0, sizeof *x);
	alru_empty(&L, i);
}

static int lookup(e26_app *a, const char *name)
{
	int i;
	for (i = 0; i < a->nasset; i++)
		if (S[i].live && !strcmp(a->asset[i].name, name)) return i;
	return -1;
}

/* a slot for an entry of bytes: least recently used entries go while the cache would pass its budget, or when no
 * slot is free; -1 when every slot is among the most recently used */
static int slot_for(e26_app *a, size_t bytes)
{
	int i, v;
	while (alru_over(&L, bytes) && (v = alru_victim(&L, -1)) >= 0) release(a, v);
	if ((i = alru_free_slot(&L, a->nasset)) >= 0) return i;
	if (a->nasset < E26_MAX_ASSETS) return a->nasset++;
	if ((v = alru_victim(&L, -1)) < 0) return -1;
	release(a, v);
	return v;
}

/* the pixels into the entry's server pixmap (and its mask when some are transparent) */
static int upload(e26_app *a, xasset *x, const unsigned char *rgba, int w, int h)
{
	XImage *xi;
	char *data;
	int y, xx, opaque = 1;
	size_t k, n = (size_t)w * h;
	for (k = 0; k < n && opaque; k++)
		if (rgba[4 * k + 3] != 255) opaque = 0;
	data = (char *)malloc(n * 4);
	if (!data) return -1;
	xi = XCreateImage(a->dpy, a->vis, (unsigned)a->depth, ZPixmap, 0, data, (unsigned)w, (unsigned)h, 32, 0);
	if (!xi) { free(data); return -1; }
	for (y = 0; y < h; y++)
		for (xx = 0; xx < w; xx++) {
			const unsigned char *p = rgba + 4 * ((size_t)y * w + xx);
			XPutPixel(xi, xx, y, xa_pixel(a, ((long)p[0] << 16) | ((long)p[1] << 8) | p[2]));
		}
	x->pm = XCreatePixmap(a->dpy, a->win, (unsigned)w, (unsigned)h, (unsigned)a->depth);
	XPutImage(a->dpy, x->pm, a->gc, xi, 0, 0, 0, 0, (unsigned)w, (unsigned)h);
	XDestroyImage(xi); /* frees data */
	if (!opaque) {
		int stride = (w + 7) / 8;
		char *bits = (char *)calloc((size_t)stride * h, 1);
		if (bits) {
			for (y = 0; y < h; y++)
				for (xx = 0; xx < w; xx++)
					if (rgba[4 * ((size_t)y * w + xx) + 3]) bits[y * stride + xx / 8] |= (char)(1 << (xx & 7));
			x->mask = XCreateBitmapFromData(a->dpy, a->win, bits, (unsigned)w, (unsigned)h);
			free(bits);
		}
	}
	x->w = w;
	x->h = h;
	return 0;
}

xasset *xa_find(e26_app *a, const char *name)
{
	int i;
	cache_init();
	if ((i = lookup(a, name)) < 0) return NULL;
	alru_use(&L, i);
	return a->asset[i].pm ? &a->asset[i] : NULL;
}

xasset *xa_get(e26_app *a, const char *name)
{
	int i, through;
	char path[1200], err[300], base[SCR_ASSET];
	const char *at;
	e26_image im;
	xasset *x;
	cache_init();
	if ((i = lookup(a, name)) >= 0) {
		alru_use(&L, i);
		return a->asset[i].pm ? &a->asset[i] : NULL;
	}
	at = strstr(name, "@ui");
	through = at && !at[3];
	snprintf(base, sizeof base, "%.*s", through ? (int)(at - name) : (int)strlen(name), name);
	snprintf(path, sizeof path, "%s/%s.ppm", a->store, base);
	if (e26_image_read_pnm(path, &im, err, sizeof err)) {
		snprintf(path, sizeof path, "%s/%s.pam", a->store, base);
		if (e26_image_read_pnm(path, &im, err, sizeof err)) {
			fprintf(stderr, "d32library: asset %s missing in the store (%s)\n", name, err);
			if ((i = slot_for(a, 0)) >= 0) {          /* remembered as missing, so it is not read (or reported) again */
				memset(&a->asset[i], 0, sizeof a->asset[i]);
				snprintf(a->asset[i].name, sizeof a->asset[i].name, "%s", name);
				alru_fill(&L, i, 0);
			}
			return NULL;
		}
	}
	if (through) through_ui_palette(a, &im);
	if ((i = slot_for(a, cost(im.w, im.h, 1, !e26_image_opaque(&im)))) < 0) { e26_image_free(&im); return NULL; }
	x = &a->asset[i];
	memset(x, 0, sizeof(*x));
	snprintf(x->name, sizeof x->name, "%s", name);
	if (upload(a, x, im.rgba, im.w, im.h)) { e26_image_free(&im); memset(x, 0, sizeof *x); return NULL; }
	x->rgba = im.rgba; /* kept for sampling */
	alru_fill(&L, i, cost(x->w, x->h, 1, x->mask != None));
	if (a->verbose) fprintf(stderr, "d32library: asset %s %dx%d%s\n", name, x->w, x->h, x->mask ? " (masked)" : "");
	return x;
}

xasset *xa_put_rgba(e26_app *a, const char *name, const unsigned char *rgba, int w, int h)
{
	int i, opaque = 1;
	size_t k;
	xasset *x;
	if (w < 1 || h < 1 || !rgba) return NULL;
	cache_init();
	for (k = 0; k < (size_t)w * h && opaque; k++)
		if (rgba[4 * k + 3] != 255) opaque = 0;
	if ((i = lookup(a, name)) >= 0) release(a, i);
	if ((i = slot_for(a, cost(w, h, 0, !opaque))) < 0) return NULL;
	x = &a->asset[i];
	memset(x, 0, sizeof *x);
	snprintf(x->name, sizeof x->name, "%s", name);
	if (upload(a, x, rgba, w, h)) { memset(x, 0, sizeof *x); return NULL; }
	alru_fill(&L, i, cost(w, h, 0, x->mask != None));
	return x;
}

void xa_forget(e26_app *a, const char *name)
{
	int i;
	cache_init();
	if ((i = lookup(a, name)) >= 0) release(a, i);
}

void xa_forget_prefix(e26_app *a, const char *prefix)
{
	int i;
	size_t n = strlen(prefix);
	cache_init();
	for (i = 0; i < a->nasset; i++)
		if (S[i].live && !strncmp(a->asset[i].name, prefix, n)) release(a, i);
}
