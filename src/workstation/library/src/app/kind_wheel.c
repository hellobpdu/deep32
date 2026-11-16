/* kind_wheel.c: kinds wheel (the Pinpointer's title list), edit (its word-wheel field) and count (the count box under
 * the list); the list's logic is room_pinpoint.c. Captures 83 and 84 (256 colours). W2-FINDER owns these in wave 2a. */
#include "library_app.h"
#include "room_finder.h"
#include "select.h"
#include "lib_client.h"
#include <stdio.h>
#include <string.h>

/* Geometry inside the list control (frame x 18-191, rows 152-391 on capture 83): the white list x 0-157 with
 * 16-px rows, the current row on an entry-14 band inset 5 px (x 5-152), lettering Arial 11 black; the Library's own
 * scroll column x 158-173 on entry 31: MAINUP and MAINDN frame 0 at its ends, MTHUMB frame 0 between (12x14 at
 * x 160; all three exact on capture 83 at (176,152), (176,375), (178,170)) */
#define PIN_ROW_H 16
#define PIN_SB_W 16
#define PIN_TEXT_X 10
int pin_doc_mode(void);
int pin_doc_display(const e26_app *, int, char *, size_t, char *, size_t, int *, int *, int *);
const char *pin_doc_tag(int);
int pin_doc_expand(e26_app *, int, int);
uint32_t pin_doc_count(void);
int pin_wheel_rows(const e26_app *, const scr_screen *, const scr_control *);

#define PIN_BASE 11 /* [m256] capitals on a row's rows 3-10 (captures 83, 84: 155-162 on the first row, 171-178 on the second) */

/* u14 (spec M14): on the wide native Pinpointer (pin_native) the rows are pin_row_pitch() high (26 px at UiScale 150)
 * in F_RUI with the baseline centred and the current row's band one pixel short of the pitch; the letterbox and the
 * compact filter windows keep the 1995 list above */
static int row_h(const e26_app *a, const scr_screen *s) { return pin_row_pitch(a, s); }
static int row_base(const e26_app *a, const scr_screen *s)
{
	const XFontStruct *f = a->font[F_RUI];
	return pin_native(a, s) ? (pin_row_pitch(a, s) + f->ascent - f->descent) / 2 : PIN_BASE;
}
static int band_h(const e26_app *a, const scr_screen *s) { return pin_native(a, s) ? row_h(a, s) - 1 : PIN_ROW_H; }
static int expander_w(const e26_app *a, const scr_screen *s) { return pin_native(a, s) ? 22 : 15; }

int pin_wheel_rows(const e26_app *a, const scr_screen *s, const scr_control *c) { return c ? c->h / row_h(a, s) : 0; }

/* the list's thumb: its top ty and height th, and the travel of its top from top to bot (frame rows) */
static void pin_thumb(e26_app *a, const scr_screen *s, const scr_control *c, int cy, int *ty, int *th, int *top, int *bot)
{
	xasset *up = xa_get(a, "ui/bitmap/MAINUP"), *t = xa_get(a, "ui/bitmap/MTHUMB");
	int h = t ? t->h : 14, range = (int)a->nview - pin_wheel_rows(a, s, c);
	int first = a->wtop < range ? a->wtop : range;
	*top = cy + (up ? up->h + 1 : 18);
	*bot = cy + c->h - (up ? up->h : 17) + 1 - h;
	*th = h;
	*ty = range > 0 ? *top + (int)((long)(*bot - *top) * first / range) : *top;
}

/* the list row under (x,y), or -1; part: 0 the list, 1 up, 2 down, 3 the track above the thumb, 4 below it, 5 the thumb */
static int wheel_hit(e26_app *a, const scr_screen *s, const scr_control *c, int x, int y, int *part)
{
	int cx = c->x + s->ox, cy = c->y + s->oy, ty, th, top, bot;
	*part = 0;
	if (x < cx || y < cy || x >= cx + c->w || y >= cy + c->h) return -1;
	if (x >= cx + c->w - PIN_SB_W) {
		pin_thumb(a, s, c, cy, &ty, &th, &top, &bot);
		if (y < cy + 17) *part = 1;
		else if (y >= cy + c->h - 16) *part = 2;
		else if ((int)a->nview > pin_wheel_rows(a, s, c) && y >= ty && y < ty + th) *part = 5; /* the thumb: it drags */
		else *part = y < ty ? 3 : 4;
		return -1;
	}
	if (y - cy >= pin_wheel_rows(a, s, c) * row_h(a, s)) return -1;
	return a->wtop + (y - cy) / row_h(a, s);
}

