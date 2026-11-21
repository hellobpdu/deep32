/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * fuzz.h: what every harness in tests/fuzz/targets shares.
 *
 * FUZZ_ASSERT is an assertion that stays on in every build (it does not depend on NDEBUG): a failed check prints
 * the condition and aborts, and libFuzzer, AddressSanitizer and the replay driver all report the abort as a crash
 * with the input that caused it. The harnesses use it for the properties a parser promises: a value it accepts
 * survives a round trip through its writer, two implementations of one computation agree, a length stays inside
 * its bound.
 */
#ifndef D32_FUZZ_H
#define D32_FUZZ_H
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define FUZZ_ASSERT(c)                                                                                          \
	do {                                                                                                    \
		if (!(c)) {                                                                                     \
			fprintf(stderr, "FUZZ_ASSERT failed: %s (%s:%d)\n", #c, __FILE__, __LINE__);            \
			abort();                                                                                \
		}                                                                                               \
	} while (0)

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size);

/* A copy of the input with a terminating NUL, for parsers that take C strings; the caller frees it. */
static inline char *fuzz_cstr(const uint8_t *data, size_t size)
{
	char *s = (char *)malloc(size + 1);
	size_t i;
	if (!s) abort();
	for (i = 0; i < size; i++) s[i] = (char)data[i];
	s[size] = 0;
	return s;
}

#endif
