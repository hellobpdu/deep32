/*
 * Process helpers: monotonic clock, privilege drop, re-exec of children
 * with fixed descriptors, imsg send/flush.
 */
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/uio.h>

#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <imsg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "chatd.h"

int64_t
now_ms(void)
{
	struct timespec	 ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* chroot to /var/empty and become uid/gid for good. */
void
proc_drop(uid_t uid, gid_t gid)
{
	uid_t	 r, e, s;

	if (chroot("/var/empty") == -1 || chdir("/") == -1)
		fatalx("proc_drop: chroot failed errno %d", errno);
	if (setgroups(1, &gid) == -1 || setresgid(gid, gid, gid) == -1 ||
	    setresuid(uid, uid, uid) == -1)
		fatalx("proc_drop: cannot drop privileges errno %d", errno);
	if (getresuid(&r, &e, &s) == -1 || r != uid || e != uid || s != uid)
		fatalx("proc_drop: uid check failed");
	if (uid != 0 && setuid(0) != -1)
		fatalx("proc_drop: regained root");
}

/*
 * fork + exec self as "-P role -I instance" with fds[i] at descriptor
 * 3 + i and nothing else open beyond stdio.
 */
pid_t
proc_exec(const char *self, const char *role, int instance, const int *fds,
    int nfds)
{
	char	 inst[16];
	char	*argv[6];
	int	 hi[16], i;
	pid_t	 pid;

	if (nfds > 12)
		return -1;
	switch ((pid = fork())) {
	case -1:
		return -1;
	case 0:
		break;
	default:
		return pid;
	}
	for (i = 0; i < nfds; i++)
		if ((hi[i] = fcntl(fds[i], F_DUPFD, 64)) == -1)
			_exit(1);
	for (i = 0; i < nfds; i++)
		if (dup2(hi[i], CHATD_FD_PARENT + i) == -1)
			_exit(1);
	closefrom(CHATD_FD_PARENT + nfds);
	snprintf(inst, sizeof(inst), "%d", instance);
	argv[0] = (char *)"d32-chatd";
	argv[1] = (char *)"-P";
	argv[2] = (char *)role;
	argv[3] = (char *)"-I";
	argv[4] = inst;
	argv[5] = NULL;
	execv(self, argv);
	_exit(1);
}

int
imsg_send(struct imsgbuf *ib, uint32_t type, const void *data, size_t len)
{
	if (imsg_compose(ib, type, 0, 0, -1, data, len) == -1)
		return -1;
	return 0;
}

/* Blocking flush, used during start-up hand-over only. */
int
imsg_flush_all(struct imsgbuf *ib)
{
	return imsgbuf_flush(ib);
}
