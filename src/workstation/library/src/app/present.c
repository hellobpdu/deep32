/* present.c: puts the back buffer on the window (W1-SHARP in wave 1; update 1's scalers kept as the fallback).
 * A 640x480 window gets the 640x480 frame as before (one XCopyArea). A window larger than the frame shows it k times
 * larger, k the largest integer that fits (scale.h), centred on black, by one of three scalers (*scaler, -scaler, or
 * LIBRARY.INI's Scaler when the session names none):
 *   sharp   (the default, item 63) the back buffer itself is 640k x 480k and the frame is drawn into it at full
 *           resolution (gfx.c: text from k-times faces, lines and fills at k, the 1995 bitmaps doubled nearest,
 *           photos resampled smoothly); it goes on the window 1:1. The pointer maps back exactly as before.
 *   render  (update 1) the 640x480 frame enlarged nearest in the server by RENDER (one XRenderComposite);
 *   cpu     (update 1) the same by XGetImage, scale_pixels and XPutImage (also when the server has no RENDER 0.6).
 * Beyond k = SCALE_SHARP_MAX the sharp scaler falls back to render (or cpu). At k = 1 every scaler is the original
 * path, pixel for pixel. */
#include "library_app.h"
#include "room_article.h"
#include <X11/Xutil.h>
#include <X11/extensions/Xrender.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef char e26_frame_size_check[(E26_W == E26_FRAME_W && E26_H == E26_FRAME_H) ? 1 : -1];

enum { SC_SHARP, SC_RENDER, SC_CPU };
static const char *const sc_names[] = { "sharp", "render", "cpu" };
static int mode = -1;            /* the scaler in use, once known */
static int stale;                /* the back buffer was made again at a new factor and not drawn since */
static XRenderPictFormat *pic_fmt;
static int back_w = E26_W, back_h = E26_H;
static int back_native;

/* The stack, rather than the focused screen, selects the scene's coordinate system. */
static int client_viewport(const e26_app *a, int w, int h)
{
	const scr_screen *full = a->nwin ? a->wstack[0].s : a->cur;
	const char *viewport;
	if (!full || full->over[0] || w < E26_W || h < E26_H || (w == E26_W && h == E26_H))
		return 0;
	if (!strcmp(full->name, "article")) {
		const art_state *st = art_st();
		if (!a->have_art || !st->is_v2 || (st->view != 2 && st->view != 3)) return 0;
	} else {
		viewport = scr_screen_prop(full, "viewport");
		if (!viewport || strcmp(viewport, "native-root")) return 0;
	}
	return nav_viewport_compatible(a);
}

int present_client_viewport(const e26_app *a)
{
	return client_viewport(a, a->sc.w, a->sc.h);
}

int present_article_viewport(const e26_app *a)
{
	const scr_screen *full = a->nwin ? a->wstack[0].s : a->cur;
	return full && !strcmp(full->name, "article") && present_client_viewport(a);
}

void present_frame_bounds(const e26_app *a, int *w, int *h)
{
	int native = present_client_viewport(a);
	*w = native ? a->sc.w : E26_W;
	*h = native ? a->sc.h : E26_H;
}

static int sc_parse(const char *v)
{
	int i;
	for (i = 0; v && i < 3; i++)
		if (!strcmp(v, sc_names[i])) return i;
	return -1;
}

/* the session (command line, app-defaults, the server's resources) names *scaler: it wins over LIBRARY.INI */
static int session_names_scaler(e26_app *a)
{
	String app_name = NULL, app_class = NULL;
	char n[200], c[200], *type = NULL;
	XrmValue v;
	XtGetApplicationNameAndClass(a->dpy, &app_name, &app_class);
	snprintf(n, sizeof n, "%s.scaler", app_name ? app_name : "d32library");
	snprintf(c, sizeof c, "%s.Scaler", app_class ? app_class : "Deep32Library");
	return XrmGetResource(XtDatabase(a->dpy), n, c, &type, &v) ? 1 : 0;
}

