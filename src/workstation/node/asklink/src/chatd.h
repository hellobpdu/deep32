/*
 * d32-chatd: the chat broker behind Deep32 Library's Ask room (item 41).
 *
 *   parent (root)      config, node identity, pins (0600, state dir), the
 *                      root-only control socket for d32-chatctl, restarts
 *   ui (_d32chat)      uplink.sock API for the UI process (which has no
 *                      network); chroot /var/empty, pledge "stdio unix"
 *   link (_d32upw/h)   one per paired relay or hub: the D32U/1 client over
 *                      libtls and the mcp channel's forwarding to the node's
 *                      local deep32-gateway; chroot /var/empty, pledge "stdio inet"
 *
 * Children re-exec the daemon with exactly -P <role> -I <n> and fixed
 * descriptors (3 = parent, 4 = role socket, 5.. = ui<->link pairs).
 */
#ifndef D32_CHATD_H
#define D32_CHATD_H

#include <sys/types.h>
#include <sys/queue.h>

#include <imsg.h>
#include <stdint.h>

#include "d32u.h"

#define CHATD_CONF		"/etc/d32/chatd.conf"
#define CHATD_STATE		"/etc/d32/state/chat"
#define CHATD_CTL_SOCK		"/var/run/d32/chatd.sock"
#define CHATD_UI_SOCK		"/var/run/d32/uplink.sock"
#define CHATD_UI_GROUP		"_d32users"
#define CHATD_UI_USER		"_d32chat"
#define CHATD_MAX_LINKS		2
#define CHATD_NAME_MAX		16
#define CHATD_IMSG_MAX		(2u * 1024 * 1024)
#define CHATD_UI_LINE_MAX	(80 * 1024)
#define CHATD_FD_PARENT		3
#define CHATD_FD_ROLE		4
#define CHATD_FD_LINKS		5

#define LINK_KIND_RELAY		1
#define LINK_KIND_HUB		2

/* link states as shown to the UI and d32-chatctl */
#define LS_UNPAIRED		0
#define LS_WAITING		1
#define LS_CONNECTING		2
#define LS_HANDSHAKE		3
#define LS_PAIRING		4
#define LS_ACTIVE		5
#define LS_ISOLATED		6

enum chatd_imsg {
	IMSG_NONE,
	/* parent -> link */
	IMSG_LINK_INIT,
	IMSG_LINK_CERT,
	IMSG_LINK_KEY,
	IMSG_LINK_EDGE_TOKEN,
	IMSG_LINK_PIN,
	IMSG_LINK_GO,
	IMSG_PAIR_WINDOW,
	IMSG_PAIR_TYPED,
	IMSG_PAIR_ABORT,
	IMSG_PIN_SAVED,
	IMSG_REVOKE,
	/* link -> parent */
	IMSG_PAIR_CODE,
	IMSG_PAIR_RESULT,
	IMSG_PIN_SAVE,
	IMSG_LINK_STATE,
	/* parent -> ui */
	IMSG_UI_INIT,
	IMSG_UI_GO,
	/* ui <-> link */
	IMSG_UI_SESSION_OPEN,
	IMSG_UI_TURN_START,
	IMSG_UI_TURN_INTERRUPT,
	IMSG_UI_SESSION_CLOSE,
	IMSG_UI_CHANNEL_OPEN,
	IMSG_UI_GONE,
	IMSG_UI_EVENT,
	IMSG_UI_SESSION_END,
	IMSG_UI_LINKSTATE,
	/* d32-chatctl <-> parent */
	IMSG_CTL_STATUS,
	IMSG_CTL_STATUS_LINK,
	IMSG_CTL_PAIR,
	IMSG_CTL_PAIR_CODE,
	IMSG_CTL_TYPED,
	IMSG_CTL_RESULT,
	IMSG_CTL_UNPAIR,
	IMSG_CTL_END
};

