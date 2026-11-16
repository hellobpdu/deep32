/* spawn.c: helper processes over a socketpair with imsg framing (spawn.h). */
#if defined(__OpenBSD__) && !defined(_BSD_SOURCE)
#define _BSD_SOURCE
#endif
#include "spawn.h"
#include <errno.h>
#include <fcntl.h>
#include <imsg.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/uio.h>
#include <sys/wait.h>
#include <unistd.h>

static int open_ib(e26_spawn *sp, int fd)
{
	sp->fd = fd;
	sp->ib = (struct imsgbuf *)calloc(1, sizeof(struct imsgbuf));
	if (!sp->ib || imsgbuf_init(sp->ib, fd) == -1) { free(sp->ib); sp->ib = NULL; return -1; }
	if (imsgbuf_set_maxsize(sp->ib, SPAWN_MAX + IMSG_HEADER_SIZE) == -1) { imsgbuf_clear(sp->ib); free(sp->ib); sp->ib = NULL; return -1; }
	return 0;
}

static int pair(int sv[2])
{
	if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == -1) return -1;
	fcntl(sv[0], F_SETFD, FD_CLOEXEC);
	return 0;
}

int spawn_exec(e26_spawn *sp, const char *path, char *const argv[])
{
	int sv[2];
	memset(sp, 0, sizeof(*sp));
	sp->fd = -1;
	snprintf(sp->name, sizeof sp->name, "%s", argv && argv[0] ? argv[0] : path);
	if (pair(sv)) return -1;
	sp->pid = fork();
	if (sp->pid == -1) { close(sv[0]); close(sv[1]); return -1; }
	if (sp->pid == 0) {
		/* the helper's end becomes descriptor 3; everything else above 2 is closed by exec (CLOEXEC) or here */
		if (sv[1] != SPAWN_FD) { if (dup2(sv[1], SPAWN_FD) == -1) _exit(127); close(sv[1]); }
		else fcntl(SPAWN_FD, F_SETFD, 0);
		setenv("E26_FD", "3", 1);
		execv(path, argv);
		_exit(127);
	}
	close(sv[1]);
	if (open_ib(sp, sv[0])) { close(sv[0]); kill(sp->pid, SIGTERM); return -1; }
	return 0;
}

static void detach_failure(int fd)
{
	int error = errno;
	ssize_t n;
	do n = write(fd, &error, sizeof error); while (n == -1 && errno == EINTR);
	_exit(127);
}

static void detach_child(int status, const char *home, const char *path,
    char *const argv[], char *const env[])
{
	struct sigaction action = { .sa_handler = SIG_DFL };
	sigset_t empty;
	int fd, i, rc;
	pid_t child;

	if (setsid() == -1) detach_failure(status);
	child = fork();
	if (child == -1) detach_failure(status);
	if (child > 0) _exit(0);

	/* Keep the status channel at fd 3 while clearing the inherited table. */
	if (status != 3) {
		if (dup3(status, 3, O_CLOEXEC) == -1) detach_failure(status);
		close(status);
		status = 3;
	}
	fd = open("/dev/null", O_RDWR);
	if (fd == -1) detach_failure(status);
	for (i = 0; i < 3; i++)
		if (dup2(fd, i) == -1) detach_failure(status);
	do rc = closefrom(4); while (rc == -1 && errno == EINTR);
	if (rc == -1 && errno != EBADF) detach_failure(status);

	if ((!home || !*home || chdir(home) == -1) && chdir("/") == -1)
		detach_failure(status);
	sigemptyset(&action.sa_mask);
	for (i = 1; i < NSIG; i++)
		if (i != SIGKILL && i != SIGSTOP &&
		    sigaction(i, &action, NULL) == -1 && errno != EINVAL)
			detach_failure(status);
	sigemptyset(&empty);
	if (sigprocmask(SIG_SETMASK, &empty, NULL) == -1) detach_failure(status);
	execve(path, argv, env);
	detach_failure(status);
}

static int detach_status(int fd)
{
	int error;
	unsigned char *bytes = (unsigned char *)&error;
	size_t got = 0;
	while (got < sizeof error) {
		ssize_t n = read(fd, bytes + got, sizeof error - got);
		if (n == 0) return got ? EIO : 0;
		if (n == -1) {
			if (errno == EINTR) continue;
			return errno;
		}
		got += (size_t)n;
	}
	return error;
}

