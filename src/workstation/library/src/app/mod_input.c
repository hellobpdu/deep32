/* mod_input.c: W1-INPUT's actions and its part of the event bus (plan 3.2: E7, W14, A20's clipboard half, L8's
 * provenance part, F15's keys). The keys themselves are in screens/input.keys.
 *
 * Actions (named, so a screen file, a menu or a key file can name them; the argument is a label unless said):
 *   focus.next, focus.prev   Tab and Shift+Tab: the next or previous control of the screen's tab order (focus.h)
 *   focus.activate           Enter: runs the control in focus as a click would
 *   input.press <id>         runs control <id> of the front screen (or of the screen under it) as a click would: the
 *                            menu keys (Alt+A area, Alt+C category, Alt+F Find, Alt+E Features, Alt+T Tools, Alt+I the
 *                            (i) menu, Alt+V Views, Alt+Space the Control menu; the period design's "Use Your Keyboard")
 *   input.close              Alt+F4: closes the window in front, or Deep32 Library on a full screen
 *   edit.copy                Ctrl+C, Ctrl+Insert with no edit field in focus: W2-ARTICLE's article.copy when it is
 *                            built; until then the open article, whole, with its cite (the period design: "If no text is
 *                            selected, copy entire article"), on CLIPBOARD and PRIMARY with D32_PROVENANCE
 *   edit.paste               pastes CLIPBOARD into the field in focus (a menu's Paste)
 * Events: a screen entered resets its focus; a press moves the focus and starts hold-to-repeat (repeat.h). */
#include "library_app.h"
#include "cite.h"
#include "edit.h"
#include "focus.h"
#include "repeat.h"
#include "select.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int a_next(e26_app *a, const e26_actx *x) { (void)x; return focus_step(a, 1); }
static int a_prev(e26_app *a, const e26_actx *x) { (void)x; return focus_step(a, -1); }

static int run_ctl(e26_app *a, scr_screen *s, int i)
{
	e26_actx x;
	const scr_control *c = &s->c[i];
	if (c->action == A_NONE || !control_enabled(a, s, i) || c->hidden) return 1;
	memset(&x, 0, sizeof x);
	x.s = s;
	x.ctl = i;
	x.arg = c->arg;
	x.label = "";
	return action_run(a, c->action, &x);
}

static int a_activate(e26_app *a, const e26_actx *x)
{
	int i = focus_get(a, a->cur);
	(void)x;
	if (i < 0 || i >= a->cur->nc) return 1;
	return run_ctl(a, a->cur, i);
}

static int a_press(e26_app *a, const e26_actx *x)
{
	scr_control *c;
	scr_screen *s = a->cur;
	if (!s || !x->arg || !x->arg[0]) return 1;
	/* the window in front only (a key reaches the focused window); a screen without the control: nothing happens */
	if (!(c = scr_control_find(s, x->arg))) return 1;
	return run_ctl(a, s, (int)(c - s->c));
}

static int a_close(e26_app *a, const e26_actx *x)
{
	(void)x;
	if (a->cur && a->cur->over[0] && a->base) { nav_close_overlay(a); return 0; }
	e26_quit(a);
	return 0;
}

/* the open article as plain text: headings with their numbers, paragraphs, figures as set (build 2's article; the
 * article format 2 copy is W2-ARTICLE's) */
static char *article_text(e26_app *a, size_t *len)
{
	size_t cap = 4096, k = 0;
	char *t = (char *)malloc(cap);
	int i;
	if (!t) return NULL;
	t[0] = 0;
	for (i = 0; i < a->art.nb; i++) {
		const art_block *b = &a->art.b[i];
		size_t need = strlen(b->text) + strlen(b->number) + 8;
		if (k + need >= cap) {
			char *nt;
			while (k + need >= cap) cap *= 2;
			if (!(nt = (char *)realloc(t, cap))) { free(t); return NULL; }
			t = nt;
		}
		if (b->kind == B_HEADING && b->number[0]) k += (size_t)snprintf(t + k, cap - k, "%s%s  %s\n", k ? "\n" : "", b->number, b->text);
		else k += (size_t)snprintf(t + k, cap - k, "%s%s\n", k ? "\n" : "", b->text);
	}
	*len = k;
	return t;
}

/* the cite of build 2's article file (id, title, source, licence): an RFC excerpt from the IETF pack is Library text
 * (article format 2: a missing class is library, never corpus) */
