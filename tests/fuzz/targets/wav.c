/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target wav: the Library importer's RIFF WAVE reader (src/workstation/library/src/import/wav.c): PCM 8 and
 * 16 bit, WAVE ADPCM and IMA ADPCM, decoded to 16-bit PCM. Every decoded sample is read, then the result is written
 * with wav_write_pcm16 and decoded again: the second decode must give the same samples, channels and rate.
 *
 * Finding F-3 (README.md): a WAVE ADPCM file whose fmt chunk gives one sample per block makes wav_decode write past
 * its sample buffer. By default the harness skips exactly those inputs (format 2 with a samples-per-block field of
 * 1, found with wav_decode's own chunk walk) so that fuzzing goes on in the rest of the decoder; built with
 * FUZZ_STRICT=1 it does not, and corpus/wav/seed-finding-F3 reproduces the overflow.
 */
#define _GNU_SOURCE
#include "fuzz.h"
#include "memfile.h"
#include "import/wav.h"
#include "common/le.h"
#include <string.h>

#ifndef FUZZ_STRICT
/* 1 when wav_decode would take the WAVE ADPCM path with one sample per block (F-3); the chunk walk is wav_decode's */
static int finding_f3(const unsigned char *p, size_t n)
{
	const unsigned char *fmt = NULL;
	size_t fmtlen = 0, i = 12;
	if (n < 12) return 0;
	while (i + 8 <= n) {
		size_t len = le32(p + i + 4);
		if (len > n - i - 8) len = n - i - 8;
		if (!memcmp(p + i, "fmt ", 4)) { fmt = p + i + 8; fmtlen = len; }
		i += 8 + len + (len & 1);
	}
	return fmt && fmtlen >= 20 && le16(fmt) == 2 && le16(fmt + 18) == 1;
}
#endif

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	wav_pcm w, back;
	char err[160];
	memfile m;
	unsigned char *bytes;
	size_t n;
	long k;
	unsigned long sum = 0;

#ifndef FUZZ_STRICT
	if (finding_f3(data, size)) return 0;
#endif
	if (wav_decode(data, size, &w, err, sizeof err) != 0) {
		FUZZ_ASSERT(w.pcm == NULL);
		return 0;
	}
	FUZZ_ASSERT(w.channels == 1 || w.channels == 2);
	FUZZ_ASSERT(w.frames >= 0);
	FUZZ_ASSERT(w.frames == 0 || w.pcm != NULL);
	for (k = 0; k < w.frames * w.channels; k++) sum += (uint16_t)w.pcm[k];
	(void)sum;

	memfile_open(&m, NULL, 0);
	FUZZ_ASSERT(wav_write_pcm16(&w, m.path) == 0);
	bytes = memfile_read(&m, &n);
	memfile_close(&m);
	FUZZ_ASSERT(wav_decode(bytes, n, &back, err, sizeof err) == 0);
	FUZZ_ASSERT(back.format == 1 && back.bits == 16);
	FUZZ_ASSERT(back.channels == w.channels && back.rate == w.rate && back.frames == w.frames);
	FUZZ_ASSERT(w.frames == 0 || memcmp(back.pcm, w.pcm, (size_t)w.frames * w.channels * 2) == 0);
	wav_free(&back);
	free(bytes);
	wav_free(&w);
	return 0;
}
