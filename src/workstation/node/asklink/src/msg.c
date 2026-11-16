/*
 * Validation of peer messages against docs/spec/schemas, and builders for
 * the node's own messages.  Every check returns 0 or E_PROTOCOL; unknown
 * fields, wrong types and floats for integers are refused.
 */
#include <string.h>

#include "msg.h"

const char *const status_codes[] = { "OFF", "LISTENING", "PAIRING",
    "CONNECTED", "DISCONNECTED", "CODEX_READY", "CODEX_SIGNED_OUT", "BUSY",
    "IDLE", "ERROR", NULL };

const char *const mcp_tools[] = { "rag_status", "search_rag", "search_many",
    "get_section", "get_outline", "lookup_id", "lookup_rfc", "find_command",
    "grep_corpus", "list_documents", "verify_quote", NULL };

int
ascii_token_ok(const char *s, size_t n, size_t max)
{
	size_t	 i;

	if (n == 0 || n > max)
		return 0;
	for (i = 0; i < n; i++)
		if ((uint8_t)s[i] < 33 || (uint8_t)s[i] > 126)
			return 0;
	return 1;
}

int
mcp_tool_index(const char *s, size_t n)
{
	int	 i;

	for (i = 0; mcp_tools[i]; i++)
		if (strlen(mcp_tools[i]) == n && memcmp(mcp_tools[i], s, n) == 0)
			return i;
	return -1;
}

int
status_index(const struct jval *v)
{
	int	 i;

	if (v == NULL || v->type != J_STR)
		return -1;
	for (i = 0; status_codes[i]; i++)
		if (json_str_is(v, status_codes[i]))
			return i;
	return -1;
}

static int
one_int(const struct jval *a, int64_t want)
{
	return a && a->type == J_ARR && a->count == 1 &&
	    a->first->type == J_INT && a->first->i == want;
}

static int
one_str(const struct jval *a, const char *want)
{
	return a && a->type == J_ARR && a->count == 1 &&
	    json_str_is(a->first, want);
}

static int
copy_list(const struct jval *a, char out[][HELLO_STR_MAX + 1], int *n)
{
	const struct jval	*e;

	*n = 0;
	if (a == NULL || a->type != J_ARR)
		return -1;
	for (e = a->first; e; e = e->next) {
		if (e->type != J_STR || e->n < 1 || e->n > HELLO_STR_MAX ||
		    memchr(e->s, '\0', e->n) != NULL)
			return -1;
		if (*n < HELLO_LIST_MAX) {
			memcpy(out[*n], e->s, e->n);
			out[*n][e->n] = '\0';
			(*n)++;
		}
	}
	return 0;
}

int
msg_hello(const uint8_t *p, size_t n, struct peer_hello *h)
{
	static const char *const keys[] = { "proto", "id", "version",
	    "versions", "role", "features", "nonce", "sessions",
	    "codex_version", "signed_in", "profiles", NULL };
	static const char *const req[] = { "proto", "id", "version",
	    "versions", "role", "features", "nonce", "sessions", NULL };
	static const char *const pkeys[] = { "name", "models", "efforts",
	    NULL };
	struct jval		*root = NULL;
	const struct jval	*v, *pr;
	int			 rv = E_PROTOCOL;

	memset(h, 0, sizeof(*h));
	h->signed_in = -1;
	if (json_parse(p, n, &root) == -1)
		return E_PROTOCOL;
	if (!json_only_keys(root, keys) || !json_has_keys(root, req))
		goto done;
	if (!json_str_is(json_get(root, "proto"), "D32U/1"))
		goto done;
	v = json_get(root, "id");
	if (v->type != J_STR || !d32u_id_ok(v->s, v->n))
		goto done;
	memcpy(h->id, v->s, v->n);
	h->id[v->n] = '\0';
	v = json_get(root, "version");
	if (v->type != J_INT || v->i != 1)
		goto done;
	if (!one_int(json_get(root, "versions"), 1))
		goto done;
	v = json_get(root, "role");
	if (json_str_is(v, "relay"))
		h->role = PEER_ROLE_RELAY;
	else if (json_str_is(v, "hub"))
		h->role = PEER_ROLE_HUB;
	else
		goto done;	/* "node" or anything else: same roles refused */
	if (!one_str(json_get(root, "features"), "research-v1"))
		goto done;
	v = json_get(root, "nonce");
	if (v->type != J_STR || hex_decode(v->s, v->n, h->nonce,
	    D32U_NONCE_LEN) == -1)
		goto done;
	v = json_get(root, "sessions");
	if (v->type != J_ARR || v->count != 0)
		goto done;
	if ((v = json_get(root, "codex_version")) != NULL) {
		if (!json_str_is(v, "0.160.0"))
			goto done;
		h->has_codex_version = 1;
	}
	if ((v = json_get(root, "signed_in")) != NULL) {
		if (v->type == J_TRUE)
			h->signed_in = 1;
		else if (v->type == J_FALSE)
			h->signed_in = 0;
		else
			goto done;
	}
	if ((v = json_get(root, "profiles")) != NULL) {
		if (v->type != J_ARR || v->count > 1)
			goto done;
		if ((pr = v->first) != NULL) {
			if (!json_only_keys(pr, pkeys) ||
			    !json_has_keys(pr, pkeys) ||
			    !json_str_is(json_get(pr, "name"), "research") ||
			    copy_list(json_get(pr, "models"), h->models,
			    &h->nmodels) == -1 ||
			    copy_list(json_get(pr, "efforts"), h->efforts,
			    &h->nefforts) == -1)
				goto done;
		}
	}
	rv = 0;
 done:
	json_free(root);
	return rv;
}

