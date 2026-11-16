/* library_app.h: Deep32 Library application state and the interfaces between its modules (build 3, W0-F0).
 *
 * Module map (docs/F0-HANDOFF.md has the ownership and the recipes):
 *   app.c       main(), options and resources, start-up (screens, palette, taxonomy, menus, modules)
 *   fonts.c     the period font sets and the type size
 *   nav.c       screens, the window stack, Go Back (article history), the notice box
 *   input.c     pointer, keys and the mouse wheel, dispatched to kinds, menus, bindings and actions
 *   actions.c   the built-in actions and the named-action fallback
 *   control.c   what every kind shares: a control's text, state, enabled rule and strip frame
 *   gfx.c       the drawing primitives (gfx.h); draw.c the frame: screens in stack order, then menus
 *   menus.c     the Category menu and its flyouts, the record menus (Features, Tools, Information, ...)
 *   present.c   the back buffer to the window (scalers sharp, render, cpu); wm.c the window manager side
 *   events.c    the UI event bus; state.c the settings store (LIBRARY.INI), shared states, reading history
 *   timer.c     one-shot timers and the animation tick; spawn.c helper processes; io.c input sources and files
 *   registry.c  the named tables (registry.h); modules.gen.c (generated) registers every module
 *   kind_*.c    one file per control kind; room_*.c one file per room (actions, sources, state) */
#ifndef E26_APP_H
#define E26_APP_H
#include <X11/Intrinsic.h>
#include <X11/Xlib.h>
#include <stddef.h>
#include <sys/types.h>
#include "article.h"
#include "gfx.h"
#include "records.h"
#include "registry.h"
#include "scale.h"
#include "scr.h"
#include "taxonomy.h"
#include "../common/d32ww.h"

#define E26_W 640
#define E26_H 480
#define E26_MAX_ASSETS 512
#define E26_HIST 32
#define E26_WSTACK 16
/* the outline pane (1995 capture 35): 17-px rows from 3 px below its top, the baseline 12 px below a row's top;
 * top-level entries start at the pane's left edge (frame x 19) and each level 7 px further in (x 26 for the
 * second); the current row on entry 14 (256-colour captures 35, 93) */
#define E26_OUTLINE_TOP 3
#define E26_OUTLINE_ROW 17
#define E26_OUTLINE_BASE 12
#define E26_OUTLINE_STEP 7
/* the line pitch of article text at the Library's default type size (Small, Arial 13 px; 1995 capture 35);
 * e26_app.line holds the pitch of the type size in use, which is also the arrows' step */
#define E26_LINE 18

typedef struct e26_xasset {
	char name[SCR_ASSET];
	int w, h;
	Pixmap pm, mask;      /* mask None when opaque */
	unsigned char *rgba;  /* kept for colour sampling */
} xasset;

typedef struct {
	int x, y;             /* document coordinates: x within the text area, y = baseline */
	int font;
	unsigned long colour;
	char *text;
	int len;
} lay_op;

/* a Go Back entry: the full screen that was left and what was in view on it (plan E5, T2) */
typedef struct {
	char screen[SCR_ID];
	char art_id[4097];    /* the article loaded when it was left, "" none (a complete protocol id) */
	int scroll, view;     /* its scroll position and view (0: Text, Outline, the one view build 2 has) */
	int flt_cat, flt_sub; /* the Pinpointer's filter */
	int cur_cat, cur_sub; /* the Category bar's category and subcategory */
} e26_hist;

/* a window on the stack (plan E3): the bottom entry is a full screen, every other an overlay window */
typedef struct {
	scr_screen *s;
	int modal;            /* input stops at it (the notice box) */
	int hidden;           /* not drawn while a box stands over it (build 2's notice over the Pinpointer) */
} e26_window;

