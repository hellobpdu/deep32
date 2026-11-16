#include "ws41.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#ifndef D32_WS_TIMEOUT_MS
#define D32_WS_TIMEOUT_MS 120000U
#endif

enum { HEADER_CAP = 28, CHUNK = 65536, STALE_SECONDS = 24 * 60 * 60,
	SWEEP_MAX = 65536, RANDOM_BYTES = 16 };

static enum d32_error
private_dir(int fd)
{
	struct stat st;
	if (fstat(fd, &st) == -1)
		return D32E_IO;
	if (!S_ISDIR(st.st_mode) || st.st_uid != geteuid() || (st.st_mode & 07777) != 0700)
		return D32E_PERM;
	return D32E_OK;
}

static bool
private_file(const struct stat *st)
{
	return S_ISREG(st->st_mode) && st->st_uid == geteuid() &&
	    st->st_nlink == 1 && (st->st_mode & 07777) == 0600;
}

static bool
same_file(const struct stat *left, const struct stat *right)
{
	return left->st_dev == right->st_dev && left->st_ino == right->st_ino;
}

static bool
spool_name(const char *name)
{
	static const char digits[] = "0123456789abcdef";
	return strlen(name) == WS_SPOOL_NAME_CAP - 1 && strncmp(name, "ws-", 3) == 0 &&
	    strspn(name + 3, digits) == RANDOM_BYTES * 2 &&
	    strcmp(name + 3 + RANDOM_BYTES * 2, ".pcapng") == 0;
}

static bool
old_spool(const struct stat *st, time_t now)
{
	return private_file(st) && difftime(now, st->st_mtime) > STALE_SECONDS;
}

