#if defined(__linux__) && !defined(_GNU_SOURCE)
#define _GNU_SOURCE
#endif

#include "d32core.h"

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>

enum { EXEC_ENV_SIZE = 4096, EXEC_REPORT_FD = 3 };

static enum d32_error
exec_error(int error)
{
	switch (error) {
	case EACCES:
	case EPERM:
		return D32E_PERM;
	case EBADF:
	case EINVAL:
		return D32E_INVAL;
	case ENOENT:
	case ENOTDIR:
	case ENOEXEC:
		return D32E_UNAVAIL;
	case EAGAIN:
	case EMFILE:
	case ENFILE:
	case ETXTBSY:
		return D32E_TEMP;
	case ENOMEM:
		return D32E_NOMEM;
	case E2BIG:
	case ENAMETOOLONG:
		return D32E_TOOBIG;
	default:
		return D32E_IO;
	}
}

static int
env_entry(char dst[EXEC_ENV_SIZE], const char *key, const char *fallback)
{
	const char *value = getenv(key);
	int n = snprintf(dst, EXEC_ENV_SIZE, "%s=%s", key,
	    value != NULL ? value : fallback);

	return n >= 0 && n < EXEC_ENV_SIZE;
}

static int
copy_fd(int fd)
{
	int result;

	do {
		result = fcntl(fd, F_DUPFD_CLOEXEC, EXEC_REPORT_FD);
	} while (result == -1 && errno == EINTR);
	return result;
}

static int
replace_fd(int from, int to)
{
	int result;

	do {
		result = dup2(from, to);
	} while (result == -1 && errno == EINTR);
	return result;
}

static void
report_error(int fd, int error)
{
	const unsigned char *p = (const unsigned char *)&error;
	size_t done = 0;
	ssize_t n;

	while (done < sizeof(error)) {
		n = write(fd, p + done, sizeof(error) - done);
		if (n == -1 && errno == EINTR)
			continue;
		if (n <= 0)
			break;
		done += (size_t)n;
	}
	_exit(127);
}

static void
run_child(const char *abs, char *const argv[], char *const env[],
    const int streams[3], int read_end, int report)
{
	int i;

	(void)close(read_end);
	for (i = 0; i < 3; i++) {
		if (streams[i] != -1 && replace_fd(streams[i], i) == -1)
			report_error(report, errno);
	}
	if (report != EXEC_REPORT_FD) {
		if (replace_fd(report, EXEC_REPORT_FD) == -1)
			report_error(report, errno);
		report = EXEC_REPORT_FD;
		if (fcntl(report, F_SETFD, FD_CLOEXEC) == -1)
			report_error(report, errno);
	}
	(void)closefrom(EXEC_REPORT_FD + 1);
	execve(abs, argv, env);
	report_error(report, errno);
}

static enum d32_error
exec_result(int fd, pid_t child)
{
	int error;
	unsigned char *p = (unsigned char *)&error;
	size_t done = 0;
	ssize_t n;

	while (done < sizeof(error)) {
		n = read(fd, p + done, sizeof(error) - done);
		if (n == -1 && errno == EINTR)
			continue;
		if (n == 0 && done == 0)
			return D32E_OK;
		if (n <= 0)
			break;
		done += (size_t)n;
	}
	if (done != sizeof(error)) {
		(void)kill(child, SIGKILL);
		error = EIO;
	}
	while (waitpid(child, NULL, 0) == -1 && errno == EINTR)
		;
	return exec_error(error);
}

enum d32_error
d32_exec_fixed(const char *abs, char *const argv[], int stdin_fd, int stdout_fd,
    int stderr_fd, pid_t *pid)
{
	const int requested[] = {stdin_fd, stdout_fd, stderr_fd};
	int streams[] = {-1, -1, -1};
	int pipefd[] = {-1, -1};
	char path[] = "PATH=/usr/bin:/bin";
	char home[EXEC_ENV_SIZE], display[EXEC_ENV_SIZE], lang[EXEC_ENV_SIZE];
	char *const env[] = {path, home, display, lang, NULL};
	enum d32_error result;
	pid_t child;
	int i, fd;

	if (pid == NULL)
		return D32E_INVAL;
	*pid = -1;
	if (abs == NULL || abs[0] != '/' || abs[1] == '\0' ||
	    argv == NULL || argv[0] == NULL || argv[0][0] == '\0')
		return D32E_INVAL;
	if (!env_entry(home, "HOME", "") || !env_entry(display, "DISPLAY", "") ||
	    !env_entry(lang, "LANG", "C"))
		return D32E_TOOBIG;
	for (i = 0; i < 3; i++) {
		if (requested[i] < -1)
			return D32E_INVAL;
		if (requested[i] != -1 && fcntl(requested[i], F_GETFD) == -1)
			return exec_error(errno);
	}
	/* Copy mappings before fork so remapping one stdio fd cannot affect
	 * another mapping (for example, swapping stdout and stderr). */
	for (i = 0; i < 3; i++) {
		if (requested[i] == -1)
			continue;
		if ((streams[i] = copy_fd(requested[i])) == -1) {
			result = exec_error(errno);
			goto done;
		}
	}
	if (pipe2(pipefd, O_CLOEXEC) == -1) {
		result = exec_error(errno);
		goto done;
	}
	for (i = 0; i < 2; i++) {
		if (pipefd[i] >= EXEC_REPORT_FD)
			continue;
		if ((fd = copy_fd(pipefd[i])) == -1) {
			result = exec_error(errno);
			goto done;
		}
		(void)close(pipefd[i]);
		pipefd[i] = fd;
	}
	child = fork();
	if (child == -1) {
		result = exec_error(errno);
		goto done;
	}
	if (child == 0)
		run_child(abs, argv, env, streams, pipefd[0], pipefd[1]);
	(void)close(pipefd[1]);
	pipefd[1] = -1;
	result = exec_result(pipefd[0], child);
	if (result == D32E_OK)
		*pid = child;
done:
	for (i = 0; i < 3; i++)
		if (streams[i] != -1)
			(void)close(streams[i]);
	for (i = 0; i < 2; i++)
		if (pipefd[i] != -1)
			(void)close(pipefd[i]);
	return result;
}
