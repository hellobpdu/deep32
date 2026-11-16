/* room_main.c: the sources of the shared screens (main.scr: the Main screen and the top bar; dialogs.scr: the notice
 * box): "category" and "subcategory" (the two bars of the top bar), "taxonomy-status", "notice-title" and
 * "notice-text". Core: INTEG owns it after wave 0. */
#include "library_app.h"
#include "appearance.h"
#include "room_finder.h"
#include "room_notemark.h"
#include <stdio.h>
#include <string.h>

/* while the Category menu is open the original's two bars read "Area of Interest" and "Category" (1995 capture 06;
 * string 14550, and the last word of 14700 "Area of Interest         Category"); else his category and subcategory */
static const char *bar_text(e26_app *a, const scr_control *c, char *buf, size_t n, int sub)
{
	if (a->menu && c->action == A_MENU && !strcmp(a->menu->id, c->arg) && a->have_str) {
		const char *o = rec_string(&a->str, !sub ? 14550 : 14700);
		if (o && sub) { const char *sp = strrchr(o, ' '); if (sp) o = sp + 1; }
		if (o) { rec_label(o, buf, n, 0); return buf; }
		return NULL;
	}
	if (!a->have_tax) return NULL;
	if (!sub) snprintf(buf, n, "%s", a->tax.cat[a->cur_cat].bar);
	else {
		const tax_cat *tc = &a->tax.cat[a->cur_cat];
		snprintf(buf, n, "%s", a->cur_sub >= 0 && a->cur_sub < tc->nsub ? tc->sub[a->cur_sub].label : c->label);
	}
	return buf;
}

static const char *src_category(e26_app *a, const scr_control *c, char *buf, size_t n) { return bar_text(a, c, buf, n, 0); }
static const char *src_subcategory(e26_app *a, const scr_control *c, char *buf, size_t n) { return bar_text(a, c, buf, n, 1); }

static const char *src_taxonomy(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	(void)c;
	if (!a->have_tax) return NULL;
	snprintf(buf, n, "Taxonomy: %s (%d categories)", a->tax.status[0] ? a->tax.status : "no status", a->tax.ncat);
	return buf;
}

static const char *src_notice_title(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	(void)c;
	snprintf(buf, n, "%s", a->notice_title);
	return buf;
}

static const char *src_notice_text(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	(void)c;
	snprintf(buf, n, "%s", a->notice_text);
	return buf;
}

enum { MOVE_RIGHT = 1, MOVE_DOWN = 2, GROW_WIDE = 4, GROW_TALL = 8 };
static const struct {
	const char *id;
	int delta;
} main_layout[] = {
	{ "capground", GROW_WIDE }, { "capstrip", GROW_WIDE }, { "captext", GROW_WIDE },
	{ "capmin", MOVE_RIGHT }, { "caprestore", MOVE_RIGHT }, { "capclose", MOVE_RIGHT },
	{ "barground", GROW_WIDE }, { "band", GROW_WIDE },
	{ "column", GROW_TALL }, { "page", GROW_WIDE | GROW_TALL }, { "home", GROW_WIDE | GROW_TALL },
	{ "status", MOVE_DOWN | GROW_WIDE }
};

static struct {
	scr_screen *screen;
	int w, h;
	struct { scr_control *control; int x, y, w, h; } rect[sizeof main_layout / sizeof main_layout[0]];
} main_baseline;

