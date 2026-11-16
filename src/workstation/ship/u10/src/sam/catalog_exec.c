#include "catalog.h"

#include <stdio.h>
#include <string.h>

#define READY_OUTPUT_MAX 8192u
#define POLICY_OUTPUT_MAX 128u
#define READY_PATH "/usr/local/d32/sbin/d32ready"
#define UPDATER_PATH "/usr/local/d32/sbin/d32upd"
#define POLICY_PATH "/etc/doas.conf"
#define DOAS_PATH "/usr/bin/doas"
#define TERM_PATH "/usr/local/dt/bin/dtterm"

static bool
exact(const void *data, size_t length, const char *literal)
{
	return data != NULL && length == strlen(literal) &&
	    memcmp(data, literal, length) == 0;
}

enum sam_state
sam_policy_answer(int status, const void *data, size_t length)
{
	if (status == 0 && exact(data, length, "permit\n"))
		return SAM_OK;
	if (exact(data, length, "permit nopass\n") || exact(data, length, "deny\n"))
		return SAM_DENIED;
	return SAM_PREVIEW;
}

static bool
take_text(const char **at, const char *end, const char *literal)
{
	size_t n = strlen(literal);

	if ((size_t)(end - *at) < n || memcmp(*at, literal, n) != 0)
		return false;
	*at += n;
	return true;
}

static bool
take_count(const char **at, const char *end, unsigned *count)
{
	const char *start = *at;
	unsigned value = 0;

	while (*at < end && **at >= '0' && **at <= '9') {
		if (*at - start == 4 || (*at != start && *start == '0'))
			return false;
		value = value * 10 + (unsigned)(**at - '0');
		++*at;
	}
	*count = value;
	return *at != start;
}

enum sam_state
sam_ready_answer(int status, const void *data, size_t length,
    struct sam_result *result)
{
	const char *start = data, *line, *end, *next, *prefix;
	unsigned failed = 0, warnings = 0;

	if (result == NULL)
		return SAM_INVALID;
	result->value[0] = '\0';
	if (start == NULL || length == 0 || length > READY_OUTPUT_MAX ||
	    start[length - 1] != '\n' || memchr(start, '\0', length) != NULL)
		return SAM_INVALID;
	end = start + length;
	line = start;
	while ((next = memchr(line, '\n', (size_t)(end - line))) != end - 1) {
		prefix = line;
		if (take_text(&prefix, next, "The system is "))
			return SAM_INVALID;
		line = next + 1;
	}
	switch (status) {
	case 0:
		if (!exact(line, (size_t)(end - line), "The system is ready.\n"))
			return SAM_INVALID;
		(void)snprintf(result->value, sizeof(result->value), "The system is ready.");
		break;
	case 1:
		if (!take_text(&line, end, "The system is NOT ready: ") ||
		    !take_count(&line, end, &failed) || failed == 0 ||
		    !take_text(&line, end, " failed, ") ||
		    !take_count(&line, end, &warnings) ||
		    !exact(line, (size_t)(end - line), " warnings.\n"))
			return SAM_INVALID;
		(void)snprintf(result->value, sizeof(result->value),
		    "The system is NOT ready: %u failed, %u warnings.", failed, warnings);
		break;
	case 2:
		if (!take_text(&line, end, "The system is ready, with ") ||
		    !take_count(&line, end, &warnings) || warnings == 0 ||
		    !exact(line, (size_t)(end - line), " warnings.\n"))
			return SAM_INVALID;
		(void)snprintf(result->value, sizeof(result->value),
		    "The system is ready, with %u warnings.", warnings);
		break;
	default:
		return SAM_INVALID;
	}
	return SAM_OK;
}

#ifdef __OpenBSD__

#include "d32core.h"

#include <sys/stat.h>
#include <sys/wait.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <sha2.h>
#include <signal.h>
#include <unistd.h>

#define PROBE_TIMEOUT_MS 3000
#define PIN_FILE_MAX (256u * 1024u)

static bool
normal_user(void)
{
	return getuid() != 0 && getuid() == geteuid() && getgid() == getegid();
}

static bool
pinned_fd(int fd, const char *expected)
{
	unsigned char block[4096];
	char digest[SHA256_DIGEST_STRING_LENGTH];
	SHA2_CTX ctx;
	size_t total = 0;
	ssize_t n;

	SHA256Init(&ctx);
	for (;;) {
		n = read(fd, block, sizeof(block));
		if (n == -1 && errno == EINTR)
			continue;
		if (n < 0 || (size_t)n > PIN_FILE_MAX - total)
			return false;
		if (n == 0)
			break;
		SHA256Update(&ctx, block, (size_t)n);
		total += (size_t)n;
	}
	SHA256End(&ctx, digest);
	return strcmp(digest, expected) == 0;
}

static bool
protected_file(const char *path, bool executable, const char *expected)
{
	struct stat st;
	int fd;
	bool ok;

	if (sam_open_protected(path, &fd) != SAM_OK)
		return false;
	ok = fstat(fd, &st) == 0 && S_ISREG(st.st_mode) &&
	    (!executable || access(path, X_OK) == 0) &&
	    (expected == NULL || (st.st_size > 0 && (uintmax_t)st.st_size <= PIN_FILE_MAX &&
	    pinned_fd(fd, expected)));
	(void)close(fd);
	return ok;
}

