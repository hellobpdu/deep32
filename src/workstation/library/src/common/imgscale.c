/* imgscale.c: pictures to a box (imgscale.h). Every output pixel is a weighted sum of source pixels with integer
 * weights per axis (area overlaps when an axis shrinks, one tap or two bilinear taps when it grows), so the result is
 * exact and repeatable. */
#include "imgscale.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
	int *first, *n;      /* per output index: first source index and the number of taps */
	uint32_t *w;         /* the taps' weights, n per output index, stored at first[i]'s slot (see off) */
	int *off;            /* where output index i's weights start in w */
	uint64_t total;      /* the sum of every output index's weights */
} taps;

static void taps_free(taps *t) { free(t->first); free(t->n); free(t->w); free(t->off); memset(t, 0, sizeof *t); }

/* the weights of one axis from s source pixels to d output pixels */
static int taps_make(taps *t, int s, int d, int filter)
{
	int i, k = 0, cap;
	memset(t, 0, sizeof *t);
	cap = d < s ? s + 2 * d : 2 * d;               /* area: at most s + d taps in all; growing: at most two each */
	t->first = (int *)malloc((size_t)d * sizeof(int));
	t->n = (int *)malloc((size_t)d * sizeof(int));
	t->off = (int *)malloc((size_t)d * sizeof(int));
	t->w = (uint32_t *)malloc((size_t)cap * sizeof(uint32_t));
	if (!t->first || !t->n || !t->off || !t->w) { taps_free(t); return -1; }
	for (i = 0; i < d; i++) {
		t->off[i] = k;
		if (d < s) {                                 /* area: output i covers [i*s, (i+1)*s) in units of 1/d */
			long lo = (long)i * s, hi = (long)(i + 1) * s, j;
			t->first[i] = (int)(lo / d);
			t->n[i] = 0;
			for (j = lo / d; j * d < hi; j++) {
				long a = j * d > lo ? j * d : lo, b = (j + 1) * d < hi ? (j + 1) * d : hi;
				t->w[k++] = (uint32_t)(b - a);
				t->n[i]++;
			}
			t->total = (uint64_t)s;
		} else if (d == s) {
			t->first[i] = i; t->n[i] = 1; t->w[k++] = 1; t->total = 1;
		} else if (filter == IMG_NEAREST) {
			t->first[i] = (int)(((2L * i + 1) * s) / (2L * d));
			t->n[i] = 1; t->w[k++] = 1; t->total = 1;
		} else {                                     /* bilinear: the sample at ((2i+1)s - d) / 2d, clamped */
			long num = (2L * i + 1) * s - d, den = 2L * d, j0, fr;
			t->total = (uint64_t)den;
			if (num <= 0) { t->first[i] = 0; t->n[i] = 1; t->w[k++] = (uint32_t)den; continue; }
			j0 = num / den; fr = num - j0 * den;
			if (j0 >= s - 1 || fr == 0) { t->first[i] = (int)(j0 >= s - 1 ? s - 1 : j0); t->n[i] = 1; t->w[k++] = (uint32_t)den; continue; }
			t->first[i] = (int)j0; t->n[i] = 2;
			t->w[k++] = (uint32_t)(den - fr);
			t->w[k++] = (uint32_t)fr;
		}
	}
	return 0;
}

/* u14 S4: Catmull-Rom (a = -0.5) weights in 8.8 fixed point for a sample t/256 of the way from tap 1 to tap 2 */
static void cubic_weights(long t, int w[4])
{
	long t2 = t * t, t3 = t2 * t;
	w[0] = (int)((-t3 + 512 * t2 - 65536 * t) / 131072);
	w[1] = (int)((3 * t3 - 1280 * t2 + 33554432L) / 131072);
	w[2] = (int)((-3 * t3 + 1024 * t2 + 65536 * t) / 131072);
	w[3] = 256 - w[0] - w[1] - w[2];
}

/* one axis of a bicubic enlargement: per output index the first of its four (clamped) source taps and their weights */
static int cubic_axis(int s, int d, int **first, int **wt)
{
	int i, k;
	*first = (int *)malloc((size_t)d * 4 * sizeof(int));
	*wt = (int *)malloc((size_t)d * 4 * sizeof(int));
	if (!*first || !*wt) { free(*first); free(*wt); *first = *wt = NULL; return -1; }
	for (i = 0; i < d; i++) {
		/* the centre-aligned sample ((2i+1)s - d) / 2d, its floor and the fraction in 1/256 */
		long num = (2L * i + 1) * s - d, den = 2L * d, j0 = num >= 0 ? num / den : -((-num + den - 1) / den);
		int w[4];
		cubic_weights(((num - j0 * den) * 256) / den, w);
		for (k = 0; k < 4; k++) {
			long j = j0 - 1 + k;
			(*first)[4 * i + k] = (int)(j < 0 ? 0 : j >= s ? s - 1 : j);
			(*wt)[4 * i + k] = w[k];
		}
	}
	return 0;
}

