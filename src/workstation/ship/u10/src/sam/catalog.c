#include "catalog.h"
#include "d32core.h"

#include <sys/stat.h>

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#define RELEASE_FILE "/etc/d32/release"
#define UPDATER "/usr/local/d32/sbin/d32upd"
#define DTTERM "/usr/local/dt/bin/dtterm"
#define DTACTION "/usr/local/dt/bin/dtaction"
#define DOAS "/usr/bin/doas"
#define READY "/usr/local/d32/sbin/d32ready"
#define RELEASE_MAX 512u
#define NITEMS(a) (sizeof(a) / sizeof((a)[0]))

static enum sam_state release_marker(const struct sam_context *, struct sam_result *);

static enum sam_state
updater_denied(const struct sam_context *context, struct sam_result *out)
{
	(void)context;
	(void)out;
	return SAM_DENIED;
}

static enum sam_state
services_unavailable(const struct sam_context *context, struct sam_result *out)
{
	(void)context;
	(void)sam_append(out, "Started services require administrator access on this release.");
	return SAM_UNAVAILABLE;
}

static const char *const svc_argv[] = {"/usr/sbin/rcctl", "ls", "started", NULL};
static const char *const boot_argv[] = {READY, "-q", NULL};
static const char *const look_argv[] = {DTACTION, "D32LookProfile", NULL};
static const char *const sound_argv[] = {DTACTION, "D32Sound", NULL};
static const char *const audio_argv[] = {DTACTION, "AudioControl", NULL};
static const char *const apply_argv[] = {
	DTTERM, "-title", "Deep32 Admin: upd.apply", "-e", DOAS, UPDATER,
	"apply-staged", NULL
};
static const char *const rollback_argv[] = {
	DTTERM, "-title", "Deep32 Admin: upd.rollback", "-e", DOAS, UPDATER,
	"revert", NULL
};

static const struct sam_operation operations[] = {
	{"sys.summary", "System Summary", SAM_READ, SAM_SUMMARY, "current user",
	    NULL, "bytes", "OpenBSD sysctl", sam_system_summary},
	{"d32.release", "Deep32 Release", SAM_READ, SAM_SOFTWARE, "current user",
	    NULL, "release and sequence", RELEASE_FILE, release_marker},
	{"upd.state", "Updater State", SAM_READ, SAM_SOFTWARE, "current user",
	    NULL, "state", UPDATER " status", updater_denied},
	{"disk.free", "Local Disk Space", SAM_READ, SAM_STORAGE, "current user",
	    NULL, "bytes", "OpenBSD getfsstat (local mounts)", sam_disk_free},
	{"pkg.list", "Installed Packages", SAM_READ, SAM_SOFTWARE, "current user",
	    NULL, "package names", "/var/db/pkg", sam_package_list},
	{"svc.status", "Started Services", SAM_READ, SAM_SERVICES, "current user",
	    svc_argv, "service names", "/usr/sbin/rcctl ls started", services_unavailable},
	{"boot.ready", "Boot Readiness", SAM_READ, SAM_BOOT, "current user",
	    boot_argv, "checklist status", READY " -q", sam_boot_ready},
	{"look", "Desktop Look", SAM_DELEGATE, SAM_DESKTOP, "current user",
	    look_argv, "", "CDE action D32LookProfile", NULL},
	{"sound", "Sound", SAM_DELEGATE, SAM_DESKTOP, "current user",
	    sound_argv, "", "CDE action D32Sound", NULL},
	{"audio", "Audio Control", SAM_DELEGATE, SAM_DESKTOP, "current user",
	    audio_argv, "", "CDE action AudioControl", NULL},
	{"upd.apply", "Apply Staged Update", SAM_TERMINAL, SAM_SOFTWARE,
	    "authenticated administrator in terminal", apply_argv, "argv",
	    "Fixed updater command and current doas policy", NULL},
	{"upd.rollback", "Roll Back Update", SAM_TERMINAL, SAM_SOFTWARE,
	    "authenticated administrator in terminal", rollback_argv, "argv",
	    "Fixed updater command and current doas policy", NULL},
	{"prefs.sam", "SAM Preferences", SAM_PREF, SAM_DESKTOP, "current user",
	    NULL, "page and interval", "~/.dt/d32/sam.conf", NULL},
};

static const char *const categories[] = {"Summary", "Software & Updates",
	"Storage", "Services", "Boot Readiness", "Desktop Settings"};
static const char *const states[] = {"Available", "Unavailable", "Denied",
	"Unknown (invalid data)", "Unknown (read failed)", "Command Preview"};

