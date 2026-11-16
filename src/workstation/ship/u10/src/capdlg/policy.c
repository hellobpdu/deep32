#include "policy.h"
#include "d32core.h"

#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

enum { CAP_FILE_LIMIT = 32768, CAP_LINE_LIMIT = 560 };

static bool
letter(char c)
{
	return c >= 'a' && c <= 'z';
}

static bool
number(char c)
{
	return c >= '0' && c <= '9';
}

static int
hex_digit(char c)
{
	if (number(c))
		return c - '0';
	if (c >= 'a' && c <= 'f')
		return c - 'a' + 10;
	if (c >= 'A' && c <= 'F')
		return c - 'A' + 10;
	return -1;
}

static bool
address(const char *s, uint8_t bytes[6])
{
	unsigned i;
	unsigned any = 0;

	if (strlen(s) != 17)
		return false;
	for (i = 0; i < 6; i++) {
		int high = hex_digit(s[i * 3]), low = hex_digit(s[i * 3 + 1]);

		if (high < 0 || low < 0 || (i != 5 && s[i * 3 + 2] != ':'))
			return false;
		bytes[i] = (uint8_t)(high * 16 + low);
		any |= bytes[i];
	}
	return any != 0 && !(bytes[0] & 1);
}

static char *
word(char **line)
{
	char *begin, *end;

	begin = *line + strspn(*line, " \t");
	end = begin + strcspn(begin, " \t");
	if (*end != '\0')
		*end++ = '\0';
	*line = end;
	return begin;
}

static bool
parse_interface(char *line, struct cap_policy *p)
{
	struct cap_interface iface = { 0 };
	const char *name = word(&line);
	const char *lladdr = word(&line);
	const char *kind = word(&line);
	size_t i, n;

	if (strncmp(name, "iface=", 6) || strncmp(lladdr, "lladdr=", 7) ||
	    strcmp(kind, "class=lab") || *word(&line) != '\0')
		return false;
	name += 6;
	n = strlen(name);
	if (!letter(name[0]) || n >= sizeof(iface.name) || !address(lladdr + 7, iface.lladdr) ||
	    p->nifaces == CAP_MAX_IFACES)
		return false;
	for (i = 1; i < n; i++)
		if (!letter(name[i]) && !number(name[i]) && name[i] != '_' && name[i] != '-')
			return false;
	for (i = 0; i < p->nifaces; i++)
		if (!strcmp(p->ifaces[i].name, name))
			return false;
	memcpy(iface.name, name, n + 1);
	p->ifaces[p->nifaces++] = iface;
	return true;
}

static bool
parse_filter(char *line, struct cap_policy *p)
{
	struct cap_filter filter = { 0 };
	char *expr = strchr(line, '=');
	size_t i, n, size;

	if (expr == NULL || p->nfilters == CAP_MAX_FILTERS)
		return false;
	*expr++ = '\0';
	n = strlen(line);
	expr += strspn(expr, " ");
	size = strlen(expr);
	if (!letter(line[0]) || n > CAP_FILTER_ID || !strcmp(line, "none") ||
	    size == 0 || size > CAP_EXPRESSION || strchr(expr, '\t'))
		return false;
	for (i = 1; i < n; i++)
		if (!letter(line[i]) && !number(line[i]) && line[i] != '-')
			return false;
	for (i = 0; i < p->nfilters; i++)
		if (!strcmp(p->filters[i].id, line))
			return false;
	memcpy(filter.id, line, n + 1);
	memcpy(filter.expression, expr, size + 1);
	p->filters[p->nfilters++] = filter;
	return true;
}

