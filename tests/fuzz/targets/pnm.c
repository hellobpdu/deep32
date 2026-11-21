/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target pnm: the asset store's Netpbm reader e26_image_read_pnm (src/workstation/library/src/common/image.c),
 * P6 and P7 headers with comments. The input is handed to the reader as an in-memory file. An image it accepts is
 * written back with e26_image_write_pnm and read again: the pixels and the store's SHA-256 identity must not change.
 */
#define _GNU_SOURCE
#include "fuzz.h"
#include "memfile.h"
#include "common/image.h"
#include <string.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	e26_image im, back;
	char err[256], h1[65], h2[65];
	memfile in, out;

	memfile_open(&in, data, size);
	if (e26_image_read_pnm(in.path, &im, err, sizeof err) != 0) {
		memfile_close(&in);
		FUZZ_ASSERT(im.rgba == NULL);
		return 0;
	}
	memfile_close(&in);
	FUZZ_ASSERT(im.rgba != NULL && im.w > 0 && im.h > 0 && (long)im.w * im.h <= E26_IMAGE_MAX_PIXELS);
	e26_image_sha256(&im, h1);

	memfile_open(&out, NULL, 0);
	FUZZ_ASSERT(e26_image_write_pnm(&im, out.path) == 0);
	FUZZ_ASSERT(e26_image_read_pnm(out.path, &back, err, sizeof err) == 0);
	memfile_close(&out);
	FUZZ_ASSERT(back.w == im.w && back.h == im.h);
	FUZZ_ASSERT(memcmp(back.rgba, im.rgba, (size_t)im.w * im.h * 4) == 0);
	e26_image_sha256(&back, h2);
	FUZZ_ASSERT(strcmp(h1, h2) == 0);
	e26_image_free(&back);
	e26_image_free(&im);
	return 0;
}
