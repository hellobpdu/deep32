/* scr.h: Deep32 Library declarative screen files (format "d32scr 1", documented in docs/SCREEN-FORMAT.md).
 * Derived from the period design's own two record types (SCREENS.md sections 2-3): a control carries an optional
 * original control record (7 colour slots, flags, label), a bitmap strip with its frame count, a label and a
 * rectangle; a menu carries an optional original menu header (colour pair, item height, width) and its items'
 * source. Coordinates are the original's 640x480 logical pixels; bitmaps are never scaled.
 *
 * Build 3 (W0-F0): kinds and actions are looked up in the registry (registry.h), so a module adds a kind or an
 * action without a grammar edit; named actions ("room.atlas") fall back to the notice box until a module builds
 * them; generic "prop" and "data" statements carry what a new kind needs; every screens/ *.scr is loaded
 * (scr_load_dir: main.scr and dialogs.scr first, the others in name order) and "include store:<path>" reads an
 * importer-generated fragment from the asset store; screens/ *.keys files bind keys to actions (scr_keys_load_dir).
 * Up to 128 screens; controls, images, blocks, menus and key bindings have no fixed cap. */
#ifndef E26_SCR_H
#define E26_SCR_H
#include <stddef.h>

#define SCR_ID 40
#define SCR_ASSET 160
#define SCR_LABEL 256
#define SCR_MAX_SCREENS 128
#define SCR_MAX_MENU_ITEMS 64
#define SCR_MAX_MENU_ICONS 16
#define SCR_FRAME_W 640            /* the whole frame: the window the period design filled on a 640x480 screen */
#define SCR_FRAME_H 480

/* The build-2 kinds and actions keep these numbers (registry.c registers their names in this order); a module's
 * kind or action is registered at run time and gets the next free number (reg_find(REG_KIND, "map")). */
enum scr_kind { K_BUTTON, K_TOGGLE, K_RADIO, K_TEXT, K_LABEL, K_BAR, K_PANEL, K_ARTICLE, K_OUTLINE, K_LIST, K_IMAGE, K_SCROLL,
                K_WHEEL, K_EDIT, K_COUNT, K_CATPANEL, K_NKINDS };
enum scr_state { S_NORMAL, S_HOT, S_PRESSED, S_DISABLED, S_CHECKED, S_CHECKED_HOT, S_CHECKED_PRESSED, S_CHECKED_DISABLED, S_NSTATES };
enum scr_action { A_NONE, A_GOTO, A_CLOSE, A_MENU, A_OPEN_ARTICLE, A_TOGGLE, A_SELECT, A_BACK, A_QUIT, A_ICONIFY,
                  A_SCROLL_UP, A_SCROLL_DOWN, A_SECTION_PREV, A_SECTION_NEXT, A_NOTICE, A_RESET, A_MAXIMIZE, A_NACTIONS };
#define F_ART_SIZE_COUNT 6
enum scr_font { F_UI, F_UI_BOLD, F_SMALL, F_TITLE, F_BODY, F_BODY_BOLD, F_FIXED, F_UI10, F_UI13, F_UI_BOLD13, F_SERIF12,
	F_BODY_ITALIC, F_BODY_BOLD_ITALIC, F_BODY_SMALL, F_FIXED_SMALL, F_SYMBOL,
	F_HEAD, F_HEAD_ITALIC, F_SUBHEAD, F_SUBHEAD_ITALIC,
	F_HEAD3, F_RUI, F_RUI_BOLD,
	F_ART_SIZE, F_NFONTS = F_ART_SIZE + 2 * F_ART_SIZE_COUNT };
/* u14 (readability spec M2): F_HEAD3 is the third heading level (Times New Roman Bold at 1.15 x the body; Times Bold
 * when the reader face is Times); F_RUI and F_RUI_BOLD are the reader's interface face (outline, status, Pinpointer
 * lists) at round(11 x ReaderUiScale / 100) px: Helvetica for the Helvetica themes and face, else Arial. Screen files
 * name them "heading3", "reader-ui" and "reader-ui-bold". */
/* W1-LAYOUT (LAYOUT-CR1): the article's other faces at the type size: italic, bold italic, a smaller body (sub- and
 * superscripts, small capitals), a smaller mono face (wide preformatted text) and Symbol (Greek and mathematics) */
#define E26_RICH_FACES 1
/* format 2.2: F_ART_SIZE.. hold the body's medium and bold faces at the other sizes the set ships (small, big) */
enum scr_align { AL_LEFT, AL_CENTRE, AL_RIGHT };

/* "prop <name> <value>": a named value on a control or a screen, for whatever kind or room reads it */
typedef struct {
	char *name, *value;
} scr_prop;
/* "data <value> [<value> ...]": one row of values on a control or a screen (a table a kind lays out) */
typedef struct {
	char **v;
	int n;
} scr_row;