static enum cap_policy_state
load_file(int dir, const char *name, uid_t owner,
    bool (*parse)(char *, struct cap_policy *), struct cap_policy *out)
{
	enum cap_policy_state state = CAP_POLICY_INVALID;
	struct d32_reader reader;
	struct stat st, after;
	char buf[CAP_LINE_LIMIT], *line;
	size_t i, length;
	bool eof;
	int fd;

	fd = openat(dir, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
	if (fd == -1)
		return errno == ENOENT ? CAP_POLICY_ABSENT : CAP_POLICY_INVALID;
	if (fstat(fd, &st) == -1 || !S_ISREG(st.st_mode) || st.st_uid != owner ||
	    (st.st_mode & 07777) != 0444 || st.st_size > CAP_FILE_LIMIT)
		goto done;
	d32_reader_init(&reader, fd, CAP_FILE_LIMIT);
	for (;;) {
		if (d32_read_line(&reader, buf, sizeof(buf), &length, &eof) != D32E_OK)
			goto done;
		if (eof)
			break;
		if (length != 0 && buf[length - 1] == '\r')
			buf[--length] = '\0';
		for (i = 0; i < length; i++)
			if (((unsigned char)buf[i] < 32 && buf[i] != '\t') ||
			    (unsigned char)buf[i] > 126)
				goto done;
		line = buf + strspn(buf, " \t");
		if (*line != '\0' && *line != '#' && !parse(line, out))
			goto done;
	}
	if (fstat(fd, &after) == 0 && st.st_size == after.st_size &&
	    st.st_mtim.tv_sec == after.st_mtim.tv_sec &&
	    st.st_mtim.tv_nsec == after.st_mtim.tv_nsec &&
	    st.st_ctim.tv_sec == after.st_ctim.tv_sec &&
	    st.st_ctim.tv_nsec == after.st_ctim.tv_nsec)
		state = CAP_POLICY_READY;
done:
	close(fd);
	return state;
}

enum cap_policy_state
cap_policy_load_at(int dir, uid_t owner, struct cap_policy *out)
{
	struct cap_policy staging = { 0 };
	enum cap_policy_state state;

	memset(out, 0, sizeof(*out));
	state = load_file(dir, "capture.conf", owner, parse_interface, &staging);
	if (state != CAP_POLICY_READY)
		return state;
	if (staging.nifaces == 0)
		return CAP_POLICY_ABSENT;
	state = load_file(dir, "capture-filters", owner, parse_filter, &staging);
	if (state == CAP_POLICY_READY)
		*out = staging;
	return state;
}

static int
trusted_dir(int fd)
{
	struct stat st;

	if (fd == -1)
		return -1;
	if (fstat(fd, &st) == 0 && st.st_uid == 0 && !(st.st_mode & 0022))
		return fd;
	close(fd);
	errno = EPERM;
	return -1;
}

enum cap_policy_state
cap_policy_load(struct cap_policy *out)
{
	int etc, dir, saved;
	enum cap_policy_state state;

	memset(out, 0, sizeof(*out));
	etc = trusted_dir(open("/etc", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
	if (etc == -1)
		return CAP_POLICY_INVALID;
	dir = trusted_dir(openat(etc, "d32", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
	saved = errno;
	close(etc);
	if (dir == -1)
		return saved == ENOENT ? CAP_POLICY_ABSENT : CAP_POLICY_INVALID;
	state = cap_policy_load_at(dir, 0, out);
	close(dir);
	return state;
}

bool
cap_helper_present(const char *name)
{
	static const char *const dirs[] = { "usr", "local", "d32", "sbin" };
	struct stat st;
	unsigned i;
	int dir, next;
	bool present;

	if (strcmp(name, "d32-capture") && strcmp(name, "wireshark-run"))
		return false;
	dir = trusted_dir(open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC));
	for (i = 0; dir != -1 && i < sizeof(dirs) / sizeof(dirs[0]); i++) {
		next = trusted_dir(openat(dir, dirs[i], O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
		close(dir);
		dir = next;
	}
	if (dir == -1)
		return false;
	present = fstatat(dir, name, &st, AT_SYMLINK_NOFOLLOW) == 0 && S_ISREG(st.st_mode) &&
	    st.st_uid == 0 && (st.st_mode & 0001) != 0 && !(st.st_mode & 06022);
	close(dir);
	return present;
}

enum cap_gate
cap_gate(enum cap_policy_state policy, bool broker, bool viewer, bool authority, bool lab)
{
	if (policy == CAP_POLICY_ABSENT)
		return CAP_NO_POLICY;
	if (policy != CAP_POLICY_READY)
		return CAP_BAD_POLICY;
	if (!broker)
		return CAP_NO_BROKER;
	if (!viewer)
		return CAP_NO_VIEWER;
	if (!authority)
		return CAP_NO_AUTHORITY;
	if (!lab)
		return CAP_NO_LAB_INTERFACE;
	return CAP_ALLOWED;
}

const char *
cap_gate_text(enum cap_gate state)
{
	switch (state) {
	case CAP_NO_POLICY: return "A Lab interface policy is required.";
	case CAP_BAD_POLICY: return "The Lab Capture policy could not be verified.";
	case CAP_NO_BROKER: return "The approved capture helper is unavailable.";
	case CAP_NO_VIEWER: return "The approved capture viewer is unavailable.";
	case CAP_NO_AUTHORITY: return "Non-interactive capture authorization is unavailable.";
	case CAP_NO_LAB_INTERFACE: return "The Lab interface identity could not be verified.";
	case CAP_ALLOWED: return "Lab Capture prerequisites are present.";
	}
	return "Lab Capture prerequisites could not be verified.";
}
