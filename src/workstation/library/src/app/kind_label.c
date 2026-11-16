/* kind_label.c: kinds label and text: lettering on an optional ground (titles, spaced captions, wrapped notes, a
 * line on a given baseline). */
#include "library_app.h"

static void draw(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int x, int y, int under)
{
	scr_control *c = &s->c[i];
	char buf[300];
	const char *label = control_text(a, c, buf, sizeof buf);
	long fg = c->fg >= 0 ? c->fg : -1;
	(void)under;
	if (c->bg >= 0) gfx_fill(g, x, y, c->w, c->h, c->bg);
	if (c->font == F_TITLE) {
		gfx_clip(g, x, y, c->w, c->h);
		gfx_text(g, x + (c->text_x >= 0 ? c->text_x : 8), c->text_y >= 0 ? y + c->text_y : y + (c->h + gfx_ascent(g, F_TITLE) - gfx_descent(g, F_TITLE)) / 2, F_TITLE,
		         fg >= 0 ? fg : C_WHITE, label, -1);
		gfx_unclip(g);
	} else if (c->tracking || c->upper) {
		/* one spaced line, centred (or aligned) in the control, on the line the control's height centres */
		int lw = gfx_spaced(g, 0, 0, c->font, 0, label, c->tracking, c->upper, 0);
		int lx = c->text_x >= 0 ? x + c->text_x : c->align == AL_CENTRE ? x + (c->w - lw) / 2 : c->align == AL_RIGHT ? x + c->w - lw : x;
		gfx_spaced(g, lx, c->text_y >= 0 ? y + c->text_y : y + (c->h + gfx_ascent(g, c->font) - gfx_descent(g, c->font)) / 2, c->font,
		           fg >= 0 ? fg : C_WHITE, label, c->tracking, c->upper, 1);
	} else if (c->pitch > 0) {
		/* wrapped lines on a fixed pitch from a given first baseline (the Pinpointer's Words hints: Arial 13 px on a
		 * 13-px pitch, capture256 10) */
		gfx_wrap_lines(g, x + (c->text_x >= 0 ? c->text_x : 0), y + (c->text_y >= 0 ? c->text_y : gfx_ascent(g, c->font)), c->w,
		               c->font, fg >= 0 ? fg : C_WHITE, label, c->align, c->pitch);
	} else if (c->text_y >= 0) {
		/* one line on a given baseline (the Find Wizard's question, two such lines: capture256 20) */
		int lw = gfx_text_width(g, c->font, label, -1);
		int lx = c->text_x >= 0 ? x + c->text_x : c->align == AL_CENTRE ? x + (c->w - lw) / 2 : c->align == AL_RIGHT ? x + c->w - lw : x;
		gfx_text(g, lx, y + c->text_y, c->font, fg >= 0 ? fg : C_WHITE, label, -1);
	} else gfx_wrap_text(g, x + (c->text_x >= 0 ? c->text_x : 0), y, c->w, c->font, fg >= 0 ? fg : C_WHITE, label, c->align);
}

void e26_mod_kind_label(void);
void e26_mod_kind_label(void)
{
	static const kind_ops ops = { draw, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL };
	reg_add(REG_KIND, "label", &ops, "kind_label", 0);
	reg_add(REG_KIND, "text", &ops, "kind_label", 0);
}
