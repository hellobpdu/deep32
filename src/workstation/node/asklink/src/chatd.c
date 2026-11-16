/*
 * d32-chatd parent.  See chatd.h for the process layout.
 *
 * Configuration (/etc/d32/chatd.conf, root-owned, read once; a change
 * needs a local restart):
 *
 *   node-id         workstation
 *   state-dir       /etc/d32/state/chat
 *   control-socket  /var/run/d32/chatd.sock
 *   ui-socket       /var/run/d32/uplink.sock
 *   ui-group        _d32users
 *   ui-user         _d32chat
 *   edge            127.0.0.1 8766 /mcp
 *   edge-token-file /etc/d32/state/chat/edge.token
 *   link work relay <relay tailnet IP> 7452 user _d32upw class research-relay
 *   link hub  hub   <relay tailnet IP> 7452 user _d32uph class research
 *
 * A trailing "tls12" on a link line is the local operator's recorded
 * decision that TLS 1.3 is unavailable at that relay (d32u1.md).
 */
#include <sys/types.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <arpa/inet.h>

#include <err.h>
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <imsg.h>
#include <limits.h>
#include <poll.h>
#include <pwd.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "chatd.h"

#define MAX_CTL		4
#define RESTART_MAX	3
#define RESTART_WINDOW	(10 * 60 * 1000)

struct linkconf {
	char		 name[CHATD_NAME_MAX];
	int		 kind;
	uint32_t	 addr;
	uint16_t	 port;
	char		 user[32];
	char		 cls[16];
	int		 tls12;
	uid_t		 uid;
	gid_t		 gid;
	int		 have_pin;
	struct pin_rec	 pin;
	struct imsgbuf	 ib;
	int		 fd;
	pid_t		 pid;
	struct link_state st;
	int		 owner;		/* ctl slot pairing this link, or -1 */
	/*
	 * The ceremony as the parent sees it.  The parent computes the ten
	 * words itself from the node pin and the link child's offer (relay
	 * pin, both nonces), shows those, keeps the operator's typed words
	 * and persists only the offered pin, only if the typed words match
	 * its own code.  A compromised link child cannot pin a relay of its
	 * choice: that needs a 110-bit SAS collision with the real relay's
	 * screen.
	 */
	int		 pair_have;
	char		 pair_id[D32U_ID_MAX + 1];
	uint8_t		 pair_pin[D32U_PIN_LEN];
	char		 pair_code[SAS_CODE_MAX];
	char		 pair_typed[SAS_INPUT_MAX + 1];
	size_t		 pair_typedlen;
};

struct ctlconn {
	int		 fd;
	struct imsgbuf	 ib;
	int		 pairing;	/* link index or -1 */
};

static struct {
	char		 node_id[D32U_ID_MAX + 1];
	char		 state_dir[PATH_MAX];
	char		 ctl_path[PATH_MAX];
	char		 ui_path[PATH_MAX];
	char		 ui_group[32];
	char		 ui_user[32];
	uint32_t	 edge_addr;
	uint16_t	 edge_port;
	char		 edge_path[64];
	char		 edge_token_file[PATH_MAX];
	char		*edge_token;
	size_t		 edge_token_len;
	int		 nlinks;
	struct linkconf	 links[CHATD_MAX_LINKS];
	char		*cert;
	char		*key;
	size_t		 certlen;
	size_t		 keylen;
	uint8_t		 node_pin[D32U_PIN_LEN];
	int		 verbose;
	int		 debug;
	const char	*self;
	int		 ctl_listen;
	int		 ui_listen;
	struct ctlconn	 ctl[MAX_CTL];
	struct imsgbuf	 ui_ib;
	int		 ui_fd;
	pid_t		 ui_pid;
	uid_t		 ui_uid;
	gid_t		 ui_gid;
	gid_t		 ui_sock_gid;
	int64_t		 restarts[RESTART_MAX + 1];
	int		 isolated;
	volatile sig_atomic_t quit;
} P;

static int	 ascii_ok_path(const char *);
static void	 ctl_result(int, int, int);

static __dead void
usage(void)
{
	fprintf(stderr, "usage: d32-chatd [-dv] [-f file]\n");
	exit(1);
}

static int
parse_ip(const char *s, uint32_t *out)
{
	struct in_addr	 a;

	if (inet_pton(AF_INET, s, &a) != 1)
		return -1;
	*out = a.s_addr;
	return 0;
}

static int
parse_port(const char *s, uint16_t *out)
{
	const char	*e = NULL;
	long long	 v = strtonum(s, 1, 65535, &e);

	if (e)
		return -1;
	*out = v;
	return 0;
}

