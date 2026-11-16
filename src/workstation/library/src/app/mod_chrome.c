/* mod_chrome.c: W1-CHROME's module (plan 3.2: W2-W6, O10; design 2.3: Go Forward). It registers CHROME's settings
 * and actions and wires wm.c, menus.c and wipe.c to the engine through the event bus, so no core file changes:
 *   settings  Wipes (1): a quick wipe between rooms; PeriodPacing (0): the period's top-down paint of the Title card,
 *             the first screen and every room (Deep32 Library's own keys in LIBRARY.INI; the X resources wipes,
 *             periodPacing and flyoutsOnMousePass override them for a session: -xrm '*wipes: False')
 *   actions   wm.close (Alt+F4: a menu, then a window, the Title card to the Main screen, else exit),
 *             menu.open <menu> (Alt+E and the other menu keys: the menu under its bar control, its first item under
 *             the keyboard), nav.forward (Go Forward, Alt+period: back over what Go Back left)
 *   events    SCREEN_ENTER: wm_install once the shell is realised, Go Forward's bookkeeping, the wipe;
 *             PRESS: a press on the caption (drag, double-click); HISTORY_BACK: what Go Back leaves goes on the
 *             forward list; RESIZE: a running wipe ends */
#include "chrome.h"
#include <X11/Xresource.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>

/* ---------------------------------------------------------------- settings */

static struct { const char *res; int given, value; } rcache[8];
static int nrcache;

/* the session's resource for res, cached: -1 not given, else 0 or 1 */
static int resource_bool(e26_app *a, const char *res, const char *cls)
{
	String app_name = NULL, app_class = NULL;
	char n[200], c[200], *type = NULL, s[16];
	XrmValue v;
	int i;
	for (i = 0; i < nrcache; i++)
		if (!strcmp(rcache[i].res, res)) return rcache[i].given ? rcache[i].value : -1;
	if (nrcache == (int)(sizeof rcache / sizeof rcache[0])) return -1;
	rcache[nrcache].res = res;
	rcache[nrcache].given = 0;
	XtGetApplicationNameAndClass(a->dpy, &app_name, &app_class);
	snprintf(n, sizeof n, "%s.%s", app_name ? app_name : "d32library", res);
	snprintf(c, sizeof c, "%s.%s", app_class ? app_class : "Deep32Library", cls);
	if (XrmGetResource(XtDatabase(a->dpy), n, c, &type, &v) && v.addr) {
		snprintf(s, sizeof s, "%.*s", (int)(v.size < sizeof s ? v.size : sizeof s - 1), (const char *)v.addr);
		if (!strcasecmp(s, "true") || !strcasecmp(s, "on") || !strcasecmp(s, "yes") || !strcmp(s, "1")) { rcache[nrcache].given = 1; rcache[nrcache].value = 1; }
		else if (!strcasecmp(s, "false") || !strcasecmp(s, "off") || !strcasecmp(s, "no") || !strcmp(s, "0")) { rcache[nrcache].given = 1; rcache[nrcache].value = 0; }
	}
	nrcache++;
	return rcache[nrcache - 1].given ? rcache[nrcache - 1].value : -1;
}

int chrome_flag(e26_app *a, const char *key, const char *res, const char *cls, int def)
{
	if (a->dpy && res && cls) {
		int r = resource_bool(a, res, cls);
		if (r >= 0) return r;
	}
	return set_get_bool(key, def);
}

const char *chrome_room(const scr_screen *s)
{
	const char *r = scr_screen_prop(s, "room");
	if (r) return r;
	if (!strcmp(s->name, "main") || !strcmp(s->name, "article")) return "main";
	return s->name;
}

/* ---------------------------------------------------------------- Go Forward (97) */

static e26_hist now;          /* what is in view (at the last key, press or screen) */
static e26_hist fwd[E26_HIST];
static int nfwd;
static int going;             /* a Back or a Forward is under way: the screen it enters keeps the forward list */
static int last_nhist;
static e26_hist last_top;

int chrome_forward_depth(void) { return nfwd; }

static void snapshot(e26_app *a, e26_hist *h)
{
	const scr_screen *full = a->nwin ? a->wstack[0].s : a->cur;
	nav_snapshot(a, h);
	snprintf(h->screen, sizeof h->screen, "%s", full ? full->name : "");
}

void chrome_note(e26_app *a)
{
	if (a->cur) snapshot(a, &now);
}

static void on_back(e26_app *a, const e26_event *ev, void *ud)
{
	(void)a; (void)ev; (void)ud;
	going = 1;
	if (!now.screen[0] || !strcmp(now.screen, "title")) return;
	if (nfwd == E26_HIST) { memmove(&fwd[0], &fwd[1], (E26_HIST - 1) * sizeof fwd[0]); nfwd--; }
	fwd[nfwd++] = now;
}

