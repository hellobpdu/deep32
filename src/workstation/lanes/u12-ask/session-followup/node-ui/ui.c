/*
 * The ui child: the local API for Deep32 Library's Ask room (the UI process
 * has no network).  Newline-delimited strict JSON over the UNIX stream
 * socket /var/run/d32/uplink.sock (0660 root:_d32users, FIX-16).
 *
 * Requests (one JSON object per line):
 *   {"op":"status"}
 *   {"op":"session.open"[,"link":"work"][,"engine":"codex"|"claude"]
 *       [,"model":M][,"effort":E]}
 *   {"op":"turn.start","session":S,"text":T}
 *   {"op":"turn.interrupt","session":S}
 *   {"op":"session.close","session":S}
 *   {"op":"channel.open"[,"link":"work"],"channel":"seal"|"lib"|"sync"}
 * The configured Ask owner can additionally use ask.status/enroll/unlock,
 * ask.capabilities, ask.close, and ask.codex.login.begin/cancel. The UI
 * sends neither an authentication window ID nor a grant ID.
 * Events: status, link, session.opened, agent.delta, agent.final,
 *   turn.completed, error {code}, session.closed, mcp (the RAG ledger),
 *   channel.refused.
 */
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/queue.h>
#include <sys/un.h>

#include <errno.h>
#include <imsg.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "chatd.h"
#include "msg.h"

#define UI_MAX_CLIENTS	16
#define UI_OUT_MAX	(4u * 1024 * 1024)

struct client {
	TAILQ_ENTRY(client)	 entry;
	uint32_t		 id;
	int			 fd;
	uid_t			 uid;
	struct dbuf		 in;
	struct dbuf		 out;
	int			 ask_link;
	int			 ask_pending;
	int			 ask_refresh;
	int			 ask_cancel_pending;
	int			 ask_unlocked;
	int			 ask_closed;
	char			 ask_window[CHATD_ASK_WINDOW_SIZE];
	int			 dead;
};
TAILQ_HEAD(clientq, client);

struct usess {
	TAILQ_ENTRY(usess)	 entry;
	char			 id[D32U_ID_MAX + 1];
	uint32_t		 client;
	int			 link;
};
TAILQ_HEAD(usessq, usess);

static struct {
	struct ui_init		 cfg;
	struct imsgbuf		 parent;
	struct imsgbuf		 link[CHATD_MAX_LINKS];
	struct link_state	 ls[CHATD_MAX_LINKS];
	struct clientq		 clients;
	struct usessq		 sessions;
	int			 nclients;
	uint32_t		 next_id;
} U;

static const char *const ls_names[] = { "UNPAIRED", "WAITING",
    "CONNECTING", "HANDSHAKE", "PAIRING", "ACTIVE", "ISOLATED" };

static struct client *
client_find(uint32_t id)
{
	struct client	*c;

	TAILQ_FOREACH(c, &U.clients, entry)
		if (c->id == id && !c->dead)
			return c;
	return NULL;
}

static void
client_write(struct client *c, struct dbuf *line)
{
	if (c->out.len + line->len > UI_OUT_MAX ||
	    dbuf_add(&c->out, line->b, line->len) == -1) {
		/* a UI that does not read loses its connection */
		c->dead = 1;
	}
}

static void
client_reply(struct client *c, struct dbuf *d)
{
	dbuf_add(d, "\n", 1);
	client_write(c, d);
	dbuf_free(d);
}

static void
client_error(struct client *c, int code)
{
	struct dbuf	 d = { 0 };

	dbuf_printf(&d, "{\"op\":\"error\",\"code\":%d}", code);
	client_reply(c, &d);
}

static void
put_list(struct dbuf *d, char list[][65], int n)
{
	int	 i;

	dbuf_addstr(d, "[");
	for (i = 0; i < n; i++) {
		if (i)
			dbuf_addstr(d, ",");
		json_put_str(d, list[i], strnlen(list[i], 64));
	}
	dbuf_addstr(d, "]");
}