static void
parse_config(const char *file)
{
	FILE		*f;
	char		 line[512], *w[16], *p, *last;
	int		 n, lineno = 0, i;
	struct linkconf	*l;

	strlcpy(P.state_dir, CHATD_STATE, sizeof(P.state_dir));
	strlcpy(P.ctl_path, CHATD_CTL_SOCK, sizeof(P.ctl_path));
	strlcpy(P.ui_path, CHATD_UI_SOCK, sizeof(P.ui_path));
	strlcpy(P.ui_group, CHATD_UI_GROUP, sizeof(P.ui_group));
	strlcpy(P.ui_user, CHATD_UI_USER, sizeof(P.ui_user));
	strlcpy(P.edge_path, "/mcp", sizeof(P.edge_path));
	if ((f = fopen(file, "r")) == NULL)
		err(1, "%s", file);
	while (fgets(line, sizeof(line), f) != NULL) {
		lineno++;
		if ((p = strchr(line, '#')) != NULL)
			*p = '\0';
		n = 0;
		for (p = strtok_r(line, " \t\r\n", &last); p && n < 16;
		    p = strtok_r(NULL, " \t\r\n", &last))
			w[n++] = p;
		if (n == 0)
			continue;
#define NEED(k) do { if (n != (k)) errx(1, "%s:%d: syntax", file, lineno); } while (0)
		if (strcmp(w[0], "node-id") == 0) {
			NEED(2);
			if (!d32u_id_ok(w[1], strlen(w[1])))
				errx(1, "%s:%d: bad node id", file, lineno);
			strlcpy(P.node_id, w[1], sizeof(P.node_id));
		} else if (strcmp(w[0], "state-dir") == 0) {
			NEED(2);
			strlcpy(P.state_dir, w[1], sizeof(P.state_dir));
		} else if (strcmp(w[0], "control-socket") == 0) {
			NEED(2);
			strlcpy(P.ctl_path, w[1], sizeof(P.ctl_path));
		} else if (strcmp(w[0], "ui-socket") == 0) {
			NEED(2);
			strlcpy(P.ui_path, w[1], sizeof(P.ui_path));
		} else if (strcmp(w[0], "ui-group") == 0) {
			NEED(2);
			strlcpy(P.ui_group, w[1], sizeof(P.ui_group));
		} else if (strcmp(w[0], "ui-user") == 0) {
			NEED(2);
			strlcpy(P.ui_user, w[1], sizeof(P.ui_user));
		} else if (strcmp(w[0], "edge") == 0) {
			NEED(4);
			if (parse_ip(w[1], &P.edge_addr) == -1 ||
			    parse_port(w[2], &P.edge_port) == -1 ||
			    w[3][0] != '/' ||
			    strlcpy(P.edge_path, w[3], sizeof(P.edge_path)) >=
			    sizeof(P.edge_path) ||
			    !ascii_ok_path(w[3]))
				errx(1, "%s:%d: bad edge", file, lineno);
			if ((ntohl(P.edge_addr) >> 24) != 127)
				errx(1, "%s:%d: edge must be loopback", file,
				    lineno);
		} else if (strcmp(w[0], "edge-token-file") == 0) {
			NEED(2);
			strlcpy(P.edge_token_file, w[1],
			    sizeof(P.edge_token_file));
		} else if (strcmp(w[0], "link") == 0) {
			if (n != 9 && n != 10)
				errx(1, "%s:%d: link syntax", file, lineno);
			if (P.nlinks >= CHATD_MAX_LINKS)
				errx(1, "%s:%d: too many links", file, lineno);
			l = &P.links[P.nlinks];
			if (!d32u_id_ok(w[1], strlen(w[1])) ||
			    strlen(w[1]) >= sizeof(l->name))
				errx(1, "%s:%d: bad link name", file, lineno);
			strlcpy(l->name, w[1], sizeof(l->name));
			if (strcmp(w[2], "relay") == 0)
				l->kind = LINK_KIND_RELAY;
			else if (strcmp(w[2], "hub") == 0)
				l->kind = LINK_KIND_HUB;
			else
				errx(1, "%s:%d: link kind", file, lineno);
			if (parse_ip(w[3], &l->addr) == -1 ||
			    parse_port(w[4], &l->port) == -1)
				errx(1, "%s:%d: link address", file, lineno);
			if (strcmp(w[5], "user") != 0 ||
			    strlcpy(l->user, w[6], sizeof(l->user)) >=
			    sizeof(l->user) || strcmp(w[7], "class") != 0)
				errx(1, "%s:%d: link syntax", file, lineno);
			if (strcmp(w[8], "research") != 0 &&
			    strcmp(w[8], "research-relay") != 0)
				errx(1, "%s:%d: class must be research or "
				    "research-relay", file, lineno);
			strlcpy(l->cls, w[8], sizeof(l->cls));
			if (n == 10) {
				if (strcmp(w[9], "tls12") != 0)
					errx(1, "%s:%d: link option", file,
					    lineno);
				l->tls12 = 1;
			}
			for (i = 0; i < P.nlinks; i++)
				if (strcmp(P.links[i].name, l->name) == 0)
					errx(1, "%s:%d: duplicate link", file,
					    lineno);
			l->owner = -1;
			l->fd = -1;
			P.nlinks++;
		} else
			errx(1, "%s:%d: unknown keyword", file, lineno);
#undef NEED
	}
	fclose(f);
	if (P.node_id[0] == '\0' || P.nlinks == 0 || P.edge_port == 0)
		errx(1, "%s: node-id, edge and at least one link required",
		    file);
}

static int
ascii_ok_path(const char *s)
{
	for (; *s; s++)
		if (*s < 33 || *s > 126)
			return 0;
	return 1;
}