/* the list's first row for the thumb's top at frame row y (the thumb dragged: Windows' rule, and the Library's own article
 * column's) */
static int wheel_drag_row(e26_app *a, const scr_screen *s, const scr_control *c, int y)
{
	int cy = c->y + s->oy, ty, th, top, bot, range = (int)a->nview - pin_wheel_rows(a, s, c);
	pin_thumb(a, s, c, cy, &ty, &th, &top, &bot);
	if (range <= 0 || bot <= top) return 0;
	if (y < top) y = top;
	if (y > bot) y = bot;
	return (int)(((long)(y - top) * range + (bot - top) / 2) / (bot - top));
}

static int wheel_thumb_top(e26_app *a, const scr_screen *s, const scr_control *c)
{
	int ty, th, top, bot;
	pin_thumb(a, s, c, c->y + s->oy, &ty, &th, &top, &bot);
	return ty;
}

static void crop(gfx_ctx *g, int font, const char *dots, const char *text, int width, char *out, size_t cap)
{
	int len = (int)strlen(text), used = len;
	if (gfx_text_width(g, font, text, len) <= width) { snprintf(out, cap, "%s", text); return; }
	width -= gfx_text_width(g, font, dots, -1);
	if (used > width) used = width > 0 ? width : 0;
	while (used > 0 && gfx_text_width(g, font, text, used) > width) used--;
	snprintf(out, cap, "%.*s%s", used, text, dots);
}

/* u14 (spec M15): a document's identifier as a reader names it, for the tag that tells titles that read alike apart:
 * rfc1364 "RFC 1364", draft:draft-x-07 "draft-x-07", wiki:Foo_Bar "Foo Bar"; others as they are (UTF-8) */
static void humanise(const char *id, char *out, size_t cap)
{
	const char *digits = id + 3;
	size_t i;
	if (!strncmp(id, "rfc", 3) && *digits && digits[strspn(digits, "0123456789")] == 0) snprintf(out, cap, "RFC %s", digits);
	else if (!strncmp(id, "draft:", 6)) snprintf(out, cap, "%s", id + 6);
	else if (!strncmp(id, "wiki:", 5)) {
		snprintf(out, cap, "%s", id + 5);
		for (i = 0; out[i]; i++) if (out[i] == '_') out[i] = ' ';
	} else snprintf(out, cap, "%s", id);
}

static int draw_document(e26_app *a, gfx_ctx *g, const scr_screen *s, int row, int x, int y, int width)
{
	char label[3700], id[1024], text[3700], clipped[3700], tag[1024], human[1100];
	const char *section = NULL, *separator, *type = pin_doc_tag(row);
	int native = pin_native(a, s), font = pin_row_font(a, s), base = row_base(a, s);
	const char *dots = pin_ellipsis(a, font);
	int level, expander, duplicate, indent, avail, tag_width = 0;
	long tag_ink = native ? PIN_QUIET : C_BLACK;
	if (pin_doc_display(a, row, label, sizeof label, id, sizeof id, &level, &expander, &duplicate)) return -1;
	sel_utf8_to_cp1252(label, strlen(label), text, sizeof text);
	indent = native ? 24 + level * 14 : 15 + level * 8;
	avail = width - indent - 10;
	if (row == a->wsel) gfx_fill(g, x + 5, y, width - 10, band_h(a, s), ui_rgb(a, UI_STONE));
	if (expander) gfx_text(g, x + (native ? 9 : 4), y + base, font, C_BLACK, expander > 0 ? "+" : "-", 1);
	/* a search hit reads "<title> \xbb <heading>" (M15): the document's title, then the section the words are in */
	for (separator = text; (separator = strstr(separator, " \xbb ")) != NULL; separator += 3)
		section = separator;
	tag[0] = 0;
	if (id[0] && (duplicate || strchr(text, '?') ||
	    (!section && gfx_text_width(g, font, text, -1) > avail))) {
		char cp[sizeof human];
		const char *tail;
		int max = width / 2;
		humanise(id, human, sizeof human);
		sel_utf8_to_cp1252(human, strlen(human), cp, sizeof cp);
		tail = cp;
		if ((int)strlen(cp) > max) tail += strlen(cp) - max;
		while (*tail && gfx_text_width(g, font, tail, -1) > max) tail++;
		/* a row that already names it ("RFC 1654 - ...", "... - draft-x-07") needs no tag to be told apart */
		if (!strstr(text, cp)) snprintf(tag, sizeof tag, "%s%.1000s", tail == cp ? "" : dots, tail);
	}
	if (!tag[0] && section && type && *type) snprintf(tag, sizeof tag, "%s", type); /* the short type, quiet (M15) */
	if (tag[0]) {
		tag_width = gfx_text_width(g, font, tag, -1);
		gfx_text(g, x + width - (native ? 8 : 5) - tag_width, y + base, font, tag_ink, tag, -1);
		tag_width += native ? 12 : 0;
	}
	avail -= tag_width;
	if (section && gfx_text_width(g, font, text, -1) > avail) {
		char head[3700], tail[3700], short_head[3700];
		int suffix_width;
		crop(g, font, dots, section, avail / 2, tail, sizeof tail);
		suffix_width = gfx_text_width(g, font, tail, -1);
		snprintf(head, sizeof head, "%.*s", (int)(section - text), text);
		crop(g, font, dots, head, avail - suffix_width, short_head, sizeof short_head);
		snprintf(clipped, sizeof clipped, "%.1800s%.1800s", short_head, tail);
	} else crop(g, font, dots, text, avail, clipped, sizeof clipped);
	gfx_text(g, x + indent, y + base, font, C_BLACK, clipped, -1);
	return 0;
}

