/* SPDX-License-Identifier: AGPL-3.0-or-later */
/* Copyright (C) 2026 Jeffrey A. Cummings */
/* op_pdfwrite.c: e26-render's pdf_write (W1-IMAGE for W2-RESEARCH's Print and export to PDF, plan T6;
 * LIB-PROTOCOL "e26-render named operations"). The interface lays out what is printed as a display list (text, lines
 * and boxes on pages measured in points, top-left origin) and hands over a descriptor it opened for writing, chosen
 * by its export dialogue; the renderer never names a path. Every page carries the source cite (bottom left) and the
 * handling marking (top and bottom centre, as markings are banners), and the document's information holds the
 * title, the cite (Subject) and the marking (Keywords). Text is set in the three standard faces (Helvetica, Times,
 * Courier), which every PDF reader has, in WinAnsi: a character outside it prints as '?'. Nothing else is embedded:
 * this edition of the display list has no pictures (LIB-PROTOCOL: a versioned extension adds them). */
#include "render.h"
#include "mu.h"
#include "../app/registry.h"
#include "../common/helper.h"
#include "../common/sha256.h"
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define MAX_PAGES 256
#define MAX_ITEMS 4096
#define MAX_TEXT 65536
#define MAX_DIM 14400.0

#ifdef HAVE_MUPDF
/* the next code point of UTF-8 text; a byte that does not start a valid sequence reads as itself (Latin-1: the
 * shared JSON reader turns a é escape into the one byte 0xE9) */
static int next_cp(const unsigned char **p)
{
	const unsigned char *s = *p;
	int c = s[0], n, i;
	if (c < 0x80) { *p = s + 1; return c; }
	n = c >= 0xf8 ? -1 : c >= 0xf0 ? 3 : c >= 0xe0 ? 2 : c >= 0xc2 ? 1 : -1;
	if (n < 0) { *p = s + 1; return c; }
	c &= 0x3f >> n;
	for (i = 1; i <= n; i++) {
		if ((s[i] & 0xc0) != 0x80) { *p = s + 1; return s[0]; }
		c = c << 6 | (s[i] & 0x3f);
	}
	*p = s + n + 1;
	return c;
}

/* a code point in WinAnsiEncoding (cp1252), or '?' */
static int winansi(int c)
{
	static const unsigned short HI[32] = {   /* 0x80-0x9f */
		0x20ac, 0, 0x201a, 0x0192, 0x201e, 0x2026, 0x2020, 0x2021, 0x02c6, 0x2030, 0x0160, 0x2039, 0x0152, 0, 0x017d, 0,
		0, 0x2018, 0x2019, 0x201c, 0x201d, 0x2022, 0x2013, 0x2014, 0x02dc, 0x2122, 0x0161, 0x203a, 0x0153, 0, 0x017e, 0x0178,
	};
	int i;
	if ((c >= 0x20 && c < 0x7f) || (c >= 0xa0 && c <= 0xff)) return c;
	for (i = 0; i < 32; i++)
		if (HI[i] && HI[i] == c) return 0x80 + i;
	return '?';
}

/* a PDF string literal of UTF-8 text in WinAnsi, into the content stream */
static void put_string(fz_context *ctx, fz_buffer *b, const char *s, size_t n)
{
	const unsigned char *p = (const unsigned char *)s, *end = p + n;
	fz_append_byte(ctx, b, '(');
	while (p < end) {
		int c = winansi(next_cp(&p));
		if (c == '(' || c == ')' || c == '\\') { fz_append_byte(ctx, b, '\\'); fz_append_byte(ctx, b, c); }
		else if (c < 0x20 || c >= 0x7f) {         /* an octal escape (MuPDF's printf has no %o) */
			fz_append_byte(ctx, b, '\\');
			fz_append_byte(ctx, b, '0' + (c >> 6));
			fz_append_byte(ctx, b, '0' + ((c >> 3) & 7));
			fz_append_byte(ctx, b, '0' + (c & 7));
		}
		else fz_append_byte(ctx, b, c);
	}
	fz_append_byte(ctx, b, ')');
}

/* about how wide Helvetica text of n characters is at size pt (for centring the banners; half an em a letter) */
static float approx_width(const char *s, float pt)
{
	size_t n = 0;
	const unsigned char *p = (const unsigned char *)s;
	while (*p) { next_cp(&p); n++; }
	return (float)n * pt * 0.5f;
}

