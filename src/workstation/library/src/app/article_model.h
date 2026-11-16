/* article_model.h: ARTICLE's X-free wire, outline, page and history operations. */
#ifndef E26_ARTICLE_MODEL_H
#define E26_ARTICLE_MODEL_H
#include "layout.h"
#include "reader_pos.h"
#include "json.h"
#include "../common/e26j.h"

typedef struct {
	e26j_buf meta, body;
	char source_hash[65];
	size_t next, total;
	int begun, complete;
} ar_wire;
void ar_json(e26j_buf *b, const jv *v);
/* Ordered, complete article pages only. 0 more, 1 complete, -1 malformed/oversized. */
int ar_wire_page(ar_wire *w, const jv *page);
int ar_wire_doc(ar_wire *w, a2_doc *d, char *err, size_t n);
void ar_wire_free(ar_wire *w);

typedef struct {
	int *visible;
	unsigned char *closed;
	int n, capacity;
	int toc;              /* u14 M11: a table of contents (headings to level 4, no minor entries); 0 lists every entry */
	int exhibits;         /* with toc: figures, tables and code are listed too (OutlineExhibits) */
} ar_tree;
int ar_tree_build(ar_tree *t, const lay_doc *L, int reset);
int ar_tree_toggle(ar_tree *t, const lay_doc *L, int entry);
int ar_tree_row(const ar_tree *t, int entry);
/* the row of the nearest visible heading at or before entry (the outline's current row), -1 */
int ar_tree_heading_row(const ar_tree *t, const lay_doc *L, int entry);
void ar_tree_free(ar_tree *t);
int ar_page_number(const char *anchor);
/* Explicit page anchors first; retained pre/code form feeds otherwise. -1 absent. */
int ar_page_y(const lay_doc *L, const a2_doc *d, int page);
int ar_current_page(const lay_doc *L, const a2_doc *d, int y);
/* Page reference surrounding a canonical plain-text offset, or 0. */
int ar_has_pages(const lay_doc *L);
int ar_page_reference(const char *text, size_t len, size_t pos);
const char *ar_marking(const a2_doc *d);
/* UTF-8, malloc'd. Keeps the source cite and marking; never asserts verification. u14 S17: ar_copy and ar_copy_at
 * (the current section) add a one-line citation; ar_copy_attributed adds the full attribution and licence. */
char *ar_copy(const a2_doc *d, const char *text, size_t len);
char *ar_copy_at(const a2_doc *d, const char *section, const char *text, size_t len);
char *ar_copy_attributed(const a2_doc *d, const char *text, size_t len);

#define AR_HISTORY 32
typedef struct {
	char *id;
	int pack, scroll, view;
	char title[64], section[64];
	e26_reader_pos position;
	int position_valid, inset;
} ar_place;
typedef struct { ar_place p[AR_HISTORY]; int n, at; } ar_history;
void ar_history_save(ar_history *h, int scroll, int view);
int ar_history_push(ar_history *h, const char *id, int pack, int scroll, int view);
const ar_place *ar_history_step(const ar_history *h, int dir);
void ar_history_free(ar_history *h);
#endif
