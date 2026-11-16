/* select.c: the X selections (W1-INPUT; select.h), through the Intrinsics (XtOwnSelection, XtGetSelectionValue; Xt
 * answers MULTIPLE and TIMESTAMP itself). */
#include "library_app.h"
#include "select.h"
#include <X11/Xatom.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* cp1252 0x80-0x9F as Unicode (0: no character) */
static const unsigned short cp_hi[32] = {
	0x20AC, 0, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030, 0x0160, 0x2039, 0x0152, 0, 0x017D, 0,
	0, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014, 0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0, 0x017E, 0x0178,
};

static unsigned cp_to_uni(unsigned char c)
{
	if (c >= 0x80 && c < 0xA0) return cp_hi[c - 0x80];
	return c;
}

size_t sel_cp1252_to_utf8(const char *in, size_t n, char *out, size_t cap)
{
	size_t i, k = 0;
	if (!cap) return 0;
	for (i = 0; i < n; i++) {
		unsigned u = cp_to_uni((unsigned char)in[i]);
		if (!u) u = '?';
		if (u < 0x80) { if (k + 1 >= cap) break; out[k++] = (char)u; }
		else if (u < 0x800) { if (k + 2 >= cap) break; out[k++] = (char)(0xC0 | (u >> 6)); out[k++] = (char)(0x80 | (u & 0x3F)); }
		else {
			if (k + 3 >= cap) break;
			out[k++] = (char)(0xE0 | (u >> 12));
			out[k++] = (char)(0x80 | ((u >> 6) & 0x3F));
			out[k++] = (char)(0x80 | (u & 0x3F));
		}
	}
	out[k] = 0;
	return k;
}

/* one UTF-8 sequence at in[*i] (advances *i); a broken sequence is one byte read as U+FFFD */
static unsigned utf8_next(const unsigned char *in, size_t n, size_t *i)
{
	unsigned c = in[*i], u, need, k;
	if (c < 0x80) { (*i)++; return c; }
	if (c >= 0xC2 && c < 0xE0) { need = 1; u = c & 0x1F; }
	else if (c >= 0xE0 && c < 0xF0) { need = 2; u = c & 0x0F; }
	else if (c >= 0xF0 && c < 0xF5) { need = 3; u = c & 0x07; }
	else { (*i)++; return 0xFFFD; }
	for (k = 1; k <= need; k++) {
		if (*i + k >= n || (in[*i + k] & 0xC0) != 0x80) { (*i)++; return 0xFFFD; }
		u = (u << 6) | (in[*i + k] & 0x3F);
	}
	*i += need + 1;
	return u;
}

size_t sel_utf8_to_cp1252(const char *in, size_t n, char *out, size_t cap)
{
	size_t i = 0, k = 0;
	const unsigned char *s = (const unsigned char *)in;
	if (!cap) return 0;
	while (i < n && k + 1 < cap) {
		unsigned u = utf8_next(s, n, &i);
		int c = -1, j;
		if (u == '\r') continue;               /* CR LF from other programs: the field keeps LF */
		if (u < 0x80 || (u >= 0xA0 && u < 0x100)) c = (int)u;
		else for (j = 0; j < 32; j++) if (cp_hi[j] && cp_hi[j] == u) c = 0x80 + j;
		if (c < 0) c = u == 0x00A0 ? ' ' : '?';
		out[k++] = (char)c;
	}
	out[k] = 0;
	return k;
}

size_t sel_cp1252_to_latin1(const char *in, size_t n, char *out, size_t cap)
{
	size_t i, k = 0;
	if (!cap) return 0;
	for (i = 0; i < n; i++) {
		unsigned u = cp_to_uni((unsigned char)in[i]);
		const char *r = NULL;
		char one[2];
		if (u < 0x100 && u) { one[0] = (char)u; one[1] = 0; r = one; }
		else switch (u) {
			case 0x2018: case 0x2019: case 0x201A: case 0x2039: case 0x203A: r = "'"; break;
			case 0x201C: case 0x201D: case 0x201E: r = "\""; break;
			case 0x2013: case 0x2014: r = "-"; break;
			case 0x2026: r = "..."; break;
			case 0x2022: r = "*"; break;
			case 0x20AC: r = "EUR"; break;
			case 0x2122: r = "TM"; break;
			default: r = "?"; break;
		}
		if (k + strlen(r) >= cap) break;
		memcpy(out + k, r, strlen(r));
		k += strlen(r);
	}
	out[k] = 0;
	return k;
}

