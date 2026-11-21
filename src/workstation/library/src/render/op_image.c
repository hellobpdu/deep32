/* SPDX-License-Identifier: AGPL-3.0-or-later */
/* Copyright (C) 2026 Jeffrey A. Cummings */
/* op_image.c: e26-render's decode_fit (W1-IMAGE, plan R3 and W10; LIB-PROTOCOL "e26-render named operations").
 * A picture arrives as a read-only descriptor with its declared MIME type; its first bytes must agree with that
 * type. WebP is decoded by libwebp, PNG, JPEG and GIF by MuPDF's image loaders, SVG drawn by MuPDF's SVG document at
 * the size the box asks for. The picture then goes into the box (imgscale.h: fit or fill, area-average down, nearest
 * or bilinear up, on the ground), optionally dithered to the period's 256 colours, and comes back as RGBA. Limits
 * (LIB-PROTOCOL): 16 million decoded input pixels, 64 MiB of encoded input, an answer that fits one E26P body. */
#include "render.h"
#include "mu.h"
#include "../app/registry.h"
#include "../common/helper.h"
#include "../common/imgscale.h"
#include <stdlib.h>
#include <string.h>
#ifdef HAVE_LIBWEBP
#include <webp/decode.h>
#endif

#define DF_MAX_INPUT (64u * 1024u * 1024u)
#define DF_MAX_PIXELS 16000000L

enum { M_WEBP, M_PNG, M_JPEG, M_GIF, M_SVG, M_N };
static const char *const MIMES[M_N] = { "image/webp", "image/png", "image/jpeg", "image/gif", "image/svg+xml" };

/* what the bytes say they are: an M_*, or -1 */
static int sniff(const unsigned char *b, size_t n)
{
	size_t i = 0, lim;
	if (n >= 12 && !memcmp(b, "RIFF", 4) && !memcmp(b + 8, "WEBP", 4)) return M_WEBP;
	if (n >= 8 && !memcmp(b, "\x89PNG\r\n\x1a\n", 8)) return M_PNG;
	if (n >= 3 && b[0] == 0xff && b[1] == 0xd8 && b[2] == 0xff) return M_JPEG;
	if (n >= 6 && (!memcmp(b, "GIF87a", 6) || !memcmp(b, "GIF89a", 6))) return M_GIF;
	/* SVG: text (a UTF-8 byte order mark allowed) whose first markup is XML, a comment, a doctype or <svg, with an
	 * <svg element in its first 4 KiB */
	if (n >= 3 && !memcmp(b, "\xef\xbb\xbf", 3)) i = 3;
	while (i < n && (b[i] == ' ' || b[i] == '\t' || b[i] == '\r' || b[i] == '\n')) i++;
	if (i < n && b[i] == '<') {
		lim = n < 4096 ? n : 4096;
		for (; i + 4 <= lim; i++)
			if (!memcmp(b + i, "<svg", 4)) return M_SVG;
	}
	return -1;
}

static char *why(const char *s) { return rs_dup(s); }

