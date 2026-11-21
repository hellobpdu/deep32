/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target imgscale: the Library's picture scaler (src/workstation/library/src/common/imgscale.c): area
 * averaging, nearest, bilinear and bicubic resampling, fit, fill, 1:1 and zoomed views, compositing and the
 * 256-color dither. The first 16 bytes choose the sizes, mode, filter, zoom, offsets, row padding and ground; the
 * rest are the source pixels (repeated when short). Source and destination buffers are allocated at exactly the
 * size the interface states, so a read or write outside them is reported.
 */
#include "fuzz.h"
#include "common/imgscale.h"
#include <string.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	int sw, sh, bw, bh, mode, filter, zoom, x, y, w, h, cx, cy, cw, ch, i;
	size_t pad, stride, srclen, k;
	unsigned char bg[4], *src, *dst;
	const uint8_t *px;
	size_t npx;

	if (size < 16) return 0;
	sw = 1 + data[0] % 96;
	sh = 1 + data[1] % 96;
	bw = 1 + data[2] % 160;
	bh = 1 + data[3] % 160;
	mode = data[4] % 3;
	filter = data[5] % 3;
	zoom = 1 + (data[6] | data[7] << 8) % 1600;
	x = data[8] * 3;
	y = data[9] * 3;
	pad = data[10] % 13;
	for (i = 0; i < 4; i++) bg[i] = data[11 + i];
	/* data[15] is reserved so seeds keep their layout if a parameter is added */
	px = data + 16;
	npx = size - 16;

	stride = (size_t)sw * 4 + pad;
	srclen = stride * (size_t)sh;
	src = (unsigned char *)malloc(srclen);
	dst = (unsigned char *)malloc((size_t)bw * bh * 4);
	if (!src || !dst) abort();
	for (k = 0; k < srclen; k++) src[k] = npx ? px[k % npx] : (unsigned char)k;

	/* resample with row padding, into a bw x bh destination */
	FUZZ_ASSERT(img_resample(src, stride, sw, sh, dst, bw, bh, filter) == 0);

	/* the remaining calls take tightly packed rows: repack without the padding */
	if (pad) {
		int r;
		for (r = 1; r < sh; r++) memmove(src + (size_t)r * sw * 4, src + (size_t)r * stride, (size_t)sw * 4);
	}
	img_fit_size(sw, sh, bw, bh, &w, &h);
	FUZZ_ASSERT(w >= 1 && h >= 1 && w <= bw && h <= bh);
	img_fill_crop(sw, sh, bw, bh, &cx, &cy, &cw, &ch);
	FUZZ_ASSERT(cx >= 0 && cy >= 0 && cw >= 1 && ch >= 1 && cx + cw <= sw && cy + ch <= sh);

	FUZZ_ASSERT(img_fit(src, sw, sh, dst, bw, bh, mode, filter, bg) == 0);
	(void)img_actual(src, sw, sh, dst, bw, bh, x, y, bg);
	(void)img_zoom(src, sw, sh, dst, bw, bh, zoom, x, y, filter, bg);
	img_over(dst, (size_t)bw * bh, bg);
	img_dither256(dst, bw, bh);
	free(src);
	free(dst);
	return 0;
}
