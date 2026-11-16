/* kind_bar.c: kind bar: a caption or section bar, or an inset field (the Category field), with the period menu
 * marker and its lettering. The Category bars take his category's face (TAXONOMY colour_rules). */
#include "library_app.h"
#include "focus.h"
#include <string.h>

static void draw(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int x, int y, int under)
{
	scr_control *c = &s->c[i];
	char buf[300];
	const char *label = control_text(a, c, buf, sizeof buf);
	long fg = c->fg >= 0 ? c->fg : -1;
	long bg = c->bg >= 0 ? c->bg : C_CAPTION;
	int tx = x + (c->text_x >= 0 ? c->text_x : 6);
	(void)under;
	if ((!strcmp(c->source, "category") || !strcmp(c->source, "subcategory")) && a->have_tax && a->tax.cat[a->cur_cat].chrome_face >= 0)
		bg = a->tax.cat[a->cur_cat].chrome_face; /* TAXONOMY colour_rules: the face fills both bars, the same
		                                            colour as his strip and the flyout (menus.c taxonomy_colours) */
	gfx_fill(g, x, y, c->w, c->h, bg);
	/* no edge while its menu is open: the 256-colour captures 06, 95 and 97 show the bars unchanged (build 1 and
	 * the authenticity round drew the class's pressed dark edge here) */
	if (c->sunken) { /* an inset field: the class's dark edge (black) along the top and the left */
		gfx_line(g, x, y, x + c->w - 1, y, C_BLACK);
		gfx_line(g, x, y, x, y + c->h - 1, C_BLACK);
	}
	/* the marker, then the label; the 1995 captures have no rule between them (build 1 drew one) */
	/* the marker's top (h + 1) / 2 - 2 below the bar's: row 24 on the 18-px top bar, row 88 on the 17-px Views and
	 * section bars and on the 15-px wizard caption (capture256 20, 35; it was h / 2 - 2, 1 px high on odd heights);
	 * a bar in focus has its marker in entry 13 (capture256 176-188) */
	if (c->marker) { gfx_marker(g, x + 5, y + (c->h + 1) / 2 - 2, s == a->cur && focus_shown(a, s, i) ? ui_rgb(a, UI_TAUPE) : c->mfg >= 0 ? c->mfg : fg >= 0 ? fg : contrast(a, bg)); if (c->text_x < 0) tx = x + 15; }
	gfx_clip(g, x, y, c->w - 2, c->h);
	{
		int by = c->text_y >= 0 ? y + c->text_y : y + (c->h + gfx_ascent(g, c->font) - gfx_descent(g, c->font)) / 2;
		if (c->tracking || c->upper)
			gfx_spaced(g, tx, by, c->font, fg >= 0 ? fg : contrast(a, bg), label, c->tracking, c->upper, 1);
		else gfx_text(g, tx, by, c->font, fg >= 0 ? fg : contrast(a, bg), label, -1);
	}
	gfx_unclip(g);
}

void e26_mod_kind_bar(void);
void e26_mod_kind_bar(void)
{
	static const kind_ops ops = { draw, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL };
	reg_add(REG_KIND, "bar", &ops, "kind_bar", 0);
}
