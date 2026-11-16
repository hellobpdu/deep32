/* openat_ipc.c: bounded Unix-domain launch transport, with a lifetime lock. */
#include "openat_ipc.h"
#include "openat.h"
#include "../common/sha256.h"
#include <sys/types.h>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#ifdef __OpenBSD__
/* The build requests a POSIX namespace; these are OpenBSD extensions. */
int getpeereid(int, uid_t *, gid_t *);
int flock(int, int);
#ifndef LOCK_EX /* sys/fcntl.h defines these only when __BSD_VISIBLE */
#define LOCK_EX 0x02
#define LOCK_NB 0x04
#endif
#endif

#define IPC_SLOTS 16
#define IPC_CLIENT_MS 3000
#define IPC_REQUEST_MS 2000
#define IPC_PATH 104
#define IPC_LINE (OPENAT_URI_MAX + 6) /* "OPEN " + URI + LF */

typedef struct {
	int fd;
	double deadline;
	char line[IPC_LINE + 1];
	size_t used;
	const char *ack;
	size_t sent;
} ipc_peer;

struct openat_ipc {
	int dirfd, lockfd, fd;
	uid_t uid;
	char dir[64], path[IPC_PATH], name[48], lockname[48];
	struct stat directory, socket;
	int bound;
	ipc_peer peers[IPC_SLOTS];
};

static double now_ms(void)
{
	struct timespec ts;
	if (clock_gettime(CLOCK_MONOTONIC, &ts) < 0) return -1.0;
	return (double)ts.tv_sec * 1000.0 + (double)ts.tv_nsec / 1000000.0;
}

static int fd_flags(int fd)
{
	int f = fcntl(fd, F_GETFD);
	int s = fcntl(fd, F_GETFL);
	if (f < 0 || s < 0 || fcntl(fd, F_SETFD, f | FD_CLOEXEC) < 0 ||
	    fcntl(fd, F_SETFL, s | O_NONBLOCK) < 0) return -1;
	return 0;
}

static int same_inode(const struct stat *a, const struct stat *b)
{
	return a->st_dev == b->st_dev && a->st_ino == b->st_ino;
}

static int private_dir(const struct stat *s, uid_t uid)
{
	return S_ISDIR(s->st_mode) && s->st_uid == uid &&
	    (s->st_mode & 07777) == 0700;
}

static int private_socket(const struct stat *s, uid_t uid)
{
	return S_ISSOCK(s->st_mode) && s->st_uid == uid &&
	    (s->st_mode & 07777) == 0600 && s->st_nlink == 1;
}

static int directory_unchanged(openat_ipc *ipc)
{
	struct stat s;
	if (lstat(ipc->dir, &s) < 0) return -1;
	if (!private_dir(&s, ipc->uid) || !same_inode(&s, &ipc->directory)) {
		errno = EACCES;
		return -1;
	}
	return 0;
}

static int display_word(const char *p, size_t n, const char *word)
{
	size_t i;
	if (strlen(word) != n) return 0;
	for (i = 0; i < n; i++) {
		unsigned char a = (unsigned char)p[i], b = (unsigned char)word[i];
		if (a >= 'A' && a <= 'Z') a = (unsigned char)(a + ('a' - 'A'));
		if (b >= 'A' && b <= 'Z') b = (unsigned char)(b + ('a' - 'A'));
		if (a != b) return 0;
	}
	return 1;
}

static int local_display_prefix(const char *p, size_t n)
{
	char host[256];
	size_t h;
	if (!n || display_word(p, n, "unix") || display_word(p, n, "unix/") ||
	    display_word(p, n, "localhost") || display_word(p, n, "localhost/unix") ||
	    display_word(p, n, "unix/localhost")) return 1;
	if (gethostname(host, sizeof host) < 0) return -1;
	if (!memchr(host, '\0', sizeof host)) { errno = ENAMETOOLONG; return -1; }
	h = strlen(host);
	if (!h) { errno = EINVAL; return -1; }
	if (display_word(p, n, host)) return 1;
	if (n == h + 5 && display_word(p, h, host) && display_word(p + h, 5, "/unix")) return 1;
	if (n == h + 5 && display_word(p, 5, "unix/") && display_word(p + 5, h, host)) return 1;
	return 0;
}

