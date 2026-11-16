/* gfx.h: every pixel Deep32 Library draws goes through these primitives (W0-F0, W1-SHARP). A drawing context names the
 * drawable and a scale factor k: callers give logical 640x480 coordinates and logical font metrics; the primitives
 * place them at k times on the drawable. At k = 1 every primitive is the X call build 2 made, so the frame is
 * byte-identical. At k > 1 (SHARP mode, item 63: the back buffer is 640k x 480k, present.c) the frame is drawn at
 * full resolution without touching a room or a kind:
 *   - fills, lines and frames cover exactly the device pixels of their logical pixels (a 1-px line is k px wide);
 *     a sloped line and a polygon are drawn at device resolution from their scaled ends and corners;
 *   - text comes from the same face at k times its pixel size, each glyph's ink centred where its 1x glyph's ink
 *     is (scale.h), so lines, wraps and hit boxes are those of 1x; a face with no outline (a Windows 3.1 raster face,
 *     a bitmap fallback) is drawn as its 1x glyphs doubled nearest, as the 1995 bitmaps are;
 *   - the 1995 interface bitmaps (gfx_blit, gfx_copy) are doubled nearest, with their masks (period-true);
 *   - photos and content pictures (gfx_photo, gfx_picture_*) are resampled smoothly (scale.h: area-average when
 *     shrinking, Lanczos-3 when enlarging) from the best pixels the caller has.
 * Text has one entry point, gfx_text; the measuring calls answer in logical pixels at every k. */
#ifndef E26_GFX_H
#define E26_GFX_H
#include <X11/Xlib.h>
#include <stddef.h>

struct e26_app;
struct e26_xasset;

typedef struct gfx_ctx {
	struct e26_app *a;   /* the application: display, GC, fonts, colour masks */
	Drawable d;          /* the back buffer (the frame), or another drawable (the splash) */
	int k;               /* device pixels per logical pixel: 1, or the SHARP factor of the back buffer */
} gfx_ctx;

void gfx_init(gfx_ctx *g, struct e26_app *a, Drawable d, int k);

/* shapes (colours are 0xRRGGBB) */
void gfx_fill(gfx_ctx *g, int x, int y, int w, int h, long rgb);
void gfx_line(gfx_ctx *g, int x1, int y1, int x2, int y2, long rgb);
void gfx_poly(gfx_ctx *g, const int *xy, int npoints, long rgb);    /* a filled convex polygon, xy = x0 y0 x1 y1 ... */
/* the 1-px frame the period program draws round a control: top-left pen on the left column and top row, bottom-right pen on
 * the bottom row and right column (winning the shared corners); a negative pen draws no frame */
void gfx_frame2(gfx_ctx *g, int x, int y, int w, int h, long tl, long br);

/* bitmaps from the asset store (xassets.c): a part of a strip, with its transparency mask when it has one */
void gfx_blit(gfx_ctx *g, const struct e26_xasset *x, int sx, int sy, int w, int h, int dx, int dy);
/* the same, ignoring the mask (an engraving copied with its own ground) */
void gfx_copy(gfx_ctx *g, const struct e26_xasset *x, int sx, int sy, int w, int h, int dx, int dy);
/* a store bitmap that is a photo or a content picture, not interface art: at k > 1 resampled smoothly instead of
 * doubled (its mask, if any, doubled); at k = 1 exactly gfx_blit */
void gfx_photo(gfx_ctx *g, const struct e26_xasset *x, int sx, int sy, int w, int h, int dx, int dy);

/* pictures from pixels (W1-IMAGE's kind_picture: e26-render decodes and fits; ask it for gfx_device_size of the
 * logical box, so the pixels arrive at full resolution). A gfx_pic is a server pixmap at device resolution for one
 * k: make it once, draw it on every frame, make it again when gfx_picture_draw returns -1 (k changed). */
typedef struct gfx_pic {
	Pixmap pm;
	int w, h;            /* the logical box */
	int k;               /* the factor it was made for */
} gfx_pic;
void gfx_device_size(const gfx_ctx *g, int w, int h, int *dw, int *dh);  /* a logical box in device pixels */
/* rgba: pw x ph pixels, 4 bytes each (R, G, B, A; A ignored), stride bytes a row; resampled smoothly to the box at
 * g's k unless already that size; 0, or -1 (no memory) */