static int colour(const jv *v, int rgb[3])
{
	int i;
	if (!v || v->type != J_ARR || v->n != 3) return -1;
	for (i = 0; i < 3; i++) {
		double d;
		if (!ra_num(v->items[i], 0, 255, &d) || d != (double)(int)d) return -1;
		rgb[i] = (int)d;
	}
	return 0;
}

static const char *const TEXT_KEYS[] = { "kind", "x", "y", "text", "font", "size", "colour", NULL };
static const char *const LINE_KEYS[] = { "kind", "x1", "y1", "x2", "y2", "width", "colour", NULL };
static const char *const RECT_KEYS[] = { "kind", "x", "y", "width", "height", "colour", "fill", NULL };
static const char *const PAGE_KEYS[] = { "width", "height", "items", NULL };

/* one item into the page's content stream; -1 when it is malformed */
static int item(fz_context *ctx, fz_buffer *b, const jv *it, double ph)
{
	const char *kind = json_str(it, "kind");
	int rgb[3];
	double x, y, x2, y2, w, h;
	if (!kind) return -1;
	if (!strcmp(kind, "text")) {
		const char *font, *text;
		double size;
		int f;
		if (!ra_only(it, TEXT_KEYS) || !ra_num(json_get(it, "x"), -MAX_DIM, MAX_DIM, &x) || !ra_num(json_get(it, "y"), -MAX_DIM, MAX_DIM, &y) ||
		    ra_str(it, "text", MAX_TEXT, NULL, &text) != 1 || ra_str(it, "font", 8, NULL, &font) != 1 ||
		    !ra_num(json_get(it, "size"), 0.01, 1000, &size) || colour(json_get(it, "colour"), rgb)) return -1;
		f = !strcmp(font, "sans") ? 1 : !strcmp(font, "serif") ? 2 : !strcmp(font, "mono") ? 3 : 0;
		if (!f) return -1;
		fz_append_printf(ctx, b, "BT /F%d %g Tf %g %g %g rg 1 0 0 1 %g %g Tm ", f, size, rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, x, ph - y);
		put_string(ctx, b, text, strlen(text));
		fz_append_string(ctx, b, " Tj ET\n");
	} else if (!strcmp(kind, "line")) {
		if (!ra_only(it, LINE_KEYS) || !ra_num(json_get(it, "x1"), -MAX_DIM, MAX_DIM, &x) || !ra_num(json_get(it, "y1"), -MAX_DIM, MAX_DIM, &y) ||
		    !ra_num(json_get(it, "x2"), -MAX_DIM, MAX_DIM, &x2) || !ra_num(json_get(it, "y2"), -MAX_DIM, MAX_DIM, &y2) ||
		    !ra_num(json_get(it, "width"), 0.001, 1000, &w) || colour(json_get(it, "colour"), rgb)) return -1;
		fz_append_printf(ctx, b, "%g w %g %g %g RG %g %g m %g %g l S\n", w, rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, x, ph - y, x2, ph - y2);
	} else if (!strcmp(kind, "rect")) {
		int fill = 0;
		if (!ra_only(it, RECT_KEYS) || !ra_num(json_get(it, "x"), -MAX_DIM, MAX_DIM, &x) || !ra_num(json_get(it, "y"), -MAX_DIM, MAX_DIM, &y) ||
		    !ra_num(json_get(it, "width"), 0.001, MAX_DIM, &w) || !ra_num(json_get(it, "height"), 0.001, MAX_DIM, &h) ||
		    colour(json_get(it, "colour"), rgb) || ra_bool(it, "fill", -1, &fill) != 1) return -1;
		if (fill) fz_append_printf(ctx, b, "%g %g %g rg %g %g %g %g re f\n", rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, x, ph - y - h, w, h);
		else fz_append_printf(ctx, b, "1 w %g %g %g RG %g %g %g %g re S\n", rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, x, ph - y - h, w, h);
	} else return -1;
	return 0;
}