int sel_prov_format(const sel_prov *p, char *out, size_t n)
{
	int r;
	if (!p->cls[0] || strpbrk(p->cls, " \n") || strpbrk(p->key, " \n") || strpbrk(p->version, " \n")) return -1;
	r = snprintf(out, n, "D32_PROVENANCE 1\nclass %s\nkey %s\nversion %s\n", p->cls, p->key[0] ? p->key : "-",
	             p->version[0] ? p->version : "-");
	return r < 0 || (size_t)r >= n ? -1 : r;
}

int sel_prov_parse(const char *in, size_t len, sel_prov *p)
{
	char buf[512], *line, *save = NULL;
	int got = 0;
	memset(p, 0, sizeof *p);
	if (!in || len >= sizeof buf) return -1;
	memcpy(buf, in, len);
	buf[len] = 0;
	line = strtok_r(buf, "\n", &save);
	if (!line || strcmp(line, "D32_PROVENANCE 1")) return -1;
	while ((line = strtok_r(NULL, "\n", &save))) {
		if (!strncmp(line, "class ", 6)) { snprintf(p->cls, sizeof p->cls, "%s", line + 6); got |= 1; }
		else if (!strncmp(line, "key ", 4)) { snprintf(p->key, sizeof p->key, "%s", line + 4); got |= 2; }
		else if (!strncmp(line, "version ", 8)) { snprintf(p->version, sizeof p->version, "%s", line + 8); got |= 4; }
	}
	if (got != 7 || (strcmp(p->cls, "library") && strcmp(p->cls, "corpus"))) return -1;
	return 0;
}

/* ---- X */

static Atom at_clipboard, at_utf8, at_targets, at_text, at_prov;

static struct owned {
	char *utf8, *latin;
	size_t nu, nl;
	char prov[400];
	int has_prov, owned;
} own[2];

static void atoms(Display *d)
{
	if (at_utf8) return;
	at_clipboard = XInternAtom(d, "CLIPBOARD", False);
	at_utf8 = XInternAtom(d, "UTF8_STRING", False);
	at_targets = XInternAtom(d, "TARGETS", False);
	at_text = XInternAtom(d, "TEXT", False);
	at_prov = XInternAtom(d, "D32_PROVENANCE", False);
}

static int which_of(Atom sel) { return sel == XA_PRIMARY ? SEL_PRIMARY : sel == at_clipboard ? SEL_CLIPBOARD : -1; }

static void drop(int w)
{
	free(own[w].utf8);
	free(own[w].latin);
	memset(&own[w], 0, sizeof own[w]);
}

static Boolean convert(Widget wd, Atom *sel, Atom *target, Atom *type, XtPointer *value, unsigned long *len, int *format)
{
	int w = which_of(*sel);
	(void)wd;
	if (w < 0 || !own[w].owned) return False;
	if (*target == at_targets) {
		Atom *t = (Atom *)XtMalloc(6 * sizeof(Atom));
		int n = 0;
		t[n++] = at_targets;
		t[n++] = at_utf8;
		t[n++] = XA_STRING;
		t[n++] = at_text;
		if (own[w].has_prov) t[n++] = at_prov;
		*type = XA_ATOM;
		*value = (XtPointer)t;
		*len = (unsigned long)n;
		*format = 32;
		return True;
	}
	if (*target == at_utf8 || *target == XA_STRING || *target == at_text || (*target == at_prov && own[w].has_prov)) {
		const char *src;
		size_t n;
		char *v;
		if (*target == at_utf8) { src = own[w].utf8; n = own[w].nu; *type = at_utf8; }
		else if (*target == at_prov) { src = own[w].prov; n = strlen(own[w].prov); *type = at_prov; }
		else { src = own[w].latin; n = own[w].nl; *type = XA_STRING; }
		v = XtMalloc((Cardinal)(n ? n : 1));
		if (n) memcpy(v, src, n);
		*value = (XtPointer)v;
		*len = (unsigned long)n;
		*format = 8;
		return True;
	}
	return False;
}

static void lose(Widget wd, Atom *sel)
{
	int w = which_of(*sel);
	(void)wd;
	if (w >= 0) drop(w);
}

