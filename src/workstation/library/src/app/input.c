/* input.c: the pointer, the keys and the mouse wheel. Pointer events come in window coordinates and are mapped back
 * to the 640x480 frame (scale.h; at 640x480 the mapping is the identity). Each event goes to the open menu first
 * (menus.c), then to the kind of the control under the pointer (its kind_ops), then to the control's action
 * (action_run). Keys: Escape closes a menu or the window in front; an edit field takes typing (key_focus); then a
 * control's own key; then the screens/ *.keys bindings; then a kind's late keys (the article's arrows). */
#include "library_app.h"
#include "room_article.h"
#include <X11/Xutil.h>
#include <X11/keysym.h>
#include <stdio.h>
#include <string.h>

int hit_control(e26_app *a, int x, int y)
{
	scr_screen *s = a->cur;
	int i;
	for (i = s->nc - 1; i >= 0; i--) {
		const scr_control *c = &s->c[i];
		int cx = c->x + (c->absolute ? 0 : s->ox), cy = c->y + (c->absolute ? 0 : s->oy);
		if (x >= cx && y >= cy && x < cx + c->w && y < cy + c->h) return i;
	}
	return -1;
}

static int interactive(const scr_control *c)
{
	const kind_ops *k = kind_of(c->kind);
	return c->action != A_NONE || (k && k->interactive);
}

static void run_control(e26_app *a, scr_screen *s, int i)
{
	e26_actx x;
	memset(&x, 0, sizeof x);
	x.s = s;
	x.ctl = i;
	x.arg = s->c[i].arg;
	x.label = "";
	if (action_run(a, s->c[i].action, &x)) nav_redraw(a);
}

static void motion(e26_app *a, int x, int y)
{
	int h, i, n, changed = 0;
	const kind_ops *k;
	if (a->drag_ops) { if (a->drag_ops->drag) a->drag_ops->drag(a, x, y); return; }
	if (menus_motion(a, x, y, &changed)) return;
	h = hit_control(a, x, y);
	if (h >= 0 && !interactive(&a->cur->c[h])) h = -1;
	k = h >= 0 ? kind_of(a->cur->c[h].kind) : NULL;
	if (k && k->hover) changed |= k->hover(a, a->cur, h, x, y);
	/* every other kind with a hover forgets it (the outline's row under the pointer) */
	for (i = 0, n = reg_count(REG_KIND); i < n; i++) {
		const kind_ops *o = kind_of(i);
		if (o && o->hover && o != k) changed |= o->hover(a, NULL, -1, x, y);
	}
	if (h != a->hot) { a->hot = h; changed = 1; }
	if (changed) nav_redraw(a);
}

static void press(e26_app *a, int x, int y)
{
	int h;
	const kind_ops *k;
	e26_event ev;
	if (menus_press(a, x, y)) return;
	nav_focus_at(a, x, y); /* a press beside a non-modal window focuses what lies under it, then lands there */
	h = hit_control(a, x, y);
	memset(&ev, 0, sizeof ev);
	ev.type = EV_PRESS;
	ev.screen = a->cur;
	ev.ctl = h;
	ev.x = x;
	ev.y = y;
	ev_publish(a, &ev);
	k = h >= 0 ? kind_of(a->cur->c[h].kind) : NULL;
	if (k && k->press && k->press(a, a->cur, h, x, y)) return;
	if (h >= 0 && interactive(&a->cur->c[h]) && control_enabled(a, a->cur, h)) a->pressed = h;
	a->hot = h >= 0 && interactive(&a->cur->c[h]) ? h : -1;
	nav_redraw(a);
}

static void release(e26_app *a, int x, int y)
{
	int h = hit_control(a, x, y), p = a->pressed;
	a->drag_ops = NULL;
	a->pressed = -1;
	if (p >= 0 && h == p) {
		const kind_ops *k = kind_of(a->cur->c[p].kind);
		if (k && k->release) { if (k->release(a, a->cur, p, x, y)) return; }
		else { run_control(a, a->cur, p); return; }
	}
	nav_redraw(a);
}

/* "Control+Shift+b" against a key event's keysym and modifiers */
static int binding_matches(const char *spec, KeySym ks, unsigned state)
{
	unsigned want = 0;
	const char *p = spec;
	KeySym k;
	for (;;) {
		if (!strncmp(p, "Control+", 8)) { want |= ControlMask; p += 8; }
		else if (!strncmp(p, "Shift+", 6)) { want |= ShiftMask; p += 6; }
		else if (!strncmp(p, "Alt+", 4)) { want |= Mod1Mask; p += 4; }
		else break;
	}
	k = XStringToKeysym(p);
	if (k == NoSymbol) return 0;
	if ((state & (ControlMask | ShiftMask | Mod1Mask)) != want) return 0;
	return k == ks; /* XLookupKeysym(ev, 0): the unshifted keysym, so Control+b is XK_b with ControlMask */
}

/* u14 M12: a named action as a key binding runs it (the reader's mouse buttons and Ctrl+wheel) */
static void run_named(e26_app *a, const char *name, const char *arg)
{
	e26_actx x;
	int id = reg_find(REG_ACTION, name);
	if (id < 0) return;
	memset(&x, 0, sizeof x);
	x.ctl = -1;
	x.arg = arg;
	x.label = "";
	if (action_run(a, id, &x)) nav_redraw(a);
}

/* the article screen in front, with nothing over it (no menu, no window) */
static int reading(const e26_app *a) { return !a->menu && !a->drag_ops && a->cur && !strcmp(a->cur->name, "article"); }