static void
lookup_users(void)
{
	struct passwd	*pw;
	struct group	*gr;
	int		 i;

	if ((pw = getpwnam(P.ui_user)) == NULL)
		errx(1, "unknown ui user %s", P.ui_user);
	P.ui_uid = pw->pw_uid;
	P.ui_gid = pw->pw_gid;
	if ((gr = getgrnam(P.ui_group)) == NULL)
		errx(1, "unknown ui group %s", P.ui_group);
	P.ui_sock_gid = gr->gr_gid;
	for (i = 0; i < P.nlinks; i++) {
		if ((pw = getpwnam(P.links[i].user)) == NULL)
			errx(1, "unknown link user %s", P.links[i].user);
		if (pw->pw_uid == 0 || pw->pw_uid == P.ui_uid)
			errx(1, "link %s: needs its own unprivileged user",
			    P.links[i].name);
		P.links[i].uid = pw->pw_uid;
		P.links[i].gid = pw->pw_gid;
	}
	if (P.ui_uid == 0)
		errx(1, "ui user must not be root");
}

static int
listen_unix(const char *path, mode_t mode, gid_t gid)
{
	struct sockaddr_un	 sun;
	int			 fd;
	mode_t			 old;

	memset(&sun, 0, sizeof(sun));
	sun.sun_family = AF_UNIX;
	if (strlcpy(sun.sun_path, path, sizeof(sun.sun_path)) >=
	    sizeof(sun.sun_path))
		errx(1, "socket path too long");
	if ((fd = socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC,
	    0)) == -1)
		err(1, "socket");
	(void)unlink(path);
	old = umask(0177);
	if (bind(fd, (struct sockaddr *)&sun, sizeof(sun)) == -1)
		err(1, "bind %s", path);
	umask(old);
	if (chown(path, 0, gid) == -1 || chmod(path, mode) == -1)
		err(1, "permissions on %s", path);
	if (listen(fd, 8) == -1)
		err(1, "listen");
	return fd;
}

static void
read_token(void)
{
	struct stat	 st;
	int		 fd;
	ssize_t		 n;

	if (P.edge_token_file[0] == '\0')
		return;
	if ((fd = open(P.edge_token_file, O_RDONLY | O_NOFOLLOW)) == -1 ||
	    fstat(fd, &st) == -1)
		err(1, "%s", P.edge_token_file);
	if ((st.st_mode & 077) || st.st_uid != 0 || st.st_size > 255)
		errx(1, "%s: must be 0600 root, at most 255 bytes",
		    P.edge_token_file);
	if ((P.edge_token = calloc(1, 256)) == NULL)
		err(1, NULL);
	if ((n = read(fd, P.edge_token, 255)) == -1)
		err(1, "read token");
	close(fd);
	while (n > 0 && (P.edge_token[n - 1] == '\n' ||
	    P.edge_token[n - 1] == '\r'))
		n--;
	P.edge_token[n] = '\0';
	P.edge_token_len = n;
}

/* ---------- children ---------- */

static void
send_link_init(int i)
{
	struct linkconf		*l = &P.links[i];
	struct link_init	 li;

	memset(&li, 0, sizeof(li));
	strlcpy(li.name, l->name, sizeof(li.name));
	strlcpy(li.node_id, P.node_id, sizeof(li.node_id));
	strlcpy(li.caller_class, l->cls, sizeof(li.caller_class));
	li.kind = l->kind;
	li.index = i;
	li.verbose = P.verbose;
	li.allow_tls12 = l->tls12;
	li.uid = l->uid;
	li.gid = l->gid;
	li.relay_addr = l->addr;
	li.relay_port = l->port;
	li.edge_addr = P.edge_addr;
	li.edge_port = P.edge_port;
	strlcpy(li.edge_path, P.edge_path, sizeof(li.edge_path));
	memcpy(li.node_pin, P.node_pin, sizeof(li.node_pin));
	if (imsg_send(&l->ib, IMSG_LINK_INIT, &li, sizeof(li)) == -1 ||
	    imsg_send(&l->ib, IMSG_LINK_CERT, P.cert, P.certlen) == -1 ||
	    imsg_send(&l->ib, IMSG_LINK_KEY, P.key, P.keylen) == -1 ||
	    imsg_send(&l->ib, IMSG_LINK_EDGE_TOKEN, P.edge_token,
	    P.edge_token_len) == -1 ||
	    (l->have_pin && imsg_send(&l->ib, IMSG_LINK_PIN, &l->pin,
	    sizeof(l->pin)) == -1) ||
	    imsg_send(&l->ib, IMSG_LINK_GO, NULL, 0) == -1)
		fatalx("parent: link init");
	explicit_bzero(&li, sizeof(li));
	/* no blocking flush: the main loop writes the rest on POLLOUT, and
	 * a child that dies first is noticed by waitpid() */
	if (imsgbuf_write(&l->ib) == -1)
		log_warnx("parent: link %d init write failed errno %d", i,
		    errno);
}

