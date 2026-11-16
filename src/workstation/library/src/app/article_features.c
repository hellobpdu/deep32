/* article_features.c: ARTICLE's views, windows, navigation and source-labelled copy. */
#include "room_article.h"
#include "article_library.h"
#include "article_clipboard.h"
#include "appearance.h"
#include "cite.h"
#include "chrome.h"
#include "edit.h"
#include "finder_model.h"
#include "openat.h"
#include "select.h"
#include "../common/text8.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned long list_epoch;
static int assignment_known;

static void redraw(e26_app *a) { if (a->dpy) nav_redraw(a); }
static void window(e26_app *a, const char *name)
{
	int i;
	for (i = 0; i < a->nwin; i++) if (a->wstack[i].s && !strcmp(a->wstack[i].s->name, name)) {
		(void)nav_focus_at(a, a->wstack[i].s->ox + 3, a->wstack[i].s->oy + 3); return;
	}
	if (nav_push_window(a, name, 0)) nav_notice_text(a, "Article window", "There is no room for another floating window. Close one and try again.");
}

static void set_rect(scr_screen *s, const char *id, int x, int y, int w, int h)
{
	scr_control *c = scr_control_find(s, id);
	if (c) { c->x = x; c->y = y; c->w = w; c->h = h; }
}

int present_article_viewport(const e26_app *a);

static void set_font(scr_screen *s, const char *id, int font, int text_y)
{
	scr_control *c = scr_control_find(s, id);
	if (c) { c->font = font; if (text_y != -2) c->text_y = text_y; }
}

/* u14 M10: the reader's chrome in the native viewport, sized by the reader's interface face (F_RUI: 17 px at UiScale
 * 150): bars, buttons and list rows one unit u = its ascent + descent + 6 (26 px) high, the status strip u + 2, the
 * outline round(0.2 x window width) in [260, 420] (384 at 1920) wide, scaled down below UiScale 150. S15: the Text
 * view (2) has no left column; its reading buttons, Related Articles and Forward sit in the status strip. */
static int native_chrome(e26_app *a, const art_state *st, scr_screen *s, int dx, int dy)
{
	scr_control *c;
	int u, strip, ow, left, tx, tw, sx, ty, th, bar, x, rel_y, rb_y, scale = a->ui_scale > 0 ? a->ui_scale : 100;
	int asc, desc, view2 = st->view == 2, results = pin_have_results();
	if (!st->is_v2 || !a->font[F_RUI] || (st->view != 2 && st->view != 3)) return 0;
	asc = gfx_ascent(&a->g, F_RUI); desc = gfx_descent(&a->g, F_RUI);
	u = asc + desc + 6;
	strip = u + 2;
	bar = u;
	ow = ((E26_W + dx) * 20 * scale / 150 + 50) / 100;
	ow = ow < 260 ? 260 : ow > 420 ? 420 : ow;
	left = view2 ? 0 : ow + 16;
	tx = left;
	tw = s->w - 2 - left;
	sx = tx + tw - 16;
	ty = 65 + bar + 3;                 /* S12: the 3-px progress bar under the section bar */
	th = s->h - strip - 1 - ty;
	rel_y = s->h - strip + (strip - u) / 2;
	set_rect(s, "column", 0, 58, left, 405 + dy);
	set_rect(s, "text", tx, ty, tw, th);
	/* the section bar's row: the marker, the section, Results, Text and Size, then the scroll column */
	x = tx + tw - 22 - 120;
	set_rect(s, "reader-zoom", x, 65, 120, bar);
	set_rect(s, "reader-font", x - 1 - 80, 65, 80, bar);
	x -= 81;
	set_rect(s, "return-results", x - 1 - 92, 65, results ? 92 : 0, results ? bar : 0);
	if (results) x -= 93;
	set_rect(s, "section-menu", left, 65, 22, bar);
	set_rect(s, "section", left + 22, 65, x - 1 - (left + 22), bar);
	set_rect(s, "read-progress", left, 65 + bar, tx + tw - 16 - left, 3);
	set_rect(s, "cap-views", 10, 65, view2 ? 0 : ow - 10, view2 ? 0 : bar);
	set_rect(s, "sb-ground", sx, 58, 16, ty - 58);
	set_rect(s, "sec-prev", sx, 58, 16, 12); set_rect(s, "sec-next", sx, 71, 16, 12);
	set_rect(s, "sb-up", sx, ty, 16, 16);
	set_rect(s, "sb-track", sx, ty + 16, 16, th - 32);
	set_rect(s, "sb-down", sx, ty + th - 16, 16, 16);
	set_rect(s, "media-frame", left, ty, 287, 360);
	if (view2) {
		/* the reading buttons in a row at the strip's left, then Related Articles and Forward; the status after them */
		int k, cw = 0;
		static const char *const labels[] = { "Outline window", "Go to page...", "Further Reading", "More Information" };
		for (k = 0; k < 4; k++) { int w = gfx_text_width(&a->g, F_RUI, labels[k], -1) + 16; if (w > cw) cw = w; }
		set_rect(s, "article-chips", 0, 0, 0, 0);
		set_rect(s, "reading-buttons", 4, rel_y, 4 * cw, u);
		set_rect(s, "btn-related", 4 + 4 * cw + 6, rel_y, 140, u);
		set_rect(s, "article-forward", 4 + 4 * cw + 6 + 144, rel_y, 80, u);
		x = 4 + 4 * cw + 6 + 144 + 86;
		set_rect(s, "outline", 0, 0, 0, 0);
	} else {
		rb_y = rel_y - 6 - 4 * u;
		set_rect(s, "article-chips", 10, 65 + bar + 2, ow - 10, 2 * u - 4);
		set_rect(s, "outline", 0, 65 + bar + 2 + 2 * u - 4 + 3, ow, rb_y - 6 - (65 + bar + 2 + 2 * u - 4 + 3));
		set_rect(s, "reading-buttons", 10, rb_y, ow - 10, 4 * u);
		set_rect(s, "btn-related", 10, rel_y, 140, u);
		set_rect(s, "article-forward", 156, rel_y, 80, u);
		x = left;
	}
	set_rect(s, "article-status", x, s->h - strip, s->w - 2 - x, strip);
	set_rect(s, "article-progress", s->w - 6 - 400, s->h - strip, 400, strip);
	{
		static const char *const bars[] = { "section", "section-menu", "reader-zoom", "reader-font", "return-results", "cap-views",
		                                    "btn-related", "article-forward" };
		size_t k;
		for (k = 0; k < sizeof bars / sizeof bars[0]; k++) set_font(s, bars[k], F_RUI, -2);
		set_font(s, "article-status", F_RUI, (strip + asc - desc) / 2);
		set_font(s, "article-progress", F_RUI, (strip + asc - desc) / 2);
	}
	if ((c = scr_control_find(s, "outline")) != NULL) c->hidden = view2;
	if ((c = scr_control_find(s, "article-chips")) != NULL) c->hidden = view2;
	if ((c = scr_control_find(s, "cap-views")) != NULL) c->hidden = view2;
	if ((c = scr_control_find(s, "read-progress")) != NULL) c->hidden = 0;
	return 1;
}

/* the letterbox's fonts (the 1995 chrome), after the native reader changed them */
static void letterbox_fonts(scr_screen *s)
{
	static const char *const bars[] = { "section", "section-menu", "reader-zoom", "reader-font", "return-results", "cap-views",
	                                    "btn-related", "article-forward" };
	scr_control *c;
	size_t k;
	for (k = 0; k < sizeof bars / sizeof bars[0]; k++) set_font(s, bars[k], F_UI, -2);
	set_font(s, "article-status", F_UI, 13);
	set_font(s, "article-progress", F_UI, 13);
	set_rect(s, "cap-views", 10, 65, 138, 17);
	if ((c = scr_control_find(s, "cap-views")) != NULL) c->hidden = 0;
	set_rect(s, "read-progress", 0, 0, 0, 0);
	if ((c = scr_control_find(s, "read-progress")) != NULL) c->hidden = 1;
}

