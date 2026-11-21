/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target d32ww: the word-wheel reader (src/workstation/library/src/common/d32ww.c): the validation in
 * d32ww_open_mem, entry and posting access, folding and prefix search.
 *
 * Input layout: byte 0 fold flags, byte 1 pad width, byte 2 query length q, then q bytes of typed query, then the
 * wheel file. A wheel carries a CRC-32 and its own size, so the harness writes the correct values into a copy
 * before opening it; otherwise almost every mutation would stop at the checksum.
 *
 * Properties: folding never makes text longer than the reader allows for (the input plus one separator, before
 * digit padding); every entry of an opened wheel reads inside the buffer; a search returns a range inside the wheel
 * in which every key starts with the folded query, and the key before the range sorts below it.
 */
#include "fuzz.h"
#include "common/d32ww.h"
#include <string.h>

static void put32(unsigned char *p, uint32_t v)
{
	p[0] = (unsigned char)v;
	p[1] = (unsigned char)(v >> 8);
	p[2] = (unsigned char)(v >> 16);
	p[3] = (unsigned char)(v >> 24);
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	uint32_t fflags, pad;
	size_t qlen, wlen;
	const char *query;
	unsigned char *wheel;
	char *f;
	d32ww w;
	char err[160];

	if (size < 3) return 0;
	fflags = data[0];
	pad = data[1];
	qlen = data[2];
	if (size - 3 < qlen) return 0;
	query = (const char *)data + 3;

	f = d32ww_fold(query, qlen, fflags, pad, data[0] >> 7);
	FUZZ_ASSERT(f != NULL);
	if (!(fflags & D32WW_F_PAD_DIGITS) || pad == 0) FUZZ_ASSERT(strlen(f) <= qlen + 1);
	free(f);

	wlen = size - 3 - qlen;
	wheel = (unsigned char *)malloc(wlen ? wlen : 1);
	if (!wheel) abort();
	memcpy(wheel, data + 3 + qlen, wlen);
	if (wlen >= D32WW_HEADER_SIZE && memcmp(wheel, "D32WHEEL", 8) == 0) {
		put32(wheel + 16, (uint32_t)wlen);
		put32(wheel + 20, d32ww_crc32(wheel + D32WW_HEADER_SIZE, wlen - D32WW_HEADER_SIZE));
	}
	if (d32ww_open_mem(&w, wheel, wlen, 0, err, sizeof err) == 0) {
		uint32_t i, k;
		d32ww_range r;
		char *folded = NULL;
		(void)strlen(w.name); (void)strlen(w.catalogue_id); (void)strlen(w.catalogue_sha256); (void)strlen(w.built_at);
		for (i = 0; i < w.n_entries && i < 4096; i++) {
			d32ww_entry e;
			FUZZ_ASSERT(d32ww_entry_get(&w, i, &e) == 0);
			(void)strlen(e.key); (void)strlen(e.display); (void)strlen(e.label);
			for (k = 0; k < e.post_count && k < 64; k++) (void)d32ww_posting(&w, &e, k);
			FUZZ_ASSERT(d32ww_posting(&w, &e, e.post_count) == 0);
		}
		FUZZ_ASSERT(d32ww_entry_get(&w, w.n_entries, NULL) == -1);
		FUZZ_ASSERT(d32ww_find(&w, query, qlen, &r, &folded) == 0);
		FUZZ_ASSERT(folded != NULL);
		FUZZ_ASSERT(r.lo <= r.hi && r.hi <= w.n_entries && r.count == r.hi - r.lo);
		FUZZ_ASSERT(w.n_entries == 0 ? r.nearest == 0 : r.nearest < w.n_entries);
		{
			size_t fl = strlen(folded);
			uint32_t end = r.hi - r.lo > 4096 ? r.lo + 4096 : r.hi;
			for (i = r.lo; i < end; i++) {
				d32ww_entry e;
				FUZZ_ASSERT(d32ww_entry_get(&w, i, &e) == 0);
				FUZZ_ASSERT(strncmp(e.key, folded, fl) == 0);
			}
			if (r.lo > 0) {
				d32ww_entry e;
				FUZZ_ASSERT(d32ww_entry_get(&w, r.lo - 1, &e) == 0);
				FUZZ_ASSERT(strcmp(e.key, folded) < 0);
			}
		}
		free(folded);
		d32ww_close(&w);
	}
	free(wheel);
	return 0;
}