static void
link_json(struct dbuf *d, const struct link_state *s)
{
	dbuf_addstr(d, "{\"link\":");
	json_put_str(d, s->name, strnlen(s->name, CHATD_NAME_MAX - 1));
	dbuf_printf(d, ",\"state\":\"%s\",\"paired\":%s,\"relay\":",
	    ls_names[s->state >= 0 && s->state <= LS_ISOLATED ? s->state : 0],
	    s->paired ? "true" : "false");
	if (s->relay_id[0])
		json_put_str(d, s->relay_id, strnlen(s->relay_id, 64));
	else
		dbuf_addstr(d, "null");
	dbuf_printf(d, ",\"sessions\":%d,\"signed_in\":%s,\"codex_version\":%s,"
	    "\"models\":", s->sessions, s->signed_in == 1 ? "true" :
	    s->signed_in == 0 ? "false" : "null",
	    s->has_codex_version ? "\"0.160.0\"" : "null");
	put_list(d, (char (*)[65])s->models, s->nmodels);
	dbuf_addstr(d, ",\"efforts\":");
	put_list(d, (char (*)[65])s->efforts, s->nefforts);
	dbuf_printf(d, ",\"turns\":%lld,\"mcp_calls\":%lld,\"mcp_refused\":%lld,"
	    "\"connects\":%lld}", (long long)s->turns,
	    (long long)s->mcp_calls, (long long)s->mcp_refused,
	    (long long)s->connects);
}

static void
broadcast_link(int i)
{
	struct client	*c;
	struct dbuf	 d = { 0 };

	dbuf_addstr(&d, "{\"op\":\"link\",\"info\":");
	link_json(&d, &U.ls[i]);
	dbuf_addstr(&d, "}\n");
	TAILQ_FOREACH(c, &U.clients, entry)
		if (!c->dead)
			client_write(c, &d);
	dbuf_free(&d);
}

static int
link_by_name(const struct jval *v)
{
	int	 i;

	if (v == NULL)
		return 0;
	if (v->type != J_STR)
		return -1;
	for (i = 0; i < U.cfg.nlinks; i++)
		if (json_str_is(v, U.cfg.names[i]))
			return i;
	return -1;
}

static struct usess *
usess_find(const struct jval *v, uint32_t client)
{
	struct usess	*s;

	if (v == NULL || v->type != J_STR)
		return NULL;
	TAILQ_FOREACH(s, &U.sessions, entry)
		if (s->client == client && json_str_is(v, s->id))
			return s;
	return NULL;
}

static int
copy_opt(const struct jval *v, char *out, size_t outlen)
{
	if (v == NULL) {
		out[0] = '\0';
		return 0;
	}
	if (v->type != J_STR || v->n == 0 || v->n >= outlen ||
	    memchr(v->s, '\0', v->n) != NULL)
		return -1;
	memcpy(out, v->s, v->n);
	out[v->n] = '\0';
	return 0;
}

static int
ask_base64_value(unsigned char c)
{
	if (c >= 'A' && c <= 'Z')
		return c - 'A';
	if (c >= 'a' && c <= 'z')
		return c - 'a' + 26;
	if (c >= '0' && c <= '9')
		return c - '0' + 52;
	if (c == '+')
		return 62;
	return c == '/' ? 63 : -1;
}

static int
ask_password(const struct jval *v, char *out)
{
	size_t	 i, decoded, padding = 0;

	if (v == NULL || v->type != J_STR || v->n < 16 ||
	    v->n >= CHATD_ASK_PASSWORD_SIZE || v->n % 4 != 0)
		return -1;
	if (v->s[v->n - 1] == '=') {
		padding++;
		if (v->s[v->n - 2] == '=')
			padding++;
	}
	decoded = v->n / 4 * 3 - padding;
	if (decoded < 12 || decoded > 256)
		return -1;
	for (i = 0; i < v->n - padding; i++)
		if (ask_base64_value(v->s[i]) < 0)
			return -1;
	if (padding && (ask_base64_value(v->s[i - 1]) &
	    (padding == 1 ? 3 : 15)) != 0)
		return -1;
	memcpy(out, v->s, v->n);
	return 0;
}

