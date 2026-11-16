/* scale.h: maximise as integer scaling (update 1). Deep32 Library draws its 640x480 logical frame into a back buffer
 * and nothing else changes size: a window larger than the frame (maximised by the window manager or by the Library's
 * own caption button) shows the frame enlarged k times, k the largest integer that fits (2 on 1920x1080), centred
 * on black, every frame pixel a k x k block (nearest neighbour). The pointer is mapped back to frame coordinates.
 * At k = 1 in a 640x480 window the frame is drawn exactly as before. No X here: test_unit checks it all. */
#ifndef E26_SCALE_H
#define E26_SCALE_H
#include <stddef.h>

#define E26_FRAME_W 640
#define E26_FRAME_H 480

typedef struct {
	int k;        /* the factor, at least 1 */
	int ox, oy;   /* the enlarged frame's top-left in the window (0 when the window is smaller than it) */
	int w, h;     /* the window's size */
} e26_scale;

/* the layout for a window of win_w x win_h; returns 1 when it differs from what *s held */
int scale_fit(e26_scale *s, int win_w, int win_h);
/* a window point to frame coordinates (floor division: a point on the black margin maps outside 0..639, 0..479) */
void scale_to_frame(const e26_scale *s, int wx, int wy, int *fx, int *fy);
/* a frame point to the window: the top-left pixel of its k x k block */
void scale_to_window(const e26_scale *s, int fx, int fy, int *wx, int *wy);
/* 1 when the layout is the unscaled one in a window of exactly the frame's size (the original present path) */
int scale_is_identity(const e26_scale *s);
/* the margin round the enlarged frame as up to four rectangles (x, y, w, h each); returns how many */
int scale_margins(const e26_scale *s, int r[4][4]);
/* nearest-neighbour enlargement of a w x h image of bpp bytes per pixel (1 to 4) by k: dst holds w*k x h*k pixels
 * (strides in bytes). Rows are built once and copied k - 1 times. */
void scale_pixels(const unsigned char *src, size_t src_stride, unsigned char *dst, size_t dst_stride,
                  int w, int h, int bpp, int k);

/* SHARP mode (build 3, W1-SHARP, item 63): instead of enlarging the finished 640x480 frame, the frame is drawn at
 * k times into a 640k x 480k back buffer (gfx.c, present.c). The arithmetic it needs, X-free like the rest:
 *   - a glyph from a k-times face goes where its 1x glyph is: its ink centred on k x the 1x ink's centre (the
 *     faces are hinted separately at each size, so the ink sits differently in the advance), or, for a glyph with no
 *     ink, centred in its logical cell (k x the 1x pen, k x the 1x advance wide); text keeps its 1x layout to the
 *     pixel and its 1x rhythm;
 *   - an XLFD with its pixel size times k (the same face at k times its size);
 *   - smooth resampling for photos and content pictures (never for the 1995 interface bitmaps, which are doubled
 *     nearest): area-average along an axis that shrinks, Lanczos-3 along one that grows. */
#define SCALE_SHARP_MAX 8 /* the largest k the sharp back buffer is made for (beyond it: update 1's scaling) */
/* a glyph across: its advance, and its ink's left and right edges from its origin (rb <= lb: no ink) */
typedef struct {
	int w, lb, rb;
} scale_glyph;
/* the k-times glyph's origin (from k x the string's 1x origin) for the 1x glyph g1 at pen1 */
int scale_glyph_x(int k, int pen1, const scale_glyph *g1, const scale_glyph *gk);
/* name with its PIXEL_SIZE times k and POINT_SIZE and AVERAGE_WIDTH left to the server ("*"); 0, or -1 when name is
 * not a 14-field XLFD with a numeric pixel size; *px (when not NULL) gets the 1x pixel size */
int scale_xlfd_k(const char *name, int k, char *out, size_t n, int *px);
/* resample sw x sh pixels of 4 bytes each (every byte a channel of its own) to dw x dh; 0, or -1 (no memory, bad
 * size). Equal sizes copy; an integer shrink averages each k x k block exactly (rounded); a constant stays constant. */
int scale_smooth(const unsigned char *src, size_t src_stride, int sw, int sh,
                 unsigned char *dst, size_t dst_stride, int dw, int dh);

#endif
