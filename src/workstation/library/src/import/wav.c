/* wav.c: RIFF WAVE to 16-bit PCM. WAVE ADPCM format 0x0002: each block starts with a predictor index, an initial delta
 * and two samples per channel; then 4-bit codes, high nibble first; the adaptation table and the seven
 * standard coefficient pairs are those of the format's published description (the fmt chunk's own pairs are
 * used when present). IMA ADPCM: the standard step and index tables. */
#include "wav.h"
#include "../common/le.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define FAIL(...) do { snprintf(err, errlen, __VA_ARGS__); goto fail; } while (0)
#define MAX_FRAMES (60L * 60 * 48000)

static long asr8(long v) { return v >= 0 ? v / 256 : -((-v + 255) / 256); } /* floor(v / 256), as >> 8 */
static int16_t clamp16(long v) { return (int16_t)(v < -32768 ? -32768 : v > 32767 ? 32767 : v); }

static const int ms_adapt[16] = { 230, 230, 230, 230, 307, 409, 512, 614, 768, 614, 512, 409, 307, 230, 230, 230 };
static const int ms_coef_std[7][2] = { { 256, 0 }, { 512, -256 }, { 0, 0 }, { 192, 64 }, { 240, 0 }, { 460, -208 }, { 392, -232 } };
static const int ima_index[16] = { -1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8 };
static const int ima_step[89] = { 7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55,
	60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544,
	598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660,
	4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350,
	22385, 24623, 27086, 29794, 32767 };

int wav_decode(const unsigned char *p, size_t n, wav_pcm *out, char *err, size_t errlen)
{
	const unsigned char *fmt = NULL, *data = NULL;
	size_t fmtlen = 0, datalen = 0, i = 12;
	int align = 0, coef[32][2], ncoef = 7, spb = 0, ch;
	long cap, k;
	memset(out, 0, sizeof(*out));
	if (n < 12 || memcmp(p, "RIFF", 4) || memcmp(p + 8, "WAVE", 4)) FAIL("not RIFF WAVE");
	while (i + 8 <= n) {
		size_t len = le32(p + i + 4);
		if (len > n - i - 8) len = n - i - 8;
		if (!memcmp(p + i, "fmt ", 4)) { fmt = p + i + 8; fmtlen = len; }
		else if (!memcmp(p + i, "data", 4)) { data = p + i + 8; datalen = len; }
		i += 8 + len + (len & 1);
	}
	if (!fmt || fmtlen < 16 || !data) FAIL("missing fmt or data chunk");
	out->format = le16(fmt);
	out->channels = le16(fmt + 2);
	out->rate = (long)le32(fmt + 4);
	align = le16(fmt + 12);
	out->bits = le16(fmt + 14);
	ch = out->channels;
	if (ch < 1 || ch > 2 || out->rate < 1000 || out->rate > 192000) FAIL("implausible channels/rate");
	memcpy(coef, ms_coef_std, sizeof(ms_coef_std));
	if (out->format == 1) {
		int bps = out->bits / 8;
		if (out->bits != 8 && out->bits != 16) FAIL("PCM %d bits", out->bits);
		out->frames = (long)(datalen / (size_t)(bps * ch));
		if (out->frames > MAX_FRAMES) FAIL("too long");
		out->pcm = (int16_t *)malloc((size_t)out->frames * ch * sizeof(int16_t) + 2);
		if (!out->pcm) FAIL("allocation");
		for (k = 0; k < out->frames * ch; k++)
			out->pcm[k] = bps == 1 ? (int16_t)((data[k] - 128) << 8) : le16s(data + 2 * k);
		return 0;
	}
	if (out->format != 2 && out->format != 0x11) FAIL("format tag %d not supported", out->format);
	if (out->bits != 4 || align < 7 * ch || align > 65536) FAIL("ADPCM block %d bits %d", align, out->bits);
	if (fmtlen >= 20) spb = le16(fmt + 18);
	if (out->format == 2) {
		if (fmtlen >= 22) {
			int nc = le16(fmt + 20);
			if (nc >= 1 && nc <= 32 && fmtlen >= 22 + (size_t)nc * 4) {
				ncoef = nc;
				for (k = 0; k < nc; k++) { coef[k][0] = le16s(fmt + 22 + 4 * k); coef[k][1] = le16s(fmt + 24 + 4 * k); }
			}
		}
		if (spb <= 0) spb = (align - 7 * ch) * 2 / ch + 2;
	} else if (spb <= 0) spb = (align - 4 * ch) * 2 / ch + 1;
	cap = (long)((datalen / (size_t)align) + 1) * spb;
	if (cap > MAX_FRAMES) FAIL("too long");
	out->pcm = (int16_t *)malloc((size_t)cap * ch * sizeof(int16_t) + 2);
	if (!out->pcm) FAIL("allocation");
	for (i = 0; i < datalen; i += (size_t)align) {
		const unsigned char *b = data + i;
		size_t blen = datalen - i < (size_t)align ? datalen - i : (size_t)align;
		long fr = 0;
		if (out->format == 2) {
			int pred[2], c;
			long delta[2], s1[2], s2[2];
			size_t q;
			if (blen < (size_t)7 * ch) break;
			for (c = 0; c < ch; c++) {
				pred[c] = b[c];
				if (pred[c] >= ncoef) FAIL("ADPCM predictor %d", pred[c]);
				delta[c] = le16s(b + ch + 2 * c);
				s1[c] = le16s(b + 3 * ch + 2 * c);
				s2[c] = le16s(b + 5 * ch + 2 * c);
			}
			for (c = 0; c < ch; c++) out->pcm[(out->frames + 0) * ch + c] = (int16_t)s2[c];
			for (c = 0; c < ch; c++) out->pcm[(out->frames + 1) * ch + c] = (int16_t)s1[c];
			fr = 2;
			c = 0;
			for (q = (size_t)7 * ch; q < blen && fr < spb; q++) {
				int nib, half;
				for (half = 0; half < 2; half++) {
					long predv, v;
					nib = half ? (b[q] & 15) : (b[q] >> 4);
					predv = asr8(s1[c] * coef[pred[c]][0] + s2[c] * coef[pred[c]][1]);
					v = clamp16(predv + (long)(nib >= 8 ? nib - 16 : nib) * delta[c]);
					s2[c] = s1[c];
					s1[c] = v;
					delta[c] = (ms_adapt[nib] * delta[c]) / 256;
					if (delta[c] < 16) delta[c] = 16;
					out->pcm[(out->frames + fr) * ch + c] = (int16_t)v;
					if (++c == ch) { c = 0; fr++; }
					if (fr >= spb) break;
				}
			}
		} else {
			/* IMA ADPCM: per channel a 4-byte header, then 4-byte groups per channel (8 samples each) */
			long pv[2];
			int idx[2], c;
			size_t q;
			if (blen < (size_t)4 * ch) break;
			for (c = 0; c < ch; c++) {
				pv[c] = le16s(b + 4 * c);
				idx[c] = b[4 * c + 2] > 88 ? 88 : b[4 * c + 2];
				out->pcm[out->frames * ch + c] = (int16_t)pv[c];
			}
			fr = 1;
			for (q = (size_t)4 * ch; q + (size_t)4 * ch <= blen && fr < spb; q += (size_t)4 * ch) {
				for (c = 0; c < ch; c++) {
					int j;
					for (j = 0; j < 8; j++) {
						int nib = (b[q + 4 * c + j / 2] >> ((j & 1) ? 4 : 0)) & 15, st = ima_step[idx[c]];
						long diff = st >> 3;
						if (nib & 4) diff += st;
						if (nib & 2) diff += st >> 1;
						if (nib & 1) diff += st >> 2;
						pv[c] = clamp16(nib & 8 ? pv[c] - diff : pv[c] + diff);
						idx[c] += ima_index[nib];
						if (idx[c] < 0) idx[c] = 0;
						if (idx[c] > 88) idx[c] = 88;
						if (fr + j < spb) out->pcm[(out->frames + fr + j) * ch + c] = (int16_t)pv[c];
					}
				}
				fr += 8;
				if (fr > spb) fr = spb;
			}
		}
		out->frames += fr;
		if (out->frames > cap) FAIL("internal frame overrun");
	}
	/* The fact chunk, when present, holds the true sample count of the last (partial) block. */
	{
		size_t j = 12;
		while (j + 8 <= n) {
			size_t len = le32(p + j + 4);
			if (!memcmp(p + j, "fact", 4) && len >= 4 && j + 12 <= n) {
				long fc = (long)le32(p + j + 8);
				if (fc > 0 && fc < out->frames) out->frames = fc;
			}
			if (len > n - j - 8) break;
			j += 8 + len + (len & 1);
		}
	}
	return 0;
fail:
	wav_free(out);
	return -1;
}

