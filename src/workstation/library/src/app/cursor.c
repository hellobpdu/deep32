/* cursor.c: the Library's pointer shapes (cursor.h). The images come from the asset store (the importer's ui/cursor and
 * e97s/ui/cursor PAMs: each RT_CURSOR resource's DIB, its AND mask as alpha); the hotspots are the two words that
 * precede each DIB in the resource, which the importer drops, read once from the period program and the period resource library on the
 * build VM (numbers only, below). A cursor is made through RENDER (an ARGB picture, XRenderCreateCursor), enlarged
 * k times nearest when the frame is (update 1's maximise; a->sc.k), or, on a server without RENDER 0.5, as a two-colour
 * pixmap cursor (the pictures are black and white, so nothing is lost). */
#include "library_app.h"
#include "cursor.h"
#include "../common/image.h"
#include <X11/Xutil.h>
#include <X11/extensions/Xrender.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* [d] hotspots: the period program RT_CURSOR 4 (group HANDCURSOR; CURHAND, 7, is the same picture) (6,0), 9 (ZOOMCURSOR;
 * 1, group 102, the same picture) (7,8), 8 (SPLITCURSOR) (15,15); the period resource library RT_CURSOR 1 and 2 (group 540) (6,0) and
 * (3,0), 3 and 4 (group 542) (7,8) and (5,5), 6-14 (groups 14880-14888) (15,15). 97's 1, 3 and 5 are pixel for pixel
 * 95's 4, 9 and 8 (the store's pixel hashes), so the 95 pictures serve both. The pan arrows' directions were read
 * from where each picture's ink lies round its hotspot (6 all four ways, 7 W, 8 E, 9 N, 10 S, 11 NW, 12 NE, 13 SW,
 * 14 SE). */
static const cursor_def defs[CUR_N] = {
	{ "arrow", NULL, 0, 0 },
	{ "hand", "ui/cursor/4", 6, 0 },
	{ "magnify", "ui/cursor/9", 7, 8 },
	{ "split", "ui/cursor/8", 15, 15 },
	{ "pan", "e97s/ui/cursor/6", 15, 15 },
	{ "pan-w", "e97s/ui/cursor/7", 15, 15 },
	{ "pan-e", "e97s/ui/cursor/8", 15, 15 },
	{ "pan-n", "e97s/ui/cursor/9", 15, 15 },
	{ "pan-s", "e97s/ui/cursor/10", 15, 15 },
	{ "pan-nw", "e97s/ui/cursor/11", 15, 15 },
	{ "pan-ne", "e97s/ui/cursor/12", 15, 15 },
	{ "pan-sw", "e97s/ui/cursor/13", 15, 15 },
	{ "pan-se", "e97s/ui/cursor/14", 15, 15 },
	{ "hand-small", "e97s/ui/cursor/2", 3, 0 },
	{ "magnify-small", "e97s/ui/cursor/4", 5, 5 },
};

static struct {
	e26_image im;         /* the store's picture, read once */
	int tried, have;
	Cursor c;             /* made at factor k_made */
} cur[CUR_N];
static int k_made, installed, shown = -1, hold = -1, last_x = -1, last_y = -1;
static cursor_probe_fn *probes;
static int nprobes;

const cursor_def *cursor_def_of(int id) { return id >= 0 && id < CUR_N ? &defs[id] : NULL; }

int cursor_by_name(const char *name)
{
	int i;
	for (i = 0; name && i < CUR_N; i++)
		if (!strcmp(defs[i].name, name)) return i;
	return -1;
}

void cursor_probe(int kind, cursor_probe_fn fn)
{
	if (kind < 0) return;
	if (kind >= nprobes) {
		int n = kind + 16, i;
		cursor_probe_fn *np = (cursor_probe_fn *)realloc(probes, (size_t)n * sizeof(cursor_probe_fn));
		if (!np) return;
		for (i = nprobes; i < n; i++) np[i] = NULL;
		probes = np;
		nprobes = n;
	}
	probes[kind] = fn;
}

void cursor_hold(int id) { hold = id >= 0 && id < CUR_N ? id : -1; }
int cursor_shown(void) { return shown; }

int cursor_scale_rgba(const unsigned char *src, int w, int h, int k, unsigned char *dst)
{
	if (!src || !dst || w < 1 || h < 1 || k < 1) return -1;
	scale_pixels(src, (size_t)w * 4, dst, (size_t)w * k * 4, w, h, 4, k);
	return 0;
}

void cursor_hot_at(const cursor_def *d, int k, int *hx, int *hy)
{
	if (k < 1) k = 1;
	*hx = d->hx * k + k / 2;
	*hy = d->hy * k + k / 2;
}

/* The article's jumps until the package that lays out the article registers its own hit test: a run drawn in the
 * jump colours (JumpColor, or the list jumps' entry 19) under the pointer. Same geometry as kind_article.c draws. */
