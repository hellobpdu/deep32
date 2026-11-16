/* kind_edit.c: kind field, the edit field (W1-INPUT; edit.h has the grammar, the look and the keys). The editing core
 * (edit_key, edit_wrap) takes keysyms and text and draws nothing, so tests/test_input.c drives it without a display. */
#include "edit.h"
#include "cite.h"
#include "focus.h"
#include "select.h"
#include <X11/Xutil.h>
#include <X11/keysym.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define C_HILITE 0xA6CAF0L   /* the capture lane's highlight (capture256 29), as kind_wheel.c's field */
#define ED_INSET 4           /* text from 4 px in (capture256 84) */

/* ---- the editing core */

static int grow(e26_field *f, int need)
{
	char *nt;
	int cap;
	if (need + 1 <= f->cap) return 0;
	cap = f->cap ? f->cap : 64;
	while (cap < need + 1) cap *= 2;
	if (!(nt = (char *)realloc(f->t, (size_t)cap))) return -1;
	if (!f->t) nt[0] = 0;
	f->t = nt;
	f->cap = cap;
	return 0;
}

static int limit(const e26_field *f) { return f->maxlen > 0 ? f->maxlen : f->multiline ? 65535 : 255; }

int edit_selection(const e26_field *f, int *from, int *to)
{
	int a = f->anchor < f->caret ? f->anchor : f->caret, b = f->anchor < f->caret ? f->caret : f->anchor;
	if (from) *from = a;
	if (to) *to = b;
	return b > a;
}

int edit_delete_selection(e26_field *f)
{
	int a, b;
	if (!edit_selection(f, &a, &b)) return 0;
	memmove(f->t + a, f->t + b, (size_t)(f->len - b + 1));
	f->len -= b - a;
	f->caret = f->anchor = a;
	return 1;
}

int edit_insert(e26_field *f, const char *s, int n)
{
	int room, k, i;
	if (grow(f, f->len) < 0) return 0;
	edit_delete_selection(f);
	room = limit(f) - f->len;
	if (n > room) n = room < 0 ? 0 : room;
	/* one line holds no line breaks or tabs: they become spaces (a paste of several lines) */
	if (n <= 0 || grow(f, f->len + n) < 0) return 0;
	memmove(f->t + f->caret + n, f->t + f->caret, (size_t)(f->len - f->caret + 1));
	for (i = k = 0; i < n; i++) {
		char c = s[i];
		if (c == '\t' || (!f->multiline && c == '\n')) c = ' ';
		f->t[f->caret + k++] = c;
	}
	f->len += n;
	f->caret += n;
	f->anchor = f->caret;
	return n;
}

int edit_set(e26_field *f, const char *text)
{
	if (grow(f, 0) < 0) return -1;
	f->t[0] = 0;
	f->len = f->caret = f->anchor = f->top = 0;
	f->library = 0;
	f->prov_key[0] = 0;
	edit_insert(f, text, (int)strlen(text));
	return 0;
}

static int is_word(char c) { return (unsigned char)c > ' ' && !strchr(".,;:!?()[]{}\"'", c); }

void edit_word_at(const e26_field *f, int pos, int *from, int *to)
{
	int a = pos, b = pos;
	if (pos < f->len && is_word(f->t[pos])) {
		while (a > 0 && is_word(f->t[a - 1])) a--;
		while (b < f->len && is_word(f->t[b])) b++;
	} else if (pos < f->len) b = pos + 1;
	*from = a;
	*to = b;
}

static int word_left(const e26_field *f, int p)
{
	while (p > 0 && !is_word(f->t[p - 1])) p--;
	while (p > 0 && is_word(f->t[p - 1])) p--;
	return p;
}

static int word_right(const e26_field *f, int p)
{
	while (p < f->len && is_word(f->t[p])) p++;
	while (p < f->len && !is_word(f->t[p])) p++;
	return p;
}

static int line_start(const e26_field *f, int p) { while (p > 0 && f->t[p - 1] != '\n') p--; return p; }
static int line_end(const e26_field *f, int p) { while (p < f->len && f->t[p] != '\n') p++; return p; }