static void
pair_forget(struct linkconf *l)
{
	l->pair_have = 0;
	l->pair_typedlen = 0;
	explicit_bzero(l->pair_id, sizeof(l->pair_id));
	explicit_bzero(l->pair_pin, sizeof(l->pair_pin));
	explicit_bzero(l->pair_code, sizeof(l->pair_code));
	explicit_bzero(l->pair_typed, sizeof(l->pair_typed));
}

/* The parent's end of a child socket never blocks (a stale poll event
 * or a silent child must not wedge the parent). */
static void
set_nonblock(int fd)
{
	int	 fl;

	if ((fl = fcntl(fd, F_GETFL)) == -1 ||
	    fcntl(fd, F_SETFL, fl | O_NONBLOCK) == -1)
		fatalx("parent: fcntl errno %d", errno);
}

static void
start_children(void)
{
	int		 pui[2], pl[CHATD_MAX_LINKS][2], ul[CHATD_MAX_LINKS][2];
	int		 fds[2 + CHATD_MAX_LINKS], i;
	struct ui_init	 ui;

	if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pui) == -1)
		fatalx("parent: socketpair");
	for (i = 0; i < P.nlinks; i++)
		if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0,
		    pl[i]) == -1 || socketpair(AF_UNIX, SOCK_STREAM |
		    SOCK_CLOEXEC, 0, ul[i]) == -1)
			fatalx("parent: socketpair");
	fds[0] = pui[1];
	fds[1] = P.ui_listen;
	for (i = 0; i < P.nlinks; i++)
		fds[2 + i] = ul[i][0];
	if ((P.ui_pid = proc_exec(P.self, "ui", 0, fds, 2 + P.nlinks)) == -1)
		fatalx("parent: exec ui");
	for (i = 0; i < P.nlinks; i++) {
		fds[0] = pl[i][1];
		fds[1] = ul[i][1];
		if ((P.links[i].pid = proc_exec(P.self, "link", i, fds, 2)) ==
		    -1)
			fatalx("parent: exec link");
	}
	close(pui[1]);
	P.ui_fd = pui[0];
	set_nonblock(P.ui_fd);
	if (imsgbuf_init(&P.ui_ib, P.ui_fd) == -1)
		fatalx("parent: imsgbuf");
	for (i = 0; i < P.nlinks; i++) {
		close(pl[i][1]);
		close(ul[i][0]);
		close(ul[i][1]);
		P.links[i].fd = pl[i][0];
		set_nonblock(P.links[i].fd);
		if (imsgbuf_init(&P.links[i].ib, P.links[i].fd) == -1)
			fatalx("parent: imsgbuf");
		send_link_init(i);
	}
	memset(&ui, 0, sizeof(ui));
	ui.uid = P.ui_uid;
	ui.gid = P.ui_gid;
	ui.nlinks = P.nlinks;
	ui.verbose = P.verbose;
	for (i = 0; i < P.nlinks; i++)
		strlcpy(ui.names[i], P.links[i].name, sizeof(ui.names[i]));
	if (imsg_send(&P.ui_ib, IMSG_UI_INIT, &ui, sizeof(ui)) == -1 ||
	    imsg_send(&P.ui_ib, IMSG_UI_GO, NULL, 0) == -1)
		fatalx("parent: ui init");
	if (imsgbuf_write(&P.ui_ib) == -1)
		log_warnx("parent: ui init write failed errno %d", errno);
	log_info("parent: started ui (pid %d) and %d link child(ren)",
	    (int)P.ui_pid, P.nlinks);
}

/* Is pid one of the children we started and have not reaped yet? */
static int
child_forget(pid_t pid)
{
	int	 i;

	if (pid <= 0)
		return 0;
	if (P.ui_pid == pid) {
		P.ui_pid = 0;
		return 1;
	}
	for (i = 0; i < P.nlinks; i++)
		if (P.links[i].pid == pid) {
			P.links[i].pid = 0;
			return 1;
		}
	return 0;
}

static int
children_left(void)
{
	int	 i, n = P.ui_pid > 0;

	for (i = 0; i < P.nlinks; i++)
		n += P.links[i].pid > 0;
	return n;
}

static void
signal_children(int sig)
{
	int	 i;

	if (P.ui_pid > 0)
		kill(P.ui_pid, sig);
	for (i = 0; i < P.nlinks; i++)
		if (P.links[i].pid > 0)
			kill(P.links[i].pid, sig);
}

/*
 * SIGTERM every child still running and reap it.  Bounded: a child that
 * has not gone after two seconds (stopped, or stuck) gets SIGKILL, so the
 * parent can always restart, isolate or exit.  Only pids it has not
 * reaped are signalled (never a recycled pid).
 */