static int article_jump(e26_app *a, scr_screen *s, int i, int x, int y)
{
	const scr_control *c = &s->c[i];
	int cx = c->x + (c->absolute ? 0 : s->ox), cy = c->y + (c->absolute ? 0 : s->oy), k;
	int x0 = cx + (c->text_x >= 0 ? c->text_x : 10), y0 = cy - a->scroll;
	if (!a->have_art || x >= cx + c->w - 16) return -1;
	for (k = 0; k < a->nops; k++) {
		const lay_op *o = &a->ops[k];
		int bx, by, w;
		if ((o->colour != (unsigned long)C_JUMP && o->colour != (unsigned long)C_LISTJUMP) || o->font < 0 || o->font >= F_NFONTS ||
		    !a->font[o->font]) continue;
		bx = x0 + o->x;
		by = y0 + o->y;
		if (y < by - gfx_ascent(&a->g, o->font) || y >= by + gfx_descent(&a->g, o->font) || x < bx) continue;
		w = gfx_text_width(&a->g, o->font, o->text, o->len);
		if (x < bx + w) return CUR_HAND;
	}
	return -1;
}

int cursor_at(e26_app *a, int x, int y)
{
	scr_screen *s = a->cur;
	int h, k, id;
	const char *p;
	if (hold >= 0) return hold;
	if (!s || a->menu) return CUR_ARROW;
	h = hit_control(a, x, y);
	if (h < 0 || s->c[h].hidden) return CUR_ARROW;
	k = s->c[h].kind;
	if (k >= 0 && k < nprobes && probes[k]) { if ((id = probes[k](a, s, h, x, y)) >= 0) return id; }
	else if (k == K_ARTICLE && (id = article_jump(a, s, h, x, y)) >= 0) return id;
	if ((p = scr_prop_get(&s->c[h], "cursor")) && control_enabled(a, s, h) && (id = cursor_by_name(p)) >= 0) return id;
	return CUR_ARROW;
}

/* ---- X */

static int have_render(e26_app *a)
{
	static int known = -1;
	int ev, er, maj = 0, min = 0;
	if (known < 0)
		known = XRenderQueryExtension(a->dpy, &ev, &er) && XRenderQueryVersion(a->dpy, &maj, &min) && (maj > 0 || min >= 5) &&
		        XRenderFindStandardFormat(a->dpy, PictStandardARGB32) != NULL;
	return known;
}

static Cursor make(e26_app *a, int id, int k)
{
	const cursor_def *d = &defs[id];
	const e26_image *im = &cur[id].im;
	int w = im->w * k, h = im->h * k, hx, hy, x, y;
	unsigned int bw = 0, bh = 0;
	unsigned char *px;
	Cursor c = None;
	if (k > 1 && (!XQueryBestCursor(a->dpy, a->win, (unsigned)w, (unsigned)h, &bw, &bh) || (int)bw < w || (int)bh < h))
		return make(a, id, 1); /* the server cannot show it that large */
	if (!(px = (unsigned char *)malloc((size_t)w * h * 4))) return None;
	cursor_scale_rgba(im->rgba, im->w, im->h, k, px);
	cursor_hot_at(d, k, &hx, &hy);
	if (have_render(a)) {
		XRenderPictFormat *fmt = XRenderFindStandardFormat(a->dpy, PictStandardARGB32);
		Pixmap pm = XCreatePixmap(a->dpy, a->win, (unsigned)w, (unsigned)h, 32);
		XImage *xi = XCreateImage(a->dpy, NULL, 32, ZPixmap, 0, NULL, (unsigned)w, (unsigned)h, 32, 0);
		GC gc = XCreateGC(a->dpy, pm, 0, NULL);
		if (xi && (xi->data = (char *)malloc((size_t)xi->bytes_per_line * h))) {
			Picture pic;
			for (y = 0; y < h; y++)
				for (x = 0; x < w; x++) {
					const unsigned char *p = px + 4 * ((size_t)y * w + x);
					/* premultiplied ARGB: the pictures are fully opaque or fully clear */
					XPutPixel(xi, x, y, p[3] ? 0xFF000000UL | ((unsigned long)p[0] << 16) | ((unsigned long)p[1] << 8) | p[2] : 0UL);
				}
			XPutImage(a->dpy, pm, gc, xi, 0, 0, 0, 0, (unsigned)w, (unsigned)h);
			pic = XRenderCreatePicture(a->dpy, pm, fmt, 0, NULL);
			c = XRenderCreateCursor(a->dpy, pic, (unsigned)hx, (unsigned)hy);
			XRenderFreePicture(a->dpy, pic);
		}
		if (xi) XDestroyImage(xi);
		XFreeGC(a->dpy, gc);
		XFreePixmap(a->dpy, pm);
	} else {
		int stride = (w + 7) / 8;
		char *src = (char *)calloc((size_t)stride * h, 1), *mask = (char *)calloc((size_t)stride * h, 1);
		if (src && mask) {
			XColor fg, bg;
			Pixmap ps, pmk;
			for (y = 0; y < h; y++)
				for (x = 0; x < w; x++) {
					const unsigned char *p = px + 4 * ((size_t)y * w + x);
					if (!p[3]) continue;
					mask[y * stride + x / 8] |= (char)(1 << (x & 7));
					if (p[0] + p[1] + p[2] < 384) src[y * stride + x / 8] |= (char)(1 << (x & 7));
				}
			memset(&fg, 0, sizeof fg);
			memset(&bg, 0, sizeof bg);
			bg.red = bg.green = bg.blue = 0xFFFF;
			ps = XCreateBitmapFromData(a->dpy, a->win, src, (unsigned)w, (unsigned)h);
			pmk = XCreateBitmapFromData(a->dpy, a->win, mask, (unsigned)w, (unsigned)h);
			c = XCreatePixmapCursor(a->dpy, ps, pmk, &fg, &bg, (unsigned)hx, (unsigned)hy);
			XFreePixmap(a->dpy, ps);
			XFreePixmap(a->dpy, pmk);
		}
		free(src);
		free(mask);
	}
	free(px);
	return c;
}