static uint32_t
ask_operation(const struct jval *op)
{
	static const char *const names[] = {
		[UI_ASK_STATUS] = "ask.status",
		[UI_ASK_ENROLL] = "ask.enroll",
		[UI_ASK_UNLOCK] = "ask.unlock",
		[UI_ASK_CAPABILITIES] = "ask.capabilities",
		[UI_ASK_CLOSE] = "ask.close",
		[UI_ASK_CODEX_LOGIN_BEGIN] = "ask.codex.login.begin",
		[UI_ASK_CODEX_LOGIN_CANCEL] = "ask.codex.login.cancel"
	};
	uint32_t	 i;

	for (i = 1; i < sizeof(names) / sizeof(names[0]); i++)
		if (json_str_is(op, names[i]))
			return i;
	return 0;
}

static void
ask_request(struct client *c, const struct jval *root, uint32_t op, size_t len)
{
	static const char *const plain[] = { "op", "link", NULL };
	static const char *const password[] = { "op", "link", "password_b64", NULL };
	const struct jval	*v = json_get(root, "link");
	struct ui_ask_req	 r = { 0 };
	struct usess		*s;
	uint8_t			 random[16];
	int			 li, with_password;

	if (!U.cfg.ask_owner_set || c->uid != U.cfg.ask_owner_uid ||
	    c->ask_closed || (op == UI_ASK_STATUS && c->ask_refresh) ||
	    (op == UI_ASK_CODEX_LOGIN_CANCEL && c->ask_cancel_pending) ||
	    (op != UI_ASK_STATUS && op != UI_ASK_CLOSE &&
	    op != UI_ASK_CODEX_LOGIN_CANCEL && (c->ask_pending || c->ask_refresh ||
	    c->ask_cancel_pending))) {
		client_error(c, E_WINDOW_LOCKED);
		return;
	}
	with_password = op == UI_ASK_ENROLL || op == UI_ASK_UNLOCK;
	li = v == NULL ? U.cfg.ask_link : link_by_name(v);
	if (len > CHATD_ASK_LINE_MAX || li != U.cfg.ask_link ||
	    !json_only_keys(root, with_password ? password : plain) ||
	    (with_password && ask_password(json_get(root, "password_b64"),
	    r.password_b64) == -1)) {
		client_error(c, E_PROTOCOL);
		goto done;
	}
	if (c->ask_link == -1) {
		if (op != UI_ASK_STATUS) {
			client_error(c, E_WINDOW_LOCKED);
			goto done;
		}
		TAILQ_FOREACH(s, &U.sessions, entry)
			if (s->client == c->id) {
				client_error(c, E_WINDOW_LOCKED);
				goto done;
			}
		arc4random_buf(random, sizeof(random));
		hex_encode(random, sizeof(random), c->ask_window);
		c->ask_link = li;
	}
	if (op != UI_ASK_STATUS && op != UI_ASK_CLOSE && !with_password &&
	    !c->ask_unlocked) {
		client_error(c, E_WINDOW_LOCKED);
		goto done;
	}
	r.client = c->id;
	r.op = op;
	memcpy(r.window, c->ask_window, sizeof(r.window));
	if (imsg_send(&U.link[li], IMSG_UI_ASK_AUTH, &r, sizeof(r)) == -1) {
		c->dead = 1;
		goto done;
	}
	if (op == UI_ASK_CLOSE) {
		c->ask_closed = 1;
		c->ask_unlocked = 0;
	} else if (op == UI_ASK_CODEX_LOGIN_CANCEL)
		c->ask_cancel_pending = 1;
	else if (op == UI_ASK_STATUS && (c->ask_pending || c->ask_cancel_pending))
		c->ask_refresh = 1;
	else
		c->ask_pending = (int)op;
 done:
	explicit_bzero(&r, sizeof(r));
}

