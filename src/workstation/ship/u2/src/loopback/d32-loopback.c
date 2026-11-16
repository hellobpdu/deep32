/*
 * libd32loopback.so: CDE session listeners on loopback only (U43, update 2).
 *
 * ToolTalk (libtt) and libICE bind their listening sockets to INADDR_ANY / in6addr_any, and CDE 2.5.4pre0 has no
 * option to change that. This library, preloaded into the CDE session (Xsession.d/0145.d32loopback exports
 * LD_PRELOAD), interposes bind(2): an IPv4 wildcard address becomes 127.0.0.1 and an IPv6 wildcard becomes ::1,
 * port unchanged; every other address and family passes through untouched. listen(2) on a socket nobody bound
 * (which the kernel would bind to the wildcard) is bound to loopback first. ToolTalk's clients already reach
 * ttsession through 127.0.0.1, so nothing changes for them.
 *
 * Limits, said plainly: ld.so ignores (and removes) LD_PRELOAD for set-user-ID and set-group-ID programs, so it does
 * not reach dtsession (setuid root on the node, for the screen lock) or anything dtsession starts (dtwm, dtfile,
 * dtexec, the front panel's actions), nor dtterm's shells. Those keep their wildcard ToolTalk and ICE sockets; pf's
 * default block (no inbound pass rule matches them) is what keeps the network off them, as before. What it does
 * cover: ttsession, the ToolTalk message server Xsession starts before dtsession (its *:<port> listener, the one
 * that accepts messages), and every other program Xsession starts directly.
 *
 * Build (the build VM): cc -O2 -Wall -Wextra -Werror -fPIC -shared -o libd32loopback.so d32-loopback.c
 */
#include <sys/types.h>
#include <sys/socket.h>

#include <netinet/in.h>

#include <dlfcn.h>
#include <errno.h>
#include <string.h>

static int (*real_bind)(int, const struct sockaddr *, socklen_t);

static int
next_bind(int s, const struct sockaddr *sa, socklen_t len)
{
	if (real_bind == NULL) {
		real_bind = (int (*)(int, const struct sockaddr *, socklen_t))dlsym(RTLD_NEXT, "bind");
		if (real_bind == NULL) {
			errno = ENOSYS;
			return -1;
		}
	}
	return real_bind(s, sa, len);
}

int
bind(int s, const struct sockaddr *sa, socklen_t len)
{
	if (sa != NULL && sa->sa_family == AF_INET && len >= (socklen_t)sizeof(struct sockaddr_in)) {
		struct sockaddr_in sin;

		memcpy(&sin, sa, sizeof sin);
		if (sin.sin_addr.s_addr == htonl(INADDR_ANY)) {
			sin.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
			return next_bind(s, (struct sockaddr *)&sin, sizeof sin);
		}
	} else if (sa != NULL && sa->sa_family == AF_INET6 && len >= (socklen_t)sizeof(struct sockaddr_in6)) {
		struct sockaddr_in6 sin6;

		memcpy(&sin6, sa, sizeof sin6);
		if (IN6_IS_ADDR_UNSPECIFIED(&sin6.sin6_addr)) {
			sin6.sin6_addr = in6addr_loopback;
			return next_bind(s, (struct sockaddr *)&sin6, sizeof sin6);
		}
	}
	return next_bind(s, sa, len);
}

int
listen(int s, int backlog)
{
	static int (*real_listen)(int, int);
	struct sockaddr_storage ss;
	socklen_t len = sizeof ss;

	if (real_listen == NULL) {
		real_listen = (int (*)(int, int))dlsym(RTLD_NEXT, "listen");
		if (real_listen == NULL) {
			errno = ENOSYS;
			return -1;
		}
	}
	/* an unbound inet socket: bind it to loopback (port 0, the kernel picks one) before the kernel binds it to *. */
	memset(&ss, 0, sizeof ss);
	if (getsockname(s, (struct sockaddr *)&ss, &len) == 0) {
		if (ss.ss_family == AF_INET && ((struct sockaddr_in *)&ss)->sin_port == 0 &&
		    ((struct sockaddr_in *)&ss)->sin_addr.s_addr == htonl(INADDR_ANY)) {
			struct sockaddr_in sin;

			memset(&sin, 0, sizeof sin);
			sin.sin_len = sizeof sin;
			sin.sin_family = AF_INET;
			sin.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
			if (next_bind(s, (struct sockaddr *)&sin, sizeof sin) == -1)
				return -1;
		} else if (ss.ss_family == AF_INET6 && ((struct sockaddr_in6 *)&ss)->sin6_port == 0 &&
		    IN6_IS_ADDR_UNSPECIFIED(&((struct sockaddr_in6 *)&ss)->sin6_addr)) {
			struct sockaddr_in6 sin6;

			memset(&sin6, 0, sizeof sin6);
			sin6.sin6_len = sizeof sin6;
			sin6.sin6_family = AF_INET6;
			sin6.sin6_addr = in6addr_loopback;
			if (next_bind(s, (struct sockaddr *)&sin6, sizeof sin6) == -1)
				return -1;
		}
	}
	return real_listen(s, backlog);
}
