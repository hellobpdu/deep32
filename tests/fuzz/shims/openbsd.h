/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * openbsd.h: the three OpenBSD libc functions the Ask link parsers call that glibc lacks. build.sh force-includes
 * this header (-include) when it compiles node/asklink/src on Linux; openbsd.c has the definitions. Only the fuzz
 * build uses them: the Workstation itself links OpenBSD's libc.
 */
#ifndef D32_FUZZ_OPENBSD_H
#define D32_FUZZ_OPENBSD_H
#include <stddef.h>

void *recallocarray(void *ptr, size_t oldnmemb, size_t newnmemb, size_t size);
void freezero(void *ptr, size_t size);
int timingsafe_bcmp(const void *b1, const void *b2, size_t n);

#endif
