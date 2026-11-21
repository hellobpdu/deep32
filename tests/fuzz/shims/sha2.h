/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * sha2.h: OpenBSD's <sha2.h> SHA-256 interface on top of the Library's FIPS 180-4 implementation
 * (src/workstation/library/src/common/sha256.c), so node/asklink/src/sas.c compiles on Linux for fuzzing.
 */
#ifndef D32_FUZZ_SHA2_H
#define D32_FUZZ_SHA2_H
#include "sha256.h"

#define SHA256_DIGEST_LENGTH 32
typedef sha256_ctx SHA2_CTX;

#define SHA256Init(ctx) sha256_init(ctx)
#define SHA256Update(ctx, data, len) sha256_update((ctx), (data), (len))
#define SHA256Final(digest, ctx) sha256_final((ctx), (digest))

#endif