/* the cite (bottom left, wrapped to the page) and the marking (top and bottom centre) */
static void furniture(fz_context *ctx, fz_buffer *b, const char *cite, const char *marking, double pw, double ph)
{
	if (marking[0]) {
		float mw = approx_width(marking, 10);
		fz_append_printf(ctx, b, "BT /F4 10 Tf 0 0 0 rg 1 0 0 1 %g %g Tm ", (pw - mw) / 2 > 0 ? (pw - mw) / 2 : 4, ph - 18);
		put_string(ctx, b, marking, strlen(marking));
		fz_append_string(ctx, b, " Tj ET\n");
		fz_append_printf(ctx, b, "BT /F4 10 Tf 0 0 0 rg 1 0 0 1 %g %g Tm ", (pw - mw) / 2 > 0 ? (pw - mw) / 2 : 4, 10.0);
		put_string(ctx, b, marking, strlen(marking));
		fz_append_string(ctx, b, " Tj ET\n");
	}
	if (cite[0]) {
		/* lines of at most as many characters as fit the page's width less half-inch margins, from 22 pt up */
		size_t per = (size_t)((pw - 72) / (7 * 0.5)), n = strlen(cite), at = 0;
		int line = 0;
		const char *lines[8];
		size_t lens[8];
		if (per < 20) per = 20;
		while (at < n && line < 8) {
			size_t take = n - at, k;
			if (take > per) {
				take = per;
				for (k = per; k > per / 2; k--)
					if (cite[at + k] == ' ') { take = k; break; }
				while (take < n - at && ((unsigned char)cite[at + take] & 0xc0) == 0x80) take++;   /* whole characters */
			}
			lines[line] = cite + at; lens[line] = take; line++;
			at += take;
			while (at < n && cite[at] == ' ') at++;
		}
		for (n = 0; n < (size_t)line; n++) {
			fz_append_printf(ctx, b, "BT /F1 7 Tf 0 0 0 rg 1 0 0 1 36 %g Tm ", 22 + 8.5 * (line - 1 - (int)n));
			put_string(ctx, b, lines[n], lens[n]);
			fz_append_string(ctx, b, " Tj ET\n");
		}
	}
}

static const char *const KEYS[] = { "output_fd", "pages", "title", "cite", "marking", NULL };

