/* b64.c: canonical base64 (b64.h). */
#include "b64.h"

static const char ALPHA[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

size_t b64_len(size_t n) { return (n + 2) / 3 * 4; }

size_t b64_encode(const unsigned char *in, size_t n, char *out)
{
	size_t i, k = 0;
	for (i = 0; i + 2 < n; i += 3) {
		unsigned v = (unsigned)in[i] << 16 | (unsigned)in[i + 1] << 8 | in[i + 2];
		out[k++] = ALPHA[v >> 18 & 63]; out[k++] = ALPHA[v >> 12 & 63];
		out[k++] = ALPHA[v >> 6 & 63]; out[k++] = ALPHA[v & 63];
	}
	if (n - i == 1) {
		unsigned v = (unsigned)in[i] << 16;
		out[k++] = ALPHA[v >> 18 & 63]; out[k++] = ALPHA[v >> 12 & 63]; out[k++] = '='; out[k++] = '=';
	} else if (n - i == 2) {
		unsigned v = (unsigned)in[i] << 16 | (unsigned)in[i + 1] << 8;
		out[k++] = ALPHA[v >> 18 & 63]; out[k++] = ALPHA[v >> 12 & 63]; out[k++] = ALPHA[v >> 6 & 63]; out[k++] = '=';
	}
	out[k] = 0;
	return k;
}

static int val(int c)
{
	if (c >= 'A' && c <= 'Z') return c - 'A';
	if (c >= 'a' && c <= 'z') return c - 'a' + 26;
	if (c >= '0' && c <= '9') return c - '0' + 52;
	if (c == '+') return 62;
	if (c == '/') return 63;
	return -1;
}

long b64_decode(const char *in, size_t len, unsigned char *out, size_t cap)
{
	size_t i, k = 0;
	if (len % 4) return -1;
	for (i = 0; i < len; i += 4) {
		int a = val((unsigned char)in[i]), b = val((unsigned char)in[i + 1]);
		int c = in[i + 2] == '=' ? -2 : val((unsigned char)in[i + 2]);
		int d = in[i + 3] == '=' ? -2 : val((unsigned char)in[i + 3]);
		int last = i + 4 == len;
		if (a < 0 || b < 0 || c == -1 || d == -1) return -1;
		if ((c == -2 || d == -2) && !last) return -1;  /* padding only at the end */
		if (c == -2 && d != -2) return -1;
		if (c == -2) {                                  /* one byte: b's low 4 bits must be zero */
			if (b & 15 || k + 1 > cap) return -1;
			out[k++] = (unsigned char)(a << 2 | b >> 4);
		} else if (d == -2) {                           /* two bytes: c's low 2 bits must be zero */
			if (c & 3 || k + 2 > cap) return -1;
			out[k++] = (unsigned char)(a << 2 | b >> 4);
			out[k++] = (unsigned char)((b & 15) << 4 | c >> 2);
		} else {
			if (k + 3 > cap) return -1;
			out[k++] = (unsigned char)(a << 2 | b >> 4);
			out[k++] = (unsigned char)((b & 15) << 4 | c >> 2);
			out[k++] = (unsigned char)((c & 3) << 6 | d);
		}
	}
	return (long)k;
}