typedef struct {
	char id[SCR_ID];
	int kind;                   /* a registered kind (registry.h); K_* for the built-in ones */
	int x, y, w, h;
	int record;                 /* original RCDATA control record id, 0 = none */
	char strip[SCR_ASSET];      /* asset name, "" = none */
	int frames;                 /* frames in the strip */
	int frame_of[S_NSTATES];    /* -1 = not declared */
	char label[SCR_LABEL];
	int label_sid;              /* original string id, 0 = none */
	int font, align;
	long fg, bg;                /* 0xRRGGBB, SCR_UI(n) until resolved, -1 = unset */
	long mfg;                   /* the menu marker's colour where it differs from the label's (as fg); -1 = the label's */
	long ibg;                   /* bg while a window of the program stands in front of this screen (an overlay); -1 = bg */
	long slot[7];               /* the record's 7 colour slots resolved (0xRRGGBB, -2 = transparent); see records.h */
	int has_slots;              /* 1 once slot[] is filled (a record, or the class defaults for a text button) */
	char group[SCR_ID];
	char enabled_by[SCR_ID];    /* control id, or "group:<name>" (any checked) */
	int checked, disabled;      /* initial state */
	int action;                 /* a registered action (registry.h); A_* for the built-in ones */
	char arg[SCR_ID];           /* its argument; for a named action no module builds yet, the notice's title */
	char key[SCR_ID];           /* X keysym name */
	char source[SCR_ID];
	char marker;                /* 1 = draw the period menu marker (a small down arrow) */
	int absolute;               /* 1 = rect in frame coordinates, not offset by the screen origin (the caption) */
	int text_x;                 /* label inset from the left edge (bars, titles, the article), -1 = the kind's default */
	int text_y;                 /* baseline below the control's top for one-line labels, -1 = centred */
	int pitch;                  /* line pitch of a wrapped label (its first baseline at text_y), 0 = the font's height + 1 */
	int tracking;               /* extra space after every letter, tenths of a pixel (spaced captions) */
	int upper;                  /* 1 = the label in capitals */
	int sunken;                 /* 1 = a 1-px dark top and left edge (an inset field) */
	int startup_ms;             /* > 0: shown only for this long after the program's first screen appears, once */
	char state[SCR_ID];         /* shared checked state: controls with the same key on any screen are one (wizard pages) */
	int category;               /* > 0: the n-th category of the taxonomy (a Find Wizard button: its strip is his art) */
	int hidden;                 /* run time: not drawn and not hit (a category button beyond the taxonomy) */
	scr_prop *props;            /* "prop" lines */
	int nprops;
	scr_row *rows;              /* "data" lines */
	int nrows;
} scr_control;

typedef struct {
	char asset[SCR_ASSET];
	int x, y;
} scr_image;

/* An item of a menu built from an original menu header's item list (12050 Features, 12051 Tools, 12053 the
 * Information menu): a text item, an item that is a bitmap strip ("|Atlas_icon": normal and pointer frames), or a
 * rule (an item with no string). The action is the screen file's "on <command>" for the item's command id. */
enum { MI_TEXT, MI_BITMAP, MI_RULE };
typedef struct {
	int kind;
	int cmd;                    /* the item's command id (the record's last field) */
	char label[64];
	char asset[SCR_ASSET];      /* MI_BITMAP: the strip; MI_TEXT: an icon drawn before the label ("icon"), or "" */
	int frames;
	int action;
	char arg[SCR_LABEL];
} scr_menu_item;

typedef struct {
	char id[SCR_ID];
	int record;                 /* original menu header id (e.g. 12000), 0 = none */
	int item_height, width;     /* explicit values; 0 = take from the record */
	char items[SCR_ID];         /* "taxonomy" or "taxonomy-sub" */
	char sub[SCR_ID];           /* menu id of the flyout for each item, "" = none */
	char style[SCR_ASSET];      /* asset of an original strip whose colours the items take (e.g. CAT_PHYS) */
	long colour[3];             /* the record's colours A (ground), B (ground under the pointer), C (label, rules),
	                               resolved 0xRRGGBB, -1 = no record */
	unsigned rec_flags;         /* the record's flags (REC_MENU_RULES), 0 = no record */
	int margin;                 /* the record's left margin for labels, 0 = no record */
	char art[SCR_ASSET];        /* "art <dir>": his category strips <dir>/<category id> (2 frames: normal, selected) */
	int at_x, at_y;             /* "at x y": the menu's top-left in the frame; -1 = under the control that opens it */
	struct { int cmd, action; char arg[SCR_LABEL]; } on[SCR_MAX_MENU_ITEMS]; /* "on <cmd> <action> [arg]" */
	int non;
	int hide[SCR_MAX_MENU_ITEMS], nhide; /* "hide <cmd>": items left out (MindMaze, 00-UPDATE item 37) */
	struct { int cmd, frames; char asset[SCR_ASSET]; } icon[SCR_MAX_MENU_ICONS]; /* "icon <cmd> <asset> <frames>" */
	int nicon;
	scr_menu_item it[SCR_MAX_MENU_ITEMS]; /* run time, "items record": loaded from the store (menus.c) */
	int nit;
	char file[64];              /* the screen file that defined it */
} scr_menu;

