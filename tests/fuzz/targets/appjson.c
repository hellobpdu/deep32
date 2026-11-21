/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target appjson: the Library interface's small JSON reader (src/workstation/library/src/app/json.c), used for
 * the taxonomy file, the render helper's answers and the helpers' request bodies. An accepted tree is walked and
 * every string read.
 *
 * The reader takes a pointer and a length, but it compares the literals true, false and null with strncmp, which
 * can read up to four bytes past the length when the text is not NUL-terminated (finding F-1 in README.md). The
 * helpers' and the render client's callers pass NUL-terminated bodies, so by default the harness does the same and
 * keeps fuzzing the rest of the reader. Built with FUZZ_STRICT=1 (build.sh), it passes the bytes without a
 * terminator, as the taxonomy loader does, and the finding's input in corpus/appjson reproduces the over-read.
 */
#include "fuzz.h"
#include "app/json.h"
#include <string.h>

static void walk(const jv *v)
{
	size_t i;
	switch (v->type) {
	case J_NULL: case J_BOOL: case J_NUM: break;
	case J_STR: FUZZ_ASSERT(v->str != NULL); (void)strlen(v->str); break;
	case J_ARR:
	case J_OBJ:
		for (i = 0; i < v->n; i++) {
			if (v->type == J_OBJ) {
				FUZZ_ASSERT(v->keys[i] != NULL);
				FUZZ_ASSERT(json_get(v, v->keys[i]) != NULL);
				(void)json_str(v, v->keys[i]);
			}
			walk(v->items[i]);
		}
		break;
	default: FUZZ_ASSERT(!"unknown jv type");
	}
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	char err[160];
	jv *v;
#ifdef FUZZ_STRICT
	v = json_parse((const char *)data, size, err, sizeof err);
#else
	char *text = fuzz_cstr(data, size);
	v = json_parse(text, size, err, sizeof err);
	free(text);
#endif
	if (!v) return 0;
	walk(v);
	json_free(v);
	return 0;
}
