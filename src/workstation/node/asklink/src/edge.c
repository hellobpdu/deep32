/*
 * Minimal HTTP/1.1 client for one MCP exchange with the local deep32-gateway.
 * Connection: close; the response is read to EOF and bounded.  Accepts a
 * JSON body or a single-event text/event-stream body; Content-Length or
 * chunked framing.  Peer text never reaches a log.
 */
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

#include "chatd.h"
#include "edge.h"
#include "msg.h"

int
edge_start(struct edgecall *ec, const struct edgecfg *cfg,
    const struct dbuf *body, const char *sid, size_t sidlen,
    const char *pver, size_t pverlen, int64_t now)
{
	struct sockaddr_in	 sin;
	char			 ip[16];
	uint32_t		 a = ntohl(cfg->addr);

	memset(ec, 0, sizeof(*ec));
	ec->fd = -1;
	ec->started = now;
	ec->deadline = now + (int64_t)(D32U_MCP_SEC - 5) * 1000;
	snprintf(ip, sizeof(ip), "%u.%u.%u.%u", a >> 24, (a >> 16) & 255,
	    (a >> 8) & 255, a & 255);
	if (dbuf_printf(&ec->out, "POST %s HTTP/1.1\r\nHost: %s:%u\r\n",
	    cfg->path, ip, cfg->port) == -1 ||
	    dbuf_addstr(&ec->out, "Content-Type: application/json\r\n"
	    "Accept: application/json, text/event-stream\r\n"
	    "Connection: close\r\n") == -1 ||
	    (cfg->token[0] && (dbuf_addstr(&ec->out,
	    "Authorization: Bearer ") == -1 ||
	    dbuf_addstr(&ec->out, cfg->token) == -1 ||
	    dbuf_addstr(&ec->out, "\r\n") == -1)))
		goto fail;
	/* sid and pver were checked to be printable ASCII (no CR/LF) */
	if (sid && (dbuf_addstr(&ec->out, "Mcp-Session-Id: ") == -1 ||
	    dbuf_add(&ec->out, sid, sidlen) == -1 ||
	    dbuf_addstr(&ec->out, "\r\n") == -1))
		goto fail;
	if (pver && (dbuf_addstr(&ec->out, "MCP-Protocol-Version: ") == -1 ||
	    dbuf_add(&ec->out, pver, pverlen) == -1 ||
	    dbuf_addstr(&ec->out, "\r\n") == -1))
		goto fail;
	if (dbuf_printf(&ec->out, "Content-Length: %zu\r\n\r\n",
	    body->len) == -1 || dbuf_add(&ec->out, body->b, body->len) == -1)
		goto fail;
	if ((ec->fd = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK |
	    SOCK_CLOEXEC, 0)) == -1)
		goto fail;
	memset(&sin, 0, sizeof(sin));
	sin.sin_len = sizeof(sin);
	sin.sin_family = AF_INET;
	sin.sin_addr.s_addr = cfg->addr;
	sin.sin_port = htons(cfg->port);
	if (connect(ec->fd, (struct sockaddr *)&sin, sizeof(sin)) == -1) {
		if (errno != EINPROGRESS)
			goto fail;
		ec->state = EC_CONNECT;
	} else
		ec->state = EC_WRITE;
	return 0;
 fail:
	ec->state = EC_FAIL;
	return -1;
}

short
edge_events(const struct edgecall *ec)
{
	switch (ec->state) {
	case EC_CONNECT:
	case EC_WRITE:
		return POLLOUT;
	case EC_READ:
		return POLLIN;
	}
	return 0;
}

static const char *
find_crlf2(const uint8_t *b, size_t n)
{
	size_t	 i;

	for (i = 0; i + 3 < n; i++)
		if (b[i] == '\r' && b[i + 1] == '\n' && b[i + 2] == '\r' &&
		    b[i + 3] == '\n')
			return (const char *)b + i;
	return NULL;
}

static int
dechunk(const uint8_t *p, size_t n, struct dbuf *out)
{
	size_t	 i = 0, sz;
	char	*ep, num[20];
	size_t	 k;

	for (;;) {
		for (k = 0; i + k < n && p[i + k] != '\r' && k < sizeof(num) - 1;
		    k++)
			num[k] = p[i + k];
		num[k] = '\0';
		if (i + k + 1 >= n || p[i + k] != '\r' || p[i + k + 1] != '\n')
			return -1;
		errno = 0;
		sz = strtoul(num, &ep, 16);
		if (errno || ep == num || (*ep != '\0' && *ep != ';'))
			return -1;
		i += k + 2;
		if (sz == 0)
			return 0;
		if (sz > n - i || n - i - sz < 2)
			return -1;
		if (dbuf_add(out, p + i, sz) == -1)
			return -1;
		i += sz;
		if (p[i] != '\r' || p[i + 1] != '\n')
			return -1;
		i += 2;
	}
}

/* First event's data lines of a text/event-stream body. */
static int
sse_first(struct dbuf *b)
{
	struct dbuf	 d = { 0 };
	size_t		 i = 0, e;
	int		 have = 0;

	while (i < b->len) {
		for (e = i; e < b->len && b->b[e] != '\n'; e++)
			;
		if (e - i == 0 || (e - i == 1 && b->b[i] == '\r')) {
			if (have)
				break;
		} else if (e - i >= 5 && memcmp(b->b + i, "data:", 5) == 0) {
			size_t s = i + 5, end = e;

			if (s < end && b->b[s] == ' ')
				s++;
			if (end > s && b->b[end - 1] == '\r')
				end--;
			if (have && dbuf_add(&d, "\n", 1) == -1)
				goto fail;
			if (dbuf_add(&d, b->b + s, end - s) == -1)
				goto fail;
			have = 1;
		}
		i = e + 1;
	}
	if (!have)
		goto fail;
	dbuf_free(b);
	*b = d;
	return 0;
 fail:
	dbuf_free(&d);
	return -1;
}

