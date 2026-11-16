/*
 * D32U/1 frame codec (d32u1.md "Header, numbers, flags").
 * The 12-byte header is validated before any payload allocation.
 */
#include <string.h>

#include "d32u.h"

uint32_t
d32u_get32(const uint8_t *b)
{
	return ((uint32_t)b[0] << 24) | ((uint32_t)b[1] << 16) |
	    ((uint32_t)b[2] << 8) | (uint32_t)b[3];
}

void
d32u_put32(uint8_t *b, uint32_t v)
{
	b[0] = v >> 24;
	b[1] = v >> 16;
	b[2] = v >> 8;
	b[3] = v;
}

int
d32u_is_control(uint8_t type)
{
	switch (type) {
	case T_HELLO:
	case T_HELLO_ACK:
	case T_PING:
	case T_PONG:
	case T_ERROR:
	case T_PAIR_CONFIRM:
	case T_PAIR_COMMIT:
		return 1;
	}
	return 0;
}

/*
 * Returns 0 or the D32U error code for a header that is never legal,
 * whatever the connection state.  Length is checked first so that a
 * receiver can refuse before it allocates.
 */
int
d32u_hdr_check(const struct d32u_hdr *h)
{
	if (h->len > D32U_MAX_PAYLOAD)
		return E_LIMIT;
	if (h->type < T_HELLO || h->type > T_PAIR_COMMIT)
		return E_PROTOCOL;
	if (h->type == T_DATA) {
		if (h->flags & ~F_END_MESSAGE)
			return E_PROTOCOL;
	} else if (h->flags != 0)
		return E_PROTOCOL;
	if (d32u_is_control(h->type)) {
		if (h->stream != 0 || h->channel != 0)
			return E_PROTOCOL;
	} else if (h->stream == 0 || h->channel == 0)
		return E_PROTOCOL;
	switch (h->type) {
	case T_HELLO:
	case T_HELLO_ACK:
	case T_OPEN:
		if (h->len > D32U_CTL_MAX)
			return E_LIMIT;
		break;
	case T_OPEN_ACK:
	case T_CLOSE:
	case T_PAIR_CONFIRM:
	case T_PAIR_COMMIT:
		if (h->len != 0)
			return E_PROTOCOL;
		break;
	case T_WINDOW:
	case T_RESET:
	case T_ERROR:
		if (h->len != 4)
			return E_PROTOCOL;
		break;
	case T_PING:
	case T_PONG:
		if (h->len != 8)
			return E_PROTOCOL;
		break;
	case T_DATA:
		if (h->len == 0)
			return E_PROTOCOL;
		break;
	}
	return 0;
}

int
d32u_hdr_parse(const uint8_t *b, struct d32u_hdr *h)
{
	h->len = d32u_get32(b);
	h->stream = d32u_get32(b + 4);
	h->type = b[8];
	h->channel = b[9];
	h->flags = ((uint16_t)b[10] << 8) | b[11];
	return d32u_hdr_check(h);
}

void
d32u_hdr_write(const struct d32u_hdr *h, uint8_t *b)
{
	d32u_put32(b, h->len);
	d32u_put32(b + 4, h->stream);
	b[8] = h->type;
	b[9] = h->channel;
	b[10] = h->flags >> 8;
	b[11] = h->flags & 0xff;
}

/* Decode exactly one complete frame; trailing or missing bytes fail. */
int
d32u_frame_decode(const uint8_t *buf, size_t len, struct d32u_hdr *h,
    const uint8_t **payload)
{
	int	 e;

	if (len < D32U_HDR_LEN)
		return E_PROTOCOL;
	if ((e = d32u_hdr_parse(buf, h)) != 0)
		return e;
	if (len - D32U_HDR_LEN != h->len)
		return E_PROTOCOL;
	*payload = buf + D32U_HDR_LEN;
	return 0;
}

int
d32u_frame_encode(uint8_t *out, size_t outlen, const struct d32u_hdr *h,
    const uint8_t *payload, size_t *written)
{
	int	 e;

	if ((e = d32u_hdr_check(h)) != 0)
		return e;
	if (outlen < D32U_HDR_LEN + (size_t)h->len)
		return E_LIMIT;
	d32u_hdr_write(h, out);
	if (h->len)
		memcpy(out + D32U_HDR_LEN, payload, h->len);
	*written = D32U_HDR_LEN + h->len;
	return 0;
}

const char *
d32u_type_name(uint8_t t)
{
	static const char *const names[] = { "?", "HELLO", "HELLO_ACK",
	    "OPEN", "OPEN_ACK", "DATA", "WINDOW_UPDATE", "CLOSE", "RESET",
	    "PING", "PONG", "ERROR", "PAIR_CONFIRM", "PAIR_COMMIT" };

	if (t > T_PAIR_COMMIT)
		return "?";
	return names[t];
}
