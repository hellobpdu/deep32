/* kind_outline.c: the article's outline (W1-LAYOUT; W2-ARTICLE builds the floating Outline window on it). The kind
 * "outline" draws:
 *   the entries  every heading, and figures, tables and CLI exhibits in the list-jump colour under their heading, as
 *                the 1995 outline pane (capture 35): 17-px rows from 3 px below the pane's top, the baseline 12 below a
 *                row's top, level 1 at the pane's left edge and each level 7 px further in, the current section on a
 *                15-px band of entry 14 the width of the pane, black in the regular face (media entries entry 19); the
 *                entry under the pointer keeps its colour (capture256 93). The pane starts "prop pane-x" px into the
 *                control. It scrolls: the wheel and its scroll bar move it, and (99 touch, OutlineFollowsReading,
 *                default on) it keeps the current section in view as the article scrolls.
 *   its bar      "prop bar <x> <y> <w> <h>" inside the control (capture 35: frame x 1-16, rows 104-448): GREYUP at the
 *                top and GREYDOWN at the bottom (16x17; frame 0 at rest, 1 pressed), the track entry 14 between, the
 *                thumb GREYTHUM frame 0 (12x14) 2 px in and 1 px clear of the track's ends; nothing when every entry
 *                fits. The arrows step a row and the track a page, both repeating while held (timer.c); the thumb
 *                drags.
 *   its tip      an entry cut off in the pane shows whole while the pointer is on it (capture256 93): a 16-px box
 *                from 1 px left of the pane, a 1-px frame of entry 14 on entry 31, the text where the pane draws it,
 *                5 px clear on the right. The article kind draws it last (outline_draw_tip), over the text beside it. */
#include "room_article.h"
#include "appearance.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* u14 M10: in the native reader the outline is a readable table of contents: the reader's interface face (F_RUI,
 * 17 px at UiScale 150), rows of its ascent + descent + 6 (26 px), 14 px per level, the current row on UI_STONE with
 * a 3-px bar in the theme's rule colour and its text bold. The 640x480 letterbox keeps the 1995 pane above. */
static struct {
	int native, row, top, base, step, font, tip;
} OM = { 0, E26_OUTLINE_ROW, E26_OUTLINE_TOP, E26_OUTLINE_BASE, E26_OUTLINE_STEP, F_UI, 16 };

static void om_update(e26_app *a)
{
	OM.native = a && a->font[F_RUI] && a->font[F_RUI_BOLD] && present_article_viewport(a);
	if (OM.native) {
		int asc = gfx_ascent(&a->g, F_RUI), desc = gfx_descent(&a->g, F_RUI);
		OM.row = asc + desc + 6;
		OM.top = 3;
		OM.base = (OM.row + asc - desc) / 2;
		OM.step = (OM.row * 14 + 13) / 26;
		OM.font = F_RUI;
		OM.tip = OM.row - 1;
	} else {
		OM.row = E26_OUTLINE_ROW; OM.top = E26_OUTLINE_TOP; OM.base = E26_OUTLINE_BASE; OM.step = E26_OUTLINE_STEP;
		OM.font = F_UI; OM.tip = 16;
	}
}

#define ROW OM.row
#define TOP OM.top
#define ARROW 17
#define THUMB 14
/* the down arrow shows 16 of its 17 rows: the bar ends on its 16th (capture 35: glyph rows 438-444, the bar's last row 448) */
#define DOWN (ARROW - 1)
#define TRAVEL(gm) ((gm)->bh - ARROW - DOWN - 2 - THUMB)

typedef struct {
	int px, pw;           /* the pane: x offset in the control and width */
	int bx, by, bw, bh;   /* the bar inside the control (bw 0: none) */
} geom;

static void geom_of(const scr_control *c, geom *g)
{
	const char *p = scr_prop_get(c, "pane-x"), *b = scr_prop_get(c, "bar");
	memset(g, 0, sizeof *g);
	g->px = p ? atoi(p) : 0;
	g->pw = c->w - g->px;
	if (b && sscanf(b, "%d %d %d %d", &g->bx, &g->by, &g->bw, &g->bh) != 4) g->bw = 0;
	if (g->bh > c->h - g->by || (OM.native && g->bw > 0)) g->bh = c->h - g->by; /* native: the pane's own height */
}