#ifdef HAVE_MUPDF
/* PNG, JPEG, GIF: MuPDF's loaders, the first frame of a GIF */
static const char *raster(const unsigned char *buf, size_t len, unsigned char **rgba, int *w, int *h, char **result)
{
	fz_context *ctx = mu_ctx();
	fz_buffer *volatile fb = NULL;
	fz_image *volatile im = NULL;
	fz_pixmap *volatile pix = NULL, *volatile rgb = NULL;
	const char *volatile code = NULL;
	if (!ctx) { *result = why("no MuPDF context"); return "E_PACK"; }
	mu_reset_code();
	fz_try(ctx) {
		fb = fz_new_buffer_from_copied_data(ctx, buf, len);
		im = fz_new_image_from_buffer(ctx, fb);
		if ((long)im->w * im->h > DF_MAX_PIXELS || im->w < 1 || im->h < 1) { code = "E_TOO_LARGE"; }
		else {
			pix = fz_get_pixmap_from_image(ctx, im, NULL, NULL, NULL, NULL);
			rgb = fz_convert_pixmap(ctx, pix, fz_device_rgb(ctx), NULL, NULL, fz_default_color_params, 1);
			*w = fz_pixmap_width(ctx, rgb);
			*h = fz_pixmap_height(ctx, rgb);
			if (!(*rgba = (unsigned char *)malloc((size_t)*w * *h * 4))) code = "E_TOO_LARGE";
			else mu_to_rgba(fz_pixmap_samples(ctx, rgb), fz_pixmap_stride(ctx, rgb), fz_pixmap_components(ctx, rgb),
			                fz_pixmap_alpha(ctx, rgb), *w, *h, *rgba);
		}
	}
	fz_always(ctx) {
		fz_drop_pixmap(ctx, rgb);
		fz_drop_pixmap(ctx, pix);
		fz_drop_image(ctx, im);
		fz_drop_buffer(ctx, fb);
	}
	fz_catch(ctx) {
		*result = why(mu_code()[2] == 'T' ? "the picture needs more memory than allowed" : "the picture cannot be decoded");
		return mu_code();
	}
	if (code) *result = why("the picture has too many pixels");
	return code;
}

/* SVG: the drawing at the size the box asks for (fit: inside it; fill: covering it), with its transparency */
static const char *svg(const unsigned char *buf, size_t len, int bw, int bh, int mode, unsigned char **rgba, int *w, int *h, char **result)
{
	fz_context *ctx = mu_ctx();
	fz_buffer *volatile fb = NULL;
	fz_document *volatile doc = NULL;
	fz_page *volatile page = NULL;
	fz_pixmap *volatile pix = NULL;
	const char *volatile code = NULL;
	if (!ctx) { *result = why("no MuPDF context"); return "E_PACK"; }
	mu_reset_code();
	fz_try(ctx) {
		fz_rect r;
		float pw, ph, s;
		fb = fz_new_buffer_from_copied_data(ctx, buf, len);
		doc = fz_open_document_with_buffer(ctx, "image/svg+xml", fb);
		page = fz_load_page(ctx, doc, 0);
		r = fz_bound_page(ctx, page);
		pw = r.x1 - r.x0; ph = r.y1 - r.y0;
		if (!(pw > 0 && ph > 0)) code = "E_ARGUMENT";
		else {
			int tw, th;
			if (mode == IMG_FILL) { s = (float)bw / pw > (float)bh / ph ? (float)bw / pw : (float)bh / ph; tw = (int)(pw * s + 0.5f); th = (int)(ph * s + 0.5f); }
			else { img_fit_size((int)(pw * 64 + 0.5f), (int)(ph * 64 + 0.5f), bw, bh, &tw, &th); }
			if (tw < 1) tw = 1;
			if (th < 1) th = 1;
			if ((long)tw * th > DF_MAX_PIXELS) code = "E_TOO_LARGE";
			else {
				fz_matrix m = fz_scale((float)tw / pw, (float)th / ph);
				fz_irect ib;
				m = fz_pre_translate(m, -r.x0, -r.y0);
				ib = fz_make_irect(0, 0, tw, th);
				pix = fz_new_pixmap_with_bbox(ctx, fz_device_rgb(ctx), ib, NULL, 1);
				fz_clear_pixmap(ctx, pix);
				{
					fz_device *dev = fz_new_draw_device(ctx, fz_identity, pix);
					fz_try(ctx) { fz_run_page(ctx, page, dev, m, NULL); fz_close_device(ctx, dev); }
					fz_always(ctx) fz_drop_device(ctx, dev);
					fz_catch(ctx) fz_rethrow(ctx);
				}
				*w = tw; *h = th;
				if (!(*rgba = (unsigned char *)malloc((size_t)tw * th * 4))) code = "E_TOO_LARGE";
				else mu_to_rgba(fz_pixmap_samples(ctx, pix), fz_pixmap_stride(ctx, pix), fz_pixmap_components(ctx, pix), 1, tw, th, *rgba);
			}
		}
	}
	fz_always(ctx) {
		fz_drop_pixmap(ctx, pix);
		fz_drop_page(ctx, page);
		fz_drop_document(ctx, doc);
		fz_drop_buffer(ctx, fb);
	}
	fz_catch(ctx) {
		*result = why("the drawing cannot be read");
		return mu_code();
	}
	if (code) *result = why(code[2] == 'T' ? "the drawing is too large" : "the drawing has no size");
	return code;
}
#endif

