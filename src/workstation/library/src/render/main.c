/* SPDX-License-Identifier: AGPL-3.0-or-later */
/* Copyright (C) 2026 Jeffrey A. Cummings */
/* e26-render: the picture and PDF helper (W1-IMAGE; LIB-PROTOCOL.md "e26-render named operations"). d32library
 * starts it on a socketpair (descriptor 3, spawn.h); every src/render/op_<x>.c registers its operations (REG_RENDEROP)
 * through the generated render_modules.gen.c, and serve.c answers them. MuPDF (HAVE_MUPDF) reads PNG, JPEG, GIF, SVG
 * and PDF and writes PDF; libwebp (HAVE_LIBWEBP) reads WebP. Every parser of picture and PDF bytes in Deep32 Library runs
 * here, never in the interface.
 *
 * Hardening (OpenBSD): before the first request the helper unveils nothing (no path is visible) and pledges
 * "stdio recvfd": it can read and write the descriptors it holds and receive more with a request, and nothing else
 * (no open, no socket, no exec). Its inputs and its PDF output all arrive as descriptors. */
#include "../app/registry.h"
#include "../app/spawn.h"
#include "render.h"
#include <stdio.h>
#include <unistd.h>

void e26_render_modules_register(void);
#ifdef __OpenBSD__
/* unistd.h declares these only with the BSD namespace visible, which -D_POSIX_C_SOURCE hides */
int pledge(const char *, const char *);
int unveil(const char *, const char *);
#endif

int main(void)
{
	int fd = spawn_child_fd();
	e26_render_modules_register();
#ifdef __OpenBSD__
	if (unveil("/", "") == -1 || unveil(NULL, NULL) == -1) { perror("e26-render: unveil"); return 1; }
	if (pledge("stdio recvfd", NULL) == -1) { perror("e26-render: pledge"); return 1; }
#endif
	return render_serve(fd);
}