static void draw_wheel(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int cx, int cy, int under)
{
	const scr_control *c = &s->c[i];
	int r, ty, th, top, bot, sb = c->w - PIN_SB_W, pitch = row_h(a, s), base = row_base(a, s);
	int font = pin_row_font(a, s), text_x = pin_native(a, s) ? 14 : PIN_TEXT_X;
	xasset *up = xa_get(a, "ui/bitmap/MAINUP"), *dn = xa_get(a, "ui/bitmap/MAINDN"), *t = xa_get(a, "ui/bitmap/MTHUMB");
	(void)under;
	gfx_fill(g, cx, cy, sb, c->h, C_WHITE);
	gfx_clip(g, cx, cy, sb, c->h);
	for (r = 0; r < pin_wheel_rows(a, s, c) && a->have_ww; r++) {
		d32ww_entry e, o;
		char t2[300], title[1024], label[1024];
		const char *show;
		int y = cy + r * pitch, n, row = a->wtop + r, duplicate;
		if (pin_doc_mode()) {
			if (draw_document(a, g, s, row, cx, y, sb)) break;
			continue;
		}
		if (pin_entry(a, row, &e)) break;
		duplicate = e.label[0] && ((!pin_entry(a, row - 1, &o) && !strcmp(o.display, e.display)) ||
		    (!pin_entry(a, row + 1, &o) && !strcmp(o.display, e.display)));
		sel_utf8_to_cp1252(e.display, strlen(e.display), title, sizeof title);
		sel_utf8_to_cp1252(e.label, strlen(e.label), label, sizeof label);
		e.display = title; e.label = label;
		if (row == a->wsel) gfx_fill(g, cx + 5, y, sb - 10, band_h(a, s), ui_rgb(a, UI_STONE));
		/* [i] titles that read alike (eight "OSPF Version 2": RFC 2328 and drafts) carry their label, so that the row
		 * Return opens can be told from its neighbours; the Library's titles were unique */
		show = e.display;
		if (duplicate) {
			/* the title, shortened with "..." where it and its label do not fit, to no less than half the row (a
			 * long draft name is cut at its end instead): "OSPF Version 2 (RFC 2328)", "OSPF Version 2 Ma... (RFC
			 * 4750)", "The Kitchen Sin... (draft-eastla" */
			char lab[200];
			int full = (int)strlen(e.display), dn2 = full, lw, avail = sb - text_x - 6, budget;
			snprintf(lab, sizeof lab, " (%s)", e.label);
			lw = gfx_text_width(g, font, lab, -1);
			if (gfx_text_width(g, font, e.display, dn2) + lw > avail) {
				budget = avail - lw - gfx_text_width(g, font, "...", 3);
				if (budget < avail / 2) budget = avail / 2;
				while (dn2 > 1 && gfx_text_width(g, font, e.display, dn2) > budget) dn2--;
				while (dn2 > 1 && e.display[dn2 - 1] == ' ') dn2--;
			}
			snprintf(t2, sizeof t2, "%.*s%s%s", dn2, e.display, dn2 < full ? "..." : "", lab);
			show = t2;
		}
		n = (int)strlen(show);
		while (n > 1 && gfx_text_width(g, font, show, n) > sb - text_x - 6) n--;
		gfx_text(g, cx + text_x, y + base, font, C_BLACK, show, n);
	}
	gfx_unclip(g);
	/* the scroll column */
	gfx_fill(g, cx + sb, cy, c->w - sb, c->h, C_PAPER);
	if (up) gfx_blit(g, up, 0, 0, up->w / 4, up->h, cx + sb, cy);
	if (dn) {
		gfx_clip(g, cx + sb, cy, c->w - sb, c->h);
		gfx_blit(g, dn, 0, 0, dn->w / 4, dn->h, cx + sb, cy + c->h - dn->h + 1); /* its 17th row lies under the count's box */
		gfx_unclip(g);
	}
	pin_thumb(a, s, c, cy, &ty, &th, &top, &bot);
	if (t && (int)a->nview > pin_wheel_rows(a, s, c)) gfx_blit(g, t, 0, 0, t->w / 4, t->h, cx + sb + 2, ty);
}