static int rows_in(const scr_control *c) { return (c->h - TOP) / ROW; }
static int entries(void) { return art_st()->tree.n; }
static int entry_at(int row) { return row >= 0 && row < art_st()->tree.n ? art_st()->tree.visible[row] : -1; }

static void clamp_top(const scr_control *c)
{
	art_state *st = art_st();
	int max = entries() - rows_in(c);
	if (st->out_top > max) st->out_top = max;
	if (st->out_top < 0) st->out_top = 0;
}

/* an entry's text fitted to the pane: the characters that fit, "..." at the end when cut; returns 1 when cut */
static int fit(gfx_ctx *g, const char *text, int room, char *t, size_t tn, int *n)
{
	int len;
	snprintf(t, tn, "%s", text);
	len = (int)strlen(t);
	*n = len;
	while (*n > 3 && gfx_text_width(g, OM.font, t, *n) > room) (*n)--;
	if (*n < len && *n > 3) { t[*n - 3] = '.'; t[*n - 2] = '.'; t[*n - 1] = '.'; return 1; }
	return 0;
}

static int indent(const lay_entry *e) { return (e->level > 1 ? e->level - 1 : 0) * OM.step; }
/* the +/- box's room before a heading's text */
static int toggle_w(void) { return OM.native ? (OM.row * 15 + 13) / 26 : 10; }
/* where a row's text starts in the pane: the indent, then the +/- of a heading of a format-2 article */
static int text_at(const lay_entry *e)
{
	return indent(e) + (art_st()->is_v2 && e->kind == LE_HEADING ? toggle_w() + (OM.native ? 6 : 0) : 0);
}

static int bar_shown(e26_app *a, const scr_control *c, const geom *gm) { return a->have_art && gm->bw > 0 && entries() > rows_in(c); }

/* the thumb's top for a bar whose top is y */
static int thumb_y(const scr_control *c, const geom *gm, int y)
{
	int travel = TRAVEL(gm), max = entries() - rows_in(c);
	if (max <= 0 || travel <= 0) return y + ARROW + 1;
	return y + ARROW + 1 + (int)((long)travel * art_st()->out_top / max);
}

static void follow(e26_app *a, const scr_control *c)
{
	art_state *st = art_st();
	int cur;
	if (st->out_follow == a->scroll) return;
	st->out_follow = a->scroll;
	if (!set_get_bool("OutlineFollowsReading", 1)) return;
	cur = lay_current_entry(&st->L, a->scroll);
	if (cur < 0) return;
	cur = ar_tree_heading_row(&st->tree, &st->L, cur);
	if (cur < 0) return;
	if (OM.native) {
		/* u14 M11: when the current row leaves the middle third of the pane it goes to one third from the top */
		int rows = rows_in(c), third = rows / 3;
		if (cur < st->out_top + third || cur >= st->out_top + rows - third) st->out_top = cur - third;
	} else if (cur < st->out_top) st->out_top = cur;
	else if (cur >= st->out_top + rows_in(c)) st->out_top = cur - rows_in(c) + 1;
	clamp_top(c);
}

static void draw_bar(e26_app *a, gfx_ctx *g, const scr_control *c, const geom *gm, int pressed, int x, int y)
{
	art_state *st = art_st();
	xasset *up, *dn, *th;
	int held = pressed && st->repeat_ctl >= 0;
	gfx_fill(g, x, y, gm->bw, gm->bh, ui_rgb(a, UI_STONE));
	if ((up = xa_get(a, "ui/bitmap/GREYUP")) != NULL)
		gfx_blit(g, up, (held && st->repeat_dir == -1 ? 1 : 0) * (up->w / 4), 0, up->w / 4, up->h, x, y);
	if ((dn = xa_get(a, "ui/bitmap/GREYDOWN")) != NULL)
		gfx_blit(g, dn, (held && st->repeat_dir == 1 ? 1 : 0) * (dn->w / 4), 0, dn->w / 4, DOWN, x, y + gm->bh - DOWN);
	if ((th = xa_get(a, "ui/bitmap/GREYTHUM")) != NULL)
		gfx_blit(g, th, 0, 0, th->w / 4, th->h, x + (gm->bw - th->w / 4) / 2, thumb_y(c, gm, y));
}