static const char *const KEYS[] = { "input_fd", "mime", "width", "height", "scale", "filter", "background", "dither",
                                    "pan_x", "pan_y", "zoom", NULL };

static const char *decode_fit(const struct jv *args, char **result)
{
	long slot, bw, bh, pan_x = 0, pan_y = 0, zoom = 0;
	const char *mime, *scale, *filter, *code = NULL;
	int dither, kind, mode, actual, zoomed, filt, w = 0, h = 0, i;
	unsigned char bg[4] = { 255, 255, 255, 255 }, *buf = NULL, *rgba = NULL, *out;
	size_t len;
	const jv *b = json_get(args, "background");
	*result = NULL;
	if (!ra_only(args, KEYS) || ra_int(args, "input_fd", 0, 0, -1, &slot) != 1 || ra_str(args, "mime", 64, NULL, &mime) != 1 ||
	    ra_int(args, "width", 1, 4096, 0, &bw) != 1 || ra_int(args, "height", 1, 4096, 0, &bh) != 1 ||
	    ra_str(args, "scale", 8, "fit", &scale) < 0 || ra_str(args, "filter", 16, "bilinear", &filter) < 0 ||
	    ra_bool(args, "dither", 0, &dither) < 0) { *result = why("an argument is missing or malformed"); return "E_ARGUMENT"; }
	actual = !strcmp(scale, "actual");
	/* u14 S4: "zoom": a box-sized tile of the picture at zoom percent (5-800), pan offsets in zoomed pixels */
	zoomed = !strcmp(scale, "zoom");
	if (zoomed && ra_int(args, "zoom", 5, 800, 0, &zoom) != 1) { *result = why("zoom scale requires a zoom of 5-800 percent"); return "E_ARGUMENT"; }
	if (!zoomed && json_get(args, "zoom")) { *result = why("zoom is for the zoom scale only"); return "E_ARGUMENT"; }
	if (actual || zoomed) {
		if (ra_int(args, "pan_x", 0, DF_MAX_PIXELS, 0, &pan_x) != 1 ||
		    ra_int(args, "pan_y", 0, DF_MAX_PIXELS, 0, &pan_y) != 1) {
			*result = why("actual scale requires two nonnegative source offsets"); return "E_ARGUMENT";
		}
	} else if (json_get(args, "pan_x") || json_get(args, "pan_y") ||
	           (strcmp(scale, "fit") && strcmp(scale, "fill") && strcmp(scale, "fit-down"))) {
		*result = why("invalid scale or unexpected pan offsets"); return "E_ARGUMENT";
	}
	if (strcmp(filter, "nearest") && strcmp(filter, "bilinear") && strcmp(filter, "bicubic")) { *result = why("filter is nearest, bilinear or bicubic"); return "E_ARGUMENT"; }
	mode = !strcmp(scale, "fit-down") ? IMG_FIT_DOWN : !strcmp(scale, "fill") ? IMG_FILL : IMG_FIT;
	filt = !strcmp(filter, "nearest") ? IMG_NEAREST : !strcmp(filter, "bicubic") ? IMG_BICUBIC : IMG_BILINEAR;
	if (b) {
		if (b->type != J_ARR || b->n != 4) { *result = why("background is four values 0-255"); return "E_ARGUMENT"; }
		for (i = 0; i < 4; i++) {
			double d;
			if (!ra_num(b->items[i], 0, 255, &d) || d != (double)(int)d) { *result = why("background is four values 0-255"); return "E_ARGUMENT"; }
			bg[i] = (unsigned char)d;
		}
	}
	for (kind = 0; kind < M_N && strcmp(mime, MIMES[kind]); kind++) {
	}
	if (kind == M_N) { *result = why("decode_fit takes WebP, PNG, JPEG, GIF and SVG"); return "E_ARGUMENT"; }
	if ((actual || zoomed) && kind == M_SVG) { *result = why("a vector drawing has no native pixels"); return "E_ARGUMENT"; }
	if ((size_t)bw * bh > rs_max_pixels()) { *result = why("the box's pixels do not fit one response"); return "E_TOO_LARGE"; }
	i = rd_all(render_fd(0), DF_MAX_INPUT, &buf, &len);
	if (i == -2) { *result = why("the picture's file is larger than allowed"); return "E_TOO_LARGE"; }
	if (i) { *result = why("the descriptor cannot be read"); return "E_ARGUMENT"; }
	if (sniff(buf, len) != kind) { free(buf); *result = why("the bytes are not the declared type"); return "E_ARGUMENT"; }
	switch (kind) {
	case M_WEBP:
#ifdef HAVE_LIBWEBP
		if (!WebPGetInfo(buf, len, &w, &h)) { code = "E_ARGUMENT"; *result = why("the picture cannot be decoded"); }
		else if ((long)w * h > DF_MAX_PIXELS) { code = "E_TOO_LARGE"; *result = why("the picture has too many pixels"); }
		else {
			uint8_t *d = WebPDecodeRGBA(buf, len, &w, &h);
			if (!d) { code = "E_ARGUMENT"; *result = why("the picture cannot be decoded"); }
			else if (!(rgba = (unsigned char *)malloc((size_t)w * h * 4))) { code = "E_TOO_LARGE"; WebPFree(d); }
			else { memcpy(rgba, d, (size_t)w * h * 4); WebPFree(d); }
		}
#else
		code = "E_UNSUPPORTED"; *result = why("this build has no WebP decoder");
#endif
		break;
	case M_SVG:
#ifdef HAVE_MUPDF
		code = svg(buf, len, (int)bw, (int)bh, mode, &rgba, &w, &h, result);
#else
		code = "E_UNSUPPORTED"; *result = why("this build has no SVG renderer");
#endif
		break;
	default:
#ifdef HAVE_MUPDF
		code = raster(buf, len, &rgba, &w, &h, result);
#else
		code = "E_UNSUPPORTED"; *result = why("this build has no picture decoder");
#endif
		break;
	}
	free(buf);
	if (code) { free(rgba); return code; }
	if (!(out = (unsigned char *)malloc((size_t)bw * bh * 4))) { free(rgba); return "E_TOO_LARGE"; }
	if (zoomed ? img_zoom(rgba, w, h, out, (int)bw, (int)bh, (int)zoom, (int)pan_x, (int)pan_y, filt, bg) :
	    actual ? img_actual(rgba, w, h, out, (int)bw, (int)bh, (int)pan_x, (int)pan_y, bg) :
	             img_fit(rgba, w, h, out, (int)bw, (int)bh, mode, filt, bg)) {
		free(rgba); free(out); return "E_TOO_LARGE";
	}
	free(rgba);
	if (dither) img_dither256(out, (int)bw, (int)bh);
	if (actual || zoomed || mode == IMG_FIT_DOWN || filt == IMG_BICUBIC)
		*result = rs_rgba_source(out, (int)bw, (int)bh, kind == M_SVG ? 0 : w, kind == M_SVG ? 0 : h, &code);
	else *result = rs_rgba(out, (int)bw, (int)bh, &code);
	free(out);
	return *result ? NULL : code;
}

void e26_op_image(void);
void e26_op_image(void)
{
	static const e26_helper_op op = { decode_fit };
	reg_add(REG_RENDEROP, "decode_fit", &op, "op_image", RENDER_FDS(1));
}
