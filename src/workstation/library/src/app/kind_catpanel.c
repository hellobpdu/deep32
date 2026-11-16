/* kind_catpanel.c: kind catpanel: the Pinpointer's Category filter (captures 135, 136) with his 20 areas, in the Library's
 * own rhythm (build 2's review round; build 2 drew them on 12-px rows with no rules in a 171-px column). The area
 * column is the Library's, 122 px from the panel's left (x 248-369), its items from row 150: an area with a one-line name
 * 21 rows, a two-line name 34 (his TAXONOMY.json labels, two lines as the Library's "Physical Science / & Technology"), an
 * entry-13 rule after every item but the last (x 258-359 on capture 135), the names from x 260 with their baselines
 * 13 and 27 below an item's top, the chosen area on an entry-13 band 2 rows inside its item (136: History, rows
 * 231-247 of 229-249) that runs on into the category column. the Library's nine fill the column's 251 rows; his twenty
 * need 648, so [i] the column scrolls, by whole areas, with the Library's own scroll column (MAINUP, MTHUMB, MAINDN, as
 * the titles list) at its right (x 354-369), and the rules and the band stop short of it. For a chosen area its
 * categories stand on entry 13 under an "All Categories" row, the chosen row on an entry-28 band in white (capture
 * 136). W2-FINDER owns this file in wave 2a. */
#include "library_app.h"
#include <stdio.h>
#include <string.h>

#define CP_AREA_W 122
#define CP_TOP 30
#define CP_CAT_H 15
#define CP_LIST_H 251 /* rows 150-400 */
#define CP_SB_W 16

static int area_h(const e26_app *a, int i) { return strchr(a->tax.cat[i].label, '\n') ? 34 : 21; }

/* the last area that can stand at the top of the column (the ones after it then fill it), 0 when all fit */
static int max_top(const e26_app *a)
{
	int i, h = 0;
	if (!a->have_tax) return 0;
	for (i = a->tax.ncat - 1; i >= 0; i--) {
		h += area_h(a, i) + (i < a->tax.ncat - 1 ? 1 : 0);
		if (h > CP_LIST_H) return i + 1;
	}
	return 0;
}

/* the area at the top of the column moves so that area cat is in view */
void catpanel_reveal(e26_app *a, int cat)
{
	int max = max_top(a), h, i;
	if (cat < 0 || !a->have_tax || cat >= a->tax.ncat) return;
	if (cat < a->area_top) a->area_top = cat;
	for (;;) {
		for (h = 0, i = a->area_top; i <= cat; i++) h += area_h(a, i) + 1;
		if (h - 1 <= CP_LIST_H || a->area_top >= cat) break;
		a->area_top++;
	}
	if (a->area_top > max) a->area_top = max;
	if (a->area_top < 0) a->area_top = 0;
}

static void area_thumb(e26_app *a, int cy, int *ty, int *th, int *top, int *bot)
{
	xasset *up = xa_get(a, "ui/bitmap/MAINUP"), *t = xa_get(a, "ui/bitmap/MTHUMB");
	int h = t ? t->h : 14, max = max_top(a);
	*top = cy + CP_TOP + (up ? up->h + 1 : 18);
	*bot = cy + CP_TOP + CP_LIST_H - (up ? up->h : 17) - h;
	*th = h;
	*ty = max > 0 ? *top + (*bot - *top) * a->area_top / max : *top;
}

/* the area column's scroll column under the pointer: 0 none, 1 up, 2 down, 3 above the thumb, 4 below it, 5 the thumb */
static int scroll_hit(e26_app *a, const scr_control *c, int x, int y)
{
	int cx = c->x + a->cur->ox, cy = c->y + a->cur->oy, sx = cx + CP_AREA_W - CP_SB_W, ty, th, top, bot;
	if (!a->have_tax || max_top(a) <= 0 || x < sx || x >= sx + CP_SB_W || y < cy + CP_TOP || y >= cy + CP_TOP + CP_LIST_H) return 0;
	area_thumb(a, cy, &ty, &th, &top, &bot);
	if (y < cy + CP_TOP + 17) return 1;
	if (y >= cy + CP_TOP + CP_LIST_H - 16) return 2;
	if (y >= ty && y < ty + th) return 5;
	return y < ty ? 3 : 4;
}

/* the area at the top for the thumb's top at frame row y (the thumb dragged) */
static int drag_top(e26_app *a, const scr_control *c, int y)
{
	int cy = c->y + a->cur->oy, ty, th, top, bot, max = max_top(a);
	area_thumb(a, cy, &ty, &th, &top, &bot);
	if (max <= 0 || bot <= top) return 0;
	if (y < top) y = top;
	if (y > bot) y = bot;
	return ((y - top) * max + (bot - top) / 2) / (bot - top);
}