typedef struct e26_app {
	/* X and Motif */
	XtAppContext ctx;
	Widget top, da, sb;
	Display *dpy;
	Window win;
	Visual *vis;
	int depth;
	unsigned long rmask, gmask, bmask;
	GC gc;
	Pixmap back;
	gfx_ctx g;            /* the frame's drawing context (the back buffer, k = 1) */
	XFontStruct *font[F_NFONTS];
	/* options (X resources and command line) */
	String store, screens, taxonomy_path, article_dir, start, type_size, wheel_dir, title_theme, settings_dir;
	Boolean no_ui_sound, intro_sounds, show_title, verbose, splash;
	unsigned settings_overrides; /* session sound overrides remain active after Settings saves */
	int splash_ms;
	/* data */
	scr_set set;
	taxonomy tax;
	int have_tax;
	article art;
	int have_art;
	char art_id[4097];    /* a complete protocol id (LIB-PROTOCOL: up to 4096 bytes) */
	rec_strings str;
	int have_str;
	rec_palette classic_pal, pal; /* Imported interface colours and this session's appearance colours. */
	size_t appearance_theme;
	xasset asset[E26_MAX_ASSETS];
	int nasset;
	/* screens: the window stack (nav.c); cur and base are kept in step with it */
	e26_window wstack[E26_WSTACK];
	int nwin;
	scr_screen *cur;      /* the screen in front (an overlay when cur->over is set) */
	scr_screen *base;     /* the full screen under the overlay in front, NULL when a full screen is in front */
	int startup_over;     /* the start-up lines (control "startup") have had their time */
	char notice_title[64], notice_text[300]; /* the "coming in a later build" box (action notice) */
	e26_hist hist[E26_HIST];
	int nhist;
	/* the Pinpointer (room_pinpoint.c) */
	d32ww ww_titles, ww_groups, ww_keywords;
	int have_ww, have_groups, have_keywords;
	uint32_t *view, nview, max_item;
	unsigned char *allowed;
	int wtop, wsel, flt_cat, flt_sub, pan_hot;
	int area_top;         /* the Category panel's first area in view (its area column scrolls) */
	char edit[128];
	int edit_sel;         /* the field's text stands selected (a title chosen from the list, capture256 29); typing replaces it */
	/* pointer */
	int hot, pressed;     /* control index in the front screen, -1 none */
	/* menus */
	const scr_menu *menu;
	int menu_x, menu_y, menu_hot;
	int sub_open, sub_cat, sub_x, sub_y, sub_hot;
	int cur_cat, cur_sub;
	/* article layout */
	lay_op *ops;
	int nops, capops;
	int doc_h, scroll, view_h;
	int line;             /* the body text's line pitch for the type size in use (fonts.c) */
	int reader_size;      /* TextStyle (Small 0, Medium 1, Large 2): the body size of the 640x480 letterbox */
	/* u14 readability (spec M1-M3, S6), fonts.c; the Text menu (room_article.c) changes them */
	int reader_zoom;      /* the Text size in the native reader: a fonts_zoom_ladder step (ReaderZoom, or auto) */
	int reader_zoom_auto; /* 1: ReaderZoom is 0 (auto): reader_zoom is fonts_auto_zoom() */
	int font_native;      /* 1: the faces are loaded for the native article viewport (the Text size), 0: the letterbox
	                         (zoom 100, the TextStyle size, the period pitch) */
	int panel_dpi;        /* the panel's dpi (XRandR, or E26_DPI), 0 unknown */
	int ui_scale;         /* ReaderUiScale in effect (0 = auto: 150 at >= 140 dpi, else 100): F_RUI is
	                         round(11 x ui_scale / 100) px */
	int reader_spacing;   /* E26_SPACING_* (ReaderSpacing): the line pitch 1.35, 1.5 or 1.7 x the body */
	int reader_column;    /* E26_COLUMN_* (ReaderColumn): the measure, 62, 75 or 90 characters */
	int reader_paper;     /* E26_PAPER_* (ReaderPaper): the page White or Warm */
	int outline_exhibits; /* OutlineExhibits: the outline lists figures, tables and code (area D's ar_tree_build) */
	int reader_face, reader_font_choice; /* rendered face and explicit choice (-1 follows appearance) */
	int head_y[64], head_block[64], nhead;
	int outline_hot;
	const struct kind_ops *drag_ops; /* the kind whose thumb is under the pointer (input.c motion), NULL none */
	int drag_off;            /* where the thumb was taken */
	int drag_ctl;            /* the control whose thumb is dragged */
	/* sound */
	pid_t snd_pid;
	/* maximise as integer scaling (update 1: scale.h, present.c, wm.c) */
	e26_scale sc;         /* the frame's factor and place in the window */
	String scaler;        /* *scaler: "render" (the default: RENDER with a nearest filter) or "cpu" */
	int use_render;       /* the RENDER path is set up */
	unsigned long pic_src, pic_dst; /* its Pictures (XIDs): the back buffer and the window */
	int pic_k;            /* the factor the source transform holds */
	XImage *zoom;         /* the cpu path's enlarged frame */
	int restore_x, restore_y, have_restore; /* where the Library's own maximise found the window (its frame's position) */
} e26_app;