void art_view_apply(e26_app *a)
{
	art_state *st = art_st();
	scr_screen *s = scr_find(&a->set, "article");
	scr_control *c;
	int picture, outline, left = st->is_v2 ? 164 : 158, tx, tw, sx, section_w, dx = 0, dy = 0, native = 0;
	if (!s) return;
	if (present_article_viewport(a)) {
		dx = a->sc.w - E26_W;
		dy = a->sc.h - E26_H;
		native = 1;
	}
	/* The caption's window buttons follow the edge. The menu buttons retain
	 * their old anchors so the menu records and their hit targets still agree. */
	if (dx || dy || s->w > E26_W || s->h > 463) {
		s->w = E26_W + dx;
		s->h = 463 + dy;
		set_rect(s, "capground", 0, 0, 640 + dx, 16);
		set_rect(s, "capstrip", 1, 1, 638 + dx, 15);
		set_rect(s, "captext", 1, 2, 637 + dx, 13);
		set_rect(s, "capmin", 590 + dx, 2, 13, 13);
		set_rect(s, "caprestore", 605 + dx, 2, 13, 13);
		set_rect(s, "capclose", 623 + dx, 2, 13, 13);
		set_rect(s, "barground", 0, 0, 640 + dx, 19);
		set_rect(s, "band", 0, 19, 640 + dx, 39);
		set_rect(s, "page", 0, 58, 640 + dx, 405 + dy);
	}
	picture = st->is_v2 && (st->view == 0 || st->view == 1);
	outline = !st->is_v2 || st->view == 1 || st->view == 3;
	tx = picture ? left + 287 : left;
	tw = st->is_v2 ? (picture ? 187 : 474 + dx) : 480;
	sx = tx + tw - 16;
	section_w = tx + tw - left;
	set_rect(s, "column", 0, 58, left, 405 + dy);
	set_rect(s, "text", tx, 82, tw, st->is_v2 ? 360 + dy : 381);
	set_rect(s, "section-menu", left, 65, st->is_v2 ? 18 : 0, st->is_v2 ? 17 : 0);
	set_rect(s, "section", left + (st->is_v2 ? 18 : 0), 65, section_w - 21 - (st->is_v2 ? 128 : 0) -
	    (pin_have_results() ? 62 : 0), 17);
	if ((c = scr_control_find(s, "section")) != NULL) {
		c->action = st->is_v2 ? reg_find(REG_ACTION, "article.outline") : A_MENU;
		c->marker = !st->is_v2;
		c->text_x = st->is_v2 ? 1 : 16;
	}
	set_rect(s, "reader-zoom", tx + tw - 78, 65, st->is_v2 ? 56 : 0, st->is_v2 ? 17 : 0);
	set_rect(s, "reader-font", tx + tw - 131, 65, st->is_v2 ? 52 : 0, st->is_v2 ? 17 : 0);
	set_rect(s, "return-results", tx + tw - (st->is_v2 ? 193 : 83), 65,
	    pin_have_results() ? 61 : 0, pin_have_results() ? 17 : 0);
	set_rect(s, "outline", 0, st->is_v2 ? 116 : 86, 148, st->is_v2 ? 239 + dy : 347);
	set_rect(s, "sb-ground", sx, 58, 16, 26);
	set_rect(s, "sec-prev", sx, 58, 16, 12); set_rect(s, "sec-next", sx, 71, 16, 12);
	set_rect(s, "sb-up", sx, 82, 16, 16);
	set_rect(s, "sb-track", sx, 98, 16, st->is_v2 ? 328 + dy : 349);
	set_rect(s, "sb-down", sx, st->is_v2 ? 426 + dy : 447, 16, 16);
	set_rect(s, "btn-related", 10, 435 + dy, st->is_v2 ? 92 : 138, 18);
	set_rect(s, "media-frame", left, 82, 287, 360);
	set_rect(s, "article-chips", 10, 84, 138, 29);
	set_rect(s, "reading-buttons", 10, 358 + dy, 138, 72);
	set_rect(s, "article-status", 164, 443 + dy, 474 + dx, 19);
	set_rect(s, "article-progress", 472 + dx, 443 + dy, 162, 19);
	set_rect(s, "article-forward", 106, 435 + dy, 49, 18);
	if ((c = scr_control_find(s, "outline")) != NULL) c->hidden = !outline;
	if ((c = scr_control_find(s, "media-frame")) != NULL) { c->hidden = !picture; c->x = left; }
	if ((c = scr_control_find(s, "article-chips")) != NULL) c->hidden = !st->is_v2;
	if ((c = scr_control_find(s, "article-status")) != NULL) c->hidden = !st->is_v2;
	if ((c = scr_control_find(s, "article-progress")) != NULL) c->hidden = !st->is_v2;
	if ((c = scr_control_find(s, "article-forward")) != NULL) c->hidden = !st->is_v2;
	if ((c = scr_control_find(s, "reading-buttons")) != NULL) c->hidden = !st->is_v2;
	if (!(native && native_chrome(a, st, s, dx, dy))) letterbox_fonts(s);
	/* a hidden control is not drawn, but the core's hit test (input.c hit_control) still finds it: a hidden control of
	 * this room keeps no area, so a press reaches what is drawn under it (build 2's text, Related Articles) */
	{
		static const char *const own[] = { "outline", "media-frame", "article-chips", "reading-buttons", "article-status", "article-progress", "article-forward",
		                                   "read-progress" };
		size_t k;
		for (k = 0; k < sizeof own / sizeof own[0]; k++) if ((c = scr_control_find(s, own[k])) != NULL && c->hidden) c->w = c->h = 0;
	}
	if ((c = scr_control_find(s, "areabar")) != NULL) snprintf(c->source, sizeof c->source, "%s", st->is_v2 ? "article-area" : "category");
	if ((c = scr_control_find(s, "catfield")) != NULL) snprintf(c->source, sizeof c->source, "%s", st->is_v2 ? "article-category" : "subcategory");
	if (st->is_v2 && assignment_known) {
		if ((c = scr_control_find(s, "areabar")) != NULL) c->bg = a->tax.cat[a->cur_cat].chrome_face;
		if ((c = scr_control_find(s, "catfield")) != NULL) c->bg = a->tax.cat[a->cur_cat].chrome_face;
	}
	if ((c = scr_control_find(s, "column")) != NULL) snprintf(c->source, sizeof c->source, "%s", st->is_v2 ? "article-watermark" : "watermark");
	/* Shared blocks are copied into this room at parse time. Only this room's copy is wired. */
	if ((c = scr_control_find(s, "goback")) != NULL) {
		c->action = st->is_v2 ? reg_find(REG_ACTION, "article.back") : A_BACK;
		c->disabled = st->is_v2 && st->history.at == 0 && a->nhist == 0;
		if (st->is_v2) {
			c->strip[0] = '\0'; c->record = 0;
			c->w = 40; c->font = F_SMALL;
			snprintf(c->label, sizeof c->label, "Go Back");
			/* u14 M10: the reader's interface face where it fits the 1995 button, else the period's own (larger than
			 * F_SMALL's 9 px) */
			if (native && a->font[F_RUI]) c->font = gfx_text_width(&a->g, F_RUI, "Go Back", -1) <= c->w - 4 ? F_RUI :
			                                       a->font[F_UI] && gfx_text_width(&a->g, F_UI, "Go Back", -1) <= c->w - 4 ? F_UI : F_SMALL;
		} else {
			snprintf(c->strip, sizeof c->strip, "ui/bitmap/MAIN_GOBACK");
			c->record = 10051; c->w = 53; c->font = F_UI;
		}
	}
	if ((c = scr_control_find(s, "back-history")) != NULL) {
		c->hidden = !st->is_v2;
		c->w = st->is_v2 ? 13 : 0;
	}
}

