#include <sys/file.h>
#include <sys/stat.h>

#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "d32_io.h"
#include "output.h"

static bool
owned_directory(int fd)
{
	struct stat st;

	return fstat(fd, &st) == 0 && S_ISDIR(st.st_mode) && st.st_uid == getuid() &&
	    (st.st_mode & 0022) == 0;
}

/* Each component is opened relative to its verified parent. */
static int
directory_at(int parent, const char *path, bool own, bool create)
{
	char parts[PATH_MAX], *next, *name;
	int dir, child;

	if (strlcpy(parts, path, sizeof(parts)) >= sizeof(parts))
		return -1;
	for (const unsigned char *p = (const unsigned char *)parts; *p; p++)
		if (*p < 32 || *p == 127)
			return -1;
	dir = fcntl(parent, F_DUPFD_CLOEXEC, 0);
	if (dir == -1)
		return -1;
	next = parts;
	while ((name = strsep(&next, "/")) != NULL) {
		if (*name == '\0' || strcmp(name, ".") == 0 || strcmp(name, "..") == 0) {
			close(dir);
			return -1;
		}
		child = openat(dir, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
		if (child == -1 && errno == ENOENT && create &&
		    (mkdirat(dir, name, 0700) == 0 || errno == EEXIST))
			child = openat(dir, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
		close(dir);
		if (child == -1)
			return -1;
		if (own && !owned_directory(child)) {
			close(child);
			return -1;
		}
		dir = child;
	}
	return dir;
}

void
sc_output_close(struct sc_output *output)
{
	if (output->lock_fd != -1)
		close(output->lock_fd);
	if (output->home_fd != -1)
		close(output->home_fd);
}

enum d32_error
sc_output_init(struct sc_output *output, const char *home)
{
	int root, state, captures, n;
	struct stat st;

	output->home_fd = output->lock_fd = -1;
	if (home == NULL || home[0] != '/' || home[1] == '\0')
		return D32E_INVAL;
	n = snprintf(output->base, sizeof(output->base), "%s/Captures", home);
	if (n < 0 || (size_t)n >= sizeof(output->base))
		return D32E_INVAL;
	n = snprintf(output->path, sizeof(output->path), "%s/Screen", output->base);
	if (n < 0 || (size_t)n >= sizeof(output->path))
		return D32E_INVAL;
	root = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	if (root == -1)
		return D32E_IO;
	output->home_fd = directory_at(root, home + 1, false, false);
	close(root);
	if (output->home_fd == -1 || !owned_directory(output->home_fd))
		return D32E_PERM;
	state = directory_at(output->home_fd, ".dt/d32", true, true);
	if (state == -1)
		return D32E_PERM;
	output->lock_fd = openat(state, ".screencap.lock",
	    O_RDWR | O_CREAT | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC, 0600);
	close(state);
	if (output->lock_fd == -1 || fstat(output->lock_fd, &st) == -1 ||
	    !S_ISREG(st.st_mode) || st.st_uid != getuid() || st.st_nlink != 1 ||
	    (st.st_mode & 0777) != 0600)
		return D32E_PERM;
	if (flock(output->lock_fd, LOCK_EX | LOCK_NB) == -1)
		return errno == EWOULDBLOCK ? D32E_EXISTS : D32E_IO;
	/* Unveil needs an existing directory to authorize its descendants. */
	captures = directory_at(output->home_fd, "Captures", true, true);
	if (captures != -1)
		close(captures);
	return D32E_OK;
}

static const char *
relative_path(const struct sc_output *output, const char *path)
{
	size_t base_len = strlen(output->base);

	if (strncmp(path, output->base, base_len) != 0 ||
	    (path[base_len] != '\0' && path[base_len] != '/'))
		return NULL;
	return path + base_len - (sizeof("Captures") - 1);
}

enum d32_error
sc_output_choose(struct sc_output *output, const char *path)
{
	char candidate[PATH_MAX];
	const char *relative;
	size_t len;
	int fd;

	len = strlcpy(candidate, path, sizeof(candidate));
	if (len >= sizeof(candidate))
		return D32E_INVAL;
	while (len > 1 && candidate[len - 1] == '/')
		candidate[--len] = '\0';
	relative = relative_path(output, candidate);
	if (relative == NULL)
		return D32E_PERM;
	fd = directory_at(output->home_fd, relative, true, false);
	if (fd == -1)
		return D32E_PERM;
	close(fd);
	strlcpy(output->path, candidate, sizeof(output->path));
	return D32E_OK;
}

enum d32_error
sc_output_save(const struct sc_output *output, const void *png, size_t len,
    char saved[PATH_MAX])
{
	const char *relative = relative_path(output, output->path);
	char stem[32], name[40];
	struct tm parts;
	time_t now = time(NULL);
	enum d32_error result = D32E_INVAL;
	int dir, n;
	bool default_path;

	if (relative == NULL || localtime_r(&now, &parts) == NULL ||
	    strftime(stem, sizeof(stem), "d32-%Y%m%d-%H%M%S", &parts) == 0)
		return D32E_INVAL;
	default_path = strcmp(relative, "Captures/Screen") == 0;
	dir = directory_at(output->home_fd, relative, true, default_path);
	if (dir == -1)
		return D32E_PERM;
	for (unsigned i = 0; i <= 99; i++) {
		if (i == 0)
			snprintf(name, sizeof(name), "%s.png", stem);
		else
			snprintf(name, sizeof(name), "%s-%u.png", stem, i);
		n = snprintf(saved, PATH_MAX, "%s/%s", output->path, name);
		if (n < 0 || n >= PATH_MAX)
			break;
		result = d32_atomic_write(dir, name, png, len, 0600, true);
		if (result != D32E_EXISTS)
			break;
	}
	close(dir);
	return result;
}
