/* gfx.c: the drawing primitives (gfx.h). At k = 1 each is exactly the X request build 2's draw.c made, in the same
 * order, so the frame is byte-identical. At k > 1 (SHARP mode, W1-SHARP, item 63) each draws its logical shape at
 * full resolution on the 640k x 480k back buffer: coordinates times k, text from k-times faces placed in its 1x
 * cells, the 1995 bitmaps doubled nearest, photos resampled smoothly. Nothing outside this file and present.c makes
 * an X drawing request. */
#include "library_app.h"
#include <X11/Xutil.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void gfx_init(gfx_ctx *g, e26_app *a, Drawable d, int k)
{
	g->a = a;
	g->d = d;
	g->k = k > 0 ? k : 1;
}

/* ---- SHARP's private GCs: uploads go through their own GC, never the frame's (which may hold a clip) */

static GC up_gc, bit_gc;      /* the screen's depth; depth 1 */

static GC upload_gc(e26_app *a)
{
	XGCValues v;
	if (!up_gc) {
		up_gc = XCreateGC(a->dpy, a->win, 0, &v);
		XSetGraphicsExposures(a->dpy, up_gc, False);
	}
	return up_gc;
}

static GC bitmap_gc(e26_app *a, Pixmap bm)
{
	XGCValues v;
	if (!bit_gc) {
		bit_gc = XCreateGC(a->dpy, bm, 0, &v);
		XSetGraphicsExposures(a->dpy, bit_gc, False);
	}
	return bit_gc;
}

/* an XImage of w x h at the screen's depth with its own buffer, or NULL */
static XImage *image_new(e26_app *a, int w, int h)
{
	XImage *im = XCreateImage(a->dpy, a->vis, (unsigned)a->depth, ZPixmap, 0, NULL, (unsigned)w, (unsigned)h, 32, 0);
	if (!im) return NULL;
	if (!(im->data = (char *)calloc((size_t)im->bytes_per_line, (size_t)h))) { XDestroyImage(im); return NULL; }
	return im;
}

/* a 1-bit mask k times larger (each mask pixel a k x k block); None when it cannot be made */
static Pixmap mask_k(e26_app *a, Pixmap mask, int w, int h, int k)
{
	XImage *m = XGetImage(a->dpy, mask, 0, 0, (unsigned)w, (unsigned)h, 1, XYPixmap);
	int W = w * k, H = h * k, stride = (W + 7) / 8, x, y, j, i;
	char *bits;
	Pixmap out = None;
	if (!m) return None;
	bits = (char *)calloc((size_t)stride, (size_t)H);
	if (bits) {
		for (y = 0; y < h; y++)
			for (x = 0; x < w; x++)
				if (XGetPixel(m, x, y))
					for (j = 0; j < k; j++)
						for (i = 0; i < k; i++) {
							int X = x * k + i, Y = y * k + j;
							bits[(size_t)Y * (size_t)stride + (size_t)(X / 8)] |= (char)(1 << (X & 7));
						}
		out = XCreateBitmapFromData(a->dpy, a->win, bits, (unsigned)W, (unsigned)H);
		free(bits);
	}
	XDestroyImage(m);
	return out;
}

/* ---- the store bitmaps at k: doubled nearest (interface art) or resampled smoothly (photos), cached per pixmap */

typedef struct {
	Pixmap src;     /* the asset's 1x pixmap */
	int k, smooth;
	Pixmap pm, mask;
	size_t bytes;
} kasset;
static kasset *kas;
static int nkas, akas;
static size_t kas_bytes;
#define KAS_BUDGET ((size_t)96 << 20)

static void kas_drop(e26_app *a, int i)
{
	if (kas[i].pm) XFreePixmap(a->dpy, kas[i].pm);
	if (kas[i].mask) XFreePixmap(a->dpy, kas[i].mask);
	kas_bytes -= kas[i].bytes;
	kas[i] = kas[--nkas];
}