int edit_key(e26_field *f, unsigned long ks, unsigned state, const char *typed, int ntyped)
{
	int shift = (state & ShiftMask) != 0, ctrl = (state & ControlMask) != 0, a, b, to = -1;
	if (grow(f, f->len) < 0) return ED_NONE;
	if (state & Mod1Mask) return ED_NONE;                         /* Alt+: the menus and the rooms' keys */
	if (ks == XK_KP_Enter) ks = XK_Return;
	if ((ctrl && ks == XK_c) || (ctrl && ks == XK_Insert)) return ED_COPY;
	if ((ctrl && ks == XK_x) || (shift && !ctrl && ks == XK_Delete)) return ED_CUT;
	if ((ctrl && ks == XK_v) || (shift && !ctrl && ks == XK_Insert)) return ED_PASTE;
	switch (ks) {
	case XK_Left: case XK_KP_Left:
		if (!shift && edit_selection(f, &a, &b)) to = a;
		else to = ctrl ? word_left(f, f->caret) : f->caret > 0 ? f->caret - 1 : 0;
		break;
	case XK_Right: case XK_KP_Right:
		if (!shift && edit_selection(f, &a, &b)) to = b;
		else to = ctrl ? word_right(f, f->caret) : f->caret < f->len ? f->caret + 1 : f->len;
		break;
	case XK_Home: case XK_KP_Home: to = ctrl ? 0 : line_start(f, f->caret); break;
	case XK_End: case XK_KP_End: to = ctrl ? f->len : line_end(f, f->caret); break;
	case XK_BackSpace:
		if (!edit_delete_selection(f)) {
			if (!f->caret) return ED_MOVED;
			a = ctrl ? word_left(f, f->caret) : f->caret - 1;
			f->anchor = a;
			edit_delete_selection(f);
		}
		return ED_CHANGED;
	case XK_Delete: case XK_KP_Delete:
		if (!edit_delete_selection(f)) {
			if (f->caret >= f->len) return ED_MOVED;
			f->anchor = ctrl ? word_right(f, f->caret) : f->caret + 1;
			edit_delete_selection(f);
		}
		return ED_CHANGED;
	case XK_Return:
		/* several lines: Shift+Enter breaks the line (Enter too when the field has no action: edit_key's caller
		 * decides, by sending Shift) */
		if (f->multiline && shift) { edit_insert(f, "\n", 1); return ED_CHANGED; }
		return ED_SUBMIT;
	default:
		break;
	}
	if (to >= 0) {
		f->caret = to;
		if (!shift) f->anchor = to;
		return ED_MOVED;
	}
	if (ctrl) return ED_NONE;                                     /* Ctrl+B, Ctrl+R ...: the screen's keys */
	if (ntyped == 1 && (unsigned char)typed[0] >= 0x20 && (unsigned char)typed[0] != 0x7f) {
		edit_insert(f, typed, 1);
		return ED_CHANGED;
	}
	return ED_NONE;                                               /* Tab, Esc, the function keys */
}

int edit_wrap(const char *t, int len, int width, edit_measure m, void *ud, int *starts, int max)
{
	int n = 0, s = 0;
	if (max < 1) return 0;
	starts[n++] = 0;
	while (s < len && n < max) {
		int e = s, brk = -1, w;
		/* the longest run from s that fits; a '\n' ends the line */
		while (e < len && t[e] != '\n') {
			w = m(ud, t + s, e - s + 1);
			if (w > width && e > s) break;
			if (t[e] == ' ') brk = e + 1;
			e++;
		}
		if (e >= len) break;
		if (t[e] == '\n') { s = e + 1; starts[n++] = s; continue; }
		/* passed the width: break after the last space, else inside the word */
		s = brk > s ? brk : e;
		starts[n++] = s;
	}
	return n;
}

int edit_line_of(const int *starts, int nlines, int pos)
{
	int l = 0;
	while (l + 1 < nlines && starts[l + 1] <= pos) l++;
	return l;
}

/* ---- the fields of the screens */

typedef struct {
	const scr_screen *s;
	int ctl;
	e26_field f;
} field_slot;
static field_slot *slots;
static int nslots;

