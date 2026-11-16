/* wm.c: the window manager side (W1-CHROME owns this file in wave 1). Deep32 Library under dtwm has its own caption and
 * only a border from the window manager (app.c: _MOTIF_WM_HINTS decorations = border), so it does itself what a
 * window manager's title bar would:
 *   - maximise (update 1): the caption's middle button does what the window menu's Maximize (Alt+F10) does: the
 *     window fills the monitor it is on, less the window manager's frame, and the frame inside it is enlarged k times
 *     (present.c); pressed again, the window is 640x480 again where it was. A double-click on the caption does the same.
 *   - a drag on the caption moves the window (not while maximised);
 *   - the caption turns to entry 12 when another program's window takes the keyboard focus, as the Library's does when a
 *     window of its own stands in front (capture256 15-28: every control with an inactive-bg), and back on FocusIn;
 *   - the icon: LIBRARYICON, the disc's own 32x32 icon (ui/raw/GROUP_ICON_LIBRARYICON.bin names it), in WM_HINTS;
 *   - Close (WM_DELETE_WINDOW, dtwm's Alt+F4 and window-menu Close) on the Title card opens the Main screen, as
 *     Enter does; anywhere else Library exits (app.c's quit callback);
 *   - the keys of an open menu reach menus.c before input.c (menus_key).
 * wm_install puts the handlers on the shell once it is realised (mod_chrome.c, at the first screen); nothing here
 * touches a core file. */
#include "chrome.h"
#include <X11/Xatom.h>
#include <X11/Xutil.h>
#include <X11/extensions/Xinerama.h>
#include <Xm/Xm.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* The window manager's frame round the shell: the outermost window under the root holding it (the shell itself
 * when no window manager reparents it), its position, and the decoration on each side of the client. */
static void frame_extents(e26_app *a, int *fx, int *fy, int *l, int *t, int *r, int *b)
{
	Window w = XtWindow(a->top), cur = w, root, parent, child, *kids = NULL;
	unsigned int nk, fw = 0, fh = 0, bw = 0, depth;
	int cx = 0, cy = 0, x = 0, y = 0;
	XWindowAttributes wa;
	XGetWindowAttributes(a->dpy, w, &wa);
	XTranslateCoordinates(a->dpy, w, wa.root, 0, 0, &cx, &cy, &child);
	for (;;) {
		if (!XQueryTree(a->dpy, cur, &root, &parent, &kids, &nk)) break;
		if (kids) XFree(kids);
		if (parent == None || parent == root) break;
		cur = parent;
	}
	XGetGeometry(a->dpy, cur, &root, &x, &y, &fw, &fh, &bw, &depth);
	*fx = x;
	*fy = y;
	*l = cx - x;
	*t = cy - y;
	*r = x + (int)fw + 2 * (int)bw - (cx + wa.width);
	*b = y + (int)fh + 2 * (int)bw - (cy + wa.height);
	if (*l < 0) *l = 0;
	if (*t < 0) *t = 0;
	if (*r < 0) *r = 0;
	if (*b < 0) *b = 0;
}

/* The monitor holding a root point (Xinerama, which RandR also answers for): a laptop panel and a dock monitor are
 * one X screen, and the window fills only its own monitor; without Xinerama, the whole screen. */
static void monitor_at(e26_app *a, int px, int py, int *mx, int *my, int *mw, int *mh)
{
	int scr = DefaultScreen(a->dpy), ev, er, n = 0, i, pick = -1;
	*mx = 0;
	*my = 0;
	*mw = DisplayWidth(a->dpy, scr);
	*mh = DisplayHeight(a->dpy, scr);
	if (XineramaQueryExtension(a->dpy, &ev, &er) && XineramaIsActive(a->dpy)) {
		XineramaScreenInfo *si = XineramaQueryScreens(a->dpy, &n);
		for (i = 0; si && i < n && pick < 0; i++)
			if (px >= si[i].x_org && py >= si[i].y_org && px < si[i].x_org + si[i].width && py < si[i].y_org + si[i].height) pick = i;
		if (si && pick < 0 && n > 0) pick = 0;
		if (si && pick >= 0) {
			*mx = si[pick].x_org;
			*my = si[pick].y_org;
			*mw = si[pick].width;
			*mh = si[pick].height;
		}
		if (si) XFree(si);
	}
}