static void layout_main(e26_app *a, const e26_event *ev, void *ud)
{
	scr_screen *main = scr_find(&a->set, "main");
	const scr_screen *base = a->nwin ? a->wstack[0].s : a->cur;
	int w = E26_W, h = E26_H, dx, dy;
	size_t i;
	(void)ev;
	(void)ud;
	if (!main) return;
	if (base == main) present_frame_bounds(a, &w, &h);
	dx = w - E26_W;
	dy = h - E26_H;
	if (main_baseline.screen != main) {
		if (!dx && !dy) return;
		main_baseline.screen = main;
		main_baseline.w = main->w;
		main_baseline.h = main->h;
		for (i = 0; i < sizeof main_layout / sizeof main_layout[0]; i++) {
			scr_control *c = scr_control_find(main, main_layout[i].id);
			main_baseline.rect[i].control = c;
			if (!c) continue;
			main_baseline.rect[i].x = c->x;
			main_baseline.rect[i].y = c->y;
			main_baseline.rect[i].w = c->w;
			main_baseline.rect[i].h = c->h;
		}
	}
	main->w = main_baseline.w + dx;
	main->h = main_baseline.h + dy;
	for (i = 0; i < sizeof main_layout / sizeof main_layout[0]; i++) {
		scr_control *c = main_baseline.rect[i].control;
		int delta = main_layout[i].delta;
		if (!c) continue;
		c->x = main_baseline.rect[i].x + (delta & MOVE_RIGHT ? dx : 0);
		c->y = main_baseline.rect[i].y + (delta & MOVE_DOWN ? dy : 0);
		c->w = main_baseline.rect[i].w + (delta & GROW_WIDE ? dx : 0);
		c->h = main_baseline.rect[i].h + (delta & GROW_TALL ? dy : 0);
	}
	if (!dx && !dy) main_baseline.screen = NULL;
}

/* u14 (spec S8): the Library home on the Main screen's white page, drawn only where the Main screen is native (at
 * 640x480 the page stays the period design's): three columns in the reader UI face, "Continue reading" (the last 8
 * articles, with the section and how far through), "Notemarks" (the last 8) and "Browse the Library" (the Pinpointer
 * on the Entire Library with one Type). Period list styling: bold captions over a 1-px rule in the theme's reading
 * rule colour, the entries as list jumps (the Library's maroon), their details in quiet grey; no cards. */
enum { HOME_RECENT, HOME_NOTES, HOME_BROWSE, HOME_ROWS = 8 };
static const struct { const char *label; int type, sort; } home_browse[] = {
	{ "RFCs by number", FINDER_RFC, 1 }, { "Cisco Configuration Guides", FINDER_CISCO_CONFIG, 0 },
	{ "Cisco White Papers", FINDER_CISCO_WHITE_PAPER, 0 }, { "Cisco Validated Designs", FINDER_CISCO_CVD, 0 },
	{ "Cisco TechNotes", FINDER_TYPES, 0 }, { "Wikipedia", FINDER_WIKI, 0 }
};
#define HOME_NBROWSE ((int)(sizeof home_browse / sizeof home_browse[0]))
static struct { int x, y, w, h, column, index; } home_hit[2 * HOME_ROWS + HOME_NBROWSE];
static int home_nhit, home_pressed = -1;

/* text cut to width with an ellipsis (pin_ellipsis) */
static void home_text(gfx_ctx *g, int x, int y, int width, int font, long ink, const char *s)
{
	char out[400];
	const char *ellipsis = pin_ellipsis(g->a, font);
	int n = (int)strlen(s), dots = gfx_text_width(g, font, ellipsis, -1);
	if (gfx_text_width(g, font, s, n) <= width) { gfx_text(g, x, y, font, ink, s, n); return; }
	while (n > 0 && gfx_text_width(g, font, s, n) > width - dots) n--;
	while (n > 0 && s[n - 1] == ' ') n--;
	snprintf(out, sizeof out, "%.*s%s", n > 380 ? 380 : n, s, ellipsis);
	gfx_text(g, x, y, font, ink, out, -1);
}

