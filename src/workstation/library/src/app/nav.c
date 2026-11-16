/* nav.c: screens and the window stack (plan E3), Go Back over the article history (E5, T2), and the "coming in a
 * later build" box.
 *
 * The stack: entry 0 is a full screen, every entry above it an overlay window. nav_go replaces the stack (a full
 * screen alone, or an overlay over its base), as build 2's screen switch did; nav_push_window adds a window over the
 * front one (modal or not). The focused window takes the input (a->cur); every other draws inactive (its caption's
 * inactive-bg). Escape closes the focused window. A press outside a non-modal window focuses whatever lies under it.
 * Build 2's flows only ever hold one overlay, so they draw and behave exactly as before. */
#include "library_app.h"
#include "room_article.h"
#include <Xm/Xm.h>
#include <stdio.h>
#include <string.h>

static int focus;     /* the focused entry of the stack */

/* cur and base from the stack */
static void sync(e26_app *a)
{
	if (!a->nwin) return;
	if (focus >= a->nwin) focus = a->nwin - 1;
	a->cur = a->wstack[focus].s;
	a->base = focus > 0 ? a->wstack[0].s : NULL;
}

void nav_redraw(e26_app *a)
{
	draw_frame(a);
	draw_flush(a);
}

/* the shell's title: a screen's "prop window-title", else build 2's names */
void nav_set_title(e26_app *a)
{
	char t[200];
	const char *room = scr_screen_prop(a->cur, "window-title");
	if (!room)
		room = !strcmp(a->cur->name, "title") ? "Title Screen" :
		       !strncmp(a->cur->name, "findwiz", 7) ? "Find Wizard" :
		       !strncmp(a->cur->name, "pinpointer", 10) ? "Pinpointer" :
		       !strcmp(a->cur->name, "article") && a->have_art ? a->art.title : "Main Screen";
	snprintf(t, sizeof t, "Deep32 Encyclopedia - %s", room);
	if (a->top) XtVaSetValues(a->top, XmNtitle, t, NULL);
}

/* the screen in front changed: shared states move with it, the pointer and menus reset, rooms react (the article's
 * default and scroll bar), the screen's sound plays, and the frame is drawn */
static void entered(e26_app *a, scr_screen *prev, const char *name)
{
	e26_event ev;
	states_save(prev);
	sync(a);
	states_load(a->cur);
	a->hot = a->pressed = -1;
	a->menu = NULL;
	a->sub_open = 0;
	memset(&ev, 0, sizeof ev);
	ev.type = EV_SCREEN_ENTER;
	ev.screen = a->cur;
	ev.ctl = -1;
	ev.name = name;
	ev_publish(a, &ev);
	snd_screen(a);
	nav_set_title(a);
	nav_redraw(a);
	if (a->verbose) fprintf(stderr, "d32library: screen %s\n", name);
}

/* an overlay's base: its named screen, or ("over *") whichever full screen is in front */
static scr_screen *base_for(e26_app *a, const scr_screen *s)
{
	scr_screen *base = strcmp(s->over, "*") ? scr_find(&a->set, s->over) : !a->cur ? NULL : a->cur->over[0] ? a->base : a->cur;
	if (!base) base = scr_find(&a->set, "main");
	return base;
}

static void place(scr_screen *s, const scr_screen *base)
{
	s->ox = s->rox + base->ox;
	s->oy = s->roy + base->oy;
}

enum viewport_overlay { VIEW_LEGACY, VIEW_ARTICLE, VIEW_PINNED, VIEW_CENTRED };

static int viewport_is(const scr_screen *s, const char *mode)
{
	const char *p = s ? scr_screen_prop(s, "viewport") : NULL;
	return p && !strcmp(p, mode);
}

static enum viewport_overlay overlay_viewport(const e26_app *a, int i)
{
	const scr_screen *base = a->wstack[0].s, *s = a->wstack[i].s;
	const scr_screen *parent = a->wstack[i - 1].s;
	if (!s) return VIEW_LEGACY;
	if (!strcmp(base->name, "article") && !strncmp(s->name, "article-", 8)) return VIEW_ARTICLE;
	if (viewport_is(s, "native-pinned")) return VIEW_PINNED;
	if (viewport_is(s, "native-centred")) return VIEW_CENTRED;
	/* Notice hides its parent without removing it. The chooser leaves Settings beneath it. */
	if (parent && !strcmp(parent->name, "settings") && viewport_is(parent, "native-centred") &&
	    (!strcmp(s->name, "notice") || !strcmp(s->name, "wordproc"))) return VIEW_CENTRED;
	if (!strcmp(s->name, "notice") &&
	    ((i == 1 && viewport_is(base, "native-root")) || viewport_is(parent, "native-pinned")))
		return VIEW_CENTRED;
	return VIEW_LEGACY;
}

