/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target rfctext: the RFC plain-text classifier (src/workstation/library/src/common/rfctext.c): page
 * furniture removal, paragraph joining across page breaks, headings, definition terms and figures. The input is
 * split into lines the way a reader hands them over (each keeps its newline; a NUL byte ends its line's C string).
 * Every block it returns must have its text, a known kind and a NUL-terminated heading number.
 *
 * Finding F-4 (README.md): when a page break (a form feed, or a removed page footer or header) falls directly before
 * a line that is not blank, rfctext_classify never returns. By default the harness skips exactly those inputs, found
 * with a copy of the classifier's first pass below, so that fuzzing goes on in the rest of it; built with
 * FUZZ_STRICT=1 it does not, and corpus/rfctext/seed-finding-F4 reproduces the hang.
 */
#include "fuzz.h"
#include "common/rfctext.h"
#include <ctype.h>
#include <string.h>

#ifndef FUZZ_STRICT
/* the classifier's first pass (rfctext.c: rstrip, is_footer, is_header and the pending page break), on copies */
static int blank(const char *s) { while (*s) { if (!isspace((unsigned char)*s)) return 0; s++; } return 1; }
static int indent(const char *s) { int i = 0; while (s[i] == ' ') i++; return i; }

static void rstrip(char *s)
{
	size_t n = strlen(s);
	while (n && (s[n - 1] == '\n' || s[n - 1] == '\r' || s[n - 1] == ' ' || s[n - 1] == '\t' || s[n - 1] == '\f')) s[--n] = 0;
}

static int is_footer(const char *s)
{
	size_t n = strlen(s);
	const char *p;
	if (n < 8 || s[n - 1] != ']' || indent(s) > 0) return 0;
	p = strstr(s, "[Page ");
	return p && p > s && strspn(p + 6, "0123456789") == (size_t)(n - 1 - (size_t)(p + 6 - s));
}

static int is_header(const char *s)
{
	size_t n = strlen(s);
	return !strncmp(s, "RFC ", 4) && isdigit((unsigned char)s[4]) && n > 10 &&
	       isdigit((unsigned char)s[n - 1]) && isdigit((unsigned char)s[n - 4]) && strstr(s, "   ");
}

/* 1 when a line kept with a page break before it is not blank: rfctext_classify would loop on it (F-4) */
static int finding_f4(char **lines, int n)
{
	int i, pending = 0, hang = 0;
	for (i = 0; i < n && !hang; i++) {
		char *line = strdup(lines[i]);
		if (!line) abort();
		if (strchr(line, '\f')) pending = 1;
		rstrip(line);
		if (is_footer(line) || is_header(line)) pending = 1;
		else {
			if (pending && !blank(line)) hang = 1;
			pending = 0;
		}
		free(line);
	}
	return hang;
}
#endif

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
#ifndef FUZZ_STRICT
	if (finding_f4(lines, n)) {
		for (i = 0; i < n; i++) free(lines[i]);
		free(lines);
		return 0;
	}
#endif
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
