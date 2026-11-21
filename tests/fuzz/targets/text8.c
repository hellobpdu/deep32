/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target text8: Unicode text on the Library's 8-bit faces (src/workstation/library/src/common/text8.c).
 * Input layout: byte 0 the face flags, byte 1 a small output size, then UTF-8 (or not) text. Properties: the decoder
 * consumes 1 to 4 bytes and returns a scalar that encodes back to the same bytes (or U+FFFD for one bad byte);
 * t8_map never writes more than T8_MAXOUT bytes; t8_to_8bit stays inside its output buffer, NUL-terminates it and
 * reports the length it wrote, for a roomy buffer and for a cramped one.
 */
#include "fuzz.h"
#include "common/text8.h"
#include <string.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	int flags;
	size_t small, n, i, big;
	const char *text;
	char *out;

	if (size < 2) return 0;
	flags = data[0] & 3;
	small = data[1] % 24;
	text = (const char *)data + 2;
	n = size - 2;

	for (i = 0; i < n;) {
		uint32_t cp;
		char enc[4], m[T8_MAXOUT + 8];
		int k = t8_decode(text + i, n - i, &cp), len = -1, kind, e;
		FUZZ_ASSERT(k >= 1 && k <= 4 && (size_t)k <= n - i);
		FUZZ_ASSERT(cp <= 0x10FFFF && !(cp >= 0xD800 && cp <= 0xDFFF));
		if (cp != 0xFFFD || k == 3) {
			e = t8_encode(cp, enc);
			FUZZ_ASSERT(e == k && memcmp(enc, text + i, (size_t)k) == 0);
		} else
			FUZZ_ASSERT(k == 1);
		memset(m, 0x5A, sizeof m);
		kind = t8_map(cp, flags, m, &len);
		FUZZ_ASSERT(kind == T8_LATIN1 || kind == T8_SYMBOL || kind == T8_NONE);
		FUZZ_ASSERT(len >= 0 && len <= T8_MAXOUT);
		for (e = T8_MAXOUT; e < (int)sizeof m; e++) FUZZ_ASSERT(m[e] == 0x5A);
		(void)t8_cp1252(cp);
		(void)t8_symbol(cp);
		(void)t8_translit(cp);
		(void)t8_is_word(cp);
		i += (size_t)k;
	}

	big = n * T8_MAXOUT + 1;
	out = (char *)malloc(big);
	if (!out) abort();
	FUZZ_ASSERT(t8_to_8bit(text, n, flags, out, big) == strlen(out));
	free(out);
	if (small) {
		out = (char *)malloc(small);
		if (!out) abort();
		FUZZ_ASSERT(t8_to_8bit(text, n, flags, out, small) == strlen(out));
		FUZZ_ASSERT(strlen(out) < small);
		free(out);
	}
	return 0;
}