/* one channel of a TrueColor pixel as 0-255 */
static unsigned chan_of(unsigned long pixel, unsigned long mask)
{
	int s = 0, b = 0;
	unsigned long m = mask, v;
	if (!m) return 0;
	while (!(m & 1)) { m >>= 1; s++; }
	while (m & 1) { m >>= 1; b++; }
	v = (pixel & mask) >> s;
	if (b >= 8) return (unsigned)(v >> (b - 8));
	return (unsigned)((v * 255 + ((1ul << b) - 1) / 2) / ((1ul << b) - 1));
}

/* the asset's pixels as R, G, B, A bytes (its own copy when xassets.c kept one, else read back from its pixmap) */
static unsigned char *asset_rgba(e26_app *a, const xasset *x, int *own)
{
	XImage *im;
	unsigned char *p;
	int xx, y;
	*own = 0;
	if (x->rgba) return x->rgba;
	if (!(im = XGetImage(a->dpy, x->pm, 0, 0, (unsigned)x->w, (unsigned)x->h, AllPlanes, ZPixmap))) return NULL;
	p = (unsigned char *)malloc((size_t)x->w * (size_t)x->h * 4);
	if (p) {
		for (y = 0; y < x->h; y++)
			for (xx = 0; xx < x->w; xx++) {
				unsigned long v = XGetPixel(im, xx, y);
				unsigned char *o = p + 4 * ((size_t)y * (size_t)x->w + (size_t)xx);
				o[0] = (unsigned char)chan_of(v, a->rmask);
				o[1] = (unsigned char)chan_of(v, a->gmask);
				o[2] = (unsigned char)chan_of(v, a->bmask);
				o[3] = 255;
			}
		*own = 1;
	}
	XDestroyImage(im);
	return p;
}

/* RGBA bytes to a server pixmap of the screen's depth (through xa_pixel, exactly as xassets.c uploads) */
static Pixmap rgba_pixmap(e26_app *a, const unsigned char *px, size_t stride, int w, int h)
{
	XImage *im = image_new(a, w, h);
	Pixmap pm;
	int x, y;
	if (!im) return None;
	for (y = 0; y < h; y++)
		for (x = 0; x < w; x++) {
			const unsigned char *p = px + (size_t)y * stride + 4 * (size_t)x;
			XPutPixel(im, x, y, xa_pixel(a, ((long)p[0] << 16) | ((long)p[1] << 8) | p[2]));
		}
	pm = XCreatePixmap(a->dpy, a->win, (unsigned)w, (unsigned)h, (unsigned)a->depth);
	XPutImage(a->dpy, pm, upload_gc(a), im, 0, 0, 0, 0, (unsigned)w, (unsigned)h);
	XDestroyImage(im);
	return pm;
}

/* the asset's own server pixels, each a k x k block */
static Pixmap doubled_pixmap(e26_app *a, const xasset *x, int k)
{
	XImage *src = XGetImage(a->dpy, x->pm, 0, 0, (unsigned)x->w, (unsigned)x->h, AllPlanes, ZPixmap), *dst;
	Pixmap pm = None;
	int W = x->w * k, H = x->h * k, xx, y, i, j;
	if (!src) return None;
	if ((dst = image_new(a, W, H))) {
		int bpp = src->bits_per_pixel / 8;
		if (src->bits_per_pixel == dst->bits_per_pixel && src->bits_per_pixel % 8 == 0 && bpp >= 1 && bpp <= 4)
			scale_pixels((const unsigned char *)src->data, (size_t)src->bytes_per_line, (unsigned char *)dst->data,
			             (size_t)dst->bytes_per_line, x->w, x->h, bpp, k);
		else
			for (y = 0; y < x->h; y++)
				for (xx = 0; xx < x->w; xx++) {
					unsigned long v = XGetPixel(src, xx, y);
					for (j = 0; j < k; j++)
						for (i = 0; i < k; i++) XPutPixel(dst, xx * k + i, y * k + j, v);
				}
		pm = XCreatePixmap(a->dpy, a->win, (unsigned)W, (unsigned)H, (unsigned)a->depth);
		XPutImage(a->dpy, pm, upload_gc(a), dst, 0, 0, 0, 0, (unsigned)W, (unsigned)H);
		XDestroyImage(dst);
	}
	XDestroyImage(src);
	return pm;
}