static void draw_outline(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int cx, int cy, int under)
{
	const scr_control *c = &s->c[i];
	art_state *st = art_st();
	geom gm;
	int k, cur, y = cy + TOP, rows = rows_in(c), x;
	(void)under;
	if (!a->have_art) return;
	om_update(a);
	y = cy + TOP;
	rows = rows_in(c);
	geom_of(c, &gm);
	follow(a, c);
	clamp_top(c);
	if (bar_shown(a, c, &gm)) draw_bar(a, g, c, &gm, a->pressed == i && a->cur == s, cx + gm.bx, cy + gm.by);
	cur = lay_current_entry(&st->L, a->scroll);
	cur = cur < 0 ? -1 : ar_tree_heading_row(&st->tree, &st->L, cur);
	x = cx + gm.px;
	gfx_clip(g, x, cy, gm.pw, c->h);
	for (k = st->out_top; k < entries() && k < st->out_top + rows; k++) {
		int entry = entry_at(k), font = OM.font;
		const lay_entry *e = &st->L.en[entry];
		char t[300];
		int n, ind = indent(e);
		if (k == cur) {
			if (OM.native) {
				gfx_fill(g, x, y, gm.pw, ROW - 1, ui_rgb(a, UI_STONE));
				gfx_fill(g, x, y, 3, ROW - 1, e26_theme_rule(a->appearance_theme));
				font = F_RUI_BOLD;
			} else gfx_fill(g, x, y, gm.pw, 15, ui_rgb(a, UI_STONE));
		}
		if (st->is_v2 && e->kind == LE_HEADING) {
			gfx_text(g, x + ind + (OM.native ? 6 : 0), y + OM.base, OM.font, C_BLACK, st->tree.closed[entry] ? "+" : "-", 1);
			ind += toggle_w() + (OM.native ? 6 : 0);
		}
		fit(g, e->text, gm.pw - ind - 4, t, sizeof t, &n);
		if (font != OM.font) {
			/* the bold face is wider: fit again in it */
			while (n > 3 && gfx_text_width(g, font, t, n) > gm.pw - ind - 4) { n--; t[n - 3] = t[n - 2] = t[n - 1] = '.'; }
		}
		gfx_text(g, x + ind, y + OM.base, font, e->kind == LE_HEADING ? C_BLACK : C_LISTJUMP, t, n);
		y += ROW;
	}
	gfx_unclip(g);
}

void outline_draw_tip(e26_app *a, gfx_ctx *g, scr_screen *s)
{
	art_state *st = art_st();
	const scr_control *c = s ? find_kind(s, K_OUTLINE) : NULL;
	const lay_entry *e;
	geom gm;
	int w, bx;
	if (!c || !a->have_art || st->out_tip < 0 || st->out_tip >= st->L.nen || a->cur != s) return;
	om_update(a);
	geom_of(c, &gm);
	e = &st->L.en[st->out_tip];
	w = gfx_text_width(g, OM.font, e->text, -1);
	bx = c->x + (c->absolute ? 0 : s->ox) + gm.px - 1;
	gfx_fill(g, bx, st->out_tip_y, st->out_tip_x + w + 5 - bx, OM.tip, ui_rgb(a, UI_PAPER));
	gfx_frame2(g, bx, st->out_tip_y, st->out_tip_x + w + 5 - bx, OM.tip, ui_rgb(a, UI_STONE), ui_rgb(a, UI_STONE));
	gfx_text(g, st->out_tip_x, st->out_tip_y + OM.base, OM.font, e->kind == LE_HEADING ? C_BLACK : C_LISTJUMP, e->text, -1);
}