static void wheel_drag(e26_app *a, int x, int y)
{
	/* the list's thumb follows the pointer */
	int top = wheel_drag_row(a, a->cur, &a->cur->c[a->drag_ctl], y - a->drag_off);
	(void)x;
	if (top != a->wtop) { a->wtop = top; pin_clamp(a); nav_redraw(a); }
}

static int wheel_press(e26_app *a, scr_screen *s, int i, int x, int y);
static int wheel_release(e26_app *a, scr_screen *s, int i, int x, int y);
static int wheel_wheel(e26_app *a, scr_screen *s, int i, int dir);

static const kind_ops wheel_ops = { draw_wheel, 1, wheel_press, wheel_release, NULL, wheel_wheel, wheel_drag, NULL, NULL };

/* the Pinpointer's list: a row is chosen (and opened on release), the arrows step, the track pages, the thumb drags
 * (as Windows' list boxes and the Library's own article column let it) */
static int wheel_press(e26_app *a, scr_screen *s, int i, int x, int y)
{
	const scr_control *c = &s->c[i];
	int part, row = wheel_hit(a, s, c, x, y, &part);
	if (row >= 0 && row < (int)a->nview) { a->wsel = row; a->pressed = i; }
	else if (part == 5) { a->drag_ops = &wheel_ops; a->drag_ctl = i; a->drag_off = y - wheel_thumb_top(a, s, &s->c[i]); }
	else if (part == 1) a->wtop--;
	else if (part == 2) a->wtop++;
	else if (part == 3) a->wtop -= pin_wheel_rows(a, s, c) - 1;
	else if (part == 4) a->wtop += pin_wheel_rows(a, s, c) - 1;
	pin_clamp(a);
	nav_redraw(a);
	return 1;
}

/* a row released on, the one chosen: it opens */
static int wheel_release(e26_app *a, scr_screen *s, int i, int x, int y)
{
	int part, row = wheel_hit(a, s, &s->c[i], x, y, &part);
	if (row >= 0 && row == a->wsel) {
		if (pin_doc_mode()) {
			static double last_click;
			static int last_row = -1;
			char label[3700], id[1024];
			int level, expander, duplicate, local_x = x - s->ox - s->c[i].x, hot = expander_w(a, s);
			double now = libc_now_ms();
			int twice = last_row == row && now - last_click <= (a->dpy ? XtGetMultiClickTime(a->dpy) : 300);
			if (pin_doc_display(a, row, label, sizeof label, id, sizeof id, &level, &expander, &duplicate)) return 0;
			last_click = twice ? 0 : now;
			last_row = twice ? -1 : row;
			if (expander && (local_x < hot || twice)) {
				(void)pin_doc_expand(a, row, local_x < hot ? expander > 0 : 1);
				last_row = -1;
			} else if (twice) pin_open_entry(a, row);
		} else pin_open_entry(a, row);
		return 1;
	}
	return 0;
}

