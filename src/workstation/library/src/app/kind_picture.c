/* kind_picture.c: the picture kind (picture.h). W1-IMAGE. */
#include "library_app.h"
#include "figure_dialog.h"
#include "picture.h"
#include "render_client.h"
#include "room_article.h"
#include "xassets.h"
#include "json.h"
#include "../common/b64.h"
#include "../common/image.h"
#include "../common/imgscale.h"
#include "../common/sha256.h"
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define NJOBS 64      /* pictures being decoded at once */
#define NFAIL 128     /* pictures that could not be shown, remembered so they are not asked for at every redraw */
#define NPROV 8
#define TRIES 2       /* a picture lost with its helper (crash, timeout) is asked for again this many times */
#define VIEW_PIXELS 1500000 /* keep the device-pixel tile within one IMAGE reply even at integer scale */

/* u14 S4: the viewer shows Fit (never enlarged), Fit enlarged (bicubic), 100% (the source's pixels) or a zoom step */
enum { VIEW_NONE, VIEW_FIT, VIEW_ACTUAL, VIEW_ENLARGE, VIEW_ZOOM };

typedef struct {
	int live, waiting, inline_job, discard;  /* waiting: for picture_feed; discard: no longer wanted */
	unsigned long serial;
	long id;                    /* the render request, 0 before it is sent */
	char key[SCR_ASSET], spec[512];
	int w, h, fill, nearest, dither;
	int view, pan_x, pan_y, zoom;
	unsigned long view_epoch;
	unsigned char bg[4];
} job;

static job J[NJOBS];
static unsigned long serial;
static char failed[NFAIL][SCR_ASSET];
static int nfail_next;
static struct { char key[SCR_ASSET]; int n; } lost[NJOBS];
static struct { char scheme[24]; picture_open_fn fn; } prov[NPROV];
static int nprov;
static struct {
	unsigned long epoch;
	char spec[512], wanted[SCR_ASSET], shown[SCR_ASSET];
	int zoom, sw, sh, dw, dh, x, y;   /* zoom: 0 Fit, -1 Fit enlarged, else percent; x, y: pan in zoomed pixels */
	int ox, oy;                       /* where the tile sits in the canvas (frame pixels) */
} view;

/* the zoomed picture's size on one axis (the source's at 100%) */
static int zsize(int s, int zoom) { return zoom > 0 ? (int)(((long)s * zoom + 50) / 100) : s; }

void picture_view_reset(void)
{
	unsigned long epoch = view.epoch + 1;
	int i;
	for (i = 0; i < NJOBS; i++) if (J[i].live && J[i].view) {
		if (J[i].id > 0) rc_forget(J[i].id);
		J[i].live = 0;
	}
	memset(&view, 0, sizeof view);
	view.epoch = epoch;
}

static int pan_limit(int offset, int size, int box)
{
	int edge = size > box ? size - box : 0;
	return offset < 0 ? 0 : offset > edge ? edge : offset;
}

int picture_period_mode(void) { return set_get_bool("Colors256", 0); }

const char *picture_mime_of(const char *name)
{
	const char *dot = strrchr(name, '.');
	static const char *const T[][2] = { { ".webp", "image/webp" }, { ".png", "image/png" }, { ".jpg", "image/jpeg" },
	                                   { ".jpeg", "image/jpeg" }, { ".gif", "image/gif" }, { ".svg", "image/svg+xml" } };
	size_t i;
	if (!dot) return NULL;
	for (i = 0; i < sizeof T / sizeof T[0]; i++) {
		const char *p = dot, *q = T[i][0];
		while (*p && *q && (*p | 0x20) == *q) { p++; q++; }
		if (!*p && !*q) return T[i][1];
	}
	return NULL;
}

