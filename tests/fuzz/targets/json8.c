/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target json8: the strict UTF-8 JSON reader of the Library's render helper
 * (src/workstation/library/src/common/json8.c, building src/workstation/library/src/app/json.c's jv tree). An
 * accepted tree is walked: every string is read, and since duplicate keys are refused, json_get must find each
 * member of an object under its own key.
 */
#include "fuzz.h"
#include "common/json8.h"
#include <string.h>

/* level: the nesting level a container at v has (the root container is level 1; 64 is the deepest allowed) */
static void walk(const jv *v, int level)
{
	size_t i;
	switch (v->type) {
	case J_NULL: case J_BOOL: case J_NUM: break;
	case J_STR: FUZZ_ASSERT(v->str != NULL); (void)strlen(v->str); break;
	case J_ARR:
		FUZZ_ASSERT(level <= 64);
		for (i = 0; i < v->n; i++) walk(v->items[i], level + 1);
		break;
	case J_OBJ:
		FUZZ_ASSERT(level <= 64);
		for (i = 0; i < v->n; i++) {
			FUZZ_ASSERT(json_get(v, v->keys[i]) == v->items[i]);
			walk(v->items[i], level + 1);
		}
		break;
	default: FUZZ_ASSERT(!"unknown jv type");
	}
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	char err[160];
	jv *v = json8_parse((const char *)data, size, err, sizeof err);
	if (!v) {
		FUZZ_ASSERT(memchr(err, 0, sizeof err) != NULL);
		return 0;
	}
	walk(v, 1);
	json_free(v);
	return 0;
}
