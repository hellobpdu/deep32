/* kind_panel.c: kind panel: a ground (its inactive-bg beneath a window), a decoration from its source, and optional
 * wrapped lettering. Decorations registered here: "watermark" (the category's engraving behind the article column)
 * and "chevrons" (the Pinpointer panel's collapse mark). */
#include "library_app.h"

static void draw(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int x, int y, int under)
{
	scr_control *c = &s->c[i];
	long cbg = under && c->ibg >= 0 ? c->ibg : c->bg;
	char buf[300];
	const char *label = control_text(a, c, buf, sizeof buf);
	long fg = c->fg >= 0 ? c->fg : -1;
	const source_ops *so = source_of(c);
	gfx_fill(g, x, y, c->w, c->h, cbg >= 0 ? cbg : C_PAPER);
	if (so && so->decorate) so->decorate(a, g, c, x, y);
	if (label[0]) gfx_wrap_text(g, x + 12, y + 12, c->w - 24, c->font, fg >= 0 ? fg : C_BLACK, label, c->align);
}

/* the panel's collapse mark, three chevrons in entry 14 (capture 135, x 590-608, rows 100-111: drawn by the code,
 * no bitmap in the store matches) */
static void chevrons(e26_app *a, gfx_ctx *g, const scr_control *c, int x, int y)
{
	long rgb = c->fg >= 0 ? c->fg : ui_rgb(a, UI_STONE);
	int k, r;
	x += c->w - 19;
	y += (c->h - 12) / 2;
	for (k = 0; k < 3; k++)
		for (r = 0; r < 12; r++) {
			int d = r < 6 ? 5 - r : r - 6, len = (r == 0 || r == 11) ? 2 : 3;
			gfx_fill(g, x + 6 * k + d, y + r, len, 1, rgb);
		}
}

/* the category's engraving (TAXONOMY "watermark"), else the control's strip, left-aligned: the engraving's band runs
 * down its left edge; below it the engraving's own ground continues */
static void watermark(e26_app *a, gfx_ctx *g, const scr_control *c, int x, int y)
{
	const char *wm = c->strip;
	xasset *w;
	if (a->have_tax && a->tax.cat[a->cur_cat].watermark[0]) wm = a->tax.cat[a->cur_cat].watermark;
	w = wm[0] ? xa_get(a, wm) : NULL;
	if (w) {
		long gr = xa_sample(w, w->w - 1, w->h - 1);
		if (gr >= 0 && c->bg < 0) gfx_fill(g, x, y, c->w, c->h, gr);
		gfx_clip(g, x, y, c->w, c->h);
		gfx_copy(g, w, 0, 0, w->w, w->h, x, y);
		gfx_unclip(g);
	}
}

void e26_mod_kind_panel(void);
void e26_mod_kind_panel(void)
{
	static const kind_ops ops = { draw, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL };
	static const source_ops chev = { NULL, NULL, chevrons }, wmk = { NULL, NULL, watermark };
	reg_add(REG_KIND, "panel", &ops, "kind_panel", 0);
	reg_add(REG_SOURCE, "chevrons", &chev, "kind_panel", 0);
	reg_add(REG_SOURCE, "watermark", &wmk, "kind_panel", 0);
}
