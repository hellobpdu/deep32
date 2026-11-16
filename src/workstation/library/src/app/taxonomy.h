/* taxonomy.h: the owner's categories (item 40: the Library's category MECHANISM, his subjects). Reads either
 *  - library/TAXONOMY.json (schema "deep32-d32library-taxonomy/1"): categories with "label" as a list of menu
 *    lines, "bar_label", "colour": {"face", "ink"}, "menu": [column, row], "subcategories": [{"id","label"}],
 *    "examples": [{"kind": "corpus", "doc_key", "sub"}], and "geometry": {"category_menu": {"columns","rows"}};
 *  - or the simple build-1 placeholder: "label" a string, "colour" a string, "watermark" an asset name,
 *    "subcategories": [{"id","label","articles":[...]}].
 * Aliases tolerated: "name" for "label", "color" for "colour", "children" for "subcategories".
 * A "watermark" is used as an asset only when it names one (contains '/'); TAXONOMY.json's watermarks are
 * descriptions of art still to be drawn. */
#ifndef E26_TAXONOMY_H
#define E26_TAXONOMY_H
#include <stddef.h>

#define TAX_MAX_CAT 32
#define TAX_MAX_SUB 32
#define TAX_TEXT 96

typedef struct {
	char id[TAX_TEXT], label[TAX_TEXT], article[TAX_TEXT]; /* article: an article id or a corpus doc_key */
} tax_sub;

typedef struct {
	char id[TAX_TEXT];
	char label[TAX_TEXT];   /* menu label; '\n' separates the two lines of an Library-style item */
	char bar[TAX_TEXT];     /* one-line label for the Area/Category bars */
	char watermark[160];    /* an asset name, or "" */
	long colour, ink;       /* face and ink, 0xRRGGBB, -1 unset (the art strips keep these exact colours) */
	/* UI chrome (bars, flyouts) takes his colours snapped to the period design's 32-entry interface table, as the original's
	 * menu records name table entries (main.c taxonomy_colours; AUTHENTICITY.md 17): entry and RGB, -1 unset */
	int face_entry, ink_entry;
	long chrome_face, chrome_ink;
	char areas[8][8];       /* IETF areas this category draws on ("draws_on": "ietf-areas: RTG, ..."), lower case */
	int nareas;
	int col, row;           /* position in the Category menu grid, 0-based */
	tax_sub sub[TAX_MAX_SUB];
	int nsub;
} tax_cat;

typedef struct {
	char status[TAX_TEXT], schema[TAX_TEXT], source[512];
	int placeholder;
	int cols, rows;         /* Category menu grid */
	tax_cat cat[TAX_MAX_CAT];
	int ncat;
} taxonomy;

int tax_load(const char *path, taxonomy *t, char *err, size_t errlen);

#endif