static int origin_x(scr_screen *s, const scr_control *c) { return c->x + (c->absolute ? 0 : s->ox); }
static int origin_y(scr_screen *s, const scr_control *c) { return c->y + (c->absolute ? 0 : s->oy); }

/* the entry row under y, -1 */
static int outline_row(scr_screen *s, const scr_control *c, int y)
{
	int dy = y - origin_y(s, c) - TOP;
	return dy < 0 ? -1 : dy / ROW;
}

static int in_pane(scr_screen *s, const scr_control *c, int x)
{
	geom gm;
	geom_of(c, &gm);
	return x >= origin_x(s, c) + gm.px;
}

static int outline_hover(e26_app *a, scr_screen *s, int i, int x, int y)
{
	art_state *st = art_st();
	int hot = -1, tip = -1, row;
	om_update(a);
	if (i >= 0 && a->have_art && in_pane(s, &s->c[i], x)) {
		const scr_control *c = &s->c[i];
		geom gm;
		geom_of(c, &gm);
		row = outline_row(s, c, y);
		if (row >= 0 && row < rows_in(c) && st->out_top + row < entries()) {
			int entry = entry_at(st->out_top + row);
			const lay_entry *e = &st->L.en[entry];
			char t[300];
			int n;
			hot = entry;
			if (fit(&a->g, e->text, gm.pw - text_at(e) - 4, t, sizeof t, &n)) {
				tip = hot;
				st->out_tip_x = origin_x(s, c) + gm.px + text_at(e);
				st->out_tip_y = origin_y(s, c) + TOP + row * ROW;
			}
		}
	}
	a->outline_hot = hot;
	if (hot != st->out_hot || tip != st->out_tip) { st->out_hot = hot; st->out_tip = tip; return 1; }
	return 0;
}

/* ---------------------------------------------------------------- the bar's parts and the pointer */

enum { PART_NONE, PART_UP, PART_DOWN, PART_PAGEUP, PART_PAGEDOWN, PART_THUMB };

static int part_at(e26_app *a, scr_screen *s, const scr_control *c, int x, int y)
{
	geom gm;
	int bx, by, ty;
	geom_of(c, &gm);
	if (!bar_shown(a, c, &gm)) return PART_NONE;
	bx = origin_x(s, c) + gm.bx;
	by = origin_y(s, c) + gm.by;
	if (x < bx || x >= bx + gm.bw || y < by || y >= by + gm.bh) return PART_NONE;
	ty = thumb_y(c, &gm, by);
	if (y < by + ARROW) return PART_UP;
	if (y >= by + gm.bh - DOWN) return PART_DOWN;
	if (y < ty) return PART_PAGEUP;
	if (y >= ty + THUMB) return PART_PAGEDOWN;
	return PART_THUMB;
}

static int step(e26_app *a, const scr_control *c, int part)
{
	art_state *st = art_st();
	int before = st->out_top, page = rows_in(c) - 1;
	switch (part) {
	case PART_UP: st->out_top--; break;
	case PART_DOWN: st->out_top++; break;
	case PART_PAGEUP: st->out_top -= page; break;
	case PART_PAGEDOWN: st->out_top += page; break;
	default: break;
	}
	clamp_top(c);
	st->out_follow = a->scroll; /* moved by hand: it follows the reading again when the article scrolls */
	st->out_hot = st->out_tip = -1;
	return st->out_top != before;
}

static int held_part, held_x, held_y, drag_off;
static scr_screen *held_s;

static void repeat(e26_app *a, void *ud)
{
	art_state *st = art_st();
	(void)ud;
	if (st->repeat_ctl < 0 || a->cur != held_s || a->pressed != st->repeat_ctl) { st->repeat_ctl = -1; return; }
	om_update(a);
	/* the track repeats only until the thumb reaches the pointer */
	if ((held_part != PART_PAGEUP && held_part != PART_PAGEDOWN) || part_at(a, held_s, &held_s->c[st->repeat_ctl], held_x, held_y) == held_part)
		if (step(a, &held_s->c[st->repeat_ctl], held_part)) nav_redraw(a);
	timer_after(a, 60, repeat, NULL);
}