static void mode_init(e26_app *a)
{
	const char *v, *from;
	if (mode >= 0) return;
	if (a->scaler && session_names_scaler(a)) { v = a->scaler; from = "the session"; }
	else if (set_stored("Scaler")) { v = set_get("Scaler"); from = "LIBRARY.INI"; }
	else { v = "sharp"; from = "the default"; }
	if ((mode = sc_parse(v)) < 0) {
		fprintf(stderr, "d32library: no scaler %s (sharp, render, cpu); using sharp\n", v ? v : "");
		mode = SC_SHARP;
	}
	if (a->verbose) fprintf(stderr, "d32library: scaler %s (%s)\n", sc_names[mode], from);
}

/* the factor the back buffer is drawn at: the window's in SHARP mode (up to SCALE_SHARP_MAX), else 1 */
static int back_k(const e26_app *a)
{
	return mode == SC_SHARP && a->sc.k <= SCALE_SHARP_MAX ? a->sc.k : 1;
}

/* RENDER: the back buffer's Picture (again after the back buffer changed), nearest filter, transform not yet set */
static void render_source(e26_app *a)
{
	XRenderPictureAttributes pa;
	if (!a->use_render) return;
	if (a->pic_src) XRenderFreePicture(a->dpy, a->pic_src);
	memset(&pa, 0, sizeof pa);
	a->pic_src = XRenderCreatePicture(a->dpy, a->back, pic_fmt, 0, &pa);
	XRenderSetPictureFilter(a->dpy, a->pic_src, FilterNearest, NULL, 0);
	a->pic_k = 0;
}

static void render_setup(e26_app *a)
{
	int ev, er, maj = 0, min = 0;
	XRenderPictFormat *f;
	XRenderPictureAttributes pa;
	if (!XRenderQueryExtension(a->dpy, &ev, &er) || !XRenderQueryVersion(a->dpy, &maj, &min) || (maj == 0 && min < 6) ||
	    !(f = XRenderFindVisualFormat(a->dpy, a->vis))) {
		if (a->verbose) fprintf(stderr, "d32library: scaler cpu (the X server has no RENDER 0.6 for this visual)\n");
		return;
	}
	pic_fmt = f;
	memset(&pa, 0, sizeof pa);
	a->pic_src = XRenderCreatePicture(a->dpy, a->back, f, 0, &pa);
	a->pic_dst = XRenderCreatePicture(a->dpy, a->win, f, 0, &pa); /* clipped by children: the Motif scroll bar stays */
	XRenderSetPictureFilter(a->dpy, a->pic_src, FilterNearest, NULL, 0);
	a->use_render = 1;
	if (a->verbose)
		fprintf(stderr, "d32library: scaler %s (RENDER %d.%d, nearest filter%s)\n", mode == SC_SHARP ? "sharp" : "render", maj, min,
		        mode == SC_SHARP ? ", kept for factors beyond the sharp back buffer" : "");
}

static void render_teardown(e26_app *a)
{
	if (a->pic_src) XRenderFreePicture(a->dpy, a->pic_src);
	if (a->pic_dst) XRenderFreePicture(a->dpy, a->pic_dst);
	a->pic_src = a->pic_dst = 0;
	a->use_render = 0;
	a->pic_k = 0;
}

void present_init(e26_app *a)
{
	mode_init(a);
	a->use_render = 0;
	a->pic_k = 0;
	if (mode == SC_CPU) {
		if (a->verbose) fprintf(stderr, "d32library: scaler cpu (nearest neighbour, XPutImage)\n");
		return;
	}
	render_setup(a);
}

/* the back buffer at factor k: made again (black until the next frame is drawn), the frame's context and SHARP's
 * caches moved to k; 1 when it changed */
