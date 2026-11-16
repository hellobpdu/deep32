#include "catalog.h"

#include <sys/stat.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#ifdef __OpenBSD__
#include <sys/mount.h>
#include <sys/sysctl.h>
#include <sys/time.h>
#endif

#define PACKAGE_LIMIT 4096
#define PACKAGE_SCAN_LIMIT 8192
#define PACKAGE_NAME_MAX 255

static void
printable(char *out, size_t capacity, const char *value, size_t length)
{
	size_t i;
	unsigned char ch;

	for (i = 0; i < length && i + 1 < capacity && value[i] != '\0'; i++) {
		ch = (unsigned char)value[i];
		out[i] = ch >= 0x20 && ch <= 0x7e ? (char)ch : '?';
	}
	out[i] = '\0';
}

#ifdef __OpenBSD__
#define SYSCTL_TEXT_MAX 4096
#define MOUNT_LIMIT 512

static enum sam_state
read_mib(int category, int name, void *value, size_t *length)
{
	int mib[2] = { category, name };

	if (sysctl(mib, 2, value, length, NULL, 0) == -1)
		return sam_error_from_errno(errno);
	return SAM_OK;
}

static const char *
field_state(enum sam_state state)
{
	switch (state) {
	case SAM_DENIED:
		return "Denied";
	case SAM_UNAVAILABLE:
		return "Unavailable";
	default:
		return "Unknown";
	}
}

static enum sam_state
summary_text(struct sam_result *result, const char *label, int category,
    int name, bool first_line)
{
	char value[SYSCTL_TEXT_MAX];
	size_t length = sizeof(value) - 1;
	enum sam_state state;

	state = read_mib(category, name, value, &length);
	if (state == SAM_OK) {
		if (length >= sizeof(value))
			state = SAM_INVALID;
		else {
			value[length] = '\0';
			if (first_line)
				length = strcspn(value, "\r\n");
			printable(value, sizeof(value), value, length);
			if (value[0] == '\0')
				state = SAM_INVALID;
		}
	}
	sam_append(result, "%s: %s\n", label,
	    state == SAM_OK ? value : field_state(state));
	return state;
}

static enum sam_state
summary_cpu(struct sam_result *result)
{
	int count;
	size_t length = sizeof(count);
	enum sam_state state;

	state = read_mib(CTL_HW, HW_NCPU, &count, &length);
	if (state == SAM_OK && (length != sizeof(count) || count < 1))
		state = SAM_INVALID;
	if (state == SAM_OK)
		sam_append(result, "hw.ncpu: %d\n", count);
	else
		sam_append(result, "hw.ncpu: %s\n", field_state(state));
	return state;
}

static enum sam_state
summary_memory(struct sam_result *result)
{
#ifdef HW_PHYSMEM64
	uint64_t bytes;
	const int mib = HW_PHYSMEM64;
#else
	unsigned int bytes;
	const int mib = HW_PHYSMEM;
#endif
	size_t length = sizeof(bytes);
	enum sam_state state;

	state = read_mib(CTL_HW, mib, &bytes, &length);
	if (state == SAM_OK && (length != sizeof(bytes) || bytes == 0))
		state = SAM_INVALID;
	if (state == SAM_OK)
		sam_append(result, "hw.physmem: %" PRIu64 " bytes\n",
		    (uint64_t)bytes);
	else
		sam_append(result, "hw.physmem: %s\n", field_state(state));
	return state;
}

static enum sam_state
summary_boottime(struct sam_result *result)
{
	struct timeval boot;
	struct tm utc;
	char value[64];
	size_t length = sizeof(boot);
	enum sam_state state;

	state = read_mib(CTL_KERN, KERN_BOOTTIME, &boot, &length);
	if (state == SAM_OK && (length != sizeof(boot) ||
	    gmtime_r(&boot.tv_sec, &utc) == NULL ||
	    strftime(value, sizeof(value), "%Y-%m-%d %H:%M:%S UTC", &utc) == 0))
		state = SAM_INVALID;
	sam_append(result, "kern.boottime: %s\n",
	    state == SAM_OK ? value : field_state(state));
	return state;
}

static int
compare_mounts(const void *a, const void *b)
{
	const struct statfs *left = a, *right = b;
	int order;

	order = strncmp(left->f_mntonname, right->f_mntonname,
	    sizeof(left->f_mntonname));
	if (order != 0)
		return order;
	order = strncmp(left->f_fstypename, right->f_fstypename,
	    sizeof(left->f_fstypename));
	if (order != 0)
		return order;
	if (left->f_bsize != right->f_bsize)
		return left->f_bsize < right->f_bsize ? -1 : 1;
	if (left->f_blocks != right->f_blocks)
		return left->f_blocks < right->f_blocks ? -1 : 1;
	if (left->f_bavail != right->f_bavail)
		return left->f_bavail < right->f_bavail ? -1 : 1;
	return 0;
}

static void
byte_count(char *out, size_t capacity, uint64_t blocks, uint64_t block_size)
{
	if (block_size == 0 || blocks > UINT64_MAX / block_size)
		(void)snprintf(out, capacity, "Unknown");
	else
		(void)snprintf(out, capacity, "%" PRIu64, blocks * block_size);
}
#endif