void wm_toggle_maximize(e26_app *a)
{
	int fx, fy, l, t, r, b, mx, my, mw, mh, x, y;
	if (!XtIsRealized(a->top)) return;
	frame_extents(a, &fx, &fy, &l, &t, &r, &b);
	if (a->sc.w > E26_W || a->sc.h > E26_H) {
		/* restore: 640x480 again where the Library's own maximise found it, or (maximised by the window manager)
		 * centred on its monitor */
		monitor_at(a, fx + (a->sc.w + l + r) / 2, fy + (a->sc.h + t + b) / 2, &mx, &my, &mw, &mh);
		x = a->have_restore ? a->restore_x : mx + (mw - (E26_W + l + r)) / 2;
		y = a->have_restore ? a->restore_y : my + (mh - (E26_H + t + b)) / 2;
		a->have_restore = 0;
		if (a->verbose) fprintf(stderr, "d32library: restore to %dx%d at (%d,%d)\n", E26_W, E26_H, x, y);
		XtVaSetValues(a->top, XmNx, (Position)x, XmNy, (Position)y, XmNwidth, (Dimension)E26_W, XmNheight, (Dimension)E26_H, NULL);
		return;
	}
	monitor_at(a, fx + (a->sc.w + l + r) / 2, fy + (a->sc.h + t + b) / 2, &mx, &my, &mw, &mh);
	if (mw - l - r <= E26_W && mh - t - b <= E26_H) {
		if (a->verbose) fprintf(stderr, "d32library: maximise: the monitor (%dx%d) has no room for a larger frame\n", mw, mh);
		return;
	}
	a->restore_x = fx;
	a->restore_y = fy;
	a->have_restore = 1;
	if (a->verbose) fprintf(stderr, "d32library: maximise to %dx%d at (%d,%d) (monitor %dx%d+%d+%d, frame %d %d %d %d)\n",
	                        mw - l - r, mh - t - b, mx, my, mw, mh, mx, my, l, t, r, b);
	XtVaSetValues(a->top, XmNx, (Position)mx, XmNy, (Position)my, XmNwidth, (Dimension)(mw - l - r),
	              XmNheight, (Dimension)(mh - t - b), NULL);
}

/* ---------------------------------------------------------------- focus: the caption active or inactive */

static int active = 1;      /* the window has the keyboard focus, or nothing says it has not */
static long *saved_bg;      /* the bg of every control with an inactive-bg, while the window is inactive */
static int nsaved;

int wm_active(void) { return active; }

/* every control with an inactive-bg (the caption strip: entry 28 active, 12 inactive) takes it while another
 * program's window has the focus; the stack's own rule (a window in front) is unchanged */
static void set_active(e26_app *a, int act)
{
	int i, k, n = 0;
	act = act ? 1 : 0;
	if (act == active) return;
	if (!saved_bg) {
		for (i = 0; i < a->set.ns; i++)
			for (k = 0; k < a->set.s[i].nc; k++)
				if (a->set.s[i].c[k].ibg >= 0) n++;
		if (!(saved_bg = (long *)calloc((size_t)(n ? n : 1), sizeof(long)))) return;
		nsaved = n;
		n = 0;
	}
	for (i = 0; i < a->set.ns; i++)
		for (k = 0; k < a->set.s[i].nc; k++) {
			scr_control *c = &a->set.s[i].c[k];
			if (c->ibg < 0 || n >= nsaved) continue;
			if (!act) { saved_bg[n] = c->bg; c->bg = c->ibg; }
			else c->bg = saved_bg[n];
			n++;
		}
	active = act;
	if (a->verbose) fprintf(stderr, "d32library: %s\n", act ? "active (the caption in entry 28)" : "inactive (the caption in entry 12)");
}

