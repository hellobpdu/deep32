/* SPDX-License-Identifier: AGPL-3.0-or-later */
/* Copyright (C) 2026 Jeffrey A. Cummings */
/* mu.h: e26-render's one MuPDF context (W1-IMAGE). MuPDF's headers are not C99-pedantic clean (typedefs declared
 * twice, a C11 feature), so they are read here, with those two diagnostics off for them alone. Every allocation
 * MuPDF makes is counted against a ceiling (MU_HEAP): a picture or page that would need more fails as E_TOO_LARGE
 * instead of taking the machine's memory. */
#ifndef E26_RENDER_MU_H
#define E26_RENDER_MU_H
#ifdef HAVE_MUPDF
#if defined(__clang__)
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wpedantic"
#pragma clang diagnostic ignored "-Wtypedef-redefinition"
#elif defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wpedantic"
#endif
#include <mupdf/fitz.h>
#include <mupdf/pdf.h>
#if defined(__clang__)
#pragma clang diagnostic pop
#elif defined(__GNUC__)
#pragma GCC diagnostic pop
#endif

#define MU_HEAP (384u * 1024u * 1024u)   /* MuPDF's whole heap, every open document included */

/* the context (made on first use: after the pledge, which it needs nothing beyond); NULL when it cannot be made */
fz_context *mu_ctx(void);
/* the error code for what MuPDF threw last: E_TOO_LARGE after an allocation over the ceiling, else E_ARGUMENT
 * (the input is not what it claims, or is damaged) */
const char *mu_code(void);
void mu_reset_code(void);
/* MuPDF's premultiplied samples (n components, the last alpha when alpha is 1) to RGBA, w x h */
void mu_to_rgba(const unsigned char *s, int stride, int n, int alpha, int w, int h, unsigned char *out);
#endif
#endif