static void article_cite(e26_app *a, cite_src *c, char *num, size_t nn, char *sec, size_t ns, char *anchor, size_t na, char *lines, size_t nl)
{
	const char *p;
	int i;
	memset(c, 0, sizeof *c);
	c->cls = CITE_LIBRARY;
	c->kind = CITE_OTHER;
	c->id = a->art.id;
	c->title = a->art.title;
	c->licence = a->art.licence[0] ? a->art.licence : NULL;
	c->block = 1;
	num[0] = sec[0] = anchor[0] = lines[0] = 0;
	if (!strncmp(a->art.id, "rfc", 3) && a->art.id[3] >= '0' && a->art.id[3] <= '9') {
		c->kind = CITE_RFC;
		snprintf(num, nn, "%d", atoi(a->art.id + 3));
		c->number = num;
	}
	for (i = 0; i < a->art.nb; i++)
		if (a->art.b[i].kind == B_HEADING && a->art.b[i].number[0]) {
			snprintf(sec, ns, "section %s, %s", a->art.b[i].number, a->art.b[i].text);
			snprintf(anchor, na, "section-%s", a->art.b[i].number);
			c->section = sec;
			c->anchor = anchor;
			break;
		}
	/* "source: corpus/rfc/rfc2328.txt lines 255-614" -> "lines 255-614 of rfc2328.txt" */
	if ((p = strstr(a->art.source, " lines "))) {
		const char *f = a->art.source, *slash;
		for (slash = f; slash < p; slash++) if (*slash == '/') f = slash + 1;
		snprintf(lines, nl, "%s of %.*s", p + 1, (int)(p - f), f);
		c->lines = lines;
	}
}

static int a_copy(e26_app *a, const e26_actx *x)
{
	int art = reg_find(REG_ACTION, "article.copy"), n;
	size_t len, cap;
	char *text, *out, num[16], sec[200], anchor[40], lines[200];
	cite_src c;
	sel_prov p;
	if (focus_field_active(a)) {
		/* a menu's Copy while a field has the keys: as Ctrl+C in the field */
		e26_field *f = field_of(a->cur, focus_get(a, a->cur));
		int sa, sb;
		if (f && edit_selection(f, &sa, &sb)) sel_own(a, SEL_CLIPBOARD, f->t + sa, (size_t)(sb - sa), NULL);
		return 1;
	}
	if (art >= 0 && action_built(art)) return action_run(a, art, x);
	if (!a->have_art || !(text = article_text(a, &len))) return 1;
	article_cite(a, &c, num, sizeof num, sec, sizeof sec, anchor, sizeof anchor, lines, sizeof lines);
	cap = len + 4096;
	if (!(out = (char *)malloc(cap))) { free(text); return 1; }
	n = cite_build(&c, text, out, cap);
	if (n > 0) {
		memset(&p, 0, sizeof p);
		snprintf(p.cls, sizeof p.cls, "%s", cite_class_name(c.cls));
		snprintf(p.key, sizeof p.key, "%s", a->art.id);
		snprintf(p.version, sizeof p.version, "%s%.64s", a->art.source_sha256[0] ? "sha256:" : "-", a->art.source_sha256);
		/* Copy with cite: CLIPBOARD and PRIMARY carry the same text (LIBRARY-DESIGN 4.9) */
		sel_own(a, SEL_CLIPBOARD, out, (size_t)n, &p);
		sel_own(a, SEL_PRIMARY, out, (size_t)n, &p);
	}
	free(out);
	free(text);
	return 1;
}

static int a_paste(e26_app *a, const e26_actx *x)
{
	(void)x;
	field_paste(a, SEL_CLIPBOARD);
	return 1;
}

static void on_enter(e26_app *a, const e26_event *ev, void *ud)
{
	(void)ud;
	if (ev->screen) focus_enter(a, ev->screen);
}

static int repeat_step(e26_app *a, scr_screen *s, int ctl, void *ud)
{
	int r;
	(void)ud;
	r = run_ctl(a, s, ctl);
	if (r) nav_redraw(a);
	if (a->verbose) fprintf(stderr, "d32library: repeat: %s step %d\n", s->c[ctl].id, repeat_steps());
	return control_enabled(a, s, ctl);
}

static void on_press(e26_app *a, const e26_event *ev, void *ud)
{
	scr_screen *s = ev->screen;
	(void)ud;
	if (!s) return;
	focus_press(a, s, ev->ctl);
	if (ev->ctl >= 0 && ev->ctl < s->nc && repeat_wanted(&s->c[ev->ctl]) && control_enabled(a, s, ev->ctl))
		repeat_hold(a, s, ev->ctl, repeat_step, NULL);
}

void e26_mod_mod_input(void);
void e26_mod_mod_input(void)
{
	static const e26_action next = { a_next }, prev = { a_prev }, activate = { a_activate }, press = { a_press },
	                        close_ = { a_close }, copy = { a_copy }, paste = { a_paste };
	reg_add(REG_ACTION, "focus.next", &next, "mod_input", 0);
	reg_add(REG_ACTION, "focus.prev", &prev, "mod_input", 0);
	reg_add(REG_ACTION, "focus.activate", &activate, "mod_input", 0);
	reg_add(REG_ACTION, "input.press", &press, "mod_input", 0);
	reg_add(REG_ACTION, "input.close", &close_, "mod_input", 0);
	reg_add(REG_ACTION, "edit.copy", &copy, "mod_input", 0);
	reg_add(REG_ACTION, "edit.paste", &paste, "mod_input", 0);
	ev_subscribe(EV_SCREEN_ENTER, on_enter, NULL);
	ev_subscribe(EV_PRESS, on_press, NULL);
}