void wm_set_active(e26_app *a, int act)
{
	int was = active;
	set_active(a, act);
	if (active != was) nav_redraw(a);
}

/* the keyboard focus is on the shell or a window inside it; PointerRoot or None (no window manager hands the focus
 * out, as on the render lanes' bare Xvfb) counts as ours */
static int focus_ours(e26_app *a)
{
	Window f = None, w, root = None, parent = None, *kids = NULL, top = XtWindow(a->top);
	unsigned int nk;
	int rev;
	XGetInputFocus(a->dpy, &f, &rev);
	if (f == PointerRoot || f == None) return 1;
	for (w = f; w != None; w = parent) {
		if (w == top) return 1;
		if (!XQueryTree(a->dpy, w, &root, &parent, &kids, &nk)) return 0;
		if (kids) XFree(kids);
		if (w == root) return 0;
	}
	return 0;
}

static void focus_handler(Widget w, XtPointer cd, XEvent *ev, Boolean *cont)
{
	e26_app *a = (e26_app *)cd;
	(void)w; (void)cont;
	if (ev->type != FocusIn && ev->type != FocusOut) return;
	/* a grab (dtwm's window menu, Alt+Tab, a drag) moves no focus */
	if (ev->xfocus.mode == NotifyGrab || ev->xfocus.mode == NotifyUngrab) return;
	wm_set_active(a, focus_ours(a));
}

/* ---------------------------------------------------------------- Close */

static Atom wm_protocols, wm_delete;

/* WM_DELETE_WINDOW (dtwm's Alt+F4 and its window menu's Close) on the Title card opens the Main screen, as Enter
 * does (plan W4); anywhere else it goes on to app.c's quit callback. Inserted ahead of Motif's protocol handler. */
static void client_message(Widget w, XtPointer cd, XEvent *ev, Boolean *cont)
{
	e26_app *a = (e26_app *)cd;
	(void)w;
	if (ev->type != ClientMessage || ev->xclient.message_type != wm_protocols || ev->xclient.format != 32 ||
	    (Atom)ev->xclient.data.l[0] != wm_delete)
		return;
	if (a->cur && !strcmp(a->cur->name, "title")) {
		*cont = False;
		if (a->verbose) fprintf(stderr, "d32library: close on the Title card: the Main screen\n");
		nav_go(a, "main", 1);
	} else if (a->verbose) fprintf(stderr, "d32library: close: exit\n");
}

int wm_close(e26_app *a)
{
	if (a->menu) { menus_close(a); return 1; }
	if (a->cur && !strcmp(a->cur->name, "title")) { nav_go(a, "main", 1); return 0; }
	if (a->cur && a->cur->over[0] && a->nwin > 1) { nav_close_overlay(a); return 0; }
	e26_quit(a);
	return 0;
}

/* ---------------------------------------------------------------- the keys of an open menu */

static void key_handler(Widget w, XtPointer cd, XEvent *ev, Boolean *cont)
{
	e26_app *a = (e26_app *)cd;
	(void)w;
	if (ev->type != KeyPress || !a->cur) return;
	chrome_note(a);
	if (a->menu && menus_key(a, XLookupKeysym(&ev->xkey, 0), ev->xkey.state)) *cont = False;
}

/* ---------------------------------------------------------------- the icon */

/* LIBRARYICON: the disc's group icon names its images (GRPICONDIR: 6 bytes, then 14-byte entries, the last two
 * bytes of each the RT_ICON id); the 32x32 one goes into WM_HINTS with its mask */
