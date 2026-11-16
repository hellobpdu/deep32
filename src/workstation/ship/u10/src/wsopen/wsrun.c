#include "ws41.h"

#include <sys/stat.h>
#include <unistd.h>

static volatile sig_atomic_t cancelled;

static void
cancel(int signal_number)
{
	(void)signal_number;
	cancelled = 1;
}

static enum d32_error
install_handlers(void)
{
	const int signals[] = { SIGHUP, SIGINT, SIGTERM };
	struct sigaction action = { .sa_handler = cancel };
	if (sigemptyset(&action.sa_mask) == -1)
		return D32E_IO;
	for (size_t i = 0; i < sizeof(signals) / sizeof(signals[0]); ++i)
		if (sigaction(signals[i], &action, NULL) == -1)
			return D32E_IO;
	return D32E_OK;
}

static enum d32_error
input_kind(bool *empty)
{
	struct stat input, null;
	*empty = false;
	if (fstat(STDIN_FILENO, &input) == -1)
		return D32E_IO;
	if (S_ISREG(input.st_mode) || S_ISFIFO(input.st_mode))
		return D32E_OK;
	if (!S_ISCHR(input.st_mode))
		return D32E_NOTREG;
	if (stat("/dev/null", &null) == -1)
		return D32E_IO;
	if (!S_ISCHR(null.st_mode) || input.st_dev != null.st_dev ||
	    input.st_ino != null.st_ino || input.st_rdev != null.st_rdev)
		return D32E_NOTREG;
	*empty = true;
	return D32E_OK;
}

int
main(int argc, char **argv)
{
	uint64_t start = ws_now_ms();
	struct ws_spool spool = { .fd = -1 };
	enum d32_error result, cleanup;
	const char *detail = "arguments";
	int dirfd = -1;
	bool empty = false;
	size_t bytes = 0;
	(void)argv;
	if (argc != 1) {
		result = D32E_INVAL;
		goto done;
	}
	(void)umask(077);
	detail = "signals";
	result = install_handlers();
	if (result != D32E_OK)
		goto done;
	/* A gated build returns before looking at or reading stdin. */
	detail = "gated";
	result = ws_backend_open(&dirfd);
	if (result != D32E_OK)
		goto done;
	if (cancelled != 0)
		goto interrupted;
	detail = "spool";
	result = ws_spool_sweep(dirfd);
	if (result != D32E_OK)
		goto done;
	if (cancelled != 0)
		goto interrupted;
	detail = "input";
	result = input_kind(&empty);
	if (result != D32E_OK)
		goto done;
	if (!empty) {
		result = ws_spool_copy(STDIN_FILENO, dirfd, &spool, &bytes, &cancelled);
		if (result != D32E_OK)
			goto done;
	}
	if (cancelled != 0)
		goto interrupted;
	detail = "viewer";
	result = ws_backend_view(dirfd, empty ? NULL : spool.name, &cancelled);
	if (result == D32E_OK)
		detail = "complete";
	goto done;

interrupted:
	result = D32E_TEMP;
done:
	if (cancelled != 0 && (result == D32E_OK || result == D32E_TEMP)) {
		result = D32E_TEMP;
		detail = "cancelled";
	}
	cleanup = ws_spool_remove(dirfd, &spool);
	if (dirfd != -1 && close(dirfd) == -1 && cleanup == D32E_OK)
		cleanup = D32E_IO;
	if (cleanup != D32E_OK) {
		result = cleanup;
		detail = "cleanup";
	}
	return ws_finish("wireshark-run", empty ? "empty" : "receive", result, detail, bytes, start);
}