static Pixmap smooth_pixmap(e26_app *a, const xasset *x, int k)
{
	int own, W = x->w * k, H = x->h * k;
	unsigned char *rgba = asset_rgba(a, x, &own), *big;
	Pixmap pm = None;
	if (!rgba) return None;
	if ((big = (unsigned char *)malloc((size_t)W * (size_t)H * 4))) {
		if (!scale_smooth(rgba, (size_t)x->w * 4, x->w, x->h, big, (size_t)W * 4, W, H))
			pm = rgba_pixmap(a, big, (size_t)W * 4, W, H);
		free(big);
	}
	if (own) free(rgba);
	return pm;
}

static const kasset *asset_at_k(gfx_ctx *g, const xasset *x, int smooth)
{
	e26_app *a = g->a;
	kasset e;
	int i;
	for (i = 0; i < nkas; i++)
		if (kas[i].src == x->pm && kas[i].k == g->k && kas[i].smooth == smooth) return &kas[i];
	memset(&e, 0, sizeof e);
	e.src = x->pm;
	e.k = g->k;
	e.smooth = smooth;
	e.bytes = (size_t)x->w * (size_t)x->h * (size_t)g->k * (size_t)g->k * 4;
	while (nkas && kas_bytes + e.bytes > KAS_BUDGET) kas_drop(a, 0);
	e.pm = smooth ? smooth_pixmap(a, x, g->k) : doubled_pixmap(a, x, g->k);
	if (!e.pm) return NULL;
	if (x->mask) e.mask = mask_k(a, x->mask, x->w, x->h, g->k);
	if (nkas == akas) {
		int n = akas ? 2 * akas : 64;
		kasset *m = (kasset *)realloc(kas, (size_t)n * sizeof *m);
		if (!m) {
			XFreePixmap(a->dpy, e.pm);
			if (e.mask) XFreePixmap(a->dpy, e.mask);
			return NULL;
		}
		kas = m;
		akas = n;
	}
	kas[nkas] = e;
	kas_bytes += e.bytes;
	if (a->verbose) fprintf(stderr, "d32library: sharp: %s at x%d (%s)\n", x->name, g->k, smooth ? "smooth" : "doubled nearest");
	return &kas[nkas++];
}

/* ---- the glyphs of a face with no outline, doubled: a 16 x 16 grid of its 1x glyphs, each cell enlarged k times,
 * kept as a 1-bit pixmap and drawn as a stipple (so the frame's clip still applies) */

typedef struct {
	Font fid1;
	int k;
	Pixmap grid;
	int cw, ch, asc, ox0;   /* the 1x cell, the baseline in it, the pen's place in it */
} gstrip;
static gstrip strips[F_NFONTS];

