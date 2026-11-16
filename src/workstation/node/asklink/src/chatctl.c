/*
 * d32-chatctl: local control of d32-chatd.
 *
 *   d32-chatctl [-s socket] status
 *   d32-chatctl [-s socket] pair <link>     trust-changing: wscons console only
 *   d32-chatctl [-s socket] unpair <link>   local revocation (drops the link)
 *   d32-chatctl [-d dir] keygen             create the node identity
 *   d32-chatctl [-d dir] pin                show this node's certificate pin
 *
 * pair shows the ten words at this console and reads the relay's ten words
 * typed by the operator (sas.md items 5-6).  The console check is a guard
 * in this tool (an operator-safety rail, not a boundary): d32-chatd
 * accepts the request from any uid-0 peer on its root-only socket.  The
 * words shown are computed by the chatd parent, which also checks the
 * typed words itself before it persists a pin.  A test build compiled
 * with D32_TEST_NO_CONSOLE skips the ttyname check so that a harness can
 * type over SSH; it is never installed.
 */
#include <sys/types.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/un.h>

#include <err.h>
#include <errno.h>
#include <imsg.h>
#include <poll.h>
#include <regex.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "chatd.h"

static const char *const ls_names[] = { "unpaired", "waiting",
    "connecting", "handshake", "pairing", "active", "isolated" };

static __dead void
usage(void)
{
	fprintf(stderr, "usage: d32-chatctl [-s socket] status | pair link | "
	    "unpair link\n       d32-chatctl [-d dir] keygen | pin\n");
	exit(1);
}

static int
on_console(void)
{
#ifdef D32_TEST_NO_CONSOLE
	fprintf(stderr, "d32-chatctl: TEST BUILD: console check skipped\n");
	return 1;
#else
	const char	*t = ttyname(0);
	regex_t		 re;
	int		 ok;

	if (t == NULL)
		return 0;
	if (regcomp(&re, "^/dev/ttyC[0-9]+$", REG_EXTENDED | REG_NOSUB) != 0)
		return 0;
	ok = regexec(&re, t, 0, NULL, 0) == 0;
	regfree(&re);
	return ok;
#endif
}

static int
connect_ctl(const char *path, struct imsgbuf *ib)
{
	struct sockaddr_un	 sun;
	int			 fd;

	memset(&sun, 0, sizeof(sun));
	sun.sun_family = AF_UNIX;
	if (strlcpy(sun.sun_path, path, sizeof(sun.sun_path)) >=
	    sizeof(sun.sun_path))
		errx(1, "socket path too long");
	if ((fd = socket(AF_UNIX, SOCK_STREAM, 0)) == -1)
		err(1, "socket");
	if (connect(fd, (struct sockaddr *)&sun, sizeof(sun)) == -1)
		err(1, "connect %s", path);
	if (imsgbuf_init(ib, fd) == -1)
		err(1, "imsgbuf");
	return fd;
}

/* Waits up to ms for one message. */
static int
next_msg(struct imsgbuf *ib, int fd, struct imsg *imsg, int ms)
{
	struct pollfd	 pfd;
	int		 n;

	for (;;) {
		if ((n = imsgbuf_get(ib, imsg)) == -1)
			errx(1, "imsg");
		if (n == 1)
			return 1;
		if (imsgbuf_flush(ib) == -1)
			err(1, "write");
		pfd.fd = fd;
		pfd.events = POLLIN;
		if ((n = poll(&pfd, 1, ms)) == -1)
			err(1, "poll");
		if (n == 0)
			return 0;
		if ((n = imsgbuf_read(ib)) == -1)
			err(1, "read");
		if (n == 0)
			errx(1, "d32-chatd closed the connection");
	}
}

static void
print_status(struct imsgbuf *ib, int fd)
{
	struct imsg		 imsg;
	struct link_state	 st;
	char			 pin[2 * D32U_PIN_LEN + 1];
	int			 i;

	imsg_compose(ib, IMSG_CTL_STATUS, 0, 0, -1, NULL, 0);
	for (;;) {
		if (!next_msg(ib, fd, &imsg, 5000))
			errx(1, "timeout");
		if (imsg_get_type(&imsg) == IMSG_CTL_END) {
			if (imsg_get_data(&imsg, pin, sizeof(pin)) == 0) {
				pin[sizeof(pin) - 1] = '\0';
				printf("node pin %s\n", pin);
			}
			imsg_free(&imsg);
			return;
		}
		if (imsg_get_data(&imsg, &st, sizeof(st)) == -1)
			errx(1, "status");
		st.name[sizeof(st.name) - 1] = '\0';
		st.relay_id[sizeof(st.relay_id) - 1] = '\0';
		printf("link %s: %s, %s%s%s, sessions %d, turns %lld, "
		    "mcp calls %lld (refused %lld), connects %lld\n", st.name,
		    ls_names[st.state >= 0 && st.state <= LS_ISOLATED ?
		    st.state : 0], st.paired ? "pinned to " : "no pin",
		    st.paired ? st.relay_id : "", "", st.sessions,
		    (long long)st.turns, (long long)st.mcp_calls,
		    (long long)st.mcp_refused, (long long)st.connects);
		if (st.state == LS_ACTIVE)
			for (i = 0; i < st.nmodels && i < 16; i++)
				printf("  model %.64s\n", st.models[i]);
		imsg_free(&imsg);
	}
}