int
msg_hello_ack(const uint8_t *p, size_t n, int *paired)
{
	static const char *const keys[] = { "version", "features", "paired",
	    NULL };
	struct jval		*root = NULL;
	const struct jval	*v;
	int			 rv = E_PROTOCOL;

	if (json_parse(p, n, &root) == -1)
		return E_PROTOCOL;
	if (!json_only_keys(root, keys) || !json_has_keys(root, keys))
		goto done;
	v = json_get(root, "version");
	if (v->type != J_INT || v->i != 1)
		goto done;
	if (!one_str(json_get(root, "features"), "research-v1"))
		goto done;
	v = json_get(root, "paired");
	if (v->type != J_TRUE && v->type != J_FALSE)
		goto done;
	*paired = v->type == J_TRUE;
	rv = 0;
 done:
	json_free(root);
	return rv;
}

int
msg_build_hello(struct dbuf *d, const char *id, const uint8_t *nonce)
{
	char	 hex[2 * D32U_NONCE_LEN + 1];

	hex_encode(nonce, D32U_NONCE_LEN, hex);
	if (dbuf_addstr(d, "{\"proto\":\"D32U/1\",\"id\":") == -1 ||
	    json_put_str(d, id, strlen(id)) == -1 ||
	    dbuf_addstr(d, ",\"version\":1,\"versions\":[1],\"role\":\"node\","
	    "\"features\":[\"research-v1\"],\"nonce\":\"") == -1 ||
	    dbuf_addstr(d, hex) == -1 ||
	    dbuf_addstr(d, "\",\"sessions\":[]}") == -1)
		return -1;
	return 0;
}

int
msg_build_hello_ack(struct dbuf *d, int paired)
{
	return dbuf_printf(d, "{\"version\":1,\"features\":[\"research-v1\"],"
	    "\"paired\":%s}", paired ? "true" : "false");
}

static int
session_field(const struct jval *root, struct ctl_event *ev)
{
	const struct jval *v = json_get(root, "session");

	if (v == NULL || v->type != J_STR || !d32u_id_ok(v->s, v->n))
		return -1;
	ev->session = v->s;
	ev->sessionlen = v->n;
	return 0;
}

