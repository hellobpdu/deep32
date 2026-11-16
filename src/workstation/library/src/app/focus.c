/* focus.c: keyboard focus and the Tab walk (W1-INPUT; focus.h). */
#include "focus.h"
#include "edit.h"
#include <X11/keysym.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* per screen of the set (indexed by its place in a->set.s) */
static struct fstate {
	int ctl;          /* the control in focus, -1 none */
	int shown;        /* the keyboard put it there */
	int captured;     /* the keys are in the text pane: Tab changes nothing (capture256 189) */
	int init;
} st[SCR_MAX_SCREENS];

/* screens/focus.order */
typedef struct {
	char screen[SCR_ID];
	char **id;
	int n;
	int line;
} order_line;
static order_line *orders;
static int norders, loaded;

static struct fstate *state_of(e26_app *a, const scr_screen *s)
{
	long k;
	if (!a || !s || !a->set.s) return NULL;
	k = (long)(s - a->set.s);
	if (k < 0 || k >= a->set.ns || k >= SCR_MAX_SCREENS) return NULL;
	if (!st[k].init) { st[k].ctl = -1; st[k].init = 1; }
	return &st[k];
}

int focus_load_order(const char *dir)
{
	char path[1024], line[2048];
	FILE *f;
	int lineno = 0;
	loaded = 1;
	if (!dir || !dir[0]) return 0;
	snprintf(path, sizeof path, "%s/focus.order", dir);
	if (!(f = fopen(path, "r"))) return 0;
	while (fgets(line, sizeof line, f)) {
		char *p = strchr(line, '#'), *tok, *save = NULL;
		order_line *o, *no;
		lineno++;
		if (p) *p = 0;
		if (!(tok = strtok_r(line, " \t\r\n", &save))) continue;
		if (!(no = (order_line *)realloc(orders, (size_t)(norders + 1) * sizeof *orders))) break;
		orders = no;
		o = &orders[norders++];
		memset(o, 0, sizeof *o);
		snprintf(o->screen, sizeof o->screen, "%s", tok);
		o->line = lineno;
		while ((tok = strtok_r(NULL, " \t\r\n", &save))) {
			char **nid = (char **)realloc(o->id, (size_t)(o->n + 1) * sizeof(char *));
			if (!nid) break;
			o->id = nid;
			if (!(o->id[o->n] = strdup(tok))) break;
			o->n++;
		}
	}
	fclose(f);
	return norders;
}

static const order_line *order_for(e26_app *a, const char *screen)
{
	int i;
	if (!loaded) focus_load_order(a ? a->screens : NULL);
	for (i = 0; i < norders; i++)
		if (!strcmp(orders[i].screen, screen)) return &orders[i];
	return NULL;
}

int focus_order_lint(scr_set *set, char *report, size_t n)
{
	int i, j, errors = 0;
	size_t k;
	if (report && n) report[0] = 0;
	for (i = 0; i < norders; i++) {
		scr_screen *s = scr_find(set, orders[i].screen);
		if (!s) {
			errors++;
			if (report && (k = strlen(report)) + 1 < n) snprintf(report + k, n - k, "focus.order:%d: no screen %s\n", orders[i].line, orders[i].screen);
			continue;
		}
		for (j = 0; j < orders[i].n; j++)
			if (!scr_control_find(s, orders[i].id[j])) {
				errors++;
				if (report && (k = strlen(report)) + 1 < n)
					snprintf(report + k, n - k, "focus.order:%d: %s has no control %s\n", orders[i].line, s->name, orders[i].id[j]);
			}
	}
	return errors;
}

int focus_magic(const char *store, const scr_control *c)
{
	static struct { int record, magic; } cache[64];
	static int ncache;
	rec_control r;
	int i, m;
	if (!c->record || !store || !store[0]) return 0;
	for (i = 0; i < ncache; i++) if (cache[i].record == c->record) return cache[i].magic;
	m = !rec_control_load(store, c->record, &r) && !strcmp(r.label, "MAGICTABSTOP");
	if (ncache < (int)(sizeof cache / sizeof cache[0])) { cache[ncache].record = c->record; cache[ncache].magic = m; ncache++; }
	return m;
}

static int is_field_kind(int kind)
{
	int fk = reg_find(REG_KIND, EDIT_KIND);
	return kind == K_EDIT || (fk >= 0 && kind == fk);
}

/* the default rule: does control i belong to the walk at all (rule 3 of focus.h) */
static int in_default(e26_app *a, const scr_screen *s, int i)
{
	const scr_control *c = &s->c[i];
	const kind_ops *k = kind_of(c->kind);
	const char *ts = scr_prop_get(c, "tabstop");
	if (c->absolute) return 0;
	if (ts && !strcmp(ts, "no")) return 0;
	if (ts && !strcmp(ts, "yes")) return 1;
	if (focus_magic(a ? a->store : NULL, c)) return 1;
	if (c->kind == K_SCROLL || c->action == A_SCROLL_UP || c->action == A_SCROLL_DOWN) return 0;
	if (c->action != A_NONE) return 1;
	return k && (k->interactive || k->key_focus);
}

