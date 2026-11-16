/*
 * d32-sockexec: the root step of a Deep32 node service (PYTHON-ON-OPENBSD 7.2 step 4, HARDENING 5.8 item 4).
 *
 *   d32-sockexec -u user [-e envfile] [-l name=unix:/path:owner:group:mode] [-l name=tcp:127.0.0.1:port] ...
 *                -- /path/to/program [args ...]
 *
 * Run by rc.d as root (rc.subr has already applied the service's login class). It
 *   1. binds and listens on each -l socket (UNIX: created 0600, then chown/chmod to owner:group mode, so no
 *      other user can ever connect before the mode is final; TCP: loopback addresses only);
 *   2. opens the -e environment file (root-owned, no group or other access) read-only;
 *   3. puts the listeners on descriptors 3, 4, ... in -l order and the environment file after them, closes every
 *      other descriptor above 2, and exports D32_LISTEN_FDS ("3=core,4=front") and D32_ENV_FD;
 *   4. drops to the user (setgroups to its primary group only, setresgid, setresuid; refuses to stay root);
 *   5. execs the program. The Python launcher then imports, unveils and pledges.
 *
 * So the Python interpreter never runs as root, the service's secrets file is never readable by the service user,
 * and the socket owners and modes are exactly the socket table's (core.sock 0660 root:_d32edge and so on).
 */
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>

#include <netinet/in.h>
#include <arpa/inet.h>

#include <err.h>
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <limits.h>
#include <pwd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define MAXL	8
#define FIRSTFD	3

struct listener {
	char	 name[32];
	int	 fd;
};

static void
usage(void)
{
	fprintf(stderr, "usage: d32-sockexec -u user [-e envfile] "
	    "[-l name=unix:path:owner:group:mode | name=tcp:addr:port] ... -- program [args]\n");
	exit(1);
}

static int
check_name(const char *s, size_t n)
{
	size_t i;

	if (n == 0 || n >= sizeof(((struct listener *)0)->name))
		return -1;
	for (i = 0; i < n; i++)
		if (!((s[i] >= 'a' && s[i] <= 'z') || (s[i] >= '0' && s[i] <= '9') || s[i] == '_' || s[i] == '-'))
			return -1;
	return 0;
}

/* The directory that holds a socket must be root's and writable by nobody else: no one can swap the path. */
static void
check_dir(const char *path)
{
	char dir[PATH_MAX], *slash;
	struct stat st;

	if (strlcpy(dir, path, sizeof(dir)) >= sizeof(dir))
		errx(1, "%s: path too long", path);
	if ((slash = strrchr(dir, '/')) == NULL || slash == dir)
		errx(1, "%s: socket must live in a directory below /", path);
	*slash = '\0';
	if (lstat(dir, &st) == -1)
		err(1, "%s", dir);
	if (!S_ISDIR(st.st_mode) || st.st_uid != 0 || (st.st_mode & 022))
		errx(1, "%s: must be a directory owned by root, not group or other writable", dir);
}

static int
bind_unix(char *spec)
{
	char *path, *owner, *group, *mode, *end;
	struct sockaddr_un sun;
	struct passwd *pw;
	struct group *gr;
	struct stat st;
	mode_t old;
	long m;
	int fd;

	/* path:owner:group:mode; the path itself may not contain ':' */
	path = spec;
	if ((owner = strchr(path, ':')) == NULL)
		errx(1, "unix listener: want path:owner:group:mode");
	*owner++ = '\0';
	if ((group = strchr(owner, ':')) == NULL)
		errx(1, "unix listener: want path:owner:group:mode");
	*group++ = '\0';
	if ((mode = strchr(group, ':')) == NULL)
		errx(1, "unix listener: want path:owner:group:mode");
	*mode++ = '\0';
	if (path[0] != '/')
		errx(1, "%s: socket path must be absolute", path);
	errno = 0;
	m = strtol(mode, &end, 8);
	if (errno || *end != '\0' || m < 0 || m > 0777 || (m & 0007))
		errx(1, "%s: mode %s must be octal and give nothing to other", path, mode);
	if ((pw = getpwnam(owner)) == NULL)
		errx(1, "%s: no such user %s", path, owner);
	if ((gr = getgrnam(group)) == NULL)
		errx(1, "%s: no such group %s", path, group);
	check_dir(path);

	memset(&sun, 0, sizeof(sun));
	sun.sun_family = AF_UNIX;
	if (strlcpy(sun.sun_path, path, sizeof(sun.sun_path)) >= sizeof(sun.sun_path))
		errx(1, "%s: socket path too long", path);
	if (lstat(path, &st) == 0) {
		if (!S_ISSOCK(st.st_mode))
			errx(1, "%s: exists and is not a socket", path);
		if (unlink(path) == -1)
			err(1, "unlink %s", path);
	}
	if ((fd = socket(AF_UNIX, SOCK_STREAM, 0)) == -1)
		err(1, "socket");
	old = umask(0177);
	if (bind(fd, (struct sockaddr *)&sun, sizeof(sun)) == -1)
		err(1, "bind %s", path);
	umask(old);
	if (chown(path, pw->pw_uid, gr->gr_gid) == -1)
		err(1, "chown %s", path);
	if (chmod(path, (mode_t)m) == -1)
		err(1, "chmod %s", path);
	if (listen(fd, 128) == -1)
		err(1, "listen %s", path);
	return fd;
}

