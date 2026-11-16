#ifndef E26_APPEARANCE_H
#define E26_APPEARANCE_H

#include "library_app.h"

typedef struct {
	const char *id, *label, *description, *reader_font;
	long title, colours[6];
} e26_theme;

extern const e26_theme e26_themes[];
extern const size_t e26_theme_count;
size_t e26_theme_index(const char *id);
const char *e26_theme_reader_font(size_t index);
void e26_theme_preview_palette(const e26_app *a, size_t index, rec_palette *out);
void e26_theme_apply_bands(e26_app *a);
/* u14 M9: the theme's reading-rule colour (LC_RULE), 0xRRGGBB */
long e26_theme_rule(size_t index);
/* u14 S6: a colour of the reading paper (E26_PAPER_WHITE or E26_PAPER_WARM), 0xRRGGBB */
enum { E26_PAPER_GROUND, E26_PAPER_TEXT, E26_PAPER_LINK, E26_PAPER_QUIET };
long e26_paper_rgb(int paper, int role);

#endif