void art_view_set(e26_app *a, int view, int save)
{
	art_state *st = art_st();
	char n[8];
	if (view < 0 || view > 3) return;
	st->view = view; art_view_apply(a); art_relayout(a);
	/* Profiles from before Settings keep saving an explicit view until the new option is stored. */
	if (save && (set_stored("SaveWindowLayout") ? set_get_bool("SaveWindowLayout", 0) : set_stored("MainWindowView"))) {
		snprintf(n, sizeof n, "%d", view);
		set_put("MainWindowView", n);
		if (*state_dir()) (void)set_save();
	}
}

int art_position_uri(e26_app *a, char *out, size_t n)
{
	art_state *st = art_st();
	const scr_screen *screen = a->base ? a->base : a->cur;
	const char *pack, *marking, *anchor = "";
	char uri[OPENAT_URI_MAX + 1];
	openat_target target;
	int i, block = -1, length;
	if (!out || !n || !a->have_art || !st->is_v2 || !st->full_id ||
	    !screen || strcmp(screen->name, "article")) return -1;

	/* The default helper can serve several packs. Only a named channel identifies the serving pack. */
	pack = ar_lib_name(st->pack);
	marking = ar_marking(&st->d2);
	if (!strcmp(pack, "lib-reserved")) {
		if (!openat_private_allowed() || !st->d2.cls || strcmp(st->d2.cls, "corpus") ||
		    strcmp(marking, "RESTRICTED")) return -1;
	} else if ((strcmp(pack, "lib-e26") && strcmp(pack, "lib-corpus")) || *marking) return -1;
	if (ar_lib_resolve(pack) < 0) return -1;

	for (i = 0; i < st->L.nen; i++)
		if (st->L.en[i].kind == LE_HEADING && st->L.en[i].y <= a->scroll) block = st->L.en[i].block;
	if (block >= 0 && block < st->d2.nb && st->d2.b[block].kind == A2_HEADING && st->d2.b[block].anchor)
		anchor = st->d2.b[block].anchor;
	length = snprintf(uri, sizeof uri, "library:%s/%s%s%s", pack, st->full_id, *anchor ? "#" : "", anchor);
	if (length < 0 || (size_t)length >= sizeof uri || (size_t)length >= n ||
	    openat_parse(uri, &target) || strcmp(target.article, st->full_id)) return -1;
	memcpy(out, uri, (size_t)length + 1);
	return 0;
}

void art_features_controls(e26_app *a)
{
	art_state *st = art_st();
	scr_screen *s = scr_find(&a->set, "article");
	scr_control *c;
	if (!s) return;
	if ((c = scr_control_find(s, "section-menu")) != NULL) {
		c->hidden = !st->is_v2;
		if (c->hidden) c->w = c->h = 0;
	}
	if ((c = scr_control_find(s, "reader-zoom")) != NULL) {
		c->hidden = !st->is_v2;
		if (c->hidden) c->w = c->h = 0;
		/* u14 M10: the native reader's Size button names the Text size in effect */
		if (st->is_v2 && present_article_viewport(a) && c->w >= 100) snprintf(c->label, sizeof c->label, "Size %d%%", a->reader_zoom);
		else snprintf(c->label, sizeof c->label, "Zoom");
	}
	if ((c = scr_control_find(s, "reader-font")) != NULL) {
		c->hidden = !st->is_v2;
		if (c->hidden) c->w = c->h = 0;
	}
	if ((c = scr_control_find(s, "return-results")) != NULL) {
		c->hidden = !pin_have_results();
		if (c->hidden) c->w = c->h = 0;
	}
	if ((c = scr_control_find(s, "article-forward")) != NULL) c->disabled = !ar_history_step(&st->history, 1);
	if (st->is_v2 && (c = scr_control_find(s, "goback")) != NULL) c->disabled = st->history.at == 0 && a->nhist == 0;
	if ((c = scr_control_find(s, "back-history")) != NULL)
		c->disabled = !ar_history_step(&st->history, -1);
	if ((c = scr_control_find(s, "article-status")) != NULL)
		c->action = st->resumed_until ? reg_find(REG_ACTION, "article.start-from-top") : A_NONE;
}

/* u14 S17: the section menu (record 12075) ends with "Copy with attribution" (Copy is the selection with a one-line
 * citation); appended once, after the record's items, so their keyboard mnemonics keep their places */
static void attribution_item(e26_app *a)
{
	int i, k, action = reg_find(REG_ACTION, "article.copy");
	for (i = 0; i < a->set.nm; i++) {
		scr_menu *m = &a->set.m[i];
		scr_menu_item *it;
		if (strcmp(m->id, "article-text") || !m->nit || m->nit >= SCR_MAX_MENU_ITEMS || action < 0) continue;
		for (k = 0; k < m->nit; k++) if (!strcmp(m->it[k].arg, "attribution")) return;
		it = &m->it[m->nit++];
		memset(it, 0, sizeof *it);
		it->kind = MI_TEXT;
		it->action = action;
		snprintf(it->arg, sizeof it->arg, "attribution");
		snprintf(it->label, sizeof it->label, "Copy with attribution");
		/* the record's width cuts the label: the menu as wide as it needs (margin 10 each side, as menu_fit) */
		if (a->font[F_UI]) {
			int w = gfx_text_width(&a->g, F_UI, it->label, -1) + 2 * 10 + 8;
			if (m->width < w) m->width = w;
		}
	}
}

void art_features_opened(e26_app *a, int remember)
{
	attribution_item(a);
	art_state *st = art_st();
	int i, j;
	list_epoch++; assignment_known = 0;
	if (remember && st->full_id) (void)ar_history_push(&st->history, st->full_id, st->pack, a->scroll, st->view);
	if (st->is_v2 && a->have_tax) {
		int found = 0;
		for (i = 0; i < a->tax.ncat && !found; i++) {
			if (!strcmp(a->tax.cat[i].id, st->d2.primary_category)) { a->cur_cat = i; a->cur_sub = -1; found = 1; }
			for (j = 0; j < a->tax.cat[i].nsub && !found; j++) if (!strcmp(a->tax.cat[i].sub[j].id, st->d2.primary_category)) {
				a->cur_cat = i; a->cur_sub = j; found = 1;
			}
		}
		assignment_known = found;
		if (!found) { a->cur_cat = 0; a->cur_sub = -1; }
	}
	art_view_apply(a); art_features_controls(a);
}

int art_marking_inset(e26_app *a, const scr_control *c, const char *mark)
{
	int width, h, lines = 0, start, end, len = (int)strlen(mark);
	if (!*mark || !a->font[F_SMALL]) return 0;
	width = c->w - 24;
	if (width < 1) width = 1;
	h = gfx_font_height(&a->g, F_SMALL) + 1;
	for (start = 0; start < len; start = end) {
		for (end = start + 1; end < len && gfx_text_width(&a->g, F_SMALL, mark + start, end - start + 1) <= width; end++) ;
		lines++;
	}
	return lines * h + 4;
}

int art_banner_inset(e26_app *a, const scr_control *c)
{
	return art_marking_inset(a, c, art_st()->is_v2 ? ar_marking(&art_st()->d2) : "");
}

void art_marking_draw(e26_app *a, gfx_ctx *g, const scr_control *c, int x, int y, const char *mark)
{
	int h = art_marking_inset(a, c, mark), k, start, end, len = (int)strlen(mark), width = c->w - 24;
	if (!h) return;
	/* Character-based wrapping preserves every marking byte, including a word wider than this narrow view. */
	for (k = 0; k < 2; k++) {
		int by = y + (k ? c->h - h : 0), row = 0;
		gfx_fill(g, x, by, c->w - 16, h, ui_rgb(a, UI_STONE));
		gfx_clip(g, x, by, c->w - 16, h);
		for (start = 0; start < len; start = end) {
			for (end = start + 1; end < len && gfx_text_width(g, F_SMALL, mark + start, end - start + 1) <= width; end++) ;
			gfx_text(g, x + 4, by + 2 + gfx_ascent(g, F_SMALL) + row * (gfx_font_height(g, F_SMALL) + 1), F_SMALL, C_BLACK, mark + start, end - start);
			row++;
		}
		gfx_unclip(g);
	}
}