const struct sam_operation *
sam_catalog(size_t *count)
{
	if (count != NULL)
		*count = NITEMS(operations);
	return operations;
}

const struct sam_operation *
sam_find(const char *id)
{
	size_t i;

	if (id != NULL)
		for (i = 0; i < NITEMS(operations); i++)
			if (strcmp(id, operations[i].id) == 0)
				return &operations[i];
	return NULL;
}

static bool
known(const struct sam_operation *op)
{
	size_t i;

	for (i = 0; i < NITEMS(operations); i++)
		if (op == &operations[i])
			return true;
	return false;
}

const char *
sam_category_name(enum sam_category category)
{
	return (unsigned)category < NITEMS(categories) ? categories[category] : NULL;
}

const char *
sam_state_name(enum sam_state state)
{
	return (unsigned)state < NITEMS(states) ? states[state] : "Unknown";
}

static void
initialize(const struct sam_operation *op, struct sam_result *out)
{
	memset(out, 0, sizeof(*out));
	out->state = SAM_INVALID;
	out->read_at = time(NULL);
	if (known(op))
		out->source = op->source;
}

enum sam_state
sam_probe(const struct sam_operation *op, const struct sam_context *context,
    struct sam_result *out)
{
	if (out == NULL)
		return SAM_INVALID;
	initialize(op, out);
	if (!known(op))
		return out->state;
	if (op->class == SAM_TERMINAL)
		return sam_command_preview(op, out);
	if (op->class != SAM_READ || op->probe == NULL)
		return out->state = SAM_UNAVAILABLE;
	return out->state = op->probe(context, out);
}

bool
sam_visible(const struct sam_operation *op, enum sam_state state)
{
	return known(op) && (op != sam_find("upd.state") || state == SAM_OK);
}

enum sam_state
sam_command_preview(const struct sam_operation *op, struct sam_result *out)
{
	size_t i;

	if (out == NULL)
		return SAM_INVALID;
	initialize(op, out);
	if (!known(op) || op->class != SAM_TERMINAL)
		return out->state;
	for (i = 0; op->argv[i] != NULL; i++)
		if (!sam_append(out, "%s\n", op->argv[i]))
			return out->state = SAM_IO;
	return out->state = SAM_PREVIEW;
}

void
sam_audit(const struct sam_operation *op, enum sam_state state)
{
	static const char *const results[] = {"ok", "unavailable", "noperm", "dataerr", "ioerr", "gated"};
	const char *allowed[1];

	if (!known(op) || (unsigned)state >= NITEMS(results))
		return;
	allowed[0] = op->id;
	(void)d32_log_allowed("d32-sam", allowed, NITEMS(allowed), "id=%s result=%s",
	    op->id, results[state]);
}

bool
sam_append(struct sam_result *out, const char *format, ...)
{
	size_t used = strnlen(out->value, sizeof(out->value));
	va_list args;
	int n;

	if (used >= sizeof(out->value) || out->truncated)
		return false;
	va_start(args, format);
	n = vsnprintf(out->value + used, sizeof(out->value) - used, format, args);
	va_end(args);
	if (n < 0 || (size_t)n >= sizeof(out->value) - used) {
		out->value[used] = '\0';
		out->truncated = true;
		return false;
	}
	return true;
}

enum sam_state
sam_error_from_errno(int error)
{
	switch (error) {
	case EACCES: case EPERM: return SAM_DENIED;
	case ENOENT: case ENOTDIR: return SAM_UNAVAILABLE;
	case ELOOP: case ENAMETOOLONG: return SAM_INVALID;
	default: return SAM_IO;
	}
}

static enum sam_state
check_fd(int fd, bool directory, bool protected)
{
	struct stat st;

	if (fstat(fd, &st) == -1)
		return sam_error_from_errno(errno);
	if (directory ? !S_ISDIR(st.st_mode) : !S_ISREG(st.st_mode))
		return SAM_INVALID;
	if (protected && (st.st_uid != 0 || (st.st_mode & 022) != 0))
		return SAM_DENIED;
	return SAM_OK;
}