static void
handle_line(struct client *c, const uint8_t *line, size_t len)
{
	static const char *const k_status[] = { "op", NULL };
	static const char *const k_open[] = { "op", "link", "model", "effort",
	    "engine", NULL };
	static const char *const k_turn[] = { "op", "session", "text", NULL };
	static const char *const k_sess[] = { "op", "session", NULL };
	static const char *const k_chan[] = { "op", "link", "channel", NULL };
	struct jval		*root = NULL;
	const struct jval	*op, *v;
	struct ui_req		 r;
	struct usess		*us;
	struct dbuf		 d = { 0 };
	struct ibuf		*ib;
	uint8_t			 rnd[8];
	uint32_t		 ask_op;
	int			 li, i;

	memset(&r, 0, sizeof(r));
	r.client = c->id;
	if (json_parse(line, len, &root) == -1 || root->type != J_OBJ ||
	    (op = json_get(root, "op")) == NULL) {
		client_error(c, E_PROTOCOL);
		goto done;
	}
	if ((ask_op = ask_operation(op)) != 0) {
		ask_request(c, root, ask_op, len);
	} else if (json_str_is(op, "status")) {
		if (!json_only_keys(root, k_status)) {
			client_error(c, E_PROTOCOL);
			goto done;
		}
		dbuf_addstr(&d, "{\"op\":\"status\",\"links\":[");
		for (i = 0; i < U.cfg.nlinks; i++) {
			if (i)
				dbuf_addstr(&d, ",");
			link_json(&d, &U.ls[i]);
		}
		dbuf_addstr(&d, "]}");
		client_reply(c, &d);
	} else if (json_str_is(op, "session.open")) {
		v = json_get(root, "link");
		li = v == NULL && c->ask_link != -1 ? c->ask_link : link_by_name(v);
		if (!json_only_keys(root, k_open) ||
		    li < 0 ||
		    copy_opt(json_get(root, "model"), r.model,
		    sizeof(r.model)) == -1 ||
		    copy_opt(json_get(root, "effort"), r.effort,
		    sizeof(r.effort)) == -1) {
			client_error(c, E_PROTOCOL);
			goto done;
		}
		v = json_get(root, "engine");
		if (msg_engine(v) != 0 || copy_opt(v, r.engine,
		    sizeof(r.engine)) == -1) {
			client_error(c, E_SESSION);
			goto done;
		}
		if (c->ask_link != -1) {
			if (li != c->ask_link || !c->ask_unlocked ||
			    c->ask_closed || !r.engine[0] || !r.model[0] ||
			    !r.effort[0]) {
				client_error(c, E_WINDOW_LOCKED);
				goto done;
			}
			memcpy(r.ask_window, c->ask_window, sizeof(r.ask_window));
		}
		/* the node chooses the session id */
		arc4random_buf(rnd, sizeof(rnd));
		r.session[0] = 's';
		hex_encode(rnd, sizeof(rnd), r.session + 1);
		if ((us = calloc(1, sizeof(*us))) == NULL)
			fatalx("ui: out of memory");
		strlcpy(us->id, r.session, sizeof(us->id));
		us->client = c->id;
		us->link = li;
		TAILQ_INSERT_TAIL(&U.sessions, us, entry);
		imsg_compose(&U.link[li], IMSG_UI_SESSION_OPEN, 0, 0, -1, &r,
		    sizeof(r));
	} else if (json_str_is(op, "turn.start")) {
		if (c->ask_link != -1 && (!c->ask_unlocked || c->ask_closed)) {
			client_error(c, E_WINDOW_LOCKED);
			goto done;
		}
		if (!json_only_keys(root, k_turn) ||
		    (us = usess_find(json_get(root, "session"), c->id)) ==
		    NULL || (v = json_get(root, "text")) == NULL ||
		    v->type != J_STR || v->n == 0 || v->n > D32U_REQ_MAX) {
			client_error(c, E_SESSION);
			goto done;
		}
		strlcpy(r.session, us->id, sizeof(r.session));
		if (c->ask_link != -1)
			memcpy(r.ask_window, c->ask_window, sizeof(r.ask_window));
		if ((ib = imsg_create(&U.link[us->link], IMSG_UI_TURN_START, 0,
		    0, sizeof(r) + v->n)) == NULL ||
		    imsg_add(ib, &r, sizeof(r)) == -1 ||
		    imsg_add(ib, v->s, v->n) == -1)
			fatalx("ui: imsg");
		imsg_close(&U.link[us->link], ib);
	} else if (json_str_is(op, "turn.interrupt") ||
	    json_str_is(op, "session.close")) {
		if (!json_only_keys(root, k_sess) ||
		    (us = usess_find(json_get(root, "session"), c->id)) ==
		    NULL) {
			client_error(c, E_SESSION);
			goto done;
		}
		strlcpy(r.session, us->id, sizeof(r.session));
		if (c->ask_link != -1)
			memcpy(r.ask_window, c->ask_window, sizeof(r.ask_window));
		imsg_compose(&U.link[us->link], json_str_is(op,
		    "turn.interrupt") ? IMSG_UI_TURN_INTERRUPT :
		    IMSG_UI_SESSION_CLOSE, 0, 0, -1, &r, sizeof(r));
	} else if (json_str_is(op, "channel.open")) {
		v = json_get(root, "channel");
		if (!json_only_keys(root, k_chan) ||
		    (li = link_by_name(json_get(root, "link"))) < 0) {
			client_error(c, E_PROTOCOL);
			goto done;
		}
		if (json_str_is(v, "seal"))
			r.channel = CH_SEAL;
		else if (json_str_is(v, "lib"))
			r.channel = CH_LIB;
		else if (json_str_is(v, "sync"))
			r.channel = CH_SYNC;
		else {
			client_error(c, E_PROTOCOL);
			goto done;
		}
		/* forwarded so that the link itself refuses (and logs) */
		imsg_compose(&U.link[li], IMSG_UI_CHANNEL_OPEN, 0, 0, -1, &r,
		    sizeof(r));
	} else
		client_error(c, E_PROTOCOL);
 done:
	json_free(root);
}

