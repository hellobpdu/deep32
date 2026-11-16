#include <string.h>

#include "d32u.h"

/* HELLO id / session id: 1..64 of [A-Za-z0-9_-]. */
int
d32u_id_ok(const char *s, size_t n)
{
	size_t	 i;

	if (n == 0 || n > D32U_ID_MAX)
		return 0;
	for (i = 0; i < n; i++) {
		char c = s[i];
		if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
		    (c >= '0' && c <= '9') || c == '_' || c == '-'))
			return 0;
	}
	return 1;
}

static int
hexval(char c)
{
	if (c >= '0' && c <= '9')
		return c - '0';
	if (c >= 'a' && c <= 'f')
		return c - 'a' + 10;
	return -1;	/* lower case only: the wire form is lower-case hex */
}

int
hex_decode(const char *s, size_t n, uint8_t *out, size_t outlen)
{
	size_t	 i;
	int	 hi, lo;

	if (n != 2 * outlen)
		return -1;
	for (i = 0; i < outlen; i++) {
		if ((hi = hexval(s[2 * i])) < 0 ||
		    (lo = hexval(s[2 * i + 1])) < 0)
			return -1;
		out[i] = (hi << 4) | lo;
	}
	return 0;
}

void
hex_encode(const uint8_t *b, size_t n, char *out)
{
	static const char digits[] = "0123456789abcdef";
	size_t	 i;

	for (i = 0; i < n; i++) {
		out[2 * i] = digits[b[i] >> 4];
		out[2 * i + 1] = digits[b[i] & 15];
	}
	out[2 * n] = '\0';
}

int
ct_equal(const void *a, const void *b, size_t n)
{
	return timingsafe_bcmp(a, b, n) == 0;
}