static unsigned char clamp255(int64_t v) { return (unsigned char)(v < 0 ? 0 : v > 255 ? 255 : v); }

/* u14 S4: a bicubic (Catmull-Rom) enlargement, premultiplied so transparent edges keep no dark fringe */
static int resample_cubic(const unsigned char *src, size_t stride, int sw, int sh, unsigned char *dst, int dw, int dh)
{
	int *fx = NULL, *wx = NULL, *fy = NULL, *wy = NULL, x, y, kx, ky;
	if (cubic_axis(sw, dw, &fx, &wx) || cubic_axis(sh, dh, &fy, &wy)) { free(fx); free(wx); free(fy); free(wy); return -1; }
	for (y = 0; y < dh; y++)
		for (x = 0; x < dw; x++) {
			int64_t c[3] = { 0, 0, 0 }, sa = 0;
			unsigned char *o = dst + ((size_t)y * dw + x) * 4;
			for (ky = 0; ky < 4; ky++) {
				const unsigned char *row = src + (size_t)fy[4 * y + ky] * stride;
				int64_t w1 = wy[4 * y + ky];
				for (kx = 0; kx < 4; kx++) {
					const unsigned char *p = row + (size_t)fx[4 * x + kx] * 4;
					int64_t w = w1 * wx[4 * x + kx], wa = w * p[3];
					c[0] += wa * p[0]; c[1] += wa * p[1]; c[2] += wa * p[2];
					sa += wa;
				}
			}
			if (sa <= 0) { o[0] = o[1] = o[2] = o[3] = 0; continue; }
			o[0] = clamp255((c[0] + sa / 2) / sa);
			o[1] = clamp255((c[1] + sa / 2) / sa);
			o[2] = clamp255((c[2] + sa / 2) / sa);
			o[3] = clamp255((sa + 32768) / 65536);
		}
	free(fx); free(wx); free(fy); free(wy);
	return 0;
}

int img_resample(const unsigned char *src, size_t stride, int sw, int sh, unsigned char *dst, int dw, int dh, int filter)
{
	taps tx, ty;
	int x, y;
	uint64_t total;
	if (sw < 1 || sh < 1 || dw < 1 || dh < 1) return -1;
	if (sw == dw && sh == dh) {
		for (y = 0; y < sh; y++) memcpy(dst + (size_t)y * dw * 4, src + (size_t)y * stride, (size_t)sw * 4);
		return 0;
	}
	/* u14 S4: bicubic only where both axes grow; a shrinking axis is area-averaged as ever */
	if (filter == IMG_BICUBIC) {
		if (dw > sw && dh > sh) return resample_cubic(src, stride, sw, sh, dst, dw, dh);
		filter = IMG_BILINEAR;
	}
	if (taps_make(&tx, sw, dw, filter)) return -1;
	if (taps_make(&ty, sh, dh, filter)) { taps_free(&tx); return -1; }
	total = tx.total * ty.total;
	for (y = 0; y < dh; y++) {
		for (x = 0; x < dw; x++) {
			uint64_t c[3] = { 0, 0, 0 }, pc[3] = { 0, 0, 0 }, sa = 0;
			int jy, jx;
			unsigned char *o = dst + ((size_t)y * dw + x) * 4;
			for (jy = 0; jy < ty.n[y]; jy++) {
				const unsigned char *row = src + (size_t)(ty.first[y] + jy) * stride;
				uint64_t wy = ty.w[ty.off[y] + jy];
				for (jx = 0; jx < tx.n[x]; jx++) {
					const unsigned char *p = row + (size_t)(tx.first[x] + jx) * 4;
					uint64_t w = wy * tx.w[tx.off[x] + jx], wa = w * p[3];
					c[0] += w * p[0]; c[1] += w * p[1]; c[2] += w * p[2];
					pc[0] += wa * p[0]; pc[1] += wa * p[1]; pc[2] += wa * p[2];
					sa += wa;
				}
			}
			if (sa == 255 * total) {                 /* every tap opaque: the plain average, rounded */
				o[0] = (unsigned char)((c[0] + total / 2) / total);
				o[1] = (unsigned char)((c[1] + total / 2) / total);
				o[2] = (unsigned char)((c[2] + total / 2) / total);
				o[3] = 255;
			} else if (sa == 0) {
				o[0] = o[1] = o[2] = o[3] = 0;
			} else {                                 /* premultiplied: colour weighed by its alpha */
				o[0] = (unsigned char)((pc[0] + sa / 2) / sa);
				o[1] = (unsigned char)((pc[1] + sa / 2) / sa);
				o[2] = (unsigned char)((pc[2] + sa / 2) / sa);
				o[3] = (unsigned char)((sa + total / 2) / total);
			}
		}
	}
	taps_free(&tx);
	taps_free(&ty);
	return 0;
}

