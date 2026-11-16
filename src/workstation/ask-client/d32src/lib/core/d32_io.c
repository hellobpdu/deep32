#include "d32_io.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static enum d32_error
file_error(int error)
{
	switch (error) {
	case ENOENT:
	case ENOTDIR:
		return D32E_NOENT;
	case ELOOP:
		return D32E_LINK;
	case EISDIR:
		return D32E_NOTREG;
	case EEXIST:
		return D32E_EXISTS;
	case EACCES:
	case EPERM:
	case EROFS:
		return D32E_PERM;
	case EBADF:
	case EINVAL:
	case ENAMETOOLONG:
		return D32E_INVAL;
	default:
		return D32E_IO;
	}
}

static enum d32_error
check_pcap(int fd)
{
	static const unsigned char magic[][4] = {
		{ 0xa1, 0xb2, 0xc3, 0xd4 },
		{ 0xd4, 0xc3, 0xb2, 0xa1 },
		{ 0xa1, 0xb2, 0x3c, 0x4d },
		{ 0x4d, 0x3c, 0xb2, 0xa1 },
		{ 0x0a, 0x0d, 0x0d, 0x0a }
	};
	unsigned char head[4];
	size_t used = 0, i;
	ssize_t n;

	while (used < sizeof(head)) {
		do {
			n = pread(fd, head + used, sizeof(head) - used,
			    (off_t)used);
		} while (n == -1 && errno == EINTR);
		if (n == -1)
			return D32E_IO;
		if (n == 0)
			return D32E_FORMAT;
		used += (size_t)n;
	}
	for (i = 0; i < sizeof(magic) / sizeof(magic[0]); i++) {
		if (memcmp(head, magic[i], sizeof(head)) == 0)
			return D32E_OK;
	}
	return D32E_FORMAT;
}

enum d32_error
d32_open_input(const char *path, enum d32_input_kind kind, size_t maxbytes,
    int *out, struct stat *st)
{
	struct stat info;
	int fd, flags, rc, error;
	enum d32_error result;

	if (out != NULL)
		*out = -1;
	if (path == NULL || *path == '\0' || out == NULL || st == NULL ||
	    (kind != D32_INPUT_FILE && kind != D32_INPUT_PCAP))
		return D32E_INVAL;
	do {
		fd = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
	} while (fd == -1 && errno == EINTR);
	if (fd == -1) {
		error = errno;
		/* Some special files, such as sockets, fail before fstat. */
		if ((error == ENXIO || error == ENODEV || error == EOPNOTSUPP) &&
		    lstat(path, &info) == 0 && !S_ISREG(info.st_mode))
			return S_ISLNK(info.st_mode) ? D32E_LINK : D32E_NOTREG;
		return file_error(error);
	}
	result = D32E_IO;
	do {
		rc = fstat(fd, &info);
	} while (rc == -1 && errno == EINTR);
	if (rc == -1)
		goto fail;
	if (!S_ISREG(info.st_mode)) {
		result = D32E_NOTREG;
		goto fail;
	}
	if (info.st_size == 0) {
		result = D32E_EMPTY;
		goto fail;
	}
	if (info.st_size < 0 || (uintmax_t)info.st_size > maxbytes) {
		result = D32E_TOOBIG;
		goto fail;
	}
	do {
		flags = fcntl(fd, F_GETFL);
	} while (flags == -1 && errno == EINTR);
	if (flags == -1)
		goto fail;
	do {
		rc = fcntl(fd, F_SETFL, flags & ~O_NONBLOCK);
	} while (rc == -1 && errno == EINTR);
	if (rc == -1)
		goto fail;
	if (kind == D32_INPUT_PCAP) {
		result = check_pcap(fd);
		if (result != D32E_OK)
			goto fail;
	}
	*st = info;
	*out = fd;
	return D32E_OK;

fail:
	(void)close(fd);
	return result;
}

static enum d32_error
write_file(int fd, const unsigned char *data, size_t len, mode_t mode)
{
	struct stat st;
	size_t count;
	ssize_t n;
	int rc;

	while (len != 0) {
		count = len > (size_t)SSIZE_MAX ? (size_t)SSIZE_MAX : len;
		do {
			n = write(fd, data, count);
		} while (n == -1 && errno == EINTR);
		if (n <= 0)
			return n == 0 ? D32E_IO : file_error(errno);
		data += n;
		len -= (size_t)n;
	}
	do {
		rc = fstat(fd, &st);
	} while (rc == -1 && errno == EINTR);
	if (rc == -1)
		return file_error(errno);
	/* The usual private 0600 write also works without a pledge fattr promise. */
	if ((st.st_mode & 0777) != mode) {
		do {
			rc = fchmod(fd, mode);
		} while (rc == -1 && errno == EINTR);
		if (rc == -1)
			return file_error(errno);
	}
	do {
		rc = fsync(fd);
	} while (rc == -1 && errno == EINTR);
	return rc == 0 ? D32E_OK : file_error(errno);
}

