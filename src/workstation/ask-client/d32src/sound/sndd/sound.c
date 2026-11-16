#include "sound.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <sha2.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define ASSET_ROOT "/usr/local/share/d32/sounds"
#define LOCK_FILE ".sndd.lock"
#define PLAYER "/usr/bin/aucat"
#define PROFILE_BIT(p) (1U << (p))
#define DESKTOP_PROFILES (PROFILE_BIT(SND_WORKSTATION) | PROFILE_BIT(SND_FULL))

/* Pinned from the approved Deep32 sound lane's manifest.tsv and SHA256. */
const struct snd_sample snd_samples[SND_EVENTS] = {
	{ "xbell", "X bell", "fbdd01263b4d8e77b16824644ae4f6a0da3c436950ebc4cddce7c9d5358570ec", /* SHA-256 */
	    DESKTOP_PROFILES | PROFILE_BIT(SND_CLASSIC) },
	{ "login", "Session start", "15ee57926915ca7a8b761e187567f744283d141ba23b9d3e13256fef5234c919", /* SHA-256 */
	    DESKTOP_PROFILES },
	{ "logout", "Session end", "ef4f13eaac15c1a9884ee1018781ae840f8e8aa399a1dff7c9e1349b9f83b563", /* SHA-256 */
	    DESKTOP_PROFILES }
};

const char *const snd_profile_names[SND_PROFILES] = { "silent", "classic", "workstation", "full" };
const char *const snd_profile_labels[SND_PROFILES] = { "Silent", "Classic", "Workstation", "Full" };

static int
checked_dir(int fd, uid_t uid, bool private)
{
	struct stat st;

	if (fd == -1)
		return -1;
	if (fstat(fd, &st) == 0 && st.st_uid == uid &&
	    (private ? (st.st_mode & 07777) == 0700 : !(st.st_mode & 0022)))
		return fd;
	close(fd);
	return -1;
}