static enum sam_state
open_path(const char *root, const char *path, bool directory, bool protected, int *out)
{
	const int flags = O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK;
	char component[NAME_MAX + 1];
	const char *separator;
	int dirfd, fd, error;
	size_t len;
	bool intermediate;
	enum sam_state result;

	if (out == NULL)
		return SAM_INVALID;
	*out = -1;
	if (root == NULL || root[0] != '/' || path == NULL || path[0] != '/' || path[1] == '\0')
		return SAM_INVALID;
	dirfd = open(root, flags | O_DIRECTORY);
	if (dirfd == -1)
		return sam_error_from_errno(errno);
	result = check_fd(dirfd, true, protected);
	for (path++; result == SAM_OK; path = separator + 1) {
		separator = strchr(path, '/');
		intermediate = separator != NULL;
		len = intermediate ? (size_t)(separator - path) : strlen(path);
		if (len == 0 || len > NAME_MAX || (len == 1 && path[0] == '.') ||
		    (len == 2 && path[0] == '.' && path[1] == '.')) {
			result = SAM_INVALID;
			break;
		}
		memcpy(component, path, len);
		component[len] = '\0';
		fd = openat(dirfd, component, flags | (intermediate || directory ? O_DIRECTORY : 0));
		if (fd == -1) {
			error = errno;
			result = sam_error_from_errno(error);
			break;
		}
		(void)close(dirfd);
		dirfd = fd;
		result = check_fd(fd, intermediate || directory, protected);
		if (!intermediate)
			break;
	}
	if (result != SAM_OK)
		(void)close(dirfd);
	else
		*out = dirfd;
	return result;
}

enum sam_state
sam_open_read(const struct sam_context *context, const char *path, bool directory, int *out)
{
	return open_path(context == NULL || context->root == NULL ? "/" : context->root,
	    path, directory, false, out);
}

enum sam_state
sam_open_protected(const char *path, int *out)
{
	return open_path("/", path, false, true, out);
}

static bool
release_character(unsigned char c)
{
	return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
	    (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '+' || c == '-';
}

static bool
hspace(unsigned char c)
{
	return c == ' ' || c == '\t';
}

enum sam_state
sam_release_parse(const void *data, size_t len, char release[SAM_RELEASE_SIZE], uint64_t *seq)
{
	const unsigned char *bytes = data;
	size_t token = 0, pos, start;
	uint64_t value = 0, digit;

	if (data == NULL || release == NULL || seq == NULL || len == 0 || len > RELEASE_MAX)
		return SAM_INVALID;
	while (token < len && release_character(bytes[token]))
		token++;
	if (token == 0 || token >= SAM_RELEASE_SIZE)
		return SAM_INVALID;
	pos = token;
	while (pos < len && hspace(bytes[pos]))
		pos++;
	if (pos < len && bytes[pos] == '\n')
		pos++;
	if (pos < len) {
		if (pos == token || len - pos < 5 || memcmp(bytes + pos, "seq", 3) != 0 ||
		    !hspace(bytes[pos + 3]))
			return SAM_INVALID;
		pos += 3;
		while (pos < len && hspace(bytes[pos]))
			pos++;
		start = pos;
		while (pos < len && bytes[pos] >= '0' && bytes[pos] <= '9') {
			digit = bytes[pos++] - '0';
			if (value > (UINT64_MAX - digit) / 10)
				return SAM_INVALID;
			value = value * 10 + digit;
		}
		if (pos == start)
			return SAM_INVALID;
		while (pos < len && hspace(bytes[pos]))
			pos++;
		if (pos < len && bytes[pos] == '\n')
			pos++;
		if (pos != len)
			return SAM_INVALID;
	}
	memcpy(release, bytes, token);
	release[token] = '\0';
	*seq = value;
	return SAM_OK;
}

static enum sam_state
release_marker(const struct sam_context *context, struct sam_result *out)
{
	char data[RELEASE_MAX + 1], release[SAM_RELEASE_SIZE];
	uint64_t sequence;
	size_t used = 0;
	ssize_t got;
	int fd;
	enum sam_state result;

	if (context == NULL || context->root == NULL || strcmp(context->root, "/") == 0)
		result = sam_open_protected(RELEASE_FILE, &fd);
	else
		result = sam_open_read(context, RELEASE_FILE, false, &fd);
	if (result != SAM_OK)
		return result;
	while (used < sizeof(data)) {
		got = read(fd, data + used, sizeof(data) - used);
		if (got == -1 && errno == EINTR)
			continue;
		if (got <= 0)
			break;
		used += (size_t)got;
	}
	result = used == sizeof(data) ? SAM_INVALID : got == -1 ?
	    sam_error_from_errno(errno) : sam_release_parse(data, used, release, &sequence);
	(void)close(fd);
	if (result == SAM_OK)
		(void)sam_append(out, "%s (sequence %" PRIu64 ")", release, sequence);
	return result;
}
