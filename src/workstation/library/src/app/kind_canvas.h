/* kind_canvas.h: the canvas kind (W1-CANVAS, plan E8), the surface the Timeline wall, the Atlas map and the
 * InterActivities models are drawn on. A canvas control shows a window onto a virtual surface much larger than
 * itself:
 *   - levels: the zoom levels, each a scale of the base coordinates (percent) and the surface's size at that scale;
 *   - layers: pre-rendered pictures from the asset store placed on a level's surface (opaque, masked, or repeated
 *     as a fill pattern), copied as they are (never scaled here: each level has its own pictures);
 *   - items: rectangles and polygons in base coordinates, shown at the levels they name, with hover and hit tests
 *     (a uniform grid, so 10,000 items cost what the visible ones cost), a label, an anchored pop-up, an action.
 * Pan by drag, arrows, Page Up/Down, Home/End and the wheel; zoom by level (+ and -, the canvas.zoom-in and
 * canvas.zoom-out actions, or a click on the surface when "prop click zoom" is set); the 97 Atlas's edge band pans
 * in eight directions with its eight cursors. The pointer readout is a source ("canvas-readout").
 *
 * A canvas gets its model from its screen file ("data level", "data layer", "data rect", "data poly"; the grammar is
 * in docs/kinds/CANVAS.md) or from its room, which builds one with the calls below and binds it (cv_bind). The room
 * keeps ownership of a bound model. Everything here but drawing is X-free, so the tests drive it headless. */
#ifndef E26_KIND_CANVAS_H
#define E26_KIND_CANVAS_H
#include "library_app.h"

#define CV_MASKED   0x01  /* a layer drawn through its transparency mask */
#define CV_REPEAT_X 0x02  /* a layer repeated across the surface (a fill pattern) */
#define CV_REPEAT_Y 0x04

enum { CV_RECT, CV_POLY };

typedef struct {
	int scale;            /* surface pixels per 100 base units */
	int w, h;             /* the surface's size at this level */
} cv_level;

typedef struct {
	int level;
	char asset[SCR_ASSET];
	int x, y;             /* its top-left on the level's surface */
	unsigned flags;
} cv_layer;

typedef struct {
	int shape;            /* CV_RECT or CV_POLY */
	int x, y, w, h;       /* base coordinates: the rectangle, or the polygon's bounding box */
	int *pts, npts;       /* CV_POLY: x0 y0 x1 y1 ... in base coordinates */
	int *tri, ntri;       /* CV_POLY: its triangles (vertex indices), for filling a concave outline */
	int min_level, max_level; /* shown at these levels only */
	int action;           /* run on a click (a registered or named action); A_NONE none */
	char *arg, *label, *popup, *jump; /* owned strings, NULL none: the action's argument, the name (readout),
	                                     the pop-up's text, and the pop-up's jump line ("Go to article") */
	long fill, line, hot_fill, hot_line, fg; /* 0xRRGGBB, -1 none */
	int font;             /* the label's face (F_*) */
	int show_label;       /* draw the label on the surface */
	int cursor;           /* the cursor over it (CUR_*); -1: the hand when it has an action or a pop-up */
	void *ud;             /* the room's */
} cv_item;

struct cv_view;
struct cv_grid;
typedef struct cv_model {
	cv_level *lv;
	int nlv, caplv;
	cv_layer *ly;
	int nly, caply;
	cv_item *it;
	int nit, capit;
	long ground;          /* the surface's ground, -1: the control's bg, else black */
	/* the room's hooks (any may be NULL) */
	/* the readout for base point (bx,by) with item hot under it (-1); NULL or "" for none */
	const char *(*readout)(e26_app *a, struct cv_view *v, int bx, int by, int hot, char *buf, size_t n);
	/* draws item it in place of the default look; (x,y,w,h) its box in the frame; clipped to the canvas */
	void (*draw_item)(e26_app *a, gfx_ctx *g, struct cv_view *v, const cv_item *it, int hot, int x, int y, int w, int h);
	/* a click on item (base point bx,by); 1 = handled (the default opens its pop-up or runs its action) */
	int (*click)(e26_app *a, struct cv_view *v, int item, int bx, int by);
	void *ud;
	struct cv_grid *grid; /* the spatial index, built on first use */
} cv_model;