static void
client_read(struct client *c)
{
	uint8_t		 tmp[16384];
	ssize_t		 n;
	uint8_t		*nl;
	size_t		 l;

	if ((n = read(c->fd, tmp, sizeof(tmp))) == -1) {
		if (errno != EAGAIN && errno != EINTR)
			c->dead = 1;
		return;
	}
	if (n == 0) {
		c->dead = 1;
		return;
	}
	if (dbuf_add(&c->in, tmp, n) == -1) {
		explicit_bzero(tmp, (size_t)n);
		c->dead = 1;
		return;
	}
	explicit_bzero(tmp, (size_t)n);
	while ((nl = memchr(c->in.b, '\n', c->in.len)) != NULL) {
		l = nl - c->in.b;
		if (l > CHATD_UI_LINE_MAX) {
			c->dead = 1;
			return;
		}
		handle_line(c, c->in.b, l);
		dbuf_drop(&c->in, l + 1);
		if (c->dead)
			return;
	}
	if (c->in.len > CHATD_UI_LINE_MAX)
		c->dead = 1;
}

static void
client_flush(struct client *c)
{
	ssize_t	 n;

	while (c->out.len) {
		n = write(c->fd, c->out.b, c->out.len);
		if (n == -1) {
			if (errno != EAGAIN && errno != EINTR)
				c->dead = 1;
			return;
		}
		dbuf_drop(&c->out, n);
	}
}

static void
client_reap(void)
{
	struct client	*c, *nc;
	struct usess	*s, *ns;
	struct ui_req	 r;
	int		 i;

	for (c = TAILQ_FIRST(&U.clients); c; c = nc) {
		nc = TAILQ_NEXT(c, entry);
		if (!c->dead)
			continue;
		memset(&r, 0, sizeof(r));
		r.client = c->id;
		for (i = 0; i < U.cfg.nlinks; i++)
			imsg_compose(&U.link[i], IMSG_UI_GONE, 0, 0, -1, &r,
			    sizeof(r));
		for (s = TAILQ_FIRST(&U.sessions); s; s = ns) {
			ns = TAILQ_NEXT(s, entry);
			if (s->client == c->id) {
				TAILQ_REMOVE(&U.sessions, s, entry);
				free(s);
			}
		}
		close(c->fd);
		dbuf_free(&c->in);
		dbuf_free(&c->out);
		TAILQ_REMOVE(&U.clients, c, entry);
		U.nclients--;
		free(c);
	}
}

static void
accept_client(void)
{
	struct client	*c;
	int		 fd;
	uid_t		 uid;
	gid_t		 gid;

	if ((fd = accept4(CHATD_FD_ROLE, NULL, NULL, SOCK_NONBLOCK |
	    SOCK_CLOEXEC)) == -1)
		return;
	if (U.nclients >= UI_MAX_CLIENTS || getpeereid(fd, &uid, &gid) == -1) {
		close(fd);
		return;
	}
	if ((c = calloc(1, sizeof(*c))) == NULL)
		fatalx("ui: out of memory");
	c->fd = fd;
	c->uid = uid;
	c->ask_link = -1;
	c->id = ++U.next_id;
	TAILQ_INSERT_TAIL(&U.clients, c, entry);
	U.nclients++;
	log_info("ui: client %u connected (uid %d)", c->id, (int)uid);
}

