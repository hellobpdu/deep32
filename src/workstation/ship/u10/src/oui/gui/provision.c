#include "provision.h"

#include <errno.h>

#ifdef __OpenBSD__

#include <sys/mount.h>
#include <sys/stat.h>

#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "d32core.h"

#ifndef OUI_PROVISION_BIN
#define OUI_PROVISION_BIN "/d32/a/bin"
#endif
#ifndef OUI_IN_TRAY
#define OUI_IN_TRAY "/home/d32web/Downloads"
#endif
#ifndef OUI_MOUNT_ROOT
#define OUI_MOUNT_ROOT "/mnt"
#endif

#define PROVISIONER OUI_PROVISION_BIN "/d32oui-provision"
#define BUILDER OUI_PROVISION_BIN "/oui-build"
#define ARCHIVE_BYTES 3472949u

enum { IN_TRAY, MOUNT, MEDIA, TOOL, NGRANTS };

static struct {
	const char *path;
	const char *permissions;
	bool available;
	bool granted;
} grants[NGRANTS] = {
	[IN_TRAY] = { OUI_IN_TRAY, "r", false, false },
	[MOUNT] = { OUI_MOUNT_ROOT, "r", false, false },
	[MEDIA] = { "/media", "r", false, false },
	[TOOL] = { PROVISIONER, "x", false, false }
};

static bool
local_directory(const char *path)
{
	char resolved[PATH_MAX];
	struct stat st;
	struct statfs fs;

	return statfs(path, &fs) == 0 && (fs.f_flags & MNT_LOCAL) != 0 &&
	    realpath(path, resolved) != NULL && strcmp(path, resolved) == 0 &&
	    lstat(path, &st) == 0 && S_ISDIR(st.st_mode) &&
	    access(path, R_OK | X_OK) == 0;
}

static bool
trusted_directory(const char *path)
{
	char parent[PATH_MAX], *slash;
	struct stat st;

	if (!local_directory(path) || strlcpy(parent, path, sizeof(parent)) >= sizeof(parent))
		return false;
	for (;;) {
		if (lstat(parent, &st) == -1 || !S_ISDIR(st.st_mode) || st.st_uid != 0)
			return false;
		/* A root-owned sticky ancestor can contain root-owned QA scratch. */
		if ((st.st_mode & 022) != 0 &&
		    ((st.st_mode & S_ISVTX) == 0 || strcmp(path, parent) == 0))
			return false;
		slash = strrchr(parent, '/');
		if (slash == parent)
			return true;
		*slash = '\0';
	}
}

static bool
trusted_program(const char *path)
{
	struct stat st;

	return lstat(path, &st) == 0 && S_ISREG(st.st_mode) && st.st_uid == 0 &&
	    (st.st_mode & (S_ISUID | S_ISGID | 022)) == 0 && access(path, X_OK) == 0;
}

bool
oui_provision_available(char pattern[PATH_MAX])
{
	size_t i;
	int n;

	pattern[0] = '\0';
	grants[TOOL].available = false;
	if (getuid() == 0 || getuid() != geteuid() || getgid() != getegid() ||
	    !trusted_directory(OUI_PROVISION_BIN) ||
	    !trusted_program(PROVISIONER) || !trusted_program(BUILDER))
		return false;
	for (i = IN_TRAY; i < TOOL; i++) {
		grants[i].available = local_directory(grants[i].path);
		if (!grants[i].available || pattern[0] != '\0')
			continue;
		n = snprintf(pattern, PATH_MAX, "%s/*.tgz", grants[i].path);
		if (n < 0 || n >= PATH_MAX)
			return false;
	}
	grants[TOOL].available = pattern[0] != '\0';
	return grants[TOOL].available;
}

int
oui_provision_unveil(bool remove)
{
	size_t i;
	int error = 0;

	if (!remove && !grants[TOOL].available) {
		errno = ENOENT;
		return -1;
	}
	for (i = 0; i < NGRANTS; i++) {
		if (remove ? !grants[i].granted : !grants[i].available || grants[i].granted)
			continue;
		if (unveil(grants[i].path, remove ? "" : grants[i].permissions) == -1) {
			if (!remove)
				return -1;
			if (error == 0)
				error = errno;
		} else
			grants[i].granted = !remove;
	}
	if (error != 0) {
		errno = error;
		return -1;
	}
	return 0;
}