static int
pair(struct imsgbuf *ib, int fd, const char *link)
{
	struct imsg		 imsg;
	struct ctl_link		 cl;
	struct pair_code	 pc;
	struct pair_result	 r;
	char			 line[SAS_INPUT_MAX + 2], *w, *last;
	size_t			 len;
	int			 k;

	if (!on_console())
		errx(1, "pair is trust-changing: run it at the node's wscons "
		    "console (Ctrl-Alt-F1), not from a pty");
	memset(&cl, 0, sizeof(cl));
	if (strlcpy(cl.name, link, sizeof(cl.name)) >= sizeof(cl.name))
		errx(1, "link name");
	imsg_compose(ib, IMSG_CTL_PAIR, 0, 0, -1, &cl, sizeof(cl));
	printf("Pairing window open for five minutes on link %s; one "
	    "connection attempt.\nOpen the relay's pairing window too.\n",
	    link);
	fflush(stdout);
	if (!next_msg(ib, fd, &imsg, (D32U_PAIR_SEC + 10) * 1000))
		errx(1, "pairing window expired");
	if (imsg_get_type(&imsg) == IMSG_CTL_RESULT) {
		if (imsg_get_data(&imsg, &r, sizeof(r)) == -1)
			errx(1, "result");
		errx(1, "pairing refused or failed (D32U code %d)%s", r.code,
		    r.code == E_AUTH ? ": unpair the link first if it holds "
		    "a pin" : "");
	}
	if (imsg_get_type(&imsg) != IMSG_CTL_PAIR_CODE ||
	    imsg_get_data(&imsg, &pc, sizeof(pc)) == -1)
		errx(1, "unexpected reply");
	imsg_free(&imsg);
	pc.relay_id[sizeof(pc.relay_id) - 1] = '\0';
	pc.code[sizeof(pc.code) - 1] = '\0';
	printf("\nConnected to relay id \"%s\". This node shows:\n\n",
	    pc.relay_id);
	for (k = 1, w = strtok_r(pc.code, " ", &last); w;
	    w = strtok_r(NULL, " ", &last), k++)
		printf("  %02d %s\n", k, w);
	explicit_bzero(&pc, sizeof(pc));
	printf("\nType these ten words at the relay.\nThen type the ten "
	    "words shown at the relay here, and press Enter:\n> ");
	fflush(stdout);
	if (fgets(line, sizeof(line), stdin) == NULL)
		errx(1, "no input");
	len = strcspn(line, "\n");
	line[len] = '\0';
	imsg_compose(ib, IMSG_CTL_TYPED, 0, 0, -1, line, len);
	explicit_bzero(line, sizeof(line));
	if (imsgbuf_flush(ib) == -1)
		err(1, "write");
	if (!next_msg(ib, fd, &imsg, (D32U_PAIR_SEC + 10) * 1000))
		errx(1, "no result");
	if (imsg_get_type(&imsg) != IMSG_CTL_RESULT ||
	    imsg_get_data(&imsg, &r, sizeof(r)) == -1)
		errx(1, "unexpected reply");
	if (!r.ok) {
		printf("PAIRING FAILED (D32U code %d); nothing was trusted.\n",
		    r.code);
		return 1;
	}
	printf("PAIRED: pin stored; the link is active.\n");
	return 0;
}

int
main(int argc, char *argv[])
{
	const char	*sock = CHATD_CTL_SOCK, *dir = CHATD_STATE;
	struct imsgbuf	 ib;
	struct imsg	 imsg;
	struct ctl_link	 cl;
	struct pair_result r;
	char		*cert, *key, hex[2 * D32U_PIN_LEN + 1];
	size_t		 cl_len, kl;
	uint8_t		 pin[D32U_PIN_LEN];
	int		 ch, fd, rv = 0;
	struct rlimit	 rl = { 0, 0 };

	/* keygen and pin read the node key: never into a core file */
	if (setrlimit(RLIMIT_CORE, &rl) == -1)
		err(1, "setrlimit");
	while ((ch = getopt(argc, argv, "d:s:")) != -1) {
		switch (ch) {
		case 'd':
			dir = optarg;
			break;
		case 's':
			sock = optarg;
			break;
		default:
			usage();
		}
	}
	argc -= optind;
	argv += optind;
	if (argc < 1)
		usage();
	if (strcmp(argv[0], "keygen") == 0 || strcmp(argv[0], "pin") == 0) {
		if (argc != 1)
			usage();
		if (strcmp(argv[0], "keygen") == 0) {
			if (geteuid() != 0)
				errx(1, "need root");
			if (ident_generate(dir) == -1)
				err(1, "keygen in %s", dir);
		}
		if (ident_load(dir, &cert, &cl_len, &key, &kl) == -1)
			err(1, "identity in %s", dir);
		freezero(key, kl + 1);
		if (ident_pin(cert, cl_len, pin) == -1)
			errx(1, "certificate");
		hex_encode(pin, sizeof(pin), hex);
		printf("node pin %s\n", hex);
		return 0;
	}
	fd = connect_ctl(sock, &ib);
	if (strcmp(argv[0], "status") == 0 && argc == 1)
		print_status(&ib, fd);
	else if (strcmp(argv[0], "pair") == 0 && argc == 2)
		rv = pair(&ib, fd, argv[1]);
	else if (strcmp(argv[0], "unpair") == 0 && argc == 2) {
		memset(&cl, 0, sizeof(cl));
		if (strlcpy(cl.name, argv[1], sizeof(cl.name)) >=
		    sizeof(cl.name))
			errx(1, "link name");
		imsg_compose(&ib, IMSG_CTL_UNPAIR, 0, 0, -1, &cl, sizeof(cl));
		if (!next_msg(&ib, fd, &imsg, 5000) ||
		    imsg_get_data(&imsg, &r, sizeof(r)) == -1)
			errx(1, "no reply");
		printf(r.ok ? "unpaired: pin deleted, link dropped\n" :
		    "unpair failed\n");
		rv = !r.ok;
	} else
		usage();
	close(fd);
	return rv;
}