static int back_make(e26_app *a, int k, int w, int h)
{
	Pixmap nb;
	int factor_changed = a->g.k != k;
	if (!a->back || !a->win || (!factor_changed && back_w == w && back_h == h)) return 0;
	nb = XCreatePixmap(a->dpy, a->win, (unsigned)w, (unsigned)h, (unsigned)a->depth);
	XFreePixmap(a->dpy, a->back);
	a->back = nb;
	back_w = w; back_h = h;
	gfx_init(&a->g, a, nb, k);
	if (factor_changed) gfx_k_changed(a, k);
	render_source(a);
	stale = 1;
	if (a->verbose) fprintf(stderr, "d32library: back buffer %dx%d (x%d)\n", w, h, k);
	return 1;
}

int present_resize(e26_app *a, int w, int h)
{
	int responsive = client_viewport(a, w, h), changed, k;
	int scene_changed = responsive != back_native;
	if ((responsive || back_native) && (scene_changed || a->sc.w != w || a->sc.h != h)) {
		menus_close(a);
		a->hot = a->pressed = a->menu_hot = a->sub_hot = a->outline_hot = -1;
		a->drag_ops = NULL;
	}
	back_native = responsive;
	mode_init(a);
	if (responsive) {
		changed = a->sc.w != w || a->sc.h != h || a->sc.k != 1 || a->sc.ox || a->sc.oy;
		a->sc = (e26_scale){ .k = 1, .w = w, .h = h };
	} else changed = scale_fit(&a->sc, w, h);
	if (changed && a->verbose)
		fprintf(stderr, "d32library: window %dx%d: frame x%d at (%d,%d)\n", w, h, a->sc.k, a->sc.ox, a->sc.oy);
	k = back_k(a);
	if (back_make(a, k, responsive ? w : E26_W * k, responsive ? h : E26_H * k)) changed = 1;
	return changed || scene_changed;
}

/* A room or view change can select a new presentation without ConfigureNotify. */
void present_prepare(e26_app *a)
{
	if (a->sc.w > 0 && a->sc.h > 0) (void)present_resize(a, a->sc.w, a->sc.h);
	stale = 0;
}

int present_set_scaler(e26_app *a, const char *name)
{
	int m = sc_parse(name);
	if (m < 0) return -1;
	mode_init(a);
	if (m == mode) return 0;
	mode = m;
	if (a->win && a->back) {
		if (mode == SC_CPU) render_teardown(a);
		else if (!a->use_render) render_setup(a);
		(void)present_resize(a, a->sc.w, a->sc.h);
	}
	stale = 1;
	if (a->verbose) fprintf(stderr, "d32library: scaler %s (set)\n", sc_names[mode]);
	return 0;
}

const char *present_scaler(void)
{
	return sc_names[mode >= 0 ? mode : SC_SHARP];
}

/* RENDER maps each window pixel's centre back through the transform: 1/k (exact for 2 and 4; for 3 the 16.16 error
 * stays under 0.01 px across the 640 columns, so every k x k block holds one frame pixel) */
static void render_transform(e26_app *a, int k)
{
	XTransform t;
	if (a->pic_k == k) return;
	memset(&t, 0, sizeof t);
	t.matrix[0][0] = t.matrix[1][1] = XDoubleToFixed(1.0 / k);
	t.matrix[2][2] = XDoubleToFixed(1.0);
	XRenderSetPictureTransform(a->dpy, a->pic_src, &t);
	a->pic_k = k;
}