static const gstrip *strip_at_k(gfx_ctx *g, int font)
{
	e26_app *a = g->a;
	XFontStruct *f = a->font[font];
	gstrip *s = &strips[font];
	Pixmap one;
	XImage *im, *big;
	int c, W, H, x, y, i, j, k = g->k;
	if (s->grid && s->fid1 == f->fid && s->k == k) return s;
	if (s->grid) XFreePixmap(a->dpy, s->grid);
	memset(s, 0, sizeof *s);
	s->ox0 = f->min_bounds.lbearing < 0 ? -f->min_bounds.lbearing : 0;
	s->cw = (f->max_bounds.rbearing > f->max_bounds.width ? f->max_bounds.rbearing : f->max_bounds.width) + s->ox0 + 1;
	s->asc = f->max_bounds.ascent > f->ascent ? f->max_bounds.ascent : f->ascent;
	s->ch = s->asc + (f->max_bounds.descent > f->descent ? f->max_bounds.descent : f->descent);
	if (s->cw < 1 || s->ch < 1 || 16 * s->cw * k > 32767 || 16 * s->ch * k > 32767) return NULL;
	W = 16 * s->cw;
	H = 16 * s->ch;
	one = XCreatePixmap(a->dpy, a->win, (unsigned)W, (unsigned)H, 1);
	XSetForeground(a->dpy, bitmap_gc(a, one), 0);
	XFillRectangle(a->dpy, one, bit_gc, 0, 0, (unsigned)W, (unsigned)H);
	XSetForeground(a->dpy, bit_gc, 1);
	XSetFont(a->dpy, bit_gc, f->fid);
	for (c = 1; c < 256; c++) {
		char ch = (char)c;
		XDrawString(a->dpy, one, bit_gc, (c % 16) * s->cw + s->ox0, (c / 16) * s->ch + s->asc, &ch, 1);
	}
	im = XGetImage(a->dpy, one, 0, 0, (unsigned)W, (unsigned)H, 1, XYPixmap);
	XFreePixmap(a->dpy, one);
	if (!im) return NULL;
	big = XCreateImage(a->dpy, a->vis, 1, XYBitmap, 0, NULL, (unsigned)(W * k), (unsigned)(H * k), 8, 0);
	if (big && !(big->data = (char *)calloc((size_t)big->bytes_per_line, (size_t)(H * k)))) { XDestroyImage(big); big = NULL; }
	if (!big) { XDestroyImage(im); return NULL; }
	for (y = 0; y < H; y++)
		for (x = 0; x < W; x++)
			if (XGetPixel(im, x, y))
				for (j = 0; j < k; j++)
					for (i = 0; i < k; i++) XPutPixel(big, x * k + i, y * k + j, 1);
	XDestroyImage(im);
	s->grid = XCreatePixmap(a->dpy, a->win, (unsigned)(W * k), (unsigned)(H * k), 1);
	XSetForeground(a->dpy, bit_gc, 1);
	XSetBackground(a->dpy, bit_gc, 0);
	XPutImage(a->dpy, s->grid, bit_gc, big, 0, 0, 0, 0, (unsigned)(W * k), (unsigned)(H * k));
	XDestroyImage(big);
	s->fid1 = f->fid;
	s->k = k;
	if (a->verbose) fprintf(stderr, "d32library: sharp: font slot %d doubled from its 1x glyphs (no outline face)\n", font);
	return s;
}

void gfx_k_changed(e26_app *a, int k)
{
	int i;
	for (i = nkas - 1; i >= 0; i--)
		if (kas[i].k != k) kas_drop(a, i);
	for (i = 0; i < F_NFONTS; i++)
		if (strips[i].grid && strips[i].k != k) { XFreePixmap(a->dpy, strips[i].grid); memset(&strips[i], 0, sizeof strips[i]); }
}

void gfx_forget_pixmap(e26_app *a, Pixmap pm)
{
	int i;
	for (i = nkas - 1; i >= 0; i--)
		if (kas[i].src == pm) kas_drop(a, i);
}

/* ---- shapes */

void gfx_fill(gfx_ctx *g, int x, int y, int w, int h, long rgb)
{
	e26_app *a = g->a;
	int k = g->k;
	XSetForeground(a->dpy, a->gc, xa_pixel(a, rgb));
	if (k == 1) { XFillRectangle(a->dpy, g->d, a->gc, x, y, (unsigned)w, (unsigned)h); return; }
	if (w > 0 && h > 0) XFillRectangle(a->dpy, g->d, a->gc, x * k, y * k, (unsigned)(w * k), (unsigned)(h * k));
}