int spawn_detached(const char *path, char *const argv[])
{
	extern char **environ;
	char **env;
	const char *home = getenv("HOME");
	size_t count = 0, i, kept = 0;
	int status[2], error, wait_error = 0, child_status;
	pid_t child, reaped;

	while (environ && environ[count]) count++;
	env = calloc(count + 1, sizeof *env);
	if (!env) return -1;
	for (i = 0; i < count; i++)
		if (strncmp(environ[i], "E26_", 4)) env[kept++] = environ[i];

	if (pipe2(status, O_CLOEXEC) == -1) {
		error = errno;
		free(env);
		errno = error;
		return -1;
	}
	child = fork();
	if (child == 0) {
		close(status[0]);
		detach_child(status[1], home, path, argv, env);
	}
	error = errno;
	close(status[1]);
	free(env);
	if (child != -1) {
		do reaped = waitpid(child, &child_status, 0); while (reaped == -1 && errno == EINTR);
		if (reaped == -1 && errno != ECHILD) wait_error = errno;
		if (reaped == child && (!WIFEXITED(child_status) || WEXITSTATUS(child_status)))
			wait_error = EIO;
		error = detach_status(status[0]);
		if (!error) error = wait_error;
	}
	close(status[0]);
	if (!error) return 0;
	errno = error;
	return -1;
}

int spawn_fork(e26_spawn *sp, const char *name, int (*child)(int fd, void *ud), void *ud)
{
	int sv[2];
	memset(sp, 0, sizeof(*sp));
	sp->fd = -1;
	snprintf(sp->name, sizeof sp->name, "%s", name);
	if (pair(sv)) return -1;
	sp->pid = fork();
	if (sp->pid == -1) { close(sv[0]); close(sv[1]); return -1; }
	if (sp->pid == 0) {
		close(sv[0]);
		_exit(child(sv[1], ud));
	}
	close(sv[1]);
	if (open_ib(sp, sv[0])) { close(sv[0]); kill(sp->pid, SIGTERM); return -1; }
	return 0;
}

int spawn_flush(e26_spawn *sp)
{
	if (!sp->ib) return -1;
	return imsgbuf_flush(sp->ib) == -1 ? -1 : 0;
}

int spawn_send(e26_spawn *sp, uint32_t type, const void *data, size_t len)
{
	if (!sp->ib || len > SPAWN_MAX) return -1;
	if (imsg_compose(sp->ib, type, 0, 0, -1, data, len) == -1) return -1;
	return spawn_flush(sp);
}

int spawn_read(e26_spawn *sp)
{
	int n;
	if (!sp->ib) return -1;
	n = imsgbuf_read(sp->ib);
	if (n == -1) return errno == EAGAIN ? 1 : -1;
	return n == 0 ? 0 : 1;
}

int spawn_get(e26_spawn *sp, uint32_t *type, void *buf, size_t cap, size_t *len)
{
	struct imsg m;
	ssize_t n;
	size_t l;
	if (!sp->ib) return -1;
	n = imsgbuf_get(sp->ib, &m);
	if (n <= 0) return n == 0 ? 0 : -1;
	*type = imsg_get_type(&m);
	l = imsg_get_len(&m);
	*len = l;
	if (l > cap || (l && imsg_get_data(&m, buf, l) == -1)) { imsg_free(&m); return -1; }
	imsg_free(&m);
	return 1;
}

void spawn_close(e26_spawn *sp)
{
	if (sp->ib) { imsgbuf_clear(sp->ib); free(sp->ib); sp->ib = NULL; }
	if (sp->fd >= 0) { close(sp->fd); sp->fd = -1; }
	if (sp->pid > 0) { kill(sp->pid, SIGTERM); sp->pid = 0; }
}

int spawn_child_fd(void)
{
	const char *e = getenv("E26_FD");
	int fd = e && e[0] ? atoi(e) : SPAWN_FD;
	return fd >= 0 ? fd : SPAWN_FD;
}

int spawn_child_open(e26_spawn *sp, int fd)
{
	memset(sp, 0, sizeof(*sp));
	snprintf(sp->name, sizeof sp->name, "parent");
	return open_ib(sp, fd);
}
