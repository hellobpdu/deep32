#define _DEFAULT_SOURCE

#include <sys/stat.h>
#include <sys/types.h>

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "d32_io.h"
#include "d32_prefs.h"

enum { STEM_MAX = 48, NAME_MAXIMUM = STEM_MAX + 32 };

struct document {
	unsigned int version;
	size_t len, body;
	char raw[D32_PREFS_FILE_MAX + 1];
	char values[D32_PREFS_FILE_MAX + 1];
};

struct change {
	struct change *next;
	size_t last, cost;
	char key[D32_PREFS_KEY_MAX + 1];
	char *value; /* NULL removes all occurrences. */
};

struct d32_prefs {
	int dirfd;
	unsigned int current;
	bool migration;
	char name[NAME_MAXIMUM], lockname[NAME_MAXIMUM];
	char header[NAME_MAXIMUM];
	struct document doc;
	struct change *changes;
	size_t change_cost, reported_len;
	char reported[D32_PREFS_FILE_MAX + 1];
	d32_prefs_log_fn log;
	void *log_ctx;
};

enum line_kind { IGNORED, SETTING, MALFORMED };

struct line {
	enum line_kind kind;
	size_t start, end, keylen, value, comment;
};

static bool
letter(char c)
{
	return c >= 'a' && c <= 'z';
}

static bool
digit(char c)
{
	return c >= '0' && c <= '9';
}

static bool
valid_key(const char *key, size_t len)
{
	size_t i;

	if (!len || len > D32_PREFS_KEY_MAX || !letter(key[0]))
		return false;
	for (i = 1; i < len; i++)
		if (!letter(key[i]) && !digit(key[i]) && key[i] != '.' &&
		    key[i] != '_')
			return false;
	return true;
}

static bool
valid_value(const char *value, size_t len)
{
	size_t i;

	if (len > D32_PREFS_VALUE_MAX)
		return false;
	for (i = 0; i < len; i++)
		if (value[i] < ' ' || value[i] > '~' || value[i] == '#')
			return false;
	return true;
}

static bool
valid_stem(const char *stem)
{
	size_t i, len;

	if (!stem || !(len = strlen(stem)) || len > STEM_MAX || !letter(stem[0]))
		return false;
	for (i = 1; i < len; i++)
		if (!letter(stem[i]) && !digit(stem[i]) && stem[i] != '-')
			return false;
	return true;
}

static enum d32_error
file_error(int error)
{
	switch (error) {
	case ENOENT: return D32E_NOENT;
	case ELOOP: return D32E_LINK;
	case EACCES: case EPERM: return D32E_PERM;
	default: return D32E_IO;
	}
}

static enum d32_error
check_regular(const struct stat *st)
{
	if (!S_ISREG(st->st_mode))
		return D32E_NOTREG;
	if (st->st_nlink != 1)
		return D32E_LINK;
	if (st->st_uid != geteuid())
		return D32E_PERM;
	return D32E_OK;
}

