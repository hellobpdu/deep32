/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target e26p: the Library protocol's frame layer (src/workstation/library/src/common/e26p.c). The input is
 * a byte stream on a descriptor, read frame by frame with the helpers' blocking reader e26p_read until it reports
 * end of file or a bad frame. Each frame it accepts is written again with e26p_write and must come out byte for
 * byte as it went in; every 24-byte header e26p_unpack accepts must pack back to the same bytes.
 */
#define _GNU_SOURCE
#include "fuzz.h"
#include "memfile.h"
#include "common/e26p.h"
#include <string.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	memfile in;
	size_t off = 0;
	int frames = 0;

	if (size >= E26P_HEADER) {
		e26p_header h;
		unsigned char back[E26P_HEADER];
		if (e26p_unpack(data, &h) == 0) {
			e26p_pack(&h, back);
			FUZZ_ASSERT(memcmp(back, data, E26P_HEADER) == 0);
		}
	}

	memfile_open(&in, data, size);
	for (;;) {
		e26p_header h;
		char *body = (char *)1;
		int r = e26p_read(in.fd, &h, &body);
		if (r <= 0) {
			FUZZ_ASSERT(r == 0 || r == -1);
			FUZZ_ASSERT(body == NULL);
			break;
		}
		FUZZ_ASSERT(r == 1 && body != NULL);
		FUZZ_ASSERT(h.length >= 1 && h.length <= E26P_MAX_BODY && h.id != 0);
		FUZZ_ASSERT(body[h.length] == 0);
		FUZZ_ASSERT(off + E26P_HEADER + h.length <= size);
		{
			memfile out;
			unsigned char *bytes;
			size_t n;
			memfile_open(&out, NULL, 0);
			FUZZ_ASSERT(e26p_write(out.fd, h.id, h.kind, body, h.length) == 0);
			bytes = memfile_read(&out, &n);
			memfile_close(&out);
			FUZZ_ASSERT(n == E26P_HEADER + h.length);
			FUZZ_ASSERT(memcmp(bytes, data + off, n) == 0);
			free(bytes);
		}
		off += E26P_HEADER + h.length;
		free(body);
		if (++frames > 1000) break;
	}
	memfile_close(&in);
	return 0;
}
