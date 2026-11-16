#include "ws41.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pwd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define VIEWER "/usr/local/bin/wireshark"
#define HOME_DIR "/home/d32net"
#define SPOOL HOME_DIR "/spool"
#define SAVES HOME_DIR "/captures"
#define AUTH_PREFIX HOME_DIR "/.d32-ws."

static char socket_path[32];
static struct stat socket_identity;
static char config_path[PATH_MAX];

static bool
valid_display(const char *value)
{
	return value != NULL && strlen(value) == 4 && value[0] == ':' && value[1] == '1' &&
	    (value[2] == '7' || value[2] == '8') && value[3] >= '0' && value[3] <= '9';
}

static bool
valid_authority(const char *value)
{
	static const char alphabet[] = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
	struct stat st;
	const char *name;

	if (value == NULL || strncmp(value, AUTH_PREFIX, sizeof(AUTH_PREFIX) - 1) != 0)
		return false;
	name = value + sizeof(AUTH_PREFIX) - 1;
	return strspn(name, alphabet) == 8 && strcmp(name + 8, "/auth") == 0 &&
	    lstat(value, &st) == 0 && S_ISREG(st.st_mode) && st.st_uid == getuid() &&
	    st.st_nlink == 1 && (st.st_mode & 07777) == 0600;
}

static bool
private_directory(const struct stat *st)
{
	return S_ISDIR(st->st_mode) && st->st_uid == getuid() && (st->st_mode & 07777) == 0700;
}

enum d32_error
ws_backend_open(int *dirfd)
{
	struct passwd *user = getpwnam("d32net");
	const char *display = getenv("DISPLAY");
	const char *authority = getenv("XAUTHORITY");
	struct stat st;
	int fd;

	*dirfd = -1;
	if (user == NULL || getuid() == 0 || getuid() != user->pw_uid || geteuid() != user->pw_uid ||
	    strcmp(user->pw_dir, HOME_DIR) != 0 || !valid_display(display) ||
	    !valid_authority(authority))
		return D32E_PERM;
	/* Wireshark writes recent state on exit; this directory lasts one launch. */
	(void)snprintf(config_path, sizeof(config_path), "%.*s/config",
	    (int)(strlen(authority) - strlen("/auth")), authority);
	if (lstat(config_path, &st) == -1 || !private_directory(&st))
		return D32E_PERM;
	(void)snprintf(socket_path, sizeof(socket_path), "/tmp/.X11-unix/X%s", display + 1);
	if (lstat(socket_path, &socket_identity) == -1 || !S_ISSOCK(socket_identity.st_mode))
		return D32E_UNAVAIL;
	if (lstat(VIEWER, &st) == -1)
		return D32E_UNAVAIL;
	if (!S_ISREG(st.st_mode) || st.st_uid != 0 || (st.st_mode & 06022) != 0 || !(st.st_mode & 0001))
		return D32E_PERM;
	if (lstat(SAVES, &st) == -1 || !private_directory(&st))
		return D32E_PERM;
	fd = open(SPOOL, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
	if (fd == -1)
		return D32E_UNAVAIL;
	if (fstat(fd, &st) == -1 || !private_directory(&st)) {
		close(fd);
		return D32E_PERM;
	}
	*dirfd = fd;
	return D32E_OK;
}

static void
run_viewer(const char *name)
{
	/* The vendor probes hw.model. Let denied optional probes return ENOSYS. */
	static const char promises[] = "error stdio rpath wpath cpath unix getpw prot_exec flock fattr sendfd recvfd tty proc vminfo";
	char display[16], auth[PATH_MAX], capture[PATH_MAX];
	char config[sizeof("WIRESHARK_CONFIG_DIR=") + sizeof(config_path)];
	char *args[] = {"wireshark", "-n", NULL, NULL, NULL};
	char *env[] = {"PATH=/usr/bin:/bin:/usr/X11R6/bin", "HOME=/var/empty", "USER=d32net", "LOGNAME=d32net",
	    "LANG=C.UTF-8", display, auth, config, "XDG_CONFIG_HOME=/var/empty",
	    "XDG_DATA_HOME=/var/empty", "WIRESHARK_EXTCAP_DIR=/var/empty", "WIRESHARK_PLUGIN_DIR=/var/empty",
	    "LD_LIBRARY_PATH=/usr/local/lib:/usr/local/lib/libpcap:/usr/X11R6/lib", "QT_QPA_PLATFORM=xcb",
	    "GSETTINGS_BACKEND=memory", "DBUS_SESSION_BUS_ADDRESS=unix:path=/var/empty/d32-ws-no-bus", NULL};

	(void)snprintf(display, sizeof(display), "DISPLAY=%s", getenv("DISPLAY"));
	(void)snprintf(auth, sizeof(auth), "XAUTHORITY=%s", getenv("XAUTHORITY"));
	(void)snprintf(config, sizeof(config), "WIRESHARK_CONFIG_DIR=%s", config_path);
	if (name != NULL) {
		(void)snprintf(capture, sizeof(capture), "%s/%s", SPOOL, name);
		args[2] = "-r";
		args[3] = capture;
	}
	if (chdir(SAVES) == -1)
		_exit(77);
	closefrom(3);
	if (pledge("stdio exec", promises) == -1)
		_exit(77);
	execve(VIEWER, args, env);
	_exit(69);
}

static bool
same_display(void)
{
	struct stat now;
	return lstat(socket_path, &now) == 0 && S_ISSOCK(now.st_mode) &&
	    now.st_dev == socket_identity.st_dev && now.st_ino == socket_identity.st_ino;
}

enum d32_error
ws_backend_view(int dirfd, const char *name, const volatile sig_atomic_t *cancelled)
{
	const struct timespec pause = {.tv_nsec = 50000000};
	pid_t child, result;
	int status;

	(void)dirfd;
	child = fork();
	if (child == -1)
		return D32E_IO;
	if (child == 0)
		run_viewer(name);
	while (!*cancelled && same_display()) {
		result = waitpid(child, &status, WNOHANG);
		if (result == child)
			return WIFEXITED(status) ? ws_error_from_exit(WEXITSTATUS(status)) : D32E_IO;
		if (result == -1 && errno != EINTR)
			return D32E_IO;
		(void)nanosleep(&pause, NULL);
	}
	(void)kill(child, SIGTERM);
	for (int i = 0; i < 10; i++) {
		if (waitpid(child, &status, WNOHANG) == child)
			return D32E_TEMP;
		(void)nanosleep(&pause, NULL);
	}
	(void)kill(child, SIGKILL);
	while (waitpid(child, &status, 0) == -1 && errno == EINTR)
		;
	return D32E_TEMP;
}
