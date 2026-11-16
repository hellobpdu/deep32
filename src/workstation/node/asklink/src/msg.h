/*
 * D32U/1 message schemas (docs/spec/schemas, one file per message) for the node side.
 */
#ifndef D32U_MSG_H
#define D32U_MSG_H

#include "d32u.h"
#include "json.h"

#define PEER_ROLE_RELAY	1
#define PEER_ROLE_HUB	2
#define HELLO_LIST_MAX	16
#define HELLO_STR_MAX	64

struct peer_hello {
	char		 id[D32U_ID_MAX + 1];
	int		 role;
	uint8_t		 nonce[D32U_NONCE_LEN];
	int		 has_codex_version;
	int		 signed_in;		/* -1 absent, 0, 1 */
	int		 nmodels;
	int		 nefforts;
	char		 models[HELLO_LIST_MAX][HELLO_STR_MAX + 1];
	char		 efforts[HELLO_LIST_MAX][HELLO_STR_MAX + 1];
};

#define EV_OPENED	1
#define EV_DELTA	2
#define EV_FINAL	3
#define EV_COMPLETED	4
#define EV_ERROR	5
#define EV_STATUS	6

#define TS_COMPLETED	1
#define TS_INTERRUPTED	2
#define TS_FAILED	3

struct ctl_event {
	int		 op;
	const char	*session;
	size_t		 sessionlen;
	const char	*text;
	size_t		 textlen;
	int		 status;	/* TS_* */
	int64_t		 in_tokens;
	int64_t		 out_tokens;
	int		 code;		/* error code, or status code index */
};

struct mcp_request {
	const struct jval	*body;		/* NULL when "body":null */
	const char		*sid;		/* NULL when null */
	size_t			 sidlen;
	const char		*pver;		/* NULL when absent */
	size_t			 pverlen;
};

extern const char *const status_codes[];	/* NULL-terminated enum */
extern const char *const mcp_tools[];		/* the 11 read tools */

int	 msg_hello(const uint8_t *, size_t, struct peer_hello *);
int	 msg_hello_ack(const uint8_t *, size_t, int *);
int	 msg_build_hello(struct dbuf *, const char *, const uint8_t *);
int	 msg_build_hello_ack(struct dbuf *, int);
int	 msg_event(const struct jval *, struct ctl_event *);
int	 msg_open(const uint8_t *, size_t, int, char *, size_t);
int	 msg_status(const struct jval *, int *);
int	 msg_mcp_request(const struct jval *, struct mcp_request *);
int	 mcp_tool_index(const char *, size_t);
int	 ascii_token_ok(const char *, size_t, size_t);
int	 status_index(const struct jval *);

#endif /* D32U_MSG_H */