/* ---- kinds (one kind_<x>.c each; registered with reg_add(REG_KIND, name, &ops, ...)) */
typedef struct kind_ops {
	/* draws control i of screen s at (x,y) (its origin in the frame); under: s lies beneath a window */
	void (*draw)(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int x, int y, int under);
	int interactive;      /* the pointer hits it even without an action (it handles its own presses) */
	/* the rest may be NULL. press: button 1 went down on it; 1 = handled (the kind redrew) */
	int (*press)(e26_app *a, scr_screen *s, int i, int x, int y);
	/* button 1 came up over the control it went down on; 1 = handled (the kind redrew or navigated) */
	int (*release)(e26_app *a, scr_screen *s, int i, int x, int y);
	/* the pointer is over it (i >= 0), or elsewhere (i = -1, to forget a hover); 1 = the frame needs a redraw */
	int (*hover)(e26_app *a, scr_screen *s, int i, int x, int y);
	/* the mouse wheel over it (dir -1 up, 1 down); 1 = handled (the caller redraws) */
	int (*wheel)(e26_app *a, scr_screen *s, int i, int dir);
	/* the pointer moved while this kind's thumb is dragged (a->drag_ops) */
	void (*drag)(e26_app *a, int x, int y);
	/* a key before the screen's control keys and bindings (an edit field); 1 = handled */
	int (*key_focus)(e26_app *a, scr_screen *s, int i, XKeyEvent *ev, KeySym ks);
	/* a key nothing else took (the article's arrows); 1 = handled */
	int (*key_late)(e26_app *a, scr_screen *s, int i, KeySym ks);
} kind_ops;
const kind_ops *kind_of(int kind);                       /* NULL for a kind no module implements */

/* ---- actions (actions.c; registered with reg_add(REG_ACTION, name, &table, ...)) */
enum { E26_CTL_FINDER_TITLE = -2 };
typedef struct {
	scr_screen *s;        /* the screen of the control, NULL from a menu item or a key binding */
	int ctl;              /* the control's index, or a negative value without a control */
	const char *arg;      /* the argument ("" none) */
	const char *label;    /* a menu item's label ("" none) */
	/* Set only for E26_CTL_FINDER_TITLE; the action runs before the snapshot goes out of scope. */
	const char *catalogue_id, *generation, *marking;
} e26_actx;
/* returns 1 when the caller should redraw the frame, 0 when the handler navigated (and drew) itself */
typedef int (*e26_action_fn)(e26_app *a, const e26_actx *x);
typedef struct { e26_action_fn fn; } e26_action;
/* modules keep the table static: static const e26_action act_open = { open_fn }; reg_add(REG_ACTION, "room.x", &act_open, ...) */
/* runs action id: its handler, or for a named action no module builds yet (and "notice") the notice box titled arg
 * (or label); returns as the handler does */
int action_run(e26_app *a, int id, const e26_actx *x);
int action_built(int id);                                /* 1 when a module implements id */
void actions_register(void);                             /* the built-in handlers (app.c calls it first) */

/* ---- sources (control "source <name>"; registered with reg_add(REG_SOURCE, name, &ops, ...)) */
typedef struct {
	/* the control's text into buf; NULL to fall back to its label */
	const char *(*text)(e26_app *a, const scr_control *c, char *buf, size_t n);
	/* its checked state as the program sets it (the Pinpointer's Category button), -1 = the control's own */
	int (*checked)(e26_app *a, const scr_control *c);
	/* drawn on a panel after its ground (the watermark, the chevrons) */
	void (*decorate)(e26_app *a, gfx_ctx *g, const scr_control *c, int x, int y);
} source_ops;
const source_ops *source_of(const scr_control *c);       /* NULL: no source, or none registered */

