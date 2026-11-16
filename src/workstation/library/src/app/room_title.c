/* room_title.c: the Title card's room (W2-FRONTDESK and W2-OPENING in wave 2): the start-up splash before it,
 * and the start-up lines' time on it. */
#include "library_app.h"
#include <X11/Shell.h>
#include <X11/Xutil.h>
#include <stdio.h>

/* The start-up line on the title card (control "startup"): the period design shows its copyright line from 1.9 to 5.8 s on
 * the 256-colour capture lane (captures 89, 90), then the card alone */
static void startup_done(e26_app *a, void *ud)
{
	(void)ud;
	a->startup_over = 1;
	nav_redraw(a);
}

void title_startup_timer(e26_app *a)
{
	/* the start-up lines' time: the longest "startup" of the first screen */
	int k, ms = 0;
	for (k = 0; k < a->cur->nc; k++) if (a->cur->c[k].startup_ms > ms) ms = a->cur->c[k].startup_ms;
	if (ms > 0) timer_after(a, (unsigned long)ms, startup_done, NULL);
	else a->startup_over = 1;
}

/* The start-up splash before the Title screen: the store's SPLASH bitmap (320x224) at (21,21) of a 360x264 window, white with a 1-px black border, centred on the screen;
 * here centred on where Deep32 Library's 640x480 frame will stand (its -geometry, else the screen's centre). It stays
 * splashTime ms (0.42 s on the capture lane), then the main window opens on the Title screen. */
static Window splash_win;
static void (*splash_then)(void);

static void splash_done(e26_app *a, void *ud)
{
	(void)ud;
	if (splash_win) { XDestroyWindow(a->dpy, splash_win); splash_win = 0; }
	splash_then();
}

int title_splash(e26_app *a, void (*then)(void))
{
	xasset *sp;
	int sx, sy, scr = DefaultScreen(a->dpy);
	unsigned int gw = 0, gh = 0;
	int gx = 0, gy = 0, gm = 0;
	String geo = NULL;
	XSetWindowAttributes wa;
	Pixmap bg;
	gfx_ctx g;
	sp = xa_get(a, "ui/bitmap/SPLASH");
	if (!sp) return -1;
	XtVaGetValues(a->top, XtNgeometry, &geo, NULL);
	if (geo) gm = XParseGeometry(geo, &gx, &gy, &gw, &gh);
	if ((gm & XValue) && (gm & YValue)) {
		/* a -geometry with a size larger than the frame: the window is that size (the frame enlarged in it) */
		int ww = (gm & WidthValue) && (int)gw > E26_W ? (int)gw : E26_W, wh = (gm & HeightValue) && (int)gh > E26_H ? (int)gh : E26_H;
		if (gm & XNegative) gx = DisplayWidth(a->dpy, scr) - ww + gx;
		if (gm & YNegative) gy = DisplayHeight(a->dpy, scr) - wh + gy;
		sx = gx + (ww - 360) / 2;
		sy = gy + (wh - 264) / 2;
	} else {
		sx = (DisplayWidth(a->dpy, scr) - 360) / 2;
		sy = (DisplayHeight(a->dpy, scr) - 264) / 2;
	}
	bg = XCreatePixmap(a->dpy, a->win, 360, 264, (unsigned)a->depth);
	gfx_init(&g, a, bg, 1);
	gfx_fill(&g, 0, 0, 360, 264, ui_rgb(a, UI_BLACK));
	gfx_fill(&g, 1, 1, 358, 262, ui_rgb(a, UI_WHITE));
	gfx_copy(&g, sp, 0, 0, sp->w, sp->h, 21, 21);
	wa.override_redirect = True;
	wa.background_pixmap = bg;
	splash_win = XCreateWindow(a->dpy, a->win, sx, sy, 360, 264, 0, a->depth, InputOutput, a->vis, CWOverrideRedirect | CWBackPixmap, &wa);
	XFreePixmap(a->dpy, bg);
	XMapRaised(a->dpy, splash_win);
	XFlush(a->dpy);
	splash_then = then;
	timer_after(a, (unsigned long)(a->splash_ms > 0 ? a->splash_ms : 1), splash_done, NULL);
	return 0;
}

void e26_mod_room_title(void);
void e26_mod_room_title(void) { }
