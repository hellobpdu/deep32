/* focus.h: keyboard focus and the Tab walk (W1-INPUT, plan 3.2 W14 and E7).
 *
 * Every screen has at most one control in focus. Tab and Shift+Tab walk the screen's tab order (screens/input.keys
 * binds them to focus.next and focus.prev); Enter runs the control in focus (focus.activate); typing goes to the edit
 * field in focus (kind_edit.c). The order of a screen, first that applies:
 *   1. its own "prop tab-order <id> <id> ..." (a room writes this in its screen file);
 *   2. its line in screens/focus.order (the orders measured on the 1995 captures for the build-2 screens: the Tab
 *      walk of the Text, Outline view, capture256 176-188);
 *   3. the screen file's order of the controls that take focus: not hidden, not the caption (absolute), not a scroll
 *      bar or its arrows, "prop tabstop no" excluded; a control with an action, an interactive kind or an edit kind;
 *      a control made from a MAGICTABSTOP record (10040, label "MAGICTABSTOP") is a stop with no look of its own.
 * Disabled and hidden controls are passed over as the walk reaches them (Go Back with no history, capture256 163-188).
 *
 * The look (capture256 163-189): the period design draws focus by recolouring, never with a dotted rectangle, and the looks
 * it uses are the controls' own "under the pointer" looks; so a control the keyboard put in focus is drawn hot
 * (a->hot). Once the keys are in the text pane (a click in the text), Tab changes nothing (capture256 189). */
#ifndef E26_FOCUS_H
#define E26_FOCUS_H
#include "library_app.h"

int focus_get(e26_app *a, const scr_screen *s);              /* the control in focus on s, or -1 */
void focus_set(e26_app *a, const scr_screen *s, int i, int shown); /* shown: the keyboard put it there (drawn hot) */
int focus_shown(e26_app *a, const scr_screen *s, int i);     /* 1: i is in focus and drawn as such */
int focus_takes(e26_app *a, scr_screen *s, int i);           /* 1: i takes focus now (in the order, shown, enabled) */
/* the tab order of s into out (control indices, at most max); returns the count */
int focus_order(e26_app *a, scr_screen *s, int *out, int max);
/* Tab (dir 1) or Shift+Tab (-1) on the front screen; 1 when the focus moved */
int focus_step(e26_app *a, int dir);
/* 1 when an edit field has the keys on the front screen (the hook for the 99 type-to-find touch: W2-ARTICLE and
 * W2-FINDER open the Pinpointer on a letter only when this is 0) */
int focus_field_active(e26_app *a);
/* a screen was entered: its focus starts on "prop focus <id>", else its first edit field, else nowhere */
void focus_enter(e26_app *a, const scr_screen *s);
/* a press on control i of s (-1: none): the focus follows it when i takes focus; a press in the text pane keeps the
 * keys there */
void focus_press(e26_app *a, scr_screen *s, int i);
/* a key that reached a kind's late keys (the article's arrows, Home, End): the keys are in the text pane now */
void focus_note_key(e26_app *a, unsigned long keysym);

/* screens/focus.order ("<screen> <id> <id> ..."): read from dir (d32library's screen directory) on first use */
int focus_load_order(const char *dir);
/* the lint: every line of focus.order names a loaded screen and only controls it has. The number of errors. */
int focus_order_lint(scr_set *set, char *report, size_t n);
/* 1 when control c was made from a MAGICTABSTOP record (looked up in the store once per record) */
int focus_magic(const char *store, const scr_control *c);

#endif