int picture_provider(const char *scheme, picture_open_fn fn)
{
	int i;
	for (i = 0; i < nprov; i++)
		if (!strcmp(prov[i].scheme, scheme)) { prov[i].fn = fn; return 0; }
	if (nprov == NPROV || strlen(scheme) >= sizeof prov[0].scheme) return -1;
	snprintf(prov[nprov].scheme, sizeof prov[nprov].scheme, "%s", scheme);
	prov[nprov++].fn = fn;
	return 0;
}

/* "pic|<16 hex of the spec's SHA-256>|": every decoded size of one spec starts so */
static void spec_prefix(const char *spec, char out[24])
{
	char hex[65];
	sha256_hex(spec, strlen(spec), hex);
	snprintf(out, 24, "pic|%.16s|", hex);
}

static void make_key(const char *spec, int w, int h, int fill, int nearest, int dither, const unsigned char bg[4], char *key)
{
	char pre[24];
	spec_prefix(spec, pre);
	snprintf(key, SCR_ASSET, "%s%dx%d|%c%c%c|%02x%02x%02x", pre, w, h, fill ? 'F' : 'f', nearest ? 'N' : 'b', dither ? 'D' : 'd',
	         bg[0], bg[1], bg[2]);
}

static int is_failed(const char *key)
{
	int i;
	for (i = 0; i < NFAIL; i++)
		if (failed[i][0] && !strcmp(failed[i], key)) return 1;
	return 0;
}

static void set_failed(e26_app *a, const char *key, const char *spec, const char *why)
{
	snprintf(failed[nfail_next], SCR_ASSET, "%s", key);
	nfail_next = (nfail_next + 1) % NFAIL;
	if (a && a->verbose) fprintf(stderr, "d32library: picture %s not shown (%s)\n", spec, why);
}

static job *find_key(const char *key)
{
	int i;
	for (i = 0; i < NJOBS; i++)
		if (J[i].live && !strcmp(J[i].key, key)) return &J[i];
	return NULL;
}

int picture_waiting(const char *spec)
{
	int i;
	for (i = 0; i < NJOBS; i++) if (J[i].live && J[i].waiting && !strcmp(J[i].spec, spec)) return 1;
	return 0;
}

int picture_inline_decodes(void)
{
	int i, n = 0;
	for (i = 0; i < NJOBS; i++) n += J[i].live && J[i].inline_job && !J[i].waiting;
	return n;
}

void picture_source_failed(e26_app *a, const char *spec)
{
	int i;
	for (i = 0; i < NJOBS; i++) if (J[i].live && J[i].waiting && !strcmp(J[i].spec, spec)) {
		set_failed(a, J[i].key, spec, "no source for it");
		J[i].live = 0;
	}
}

static void redraw_later(e26_app *a, void *ud)
{
	if (ud) figure_dialog_redraw(a);
	else if (a->dpy) nav_redraw(a);
}

static job *find_serial(unsigned long s)
{
	int i;
	for (i = 0; i < NJOBS; i++)
		if (J[i].live && J[i].serial == s) return &J[i];
	return NULL;
}

/* a picture lost with its helper: 1 when it may be asked for again */
static int retry_lost(const char *key)
{
	int i, free_ = -1;
	for (i = 0; i < NJOBS; i++) {
		if (lost[i].key[0] && !strcmp(lost[i].key, key)) return ++lost[i].n <= TRIES;
		if (!lost[i].key[0] && free_ < 0) free_ = i;
	}
	if (free_ < 0) free_ = (int)(serial % NJOBS);
	snprintf(lost[free_].key, SCR_ASSET, "%s", key);
	lost[free_].n = 1;
	return 1;
}