int focus_order(e26_app *a, scr_screen *s, int *out, int max)
{
	const char *p = scr_screen_prop(s, "tab-order");
	const order_line *o;
	int n = 0, i;
	if (p) {
		char buf[1024], *tok, *save = NULL;
		snprintf(buf, sizeof buf, "%s", p);
		for (tok = strtok_r(buf, " \t", &save); tok && n < max; tok = strtok_r(NULL, " \t", &save)) {
			scr_control *c = scr_control_find(s, tok);
			if (c) out[n++] = (int)(c - s->c);
		}
		return n;
	}
	if ((o = order_for(a, s->name))) {
		for (i = 0; i < o->n && n < max; i++) {
			scr_control *c = scr_control_find(s, o->id[i]);
			if (c) out[n++] = (int)(c - s->c);
		}
		return n;
	}
	for (i = 0; i < s->nc && n < max; i++)
		if (in_default(a, s, i)) out[n++] = i;
	return n;
}

int focus_takes(e26_app *a, scr_screen *s, int i)
{
	int ord[256], n, k;
	if (i < 0 || i >= s->nc || s->c[i].hidden || !control_enabled(a, s, i)) return 0;
	n = focus_order(a, s, ord, 256);
	for (k = 0; k < n; k++) if (ord[k] == i) return 1;
	return 0;
}

int focus_get(e26_app *a, const scr_screen *s)
{
	struct fstate *f = state_of(a, s);
	return f ? f->ctl : -1;
}

void focus_set(e26_app *a, const scr_screen *s, int i, int shown)
{
	struct fstate *f = state_of(a, s);
	if (!f) return;
	f->ctl = i;
	f->shown = i >= 0 && shown;
	f->captured = 0;
}

int focus_shown(e26_app *a, const scr_screen *s, int i)
{
	struct fstate *f = state_of(a, s);
	return f && i >= 0 && f->ctl == i && f->shown;
}

int focus_step(e26_app *a, int dir)
{
	scr_screen *s = a->cur;
	struct fstate *f = state_of(a, s);
	int ord[256], n, k, at = -1, tries;
	if (!f || f->captured) return 0;
	n = focus_order(a, s, ord, 256);
	if (!n) return 0;
	for (k = 0; k < n; k++) if (ord[k] == f->ctl) at = k;
	if (at < 0) at = dir > 0 ? n - 1 : 0; /* nothing in focus: Tab starts at the first stop, Shift+Tab at the last */
	for (tries = 0; tries < n; tries++) {
		at = ((at + dir) % n + n) % n;
		if (!s->c[ord[at]].hidden && control_enabled(a, s, ord[at])) {
			f->ctl = ord[at];
			f->shown = 1;
			/* the period look: the control's own "under the pointer" look (capture256 163-188) */
			a->hot = ord[at];
			a->pressed = -1;
			return 1;
		}
	}
	return 0;
}

int focus_field_active(e26_app *a)
{
	int i = a && a->cur ? focus_get(a, a->cur) : -1;
	return i >= 0 && i < a->cur->nc && is_field_kind(a->cur->c[i].kind);
}

void focus_enter(e26_app *a, const scr_screen *s)
{
	struct fstate *f = state_of(a, s);
	const char *want;
	int ord[256], n, k;
	if (!f) return;
	f->ctl = -1;
	f->shown = 0;
	f->captured = 0;
	if ((want = scr_screen_prop(s, "focus"))) {
		scr_control *c = scr_control_find((scr_screen *)s, want);
		if (c) f->ctl = (int)(c - s->c);
		return;
	}
	n = focus_order(a, (scr_screen *)s, ord, 256);
	for (k = 0; k < n; k++)
		if (s->c[ord[k]].kind != K_EDIT && is_field_kind(s->c[ord[k]].kind) && !s->c[ord[k]].hidden) { f->ctl = ord[k]; return; }
}

void focus_press(e26_app *a, scr_screen *s, int i)
{
	struct fstate *f = state_of(a, s);
	if (!f) return;
	if (i >= 0 && i < s->nc && s->c[i].kind == K_ARTICLE) {
		/* a click in the text: the keys stay in the text pane (capture256 189) */
		f->ctl = i;
		f->shown = 0;
		f->captured = 1;
		return;
	}
	if (i >= 0 && focus_takes(a, s, i)) {
		f->ctl = i;
		f->shown = 0;
		f->captured = 0;
	}
}

void focus_note_key(e26_app *a, unsigned long ks)
{
	struct fstate *f = a ? state_of(a, a->cur) : NULL;
	int i;
	if (!f) return;
	if (ks != XK_Up && ks != XK_Down && ks != XK_Prior && ks != XK_Next && ks != XK_Home && ks != XK_End) return;
	for (i = 0; i < a->cur->nc; i++)
		if (a->cur->c[i].kind == K_ARTICLE) { f->ctl = i; f->shown = 0; f->captured = 1; return; }
}
