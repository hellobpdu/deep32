/* kind_credits.c: kind "credits", the Title card's credit lines (the owner, 2026-11-07; screens/title.scr). Each
 * "data" row is one line of interface text, centred in the control (or as "align" says), the first on the baseline
 * "text-y" below the control's top and the rest every "pitch" px. "prop mark <asset>" draws a small store bitmap
 * after the last line: "prop mark-gap" px after its text, its top "prop mark-rise" px above that line's baseline; the
 * line and the mark are centred together. A missing mark is skipped (xa_get says so once) and the lines still draw.
 * "prop after startup" keeps the lines off the frame while the start-up lines (control "startup") stand in the same
 * place: they appear when those go, and stay. Drawn only: the kind takes no pointer, so the card's own controls keep
 * every press. Every pixel goes through gfx.h, so W1-SHARP enlarges it with the rest of the frame. */
#include "library_app.h"
#include <stdlib.h>
#include <string.h>

static int prop_int(const scr_control *c, const char *name, int def)
{
	const char *v = scr_prop_get(c, name);
	return v && *v ? atoi(v) : def;
}

static void draw(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int x, int y, int under)
{
	const scr_control *c = &s->c[i];
	const char *after = scr_prop_get(c, "after"), *mark = scr_prop_get(c, "mark");
	long fg = c->fg >= 0 ? c->fg : C_WHITE;
	int r, by, pitch = c->pitch > 0 ? c->pitch : gfx_font_height(g, c->font) + 1;
	int gap = prop_int(c, "mark-gap", 4), rise = prop_int(c, "mark-rise", 0);
	xasset *m;
	(void)under;
	if (after && !strcmp(after, "startup") && !a->startup_over) return;
	m = mark && *mark ? xa_get(a, mark) : NULL;
	by = y + (c->text_y >= 0 ? c->text_y : gfx_ascent(g, c->font));
	for (r = 0; r < c->nrows; r++, by += pitch) {
		const char *t = c->rows[r].n > 0 ? c->rows[r].v[0] : "";
		int withmark = m && r == c->nrows - 1;
		int tw = gfx_text_width(g, c->font, t, -1);
		int w = tw + (withmark ? gap + m->w : 0);
		int lx = c->align == AL_CENTRE ? x + (c->w - w) / 2 : c->align == AL_RIGHT ? x + c->w - w : x;
		gfx_text(g, lx, by, c->font, fg, t, -1);
		if (withmark) gfx_blit(g, m, 0, 0, m->w, m->h, lx + tw + gap, by - rise);
	}
}

void e26_mod_kind_credits(void);
void e26_mod_kind_credits(void)
{
	static const kind_ops ops = { draw, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL };
	reg_add(REG_KIND, "credits", &ops, "kind_credits", 0);
}