static int display_number(const char **p, unsigned long *number)
{
	const unsigned char *s = (const unsigned char *)*p;
	unsigned long n = 0;
	if (*s < '0' || *s > '9') { errno = EINVAL; return -1; }
	while (*s >= '0' && *s <= '9') {
		unsigned long digit = (unsigned long)(*s - '0');
		if (n > ((unsigned long)INT_MAX - digit) / 10) { errno = EINVAL; return -1; }
		n = n * 10 + digit;
		s++;
	}
	*p = (const char *)s;
	*number = n;
	return 0;
}

int openat_ipc_display_key(const char *display, char *buf, size_t size)
{
	const char *colon, *suffix;
	size_t len;
	unsigned long number, screen;
	int local, n;
	if (!display || !*display || !buf || !size) { errno = EINVAL; return -1; }
	len = strnlen(display, OPENAT_IPC_DISPLAY_MAX + 1);
	if (len > OPENAT_IPC_DISPLAY_MAX) { errno = ENAMETOOLONG; return -1; }
	colon = strchr(display, ':');
	local = colon ? local_display_prefix(display, (size_t)(colon - display)) : 0;
	if (local < 0) return -1;
	if (!local) {
		if (len + 7 >= size) { errno = ENAMETOOLONG; return -1; }
		memmove(buf + 7, display, len + 1);
		memcpy(buf, "remote:", 7);
		return 0;
	}
	suffix = colon + 1;
	if (display_number(&suffix, &number)) return -1;
	if (*suffix == '.') {
		suffix++;
		if (display_number(&suffix, &screen)) return -1;
	}
	if (*suffix) { errno = EINVAL; return -1; }
	n = snprintf(buf, size, "local:%lu", number);
	if (n < 0 || (size_t)n >= size) { errno = ENAMETOOLONG; return -1; }
	return 0;
}

static int display_digest(const char *display, char hex[65])
{
	char key[OPENAT_IPC_DISPLAY_KEY_SIZE];
	if (openat_ipc_display_key(display, key, sizeof key)) return -1;
	sha256_hex(key, strlen(key), hex);
	return 0;
}

static int endpoint_digest(uid_t uid, const char *hex, char *path, size_t size)
{
	int n;
	if (!path || !size) { errno = EINVAL; return -1; }
	n = snprintf(path, size, "/tmp/e26-openat-%lu/%.32s.sock",
	    (unsigned long)uid, hex);
	if (n < 0 || (size_t)n >= size || (size_t)n >= sizeof(((struct sockaddr_un *)0)->sun_path)) {
		errno = ENAMETOOLONG;
		return -1;
	}
	return 0;
}

int openat_ipc_endpoint(const char *display, char *path, size_t size)
{
	char hex[65];
	if (display_digest(display, hex)) return -1;
	return endpoint_digest(geteuid(), hex, path, size);
}

