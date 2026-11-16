/* records.h: the period design's own interface records, read from the private asset store (ui/rcdata/<id>.bin,
 * ui/strings.tsv, ui/palette.tsv), as decoded in SCREENS.md sections 2-3 and PALETTE.md (with one correction,
 * below).
 *
 * Control record (RCDATA 10001-10202, 64 bytes): 7 x int32 colour slots (indices into the interface colour table;
 * -1 = not set, the window class's default stays; -2 = transparent: no fill and no frame), uint32 flags (the
 * window style bits ORed into CreateWindow: low byte 0x40 = bitmap button), ONE int16 id at offset 32
 * (0x7FFF/0x7FFE/0x7FFC = none), and the asciiz label from offset 34. (SCREENS.md said two int16 ids and a label at
 * 36; every one of the 108 records has its label at 34, e.g. 10101 "|^enter".)
 * Menu header (RCDATA 12000-12098, 64 bytes): int32 colour A (item ground), int32 colour B (ground of the item under
 * the pointer), int32 colour C (label, and the rules between items), int32 item-list id, then uint16s: +16 0,
 * +18 flags (0x0800: a rule in colour C under every item but the last), +20 item height, +22 width, +24 0, +26 0x65,
 * +28 item count, +30 left margin.
 * Item list: 14-byte items: uint16 target string id, 0, 0, colour override (2 x 0xFFFF = inherit C), 0x0065,
 * uint16 label string id. */
#ifndef E26_RECORDS_H
#define E26_RECORDS_H
#include <stddef.h>

/* The seven colour slots, in record order (PALETTE.md 3; the period program seg19:094e sends slot k to the control as
 * message 0x439 + k, seg18:0378 keeps it at +0x18 + 4k, seg19:0b8c and seg19:1080 paint the states with them). */
enum {
	REC_FACE,           /* fill in every enabled state; also the erase colour */
	REC_LABEL,          /* label colour while enabled */
	REC_FACE_DISABLED,  /* fill while disabled */
	REC_EDGE_LIGHT,     /* top and left edge under the pointer; bottom and right edge while pressed */
	REC_EDGE_DARK,      /* bottom and right edge under the pointer; top and left edge while pressed */
	REC_FRAME_IDLE,     /* the 1-px frame at rest (pointer elsewhere) */
	REC_FRAME_DISABLED, /* the 1-px frame while disabled */
	REC_NSLOTS
};
#define REC_UNSET (-1)
#define REC_TRANSPARENT (-2)

/* Indices of the interface colour table that the engine names (the RGB values live in the store only). */
enum {
	UI_WHITE = 0,      /* light edge default */
	UI_PALE_YELLOW = 1, /* partial table cells */
	UI_PALE_RED = 4,   /* negative table cells */
	UI_PALE_BLUE = 7,  /* highlighted table cells */
	UI_CAPTION = 12,   /* caption and section bars, the dark warm grey of CAPTION/CAPTUP */
	UI_TAUPE = 13,     /* Find's face, scroll-bar thumb */
	UI_STONE = 14,     /* the control class's default face (Go Back's face); the outline's current-row band; caption
	                      and section bar lettering (256-colour captures) */
	UI_BLACK = 15,     /* default label; black brush of the top bar */
	UI_MAROON = 19,    /* list jumps: the outline's media entries and the Related Articles list (256-colour captures
	                      31, 32, 35) */
	UI_PALE_GREEN = 24, /* positive table cells */
	UI_PERIWINKLE = 27,
	UI_CHARCOAL = 28,  /* the default idle frame; Find Wizard ground; scroll-bar trough */
	UI_PAPER = 31      /* light parchment: arrow faces, left column */
};
/* The class defaults that slots set to -1 leave in place: the period program seg18:0515 (PALETTERGB literals equal to
 * these table entries). */
extern const int rec_slot_default[REC_NSLOTS];
/* Colours the code sets outside the table: the disabled label, RGB(128,128,128) (seg18:0bfd, seg19:05b0), and
 * the jump colour. the period program falls back to 0x000080 = RGB(128,0,0) when LIBRARY.INI has no JumpColor
 * (seg100:0b91), but the Library's Setup writes JumpColor=255 = RGB(255,0,0) into LIBRARY.INI (the INI right after
 * the install, before the first run), so an installed the period design draws its text jumps in bright red: the
 * 256-colour captures show "Aleutian Islands" in #FF0000 and the Settings dialog with the first swatch (red)
 * selected (capture256 63). Deep32 Library has no settings file yet, so it uses the installed value. */
#define REC_DISABLED_LABEL 0x808080L
#define REC_JUMP_DEFAULT 0x800000L      /* the code's fallback, no JumpColor key */
#define REC_JUMP_INSTALLED 0xFF0000L    /* JumpColor=255, as Setup writes it */

typedef struct {
	int colour[REC_NSLOTS];
	unsigned long flags;
	int id;
	char label[30];
	int bitmap_button; /* label starts "|^" */
} rec_control;

typedef struct {
	int colour_a, colour_b, colour_c;
	int list_id;
	unsigned flags;
	int item_height, width, count, margin;
} rec_menu;
#define REC_MENU_RULES 0x0800 /* rec_menu.flags: rules between items (seg26:06e9 tests bit 3 of the high byte) */

typedef struct {
	int target_sid, label_sid;
} rec_menu_item;

int rec_control_load(const char *store, int id, rec_control *out);
int rec_menu_load(const char *store, int id, rec_menu *out);
/* Loads up to max items of an item list; returns the count or -1. */
int rec_menu_items(const char *store, int list_id, rec_menu_item *items, int max);
/* The first count entries of an item list as the original's static menus use them (12050-12053): an entry is its
 * label string (or "|Bitmap_name"), its command id (the entry's last field), or a rule: no string and both colour
 * fields 0xFFFF (12053: Help..., a rule, Settings..., About Library..., a rule, Title Screen..., Printer Setup...).
 * Returns the number read, or -1. */
typedef struct {
	int sid, cmd, rule;
} rec_menu_entry;
int rec_menu_entries(const char *store, int list_id, int count, rec_menu_entry *e, int max);

/* The interface colour table (ui/palette.tsv: "index<TAB>#RRGGBB<TAB>logical-palette-entry", 32 rows). */
#define REC_PAL_N 32
typedef struct {
	long rgb[REC_PAL_N]; /* 0xRRGGBB, -1 when the row is missing */
	int n;               /* rows read */
} rec_palette;
int rec_palette_load(const char *store, rec_palette *p); /* 0, or -1 when the file is missing or incomplete */
long rec_pal(const rec_palette *p, int index);           /* 0xRRGGBB, or -1 */

/* Original string table (ui/strings.tsv). */
typedef struct {
	int *id;
	char **text;
	size_t n;
} rec_strings;
int rec_strings_load(const char *store, rec_strings *s);
const char *rec_string(const rec_strings *s, int id); /* NULL when absent */
void rec_strings_free(rec_strings *s);

/* An original string as a label: "&&" is "&"; an "&" before a letter or digit marks a mnemonic and is dropped
 * ("&Play", "P&ause", strings 16053-16055); any other "&" is a literal ampersand, as Library draws it ("Charts &
 * Tables", string 14723: capture256 12 shows the glyph); a "||" suffix ("BC||f") is cut. With ellipsis set, a 0x85
 * byte (cp1252's ellipsis) becomes "...". */
void rec_label(const char *in, char *out, size_t n, int ellipsis);

#endif