static void done(e26_app *a, const char *code, const jv *result, void *ud)
{
	job *j = find_serial((unsigned long)(uintptr_t)ud);
	const jv *wv, *hv, *swv, *shv;
	const char *data;
	unsigned char *px;
	long n;
	int w = j ? j->w : 0, h = j ? j->h : 0, sw = 0, sh = 0;
	if (!j) return;
	j->live = 0;
	if (j->view && j->view_epoch != view.epoch) return;
	if (j->discard) goto failed;
	if (code) {
		/* the helper went (E_PACK, E_TIMEOUT, E_BAD_FRAME, E_BUSY): asked again at a later redraw, twice at most */
		int transient = !strcmp(code, "E_PACK") || !strcmp(code, "E_TIMEOUT") || !strcmp(code, "E_BAD_FRAME") || !strcmp(code, "E_BUSY");
		if (!transient || !retry_lost(j->key)) set_failed(a, j->key, j->spec, code);
		else if (a && a->verbose) fprintf(stderr, "d32library: picture %s: %s, asked again at the next redraw\n", j->spec, code);
		goto failed;
	}
	wv = json_get(result, "width");
	hv = json_get(result, "height");
	data = json_str(result, "data_b64");
	if (!wv || !hv || wv->type != J_NUM || hv->type != J_NUM || wv->num != w || hv->num != h || !data)
		goto malformed;
	if (j->view) {
		swv = json_get(result, "source_width"); shv = json_get(result, "source_height");
		if (!swv || !shv || swv->type != J_NUM || shv->type != J_NUM ||
		    swv->num < 0 || swv->num > 16000000 || shv->num < 0 || shv->num > 16000000) goto malformed;
		sw = (int)swv->num; sh = (int)shv->num;
		if (sw != swv->num || sh != shv->num || (!sw != !sh) || (double)sw * sh > 16000000 ||
		    ((j->view == VIEW_ACTUAL || j->view == VIEW_ZOOM) && !sw)) goto malformed;
	} else if (j->inline_job) {
		/* u14 M8: an inline fit-down answer carries the source's size too (0 x 0 for a drawing): ARTICLE lays its
		 * figures out at that size. An answer without it (an older helper) keeps the provisional box. */
		swv = json_get(result, "source_width"); shv = json_get(result, "source_height");
		if (swv && shv && swv->type == J_NUM && shv->type == J_NUM && swv->num >= 0 && shv->num >= 0 &&
		    swv->num <= 16000000 && shv->num <= 16000000 && swv->num == (int)swv->num && shv->num == (int)shv->num &&
		    (double)swv->num * shv->num <= 16000000)
			art_inline_dims(a, j->spec, (int)swv->num, (int)shv->num);
	}
	if (!(px = (unsigned char *)malloc((size_t)w * h * 4))) goto malformed;
	n = b64_decode(data, strlen(data), px, (size_t)w * h * 4);
	if (n != (long)w * h * 4) { free(px); set_failed(a, j->key, j->spec, "a malformed answer"); goto failed; }
	if (!xa_put_rgba(a, j->key, px, w, h)) {
		free(px); set_failed(a, j->key, j->spec, "no room in the picture cache"); goto failed;
	}
	free(px);
	if (j->view) { view.sw = sw; view.sh = sh; figure_dialog_redraw(a); }
	else if (a && a->dpy) nav_redraw(a);
	return;
malformed:
	set_failed(a, j->key, j->spec, "a malformed answer");
failed:
	/* A broken socket can call done from inside rc_call, during a draw. */
	if ((j->inline_job || j->view) && a && a->ctx) timer_after(a, 0, redraw_later, j->view ? &view : NULL);
}

