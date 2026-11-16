/* layout.h: rich article layout (build 3, W1-LAYOUT; plan A2, A3 layout half, A7 kind half, A16, W13). An article,
 * format 2 (article2.h) or build 1's RFC text (article.h), becomes a display list in document coordinates: text
 * items (one face, one colour, one jump), fills, frames, rules, fallback glyph boxes and media boxes; the lines that
 * hold them (for hit-testing and selection); the anchors (headings, footnotes, media); the jumps; and the outline's
 * entries (every heading, plus figures, tables and code exhibits under their heading).
 *
 * Every drawn character keeps its place in the article's canonical plain text (ARTICLE-FORMAT-2 "plain_text", the
 * body the full-text index and its hit offsets refer to; for a build-1 article the text as laid out), so search hits
 * and selections are byte ranges of that one text, copying is a substring of it, and the word under the pointer is
 * found in it.
 *
 * The engine is X-free: it measures through lay_metrics (gfx.h in the program, fixed fake metrics in the tests). Build
 * 1's RFC sample lays out exactly as build 2 did (the f4 renders stay byte-identical). */
#ifndef E26_LAYOUT_H
#define E26_LAYOUT_H
#include "article.h"
#include "article2.h"

/* faces the layout asks for; lay_metrics.have says which exist (the rest fall back: italic to the body, bold italic
 * to bold, the small mono face to the mono face, small to the interface's small face) */
enum lay_face { LF_BODY, LF_BOLD, LF_ITALIC, LF_BOLD_ITALIC, LF_MONO, LF_MONO_SMALL, LF_SMALL, LF_SYMBOL, LF_UI, LF_UI_BOLD,
	LF_HEAD, LF_HEAD_ITALIC, LF_SUBHEAD, LF_SUBHEAD_ITALIC,
	LF_HEAD3,             /* u14: the third heading level (falls back to LF_SUBHEAD) */
	LF_NFACES };
/* colour roles (the drawer resolves them: kind_article.c) */
enum lay_colour { LC_TEXT, LC_JUMP, LC_LISTJUMP, LC_QUIET, LC_RULE, LC_BAND, LC_BAR, LC_BARTEXT, LC_GROUND,
	LC_SHADE_YES, LC_SHADE_NO, LC_SHADE_PARTIAL, LC_SHADE_HIGHLIGHT,
	/* u14 S3: the Note, Caution and Warning callouts' tints and labels, and the CDE etched frame's two edges */
	LC_NOTE, LC_CAUTION, LC_WARNING, LC_NOTE_LABEL, LC_CAUTION_LABEL, LC_WARNING_LABEL, LC_SHADOW, LC_LIGHT,
	LC_NCOLOURS };

typedef struct {
	int (*width)(void *ud, int face, const char *s, int n);
	int (*ascent)(void *ud, int face);
	int (*descent)(void *ud, int face);
	int (*has_glyph)(void *ud, int face, int byte); /* NULL: every byte has one */
	void *ud;
	int have[LF_NFACES];  /* the face exists */
	int pitch;            /* the body text's line pitch (the type size's: 18, 20, 24) */
	int t8flags;          /* T8_HAVE_CP1252 when the text faces have 0x80-0x9F; T8_HAVE_SYMBOL is set from have[] */
	/* a media item's natural size in layout pixels (u14 M8: the source size IMAGE reported, at 1:1 device pixels and
	 * already within the view's height); NULL or 0: not known yet, the layout's provisional box */
	int (*media_size)(void *ud, const char *media_id, int *w, int *h);
	/* Adjacent size in the same face family. Extra face ids start at LF_NFACES. */
	int (*step_face)(void *ud, int face, int direction);
	int n_size_faces;
	int open_formula;       /* one-based block index, or zero for the compact view */
	int pane;               /* u14: the reader pane's text width, centred on the reading measure (the width lay_v2 is
	                         * given); code and preformatted boxes may grow into it. 0: the measure */
	int em;                 /* u14: the body face's pixel size (F_BODY's PIXEL_SIZE: 20 at 150% Helvetica), the unit of
	                         * the page's spacing tokens (readability spec section 3). 0: not known (the tests' fake
	                         * metrics): use LAY_EM(M) */
} lay_metrics;
/* the em to space by: M->em, or the period design's 13 px body when the caller set none */
#define LAY_EM(M) ((M)->em > 0 ? (M)->em : 13)

