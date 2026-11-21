/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target dib: the Library importer's Windows DIB decoder (src/workstation/library/src/import/dib.c), which
 * reads bitmaps and icons from disc images. Each input is decoded as a bitmap, as an icon (XOR and AND masks) and
 * through the 256-color system palette. A decoded image must have the size the decoder reported, and for images up
 * to a million pixels every pixel is read once (the SHA-256 of the buffer), so AddressSanitizer sees a buffer that
 * is shorter than it claims. Larger images (the decoder allows 40 million pixels) skip that pass to keep the fuzzer
 * fast; the decoder itself still writes every pixel under AddressSanitizer.
 */
#include "fuzz.h"
#include "import/dib.h"
#include "common/image.h"
#include <string.h>

static void check(const unsigned char *p, size_t n, int icon, int sys)
{
	e26_image im;
	dib_info info;
	char err[160];
	char hex[65];
	int rc = sys ? dib_decode_sys(p, n, icon, &im, &info, err, sizeof err)
		     : dib_decode(p, n, icon, &im, &info, err, sizeof err);
	if (rc != 0) {
		FUZZ_ASSERT(im.rgba == NULL);
		FUZZ_ASSERT(memchr(err, 0, sizeof err) != NULL);
		return;
	}
	FUZZ_ASSERT(im.rgba != NULL);
	FUZZ_ASSERT(im.w == info.width && im.h == info.height);
	FUZZ_ASSERT(im.w > 0 && im.h > 0 && (long)im.w * im.h <= E26_IMAGE_MAX_PIXELS);
	FUZZ_ASSERT(info.undefined_pixels >= 0 && info.undefined_pixels <= (long)im.w * im.h);
	if ((long)im.w * im.h <= (1L << 20)) e26_image_sha256(&im, hex);
	(void)e26_image_opaque(&im);
	e26_image_free(&im);
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	check(data, size, 0, 0);
	check(data, size, 1, 0);
	check(data, size, 0, 1);
	return 0;
}