static int
bind_tcp(char *spec)
{
	struct sockaddr_in sin;
	char *port, *end;
	long p;
	int fd, one = 1;

	if ((port = strrchr(spec, ':')) == NULL)
		errx(1, "tcp listener: want addr:port");
	*port++ = '\0';
	memset(&sin, 0, sizeof(sin));
	sin.sin_family = AF_INET;
	sin.sin_len = sizeof(sin);
	if (inet_pton(AF_INET, spec, &sin.sin_addr) != 1)
		errx(1, "tcp listener: %s is not an IPv4 address", spec);
	if ((ntohl(sin.sin_addr.s_addr) >> 24) != 127)
		errx(1, "tcp listener: %s is not a loopback address (the node has no inbound service)", spec);
	errno = 0;
	p = strtol(port, &end, 10);
	if (errno || *end != '\0' || p < 1024 || p > 65535)
		errx(1, "tcp listener: port %s must be 1024-65535", port);
	sin.sin_port = htons((in_port_t)p);
	if ((fd = socket(AF_INET, SOCK_STREAM, 0)) == -1)
		err(1, "socket");
	if (setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one)) == -1)
		err(1, "setsockopt");
	if (bind(fd, (struct sockaddr *)&sin, sizeof(sin)) == -1)
		err(1, "bind %s:%ld", spec, p);
	if (listen(fd, 128) == -1)
		err(1, "listen %s:%ld", spec, p);
	return fd;
}

static int
open_env(const char *path)
{
	struct stat st;
	int fd;

	if ((fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)) == -1)
		err(1, "%s", path);
	if (fstat(fd, &st) == -1)
		err(1, "%s", path);
	if (!S_ISREG(st.st_mode) || st.st_uid != 0 || (st.st_mode & 077))
		errx(1, "%s: must be a regular file owned by root with no group or other access", path);
	return fd;
}

int
main(int argc, char *argv[])
{
	struct listener l[MAXL];
	char fds[256], num[16], *user = NULL, *envfile = NULL, *eq, *kind;
	struct passwd *pw;
	int ch, i, nl = 0, envfd = -1, hi, next;
	gid_t gid;

	while ((ch = getopt(argc, argv, "e:l:u:")) != -1) {
		switch (ch) {
		case 'e':
			envfile = optarg;
			break;
		case 'l':
			if (nl == MAXL)
				errx(1, "at most %d listeners", MAXL);
			if ((eq = strchr(optarg, '=')) == NULL || check_name(optarg, eq - optarg) == -1)
				errx(1, "listener %s: want name=kind:spec (name: [a-z0-9_-], < 32 chars)", optarg);
			memcpy(l[nl].name, optarg, eq - optarg);
			l[nl].name[eq - optarg] = '\0';
			kind = eq + 1;
			if (strncmp(kind, "unix:", 5) == 0)
				l[nl].fd = bind_unix(kind + 5);
			else if (strncmp(kind, "tcp:", 4) == 0)
				l[nl].fd = bind_tcp(kind + 4);
			else
				errx(1, "listener %s: kind must be unix or tcp", l[nl].name);
			nl++;
			break;
		case 'u':
			user = optarg;
			break;
		default:
			usage();
		}
	}
	argc -= optind;
	argv += optind;
	if (user == NULL || argc < 1 || argv[0][0] != '/')
		usage();
	if (getuid() != 0 || geteuid() != 0)
		errx(1, "must be started as root (it drops to %s itself)", user);
	if ((pw = getpwnam(user)) == NULL)
		errx(1, "no such user %s", user);
	if (pw->pw_uid == 0)
		errx(1, "refusing to run a service as root");
	gid = pw->pw_gid;
	if (envfile != NULL)
		envfd = open_env(envfile);

	/*
	 * Move every descriptor above 63 first (closing the original), then dup2 each down to 3, 4, ... in order:
	 * no dup2 can clobber a descriptor that is still needed. dup2 clears close-on-exec on the copy it makes.
	 */
	hi = 64;
	for (i = 0; i < nl; i++) {
		if ((ch = fcntl(l[i].fd, F_DUPFD_CLOEXEC, hi)) == -1)
			err(1, "fcntl");
		close(l[i].fd);
		l[i].fd = ch;
	}
	if (envfd != -1) {
		if ((ch = fcntl(envfd, F_DUPFD_CLOEXEC, hi)) == -1)
			err(1, "fcntl");
		close(envfd);
		envfd = ch;
	}
	fds[0] = '\0';
	next = FIRSTFD;
	for (i = 0; i < nl; i++, next++) {
		if (dup2(l[i].fd, next) == -1)
			err(1, "dup2");
		close(l[i].fd);
		snprintf(num, sizeof(num), "%s%d=", i ? "," : "", next);
		if (strlcat(fds, num, sizeof(fds)) >= sizeof(fds) ||
		    strlcat(fds, l[i].name, sizeof(fds)) >= sizeof(fds))
			errx(1, "too many listeners");
	}
	if (setenv("D32_LISTEN_FDS", fds, 1) == -1)
		err(1, "setenv");
	if (envfd != -1) {
		if (dup2(envfd, next) == -1)
			err(1, "dup2");
		close(envfd);
		snprintf(num, sizeof(num), "%d", next);
		if (setenv("D32_ENV_FD", num, 1) == -1)
			err(1, "setenv");
		next++;
	} else
		unsetenv("D32_ENV_FD");
	closefrom(next);	/* nothing else inherited from rc, su or the shell reaches the service */

	if (setgroups(1, &gid) == -1)
		err(1, "setgroups");
	if (setresgid(gid, gid, gid) == -1)
		err(1, "setresgid");
	if (setresuid(pw->pw_uid, pw->pw_uid, pw->pw_uid) == -1)
		err(1, "setresuid");
	if (getuid() == 0 || geteuid() == 0 || getgid() == 0 || getegid() == 0)
		errx(1, "still root after the drop");
	if (setuid(0) != -1)
		errx(1, "could regain root");

	execv(argv[0], argv);
	err(1, "execv %s", argv[0]);
}
