#include "ws41.h"

#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#ifndef D32_WS_DOAS
#define D32_WS_DOAS "/usr/bin/doas"
#endif
#ifndef D32_WS_RUN
#define D32_WS_RUN "/usr/local/d32/sbin/wireshark-run"
#endif
#ifndef D32_WS_SESSION
#define D32_WS_SESSION "/d32/a/bin/d32-ws-session"
#endif

static volatile sig_atomic_t cancelled;

static void
notice_signal(int signal_number)
{
	if (signal_number != SIGCHLD)
		cancelled = 1;
}

static bool
watch_signals(sigset_t *signals)
{
	const int numbers[] = {SIGHUP, SIGINT, SIGTERM, SIGCHLD};
	struct sigaction action = {.sa_handler = notice_signal};
	if (sigemptyset(signals) == -1 || sigemptyset(&action.sa_mask) == -1)
		return false;
	for (size_t i = 0; i < sizeof(numbers) / sizeof(numbers[0]); ++i) {
		if (sigaddset(signals, numbers[i]) == -1 ||
		    sigaction(numbers[i], &action, NULL) == -1)
			return false;
	}
	return sigprocmask(SIG_UNBLOCK, signals, NULL) == 0;
}

static pid_t
wait_retry(pid_t child, int *status, int options)
{
	pid_t result;
	do {
		result = waitpid(child, status, options);
	} while (result == -1 && errno == EINTR);
	return result;
}

static enum d32_error
cancel_peer(pid_t child, const sigset_t *unblocked)
{
	const struct timespec grace = {.tv_nsec = 250000000};
	int status;
	/* The installed target UID may prevent signalling across doas. The
	 * viewer owns its lifetime after that transition. Same-UID children
	 * can be stopped and reaped here, including an unresponsive mock. */
	if (kill(child, SIGTERM) == -1 && errno != ESRCH)
		return D32E_TEMP;
	(void)pselect(0, NULL, NULL, NULL, &grace, unblocked);
	if (wait_retry(child, &status, WNOHANG) == 0 &&
	    (kill(child, SIGKILL) == 0 || errno == ESRCH))
		(void)wait_retry(child, &status, 0);
	return D32E_TEMP;
}

static enum d32_error
wait_peer(pid_t child, const sigset_t *signals)
{
	int status;
	sigset_t unblocked;
	enum d32_error result = D32E_IO;
	if (sigprocmask(SIG_BLOCK, signals, &unblocked) == -1) {
		(void)wait_retry(child, &status, 0);
		return D32E_IO;
	}
	/* Test and sleep atomically with respect to cancellation and SIGCHLD. */
	for (;;) {
		pid_t received = wait_retry(child, &status, WNOHANG);
		if (received == child) {
			if (cancelled)
				result = D32E_TEMP;
			else if (WIFEXITED(status)) {
				int code = WEXITSTATUS(status);
				result = ws_error_from_exit(code);
			}
			break;
		}
		if (received == -1)
			break;
		if (cancelled) {
			result = cancel_peer(child, &unblocked);
			break;
		}
		if (pselect(0, NULL, NULL, NULL, NULL, &unblocked) == -1 &&
		    errno != EINTR) {
			(void)cancel_peer(child, &unblocked);
			break;
		}
	}
	(void)sigprocmask(SIG_SETMASK, &unblocked, NULL);
	return cancelled ? D32E_TEMP : result;
}

static enum d32_error
runner_available(void)
{
	struct stat st;
	if (lstat(D32_WS_RUN, &st) == -1) {
		if (errno == ENOENT || errno == ENOTDIR)
			return D32E_UNAVAIL;
		return errno == EACCES || errno == EPERM ? D32E_PERM : D32E_IO;
	}
	if (!S_ISREG(st.st_mode) || (st.st_mode & 0111) == 0)
		return D32E_UNAVAIL;
	if ((st.st_mode & (S_ISUID | S_ISGID | S_IWGRP | S_IWOTH)) != 0)
		return D32E_PERM;
#ifndef D32_WS_TEST_PEER
	if (st.st_uid != 0)
		return D32E_PERM;
#endif
	return D32E_OK;
}

int
main(int argc, char **argv)
{
	const uint64_t start = ws_now_ms();
	const bool empty = argc == 2 && strcmp(argv[1], "--empty") == 0;
	const char *verb = empty ? "empty" : "open";
	const char *detail = "arguments";
	int input = -1, sink = -1;
	size_t bytes = 0;
	enum d32_error result = D32E_INVAL;
	struct stat st;
	sigset_t signals;
	pid_t child;

	if (!empty && (argc != 3 || strcmp(argv[1], "--") != 0 || argv[2][0] == '\0'))
		goto done;
	if (!watch_signals(&signals)) {
		result = D32E_IO;
		detail = "failed";
		goto done;
	}
	sink = open("/dev/null", O_WRONLY | O_NOFOLLOW | O_CLOEXEC);
	if (sink == -1) {
		result = D32E_IO;
		detail = "failed";
		goto done;
	}
#ifdef __OpenBSD__
	if (pledge("stdio rpath proc exec", NULL) == -1) {
		result = D32E_PERM;
		detail = "failed";
		goto done;
	}
#endif
	if (empty) {
		input = open("/dev/null", O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
		if (input == -1) {
			result = D32E_IO;
			detail = "failed";
			goto done;
		}
	} else {
		detail = "input";
		result = d32_open_input(argv[2], D32_INPUT_PCAP, WS_MAX_BYTES, &input, &st);
		if (result != D32E_OK)
			goto done;
		result = ws_validate_fd(input, st.st_size);
		if (result != D32E_OK)
			goto done;
		bytes = (size_t)st.st_size;
	}
	result = runner_available();
	if (result != D32E_OK) {
		detail = "missing-runner";
		goto done;
	}
	if (cancelled) {
		result = D32E_TEMP;
		detail = "cancelled";
		goto done;
	}
	/* The already-open capture travels as stdin. Host X authority goes only
	 * to the same-UID process that creates Xephyr. */
#ifdef D32_WS_TEST_PEER
	char *const fixed_argv[] = {"doas", "-n", "-u", "d32net", D32_WS_RUN, NULL};
	result = d32_exec_fixed(D32_WS_DOAS, fixed_argv, input, sink, sink, &child);
#else
	char *authority = getenv("XAUTHORITY");
	char *const session_argv[] = {"d32-ws-session", empty ? "E" : "S",
	    authority != NULL ? authority : "", NULL};
	result = d32_exec_fixed(D32_WS_SESSION, session_argv, input, sink, sink, &child);
#endif
	if (input != -1) {
		(void)close(input);
		input = -1;
	}
	(void)close(sink);
	sink = -1;
	if (result == D32E_OK)
		result = wait_peer(child, &signals);
	detail = cancelled ? "cancelled" : result == D32E_OK ? "complete" : "peer";
done:
	if (input != -1)
		(void)close(input);
	if (sink != -1)
		(void)close(sink);
	return ws_finish("d32-wsopen", verb, result, detail, bytes, start);
}