static enum d32_error
read_raw(int dirfd, const char *name, struct document *doc)
{
	int fd;
	ssize_t n;
	struct stat st;
	enum d32_error err;

	/* Reject special objects before opening, then check the opened inode too. */
	if (fstatat(dirfd, name, &st, AT_SYMLINK_NOFOLLOW) == -1)
		return file_error(errno);
	if (S_ISLNK(st.st_mode))
		return D32E_LINK;
	if ((err = check_regular(&st)) != D32E_OK)
		return err;
	fd = openat(dirfd, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
	if (fd == -1)
		return file_error(errno);
	if (fstat(fd, &st) == -1) {
		err = file_error(errno);
		goto out;
	}
	if ((err = check_regular(&st)) != D32E_OK)
		goto out;
	if (st.st_size < 0 || st.st_size > D32_PREFS_FILE_MAX) {
		err = D32E_TOOBIG;
		goto out;
	}
	doc->len = 0;
	while (doc->len < sizeof(doc->raw)) {
		n = read(fd, doc->raw + doc->len, sizeof(doc->raw) - doc->len);
		if (n == -1 && errno == EINTR)
			continue;
		if (n == -1) {
			err = file_error(errno);
			goto out;
		}
		if (!n)
			break;
		doc->len += (size_t)n;
	}
	if (doc->len > D32_PREFS_FILE_MAX)
		err = D32E_TOOBIG;
	else if (doc->len != (size_t)st.st_size)
		err = D32E_IO;
	else if (!doc->len)
		err = D32E_EMPTY;
out:
	(void)close(fd);
	return err;
}

static enum d32_error
parse_header(const struct d32_prefs *prefs, struct document *doc)
{
	const char *end;
	size_t start, len, i;
	unsigned int version = 0, d;

	end = memchr(doc->raw, '\n', doc->len);
	len = end ? (size_t)(end - doc->raw) : doc->len;
	start = strlen(prefs->header);
	if (len <= start || memcmp(doc->raw, prefs->header, start) ||
	    doc->raw[start] == '0')
		return D32E_FORMAT;
	for (i = start; i < len; i++) {
		if (!digit(doc->raw[i]))
			return D32E_FORMAT;
		d = (unsigned int)(doc->raw[i] - '0');
		if (version > (UINT_MAX - d) / 10)
			return D32E_FORMAT;
		version = version * 10 + d;
	}
	doc->version = version;
	doc->body = len + (end != NULL);
	return D32E_OK;
}

static struct line
next_line(const struct document *doc, size_t start)
{
	const char *data = doc->raw;
	const char *newline = memchr(data + start, '\n', doc->len - start);
	size_t len = newline ? (size_t)(newline - data) : doc->len;
	struct line line = { MALFORMED, start, len + (newline != NULL), 0, 0, len };
	const char *hash = memchr(data + start, '#', len - start);
	const char *eq;
	size_t i, keylen;
	bool plain = true, whitespace = true;

	if (hash)
		line.comment = (size_t)(hash - data);
	for (i = start; i < len; i++) {
		if ((data[i] < ' ' || data[i] > '~') && data[i] != '\t')
			plain = false;
		if (i < line.comment && data[i] != ' ' && data[i] != '\t')
			whitespace = false;
	}
	if (whitespace && plain) {
		line.kind = IGNORED;
		return line;
	}
	eq = memchr(data + start, '=', line.comment - start);
	keylen = (eq ? (size_t)(eq - data) : line.comment) - start;
	if (valid_key(data + start, keylen)) {
		line.keylen = keylen;
		if (eq && plain && valid_value(eq + 1,
		    line.comment - (size_t)(eq + 1 - data))) {
			line.value = (size_t)(eq + 1 - data);
			line.kind = SETTING;
		}
	}
	return line;
}

static void
report(struct d32_prefs *prefs, const char *key, size_t len)
{
	size_t i, size;
	char *name;

	if (!prefs->log)
		return;
	for (i = 0; i < prefs->reported_len; i += size + 1) {
		size = strlen(prefs->reported + i);
		if (size == len && !memcmp(prefs->reported + i, key, len))
			return;
	}
	if (len + 1 > sizeof(prefs->reported) - prefs->reported_len)
		return;
	name = prefs->reported + prefs->reported_len;
	memcpy(name, key, len);
	name[len] = '\0';
	prefs->reported_len += len + 1;
	prefs->log(name, prefs->log_ctx);
}

static void
index_values(struct d32_prefs *prefs, struct document *doc)
{
	size_t i;
	struct line line;

	memcpy(doc->values, doc->raw, doc->len);
	for (i = doc->body; i < doc->len; i = line.end) {
		line = next_line(doc, i);
		if (line.kind == MALFORMED)
			report(prefs, doc->raw + i, line.keylen);
		else if (line.kind == SETTING)
			doc->values[line.comment] = '\0';
	}
}

static enum d32_error
read_doc(struct d32_prefs *prefs, struct document *doc)
{
	enum d32_error err = read_raw(prefs->dirfd, prefs->name, doc);

	if (err == D32E_NOENT) {
		doc->version = prefs->current;
		doc->len = doc->body = 0;
		return D32E_OK;
	}
	if (err != D32E_OK || (err = parse_header(prefs, doc)) != D32E_OK)
		return err;
	if (doc->version > prefs->current)
		return D32E_NEWER;
	index_values(prefs, doc);
	return doc->version < prefs->current ? D32E_VERSION : D32E_OK;
}

enum d32_error
d32_prefs_load(int dirfd, const char *stem, unsigned int version,
    d32_prefs_log_fn log, void *log_ctx, struct d32_prefs **out)
{
	const char *tag;
	struct d32_prefs *prefs;
	struct stat st;
	enum d32_error err;

	if (!out)
		return D32E_INVAL;
	*out = NULL;
	if (!valid_stem(stem) || !version)
		return D32E_INVAL;
	tag = strncmp(stem, "d32", 3) == 0 ? stem + 3 : stem;
	if (*tag == '-')
		tag++;
	if (!valid_stem(tag))
		return D32E_INVAL;
	if (fstat(dirfd, &st) == -1)
		return file_error(errno);
	if (!S_ISDIR(st.st_mode))
		return D32E_NOTREG;
	if (st.st_uid != geteuid() || (st.st_mode & 07777) != 0700)
		return D32E_PERM;
	if (!(prefs = calloc(1, sizeof(*prefs))))
		return D32E_NOMEM;
	prefs->dirfd = fcntl(dirfd, F_DUPFD_CLOEXEC, 0);
	if (prefs->dirfd == -1) {
		err = file_error(errno);
		free(prefs);
		return err;
	}
	(void)snprintf(prefs->name, sizeof(prefs->name), "%s.conf", stem);
	(void)snprintf(prefs->lockname, sizeof(prefs->lockname), ".%s.lock", stem);
	(void)snprintf(prefs->header, sizeof(prefs->header), "# d32-%s v", tag);
	prefs->current = version;
	prefs->log = log;
	prefs->log_ctx = log_ctx;
	err = read_doc(prefs, &prefs->doc);
	if (err != D32E_OK && err != D32E_VERSION && err != D32E_NEWER) {
		d32_prefs_free(prefs);
		return err;
	}
	*out = prefs;
	return err;
}

static void
clear_changes(struct d32_prefs *prefs)
{
	struct change *change;

	while ((change = prefs->changes)) {
		prefs->changes = change->next;
		free(change->value);
		free(change);
	}
	prefs->change_cost = 0;
}

void
d32_prefs_free(struct d32_prefs *prefs)
{
	if (prefs) {
		clear_changes(prefs);
		(void)close(prefs->dirfd);
		free(prefs);
	}
}

unsigned int
d32_prefs_version(const struct d32_prefs *prefs)
{
	return prefs ? prefs->doc.version : 0;
}

static struct change *
find_change(const struct d32_prefs *prefs, const char *key, size_t len)
{
	struct change *change;

	for (change = prefs->changes; change; change = change->next)
		if (strlen(change->key) == len && !memcmp(change->key, key, len))
			return change;
	return NULL;
}

const char *
d32_prefs_get(const struct d32_prefs *prefs, const char *key, const char *fallback)
{
	size_t i, len;
	const char *value = fallback;
	struct change *change;
	struct line line;

	if (!prefs || prefs->doc.version > prefs->current || !key ||
	    !valid_key(key, len = strlen(key)))
		return fallback;
	if ((change = find_change(prefs, key, len)))
		return change->value ? change->value : fallback;
	for (i = prefs->doc.body; i < prefs->doc.len; i = line.end) {
		line = next_line(&prefs->doc, i);
		if (line.kind == SETTING && line.keylen == len &&
		    !memcmp(prefs->doc.raw + i, key, len))
			value = prefs->doc.values + line.value;
	}
	return value;
}

static enum d32_error
set_change(struct d32_prefs *prefs, const char *key, const char *value)
{
	size_t len, cost, value_len = value ? strlen(value) : 0;
	char *copy = NULL;
	struct change *change, **link;

	if (!prefs || !key || !valid_key(key, len = strlen(key)) ||
	    (value && !valid_value(value, value_len)))
		return D32E_INVAL;
	if (prefs->doc.version > prefs->current)
		return D32E_NEWER;
	if (prefs->doc.version < prefs->current && !prefs->migration)
		return D32E_VERSION;
	for (link = &prefs->changes; *link; link = &(*link)->next)
		if (!strcmp((*link)->key, key))
			break;
	change = *link;
	cost = len + value_len + 2;
	if (cost > D32_PREFS_FILE_MAX - prefs->change_cost + (change ? change->cost : 0))
		return D32E_TOOBIG;
	if (value) {
		copy = malloc(value_len + 1);
		if (!copy)
			return D32E_NOMEM;
		memcpy(copy, value, value_len + 1);
	}
	if (!change) {
		change = calloc(1, sizeof(*change));
		if (!change) {
			free(copy);
			return D32E_NOMEM;
		}
		memcpy(change->key, key, len + 1);
		*link = change;
	}
	free(change->value);
	prefs->change_cost = prefs->change_cost - change->cost + cost;
	change->cost = cost;
	change->value = copy;
	return D32E_OK;
}

enum d32_error
d32_prefs_set(struct d32_prefs *prefs, const char *key, const char *value)
{
	return value ? set_change(prefs, key, value) : D32E_INVAL;
}

enum d32_error
d32_prefs_unset(struct d32_prefs *prefs, const char *key)
{
	return set_change(prefs, key, NULL);
}

/* lockf record locks are process-wide. Gate every lock-fd open and close
 * among threads too. A child can reclaim its private copy after fork. */
static pid_t lock_owner;

static void
take_process_gate(void)
{
	const struct timespec pause = { 0, 1000000 };
	pid_t pid = getpid(), owner;

	for (;;) {
		owner = __atomic_load_n(&lock_owner, __ATOMIC_RELAXED);
		if (owner != pid && __atomic_compare_exchange_n(&lock_owner, &owner,
		    pid, false, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED))
			return;
		(void)nanosleep(&pause, NULL);
	}
}

static void
drop_lock(int fd)
{
	if (fd != -1)
		(void)close(fd);
	__atomic_store_n(&lock_owner, 0, __ATOMIC_RELEASE);
}

static enum d32_error
take_lock(const struct d32_prefs *prefs, int *out)
{
	int fd, r;
	struct stat st, named;
	enum d32_error err;

	if (fstatat(prefs->dirfd, prefs->lockname, &named, AT_SYMLINK_NOFOLLOW) == 0) {
		if (S_ISLNK(named.st_mode))
			return D32E_LINK;
		if ((err = check_regular(&named)) != D32E_OK)
			return err;
	} else if (errno != ENOENT)
		return file_error(errno);
	take_process_gate();
	fd = openat(prefs->dirfd, prefs->lockname,
	    O_RDWR | O_CREAT | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0600);
	if (fd == -1) {
		err = file_error(errno);
		goto fail;
	}
	if (fstat(fd, &st) == -1) {
		err = file_error(errno);
		goto fail;
	}
	if ((err = check_regular(&st)) != D32E_OK)
		goto fail;
	if ((st.st_mode & 07777) != 0600) {
		err = D32E_PERM;
		goto fail;
	}
	do {
		r = lockf(fd, F_LOCK, 0);
	} while (r == -1 && errno == EINTR);
	if (r == -1) {
		err = file_error(errno);
		goto fail;
	}
	if (fstatat(prefs->dirfd, prefs->lockname, &named, AT_SYMLINK_NOFOLLOW) == -1) {
		err = file_error(errno);
		goto fail;
	}
	if (st.st_dev != named.st_dev || st.st_ino != named.st_ino) {
		err = D32E_LINK;
		goto fail;
	}
	if ((err = check_regular(&named)) != D32E_OK)
		goto fail;
	*out = fd;
	return D32E_OK;

fail:
	drop_lock(fd);
	return err;
}

static bool
append(struct document *out, const char *bytes, size_t len)
{
	if (len > D32_PREFS_FILE_MAX - out->len)
		return false;
	memcpy(out->raw + out->len, bytes, len);
	out->len += len;
	return true;
}

static bool
append_change(struct document *out, const struct change *change,
    const char *suffix, size_t suffix_len)
{
	if (out->len && out->raw[out->len - 1] != '\n' && !append(out, "\n", 1))
		return false;
	return append(out, change->key, strlen(change->key)) &&
	    append(out, "=", 1) && append(out, change->value, strlen(change->value)) &&
	    append(out, suffix, suffix_len);
}

static enum d32_error
serialize(struct d32_prefs *prefs, const struct document *source,
    struct document *out)
{
	struct change *change;
	struct line line;
	size_t i;
	int n;

	n = snprintf(out->raw, sizeof(out->raw), "%s%u\n", prefs->header, prefs->current);
	if (n < 0 || n > D32_PREFS_FILE_MAX)
		return D32E_FORMAT;
	out->len = out->body = (size_t)n;
	out->version = prefs->current;
	for (change = prefs->changes; change; change = change->next)
		change->last = (size_t)-1;
	for (i = source->body; i < source->len; i = line.end) {
		line = next_line(source, i);
		if (line.kind == SETTING &&
		    (change = find_change(prefs, source->raw + i, line.keylen)))
			change->last = i;
	}
	for (i = source->body; i < source->len; i = line.end) {
		line = next_line(source, i);
		if (line.kind == MALFORMED)
			continue;
		change = line.kind == SETTING ?
		    find_change(prefs, source->raw + i, line.keylen) : NULL;
		if (change && !change->value)
			continue;
		if (change && change->last == i) {
			if (!append_change(out, change, source->raw + line.comment,
			    line.end - line.comment))
				return D32E_TOOBIG;
		} else if (!append(out, source->raw + i, line.end - i))
			return D32E_TOOBIG;
	}
	for (change = prefs->changes; change; change = change->next)
		if (change->value && change->last == (size_t)-1 &&
		    !append_change(out, change, "\n", 1))
			return D32E_TOOBIG;
	index_values(prefs, out);
	return D32E_OK;
}

static enum d32_error
sync_directory(int dirfd)
{
	int result;

	do {
		result = fsync(dirfd);
	} while (result == -1 && errno == EINTR);
	return result == 0 ? D32E_OK : file_error(errno);
}

static enum d32_error
write_doc(const struct d32_prefs *prefs, const struct document *doc)
{
	enum d32_error err = d32_atomic_write(prefs->dirfd, prefs->name, doc->raw,
	    doc->len, 0600, false);

	return err == D32E_OK ? sync_directory(prefs->dirfd) : err;
}

enum d32_error
d32_prefs_save(struct d32_prefs *prefs)
{
	struct document fresh = { 0 }, result = { 0 };
	enum d32_error err;
	int fd;

	if (!prefs || prefs->migration)
		return D32E_INVAL;
	if (prefs->doc.version > prefs->current)
		return D32E_NEWER;
	if (prefs->doc.version < prefs->current)
		return D32E_VERSION;
	if (!prefs->changes)
		return D32E_OK;
	if ((err = take_lock(prefs, &fd)) != D32E_OK)
		return err;
	err = read_doc(prefs, &fresh);
	if (err == D32E_NEWER || err == D32E_VERSION)
		prefs->doc = fresh;
	if (err == D32E_OK && (err = serialize(prefs, &fresh, &result)) == D32E_OK)
		err = write_doc(prefs, &result);
	if (err == D32E_OK) {
		prefs->doc = result;
		clear_changes(prefs);
	}
	drop_lock(fd);
	return err;
}

static enum d32_error
backup(const struct d32_prefs *prefs, const struct document *original)
{
	struct document found;
	char name[NAME_MAXIMUM];
	int n;
	enum d32_error err;

	n = snprintf(name, sizeof(name), "%s.v%u.bak", prefs->name, original->version);
	if (n < 0 || (size_t)n >= sizeof(name))
		return D32E_FORMAT;
	err = d32_atomic_write(prefs->dirfd, name, original->raw, original->len, 0600, true);
	if (err != D32E_EXISTS)
		return err;
	/* Reuse only the exact preimage left by a prior interrupted migration. */
	err = read_raw(prefs->dirfd, name, &found);
	if (err != D32E_OK)
		return err;
	return found.len == original->len && !memcmp(found.raw, original->raw, found.len) ?
	    D32E_OK : D32E_EXISTS;
}

enum d32_error
d32_prefs_migrate(struct d32_prefs *prefs, d32_prefs_step_fn step, void *arg)
{
	struct document fresh = { 0 }, result = { 0 };
	struct d32_prefs draft;
	enum d32_error err;
	int fd;

	if (!prefs || !step || prefs->migration || prefs->changes)
		return D32E_INVAL;
	if (prefs->doc.version > prefs->current)
		return D32E_NEWER;
	if ((err = take_lock(prefs, &fd)) != D32E_OK)
		return err;
	err = read_doc(prefs, &fresh);
	if (err == D32E_OK || err == D32E_NEWER) {
		prefs->doc = fresh;
		if (err == D32E_OK && fresh.len)
			err = sync_directory(prefs->dirfd);
		goto out;
	}
	if (err != D32E_VERSION)
		goto out;

	draft = *prefs;
	draft.doc = fresh;
	draft.migration = true;
	while (draft.doc.version < draft.current) {
		err = step(&draft, draft.doc.version, arg);
		if (err != D32E_OK)
			break;
		draft.doc.version++;
	}
	if (err == D32E_OK)
		err = serialize(&draft, &fresh, &result);
	if (err == D32E_OK)
		err = backup(prefs, &fresh);
	if (err == D32E_OK)
		err = sync_directory(prefs->dirfd);
	if (err == D32E_OK)
		err = write_doc(prefs, &result);
	if (err == D32E_OK)
		prefs->doc = result;
	clear_changes(&draft);
out:
	drop_lock(fd);
	return err;
}