/* ---- the UI event bus (events.c) */
enum e26_event_type { EV_PRESS, EV_MENU_OPEN, EV_MENU_PICK, EV_SCREEN_ENTER, EV_ARTICLE_OPEN, EV_RESIZE, EV_HISTORY_BACK, EV_NTYPES };
typedef struct {
	int type;
	scr_screen *screen;   /* PRESS: the front screen; SCREEN_ENTER: the screen entered (or the new base: switched 1) */
	int ctl;              /* PRESS: the control under the pointer, -1 */
	const char *name;     /* MENU_OPEN, MENU_PICK: the menu id; ARTICLE_OPEN: the article id */
	const char *arg;      /* MENU_PICK: the item's label */
	int x, y;             /* PRESS: frame coordinates */
	int switched;         /* SCREEN_ENTER: the screen under the overlay in front changed (the overlay stays) */
	const e26_hist *hist; /* HISTORY_BACK: the entry being restored */
} e26_event;
typedef void (*e26_event_fn)(e26_app *a, const e26_event *ev, void *ud);
int ev_subscribe(int type, e26_event_fn fn, void *ud);   /* an id, or -1 */
void ev_unsubscribe(int id);
void ev_publish(e26_app *a, const e26_event *ev);
const char *ev_name(int type);

/* ---- timers (timer.c): one-shot timers and the animation tick, which runs only while something animates */
typedef void (*e26_timer_fn)(e26_app *a, void *ud);
typedef int (*e26_tick_fn)(e26_app *a, void *ud);        /* 1 = keep animating, 0 = done */
#define E26_TICK_MS 33
void timer_after(e26_app *a, unsigned long ms, e26_timer_fn fn, void *ud);
int timer_animate(e26_app *a, e26_tick_fn fn, void *ud); /* an id; starts the tick when it was idle */
void timer_stop(int id);
int timer_ticking(void);                                 /* animators running */
int timer_tick_once(e26_app *a);                         /* runs every animator once; returns how many remain */

/* ---- input sources (io.c) */
typedef void (*e26_io_fn)(e26_app *a, int fd, void *ud);
int io_watch(e26_app *a, int fd, e26_io_fn fn, void *ud); /* calls fn when fd is readable; an id, or -1 */
void io_unwatch(int id);

/* ---- settings store, shared states and reading history (state.c) */
enum { SET_BOOL, SET_INT, SET_STRING };
typedef struct {
	const char *key;      /* the period design's LIBRARY.INI name, e.g. "ShowTitleScreen" */
	const char *resource; /* the X resource that overrides it for the session, NULL none */
	int type;
	const char *def;      /* its default, as text */
} e26_setting;
/* registered as static tables too: static const e26_setting s = { "Key", "resource", SET_BOOL, "1" } */
int state_open(const char *dir);                         /* reads <dir>/LIBRARY.INI ("" or NULL: ~/.d32/e26) */
const char *state_dir(void);                             /* "" before state_open */
const char *set_get(const char *key);                    /* the stored value, else the key's default, else NULL */
int set_stored(const char *key);                         /* 1 when LIBRARY.INI holds the key */
int set_get_bool(const char *key, int def);
int set_get_int(const char *key, int def);
int set_put(const char *key, const char *value);         /* in memory; set_save writes the file */
int set_save(void);                                      /* 0, or -1 (and a message) */
int set_put_save(const char *const *keys, const char *const *values, size_t count);
void state_builtin_keys(void);                           /* registers the period design's keys the engine reads */
/* shared checked states (control "state"): the Find Wizard's two category pages share the radios and the ticks */
int *state_shared(const char *key);
void states_save(scr_screen *s);
void states_load(scr_screen *s);
/* reading history (T7): <settings>/history.tsv, one line per article opened */
void state_note_article(const char *id, int scroll);
#include "reader_pos.h"
int state_reader_load(const char *pack, const char *id, e26_reader_pos *pos);
void state_reader_save(const char *pack, const char *id, const e26_reader_pos *pos);

/* ---- xassets.c (W1-IMAGE) */
xasset *xa_get(e26_app *a, const char *name);   /* loads on first use; NULL (and a message) when missing */
unsigned long xa_pixel(e26_app *a, long rgb);
long xa_sample(const xasset *x, int px, int py); /* 0xRRGGBB or -1 */

/* ---- draw.c: the frame */
long ui_rgb(const e26_app *a, int index); /* entry of the interface colour table (0xRRGGBB) */
void draw_frame(e26_app *a);
void draw_flush(e26_app *a);
void draw_screen(e26_app *a, gfx_ctx *g, scr_screen *s, int under);
#define C_BLACK      ui_rgb(a, UI_BLACK)
#define C_WHITE      ui_rgb(a, UI_WHITE)
#define C_CAPTION    ui_rgb(a, UI_CAPTION)   /* caption and section bars (CAPTION, CAPTUP and CAPTDN's ground) */
#define C_PAPER      ui_rgb(a, UI_PAPER)     /* left column ground when the engraving gives none */
#define C_QUIET      ui_rgb(a, UI_CAPTION)   /* quiet notes on the parchment column */
unsigned long e26_jump_rgb(void);
#define C_JUMP       (e26_jump_rgb())        /* text jumps: current Library JumpColor */
#define C_LISTJUMP   ui_rgb(a, UI_MAROON)    /* list jumps: outline media entries, the Related Articles list */
#define C_DISABLED   REC_DISABLED_LABEL      /* disabled labels, RGB(128,128,128) */

