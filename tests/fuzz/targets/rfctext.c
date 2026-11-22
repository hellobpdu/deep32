/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target rfctext: the RFC plain-text classifier (src/workstation/library/src/common/rfctext.c): page
 * furniture removal, paragraph joining across page breaks, headings, definition terms and figures. The input is
 * split into lines the way a reader hands them over (each keeps its newline; a NUL byte ends its line's C string).
 * Every block it returns must have its text, a known kind and a NUL-terminated heading number.
 *
 * Finding F-4 (README.md) is fixed: a page break (a form feed, or a removed page footer or header) directly before
 * a line that is not blank no longer makes rfctext_classify loop. The harness takes every input;
 * corpus/rfctext/seed-finding-F4 is now a regression input.
 */
#include "fuzz.h"
#include "common/rfctext.h"
#include <string.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	char **lines;
	int n = 0, i;
	size_t start = 0, k, cap = 1;
	rfc_doc doc;

	for (k = 0; k < size; k++) if (data[k] == '\n') cap++;
	lines = (char **)malloc(cap * sizeof *lines);
	if (!lines) abort();
	for (k = 0; k <= size; k++) {
		if (k == size || data[k] == '\n') {
			size_t end = k < size ? k + 1 : k;
			if (end > start) {
				lines[n] = fuzz_cstr(data + start, end - start);
				n++;
			}
			start = end;
		}
	}
	memset(&doc, 0, sizeof doc);
	if (rfctext_classify(lines, n, &doc) == 0) {
		FUZZ_ASSERT(doc.nb >= 0);
		FUZZ_ASSERT(doc.nb == 0 || doc.b != NULL);
		for (i = 0; i < doc.nb; i++) {
			const rfc_block *b = &doc.b[i];
			FUZZ_ASSERT(b->kind == RFC_HEADING || b->kind == RFC_PARA || b->kind == RFC_TERM || b->kind == RFC_PRE);
			FUZZ_ASSERT(b->text != NULL);
			(void)strlen(b->text);
			FUZZ_ASSERT(memchr(b->number, 0, sizeof b->number) != NULL);
		}
		rfctext_free(&doc);
	}
	for (i = 0; i < n; i++) free(lines[i]);
	free(lines);
	return 0;
}