static void
stop_children(void)
{
	int64_t	 until = now_ms() + 2000;
	pid_t	 pid;
	int	 i, killed = 0;

	signal_children(SIGTERM);
	while (children_left()) {
		if ((pid = waitpid(-1, NULL, WNOHANG)) > 0) {
			child_forget(pid);
			continue;
		}
		if (pid == -1 && errno != EINTR)
			break;		/* ECHILD: nothing left to reap */
		if (!killed && now_ms() >= until) {
			log_warnx("parent: a child ignored SIGTERM for 2 s: "
			    "SIGKILL");
			signal_children(SIGKILL);
			killed = 1;
		}
		if (killed && now_ms() >= until + 3000) {
			/* never block: a late zombie is reaped by the main
			 * loop, which ignores pids it no longer tracks */
			log_warnx("parent: a child outlived SIGKILL by 3 s");
			break;
		}
		(void)poll(NULL, 0, 20);
	}
	P.ui_pid = 0;
	if (P.ui_fd != -1) {
		imsgbuf_clear(&P.ui_ib);
		close(P.ui_fd);
		P.ui_fd = -1;
	}
	for (i = 0; i < P.nlinks; i++) {
		if (P.links[i].fd != -1) {
			imsgbuf_clear(&P.links[i].ib);
			close(P.links[i].fd);
		}
		P.links[i].fd = -1;
		P.links[i].pid = 0;
		pair_forget(&P.links[i]);
		memset(&P.links[i].st, 0, sizeof(P.links[i].st));
		P.links[i].st.index = i;
		P.links[i].st.state = P.isolated ? LS_ISOLATED : LS_WAITING;
	}
}

/*
 * Restart budget (ISO 3.1 item 5): more than 3 abnormal exits in ten
 * minutes isolates the node's uplink until a local restart.  pid has
 * already been reaped and forgotten by the caller.
 */
static void
child_died(pid_t pid, int status)
{
	int64_t	 now = now_ms();
	int	 i, n = 0;

	log_warnx("parent: child %d exited (status %d); restarting all",
	    (int)pid, status);
	for (i = 0; i < RESTART_MAX; i++)
		P.restarts[i] = P.restarts[i + 1];
	P.restarts[RESTART_MAX] = now;
	for (i = 0; i <= RESTART_MAX; i++)
		if (P.restarts[i] && now - P.restarts[i] < RESTART_WINDOW)
			n++;
	stop_children();
	/* a console waiting on a ceremony learns that it ended */
	for (i = 0; i < MAX_CTL; i++)
		if (P.ctl[i].fd != -1 && P.ctl[i].pairing != -1) {
			ctl_result(i, 0, E_RELAY_LOST);
			P.ctl[i].pairing = -1;
		}
	for (i = 0; i < P.nlinks; i++)
		P.links[i].owner = -1;
	if (n > RESTART_MAX) {
		P.isolated = 1;
		for (i = 0; i < P.nlinks; i++)
			P.links[i].st.state = LS_ISOLATED;
		log_warnx("parent: restart budget exceeded: ISOLATED (every "
		    "link dropped until a local restart)");
		return;
	}
	start_children();
}

/* ---------- control clients ---------- */

static void
ctl_close(int slot)
{
	struct ctlconn	*c = &P.ctl[slot];
	int		 i;

	for (i = 0; i < P.nlinks; i++)
		if (P.links[i].owner == slot) {
			P.links[i].owner = -1;
			pair_forget(&P.links[i]);
			if (P.links[i].fd != -1)
				imsg_send(&P.links[i].ib, IMSG_PAIR_ABORT,
				    NULL, 0);
		}
	imsgbuf_clear(&c->ib);
	close(c->fd);
	c->fd = -1;
	c->pairing = -1;
}

static int
link_index(const struct ctl_link *cl)
{
	int	 i;

	for (i = 0; i < P.nlinks; i++)
		if (strncmp(P.links[i].name, cl->name, sizeof(cl->name)) == 0)
			return i;
	return -1;
}

static void
ctl_result(int slot, int ok, int code)
{
	struct pair_result	 r = { ok, code };

	imsg_send(&P.ctl[slot].ib, IMSG_CTL_RESULT, &r, sizeof(r));
}