struct link_init {
	char		 name[CHATD_NAME_MAX];
	char		 node_id[D32U_ID_MAX + 1];
	char		 caller_class[16];
	int		 kind;
	int		 index;
	int		 verbose;
	int		 allow_tls12;
	uid_t		 uid;
	gid_t		 gid;
	uint32_t	 relay_addr;		/* IPv4, network order */
	uint16_t	 relay_port;		/* host order */
	uint32_t	 edge_addr;
	uint16_t	 edge_port;
	char		 edge_path[64];
	uint8_t		 node_pin[D32U_PIN_LEN];
};

struct pin_rec {
	char		 relay_id[D32U_ID_MAX + 1];
	uint8_t		 pin[D32U_PIN_LEN];
};

/* parent -> d32-chatctl: the words to show */
struct pair_code {
	char		 relay_id[D32U_ID_MAX + 1];
	char		 code[SAS_CODE_MAX];
};

/* link -> parent: what the words are computed from (the parent computes
 * and checks them itself; see chatd.c, struct linkconf) */
struct pair_offer {
	char		 relay_id[D32U_ID_MAX + 1];
	uint8_t		 relay_pin[D32U_PIN_LEN];
	uint8_t		 nonce[2 * D32U_NONCE_LEN];	/* node's, relay's */
};

struct pair_result {
	int		 ok;
	int		 code;		/* D32U error code on failure */
};

struct link_state {
	int		 index;
	int		 state;		/* LS_* */
	int		 paired;	/* a pin is stored */
	int		 sessions;
	int		 signed_in;
	int		 has_codex_version;
	int		 nmodels;
	int		 nefforts;
	char		 name[CHATD_NAME_MAX];
	char		 relay_id[D32U_ID_MAX + 1];
	char		 models[16][65];
	char		 efforts[16][65];
	int64_t		 turns;
	int64_t		 mcp_calls;
	int64_t		 mcp_refused;
	int64_t		 connects;
};

struct ui_init {
	uid_t		 uid;
	gid_t		 gid;
	int		 nlinks;
	int		 verbose;
	char		 names[CHATD_MAX_LINKS][CHATD_NAME_MAX];
};

struct ui_req {
	uint32_t	 client;
	char		 session[D32U_ID_MAX + 1];
	char		 model[65];
	char		 effort[65];
	int		 channel;
	/* turn text follows the struct for IMSG_UI_TURN_START */
};

struct ui_event_hdr {
	uint32_t	 client;
	char		 session[D32U_ID_MAX + 1];
	/* one JSON line follows */
};

struct ctl_link {
	char		 name[CHATD_NAME_MAX];
};

/* log.c */
void	 log_init(int, int, const char *);
void	 log_setverbose(int);
void	 log_info(const char *, ...) __attribute__((__format__(__printf__, 1, 2)));
void	 log_warnx(const char *, ...) __attribute__((__format__(__printf__, 1, 2)));
void	 log_debug(const char *, ...) __attribute__((__format__(__printf__, 1, 2)));
__dead void fatalx(const char *, ...) __attribute__((__format__(__printf__, 1, 2)));

/* proc.c */
int64_t	 now_ms(void);
void	 proc_drop(uid_t, gid_t);
pid_t	 proc_exec(const char *, const char *, int, const int *, int);
int	 imsg_send(struct imsgbuf *, uint32_t, const void *, size_t);
int	 imsg_flush_all(struct imsgbuf *);

/* ident.c */
int	 ident_generate(const char *);
int	 ident_load(const char *, char **, size_t *, char **, size_t *);
int	 ident_pin(const char *, size_t, uint8_t *);
int	 pin_load(const char *, const char *, struct pin_rec *);
int	 pin_save(const char *, const char *, const struct pin_rec *);
int	 pin_delete(const char *, const char *);

/* link.c, ui.c */
__dead void link_main(int);
__dead void ui_main(void);

#endif /* D32_CHATD_H */