/* every cursor at the frame's factor: read from the store the first time, made again only when k changes */
static void make_all(e26_app *a)
{
	int i, k = a->sc.k > 0 ? a->sc.k : 1, n = 0;
	char path[1200], err[300];
	if (k == k_made) return;
	for (i = 1; i < CUR_N; i++) {
		if (!cur[i].tried) {
			cur[i].tried = 1;
			snprintf(path, sizeof path, "%s/%s.pam", a->store, defs[i].path);
			if (e26_image_read_pnm(path, &cur[i].im, err, sizeof err) == 0 && cur[i].im.w > 0 && cur[i].im.w <= 64 && cur[i].im.h <= 64) cur[i].have = 1;
			else if (a->verbose) fprintf(stderr, "d32library: cursor %s: %s (the arrow stands in)\n", defs[i].name, err);
		}
		if (cur[i].c) { XFreeCursor(a->dpy, cur[i].c); cur[i].c = None; }
		if (cur[i].have && (cur[i].c = make(a, i, k))) n++;
	}
	k_made = k;
	shown = -1; /* shown again at the new size */
	if (a->verbose) fprintf(stderr, "d32library: cursors: %d of %d made at x%d (%s)\n", n, CUR_N - 1, k, have_render(a) ? "RENDER ARGB" : "pixmap");
}

void cursor_show(e26_app *a, int id)
{
	if (!a->dpy || !installed || id < 0 || id >= CUR_N) return;
	if (id != CUR_ARROW && !cur[id].c) id = CUR_ARROW;
	if (id == shown) return;
	if (id == CUR_ARROW) XUndefineCursor(a->dpy, a->win);
	else XDefineCursor(a->dpy, a->win, cur[id].c);
	shown = id;
	if (a->verbose) fprintf(stderr, "d32library: cursor %s\n", defs[id].name);
}

void cursor_refresh(e26_app *a)
{
	if (last_x >= 0) cursor_show(a, cursor_at(a, last_x, last_y));
}

/* after input.c's handler (Xt calls handlers in the order they were added): the state the event left decides */
static void on_pointer(Widget w, XtPointer cd, XEvent *ev, Boolean *cont)
{
	e26_app *a = (e26_app *)cd;
	int wx, wy;
	(void)w; (void)cont;
	switch (ev->type) {
	case MotionNotify: wx = ev->xmotion.x; wy = ev->xmotion.y; break;
	case ButtonPress: case ButtonRelease: wx = ev->xbutton.x; wy = ev->xbutton.y; break;
	case EnterNotify: wx = ev->xcrossing.x; wy = ev->xcrossing.y; break;
	default: return;
	}
	scale_to_frame(&a->sc, wx, wy, &last_x, &last_y);
	if (hold >= 0 && !a->drag_ops) hold = -1; /* the gesture ended (released anywhere) */
	cursor_show(a, cursor_at(a, last_x, last_y));
}

/* the first screen in the Library's window: the handler goes on, every cursor is made (before any pledge) */
static void on_enter(e26_app *a, const e26_event *ev, void *ud)
{
	(void)ev; (void)ud;
	if (!a->dpy || !a->da || !a->win || !XtIsRealized(a->da) || a->win != XtWindow(a->da)) return;
	if (!installed) {
		XtAddEventHandler(a->da, PointerMotionMask | ButtonPressMask | ButtonReleaseMask | EnterWindowMask, False, on_pointer, (XtPointer)a);
		installed = 1;
		make_all(a);
	}
	hold = -1;
	cursor_refresh(a);
}

static void on_resize(e26_app *a, const e26_event *ev, void *ud)
{
	(void)ev; (void)ud;
	if (!installed) return;
	make_all(a);
	cursor_refresh(a);
}

void cursor_register(void)
{
	static int done;
	if (done) return;
	done = 1;
	ev_subscribe(EV_SCREEN_ENTER, on_enter, NULL);
	ev_subscribe(EV_RESIZE, on_resize, NULL);
}
