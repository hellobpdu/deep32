/*
 * The link child: one D32U/1 connection to one paired relay or hub.
 *
 * The node alone dials.  TLS 1.3 (libtls) with mutual certificates; the
 * relay's complete-DER SHA-256 pin, bound to its HELLO id, is the only
 * trust anchor.  Unknown certificates are accepted only inside a pairing
 * window opened at the node's console, and only for one attempt; then the
 * ten-word ceremony of sas.md runs before any OPEN.
 *
 * Channels on this link: codex-ctl (node-opened), mcp (relay-opened,
 * forwarded to the node's local deep32-gateway for the eleven read tools only),
 * status (either).  seal, lib, sync and every unknown channel are refused.
 */
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/queue.h>
#include <netinet/in.h>
#include <netinet/tcp.h>

#include <errno.h>
#include <imsg.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <tls.h>
#include <unistd.h>

#include "chatd.h"
#include "edge.h"
#include "msg.h"

#define ST_IDLE		0	/* no pin and no pairing window */
#define ST_WAIT		1	/* reconnect back-off */
#define ST_TCP		2
#define ST_TLS		3
#define ST_HELLO	4
#define ST_ACK		5
#define ST_PAIR		6
#define ST_ACTIVE	7

#define WBUF_SOFT	(256 * 1024)
#define MCP_PER_HOUR	300

struct omsg {
	TAILQ_ENTRY(omsg)	 entry;
	uint8_t			*b;
	size_t			 len;
	size_t			 off;
};
TAILQ_HEAD(omsgq, omsg);

struct session;

struct stream {
	TAILQ_ENTRY(stream)	 entry;
	uint32_t		 id;
	uint8_t			 ch;
	int			 acked;
	int			 closing;
	int			 got_request;
	int32_t			 scredit;
	int32_t			 rcredit;
	int64_t			 stall_since;
	struct dbuf		 rx;
	struct omsgq		 outq;
	struct session		*sess;
	/* mcp */
	struct edgecall		*call;
	struct dbuf		 reqid;
	int			 notif;
	int			 method;	/* index into mcp_methods */
	int			 tool;		/* index into mcp_tools or -1 */
};
TAILQ_HEAD(streamq, stream);

struct session {
	TAILQ_ENTRY(session)	 entry;
	char			 id[D32U_ID_MAX + 1];
	uint32_t		 client;
	struct stream		*ctl;
	int			 opened;
	int			 turn;
	int			 nmcp;
	int64_t			 win_start;
	int			 win_calls;
};
TAILQ_HEAD(sessionq, session);

static const char *const mcp_methods[] = { "initialize",
    "notifications/initialized", "tools/list", "tools/call", NULL };

static struct {
	struct link_init	 cfg;
	struct edgecfg		 edge;
	struct imsgbuf		 parent;
	struct imsgbuf		 ui;
	struct tls_config	*tcfg;
	struct tls		*tls;
	int			 fd;
	int			 state;
	char			*cert;
	char			*key;
	size_t			 certlen;
	size_t			 keylen;
	int			 have_pin;
	struct pin_rec		 pin;
	int64_t			 window_until;
	int			 window_armed;
	int			 window_used;
	int			 backoff;
	int64_t			 next_try;
	int64_t			 deadline;
	uint8_t			 node_nonce[D32U_NONCE_LEN];
	uint8_t			 relay_pin[D32U_PIN_LEN];
	struct peer_hello	 hello;
	int			 pinned_conn;
	int			 paired_local;
	/* pairing */
	char			 code[SAS_CODE_MAX];
	int			 local_ok;
	int			 remote_confirm;
	int			 remote_commit;
	int			 pin_requested;
	int			 sent_commit;
	/* io */
	uint8_t			*rbuf;
	size_t			 rlen;
	int64_t			 partial_since;
	struct dbuf		 wbuf;
	int			 want_out;	/* libtls asked for POLLOUT */
	uint32_t		 gen;		/* connection generation */
	/* mux */
	uint32_t		 next_odd;
	uint32_t		 highest_even;
	int			 used_sessions;
	int			 recycle;	/* all 16 used, none live */
	struct streamq		 streams;
	int			 nstreams;
	struct sessionq		 sessions;
	int			 nsessions;
	/* liveness */
	int64_t			 last_rx;
	int64_t			 last_ping;
	uint8_t			 pings[4][8];
	int			 npings;
	int64_t			 bucket;
	int			 frames_in_bucket;
	/* counters for the status view */
	int64_t			 turns;
	int64_t			 mcp_calls;
	int64_t			 mcp_refused;
	int64_t			 connects;
	int			 reported;
	int			 report_sessions;
} L;

static void	 conn_close(int, int);
static void	 send_frame(uint8_t, uint32_t, uint8_t, uint16_t,
		    const void *, size_t);
static void	 send_u32(uint8_t, uint32_t, uint8_t, uint32_t);
static void	 report(int);
static void	 stream_free(struct stream *);
static void	 stream_retire(struct stream *, int);
static void	 ui_error(uint32_t, const char *, int);
static void	 mcp_ledger(struct stream *, const char *, size_t, int64_t);

/* ---------- small helpers ---------- */

static int
ls_of(int st)
{
	switch (st) {
	case ST_IDLE:
		return L.have_pin ? LS_WAITING : LS_UNPAIRED;
	case ST_WAIT:
		return LS_WAITING;
	case ST_TCP:
		return LS_CONNECTING;
	case ST_TLS:
	case ST_HELLO:
	case ST_ACK:
		return LS_HANDSHAKE;
	case ST_PAIR:
		return LS_PAIRING;
	case ST_ACTIVE:
		return LS_ACTIVE;
	}
	return LS_UNPAIRED;
}

static void
set_state(int st)
{
	if (L.state != st)
		log_debug("link %d: state %d -> %d", L.cfg.index, L.state, st);
	L.state = st;
	report(0);
}

static struct stream *
stream_find(uint32_t id)
{
	struct stream	*s;

	TAILQ_FOREACH(s, &L.streams, entry)
		if (s->id == id)
			return s;
	return NULL;
}

static struct session *
session_find(const char *id)
{
	struct session	*se;

	TAILQ_FOREACH(se, &L.sessions, entry)
		if (strcmp(se->id, id) == 0)
			return se;
	return NULL;
}

static int
retired(uint32_t id)
{
	if (id & 1)
		return id < L.next_odd;
	return id <= L.highest_even;
}

static void
queue_msg(struct stream *s, struct dbuf *d)
{
	struct omsg	*m;

	if ((m = calloc(1, sizeof(*m))) == NULL)
		fatalx("link: out of memory");
	m->b = d->b;
	m->len = d->len;
	d->b = NULL;
	d->len = d->cap = 0;
	TAILQ_INSERT_TAIL(&s->outq, m, entry);
}

/* ---------- UI events ---------- */

static void
ui_send(uint32_t client, const char *sid, struct dbuf *line)
{
	struct ui_event_hdr	 h;
	struct ibuf		*ib;

	memset(&h, 0, sizeof(h));
	h.client = client;
	if (sid)
		strlcpy(h.session, sid, sizeof(h.session));
	if (dbuf_add(line, "\n", 1) == -1)
		goto done;
	if ((ib = imsg_create(&L.ui, IMSG_UI_EVENT, 0, 0,
	    sizeof(h) + line->len)) == NULL)
		goto done;
	if (imsg_add(ib, &h, sizeof(h)) == -1 ||
	    imsg_add(ib, line->b, line->len) == -1)
		goto done;
	imsg_close(&L.ui, ib);
 done:
	dbuf_free(line);
}

static void
ui_error(uint32_t client, const char *sid, int code)
{
	struct dbuf	 d = { 0 };

	if (sid && *sid) {
		dbuf_addstr(&d, "{\"op\":\"error\",\"session\":");
		json_put_str(&d, sid, strlen(sid));
		dbuf_printf(&d, ",\"code\":%d}", code);
	} else
		dbuf_printf(&d, "{\"op\":\"error\",\"code\":%d}", code);
	ui_send(client, sid, &d);
}

static void
ui_session_end(struct session *se)
{
	struct ui_event_hdr	 h;
	struct dbuf		 d = { 0 };

	dbuf_addstr(&d, "{\"op\":\"session.closed\",\"session\":");
	json_put_str(&d, se->id, strlen(se->id));
	dbuf_addstr(&d, "}");
	ui_send(se->client, se->id, &d);
	memset(&h, 0, sizeof(h));
	h.client = se->client;
	strlcpy(h.session, se->id, sizeof(h.session));
	imsg_compose(&L.ui, IMSG_UI_SESSION_END, 0, 0, -1, &h, sizeof(h));
}

/*
 * A session.open that never became a session: the UI gets the error and
 * session.closed, and the ui child drops the id it reserved (otherwise a
 * UI retrying while the link is down would grow that table for ever).
 */
static void
ui_open_refused(const struct ui_req *r, int code)
{
	struct ui_event_hdr	 h;
	struct dbuf		 d = { 0 };

	ui_error(r->client, r->session, code);
	dbuf_addstr(&d, "{\"op\":\"session.closed\",\"session\":");
	json_put_str(&d, r->session, strlen(r->session));
	dbuf_addstr(&d, "}");
	ui_send(r->client, r->session, &d);
	memset(&h, 0, sizeof(h));
	h.client = r->client;
	strlcpy(h.session, r->session, sizeof(h.session));
	imsg_compose(&L.ui, IMSG_UI_SESSION_END, 0, 0, -1, &h, sizeof(h));
}