static enum d32_error
sweep_file(int dirfd, const char *name, time_t now)
{
	struct stat before, opened, current;
	enum d32_error result = D32E_OK;
	if (fstatat(dirfd, name, &before, AT_SYMLINK_NOFOLLOW) == -1 || !old_spool(&before, now))
		return D32E_OK;
	int fd = openat(dirfd, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
	if (fd == -1)
		return D32E_OK;
	/* A viewer can retain a capture longer than the stale interval. */
	if (fstat(fd, &opened) == 0 && same_file(&before, &opened) && old_spool(&opened, now) &&
	    flock(fd, LOCK_EX | LOCK_NB) == 0 &&
	    fstatat(dirfd, name, &current, AT_SYMLINK_NOFOLLOW) == 0 &&
	    same_file(&opened, &current) && old_spool(&current, now) &&
	    unlinkat(dirfd, name, 0) == -1 && errno != ENOENT)
		result = D32E_IO;
	if (close(fd) == -1 && result == D32E_OK)
		result = D32E_IO;
	return result;
}

enum d32_error
ws_spool_sweep(int dirfd)
{
	enum d32_error result = private_dir(dirfd);
	if (result != D32E_OK)
		return result;
	time_t now = time(NULL);
	if (now == (time_t)-1)
		return D32E_IO;
	int scanfd = openat(dirfd, ".", O_RDONLY | O_CLOEXEC | O_DIRECTORY | O_NOFOLLOW);
	if (scanfd == -1)
		return D32E_IO;
	DIR *scan = fdopendir(scanfd);
	if (scan == NULL) {
		close(scanfd);
		return D32E_IO;
	}
	for (size_t count = 0;; ++count) {
		errno = 0;
		const struct dirent *entry = readdir(scan);
		if (entry == NULL) {
			if (errno != 0)
				result = errno == EINTR ? D32E_TEMP : D32E_IO;
			break;
		}
		if (count == SWEEP_MAX) {
			result = D32E_LIMIT;
			break;
		}
		if (spool_name(entry->d_name) &&
		    (result = sweep_file(dirfd, entry->d_name, now)) != D32E_OK)
			break;
	}
	if (closedir(scan) == -1 && result == D32E_OK)
		result = D32E_IO;
	return result;
}

static enum d32_error
create_spool(int dirfd, struct ws_spool *spool)
{
	static const char digits[] = "0123456789abcdef";
	unsigned char random[RANDOM_BYTES];
	char name[WS_SPOOL_NAME_CAP] = "ws-";
	memcpy(name + 3 + RANDOM_BYTES * 2, ".pcapng", sizeof(".pcapng"));
	for (unsigned attempt = 0; attempt < 32; ++attempt) {
		arc4random_buf(random, sizeof(random));
		for (size_t i = 0; i < sizeof(random); ++i) {
			name[3 + i * 2] = digits[random[i] >> 4];
			name[4 + i * 2] = digits[random[i] & 15];
		}
		int fd = openat(dirfd, name, O_RDWR | O_CLOEXEC | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
		if (fd == -1) {
			if (errno == EEXIST)
				continue;
			return errno == EINTR ? D32E_TEMP : D32E_CREATE;
		}
		spool->fd = fd;
		memcpy(spool->name, name, sizeof(spool->name));
		struct stat st;
		if (fstat(fd, &st) == -1)
			return D32E_IO;
		if (!private_file(&st))
			return D32E_PERM;
		if (flock(fd, LOCK_EX | LOCK_NB) == -1)
			return errno == EINTR ? D32E_TEMP : D32E_IO;
		return D32E_OK;
	}
	return D32E_CREATE;
}

static enum d32_error
check_time(uint64_t deadline, const volatile sig_atomic_t *cancelled, int *wait_ms)
{
	if (*cancelled != 0)
		return D32E_TEMP;
	uint64_t now = ws_now_ms();
	if (now == 0)
		return D32E_IO;
	if (now >= deadline)
		return D32E_TEMP;
	if (wait_ms != NULL) {
		uint64_t remaining = deadline - now;
		/* Poll in short slices to close the signal-before-poll race. */
		*wait_ms = remaining > 100 ? 100 : (int)remaining;
	}
	return D32E_OK;
}

static enum d32_error
read_chunk(int fd, void *buf, size_t cap, size_t *nread, uint64_t deadline,
    const volatile sig_atomic_t *cancelled)
{
	for (;;) {
		int wait_ms;
		enum d32_error result = check_time(deadline, cancelled, &wait_ms);
		if (result != D32E_OK)
			return result;
		ssize_t n = read(fd, buf, cap);
		if (n >= 0) {
			*nread = (size_t)n;
			return D32E_OK;
		}
		if (errno == EINTR)
			continue;
		if (errno != EAGAIN && errno != EWOULDBLOCK)
			return D32E_IO;
		struct pollfd ready = { .fd = fd, .events = POLLIN };
		if (poll(&ready, 1, wait_ms) == -1 && errno != EINTR)
			return D32E_IO;
		if ((ready.revents & POLLNVAL) != 0)
			return D32E_IO;
	}
}

static enum d32_error
write_chunk(int fd, const unsigned char *buf, size_t size, uint64_t deadline,
    const volatile sig_atomic_t *cancelled)
{
	while (size != 0) {
		enum d32_error result = check_time(deadline, cancelled, NULL);
		if (result != D32E_OK)
			return result;
		ssize_t n = write(fd, buf, size);
		if (n == -1 && errno == EINTR)
			continue;
		if (n <= 0)
			return D32E_IO;
		buf += (size_t)n;
		size -= (size_t)n;
	}
	return D32E_OK;
}

static enum d32_error
copy_capture(int input, int dirfd, struct ws_spool *spool, size_t *bytes,
    uint64_t deadline, const volatile sig_atomic_t *cancelled)
{
	unsigned char header[HEADER_CAP], buffer[CHUNK];
	size_t min = 4, n;
	enum d32_error result;
	for (;;) {
		while (*bytes < min) {
			result = read_chunk(input, header + *bytes, min - *bytes, &n, deadline, cancelled);
			if (result != D32E_OK)
				return result;
			if (n == 0)
				return D32E_FORMAT;
			*bytes += n;
		}
		if (min != 4)
			break;
		min = ws_header_min(header);
		if (min <= 4 || min > sizeof(header))
			return D32E_FORMAT;
	}
	result = create_spool(dirfd, spool);
	if (result != D32E_OK)
		return result;
	result = write_chunk(spool->fd, header, *bytes, deadline, cancelled);
	if (result != D32E_OK)
		return result;
	for (;;) {
		size_t remaining = WS_MAX_BYTES - *bytes;
		size_t wanted = remaining < sizeof(buffer) ? remaining + 1 : sizeof(buffer);
		result = read_chunk(input, buffer, wanted, &n, deadline, cancelled);
		if (result != D32E_OK)
			return result;
		if (n == 0)
			break;
		*bytes += n;
		if (n > remaining)
			return D32E_TOOBIG;
		result = write_chunk(spool->fd, buffer, n, deadline, cancelled);
		if (result != D32E_OK)
			return result;
	}
	result = ws_validate_fd(spool->fd, (off_t)*bytes);
	if (result != D32E_OK)
		return result;
	if (fsync(spool->fd) == -1)
		return errno == EINTR ? D32E_TEMP : D32E_IO;
	return check_time(deadline, cancelled, NULL);
}

enum d32_error
ws_spool_copy(int input, int dirfd, struct ws_spool *spool, size_t *bytes,
    const volatile sig_atomic_t *cancelled)
{
	*bytes = 0;
	enum d32_error result = private_dir(dirfd);
	if (result != D32E_OK)
		return result;
	struct stat st;
	if (fstat(input, &st) == -1)
		return D32E_IO;
	if (!S_ISREG(st.st_mode) && !S_ISFIFO(st.st_mode))
		return D32E_NOTREG;
	if (S_ISREG(st.st_mode) && (uintmax_t)st.st_size > WS_MAX_BYTES)
		return D32E_TOOBIG;
	uint64_t now = ws_now_ms();
	if (now == 0 || UINT64_MAX - now < D32_WS_TIMEOUT_MS)
		return D32E_IO;
	int flags = fcntl(input, F_GETFL);
	if (flags == -1 || (flags & O_ACCMODE) == O_WRONLY)
		return D32E_IO;
	bool nonblock = S_ISFIFO(st.st_mode) && (flags & O_NONBLOCK) == 0;
	if (nonblock && fcntl(input, F_SETFL, flags | O_NONBLOCK) == -1)
		return D32E_IO;
	result = copy_capture(input, dirfd, spool, bytes, now + D32_WS_TIMEOUT_MS, cancelled);
	if (nonblock && fcntl(input, F_SETFL, flags) == -1 && result == D32E_OK)
		result = D32E_IO;
	return result;
}

enum d32_error
ws_spool_remove(int dirfd, struct ws_spool *spool)
{
	if (spool->fd == -1)
		return D32E_OK;
	enum d32_error result = D32E_OK;
	struct stat opened, current;
	if (fstat(spool->fd, &opened) == -1)
		result = D32E_IO;
	else if (fstatat(dirfd, spool->name, &current, AT_SYMLINK_NOFOLLOW) == -1) {
		if (errno != ENOENT)
			result = D32E_IO;
	} else if (!same_file(&opened, &current))
		result = D32E_PERM;
	else {
		int rc;
		do {
			rc = unlinkat(dirfd, spool->name, 0);
		} while (rc == -1 && errno == EINTR);
		if (rc == -1 && errno != ENOENT)
			result = D32E_IO;
	}
	if (close(spool->fd) == -1 && result == D32E_OK)
		result = D32E_IO;
	spool->fd = -1;
	spool->name[0] = '\0';
	return result;
}
