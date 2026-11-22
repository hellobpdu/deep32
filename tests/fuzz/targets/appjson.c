/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target appjson: the Library interface's small JSON reader (src/workstation/library/src/app/json.c), used for
 * the taxonomy file, the render helper's answers and the helpers' request bodies. An accepted tree is walked and
 * every string read.
 *
 * The reader takes a pointer and a length. It used to compare the literals true, false and null with strncmp, which
 * could read up to four bytes past the length when the text is not NUL-terminated (finding F-1 in README.md, fixed:
 * the comparison now stays inside the length). The harness passes the bytes without a terminator, as the taxonomy
 * loader does, and corpus/appjson/seed-finding-F1 is now a regression input.
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
	v = json_parse((const char *)data, size, err, sizeof err);
	if (!v) return 0;
	walk(v);
	json_free(v);
	return 0;
}