e26_field *field_of(const scr_screen *s, int ctl)
{
	int i;
	field_slot *ns;
	const char *p;
	if (!s || ctl < 0 || ctl >= s->nc) return NULL;
	for (i = 0; i < nslots; i++) if (slots[i].s == s && slots[i].ctl == ctl) return &slots[i].f;
	if (!(ns = (field_slot *)realloc(slots, (size_t)(nslots + 1) * sizeof *slots))) return NULL;
	slots = ns;
	memset(&slots[nslots], 0, sizeof slots[nslots]);
	slots[nslots].s = s;
	slots[nslots].ctl = ctl;
	p = scr_prop_get(&s->c[ctl], "multiline");
	slots[nslots].f.multiline = p && (!strcmp(p, "yes") || !strcmp(p, "1") || !strcmp(p, "on"));
	if ((p = scr_prop_get(&s->c[ctl], "maxlen"))) slots[nslots].f.maxlen = atoi(p);
	grow(&slots[nslots].f, 0);
	if ((p = scr_prop_get(&s->c[ctl], "text"))) edit_insert(&slots[nslots].f, p, (int)strlen(p));
	return &slots[nslots++].f;
}

const char *field_text(const scr_screen *s, const char *id)
{
	scr_control *c = s ? scr_control_find((scr_screen *)s, id) : NULL;
	e26_field *f = c ? field_of(s, (int)(c - s->c)) : NULL;
	return f && f->t ? f->t : NULL;
}

int field_set_text(const scr_screen *s, const char *id, const char *text)
{
	scr_control *c = s ? scr_control_find((scr_screen *)s, id) : NULL;
	e26_field *f = c ? field_of(s, (int)(c - s->c)) : NULL;
	return f ? edit_set(f, text) : -1;
}

int field_library(const scr_screen *s, const char *id)
{
	scr_control *c = s ? scr_control_find((scr_screen *)s, id) : NULL;
	e26_field *f = c ? field_of(s, (int)(c - s->c)) : NULL;
	return f ? f->library : 0;
}

/* ---- the kind */

typedef struct { gfx_ctx g; int font; } meas;

static int measure(void *ud, const char *s, int n)
{
	meas *m = (meas *)ud;
	return gfx_text_width(&m->g, m->font, s, n);
}

static int pitch(gfx_ctx *g, int font) { return gfx_font_height(g, font) + 1; }

#define ED_MAXLINES 4096
static int lines_of(e26_app *a, const scr_control *c, const e26_field *f, int *starts)
{
	meas m;
	m.g = a->g;
	m.font = c->font;
	if (!f->multiline) { starts[0] = 0; return 1; }
	return edit_wrap(f->t, f->len, c->w - 2 * ED_INSET, measure, &m, starts, ED_MAXLINES);
}

static int visible_lines(e26_app *a, const scr_control *c)
{
	int v = (c->h - 4) / pitch(&a->g, c->font);
	return v < 1 ? 1 : v;
}

/* keeps the caret in view */
static void follow(e26_app *a, const scr_control *c, e26_field *f)
{
	static int starts[ED_MAXLINES];
	if (f->multiline) {
		int n = lines_of(a, c, f, starts), l = edit_line_of(starts, n, f->caret), v = visible_lines(a, c);
		if (l < f->top) f->top = l;
		if (l >= f->top + v) f->top = l - v + 1;
		if (f->top > n - 1) f->top = n - 1 < 0 ? 0 : n - 1;
	} else {
		int x = gfx_text_width(&a->g, c->font, f->t, f->caret), room = c->w - 2 * ED_INSET - 2;
		if (x - f->top > room) f->top = x - room;
		if (x < f->top) f->top = x;
		if (f->top < 0) f->top = 0;
	}
}