int nav_viewport_compatible(const e26_app *a)
{
	int i;
	for (i = 1; i < a->nwin; i++)
		if (!a->wstack[i].hidden && overlay_viewport(a, i) == VIEW_LEGACY) return 0;
	return 1;
}

static int viewport_clamp(int origin, int extent, int limit)
{
	int end = limit > extent ? limit - extent : 0;
	return origin < 0 ? 0 : origin > end ? end : origin;
}

void nav_viewport_place(e26_app *a)
{
	const scr_screen *base = a->nwin ? a->wstack[0].s : NULL;
	int w, h, i;
	if (!base || a->nwin < 2) return;
	present_frame_bounds(a, &w, &h);
	for (i = 1; i < a->nwin; i++) {
		scr_screen *s = a->wstack[i].s;
		enum viewport_overlay kind = overlay_viewport(a, i);
		if (kind != VIEW_PINNED && kind != VIEW_CENTRED) continue;
		place(s, base);
		if (w == E26_W && h == E26_H) continue;
		if (kind == VIEW_CENTRED) {
			s->ox += (w - E26_W) / 2;
			s->oy += (h - E26_H) / 2;
		}
		s->ox = viewport_clamp(s->ox, s->w, w);
		s->oy = viewport_clamp(s->oy, s->h, h);
	}
}

void nav_snapshot(e26_app *a, e26_hist *h)
{
	memset(h, 0, sizeof(*h));
	if (a->have_art) snprintf(h->art_id, sizeof h->art_id, "%s", a->art_id);
	h->scroll = a->scroll;
	h->view = art_st()->is_v2 ? art_st()->view : 0;
	h->flt_cat = a->flt_cat;
	h->flt_sub = a->flt_sub;
	h->cur_cat = a->cur_cat;
	h->cur_sub = a->cur_sub;
}

/* Go Back remembers the full screen that was left (snap: what was in view on it, taken before the caller loaded
 * anything new). Leaving the title is not remembered; nor is a screen replacing itself, unless the article in view
 * changes (build 2 skipped that article on Back: plan E5) */
static void hist_push(e26_app *a, const char *from, const char *to, const e26_hist *snap)
{
	e26_hist now, *h;
	if (!snap) { nav_snapshot(a, &now); snap = &now; }
	if (!strcmp(from, "title")) return;
	if (!strcmp(from, to) && !(snap->art_id[0] && a->have_art && strcmp(snap->art_id, a->art_id))) return;
	if (a->nhist >= E26_HIST) {
		memmove(&a->hist[0], &a->hist[1], (E26_HIST - 1) * sizeof a->hist[0]); /* the oldest goes */
		a->nhist--;
	}
	h = &a->hist[a->nhist++];
	*h = *snap;
	snprintf(h->screen, sizeof h->screen, "%s", from);
}

void nav_go(e26_app *a, const char *name, int push) { nav_go_from(a, name, push, NULL); }

void nav_go_from(e26_app *a, const char *name, int push, const e26_hist *snap)
{
	scr_screen *s = scr_find(&a->set, name), *base = NULL, *prev = a->cur;
	if (!s) { fprintf(stderr, "d32library: no screen %s\n", name); return; }
	if (s->over[0]) {
		base = base_for(a, s);
		if (base) place(s, base);
	}
	if (a->nwin && !strcmp(a->wstack[0].s->name, "article") &&
	    strcmp((base ? base : s)->name, "article")) art_save_position(a);
	if (push && !s->over[0] && prev) {
		/* Go Back remembers full screens; leaving an overlay (the Find Wizard) remembers the screen under it */
		hist_push(a, prev->over[0] && a->base ? a->base->name : prev->name, name, snap);
	}
	memset(a->wstack, 0, sizeof a->wstack);
	a->nwin = 0;
	if (base && base != s) a->wstack[a->nwin++].s = base;
	a->wstack[a->nwin].s = s;
	a->wstack[a->nwin++].modal = s->over[0] != 0;
	focus = a->nwin - 1;
	entered(a, prev ? prev : s, name);
}

int nav_push_window(e26_app *a, const char *name, int modal)
{
	scr_screen *s = scr_find(&a->set, name), *prev = a->cur;
	if (!s || !s->over[0] || !a->nwin || a->nwin == E26_WSTACK) return -1;
	place(s, a->wstack[0].s);
	a->wstack[a->nwin].s = s;
	a->wstack[a->nwin].modal = modal;
	a->wstack[a->nwin].hidden = 0;
	focus = a->nwin++;
	entered(a, prev, name);
	return 0;
}

/* a press at (x,y) outside the focused non-modal window: the topmost window under it is raised and focused, or the
 * full screen is focused (the windows stay above it, inactive). 1 when the focus moved. */
