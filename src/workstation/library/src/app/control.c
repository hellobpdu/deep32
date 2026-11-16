/* control.c: what every kind shares: a control's text (its label, its original string or record, or its source), its
 * state, the enabled rule, its strip frame by state, and the class's frame pens. */
#include "library_app.h"
#include "focus.h"
#include <stdio.h>
#include <string.h>

const kind_ops *kind_of(int kind) { return (const kind_ops *)reg_impl(REG_KIND, kind); }

const source_ops *source_of(const scr_control *c)
{
	int id;
	if (!c->source[0] || (id = reg_find(REG_SOURCE, c->source)) < 0) return NULL;
	return (const source_ops *)reg_impl(REG_SOURCE, id);
}

scr_control *find_kind(scr_screen *s, int kind)
{
	int i;
	for (i = 0; i < s->nc; i++)
		if (s->c[i].kind == kind) return &s->c[i];
	return NULL;
}

/* 26246 -> "26,246", as the Pinpointer's count shows it (capture 83) */
void group_digits(unsigned long v, char *out, size_t n)
{
	char t[32];
	int len, i, k = 0;
	snprintf(t, sizeof t, "%lu", v);
	len = (int)strlen(t);
	for (i = 0; i < len && k + 2 < (int)n; i++) {
		if (i && (len - i) % 3 == 0) out[k++] = ',';
		out[k++] = t[i];
	}
	out[k] = 0;
}

long contrast(const e26_app *a, long bg)
{
	long r = (bg >> 16) & 255, g = (bg >> 8) & 255, b = bg & 255;
	return (r * 299 + g * 587 + b * 114) / 1000 > 140 ? C_BLACK : C_WHITE;
}

/* Windows label conventions in the original strings (records.c rec_label): "&&" is "&", an "&" before a letter or
 * digit marks a mnemonic, any other "&" is drawn ("Charts & Tables", string 14723, capture256 12) */
static void win_label(const char *in, char *out, size_t n) { rec_label(in, out, n, 0); }

const char *control_text(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	const source_ops *so;
	buf[0] = 0;
	if (c->label_sid && a->have_str) {
		const char *o = rec_string(&a->str, c->label_sid);
		if (o) {
			/* the Pinpointer's help strings break their lines with '|' and mark a key name with it ("|ENTER|",
			 * string 14713): a line break, and the key name in the small face (GFX_KEY_ON ... GFX_KEY_OFF) */
			char *p;
			win_label(o, buf, n);
			while ((p = strstr(buf, "|ENTER|"))) { p[0] = GFX_KEY_ON; p[6] = GFX_KEY_OFF; }
			for (p = buf; *p; p++) if (*p == '|') *p = '\n';
			return buf;
		}
	}
	if (!c->label[0] && c->record && !c->source[0]) {
		/* a text button's label from its original control record ('#' is the record's line break) */
		rec_control r;
		if (!rec_control_load(a->store, c->record, &r) && !r.bitmap_button && strcmp(r.label, "MAGICTABSTOP")) {
			char *h;
			win_label(r.label, buf, n);
			while ((h = strchr(buf, '#'))) *h = ' ';
			return buf;
		}
	}
	if ((so = source_of(c)) && so->text) {
		const char *t = so->text(a, c, buf, n);
		if (t) return t;
	}
	snprintf(buf, n, "%s", c->label);
	return buf;
}

int control_enabled(e26_app *a, scr_screen *s, int i)
{
	const scr_control *c = &s->c[i];
	if (c->disabled) return 0;
	if (c->action == A_BACK && a->nhist == 0) return 0;
	if (c->enabled_by[0]) {
		if (!strncmp(c->enabled_by, "group:", 6)) {
			int k;
			for (k = 0; k < s->nc; k++)
				if (!strcmp(s->c[k].group, c->enabled_by + 6) && s->c[k].checked) return 1;
			return 0;
		} else {
			scr_control *e = scr_control_find(s, c->enabled_by);
			return e && e->checked;
		}
	}
	return 1;
}

/* checked, or set by the program through its source: the Pinpointer's Category button shows frame 1 while a category
 * filter applies (capture256 09, 10, 12: ENCORG_CATEG frame 1 exact, 0 of 2,695 pixels; with its panel open, frame 3) */
int control_checked(e26_app *a, const scr_control *c)
{
	const source_ops *so = source_of(c);
	if (so && so->checked) {
		int v = so->checked(a, c);
		if (v >= 0) return v;
	}
	return c->checked;
}

int control_state(e26_app *a, scr_screen *s, int i)
{
	int base = control_checked(a, &s->c[i]) ? S_CHECKED : S_NORMAL;
	if (!control_enabled(a, s, i)) return base + S_DISABLED;
	if (s == a->cur && a->pressed == i && a->hot == i) return base + S_PRESSED;
	/* the control the keyboard put in focus keeps its focus look when the pointer moves (capture256 163-188) */
	if (s == a->cur && a->pressed < 0 && (a->hot == i || focus_shown(a, s, i))) return base + S_HOT;
	return base;
}

int frame_for(const scr_control *c, int st)
{
	static const int fallback[S_NSTATES] = { -1, S_NORMAL, S_NORMAL, S_NORMAL, S_NORMAL, S_CHECKED, S_CHECKED, S_DISABLED };
	int guard = 0;
	while (st >= 0 && guard++ < 4) {
		if (c->frame_of[st] >= 0) return c->frame_of[st];
		st = fallback[st];
	}
	return 0;
}

/* The class's frame pens by state (PALETTE.md 3; seg18:088a, seg19:0b8c): at rest the idle frame colour all round,
 * under the pointer light top-left and dark bottom-right, pressed (or down) the reverse, disabled the disabled
 * frame colour. */
void class_pens(const scr_control *c, int st, int en, long *tl, long *br)
{
	const long *sl = c->slot;
	int pr = (st == S_PRESSED || st == S_CHECKED || st == S_CHECKED_HOT || st == S_CHECKED_PRESSED);
	if (!en) *tl = *br = sl[REC_FRAME_DISABLED];
	else if (pr) { *tl = sl[REC_EDGE_DARK]; *br = sl[REC_EDGE_LIGHT]; }
	else if (st == S_HOT) { *tl = sl[REC_EDGE_LIGHT]; *br = sl[REC_EDGE_DARK]; }
	else *tl = *br = sl[REC_FRAME_IDLE];
}