/* ---- control.c: what every kind shares */
int frame_for(const scr_control *c, int state);
const char *control_text(e26_app *a, const scr_control *c, char *buf, size_t n);
int control_state(e26_app *a, scr_screen *s, int i);
int control_enabled(e26_app *a, scr_screen *s, int i);
int control_checked(e26_app *a, const scr_control *c);
scr_control *find_kind(scr_screen *s, int kind);
void class_pens(const scr_control *c, int st, int en, long *tl, long *br);
long contrast(const e26_app *a, long bg);       /* black or white lettering for a ground (entries 15 and 0) */
void group_digits(unsigned long v, char *out, size_t n); /* 26246 -> "26,246" */

/* ---- nav.c: screens, the window stack, Go Back, the notice box */
void nav_go(e26_app *a, const char *name, int push);     /* a full screen, or an overlay over one */
void nav_base_switch(e26_app *a, const char *name);      /* the full screen under the overlay in front becomes another */
/* the same with Go Back's record of the screen left taken before the caller loaded something new (an article) */
void nav_snapshot(e26_app *a, e26_hist *h);
void nav_go_from(e26_app *a, const char *name, int push, const e26_hist *snap);
void nav_base_switch_from(e26_app *a, const char *name, const e26_hist *snap);
int nav_focus_at(e26_app *a, int x, int y);              /* a press outside a non-modal window moves the focus; 1 moved */
int nav_focus(void);                                     /* the focused entry of the stack */
void nav_close_overlay(e26_app *a);                      /* Escape, Close: the window in front goes */
int nav_push_window(e26_app *a, const char *name, int modal); /* a further window over the front one; the ones below
                                                               keep drawing, with inactive captions */
void nav_back(e26_app *a);                               /* Go Back, Ctrl+B */
void nav_notice(e26_app *a, const char *title);          /* "<title> is coming in a later build of Deep32 Library." */
void nav_notice_text(e26_app *a, const char *title, const char *text); /* the box with any text */
void nav_redraw(e26_app *a);                             /* draw_frame + draw_flush */
void nav_set_title(e26_app *a);                          /* the shell's title for the screen in front */

/* ---- input.c */
void input_handler(Widget w, XtPointer cd, XEvent *ev, Boolean *cont);
int hit_control(e26_app *a, int x, int y);

/* ---- fonts.c (W1-SHARP) */
typedef struct { const char *id; const char *label; } e26_reader_face;
extern const e26_reader_face fonts_reader_faces[];
extern const size_t fonts_reader_face_count;
int fonts_reader_select(e26_app *a, int face); /* 0 success; failure retains the current face */
const char *fonts_reader_theme_name(size_t theme);
void fonts_load(e26_app *a);
/* the article's type size again (Small 0, Medium 1, Large 2: the period design's TextStyle): the body slots reloaded and
 * e26_app.line set (W1-LAYOUT, LAYOUT-CR1; room_article.c lays the article out again) */
void fonts_type_size(e26_app *a, int ts);
#define E26_FONTS_TYPESIZE 1
/* u14 readability (spec M1-M3): the Text size ladder, the panel's default, the native/letterbox switch */
#define E26_ZOOM_STEPS 6
extern const int fonts_zoom_ladder[E26_ZOOM_STEPS];      /* 100 125 150 175 200 250 */
enum { E26_SPACING_COMPACT, E26_SPACING_NORMAL, E26_SPACING_RELAXED };
enum { E26_COLUMN_NARROW, E26_COLUMN_NORMAL, E26_COLUMN_WIDE };
enum { E26_PAPER_WHITE, E26_PAPER_WARM };
extern const char *const fonts_spacing_names[3];         /* "compact" "normal" "relaxed": ReaderSpacing's values */
extern const char *const fonts_column_names[3];          /* "narrow" "normal" "wide": ReaderColumn's */
extern const char *const fonts_paper_names[2];           /* "white" "warm": ReaderPaper's */
int fonts_auto_zoom(const e26_app *a);                   /* 150 at >= 140 dpi, 125 at 115-139, else 100 */
int fonts_zoom_step(int zoom);                           /* the ladder index nearest zoom */
int fonts_zoom_px(e26_app *a, int zoom);                 /* the reader face's body px at zoom (snapped to a native strike) */
/* the faces for the native article viewport (1: the Text size) or the letterbox (0: 100%); 1 when they were
 * reloaded (the caller lays the article out again) */
