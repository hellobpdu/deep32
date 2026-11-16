/*
 * The ui child: the local API for Deep32 Library's Ask room (the UI process
 * has no network).  Newline-delimited strict JSON over the UNIX stream
 * socket /var/run/d32/uplink.sock (0660 root:_d32users, FIX-16).
 *
 * Requests (one JSON object per line):
 *   {"op":"status"}
 *   {"op":"session.open"[,"link":"work"][,"model":M][,"effort":E]}
 *   {"op":"turn.start","session":S,"text":T}
 *   {"op":"turn.interrupt","session":S}
 *   {"op":"session.close","session":S}
 *   {"op":"channel.open"[,"link":"work"],"channel":"seal"|"lib"|"sync"}
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

static void
handle_line(struct client *c, const uint8_t *line, size_t len)
{
	static const char *const k_status[] = { "op", NULL };
	static const char *const k_open[] = { "op", "link", "model", "effort",
	    NULL };
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
	int			 li, i;

	memset(&r, 0, sizeof(r));
	r.client = c->id;
	if (json_parse(line, len, &root) == -1 || root->type != J_OBJ ||
	    (op = json_get(root, "op")) == NULL) {
		client_error(c, E_PROTOCOL);
		goto done;
	}
	if (json_str_is(op, "status")) {
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
		if (!json_only_keys(root, k_open) ||
		    (li = link_by_name(json_get(root, "link"))) < 0 ||
		    copy_opt(json_get(root, "model"), r.model,
		    sizeof(r.model)) == -1 ||
		    copy_opt(json_get(root, "effort"), r.effort,
		    sizeof(r.effort)) == -1) {
			client_error(c, E_PROTOCOL);
			goto done;
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
		if (!json_only_keys(root, k_turn) ||
		    (us = usess_find(json_get(root, "session"), c->id)) ==
		    NULL || (v = json_get(root, "text")) == NULL ||
		    v->type != J_STR || v->n == 0 || v->n > D32U_REQ_MAX) {
			client_error(c, E_SESSION);
			goto done;
		}
		strlcpy(r.session, us->id, sizeof(r.session));
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
		c->dead = 1;
		return;
	}
	while ((nl = memchr(c->in.b, '\n', c->in.len)) != NULL) {
		l = nl - c->in.b;
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
	c->id = ++U.next_id;
	TAILQ_INSERT_TAIL(&U.clients, c, entry);
	U.nclients++;
	log_info("ui: client %u connected (uid %d)", c->id, (int)uid);
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