static int prepare_directory(openat_ipc *ipc, const char *display)
{
	char hex[65];
	struct stat s, f;
	int n;
	if (display_digest(display, hex)) return -1;
	n = snprintf(ipc->dir, sizeof ipc->dir, "/tmp/e26-openat-%lu", (unsigned long)ipc->uid);
	if (n < 0 || (size_t)n >= sizeof ipc->dir) { errno = ENAMETOOLONG; return -1; }
	if (endpoint_digest(ipc->uid, hex, ipc->path, sizeof ipc->path)) return -1;
	(void)snprintf(ipc->name, sizeof ipc->name, "%.32s.sock", hex);
	(void)snprintf(ipc->lockname, sizeof ipc->lockname, "%.32s.lock", hex);
	if (mkdir(ipc->dir, 0700) < 0 && errno != EEXIST) return -1;
	if (lstat(ipc->dir, &s) < 0) return -1;
	if (!private_dir(&s, ipc->uid)) { errno = EACCES; return -1; }
	ipc->dirfd = open(ipc->dir, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
	if (ipc->dirfd < 0 || fstat(ipc->dirfd, &f) < 0) return -1;
	if (!private_dir(&f, ipc->uid) || !same_inode(&s, &f)) { errno = EACCES; return -1; }
	ipc->directory = f;
	return directory_unchanged(ipc);
}

static int prepare_lock(openat_ipc *ipc)
{
	struct stat s, f;
	ipc->lockfd = openat(ipc->dirfd, ipc->lockname,
	    O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK, 0600);
	if (ipc->lockfd < 0 || fstat(ipc->lockfd, &f) < 0 ||
	    fstatat(ipc->dirfd, ipc->lockname, &s, AT_SYMLINK_NOFOLLOW) < 0) return -1;
	if (!S_ISREG(f.st_mode) || f.st_uid != ipc->uid ||
	    (f.st_mode & 07777) != 0600 || f.st_nlink != 1 || !same_inode(&s, &f)) {
		errno = EACCES;
		return -1;
	}
	return directory_unchanged(ipc);
}

static int peer_is_user(int fd, uid_t expected)
{
#ifdef __OpenBSD__
	uid_t uid;
	gid_t gid;
	if (getpeereid(fd, &uid, &gid) < 0) return -1;
	if (uid != expected) { errno = EACCES; return -1; }
	return 0;
#else
	/* Never weaken the uid check on a platform without the target API. */
	(void)fd;
	(void)expected;
	errno = ENOTSUP;
	return -1;
#endif
}

static void socket_address(const char *path, struct sockaddr_un *sa)
{
	memset(sa, 0, sizeof *sa);
	sa->sun_family = AF_UNIX;
	(void)snprintf(sa->sun_path, sizeof sa->sun_path, "%s", path);
#ifdef __OpenBSD__
	sa->sun_len = (unsigned char)sizeof *sa;
#endif
}

static int wait_fd(int fd, short events, double deadline)
{
	struct pollfd p;
	double remaining, now;
	int ms, n;
	p.fd = fd;
	p.events = events;
	p.revents = 0;
	for (;;) {
		now = now_ms();
		if (now < 0.0) return -1;
		remaining = deadline - now;
		if (remaining <= 0.0) { errno = ETIMEDOUT; return -1; }
		ms = remaining > INT_MAX ? INT_MAX : (int)remaining + 1;
		n = poll(&p, 1, ms);
		if (n < 0 && errno == EINTR) continue;
		if (n < 0) return -1;
		if (!n) { errno = ETIMEDOUT; return -1; }
		if (p.revents & POLLNVAL) { errno = EBADF; return -1; }
		if (p.revents & (events | POLLERR | POLLHUP)) return 0;
	}
}

static int connect_owner(openat_ipc *ipc, double deadline)
{
	struct sockaddr_un sa;
	struct stat s;
	int fd, e, n;
	socklen_t len;
	double now;
	socket_address(ipc->path, &sa);
	for (;;) {
		now = now_ms();
		if (now < 0.0) return -1;
		if (now >= deadline) { errno = ETIMEDOUT; return -1; }
		if (directory_unchanged(ipc)) return -1;
		if (fstatat(ipc->dirfd, ipc->name, &s, AT_SYMLINK_NOFOLLOW) < 0) {
			if (errno != ENOENT) return -1;
		} else {
			if (!private_socket(&s, ipc->uid)) { errno = EACCES; return -1; }
			fd = socket(AF_UNIX, SOCK_STREAM, 0);
			if (fd < 0) return -1;
			if (fd_flags(fd)) { e = errno; close(fd); errno = e; return -1; }
			n = connect(fd, (struct sockaddr *)&sa, sizeof sa);
			e = n < 0 ? errno : 0;
			if (n < 0 && (e == EINPROGRESS || e == EAGAIN || e == EWOULDBLOCK)) {
				if (wait_fd(fd, POLLOUT, deadline)) { e = errno; close(fd); errno = e; return -1; }
				len = sizeof e;
				if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &e, &len) < 0) e = errno;
			}
			if (!e) {
				if (!peer_is_user(fd, ipc->uid)) return fd;
				e = errno;
			}
			close(fd);
			if (e != ECONNREFUSED && e != ENOENT) { errno = e; return -1; }
		}
		/* Lock ownership can precede bind/listen. Retry only within one deadline. */
		(void)poll(NULL, 0, 20);
	}
}