static void submit(e26_app *a, job *j, int fd, const char *mime)
{
	char args[400], pan[80] = "";
	const char *scale = j->view == VIEW_ACTUAL ? "actual" : j->view == VIEW_ZOOM ? "zoom" :
	                    j->view == VIEW_FIT || j->inline_job ? "fit-down" : j->fill ? "fill" : "fit";
	const char *filter = j->nearest ? "nearest" :
	                     j->view == VIEW_ENLARGE || (j->view == VIEW_ZOOM && j->zoom > 100) ? "bicubic" : "bilinear";
	if (j->view == VIEW_ACTUAL)
		snprintf(pan, sizeof pan, ",\"pan_x\":%d,\"pan_y\":%d", j->pan_x, j->pan_y);
	else if (j->view == VIEW_ZOOM)
		snprintf(pan, sizeof pan, ",\"zoom\":%d,\"pan_x\":%d,\"pan_y\":%d", j->zoom, j->pan_x, j->pan_y);
	snprintf(args, sizeof args,
	         "{\"input_fd\":0,\"mime\":\"%s\",\"width\":%d,\"height\":%d,\"scale\":\"%s\",\"filter\":\"%s\","
	         "\"background\":[%d,%d,%d,255],\"dither\":%s%s}",
	         mime, j->w, j->h, scale, filter, j->bg[0], j->bg[1], j->bg[2],
	         j->dither ? "true" : "false", pan);
	j->waiting = 0;
	j->id = rc_call(a, "decode_fit", args, fd, done, (void *)(uintptr_t)j->serial);
	if (j->id < 0) {
		j->live = 0;
		set_failed(a, j->key, j->spec, "e26-render cannot be started");
		if ((j->inline_job || j->view) && a && a->ctx) timer_after(a, 0, redraw_later, j->view ? &view : NULL);
	}
}

/* a store bitmap, decoded here (the store's own Netpbm), fitted and kept */
static void from_store(e26_app *a, job *j, const char *asset)
{
	char path[1200], err[300];
	e26_image im;
	unsigned char *out;
	snprintf(path, sizeof path, "%s/%s.ppm", a->store, asset);
	if (e26_image_read_pnm(path, &im, err, sizeof err)) {
		snprintf(path, sizeof path, "%s/%s.pam", a->store, asset);
		if (e26_image_read_pnm(path, &im, err, sizeof err)) { set_failed(a, j->key, j->spec, "not in the store"); return; }
	}
	out = (unsigned char *)malloc((size_t)j->w * j->h * 4);
	if (!out || img_fit(im.rgba, im.w, im.h, out, j->w, j->h, j->fill ? IMG_FILL : IMG_FIT, j->nearest ? IMG_NEAREST : IMG_BILINEAR, j->bg)) {
		free(out);
		e26_image_free(&im);
		set_failed(a, j->key, j->spec, "no memory");
		return;
	}
	e26_image_free(&im);
	if (j->dither) img_dither256(out, j->w, j->h);
	if (!xa_put_rgba(a, j->key, out, j->w, j->h)) set_failed(a, j->key, j->spec, "no room in the picture cache");
	free(out);
}