/*
 * End a session.  No stream may keep a pointer to it afterwards, and no
 * mcp request of a dead session may reach the edge:
 *  - an mcp stream whose request already arrived (in flight at the edge,
 *    or answered) is RESET CANCELLED and freed now.  The relay sends
 *    nothing more on it but CLOSE/RESET/WINDOW_UPDATE, which are
 *    ignorable on a retired id, so the RESET cannot race its DATA;
 *  - an mcp stream still waiting for its request is detached (sess NULL).
 *    Its request is refused with RESET SESSION when it completes and is
 *    never forwarded.  Resetting it now could make the relay's in-flight
 *    request DATA arrive on a retired id, which d32u1.md makes fatal.
 */
static void
session_free(struct session *se, int notify_code)
{
	struct stream	*s, *ns;

	for (s = TAILQ_FIRST(&L.streams); s; s = ns) {
		ns = TAILQ_NEXT(s, entry);
		if (s->sess != se)
			continue;
		if (s->ch == CH_MCP && s->got_request) {
			if (s->call)
				mcp_ledger(s, "cancelled", 0,
				    now_ms() - s->call->started);
			if (L.tls && L.state == ST_ACTIVE)
				send_u32(T_RESET, s->id, s->ch, E_CANCELLED);
			stream_free(s);		/* drops se->nmcp */
		} else {
			if (s->ch == CH_MCP)
				se->nmcp--;
			s->sess = NULL;
		}
	}
	se->ctl = NULL;
	if (notify_code)
		ui_error(se->client, se->id, notify_code);
	ui_session_end(se);
	TAILQ_REMOVE(&L.sessions, se, entry);
	L.nsessions--;
	/*
	 * The relay counts sessions per connection (16 for its lifetime).
	 * Once the node has used them all and none is live, the link is
	 * recycled at once so that the next Ask conversation works.
	 */
	if (L.state == ST_ACTIVE && L.nsessions == 0 &&
	    L.used_sessions >= D32U_MAX_SESSIONS)
		L.recycle = 1;
	explicit_bzero(se, sizeof(*se));
	free(se);
	report(0);
}

/* ---------- framing out ---------- */

static void
send_frame(uint8_t type, uint32_t stream, uint8_t ch, uint16_t flags,
    const void *payload, size_t len)
{
	struct d32u_hdr	 h;
	uint8_t		 hb[D32U_HDR_LEN];

	h.len = len;
	h.stream = stream;
	h.type = type;
	h.channel = ch;
	h.flags = flags;
	if (d32u_hdr_check(&h) != 0)
		fatalx("link: refusing to send invalid frame type %u", type);
	d32u_hdr_write(&h, hb);
	if (dbuf_add(&L.wbuf, hb, sizeof(hb)) == -1 ||
	    dbuf_add(&L.wbuf, payload, len) == -1)
		fatalx("link: output buffer overflow");
	if (type != T_DATA)
		log_debug("link %d: tx type %u stream %u ch %u len %zu",
		    L.cfg.index, type, stream, ch, len);
}

static void
send_u32(uint8_t type, uint32_t stream, uint8_t ch, uint32_t v)
{
	uint8_t	 b[4];

	d32u_put32(b, v);
	send_frame(type, stream, ch, 0, b, 4);
}

static void
send_open(struct stream *s, const char *sess)
{
	struct dbuf	 d = { 0 };

	if (sess) {
		dbuf_addstr(&d, "{\"session\":");
		json_put_str(&d, sess, strlen(sess));
		dbuf_addstr(&d, "}");
	} else
		dbuf_addstr(&d, "{}");
	send_frame(T_OPEN, s->id, s->ch, 0, d.b, d.len);
	dbuf_free(&d);
}

/* Returns -1 on a write error; never closes the connection itself. */
static int
flush_quiet(void)
{
	ssize_t	 n;

	while (L.tls && L.wbuf.len > 0 && L.state >= ST_HELLO) {
		n = tls_write(L.tls, L.wbuf.b, L.wbuf.len);
		if (n == TLS_WANT_POLLOUT) {
			L.want_out = 1;
			return 0;
		}
		if (n == TLS_WANT_POLLIN)
			return 0;
		if (n == -1)
			return -1;
		dbuf_drop(&L.wbuf, n);
	}
	return 0;
}

static void
flush_net(void)
{
	if (flush_quiet() == -1) {
		log_warnx("link %d: write failed", L.cfg.index);
		conn_close(0, E_RELAY_LOST);
	}
}

/* Move queued DATA into the output, honouring credit and fragments. */
static void
pump(void)
{
	struct stream	*s;
	struct omsg	*m;
	size_t		 n;
	int		 progress = 1;

	if (L.state != ST_ACTIVE)
		return;
	while (progress && L.wbuf.len < WBUF_SOFT) {
		progress = 0;
		TAILQ_FOREACH(s, &L.streams, entry) {
			if ((m = TAILQ_FIRST(&s->outq)) == NULL || !s->acked)
				continue;
			if (s->scredit <= 0) {
				if (s->stall_since == 0)
					s->stall_since = now_ms();
				continue;
			}
			s->stall_since = 0;
			n = m->len - m->off;
			if (n > D32U_FRAGMENT)
				n = D32U_FRAGMENT;
			if (n > (size_t)s->scredit)
				n = s->scredit;
			send_frame(T_DATA, s->id, s->ch,
			    m->off + n == m->len ? F_END_MESSAGE : 0,
			    m->b + m->off, n);
			s->scredit -= n;
			m->off += n;
			if (m->off == m->len) {
				TAILQ_REMOVE(&s->outq, m, entry);
				freezero(m->b, m->len);
				free(m);
			}
			progress = 1;
			if (L.wbuf.len >= WBUF_SOFT)
				break;
		}
	}
}

/* ---------- streams and sessions ---------- */

static struct stream *
stream_new(uint32_t id, uint8_t ch)
{
	struct stream	*s;

	if ((s = calloc(1, sizeof(*s))) == NULL)
		fatalx("link: out of memory");
	s->id = id;
	s->ch = ch;
	s->scredit = D32U_WINDOW;
	s->rcredit = D32U_WINDOW;
	s->tool = -1;
	s->method = -1;
	TAILQ_INIT(&s->outq);
	TAILQ_INSERT_TAIL(&L.streams, s, entry);
	L.nstreams++;
	return s;
}

static void
stream_free(struct stream *s)
{
	struct omsg	*m;

	while ((m = TAILQ_FIRST(&s->outq)) != NULL) {
		TAILQ_REMOVE(&s->outq, m, entry);
		freezero(m->b, m->len);
		free(m);
	}
	if (s->call) {
		edge_free(s->call);
		free(s->call);
	}
	dbuf_free(&s->rx);
	dbuf_free(&s->reqid);
	if (s->sess) {
		if (s->ch == CH_MCP)
			s->sess->nmcp--;
		else if (s->ch == CH_CODEX_CTL)
			s->sess->ctl = NULL;
	}
	TAILQ_REMOVE(&L.streams, s, entry);
	L.nstreams--;
	free(s);
}

/* Discard a stream; a codex-ctl stream ends its session. */
static void
stream_retire(struct stream *s, int code)
{
	struct session	*se = NULL;

	if (s->ch == CH_CODEX_CTL)
		se = s->sess;
	stream_free(s);
	if (se)
		session_free(se, code);
}

static void
stream_reset(struct stream *s, uint32_t code)
{
	send_u32(T_RESET, s->id, s->ch, code);
	stream_retire(s, code);
}

/* ---------- MCP: the read-only query surface ---------- */

static void
mcp_error_body(struct dbuf *d, const struct stream *s, int code)
{
	dbuf_addstr(d, "{\"jsonrpc\":\"2.0\",\"id\":");
	if (s->reqid.len)
		dbuf_add(d, s->reqid.b, s->reqid.len);
	else
		dbuf_addstr(d, "null");
	dbuf_printf(d, ",\"error\":{\"code\":%d,\"message\":\"%s\"}}", code,
	    code == -32601 ? "Unavailable" : "Edge unavailable");
}

static void
mcp_ledger(struct stream *s, const char *result, size_t bytes, int64_t ms)
{
	struct dbuf	 d = { 0 };

	if (s->sess == NULL)
		return;
	dbuf_addstr(&d, "{\"op\":\"mcp\",\"session\":");
	json_put_str(&d, s->sess->id, strlen(s->sess->id));
	dbuf_printf(&d, ",\"method\":\"%s\",\"tool\":\"%s\",\"result\":\"%s\","
	    "\"bytes\":%zu,\"ms\":%lld}",
	    s->method >= 0 ? mcp_methods[s->method] : "other",
	    s->tool >= 0 ? mcp_tools[s->tool] : (s->method == 3 ?
	    "other" : ""), result, bytes, (long long)ms);
	ui_send(s->sess->client, s->sess->id, &d);
}

static void
mcp_reply(struct stream *s, int http, const struct dbuf *body,
    const char *sid)
{
	struct dbuf	 d = { 0 }, err = { 0 };

	dbuf_addstr(&d, "{\"body\":");
	if (body && body->len)
		dbuf_add(&d, body->b, body->len);
	else
		dbuf_addstr(&d, "null");
	dbuf_addstr(&d, ",\"mcp_session_id\":");
	if (sid && *sid)
		json_put_str(&d, sid, strlen(sid));
	else
		dbuf_addstr(&d, "null");
	dbuf_printf(&d, ",\"http_status\":%d}", http);
	if (d.len > D32U_RESP_MAX) {
		dbuf_free(&d);
		mcp_error_body(&err, s, -32603);
		mcp_reply(s, 200, &err, NULL);
		dbuf_free(&err);
		return;
	}
	queue_msg(s, &d);
}