enum lay_item_kind { LI_TEXT, LI_FILL, LI_FRAME, LI_HLINE, LI_BOX, LI_MEDIA, LI_FORMULA };
#define LI_F_UNDER 1          /* underlined (a code variable) */
#define LI_F_ALT 2            /* a media box's stand-in text (the picture replaces it once IMAGE draws it) */
#define LI_F_DASH 4           /* LI_HLINE dotted (a form feed in preformatted text) */
#define LI_F_SYMBOL 8         /* LI_TEXT: Symbol-encoded bytes */
#define LI_F_TAB 16           /* LI_TEXT: one tab of preformatted text, drawn as the spaces to the next stop */
#define LI_F_UNDERLINE 32     /* 2.2 underline at the current face's underline position */
#define LI_F_STRIKE 64        /* 2.2 strike at the current face's strike position */
#define LI_F_RESIZED 128      /* selection band follows the face's metrics */
#define LI_F_DRAWN 256        /* LI_MEDIA: pixels drawn in this frame (set by ARTICLE, not layout) */
#define LI_F_FORMULA_LINK 512 /* generated label represents a full source span (a formula; u14: a footnote marker "[28]") */
#define LI_F_TYPOGRAPHY (LI_F_UNDERLINE | LI_F_STRIKE | LI_F_RESIZED)

typedef struct {
	int kind;             /* LI_* */
	int x, y, w, h;       /* LI_TEXT: x, baseline y, width (h unused); shapes: the rectangle (LI_HLINE: h = 1) */
	int face;             /* LI_TEXT */
	int colour;           /* LC_* */
	int off, len;         /* LI_TEXT: the drawn bytes in lay_doc.t8 */
	int src, srclen;      /* the plain-text bytes this item shows; src -1: a decoration (a bullet, a label) */
	int link;             /* index in lay_doc.lk, -1 none */
	int line;             /* LI_TEXT, LI_BOX: its line, -1 */
	int block;            /* the block (v2) or build-1 block it belongs to */
	int flags;            /* LI_F_* */
	int media;            /* LI_MEDIA: index in the article's media, -1 */
} lay_item;

typedef struct {
	int top, base, bot;   /* document y: the line's top, baseline and bottom (the selection band) */
	int x0, x1;           /* its left and right ends */
	int first, n;         /* its text and box items: lay_doc.it[first .. first+n) */
	int block;
	int inset;            /* one-based horizontal inset index, zero for prose */
} lay_line;

typedef struct {
	int x, y, w, h;       /* bordered content, including horizontal track if needed */
	int range, offset;
	int block;
} lay_inset;

#define LAY_INSET_TRACK 15

enum lay_link_kind { LK_TARGET, LK_FOOTNOTE, LK_GLOSSARY };
typedef struct {
	int kind;             /* LK_* */
	const a2_target *target; /* LK_TARGET */
	const char *name;     /* LK_FOOTNOTE: the footnote id; LK_GLOSSARY: the term */
	const char *definition; /* LK_GLOSSARY */
} lay_link;

enum lay_anchor_kind { LA_HEADING, LA_FOOTNOTE, LA_MEDIA };
typedef struct {
	int kind;
	const char *name;
	int y;                /* the top of its block */
	int block;
} lay_anchor;