static void start(e26_app *a, const char *spec, const char *key, const scr_control *c, const unsigned char bg[4], int dither,
                  int dw, int dh, int inline_job, int viewer)
{
	const char *colon = strchr(spec, ':'), *mime = scr_prop_get(c, "mime"), *fit = scr_prop_get(c, "fit"), *filt = scr_prop_get(c, "filter");
	job *j = NULL;
	int i;
	for (i = 0; i < NJOBS && !j; i++)
		if (!J[i].live) j = &J[i];
	if (!j) return;                                   /* busy: asked for again at the next redraw */
	memset(j, 0, sizeof *j);
	j->live = 1;
	j->inline_job = inline_job;
	j->serial = ++serial;
	snprintf(j->key, sizeof j->key, "%s", key);
	snprintf(j->spec, sizeof j->spec, "%s", spec);
	j->w = dw;                                        /* device pixels: the box at the frame's k (W1-SHARP) */
	j->h = dh;
	j->fill = fit && !strcmp(fit, "fill");
	j->nearest = filt && !strcmp(filt, "nearest");
	j->dither = dither;
	if (viewer) {
		j->view = view.zoom == 100 ? VIEW_ACTUAL : view.zoom > 0 ? VIEW_ZOOM : view.zoom < 0 ? VIEW_ENLARGE : VIEW_FIT;
		j->zoom = view.zoom;
		j->view_epoch = view.epoch;
		j->pan_x = view.x; j->pan_y = view.y;
	}
	memcpy(j->bg, bg, 4);
	if (!colon || !strncmp(spec, "store:", 6)) {
		from_store(a, j, colon ? spec + 6 : spec);
		j->live = 0;
	} else if (!strncmp(spec, "file:", 5)) {
		int fd;
		if (!mime) mime = picture_mime_of(spec + 5);
		if (!mime) { j->live = 0; set_failed(a, key, spec, "no picture type"); return; }
		if ((fd = open(spec + 5, O_RDONLY | O_CLOEXEC)) < 0) { j->live = 0; set_failed(a, key, spec, "cannot be opened"); return; }
		submit(a, j, fd, mime);
	} else {
		char scheme[24], m[64] = "";
		int fd = -1, r = -1;
		snprintf(scheme, sizeof scheme, "%.*s", (int)(colon - spec) < 23 ? (int)(colon - spec) : 23, spec);
		j->waiting = 1;  /* a provider can report a failed fetch synchronously */
		for (i = 0; i < nprov; i++)
			if (!strcmp(prov[i].scheme, scheme)) r = prov[i].fn(a, spec, &fd, m, sizeof m);
		if (r < 0) { j->live = 0; set_failed(a, key, spec, "no source for it"); return; }
		if (r == 0) return;
		submit(a, j, fd, mime ? mime : m);
	}
}

void picture_feed(e26_app *a, const char *spec, int fd, const char *mime)
{
	int i;
	for (i = 0; i < NJOBS; i++)
		if (J[i].live && J[i].waiting && !strcmp(J[i].spec, spec)) {
			/* submit owns its descriptor and may close it immediately. Keep fd for the other boxes. */
			int d = fcntl(fd, F_DUPFD_CLOEXEC, 0);
			if (d < 0) { J[i].live = 0; set_failed(a, J[i].key, spec, "cannot copy its descriptor"); continue; }
			submit(a, &J[i], d, mime);
		}
	close(fd);
}

void picture_forget(e26_app *a, const char *spec)
{
	char pre[24];
	int i;
	spec_prefix(spec, pre);
	xa_forget_prefix(a, pre);
	for (i = 0; i < NFAIL; i++)
		if (!strncmp(failed[i], pre, strlen(pre))) failed[i][0] = 0;
	for (i = 0; i < NJOBS; i++)
		if (J[i].live && !strcmp(J[i].spec, spec)) {
			/* IMAGE serves a decode to completion even after a cancel. Keep its inline slot
			 * until then, but do not cache the obsolete pixels. */
			if (J[i].inline_job && !J[i].waiting) { J[i].discard = 1; continue; }
			if (J[i].id > 0) rc_forget(J[i].id);
			J[i].live = 0;
		}
}

static int draw_fitted(gfx_ctx *g, const xasset *p, int x, int y, int w, int h, int dw, int dh)
{
	gfx_pic pic;
	if (!p || p->w != dw || p->h != dh) return 0;
	/* The cache holds device pixels, copied 1:1 even in SHARP mode. */
	pic.pm = p->pm;
	pic.w = w;
	pic.h = h;
	pic.k = g->k;
	return !gfx_picture_draw(g, &pic, x, y);
}

enum picture_status picture_box(gfx_ctx *g, const char *spec, int x, int y, int w, int h, long ground,
                                enum picture_box_mode mode)
{
	static const scr_control options = { 0 };
	unsigned char bg[4] = { (unsigned char)(ground >> 16), (unsigned char)(ground >> 8), (unsigned char)ground, 255 };
	e26_app *a = g->a;
	int dw, dh, dither = picture_period_mode();
	char key[SCR_ASSET];
	xasset *p;
	gfx_device_size(g, w, h, &dw, &dh);
	make_key(spec, dw, dh, 0, 0, dither, bg, key);
	p = xa_find(a, key);
	if (p && (mode != PICTURE_DRAW || draw_fitted(g, p, x, y, w, h, dw, dh))) return PICTURE_READY;
	if (mode == PICTURE_LOAD && !find_key(key) && !is_failed(key)) start(a, spec, key, &options, bg, dither, dw, dh, 1, 0);
	if (find_key(key)) return PICTURE_PENDING;
	return is_failed(key) ? PICTURE_FAILED : PICTURE_IDLE;
}