void gfx_line(gfx_ctx *g, int x1, int y1, int x2, int y2, long rgb)
{
	e26_app *a = g->a;
	int k = g->k;
	XSetForeground(a->dpy, a->gc, xa_pixel(a, rgb));
	if (k == 1) { XDrawLine(a->dpy, g->d, a->gc, x1, y1, x2, y2); return; }
	if (y1 == y2 || x1 == x2) { /* the logical pixels it covers, both ends included (as X's thin line) */
		int lx = x1 < x2 ? x1 : x2, ly = y1 < y2 ? y1 : y2;
		int w = (x1 < x2 ? x2 - x1 : x1 - x2) + 1, h = (y1 < y2 ? y2 - y1 : y1 - y2) + 1;
		XFillRectangle(a->dpy, g->d, a->gc, lx * k, ly * k, (unsigned)(w * k), (unsigned)(h * k));
		return;
	}
	/* a sloped line at device resolution: k px wide between its end pixels' centres */
	XSetLineAttributes(a->dpy, a->gc, (unsigned)k, LineSolid, CapProjecting, JoinMiter);
	XDrawLine(a->dpy, g->d, a->gc, x1 * k + k / 2, y1 * k + k / 2, x2 * k + k / 2, y2 * k + k / 2);
	XSetLineAttributes(a->dpy, a->gc, 0, LineSolid, CapButt, JoinMiter);
}

void gfx_poly(gfx_ctx *g, const int *xy, int np, long rgb)
{
	e26_app *a = g->a;
	XPoint p[16];
	int i, k = g->k;
	if (np > 16) np = 16;
	for (i = 0; i < np; i++) { p[i].x = (short)(xy[2 * i] * k); p[i].y = (short)(xy[2 * i + 1] * k); }
	XSetForeground(a->dpy, a->gc, xa_pixel(a, rgb));
	XFillPolygon(a->dpy, g->d, a->gc, p, np, Convex, CoordModeOrigin);
}

void gfx_frame2(gfx_ctx *g, int x, int y, int w, int h, long tl, long br)
{
	if (tl < 0 || br < 0) return;
	gfx_line(g, x, y, x + w - 1, y, tl);
	gfx_line(g, x, y, x, y + h - 1, tl);
	gfx_line(g, x, y + h - 1, x + w - 1, y + h - 1, br);
	gfx_line(g, x + w - 1, y, x + w - 1, y + h - 1, br);
}

/* ---- bitmaps */

static void blit_k(gfx_ctx *g, const xasset *x, int sx, int sy, int w, int h, int dx, int dy, int masked, int smooth)
{
	e26_app *a = g->a;
	int k = g->k;
	const kasset *e = asset_at_k(g, x, smooth);
	if (!e) { /* no memory for the enlarged copy: the 1x pixels at their place (never a crash) */
		XCopyArea(a->dpy, x->pm, g->d, a->gc, sx, sy, (unsigned)w, (unsigned)h, dx * k, dy * k);
		return;
	}
	if (masked && e->mask) {
		XSetClipMask(a->dpy, a->gc, e->mask);
		XSetClipOrigin(a->dpy, a->gc, (dx - sx) * k, (dy - sy) * k);
	}
	XCopyArea(a->dpy, e->pm, g->d, a->gc, sx * k, sy * k, (unsigned)(w * k), (unsigned)(h * k), dx * k, dy * k);
	if (masked && x->mask) XSetClipMask(a->dpy, a->gc, None); /* as at 1x: a masked blit ends with no clip */
}

void gfx_blit(gfx_ctx *g, const xasset *x, int sx, int sy, int w, int h, int dx, int dy)
{
	e26_app *a = g->a;
	if (g->k > 1) { blit_k(g, x, sx, sy, w, h, dx, dy, 1, 0); return; }
	if (x->mask) {
		XSetClipMask(a->dpy, a->gc, x->mask);
		XSetClipOrigin(a->dpy, a->gc, dx - sx, dy - sy);
	}
	XCopyArea(a->dpy, x->pm, g->d, a->gc, sx, sy, (unsigned)w, (unsigned)h, dx, dy);
	if (x->mask) XSetClipMask(a->dpy, a->gc, None);
}

