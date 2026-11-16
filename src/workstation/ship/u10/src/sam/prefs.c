#include "sam.h"
#include "d32core.h"

#include <sys/stat.h>

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static const char *const page_keys[SAM_CATEGORY_COUNT] = {
	"summary", "software", "storage", "services", "boot", "desktop"
};

static enum sam_state
prefs_error(enum d32_error error)
{
	switch (error) {
	case D32E_OK:
		return SAM_OK;
	case D32E_NOENT:
	case D32E_UNAVAIL:
		return SAM_UNAVAILABLE;
	case D32E_LINK:
	case D32E_PERM:
	case D32E_NEWER:
		return SAM_DENIED;
	case D32E_NOTREG:
	case D32E_TOOBIG:
	case D32E_EMPTY:
	case D32E_INVAL:
	case D32E_FORMAT:
	case D32E_LIMIT:
	case D32E_VERSION:
		return SAM_INVALID;
	default:
		return SAM_IO;
	}
}

static enum sam_state
directory_owner(int fd, bool private)
{
	struct stat st;

	if (fstat(fd, &st) == -1)
		return sam_error_from_errno(errno);
	if (!S_ISDIR(st.st_mode))
		return SAM_INVALID;
	if (st.st_uid != geteuid() || (st.st_mode & 022) != 0 ||
	    (private && (st.st_mode & 07777) != 0700))
		return SAM_DENIED;
	return SAM_OK;
}

static enum sam_state
open_child(int parent, const char *name, bool create, bool private, int *out)
{
	const int flags = O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC;
	enum sam_state state;
	int fd;

	fd = openat(parent, name, flags);
	if (fd == -1 && errno == ENOENT && create) {
		if (mkdirat(parent, name, 0700) == -1 && errno != EEXIST)
			return sam_error_from_errno(errno);
		fd = openat(parent, name, flags);
	}
	if (fd == -1)
		return sam_error_from_errno(errno);
	state = directory_owner(fd, private);
	if (state == SAM_OK)
		*out = fd;
	else
		(void)close(fd);
	return state;
}

static void
log_bad_pref(const char *key, void *unused)
{
	const char *allowed[] = {"prefs.sam", key[0] == '\0' ? "invalid" : key};

	(void)unused;
	(void)d32_log_allowed("d32-sam", allowed, sizeof(allowed) / sizeof(allowed[0]),
	    "id=%s result=%s detail=%s", allowed[0], "dataerr", allowed[1]);
}

static void
read_values(struct sam_prefs *prefs, struct d32_prefs *document)
{
	const char *page = d32_prefs_get(document, "default.page", "summary");
	const char *refresh = d32_prefs_get(document, "refresh.interval", "off");
	size_t i;

	prefs->default_page = SAM_SUMMARY;
	prefs->refresh_seconds = 0;
	for (i = 0; i < SAM_CATEGORY_COUNT; i++) {
		if (strcmp(page, page_keys[i]) == 0) {
			prefs->default_page = (enum sam_category)i;
			break;
		}
	}
	if (strcmp(refresh, "30") == 0)
		prefs->refresh_seconds = 30;
	else if (strcmp(refresh, "300") == 0)
		prefs->refresh_seconds = 300;
}

enum sam_state
sam_prefs_open(struct sam_prefs *prefs, bool create)
{
	struct d32_prefs *document = NULL;
	const char *home = getenv("HOME");
	char path[PATH_MAX];
	enum sam_state state;
	enum d32_error error;
	int fd, desktop, length;

	if (prefs == NULL)
		return SAM_INVALID;
	memset(prefs, 0, sizeof(*prefs));
	prefs->dirfd = -1;
	prefs->default_page = SAM_SUMMARY;
	if (home == NULL || home[0] == '\0')
		return SAM_UNAVAILABLE;
	length = snprintf(path, sizeof(path), "%s/.dt/d32", home);
	if (length < 0 || (size_t)length >= sizeof(path))
		return SAM_INVALID;
	state = sam_open_read(NULL, home, true, &fd);
	if (state != SAM_OK)
		return state;
	state = directory_owner(fd, false);
	if (state == SAM_OK)
		state = open_child(fd, ".dt", create, false, &desktop);
	(void)close(fd);
	if (state != SAM_OK)
		return state;
	state = open_child(desktop, "d32", create, true, &prefs->dirfd);
	(void)close(desktop);
	if (state != SAM_OK)
		return state;
	memcpy(prefs->path, path, (size_t)length + 1);
	error = d32_prefs_load(prefs->dirfd, "sam", 1, log_bad_pref, NULL, &document);
	if (error == D32E_NEWER)
		prefs->newer = true;
	else if (error == D32E_OK)
		read_values(prefs, document);
	else {
		d32_prefs_free(document);
		return prefs_error(error);
	}
	if (create)
		prefs->document = document;
	else
		d32_prefs_free(document);
	return SAM_OK;
}

void
sam_prefs_close(struct sam_prefs *prefs)
{
	if (prefs == NULL)
		return;
	d32_prefs_free(prefs->document);
	prefs->document = NULL;
	if (prefs->dirfd >= 0) {
		(void)close(prefs->dirfd);
		prefs->dirfd = -1;
	}
}