static void
mcp_refuse(struct stream *s)
{
	struct dbuf	 body = { 0 };

	L.mcp_refused++;
	log_info("link %d: mcp method/tool refused locally (-32601)",
	    L.cfg.index);
	if (s->notif) {
		mcp_reply(s, 202, NULL, NULL);
	} else {
		mcp_error_body(&body, s, -32601);
		mcp_reply(s, 200, &body, NULL);
		dbuf_free(&body);
	}
	mcp_ledger(s, "refused", 0, 0);
}

/* tools/list: keep only the eleven read tools in the edge's answer. */
static void
filter_tools(struct jval *root)
{
	const struct jval	*res, *tools;
	struct jval		*t, *prev = NULL, *next;
	const struct jval	*name;

	if ((res = json_get(root, "result")) == NULL ||
	    (tools = json_get(res, "tools")) == NULL || tools->type != J_ARR)
		return;
	for (t = tools->first; t; t = next) {
		next = t->next;
		name = json_get(t, "name");
		if (name && name->type == J_STR &&
		    mcp_tool_index(name->s, name->n) >= 0) {
			prev = t;
			continue;
		}
		if (prev)
			prev->next = next;
		else
			((struct jval *)tools)->first = next;
		if (((struct jval *)tools)->last == t)
			((struct jval *)tools)->last = prev;
		((struct jval *)tools)->count--;
		t->next = NULL;
		json_free(t);
	}
}

static void
mcp_finish(struct stream *s)
{
	struct edgecall	*ec = s->call;
	struct jval	*root = NULL;
	struct dbuf	 body = { 0 }, rid = { 0 };
	const struct jval *v;
	int64_t		 ms = now_ms() - ec->started;
	int		 ok = 0;

	if (s->notif) {
		mcp_reply(s, 202, NULL, NULL);
		mcp_ledger(s, ec->state == EC_DONE ? "ok" : "error", 0, ms);
		goto done;
	}
	if (ec->state == EC_DONE && ec->http_status == 202 &&
	    ec->body.len == 0) {
		mcp_reply(s, 202, NULL, ec->sid);
		mcp_ledger(s, "ok", 0, ms);
		goto done;
	}
	if (ec->state == EC_DONE && ec->http_status == 200 &&
	    json_parse(ec->body.b, ec->body.len, &root) == 0 &&
	    root->type == J_OBJ &&
	    json_str_is(json_get(root, "jsonrpc"), "2.0") &&
	    (v = json_get(root, "id")) != NULL && json_write(&rid, v) == 0 &&
	    rid.len == s->reqid.len &&
	    memcmp(rid.b, s->reqid.b, rid.len) == 0) {
		if (s->method == 2)
			filter_tools(root);
		if (json_write(&body, root) == 0)
			ok = 1;
	}
	if (ok) {
		mcp_reply(s, 200, &body, ec->sid);
		mcp_ledger(s, "ok", body.len, ms);
	} else {
		dbuf_reset(&body);
		mcp_error_body(&body, s, -32603);
		mcp_reply(s, 200, &body, NULL);
		mcp_ledger(s, "error", 0, ms);
		log_warnx("link %d: edge call failed (state %d, http %d)",
		    L.cfg.index, ec->state, ec->http_status);
	}
 done:
	json_free(root);
	dbuf_free(&body);
	dbuf_free(&rid);
	edge_free(ec);
	free(ec);
	s->call = NULL;
}

static int
method_index(const struct jval *m)
{
	int	 i;

	for (i = 0; mcp_methods[i]; i++)
		if (json_str_is(m, mcp_methods[i]))
			return i;
	return -1;
}

static int
mcp_request(struct stream *s, const struct jval *msg)
{
	struct mcp_request	 r;
	const struct jval	*id, *m, *params, *name;
	struct dbuf		 body = { 0 };
	struct session		*se = s->sess;
	int64_t			 now = now_ms();

	if (msg_mcp_request(msg, &r) != 0 || s->got_request)
		return E_PROTOCOL;	/* one request per mcp stream */
	if (se == NULL) {
		/* its session ended while the request was on its way */
		log_info("link %d: mcp request for an ended session refused "
		    "(RESET 7)", L.cfg.index);
		stream_reset(s, E_SESSION);
		return 0;
	}
	s->got_request = 1;
	if (r.body == NULL) {
		mcp_reply(s, 202, NULL, NULL);
		return 0;
	}
	id = json_get(r.body, "id");
	s->notif = id == NULL;
	if (id && json_write(&s->reqid, id) == -1)
		return E_PROTOCOL;
	m = json_get(r.body, "method");
	s->method = method_index(m);
	if (s->method == 3) {
		params = json_get(r.body, "params");
		name = json_get(params, "name");
		if (name && name->type == J_STR)
			s->tool = mcp_tool_index(name->s, name->n);
	}
	L.mcp_calls++;
	/*
	 * Allow-list: initialize, notifications/initialized, tools/list and
	 * tools/call of the eleven read tools.  Everything else (admin tools,
	 * other methods, malformed JSON-RPC) is answered here and never
	 * reaches the edge.
	 */
	if (!json_str_is(json_get(r.body, "jsonrpc"), "2.0") ||
	    s->method < 0 || (s->method == 3 && s->tool < 0) ||
	    (s->method == 1) != s->notif) {
		mcp_refuse(s);
		return 0;
	}
	if (now - se->win_start >= 3600 * 1000) {
		se->win_start = now;
		se->win_calls = 0;
	}
	if (++se->win_calls > MCP_PER_HOUR) {
		struct dbuf eb = { 0 };

		log_warnx("link %d: session over its hourly MCP budget",
		    L.cfg.index);
		mcp_error_body(&eb, s, -32603);
		mcp_reply(s, 200, &eb, NULL);
		dbuf_free(&eb);
		mcp_ledger(s, "budget", 0, 0);
		return 0;
	}
	if (json_write(&body, r.body) == -1 || body.len > D32U_REQ_MAX) {
		dbuf_free(&body);
		return E_LIMIT;
	}
	if ((s->call = calloc(1, sizeof(*s->call))) == NULL)
		fatalx("link: out of memory");
	if (edge_start(s->call, &L.edge, &body, r.sid, r.sidlen, r.pver,
	    r.pverlen, now) == -1)
		mcp_finish(s);
	dbuf_free(&body);
	return 0;
}

/* ---------- codex-ctl events ---------- */

static int
ctl_event(struct stream *s, const struct jval *msg)
{
	struct ctl_event	 ev;
	struct session		*se = s->sess;
	struct dbuf		 d = { 0 };
	char			*text = NULL;
	size_t			 tl;
	static const char *const ts[] = { "", "completed", "interrupted",
	    "failed" };

	if (msg_event(msg, &ev) != 0)
		return E_PROTOCOL;
	if (se == NULL)
		return 0;	/* session already ended locally */
	if (ev.sessionlen != strlen(se->id) ||
	    memcmp(ev.session, se->id, ev.sessionlen) != 0)
		return E_PROTOCOL;	/* relay repeats the session id */
	switch (ev.op) {
	case EV_OPENED:
		if (se->opened)
			return E_PROTOCOL;
		se->opened = 1;
		dbuf_addstr(&d, "{\"op\":\"session.opened\",\"session\":");
		json_put_str(&d, se->id, strlen(se->id));
		dbuf_addstr(&d, "}");
		break;
	case EV_DELTA:
	case EV_FINAL:
		if ((text = malloc(ev.textlen + 1)) == NULL)
			fatalx("link: out of memory");
		memcpy(text, ev.text, ev.textlen);
		tl = text_sanitise(text, ev.textlen);
		dbuf_printf(&d, "{\"op\":\"%s\",\"session\":",
		    ev.op == EV_DELTA ? "agent.delta" : "agent.final");
		json_put_str(&d, se->id, strlen(se->id));
		dbuf_addstr(&d, ",\"text\":");
		json_put_str(&d, text, tl);
		dbuf_addstr(&d, "}");
		free(text);
		break;
	case EV_COMPLETED:
		se->turn = 0;
		dbuf_addstr(&d, "{\"op\":\"turn.completed\",\"session\":");
		json_put_str(&d, se->id, strlen(se->id));
		dbuf_printf(&d, ",\"status\":\"%s\",\"usage\":{\"input_tokens\":"
		    "%lld,\"output_tokens\":%lld}}", ts[ev.status],
		    (long long)ev.in_tokens, (long long)ev.out_tokens);
		break;
	case EV_ERROR:
		se->turn = 0;
		log_info("link %d: relay error event code %d", L.cfg.index,
		    ev.code);
		ui_error(se->client, se->id, ev.code);
		if (!se->opened) {
			/* refused session.open: end the stream bilaterally */
			send_frame(T_CLOSE, s->id, s->ch, 0, NULL, 0);
			s->closing = 1;
		}
		return 0;
	case EV_STATUS:
		dbuf_addstr(&d, "{\"op\":\"status\",\"session\":");
		json_put_str(&d, se->id, strlen(se->id));
		dbuf_printf(&d, ",\"code\":\"%s\"}", status_codes[ev.code]);
		break;
	}
	ui_send(se->client, se->id, &d);
	return 0;
}