void gfx_copy(gfx_ctx *g, const xasset *x, int sx, int sy, int w, int h, int dx, int dy)
{
	e26_app *a = g->a;
	if (g->k > 1) { blit_k(g, x, sx, sy, w, h, dx, dy, 0, 0); return; }
	XCopyArea(a->dpy, x->pm, g->d, a->gc, sx, sy, (unsigned)w, (unsigned)h, dx, dy);
}

void gfx_photo(gfx_ctx *g, const xasset *x, int sx, int sy, int w, int h, int dx, int dy)
{
	if (g->k > 1) blit_k(g, x, sx, sy, w, h, dx, dy, 1, 1);
	else gfx_blit(g, x, sx, sy, w, h, dx, dy);
}

/* ---- pictures from pixels */

void gfx_device_size(const gfx_ctx *g, int w, int h, int *dw, int *dh)
{
	*dw = w * g->k;
	*dh = h * g->k;
}

int gfx_picture_make(gfx_ctx *g, const unsigned char *rgba, int pw, int ph, size_t stride, int w, int h, gfx_pic *out)
{
	e26_app *a = g->a;
	int dw = w * g->k, dh = h * g->k;
	unsigned char *buf = NULL;
	memset(out, 0, sizeof *out);
	if (!rgba || pw < 1 || ph < 1 || w < 1 || h < 1) return -1;
	if (pw != dw || ph != dh) {
		if (!(buf = (unsigned char *)malloc((size_t)dw * (size_t)dh * 4))) return -1;
		if (scale_smooth(rgba, stride, pw, ph, buf, (size_t)dw * 4, dw, dh)) { free(buf); return -1; }
		rgba = buf;
		stride = (size_t)dw * 4;
	}
	out->pm = rgba_pixmap(a, rgba, stride, dw, dh);
	free(buf);
	if (!out->pm) return -1;
	out->w = w;
	out->h = h;
	out->k = g->k;
	return 0;
}

int gfx_picture_draw(gfx_ctx *g, const gfx_pic *p, int x, int y)
{
	e26_app *a = g->a;
	if (!p->pm || p->k != g->k) return -1;
	XCopyArea(a->dpy, p->pm, g->d, a->gc, 0, 0, (unsigned)(p->w * p->k), (unsigned)(p->h * p->k), x * g->k, y * g->k);
	return 0;
}

void gfx_picture_free(gfx_ctx *g, gfx_pic *p)
{
	if (p->pm) XFreePixmap(g->a->dpy, p->pm);
	memset(p, 0, sizeof *p);
}

/* ---- clipping */

void gfx_clip(gfx_ctx *g, int x, int y, int w, int h)
{
	XRectangle r;
	int k = g->k;
	r.x = (short)(x * k); r.y = (short)(y * k); r.width = (unsigned short)(w * k); r.height = (unsigned short)(h * k);
	XSetClipRectangles(g->a->dpy, g->a->gc, 0, 0, &r, 1, Unsorted);
}

void gfx_unclip(gfx_ctx *g) { XSetClipMask(g->a->dpy, g->a->gc, None); }

/* ---- text: the one entry point */

static void glyph_of(XFontStruct *f, const char *c, scale_glyph *o)
{
	int dir, asc, desc;
	XCharStruct cs;
	XTextExtents(f, c, 1, &dir, &asc, &desc, &cs);
	o->w = cs.width;
	o->lb = cs.lbearing;
	o->rb = cs.rbearing;
}