enum lay_entry_kind { LE_HEADING, LE_FIGURE, LE_TABLE, LE_CODE };
typedef struct {
	int kind;             /* LE_* */
	int level;            /* headings 1-6 (build 1: the RFC section depth); media entries: their heading's + 1 */
	int y;                /* where the entry's section or item starts (scroll there) */
	int block;
	char *text;           /* 8-bit text for the interface face ("1.2  Conventions", "Figure 1: ...") */
	unsigned char minor;  /* u14 M4/M11: a minor heading (Cisco's Procedure, Before you begin, Step n ...): left out of the table of contents */
} lay_entry;

typedef struct {
	lay_item *it;
	int nit, cit;
	lay_line *ln;
	int nln, cln;
	lay_inset *inset;
	int ninset, cinset;
	char *t8;             /* the drawn bytes */
	size_t nt8, ct8;
	a2_buf pt;            /* the plain text */
	lay_link *lk;
	int nlk, clk;
	lay_anchor *an;
	int nan, can;
	lay_entry *en;
	int nen, cen;
	int nheads;           /* heading entries among en */
	int h;                /* the document's height */
	int width;
	int v2;               /* laid out from format 2 */
	int oom;              /* an allocation failed: the display list is incomplete (reported, never silent) */
	int ms;               /* how long the layout took (milliseconds), for the -v log */
} lay_doc;

/* lays out a format-2 article (d) or a build-1 article (v1) in width px; frees and replaces L's contents */
void lay_v2(lay_doc *L, const lay_metrics *M, const a2_doc *d, int width);
void lay_v1(lay_doc *L, const lay_metrics *M, const article *v1, int width);
void lay_free(lay_doc *L);

/* ---- hit-testing and selection (document coordinates) */
typedef struct {
	int item;             /* the text item under the point, -1 */
	int line;             /* its line (or the nearest line), -1 */
	int pos;              /* the plain-text offset of the character boundary nearest the point (-1 none) */
	int link;             /* the jump under the point, -1 */
} lay_hit;
/* what lies at (x, y); exact: 1 = only a point on a text item counts (jumps), 0 = the nearest boundary (dragging) */
void lay_hit_test(const lay_doc *L, const lay_metrics *M, int x, int y, int exact, lay_hit *h);
const lay_inset *lay_line_inset(const lay_doc *L, int line);
/* the word round plain-text offset pos: [*s, *e), 0 when no word is there */
int lay_word_at(const lay_doc *L, int pos, int *s, int *e);
/* the x of plain-text offset pos inside a text item (document coordinates) */
int lay_item_x(const lay_doc *L, const lay_metrics *M, const lay_item *it, int pos);
/* the item drawing plain-text offset pos (the first whose span holds it), -1 */
int lay_item_at_pos(const lay_doc *L, int pos);
/* the anchor named name of a kind (LA_*), -1 */
int lay_anchor_find(const lay_doc *L, int kind, const char *name);
/* the current heading entry for a scroll position (the outline's band): the last heading whose section has begun */
int lay_current_entry(const lay_doc *L, int scroll);
/* the index in en of the n-th heading entry, -1 */
int lay_heading_entry(const lay_doc *L, int n);
/* the plain text between two offsets as a malloc'd UTF-8 string (copying a selection) */
char *lay_text_range(const lay_doc *L, int s, int e);

/* faces as the layout resolves them (a missing face falls back) */
int lay_face(const lay_metrics *M, int face);

/* u14 (readability spec M6, M7, S5): what lay_v2 leaves undrawn; its plain text stays (search offsets, copy).
 * LAY_HIDE_BLOCK: the whole block (a paragraph repeating the title in the first four blocks, a Cisco running page
 * footer "(c) ... Cisco and/or its affiliates ... Page n of m", a "Source image (no alternative text supplied)" note, a
 * "See reference ref-..." footnote, a one-row "Note | text" table repeating the callout just before it);
 * LAY_HIDE_CAPTION: a figure's caption only (empty, "Image", "Image on page n", or
 * its picture's alternative text); 0: drawn */
enum { LAY_HIDE_BLOCK = 1, LAY_HIDE_CAPTION = 2 };
int lay_hidden(const a2_doc *d, int block);

#endif