void art_draw_banners(e26_app *a, gfx_ctx *g, const scr_control *c, int x, int y)
{
	art_marking_draw(a, g, c, x, y, art_st()->is_v2 ? ar_marking(&art_st()->d2) : "");
}

int art_page_jump(e26_app *a, int page)
{
	art_state *st = art_st();
	int y = ar_page_y(&st->L, &st->d2, page);
	if (y < 0) { nav_notice_text(a, "Go to page", "This source page is not present in the reader edition."); return -1; }
	art_history_capture(a);
	if (st->full_id) (void)ar_history_push(&st->history, st->full_id, st->pack, y, st->view);
	art_scroll_to(a, y); redraw(a); return 0;
}

int art_page_click(e26_app *a, int pos)
{
	art_state *st = art_st();
	int p = pos < 0 || !ar_has_pages(&st->L) ? 0 : ar_page_reference(st->L.pt.s, st->L.pt.n, (size_t)pos);
	if (!p) return 0;
	(void)art_page_jump(a, p); return 1;
}

static int history_move(e26_app *a, int dir)
{
	art_state *st = art_st();
	const ar_place *p = ar_history_step(&st->history, dir);
	int at = st->history.at + dir;
	if (!p) { if (dir < 0) { art_fetch_cancel(); st->busy = 0; nav_back(a); } return 0; }
	art_history_capture(a);
	st->resumed_until = 0;
	if (st->full_id && !strcmp(st->full_id, p->id) && st->pack == p->pack) {
		art_fetch_cancel(); st->busy = 0; st->history.at = at;
		if (st->view != p->view) art_view_set(a, p->view, 0);
		art_history_position(a, p); redraw(a); return 0;
	}
	if (p->pack < 0) {
		(void)art_restore_local(a, p, at); redraw(a); return 0;
	}
	art_fetch_start(a, p->pack, p->id, NULL, at, p->scroll, p->view);
	return 1;
}

static int back(e26_app *a, const e26_actx *x) { (void)x; return history_move(a, -1); }
static int forward(e26_app *a, const e26_actx *x) { (void)x; return history_move(a, 1); }

/* u14 M13: an article id people can read when the Library gives no title: rfc1092 -> "RFC 1092",
 * draft:draft-x-07 -> "draft-x-07 (Internet-Draft)", wiki:Foo_Bar -> "Foo Bar" (and a Cisco id's hash prefix left out) */
void art_humanise_id(const char *id, char *out, size_t n)
{
	const char *colon = strchr(id, ':'), *rest = colon ? colon + 1 : id;
	size_t i, k, hex;
	if (!n) return;
	if (!strncmp(id, "rfc", 3) && id[3] >= '0' && id[3] <= '9') {
		const char *d = id + 3;
		while (*d == '0' && d[1] >= '0' && d[1] <= '9') d++;
		snprintf(out, n, "RFC %s", d);
		return;
	}
	if (!strncmp(id, "draft:", 6)) { snprintf(out, n, "%s (Internet-Draft)", id + 6); return; }
	for (hex = 0; (rest[hex] >= '0' && rest[hex] <= '9') || (rest[hex] >= 'a' && rest[hex] <= 'f'); hex++) ;
	if (hex == 16 && rest[16] == '-' && rest[17]) rest += 17;
	for (i = k = 0; rest[i] && k + 1 < n; i++) out[k++] = rest[i] == '_' ? ' ' : rest[i];
	out[k] = '\0';
}

/* a place's "Title -- Section" (cp1252): the em dash where the face has it; a public article without a captured title
 * by its readable id */
static void place_label(e26_app *a, const ar_place *p, char *out, size_t n)
{
	const char *pack = p->pack < 0 ? "lib-e26" : ar_lib_name(p->pack);
	const XFontStruct *f = a->font[F_UI];
	int dash = !f || (f->min_char_or_byte2 <= 0x97 && f->max_char_or_byte2 >= 0x97 &&
	                  (!f->per_char || f->per_char[0x97 - f->min_char_or_byte2].width > 0));
	char title[64];
	if (p->title[0]) snprintf(title, sizeof title, "%s", p->title);
	else if (p->id && (!strcmp(pack, "lib-e26") || !strcmp(pack, "lib-corpus"))) art_humanise_id(p->id, title, sizeof title);
	else snprintf(title, sizeof title, "Article");
	snprintf(out, n, "%.60s %s %.40s", title, dash ? "\x97" : "-", p->section[0] ? p->section : "Saved place");
}

void art_history_items(e26_app *a, scr_menu *m)
{
	const ar_history *history = &art_st()->history;
	int i;
	m->nit = 0;
	for (i = 1; i <= 10 && i <= history->at; i++) {
		int at = history->at - i;
		const ar_place *p = &history->p[at];
		scr_menu_item *item = &m->it[m->nit++];
		size_t n;
		memset(item, 0, sizeof *item);
		item->kind = MI_TEXT;
		item->action = reg_find(REG_ACTION, "article.history-pick");
		snprintf(item->arg, sizeof item->arg, "%d", at);
		place_label(a, p, item->label, sizeof item->label);
		n = strlen(item->label);
		while (a->font[F_UI] && n > 1 &&
		    gfx_text_width(&a->g, F_UI, item->label, (int)n) > m->width - 20)
			item->label[--n] = '\0';
	}
}

static int history_pick(e26_app *a, const e26_actx *x)
{
	const ar_history *history = &art_st()->history;
	char *end;
	long at;
	if (!x->arg || !x->arg[0]) return 0;
	at = strtol(x->arg, &end, 10);
	if (*end || at < 0 || at >= history->at || history->at - at > 10) return 0;
	return history_move(a, (int)at - history->at);
}

static int history_menu(e26_app *a, const e26_actx *x)
{
	(void)x;
	if (!ar_history_step(&art_st()->history, -1)) return 0;
	menus_open_by_key(a, "article-history");
	return 1;
}

static void panel_clear(void);

/* u14 M18: Tools > Back List: this session's places, newest first, "Title -- Section", each row going back there */
#define BACKLIST_MAX 30
static int backlist_open(e26_app *a, const e26_actx *x)
{
	art_state *st = art_st();
	const ar_history *h = &st->history;
	e26j_buf b;
	int i, rows = 0;
	(void)x;
	if (a->have_art) art_history_capture(a);
	panel_clear();
	e26j_init(&b);
	st->panel_ids = (char **)calloc(BACKLIST_MAX, sizeof *st->panel_ids);
	for (i = h->n - 1; st->panel_ids && i >= 0 && rows < BACKLIST_MAX; i--) {
		char label[128], utf[400], id[24];
		place_label(a, &h->p[i], label, sizeof label);
		sel_cp1252_to_utf8(label, strlen(label), utf, sizeof utf);
		if (i == h->at) e26j_lit(&b, "\x01");   /* where the reader is: the group style (bold), still a place to go */
		e26j_lit(&b, utf); e26j_lit(&b, "\n");
		snprintf(id, sizeof id, "@place:%d", i);
		st->panel_ids[rows++] = strdup(id);
	}
	if (!rows) e26j_lit(&b, "No places yet: the articles you read in this session are listed here, newest first.");
	st->panel_text = e26j_take(&b);
	st->panel_rows = rows;
	if (!st->panel_text) { panel_clear(); st->panel_text = strdup("The Back List could not be made."); }
	art_panel_publish("article-backlist");
	window(a, "article-backlist");
	return 0;
}

