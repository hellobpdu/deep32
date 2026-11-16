/*
 * D32U/1 node side: shared definitions.
 * Normative contract: docs/spec/d32u1.md and sas.md (relay/d32u1 branch, commit cbe83b7),
 * copied read-only into tests/spec/.  Every integer on the wire is unsigned big-endian.
 */
#ifndef D32U_H
#define D32U_H

#include <sys/types.h>
#include <stddef.h>
#include <stdint.h>

#define D32U_HDR_LEN		12
#define D32U_MAX_PAYLOAD	1048576u	/* payload only, header excluded */
#define D32U_CTL_MAX		16384u		/* HELLO, HELLO_ACK, OPEN */
#define D32U_WINDOW		262144		/* initial and maximum credit */
#define D32U_FRAGMENT		65536		/* sender fragment size */
#define D32U_REQ_MAX		65536		/* request message (node or relay) */
#define D32U_RESP_MAX		1048576		/* response or relay event message */
#define D32U_MAX_SESSIONS	16
#define D32U_MAX_STREAMS	64
#define D32U_MAX_MCP		4		/* MCP streams per session */
#define D32U_PING_SEC		15
#define D32U_SILENCE_SEC	45
#define D32U_HANDSHAKE_SEC	10
#define D32U_PARTIAL_SEC	10
#define D32U_CREDIT_SEC		180
#define D32U_MCP_SEC		180
#define D32U_PAIR_SEC		300
#define D32U_RATE_MAX		256		/* received frames per second */
#define D32U_PIN_LEN		32
#define D32U_NONCE_LEN		32
#define D32U_ID_MAX		64

/* frame types */
#define T_HELLO		1
#define T_HELLO_ACK	2
#define T_OPEN		3
#define T_OPEN_ACK	4
#define T_DATA		5
#define T_WINDOW	6
#define T_CLOSE		7
#define T_RESET		8
#define T_PING		9
#define T_PONG		10
#define T_ERROR		11
#define T_PAIR_CONFIRM	12
#define T_PAIR_COMMIT	13

#define F_END_MESSAGE	0x0001

/* channels */
#define CH_CODEX_CTL	1
#define CH_MCP		2
#define CH_SEAL		3
#define CH_STATUS	4
#define CH_LIB		5
#define CH_SYNC		6

/* error codes */
#define E_PROTOCOL		1
#define E_AUTH			2
#define E_CHANNEL_FORBIDDEN	3
#define E_FLOW_CONTROL		4
#define E_LIMIT			5
#define E_TIMEOUT		6
#define E_SESSION		7
#define E_SETTINGS_UNVERIFIED	8
#define E_CODEX_UNAVAILABLE	9
#define E_CANCELLED		10
#define E_RELAY_LOST		11

struct d32u_hdr {
	uint32_t	len;		/* payload length, header excluded */
	uint32_t	stream;
	uint8_t		type;
	uint8_t		channel;
	uint16_t	flags;
};

/* frame.c */
uint32_t	 d32u_get32(const uint8_t *);
void		 d32u_put32(uint8_t *, uint32_t);
int		 d32u_hdr_parse(const uint8_t *, struct d32u_hdr *);
int		 d32u_hdr_check(const struct d32u_hdr *);
void		 d32u_hdr_write(const struct d32u_hdr *, uint8_t *);
int		 d32u_frame_decode(const uint8_t *, size_t, struct d32u_hdr *,
		    const uint8_t **);
int		 d32u_frame_encode(uint8_t *, size_t, const struct d32u_hdr *,
		    const uint8_t *, size_t *);
int		 d32u_is_control(uint8_t);
const char	*d32u_type_name(uint8_t);

/* sas.c */
#define SAS_WORDS	10
#define SAS_CODE_MAX	(SAS_WORDS * 9 + 1)
#define SAS_INPUT_MAX	256
extern const char *const bip39_words[2048];
int		 sas_digest(const uint8_t *, const uint8_t *, const uint8_t *,
		    uint8_t *);
void		 sas_indices(const uint8_t *, int *);
int		 sas_code(const uint8_t *, const uint8_t *, const uint8_t *,
		    char *, size_t);
int		 sas_verify_typed(const char *, size_t, const char *);
int		 sas_pin_cmp(const uint8_t *, const uint8_t *);

/* util.c */
int		 d32u_id_ok(const char *, size_t);
int		 hex_decode(const char *, size_t, uint8_t *, size_t);
void		 hex_encode(const uint8_t *, size_t, char *);
int		 ct_equal(const void *, const void *, size_t);

#endif /* D32U_H */