static int forward(openat_ipc *ipc, const char *uri)
{
	char line[IPC_LINE + 1], ack[16];
	size_t size, sent = 0, used = 0;
	ssize_t n;
	double now = now_ms(), deadline;
	int fd, e = 0;
	if (now < 0.0) return -1;
	deadline = now + IPC_CLIENT_MS;
	if (uri && *uri) (void)snprintf(line, sizeof line, "OPEN %s\n", uri);
	else (void)snprintf(line, sizeof line, "RAISE\n");
	size = strlen(line);
	fd = connect_owner(ipc, deadline);
	if (fd < 0) return -1;
	while (sent < size) {
		if (wait_fd(fd, POLLOUT, deadline)) { e = errno; break; }
		n = send(fd, line + sent, size - sent, MSG_NOSIGNAL);
		if (n < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
		if (n <= 0) { e = n < 0 ? errno : EPIPE; break; }
		sent += (size_t)n;
	}
	if (!e && shutdown(fd, SHUT_WR) < 0) e = errno;
	while (!e && used < sizeof ack - 1) {
		if (wait_fd(fd, POLLIN, deadline)) { e = errno; break; }
		n = recv(fd, ack + used, sizeof ack - 1 - used, 0);
		if (n < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
		if (n <= 0) { e = n < 0 ? errno : EPROTO; break; }
		used += (size_t)n;
		ack[used] = '\0';
		if (memchr(ack, '\n', used)) {
			if (used == 9 && !memcmp(ack, "ACCEPTED\n", 9)) { close(fd); return 0; }
			e = used == 5 && !memcmp(ack, "BUSY\n", 5) ? EBUSY : EACCES;
		}
	}
	close(fd);
	errno = e ? e : EPROTO;
	return -1;
}

static int bind_owner(openat_ipc *ipc)
{
	struct sockaddr_un sa;
	struct stat s;
	mode_t mask;
	int e;
	if (directory_unchanged(ipc)) return -1;
	if (!fstatat(ipc->dirfd, ipc->name, &s, AT_SYMLINK_NOFOLLOW)) {
		/* A stale endpoint is removed only under this display's ownership lock. */
		if (!private_socket(&s, ipc->uid)) { errno = EACCES; return -1; }
		if (unlinkat(ipc->dirfd, ipc->name, 0) < 0) return -1;
	} else if (errno != ENOENT) return -1;
	ipc->fd = socket(AF_UNIX, SOCK_STREAM, 0);
	if (ipc->fd < 0 || fd_flags(ipc->fd)) return -1;
	socket_address(ipc->path, &sa);
	mask = umask(0177);
	e = bind(ipc->fd, (struct sockaddr *)&sa, sizeof sa);
	{ int saved = errno; umask(mask); errno = saved; }
	if (e < 0) return -1;
	if (fstatat(ipc->dirfd, ipc->name, &s, AT_SYMLINK_NOFOLLOW) < 0) return -1;
	if (!private_socket(&s, ipc->uid)) { errno = EACCES; return -1; }
	ipc->socket = s;
	ipc->bound = 1;
	if (directory_unchanged(ipc) || listen(ipc->fd, IPC_SLOTS) < 0) return -1;
	return 0;
}

int openat_ipc_claim(openat_ipc **out, const char *display, const char *uri)
{
	openat_ipc *ipc;
	openat_target target;
	int i, e, rc;
	if (!out) { errno = EINVAL; return OPENAT_IPC_ERROR; }
	*out = NULL;
	if (uri && *uri && openat_parse(uri, &target)) { errno = EINVAL; return OPENAT_IPC_ERROR; }
	if (!display || !*display) return OPENAT_IPC_DISABLED;
	ipc = (openat_ipc *)calloc(1, sizeof *ipc);
	if (!ipc) return OPENAT_IPC_ERROR;
	ipc->dirfd = ipc->lockfd = ipc->fd = -1;
	ipc->uid = geteuid();
	for (i = 0; i < IPC_SLOTS; i++) ipc->peers[i].fd = -1;
	if (prepare_directory(ipc, display) || prepare_lock(ipc)) goto fail;
	if (flock(ipc->lockfd, LOCK_EX | LOCK_NB) < 0) {
		if (errno != EWOULDBLOCK && errno != EAGAIN) goto fail;
		rc = forward(ipc, uri);
		e = errno;
		openat_ipc_close(ipc);
		errno = e;
		return rc ? OPENAT_IPC_ERROR : OPENAT_IPC_FORWARDED;
	}
	if (bind_owner(ipc)) goto fail;
	*out = ipc;
	return OPENAT_IPC_OWNER;
fail:
	e = errno;
	openat_ipc_close(ipc);
	errno = e;
	return OPENAT_IPC_ERROR;
}

static void close_peer(ipc_peer *p)
{
	if (p->fd >= 0) close(p->fd);
	memset(p, 0, sizeof *p);
	p->fd = -1;
}

static void parse_line(ipc_peer *p, openat_ipc_request request, void *arg)
{
	openat_target target;
	char *lf = (char *)memchr(p->line, '\n', p->used);
	const char *uri = NULL;
	if (p->used > IPC_LINE) { p->ack = "REJECT\n"; return; }
	if (!lf) {
		if (p->used == IPC_LINE) p->ack = "REJECT\n";
		return;
	}
	p->ack = "REJECT\n";
	if (lf != p->line + p->used - 1 || memchr(p->line, '\0', p->used)) return;
	*lf = '\0';
	if (!strcmp(p->line, "RAISE")) uri = "";
	else if (!strncmp(p->line, "OPEN ", 5) && !openat_parse(p->line + 5, &target)) uri = p->line + 5;
	if (uri && request && !request(arg, uri)) p->ack = "ACCEPTED\n";
}

void openat_ipc_step(openat_ipc *ipc, openat_ipc_request request, void *arg)
{
	int i, j, fd;
	ipc_peer *p;
	ssize_t n;
	double now;
	if (!ipc || ipc->fd < 0) return;
	now = now_ms();
	if (now < 0.0) return;
	for (i = 0; i < IPC_SLOTS; i++) {
		fd = accept(ipc->fd, NULL, NULL);
		if (fd < 0) break;
		if (fd_flags(fd) || peer_is_user(fd, ipc->uid)) { close(fd); continue; }
		for (j = 0; j < IPC_SLOTS && ipc->peers[j].fd >= 0; j++) { }
		if (j == IPC_SLOTS) {
			(void)send(fd, "BUSY\n", 5, MSG_NOSIGNAL);
			close(fd);
			continue;
		}
		p = &ipc->peers[j];
		p->fd = fd;
		p->deadline = now + IPC_REQUEST_MS;
	}
	for (i = 0; i < IPC_SLOTS; i++) {
		p = &ipc->peers[i];
		if (p->fd < 0) continue;
		if (now >= p->deadline) { close_peer(p); continue; }
		if (!p->ack) {
			n = recv(p->fd, p->line + p->used, sizeof p->line - p->used, 0);
			if (!n) { close_peer(p); continue; }
			if (n < 0 && errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK) { close_peer(p); continue; }
			if (n > 0) { p->used += (size_t)n; parse_line(p, request, arg); }
		}
		if (p->ack) {
			n = send(p->fd, p->ack + p->sent, strlen(p->ack) - p->sent, MSG_NOSIGNAL);
			if (n < 0 && errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK) { close_peer(p); continue; }
			if (n > 0) p->sent += (size_t)n;
			if (p->sent == strlen(p->ack)) close_peer(p);
		}
	}
}

int openat_ipc_fd(const openat_ipc *ipc) { return ipc ? ipc->fd : -1; }
const char *openat_ipc_path(const openat_ipc *ipc) { return ipc ? ipc->path : NULL; }
const char *openat_ipc_directory(const openat_ipc *ipc) { return ipc ? ipc->dir : NULL; }

void openat_ipc_close(openat_ipc *ipc)
{
	struct stat s;
	int i;
	if (!ipc) return;
	for (i = 0; i < IPC_SLOTS; i++) close_peer(&ipc->peers[i]);
	if (ipc->fd >= 0) close(ipc->fd);
	if (ipc->bound && !directory_unchanged(ipc) &&
	    !fstatat(ipc->dirfd, ipc->name, &s, AT_SYMLINK_NOFOLLOW) &&
	    private_socket(&s, ipc->uid) && same_inode(&s, &ipc->socket))
		(void)unlinkat(ipc->dirfd, ipc->name, 0);
	/* Never unlink the lock: replacing its inode could create two owners. */
	if (ipc->lockfd >= 0) close(ipc->lockfd);
	if (ipc->dirfd >= 0) close(ipc->dirfd);
	free(ipc);
}
