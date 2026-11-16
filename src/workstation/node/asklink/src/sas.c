/*
 * D32U/1 SAS (sas.md): ten BIP-39 English words from
 * SHA-256(lower pin || higher pin || node nonce || relay nonce).
 */
#include <sys/types.h>

#include <sha2.h>
#include <string.h>

#include "d32u.h"

int
sas_pin_cmp(const uint8_t *a, const uint8_t *b)
{
	return memcmp(a, b, D32U_PIN_LEN);	/* unsigned lexicographic */
}

/* nonce is exactly 64 bytes: node HELLO nonce then relay HELLO nonce. */
int
sas_digest(const uint8_t *pin_a, const uint8_t *pin_b, const uint8_t *nonce,
    uint8_t *digest)
{
	SHA2_CTX	 ctx;
	int		 c;

	c = sas_pin_cmp(pin_a, pin_b);
	if (c == 0)
		return -1;	/* identity reflection */
	SHA256Init(&ctx);
	SHA256Update(&ctx, c < 0 ? pin_a : pin_b, D32U_PIN_LEN);
	SHA256Update(&ctx, c < 0 ? pin_b : pin_a, D32U_PIN_LEN);
	SHA256Update(&ctx, nonce, 2 * D32U_NONCE_LEN);
	SHA256Final(digest, &ctx);
	explicit_bzero(&ctx, sizeof(ctx));
	return 0;
}

/* First 110 bits, MSB first, 11 bits per word. */
void
sas_indices(const uint8_t *digest, int *idx)
{
	int	 k, bit, n;

	for (k = 0; k < SAS_WORDS; k++) {
		n = 0;
		for (bit = 11 * k; bit < 11 * k + 11; bit++)
			n = (n << 1) | ((digest[bit / 8] >> (7 - bit % 8)) & 1);
		idx[k] = n;
	}
}

/* Writes "WORD WORD ... WORD" (upper case, single spaces). */
int
sas_code(const uint8_t *pin_a, const uint8_t *pin_b, const uint8_t *nonce,
    char *out, size_t outlen)
{
	uint8_t		 digest[SHA256_DIGEST_LENGTH];
	int		 idx[SAS_WORDS], k;
	size_t		 o = 0, i;
	const char	*w;

	if (outlen < SAS_CODE_MAX)
		return -1;
	if (sas_digest(pin_a, pin_b, nonce, digest) == -1)
		return -1;
	sas_indices(digest, idx);
	explicit_bzero(digest, sizeof(digest));
	for (k = 0; k < SAS_WORDS; k++) {
		if (k)
			out[o++] = ' ';
		for (w = bip39_words[idx[k]], i = 0; w[i] != '\0'; i++)
			out[o++] = (w[i] >= 'a' && w[i] <= 'z') ?
			    w[i] - 'a' + 'A' : w[i];
	}
	out[o] = '\0';
	return 0;
}

static int
is_ascii_space(char c)
{
	return c == ' ' || c == '\t' || c == '\r' || c == '\n' ||
	    c == '\v' || c == '\f';
}

/*
 * Typed confirmation: ASCII letters separated by ASCII whitespace,
 * case-insensitive, exactly the ten complete words in order.  Digits
 * (the 01..10 numbering) and any other byte are refused.
 */
int
sas_verify_typed(const char *in, size_t len, const char *code)
{
	char	 norm[SAS_INPUT_MAX + 1];
	size_t	 i, o = 0;
	int	 inword = 0, ok;

	if (len == 0 || len > SAS_INPUT_MAX)
		return 0;
	for (i = 0; i < len; i++) {
		char c = in[i];

		if (is_ascii_space(c)) {
			inword = 0;
			continue;
		}
		if (c >= 'a' && c <= 'z')
			c = c - 'a' + 'A';
		else if (!(c >= 'A' && c <= 'Z'))
			return 0;
		if (!inword && o > 0)
			norm[o++] = ' ';
		inword = 1;
		norm[o++] = c;
	}
	norm[o] = '\0';
	ok = strlen(code) == o && ct_equal(norm, code, o);
	explicit_bzero(norm, sizeof(norm));
	return ok;
}