static int wheel_wheel(e26_app *a, scr_screen *s, int i, int dir)
{
	(void)s; (void)i;
	a->wtop += 3 * dir;
	pin_clamp(a);
	return 1;
}

/* the word-wheel field: white, the typed text in Arial 11 black from 4 px in (capture256 84: "volc" from x 22, the
 * field from 18), the caret after it 2 px wide on rows 2-15 (x 42-43, rows 134-147). A title chosen from the list
 * stands there selected: black on Windows' highlight, #A6CAF0 (the capture lane's colour scheme; capture256 29:
 * "Alaska" on x 22-57, rows 134-147), with no caret; typing replaces it. u14 (M14): on the wide native Pinpointer
 * the field is 24 px high and the text is F_RUI, the caret and highlight as tall as the face, centred. */
#define C_HILITE 0xA6CAF0L
static void draw_edit(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int cx, int cy, int under)
{
	const scr_control *c = &s->c[i];
	int font = pin_row_font(a, s), native = pin_native(a, s);
	const XFontStruct *f = a->font[F_RUI];
	int w = gfx_text_width(g, font, a->edit, -1), pen = cx + (native ? 6 : 4);
	int mark_y = native ? cy + (c->h - f->ascent - f->descent) / 2 : cy + 2, mark_h = native ? f->ascent + f->descent : 14;
	int base = native ? cy + (c->h + f->ascent - f->descent) / 2 : cy + c->h - 5;
	(void)under;
	gfx_fill(g, cx, cy, c->w, c->h, C_WHITE);
	gfx_clip(g, cx, cy, c->w, c->h);
	if (a->edit_sel && a->edit[0]) gfx_fill(g, pen, mark_y, w, mark_h, C_HILITE);
	gfx_text(g, pen, base, font, C_BLACK, a->edit, -1);
	if (!a->edit_sel && a->cur && !strncmp(a->cur->name, "pinpointer", 10)) gfx_fill(g, pen + w, mark_y, 2, mark_h, C_BLACK);
	gfx_unclip(g);
}

static int edit_key(e26_app *a, scr_screen *s, int i, XKeyEvent *ev, KeySym ks)
{
	(void)s; (void)i;
	return pin_key(a, ev, ks);
}

/* the count: a white box, a bar in the control's colour for the share of all titles in the list, and the number
 * right-aligned (capture 83: entry 25 across the box for 26,246 of 26,246; capture 09: a sliver for 577) */
static void draw_count(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int cx, int cy, int under)
{
	const scr_control *c = &s->c[i];
	char num[32];
	uint32_t total = pin_total(a), shown = pin_doc_mode() ? pin_doc_count() : a->nview;
	int k, blocks, font = pin_row_font(a, s);
	const XFontStruct *f = a->font[F_RUI];
	(void)under;
	gfx_fill(g, cx, cy, c->w, c->h, C_WHITE);
	/* [m256] the bar is 32 blocks of 3 px on a 4-px pitch from 2 px in (x 20-146 full on capture 83), 14 rows high
	 * from 2 px down; as many blocks as the share of all titles, one at least while the list holds any */
	blocks = total ? (int)((32LL * shown + total / 2) / total) : 0;
	if (shown && blocks < 1) blocks = 1;
	for (k = 0; k < blocks; k++) gfx_fill(g, cx + 2 + 4 * k, cy + 2, 3, c->h - 3, c->bg >= 0 ? c->bg : ui_rgb(a, 25));
	group_digits((unsigned long)shown, num, sizeof num);
	/* [m256] the digits on rows 5-12 of the box (capture 83: 398-405 in the box from 393); F_RUI centred natively */
	gfx_text(g, cx + c->w - 3 - gfx_text_width(g, font, num, -1),
	    font == F_RUI ? cy + (c->h + f->ascent - f->descent) / 2 : cy + c->h - 4, font, C_BLACK, num, -1);
}

void e26_mod_kind_wheel(void);
void e26_mod_kind_wheel(void)
{
	static const kind_ops edit = { draw_edit, 0, NULL, NULL, NULL, NULL, NULL, edit_key, NULL };
	static const kind_ops count = { draw_count, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL };
	reg_add(REG_KIND, "wheel", &wheel_ops, "kind_wheel", 0);
	reg_add(REG_KIND, "edit", &edit, "kind_wheel", 0);
	reg_add(REG_KIND, "count", &count, "kind_wheel", 0);
}