static void key(e26_app *a, XKeyEvent *ev)
{
	KeySym ks = XLookupKeysym(ev, 0);
	int i;
	/* u14 S7: Find in article takes the typing while its line is open in the section bar */
	if (reading(a) && art_find_key(a, ev, ks)) return;
	if (ks == XK_Escape) {
		if (a->menu) { menus_close(a); nav_redraw(a); return; }
		if (a->cur->over[0] && a->base) { nav_close_overlay(a); return; }
	}
	/* an edit field takes the typing (the Pinpointer's word wheel: the list follows every keystroke, capture 84) */
	for (i = 0; i < a->cur->nc; i++) {
		const kind_ops *k = kind_of(a->cur->c[i].kind);
		if (k && k->key_focus) {
			if (k->key_focus(a, a->cur, i, ev, ks)) return;
			break;
		}
	}
	if (ks == XK_KP_Enter) ks = XK_Return;
	for (i = 0; i < a->cur->nc; i++) {
		scr_control *c = &a->cur->c[i];
		if (c->key[0] && XStringToKeysym(c->key) == ks && control_enabled(a, a->cur, i)) { run_control(a, a->cur, i); return; }
	}
	/* screens/ *.keys: a binding on this screen, then one on every screen */
	{
		int pass;
		for (pass = 0; pass < 2; pass++)
			for (i = 0; i < a->set.nkeys; i++) {
				const scr_key *b = &a->set.keys[i];
				e26_actx x;
				if (pass == 0 ? strcmp(b->scope, a->cur->name) : strcmp(b->scope, "*")) continue;
				if (!binding_matches(b->key, ks, ev->state)) continue;
				memset(&x, 0, sizeof x);
				x.ctl = -1;
				x.arg = b->arg;
				x.label = "";
				if (action_run(a, b->action, &x)) nav_redraw(a);
				return;
			}
	}
	for (i = 0; i < a->cur->nc; i++) {
		const kind_ops *k = kind_of(a->cur->c[i].kind);
		if (k && k->key_late) {
			k->key_late(a, a->cur, i, ks);
			return;
		}
	}
}

/* The mouse wheel (Windows 3.1 had none; X sends buttons 4 and 5): over a control whose kind scrolls (the Pinpointer's
 * list, the Category panel's area column) that control; while an overlay is in front nothing under it moves (build 2
 * scrolled the hidden article); on a full screen the article three lines */
static void wheel(e26_app *a, int x, int y, int dir, unsigned state)
{
	int h = hit_control(a, x, y);
	const kind_ops *k = h >= 0 ? kind_of(a->cur->c[h].kind) : NULL;
	if (a->menu || a->drag_ops) return;
	/* u14 M12: Ctrl+wheel is the reader's Text size, a ladder step a notch */
	if ((state & ControlMask) && reading(a)) { run_named(a, "article.zoom", dir < 0 ? "in" : "out"); return; }
	if (!(k && k->wheel && k->wheel(a, a->cur, h, dir))) {
		if (a->cur->over[0]) return;
		art_scroll_by(a, 3 * dir * a->line);
	}
	nav_redraw(a);
}

/* The pointer left the window: nothing is under it any more, so the hot control and every kind's hover (the
 * outline's row, a canvas's item and readout) are forgotten. An open menu keeps its own highlight. */
static void leave(e26_app *a)
{
	int i, n, changed = 0;
	if (a->menu) return;
	if (a->hot >= 0) { a->hot = -1; changed = 1; }
	for (i = 0, n = reg_count(REG_KIND); i < n; i++) {
		const kind_ops *o = kind_of(i);
		if (o && o->hover) changed |= o->hover(a, NULL, -1, -1, -1);
	}
	if (changed) nav_redraw(a);
}

/* Pointer events come in window coordinates; every handler above works in the 640x480 frame's, so a maximised
 * window's point is mapped back (scale.h: floor division, so the black margin maps outside the frame and hits
 * nothing, and a drag that leaves the frame still moves its thumb). At 640x480 the mapping is the identity. */
void input_handler(Widget w, XtPointer cd, XEvent *ev, Boolean *cont)
{
	e26_app *a = (e26_app *)cd;
	int x, y;
	(void)w; (void)cont;
	if (!a->cur) return;
	switch (ev->type) {
	case MotionNotify: scale_to_frame(&a->sc, ev->xmotion.x, ev->xmotion.y, &x, &y); motion(a, x, y); break;
	case ButtonPress:
		scale_to_frame(&a->sc, ev->xbutton.x, ev->xbutton.y, &x, &y);
		if (ev->xbutton.button == 1) press(a, x, y);
		else if (ev->xbutton.button == 4 || ev->xbutton.button == 5) wheel(a, x, y, ev->xbutton.button == 4 ? -1 : 1, ev->xbutton.state);
		/* u14 M12: the mouse's back and forward buttons (8, 9) in the reader */
		else if ((ev->xbutton.button == 8 || ev->xbutton.button == 9) && reading(a))
			run_named(a, ev->xbutton.button == 8 ? "article.back" : "article.forward", "");
		break;
	case ButtonRelease: scale_to_frame(&a->sc, ev->xbutton.x, ev->xbutton.y, &x, &y); if (ev->xbutton.button == 1) release(a, x, y); break;
	case LeaveNotify: leave(a); break;
	case KeyPress: key(a, &ev->xkey); break;
	default: break;
	}
}
