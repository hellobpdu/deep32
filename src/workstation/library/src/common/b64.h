/* b64.h: canonical base64 (RFC 4648 section 4, with padding), as LIB-PROTOCOL.md's data_b64 fields and bitmaps use
 * it (W1-IMAGE). No line breaks; the decoder refuses anything not canonical. */
#ifndef E26_B64_H
#define E26_B64_H
#include <stddef.h>

/* the encoded length of n bytes (without a terminating NUL) */
size_t b64_len(size_t n);
/* encodes n bytes into out (b64_len(n) + 1 bytes: NUL-terminated); returns the encoded length */
size_t b64_encode(const unsigned char *in, size_t n, char *out);
/* decodes len characters into out (at most cap bytes); returns the decoded length, or -1 when the text is not
 * canonical base64 (wrong length, characters outside the alphabet, misplaced padding, non-zero tail bits) or does
 * not fit */
long b64_decode(const char *in, size_t len, unsigned char *out, size_t cap);

#endif
