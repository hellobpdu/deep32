/* scale.c: the arithmetic of maximise as integer scaling (scale.h, update 1) and of SHARP mode (W1-SHARP). */
#include "scale.h"
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int floordiv(int a, int b) /* b > 0 */
{
	return a >= 0 ? a / b : -((-a + b - 1) / b);
}

int scale_fit(e26_scale *s, int win_w, int win_h)
{
	e26_scale n;
	int kx = win_w / E26_FRAME_W, ky = win_h / E26_FRAME_H;
	n.w = win_w;
	n.h = win_h;
	n.k = kx < ky ? kx : ky;
	if (n.k < 1) n.k = 1;
	n.ox = (win_w - n.k * E26_FRAME_W) / 2;
	n.oy = (win_h - n.k * E26_FRAME_H) / 2;
	if (n.ox < 0) n.ox = 0; /* a window smaller than the frame shows its top-left, as before */
	if (n.oy < 0) n.oy = 0;
	if (s->k == n.k && s->ox == n.ox && s->oy == n.oy && s->w == n.w && s->h == n.h) return 0;
	*s = n;
	return 1;
}

void scale_to_frame(const e26_scale *s, int wx, int wy, int *fx, int *fy)
{
	int k = s->k > 0 ? s->k : 1;
	*fx = floordiv(wx - s->ox, k);
	*fy = floordiv(wy - s->oy, k);
}

void scale_to_window(const e26_scale *s, int fx, int fy, int *wx, int *wy)
{
	int k = s->k > 0 ? s->k : 1;
	*wx = s->ox + fx * k;
	*wy = s->oy + fy * k;
}

int scale_is_identity(const e26_scale *s)
{
	return s->k <= 1 && s->ox == 0 && s->oy == 0 && s->w <= E26_FRAME_W && s->h <= E26_FRAME_H;
}

int scale_margins(const e26_scale *s, int r[4][4])
{
	int n = 0, fw = s->k * E26_FRAME_W, fh = s->k * E26_FRAME_H;
	int right = s->ox + fw, bottom = s->oy + fh;
	if (s->oy > 0) { r[n][0] = 0; r[n][1] = 0; r[n][2] = s->w; r[n][3] = s->oy; n++; }                 /* above */
	if (bottom < s->h) { r[n][0] = 0; r[n][1] = bottom; r[n][2] = s->w; r[n][3] = s->h - bottom; n++; } /* below */
	if (s->ox > 0) { r[n][0] = 0; r[n][1] = s->oy; r[n][2] = s->ox; r[n][3] = fh; n++; }                /* left */
	if (right < s->w) { r[n][0] = right; r[n][1] = s->oy; r[n][2] = s->w - right; r[n][3] = fh; n++; }  /* right */
	return n;
}

void scale_pixels(const unsigned char *src, size_t src_stride, unsigned char *dst, size_t dst_stride,
                  int w, int h, int bpp, int k)
{
	int x, y, j;
	size_t row = (size_t)w * (size_t)k * (size_t)bpp;
	for (y = 0; y < h; y++) {
		const unsigned char *s = src + (size_t)y * src_stride;
		unsigned char *d = dst + (size_t)y * (size_t)k * dst_stride, *o = d;
		if (bpp == 4) {
			uint32_t v;
			for (x = 0; x < w; x++) {
				memcpy(&v, s + (size_t)x * 4, 4);
				for (j = 0; j < k; j++, o += 4) memcpy(o, &v, 4);
			}
		} else {
			for (x = 0; x < w; x++)
				for (j = 0; j < k; j++, o += bpp) memcpy(o, s + (size_t)x * (size_t)bpp, (size_t)bpp);
		}
		for (j = 1; j < k; j++) memcpy(d + (size_t)j * dst_stride, d, row);
	}
}

/* ---- SHARP mode (W1-SHARP): glyph cells, k-times font names, smooth resampling */

int scale_glyph_x(int k, int pen1, const scale_glyph *g1, const scale_glyph *gk)
{
	if (g1->rb > g1->lb && gk->rb > gk->lb) /* ink on both: the k ink's centre on k x the 1x ink's centre */
		return k * pen1 + floordiv(k * (g1->lb + g1->rb) - (gk->lb + gk->rb), 2);
	return k * pen1 + floordiv(k * g1->w - gk->w, 2); /* no ink: centred in the logical cell */
}

int scale_xlfd_k(const char *name, int k, char *out, size_t n, int *px)
{
	const char *f[15];
	int nf = 0, i, p = 0;
	const char *s;
	if (!name || name[0] != '-' || k < 1) return -1;
	for (s = name; *s; s++)
		if (*s == '-') {
			if (nf == 14) return -1;
			f[nf++] = s + 1; /* f[i]: the start of field i (0 = foundry) */
		}
	if (nf != 14) return -1;
	for (s = f[6]; *s != '-'; s++) {
		if (*s < '0' || *s > '9') return -1;
		p = p * 10 + (*s - '0');
		if (p > 10000) return -1;
	}
	if (p <= 0) return -1;
	if (px) *px = p;
	/* fields 0-5 as they are, the pixel size times k, "*" for the point size, the two resolutions and the spacing
	 * as they are, "*" for the average width, then the registry and the encoding */
	i = snprintf(out, n, "%.*s%d-*-%.*s-*-%s", (int)(f[6] - name), name, p * k, (int)(f[11] - f[8] - 1), f[8], f[12]);
	return i < 0 || (size_t)i >= n ? -1 : 0;
}