/* ---------- frame handling ---------- */

static int
handle_open(const struct d32u_hdr *h, const uint8_t *p)
{
	struct stream	*s;
	struct session	*se;
	char		 sess[D32U_ID_MAX + 1];
	int		 e;

	/*
	 * Forbidden channels get RESET 3 before their body or ownership is
	 * examined: seal, lib, sync and anything unknown, on every link of
	 * this v1 contract (hub payloads are reserved).
	 */
	if (h->channel != CH_CODEX_CTL && h->channel != CH_MCP &&
	    h->channel != CH_STATUS) {
		if (!(h->stream & 1) && h->stream > L.highest_even)
			L.highest_even = h->stream;
		log_info("link %d: refused relay OPEN on channel %u (RESET 3)",
		    L.cfg.index, h->channel);
		send_u32(T_RESET, h->stream, h->channel, E_CHANNEL_FORBIDDEN);
		return 0;
	}
	if (h->channel == CH_CODEX_CTL || (h->stream & 1) ||
	    h->stream <= L.highest_even)
		return E_PROTOCOL;	/* wrong opener, parity or order */
	L.highest_even = h->stream;
	if (L.nstreams >= D32U_MAX_STREAMS) {
		send_u32(T_RESET, h->stream, h->channel, E_LIMIT);
		return 0;
	}
	if (h->channel == CH_STATUS) {
		if ((e = msg_open(p, h->len, 0, NULL, 0)) != 0)
			return e;
		s = stream_new(h->stream, h->channel);
		s->acked = 1;
		send_frame(T_OPEN_ACK, s->id, s->ch, 0, NULL, 0);
		return 0;
	}
	if ((e = msg_open(p, h->len, 1, sess, sizeof(sess))) != 0)
		return e;
	if ((se = session_find(sess)) == NULL || !se->opened) {
		send_u32(T_RESET, h->stream, h->channel, E_SESSION);
		return 0;
	}
	if (se->nmcp >= D32U_MAX_MCP) {
		send_u32(T_RESET, h->stream, h->channel, E_LIMIT);
		return 0;
	}
	s = stream_new(h->stream, h->channel);
	s->sess = se;
	se->nmcp++;
	s->acked = 1;
	send_frame(T_OPEN_ACK, s->id, s->ch, 0, NULL, 0);
	return 0;
}

static int
handle_data(struct stream *s, const struct d32u_hdr *h, const uint8_t *p)
{
	struct jval	*msg = NULL;
	size_t		 limit;
	int		 e = 0, code;
	struct dbuf	 d = { 0 };

	if (!s->acked)
		return E_PROTOCOL;
	if ((int64_t)h->len > s->rcredit)
		return E_FLOW_CONTROL;
	s->rcredit -= h->len;
	limit = s->ch == CH_CODEX_CTL ? D32U_RESP_MAX : D32U_REQ_MAX;
	if (s->rx.len + h->len > limit) {
		stream_reset(s, E_LIMIT);
		return 0;
	}
	if (dbuf_add(&s->rx, p, h->len) == -1)
		fatalx("link: out of memory");
	/* the bytes are now in the bounded message buffer: return credit */
	s->rcredit += h->len;
	send_u32(T_WINDOW, s->id, s->ch, h->len);
	if (!(h->flags & F_END_MESSAGE))
		return 0;
	if (json_parse(s->rx.b, s->rx.len, &msg) == -1) {
		dbuf_reset(&s->rx);
		return E_PROTOCOL;
	}
	dbuf_reset(&s->rx);
	switch (s->ch) {
	case CH_CODEX_CTL:
		e = ctl_event(s, msg);
		break;
	case CH_MCP:
		e = mcp_request(s, msg);
		break;
	case CH_STATUS:
		if ((e = msg_status(msg, &code)) == 0 && !(s->id & 1)) {
			dbuf_addstr(&d, "{\"code\":\"CONNECTED\"}");
			queue_msg(s, &d);
		}
		break;
	}
	json_free(msg);
	return e;
}

static int
handle_active(const struct d32u_hdr *h, const uint8_t *p)
{
	struct stream	*s;
	uint32_t	 n;
	int		 i;

	switch (h->type) {
	case T_PING:
		send_frame(T_PONG, 0, 0, 0, p, 8);
		return 0;
	case T_PONG:
		for (i = 0; i < L.npings; i++)
			if (memcmp(L.pings[i], p, 8) == 0) {
				memmove(L.pings[i], L.pings[i + 1],
				    (L.npings - i - 1) * 8);
				L.npings--;
				return 0;
			}
		return E_PROTOCOL;	/* unmatched PONG */
	case T_OPEN:
		return handle_open(h, p);
	case T_HELLO:
	case T_HELLO_ACK:
	case T_PAIR_CONFIRM:
	case T_PAIR_COMMIT:
		return E_PROTOCOL;
	}
	if ((s = stream_find(h->stream)) == NULL) {
		if (retired(h->stream) && (h->type == T_CLOSE ||
		    h->type == T_RESET || h->type == T_WINDOW))
			return 0;	/* late control on a retired id */
		return E_PROTOCOL;
	}
	if (h->channel != s->ch)
		return E_PROTOCOL;
	switch (h->type) {
	case T_OPEN_ACK:
		if (s->acked || !(s->id & 1))
			return E_PROTOCOL;
		s->acked = 1;
		return 0;
	case T_WINDOW:
		n = d32u_get32(p);
		if (!s->acked || n == 0 || n > D32U_WINDOW ||
		    (int64_t)s->scredit + n > D32U_WINDOW)
			return E_FLOW_CONTROL;
		s->scredit += n;
		return 0;
	case T_CLOSE:
		if (!s->closing)
			send_frame(T_CLOSE, s->id, s->ch, 0, NULL, 0);
		stream_retire(s, 0);
		return 0;
	case T_RESET:
		n = d32u_get32(p);
		log_info("link %d: relay RESET stream %u ch %u code %u",
		    L.cfg.index, h->stream, h->channel, n);
		stream_retire(s, n >= 1 && n <= 11 ? (int)n : E_PROTOCOL);
		return 0;
	case T_DATA:
		return handle_data(s, h, p);
	}
	return E_PROTOCOL;
}

/* ---------- handshake and pairing ---------- */

static void
pair_result(int ok, int code)
{
	struct pair_result	 r = { ok, code };

	imsg_compose(&L.parent, IMSG_PAIR_RESULT, 0, 0, -1, &r, sizeof(r));
}

static void
pair_clear(void)
{
	explicit_bzero(L.code, sizeof(L.code));
	explicit_bzero(L.node_nonce, sizeof(L.node_nonce));
	explicit_bzero(L.hello.nonce, sizeof(L.hello.nonce));
	L.local_ok = L.remote_confirm = L.remote_commit = 0;
	L.pin_requested = L.sent_commit = 0;
}

static void
go_active(void)
{
	L.last_rx = L.last_ping = now_ms();
	L.npings = 0;
	L.next_odd = 1;
	L.highest_even = 0;
	L.used_sessions = 0;
	L.recycle = 0;
	L.backoff = 0;
	L.connects++;
	pair_clear();
	set_state(ST_ACTIVE);
	log_info("link %d: ACTIVE (pinned relay, TLS 1.3, mutual)",
	    L.cfg.index);
}

static void
pair_progress(void)
{
	struct pin_rec	 r;

	if (L.local_ok && L.remote_confirm && !L.pin_requested) {
		/* both confirmations: the parent persists pin + HELLO id */
		memset(&r, 0, sizeof(r));
		strlcpy(r.relay_id, L.hello.id, sizeof(r.relay_id));
		memcpy(r.pin, L.relay_pin, sizeof(r.pin));
		imsg_compose(&L.parent, IMSG_PIN_SAVE, 0, 0, -1, &r,
		    sizeof(r));
		L.pin_requested = 1;
	}
	if (L.sent_commit && L.remote_commit) {
		pair_result(1, 0);
		go_active();
	}
}

static int
handle_pairing(const struct d32u_hdr *h)
{
	switch (h->type) {
	case T_PAIR_CONFIRM:
		if (L.remote_confirm)
			return E_PROTOCOL;
		L.remote_confirm = 1;
		log_info("link %d: relay confirmed its typed words",
		    L.cfg.index);
		pair_progress();
		return 0;
	case T_PAIR_COMMIT:
		if (!L.local_ok || !L.remote_confirm || L.remote_commit)
			return E_PROTOCOL;
		L.remote_commit = 1;
		pair_progress();
		return 0;
	}
	return E_PROTOCOL;	/* nothing else is legal before ACTIVE */
}

