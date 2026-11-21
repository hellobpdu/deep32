/* SPDX-License-Identifier: AGPL-3.0-or-later */
/* Copyright (C) 2026 Jeffrey A. Cummings */
/* render.h: what e26-render's operations share (W1-IMAGE). LIB-PROTOCOL.md "e26-render named operations".
 *
 * e26-render serves E26P frames on descriptor 3 (spawn.h) like e26-lib, with one difference that is the whole point
 * of a separate helper: a request may carry descriptors (SCM_RIGHTS, attached to the first byte of its frame), an
 * input to decode or an output to write a PDF to. The helper opens no path: before its first request it pledges
 * "stdio recvfd" and unveils nothing, so every byte it parses reaches it through a descriptor the interface opened and
 * chose to hand over. serve.c reads the frames with recvmsg, checks the envelope, runs the operation the request
 * names (REG_RENDEROP) and closes every descriptor the operation did not keep.
 *
 * An operation is a file src/render/op_<x>.c defining void e26_op_<x>(void), which registers it with
 *   reg_add(REG_RENDEROP, "<name>", &op, "op_<x>", RENDER_FDS(n));
 * n being how many descriptors its requests carry (0, or 1 for decode_fit, pdf_open and pdf_write): a request with
 * more or fewer is refused with E_ARGUMENT before the operation runs. */
#ifndef E26_RENDER_H
#define E26_RENDER_H
#include <stddef.h>
#include "../app/json.h"

#define RENDER_FDS(n) ((unsigned)(n) & 7u)
#define RENDER_MAX_FDS 4

/* serves requests on fd until end of file or a malformed frame; the exit code */
int render_serve(int fd);
/* the request being served: the descriptor in ancillary slot i, or -1; render_take_fd also keeps it from being
 * closed when the operation returns (pdf_open keeps its input until pdf_close) */
int render_fd(int slot);
int render_take_fd(int slot);

/* ---- arguments (LIB-PROTOCOL "Envelopes and common types": unlisted fields are E_ARGUMENT, booleans are not
 * numbers, integer bounds inclusive) */
/* 1 when every key of args is in the NULL-terminated list */
int ra_only(const jv *args, const char *const *keys);
/* an integer field within [lo, hi]: 1 found and valid (*out), 0 absent (*out = def), -1 present but invalid */
int ra_int(const jv *args, const char *key, long lo, long hi, long def, long *out);
/* a string field: 1 found (*out), 0 absent (*out = def), -1 not a string or longer than max bytes */
int ra_str(const jv *args, const char *key, size_t max, const char *def, const char **out);
/* a boolean field: 1, 0 absent (*out = def), -1 not a boolean */
int ra_bool(const jv *args, const char *key, int def, int *out);
/* a JSON number that is finite and within [lo, hi]: 1, or 0 */
int ra_num(const jv *v, double lo, double hi, double *out);

/* ---- results */
/* a malloc'd copy of s (an error message), NULL when out of memory */
char *rs_dup(const char *s);
/* a malloc'd JSON string literal of the UTF-8 text s (n bytes): quotes, backslashes and control characters escaped */
char *rs_json_str(const char *s, size_t n);
/* {width,height,stride,format:"rgba8",data_b64} for w x h RGBA pixels; NULL with *code E_TOO_LARGE when the encoded
 * envelope would not fit an E26P body, or E_PACK when out of memory */
char *rs_rgba(const unsigned char *rgba, int w, int h, const char **code);
/* The same RGBA result plus source dimensions for the figure viewer (0,0 for vector art). */
char *rs_rgba_source(const unsigned char *rgba, int w, int h, int sw, int sh, const char **code);
/* the largest RGBA picture (in pixels) whose response fits an E26P body */
size_t rs_max_pixels(void);

/* ---- input descriptors */
/* reads the whole of fd (from its start when it can seek) into a malloc'd buffer of at most max bytes: 0 (*buf, *len),
 * -1 a read error, -2 larger than max */
int rd_all(int fd, size_t max, unsigned char **buf, size_t *len);

#endif
