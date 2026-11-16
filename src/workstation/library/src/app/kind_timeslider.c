/* kind_timeslider.c: a piecewise BC/AD drag range over the private Time picture.
 * No disc pixels live here. ART's re-engraved scale is an explicit hand-off. */
#include "room_finder.h"
#include <stdio.h>

static int anchor;
static int year_at(int x, int left, int width)
{
	double t;
	if (width < 2) return 1;
	if (x < left) x = left;
	if (x >= left + width) x = left + width - 1;
	t = (double)(x - left) / (width - 1);
	/* Three fifths for BC, two fifths for AD: fine movement around modern years. */
	if (t < 0.6) return -(int)(1.0 + 99999.0 * (1.0 - t / 0.6) * (1.0 - t / 0.6));
	t = (t - 0.6) / 0.4;
	return 1 + (int)(2029.0 * t);
}
static void draw(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int x, int y, int under)
{
	const scr_control *c = &s->c[i]; finder_model *f = finder_st();
	xasset *art = c->strip[0] ? xa_get(a, c->strip) : NULL; char line[100];
	(void)under;
	if (art) gfx_blit(g, art, 0, 0, art->w, art->h, x, y);
	else gfx_fill(g, x, y, c->w, c->h, C_PAPER);
	/* This annotation defines the live drag scale, not the legacy artwork's dates. */
	gfx_fill(g, x, y + c->h - 30, c->w, 30, C_PAPER);
	gfx_text(g, x + 2, y + c->h - 18, F_UI, C_BLACK, "100000 BC                         AD 2030", -1);
	if (f->years) {
		snprintf(line, sizeof line, "%d %s to %d %s", f->year_min < 0 ? -f->year_min : f->year_min, f->year_min < 0 ? "BC" : "AD",
		    f->year_max < 0 ? -f->year_max : f->year_max, f->year_max < 0 ? "BC" : "AD");
		gfx_text(g, x + 2, y + c->h - 4, F_UI, C_BLACK, line, -1);
	}
}
static void drag(e26_app *a, int x, int y)
{
	const scr_control *c = &a->cur->c[a->drag_ctl];
	(void)y; finder_year_range(a, anchor, year_at(x, c->x + a->cur->ox, c->w)); nav_redraw(a);
}
static int press(e26_app *a, scr_screen *s, int i, int x, int y);
static const kind_ops ops = { draw, 1, press, NULL, NULL, NULL, drag, NULL, NULL };
static int press(e26_app *a, scr_screen *s, int i, int x, int y)
{
	(void)y; anchor = year_at(x, s->c[i].x + s->ox, s->c[i].w);
	a->drag_ops = &ops; a->drag_ctl = i; finder_year_range(a, anchor, anchor); nav_redraw(a); return 1;
}
void e26_mod_kind_timeslider(void);
void e26_mod_kind_timeslider(void) { reg_add(REG_KIND, "timeslider", &ops, "kind_timeslider", 0); }
