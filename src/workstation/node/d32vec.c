/*
 * d32vec: the exact dense scan behind Deep32's exact_local (PYTHON-ON-OPENBSD 6), replacing Qdrant on the node.
 *
 * Scores are Qdrant 1.19.1's cosine scores as production computes them on deep32-engine (Ivy Bridge: AVX without FMA,
 * so Qdrant takes dot_similarity_sse, CORE-PORT 7.1):
 *   - four 128-bit accumulators over 16 floats per step, acc_k = (x * y) + acc_k: a multiply, then an add, never
 *     a fused multiply-add;
 *   - then hsum(acc1) + hsum(acc2) + hsum(acc3) + hsum(acc4), left to right, with
 *     hsum(x) = (x0 + x2) + (x1 + x3)   (_mm_add_ps(x, movehl(x)), then _mm_add_ss with shuffle 0x55);
 *   - then a scalar tail r += x[i] * y[i] (none for 1024).
 * IEEE-754 single precision add and multiply are deterministic, so the same operations in the same order give the
 * same bits. Stored rows were normalised by Qdrant at upsert; the query is normalised by the caller
 * (exact_local.qdrant_cosine_preprocess, which uses d32_dot for the squared length as Qdrant does).
 *
 * Two implementations of the one order, both always built:
 *   d32_dot       SSE intrinsics (x86-64 baseline: SSE2 is part of amd64, so no -march is needed);
 *   d32_dot_lanes plain C99 on 16 scalar lanes; the portable fallback and an independent cross-check.
 *
 * Build (OpenBSD base clang; no -march, no -ffast-math; -ffp-contract=off keeps the compiler from fusing x*y+acc):
 *   cc -std=c99 -O2 -ffp-contract=off -fPIC -shared -Wall -Wextra -Werror -o libd32vec.so d32vec.c
 *
 * Threads: every function is pure (no globals written), so callers run row ranges of one scan on several threads.
 * ctypes releases the GIL around each call.
 */
#include <stddef.h>
#include <stdint.h>

#if defined(__x86_64__) || defined(__amd64__) || defined(__SSE2__)
#include <xmmintrin.h>
#define D32_HAVE_SSE 1
#else
#define D32_HAVE_SSE 0
#endif

#define D32VEC_ABI 1

int
d32vec_abi(void)
{
	return D32VEC_ABI;
}

/* 1 when d32_dot runs the SSE intrinsics, 0 when it falls back to d32_dot_lanes */
int
d32vec_sse(void)
{
	return D32_HAVE_SSE;
}

/* Qdrant's order on 16 scalar lanes: lane l of accumulator k holds sum over steps s of x[16s+4k+l] * y[16s+4k+l] */
float
d32_dot_lanes(const float *x, const float *y, size_t n)
{
	float acc[4][4] = {{0.0f}};
	float h[4], r;
	size_t m = n - (n % 16), i, k, l;

	for (i = 0; i < m; i += 16)
		for (k = 0; k < 4; k++)
			for (l = 0; l < 4; l++) {
				float p = x[i + 4 * k + l] * y[i + 4 * k + l];
				acc[k][l] = p + acc[k][l];
			}
	for (k = 0; k < 4; k++)
		h[k] = (acc[k][0] + acc[k][2]) + (acc[k][1] + acc[k][3]);
	r = ((h[0] + h[1]) + h[2]) + h[3];
	for (i = m; i < n; i++) {
		float p = x[i] * y[i];
		r = r + p;
	}
	return r;
}

#if D32_HAVE_SSE
static inline float
hsum128(__m128 x)
{
	__m128 x64 = _mm_add_ps(x, _mm_movehl_ps(x, x));
	__m128 x32 = _mm_add_ss(x64, _mm_shuffle_ps(x64, x64, 0x55));
	return _mm_cvtss_f32(x32);
}
#endif

float
d32_dot(const float *x, const float *y, size_t n)
{
#if D32_HAVE_SSE
	size_t m = n - (n % 16), i;
	__m128 s1 = _mm_setzero_ps(), s2 = _mm_setzero_ps();
	__m128 s3 = _mm_setzero_ps(), s4 = _mm_setzero_ps();
	float r;

	for (i = 0; i < m; i += 16) {
		s1 = _mm_add_ps(_mm_mul_ps(_mm_loadu_ps(x + i), _mm_loadu_ps(y + i)), s1);
		s2 = _mm_add_ps(_mm_mul_ps(_mm_loadu_ps(x + i + 4), _mm_loadu_ps(y + i + 4)), s2);
		s3 = _mm_add_ps(_mm_mul_ps(_mm_loadu_ps(x + i + 8), _mm_loadu_ps(y + i + 8)), s3);
		s4 = _mm_add_ps(_mm_mul_ps(_mm_loadu_ps(x + i + 12), _mm_loadu_ps(y + i + 12)), s4);
	}
	r = hsum128(s1) + hsum128(s2) + hsum128(s3) + hsum128(s4);
	for (; i < n; i++) {
		float p = x[i] * y[i];
		r = r + p;
	}
	return r;
#else
	return d32_dot_lanes(x, y, n);
#endif
}

/* scores[r] = d32_dot(M + r * dim, q, dim) for r in [r0, r1): one row range of a row-major N x dim matrix.
 * The index is the absolute row, so threads share one scores array without offsets. */
void
d32_scan(const float *M, size_t dim, size_t r0, size_t r1, const float *q, float *scores)
{
	size_t r;

	for (r = r0; r < r1; r++)
		scores[r] = d32_dot(M + r * dim, q, dim);
}

/* the same scan through d32_dot_lanes (cross-check only; never on the serving path) */
void
d32_scan_lanes(const float *M, size_t dim, size_t r0, size_t r1, const float *q, float *scores)
{
	size_t r;

	for (r = r0; r < r1; r++)
		scores[r] = d32_dot_lanes(M + r * dim, q, dim);
}