static int thumb_top(e26_app *a, const scr_control *c)
{
	int ty, th, top, bot;
	area_thumb(a, c->y + a->cur->oy, &ty, &th, &top, &bot);
	return ty;
}

static int panel_hit(e26_app *a, const scr_control *c, int x, int y, int *cat, int *sub)
{
	int cx = c->x + a->cur->ox, cy = c->y + a->cur->oy;
	*cat = *sub = -2;
	if (!a->have_tax || x < cx || y < cy + CP_TOP || x >= cx + c->w || y >= cy + c->h) return 0;
	if (x < cx + CP_AREA_W) {
		int i, top = cy + CP_TOP;
		if ((max_top(a) > 0 && x >= cx + CP_AREA_W - CP_SB_W) || y >= cy + CP_TOP + CP_LIST_H) return 0;
		for (i = a->area_top; i < a->tax.ncat; i++) {
			int h = area_h(a, i) + 1;
			if (y < top + h) { *cat = i; return 1; }
			top += h;
		}
		return 0;
	}
	if (a->pan_hot < 0) return 0;
	if (y < cy + CP_TOP + 18) { *cat = a->pan_hot; *sub = -1; return 1; }
	{
		int k = (y - cy - CP_TOP - 18) / CP_CAT_H;
		if (k < a->tax.cat[a->pan_hot].nsub) { *cat = a->pan_hot; *sub = k; return 1; }
	}
	return 0;
}

static void draw(e26_app *a, gfx_ctx *g, scr_screen *s, int ci, int cx, int cy, int under)
{
	const scr_control *c = &s->c[ci];
	int i;
	const char *head = a->have_str ? rec_string(&a->str, 14700) : NULL;
	char h1[64] = "Area of Interest", h2[64] = "Category";
	int colx, colw;
	(void)under;
	if (head) { /* "Area of Interest         Category" */
		const char *sp = strstr(head, "  ");
		if (sp) {
			snprintf(h1, sizeof h1, "%.*s", (int)(sp - head), head);
			while (*sp == ' ') sp++;
			snprintf(h2, sizeof h2, "%s", sp);
		}
	}
	/* [m256] capture 135, 136 (frame coordinates, the panel at (248,120)): the two headings in Arial Bold 13 px
	 * (GetFont 105), capitals on rows 130-139, "Area of Interest" from x 258 and "Category" 22 px into the category
	 * column; the black rule on row 148 from x 258 to 595; the category column in entry 13 (entry 14 with no area
	 * chosen) under the rule, rows 149-400, to x 595; "All Categories" and the chosen category on a band inset 10 px
	 * at either side, the lettering 15 px in, "All Categories" on rows 150-167 with its baseline on 163, then 15-px
	 * rows with their baselines 13 below a row's top (181, 196, ...) */
	colx = cx + CP_AREA_W;
	colw = c->w - 10 - CP_AREA_W + 1;
	gfx_fill(g, cx, cy, c->w, c->h, C_PAPER);
	gfx_text(g, cx + 10, cy + 20, F_UI_BOLD13, C_BLACK, h1, -1);
	gfx_text(g, colx + 21, cy + 20, F_UI_BOLD13, C_BLACK, h2, -1);
	gfx_line(g, cx + 10, cy + CP_TOP - 2, cx + c->w - 10, cy + CP_TOP - 2, C_BLACK);
	/* with no area chosen the empty column is entry 14 one pixel smaller all round (capture 135: x 371-594, rows
	 * 150-399; with History chosen, 136: entry 13, x 370-595, rows 149-400) */
	if (a->pan_hot >= 0) gfx_fill(g, colx, cy + CP_TOP - 1, colw, c->h - CP_TOP - 3, ui_rgb(a, UI_TAUPE));
	else gfx_fill(g, colx + 1, cy + CP_TOP, colw - 2, c->h - CP_TOP - 5, ui_rgb(a, UI_STONE));
	if (!a->have_tax) return;
	{
		int sb = max_top(a) > 0, sx = cx + CP_AREA_W - CP_SB_W, y = cy + CP_TOP;
		int rule_r = sb ? sx - 2 : cx + 111, band_r = sb ? sx - 1 : cx + CP_AREA_W - 1;
		gfx_clip(g, cx, cy + CP_TOP, CP_AREA_W, CP_LIST_H);
		for (i = a->area_top; i < a->tax.ncat && y < cy + CP_TOP + CP_LIST_H; i++) {
			int h = area_h(a, i);
			char l[TAX_TEXT], *br;
			snprintf(l, sizeof l, "%s", a->tax.cat[i].label);
			br = strchr(l, '\n');
			if (br) *br = 0;
			if (i == a->pan_hot) gfx_fill(g, cx + 10, y + 2, band_r - (cx + 10) + 1, h - 4, ui_rgb(a, UI_TAUPE));
			gfx_text(g, cx + 12, y + 13, F_UI, C_BLACK, l, -1);
			if (br) gfx_text(g, cx + 12, y + 27, F_UI, C_BLACK, br + 1, -1);
			if (i < a->tax.ncat - 1) gfx_line(g, cx + 10, y + h, rule_r, y + h, ui_rgb(a, UI_TAUPE));
			y += h + 1;
		}
		gfx_unclip(g);
		if (sb) {
			/* the Library's own scroll column, as the titles list's: MAINUP and MAINDN frame 0 at its ends, MTHUMB between */
			xasset *up = xa_get(a, "ui/bitmap/MAINUP"), *dn = xa_get(a, "ui/bitmap/MAINDN"), *t = xa_get(a, "ui/bitmap/MTHUMB");
			int ty, th, top, bot;
			gfx_fill(g, sx, cy + CP_TOP, CP_SB_W, CP_LIST_H, C_PAPER);
			if (up) gfx_blit(g, up, 0, 0, up->w / 4, up->h, sx, cy + CP_TOP);
			if (dn) gfx_blit(g, dn, 0, 0, dn->w / 4, dn->h, sx, cy + CP_TOP + CP_LIST_H - dn->h);
			area_thumb(a, cy, &ty, &th, &top, &bot);
			if (t) gfx_blit(g, t, 0, 0, t->w / 4, t->h, sx + 2, ty);
		}
	}
	if (a->pan_hot >= 0) {
		const tax_cat *tc = &a->tax.cat[a->pan_hot];
		int y = cy + CP_TOP, all = a->flt_cat == a->pan_hot && a->flt_sub < 0;
		gfx_clip(g, colx, y, colw, c->h - CP_TOP);
		if (all) gfx_fill(g, colx + 10, y, colw - 20, 18, ui_rgb(a, UI_CHARCOAL));
		gfx_text(g, colx + 15, y + 13, F_UI, all ? C_WHITE : C_BLACK, "All Categories", -1);
		for (i = 0; i < tc->nsub; i++) {
			int yy = y + 18 + i * CP_CAT_H, on = a->flt_cat == a->pan_hot && a->flt_sub == i;
			if (on) gfx_fill(g, colx + 10, yy, colw - 20, CP_CAT_H, ui_rgb(a, UI_CHARCOAL));
			gfx_text(g, colx + 15, yy + 13, F_UI, on ? C_WHITE : C_BLACK, tc->sub[i].label, -1);
		}
		gfx_unclip(g);
	}
}

