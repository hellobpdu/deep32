/* article_clipboard.c: v2 keeps UTF-8 rather than passing it through INPUT's cp1252-only sel_own. */
#include "article_clipboard.h"
#include "library_app.h"
#include <X11/Xatom.h>
#include <stdlib.h>
#include <string.h>

static char *utf8, *latin;
static char provenance[512];
static Atom targets, utf8_atom, text_atom, prov_atom, clipboard;

static Boolean convert(Widget w, Atom *selection, Atom *target, Atom *type, XtPointer *value,
                       unsigned long *length, int *format)
{
	const char *s = NULL;
	(void)w; (void)selection;
	if (*target == targets) {
		Atom *a = (Atom *)XtMalloc(5 * sizeof(Atom));
		if (!a) return False;
		a[0] = targets; a[1] = utf8_atom; a[2] = XA_STRING; a[3] = text_atom; a[4] = prov_atom;
		*value = (XtPointer)a; *type = XA_ATOM; *format = 32; *length = provenance[0] ? 5 : 4;
		return True;
	}
	if (*target == utf8_atom || *target == text_atom) { s = utf8; *type = utf8_atom; }
	else if (*target == XA_STRING) { s = latin; *type = XA_STRING; }
	else if (*target == prov_atom && provenance[0]) { s = provenance; *type = prov_atom; }
	if (!s) return False;
	*length = (unsigned long)strlen(s); *format = 8;
	*value = (XtPointer)XtMalloc((Cardinal)(*length + 1));
	if (!*value) return False;
	memcpy(*value, s, (size_t)*length + 1);
	return True;
}

int ar_clipboard(e26_app *a, const char *text, const sel_prov *p)
{
	char *u, *l, *cp;
	size_t n = strlen(text);
	if (n > 64u * 1024u * 1024u) return -1;
	u = strdup(text); l = (char *)malloc(n + 1); cp = (char *)malloc(n + 1);
	if (!u || !l || !cp) { free(u); free(l); free(cp); return -1; }
	sel_utf8_to_cp1252(text, n, cp, n + 1);
	sel_cp1252_to_latin1(cp, strlen(cp), l, n + 1); free(cp);
	free(utf8); free(latin); utf8 = u; latin = l;
	provenance[0] = 0;
	if (p) (void)sel_prov_format(p, provenance, sizeof provenance);
	if (!a || !a->top || !a->dpy) return 0;
	targets = XInternAtom(a->dpy, "TARGETS", False); utf8_atom = XInternAtom(a->dpy, "UTF8_STRING", False);
	text_atom = XInternAtom(a->dpy, "TEXT", False); prov_atom = XInternAtom(a->dpy, "D32_PROVENANCE", False);
	clipboard = XInternAtom(a->dpy, "CLIPBOARD", False);
	if (!XtOwnSelection(a->top, clipboard, XtLastTimestampProcessed(a->dpy), convert, NULL, NULL)) return -1;
	(void)XtOwnSelection(a->top, XA_PRIMARY, XtLastTimestampProcessed(a->dpy), convert, NULL, NULL);
	return 0;
}

const char *ar_clipboard_text(void) { return utf8; }