static void present_cpu(e26_app *a)
{
	const e26_scale *s = &a->sc;
	int fw = E26_W * s->k, fh = E26_H * s->k, bpp;
	XImage *src = XGetImage(a->dpy, a->back, 0, 0, E26_W, E26_H, AllPlanes, ZPixmap);
	if (!src) return;
	bpp = src->bits_per_pixel / 8;
	if (a->zoom && (a->zoom->width != fw || a->zoom->height != fh || a->zoom->bits_per_pixel != src->bits_per_pixel)) {
		XDestroyImage(a->zoom);
		a->zoom = NULL;
	}
	if (!a->zoom && src->bits_per_pixel % 8 == 0 && bpp >= 1 && bpp <= 4) {
		a->zoom = XCreateImage(a->dpy, a->vis, (unsigned)a->depth, ZPixmap, 0, NULL, (unsigned)fw, (unsigned)fh, 32, 0);
		if (a->zoom && !(a->zoom->data = malloc((size_t)a->zoom->bytes_per_line * (size_t)fh))) {
			XDestroyImage(a->zoom);
			a->zoom = NULL;
		}
	}
	if (a->zoom && a->zoom->bits_per_pixel == src->bits_per_pixel) {
		scale_pixels((const unsigned char *)src->data, (size_t)src->bytes_per_line, (unsigned char *)a->zoom->data,
		             (size_t)a->zoom->bytes_per_line, E26_W, E26_H, bpp, s->k);
		XPutImage(a->dpy, a->win, a->gc, a->zoom, 0, 0, s->ox, s->oy, (unsigned)fw, (unsigned)fh);
	} else /* no memory or an odd pixel size: the frame unscaled, centred */
		XCopyArea(a->dpy, a->back, a->win, a->gc, 0, 0, E26_W, E26_H, s->ox + (fw - E26_W) / 2, s->oy + (fh - E26_H) / 2);
	XDestroyImage(src);
}

void present_frame(e26_app *a)
{
	const e26_scale *s = &a->sc;
	int r[4][4], n, i, k;
	if (!a->win) return;
	if (stale && a->cur) { /* a new factor since the last frame: draw it at the new one first */
		stale = 0;
		draw_frame(a);
	}
	if (present_client_viewport(a)) {
		XCopyArea(a->dpy, a->back, a->win, a->gc, 0, 0, (unsigned)s->w, (unsigned)s->h, 0, 0);
		return;
	}
	k = a->g.k;
	if (scale_is_identity(s)) { /* 640x480: the original path, pixel for pixel */
		XCopyArea(a->dpy, a->back, a->win, a->gc, 0, 0, E26_W, E26_H, 0, 0);
		return;
	}
	n = scale_margins(s, r);
	XSetForeground(a->dpy, a->gc, xa_pixel(a, ui_rgb(a, UI_BLACK)));
	for (i = 0; i < n; i++) XFillRectangle(a->dpy, a->win, a->gc, r[i][0], r[i][1], (unsigned)r[i][2], (unsigned)r[i][3]);
	if (k == s->k) { /* the back buffer is already at the window's factor: 1x, or SHARP at k */
		XCopyArea(a->dpy, a->back, a->win, a->gc, 0, 0, (unsigned)(E26_W * k), (unsigned)(E26_H * k), s->ox, s->oy);
		return;
	}
	if (a->use_render) {
		render_transform(a, s->k);
		XRenderComposite(a->dpy, PictOpSrc, a->pic_src, None, a->pic_dst, 0, 0, 0, 0, s->ox, s->oy,
		                 (unsigned)(E26_W * s->k), (unsigned)(E26_H * s->k));
		return;
	}
	present_cpu(a);
}

void present_area(e26_app *a, int x, int y, int w, int h)
{
	const e26_scale *s = &a->sc;
	int k = s->k, width, height;
	if (!a->win) return;
	present_frame_bounds(a, &width, &height);
	if (x < 0) { w += x; x = 0; }
	if (y < 0) { h += y; y = 0; }
	if (x + w > width) w = width - x;
	if (y + h > height) h = height - y;
	if (w <= 0 || h <= 0) return;
	if (stale) { present_frame(a); return; }
	if (a->g.k == k) {
		XCopyArea(a->dpy, a->back, a->win, a->gc, x * k, y * k, (unsigned)(w * k), (unsigned)(h * k), s->ox + x * k, s->oy + y * k);
		return;
	}
	if (a->use_render) {
		render_transform(a, k);
		XRenderComposite(a->dpy, PictOpSrc, a->pic_src, None, a->pic_dst, x * k, y * k, 0, 0, s->ox + x * k, s->oy + y * k,
		                 (unsigned)(w * k), (unsigned)(h * k));
		return;
	}
	present_frame(a);
}
