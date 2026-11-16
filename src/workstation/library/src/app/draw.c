/* draw.c: the frame. The window stack's screens bottom to top (a screen beneath a window draws its inactive
 * colours), every control by its kind (kind_*.c), the 1-px window border, then the open menu (menus.c), all into
 * the 640x480 back buffer through gfx.h; draw_flush puts it on the window (present.c). */
#include "library_app.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void art_viewport_sync(e26_app *a);

long ui_rgb(const e26_app *a, int index)
{
	long v = rec_pal(&a->pal, index);
	return v < 0 ? 0 : v;
}

/* under: 1 when the screen is drawn beneath an overlay window (the Find Wizard), where the Library's main window is not
 * the active one and a control's inactive-bg replaces its bg (the caption: capture256 15-28) */
void draw_screen(e26_app *a, gfx_ctx *g, scr_screen *s, int under)
{
	int i, ox = s->ox, oy = s->oy;
	if (s->ground >= 0) gfx_fill(g, ox, oy, s->w, s->h, s->ground);
	for (i = 0; i < s->nimg; i++) {
		xasset *x = xa_get(a, s->img[i].asset);
		if (!x) continue;
		/* a screen's images stay inside the screen (FINDIT_SCREEN is 2 px taller than the wizard's page) */
		gfx_clip(g, ox, oy, s->w, s->h);
		gfx_blit(g, x, 0, 0, x->w, x->h, ox + s->img[i].x, oy + s->img[i].y);
		gfx_unclip(g);
	}
	for (i = 0; i < s->nc; i++) {
		const scr_control *c = &s->c[i];
		const kind_ops *k = kind_of(c->kind);
		int cx = c->absolute ? 0 : ox, cy = c->absolute ? 0 : oy;
		if (c->hidden || (c->startup_ms > 0 && a->startup_over)) continue;
		if (k && k->draw) k->draw(a, g, s, i, c->x + cx, c->y + cy, under);
	}
}

void draw_frame(e26_app *a)
{
	gfx_ctx *g = &a->g;
	scr_screen *full = a->nwin ? a->wstack[0].s : a->cur;
	int i, focus = nav_focus(), width, height;
	if (!a->back) return; /* no frame yet (the tests drive the screens headless) */
	present_prepare(a);
	present_frame_bounds(a, &width, &height);
	art_viewport_sync(a);
	nav_viewport_place(a);
	gfx_fill(g, 0, 0, width, height, C_BLACK);
	/* the stack bottom up: the full screen, then each window; every one but the focused one draws as inactive (in
	 * build 2's flows the focused one is the top one) */
	for (i = 0; i < a->nwin; i++)
		if (!a->wstack[i].hidden) draw_screen(a, g, a->wstack[i].s, i != focus);
	/* the Library's maximised window keeps a 1-px black border round its client area [m]: on every capture of a
	 * captioned screen column 0, column 639 and row 479 are black on all their pixels, so the client area is
	 * x 1-638, rows 16-478 (two pixels narrower than the code's 640-px work area). What the code places at work
	 * x 638 or row 463 lies under the border: the (i) button's 20th column (INFO_BAR is 20 wide at work x 619)
	 * and the scroll column's down arrow's 17th row. */
	if (full && (full->ox > 0 || full->oy > 0)) {
		gfx_fill(g, width - 1, 0, 1, height, C_BLACK);
		gfx_fill(g, 0, height - 1, width, 1, C_BLACK);
	}
	menus_draw(a, g);
}

void draw_flush(e26_app *a)
{
	if (!a->back) return;
	present_frame(a); /* 1:1 in a 640x480 window; enlarged k times in a larger one (present.c) */
	XFlush(a->dpy);
}