static int rdiv(long a, long b) { return (int)((2 * a + b) / (2 * b)); }

void img_fit_size(int sw, int sh, int bw, int bh, int *w, int *h)
{
	if ((long)sw * bh >= (long)bw * sh) { *w = bw; *h = rdiv((long)sh * bw, sw); }
	else { *h = bh; *w = rdiv((long)sw * bh, sh); }
	if (*w < 1) *w = 1;
	if (*h < 1) *h = 1;
	if (*w > bw) *w = bw;
	if (*h > bh) *h = bh;
}

void img_fill_crop(int sw, int sh, int bw, int bh, int *cx, int *cy, int *cw, int *ch)
{
	if ((long)sw * bh > (long)bw * sh) { *ch = sh; *cw = rdiv((long)sh * bw, bh); }
	else { *cw = sw; *ch = rdiv((long)sw * bh, bw); }
	if (*cw < 1) *cw = 1;
	if (*ch < 1) *ch = 1;
	if (*cw > sw) *cw = sw;
	if (*ch > sh) *ch = sh;
	*cx = (sw - *cw) / 2;
	*cy = (sh - *ch) / 2;
}

static void over1(unsigned char *p, const unsigned char bg[4])
{
	unsigned a = p[3], na = 255 - a, k;
	if (a == 255) return;
	for (k = 0; k < 3; k++) p[k] = (unsigned char)((p[k] * a + bg[k] * na + 127) / 255);
	p[3] = (unsigned char)(a + (bg[3] * na + 127) / 255);
}

void img_over(unsigned char *rgba, size_t npix, const unsigned char bg[4])
{
	size_t i;
	for (i = 0; i < npix; i++) over1(rgba + 4 * i, bg);
}

int img_fit(const unsigned char *src, int sw, int sh, unsigned char *dst, int bw, int bh, int mode, int filter,
            const unsigned char bg[4])
{
	int w, h, ox, oy, y, x;
	size_t i, n = (size_t)bw * bh;
	if (sw < 1 || sh < 1 || bw < 1 || bh < 1) return -1;
	for (i = 0; i < n; i++) memcpy(dst + 4 * i, bg, 4);
	if (mode == IMG_FILL) {
		int cx, cy, cw, ch;
		img_fill_crop(sw, sh, bw, bh, &cx, &cy, &cw, &ch);
		if (img_resample(src + ((size_t)cy * sw + cx) * 4, (size_t)sw * 4, cw, ch, dst, bw, bh, filter)) return -1;
		img_over(dst, n, bg);
		return 0;
	}
	if (mode == IMG_FIT_DOWN && sw <= bw && sh <= bh) { w = sw; h = sh; }
	else img_fit_size(sw, sh, bw, bh, &w, &h);
	ox = (bw - w) / 2;
	oy = (bh - h) / 2;
	{
		unsigned char *tmp = (unsigned char *)malloc((size_t)w * h * 4);
		if (!tmp) return -1;
		if (img_resample(src, (size_t)sw * 4, sw, sh, tmp, w, h, filter)) { free(tmp); return -1; }
		img_over(tmp, (size_t)w * h, bg);
		for (y = 0; y < h; y++)
			for (x = 0; x < w; x++) memcpy(dst + (((size_t)(oy + y) * bw) + ox + x) * 4, tmp + ((size_t)y * w + x) * 4, 4);
		free(tmp);
	}
	return 0;
}

int img_actual(const unsigned char *src, int sw, int sh, unsigned char *dst, int bw, int bh,
               int x, int y, const unsigned char bg[4])
{
	int w = sw < bw ? sw : bw, h = sh < bh ? sh : bh;
	int ox = (bw - w) / 2, oy = (bh - h) / 2, row;
	size_t i, n = (size_t)bw * bh;
	if (w < 1 || h < 1 || x < 0 || y < 0) return -1;
	if (x > sw - w) x = sw - w;
	if (y > sh - h) y = sh - h;
	for (i = 0; i < n; i++) memcpy(dst + 4 * i, bg, 4);
	for (row = 0; row < h; row++) {
		unsigned char *out = dst + ((size_t)(oy + row) * bw + ox) * 4;
		memcpy(out, src + ((size_t)(y + row) * sw + x) * 4, (size_t)w * 4);
		img_over(out, (size_t)w, bg);
	}
	return 0;
}

/* u14 S4: a bw x bh tile of the picture zoomed to zoom percent, its top-left at (x, y) of the zoomed picture (clamped;
 * an axis smaller than the box is centred). Below 100% the whole picture is area-averaged first; above, only the
 * source part under the tile (two pixels more each side for the filter's taps) is enlarged. */