static long colour_of(e26_app *a, long v) { return SCR_IS_UI(v) ? ui_rgb(a, (int)(v - SCR_UI_BASE)) : v; }

static void draw_picture(gfx_ctx *g, const scr_control *c, const char *spec, int x, int y,
                         int w, int h, long ground, int viewer)
{
	e26_app *a = g->a;
	char key[SCR_ASSET];
	const char *d = scr_prop_get(c, "dither");
	unsigned char bg[4];
	int dither = (d && d[0] == '1') || picture_period_mode(), dw, dh;
	xasset *p;
	if (ground < 0) ground = ui_rgb(a, UI_PAPER);
	if (w < 1 || h < 1) return;
	gfx_fill(g, x, y, w, h, ground);
	if (!spec || !spec[0]) return;
	bg[0] = (unsigned char)(ground >> 16); bg[1] = (unsigned char)(ground >> 8); bg[2] = (unsigned char)ground; bg[3] = 255;
	if (viewer) {
		int old_w = w, old_h = h;
		if (view.spec[0] && strcmp(view.spec, spec)) picture_view_reset();
		snprintf(view.spec, sizeof view.spec, "%s", spec);
		if (w > 4096 / g->k) w = 4096 / g->k;
		if (h > 4096 / g->k) h = 4096 / g->k;
		if (!w) return;
		if (h > VIEW_PIXELS / (w * g->k) / g->k) h = VIEW_PIXELS / (w * g->k) / g->k;
		if (!h) return;
		x += (old_w - w) / 2;
		y += (old_h - h) / 2;
		view.ox = x; view.oy = y;
	}
	gfx_device_size(g, w, h, &dw, &dh);
	make_key(spec, dw, dh, scr_prop_get(c, "fit") && !strcmp(scr_prop_get(c, "fit"), "fill"),
	         scr_prop_get(c, "filter") && !strcmp(scr_prop_get(c, "filter"), "nearest"), dither, bg, key);
	if (viewer) {
		size_t n = strlen(key);
		view.dw = dw; view.dh = dh;
		view.x = pan_limit(view.x, zsize(view.sw, view.zoom), dw);
		view.y = pan_limit(view.y, zsize(view.sh, view.zoom), dh);
		snprintf(key + n, sizeof key - n, "|v%lu|%d|%d,%d", view.epoch, view.zoom, view.x, view.y);
	}
	if (!(p = xa_find(a, key)) && !find_key(key) && !is_failed(key) &&
	    (!viewer || !find_key(view.wanted))) {
		start(a, spec, key, c, bg, dither, dw, dh, 0, viewer);
		p = xa_find(a, key);                      /* a store picture is there at once */
	}
	if (!viewer) { (void)draw_fitted(g, p, x, y, w, h, dw, dh); return; }
	/* Keep the previous tile visible while the next one is decoded. */
	if (find_key(key) || !find_key(view.wanted)) snprintf(view.wanted, sizeof view.wanted, "%s", key);
	if (draw_fitted(g, p, x, y, w, h, dw, dh))
		snprintf(view.shown, sizeof view.shown, "%s", key);
	else if (!draw_fitted(g, xa_find(a, view.shown), x, y, w, h, dw, dh))
		gfx_text(g, x + 8, y + 20, F_UI, ui_rgb(a, UI_BLACK),
		         is_failed(key) ? "Image unavailable." : "Loading image...", -1);
}

