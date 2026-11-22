/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target wav: the Library importer's RIFF WAVE reader (src/workstation/library/src/import/wav.c): PCM 8 and
 * 16 bit, WAVE ADPCM and IMA ADPCM, decoded to 16-bit PCM. Every decoded sample is read, then the result is written
 * with wav_write_pcm16 and decoded again: the second decode must give the same samples, channels and rate.
 *
 * Findings F-2, F-3 and F-5 (README.md) are fixed in the decoder: an 8-bit sample is scaled without a shift of a
 * negative value, a WAVE ADPCM file with fewer than two samples per block is refused, and the WAVE ADPCM step size is
 * bounded. The harness takes every input; corpus/wav/seed-finding-F2, -F3 and -F5 are now regression inputs.
 */
#define _GNU_SOURCE
#include "fuzz.h"
#include "memfile.h"
#include "import/wav.h"
#include "common/le.h"
#include <string.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	wav_pcm w, back;
	char err[160];
	memfile m;
	unsigned char *bytes;
	size_t n;
	long k;
	unsigned long sum = 0;

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