int art_place_open(e26_app *a, int at)
{
	art_state *st = art_st();
	const ar_place *p;
	int k, same;
	if (at < 0 || at >= st->history.n) return 0;
	p = &st->history.p[at];
	for (k = 0; k < E26_WSTACK && a->nwin > 1; k++) nav_close_overlay(a);
	same = a->have_art && st->full_id && !strcmp(st->full_id, p->id) && st->pack == p->pack;
	if (same && (!a->cur || strcmp(a->cur->name, "article"))) nav_go(a, "article", 1);
	(void)history_move(a, at - st->history.at);
	if (a->have_art && a->cur && strcmp(a->cur->name, "article") && st->full_id && !strcmp(st->full_id, p->id))
		nav_go(a, "article", 1);
	return 1;
}

static int font_menu(e26_app *a, const e26_actx *x)
{
	(void)x;
	if (!art_st()->is_v2) return 0;
	menus_open_by_key(a, "article-font");
	return 1;
}

static int start_from_top(e26_app *a, const e26_actx *x)
{
	(void)x;
	art_st()->resumed_until = 0;
	art_scroll_to(a, 0);
	art_save_position(a);
	art_features_controls(a);
	return 1;
}

static int reader_help(e26_app *a, const e26_actx *x)
{
	(void)x;
	/* u14 M12 (the notice holds 299 bytes) */
	nav_notice_text(a, "Reading keys",
	    "Space/Shift+Space, PgDn/PgUp: page. Home/End. Alt+Up/Down: sections. "
	    "Backspace, Alt+Left, mouse 8: Back. Alt+Right, mouse 9: Forward. Ctrl+Shift+B: places. "
	    "Ctrl+F, F3: find. Ctrl+O: outline. Ctrl+=/-, Ctrl+wheel: text size; Ctrl+0: default. "
	    "Ctrl+Shift+T: Text menu.");
	return 0;
}
static int view_tp(e26_app *a, const e26_actx *x) { (void)x; art_view_set(a, 0, 1); return 1; }
static int view_top(e26_app *a, const e26_actx *x) { (void)x; art_view_set(a, 1, 1); return 1; }
static int view_t(e26_app *a, const e26_actx *x) { (void)x; art_view_set(a, 2, 1); return 1; }
static int view_to(e26_app *a, const e26_actx *x) { (void)x; art_view_set(a, 3, 1); return 1; }
static int outline(e26_app *a, const e26_actx *x) { (void)x; window(a, "article-outline"); return 0; }

/* build 2's article cite, as INPUT (mod_input.c article_cite) builds it */
static void legacy_cite(e26_app *a, cite_src *c, char *num, size_t nn, char *sec, size_t ns, char *anchor, size_t na, char *lines, size_t nl)
{
	const char *p;
	int i;
	memset(c, 0, sizeof *c);
	c->cls = CITE_LIBRARY;
	c->kind = CITE_OTHER;
	c->id = a->art.id;
	c->title = a->art.title;
	c->licence = a->art.licence[0] ? a->art.licence : NULL;
	c->block = 1;
	num[0] = sec[0] = anchor[0] = lines[0] = 0;
	if (!strncmp(a->art.id, "rfc", 3) && a->art.id[3] >= '0' && a->art.id[3] <= '9') {
		c->kind = CITE_RFC;
		snprintf(num, nn, "%d", atoi(a->art.id + 3));
		c->number = num;
	}
	for (i = 0; i < a->art.nb; i++)
		if (a->art.b[i].kind == B_HEADING && a->art.b[i].number[0]) {
			snprintf(sec, ns, "section %s, %s", a->art.b[i].number, a->art.b[i].text);
			snprintf(anchor, na, "section-%s", a->art.b[i].number);
			c->section = sec;
			c->anchor = anchor;
			break;
		}
	if ((p = strstr(a->art.source, " lines "))) {
		const char *f = a->art.source, *slash;
		for (slash = f; slash < p; slash++) if (*slash == '/') f = slash + 1;
		snprintf(lines, nl, "%s of %.*s", p + 1, (int)(p - f), f);
		c->lines = lines;
	}
}

/* a short note in the status strip for four seconds (no dialog: a dialog would take the reader to the letterbox) */
static char note[200];   /* art_state.message */
static void note_expired(e26_app *a, void *ud)
{
	art_state *st = art_st();
	(void)ud;
	if (!note[0] || strcmp(st->message, note)) return;
	st->message[0] = note[0] = '\0';
	if (a->dpy) nav_redraw(a);
}

void art_status_note(e26_app *a, const char *text)
{
	art_state *st = art_st();
	if (st->busy) return;
	snprintf(st->message, sizeof st->message, "%s", text);
	snprintf(note, sizeof note, "%s", text);
	if (a->ctx) timer_after(a, 4000, note_expired, NULL);
}

static int select_all(e26_app *a, const e26_actx *x)
{
	art_state *st = art_st();
	(void)x;
	if (!a->have_art || !st->L.pt.n) return 0;
	st->sel_a = 0;
	st->sel_b = (int)st->L.pt.n;
	return 1;
}

int art_copy_action(e26_app *a, const e26_actx *x)
{
	art_state *st = art_st();
	char *selection, *out;
	sel_prov p;
	(void)x;
	if (!st->is_v2) {
		/* build 2's article, as INPUT copied it before article.copy existed: an RFC cites its number, first
		 * numbered section and source lines; CLIPBOARD and PRIMARY carry D32_PROVENANCE with the source SHA-256 */
		cite_src c;
		char num[16], sec[200], anchor[40], lines[200];
		size_t cap = st->L.pt.n + 4096;
		int n = -1;
		legacy_cite(a, &c, num, sizeof num, sec, sizeof sec, anchor, sizeof anchor, lines, sizeof lines);
		if ((out = (char *)malloc(cap)) != NULL) n = cite_build(&c, st->L.pt.s ? st->L.pt.s : "", out, cap);
		if (n > 0) {
			memset(&p, 0, sizeof p);
			snprintf(p.cls, sizeof p.cls, "%s", cite_class_name(c.cls));
			snprintf(p.key, sizeof p.key, "%s", a->art.id);
			snprintf(p.version, sizeof p.version, "%s%.64s", a->art.source_sha256[0] ? "sha256:" : "-", a->art.source_sha256);
			(void)sel_own(a, SEL_CLIPBOARD, out, (size_t)n, &p);
			(void)sel_own(a, SEL_PRIMARY, out, (size_t)n, &p);
		}
		free(out); return 0;
	}
	/* u14 S17: no selection copies nothing (Ctrl+A selects the whole article); "Copy with attribution" (the section
	 * menu, argument "attribution") adds the full attribution and licence */
	selection = art_selection(a);
	if (!selection) {
		art_status_note(a, "Select text to copy (Ctrl+A selects all).");
		return 1;
	}
	if (x && x->arg && !strcmp(x->arg, "attribution")) out = ar_copy_attributed(&st->d2, selection, strlen(selection));
	else {
		int e = lay_current_entry(&st->L, a->scroll);
		char section[400] = "";
		if (e >= 0 && st->L.en[e].text) sel_cp1252_to_utf8(st->L.en[e].text, strlen(st->L.en[e].text), section, sizeof section);
		out = ar_copy_at(&st->d2, section, selection, strlen(selection));
	}
	free(selection);
	memset(&p, 0, sizeof p);
	snprintf(p.cls, sizeof p.cls, "%s", st->d2.cls);
	if (strlen(st->d2.id) < sizeof p.key) snprintf(p.key, sizeof p.key, "%s", st->d2.id);
	snprintf(p.version, sizeof p.version, "sha256:%.64s", st->d2.content_sha256);
	if (!out || ar_clipboard(a, out, p.key[0] ? &p : NULL)) nav_notice_text(a, "Copy", "The article could not be copied. Try a smaller selection.");
	free(out); return 0;
}

static int copy_url(e26_app *a, const e26_actx *x)
{
	art_state *st = art_st();
	(void)x;
	if (st->is_v2) (void)ar_clipboard(a, st->d2.source_url, NULL);
	return 1;
}

