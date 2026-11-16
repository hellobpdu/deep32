/* helper.h: the request loop the Library helpers share (e26-lib: src/lib, e26-render: src/render). A helper reads
 * E26P requests on its socket (descriptor 3, spawn.h), finds the request's "op" in its registry table (REG_QUERY for
 * e26-lib, REG_RENDEROP for e26-render) and answers with LIB-PROTOCOL's envelopes. An op nothing registered gets
 * E_UNSUPPORTED; a body that is not a JSON object with "op" and "args" gets E_ARGUMENT; a malformed frame closes the
 * connection. A query or operation is one file, src/lib/q_<room>.c or src/render/op_<x>.c, defining
 * void e26_q_<room>(void) or void e26_op_<x>(void) that registers it; the Makefile collects them. */
#ifndef E26_HELPER_H
#define E26_HELPER_H
#include <stddef.h>

struct jv;

/* a named query or render operation: NULL and *result (a malloc'd JSON value, the envelope's "result") on success, or
 * an error code ("E_NOT_FOUND", "E_ARGUMENT", ...) and optionally *result a malloc'd user message */
typedef struct {
	const char *(*run)(const struct jv *args, char **result);
} e26_helper_op;

/* serves requests on fd until end of file or a malformed frame; returns the exit code */
int helper_serve(int fd, int table, const char *generation);

#endif
