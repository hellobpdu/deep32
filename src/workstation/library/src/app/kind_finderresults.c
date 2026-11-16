/* kind_finderresults.c: bounded, plain-text result snippets and Past Requests.
 * Geometry is a 1995 list in a synthesised window, pending private golden approval. */
#include "room_finder.h"
#include "edit.h"
#include "select.h"
#include <X11/keysym.h>
#include <stdio.h>
#include <string.h>

/* u14 (spec M14): a result is 46 px in the 1995 window (title, catalogue line, snippet); drawn natively it is two
 * F_RUI lines, 2 x (F_RUI height + 2) + 8 px: the title in F_RUI_BOLD, then the snippet in quiet grey */
#define RESULT_H 46
static int native(const e26_app *a) { return a->font[F_RUI] && present_client_viewport(a); }
static int result_h(const e26_app *a)
{
	return native(a) ? 2 * (a->font[F_RUI]->ascent + a->font[F_RUI]->descent + 2) + 8 : RESULT_H;
}
int finder_results_rows(const e26_app *a, const scr_screen *s)
{
	const scr_control *c = s ? scr_control_find((scr_screen *)s, "results") : NULL;
	return c ? c->h / result_h(a) : 5;
}
static void text(gfx_ctx *g, int x, int y, int width, int font, long ink, const char *utf8)
{
	char out[2048]; int n;
	sel_utf8_to_cp1252(utf8, strlen(utf8), out, sizeof out);
	n = (int)strlen(out);
	while (n && gfx_text_width(g, font, out, n) > width) n--;
	gfx_text(g, x, y, font, ink, out, n);
}
static void results_draw(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int x, int y, int under)
{
	const scr_control *c = &s->c[i]; int k, rh = result_h(a), rows = c->h / rh, big = native(a);
	const XFontStruct *f = a->font[F_RUI];
	(void)under;
	gfx_fill(g, x, y, c->w, c->h, C_WHITE);
	gfx_clip(g, x, y, c->w, c->h);
	for (k = 0; k < rows; k++) {
		int row = a->wtop + k, top = y + k * rh;
		const finder_row *r = finder_row_at(row); char meta[320];
		if (!r) break;
		if (row == a->wsel) gfx_fill(g, x + 1, top, c->w - 2, rh - 1, ui_rgb(a, UI_STONE));
		if (big) {
			int line = f->ascent + f->descent + 2;
			text(g, x + 8, top + 4 + f->ascent, c->w - 32, F_RUI_BOLD, C_BLACK, r->title);
			text(g, x + 8, top + 4 + line + f->ascent, c->w - 32, F_RUI, PIN_QUIET,
			    r->catalogue_only ? "Catalogue entry: article not installed" : r->snippet);
			gfx_line(g, x + 4, top + rh - 1, x + c->w - 20, top + rh - 1, ui_rgb(a, UI_TAUPE));
			continue;
		}
		text(g, x + 6, top + 12, c->w - 28, F_UI_BOLD, C_BLACK, r->title);
		snprintf(meta, sizeof meta, "%.127s%s%.120s | %.30s", r->marking, r->marking[0] ? " | " : "", r->pack, r->cls);
		text(g, x + 6, top + 26, c->w - 28, F_UI10, C_BLACK, meta);
		text(g, x + 6, top + 40, c->w - 28, F_UI, C_BLACK, r->catalogue_only ? "Catalogue entry: article not installed" : r->snippet);
		gfx_line(g, x + 4, top + rh - 1, x + c->w - 20, top + rh - 1, ui_rgb(a, UI_TAUPE));
	}
	gfx_unclip(g);
	/* Simple period scroll arrows. All coordinates and text still go through SHARP. */
	gfx_fill(g, x + c->w - 16, y, 16, c->h, C_PAPER);
	gfx_text(g, x + c->w - 12, y + 12, F_UI, C_BLACK, "^", 1);
	gfx_text(g, x + c->w - 12, y + c->h - 4, F_UI, C_BLACK, "v", 1);
}
static int results_press(e26_app *a, scr_screen *s, int i, int x, int y)
{
	const scr_control *c = &s->c[i]; int cx = c->x + s->ox, cy = c->y + s->oy;
	if (x >= cx + c->w - 16) finder_scroll(a, y < cy + c->h / 2 ? -1 : 1, c->h / result_h(a));
	else {
		int row = a->wtop + (y - cy) / result_h(a);
		if (finder_row_at(row)) { a->wsel = row; a->pressed = i; }
	}
	nav_redraw(a); return 1;
}
static int results_release(e26_app *a, scr_screen *s, int i, int x, int y)
{
	const scr_control *c = &s->c[i]; int row = a->wtop + (y - c->y - s->oy) / result_h(a);
	if (x < c->x + s->ox + c->w - 16 && row == a->wsel) pin_open_entry(a, row);
	return 1;
}
static int results_wheel(e26_app *a, scr_screen *s, int i, int dir) { finder_scroll(a, dir * 3, s->c[i].h / result_h(a)); return 1; }
static int results_key(e26_app *a, scr_screen *s, int i, KeySym ks)
{
	int rows = s->c[i].h / result_h(a), delta = 0;
	if (ks == XK_Return || ks == XK_KP_Enter) { pin_open_entry(a, a->wsel); return 1; }
	if (ks == XK_Up) delta = -1; else if (ks == XK_Down) delta = 1;
	else if (ks == XK_Prior) delta = -rows; else if (ks == XK_Next) delta = rows;
	else if (ks == XK_Home) { a->wsel = 0; finder_scroll(a, -a->wtop, rows); return 1; }
	else if (ks == XK_End) { a->wsel = a->nview ? (int)a->nview - 1 : 0; finder_scroll(a, (int)a->nview - a->wtop, rows); return 1; }
	else return 0;
	a->wsel += delta;
	if (a->wsel < 0) a->wsel = 0;
	if (a->wsel >= (int)a->nview) a->wsel = a->nview ? (int)a->nview - 1 : 0;
	if (a->wsel < a->wtop) finder_scroll(a, a->wsel - a->wtop, rows);
	else if (a->wsel >= a->wtop + rows) finder_scroll(a, a->wsel - a->wtop - rows + 1, rows);
	return 1;
}
static int results_focus_key(e26_app *a, scr_screen *s, int i, XKeyEvent *ev, KeySym ks)
{
	if (ev->state & (ControlMask | Mod1Mask)) return 0;
	return results_key(a, s, i, ks);
}
static void past_draw(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int x, int y, int under)
{
	finder_model *f = finder_st(); const scr_control *c = &s->c[i]; int k;
	(void)under;
	gfx_fill(g, x, y, c->w, c->h, C_WHITE);
	for (k = 0; k < f->npast && (k + 1) * 15 <= c->h; k++) text(g, x + 4, y + 12 + k * 15, c->w - 8, F_UI, C_BLACK, f->past[k]);
}
static int past_press(e26_app *a, scr_screen *s, int i, int x, int y)
{
	finder_model *f = finder_st(); int row = (y - s->c[i].y - s->oy) / 15, k; char cp[512];
	(void)x;
	if (row < 0 || row >= f->npast) return 1;
	snprintf(f->query, sizeof f->query, "%s", f->past[row]);
	sel_utf8_to_cp1252(f->query, strlen(f->query), cp, sizeof cp);
	for (k = 0; k < a->set.ns; k++) {
		(void)field_set_text(&a->set.s[k], "box1", cp);
		(void)field_set_text(&a->set.s[k], "box2", ""); (void)field_set_text(&a->set.s[k], "box3", "");
	}
	nav_close_overlay(a); finder_begin_fields(); finder_search(a, NULL); return 1;
}
void e26_mod_kind_finderresults(void);
void e26_mod_kind_finderresults(void)
{
	static const kind_ops results = { results_draw, 1, results_press, results_release, NULL, results_wheel, NULL, results_focus_key, results_key };
	static const kind_ops past = { past_draw, 1, past_press, NULL, NULL, NULL, NULL, NULL, NULL };
	reg_add(REG_KIND, "finder-results", &results, "kind_finderresults", 0);
	reg_add(REG_KIND, "finder-past", &past, "kind_finderresults", 0);
}