static int go_page(e26_app *a, const e26_actx *x)
{
	(void)x; window(a, "article-page"); (void)field_set_text(a->cur, "page-number", ""); return 0;
}

static int page_submit(e26_app *a, const e26_actx *x)
{
	const char *s = a->cur ? field_text(a->cur, "page-number") : NULL;
	char *end;
	long n;
	/* A field action's argument is its UI label. The entered page takes precedence. */
	if (!s) s = x->arg;
	n = s ? strtol(s, &end, 10) : 0;
	if (!s || !*s || *end || n < 1 || n > 1000000) { nav_notice_text(a, "Go to page", "Enter a source page number from 1 to 1000000."); return 0; }
	nav_close_overlay(a); (void)art_page_jump(a, (int)n); return 0;
}

/* The listing windows retain row ids separately from labels. Only an available target gets an id. */
static void panel_clear(void)
{
	art_state *st = art_st();
	int i;
	free(st->panel_text); st->panel_text = NULL;
	for (i = 0; i < st->panel_rows; i++) free(st->panel_ids[i]);
	free(st->panel_ids); st->panel_ids = NULL; st->panel_rows = st->panel_top = 0;
}

typedef struct {
	e26_app *a;
	char *id;
	int pack, kind, start;
	unsigned long epoch;
	e26j_buf text;
	char **ids;
	int n;
	char group[32];       /* u14 M13: the reference kind whose heading row was last written */
} listing;
static void list_ask(listing *q);

static void list_free(listing *q)
{
	int i;
	for (i = 0; i < q->n; i++) free(q->ids[i]);
	free(q->ids); free(q->id); e26j_free(&q->text); free(q);
}

static void plain_runs(const jv *v, e26j_buf *b)
{
	size_t i;
	if (!v) return;
	if (v->type == J_STR) { e26j_lit(b, v->str); return; }
	if (v->type == J_ARR) { for (i = 0; i < v->n; i++) plain_runs(v->items[i], b); return; }
	if (v->type == J_OBJ) {
		const char *text = json_str(v, "text");
		if (text) e26j_lit(b, text);
		else { const jv *r = json_get(v, "runs"); if (!r) r = json_get(v, "label"); plain_runs(r, b); }
	}
}

static void list_received(const lib_reply *r, void *ud)
{
	listing *q = (listing *)ud;
	art_state *st = art_st();
	const jv *rows = json_get(r->result, "rows"), *next = json_get(r->result, "next");
	uint64_t to;
	size_t i;
	if (q->epoch != list_epoch || !st->full_id || strcmp(st->full_id, q->id) || st->pack != q->pack) { list_free(q); return; }
	if (!r->ok || !rows || rows->type != J_ARR || !next) {
		e26_app *a = q->a; list_free(q); nav_notice_text(a, "Article list", r->message ? r->message : "The Library returned an invalid list."); return;
	}
	for (i = 0; i < rows->n; i++) {
		const jv *row = rows->items[i], *available = json_get(row, "available"), *target = json_get(row, "target");
		const char *id = json_str(row, "target_id"), *kind = json_str(row, "kind");
		char **ids = (char **)realloc(q->ids, (size_t)(q->n + 2) * sizeof *ids);
		size_t before;
		if (!ids || q->n >= 65536 || q->text.n > 8u * 1024u * 1024u) { if (ids) q->ids = ids; q->text.bad = 1; break; }
		q->ids = ids;
		/* u14 M13: one heading row (bold, no target) where the reference kind changes, then one row per entry */
		if (q->kind && kind && strcmp(q->group, kind)) {
			static const char *const groups[][2] = { { "normative", "Normative references" },
				{ "informative", "Informative references" }, { "further-reading", "Further reading" } };
			const char *title = kind;
			size_t g;
			for (g = 0; g < sizeof groups / sizeof groups[0]; g++) if (!strcmp(kind, groups[g][0])) title = groups[g][1];
			snprintf(q->group, sizeof q->group, "%s", kind);
			e26j_lit(&q->text, "\x01"); e26j_lit(&q->text, title); e26j_lit(&q->text, "\n");
			q->ids[q->n++] = NULL;
		}
		q->ids[q->n++] = available && available->type == J_BOOL && available->b && id ? strdup(id) : NULL;
		if (q->ids[q->n - 1] && target) {
			const char *anchor = json_str(target, "anchor"), *section = json_str(target, "section");
			char rfc_anchor[128];
			if (!anchor && section && strlen(section) + 2 < sizeof rfc_anchor) {
				size_t z;
				snprintf(rfc_anchor, sizeof rfc_anchor, "s%s", section);
				for (z = 0; rfc_anchor[z]; z++) if (rfc_anchor[z] >= 'A' && rfc_anchor[z] <= 'Z') rfc_anchor[z] = (char)(rfc_anchor[z] - 'A' + 'a');
				anchor = rfc_anchor;
			}
			if (anchor) {
				size_t size = strlen(id) + strlen(anchor) + 2;
				char *jump = (char *)malloc(size);
				if (jump) { snprintf(jump, size, "%s#%s", id, anchor); free(q->ids[q->n - 1]); q->ids[q->n - 1] = jump; }
			}
		}
		if (!q->ids[q->n - 1]) e26j_lit(&q->text, "\x02");   /* not in the Library: quiet, no target */
		before = q->text.n;
		/* an RFC by its number first ("RFC 1105: Border Gateway Protocol (BGP)": several RFCs share a title) */
		if (!q->kind && id && !strncmp(id, "rfc", 3) && id[3] >= '0' && id[3] <= '9' && json_get(row, "label") &&
		    json_get(row, "label")->type != J_NULL) {
			char number[32];
			art_humanise_id(id, number, sizeof number);
			e26j_lit(&q->text, number); e26j_lit(&q->text, ": ");
			plain_runs(json_get(row, "label"), &q->text);
			if (q->text.n == before + strlen(number) + 2) q->text.n -= 2;   /* an empty title: the number alone */
		} else if (json_get(row, "label")) plain_runs(json_get(row, "label"), &q->text);
		if (q->text.n == before && id) {
			char readable[300];
			art_humanise_id(id, readable, sizeof readable);
			e26j_lit(&q->text, readable);
		}
		if (!id && target && json_str(target, "url")) { e26j_lit(&q->text, " "); e26j_lit(&q->text, json_str(target, "url")); }
		else if (!q->ids[q->n - 1]) e26j_lit(&q->text, "  (not in Library)");
		if (!q->text.bad) {
			size_t z;   /* one entry is one row: no line breaks or row marks inside it */
			for (z = before; z < q->text.n; z++) if ((unsigned char)q->text.p[z] < 0x20) q->text.p[z] = ' ';
		}
		e26j_lit(&q->text, "\n");
		if (q->text.n > 8u * 1024u * 1024u) { q->text.bad = 1; break; }
	}
	if (!q->text.bad && next->type != J_NULL && !e26j_u64(next, &to) && to > (uint64_t)q->start && to <= 65536) {
		q->start = (int)to; list_ask(q); return;
	}
	if (next->type != J_NULL) q->text.bad = 1;
	if (!q->n) e26j_lit(&q->text, "No entries in this article's Library pack.");
	panel_clear();
	st->panel_text = e26j_take(&q->text); st->panel_ids = q->ids; st->panel_rows = q->n; q->ids = NULL; q->n = 0;
	if (!st->panel_text) {
		panel_clear(); st->panel_text = strdup("This list is too large to display. Refine it in the Library.");
	}
	art_panel_publish(q->kind ? "article-further" : "article-related");
	redraw(q->a); list_free(q);
}

static void list_ask(listing *q)
{
	e26j_buf b;
	char *args;
	uint64_t request;
	e26j_init(&b); e26j_lit(&b, "{\"id\":"); e26j_cstr(&b, q->id);
	e26j_lit(&b, ",\"start\":"); e26j_i(&b, q->start); e26j_lit(&b, ",\"limit\":128}");
	args = e26j_take(&b);
	request = args ? ar_lib_request(q->a, q->pack, q->kind ? "references" : "related", args, list_received, q) : 0;
	free(args);
	if (!request) { e26_app *a = q->a; list_free(q); nav_notice_text(a, "Article list", "The Library list request could not start."); }
}

