/* sha256.h: FIPS 180-4 SHA-256, portable C99 (for manifests; not for secrets). */
#ifndef E26_SHA256_H
#define E26_SHA256_H
#include <stddef.h>
#include <stdint.h>

typedef struct {
	uint32_t h[8];
	uint64_t len;
	unsigned char buf[64];
	size_t nbuf;
} sha256_ctx;

void sha256_init(sha256_ctx *c);
void sha256_update(sha256_ctx *c, const void *data, size_t n);
void sha256_final(sha256_ctx *c, unsigned char out[32]);
/* hex must hold 65 bytes */
void sha256_hex(const void *data, size_t n, char hex[65]);
void sha256_tohex(const unsigned char d[32], char hex[65]);

#endif