static const kind_ops outline_ops;

static void thumb_drag(e26_app *a, int x, int y)
{
	art_state *st = art_st();
	scr_control *c = find_kind(a->cur, K_OUTLINE);
	geom gm;
	int travel, max;
	(void)x;
	if (!c) return;
	om_update(a);
	geom_of(c, &gm);
	travel = TRAVEL(&gm);
	max = entries() - rows_in(c);
	if (travel <= 0 || max <= 0) return;
	st->out_top = (int)(((long)(y - drag_off - (origin_y(a->cur, c) + gm.by + ARROW + 1)) * max + travel / 2) / travel);
	clamp_top(c);
	st->out_follow = a->scroll;
	nav_redraw(a);
}

static int outline_press(e26_app *a, scr_screen *s, int i, int x, int y)
{
	art_state *st = art_st();
	const scr_control *c = &s->c[i];
	int part;
	om_update(a);
	part = part_at(a, s, c, x, y);
	if (part == PART_NONE) return 0;
	if (part == PART_THUMB) {
		geom gm;
		geom_of(c, &gm);
		drag_off = y - thumb_y(c, &gm, origin_y(s, c) + gm.by);
		a->drag_ops = &outline_ops;
		return 0;
	}
	held_part = part;
	held_x = x;
	held_y = y;
	held_s = s;
	st->repeat_ctl = i;
	st->repeat_dir = part == PART_UP || part == PART_PAGEUP ? -1 : 1;
	step(a, c, part);
	timer_after(a, 350, repeat, NULL);
	return 0; /* input.c marks it pressed (the arrow's pressed frame) and redraws */
}

/* a row released on jumps to its section (or the figure, table or exhibit) */
static int outline_release(e26_app *a, scr_screen *s, int i, int x, int y)
{
	art_state *st = art_st();
	const scr_control *c = &s->c[i];
	int row;
	om_update(a);
	if (st->repeat_ctl >= 0 || !in_pane(s, c, x)) { st->repeat_ctl = -1; return 0; }
	row = outline_row(s, c, y);
	if (row >= 0 && row < rows_in(c) && st->out_top + row < entries()) {
		int entry = entry_at(st->out_top + row);
		const lay_entry *e = &st->L.en[entry];
		geom gm;
		geom_of(c, &gm);
		if (st->is_v2 && e->kind == LE_HEADING && x < origin_x(s, c) + gm.px + text_at(e)) {
			(void)ar_tree_toggle(&st->tree, &st->L, entry); clamp_top(c); st->out_hot = st->out_tip = -1; return 0;
		}
		art_resume_done(a);
		art_history_capture(a);
		if (st->is_v2 && st->full_id) (void)ar_history_push(&st->history, st->full_id, st->pack, e->y, st->view);
		a->scroll = e->y;
		art_place_scrollbar(a);
		st->out_follow = a->scroll; /* the outline stays where it is: the click was in it */
	}
	return 0;
}

static int outline_wheel(e26_app *a, scr_screen *s, int i, int dir)
{
	art_state *st = art_st();
	const scr_control *c = &s->c[i];
	om_update(a);
	if (!a->have_art || entries() <= rows_in(c)) return 0; /* a short outline: the article scrolls (input.c) */
	st->out_top += 3 * dir;
	clamp_top(c);
	st->out_follow = a->scroll;
	st->out_hot = st->out_tip = -1;
	return 1;
}

static const kind_ops outline_ops = { draw_outline, 1, outline_press, outline_release, outline_hover, outline_wheel, thumb_drag, NULL, NULL };

void e26_mod_kind_outline(void);
void e26_mod_kind_outline(void)
{
	reg_add(REG_KIND, "outline", &outline_ops, "kind_outline", 0);
}