static int
ask_token(const struct jval *v, size_t limit, const char *extra)
{
	size_t	 i;
	char	 c;

	if (v == NULL || v->type != J_STR || v->n == 0 || v->n > limit)
		return 0;
	for (i = 0; i < v->n; i++) {
		c = v->s[i];
		if ((c < 'A' || c > 'Z') && (c < 'a' || c > 'z') &&
		    (c < '0' || c > '9') && (c == '\0' || strchr(extra, c) == NULL))
			return 0;
	}
	return 1;
}

static int
ask_event_safe(struct client *c, const uint8_t *line, size_t len)
{
	static const char *const bare[] = { "op", NULL };
	static const char *const state[] = { "op", "lock", "codex", "claude", "reason", NULL };
	static const char *const capabilities[] = { "op", "tuples", NULL };
	static const char *const choice[] = { "engine", "model", "effort", "billing",
	    "preferred", NULL };
	static const char *const challenge[] = { "op", "url", "code", NULL };
	static const char *const codex_state[] = { "op", "state", NULL };
	struct jval		*root = NULL;
	const struct jval	*op, *v, *row, *engine, *billing, *preferred;
	int			 safe = 0, completes = 1;

	if (len < 2 || len > CHATD_ASK_LINE_MAX || line[len - 1] != '\n' ||
	    memchr(line, '\n', len - 1) != NULL ||
	    json_parse(line, len - 1, &root) == -1 || root->type != J_OBJ)
		goto done;
	op = json_get(root, "op");
	if (json_str_is(op, "ask.unlocked")) {
		safe = json_only_keys(root, bare) && !c->ask_closed &&
		    (c->ask_pending == UI_ASK_ENROLL || c->ask_pending == UI_ASK_UNLOCK);
		if (safe)
			c->ask_unlocked = 1;
	} else if (json_str_is(op, "ask.closed")) {
		safe = json_only_keys(root, bare);
		if (safe) {
			c->ask_closed = 1;
			c->ask_unlocked = 0;
			c->ask_pending = c->ask_refresh = c->ask_cancel_pending = 0;
		}
	} else if (json_str_is(op, "ask.state")) {
		v = json_get(root, "lock");
		safe = json_only_keys(root, state) && ask_token(v, 32, "_-") &&
		    ask_token(json_get(root, "codex"), 32, "_-") &&
		    ask_token(json_get(root, "claude"), 32, "_-") &&
		    ask_token(json_get(root, "reason"), 32, "_-");
		if (safe && !json_str_is(v, "unlocked"))
			c->ask_unlocked = 0;
		if (safe && (json_str_is(v, "offline") || json_str_is(v, "unsupported")))
			c->ask_pending = c->ask_refresh = c->ask_cancel_pending = 0;
	} else if (json_str_is(op, "ask.capabilities")) {
		v = json_get(root, "tuples");
		if (!json_only_keys(root, capabilities) || !c->ask_unlocked ||
		    c->ask_pending != UI_ASK_CAPABILITIES ||
		    v == NULL || v->type != J_ARR || v->count > 128)
			goto done;
		for (row = v->first; row != NULL; row = row->next) {
			engine = json_get(row, "engine");
			billing = json_get(row, "billing");
			preferred = json_get(row, "preferred");
			if (!json_only_keys(row, choice) ||
			    (!json_str_is(engine, "codex") && !json_str_is(engine, "claude")) ||
			    !ask_token(json_get(row, "model"), 64, "._:/-") ||
			    !ask_token(json_get(row, "effort"), 32, "-") ||
			    !json_str_is(billing, json_str_is(engine, "codex") ? "" : "plan") ||
			    preferred == NULL || (preferred->type != J_TRUE && preferred->type != J_FALSE))
				goto done;
		}
		safe = 1;
	} else if (json_str_is(op, "ask.codex.challenge")) {
		v = json_get(root, "code");
		safe = c->ask_unlocked && c->ask_pending == UI_ASK_CODEX_LOGIN_BEGIN &&
		    json_only_keys(root, challenge) &&
		    json_str_is(json_get(root, "url"), "https://auth.openai.com/codex/device") &&
		    ask_token(v, 32, "-") && v->n >= 4;
	} else if (json_str_is(op, "ask.codex.state")) {
		safe = c->ask_unlocked && json_only_keys(root, codex_state) &&
		    ask_token(json_get(root, "state"), 32, "_-");
		completes = c->ask_pending == UI_ASK_CODEX_LOGIN_BEGIN;
		if (safe && !completes)
			c->ask_cancel_pending = 0;
	}
 done:
	json_free(root);
	if (safe && completes) {
		if (c->ask_pending)
			c->ask_pending = 0;
		else if (c->ask_refresh)
			c->ask_refresh = 0;
		else
			c->ask_cancel_pending = 0;
	}
	return safe;
}