int gfx_picture_make(gfx_ctx *g, const unsigned char *rgba, int pw, int ph, size_t stride, int w, int h, gfx_pic *out);
int gfx_picture_draw(gfx_ctx *g, const gfx_pic *p, int x, int y);       /* -1: made for another k (make it again) */
void gfx_picture_free(gfx_ctx *g, gfx_pic *p);

/* clipping: one rectangle at a time (a masked blit resets it, as build 2's did) */
void gfx_clip(gfx_ctx *g, int x, int y, int w, int h);
void gfx_unclip(gfx_ctx *g);

/* text: the one entry point that puts glyphs on a drawable (n < 0: the whole string), and its metrics */
void gfx_text(gfx_ctx *g, int x, int y, int font, long rgb, const char *s, int n);
int gfx_text_width(gfx_ctx *g, int font, const char *s, int n);
int gfx_ascent(gfx_ctx *g, int font);
int gfx_descent(gfx_ctx *g, int font);
int gfx_font_height(gfx_ctx *g, int font);    /* ascent + descent */

/* text helpers built on gfx_text */
/* a one-line label with letter spacing (tenths of a pixel after every letter) and optional capitals; returns the
 * width; draw 0 only measures */
int gfx_spaced(gfx_ctx *g, int x, int y, int font, long rgb, const char *s, int tracking, int upper, int draw);
/* a run with key names marked GFX_KEY_ON ... GFX_KEY_OFF lettered in the small face ("|ENTER|", string 14713) */
#define GFX_KEY_ON '\x01'
#define GFX_KEY_OFF '\x02'
int gfx_seg_width(gfx_ctx *g, int font, const char *s, int n);
void gfx_seg_text(gfx_ctx *g, int x, int y, int font, long rgb, const char *s, int n);
/* lines broken at '\n' and wherever the next word would pass w: the first baseline at by, then one every lh px */
void gfx_wrap_lines(gfx_ctx *g, int x, int by, int w, int font, long rgb, const char *s, int align, int lh);
/* as gfx_wrap_lines from the top y, on the font's own pitch (its height + 1) */
void gfx_wrap_text(gfx_ctx *g, int x, int y, int w, int font, long rgb, const char *s, int align);

/* the period glyphs the code draws: the menu marker (a small solid down arrow) and the flyout arrow */
void gfx_marker(gfx_ctx *g, int x, int y, long rgb);
void gfx_arrow_right(gfx_ctx *g, int x, int y, long rgb);

/* SHARP's caches (bitmaps, glyph strips and faces at k): present.c drops every one not made for k when the back
 * buffer changes factor; xassets.c calls gfx_forget_pixmap before it frees an asset's pixmap (W1-IMAGE's LRU) */
void gfx_k_changed(struct e26_app *a, int k);
void gfx_forget_pixmap(struct e26_app *a, Pixmap pm);
/* how text at k is drawn for a font slot: 1 from a k-times outline face, 0 doubled from 1x glyphs (fonts.c) */
int gfx_text_sharp(gfx_ctx *g, int font);

/* ---- fonts.c (W1-SHARP): the same face at k times its pixel size for a slot, or NULL when it has no outline (the
 * slot's text is then doubled); loaded on first use, kept until the slot's 1x face or k changes */
XFontStruct *fonts_at_k(struct e26_app *a, int slot, int k);
int fonts_article_step(int slot, int direction);

/* ---- present.c (W1-SHARP): the scaler and the back buffer at k (the declarations in library_app.h stay as they are) */
/* *scaler or LIBRARY.INI Scaler: "sharp" (the default: the frame drawn at k), "render" or "cpu" (update 1: the
 * 640x480 frame enlarged nearest, by RENDER or by the CPU); 0, or -1 for an unknown name (nothing changes). The
 * caller redraws (nav_redraw). */
int present_set_scaler(struct e26_app *a, const char *name);
const char *present_scaler(void);   /* the scaler in use */
/* a logical rectangle of the back buffer to the window (for wipes and partial paints, W1-CHROME) */
void present_area(struct e26_app *a, int x, int y, int w, int h);

#endif