static int a_forward(e26_app *a, const e26_actx *x)
{
	e26_hist f, snap;
	(void)x;
	if (!nfwd) return 1; /* nothing ahead */
	f = fwd[--nfwd];
	if (!scr_find(&a->set, f.screen)) return 1;
	going = 1;
	snapshot(a, &snap);
	if (f.art_id[0] && !strcmp(f.screen, "article")) {
		if (!a->have_art || strcmp(a->art_id, f.art_id)) art_load(a, f.art_id);
		a->scroll = f.scroll;
	}
	a->cur_cat = f.cur_cat;
	a->cur_sub = f.cur_sub;
	nav_go_from(a, f.screen, 1, &snap);
	return 0;
}

/* a screen entered after a new step (a page pushed on Go Back's list, not by Back or Forward) drops the forward list */
static void forward_bookkeeping(e26_app *a)
{
	const e26_hist *top = a->nhist > 0 ? &a->hist[a->nhist - 1] : NULL;
	int pushed = a->nhist > last_nhist ||
	             (top && a->nhist == last_nhist && (strcmp(top->screen, last_top.screen) || strcmp(top->art_id, last_top.art_id) || top->scroll != last_top.scroll));
	if (pushed && !going) nfwd = 0;
	going = 0;
	last_nhist = a->nhist;
	if (top) last_top = *top;
	else memset(&last_top, 0, sizeof last_top);
}

/* ---------------------------------------------------------------- events */

static int installed;
static const scr_screen *last_full;

static void on_enter(e26_app *a, const e26_event *ev, void *ud)
{
	const scr_screen *full = a->nwin ? a->wstack[0].s : a->cur;
	int pace;
	(void)ud;
	if (!installed && a->top && a->dpy && XtIsRealized(a->top)) {
		installed = 1;
		wm_install(a);
	}
	if (ev->switched) { last_full = full; chrome_note(a); return; }
	forward_bookkeeping(a);
	pace = chrome_flag(a, "PeriodPacing", "periodPacing", "PeriodPacing", 0);
	if (!last_full) {
		if (pace) wipe_begin(a, PACE_STEPS, PACE_MS, 1);
	} else if (full && full != last_full && strcmp(chrome_room(full), chrome_room(last_full))) {
		if (pace) wipe_begin(a, PACE_STEPS, PACE_MS, 0);
		else if (chrome_flag(a, "Wipes", "wipes", "Wipes", 1)) wipe_begin(a, WIPE_STEPS, WIPE_MS, 0);
	}
	last_full = full;
	chrome_note(a);
}

static void on_press(e26_app *a, const e26_event *ev, void *ud)
{
	(void)ud;
	chrome_note(a);
	wm_caption_press(a, ev);
}

static void on_resize(e26_app *a, const e26_event *ev, void *ud)
{
	(void)ev; (void)ud;
	wipe_finish(a);
}

/* ---------------------------------------------------------------- actions */

static int a_close(e26_app *a, const e26_actx *x) { (void)x; return wm_close(a); }
static int a_raise(e26_app *a, const e26_actx *x) { (void)x; wm_raise(a); return 0; }

static int a_menu_open(e26_app *a, const e26_actx *x)
{
	menus_open_by_key(a, x->arg);
	return 1;
}

void e26_mod_mod_chrome(void);
void e26_mod_mod_chrome(void)
{
	static const e26_setting wipes = { "Wipes", "wipes", SET_BOOL, "1" };
	static const e26_setting pace = { "PeriodPacing", "periodPacing", SET_BOOL, "0" };
	static const e26_action close_ = { a_close }, open_ = { a_menu_open }, forward_ = { a_forward }, raise_ = { a_raise };
	reg_add(REG_SETTING, wipes.key, &wipes, "mod_chrome", 0);
	reg_add(REG_SETTING, pace.key, &pace, "mod_chrome", 0);
	reg_add(REG_ACTION, "wm.close", &close_, "mod_chrome", 0);
	reg_add(REG_ACTION, "wm.raise", &raise_, "mod_chrome", 0);
	reg_add(REG_ACTION, "menu.open", &open_, "mod_chrome", 0);
	reg_add(REG_ACTION, "nav.forward", &forward_, "mod_chrome", 0);
	ev_subscribe(EV_SCREEN_ENTER, on_enter, NULL);
	ev_subscribe(EV_PRESS, on_press, NULL);
	ev_subscribe(EV_HISTORY_BACK, on_back, NULL);
	ev_subscribe(EV_RESIZE, on_resize, NULL);
}