void wav_free(wav_pcm *w)
{
	free(w->pcm);
	w->pcm = NULL;
	w->frames = 0;
}

static void put32(unsigned char *b, unsigned long v) { b[0] = v & 255; b[1] = (v >> 8) & 255; b[2] = (v >> 16) & 255; b[3] = (v >> 24) & 255; }
static void put16(unsigned char *b, unsigned v) { b[0] = v & 255; b[1] = (v >> 8) & 255; }

int wav_write_pcm16(const wav_pcm *w, const char *path)
{
	unsigned char h[44];
	unsigned long bytes = (unsigned long)w->frames * w->channels * 2;
	long k;
	int ok = 1;
	FILE *f = fopen(path, "wb");
	if (!f) return -1;
	memcpy(h, "RIFF", 4); put32(h + 4, 36 + bytes); memcpy(h + 8, "WAVEfmt ", 8);
	put32(h + 16, 16); put16(h + 20, 1); put16(h + 22, (unsigned)w->channels); put32(h + 24, (unsigned long)w->rate);
	put32(h + 28, (unsigned long)w->rate * w->channels * 2); put16(h + 32, (unsigned)w->channels * 2); put16(h + 34, 16);
	memcpy(h + 36, "data", 4); put32(h + 40, bytes);
	ok = fwrite(h, 1, 44, f) == 44;
	for (k = 0; k < w->frames * w->channels && ok; k++) {
		unsigned char s[2];
		put16(s, (unsigned)(uint16_t)w->pcm[k]);
		ok = fwrite(s, 1, 2, f) == 2;
	}
	if (fclose(f)) ok = 0;
	return ok ? 0 : -1;
}