static void
ctl_dispatch(int slot)
{
	struct ctlconn	*c = &P.ctl[slot];
	struct imsg	 imsg;
	struct ctl_link	 cl;
	char		 typed[SAS_INPUT_MAX + 1], pin[2 * D32U_PIN_LEN + 1];
	size_t		 len;
	int		 n, li, i;

	for (;;) {
		if ((n = imsgbuf_get(&c->ib, &imsg)) == -1) {
			ctl_close(slot);
			return;
		}
		if (n == 0)
			return;
		switch (imsg_get_type(&imsg)) {
		case IMSG_CTL_STATUS:
			for (i = 0; i < P.nlinks; i++) {
				struct link_state st = P.links[i].st;

				strlcpy(st.name, P.links[i].name,
				    sizeof(st.name));
				st.paired = P.links[i].have_pin;
				if (P.isolated)
					st.state = LS_ISOLATED;
				imsg_send(&c->ib, IMSG_CTL_STATUS_LINK, &st,
				    sizeof(st));
			}
			hex_encode(P.node_pin, D32U_PIN_LEN, pin);
			imsg_send(&c->ib, IMSG_CTL_END, pin, sizeof(pin));
			break;
		case IMSG_CTL_PAIR:
			if (imsg_get_data(&imsg, &cl, sizeof(cl)) == -1 ||
			    (li = link_index(&cl)) < 0) {
				ctl_result(slot, 0, E_PROTOCOL);
				break;
			}
			if (P.isolated || P.links[li].have_pin ||
			    P.links[li].owner != -1 || c->pairing != -1) {
				/* known pins change only after local revocation */
				ctl_result(slot, 0, E_AUTH);
				break;
			}
			pair_forget(&P.links[li]);
			P.links[li].owner = slot;
			c->pairing = li;
			imsg_send(&P.links[li].ib, IMSG_PAIR_WINDOW, NULL, 0);
			log_info("parent: console opened a pairing window for "
			    "link %d", li);
			break;
		case IMSG_CTL_TYPED:
			len = imsg_get_len(&imsg);
			li = c->pairing;
			/* once per ceremony, after the words were shown */
			if (li < 0 || !P.links[li].pair_have ||
			    P.links[li].pair_typedlen != 0 || len == 0 ||
			    len > SAS_INPUT_MAX ||
			    imsg_get_buf(&imsg, typed, len) == -1) {
				ctl_result(slot, 0, E_PROTOCOL);
				break;
			}
			/* kept for the parent's own check before any pin is
			 * persisted (link_dispatch, IMSG_PIN_SAVE) */
			memcpy(P.links[li].pair_typed, typed, len);
			P.links[li].pair_typed[len] = '\0';
			P.links[li].pair_typedlen = len;
			imsg_send(&P.links[li].ib, IMSG_PAIR_TYPED, typed, len);
			explicit_bzero(typed, sizeof(typed));
			break;
		case IMSG_CTL_UNPAIR:
			if (imsg_get_data(&imsg, &cl, sizeof(cl)) == -1 ||
			    (li = link_index(&cl)) < 0) {
				ctl_result(slot, 0, E_PROTOCOL);
				break;
			}
			if (pin_delete(P.state_dir, P.links[li].name) == -1) {
				ctl_result(slot, 0, E_PROTOCOL);
				break;
			}
			P.links[li].have_pin = 0;
			explicit_bzero(&P.links[li].pin, sizeof(P.links[li].pin));
			if (P.links[li].fd != -1)
				imsg_send(&P.links[li].ib, IMSG_REVOKE, NULL, 0);
			log_info("parent: pin for link %d revoked locally", li);
			ctl_result(slot, 1, 0);
			break;
		default:
			ctl_close(slot);
			imsg_free(&imsg);
			return;
		}
		imsg_free(&imsg);
	}
}

static void
ctl_accept(void)
{
	int	 fd, i;
	uid_t	 uid;
	gid_t	 gid;

	if ((fd = accept4(P.ctl_listen, NULL, NULL, SOCK_NONBLOCK |
	    SOCK_CLOEXEC)) == -1)
		return;
	if (getpeereid(fd, &uid, &gid) == -1 || uid != 0) {
		close(fd);
		return;
	}
	for (i = 0; i < MAX_CTL; i++)
		if (P.ctl[i].fd == -1) {
			P.ctl[i].fd = fd;
			P.ctl[i].pairing = -1;
			if (imsgbuf_init(&P.ctl[i].ib, fd) == -1)
				fatalx("parent: imsgbuf");
			return;
		}
	close(fd);
}

/* ---------- link messages ---------- */

/*
 * A link child that breaks the imsg contract is killed, not trusted and
 * not allowed to take the parent down: waitpid() then counts it against
 * the restart budget.
 */
static void
link_bad(int li, const char *what)
{
	log_warnx("parent: link %d broke the imsg contract (%s): killed", li,
	    what);
	if (P.links[li].pid > 0)
		kill(P.links[li].pid, SIGKILL);
	if (P.links[li].fd != -1) {
		imsgbuf_clear(&P.links[li].ib);
		close(P.links[li].fd);
		P.links[li].fd = -1;
	}
}