static const char *pdf_write(const struct jv *args, char **result)
{
	fz_context *ctx = mu_ctx();
	long slot;
	const char *title, *cite, *marking;
	const jv *pages = json_get(args, "pages");
	pdf_document *volatile doc = NULL;
	fz_buffer *volatile out = NULL, *volatile cb = NULL;
	fz_output *volatile o = NULL;
	volatile int bad = 0;
	size_t i;
	*result = NULL;
	if (!ra_only(args, KEYS) || ra_int(args, "output_fd", 0, 0, -1, &slot) != 1 || ra_str(args, "title", 4096, NULL, &title) != 1 ||
	    ra_str(args, "cite", 4096, NULL, &cite) != 1 || ra_str(args, "marking", 4096, NULL, &marking) != 1 ||
	    !pages || pages->type != J_ARR || pages->n < 1 || pages->n > MAX_PAGES) {
		*result = rs_dup("an argument is missing or malformed");
		return "E_ARGUMENT";
	}
	for (i = 0; i < pages->n; i++) {           /* the whole list is checked before anything is written */
		const jv *p = pages->items[i], *items = json_get(p, "items");
		double w, h;
		if (!p || p->type != J_OBJ || !ra_only(p, PAGE_KEYS) || !ra_num(json_get(p, "width"), 0.001, MAX_DIM, &w) ||
		    !ra_num(json_get(p, "height"), 0.001, MAX_DIM, &h) || !items || items->type != J_ARR || items->n > MAX_ITEMS) {
			*result = rs_dup("a page is malformed");
			return "E_ARGUMENT";
		}
	}
	if (!ctx) { *result = rs_dup("no MuPDF context"); return "E_PACK"; }
	mu_reset_code();
	fz_try(ctx) {
		pdf_obj *fonts, *res, *info;
		static const char *const FACES[4] = { "Helvetica", "Times-Roman", "Courier", "Helvetica-Bold" };
		int f;
		doc = pdf_create_document(ctx);
		fonts = pdf_new_dict(ctx, doc, 4);
		for (f = 0; f < 4; f++) {
			char nm[8];
			pdf_obj *fd = pdf_add_new_dict(ctx, doc, 4);
			pdf_dict_put(ctx, fd, PDF_NAME(Type), PDF_NAME(Font));
			pdf_dict_put(ctx, fd, PDF_NAME(Subtype), PDF_NAME(Type1));
			pdf_dict_put_name(ctx, fd, PDF_NAME(BaseFont), FACES[f]);
			pdf_dict_put(ctx, fd, PDF_NAME(Encoding), PDF_NAME(WinAnsiEncoding));
			snprintf(nm, sizeof nm, "F%d", f + 1);
			pdf_dict_puts(ctx, fonts, nm, fd);
			pdf_drop_obj(ctx, fd);
		}
		res = pdf_new_dict(ctx, doc, 1);
		pdf_dict_put_drop(ctx, res, PDF_NAME(Font), fonts);
		for (i = 0; i < pages->n && !bad; i++) {
			const jv *p = pages->items[i], *items = json_get(p, "items");
			double w = json_get(p, "width")->num, h = json_get(p, "height")->num;
			size_t k;
			pdf_obj *pg;
			cb = fz_new_buffer(ctx, 4096);
			for (k = 0; k < items->n && !bad; k++)
				if (!items->items[k] || items->items[k]->type != J_OBJ || item(ctx, cb, items->items[k], h)) bad = 1;
			if (!bad) {
				furniture(ctx, cb, cite, marking, w, h);
				pg = pdf_add_page(ctx, doc, fz_make_rect(0, 0, (float)w, (float)h), 0, res, cb);
				pdf_insert_page(ctx, doc, -1, pg);
				pdf_drop_obj(ctx, pg);
			}
			fz_drop_buffer(ctx, cb);
			cb = NULL;
		}
		pdf_drop_obj(ctx, res);
		if (!bad) {
			pdf_write_options opts = pdf_default_write_options;
			info = pdf_add_new_dict(ctx, doc, 6);
			pdf_dict_put_text_string(ctx, info, PDF_NAME(Title), title);
			pdf_dict_put_text_string(ctx, info, PDF_NAME(Subject), cite);
			pdf_dict_put_text_string(ctx, info, PDF_NAME(Keywords), marking);
			pdf_dict_put_text_string(ctx, info, PDF_NAME(Creator), "Deep32 Encyclopedia");
			pdf_dict_put_text_string(ctx, info, PDF_NAME(Producer), "Deep32 Encyclopedia e26-render");
			pdf_dict_put_drop(ctx, pdf_trailer(ctx, doc), PDF_NAME(Info), info);
			opts.do_compress = 1;
			out = fz_new_buffer(ctx, 65536);
			o = fz_new_output_with_buffer(ctx, out);
			pdf_write_document(ctx, doc, o, &opts);
			fz_close_output(ctx, o);
		}
	}
	fz_always(ctx) {
		fz_drop_output(ctx, o);
		fz_drop_buffer(ctx, cb);
		pdf_drop_document(ctx, doc);
	}
	fz_catch(ctx) {
		fz_drop_buffer(ctx, out);
		*result = rs_dup("the PDF cannot be made");
		return mu_code();
	}
	if (bad) { fz_drop_buffer(ctx, out); *result = rs_dup("an item is malformed"); return "E_ARGUMENT"; }
	{
		unsigned char *data;
		size_t n = fz_buffer_storage(ctx, out, &data), done = 0;
		char hex[65], res[200];
		int fd = render_fd(0);
		while (done < n) {
			ssize_t w = write(fd, data + done, n - done);
			if (w < 0 && errno == EINTR) continue;
			if (w <= 0) { fz_drop_buffer(ctx, out); *result = rs_dup("the output cannot be written"); return "E_ARGUMENT"; }
			done += (size_t)w;
		}
		sha256_hex(data, n, hex);
		snprintf(res, sizeof res, "{\"bytes\":%lu,\"pages\":%lu,\"sha256\":\"%s\"}", (unsigned long)n, (unsigned long)pages->n, hex);
		fz_drop_buffer(ctx, out);
		*result = rs_dup(res);
	}
	return *result ? NULL : "E_PACK";
}
#else
static const char *pdf_write(const struct jv *args, char **result) { (void)args; *result = rs_dup("this build has no PDF writer"); return "E_UNSUPPORTED"; }
#endif

void e26_op_pdfwrite(void);
void e26_op_pdfwrite(void)
{
	static const e26_helper_op op = { pdf_write };
	reg_add(REG_RENDEROP, "pdf_write", &op, "op_pdfwrite", RENDER_FDS(1));
}