typedef struct {
	char name[SCR_ID];
	int w, h, ox, oy;           /* size, and origin: a full screen's work area in the frame, or an overlay's place
	                               in its base screen (app.c makes overlay origins absolute after loading) */
	char over[SCR_ID];          /* the full screen under this overlay, or "*": whichever full screen is in front when
	                               it opens (the Pinpointer, notices) */
	int rox, roy;               /* an overlay's origin as written (inside its base's work area) */
	long ground;
	scr_image *img;
	int nimg, capimg;
	char sound[SCR_ASSET];
	int sound_loop;
	scr_control *c;
	int nc, capc;
	scr_prop *props;            /* screen-level "prop" lines (e.g. "prop window-title ...") */
	int nprops;
	scr_row *rows;              /* screen-level "data" lines */
	int nrows;
	char file[64];              /* the screen file that defined it (the registry lint names it) */
} scr_screen;

/* "<key> <scope> <action> [arg]" in a screens/ *.keys file: a key bound on one screen (scope = its name) or on
 * every screen ("*"). A control's own "key" wins over a binding; two bindings of one key in one scope are refused. */
typedef struct {
	char key[SCR_ID];           /* [Control+][Shift+][Alt+]<keysym name>, e.g. "Control+b" */
	char scope[SCR_ID];
	int action;
	char arg[SCR_LABEL];
	char file[64];
	int line;
} scr_key;

typedef struct {
	scr_screen *s;
	int ns, caps;
	scr_menu *m;
	int nm, capm;
	scr_screen *blocks;         /* a block is a screen without size, reused with 'uses' */
	int nb, capb;
	scr_key *keys;
	int nkeys, capk;
	char store[512];            /* the asset store, for "include store:<path>" ("" = refuse such includes) */
} scr_set;

/* Colours in screen files: "ui:<n>" names entry n (0-31) of the period design's interface colour table, read from the
 * store at run time (records.h, ui/palette.tsv); "#rrggbb" is kept for colours that are not the Library's. Until
 * scr_resolve_colours runs, a "ui:" colour is held as SCR_UI(n). */
#define SCR_UI_BASE 0x40000000L
#define SCR_UI(n) (SCR_UI_BASE + (long)(n))
#define SCR_IS_UI(v) ((v) >= SCR_UI_BASE && (v) < SCR_UI_BASE + 256)
/* Parses "#rrggbb" or "ui:<n>" (n 0-255); 0, or -1. */
int scr_parse_colour(const char *s, long *v);
/* Replaces every SCR_UI(n) colour (ground, fg, bg) with pal[n]; -1 with a message when n >= npal or pal[n] < 0. */
int scr_resolve_colours(scr_set *set, const long *pal, int npal, char *err, size_t errlen);
/* One colour: SCR_UI(n) -> pal[n] (or -1 when absent); any other value unchanged. */
long scr_colour(long v, const long *pal, int npal);

/* Loads <dir>/<file> (and its includes) into set (appending). Returns 0, or -1 with "file:line: reason". */
int scr_load(scr_set *set, const char *dir, const char *file, char *err, size_t errlen);
/* Every <dir>/ *.scr: main.scr and dialogs.scr first (they hold the shared blocks), then the rest by name. */
int scr_load_dir(scr_set *set, const char *dir, char *err, size_t errlen);
/* Every <dir>/ *.keys by name. Returns 0, or -1 with "file:line: reason" (a bad line, or a key bound twice). */
int scr_keys_load_dir(scr_set *set, const char *dir, char *err, size_t errlen);
/* The store "include store:<path>" reads from (no store: such includes are refused). */
void scr_set_store(scr_set *set, const char *store);
/* Checks cross-references (goto targets, over, menus, enabled-by, frame indices). Returns 0 or -1. */
int scr_validate(const scr_set *set, char *err, size_t errlen);
scr_screen *scr_find(scr_set *set, const char *name);
const scr_menu *scr_menu_find(const scr_set *set, const char *id);
scr_control *scr_control_find(scr_screen *s, const char *id);
const char *scr_state_name(int st);
const char *scr_font_name(int font);
/* "prop" values: NULL when the control (screen) has none of that name */
const char *scr_prop_get(const scr_control *c, const char *name);
const char *scr_screen_prop(const scr_screen *s, const char *name);
/* 1 when running action id with no handler shows the notice box: "notice" itself, or a named action */
int scr_action_is_notice(int action);

#endif