static int
handle_handshake(const struct d32u_hdr *h, const uint8_t *p)
{
	struct dbuf	 d = { 0 };
	int		 e, paired_remote;
	uint8_t		 nonce[2 * D32U_NONCE_LEN];

	if (L.state == ST_HELLO) {
		if (h->type != T_HELLO)
			return E_PROTOCOL;
		if ((e = msg_hello(p, h->len, &L.hello)) != 0)
			return e;
		if (L.pinned_conn) {
			if (strcmp(L.hello.id, L.pin.relay_id) != 0) {
				log_warnx("link %d: pinned certificate with a "
				    "different HELLO id: refused", L.cfg.index);
				return E_AUTH;
			}
			L.paired_local = 1;
		} else
			L.paired_local = 0;
		msg_build_hello_ack(&d, L.paired_local);
		send_frame(T_HELLO_ACK, 0, 0, 0, d.b, d.len);
		dbuf_free(&d);
		L.deadline = now_ms() + D32U_HANDSHAKE_SEC * 1000;
		set_state(ST_ACK);
		return 0;
	}
	if (L.state == ST_ACK) {
		if (h->type != T_HELLO_ACK)
			return E_PROTOCOL;
		if ((e = msg_hello_ack(p, h->len, &paired_remote)) != 0)
			return e;
		if (L.paired_local && paired_remote) {
			go_active();
			return 0;
		}
		/* either side unpaired: a new two-console ceremony */
		if (!L.window_used) {
			log_warnx("link %d: pairing needed but no local window "
			    "open (paired local %d remote %d)", L.cfg.index,
			    L.paired_local, paired_remote);
			return E_AUTH;
		}
		L.window_armed = 0;
		L.window_used = 1;
		memcpy(nonce, L.node_nonce, D32U_NONCE_LEN);
		memcpy(nonce + D32U_NONCE_LEN, L.hello.nonce, D32U_NONCE_LEN);
		e = sas_code(L.cfg.node_pin, L.relay_pin, nonce, L.code,
		    sizeof(L.code));
		if (e == -1) {
			explicit_bzero(nonce, sizeof(nonce));
			return E_AUTH;	/* equal pins: identity reflection */
		}
		{
			/* the parent computes, shows and checks the words
			 * itself from this offer */
			struct pair_offer po;

			memset(&po, 0, sizeof(po));
			strlcpy(po.relay_id, L.hello.id, sizeof(po.relay_id));
			memcpy(po.relay_pin, L.relay_pin, sizeof(po.relay_pin));
			memcpy(po.nonce, nonce, sizeof(po.nonce));
			imsg_compose(&L.parent, IMSG_PAIR_CODE, 0, 0, -1, &po,
			    sizeof(po));
			explicit_bzero(&po, sizeof(po));
		}
		explicit_bzero(nonce, sizeof(nonce));
		L.deadline = L.window_until;
		set_state(ST_PAIR);
		log_info("link %d: pairing ceremony started", L.cfg.index);
		return 0;
	}
	if (L.state == ST_PAIR)
		return handle_pairing(h);
	return E_PROTOCOL;
}

static void
handshake_done(void)
{
	const char	*v, *hash;
	char		 lower[2 * D32U_PIN_LEN + 1];
	struct dbuf	 d = { 0 };
	size_t		 i;

	v = tls_conn_version(L.tls);
	if (v == NULL || !(strcmp(v, "TLSv1.3") == 0 ||
	    (L.cfg.allow_tls12 && strcmp(v, "TLSv1.2") == 0))) {
		log_warnx("link %d: TLS version refused", L.cfg.index);
		conn_close(E_AUTH, 0);
		return;
	}
	if ((hash = tls_peer_cert_hash(L.tls)) == NULL ||
	    strncmp(hash, "SHA256:", 7) != 0 ||
	    strlen(hash + 7) != 2 * D32U_PIN_LEN) {
		log_warnx("link %d: no peer certificate hash", L.cfg.index);
		conn_close(E_AUTH, 0);
		return;
	}
	for (i = 0; i < 2 * D32U_PIN_LEN; i++) {
		char c = hash[7 + i];
		lower[i] = (c >= 'A' && c <= 'F') ? c - 'A' + 'a' : c;
	}
	lower[i] = '\0';
	if (hex_decode(lower, 2 * D32U_PIN_LEN, L.relay_pin,
	    D32U_PIN_LEN) == -1) {
		conn_close(E_AUTH, 0);
		return;
	}
	if (L.have_pin) {
		if (!ct_equal(L.relay_pin, L.pin.pin, D32U_PIN_LEN)) {
			log_warnx("link %d: relay certificate does not match "
			    "the stored pin; refused until local revocation",
			    L.cfg.index);
			conn_close(E_AUTH, 0);
			return;
		}
		L.pinned_conn = 1;
	} else if (!L.window_used) {
		conn_close(E_AUTH, 0);
		return;
	}
	if (L.pinned_conn)
		log_info("link %d: TLS 1.3 up, relay pin matches", L.cfg.index);
	else
		log_info("link %d: TLS 1.3 up, relay pin unknown (pairing "
		    "window)", L.cfg.index);
	arc4random_buf(L.node_nonce, sizeof(L.node_nonce));
	if (msg_build_hello(&d, L.cfg.node_id, L.node_nonce) == -1)
		fatalx("link: hello");
	send_frame(T_HELLO, 0, 0, 0, d.b, d.len);
	dbuf_free(&d);
	L.deadline = now_ms() + D32U_HANDSHAKE_SEC * 1000;
	set_state(ST_HELLO);
}

/* ---------- connection lifecycle ---------- */

static void
schedule_retry(void)
{
	static const int delays[] = { 1, 2, 4, 8, 16, 30 };
	int64_t		 ms;
	int		 i = L.backoff < 5 ? L.backoff : 5;

	/* x random 0.8 .. 1.2 */
	ms = (int64_t)delays[i] * (800 + arc4random_uniform(401));
	L.backoff++;
	L.next_try = now_ms() + ms;
	log_info("link %d: reconnect in %lld ms (attempt %d)", L.cfg.index,
	    (long long)ms, L.backoff);
	set_state(ST_WAIT);
}

static void
conn_close(int send_error, int session_code)
{
	struct stream	*s;
	struct session	*se;
	int		 was = L.state;

	if (L.tls && send_error && L.state >= ST_HELLO) {
		send_u32(T_ERROR, 0, 0, send_error);
		(void)flush_quiet();	/* best effort */
	}
	if (L.tls) {
		(void)tls_close(L.tls);
		tls_free(L.tls);
		L.tls = NULL;
	}
	if (L.fd != -1) {
		close(L.fd);
		L.fd = -1;
	}
	while ((s = TAILQ_FIRST(&L.streams)) != NULL)
		stream_free(s);
	while ((se = TAILQ_FIRST(&L.sessions)) != NULL)
		session_free(se, E_RELAY_LOST);	/* local synthesized event */
	L.recycle = 0;
	/* a ceremony (or the unpinned attempt that would have led to one)
	 * ended without both commits: tell the console */
	if ((was == ST_PAIR && !(L.sent_commit && L.remote_commit)) ||
	    (L.window_used && was != ST_PAIR && was != ST_ACTIVE))
		pair_result(0, send_error ? send_error : E_RELAY_LOST);
	pair_clear();
	L.pinned_conn = L.paired_local = 0;
	L.window_used = 0;
	L.want_out = 0;
	L.gen++;
	L.rlen = 0;
	L.partial_since = 0;
	dbuf_reset(&L.wbuf);
	explicit_bzero(&L.hello, sizeof(L.hello));
	if (was != ST_IDLE && was != ST_WAIT)
		log_info("link %d: connection closed (error sent %d)",
		    L.cfg.index, send_error);
	(void)session_code;
	if (L.have_pin)
		schedule_retry();
	else
		set_state(ST_IDLE);
}

static void
start_connect(void)
{
	struct sockaddr_in	 sin;
	int			 one = 1;
	int64_t			 now = now_ms();

	if (!L.have_pin) {
		if (!L.window_armed || now >= L.window_until) {
			L.window_armed = 0;
			set_state(ST_IDLE);
			return;
		}
		/* one unpinned attempt consumes the window, even if it fails */
		L.window_armed = 0;
		L.window_used = 1;
	}
	if ((L.fd = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK |
	    SOCK_CLOEXEC, 0)) == -1) {
		conn_close(0, 0);
		return;
	}
	(void)setsockopt(L.fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
	memset(&sin, 0, sizeof(sin));
	sin.sin_len = sizeof(sin);
	sin.sin_family = AF_INET;
	sin.sin_addr.s_addr = L.cfg.relay_addr;
	sin.sin_port = htons(L.cfg.relay_port);
	L.deadline = now + D32U_HANDSHAKE_SEC * 1000;
	if (connect(L.fd, (struct sockaddr *)&sin, sizeof(sin)) == -1 &&
	    errno != EINPROGRESS) {
		log_info("link %d: connect failed errno %d", L.cfg.index,
		    errno);
		conn_close(0, 0);
		return;
	}
	log_debug("link %d: connecting", L.cfg.index);
	L.gen++;
	set_state(ST_TCP);
}

static void
start_tls(void)
{
	int		 e;
	socklen_t	 el = sizeof(e);

	if (getsockopt(L.fd, SOL_SOCKET, SO_ERROR, &e, &el) == -1 || e != 0) {
		if (e == EINPROGRESS)
			return;
		log_info("link %d: connect failed errno %d", L.cfg.index, e);
		conn_close(0, 0);
		return;
	}
	if ((L.tls = tls_client()) == NULL ||
	    tls_configure(L.tls, L.tcfg) == -1 ||
	    tls_connect_socket(L.tls, L.fd, NULL) == -1) {
		log_warnx("link %d: tls client setup failed", L.cfg.index);
		conn_close(0, 0);
		return;
	}
	set_state(ST_TLS);
}