int fonts_viewport(e26_app *a, int native);

/* ---- menus.c (W1-CHROME): the Category menu and flyouts, the record menus */
void menus_load(e26_app *a);
void menus_draw(e26_app *a, gfx_ctx *g);
int menus_motion(e26_app *a, int x, int y, int *changed); /* 1 = a menu took the pointer; *changed: redraw needed */
int menus_press(e26_app *a, int x, int y);               /* 1 = a menu took the press */
void menus_close(e26_app *a);
void menus_toggle(e26_app *a, const char *id, int x, int y); /* opens menu id from (x,y) (or its "at"), or closes it */
int menus_record_open(const e26_app *a);                 /* a record menu (Features, Tools, Information) is open */
void taxonomy_colours(e26_app *a);

/* ---- room_article.c (W2-ARTICLE) */
int art_load(e26_app *a, const char *want);              /* an article id, or a corpus doc_key with a sample */
void art_open(e26_app *a, const char *id);               /* loads it and shows the article screen */
void art_scroll_by(e26_app *a, int dy);
void art_place_scrollbar(e26_app *a);
void art_scroll_cb(Widget w, XtPointer cd, XtPointer call);
int art_current_heading(e26_app *a);
void art_layout(e26_app *a, int width);
int art_scroll_thumb(e26_app *a, const scr_control *c, int *ty, int *th);
int art_scroll_thumb_at(e26_app *a, const scr_control *c, int y, int *ty, int *th);

/* ---- room_pinpoint.c (W2-FINDER) */
int pin_open(e26_app *a);
void pin_reset(e26_app *a);
void pin_filter(e26_app *a, int cat, int sub);
void pin_type(e26_app *a);
void pin_clamp(e26_app *a);
int pin_rows(void);
int pin_entry(const e26_app *a, int row, d32ww_entry *e);
uint32_t pin_total(const e26_app *a);
int pin_find_label(const e26_app *a, const char *label); /* the list row whose label is label ("RFC 2328"), or -1 */
void pin_open_entry(e26_app *a, int row);
void pin_open_filtered(e26_app *a, int cat, int sub);
int pin_have_results(void);
int pin_key(e26_app *a, XKeyEvent *ev, KeySym ks);       /* typing in the word wheel; 1 = handled */
void catpanel_reveal(e26_app *a, int cat);               /* kind_catpanel.c: scroll the area column to show cat */

/* ---- room_title.c (W2-FRONTDESK, W2-OPENING) */
int title_splash(e26_app *a, void (*then)(void));        /* shows the start-up splash, then calls then; -1 none */
void title_startup_timer(e26_app *a);

/* ---- room_findwiz.c */
void findwiz_category_buttons(e26_app *a);

/* ---- present.c (W1-CHROME): the back buffer to the window, enlarged k times in a larger window */
void present_init(e26_app *a);                  /* after the window and the back buffer exist */
int present_resize(e26_app *a, int w, int h);   /* the window's new size; 1 when the frame's layout changed */
void present_prepare(e26_app *a);
int present_client_viewport(const e26_app *a);
int present_article_viewport(const e26_app *a);
void present_frame_bounds(const e26_app *a, int *w, int *h);
int nav_viewport_compatible(const e26_app *a);
void nav_viewport_place(e26_app *a);
void present_frame(e26_app *a);

/* ---- wm.c (W1-CHROME) */
void wm_toggle_maximize(e26_app *a);            /* the caption's middle button: fill the monitor, or 640x480 again */

/* ---- sound.c (W1-SOUND) */
pid_t snd_play(const char *path, int loop, int verbose);
void snd_stop(pid_t pid);
void snd_screen(e26_app *a);                    /* the front screen's sound (the Title's theme) */

/* ---- actions.c */
void e26_quit(e26_app *a);                      /* stops the sound and exits */

#endif
