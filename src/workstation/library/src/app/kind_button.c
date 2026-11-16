/* kind_button.c: kinds button, toggle, radio and image: a bitmap strip (one frame per state) or, without a strip, a
 * text button painted as the period program's control class paints one from its record. */
#include "library_app.h"
#include "focus.h"
#include <string.h>

static void draw(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int x, int y, int under)
{
	scr_control *c = &s->c[i];
	int st = control_state(a, s, i), en = control_enabled(a, s, i);
	char buf[300];
	const char *label = control_text(a, c, buf, sizeof buf);
	long fg = c->fg >= 0 ? c->fg : -1;
	xasset *x1 = c->strip[0] ? xa_get(a, c->strip) : NULL;
	(void)under;
	if (x1) {
		int fw = x1->w / c->frames, fr = frame_for(c, st), bx = x, by = y;
		if (fr >= c->frames) fr = 0;
		/* a record-made button whose window is larger than its bitmap shows the bitmap centred inside the class
		 * frame (the Pinpointer's Find, ENCFIL_FIND 43x16 in a 45x18 window, ringed in entry 19: capture256 10) */
		if (c->kind == K_BUTTON && c->record > 0 && c->w > fw) bx = x + (c->w - fw) / 2;
		if (c->kind == K_BUTTON && c->record > 0 && c->h > x1->h) by = y + (c->h - x1->h) / 2;
		gfx_blit(g, x1, fr * fw, 0, fw, x1->h, bx, by);
		if (c->record > 0 && (c->kind == K_BUTTON || c->kind == K_TOGGLE || c->kind == K_RADIO)) {
			/* a bitmap button made from a record keeps the class's 1-px frame by state round the control, over
			 * the strip's own outer ring [m]: on the 16-colour captures that ring is a solid pen colour while
			 * the bitmap's pixels are dithered, even where the bitmap has the face colour there (Go Back,
			 * capture 35: a solid light-grey ring, idle slot 14), and it differs from the bitmap's ring (the
			 * wizard's Find, captures 20, 26, 27: black on all four sides in every state, record 10082's slots
			 * 3-6 = entry 28, where FINDWIZARD_FIND has #52845B; the top bar's Find, capture 35: grey all
			 * round, idle slot 13, where MAIN_FIND's right column is entry 12). Transparent slots (-2, the
			 * title's buttons) draw none. The same holds for record-made radios and check boxes (256-colour
			 * captures: the wizard's radios, record 10088, show FINDWIZARD_RADIOBUTTON_5 with its outer ring
			 * in entry 13, the page's colour; the title's check box, record 10074, a ring of entry 31). */
			long tl, br;
			class_pens(c, st, en, &tl, &br);
			/* round the bitmap's own window: for a radio or check box the strip's frame, not the label
			 * beside it (capture256 20: column 11 of the radio's frame is entry 13 too) */
			gfx_frame2(g, x, y, c->kind == K_BUTTON ? c->w : fw, c->kind == K_BUTTON ? c->h : x1->h, tl, br);
		} else if (c->kind == K_BUTTON && c->frames == 1 && s == a->cur && focus_shown(a, s, i)) {
			/* a one-frame strip button made without a record (ENCSMALL, the (i)) in focus is raised: white
			 * top-left, black bottom-right (capture256 181, 187). A strip with its own hot frame (the section
			 * buttons, frame 3) shows that instead (capture256 188) */
			gfx_frame2(g, x, y, c->w, c->h, C_WHITE, C_BLACK);
		}
		if (label[0] && (c->kind == K_RADIO || c->kind == K_TOGGLE))
			gfx_text(g, c->text_x >= 0 ? x + c->text_x : x + fw + 6,
			         c->text_y >= 0 ? y + c->text_y : y + (x1->h + gfx_ascent(g, c->font)) / 2 - 1, c->font,
			         en ? (fg >= 0 ? fg : C_WHITE) : C_DISABLED, label, -1);
	} else if (c->strip[0] == 0) {
		/* a text button painted as the period program's control class paints one created from a record (PALETTE.md 3;
		 * seg18:088a picks the state, seg19:0b8c paints it): the face, then a 1-px frame by state: at rest the
		 * idle frame colour all round, under the pointer light top-left and dark bottom-right, pressed (or down)
		 * the reverse, disabled the disabled frame on the disabled face; the label in the label colour, or
		 * RGB(128,128,128) when disabled. Slots come from the record, or the class defaults without one. */
		const long *sl = c->slot;
		int pr = (st == S_PRESSED || st == S_CHECKED || st == S_CHECKED_HOT || st == S_CHECKED_PRESSED), lw, ty;
		long face = !en ? sl[REC_FACE_DISABLED] : sl[REC_FACE], tl, br, ink;
		if (c->bg >= 0) face = c->bg;
		class_pens(c, st, en, &tl, &br);
		ink = !en ? C_DISABLED : fg >= 0 ? fg : sl[REC_LABEL] >= 0 ? sl[REC_LABEL] : C_BLACK;
		if (face >= 0) gfx_fill(g, x, y, c->w, c->h, face);
		gfx_frame2(g, x, y, c->w, c->h, tl, br);
		lw = gfx_text_width(g, c->font, label, -1) + (c->marker ? 11 : 0);
		ty = y + (c->h + gfx_ascent(g, c->font) - gfx_descent(g, c->font)) / 2 + pr;
		/* a label with the menu marker starts at a fixed inset, marker at 5 and lettering at 16 (1995 captures:
		 * Features and Tools, 0 differing pixels); a plain label is centred */
		{
			int lx = c->marker ? x + 5 : x + (c->w - lw) / 2;
			if (c->marker) gfx_marker(g, lx + pr, y + c->h / 2 - 2 + pr, ink);
			gfx_text(g, lx + (c->marker ? 11 : 0) + pr, ty, c->font, ink, label, -1);
		}
	}
}

void e26_mod_kind_button(void);
void e26_mod_kind_button(void)
{
	static const kind_ops ops = { draw, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL };
	reg_add(REG_KIND, "button", &ops, "kind_button", 0);
	reg_add(REG_KIND, "toggle", &ops, "kind_button", 0);
	reg_add(REG_KIND, "radio", &ops, "kind_button", 0);
	reg_add(REG_KIND, "image", &ops, "kind_button", 0);
}