static int
parse_response(struct edgecall *ec)
{
	const char	*hend, *line, *eol;
	const uint8_t	*body;
	size_t		 bodylen, clen = (size_t)-1;
	int		 chunked = 0, sse = 0, status;
	char		 hdr[64];

	if ((hend = find_crlf2(ec->in.b, ec->in.len)) == NULL)
		return -1;
	if (ec->in.len < 12 || (memcmp(ec->in.b, "HTTP/1.1 ", 9) != 0 &&
	    memcmp(ec->in.b, "HTTP/1.0 ", 9) != 0))
		return -1;
	if (ec->in.b[9] < '1' || ec->in.b[9] > '5' ||
	    ec->in.b[10] < '0' || ec->in.b[10] > '9' ||
	    ec->in.b[11] < '0' || ec->in.b[11] > '9')
		return -1;
	status = (ec->in.b[9] - '0') * 100 + (ec->in.b[10] - '0') * 10 +
	    (ec->in.b[11] - '0');
	line = memchr(ec->in.b, '\n', hend - (const char *)ec->in.b);
	while (line && line < hend) {
		const char *colon, *v;
		size_t nl, vl;

		line++;
		eol = memchr(line, '\r', hend + 2 - line);
		if (eol == NULL || eol <= line)
			break;
		if ((colon = memchr(line, ':', eol - line)) == NULL)
			return -1;
		nl = colon - line;
		v = colon + 1;
		while (v < eol && (*v == ' ' || *v == '\t'))
			v++;
		vl = eol - v;
		if (nl < sizeof(hdr)) {
			memcpy(hdr, line, nl);
			hdr[nl] = '\0';
			if (strcasecmp(hdr, "content-length") == 0) {
				char num[24], *ep;

				if (vl == 0 || vl >= sizeof(num))
					return -1;
				memcpy(num, v, vl);
				num[vl] = '\0';
				errno = 0;
				clen = strtoul(num, &ep, 10);
				if (errno || *ep)
					return -1;
			} else if (strcasecmp(hdr, "transfer-encoding") == 0) {
				if (vl == 7 && strncasecmp(v, "chunked", 7) == 0)
					chunked = 1;
				else
					return -1;
			} else if (strcasecmp(hdr, "content-type") == 0) {
				if (vl >= 17 &&
				    strncasecmp(v, "text/event-stream", 17) == 0)
					sse = 1;
			} else if (strcasecmp(hdr, "mcp-session-id") == 0) {
				if (vl > 0 && vl < sizeof(ec->sid) &&
				    ascii_token_ok(v, vl, 128)) {
					memcpy(ec->sid, v, vl);
					ec->sid[vl] = '\0';
				}
			}
		}
		line = memchr(eol, '\n', hend + 4 - eol);
	}
	body = (const uint8_t *)hend + 4;
	bodylen = ec->in.len - (body - ec->in.b);
	if (chunked) {
		if (dechunk(body, bodylen, &ec->body) == -1)
			return -1;
	} else {
		if (clen != (size_t)-1) {
			if (clen > bodylen)
				return -1;
			bodylen = clen;
		}
		if (dbuf_add(&ec->body, body, bodylen) == -1)
			return -1;
	}
	if (sse && ec->body.len > 0 && sse_first(&ec->body) == -1)
		return -1;
	ec->http_status = status;
	return 0;
}

/* Returns 1 when the call has finished (EC_DONE or EC_FAIL). */
int
edge_io(struct edgecall *ec, int64_t now)
{
	uint8_t		 tmp[16384];
	ssize_t		 n;
	int		 e;
	socklen_t	 el = sizeof(e);

	if (ec->state == EC_DONE || ec->state == EC_FAIL)
		return 1;
	if (now >= ec->deadline)
		goto fail;
	switch (ec->state) {
	case EC_CONNECT:
		if (getsockopt(ec->fd, SOL_SOCKET, SO_ERROR, &e, &el) == -1 ||
		    e != 0) {
			if (e == EINPROGRESS)
				return 0;
			goto fail;
		}
		ec->state = EC_WRITE;
		/* FALLTHROUGH */
	case EC_WRITE:
		while (ec->outoff < ec->out.len) {
			n = write(ec->fd, ec->out.b + ec->outoff,
			    ec->out.len - ec->outoff);
			if (n == -1) {
				if (errno == EAGAIN || errno == EINTR)
					return 0;
				goto fail;
			}
			ec->outoff += n;
		}
		ec->state = EC_READ;
		return 0;
	case EC_READ:
		for (;;) {
			n = read(ec->fd, tmp, sizeof(tmp));
			if (n == -1) {
				if (errno == EAGAIN || errno == EINTR)
					return 0;
				goto fail;
			}
			if (n == 0)
				break;
			if (ec->in.len + n > EDGE_RESP_MAX ||
			    dbuf_add(&ec->in, tmp, n) == -1)
				goto fail;
		}
		close(ec->fd);
		ec->fd = -1;
		if (parse_response(ec) == -1)
			goto fail;
		ec->state = EC_DONE;
		return 1;
	}
 fail:
	if (ec->fd != -1)
		close(ec->fd);
	ec->fd = -1;
	ec->state = EC_FAIL;
	return 1;
}

void
edge_free(struct edgecall *ec)
{
	if (ec->fd != -1)
		close(ec->fd);
	ec->fd = -1;
	dbuf_free(&ec->out);
	dbuf_free(&ec->in);
	dbuf_free(&ec->body);
}