static void draw(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int x, int y, int under)
{
	static int starts[ED_MAXLINES];
	const scr_control *c = &s->c[i];
	e26_field *f = field_of(s, i);
	int focused = s == a->cur && focus_get(a, s) == i && !under, sa = 0, sb = 0, sel, n, l, v, lh, ox;
	if (!f) return;
	sel = edit_selection(f, &sa, &sb);
	gfx_fill(g, x, y, c->w, c->h, c->bg >= 0 ? c->bg : C_WHITE);
	if (c->sunken) { gfx_line(g, x, y, x + c->w - 1, y, C_BLACK); gfx_line(g, x, y, x, y + c->h - 1, C_BLACK); }
	gfx_clip(g, x + 1, y + 1, c->w - 2, c->h - 2);
	if (!f->multiline) {
		int pen = x + ED_INSET - f->top, by = y + c->h - 5;
		/* the selection on rows 2-15 behind its letters, the caret after the text on the same rows (capture256 29, 84) */
		if (sel) {
			int x0 = gfx_text_width(g, c->font, f->t, sa), x1 = gfx_text_width(g, c->font, f->t, sb);
			gfx_fill(g, pen + x0, y + 2, x1 - x0, 14, C_HILITE);
		}
		gfx_text(g, pen, by, c->font, c->fg >= 0 ? c->fg : C_BLACK, f->t, f->len);
		if (focused && !sel) gfx_fill(g, pen + gfx_text_width(g, c->font, f->t, f->caret), y + 2, 2, 14, C_BLACK);
	} else {
		n = lines_of(a, c, f, starts);
		v = visible_lines(a, c);
		lh = pitch(g, c->font);
		ox = x + ED_INSET;
		for (l = f->top; l < n && l < f->top + v; l++) {
			int ls = starts[l], le = l + 1 < n ? starts[l + 1] : f->len, ty = y + 2 + (l - f->top) * lh, shown = le - ls;
			if (shown > 0 && f->t[ls + shown - 1] == '\n') shown--;
			if (sel && sb > ls && sa < le) {
				int a0 = sa > ls ? sa : ls, b0 = sb < ls + shown ? sb : ls + shown;
				int x0 = gfx_text_width(g, c->font, f->t + ls, a0 - ls), x1 = gfx_text_width(g, c->font, f->t + ls, b0 - ls);
				if (sb > ls + shown && l + 1 < n) x1 += 4; /* the line break itself selected */
				gfx_fill(g, ox + x0, ty, x1 - x0, lh, C_HILITE);
			}
			gfx_text(g, ox, ty + gfx_ascent(g, c->font), c->font, c->fg >= 0 ? c->fg : C_BLACK, f->t + ls, shown);
			if (focused && !sel && f->caret >= ls && (f->caret < le || l == n - 1) && edit_line_of(starts, n, f->caret) == l)
				gfx_fill(g, ox + gfx_text_width(g, c->font, f->t + ls, f->caret - ls), ty, 2, lh - 1, C_BLACK);
		}
	}
	gfx_unclip(g);
}

/* the byte under frame point (px, py) of control i */
static int pos_at(e26_app *a, const scr_screen *s, int i, int px, int py)
{
	static int starts[ED_MAXLINES];
	const scr_control *c = &s->c[i];
	e26_field *f = field_of(s, i);
	int cx = c->x + (c->absolute ? 0 : s->ox), cy = c->y + (c->absolute ? 0 : s->oy), ls, le, l, n, k, best;
	if (!f) return 0;
	if (f->multiline) {
		n = lines_of(a, c, f, starts);
		l = f->top + (py - cy - 2) / pitch(&a->g, c->font);
		if (py < cy + 2) l = f->top - 1;
		if (l < 0) l = 0;
		if (l >= n) return f->len;
		ls = starts[l];
		le = l + 1 < n ? starts[l + 1] : f->len;
		if (le > ls && f->t[le - 1] == '\n') le--;
		px -= cx + ED_INSET;
	} else {
		ls = 0;
		le = f->len;
		px -= cx + ED_INSET - f->top;
	}
	/* the boundary nearest the point */
	best = ls;
	for (k = ls; k <= le; k++) {
		int w = gfx_text_width(&a->g, c->font, f->t + ls, k - ls);
		if (w <= px) best = k;
		if (k < le && w <= px && px - w > (gfx_text_width(&a->g, c->font, f->t + ls, k + 1 - ls) - w) / 2) best = k + 1;
	}
	return best;
}

