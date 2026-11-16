/* room_finder.h: FINDER's room seam. No Library database enters the UI process. */
#ifndef E26_ROOM_FINDER_H
#define E26_ROOM_FINDER_H
#include "library_app.h"
#include "finder_model.h"
finder_model *finder_st(void);
void finder_refresh(e26_app *a);
void finder_fields(e26_app *a, const scr_screen *s);
void finder_begin_fields(void);
void finder_search(e26_app *a, const scr_screen *s);
void finder_categories(e26_app *a, const char *const *ids, size_t n);
void finder_year_range(e26_app *a, int from, int to);
const finder_row *finder_row_at(int row);
void finder_scroll(e26_app *a, int delta, int rows);
const char *finder_wizard_page(size_t step);
/* u14 readability (spec M14, M16, S8, S9): the wide Pinpointer over a native viewport reads in the reader UI face
 * (F_RUI): pin_native() is true for screen "pinpointer" there, its list rows are pin_row_pitch() high (F_RUI ascent
 * + descent + 6, 26 px at UiScale 150; 16 px in the 640x480 letterbox) in pin_row_font() */
int pin_native(const e26_app *a, const scr_screen *s);
int pin_row_pitch(const e26_app *a, const scr_screen *s);
int pin_row_font(const e26_app *a, const scr_screen *s);
#define PIN_QUIET 0x4A4A4AL   /* quiet text (type tags, meta lines): the spec's quiet grey, 8.9:1 on white */
/* the ellipsis a face can draw: cp1252's 0x85 in the 1995 faces, "..." in the ISO 8859-1 bitmap faces (Helvetica) */
const char *pin_ellipsis(const e26_app *a, int font);
/* kind_finderresults.c: the rows the Search Results list holds (RESULT_H: 46 px, or 2 x (F_RUI height + 2) + 8
 * when the window is drawn natively) */
int finder_results_rows(const e26_app *a, const scr_screen *s);
/* the Pinpointer on the Entire Library with one Type (FINDER_*: FINDER_CISCO_CONFIG, ..., FINDER_TYPES = TechNotes)
 * and a sort (0 title, 1 identifier/number), from the Library home's "Browse the Library" */
void pin_browse_type(e26_app *a, int type, int sort);
#endif