enum d32_error
d32_atomic_write(int dirfd, const char *name, const void *buf, size_t len,
    mode_t mode, bool noclobber)
{
	static const char hex[] = "0123456789abcdef";
	char temp[] = ".d32-00000000000000000000000000000000";
	unsigned char random[16];
	struct stat st;
	enum d32_error result;
	int fd = -1, rc;
	size_t attempt, i;

	if (dirfd < 0 || name == NULL || *name == '\0' ||
	    strcmp(name, ".") == 0 || strcmp(name, "..") == 0 ||
	    strchr(name, '/') != NULL || (buf == NULL && len != 0) ||
	    (mode & ~(mode_t)0777) != 0)
		return D32E_INVAL;
	do {
		rc = fstat(dirfd, &st);
	} while (rc == -1 && errno == EINTR);
	if (rc == -1)
		return file_error(errno);
	if (!S_ISDIR(st.st_mode))
		return D32E_NOTREG;

	/* mkstemp has no dirfd form. Create the equivalent private file
	 * exclusively relative to dirfd so directory renames cannot redirect it.
	 */
	for (attempt = 0; attempt < 128; attempt++) {
		arc4random_buf(random, sizeof(random));
		for (i = 0; i < sizeof(random); i++) {
			temp[5 + 2 * i] = hex[random[i] >> 4];
			temp[6 + 2 * i] = hex[random[i] & 15];
		}
		if (strcmp(temp, name) == 0)
			continue;
		do {
			fd = openat(dirfd, temp,
			    O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC,
			    0600);
		} while (fd == -1 && errno == EINTR);
		if (fd != -1)
			break;
		if (errno != EEXIST)
			return file_error(errno);
	}
	if (fd == -1)
		return D32E_IO;

	result = write_file(fd, buf, len, mode);
	if (close(fd) == -1 && result == D32E_OK)
		result = file_error(errno);
	if (result == D32E_OK) {
		if (noclobber)
			rc = linkat(dirfd, temp, dirfd, name, 0);
		else
			rc = renameat(dirfd, temp, dirfd, name);
		if (rc == 0 && !noclobber)
			return D32E_OK;
		if (rc == -1)
			result = file_error(errno);
	}

	do {
		rc = unlinkat(dirfd, temp, 0);
	} while (rc == -1 && errno == EINTR);
	if (rc == -1 && errno != ENOENT)
		return file_error(errno);
	return result;
}

void
d32_reader_init(struct d32_reader *reader, int fd, size_t maxbytes)
{
	reader->fd = fd;
	reader->remaining = maxbytes;
	reader->next = 0;
	reader->used = 0;
	reader->eof = false;
	reader->error = D32E_OK;
}

static enum d32_error
fill_reader(struct d32_reader *reader)
{
	size_t count;
	ssize_t n;

	if (reader->error != D32E_OK || reader->next != reader->used ||
	    reader->eof)
		return reader->error;
	count = sizeof(reader->buffer);
	if (count > reader->remaining)
		count = reader->remaining == 0 ? 1 : reader->remaining;
	do {
		n = read(reader->fd, reader->buffer, count);
	} while (n == -1 && errno == EINTR);
	if (n == -1)
		return reader->error = file_error(errno);
	if (n == 0) {
		reader->eof = true;
		return D32E_OK;
	}
	if (reader->remaining == 0)
		return reader->error = D32E_LIMIT;
	reader->remaining -= (size_t)n;
	reader->next = 0;
	reader->used = (size_t)n;
	return D32E_OK;
}

enum d32_error
d32_read_line(struct d32_reader *reader, char *buf, size_t capacity,
    size_t *length, bool *eof)
{
	const unsigned char *first, *newline;
	size_t available, count, used = 0;
	enum d32_error result;

	if (reader == NULL || buf == NULL || capacity == 0 || length == NULL ||
	    eof == NULL)
		return D32E_INVAL;
	*length = 0;
	*eof = false;
	buf[0] = '\0';
	for (;;) {
		result = fill_reader(reader);
		if (result != D32E_OK)
			break;
		if (reader->eof) {
			*eof = used == 0;
			break;
		}
		first = reader->buffer + reader->next;
		available = reader->used - reader->next;
		newline = memchr(first, '\n', available);
		count = newline == NULL ? available : (size_t)(newline - first);
		if (count > capacity - used - 1) {
			result = reader->error = D32E_LIMIT;
			break;
		}
		if (memchr(first, '\0', count) != NULL) {
			result = reader->error = D32E_FORMAT;
			break;
		}
		memcpy(buf + used, first, count);
		used += count;
		reader->next += count;
		if (newline != NULL) {
			reader->next++;
			break;
		}
	}
	if (result != D32E_OK) {
		buf[0] = '\0';
		return result;
	}
	buf[used] = '\0';
	*length = used;
	return D32E_OK;
}

enum d32_error
d32_read_record(struct d32_reader *reader, void *buf, size_t len, bool *eof)
{
	unsigned char *data = buf;
	size_t count, used = 0;
	enum d32_error result;

	if (reader == NULL || buf == NULL || len == 0 || eof == NULL)
		return D32E_INVAL;
	*eof = false;
	while (used < len) {
		result = fill_reader(reader);
		if (result != D32E_OK)
			return result;
		if (reader->eof) {
			if (used != 0)
				return reader->error = D32E_FORMAT;
			*eof = true;
			break;
		}
		count = reader->used - reader->next;
		if (count > len - used)
			count = len - used;
		memcpy(data + used, reader->buffer + reader->next, count);
		reader->next += count;
		used += count;
	}
	return D32E_OK;
}