static const scr_screen *drag_s;
static int drag_i = -1;
static const kind_ops *field_ops;

/* the selection becomes PRIMARY whenever it is not empty (X convention) */
static void own_primary(e26_app *a, const e26_field *f)
{
	int sa, sb;
	sel_prov p;
	if (!edit_selection(f, &sa, &sb)) return;
	memset(&p, 0, sizeof p);
	if (f->library) { snprintf(p.cls, sizeof p.cls, "library"); snprintf(p.key, sizeof p.key, "%s", f->prov_key[0] ? f->prov_key : "-"); snprintf(p.version, sizeof p.version, "-"); }
	sel_own(a, SEL_PRIMARY, f->t + sa, (size_t)(sb - sa), f->library ? &p : NULL);
}

static double now_ms(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}

static int press(e26_app *a, scr_screen *s, int i, int x, int y)
{
	static double last;
	static int lx, ly, li = -1;
	static const scr_screen *ls;
	e26_field *f = field_of(s, i);
	double t = now_ms();
	int p;
	if (!f) return 0;
	focus_set(a, s, i, 0);
	p = pos_at(a, s, i, x, y);
	if (ls == s && li == i && t - last < 500 && abs(x - lx) <= 3 && abs(y - ly) <= 3) {
		/* a double click selects the word */
		edit_word_at(f, p, &f->anchor, &f->caret);
		own_primary(a, f);
		last = 0;
	} else {
		f->caret = f->anchor = p;
		last = t;
	}
	ls = s; li = i; lx = x; ly = y;
	drag_s = s;
	drag_i = i;
	a->drag_ops = field_ops;
	a->hot = -1;
	nav_redraw(a);
	return 1;
}

static void drag(e26_app *a, int x, int y)
{
	e26_field *f;
	int p, was;
	if (drag_s != a->cur || drag_i < 0 || !(f = field_of(drag_s, drag_i))) return;
	p = pos_at(a, drag_s, drag_i, x, y);
	if (p == f->caret) return;
	was = edit_selection(f, NULL, NULL);
	f->caret = p;
	follow(a, &drag_s->c[drag_i], f);
	if (edit_selection(f, NULL, NULL) || was) own_primary(a, f);
	nav_redraw(a);
}

static int wheel(e26_app *a, scr_screen *s, int i, int dir)
{
	static int starts[ED_MAXLINES];
	e26_field *f = field_of(s, i);
	int n;
	if (!f || !f->multiline) return 0;
	n = lines_of(a, &s->c[i], f, starts);
	f->top += 3 * dir;
	if (f->top > n - visible_lines(a, &s->c[i])) f->top = n - visible_lines(a, &s->c[i]);
	if (f->top < 0) f->top = 0;
	return 1;
}

/* a paste: the field is looked up again (the screen may have changed while the owner answered) */
typedef struct { const scr_screen *s; int ctl; } paste_to;

static void pasted(e26_app *a, const char *text, size_t n, const sel_prov *prov, void *ud)
{
	paste_to *p = (paste_to *)ud;
	e26_field *f = text ? field_of(p->s, p->ctl) : NULL;
	if (f) {
		edit_insert(f, text, (int)n);
		if ((prov && !strcmp(prov->cls, "library")) || cite_has_trailer(text)) {
			f->library = 1;
			if (prov) snprintf(f->prov_key, sizeof f->prov_key, "%s", prov->key);
		}
		follow(a, &p->s->c[p->ctl], f);
		if (a->verbose) fprintf(stderr, "d32library: field %s: pasted %lu bytes%s\n", p->s->c[p->ctl].id, (unsigned long)n, f->library ? " (Deep32 Encyclopedia text)" : "");
		if (p->s == a->cur) nav_redraw(a);
	}
	free(p);
}

int field_paste(e26_app *a, int which)
{
	int i = a && a->cur ? focus_get(a, a->cur) : -1;
	paste_to *p;
	if (i < 0 || !focus_field_active(a) || a->cur->c[i].kind == K_EDIT) return 0;
	if (!(p = (paste_to *)malloc(sizeof *p))) return 0;
	p->s = a->cur;
	p->ctl = i;
	if (sel_request(a, which, pasted, p) < 0) { free(p); return 0; }
	return 1;
}

