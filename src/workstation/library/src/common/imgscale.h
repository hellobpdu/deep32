/* imgscale.h: pictures to a box (W1-IMAGE, plan R3 and W10). X-free and exact: integer arithmetic only, so the same
 * input gives the same pixels on every machine and the tests can state them.
 *   - an axis that shrinks is area-averaged (every source pixel weighs by the part of it the output pixel covers);
 *   - an axis that grows takes the nearest pixel or a bilinear blend (centre-aligned samples, edges clamped);
 *   - an axis that keeps its size is copied;
 *   - transparency is averaged premultiplied (no dark fringes), then composited over the ground.
 * "Fit" keeps the aspect and pads the box with the ground (the Library's ground round a picture); "fill" crops the
 * middle. The optional period mode (00-UPDATE item 27, plan W10) dithers to 256 colours with an ordered (Bayer 8x8)
 * dither onto the 6x6x6 colour cube a 256-colour Windows 3.1 display offered photographs; cube colours stay exact.
 * RGBA everywhere: 4 bytes a pixel, R G B A, rows top-down. (Not "scale.c": src/app/scale.c is update 1's
 * integer maximise of the whole frame, and the archive cannot hold two members of one name.) */
#ifndef E26_IMGSCALE_H
#define E26_IMGSCALE_H
#include <stddef.h>

enum { IMG_NEAREST, IMG_BILINEAR, IMG_BICUBIC };  /* u14 S4: bicubic (Catmull-Rom) where both axes grow, else as bilinear */
enum { IMG_FIT, IMG_FILL, IMG_FIT_DOWN };

/* resamples a sw x sh RGBA picture (stride bytes per source row) to dw x dh into dst (dw * 4 bytes per row);
 * 0, or -1 (a size below 1 or no memory) */
int img_resample(const unsigned char *src, size_t stride, int sw, int sh, unsigned char *dst, int dw, int dh, int filter);
/* the size a sw x sh picture takes inside a bw x bh box with its aspect kept (rounded, at least 1 x 1) */
void img_fit_size(int sw, int sh, int bw, int bh, int *w, int *h);
/* the centred part of a sw x sh picture with the aspect of a bw x bh box (what "fill" shows) */
void img_fill_crop(int sw, int sh, int bw, int bh, int *cx, int *cy, int *cw, int *ch);
/* a picture into a bw x bh box (dst: bw * bh * 4 bytes) on the ground bg (R G B A): IMG_FIT centres it and pads with
 * the ground, IMG_FILL crops the middle, and IMG_FIT_DOWN never enlarges. Every pixel is composited over the ground. */
int img_fit(const unsigned char *src, int sw, int sh, unsigned char *dst, int bw, int bh, int mode, int filter,
            const unsigned char bg[4]);
/* A 1:1 view at source offset (x,y), clamped to the image. Centre any axis smaller than the box. */
int img_actual(const unsigned char *src, int sw, int sh, unsigned char *dst, int bw, int bh,
               int x, int y, const unsigned char bg[4]);
/* u14 S4: a bw x bh tile of the picture at zoom percent, its top-left at (x, y) of the zoomed picture (clamped; a
 * smaller axis centred); 0, or -1 */
int img_zoom(const unsigned char *src, int sw, int sh, unsigned char *dst, int bw, int bh, int zoom,
             int x, int y, int filter, const unsigned char bg[4]);
/* RGBA over a ground, in place */
void img_over(unsigned char *rgba, size_t npix, const unsigned char bg[4]);
/* the period mode: an ordered dither of the colour channels onto the 6x6x6 cube (levels 0, 51, ..., 255), in place;
 * alpha is kept */
void img_dither256(unsigned char *rgba, int w, int h);

#endif