static void
link_dispatch(int li)
{
	struct linkconf		*l = &P.links[li];
	struct imsg		 imsg;
	struct pin_rec		 pr;
	struct pair_offer	 po;
	struct pair_code	 pc;
	struct pair_result	 res;
	int			 n, ok;

	for (;;) {
		if ((n = imsgbuf_get(&l->ib, &imsg)) == -1) {
			link_bad(li, "framing");
			return;
		}
		if (n == 0)
			return;
		switch (imsg_get_type(&imsg)) {
		case IMSG_LINK_STATE:
			if (imsg_get_data(&imsg, &l->st, sizeof(l->st)) == -1)
				goto bad;
			break;
		case IMSG_PAIR_CODE:
			/*
			 * The parent computes the words from the node pin and
			 * the offer; the link child's own text is not shown.
			 */
			if (imsg_get_data(&imsg, &po, sizeof(po)) == -1)
				goto bad;
			po.relay_id[sizeof(po.relay_id) - 1] = '\0';
			if (l->owner < 0 || l->pair_have || l->have_pin ||
			    !d32u_id_ok(po.relay_id, strlen(po.relay_id)) ||
			    sas_code(P.node_pin, po.relay_pin, po.nonce,
			    l->pair_code, sizeof(l->pair_code)) == -1) {
				log_warnx("parent: link %d pairing offer "
				    "refused", li);
				explicit_bzero(&po, sizeof(po));
				pair_forget(l);
				imsg_send(&l->ib, IMSG_PAIR_ABORT, NULL, 0);
				break;
			}
			strlcpy(l->pair_id, po.relay_id, sizeof(l->pair_id));
			memcpy(l->pair_pin, po.relay_pin, sizeof(l->pair_pin));
			l->pair_have = 1;
			memset(&pc, 0, sizeof(pc));
			strlcpy(pc.relay_id, l->pair_id, sizeof(pc.relay_id));
			strlcpy(pc.code, l->pair_code, sizeof(pc.code));
			imsg_send(&P.ctl[l->owner].ib, IMSG_CTL_PAIR_CODE, &pc,
			    sizeof(pc));
			explicit_bzero(&pc, sizeof(pc));
			explicit_bzero(&po, sizeof(po));
			break;
		case IMSG_PIN_SAVE:
			if (imsg_get_data(&imsg, &pr, sizeof(pr)) == -1)
				goto bad;
			pr.relay_id[sizeof(pr.relay_id) - 1] = '\0';
			ok = 0;
			/*
			 * Only inside a console-opened ceremony, only the pin
			 * the words were computed for, and only if the words
			 * the operator typed match the parent's own code.
			 */
			if (l->owner >= 0 && !l->have_pin && l->pair_have &&
			    l->pair_typedlen > 0 &&
			    strcmp(pr.relay_id, l->pair_id) == 0 &&
			    ct_equal(pr.pin, l->pair_pin, D32U_PIN_LEN) &&
			    sas_verify_typed(l->pair_typed, l->pair_typedlen,
			    l->pair_code)) {
				if (pin_save(P.state_dir, l->name, &pr) == 0) {
					l->pin = pr;
					l->have_pin = 1;
					ok = 1;
					log_info("parent: typed words match the "
					    "parent's code; pin for link %d "
					    "persisted (0600)", li);
				}
			} else
				log_warnx("parent: link %d asked to persist a "
				    "pin the console did not confirm: refused",
				    li);
			pair_forget(l);
			explicit_bzero(&pr, sizeof(pr));
			imsg_send(&l->ib, IMSG_PIN_SAVED, &ok, sizeof(ok));
			break;
		case IMSG_PAIR_RESULT:
			if (imsg_get_data(&imsg, &res, sizeof(res)) == -1)
				goto bad;
			pair_forget(l);
			if (l->owner >= 0) {
				imsg_send(&P.ctl[l->owner].ib, IMSG_CTL_RESULT,
				    &res, sizeof(res));
				P.ctl[l->owner].pairing = -1;
				l->owner = -1;
			}
			break;
		default:
			goto bad;
		}
		imsg_free(&imsg);
	}
 bad:
	imsg_free(&imsg);
	link_bad(li, "message");
}

static void
on_signal(int sig)
{
	(void)sig;
	P.quit = 1;
}