static void draw(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int x, int y, int under)
{
	const scr_control *c = &s->c[i];
	const source_ops *src = source_of(c);
	const char *spec = NULL;
	char buf[512];
	(void)under;
	if (src && src->text) spec = src->text(a, c, buf, sizeof buf);
	if (!spec) spec = scr_prop_get(c, "image");
	draw_picture(g, c, spec, x, y, c->w, c->h, colour_of(a, c->bg >= 0 ? c->bg : s->ground), 0);
}

void picture_view_draw(gfx_ctx *g, const char *spec, int w, int h)
{
	static const scr_control options = { 0 };
	draw_picture(g, &options, spec, 0, 0, w, h, ui_rgb(g->a, UI_PAPER), 1);
}

/* the zoom Fit shows at (percent of the source; Fit never enlarges, Fit enlarged does) */
static int fit_zoom(int enlarge)
{
	long zx, zy, z;
	if (!view.sw || !view.sh || !view.dw || !view.dh) return 100;
	zx = (long)view.dw * 100 / view.sw;
	zy = (long)view.dh * 100 / view.sh;
	z = zx < zy ? zx : zy;
	if (!enlarge && z > 100) z = 100;
	return z < 1 ? 1 : (int)z;
}

static int zoom_now(void) { return view.zoom > 0 ? view.zoom : fit_zoom(view.zoom < 0); }

/* u14 S4: goes to zoom (0 Fit, -1 Fit enlarged, or percent) keeping the source point under (px, py) of the canvas in
 * place (the canvas' centre for the keys) */
static int zoom_to(int zoom, int px, int py)
{
	int z0 = zoom_now(), z1, w0, h0, w1, h1, ox, oy;
	long sx, sy;
	if (zoom == view.zoom) return 0;
	if (zoom <= 0) { view.zoom = zoom; view.x = view.y = 0; return 1; }
	z1 = zoom;
	w0 = zsize(view.sw, z0); h0 = zsize(view.sh, z0);
	ox = w0 < view.dw ? (view.dw - w0) / 2 : -(view.zoom > 0 ? view.x : 0);
	oy = h0 < view.dh ? (view.dh - h0) / 2 : -(view.zoom > 0 ? view.y : 0);
	sx = (long)(px - ox) * 1000 / z0;   /* the source point, in thousandths of a pixel per percent */
	sy = (long)(py - oy) * 1000 / z0;
	w1 = zsize(view.sw, z1); h1 = zsize(view.sh, z1);
	view.zoom = z1;
	view.x = pan_limit((int)(sx * z1 / 1000) - px, w1, view.dw);
	view.y = pan_limit((int)(sy * z1 / 1000) - py, h1, view.dh);
	return 1;
}

/* the zoom steps: Fit, 25, 50, 100, 200, 400 */
static const int zoom_steps[] = { 25, 50, 100, 200, 400 };

/* commands: fit, enlarge (Fit enlarged), actual (100%), toggle (Fit or 100%), in, out (a step, at the canvas' centre),
 * in@x,y and out@x,y (at a canvas point: the wheel), pan:dx,dy (a drag), and the pan keys */