typedef struct cv_view {
	char screen[SCR_ID], ctl[SCR_ID];
	cv_model *m;
	int own;              /* the model was built from the screen file's rows (freed with the view) */
	int fx, fy, w, h;     /* the control's place in the frame and its size */
	int level, vx, vy;    /* the zoom level, and the view's top-left on that level's surface */
	int hot, popup;       /* the item under the pointer, the item whose pop-up is open (-1 none) */
	int px, py, pvx, pvy, moved, dragging; /* the gesture: where it began, whether it became a drag */
	int edge, edge_gesture, anim; /* a press on the edge band: its direction (CUR_PAN_*), its animator */
	int propped;          /* the control's props have been read */
	int step;             /* pixels per arrow press ("prop step", default 16) */
	int edge_band;        /* the edge-pan band's width ("prop edge-pan", 0 none) */
	int click_zoom;       /* a click on the bare surface zooms in ("prop click zoom") */
	int readout_coords;   /* the readout shows base coordinates when no item is under the pointer */
	long hot_line;        /* the default hover outline ("prop hot-line"), -1 none */
	long pop_bg;          /* the pop-up's ground ("prop popup-ground"), default entry 31 */
	int pop_x, pop_y, pop_w, pop_h, pop_jump_y; /* the pop-up as last drawn (frame), its jump line's top (-1 none) */
	char readout[160];
} cv_view;

/* building a model */
cv_model *cv_model_new(void);
void cv_model_free(cv_model *m);
int cv_add_level(cv_model *m, int scale, int w, int h);                  /* its index, -1 */
int cv_add_layer(cv_model *m, int level, const char *asset, int x, int y, unsigned flags);
int cv_add_rect(cv_model *m, int x, int y, int w, int h);               /* an item's index, -1 */
int cv_add_poly(cv_model *m, const int *xy, int npts);                  /* 3 to 4096 points */
/* an item's strings (copied; NULL leaves one as it is) and action */
int cv_set_text(cv_model *m, int item, const char *label, const char *popup, const char *jump);
int cv_set_action(cv_model *m, int item, int action, const char *arg);
void cv_model_changed(cv_model *m);    /* items were added or moved after the canvas first showed: rebuild the index */
/* the screen-file rows of control c into m ("data level|layer|rect|poly ..."); 0, or -1 and the first bad row in err
 * (the good rows are kept) */
int cv_parse_rows(e26_app *a, cv_model *m, const scr_control *c, char *err, size_t n);

/* views */
cv_view *cv_view_of(e26_app *a, scr_screen *s, int ctl);                /* the canvas state of a control (created) */
cv_view *cv_find(const char *screen, const char *ctl);                   /* NULL when it never showed or was bound */
int cv_bind(e26_app *a, const char *screen, const char *ctl, cv_model *m); /* shows m (the room keeps it); 0 or -1 */
cv_view *cv_last(void);                                                  /* the canvas the pointer last touched */
void cv_pan(cv_view *v, int dx, int dy);                                 /* by surface pixels, kept on the surface */
void cv_zoom(cv_view *v, int level, int ax, int ay); /* to level, keeping view point (ax,ay) still (-1,-1: the middle) */
void cv_center(cv_view *v, int bx, int by);                              /* base point (bx,by) to the view's middle */
int cv_item_at(cv_view *v, int x, int y);            /* the topmost item shown at view point (x,y), -1 */
int cv_visible(cv_view *v, const int **ids);          /* the items shown in the view, bottom first */
int cv_edge_dir(const cv_view *v, int x, int y);      /* CUR_PAN_W ... CUR_PAN_SE in the edge band, -1 */
int cv_surface_w(const cv_view *v);
int cv_surface_h(const cv_view *v);

/* geometry (exported for the tests) */
int cv_lvc(const cv_model *m, int level, int base);   /* a base coordinate at a level (floor) */
int cv_poly_contains(const int *xy, int n, int px, int py); /* 1 when pixel (px,py)'s centre is inside (even-odd) */
int cv_triangulate(const int *xy, int n, int *tri);   /* ear clipping: 3*(n-2) indices; returns the triangles */

#endif