int nav_focus_at(e26_app *a, int x, int y)
{
	int i, to = 0;
	if (a->nwin < 2 || a->wstack[focus].modal) return 0; /* a modal window takes every press */
	for (i = a->nwin - 1; i > 0; i--) {
		const scr_screen *s = a->wstack[i].s;
		if (!a->wstack[i].hidden && x >= s->ox && y >= s->oy && x < s->ox + s->w && y < s->oy + s->h) { to = i; break; }
	}
	if (to == focus) return 0;
	if (to > 0) {
		/* raise it: it moves to the top of the stack */
		e26_window w = a->wstack[to];
		memmove(&a->wstack[to], &a->wstack[to + 1], (size_t)(a->nwin - to - 1) * sizeof w);
		a->wstack[a->nwin - 1] = w;
		to = a->nwin - 1;
	}
	{
		scr_screen *prev = a->cur;
		focus = to;
		entered(a, prev, a->wstack[to].s->name);
	}
	return 1;
}

void nav_base_switch(e26_app *a, const char *name) { nav_base_switch_from(a, name, NULL); }

void nav_base_switch_from(e26_app *a, const char *name, const e26_hist *snap)
{
	scr_screen *s = scr_find(&a->set, name);
	e26_event ev;
	int i;
	if (!s || !a->cur->over[0] || s->over[0] || !a->nwin) return;
	if (!strcmp(a->wstack[0].s->name, "article") && strcmp(s->name, "article")) art_save_position(a);
	if (a->base) hist_push(a, a->base->name, name, snap);
	a->wstack[0].s = s;
	for (i = 1; i < a->nwin; i++) place(a->wstack[i].s, s);
	sync(a);
	a->hot = a->pressed = -1;
	memset(&ev, 0, sizeof ev);
	ev.type = EV_SCREEN_ENTER;
	ev.screen = s;
	ev.ctl = -1;
	ev.name = name;
	ev.switched = 1;
	ev_publish(a, &ev);
	nav_set_title(a);
	nav_redraw(a);
	if (a->verbose) fprintf(stderr, "d32library: screen %s under %s\n", name, a->cur->name);
}

/* Closing a window: it leaves the stack; a window a box had hidden (the Pinpointer under the notice box) shows
 * again and takes the focus, else the topmost window, else the full screen */
void nav_close_overlay(e26_app *a)
{
	scr_screen *prev = a->cur;
	int i;
	if (!a->cur || !a->cur->over[0] || a->nwin < 2 || focus == 0) return;
	memmove(&a->wstack[focus], &a->wstack[focus + 1], (size_t)(a->nwin - focus - 1) * sizeof a->wstack[0]);
	a->nwin--;
	focus = a->nwin - 1;
	for (i = a->nwin - 1; i > 0; i--)
		if (a->wstack[i].hidden) { a->wstack[i].hidden = 0; focus = i; break; }
	entered(a, prev, a->wstack[focus].s->name);
}

void nav_back(e26_app *a)
{
	e26_hist h;
	e26_event ev;
	if (a->nhist <= 0) return;
	h = a->hist[--a->nhist];
	/* the article in view when the screen was left comes back where it was (plan E5: Back never skips an article) */
	if (h.art_id[0] && !strcmp(h.screen, "article")) {
		if (!a->have_art || strcmp(a->art_id, h.art_id)) art_load(a, h.art_id);
		a->scroll = h.scroll;
	}
	memset(&ev, 0, sizeof ev);
	ev.type = EV_HISTORY_BACK;
	ev.hist = &h;
	ev.ctl = -1;
	ev_publish(a, &ev);
	nav_go(a, h.screen, 0);
}

void nav_notice_text(e26_app *a, const char *title, const char *text)
{
	snprintf(a->notice_title, sizeof a->notice_title, "%s", title);
	snprintf(a->notice_text, sizeof a->notice_text, "%s", text);
	a->menu = NULL;
	a->sub_open = 0;
	/* a box opened from a window (the Pinpointer's panels) stands in its place and returns to it when closed */
	if (a->cur && a->cur->over[0] && strcmp(a->cur->name, "notice") && a->nwin < E26_WSTACK) {
		scr_screen *s = scr_find(&a->set, "notice"), *prev = a->cur;
		if (!s) return;
		place(s, a->wstack[0].s);
		a->wstack[focus].hidden = 1;
		a->wstack[a->nwin].s = s;
		a->wstack[a->nwin].modal = 1;
		a->wstack[a->nwin].hidden = 0;
		focus = a->nwin++;
		entered(a, prev, "notice");
		return;
	}
	nav_go(a, "notice", 0);
}

/* "coming in a later build": the Library's own dialog style (the Settings dialog, capture256 63), screens/dialogs.scr */
void nav_notice(e26_app *a, const char *room)
{
	char t[64], text[300];
	size_t k;
	snprintf(t, sizeof t, "%s", room);
	for (k = 0; t[k]; k++) if (t[k] == '.') { t[k] = 0; break; } /* "Help..." -> "Help" */
	snprintf(text, sizeof text, "%s is coming in a later build of Deep32 Encyclopedia.", t);
	nav_notice_text(a, t, text);
}

int nav_focus(void) { return focus; }
