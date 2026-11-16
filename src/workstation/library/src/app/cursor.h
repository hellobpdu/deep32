/* cursor.h: the Library's own pointer shapes (W1-CANVAS, plan W7). The cursors are the period design's and 97's (the store's
 * ui/cursor and e97s/ui/cursor, imported from the period program and the period resource library), shown as ARGB cursors through RENDER
 * and enlarged k times (nearest) with the frame, so a maximised Library keeps them the size of everything else.
 *
 * Who decides the shape: the control under the pointer. A kind registers a probe (cursor_probe) that answers for a
 * point on one of its controls (the canvas: the hand over an item, the eight pan arrows on its edge band, the
 * magnifier over a surface that zooms on a click); a control may name one with "prop cursor <name>" (a Home tile:
 * "prop cursor hand"); anything else keeps the arrow (None: the window manager's pointer, as the period design kept
 * Windows' arrow). The article kind has a default probe: the hand over runs drawn in the jump colours; the package
 * that owns the article's layout replaces it with its own hit test (cursor_probe on K_ARTICLE).
 *
 * The module follows the pointer with its own Xt event handler on the drawing area (after input.c's), so no core file
 * changes; every cursor is made at the first screen (before any pledge) and again only when the frame's factor
 * changes. No libXcursor: nothing is loaded with dlopen. */
#ifndef E26_CURSOR_H
#define E26_CURSOR_H
#include "scr.h"

struct e26_app;

enum e26_cursor {
	CUR_ARROW,            /* None: the window's default pointer */
	CUR_HAND,             /* 95 HANDCURSOR (ui/cursor/4; 97's 540 is the same picture): over jumps and items */
	CUR_MAGNIFY,          /* 95 ZOOMCURSOR (ui/cursor/9; 97's 542 the same): over enlargeable media, a zooming surface */
	CUR_SPLIT,            /* 95 SPLITCURSOR (ui/cursor/8; 97's 411 the same): a pane divider */
	CUR_PAN,              /* 97 14880 (e97s/ui/cursor/6): the four-way arrow, while a surface is dragged */
	CUR_PAN_W, CUR_PAN_E, CUR_PAN_N, CUR_PAN_S, /* 97 14881-14884 (e97s/ui/cursor/7-10): the Atlas's edge arrows */
	CUR_PAN_NW, CUR_PAN_NE, CUR_PAN_SW, CUR_PAN_SE, /* 97 14885-14888 (e97s/ui/cursor/11-14) */
	CUR_HAND_SMALL,       /* 97 540's 16x16 image (e97s/ui/cursor/2) */
	CUR_MAGNIFY_SMALL,    /* 97 542's 16x16 image (e97s/ui/cursor/4) */
	CUR_N
};

/* a probe: the cursor for frame point (x,y) on control i of screen s, or -1 for no opinion (the arrow) */
typedef int (*cursor_probe_fn)(struct e26_app *a, scr_screen *s, int i, int x, int y);

/* the store file, hotspot and name of a cursor (names: "arrow", "hand", "magnify", "split", "pan", "pan-w", ...) */
typedef struct {
	const char *name;
	const char *path;     /* under the store, without ".pam"; NULL for the arrow */
	int hx, hy;           /* the hotspot in the image's own pixels, as the resource's first two words give it */
} cursor_def;
const cursor_def *cursor_def_of(int id);   /* NULL out of range */
int cursor_by_name(const char *name);      /* a CUR_*, or -1 */

/* registers probe for a kind (its registry id); a later call replaces it */
void cursor_probe(int kind, cursor_probe_fn fn);
/* the cursor for frame point (x,y) of the screen in front, as the event handler shows it (X-free) */
int cursor_at(struct e26_app *a, int x, int y);
/* while a gesture runs (a canvas being dragged) the cursor stays id wherever the pointer goes; -1 ends it. The hold
 * also ends by itself when the drag does (a->drag_ops back to NULL). */
void cursor_hold(int id);
/* shows id on the Library's window now (no request when it is already shown); CUR_ARROW undefines it */
void cursor_show(struct e26_app *a, int id);
int cursor_shown(void);                    /* the id last shown, -1 before any */
/* the cursor of the last pointer position again (after a scroll or a screen change under a still pointer) */
void cursor_refresh(struct e26_app *a);

/* X-free helpers the tests check: the image of a cursor enlarged k times (nearest), and its hotspot at k (the middle
 * of the hotspot pixel's k x k block, so that the frame point it maps back to is the original hotspot) */
int cursor_scale_rgba(const unsigned char *src, int w, int h, int k, unsigned char *dst);
void cursor_hot_at(const cursor_def *d, int k, int *hx, int *hy);

/* called by the canvas module's registration (the cursor module has no registration of its own) */
void cursor_register(void);

#endif