enum sam_state
sam_system_summary(const struct sam_context *ctx, struct sam_result *result)
{
#ifdef __OpenBSD__
	enum sam_state fields[6];
	size_t i;

	(void)ctx;
	fields[0] = summary_text(result, "kern.osrelease", CTL_KERN,
	    KERN_OSRELEASE, false);
	fields[1] = summary_text(result, "kern.version", CTL_KERN, KERN_VERSION,
	    true);
	fields[2] = summary_text(result, "hw.model", CTL_HW, HW_MODEL, false);
	fields[3] = summary_cpu(result);
	fields[4] = summary_memory(result);
	fields[5] = summary_boottime(result);
	for (i = 0; i < sizeof(fields) / sizeof(fields[0]); i++)
		if (fields[i] == SAM_OK)
			return SAM_OK;
	return fields[0];
#else
	(void)ctx;
	(void)result;
	return SAM_UNAVAILABLE;
#endif
}

enum sam_state
sam_disk_free(const struct sam_context *ctx, struct sam_result *result)
{
#ifdef __OpenBSD__
	struct statfs *mounts;
	char mount[sizeof(mounts->f_mntonname) + 1];
	char type[sizeof(mounts->f_fstypename) + 1], free_bytes[32], total[32];
	int count, i, local = 0;
	int64_t available;
	enum sam_state state = SAM_OK;

	(void)ctx;
	/* One extra slot detects truncation without a size-query race. */
	mounts = calloc(MOUNT_LIMIT + 1, sizeof(*mounts));
	if (mounts == NULL)
		return SAM_IO;
	count = getfsstat(mounts, (MOUNT_LIMIT + 1) * sizeof(*mounts), MNT_NOWAIT);
	if (count == -1) {
		state = sam_error_from_errno(errno);
		goto done;
	}
	if (count > MOUNT_LIMIT) {
		result->truncated = true;
		sam_append(result, "Mount snapshot exceeds the inspection limit.\n");
		state = SAM_UNAVAILABLE;
		goto done;
	}
	for (i = 0; i < count; i++)
		if ((mounts[i].f_flags & MNT_LOCAL) != 0)
			mounts[local++] = mounts[i];
	qsort(mounts, (size_t)local, sizeof(*mounts), compare_mounts);
	for (i = 0; i < local; i++) {
		printable(mount, sizeof(mount), mounts[i].f_mntonname,
		    sizeof(mounts[i].f_mntonname));
		printable(type, sizeof(type), mounts[i].f_fstypename,
		    sizeof(mounts[i].f_fstypename));
		available = (int64_t)mounts[i].f_bavail;
		byte_count(free_bytes, sizeof(free_bytes),
		    available > 0 ? (uint64_t)available : 0, mounts[i].f_bsize);
		byte_count(total, sizeof(total), mounts[i].f_blocks, mounts[i].f_bsize);
		if (!sam_append(result, "%s (%s): %s available, %s total bytes\n",
		    mount, type, free_bytes, total))
			break;
	}
	if (local == 0)
		sam_append(result, "No local mounts.\n");
done:
	free(mounts);
	return state;
#else
	(void)ctx;
	(void)result;
	return SAM_UNAVAILABLE;
#endif
}

static int
compare_packages(const void *a, const void *b)
{
	return strcmp(a, b);
}

enum sam_state
sam_package_list(const struct sam_context *ctx, struct sam_result *result)
{
	enum sam_state state;
	int fd;
	DIR *dir;
	struct dirent *entry;
	struct stat st;
	char (*names)[PACKAGE_NAME_MAX + 1];
	char name[PACKAGE_NAME_MAX + 1];
	size_t count = 0, scanned = 0, i, length;

	state = sam_open_read(ctx, "/var/db/pkg", true, &fd);
	if (state != SAM_OK)
		return state;
	dir = fdopendir(fd);
	if (dir == NULL) {
		state = sam_error_from_errno(errno);
		(void)close(fd);
		return state;
	}
	names = calloc(PACKAGE_LIMIT, sizeof(*names));
	if (names == NULL) {
		(void)closedir(dir);
		return SAM_IO;
	}
	for (;;) {
		errno = 0;
		entry = readdir(dir);
		if (entry == NULL) {
			if (errno != 0)
				state = sam_error_from_errno(errno);
			break;
		}
		if (++scanned > PACKAGE_SCAN_LIMIT)
			goto limit;
		if (entry->d_name[0] == '.')
			continue;
		if (fstatat(fd, entry->d_name, &st, AT_SYMLINK_NOFOLLOW) == -1) {
			if (errno == ENOENT)
				continue;
			state = sam_error_from_errno(errno);
			break;
		}
		if (!S_ISDIR(st.st_mode))
			continue;
		length = strnlen(entry->d_name, sizeof(name));
		if (count == PACKAGE_LIMIT || length == sizeof(name))
			goto limit;
		memcpy(names[count++], entry->d_name, length + 1);
	}
	if (state != SAM_OK)
		goto done;
	qsort(names, count, sizeof(*names), compare_packages);
	for (i = 0; i < count; i++) {
		printable(name, sizeof(name), names[i], strlen(names[i]));
		if (!sam_append(result, "%s\n", name))
			break;
	}
	if (count == 0)
		sam_append(result, "No installed package directories.\n");
	goto done;
limit:
	result->truncated = true;
	sam_append(result, "Package directory exceeds the inspection limit.\n");
	state = SAM_UNAVAILABLE;
done:
	free(names);
	(void)closedir(dir);
	return state;
}
