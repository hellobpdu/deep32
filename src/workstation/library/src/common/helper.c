/* helper.c: the Library helpers' request loop (helper.h). */
#include "helper.h"
#include "e26p.h"
#include "../app/json.h"
#include "../app/registry.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* a JSON string literal of s (quotes and backslashes escaped, control characters as \u00XX) into out */
static void jstr(char *out, size_t n, const char *s)
{
	size_t k = 0;
	if (n < 3) { if (n) out[0] = 0; return; }
	out[k++] = '"';
	for (; *s && k + 7 < n; s++) {
		unsigned char c = (unsigned char)*s;
		if (c == '"' || c == '\\') { out[k++] = '\\'; out[k++] = (char)c; }
		else if (c < 0x20) k += (size_t)snprintf(out + k, n - k, "\\u%04x", c);
		else out[k++] = (char)c;
	}
	out[k++] = '"';
	out[k] = 0;
}

static int reply(int fd, unsigned long long id, const char *body)
{
	return e26p_write(fd, id, E26P_RESPONSE, body, strlen(body));
}

static int fail(int fd, unsigned long long id, const char *code, const char *message, const char *generation)
{
	char m[600], g[200], body[1024];
	jstr(m, sizeof m, message);
	if (generation) jstr(g, sizeof g, generation); else snprintf(g, sizeof g, "null");
	snprintf(body, sizeof body, "{\"ok\":false,\"error\":{\"code\":\"%s\",\"message\":%s},\"generation\":%s}", code, m, g);
	return reply(fd, id, body);
}

int helper_serve(int fd, int table, const char *generation)
{
	for (;;) {
		e26p_header h;
		char *body = NULL, err[200], g[200];
		jv *v;
		int r = e26p_read(fd, &h, &body), id;
		const char *op;
		if (r <= 0) { free(body); return r == 0 ? 0 : 1; }
		if (h.kind != E26P_REQUEST) { free(body); if (h.kind == E26P_CANCEL) continue; return 1; } /* an unsolicited response */
		v = json_parse(body, h.length, err, sizeof err);
		free(body);
		op = v && v->type == J_OBJ ? json_str(v, "op") : NULL;
		if (!op || !json_get(v, "args") || json_get(v, "args")->type != J_OBJ) {
			if (fail(fd, h.id, "E_ARGUMENT", "The request is not a named query with arguments.", generation)) { json_free(v); return 1; }
		} else if ((id = reg_find(table, op)) < 0 || !reg_impl(table, id)) {
			if (fail(fd, h.id, "E_UNSUPPORTED", "This build has no such query.", generation)) { json_free(v); return 1; }
		} else {
			const e26_helper_op *o = (const e26_helper_op *)reg_impl(table, id);
			char *result = NULL;
			const char *code = o->run(json_get(v, "args"), &result);
			if (code) {
				if (fail(fd, h.id, code, result ? result : "The request could not be answered.", generation)) { free(result); json_free(v); return 1; }
			} else {
				size_t n = (result ? strlen(result) : 4) + 256;
				char *out = (char *)malloc(n);
				if (generation) jstr(g, sizeof g, generation); else snprintf(g, sizeof g, "null");
				if (!out) { free(result); json_free(v); return 1; }
				snprintf(out, n, "{\"ok\":true,\"result\":%s,\"generation\":%s}", result ? result : "null", g);
				r = reply(fd, h.id, out);
				free(out);
				if (r) { free(result); json_free(v); return 1; }
			}
			free(result);
		}
		json_free(v);
	}
}