static void drag(e26_app *a, int x, int y)
{
	/* the area column, its thumb dragged */
	int top = drag_top(a, &a->cur->c[a->drag_ctl], y - a->drag_off);
	(void)x;
	if (top != a->area_top) { a->area_top = top; nav_redraw(a); }
}

static int press(e26_app *a, scr_screen *s, int i, int x, int y);
static int wheel(e26_app *a, scr_screen *s, int i, int dir);

static const kind_ops ops = { draw, 1, press, NULL, NULL, wheel, drag, NULL, NULL };

/* the area column's scroll column: the arrows step an area, the track pages three, the thumb drags; an area or a
 * category chosen filters the list */
static int press(e26_app *a, scr_screen *s, int i, int x, int y)
{
	int cat, sub, part = scroll_hit(a, &s->c[i], x, y), max = max_top(a);
	if (part) {
		if (part == 5) { a->drag_ops = &ops; a->drag_ctl = i; a->drag_off = y - thumb_top(a, &s->c[i]); }
		else a->area_top += part == 1 ? -1 : part == 2 ? 1 : part == 3 ? -3 : 3;
		if (a->area_top > max) a->area_top = max;
		if (a->area_top < 0) a->area_top = 0;
	} else if (panel_hit(a, &s->c[i], x, y, &cat, &sub)) {
		if (sub == -2) { a->pan_hot = cat; pin_filter(a, cat, -1); }
		else pin_filter(a, cat, sub);
	}
	nav_redraw(a);
	return 1;
}

/* the mouse wheel over the panel: its area column moves an area a notch */
static int wheel(e26_app *a, scr_screen *s, int i, int dir)
{
	int max = max_top(a);
	(void)s; (void)i;
	a->area_top += dir;
	if (a->area_top > max) a->area_top = max;
	if (a->area_top < 0) a->area_top = 0;
	return 1;
}

void e26_mod_kind_catpanel(void);
void e26_mod_kind_catpanel(void)
{
	reg_add(REG_KIND, "catpanel", &ops, "kind_catpanel", 0);
}