static void set_icon(e26_app *a)
{
	char path[1200], name[64];
	unsigned char b[256];
	FILE *f;
	size_t n;
	int k, count, pick = -1, id;
	xasset *x;
	snprintf(path, sizeof path, "%s/ui/raw/GROUP_ICON_LIBRARYICON.bin", a->store);
	if (!(f = fopen(path, "rb"))) { if (a->verbose) fprintf(stderr, "d32library: no LIBRARYICON in the store: no icon\n"); return; }
	n = fread(b, 1, sizeof b, f);
	fclose(f);
	count = n >= 6 ? b[4] | b[5] << 8 : 0;
	for (k = 0; k < count && 6 + 14 * (size_t)k + 14 <= n; k++)
		if (pick < 0 || b[6 + 14 * k] == 32) pick = k;
	if (pick < 0) return;
	id = b[6 + 14 * pick + 12] | b[6 + 14 * pick + 13] << 8;
	snprintf(name, sizeof name, "ui/icon/%d", id);
	if (!(x = xa_get(a, name))) return;
	/* WMShell writes the whole of WM_HINTS again: input stays True, or dtwm (explicit focus) would never give the
	 * focus back to Library once another window has had it */
	XtVaSetValues(a->top, XmNiconPixmap, x->pm, XmNinput, True, NULL);
	if (x->mask) XtVaSetValues(a->top, XmNiconMask, x->mask, NULL);
	if (a->verbose) fprintf(stderr, "d32library: icon %s (LIBRARYICON) %dx%d\n", name, x->w, x->h);
}

/* ---------------------------------------------------------------- the caption: drag and double-click */

static struct { int rx, ry, fx, fy, lx, ly; } drag;
static long last_ms;
static int last_x, last_y;

static long now_ms(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return (long)t.tv_sec * 1000L + t.tv_nsec / 1000000L;
}

/* the pointer moved with button 1 down after a press on the caption: the window follows it (its frame's position,
 * as wm_toggle_maximize places it) */
static void caption_drag(e26_app *a, int x, int y)
{
	Window root, child;
	int rx, ry, wx, wy, nx, ny;
	unsigned int mask;
	(void)x; (void)y;
	if (!XQueryPointer(a->dpy, XtWindow(a->top), &root, &child, &rx, &ry, &wx, &wy, &mask)) return;
	nx = drag.fx + rx - drag.rx;
	ny = drag.fy + ry - drag.ry;
	if (nx == drag.lx && ny == drag.ly) return;
	drag.lx = nx;
	drag.ly = ny;
	XtVaSetValues(a->top, XmNx, (Position)nx, XmNy, (Position)ny, NULL);
}

static const kind_ops caption_ops = { NULL, 0, NULL, NULL, NULL, NULL, caption_drag, NULL, NULL };

void wm_caption_press(e26_app *a, const e26_event *ev)
{
	scr_screen *full = a->nwin ? a->wstack[0].s : a->cur;
	const scr_control *cap;
	Window root, child;
	int i, fx, fy, l, t, r, b, rx, ry, wx, wy;
	unsigned int mask;
	long now;
	if (!a->top || !a->dpy || !XtIsRealized(a->top) || !full || ev->x < 0 || ev->y < 0) return;
	if (!(cap = scr_control_find(full, "capground")) && !(cap = scr_control_find(full, "capstrip"))) return;
	if (ev->y >= cap->y + cap->h || ev->x >= cap->x + cap->w) return;
	for (i = 0; i < full->nc; i++) { /* the caption's buttons are not the caption */
		const scr_control *c = &full->c[i];
		if (c->absolute && c->action != A_NONE && ev->x >= c->x && ev->x < c->x + c->w && ev->y >= c->y && ev->y < c->y + c->h) return;
	}
	now = now_ms();
	if (last_ms && now - last_ms <= (long)XtGetMultiClickTime(a->dpy) && abs(ev->x - last_x) <= 4 && abs(ev->y - last_y) <= 4) {
		last_ms = 0;
		a->drag_ops = NULL;
		if (a->verbose) fprintf(stderr, "d32library: caption double-click: maximise or restore\n");
		wm_toggle_maximize(a);
		return;
	}
	last_ms = now;
	last_x = ev->x;
	last_y = ev->y;
	if (a->sc.w > E26_W || a->sc.h > E26_H) return; /* maximised: the window stays where it is */
	if (!XQueryPointer(a->dpy, XtWindow(a->top), &root, &child, &rx, &ry, &wx, &wy, &mask)) return;
	frame_extents(a, &fx, &fy, &l, &t, &r, &b);
	drag.rx = rx;
	drag.ry = ry;
	drag.fx = drag.lx = fx;
	drag.fy = drag.ly = fy;
	a->drag_ops = &caption_ops;
}