static void
process_frames(void)
{
	struct d32u_hdr	 h;
	size_t		 used;
	int		 e;
	int64_t		 now;

	while (L.rlen >= D32U_HDR_LEN && L.fd != -1) {
		if ((e = d32u_hdr_parse(L.rbuf, &h)) != 0) {
			log_warnx("link %d: invalid frame header (error %d)",
			    L.cfg.index, e);
			conn_close(e, 0);
			return;
		}
		if (L.rlen < D32U_HDR_LEN + (size_t)h.len)
			break;
		now = now_ms();
		if (now / 1000 != L.bucket) {
			L.bucket = now / 1000;
			L.frames_in_bucket = 0;
		}
		if (++L.frames_in_bucket > D32U_RATE_MAX) {
			log_warnx("link %d: frame rate limit", L.cfg.index);
			conn_close(E_LIMIT, 0);
			return;
		}
		if (h.type != T_DATA && h.type != T_WINDOW)
			log_debug("link %d: rx type %u stream %u ch %u len %u",
			    L.cfg.index, h.type, h.stream, h.channel, h.len);
		if (h.type == T_ERROR) {
			log_info("link %d: relay sent ERROR %u", L.cfg.index,
			    d32u_get32(L.rbuf + D32U_HDR_LEN));
			conn_close(0, 0);
			return;
		}
		if (L.state == ST_ACTIVE)
			e = handle_active(&h, L.rbuf + D32U_HDR_LEN);
		else
			e = handle_handshake(&h, L.rbuf + D32U_HDR_LEN);
		if (e != 0) {
			log_warnx("link %d: peer violation, error %d (frame "
			    "type %u)", L.cfg.index, e, h.type);
			conn_close(e, 0);
			return;
		}
		if (L.fd == -1)
			return;
		L.last_rx = now;	/* only valid frames refresh liveness */
		used = D32U_HDR_LEN + h.len;
		memmove(L.rbuf, L.rbuf + used, L.rlen - used);
		L.rlen -= used;
		L.partial_since = L.rlen ? now : 0;
	}
	if (L.rlen && L.partial_since == 0)
		L.partial_since = now_ms();
}

static void
net_io(short revents)
{
	ssize_t	 n;
	int	 r;

	if (L.state == ST_TCP) {
		start_tls();
		if (L.state != ST_TLS)
			return;
	}
	L.want_out = 0;
	if (L.state == ST_TLS) {
		r = tls_handshake(L.tls);
		if (r == TLS_WANT_POLLOUT)
			L.want_out = 1;
		if (r == TLS_WANT_POLLIN || r == TLS_WANT_POLLOUT)
			return;
		if (r == -1) {
			log_warnx("link %d: TLS handshake failed", L.cfg.index);
			conn_close(0, 0);
			return;
		}
		handshake_done();
		flush_net();
		/* the relay's HELLO may already be inside libtls: read on */
	}
	while (L.tls && L.fd != -1 && L.state >= ST_HELLO) {
		n = tls_read(L.tls, L.rbuf + L.rlen,
		    D32U_HDR_LEN + D32U_MAX_PAYLOAD - L.rlen);
		if (n == TLS_WANT_POLLOUT)
			L.want_out = 1;
		if (n == TLS_WANT_POLLIN || n == TLS_WANT_POLLOUT)
			break;
		if (n == -1 || n == 0) {
			log_info("link %d: connection lost (read %d)",
			    L.cfg.index, (int)n);
			conn_close(0, 0);
			return;
		}
		L.rlen += n;
		process_frames();
	}
	(void)revents;
}

/* ---------- timers ---------- */

static void
timers(void)
{
	int64_t		 now = now_ms();
	struct stream	*s, *ns;

	switch (L.state) {
	case ST_IDLE:
		if (L.window_armed && now < L.window_until)
			start_connect();
		else if (L.window_armed) {
			L.window_armed = 0;
			log_info("link %d: pairing window expired",
			    L.cfg.index);
			pair_result(0, E_TIMEOUT);
		}
		return;
	case ST_WAIT:
		if (now >= L.next_try)
			start_connect();
		return;
	case ST_TCP:
	case ST_TLS:
	case ST_HELLO:
	case ST_ACK:
	case ST_PAIR:
		if (now >= L.deadline) {
			log_warnx("link %d: handshake or pairing deadline",
			    L.cfg.index);
			conn_close(E_TIMEOUT, 0);
		}
		return;
	}
	/* ACTIVE */
	if (now - L.last_rx >= D32U_SILENCE_SEC * 1000) {
		log_warnx("link %d: keepalive: no valid frame for %d s, "
		    "dropping", L.cfg.index, D32U_SILENCE_SEC);
		conn_close(E_TIMEOUT, 0);
		return;
	}
	if (L.partial_since && now - L.partial_since >= D32U_PARTIAL_SEC *
	    1000) {
		log_warnx("link %d: partial frame deadline", L.cfg.index);
		conn_close(E_TIMEOUT, 0);
		return;
	}
	if (now - L.last_ping >= D32U_PING_SEC * 1000) {
		if (L.npings >= 4) {
			conn_close(E_TIMEOUT, 0);
			return;
		}
		arc4random_buf(L.pings[L.npings], 8);
		send_frame(T_PING, 0, 0, 0, L.pings[L.npings], 8);
		L.npings++;
		L.last_ping = now;
	}
	for (s = TAILQ_FIRST(&L.streams); s; s = ns) {
		ns = TAILQ_NEXT(s, entry);
		if (s->stall_since && !TAILQ_EMPTY(&s->outq) &&
		    now - s->stall_since >= D32U_CREDIT_SEC * 1000) {
			log_warnx("link %d: credit wait timeout stream %u",
			    L.cfg.index, s->id);
			stream_reset(s, E_TIMEOUT);
			/* a codex-ctl reset ends its session and frees its
			 * mcp streams, maybe ns: start again from the head */
			ns = TAILQ_FIRST(&L.streams);
		}
	}
}

static int
timeout_ms(void)
{
	int64_t	 now = now_ms(), t = 1000;

	if (L.state == ST_WAIT)
		t = L.next_try - now;
	else if (L.state >= ST_TCP && L.state <= ST_PAIR)
		t = L.deadline - now;
	if (L.state == ST_ACTIVE) {
		int64_t p = L.last_ping + D32U_PING_SEC * 1000 - now;
		if (p < t)
			t = p;
	}
	if (t > 1000)
		t = 1000;
	if (t < 0)
		t = 0;
	return (int)t;
}

/* ---------- status ---------- */

static void
report(int force)
{
	struct link_state	 st;
	int			 i, ls = ls_of(L.state);

	if (!force && ls == L.reported && L.nsessions == L.report_sessions)
		return;
	L.reported = ls;
	L.report_sessions = L.nsessions;
	memset(&st, 0, sizeof(st));
	st.index = L.cfg.index;
	st.state = ls;
	st.paired = L.have_pin;
	st.sessions = L.nsessions;
	st.signed_in = -1;		/* unknown unless ACTIVE */
	strlcpy(st.name, L.cfg.name, sizeof(st.name));
	if (L.have_pin)
		strlcpy(st.relay_id, L.pin.relay_id, sizeof(st.relay_id));
	if (L.state == ST_ACTIVE) {
		st.signed_in = L.hello.signed_in;
		st.has_codex_version = L.hello.has_codex_version;
		st.nmodels = L.hello.nmodels;
		st.nefforts = L.hello.nefforts;
		for (i = 0; i < L.hello.nmodels; i++)
			strlcpy(st.models[i], L.hello.models[i], 65);
		for (i = 0; i < L.hello.nefforts; i++)
			strlcpy(st.efforts[i], L.hello.efforts[i], 65);
	}
	st.turns = L.turns;
	st.mcp_calls = L.mcp_calls;
	st.mcp_refused = L.mcp_refused;
	st.connects = L.connects;
	imsg_compose(&L.parent, IMSG_LINK_STATE, 0, 0, -1, &st, sizeof(st));
	imsg_compose(&L.ui, IMSG_UI_LINKSTATE, 0, 0, -1, &st, sizeof(st));
}

/* ---------- requests from the ui child ---------- */

static int
in_list(const char *v, char list[][HELLO_STR_MAX + 1], int n)
{
	int	 i;

	for (i = 0; i < n; i++)
		if (strcmp(v, list[i]) == 0)
			return 1;
	return 0;
}

