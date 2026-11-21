/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target d32json: the JSON library of the Workstation's desktop tools and the Ask client
 * (src/workstation/ship/u10/src/lib/core/d32_json.c; src/workstation/ask-client/d32src/lib/core holds an identical
 * copy). Properties: every member of an object is found again by d32_json_get under its own key; every value's
 * views lie inside the input or the document; the canonical text d32_json_write produces parses again and writes
 * to the same bytes.
 */
#include "fuzz.h"
#include "d32_json.h"
#include <string.h>

static void walk(const d32_json *doc, d32_json_id id, const uint8_t *in, size_t inlen)
{
	enum d32_json_type t = d32_json_typeof(doc, id);
	size_t len = 0, n = 0;
	const char *s;
	d32_json_id c;

	FUZZ_ASSERT(t >= D32_JSON_NULL && t <= D32_JSON_OBJECT);
	s = d32_json_raw(doc, id, &len);
	FUZZ_ASSERT(s != NULL && s >= (const char *)in && s + len <= (const char *)in + inlen);
	switch (t) {
	case D32_JSON_BOOL: FUZZ_ASSERT(d32_json_bool(doc, id) == 0 || d32_json_bool(doc, id) == 1); break;
	case D32_JSON_NUMBER:
		s = d32_json_number(doc, id, &len);
		FUZZ_ASSERT(s != NULL && len > 0 && s >= (const char *)in && s + len <= (const char *)in + inlen);
		break;
	case D32_JSON_STRING:
		s = d32_json_string(doc, id, &len);
		FUZZ_ASSERT(s != NULL && s[len] == 0);
		break;
	case D32_JSON_ARRAY:
	case D32_JSON_OBJECT:
		for (c = d32_json_first(doc, id); c; c = d32_json_next(doc, c)) {
			n++;
			if (t == D32_JSON_OBJECT) {
				size_t klen = 0;
				const char *k = d32_json_key(doc, c, &klen);
				FUZZ_ASSERT(k != NULL && k[klen] == 0);
				FUZZ_ASSERT(d32_json_get(doc, id, k, klen) == c);
			}
			walk(doc, c, in, inlen);
		}
		FUZZ_ASSERT(n == d32_json_count(doc, id));
		break;
	default: break;
	}
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	d32_json *doc = NULL, *again = NULL;
	size_t off = 0, l1 = 0, l2 = 0;
	char *t1 = NULL, *t2 = NULL;

	if (d32_json_parse(data, size, &doc, &off) != D32E_OK) {
		FUZZ_ASSERT(doc == NULL && off <= size);
		return 0;
	}
	FUZZ_ASSERT(doc != NULL && d32_json_root(doc) != 0);
	walk(doc, d32_json_root(doc), data, size);
	FUZZ_ASSERT(d32_json_write(doc, d32_json_root(doc), &t1, &l1) == D32E_OK);
	FUZZ_ASSERT(t1 != NULL && t1[l1] == 0);
	FUZZ_ASSERT(d32_json_parse(t1, l1, &again, NULL) == D32E_OK);
	FUZZ_ASSERT(d32_json_write(again, d32_json_root(again), &t2, &l2) == D32E_OK);
	FUZZ_ASSERT(l1 == l2 && memcmp(t1, t2, l1) == 0);
	d32_json_free(again);
	d32_json_free(doc);
	free(t1);
	free(t2);
	return 0;
}