static int list_open(e26_app *a, int kind)
{
	art_state *st = art_st();
	listing *q;
	panel_clear();
	st->panel_text = strdup("Reading the article's Library list...");
	art_panel_publish(kind ? "article-further" : "article-related");
	window(a, kind ? "article-further" : "article-related");
	if (!st->is_v2 || st->pack < 0) {
		free(st->panel_text); st->panel_text = strdup("This local fixture has no Library connection. Run the pack scenario for linked lists.");
		art_panel_publish(kind ? "article-further" : "article-related"); redraw(a); return 0;
	}
	q = (listing *)calloc(1, sizeof *q);
	if (!q) return 0;
	q->a = a; q->id = strdup(st->full_id); q->kind = kind; q->pack = st->pack; q->epoch = list_epoch;
	if (!q->id) { list_free(q); return 0; }
	list_ask(q); return 0;
}

static int related(e26_app *a, const e26_actx *x) { (void)x; return list_open(a, 0); }
static int further(e26_app *a, const e26_actx *x) { (void)x; return list_open(a, 1); }

/* Dictionary uses the same named query whether opened from a glossary term or a double-click. */
static void dictionary_received(const lib_reply *r, void *ud)
{
	listing *q = (listing *)ud;
	art_state *st = art_st();
	const jv *rows = json_get(r->result, "rows");
	size_t i;
	if (q->epoch != list_epoch) { list_free(q); return; }
	if (r->ok && rows && rows->type == J_ARR) {
		for (i = 0; i < rows->n; i++) {
			const char *term = json_str(rows->items[i], "term"), *cls = json_str(rows->items[i], "class");
			e26j_lit(&q->text, term ? term : "Term"); e26j_lit(&q->text, "\n");
			plain_runs(json_get(rows->items[i], "definition"), &q->text);
			e26j_lit(&q->text, cls && !strcmp(cls, "library") ? "\nDeep32 Encyclopedia text (not evidence).\n\n" : "\nCorpus glossary entry.\n\n");
		}
		if (!rows->n) e26j_lit(&q->text, "No glossary entry for this word.");
	} else e26j_lit(&q->text, r->message ? r->message : "The glossary is unavailable.");
	panel_clear(); st->panel_text = e26j_take(&q->text); art_panel_publish("article-dictionary"); redraw(q->a); list_free(q);
}

void art_dictionary(e26_app *a, const char *term)
{
	art_state *st = art_st();
	e26j_buf b;
	listing *q;
	char *args;
	uint64_t request;
	int id = reg_find(REG_ACTION, "dictionary.lookup");
	if (id >= 0 && action_built(id)) {
		e26_actx x; memset(&x, 0, sizeof x); x.ctl = -1; x.arg = term; x.label = term;
		if (action_run(a, id, &x)) redraw(a);
		return;
	}
	panel_clear(); st->panel_text = strdup("Looking up the word..."); art_panel_publish("article-dictionary"); window(a, "article-dictionary");
	if (st->pack < 0) {
		int i;
		for (i = 0; i < st->L.nlk; i++) if (st->L.lk[i].kind == LK_GLOSSARY && !strcmp(st->L.lk[i].name, term)) {
			free(st->panel_text); st->panel_text = strdup(st->L.lk[i].definition); art_panel_publish("article-dictionary"); redraw(a); return;
		}
		free(st->panel_text); st->panel_text = strdup("No glossary entry in this local fixture."); art_panel_publish("article-dictionary"); redraw(a); return;
	}
	q = (listing *)calloc(1, sizeof *q); if (!q) return;
	q->a = a; q->epoch = list_epoch;
	e26j_init(&b); e26j_lit(&b, "{\"term\":"); e26j_cstr(&b, term); e26j_lit(&b, ",\"limit\":32}"); args = e26j_take(&b);
	request = args ? ar_lib_request(a, st->pack, "dictionary", args, dictionary_received, q) : 0;
	free(args);
	if (!request) { list_free(q); nav_notice_text(a, "Dictionary", "The glossary request could not start."); }
}

static int attribution(e26_app *a, const e26_actx *x)
{
	art_state *st = art_st();
	(void)x;
	panel_clear();
	if (st->is_v2) st->panel_text = ar_copy_attributed(&st->d2, "Source and attribution", strlen("Source and attribution"));
	else st->panel_text = strdup("The legacy reader's attribution is in its source article.");
	art_panel_publish("article-attribution");
	window(a, "article-attribution"); return 0;
}

static int moreinfo(e26_app *a, const e26_actx *x)
{
	int id = reg_find(REG_ACTION, "room.moreinfo");
	(void)x;
	if (id >= 0 && action_built(id)) { e26_actx c; memset(&c, 0, sizeof c); c.ctl = -1; c.arg = art_st()->full_id; return action_run(a, id, &c); }
	nav_notice_text(a, "More Information", "The tabbed More Information room has not merged. Further Reading, Related Articles and source attribution are available in this article.");
	return 0;
}

static int print_article(e26_app *a, const e26_actx *x)
{
	int id = reg_find(REG_ACTION, "research.print-article");
	if (id >= 0 && action_built(id)) return action_run(a, id, x);
	nav_notice_text(a, "Print Article", "Printing and PDF export need the Research room. Copy with cite is available here."); return 0;
}

static const char *status(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	art_state *st = art_st();
	int e = lay_current_entry(&st->L, a->scroll), i, chapter = -1;
	int title_limit = c->w < 540 ? 21 : 28;
	if (st->busy || st->message[0]) { snprintf(buf, n, "%s", st->message); return buf; }
	if (st->resumed_until) {
		while (e >= 0 && st->L.en[e].kind != LE_HEADING) e--;
		snprintf(buf, n, "Resumed at %.40s  \xb7  Start from top", e < 0 ? "saved place" : st->L.en[e].text);
		return buf;
	}
	for (i = 0; i <= e; i++) if (st->L.en[i].kind == LE_HEADING && st->L.en[i].level == 1) chapter = i;
	{
		/* the source page only where the source has pages (page anchors or form feeds); PACK-CORPUS 6: most corpus
		 * documents have none yet */
		char page[24] = "";
		const char *source = st->is_v2 && !strcmp(st->d2.cls, "corpus") ? "RAG" : "Library";
		if (ar_has_pages(&st->L)) snprintf(page, sizeof page, "Page %d | ", ar_current_page(&st->L, &st->d2, a->scroll));
		if (chapter < 0 || e == chapter)
			snprintf(buf, n, "%s%.*s | %s", page, 2 * title_limit, e >= 0 ? st->L.en[e].text : "Article", source);
		else snprintf(buf, n, "%s%.*s | %.*s | %s", page, title_limit, st->L.en[chapter].text,
		         title_limit, st->L.en[e].text, source);
	}
	return buf;
}

static const char *progress(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	const art_state *st = art_st();
	int range = a->doc_h - a->view_h, section, percent, total, i;
	long minutes;
	char where[48] = "";
	(void)c;
	if (!a->have_art || !st->is_v2 || st->busy || st->message[0] || st->L.oom) return "";
	/* Estimate at 200 words/minute using the fraction of the scroll range remaining; u14 M11: only under two hours */
	if (range <= 0) {
		if (!st->d2.word_count || (st->d2.word_count + 199) / 200 >= 120) return "";
		snprintf(buf, n, "~%ld min read", (st->d2.word_count + 199) / 200);
		return buf;
	}
	percent = a->scroll >= range ? 100 : (int)(100LL * a->scroll / range);
	/* u14 M11: "Section 12 of 86 . 38%", counting the level-1 and level-2 headings (at the end: the last one, which
	 * can be visible below the top line) */
	for (total = section = 0, i = 0; i < st->L.nen; i++) {
		const lay_entry *e = &st->L.en[i];
		if (e->kind != LE_HEADING || e->level > 2) continue;
		total++;
		if (a->scroll >= range || e->y <= a->scroll + 6) section = total;
	}
	if (total) /* above the first counted heading (front matter) the reader is in the first section */
		snprintf(where, sizeof where, "Section %d of %d \xb7 ", section ? section : 1, total);
	minutes = (st->d2.word_count * (100 - percent) + 19999) / 20000;
	if (minutes > 0 && minutes < 120) snprintf(buf, n, "%s%d%% \xb7 ~%ld min left", where, percent, minutes);
	else snprintf(buf, n, "%s%d%%", where, percent);
	return buf;
}