static void home_draw(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int x, int y, int under)
{
	static const char *const caption[] = { "Continue reading", "Notemarks", "Browse the Library" };
	static const char *const empty[] = { "Articles you open are listed here.",
	    "Ctrl+D in an article marks your place." };
	const scr_control *c = &s->c[i];
	const XFontStruct *f = a->font[F_RUI];
	int asc, fh, line, entry, gutter = 56, cw, col, k;
	long rule = e26_theme_rule(a->appearance_theme), quiet = PIN_QUIET;
	(void)under;
	home_nhit = 0;
	if (!f || !present_client_viewport(a)) return;
	asc = f->ascent; fh = f->ascent + f->descent; line = fh + 2; entry = 2 * line + 8;
	cw = (c->w - 2 * gutter) / 3;
	if (cw > 520) cw = 520;
	if (cw < 160) return;
	for (col = HOME_RECENT; col <= HOME_BROWSE; col++) {
		int cx = x + col * (cw + gutter), cy = y;
		int list = col == HOME_RECENT ? PLACES_RECENT : PLACES_NOTEMARKS;
		int n = col == HOME_BROWSE ? HOME_NBROWSE : places_count(list);
		gfx_text(g, cx, cy + asc, F_RUI_BOLD, C_BLACK, caption[col], -1);
		gfx_fill(g, cx, cy + fh + 4, cw, 1, rule);
		cy += fh + 14;
		if (!n) { home_text(g, cx, cy + asc, cw, F_RUI, quiet, empty[col]); continue; }
		for (k = 0; k < n && k < (col == HOME_BROWSE ? HOME_NBROWSE : HOME_ROWS); k++) {
			int h = col == HOME_BROWSE ? fh + 6 : entry;
			if (home_nhit < (int)(sizeof home_hit / sizeof home_hit[0])) {
				home_hit[home_nhit].x = cx; home_hit[home_nhit].y = cy; home_hit[home_nhit].w = cw;
				home_hit[home_nhit].h = h; home_hit[home_nhit].column = col; home_hit[home_nhit].index = k;
				home_nhit++;
			}
			if (col == HOME_BROWSE) home_text(g, cx, cy + asc + 3, cw, F_RUI, C_LISTJUMP, home_browse[k].label);
			else {
				const e26_place *p = places_get(list, k);
				char meta[260];
				if (col == HOME_RECENT) snprintf(meta, sizeof meta, "%.200s \xb7 %d%%", p->section, p->percent);
				else snprintf(meta, sizeof meta, "%.200s", p->section);
				home_text(g, cx, cy + asc, cw, F_RUI, C_LISTJUMP, p->title);
				home_text(g, cx, cy + line + asc, cw, F_RUI, quiet, meta);
			}
			cy += h;
		}
	}
}

static int home_at(int x, int y)
{
	int k;
	for (k = 0; k < home_nhit; k++)
		if (x >= home_hit[k].x && x < home_hit[k].x + home_hit[k].w &&
		    y >= home_hit[k].y && y < home_hit[k].y + home_hit[k].h) return k;
	return -1;
}

static int home_press(e26_app *a, scr_screen *s, int i, int x, int y)
{
	(void)s;
	home_pressed = present_client_viewport(a) ? home_at(x, y) : -1;
	if (home_pressed < 0) return 0;
	a->pressed = i;
	return 1;
}

static int home_release(e26_app *a, scr_screen *s, int i, int x, int y)
{
	int k = home_at(x, y), was = home_pressed;
	(void)s; (void)i;
	home_pressed = -1;
	if (k < 0 || k != was) return 0;
	if (home_hit[k].column == HOME_BROWSE)
		pin_browse_type(a, home_browse[home_hit[k].index].type, home_browse[home_hit[k].index].sort);
	else places_open(a, home_hit[k].column == HOME_RECENT ? PLACES_RECENT : PLACES_NOTEMARKS, home_hit[k].index);
	return 1;
}

void e26_mod_room_main(void);
void e26_mod_room_main(void)
{
	static const kind_ops home = { home_draw, 1, home_press, home_release, NULL, NULL, NULL, NULL, NULL };
	static const source_ops cat = { src_category, NULL, NULL }, sub = { src_subcategory, NULL, NULL }, tax = { src_taxonomy, NULL, NULL },
	                        nt = { src_notice_title, NULL, NULL }, nx = { src_notice_text, NULL, NULL };
	reg_add(REG_KIND, "library-home", &home, "room_main", 0);
	reg_add(REG_SOURCE, "category", &cat, "room_main", 0);
	reg_add(REG_SOURCE, "subcategory", &sub, "room_main", 0);
	reg_add(REG_SOURCE, "taxonomy-status", &tax, "room_main", 0);
	reg_add(REG_SOURCE, "notice-title", &nt, "room_main", 0);
	reg_add(REG_SOURCE, "notice-text", &nx, "room_main", 0);
	ev_subscribe(EV_SCREEN_ENTER, layout_main, NULL);
	ev_subscribe(EV_RESIZE, layout_main, NULL);
}