int
main(int argc, char *argv[])
{
	const char	*conf = CHATD_CONF, *errstr = NULL;
	const char	*role = NULL;
	int		 ch, instance = -1, i, n, nfds, status, restarted;
	struct pollfd	 pfd[2 + CHATD_MAX_LINKS + MAX_CTL];
	int		 kind[2 + CHATD_MAX_LINKS + MAX_CTL];
	pid_t		 pid;
	struct rlimit	 rl = { 0, 0 };

	if (argc == 5 && strcmp(argv[1], "-P") == 0 &&
	    strcmp(argv[3], "-I") == 0) {
		role = argv[2];
		instance = strtonum(argv[4], 0, CHATD_MAX_LINKS - 1, &errstr);
		if (errstr)
			abort();
		if (strcmp(role, "link") == 0)
			link_main(instance);
		if (strcmp(role, "ui") == 0 && instance == 0)
			ui_main();
		abort();
	}
	while ((ch = getopt(argc, argv, "df:v")) != -1) {
		switch (ch) {
		case 'd':
			P.debug = 1;
			break;
		case 'f':
			conf = optarg;
			break;
		case 'v':
			P.verbose++;
			break;
		default:
			usage();
		}
	}
	if (argc != optind)
		usage();
	if (geteuid() != 0)
		errx(1, "need root privileges");
	if (argv[0][0] != '/')
		errx(1, "must be started with an absolute path");
	P.self = argv[0];
	P.ui_fd = -1;
	for (i = 0; i < MAX_CTL; i++)
		P.ctl[i].fd = -1;
	parse_config(conf);
	lookup_users();
	log_init(P.debug, P.verbose, "d32-chatd");
	/*
	 * The parent holds the node key: a crash (a pledge or unveil abort,
	 * a fatal) must never write it to a core file.  The children inherit
	 * the limit.
	 */
	if (setrlimit(RLIMIT_CORE, &rl) == -1)
		err(1, "setrlimit");

	if (ident_load(P.state_dir, &P.cert, &P.certlen, &P.key,
	    &P.keylen) == -1)
		err(1, "node identity in %s (d32-chatctl keygen)", P.state_dir);
	if (ident_pin(P.cert, P.certlen, P.node_pin) == -1)
		errx(1, "node certificate unreadable");
	for (i = 0; i < P.nlinks; i++)
		if (pin_load(P.state_dir, P.links[i].name,
		    &P.links[i].pin) == 0)
			P.links[i].have_pin = 1;
	read_token();
	P.ctl_listen = listen_unix(P.ctl_path, 0600, 0);
	P.ui_listen = listen_unix(P.ui_path, 0660, P.ui_sock_gid);

	if (!P.debug && daemon(0, 0) == -1)
		err(1, "daemon");
	signal(SIGPIPE, SIG_IGN);
	signal(SIGTERM, on_signal);
	signal(SIGINT, on_signal);
	start_children();

	if (unveil(P.self, "x") == -1 || unveil(P.state_dir, "rwc") == -1 ||
	    unveil(NULL, NULL) == -1)
		fatalx("parent: unveil");
	/* rpath stays: rename(2), used for the atomic pin write, looks up
	 * its source with rpath|cpath (without it the parent is killed) */
	if (pledge("stdio rpath wpath cpath fattr unix proc exec", NULL) == -1)
		fatalx("parent: pledge");
	log_info("parent: node %d link(s); pledge stdio rpath wpath cpath "
	    "fattr unix proc exec; unveil state dir (rwc) + own binary (x)",
	    P.nlinks);

	while (!P.quit) {
		nfds = 0;
		pfd[nfds].fd = P.ctl_listen;
		pfd[nfds].events = POLLIN;
		kind[nfds++] = -1;
		if (P.ui_fd != -1) {
			pfd[nfds].fd = P.ui_fd;
			pfd[nfds].events = POLLIN |
			    (imsgbuf_queuelen(&P.ui_ib) ? POLLOUT : 0);
			kind[nfds++] = -2;
		}
		for (i = 0; i < P.nlinks; i++)
			if (P.links[i].fd != -1) {
				pfd[nfds].fd = P.links[i].fd;
				pfd[nfds].events = POLLIN |
				    (imsgbuf_queuelen(&P.links[i].ib) ?
				    POLLOUT : 0);
				kind[nfds++] = 100 + i;
			}
		for (i = 0; i < MAX_CTL; i++)
			if (P.ctl[i].fd != -1) {
				pfd[nfds].fd = P.ctl[i].fd;
				pfd[nfds].events = POLLIN |
				    (imsgbuf_queuelen(&P.ctl[i].ib) ?
				    POLLOUT : 0);
				kind[nfds++] = 200 + i;
			}
		if (poll(pfd, nfds, 1000) == -1 && errno != EINTR)
			fatalx("parent: poll");
		restarted = 0;
		while ((pid = waitpid(-1, &status, WNOHANG)) > 0)
			if (child_forget(pid)) {
				child_died(pid, status);
				restarted = 1;
			}
		if (P.quit)
			break;
		/*
		 * After a restart pfd[] and kind[] describe descriptors that
		 * were closed, and the new ones may reuse their numbers:
		 * never act on those stale events.  Poll again.
		 */
		if (restarted)
			continue;
		for (i = 0; i < nfds; i++) {
			if (pfd[i].revents == 0)
				continue;
			if (kind[i] == -1) {
				ctl_accept();
			} else if (kind[i] == -2) {
				if (P.ui_fd == -1)
					continue;
				if ((pfd[i].revents & POLLOUT) &&
				    imsgbuf_write(&P.ui_ib) == -1)
					continue;
				if (pfd[i].revents & (POLLIN | POLLHUP)) {
					struct imsg im;

					if (imsgbuf_read(&P.ui_ib) <= 0)
						continue;	/* reaped above */
					while (imsgbuf_get(&P.ui_ib, &im) == 1)
						imsg_free(&im);
				}
			} else if (kind[i] >= 100 && kind[i] < 200) {
				struct linkconf *l = &P.links[kind[i] - 100];

				if (l->fd == -1)
					continue;
				if ((pfd[i].revents & POLLOUT) &&
				    imsgbuf_write(&l->ib) == -1)
					continue;
				if (pfd[i].revents & (POLLIN | POLLHUP)) {
					if ((n = imsgbuf_read(&l->ib)) <= 0)
						continue;	/* reaped */
					link_dispatch(kind[i] - 100);
				}
			} else {
				int slot = kind[i] - 200;

				if (P.ctl[slot].fd == -1)
					continue;
				if (pfd[i].revents & (POLLIN | POLLHUP)) {
					if ((n = imsgbuf_read(&P.ctl[slot].ib))
					    <= 0) {
						ctl_close(slot);
						continue;
					}
					ctl_dispatch(slot);
				}
				if (P.ctl[slot].fd != -1 &&
				    imsgbuf_queuelen(&P.ctl[slot].ib) &&
				    imsgbuf_write(&P.ctl[slot].ib) == -1)
					ctl_close(slot);
			}
		}
		/* replies generated while dispatching */
		for (i = 0; i < MAX_CTL; i++)
			if (P.ctl[i].fd != -1 && imsgbuf_queuelen(&P.ctl[i].ib)
			    && imsgbuf_write(&P.ctl[i].ib) == -1)
				ctl_close(i);
		for (i = 0; i < P.nlinks; i++)
			if (P.links[i].fd != -1 &&
			    imsgbuf_queuelen(&P.links[i].ib))
				(void)imsgbuf_write(&P.links[i].ib);
	}
	log_info("parent: terminating");
	stop_children();
	freezero(P.key, P.keylen + 1);
	return 0;
}
