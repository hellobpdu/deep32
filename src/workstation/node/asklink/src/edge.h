/*
 * Forwarding of allowed MCP requests to the node's local deep32-gateway
 * (First light: production's Python deep32-gateway on loopback HTTP, POST /mcp,
 * application/json).  Non-blocking, driven by the link's poll loop.
 */
#ifndef D32_EDGE_H
#define D32_EDGE_H

#include "json.h"

#define EC_CONNECT	1
#define EC_WRITE	2
#define EC_READ		3
#define EC_DONE		4
#define EC_FAIL		5

#define EDGE_RESP_MAX	(1048576 + 16384)

struct edgecfg {
	uint32_t	 addr;		/* network order */
	uint16_t	 port;		/* host order */
	char		 path[64];
	char		 token[256];
};

struct edgecall {
	int		 fd;
	int		 state;
	int64_t		 started;
	int64_t		 deadline;
	struct dbuf	 out;
	size_t		 outoff;
	struct dbuf	 in;
	struct dbuf	 body;
	int		 http_status;
	char		 sid[129];
};

int	 edge_start(struct edgecall *, const struct edgecfg *,
	    const struct dbuf *, const char *, size_t, const char *, size_t,
	    int64_t);
short	 edge_events(const struct edgecall *);
int	 edge_io(struct edgecall *, int64_t);
void	 edge_free(struct edgecall *);

#endif /* D32_EDGE_H */
