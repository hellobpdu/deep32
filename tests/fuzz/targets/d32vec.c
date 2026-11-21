/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target d32vec: the Workstation's exact dense scan (src/workstation/node/d32vec.c), built as the node builds
 * it (-ffp-contract=off). Its two implementations of one summation order, d32_dot (SSE) and d32_dot_lanes (plain
 * C), must give the same bits for every input, including infinities, subnormals and signed zeros; NaN results must
 * be NaN on both sides (the payload may differ). d32_scan and d32_scan_lanes must write exactly the rows asked for.
 * Every vector, matrix and score array is allocated at its exact size, so AddressSanitizer sees any access past an
 * end, and the tail loop (lengths that are not a multiple of 16) is reached for every length.
 *
 * Input layout: byte 0 picks the number of matrix rows (1 to 8) and byte 1 the scanned row range; the rest is read
 * as little-endian IEEE-754 single-precision values, first the matrix, then the query.
 */
#include "fuzz.h"
#include <math.h>
#include <string.h>

int d32vec_abi(void);
int d32vec_sse(void);
float d32_dot(const float *x, const float *y, size_t n);
float d32_dot_lanes(const float *x, const float *y, size_t n);
void d32_scan(const float *M, size_t dim, size_t r0, size_t r1, const float *q, float *scores);
void d32_scan_lanes(const float *M, size_t dim, size_t r0, size_t r1, const float *q, float *scores);

static void same(float a, float b)
{
	uint32_t ua, ub;
	if (isnan(a) || isnan(b)) {
		FUZZ_ASSERT(isnan(a) && isnan(b));
		return;
	}
	memcpy(&ua, &a, 4);
	memcpy(&ub, &b, 4);
	FUZZ_ASSERT(ua == ub);
}

static float *floats(const uint8_t *p, size_t n)
{
	float *f = (float *)malloc(n ? n * sizeof(float) : 1);
	size_t i;
	if (!f) abort();
	for (i = 0; i < n; i++) {
		uint32_t u = (uint32_t)p[4 * i] | (uint32_t)p[4 * i + 1] << 8 | (uint32_t)p[4 * i + 2] << 16 |
			     (uint32_t)p[4 * i + 3] << 24;
		memcpy(&f[i], &u, 4);
	}
	return f;
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	size_t rows, dim, total, r0, r1, r;
	float *M, *q, *s1, *s2, *x, *y;

	FUZZ_ASSERT(d32vec_abi() == 1);
	(void)d32vec_sse();
	if (size < 2) return 0;
	rows = 1 + data[0] % 8;
	total = (size - 2) / 4;
	dim = total / (rows + 1);
	r0 = data[1] % (rows + 1);
	r1 = r0 + (data[1] >> 4) % (rows + 1 - r0);

	/* the whole input as two vectors of equal length: every length from 0 up */
	x = floats(data + 2, total / 2);
	y = floats(data + 2 + 4 * (total / 2), total / 2);
	same(d32_dot(x, y, total / 2), d32_dot_lanes(x, y, total / 2));
	same(d32_dot(x, x, total / 2), d32_dot_lanes(x, x, total / 2));
	free(x);
	free(y);

	/* a rows x dim matrix and a dim query, scanned over [r0, r1) */
	M = floats(data + 2, rows * dim);
	q = floats(data + 2 + 4 * rows * dim, dim);
	s1 = (float *)malloc((r1 ? r1 : 1) * sizeof(float));
	s2 = (float *)malloc((r1 ? r1 : 1) * sizeof(float));
	if (!s1 || !s2) abort();
	for (r = 0; r < r1; r++) s1[r] = s2[r] = -1.0f;
	d32_scan(M, dim, r0, r1, q, s1);
	d32_scan_lanes(M, dim, r0, r1, q, s2);
	for (r = 0; r < r1; r++) {
		if (r < r0) {
			FUZZ_ASSERT(s1[r] == -1.0f && s2[r] == -1.0f);
			continue;
		}
		same(s1[r], s2[r]);
		same(s1[r], d32_dot(M + r * dim, q, dim));
	}
	free(M);
	free(q);
	free(s1);
	free(s2);
	return 0;
}