static void
dispatch_link(int li)
{
	struct imsg		 imsg;
	struct ibuf		 ib;
	struct ui_event_hdr	 h;
	struct client		*c;
	struct usess		*s;
	struct dbuf		 d = { 0 };
	int			 n;

	for (;;) {
		if ((n = imsgbuf_get(&U.link[li], &imsg)) == -1)
			fatalx("ui: link imsg");
		if (n == 0)
			return;
		switch (imsg_get_type(&imsg)) {
		case IMSG_UI_LINKSTATE:
			if (imsg_get_data(&imsg, &U.ls[li],
			    sizeof(U.ls[li])) == -1)
				fatalx("ui: link state");
			broadcast_link(li);
			break;
		case IMSG_UI_EVENT:
			if (imsg_get_ibuf(&imsg, &ib) == -1 ||
			    ibuf_get(&ib, &h, sizeof(h)) == -1)
				fatalx("ui: event");
			if ((c = client_find(h.client)) != NULL) {
				dbuf_add(&d, ibuf_data(&ib), ibuf_size(&ib));
				client_write(c, &d);
				dbuf_free(&d);
			}
			break;
		case IMSG_UI_ASK_EVENT:
			if (imsg_get_ibuf(&imsg, &ib) == -1 ||
			    ibuf_get(&ib, &h, sizeof(h)) == -1)
				fatalx("ui: ask event");
			if ((c = client_find(h.client)) == NULL || c->ask_link != li ||
			    !U.cfg.ask_owner_set || c->uid != U.cfg.ask_owner_uid)
				break;
			if (!ask_event_safe(c, ibuf_data(&ib), ibuf_size(&ib))) {
				c->dead = 1;
				break;
			}
			if (dbuf_add(&d, ibuf_data(&ib), ibuf_size(&ib)) == -1)
				c->dead = 1;
			else
				client_write(c, &d);
			dbuf_free(&d);
			break;
		case IMSG_UI_SESSION_END:
			if (imsg_get_data(&imsg, &h, sizeof(h)) == -1)
				fatalx("ui: session end");
			h.session[sizeof(h.session) - 1] = '\0';
			TAILQ_FOREACH(s, &U.sessions, entry)
				if (s->client == h.client &&
				    strcmp(s->id, h.session) == 0) {
					TAILQ_REMOVE(&U.sessions, s, entry);
					free(s);
					break;
				}
			break;
		default:
			fatalx("ui: unexpected link message");
		}
		imsg_free(&imsg);
	}
}