static const char *panel(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	(void)a; (void)c;
	if (!art_st()->panel_text) return "No entries.";
	t8_to_8bit(art_st()->panel_text, strlen(art_st()->panel_text), 0, buf, n); return buf;
}

static const char *marking(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	(void)a; (void)c; (void)buf; (void)n;
	return art_st()->is_v2 ? ar_marking(&art_st()->d2) : "";
}

/* 1 when the URL's host is host or one of its subdomains */
static int url_host(const char *url, const char *host)
{
	const char *h = url ? strstr(url, "://") : NULL, *end;
	size_t n, hn = strlen(host);
	if (!h) return 0;
	h += 3;
	for (end = h; *end && *end != '/' && *end != ':' && *end != '?'; end++) ;
	n = (size_t)(end - h);
	return n >= hn && !strncmp(end - hn, host, hn) && (n == hn || end[-(long)hn - 1] == '.');
}

/* u14 S13: what the document is, for the header when the taxonomy has no category for it ("RFC", "Configuration
 * Guides"): the Library's document type, else the source's (Cisco documents by their title's kind) */
static const char *doc_type(const art_state *st)
{
	const a2_doc *d = &st->d2;
	const char *t = d->title ? d->title : "", *url = d->source_url ? d->source_url : "";
	int k = finder_type_index(d->source_kind);
	if (k != FINDER_UNCLASSIFIED && k != FINDER_CISCO) return finder_type_label(k);
	if (k == FINDER_CISCO || url_host(url, "cisco.com") || url_host(url, "pubhub.devnetcloud.com")) {
		if (strstr(t, "Configuration Guide")) return "Configuration Guides";
		if (strstr(t, "Command Reference")) return "Command References";
		if (strstr(t, "White Paper") || strstr(t, "white paper")) return "White Papers";
		if (strstr(t, "Design Guide") || strstr(t, "Validated Design")) return "Validated Designs";
		return "Cisco documentation";
	}
	if (url_host(url, "rfc-editor.org") || url_host(url, "ietf.org")) return "RFC";
	if (url_host(url, "nist.gov")) return "NIST";
	return d->cls && !strcmp(d->cls, "corpus") ? "RAG corpus" : "Library";
}

static const char *area(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	(void)c;
	snprintf(buf, n, "%s", assignment_known ? a->tax.cat[a->cur_cat].bar : art_st()->is_v2 ? doc_type(art_st()) : "Library");
	return buf;
}

static const char *category(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	(void)c;
	snprintf(buf, n, "%s", assignment_known && a->cur_sub >= 0 ? a->tax.cat[a->cur_cat].sub[a->cur_sub].label : art_st()->d2.primary_category);
	return buf;
}

static void watermark(e26_app *a, gfx_ctx *g, const scr_control *c, int x, int y)
{
	const source_ops *s = (const source_ops *)reg_impl(REG_SOURCE, reg_find(REG_SOURCE, "watermark"));
	if (assignment_known && !set_get_bool("NoWatermark", 0) && s && s->decorate) s->decorate(a, g, c, x, y);
}

/* u14 S12: the native reader's 3-px reading-progress bar under the section bar: the part read in the theme's rule
 * colour on the stone of the bars */
static void readbar(e26_app *a, gfx_ctx *g, const scr_control *c, int x, int y)
{
	const art_state *st = art_st();
	int range = a->doc_h - a->view_h, w;
	if (!a->have_art || !st->is_v2 || c->w <= 0 || c->h <= 0) return;
	gfx_fill(g, x, y, c->w, c->h, ui_rgb(a, UI_STONE));
	w = range <= 0 ? c->w : (int)((long long)c->w * (a->scroll < range ? a->scroll : range) / range);
	if (w > 0) gfx_fill(g, x, y, w, c->h, e26_theme_rule(a->appearance_theme));
}

static int page_up(e26_app *a, const e26_actx *x) { (void)x; art_scroll_by(a, -a->view_h + 2 * a->line); return 1; }
static int page_down(e26_app *a, const e26_actx *x) { (void)x; art_scroll_by(a, a->view_h - 2 * a->line); return 1; }
static int home(e26_app *a, const e26_actx *x) { (void)x; art_scroll_to(a, 0); return 1; }
static int end(e26_app *a, const e26_actx *x) { (void)x; art_scroll_to(a, a->doc_h); return 1; }

#define E26_STR_(value) #value
#define E26_STR(value) E26_STR_(value)
void art_features_register(void)
{
	static const struct { const char *name; e26_action action; } actions[] = {
		{ "article.view-tp", { view_tp } }, { "article.view-top", { view_top } }, { "article.view-t", { view_t } }, { "article.view-to", { view_to } },
		{ "article.back", { back } }, { "article.forward", { forward } }, { "article.outline", { outline } }, { "article.related", { related } },
		{ "article.history-pick", { history_pick } }, { "article.history-menu", { history_menu } },
		{ "article.font-menu", { font_menu } },
		{ "article.start-from-top", { start_from_top } }, { "article.key-help", { reader_help } },
		{ "article.further", { further } }, { "article.moreinfo", { moreinfo } }, { "article.attribution", { attribution } },
		{ "article.copy", { art_copy_action } }, { "article.copy-url", { copy_url } }, { "article.print", { print_article } },
		{ "article.go-page", { go_page } }, { "article.page-submit", { page_submit } },
		{ "article.page-up", { page_up } }, { "article.page-down", { page_down } }, { "article.home", { home } }, { "article.end", { end } },
		/* u14: M18 the Back List (main.scr's Tools item names room.backlist), S17 Select All */
		{ "backlist.open", { backlist_open } }, { "room.backlist", { backlist_open } }, { "article.select-all", { select_all } }
	};
	static const source_ops stat = { status, NULL, NULL }, reading = { progress, NULL, NULL }, list = { panel, NULL, NULL };
	static const source_ops bar = { NULL, NULL, readbar };
	static const source_ops ar = { area, NULL, NULL }, cat = { category, NULL, NULL }, wm = { NULL, NULL, watermark }, mark = { marking, NULL, NULL };
	static const e26_setting view = { "MainWindowView", NULL, SET_INT, E26_STR(ART_VIEW_DEFAULT) };
	size_t i;
	for (i = 0; i < sizeof actions / sizeof actions[0]; i++) reg_add(REG_ACTION, actions[i].name, &actions[i].action, "room_article", 0);
	reg_add(REG_SOURCE, "article-status", &stat, "room_article", 0);
	reg_add(REG_SOURCE, "article-progress", &reading, "room_article", 0);
	reg_add(REG_SOURCE, "article-panel", &list, "room_article", 0);
	reg_add(REG_SOURCE, "article-area", &ar, "room_article", 0);
	reg_add(REG_SOURCE, "article-category", &cat, "room_article", 0);
	reg_add(REG_SOURCE, "article-watermark", &wm, "room_article", 0);
	reg_add(REG_SOURCE, "article-marking", &mark, "room_article", 0);
	reg_add(REG_SOURCE, "article-readbar", &bar, "room_article", 0);
	reg_add(REG_SETTING, view.key, &view, "room_article", 0);
}