int picture_view_change(const char *command)
{
	int nx = view.x, ny = view.y, px = view.dw / 2, py = view.dh / 2, i, z;
	const char *at;
	if (!command) return 0;
	if (!strncmp(command, "pan:", 4)) {
		int dx, dy;
		if (view.zoom <= 0 || sscanf(command + 4, "%d,%d", &dx, &dy) != 2) return 0;
		nx = pan_limit(view.x - dx, zsize(view.sw, view.zoom), view.dw);
		ny = pan_limit(view.y - dy, zsize(view.sh, view.zoom), view.dh);
		if (nx == view.x && ny == view.y) return 0;
		view.x = nx; view.y = ny;
		return 1;
	}
	if (find_key(view.wanted)) return 0;
	if (!strcmp(command, "fit")) return zoom_to(0, px, py);
	if (!view.sw || !view.sh) return 0; /* loading or a vector drawing */
	if (!strcmp(command, "enlarge")) return zoom_to(-1, px, py);
	if (!strcmp(command, "actual")) return zoom_to(100, px, py);
	if (!strcmp(command, "toggle")) return zoom_to(view.zoom == 100 ? 0 : 100, px, py);
	if ((at = strchr(command, '@')) != NULL) {
		if (sscanf(at + 1, "%d,%d", &px, &py) != 2) return 0;
		px -= view.ox; py -= view.oy;   /* canvas to tile */
	}
	if (!strncmp(command, "in", 2) && (!command[2] || command[2] == '@')) {
		z = zoom_now();
		for (i = 0; i < (int)(sizeof zoom_steps / sizeof zoom_steps[0]); i++) if (zoom_steps[i] > z) return zoom_to(zoom_steps[i], px, py);
		return 0;
	}
	if (!strncmp(command, "out", 3) && (!command[3] || command[3] == '@')) {
		z = zoom_now();
		for (i = (int)(sizeof zoom_steps / sizeof zoom_steps[0]) - 1; i >= 0; i--)
			if (zoom_steps[i] < z && zoom_steps[i] > fit_zoom(0)) return zoom_to(zoom_steps[i], px, py);
		return view.zoom ? zoom_to(0, px, py) : 0;
	}
	if (view.zoom <= 0) return 0;
	if (!strcmp(command, "left")) nx -= 48;
	else if (!strcmp(command, "right")) nx += 48;
	else if (!strcmp(command, "up")) ny -= 48;
	else if (!strcmp(command, "down")) ny += 48;
	else if (!strcmp(command, "page-up")) ny -= view.dh;
	else if (!strcmp(command, "page-down")) ny += view.dh;
	else if (!strcmp(command, "home")) nx = ny = 0;
	else if (!strcmp(command, "end")) { nx = zsize(view.sw, view.zoom); ny = zsize(view.sh, view.zoom); }
	nx = pan_limit(nx, zsize(view.sw, view.zoom), view.dw); ny = pan_limit(ny, zsize(view.sh, view.zoom), view.dh);
	if (nx == view.x && ny == view.y) return 0;
	view.x = nx; view.y = ny;
	return 1;
}

/* 1 when the picture is smaller than the canvas (Fit shows it at 1:1: "Fit (enlarge)" is offered) */
int picture_view_small(void) { return view.sw && view.sh && view.sw < view.dw && view.sh < view.dh; }

const char *picture_view_info(char *buf, size_t n)
{
	char mode[48];
	if (!view.sw || !view.sh) return view.shown[0] ? "Vector image  |  Fit" : "";
	if (find_key(view.wanted)) snprintf(mode, sizeof mode, "Loading...");
	else if (is_failed(view.wanted)) snprintf(mode, sizeof mode, "View unavailable");
	else if (view.zoom == 0) snprintf(mode, sizeof mode, "Fit (%d%%)", fit_zoom(0));
	else if (view.zoom < 0) snprintf(mode, sizeof mode, "Fit, enlarged (%d%%)", fit_zoom(1));
	else snprintf(mode, sizeof mode, "%d%%", view.zoom);
	snprintf(buf, n, "%d x %d pixels  |  %s", view.sw, view.sh, mode);
	return buf;
}

void e26_mod_kind_picture(void);
void e26_mod_kind_picture(void)
{
	static const kind_ops ops = { draw, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL };
	static const e26_setting colors256 = { "Colors256", NULL, SET_BOOL, "0" };
	static const e26_setting cache = { "PictureCacheMB", NULL, SET_INT, "64" };
	reg_add(REG_KIND, "picture", &ops, "kind_picture", 0);
	reg_add(REG_SETTING, colors256.key, &colors256, "kind_picture", 0);
	reg_add(REG_SETTING, cache.key, &cache, "kind_picture", 0);
}