int img_zoom(const unsigned char *src, int sw, int sh, unsigned char *dst, int bw, int bh, int zoom,
             int x, int y, int filter, const unsigned char bg[4])
{
	long zw = ((long)sw * zoom + 50) / 100, zh = ((long)sh * zoom + 50) / 100;
	int w, h, ox, oy, row, sx0, sy0, sx1, sy1, cw, ch, tw, th, ex, ey;
	unsigned char *tmp;
	size_t i, n = (size_t)bw * bh;
	if (sw < 1 || sh < 1 || bw < 1 || bh < 1 || zoom < 1 || x < 0 || y < 0) return -1;
	if (zw < 1) zw = 1;
	if (zh < 1) zh = 1;
	w = zw < bw ? (int)zw : bw; h = zh < bh ? (int)zh : bh;
	ox = (bw - w) / 2; oy = (bh - h) / 2;
	if (x > zw - w) x = (int)(zw - w);
	if (y > zh - h) y = (int)(zh - h);
	for (i = 0; i < n; i++) memcpy(dst + 4 * i, bg, 4);
	if (zoom <= 100) { sx0 = sy0 = 0; sx1 = sw; sy1 = sh; }
	else {
		sx0 = (int)((long)x * sw / zw) - 2; sy0 = (int)((long)y * sh / zh) - 2;
		sx1 = (int)(((long)(x + w) * sw + zw - 1) / zw) + 2; sy1 = (int)(((long)(y + h) * sh + zh - 1) / zh) + 2;
		if (sx0 < 0) sx0 = 0;
		if (sy0 < 0) sy0 = 0;
		if (sx1 > sw) sx1 = sw;
		if (sy1 > sh) sy1 = sh;
	}
	cw = sx1 - sx0; ch = sy1 - sy0;
	/* the zoomed size of that part, and where the tile starts in it */
	tw = (int)(((long)sx1 * zw + sw / 2) / sw - ((long)sx0 * zw + sw / 2) / sw);
	th = (int)(((long)sy1 * zh + sh / 2) / sh - ((long)sy0 * zh + sh / 2) / sh);
	ex = x - (int)(((long)sx0 * zw + sw / 2) / sw); ey = y - (int)(((long)sy0 * zh + sh / 2) / sh);
	if (cw < 1 || ch < 1 || tw < 1 || th < 1) return -1;
	if (ex < 0) ex = 0;
	if (ey < 0) ey = 0;
	if (ex > tw - w) ex = tw - w < 0 ? 0 : tw - w;
	if (ey > th - h) ey = th - h < 0 ? 0 : th - h;
	if ((size_t)tw * th > (size_t)64 * 1024 * 1024) return -1;
	if (!(tmp = (unsigned char *)malloc((size_t)tw * th * 4))) return -1;
	if (img_resample(src + ((size_t)sy0 * sw + sx0) * 4, (size_t)sw * 4, cw, ch, tmp, tw, th, filter)) { free(tmp); return -1; }
	for (row = 0; row < h && ey + row < th; row++) {
		unsigned char *out = dst + ((size_t)(oy + row) * bw + ox) * 4;
		int cols = tw - ex < w ? tw - ex : w;
		memcpy(out, tmp + ((size_t)(ey + row) * tw + ex) * 4, (size_t)cols * 4);
		img_over(out, (size_t)cols, bg);
	}
	free(tmp);
	return 0;
}

/* the 8x8 Bayer matrix (0..63) */
static const unsigned char BAYER[8][8] = {
	{ 0, 32, 8, 40, 2, 34, 10, 42 }, { 48, 16, 56, 24, 50, 18, 58, 26 },
	{ 12, 44, 4, 36, 14, 46, 6, 38 }, { 60, 28, 52, 20, 62, 30, 54, 22 },
	{ 3, 35, 11, 43, 1, 33, 9, 41 }, { 51, 19, 59, 27, 49, 17, 57, 25 },
	{ 15, 47, 7, 39, 13, 45, 5, 37 }, { 63, 31, 55, 23, 61, 29, 53, 21 },
};

void img_dither256(unsigned char *rgba, int w, int h)
{
	int x, y, k;
	for (y = 0; y < h; y++)
		for (x = 0; x < w; x++) {
			unsigned char *p = rgba + ((size_t)y * w + x) * 4;
			unsigned t = (BAYER[y & 7][x & 7] * 255u + 32) / 64;   /* threshold 0..254 */
			for (k = 0; k < 3; k++) {
				unsigned s = p[k] * 5u, lv = s / 255, fr = s % 255;
				if (fr > t) lv++;
				p[k] = (unsigned char)(lv * 51);
			}
		}
}