static void copy_selection(e26_app *a, e26_field *f)
{
	int sa, sb;
	sel_prov p;
	if (!edit_selection(f, &sa, &sb)) return;
	memset(&p, 0, sizeof p);
	if (f->library) { snprintf(p.cls, sizeof p.cls, "library"); snprintf(p.key, sizeof p.key, "%s", f->prov_key[0] ? f->prov_key : "-"); snprintf(p.version, sizeof p.version, "-"); }
	sel_own(a, SEL_CLIPBOARD, f->t + sa, (size_t)(sb - sa), f->library ? &p : NULL);
}

static int vertical(e26_app *a, const scr_control *c, e26_field *f, int dir, int shift)
{
	static int starts[ED_MAXLINES];
	int n = lines_of(a, c, f, starts), l = edit_line_of(starts, n, f->caret), x, t, k, le, best;
	x = gfx_text_width(&a->g, c->font, f->t + starts[l], f->caret - starts[l]);
	t = l + dir;
	if (t < 0 || t >= n) return 0;
	le = t + 1 < n ? starts[t + 1] : f->len;
	if (le > starts[t] && f->t[le - 1] == '\n') le--;
	best = starts[t];
	for (k = starts[t]; k <= le; k++) if (gfx_text_width(&a->g, c->font, f->t + starts[t], k - starts[t]) <= x) best = k;
	f->caret = best;
	if (!shift) f->anchor = best;
	return 1;
}

/* typing: the field in focus on this screen takes it (input.c offers it to the first edit kind; that kind passes it
 * to the field in focus) */
static int key_focus(e26_app *a, scr_screen *s, int i0, XKeyEvent *ev, KeySym ks)
{
	char typed[8];
	int i = focus_get(a, s), n, r, had;
	e26_field *f;
	const scr_control *c;
	(void)i0;
	if (i < 0 || i >= s->nc || s->c[i].kind != reg_find(REG_KIND, EDIT_KIND) || s->c[i].hidden || !(f = field_of(s, i))) return 0;
	c = &s->c[i];
	n = XLookupString(ev, typed, sizeof typed - 1, NULL, NULL);
	had = edit_selection(f, NULL, NULL);
	if (f->multiline && (ks == XK_Up || ks == XK_Down) && !(ev->state & (ControlMask | Mod1Mask))) {
		vertical(a, c, f, ks == XK_Up ? -1 : 1, (ev->state & ShiftMask) != 0);
		r = ED_MOVED;
	} else if (f->multiline && (ks == XK_Return || ks == XK_KP_Enter) && c->action == A_NONE && !(ev->state & (ControlMask | Mod1Mask))) {
		r = edit_key(f, XK_Return, ShiftMask, typed, n); /* no action: Enter is a new line */
	} else r = edit_key(f, ks, ev->state, typed, n);
	switch (r) {
	case ED_NONE: return 0;
	case ED_SUBMIT:
		if (c->action == A_NONE) return 0; /* the screen's own Enter (a dialog's default button) */
		{
			e26_actx x;
			memset(&x, 0, sizeof x);
			x.s = s;
			x.ctl = i;
			x.arg = c->arg;
			x.label = "";
			if (action_run(a, c->action, &x)) nav_redraw(a);
		}
		return 1;
	case ED_COPY: copy_selection(a, f); return 1;
	case ED_CUT: copy_selection(a, f); edit_delete_selection(f); break;
	case ED_PASTE: field_paste(a, SEL_CLIPBOARD); return 1;
	default: break;
	}
	if (edit_selection(f, NULL, NULL) && (r == ED_MOVED || !had)) own_primary(a, f);
	follow(a, c, f);
	nav_redraw(a);
	return 1;
}

void e26_mod_kind_edit(void);
void e26_mod_kind_edit(void)
{
	static const kind_ops ops = { draw, 1, press, NULL, NULL, wheel, drag, key_focus, NULL };
	field_ops = &ops;
	reg_add(REG_KIND, EDIT_KIND, &ops, "kind_edit", 0);
}