static void
ui_session_open(const struct ui_req *r)
{
	struct session	*se;
	struct stream	*s;
	struct dbuf	 d = { 0 };
	const char	*model, *effort;

	if (L.state != ST_ACTIVE) {
		ui_open_refused(r, E_RELAY_LOST);
		return;
	}
	if (!d32u_id_ok(r->session, strlen(r->session)) ||
	    session_find(r->session) != NULL) {
		ui_open_refused(r, E_SESSION);
		return;
	}
	if (L.used_sessions >= D32U_MAX_SESSIONS && L.nsessions == 0) {
		/* the relay's per-connection budget is spent: reconnect */
		L.recycle = 1;
		ui_open_refused(r, E_RELAY_LOST);
		return;
	}
	if (L.nsessions >= D32U_MAX_SESSIONS ||
	    L.used_sessions >= D32U_MAX_SESSIONS ||
	    L.nstreams >= D32U_MAX_STREAMS) {
		ui_open_refused(r, E_LIMIT);
		return;
	}
	if (L.hello.nmodels == 0 || L.hello.nefforts == 0) {
		ui_open_refused(r, E_SESSION);
		return;
	}
	model = r->model[0] ? r->model : L.hello.models[0];
	effort = r->effort[0] ? r->effort : L.hello.efforts[0];
	if (!in_list(model, L.hello.models, L.hello.nmodels) ||
	    !in_list(effort, L.hello.efforts, L.hello.nefforts)) {
		ui_open_refused(r, E_SESSION);
		return;
	}
	if (L.next_odd > UINT32_MAX - 2) {
		/* never wrap: exhaustion needs a reconnect */
		ui_open_refused(r, E_LIMIT);
		conn_close(0, 0);
		return;
	}
	if ((se = calloc(1, sizeof(*se))) == NULL)
		fatalx("link: out of memory");
	strlcpy(se->id, r->session, sizeof(se->id));
	se->client = r->client;
	se->win_start = now_ms();
	TAILQ_INSERT_TAIL(&L.sessions, se, entry);
	L.nsessions++;
	L.used_sessions++;
	s = stream_new(L.next_odd, CH_CODEX_CTL);
	L.next_odd += 2;
	s->sess = se;
	se->ctl = s;
	send_open(s, se->id);
	/* session.open is the first DATA; it waits for OPEN_ACK */
	dbuf_addstr(&d, "{\"op\":\"session.open\",\"profile\":\"research\","
	    "\"kind\":\"research\",\"skill\":\"deep32\",\"model\":");
	json_put_str(&d, model, strlen(model));
	dbuf_addstr(&d, ",\"effort\":");
	json_put_str(&d, effort, strlen(effort));
	dbuf_addstr(&d, ",\"caller_class\":");
	json_put_str(&d, L.cfg.caller_class, strlen(L.cfg.caller_class));
	dbuf_addstr(&d, "}");
	queue_msg(s, &d);
	report(0);
}

static struct session *
owned_session(const struct ui_req *r)
{
	struct session	*se = session_find(r->session);

	if (se == NULL || se->client != r->client || se->ctl == NULL ||
	    se->ctl->closing)
		return NULL;
	return se;
}

static void
ui_simple(const struct ui_req *r, const char *op)
{
	struct session	*se;
	struct dbuf	 d = { 0 };

	if ((se = owned_session(r)) == NULL || !se->opened) {
		ui_error(r->client, r->session, E_SESSION);
		return;
	}
	if (strcmp(op, "turn.interrupt") == 0 && !se->turn)
		return;
	dbuf_printf(&d, "{\"op\":\"%s\"}", op);
	queue_msg(se->ctl, &d);
}

static void
ui_turn_start(const struct ui_req *r, const char *text, size_t len)
{
	struct session	*se;
	struct dbuf	 d = { 0 };

	if ((se = owned_session(r)) == NULL || !se->opened || se->turn) {
		ui_error(r->client, r->session, E_SESSION);
		return;
	}
	if (len == 0 || len > D32U_REQ_MAX ||
	    !utf8_valid((const uint8_t *)text, len) ||
	    memchr(text, '\0', len) != NULL) {
		ui_error(r->client, r->session, E_PROTOCOL);
		return;
	}
	dbuf_addstr(&d, "{\"op\":\"turn.start\",\"text\":");
	json_put_str(&d, text, len);
	dbuf_addstr(&d, "}");
	if (d.len > D32U_REQ_MAX) {
		dbuf_free(&d);
		ui_error(r->client, r->session, E_LIMIT);
		return;
	}
	se->turn = 1;
	L.turns++;
	queue_msg(se->ctl, &d);
	report(1);
}

static void
ui_channel_open(const struct ui_req *r)
{
	struct dbuf	 d = { 0 };

	/*
	 * seal (3), lib (5) and sync (6) are never opened on this link: the
	 * research link refuses them outright, and the hub payloads on 3/5/6
	 * are reserved for a separate contract.  Nothing is sent.
	 */
	if (r->channel == CH_SEAL || r->channel == CH_LIB ||
	    r->channel == CH_SYNC) {
		log_info("link %d: refused local request to open channel %d "
		    "(link kind %d); nothing sent", L.cfg.index, r->channel,
		    L.cfg.kind);
		dbuf_printf(&d, "{\"op\":\"channel.refused\",\"channel\":%d,"
		    "\"code\":%d}", r->channel, E_CHANNEL_FORBIDDEN);
		ui_send(r->client, NULL, &d);
		return;
	}
	ui_error(r->client, NULL, E_PROTOCOL);
}

static void
ui_gone(uint32_t client)
{
	struct session	*se, *nse;
	struct dbuf	 d = { 0 };

	for (se = TAILQ_FIRST(&L.sessions); se; se = nse) {
		nse = TAILQ_NEXT(se, entry);
		if (se->client != client)
			continue;
		if (se->ctl && se->opened && !se->ctl->closing) {
			if (se->turn) {
				dbuf_addstr(&d, "{\"op\":\"turn.interrupt\"}");
				queue_msg(se->ctl, &d);
			}
			dbuf_addstr(&d, "{\"op\":\"session.close\"}");
			queue_msg(se->ctl, &d);
		}
		se->client = UINT32_MAX;	/* no one to tell */
	}
}

static void
dispatch_ui(void)
{
	struct imsg	 imsg;
	struct ui_req	 r;
	struct ibuf	 ib;
	size_t		 len;
	int		 n;

	for (;;) {
		if ((n = imsgbuf_get(&L.ui, &imsg)) == -1)
			fatalx("link: ui imsg");
		if (n == 0)
			return;
		len = imsg_get_len(&imsg);
		if (len < sizeof(r)) {
			imsg_free(&imsg);
			fatalx("link: short ui message");
		}
		if (imsg_get_ibuf(&imsg, &ib) == -1 ||
		    ibuf_get(&ib, &r, sizeof(r)) == -1)
			fatalx("link: ui message");
		r.session[sizeof(r.session) - 1] = '\0';
		r.model[sizeof(r.model) - 1] = '\0';
		r.effort[sizeof(r.effort) - 1] = '\0';
		switch (imsg_get_type(&imsg)) {
		case IMSG_UI_SESSION_OPEN:
			ui_session_open(&r);
			break;
		case IMSG_UI_TURN_START:
			ui_turn_start(&r, ibuf_data(&ib), ibuf_size(&ib));
			break;
		case IMSG_UI_TURN_INTERRUPT:
			ui_simple(&r, "turn.interrupt");
			break;
		case IMSG_UI_SESSION_CLOSE:
			ui_simple(&r, "session.close");
			break;
		case IMSG_UI_CHANNEL_OPEN:
			ui_channel_open(&r);
			break;
		case IMSG_UI_GONE:
			ui_gone(r.client);
			break;
		default:
			fatalx("link: unexpected ui message %u",
			    imsg_get_type(&imsg));
		}
		imsg_free(&imsg);
	}
}

/* ---------- messages from the parent ---------- */

static void
dispatch_parent(void)
{
	struct imsg		 imsg;
	struct pin_rec		 pr;
	char			 typed[SAS_INPUT_MAX + 1];
	int			 n, ok;
	size_t			 len;
	int64_t			 now;

	for (;;) {
		if ((n = imsgbuf_get(&L.parent, &imsg)) == -1)
			fatalx("link: parent imsg");
		if (n == 0)
			return;
		now = now_ms();
		switch (imsg_get_type(&imsg)) {
		case IMSG_PAIR_WINDOW:
			L.window_until = now + D32U_PAIR_SEC * 1000;
			L.window_armed = 1;
			log_info("link %d: pairing window open for %d s",
			    L.cfg.index, D32U_PAIR_SEC);
			if (L.state == ST_IDLE || L.state == ST_WAIT) {
				L.backoff = 0;
				start_connect();
			}
			break;
		case IMSG_PAIR_TYPED:
			len = imsg_get_len(&imsg);
			if (len > SAS_INPUT_MAX ||
			    imsg_get_buf(&imsg, typed, len) == -1)
				len = 0;
			typed[len] = '\0';
			if (L.state != ST_PAIR || L.local_ok) {
				explicit_bzero(typed, sizeof(typed));
				break;
			}
			ok = len > 0 && sas_verify_typed(typed, len, L.code);
			explicit_bzero(typed, sizeof(typed));
			if (!ok) {
				log_warnx("link %d: typed words do not match: "
				    "ceremony aborted", L.cfg.index);
				conn_close(E_AUTH, 0);
				break;
			}
			L.local_ok = 1;
			send_frame(T_PAIR_CONFIRM, 0, 0, 0, NULL, 0);
			log_info("link %d: local typed words verified, "
			    "PAIR_CONFIRM sent", L.cfg.index);
			pair_progress();
			break;
		case IMSG_PAIR_ABORT:
			if (L.state == ST_PAIR)
				conn_close(E_AUTH, 0);
			L.window_armed = 0;
			break;
		case IMSG_PIN_SAVED:
			if (imsg_get_data(&imsg, &ok, sizeof(ok)) == -1)
				fatalx("link: pin saved");
			if (L.state != ST_PAIR || !L.pin_requested)
				break;
			if (!ok) {
				conn_close(E_AUTH, 0);
				break;
			}
			memset(&L.pin, 0, sizeof(L.pin));
			strlcpy(L.pin.relay_id, L.hello.id,
			    sizeof(L.pin.relay_id));
			memcpy(L.pin.pin, L.relay_pin, D32U_PIN_LEN);
			L.have_pin = 1;
			send_frame(T_PAIR_COMMIT, 0, 0, 0, NULL, 0);
			L.sent_commit = 1;
			log_info("link %d: pin persisted, PAIR_COMMIT sent",
			    L.cfg.index);
			pair_progress();
			break;
		case IMSG_REVOKE:
			log_info("link %d: pin revoked locally; dropping link",
			    L.cfg.index);
			L.have_pin = 0;
			explicit_bzero(&L.pin, sizeof(L.pin));
			if (L.fd != -1)
				conn_close(E_AUTH, 0);
			else
				set_state(ST_IDLE);
			report(1);
			break;
		case IMSG_LINK_PIN:
			if (imsg_get_data(&imsg, &pr, sizeof(pr)) == -1)
				fatalx("link: pin");
			L.pin = pr;
			L.have_pin = 1;
			explicit_bzero(&pr, sizeof(pr));
			break;
		default:
			fatalx("link: unexpected parent message %u",
			    imsg_get_type(&imsg));
		}
		imsg_free(&imsg);
	}
}