static enum d32_error
check_archive(const char *path)
{
	char resolved[PATH_MAX];
	const char *leaf;
	const unsigned char *byte;
	struct statfs fs;
	struct stat before, st;
	enum d32_error error;
	size_t len, root_len, i;
	int fd;

	if (path == NULL || path[0] != '/')
		return D32E_INVAL;
	len = strnlen(path, PATH_MAX);
	if (len == PATH_MAX)
		return D32E_TOOBIG;
	leaf = strrchr(path, '/') + 1;
	if (*leaf == '\0' || strcmp(leaf, ".") == 0 || strcmp(leaf, "..") == 0 ||
	    strstr(path, "//") != NULL ||
	    strstr(path, "/./") != NULL || strstr(path, "/../") != NULL)
		return D32E_INVAL;
	for (byte = (const unsigned char *)path; *byte != '\0'; byte++)
		if (*byte < 0x20 || *byte == 0x7f)
			return D32E_INVAL;
	for (i = IN_TRAY; i < TOOL; i++) {
		root_len = strlen(grants[i].path);
		if (grants[i].granted && len > root_len && path[root_len] == '/' &&
		    strncmp(path, grants[i].path, root_len) == 0)
			break;
	}
	if (i == TOOL)
		return D32E_PERM;
	if (realpath(path, resolved) == NULL)
		return errno == ENOENT ? D32E_NOENT : D32E_IO;
	if (strcmp(path, resolved) != 0)
		return D32E_LINK;
	if (lstat(path, &before) == -1)
		return errno == ENOENT ? D32E_NOENT : D32E_IO;
	if (!S_ISREG(before.st_mode))
		return S_ISLNK(before.st_mode) ? D32E_LINK : D32E_NOTREG;
	if (statfs(path, &fs) == -1)
		return D32E_IO;
	if ((fs.f_flags & MNT_LOCAL) == 0)
		return D32E_PERM;
	error = d32_open_input(path, D32_INPUT_FILE, ARCHIVE_BYTES, &fd, &st);
	if (error != D32E_OK)
		return error;
	if (before.st_dev != st.st_dev || before.st_ino != st.st_ino ||
	    fstatfs(fd, &fs) == -1)
		error = D32E_IO;
	else if ((fs.f_flags & MNT_LOCAL) == 0)
		error = D32E_PERM;
	else if (st.st_size != (off_t)ARCHIVE_BYTES)
		error = D32E_FORMAT;
	(void)close(fd);
	return error;
}

enum d32_error
oui_provision_start(const char *path, pid_t *pid, int *reader)
{
	char *const args[] = { PROVISIONER, (char *)path, NULL };
	enum d32_error error;
	int output[2];

	if (pid == NULL || reader == NULL)
		return D32E_INVAL;
	*pid = -1;
	*reader = -1;
	if (!grants[TOOL].granted)
		return D32E_UNAVAIL;
	error = check_archive(path);
	if (error != D32E_OK)
		return error;
	if (pipe2(output, O_CLOEXEC) == -1)
		return D32E_IO;
	error = d32_exec_fixed(PROVISIONER, args, -1, output[1], output[1], pid);
	(void)close(output[1]);
	if (error == D32E_OK)
		*reader = output[0];
	else
		(void)close(output[0]);
	return error;
}

#else

bool
oui_provision_available(char pattern[PATH_MAX])
{
	pattern[0] = '\0';
	return false;
}

int
oui_provision_unveil(bool remove)
{
	if (remove)
		return 0;
	errno = ENOSYS;
	return -1;
}

enum d32_error
oui_provision_start(const char *path, pid_t *pid, int *reader)
{
	(void)path;
	if (pid == NULL || reader == NULL)
		return D32E_INVAL;
	*pid = -1;
	*reader = -1;
	return D32E_UNAVAIL;
}

#endif