/* from the k-times face: every glyph where its 1x glyph is (scale_glyph_x), the baseline at k y */
static void text_outline(gfx_ctx *g, XFontStruct *f1, XFontStruct *fk, int x, int y, long rgb, const char *s, int n)
{
	e26_app *a = g->a;
	XTextItem it[64];
	int k = g->k, i = 0, pen1 = 0;
	XSetFont(a->dpy, a->gc, fk->fid);
	XSetForeground(a->dpy, a->gc, xa_pixel(a, rgb));
	while (i < n) {
		int m = 0, ox = 0, p = 0;
		for (; i < n && m < 64; i++, m++) {
			scale_glyph g1, gk;
			int gx;
			glyph_of(f1, s + i, &g1);
			glyph_of(fk, s + i, &gk);
			gx = scale_glyph_x(k, pen1, &g1, &gk);
			if (m == 0) ox = p = gx;
			it[m].chars = (char *)(s + i);
			it[m].nchars = 1;
			it[m].delta = gx - p;
			it[m].font = None;
			p = gx + gk.w;
			pen1 += g1.w;
		}
		XDrawText(a->dpy, g->d, a->gc, k * x + ox, k * y, it, m);
	}
}

/* a face with no outline: its 1x glyphs doubled, each at k times its 1x pen (the 1x string, enlarged exactly) */
static void text_doubled(gfx_ctx *g, XFontStruct *f1, const gstrip *st, int x, int y, long rgb, const char *s, int n)
{
	e26_app *a = g->a;
	int k = g->k, i, pen1 = 0;
	XSetForeground(a->dpy, a->gc, xa_pixel(a, rgb));
	XSetStipple(a->dpy, a->gc, st->grid);
	XSetFillStyle(a->dpy, a->gc, FillStippled);
	for (i = 0; i < n; i++) {
		int c = (unsigned char)s[i], w1 = XTextWidth(f1, s + i, 1);
		int ox = k * (x + pen1), oy = k * y;                                               /* the glyph's origin */
		int gx = k * ((c % 16) * st->cw + st->ox0), gy = k * ((c / 16) * st->ch + st->asc); /* and in the grid */
		if (c) {
			XSetTSOrigin(a->dpy, a->gc, ox - gx, oy - gy);
			XFillRectangle(a->dpy, g->d, a->gc, ox - k * st->ox0, oy - k * st->asc, (unsigned)(k * st->cw), (unsigned)(k * st->ch));
		}
		pen1 += w1;
	}
	XSetFillStyle(a->dpy, a->gc, FillSolid);
}

void gfx_text(gfx_ctx *g, int x, int y, int font, long rgb, const char *s, int n)
{
	e26_app *a = g->a;
	XFontStruct *fk;
	const gstrip *st;
	if (g->k == 1) {
		XSetFont(a->dpy, a->gc, a->font[font]->fid);
		XSetForeground(a->dpy, a->gc, xa_pixel(a, rgb));
		XDrawString(a->dpy, g->d, a->gc, x, y, s, n < 0 ? (int)strlen(s) : n);
		return;
	}
	if (n < 0) n = (int)strlen(s);
	if (n <= 0) return;
	if ((fk = fonts_at_k(a, font, g->k))) text_outline(g, a->font[font], fk, x, y, rgb, s, n);
	else if ((st = strip_at_k(g, font))) text_doubled(g, a->font[font], st, x, y, rgb, s, n);
	else { /* nothing at k could be made: the 1x glyphs at their places (small, never lost) */
		XSetFont(a->dpy, a->gc, a->font[font]->fid);
		XSetForeground(a->dpy, a->gc, xa_pixel(a, rgb));
		XDrawString(a->dpy, g->d, a->gc, x * g->k, y * g->k, s, n);
	}
}

int gfx_text_sharp(gfx_ctx *g, int font)
{
	return g->k > 1 && fonts_at_k(g->a, font, g->k) != NULL;
}

