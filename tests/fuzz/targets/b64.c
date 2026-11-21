/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target b64: the Library protocol's canonical base64 (src/workstation/library/src/common/b64.c). Text the
 * decoder accepts must encode back to exactly the same text (canonical form), a too-small output buffer must be
 * refused, and any bytes must survive encode then decode.
 */
#include "fuzz.h"
#include "common/b64.h"
#include <string.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	const char *text = (const char *)data;
	size_t cap = size / 4 * 3;
	unsigned char *out = (unsigned char *)malloc(cap ? cap : 1);
	char *enc;
	long n;

	if (!out) abort();
	n = b64_decode(text, size, out, cap);
	if (n >= 0) {
		FUZZ_ASSERT((size_t)n <= cap);
		enc = (char *)malloc(b64_len((size_t)n) + 1);
		if (!enc) abort();
		FUZZ_ASSERT(b64_encode(out, (size_t)n, enc) == size);
		FUZZ_ASSERT(memcmp(enc, text, size) == 0);
		free(enc);
		/* one byte too few room: refused */
		if (n > 0) FUZZ_ASSERT(b64_decode(text, size, out, (size_t)n - 1) == -1);
	}
	free(out);

	/* the raw bytes, encoded and decoded */
	enc = (char *)malloc(b64_len(size) + 1);
	out = (unsigned char *)malloc(size ? size : 1);
	if (!enc || !out) abort();
	FUZZ_ASSERT(b64_encode(data, size, enc) == b64_len(size));
	FUZZ_ASSERT(enc[b64_len(size)] == 0);
	FUZZ_ASSERT(b64_decode(enc, b64_len(size), out, size) == (long)size);
	FUZZ_ASSERT(size == 0 || memcmp(out, data, size) == 0);
	free(enc);
	free(out);
	return 0;
}
