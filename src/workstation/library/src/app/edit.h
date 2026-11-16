/* edit.h: edit fields (W1-INPUT, plan 3.2 E7): kind "field" (kind_edit.c), one line or several.
 *
 * A screen declares one as a control:
 *     control ask-field
 *         kind field
 *         rect 10 300 400 60
 *         sunken                      # the inset edge (a 1-px black top and left, as the Category field)
 *         prop multiline yes          # wrap at the field's width, Shift+Enter for a new line
 *         prop maxlen 4000            # bytes (default 255 on one line, 65535 on several)
 *         action ask.send "Ask"       # Enter runs it (on several lines Shift+Enter breaks the line instead)
 *     end
 * The look follows the Pinpointer's word-wheel field, the only edit field the 1995 captures show (capture256 29, 84):
 * white ground, Arial 11 black from 4 px in, a 2-px black caret on rows 2-15, the selection black on the capture
 * lane's highlight #A6CAF0 with no caret. The caret does not blink (calm, and every render is deterministic).
 *
 * Keys in a field (only while it is in focus, focus.h): characters insert (replacing the selection); Backspace,
 * Delete; Left, Right, Home, End, and Up, Down on several lines, with Shift to select and Ctrl to move by word (Home
 * and End: the whole text); Ctrl+C or Ctrl+Insert copies, Ctrl+X or Shift+Delete cuts, Ctrl+V or Shift+Insert pastes
 * (CLIPBOARD, select.c); Enter runs the field's action; Shift+Enter breaks the line on several lines. Every other
 * chord (Ctrl+B, Alt+F, Tab, Esc) is left to the screen's keys. A selection made with the pointer or the keys becomes
 * the PRIMARY selection. Text is cp1252, the period fonts' code page; the clipboard speaks UTF-8 (select.c). */
#ifndef E26_EDIT_H
#define E26_EDIT_H
#include "library_app.h"

#define EDIT_KIND "field"

typedef struct {
	char *t;            /* the text (cp1252), NUL-terminated */
	int len, cap;
	int caret, anchor;  /* byte offsets; the selection is [min, max) of the two */
	int top;            /* several lines: the first line in view; one line: the first pixel in view */
	int multiline, maxlen;
	int library;        /* holds pasted Deep32 Library text (D32_PROVENANCE, or the cite's trailer) */
	char prov_key[96];  /* the item key the provenance named */
} e26_field;

/* what a key did (edit_key) */
enum { ED_NONE, ED_CHANGED, ED_MOVED, ED_SUBMIT, ED_COPY, ED_CUT, ED_PASTE };

/* the editing core, X-free in behaviour (the tests drive it): ks a keysym, state the X modifier mask, typed the
 * characters XLookupString gave (cp1252 / Latin-1) */
int edit_key(e26_field *f, unsigned long ks, unsigned state, const char *typed, int ntyped);
int edit_insert(e26_field *f, const char *s, int n);     /* replaces the selection; returns the bytes inserted */
int edit_delete_selection(e26_field *f);                  /* 1 when something went */
int edit_selection(const e26_field *f, int *from, int *to); /* 1 when the selection is not empty */
void edit_word_at(const e26_field *f, int pos, int *from, int *to);
int edit_set(e26_field *f, const char *text);             /* replaces the text, caret at its end */

/* line layout for several lines: line starts into starts (at most max); returns the number of lines (at least 1). A
 * line breaks at '\n' and after the last space that fits width; a word wider than width breaks where it passes it. */
typedef int (*edit_measure)(void *ud, const char *s, int n);
int edit_wrap(const char *t, int len, int width, edit_measure m, void *ud, int *starts, int max);
int edit_line_of(const int *starts, int nlines, int pos); /* the line holding byte pos */

/* the fields of the screens (one per control of kind field, made on first use) */
e26_field *field_of(const scr_screen *s, int ctl);
const char *field_text(const scr_screen *s, const char *id);       /* NULL when s has no such field */
int field_set_text(const scr_screen *s, const char *id, const char *text);
int field_library(const scr_screen *s, const char *id);            /* 1: it holds pasted Library text (ASK's chip) */
/* paste into the field in focus on the front screen (the middle button pastes PRIMARY there: input.c change request) */
int field_paste(e26_app *a, int which);

#endif
