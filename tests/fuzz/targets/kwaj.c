/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target kwaj: the Library importer's KWAJ decompressor (src/workstation/library/src/import/kwaj.c): stored,
 * XOR, LZSS, the five-table Huffman method and MSZIP with its own DEFLATE reader. Compressed files come from disc
 * images, so every byte is untrusted. The output must stay inside the documented 256 MiB bound, and on an error the
 * decoder must hand back no buffer.
 */
#include "fuzz.h"
#include "import/kwaj.h"
#include <string.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	unsigned char *out = (unsigned char *)1;
	size_t outlen = 12345;
	int rc = kwaj_decode(data, size, &out, &outlen);
	FUZZ_ASSERT(rc >= 0 && rc <= 4);
	if (rc != 0) {
		FUZZ_ASSERT(out == NULL && outlen == 0);
		return 0;
	}
	FUZZ_ASSERT(outlen <= (size_t)256 * 1024 * 1024);
	FUZZ_ASSERT(outlen == 0 || out != NULL);
	if (outlen) {
		/* touch the first and last byte: the buffer must really hold outlen bytes */
		volatile unsigned char a = out[0], b = out[outlen - 1];
		(void)a; (void)b;
	}
	kwaj_free(out);
	return 0;
}