__dead void
ui_main(void)
{
	struct pollfd	 pfd[2 + CHATD_MAX_LINKS + UI_MAX_CLIENTS];
	uint32_t	 ids[2 + CHATD_MAX_LINKS + UI_MAX_CLIENTS];
	struct imsg	 imsg;
	struct client	*c;
	int		 n, i, nfds, go = 0;

	log_init(1, 0, "d32-chatd ui");
	memset(&U, 0, sizeof(U));
	TAILQ_INIT(&U.clients);
	TAILQ_INIT(&U.sessions);
	if (imsgbuf_init(&U.parent, CHATD_FD_PARENT) == -1)
		fatalx("ui: imsgbuf");
	while (!go) {
		pfd[0].fd = CHATD_FD_PARENT;
		pfd[0].events = POLLIN;
		if (poll(pfd, 1, -1) == -1 && errno != EINTR)
			fatalx("ui: poll");
		if ((n = imsgbuf_read(&U.parent)) == -1)
			fatalx("ui: read");
		if (n == 0)
			exit(0);
		while ((n = imsgbuf_get(&U.parent, &imsg)) == 1) {
			if (imsg_get_type(&imsg) == IMSG_UI_INIT) {
				if (imsg_get_data(&imsg, &U.cfg,
				    sizeof(U.cfg)) == -1)
					fatalx("ui: init");
			} else if (imsg_get_type(&imsg) == IMSG_UI_GO)
				go = 1;
			imsg_free(&imsg);
		}
	}
	log_setverbose(U.cfg.verbose);
	if (U.cfg.nlinks < 1 || U.cfg.nlinks > CHATD_MAX_LINKS)
		fatalx("ui: links");
	if (U.cfg.ask_owner_set && (U.cfg.ask_owner_uid == 0 ||
	    U.cfg.ask_owner_uid == U.cfg.uid || U.cfg.ask_link < 0 ||
	    U.cfg.ask_link >= U.cfg.nlinks))
		fatalx("ui: ask owner configuration");
	for (i = 0; i < U.cfg.nlinks; i++) {
		if (imsgbuf_init(&U.link[i], CHATD_FD_LINKS + i) == -1 ||
		    imsgbuf_set_maxsize(&U.link[i], CHATD_IMSG_MAX) == -1)
			fatalx("ui: link imsgbuf");
		strlcpy(U.ls[i].name, U.cfg.names[i], sizeof(U.ls[i].name));
	}
	proc_drop(U.cfg.uid, U.cfg.gid);
	if (pledge("stdio unix", NULL) == -1)
		fatalx("ui: pledge");
	log_info("ui: running as uid %d, chroot /var/empty, pledge stdio unix",
	    (int)getuid());
	signal(SIGPIPE, SIG_IGN);

	for (;;) {
		nfds = 0;
		pfd[nfds].fd = CHATD_FD_PARENT;
		pfd[nfds].events = POLLIN;
		ids[nfds++] = 0;
		pfd[nfds].fd = CHATD_FD_ROLE;
		pfd[nfds].events = U.nclients < UI_MAX_CLIENTS ? POLLIN : 0;
		ids[nfds++] = 0;
		for (i = 0; i < U.cfg.nlinks; i++) {
			pfd[nfds].fd = CHATD_FD_LINKS + i;
			pfd[nfds].events = POLLIN |
			    (imsgbuf_queuelen(&U.link[i]) ? POLLOUT : 0);
			ids[nfds++] = 0;
		}
		TAILQ_FOREACH(c, &U.clients, entry) {
			pfd[nfds].fd = c->fd;
			pfd[nfds].events = POLLIN | (c->out.len ? POLLOUT : 0);
			ids[nfds++] = c->id;
		}
		if (poll(pfd, nfds, 1000) == -1) {
			if (errno == EINTR)
				continue;
			fatalx("ui: poll errno %d", errno);
		}
		if (pfd[0].revents & (POLLIN | POLLHUP)) {
			if ((n = imsgbuf_read(&U.parent)) <= 0)
				exit(0);
			while ((n = imsgbuf_get(&U.parent, &imsg)) == 1)
				imsg_free(&imsg);
		}
		if (pfd[1].revents & POLLIN)
			accept_client();
		for (i = 0; i < U.cfg.nlinks; i++) {
			if (pfd[2 + i].revents & (POLLIN | POLLHUP)) {
				if ((n = imsgbuf_read(&U.link[i])) == -1)
					fatalx("ui: link read");
				if (n == 0)
					fatalx("ui: link %d went away", i);
				dispatch_link(i);
			}
		}
		for (i = 2 + U.cfg.nlinks; i < nfds; i++) {
			if ((c = client_find(ids[i])) == NULL)
				continue;
			if (pfd[i].revents & (POLLIN | POLLHUP))
				client_read(c);
			if (!c->dead && (pfd[i].revents & POLLOUT))
				client_flush(c);
		}
		TAILQ_FOREACH(c, &U.clients, entry)
			if (!c->dead && c->out.len)
				client_flush(c);
		client_reap();
		for (i = 0; i < U.cfg.nlinks; i++)
			if (imsgbuf_queuelen(&U.link[i]) &&
			    imsgbuf_write(&U.link[i]) == -1)
				fatalx("ui: link write");
	}
}
