/* wipe.c: wipes between rooms and the period's top-down paint (W1-CHROME, plan O10; LIBRARY-DESIGN 6.5: "quick wipes
 * between rooms at a period-stepped 12 frames a second, four frames, about a third of a second"; AUTHENTICITY 2.5
 * and 10.1: the period design painted the Title card and the Main screen top-down, the Main screen complete in 1.0-1.7 s).
 *
 * How: when a room is entered (EV_SCREEN_ENTER, before the frame is drawn) the drawing context is pointed at a
 * second pixmap of the back buffer's size, so the new frame is drawn there while the back buffer, which present.c
 * puts on the window, still holds the old one (or black). Each step copies the top rows of the new frame into the
 * back buffer and presents it; the last step points the context back at the back buffer. Everything works in the
 * back buffer's own pixels, so a frame enlarged k times (update 1's scaling, W1-SHARP's sharp mode) wipes the same
 * way, and present.c is not touched. A resize, or anybody else re-pointing the context, ends the wipe at once.
 * The settings: Wipes (on) and PeriodPacing (off), mod_chrome.c; both off, a room appears at once as in build 2. */
#include "chrome.h"
#include <stdio.h>
#include <time.h>

static Pixmap neu;            /* the new frame while a wipe runs */
static GC wgc;
static int running, step, nsteps, interval;
static unsigned int bw, bh;
static unsigned long gen;     /* a timer from a wipe that has ended does nothing */

int wipe_running(void) { return running; }

int wipe_rows(int s, int n, int h)
{
	if (n <= 0 || s >= n) return h;
	if (s <= 0) return 0;
	return (int)((long)h * s / n);
}

static long ms_now(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return (long)t.tv_sec * 1000L + t.tv_nsec / 1000000L;
}
static long t_begin;

static void end(e26_app *a, int copy)
{
	if (!running) return;
	if (a->verbose) fprintf(stderr, "d32library: wipe: ended after %d of %d steps, %ld ms%s\n", step, nsteps, ms_now() - t_begin,
	                        copy ? "" : " (the drawing context was re-pointed)");
	running = 0;
	gen++;
	if (a->g.d == neu) {
		if (copy) XCopyArea(a->dpy, neu, a->back, wgc, 0, 0, bw, bh, 0, 0);
		a->g.d = a->back;
	}
	XFreePixmap(a->dpy, neu);
	neu = None;
}

void wipe_finish(e26_app *a)
{
	if (!running) return;
	end(a, 1);
	draw_flush(a);
}

static void tick(e26_app *a, void *ud)
{
	int rows;
	if (!running || (unsigned long)ud != gen) return;
	if (a->g.d != neu) { end(a, 0); return; } /* the drawing context was re-pointed (a new back buffer) */
	step++;
	rows = wipe_rows(step, nsteps, (int)bh);
	if (step >= nsteps) {
		end(a, 1);
	} else if (rows > 0) {
		/* every row revealed so far, so that what was drawn meanwhile (the pointer's hover) shows too */
		XCopyArea(a->dpy, neu, a->back, wgc, 0, 0, bw, (unsigned)rows, 0, 0);
	}
	draw_flush(a);
	if (running) timer_after(a, (unsigned long)interval, tick, (void *)gen);
}

void wipe_begin(e26_app *a, int steps, int ms, int from_black)
{
	Window root;
	int x, y;
	unsigned int border, depth;
	if (!a->dpy || !a->back || !a->win || steps < 1) return;
	if (running) end(a, 1);
	if (!XGetGeometry(a->dpy, a->back, &root, &x, &y, &bw, &bh, &border, &depth)) return;
	if (!wgc) {
		XGCValues gv;
		gv.graphics_exposures = False;
		wgc = XCreateGC(a->dpy, a->back, GCGraphicsExposures, &gv);
	}
	neu = XCreatePixmap(a->dpy, a->win, bw, bh, depth);
	if (!neu) return;
	if (from_black) {
		/* the very first frame: the window shows black until the paint reaches it */
		XSetForeground(a->dpy, wgc, xa_pixel(a, ui_rgb(a, UI_BLACK)));
		XFillRectangle(a->dpy, a->back, wgc, 0, 0, bw, bh);
	}
	a->g.d = neu;
	running = 1;
	step = 0;
	nsteps = steps;
	interval = ms > 0 ? ms : 1;
	gen++;
	t_begin = ms_now();
	if (a->verbose) fprintf(stderr, "d32library: wipe: %d steps, %d ms apart%s\n", steps, interval, from_black ? " (from black)" : "");
	timer_after(a, 1, tick, (void *)gen);
}
