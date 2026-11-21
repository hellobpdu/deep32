/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target e26j: the strict JSON reader and writer of the Library protocol
 * (src/workstation/library/src/common/e26j.c, with the jv tree of src/workstation/library/src/app/json.c).
 * Properties checked on every input:
 *   - e26j_parse and e26j_valid accept exactly the same texts;
 *   - an accepted tree written back with the e26j writer parses again, and writing that tree gives the same text;
 *   - e26j_u64 and e26j_i64 agree on every integer they both accept;
 *   - base64 text that e26j_unb64 accepts is canonical: e26j_b64 writes it back unchanged.
 */
#include "fuzz.h"
#include "common/e26j.h"
#include <string.h>

static void emit(e26j_buf *b, const jv *v)
{
	size_t i;
	switch (v->type) {
	case J_NULL: e26j_lit(b, "null"); break;
	case J_BOOL: e26j_lit(b, v->b ? "true" : "false"); break;
	case J_NUM:
		FUZZ_ASSERT(v->str != NULL);
		e26j_raw(b, v->str, strlen(v->str));
		break;
	case J_STR:
		FUZZ_ASSERT(v->str != NULL);
		FUZZ_ASSERT(e26j_utf8(v->str, strlen(v->str)));
		e26j_cstr(b, v->str);
		break;
	case J_ARR:
		e26j_lit(b, "[");
		for (i = 0; i < v->n; i++) {
			if (i) e26j_lit(b, ",");
			emit(b, v->items[i]);
		}
		e26j_lit(b, "]");
		break;
	case J_OBJ:
		e26j_lit(b, "{");
		for (i = 0; i < v->n; i++) {
			if (i) e26j_lit(b, ",");
			e26j_cstr(b, v->keys[i]);
			e26j_lit(b, ":");
			emit(b, v->items[i]);
		}
		e26j_lit(b, "}");
		break;
	default: FUZZ_ASSERT(!"unknown jv type");
	}
}

static void integers(const jv *v)
{
	size_t i;
	uint64_t u;
	int64_t s;
	if (v->type == J_NUM) {
		int uok = e26j_u64(v, &u) == 0, sok = e26j_i64(v, INT64_MIN, INT64_MAX, &s) == 0;
		if (uok && u <= (uint64_t)INT64_MAX) FUZZ_ASSERT(sok && s == (int64_t)u);
		if (sok && s >= 0) FUZZ_ASSERT(uok && u == (uint64_t)s);
	}
	if (v->type == J_ARR || v->type == J_OBJ)
		for (i = 0; i < v->n; i++) integers(v->items[i]);
}

static char *write_tree(const jv *v)
{
	e26j_buf b;
	char *t;
	e26j_init(&b);
	emit(&b, v);
	t = e26j_take(&b);
	FUZZ_ASSERT(t != NULL);
	return t;
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	const char *text = (const char *)data;
	char err[160];
	jv *v = e26j_parse(text, size, err, sizeof err);
	int valid = e26j_valid(text, size, NULL, 0);

	FUZZ_ASSERT((v != NULL) == (valid == 0));
	(void)e26j_utf8(text, size);
	if (v) {
		char *t1 = write_tree(v), *t2;
		jv *again;
		integers(v);
		again = e26j_parse(t1, strlen(t1), err, sizeof err);
		FUZZ_ASSERT(again != NULL);
		t2 = write_tree(again);
		FUZZ_ASSERT(strcmp(t1, t2) == 0);
		json_free(again);
		free(t1);
		free(t2);
		json_free(v);
	}
	{
		size_t n = 0;
		unsigned char *raw = e26j_unb64(text, size, &n);
		if (raw) {
			e26j_buf b;
			char *t;
			FUZZ_ASSERT(n <= size / 4 * 3);
			e26j_init(&b);
			e26j_b64(&b, raw, n);
			t = e26j_take(&b);
			FUZZ_ASSERT(t != NULL);
			FUZZ_ASSERT(strlen(t) == size + 2 && memcmp(t + 1, text, size) == 0);
			free(t);
			free(raw);
		}
	}
	return 0;
}