enum sam_state
sam_prefs_save(struct sam_prefs *prefs, enum sam_category page, unsigned seconds)
{
	struct d32_prefs *document = NULL;
	enum d32_error error;
	const char *refresh;

	if (prefs == NULL || (unsigned)page >= SAM_CATEGORY_COUNT)
		return SAM_INVALID;
	switch (seconds) {
	case 0: refresh = "off"; break;
	case 30: refresh = "30"; break;
	case 300: refresh = "300"; break;
	default: return SAM_INVALID;
	}
	if (prefs->document == NULL || prefs->newer || prefs->dirfd < 0)
		return SAM_DENIED;
	/* Stage changes separately. A failed save leaves no pending edits in prefs. */
	error = d32_prefs_load(prefs->dirfd, "sam", 1, log_bad_pref, NULL, &document);
	if (error == D32E_OK && page != prefs->default_page)
		error = d32_prefs_set(document, "default.page", page_keys[page]);
	if (error == D32E_OK && seconds != prefs->refresh_seconds)
		error = d32_prefs_set(document, "refresh.interval", refresh);
	if (error == D32E_OK)
		error = d32_prefs_save(document);
	if (error == D32E_NEWER) {
		prefs->newer = true;
		prefs->default_page = SAM_SUMMARY;
		prefs->refresh_seconds = 0;
	}
	if (error != D32E_OK) {
		d32_prefs_free(document);
		return prefs_error(error);
	}
	d32_prefs_free(prefs->document);
	prefs->document = document;
	read_values(prefs, document);
	return SAM_OK;
}

static enum sam_state
set_value(char *value, size_t size, const char *text)
{
	size_t length = strlen(text);

	if (length >= size)
		return SAM_INVALID;
	memcpy(value, text, length + 1);
	return SAM_OK;
}

static enum sam_state
look_pref(int dirfd, char *value, size_t size)
{
	char raw[5];
	struct stat st;
	size_t used = 0;
	ssize_t count;
	enum sam_state state = SAM_INVALID;
	int fd;

	if (dirfd < 0)
		return SAM_UNAVAILABLE;
	fd = openat(dirfd, "look", O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
	if (fd == -1)
		return sam_error_from_errno(errno);
	if (fstat(fd, &st) == -1) {
		state = sam_error_from_errno(errno);
		goto done;
	}
	if (!S_ISREG(st.st_mode))
		goto done;
	if (st.st_uid != geteuid() || (st.st_mode & 022) != 0) {
		state = SAM_DENIED;
		goto done;
	}
	while (used < sizeof(raw)) {
		count = read(fd, raw + used, sizeof(raw) - used);
		if (count == -1 && errno == EINTR)
			continue;
		if (count == -1) {
			state = sam_error_from_errno(errno);
			goto done;
		}
		if (count == 0)
			break;
		used += (size_t)count;
	}
	if (used > 0 && raw[used - 1] == '\n')
		used--;
	if (used == 2 && memcmp(raw, "hp", 2) == 0)
		state = set_value(value, size, "hp");
	else if (used == 3 && memcmp(raw, "vue", 3) == 0)
		state = set_value(value, size, "vue");
done:
	(void)close(fd);
	return state;
}

static enum sam_state
sound_pref(int dirfd, char *value, size_t size)
{
	struct d32_prefs *document = NULL;
	enum d32_error error;
	enum sam_state state;
	const char *profile = "workstation";

	if (dirfd >= 0) {
		error = d32_prefs_load(dirfd, "d32sound", 1, NULL, NULL, &document);
		if (error == D32E_OK)
			profile = d32_prefs_get(document, "profile", profile);
		else if (error != D32E_NEWER) {
			d32_prefs_free(document);
			return prefs_error(error);
		}
	}
	if (strcmp(profile, "silent") != 0 && strcmp(profile, "classic") != 0 &&
	    strcmp(profile, "workstation") != 0 && strcmp(profile, "full") != 0)
		profile = "workstation";
	state = set_value(value, size, profile);
	d32_prefs_free(document);
	return state;
}

static enum sam_state
audio_pref(int dirfd, char *value, size_t size)
{
	struct d32_prefs *document = NULL;
	struct stat st;
	enum d32_error error;

	if (dirfd < 0)
		return set_value(value, size, "Default window geometry");
	if (fstatat(dirfd, "d32mix.conf", &st, AT_SYMLINK_NOFOLLOW) == -1) {
		if (errno == ENOENT)
			return set_value(value, size, "Default window geometry");
		return sam_error_from_errno(errno);
	}
	error = d32_prefs_load(dirfd, "d32mix", 1, NULL, NULL, &document);
	d32_prefs_free(document);
	if (error == D32E_NEWER)
		return set_value(value, size, "Default window geometry");
	if (error != D32E_OK)
		return prefs_error(error);
	return set_value(value, size, "Saved window geometry only");
}

enum sam_state
sam_desktop_pref(const struct sam_prefs *prefs, const char *id, char *value,
    size_t size, const char **source)
{
	if (source != NULL)
		*source = NULL;
	if (prefs == NULL || id == NULL || value == NULL || size == 0 || source == NULL)
		return SAM_INVALID;
	value[0] = '\0';
	if (strcmp(id, "look") == 0) {
		*source = "~/.dt/d32/look";
		return look_pref(prefs->dirfd, value, size);
	}
	if (strcmp(id, "sound") == 0) {
		*source = "~/.dt/d32/d32sound.conf";
		return sound_pref(prefs->dirfd, value, size);
	}
	if (strcmp(id, "audio") == 0) {
		*source = "~/.dt/d32/d32mix.conf";
		return audio_pref(prefs->dirfd, value, size);
	}
	return SAM_INVALID;
}