/* Open each absolute component independently, including HOME's ancestors. */
static int
absolute_dir(const char *path)
{
	char buf[PATH_MAX], *next, *name;
	int fd, child;

	if (path == NULL || path[0] != '/' || strlcpy(buf, path, sizeof(buf)) >= sizeof(buf))
		return -1;
	fd = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	next = buf + 1;
	while (fd != -1 && (name = strsep(&next, "/")) != NULL) {
		if (*name == '\0')
			continue;
		if (strcmp(name, ".") == 0 || strcmp(name, "..") == 0) {
			close(fd);
			return -1;
		}
		child = openat(fd, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
		close(fd);
		fd = child;
	}
	return fd;
}

static int
user_dir(int fd, const char *name, bool private)
{
	if (mkdirat(fd, name, 0700) == -1 && errno != EEXIST)
		return -1;
	return checked_dir(openat(fd, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW |
	    O_CLOEXEC), getuid(), private);
}

int
snd_prefs_dir(void)
{
	int home, dt, dir;

	home = checked_dir(absolute_dir(getenv("HOME")), getuid(), false);
	if (home == -1)
		return -1;
	dt = user_dir(home, ".dt", false);
	close(home);
	if (dt == -1)
		return -1;
	dir = user_dir(dt, "d32", true);
	close(dt);
	return dir;
}

enum snd_profile
snd_profile_get(const struct d32_prefs *prefs)
{
	const char *name;
	int i;

	if (prefs != NULL) {
		name = d32_prefs_get(prefs, "profile", "workstation");
		for (i = 0; i < SND_PROFILES; i++)
			if (strcmp(name, snd_profile_names[i]) == 0)
				return i;
	}
	return SND_WORKSTATION;
}

void
snd_prefs_bad(const char *key, void *tag)
{
	const char *allowed[] = { key };

	(void)d32_log_allowed(tag, allowed, 1, "verb=%s result=%s detail=%s",
	    "load", "invalid", *key == '\0' ? "none" : key);
}

bool
snd_enabled(enum snd_profile profile, enum snd_event event)
{
	return (snd_samples[event].profiles & PROFILE_BIT(profile)) != 0;
}

static int
display_number(const char *name)
{
	char number[6];
	const char *end, *error;
	size_t n;
	long long value;

	if (name == NULL)
		return -1;
	if (strncmp(name, "unix:", 5) == 0)
		name += 4;
	if (*name++ != ':')
		return -1;
	n = strspn(name, "0123456789");
	if (n == 0 || n >= sizeof(number))
		return -1;
	end = name + n;
	if (*end != '\0' && (*end != '.' || end[1] == '\0' ||
	    end[1 + strspn(end + 1, "0123456789")] != '\0'))
		return -1;
	memcpy(number, name, n);
	number[n] = '\0';
	value = strtonum(number, 0, 65535, &error);
	return error == NULL ? (int)value : -1;
}

bool
snd_local_display(const char *name)
{
	return display_number(name) >= 0;
}

static int
regular_at(int dir, const char *name, uid_t uid, off_t limit, struct stat *st)
{
	int fd;

	fd = openat(dir, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
	if (fd == -1)
		return -1;
	if (fstat(fd, st) == 0 && S_ISREG(st->st_mode) && st->st_uid == uid &&
	    !(st->st_mode & 0022) && st->st_size > 0 && st->st_size <= limit)
		return fd;
	close(fd);
	return -1;
}

void
snd_assets_load(struct snd_assets *assets)
{
	struct d32_reader reader;
	struct stat st;
	char line[512], expected[64], *p, *name, *wav;
	bool eof, seen[SND_EVENTS] = { false };
	size_t n;
	int root, manifest, i;
	enum d32_error err;

	memset(assets, 0, sizeof(*assets));
	assets->fd = -1;
	root = checked_dir(absolute_dir(ASSET_ROOT), 0, false);
	if (root == -1)
		return;
	manifest = regular_at(root, "manifest.tsv", 0, 32768, &st);
	if (manifest == -1) {
		close(root);
		return;
	}
	d32_reader_init(&reader, manifest, 32769);
	while ((err = d32_read_line(&reader, line, sizeof(line), &n, &eof)) == D32E_OK && !eof) {
		p = line;
		name = strsep(&p, "\t");
		wav = strsep(&p, "\t");
		for (i = 0; i < SND_EVENTS; i++) {
			if (strcmp(name, snd_samples[i].id) != 0)
				continue;
			snprintf(expected, sizeof(expected), "wav/%s.wav", name);
			assets->listed[i] = !seen[i] && wav != NULL && strcmp(wav, expected) == 0;
			seen[i] = true;
		}
	}
	close(manifest);
	if (err != D32E_OK) {
		memset(assets->listed, 0, sizeof(assets->listed));
		close(root);
		return;
	}
	/* The staging manifest uses wav/; the package may install the WAVs flat. */
	assets->fd = openat(root, "wav", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
	if (assets->fd == -1 && errno == ENOENT)
		assets->fd = root;
	else {
		close(root);
		assets->fd = checked_dir(assets->fd, 0, false);
	}
}

void
snd_assets_close(struct snd_assets *assets)
{
	if (assets->fd != -1)
		close(assets->fd);
	assets->fd = -1;
}

static bool
same_file(const struct stat *a, const struct stat *b)
{
	return a->st_dev == b->st_dev && a->st_ino == b->st_ino && a->st_size == b->st_size &&
	    a->st_mtim.tv_sec == b->st_mtim.tv_sec && a->st_mtim.tv_nsec == b->st_mtim.tv_nsec &&
	    a->st_ctim.tv_sec == b->st_ctim.tv_sec && a->st_ctim.tv_nsec == b->st_ctim.tv_nsec;
}

int
snd_sample_open(struct snd_assets *assets, enum snd_event event)
{
	char file[64], digest[SHA256_DIGEST_STRING_LENGTH];
	unsigned char buf[8192];
	SHA2_CTX ctx;
	struct stat before, after;
	ssize_t n;
	int fd;

	if (assets->fd == -1 || !assets->listed[event])
		return -1;
	snprintf(file, sizeof(file), "%s.wav", snd_samples[event].id);
	fd = regular_at(assets->fd, file, 0, 512 * 1024, &before);
	if (fd == -1)
		return -1;
	if (assets->verified[event] && same_file(&before, &assets->last[event]))
		return fd;
	assets->verified[event] = false;
	SHA256Init(&ctx);
	for (;;) {
		n = read(fd, buf, sizeof(buf));
		if (n == -1 && errno == EINTR)
			continue;
		if (n <= 0)
			break;
		SHA256Update(&ctx, buf, (size_t)n);
	}
	SHA256End(&ctx, digest);
	if (n == -1 || fstat(fd, &after) == -1 || !same_file(&before, &after) ||
	    strcmp(digest, snd_samples[event].sha256) != 0 || lseek(fd, 0, SEEK_SET) == -1) {
		close(fd);
		return -1;
	}
	assets->last[event] = before;
	assets->verified[event] = true;
	return fd;
}

enum d32_error
snd_player_start(int sample, pid_t *pid)
{
	char *const argv[] = { "aucat", "-h", "wav", "-i", "-", NULL };
	enum d32_error error;
	int nullfd;

	*pid = -1;
	if (sample == -1)
		return D32E_UNAVAIL;
	nullfd = open("/dev/null", O_WRONLY | O_CLOEXEC);
	if (nullfd == -1)
		error = D32E_IO;
	else {
		/* aucat reads the very fd that was hashed. No pathname is reopened. */
		error = d32_exec_fixed(PLAYER, argv, sample, nullfd, nullfd, pid);
		close(nullfd);
	}
	close(sample);
	return error;
}

void
snd_player_stop(pid_t *pid)
{
	if (*pid > 0) {
		kill(*pid, SIGKILL);
		while (waitpid(*pid, NULL, 0) == -1 && errno == EINTR)
			;
	}
	*pid = -1;
}

uint64_t
snd_now(void)
{
	struct timespec t = { 0 };

	clock_gettime(CLOCK_MONOTONIC, &t);
	return (uint64_t)t.tv_sec * 1000 + (uint64_t)t.tv_nsec / 1000000;
}

bool
snd_rate_allow(struct snd_rate *rate, uint64_t now)
{
	uint64_t elapsed;

	if (!rate->initialized) {
		rate->initialized = true;
		rate->last = now;
		rate->units = 7000;
	}
	elapsed = now - rate->last;
	rate->last = now;
	rate->units = elapsed >= 1000 ? 7000 : rate->units + (unsigned)elapsed * 7;
	if (rate->units > 7000)
		rate->units = 7000;
	/* A burst bucket alone could admit 20 over two seconds. Keep both limits. */
	while (rate->count && now - rate->recent[rate->first] >= 2000) {
		rate->first = (rate->first + 1) % 14;
		rate->count--;
	}
	if (rate->units < 1000 || rate->count == 14)
		return false;
	rate->recent[(rate->first + rate->count++) % 14] = now;
	rate->units -= 1000;
	return true;
}

static int
lock_file(int dir, int flags)
{
	struct stat st;
	int fd;

	fd = openat(dir, LOCK_FILE, O_RDWR | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC | flags, 0600);
	if (fd == -1)
		return -1;
	if (fstat(fd, &st) == 0 && S_ISREG(st.st_mode) && st.st_uid == getuid() &&
	    st.st_nlink == 1 && (st.st_mode & 07777) == 0600)
		return fd;
	close(fd);
	errno = EPERM;
	return -1;
}

int
snd_lock(int dir)
{
	int fd, display = display_number(getenv("DISPLAY"));
	struct flock lock = { .l_type = F_WRLCK, .l_whence = SEEK_SET, .l_len = 1 };

	if (display < 0) {
		errno = EINVAL;
		return -1;
	}
	lock.l_start = display;
	fd = lock_file(dir, O_CREAT);
	if (fd != -1 && fcntl(fd, F_SETLK, &lock) == -1) {
		int saved = errno;

		close(fd);
		errno = saved;
		return -1;
	}
	return fd;
}

int
snd_pid_write(int dir)
{
	char file[32], buf[32];
	int n = snprintf(buf, sizeof(buf), "%ld\n", (long)getpid());
	int display = display_number(getenv("DISPLAY"));

	if (display < 0)
		return -1;
	snprintf(file, sizeof(file), "sndd.%d.pid", display);
	/* The caller holds the display lock, so a previous PID record is stale. */
	return d32_atomic_write(dir, file, buf, (size_t)n, 0600, false) == D32E_OK ? 0 : -1;
}

void
snd_pid_clear(int dir)
{
	char file[32];
	int display = display_number(getenv("DISPLAY"));

	if (display >= 0) {
		snprintf(file, sizeof(file), "sndd.%d.pid", display);
		unlinkat(dir, file, 0);
	}
}

static pid_t
lock_owner(int fd, int display)
{
	struct flock owner = { .l_type = F_WRLCK, .l_whence = SEEK_SET,
	    .l_start = display, .l_len = 1 };

	if (fcntl(fd, F_GETLK, &owner) == -1)
		return -1;
	return owner.l_type == F_WRLCK ? owner.l_pid : 0;
}

int
snd_signal(int dir, int sig, pid_t expected)
{
	enum { STOP_WAIT_MS = 2000, STOP_POLL_MS = 20 };
	struct stat st;
	char file[32], buf[32];
	const char *err;
	long long pid;
	ssize_t n;
	uint64_t deadline, now, remaining;
	pid_t owner;
	int fd, lock, result = -1, display = display_number(getenv("DISPLAY"));

	if (display < 0 || (sig != SIGHUP && sig != SIGTERM))
		return -1;
	snprintf(file, sizeof(file), "sndd.%d.pid", display);
	fd = regular_at(dir, file, getuid(), sizeof(buf) - 1, &st);
	if (fd == -1)
		return -1;
	if (st.st_nlink != 1 || (st.st_mode & 07777) != 0600) {
		close(fd);
		return -1;
	}
	n = read(fd, buf, sizeof(buf) - 1);
	close(fd);
	if (n < 2 || buf[n - 1] != '\n' || memchr(buf, '\0', (size_t)n))
		return -1;
	buf[n - 1] = '\0';
	pid = strtonum(buf, 2, INT_MAX, &err);
	if (err != NULL || (expected != 0 && (pid_t)pid != expected) ||
	    (lock = lock_file(dir, 0)) == -1)
		return -1;
	/* A stale or substituted PID alone is insufficient to signal a process. */
	if (lock_owner(lock, display) == (pid_t)pid)
		result = kill((pid_t)pid, sig);
	if (result == 0 && sig == SIGTERM) {
		/* The agent restores XKB before exiting and releasing the lock. */
		deadline = snd_now() + STOP_WAIT_MS;
		while ((owner = lock_owner(lock, display)) == (pid_t)pid) {
			now = snd_now();
			if (now >= deadline)
				break;
			remaining = deadline - now;
			(void)poll(NULL, 0, remaining < STOP_POLL_MS ?
			    (int)remaining : STOP_POLL_MS);
		}
		if (owner == (pid_t)pid || owner == -1)
			result = -1;
	}
	close(lock);
	return result;
}