int sel_own(e26_app *a, int which, const char *text, size_t n, const sel_prov *prov)
{
	size_t cap = n * 3 + 1;
	Atom sel;
	if (which != SEL_PRIMARY && which != SEL_CLIPBOARD) return -1;
	drop(which);
	if (!(own[which].utf8 = (char *)malloc(cap)) || !(own[which].latin = (char *)malloc(cap))) { drop(which); return -1; }
	own[which].nu = sel_cp1252_to_utf8(text, n, own[which].utf8, cap);
	own[which].nl = sel_cp1252_to_latin1(text, n, own[which].latin, cap);
	if (prov && sel_prov_format(prov, own[which].prov, sizeof own[which].prov) > 0) own[which].has_prov = 1;
	if (!a || !a->dpy || !a->top) { own[which].owned = 1; return 0; } /* headless (the tests): held, not announced */
	atoms(a->dpy);
	sel = which == SEL_PRIMARY ? XA_PRIMARY : at_clipboard;
	if (!XtOwnSelection(a->top, sel, XtLastTimestampProcessed(a->dpy), convert, lose, NULL)) {
		fprintf(stderr, "d32library: select: could not take the %s selection\n", which == SEL_PRIMARY ? "PRIMARY" : "CLIPBOARD");
		drop(which);
		return -1;
	}
	own[which].owned = 1;
	if (a->verbose)
		fprintf(stderr, "d32library: select: %s holds %lu bytes%s\n", which == SEL_PRIMARY ? "PRIMARY" : "CLIPBOARD",
		        (unsigned long)own[which].nu, own[which].has_prov ? " with D32_PROVENANCE" : "");
	return 0;
}

int sel_owned(int which) { return (which == SEL_PRIMARY || which == SEL_CLIPBOARD) && own[which].owned; }

const char *sel_text(int which, size_t *n)
{
	if (!sel_owned(which)) return NULL;
	if (n) *n = own[which].nu;
	return own[which].utf8;
}

/* a paste in three asks: D32_PROVENANCE (any owner may not have it), UTF8_STRING, then STRING */
typedef struct {
	e26_app *a;
	sel_paste_fn fn;
	void *ud;
	Atom sel;
	int stage;
	sel_prov prov;
	int has_prov;
} paste_req;

static void ask(paste_req *r);

static void got(Widget w, XtPointer cd, Atom *sel, Atom *type, XtPointer value, unsigned long *len, int *format)
{
	paste_req *r = (paste_req *)cd;
	(void)w; (void)sel;
	if (r->stage == 0) {
		if (value && *type == at_prov && *format == 8 && !sel_prov_parse((const char *)value, (size_t)*len, &r->prov)) r->has_prov = 1;
		if (value) XtFree((char *)value);
		r->stage = 1;
		ask(r);
		return;
	}
	if (value && *format == 8 && (*type == at_utf8 || *type == XA_STRING)) {
		size_t n = (size_t)*len, cap = n + 1, k;
		char *out = (char *)malloc(cap);
		if (out) {
			if (*type == at_utf8) k = sel_utf8_to_cp1252((const char *)value, n, out, cap);
			else { /* ISO 8859-1 is cp1252 outside 0x80-0x9F, which STRING never carries */
				size_t i;
				for (i = k = 0; i < n; i++) if (((const char *)value)[i] != '\r') out[k++] = ((const char *)value)[i];
				out[k] = 0;
			}
			r->fn(r->a, out, k, r->has_prov ? &r->prov : NULL, r->ud);
			free(out);
		}
		XtFree((char *)value);
		free(r);
		return;
	}
	if (value) XtFree((char *)value);
	if (r->stage == 1) { r->stage = 2; ask(r); return; }
	r->fn(r->a, NULL, 0, NULL, r->ud); /* nothing to paste */
	free(r);
}

static void ask(paste_req *r)
{
	Atom target = r->stage == 0 ? at_prov : r->stage == 1 ? at_utf8 : XA_STRING;
	XtGetSelectionValue(r->a->top, r->sel, target, got, (XtPointer)r, XtLastTimestampProcessed(r->a->dpy));
}

int sel_request(e26_app *a, int which, sel_paste_fn fn, void *ud)
{
	paste_req *r;
	if (!a || !a->dpy || !a->top || !fn) return -1;
	atoms(a->dpy);
	if (!(r = (paste_req *)calloc(1, sizeof *r))) return -1;
	r->a = a;
	r->fn = fn;
	r->ud = ud;
	r->sel = which == SEL_PRIMARY ? XA_PRIMARY : at_clipboard;
	ask(r);
	return 0;
}