static int64_t
monotonic_ms(void)
{
	struct timespec now;

	if (clock_gettime(CLOCK_MONOTONIC, &now) == -1)
		return -1;
	return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static int
capture_output(int fd, pid_t child, int64_t deadline, char *data,
    size_t capacity, size_t *length)
{
	struct pollfd input = { .fd = fd, .events = POLLIN };
	bool eof = false, reaped = false;
	int status = 0, result = -1, timeout;
	int64_t now;
	pid_t waited;
	ssize_t n;

	*length = 0;
	for (;;) {
		now = monotonic_ms();
		if (now < 0 || now >= deadline)
			break;
		if (!reaped) {
			waited = waitpid(child, &status, WNOHANG);
			if (waited < 0 && errno == EINTR)
				continue;
			if (waited < 0)
				return -1;
			reaped = waited == child;
		}
		if (eof && reaped) {
			if (WIFEXITED(status))
				result = WEXITSTATUS(status);
			break;
		}
		if (eof) {
			timeout = deadline - now < 10 ? (int)(deadline - now) : 10;
			if (poll(NULL, 0, timeout) == -1 && errno != EINTR)
				break;
			continue;
		}
		if (poll(&input, 1, (int)(deadline - now)) == -1) {
			if (errno == EINTR)
				continue;
			break;
		}
		if (input.revents == 0)
			continue;
		/* The extra byte distinguishes a full buffer from oversized output. */
		n = read(fd, data + *length, capacity + 1 - *length);
		if (n == -1 && (errno == EINTR || errno == EAGAIN))
			continue;
		if (n < 0 || (size_t)n > capacity - *length)
			break;
		*length += (size_t)n;
		eof = n == 0;
	}
	if (!reaped) {
		(void)kill(child, SIGKILL);
		while (waitpid(child, NULL, 0) == -1 && errno == EINTR)
			;
	}
	return result;
}

static int
fixed_probe(char *const argv[], char *data, size_t capacity, size_t *length)
{
	int in[2], out[2], result = -1;
	int64_t now;
	pid_t child;

	if (pipe2(in, O_CLOEXEC) == -1)
		return -1;
	(void)close(in[1]);
	if (pipe2(out, O_CLOEXEC) == -1) {
		(void)close(in[0]);
		return -1;
	}
	now = monotonic_ms();
	if (now >= 0 && fcntl(out[0], F_SETFL, O_NONBLOCK) != -1 &&
	    d32_exec_fixed(argv[0], argv, in[0], out[1], out[1], &child) == D32E_OK) {
		(void)close(out[1]);
		result = capture_output(out[0], child, now + PROBE_TIMEOUT_MS,
		    data, capacity, length);
	} else
		(void)close(out[1]);
	(void)close(out[0]);
	(void)close(in[0]);
	return result;
}

#endif

enum sam_state
sam_boot_ready(const struct sam_context *ctx, struct sam_result *result)
{
#ifdef __OpenBSD__
	static const char pin[] =
	    "36ef7357604cfa3e0079e247236d140ed7d65d230b648fab2f66b11240f9fd8b"; /* SHA-256 */
	char *const argv[] = {READY_PATH, "-q", NULL};
	char output[READY_OUTPUT_MAX + 1];
	size_t length = 0;
	int status;

	if (result == NULL)
		return SAM_INVALID;
	if ((ctx != NULL && ctx->root != NULL && strcmp(ctx->root, "/") != 0) ||
	    !normal_user() || !protected_file(READY_PATH, true, pin))
		return SAM_UNAVAILABLE;
	status = fixed_probe(argv, output, READY_OUTPUT_MAX, &length);
	if (status >= 0 && sam_ready_answer(status, output, length, result) == SAM_OK)
		return SAM_OK;
#else
	(void)ctx;
	(void)result;
#endif
	return SAM_UNAVAILABLE;
}

enum sam_state
sam_terminal_check(const struct sam_operation *op)
{
	if (op == NULL || (op != sam_find("upd.apply") && op != sam_find("upd.rollback")))
		return SAM_INVALID;
#ifdef __OpenBSD__
	static const char updater_pin[] =
	    "b2b5fac219fde23e7130b414c7deeefe57fd5c52c443f2ebb2ee27fac1d3cfdc"; /* SHA-256 */
	static const char policy_pin[] =
	    "a7d00a64e01820fc5baa361c9819b71e5d1a9b4c263bcfe55e52d91c587048c6"; /* SHA-256 */
	char *const argv[] = {DOAS_PATH, "-C", POLICY_PATH, UPDATER_PATH,
	    (char *)op->argv[6], NULL};
	char output[POLICY_OUTPUT_MAX + 1];
	size_t length = 0;
	int status;

	if (strcmp(op->argv[0], TERM_PATH) == 0 && strcmp(op->argv[4], DOAS_PATH) == 0 &&
	    strcmp(op->argv[5], UPDATER_PATH) == 0 && normal_user() &&
	    protected_file(UPDATER_PATH, true, updater_pin) &&
	    protected_file(POLICY_PATH, false, policy_pin) &&
	    protected_file(DOAS_PATH, true, NULL) &&
	    protected_file(TERM_PATH, true, NULL)) {
		status = fixed_probe(argv, output, POLICY_OUTPUT_MAX, &length);
		if (status >= 0)
			return sam_policy_answer(status, output, length);
	}
#endif
	return SAM_PREVIEW;
}