static void
init_from_parent(void)
{
	struct imsg	 imsg;
	struct pollfd	 pfd;
	struct pin_rec	 pr;
	size_t		 len;
	int		 n, go = 0, have_init = 0;

	while (!go) {
		pfd.fd = CHATD_FD_PARENT;
		pfd.events = POLLIN;
		if (poll(&pfd, 1, -1) == -1 && errno != EINTR)
			fatalx("link: poll");
		if ((n = imsgbuf_read(&L.parent)) == -1)
			fatalx("link: read from parent");
		if (n == 0)
			exit(0);
		while ((n = imsgbuf_get(&L.parent, &imsg)) == 1) {
			len = imsg_get_len(&imsg);
			switch (imsg_get_type(&imsg)) {
			case IMSG_LINK_INIT:
				if (imsg_get_data(&imsg, &L.cfg,
				    sizeof(L.cfg)) == -1)
					fatalx("link: init");
				have_init = 1;
				break;
			case IMSG_LINK_CERT:
			case IMSG_LINK_KEY:
			case IMSG_LINK_EDGE_TOKEN: {
				char *b = calloc(1, len + 1);

				if (b == NULL || imsg_get_buf(&imsg, b, len) == -1)
					fatalx("link: secret");
				if (imsg_get_type(&imsg) == IMSG_LINK_CERT) {
					L.cert = b;
					L.certlen = len;
				} else if (imsg_get_type(&imsg) ==
				    IMSG_LINK_KEY) {
					L.key = b;
					L.keylen = len;
				} else {
					if (len >= sizeof(L.edge.token) ||
					    (len && !ascii_token_ok(b, len, 255)))
						fatalx("link: edge token");
					memcpy(L.edge.token, b, len);
					freezero(b, len + 1);
				}
				break;
			}
			case IMSG_LINK_PIN:
				if (imsg_get_data(&imsg, &pr, sizeof(pr)) == -1)
					fatalx("link: pin");
				L.pin = pr;
				L.have_pin = 1;
				break;
			case IMSG_LINK_GO:
				go = 1;
				break;
			default:
				fatalx("link: unexpected init message");
			}
			imsg_free(&imsg);
		}
		if (n == -1)
			fatalx("link: imsg");
	}
	if (!have_init || L.cert == NULL || L.key == NULL)
		fatalx("link: incomplete init");
}

__dead void
link_main(int instance)
{
	struct pollfd	 pfd[3 + D32U_MAX_STREAMS];
	uint32_t	 ids[3 + D32U_MAX_STREAMS];
	struct stream	*s;
	int		 n, i, nfds, netidx, edgeidx;
	uint32_t	 gen;
	int64_t		 now;

	log_init(1, 0, "d32-chatd link");
	memset(&L, 0, sizeof(L));
	L.fd = -1;
	TAILQ_INIT(&L.streams);
	TAILQ_INIT(&L.sessions);
	L.reported = -1;
	if (imsgbuf_init(&L.parent, CHATD_FD_PARENT) == -1 ||
	    imsgbuf_init(&L.ui, CHATD_FD_ROLE) == -1 ||
	    imsgbuf_set_maxsize(&L.ui, CHATD_IMSG_MAX) == -1)
		fatalx("link: imsgbuf");
	init_from_parent();
	log_setverbose(L.cfg.verbose);
	if (L.cfg.index != instance)
		fatalx("link: instance mismatch");
	L.edge.addr = L.cfg.edge_addr;
	L.edge.port = L.cfg.edge_port;
	strlcpy(L.edge.path, L.cfg.edge_path, sizeof(L.edge.path));
	if ((L.rbuf = malloc(D32U_HDR_LEN + D32U_MAX_PAYLOAD)) == NULL)
		fatalx("link: out of memory");

	/* TLS: 1.3 only unless the local operator allowed 1.2; the pin is
	 * the trust anchor, so chain, name and expiry checks are off. */
	if ((L.tcfg = tls_config_new()) == NULL ||
	    tls_config_set_protocols(L.tcfg, L.cfg.allow_tls12 ?
	    (TLS_PROTOCOL_TLSv1_2 | TLS_PROTOCOL_TLSv1_3) :
	    TLS_PROTOCOL_TLSv1_3) == -1 ||
	    tls_config_set_keypair_mem(L.tcfg, (const uint8_t *)L.cert,
	    L.certlen, (const uint8_t *)L.key, L.keylen) == -1)
		fatalx("link: tls config");
	tls_config_insecure_noverifycert(L.tcfg);
	tls_config_insecure_noverifyname(L.tcfg);
	tls_config_insecure_noverifytime(L.tcfg);
	freezero(L.key, L.keylen + 1);
	L.key = NULL;

	proc_drop(L.cfg.uid, L.cfg.gid);
	if (pledge("stdio inet", NULL) == -1)
		fatalx("link: pledge");
	log_info("link %d: running as uid %d, chroot /var/empty, pledge "
	    "stdio inet; pin stored %d", L.cfg.index, (int)getuid(),
	    L.have_pin);
	signal(SIGPIPE, SIG_IGN);
	if (L.have_pin)
		start_connect();
	else
		set_state(ST_IDLE);
	report(1);

	for (;;) {
		nfds = 0;
		pfd[nfds].fd = CHATD_FD_PARENT;
		pfd[nfds].events = POLLIN |
		    (imsgbuf_queuelen(&L.parent) ? POLLOUT : 0);
		ids[nfds++] = 0;
		pfd[nfds].fd = CHATD_FD_ROLE;
		pfd[nfds].events = POLLIN |
		    (imsgbuf_queuelen(&L.ui) ? POLLOUT : 0);
		ids[nfds++] = 0;
		netidx = -1;
		gen = L.gen;
		if (L.fd != -1) {
			netidx = nfds;
			pfd[nfds].fd = L.fd;
			pfd[nfds].events = L.state == ST_TCP ? POLLOUT :
			    (POLLIN | ((L.wbuf.len || L.want_out) ?
			    POLLOUT : 0));
			ids[nfds++] = 0;
		}
		edgeidx = nfds;
		TAILQ_FOREACH(s, &L.streams, entry)
			if (s->call && s->call->fd != -1 &&
			    nfds < (int)(sizeof(pfd) / sizeof(pfd[0]))) {
				pfd[nfds].fd = s->call->fd;
				pfd[nfds].events = edge_events(s->call);
				ids[nfds++] = s->id;
			}
		if ((n = poll(pfd, nfds, timeout_ms())) == -1) {
			if (errno == EINTR)
				continue;
			fatalx("link: poll errno %d", errno);
		}
		if (pfd[0].revents & (POLLIN | POLLHUP)) {
			if ((n = imsgbuf_read(&L.parent)) == -1)
				fatalx("link: parent read");
			if (n == 0)
				exit(0);
			dispatch_parent();
		}
		if (pfd[1].revents & (POLLIN | POLLHUP)) {
			if ((n = imsgbuf_read(&L.ui)) == -1)
				fatalx("link: ui read");
			if (n == 0)
				fatalx("link: ui child went away");
			dispatch_ui();
		}
		/* the connection may have been replaced while dispatching */
		if (netidx != -1 && pfd[netidx].revents && L.gen == gen &&
		    L.fd == pfd[netidx].fd)
			net_io(pfd[netidx].revents);
		now = now_ms();
		for (i = edgeidx; i < nfds; i++) {
			if ((s = stream_find(ids[i])) == NULL || s->call == NULL)
				continue;
			if (pfd[i].revents || now >= s->call->deadline)
				if (edge_io(s->call, now))
					mcp_finish(s);
		}
		/* calls that failed before polling, or timed out */
		TAILQ_FOREACH(s, &L.streams, entry)
			if (s->call && (s->call->state == EC_FAIL ||
			    s->call->state == EC_DONE || now >= s->call->deadline)) {
				edge_io(s->call, now);
				mcp_finish(s);
			}
		timers();
		if (L.recycle) {
			L.recycle = 0;
			if (L.state == ST_ACTIVE && L.nsessions == 0) {
				log_info("link %d: the %d sessions of this "
				    "connection are used and none is live: "
				    "reconnecting now", L.cfg.index,
				    D32U_MAX_SESSIONS);
				(void)flush_quiet();	/* the CLOSE replies */
				conn_close(0, 0);
				if (L.state == ST_WAIT) {
					L.backoff = 0;
					L.next_try = now_ms();
				}
			}
		}
		pump();
		flush_net();
		if (imsgbuf_queuelen(&L.parent) && imsgbuf_write(&L.parent) == -1)
			fatalx("link: parent write");
		if (imsgbuf_queuelen(&L.ui) && imsgbuf_write(&L.ui) == -1)
			fatalx("link: ui write");
	}
}
