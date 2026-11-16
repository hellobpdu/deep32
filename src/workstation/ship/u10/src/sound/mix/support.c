#include "support.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <sha2.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define SOUND_ROOT "/usr/local/share/d32/sounds"
#define AUCAT "/usr/bin/aucat"
/* The same approved xbell WAV as T21, from the Deep32 sound lane's SHA256. */
#define BELL_HASH "fbdd01263b4d8e77b16824644ae4f6a0da3c436950ebc4cddce7c9d5358570ec"

static bool
decimal(const char *s)
{
	return *s && s[strspn(s, "0123456789")] == '\0';
}

bool
mix_local_display(const char *name)
{
	size_t n;

	if (name == NULL)
		return false;
	if (strncmp(name, "unix:", 5) == 0)
		name += 4;
	if (*name++ != ':')
		return false;
	n = strspn(name, "0123456789");
	return n > 0 && n <= 5 && (name[n] == '\0' ||
	    (name[n] == '.' && decimal(name + n + 1)));
}

bool
mix_local_device(const char *name)
{
	const char *option;
	size_t n;

	if (name == NULL || *name == '\0' || !strcmp(name, "default"))
		return true;
	if (strncmp(name, "rsnd/", 5) == 0)
		return strlen(name + 5) <= 5 && decimal(name + 5);
	if (strncmp(name, "snd", 3) != 0)
		return false;
	option = name + 3;
	if (*option == ',') {
		n = strspn(++option, "0123456789");
		if (n == 0 || n > 5)
			return false;
		option += n;
	}
	if (*option++ != '/')
		return false;
	n = strspn(option, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-");
	return n > 0 && n <= 31 && option[n] == '\0';
}

int
mix_recording_permission(void)
{
	int mib[] = { CTL_KERN, KERN_AUDIO, KERN_AUDIO_RECORD }, state;
	size_t size = sizeof(state);

	if (sysctl(mib, 3, &state, &size, NULL, 0) == -1 || size != sizeof(state))
		return -1;
	return state != 0;
}

static int
absolute_dir(const char *path)
{
	char work[PATH_MAX], *name, *part;
	int fd, next;

	if (path == NULL || path[0] != '/' || strlcpy(work, path, sizeof(work)) >= sizeof(work))
		return -1;
	fd = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	part = work + 1;
	while (fd != -1 && (name = strsep(&part, "/")) != NULL) {
		if (*name == '\0')
			continue;
		if (!strcmp(name, ".") || !strcmp(name, "..")) {
			close(fd);
			return -1;
		}
		next = openat(fd, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
		close(fd);
		fd = next;
	}
	return fd;
}

static int
checked_dir(int fd, uid_t owner, bool private)
{
	struct stat st;

	if (fd == -1)
		return -1;
	if (fstat(fd, &st) == 0 && st.st_uid == owner &&
	    (private ? (st.st_mode & 07777) == 0700 : !(st.st_mode & 0022)))
		return fd;
	close(fd);
	return -1;
}

static int
user_subdir(int parent, const char *name, bool private)
{
	if (mkdirat(parent, name, 0700) == -1 && errno != EEXIST)
		return -1;
	return checked_dir(openat(parent, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW |
	    O_CLOEXEC), getuid(), private);
}

int
mix_preferences(void)
{
	int home, dt, dir;

	home = checked_dir(absolute_dir(getenv("HOME")), getuid(), false);
	if (home == -1)
		return -1;
	dt = user_subdir(home, ".dt", false);
	close(home);
	if (dt == -1)
		return -1;
	dir = user_subdir(dt, "d32", true);
	close(dt);
	return dir;
}

void
mix_bad_setting(const char *key, void *unused)
{
	const char *allowed[] = { key };

	(void)unused;
	(void)d32_log_allowed("d32-mix", allowed, 1, "verb=%s result=%s detail=%s",
	    "load", "invalid", *key == '\0' ? "none" : key);
}

uint64_t
mix_now(void)
{
	struct timespec t = { 0 };

	clock_gettime(CLOCK_MONOTONIC, &t);
	return (uint64_t)t.tv_sec * 1000 + (uint64_t)t.tv_nsec / 1000000;
}

int
mix_bell_open(void)
{
	struct stat before, after;
	unsigned char bytes[4096];
	char digest[SHA256_DIGEST_STRING_LENGTH];
	SHA2_CTX ctx;
	ssize_t n;
	int root, dir, fd;

	root = checked_dir(absolute_dir(SOUND_ROOT), 0, false);
	if (root == -1)
		return -1;
	dir = openat(root, "wav", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
	if (dir == -1 && errno == ENOENT)
		dir = root;
	else {
		close(root);
		dir = checked_dir(dir, 0, false);
	}
	if (dir == -1)
		return -1;
	fd = openat(dir, "xbell.wav", O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
	close(dir);
	if (fd == -1)
		return -1;
	if (fstat(fd, &before) == -1 || !S_ISREG(before.st_mode) || before.st_uid != 0 ||
	    (before.st_mode & 0022) || before.st_size < 44 || before.st_size > 65536)
		goto bad;
	SHA256Init(&ctx);
	for (;;) {
		n = read(fd, bytes, sizeof(bytes));
		if (n == -1 && errno == EINTR)
			continue;
		if (n <= 0)
			break;
		SHA256Update(&ctx, bytes, (size_t)n);
	}
	SHA256End(&ctx, digest);
	if (n == -1 || strcmp(digest, BELL_HASH) || fstat(fd, &after) == -1 ||
	    before.st_size != after.st_size ||
	    before.st_mtim.tv_sec != after.st_mtim.tv_sec ||
	    before.st_mtim.tv_nsec != after.st_mtim.tv_nsec ||
	    before.st_ctim.tv_sec != after.st_ctim.tv_sec ||
	    before.st_ctim.tv_nsec != after.st_ctim.tv_nsec || lseek(fd, 0, SEEK_SET) == -1)
		goto bad;
	return fd;
bad:
	close(fd);
	return -1;
}

enum d32_error
mix_bell_play(int sample, const char *device, pid_t *pid)
{
	char *const argv[] = { "aucat", "-f", (char *)device, "-h", "wav", "-i", "-", NULL };
	enum d32_error error;
	int nullfd;

	*pid = -1;
	if (sample == -1)
		return D32E_NOENT;
	nullfd = open("/dev/null", O_WRONLY | O_CLOEXEC);
	if (nullfd == -1)
		error = D32E_IO;
	else {
		error = d32_exec_fixed(AUCAT, argv, sample, nullfd, nullfd, pid);
		close(nullfd);
	}
	close(sample);
	return error;
}

void
mix_bell_stop(pid_t *pid)
{
	if (*pid > 0) {
		kill(*pid, SIGKILL);
		while (waitpid(*pid, NULL, 0) == -1 && errno == EINTR)
			;
	}
	*pid = -1;
}