/* one axis of a resampling: for each output position its first source index, its number of taps and their weights
 * (normalised to 1) */
typedef struct {
	int *start, *count;
	float *w;
	int taps;
} axis_w;

static double sinc(double x)
{
	const double pi = 3.14159265358979323846;
	if (x > -1e-9 && x < 1e-9) return 1.0;
	return sin(pi * x) / (pi * x);
}

static void axis_free(axis_w *a)
{
	free(a->start);
	free(a->count);
	free(a->w);
}

static int axis_make(axis_w *a, int s, int d)
{
	int i, t;
	double r = (double)s / d;
	a->taps = d < s ? (int)ceil(r) + 2 : 6;
	a->start = (int *)calloc((size_t)d, sizeof(int));
	a->count = (int *)calloc((size_t)d, sizeof(int));
	a->w = (float *)calloc((size_t)d * (size_t)a->taps, sizeof(float));
	if (!a->start || !a->count || !a->w) { axis_free(a); return -1; }
	for (i = 0; i < d; i++) {
		float *w = a->w + (size_t)i * (size_t)a->taps;
		double sum = 0;
		if (d < s) { /* area average: the source interval [i r, (i + 1) r), each pixel by the share it covers */
			double lo = i * r, hi = (i + 1) * r;
			int j0 = (int)floor(lo), n = 0;
			for (t = 0; t < a->taps && j0 + t < s && j0 + t < hi; t++) {
				double l = j0 + t > lo ? j0 + t : lo, h = j0 + t + 1 < hi ? j0 + t + 1 : hi;
				w[t] = (float)(h > l ? h - l : 0);
				sum += w[t];
				n = t + 1;
			}
			a->start[i] = j0;
			a->count[i] = n;
		} else if (d == s) {
			a->start[i] = i;
			a->count[i] = 1;
			w[0] = 1;
			sum = 1;
		} else { /* Lanczos-3 round the output pixel's centre mapped into the source; the edges clamp */
			double c = (i + 0.5) * r - 0.5;
			int j0 = (int)floor(c) - 2;
			for (t = 0; t < 6; t++) {
				double x = c - (j0 + t);
				w[t] = (float)(x > -3 && x < 3 ? sinc(x) * sinc(x / 3) : 0);
				sum += w[t];
			}
			a->start[i] = j0;
			a->count[i] = 6;
		}
		if (sum != 0)
			for (t = 0; t < a->count[i]; t++) w[t] = (float)(w[t] / sum);
	}
	return 0;
}

static int clampi(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }

int scale_smooth(const unsigned char *src, size_t src_stride, int sw, int sh,
                 unsigned char *dst, size_t dst_stride, int dw, int dh)
{
	axis_w ax, ay;
	float *tmp;
	int x, y, t, c;
	if (sw < 1 || sh < 1 || dw < 1 || dh < 1) return -1;
	if (sw == dw && sh == dh) {
		for (y = 0; y < sh; y++) memcpy(dst + (size_t)y * dst_stride, src + (size_t)y * src_stride, (size_t)sw * 4);
		return 0;
	}
	if (axis_make(&ax, sw, dw)) return -1;
	if (axis_make(&ay, sh, dh)) { axis_free(&ax); return -1; }
	tmp = (float *)malloc((size_t)dw * (size_t)sh * 4 * sizeof(float));
	if (!tmp) { axis_free(&ax); axis_free(&ay); return -1; }
	for (y = 0; y < sh; y++) { /* across */
		const unsigned char *row = src + (size_t)y * src_stride;
		for (x = 0; x < dw; x++) {
			const float *w = ax.w + (size_t)x * (size_t)ax.taps;
			float acc[4] = { 0, 0, 0, 0 };
			for (t = 0; t < ax.count[x]; t++) {
				const unsigned char *p = row + 4 * (size_t)clampi(ax.start[x] + t, 0, sw - 1);
				for (c = 0; c < 4; c++) acc[c] += w[t] * p[c];
			}
			for (c = 0; c < 4; c++) tmp[((size_t)y * (size_t)dw + (size_t)x) * 4 + (size_t)c] = acc[c];
		}
	}
	for (y = 0; y < dh; y++) { /* down */
		const float *w = ay.w + (size_t)y * (size_t)ay.taps;
		unsigned char *o = dst + (size_t)y * dst_stride;
		for (x = 0; x < dw; x++) {
			float acc[4] = { 0, 0, 0, 0 };
			for (t = 0; t < ay.count[y]; t++) {
				const float *p = tmp + ((size_t)clampi(ay.start[y] + t, 0, sh - 1) * (size_t)dw + (size_t)x) * 4;
				for (c = 0; c < 4; c++) acc[c] += w[t] * p[c];
			}
			for (c = 0; c < 4; c++) o[4 * (size_t)x + (size_t)c] = (unsigned char)clampi((int)floorf(acc[c] + 0.5f), 0, 255);
		}
	}
	free(tmp);
	axis_free(&ax);
	axis_free(&ay);
	return 0;
}