int gfx_text_width(gfx_ctx *g, int font, const char *s, int n)
{
	return XTextWidth(g->a->font[font], s, n < 0 ? (int)strlen(s) : n);
}

int gfx_ascent(gfx_ctx *g, int font) { return g->a->font[font]->ascent; }
int gfx_descent(gfx_ctx *g, int font) { return g->a->font[font]->descent; }
int gfx_font_height(gfx_ctx *g, int font) { return g->a->font[font]->ascent + g->a->font[font]->descent; }

int gfx_spaced(gfx_ctx *g, int x, int y, int font, long rgb, const char *s, int tracking, int upper, int draw)
{
	int i, n = (int)strlen(s), pos10 = 0;
	char ch;
	for (i = 0; i < n; i++) {
		ch = upper && s[i] >= 'a' && s[i] <= 'z' ? (char)(s[i] - 'a' + 'A') : s[i];
		if (draw) gfx_text(g, x + (pos10 + 5) / 10, y, font, rgb, &ch, 1);
		pos10 += 10 * gfx_text_width(g, font, &ch, 1) + (i + 1 < n ? tracking : 0);
	}
	return (pos10 + 5) / 10;
}

int gfx_seg_width(gfx_ctx *g, int font, const char *s, int n)
{
	int i, w = 0, start = 0, f = font;
	for (i = 0; i <= n; i++)
		if (i == n || s[i] == GFX_KEY_ON || s[i] == GFX_KEY_OFF) {
			w += gfx_text_width(g, f, s + start, i - start);
			if (i < n) f = s[i] == GFX_KEY_ON ? F_SMALL : font;
			start = i + 1;
		}
	return w;
}

void gfx_seg_text(gfx_ctx *g, int x, int y, int font, long rgb, const char *s, int n)
{
	int i, start = 0, f = font;
	for (i = 0; i <= n; i++)
		if (i == n || s[i] == GFX_KEY_ON || s[i] == GFX_KEY_OFF) {
			if (i > start) { gfx_text(g, x, y, f, rgb, s + start, i - start); x += gfx_text_width(g, f, s + start, i - start); }
			if (i < n) f = s[i] == GFX_KEY_ON ? F_SMALL : font;
			start = i + 1;
		}
}

void gfx_wrap_lines(gfx_ctx *g, int x, int by, int w, int font, long rgb, const char *s, int align, int lh)
{
	int len = (int)strlen(s), start = 0;
	while (start < len) {
		int end = start, last = -1, lw;
		while (end < len && s[end] != '\n') {
			if (s[end] == ' ') last = end;
			if (gfx_seg_width(g, font, s + start, end - start + 1) > w && last > start) { end = last; break; }
			end++;
		}
		lw = gfx_seg_width(g, font, s + start, end - start);
		gfx_seg_text(g, align == AL_CENTRE ? x + (w - lw) / 2 : align == AL_RIGHT ? x + w - lw : x, by, font, rgb, s + start, end - start);
		by += lh;
		start = end;
		while (start < len && (s[start] == ' ' || s[start] == '\n')) start++;
	}
}

void gfx_wrap_text(gfx_ctx *g, int x, int y, int w, int font, long rgb, const char *s, int align)
{
	gfx_wrap_lines(g, x, y + gfx_ascent(g, font), w, font, rgb, s, align, gfx_font_height(g, font) + 1);
}

void gfx_marker(gfx_ctx *g, int x, int y, long rgb)
{
	int p[6];
	p[0] = x; p[1] = y;
	p[2] = x + 7; p[3] = y;
	p[4] = x + 3; p[5] = y + 4;
	gfx_poly(g, p, 3, rgb);
}

void gfx_arrow_right(gfx_ctx *g, int x, int y, long rgb)
{
	int p[6];
	p[0] = x; p[1] = y - 4;
	p[2] = x; p[3] = y + 4;
	p[4] = x + 4; p[5] = y;
	gfx_poly(g, p, 3, rgb);
}