/* A local open request raises the existing shell through the window-manager path. Mapping an iconic shell
 * requests NormalState through MapRequest; WM_CHANGE_STATE only standardises IconicState. An EWMH-aware manager
 * can honour the activation request; other managers retain their normal focus policy. */
void wm_raise(e26_app *a)
{
	XEvent ev;
	Window shell;
	if (!a || !a->top || !a->dpy || !XtIsRealized(a->top)) return;
	shell = XtWindow(a->top);
	XMapRaised(a->dpy, shell);
	memset(&ev, 0, sizeof ev);
	ev.xclient.type = ClientMessage;
	ev.xclient.display = a->dpy;
	ev.xclient.window = shell;
	ev.xclient.message_type = XInternAtom(a->dpy, "_NET_ACTIVE_WINDOW", False);
	ev.xclient.format = 32;
	ev.xclient.data.l[0] = 1; /* application request; the window manager controls focus */
	ev.xclient.data.l[1] = (long)XtLastTimestampProcessed(a->dpy);
	(void)XSendEvent(a->dpy, DefaultRootWindow(a->dpy), False,
	                 SubstructureNotifyMask | SubstructureRedirectMask, &ev);
	XFlush(a->dpy);
}

/* ---------------------------------------------------------------- install */

/* centred placement (plan W2): without a -geometry position the window stands where the splash stood, centred on the
 * screen (room_title.c), its frame included; a larger window (a -geometry size, maximised) stays where it is */
static void place_centred(e26_app *a)
{
	XSizeHints h;
	long supplied = 0;
	int fx, fy, l, t, r, b, x, y, scr = DefaultScreen(a->dpy);
	memset(&h, 0, sizeof h);
	if (XGetWMNormalHints(a->dpy, XtWindow(a->top), &h, &supplied) && (h.flags & USPosition)) return;
	if (a->sc.w > E26_W || a->sc.h > E26_H) return;
	frame_extents(a, &fx, &fy, &l, &t, &r, &b);
	x = (DisplayWidth(a->dpy, scr) - (E26_W + l + r)) / 2;
	y = (DisplayHeight(a->dpy, scr) - (E26_H + t + b)) / 2;
	if (x < 0) x = 0;
	if (y < 0) y = 0;
	if (fx == x && fy == y) return;
	if (a->verbose) fprintf(stderr, "d32library: placed centred at (%d,%d)\n", x, y);
	XtVaSetValues(a->top, XmNx, (Position)x, XmNy, (Position)y, NULL);
}

void wm_install(e26_app *a)
{
	if (!a->top || !a->dpy || !XtIsRealized(a->top)) return;
	wm_protocols = XInternAtom(a->dpy, "WM_PROTOCOLS", False);
	wm_delete = XInternAtom(a->dpy, "WM_DELETE_WINDOW", False);
	XtInsertEventHandler(a->top, NoEventMask, True, client_message, (XtPointer)a, XtListHead);
	XtAddEventHandler(a->top, FocusChangeMask, False, focus_handler, (XtPointer)a);
	XtInsertEventHandler(a->top, KeyPressMask, False, key_handler, (XtPointer)a, XtListHead);
	if (a->da) XtInsertEventHandler(a->da, KeyPressMask, False, key_handler, (XtPointer)a, XtListHead);
	set_icon(a);
	place_centred(a);
	set_active(a, focus_ours(a)); /* the screen being entered draws it */
}