/* Relay events on codex-ctl (schemas/codex-ctl.schema.json). */
int
msg_event(const struct jval *root, struct ctl_event *ev)
{
	static const char *const k_opened[] = { "op", "session", NULL };
	static const char *const k_text[] = { "op", "session", "text", NULL };
	static const char *const k_done[] = { "op", "session", "status",
	    "usage", NULL };
	static const char *const k_usage[] = { "input_tokens",
	    "output_tokens", NULL };
	static const char *const k_code[] = { "op", "session", "code", NULL };
	const struct jval	*op, *v, *u, *t;

	memset(ev, 0, sizeof(*ev));
	if (root == NULL || root->type != J_OBJ ||
	    (op = json_get(root, "op")) == NULL)
		return E_PROTOCOL;
	if (json_str_is(op, "session.opened")) {
		ev->op = EV_OPENED;
		if (!json_only_keys(root, k_opened) ||
		    !json_has_keys(root, k_opened))
			return E_PROTOCOL;
	} else if (json_str_is(op, "agent.delta") ||
	    json_str_is(op, "agent.final")) {
		ev->op = json_str_is(op, "agent.delta") ? EV_DELTA : EV_FINAL;
		if (!json_only_keys(root, k_text) ||
		    !json_has_keys(root, k_text))
			return E_PROTOCOL;
		t = json_get(root, "text");
		if (t->type != J_STR || t->n > D32U_RESP_MAX)
			return E_PROTOCOL;
		ev->text = t->s;
		ev->textlen = t->n;
	} else if (json_str_is(op, "turn.completed")) {
		ev->op = EV_COMPLETED;
		if (!json_only_keys(root, k_done) ||
		    !json_has_keys(root, k_done))
			return E_PROTOCOL;
		v = json_get(root, "status");
		if (json_str_is(v, "completed"))
			ev->status = TS_COMPLETED;
		else if (json_str_is(v, "interrupted"))
			ev->status = TS_INTERRUPTED;
		else if (json_str_is(v, "failed"))
			ev->status = TS_FAILED;
		else
			return E_PROTOCOL;
		u = json_get(root, "usage");
		if (!json_only_keys(u, k_usage) || !json_has_keys(u, k_usage))
			return E_PROTOCOL;
		v = json_get(u, "input_tokens");
		if (v->type != J_INT || v->i < 0)
			return E_PROTOCOL;
		ev->in_tokens = v->i;
		v = json_get(u, "output_tokens");
		if (v->type != J_INT || v->i < 0)
			return E_PROTOCOL;
		ev->out_tokens = v->i;
	} else if (json_str_is(op, "error")) {
		ev->op = EV_ERROR;
		if (!json_only_keys(root, k_code) ||
		    !json_has_keys(root, k_code))
			return E_PROTOCOL;
		v = json_get(root, "code");
		if (v->type != J_INT || v->i < 1 || v->i > 11)
			return E_PROTOCOL;
		ev->code = (int)v->i;
	} else if (json_str_is(op, "status")) {
		ev->op = EV_STATUS;
		if (!json_only_keys(root, k_code) ||
		    !json_has_keys(root, k_code))
			return E_PROTOCOL;
		if ((ev->code = status_index(json_get(root, "code"))) < 0)
			return E_PROTOCOL;
	} else
		return E_PROTOCOL;
	if (session_field(root, ev) == -1)
		return E_PROTOCOL;
	return 0;
}

/*
 * OPEN payload: {"session":id} for codex-ctl and mcp, {} for status.
 * want_session selects the form; the session id is copied out.
 */
int
msg_open(const uint8_t *p, size_t n, int want_session, char *sess,
    size_t sesslen)
{
	static const char *const keys[] = { "session", NULL };
	static const char *const none[] = { NULL };
	struct jval		*root = NULL;
	const struct jval	*v;
	int			 rv = E_PROTOCOL;

	if (json_parse(p, n, &root) == -1)
		return E_PROTOCOL;
	if (!want_session) {
		if (json_only_keys(root, none))
			rv = 0;
		goto done;
	}
	if (!json_only_keys(root, keys) || !json_has_keys(root, keys))
		goto done;
	v = json_get(root, "session");
	if (v->type != J_STR || !d32u_id_ok(v->s, v->n) || v->n >= sesslen)
		goto done;
	memcpy(sess, v->s, v->n);
	sess[v->n] = '\0';
	rv = 0;
 done:
	json_free(root);
	return rv;
}

int
msg_status(const struct jval *root, int *code)
{
	static const char *const keys[] = { "code", NULL };

	if (!json_only_keys(root, keys) || !json_has_keys(root, keys))
		return E_PROTOCOL;
	if ((*code = status_index(json_get(root, "code"))) < 0)
		return E_PROTOCOL;
	return 0;
}

/* mcp request envelope, relay to node (schemas/mcp.schema.json). */
int
msg_mcp_request(const struct jval *root, struct mcp_request *r)
{
	static const char *const keys[] = { "body", "mcp_session_id",
	    "protocol_version", NULL };
	static const char *const req[] = { "body", "mcp_session_id", NULL };
	const struct jval	*v;

	memset(r, 0, sizeof(*r));
	if (!json_only_keys(root, keys) || !json_has_keys(root, req))
		return E_PROTOCOL;
	v = json_get(root, "body");
	if (v->type == J_OBJ)
		r->body = v;
	else if (v->type != J_NULL)
		return E_PROTOCOL;
	v = json_get(root, "mcp_session_id");
	if (v->type == J_STR) {
		/* carried into an HTTP header: printable ASCII only */
		if (!ascii_token_ok(v->s, v->n, 128))
			return E_PROTOCOL;
		r->sid = v->s;
		r->sidlen = v->n;
	} else if (v->type != J_NULL)
		return E_PROTOCOL;
	if ((v = json_get(root, "protocol_version")) != NULL) {
		if (v->type != J_STR || !ascii_token_ok(v->s, v->n, 64))
			return E_PROTOCOL;
		r->pver = v->s;
		r->pverlen = v->n;
	}
	return 0;
}
