#define _POSIX_C_SOURCE 200809L

#include "oui.h"
#include "oui_internal.h"

#include <sys/stat.h>

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <locale.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <wctype.h>

#define GEN_MAX 64
#define MANIFEST_MAX (64U * 1024U)
#define MANIFEST_KEYS_MAX 256
#define DATA_ROOT "/usr/local/d32/share/data/oui"
#define LOCAL_REGISTRY "/.dt/d32/oui"

static const unsigned widths[] = {24, 28, 36};

struct oui_db {
	unsigned char *image;
	const unsigned char *rows[3], *dates;
	const char *names;
	size_t counts[3], names_size;
	locale_t locale;
	char gen[GEN_MAX + 1], date[11];
	bool fallback, stale;
};

struct manifest {
	char date[11], digest[65];
	size_t records;
	unsigned schema;
};

static bool
ascii_alnum(unsigned char c)
{
	return (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') ||
	    (c >= 'a' && c <= 'z');
}

static int
root_dir(const char *path)
{
	const int flags = O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC;
	char name[256];
	size_t n;
	int fd, next;

	fd = open(*path == '/' ? "/" : ".", flags);
	if (fd == -1)
		return -1;
	while (*path != '\0') {
		path += strspn(path, "/");
		n = strcspn(path, "/");
		if (n == 0)
			break;
		if (n >= sizeof(name) || (n == 2 && memcmp(path, "..", n) == 0)) {
			close(fd);
			return -1;
		}
		memcpy(name, path, n);
		name[n] = '\0';
		next = openat(fd, name, flags);
		close(fd);
		if (next == -1)
			return -1;
		fd = next;
		path += n;
	}
	return fd;
}

static ssize_t
read_retry(int fd, void *buf, size_t n)
{
	ssize_t got;

	do {
		got = read(fd, buf, n);
	} while (got == -1 && errno == EINTR);
	return got;
}

static unsigned char *
read_file(int dir, const char *name, size_t limit, size_t *len)
{
	unsigned char *buf = NULL, extra;
	struct stat st;
	ssize_t got;
	size_t size, used = 0;
	int fd;

	fd = openat(dir, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
	if (fd == -1)
		return NULL;
	if (fstat(fd, &st) == -1 || !S_ISREG(st.st_mode) || st.st_size <= 0 ||
	    (uintmax_t)st.st_size > limit)
		goto fail;
	size = (size_t)st.st_size;
	buf = malloc(size + 1);
	if (buf == NULL)
		goto fail;
	while (used < size) {
		got = read_retry(fd, buf + used, size - used);
		if (got <= 0)
			goto fail;
		used += (size_t)got;
	}
	if (read_retry(fd, &extra, 1) != 0)
		goto fail;
	close(fd);
	buf[size] = '\0';
	*len = size;
	return buf;

fail:
	close(fd);
	free(buf);
	return NULL;
}

static bool
read_pointer(int root, const char *name, char gen[GEN_MAX + 1])
{
	unsigned char *buf;
	size_t len;
	bool valid = false;

	buf = read_file(root, name, GEN_MAX + 1, &len);
	if (buf == NULL)
		return false;
	if (len < 2 || buf[len - 1] != '\n' || memchr(buf, '\0', len) != NULL)
		goto done;
	buf[--len] = '\0';
	if (!oui_gen_valid((const char *)buf))
		goto done;
	memcpy(gen, buf, len + 1);
	valid = true;

done:
	free(buf);
	return valid;
}

static bool
manifest_key(const char *key)
{
	const unsigned char *p = (const unsigned char *)key;

	if (!ascii_alnum(*p) && *p != '_')
		return false;
	for (; *p != '\0'; p++) {
		if (!ascii_alnum(*p) && *p != '_' && *p != '-' && *p != '.')
			return false;
	}
	return true;
}

static bool
lower_digest(const char *value)
{
	size_t i;

	if (strlen(value) != 64)
		return false;
	for (i = 0; i < 64; i++)
		if ((value[i] < '0' || value[i] > '9') &&
		    (value[i] < 'a' || value[i] > 'f'))
			return false;
	return true;
}

static bool
record_count(const char *text, size_t *count)
{
	const size_t max = OUI_MAX_IMAGE / OUI_RECORD_SIZE;
	size_t value = 0, digit;

	for (; *text != '\0'; text++) {
		if (*text < '0' || *text > '9')
			return false;
		digit = (size_t)(*text - '0');
		if (value > (max - digit) / 10)
			return false;
		value = value * 10 + digit;
	}
	*count = value;
	return value != 0;
}

static bool
read_manifest(int dir, const char *gen, struct manifest *manifest)
{
	static const char *const required[] = {
		"dataset", "gen", "source_url", "source_date", "source_sha256",
		"license", "converter_rev", "schema", "records", "output_sha256"
	};
	enum { NREQUIRED = sizeof(required) / sizeof(required[0]) };
	const char *values[NREQUIRED] = {NULL}, *keys[MANIFEST_KEYS_MAX];
	char *buf, *line, *end, *equals, *value;
	size_t len, nkeys = 0, i;
	bool valid = false;

	buf = (char *)read_file(dir, "MANIFEST", MANIFEST_MAX, &len);
	if (buf == NULL)
		return false;
	if (buf[len - 1] != '\n' || memchr(buf, '\0', len) != NULL)
		goto done;
	for (line = buf; *line != '\0'; line = end + 1) {
		end = strchr(line, '\n');
		*end = '\0';
		if (end != line && end[-1] == '\r')
			end[-1] = '\0';
		equals = strchr(line, '=');
		if (equals == NULL || nkeys == MANIFEST_KEYS_MAX)
			goto done;
		*equals = '\0';
		value = equals + 1;
		if (!manifest_key(line) || *value == '\0' ||
		    !oui_utf8_valid((const unsigned char *)value, strlen(value)))
			goto done;
		for (i = 0; i < nkeys; i++)
			if (strcmp(keys[i], line) == 0)
				goto done;
		keys[nkeys++] = line;
		for (i = 0; i < NREQUIRED; i++)
			if (strcmp(required[i], line) == 0)
				values[i] = value;
	}
	for (i = 0; i < NREQUIRED; i++)
		if (values[i] == NULL)
			goto done;
	if (strcmp(values[0], "oui") != 0 || strcmp(values[1], gen) != 0 ||
	    !oui_date_valid(values[3]) || !lower_digest(values[4]) ||
	    (strcmp(values[7], "1") != 0 && strcmp(values[7], "2") != 0) ||
	    !record_count(values[8], &manifest->records) ||
	    !lower_digest(values[9]))
		goto done;
	manifest->schema = (unsigned)(values[7][0] - '0');
	memcpy(manifest->date, values[3], sizeof(manifest->date));
	memcpy(manifest->digest, values[9], sizeof(manifest->digest));
	valid = true;

done:
	free(buf);
	return valid;
}

static long
day_number(int year, int month, int day)
{
	static const int prior[] = {
		0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334
	};
	long years = year - 1;
	bool leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);

	return years * 365 + years / 4 - years / 100 + years / 400 +
	    prior[month - 1] + day + (leap && month > 2);
}

static bool
old_date(const char *date)
{
	struct tm today;
	time_t now = time(NULL);
	int year, month, day;

	if (now == (time_t)-1 || gmtime_r(&now, &today) == NULL)
		return true;
	year = (date[0] - '0') * 1000 + (date[1] - '0') * 100 +
	    (date[2] - '0') * 10 + date[3] - '0';
	month = (date[5] - '0') * 10 + date[6] - '0';
	day = (date[8] - '0') * 10 + date[9] - '0';
	return day_number(today.tm_year + 1900, today.tm_mon + 1, today.tm_mday) -
	    day_number(year, month, day) > 365;
}

static bool
valid_names(const unsigned char *name, size_t len)
{
	const unsigned char *end;
	size_t used;

	while (len != 0) {
		end = memchr(name, '\0', len);
		if (end == NULL || end == name || (size_t)(end - name) > OUI_MAX_ASSIGNEE ||
		    !oui_utf8_valid(name, (size_t)(end - name)))
			return false;
		used = (size_t)(end - name) + 1;
		name += used;
		len -= used;
	}
	return true;
}

static bool
valid_rows(const struct oui_db *db, size_t section, size_t date_count, unsigned schema)
{
	const unsigned char *row = db->rows[section];
	uint64_t key, previous = 0;
	size_t i, offset, group = 0;
	unsigned type;

	for (i = 0; i < db->counts[section]; i++, row += OUI_RECORD_SIZE) {
		key = oui_get64(row);
		offset = oui_get32(row + 8);
		type = row[12];
		if (key >> widths[section] || (i != 0 && key < previous) ||
		    offset >= db->names_size || db->names[offset] == '\0' ||
		    (offset != 0 && db->names[offset - 1] != '\0') ||
		    oui_get16(row + 13) >= date_count)
			return false;
		if ((section == 0 && type != OUI_MAL) ||
		    (section == 1 && type != OUI_MAM) ||
		    (section == 2 && type != OUI_MAS && type != OUI_IAB))
			return false;
		if (i != 0 && key == previous) {
			const unsigned char *prior = row - OUI_RECORD_SIZE;

			if (schema != 2 || section != 0 || ++group > OUI_ASSIGNEE_MAX ||
			    oui_get16(row + 13) != oui_get16(prior + 13) ||
			    strcmp(db->names + oui_get32(prior + 8), db->names + offset) >= 0)
				return false;
		} else {
			group = 1;
		}
		previous = key;
	}
	return true;
}

static bool
valid_image(struct oui_db *db, size_t len, const struct manifest *manifest)
{
	const unsigned char *image = db->image;
	size_t pos = OUI_HEADER_SIZE, total = 0, count, date_count, i;
	char date[11];

	if (len < pos || memcmp(image, manifest->schema == 1 ? OUI_MAGIC_1 : OUI_MAGIC_2, 8) != 0)
		return false;
	for (i = 0; i < 3; i++) {
		count = oui_get32(image + 8 + i * 4);
		if (count > (len - pos) / OUI_RECORD_SIZE)
			return false;
		db->rows[i] = image + pos;
		db->counts[i] = count;
		pos += count * OUI_RECORD_SIZE;
		total += count;
	}
	date_count = oui_get32(image + 20);
	if (total != manifest->records || date_count == 0 ||
	    date_count > UINT16_MAX + 1U || date_count > (len - pos) / OUI_DATE_SIZE)
		return false;
	db->dates = image + pos;
	pos += date_count * OUI_DATE_SIZE;
	db->names_size = oui_get32(image + 24);
	if (db->names_size == 0 || db->names_size != len - pos ||
	    !valid_names(image + pos, db->names_size))
		return false;
	db->names = (const char *)image + pos;
	date[10] = '\0';
	for (i = 0; i < date_count; i++) {
		memcpy(date, db->dates + i * OUI_DATE_SIZE, OUI_DATE_SIZE);
		if (!oui_date_valid(date))
			return false;
	}
	for (i = 0; i < 3; i++)
		if (!valid_rows(db, i, date_count, manifest->schema))
			return false;
	return true;
}

static struct oui_db *
load_generation(int root, const char *pointer, bool fallback)
{
	struct oui_db *db = NULL;
	struct manifest manifest;
	char gen[GEN_MAX + 1], digest[65];
	size_t len;
	int dir;

	if (!read_pointer(root, pointer, gen))
		return NULL;
	dir = openat(root, gen, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
	if (dir == -1)
		return NULL;
	if (!read_manifest(dir, gen, &manifest))
		goto done;
	db = calloc(1, sizeof(*db));
	if (db == NULL)
		goto done;
	db->image = read_file(dir, "oui.d32i", OUI_MAX_IMAGE, &len);
	if (db->image == NULL)
		goto invalid;
	oui_sha256(db->image, len, digest);
	if (strcmp(manifest.digest, digest) != 0 ||
	    !valid_image(db, len, &manifest))
		goto invalid;
	memcpy(db->gen, gen, strlen(gen) + 1);
	memcpy(db->date, manifest.date, sizeof(db->date));
	db->fallback = fallback;
	db->stale = old_date(db->date);
	goto done;

invalid:
	oui_db_close(db);
	db = NULL;
done:
	close(dir);
	return db;
}

static int
open_registry_fd(struct oui_db **out, int fd, bool local)
{
	struct stat st;

	*out = NULL;
	if (fd == -1)
		return OUI_UNAVAILABLE;
	if (local && (fstat(fd, &st) == -1 || st.st_uid != getuid() ||
	    (st.st_mode & 077) != 0)) {
		close(fd);
		return OUI_UNAVAILABLE;
	}
	*out = load_generation(fd, "CURRENT", false);
	if (*out == NULL)
		*out = load_generation(fd, "PREVIOUS", true);
	close(fd);
	if (*out == NULL)
		return OUI_UNAVAILABLE;
	(*out)->locale = newlocale(LC_CTYPE_MASK, "en_US.UTF-8", (locale_t)0);
	if ((*out)->locale == (locale_t)0)
		(*out)->locale = newlocale(LC_CTYPE_MASK, "C.UTF-8", (locale_t)0);
	return OUI_OK;
}

static int
open_registry(struct oui_db **out, const char *root, bool local)
{
	if (out == NULL)
		return OUI_INVALID;
	*out = NULL;
	if (root == NULL || *root == '\0')
		return OUI_INVALID;
	return open_registry_fd(out, root_dir(root), local);
}

int
oui_db_open(struct oui_db **out, const char *root)
{
	return open_registry(out, root, false);
}

int
oui_db_open_local_at(struct oui_db **out, int parent)
{
	if (out == NULL)
		return OUI_INVALID;
	return open_registry_fd(out, openat(parent, "oui",
	    O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC), true);
}

int
oui_db_open_default(struct oui_db **out)
{
	const char *root = getenv("D32_OUI_ROOT"), *home;
	char local[PATH_MAX];
	int result, length;

	if (root != NULL)
		return oui_db_open(out, root);
	result = oui_db_open(out, DATA_ROOT);
	if (result != OUI_UNAVAILABLE)
		return result;
	home = getenv("HOME");
	if (home == NULL || home[0] != '/')
		return result;
	length = snprintf(local, sizeof(local), "%s%s", home, LOCAL_REGISTRY);
	if (length < 0 || (size_t)length >= sizeof(local))
		return result;
	return open_registry(out, local, true);
}

void
oui_db_close(struct oui_db *db)
{
	if (db != NULL) {
		if (db->locale != (locale_t)0)
			freelocale(db->locale);
		free(db->image);
		free(db);
	}
}

const char *
oui_db_generation(const struct oui_db *db)
{
	return db == NULL ? NULL : db->gen;
}

const char *
oui_db_date(const struct oui_db *db)
{
	return db == NULL ? NULL : db->date;
}

bool
oui_db_fallback(const struct oui_db *db)
{
	return db != NULL && db->fallback;
}

bool
oui_db_stale(const struct oui_db *db)
{
	return db != NULL && db->stale;
}

static unsigned char
ascii_fold(unsigned char c)
{
	return c >= 'A' && c <= 'Z' ? c + ('a' - 'A') : c;
}

static int
hex_digit(unsigned char c)
{
	c = ascii_fold(c);
	if (c >= '0' && c <= '9')
		return c - '0';
	if (c >= 'a' && c <= 'f')
		return c - 'a' + 10;
	return -1;
}

int
oui_parse_mac(const char *text, struct oui_mac *mac)
{
	static const char *const quadrants[] = {"AAI", "reserved", "ELI", "SAI"};
	size_t n, i, stride = 0;
	unsigned char separator = '\0';
	unsigned first;
	uint64_t value = 0;
	int digit;

	if (text == NULL || mac == NULL)
		return OUI_INVALID;
	n = strnlen(text, 18);
	if (n == 17 && (text[2] == ':' || text[2] == '-')) {
		stride = 3;
		separator = (unsigned char)text[2];
	} else if (n == 14) {
		stride = 5;
		separator = '.';
	} else if (n != 12)
		return OUI_INVALID;
	for (i = 0; i < n; i++) {
		if (stride != 0 && (i + 1) % stride == 0) {
			if ((unsigned char)text[i] != separator)
				return OUI_INVALID;
			continue;
		}
		digit = hex_digit((unsigned char)text[i]);
		if (digit < 0)
			return OUI_INVALID;
		value = (value << 4) | (unsigned)digit;
	}
	first = (unsigned)(value >> 40);
	mac->value = value;
	mac->broadcast = value == UINT64_C(0xffffffffffff);
	mac->group = (first & 1) != 0;
	mac->local = (first & 2) != 0;
	mac->slap = mac->local && !mac->group ? quadrants[(first >> 2) & 3] : NULL;
	return OUI_OK;
}

int
oui_parse_prefix(const char *text, uint64_t *key, unsigned *width)
{
	size_t n, i;
	uint64_t value = 0;
	int digit;

	if (text == NULL || key == NULL || width == NULL)
		return OUI_INVALID;
	n = strnlen(text, 10);
	if (n != 6 && n != 7 && n != 9)
		return OUI_INVALID;
	for (i = 0; i < n; i++) {
		digit = hex_digit((unsigned char)text[i]);
		if (digit < 0)
			return OUI_INVALID;
		value = (value << 4) | (unsigned)digit;
	}
	*key = value;
	*width = (unsigned)n * 4;
	return OUI_OK;
}

static size_t
group_count(const struct oui_db *db, size_t section, size_t first)
{
	const unsigned char *row = db->rows[section] + first * OUI_RECORD_SIZE;
	uint64_t key = oui_get64(row);
	size_t count = 1;

	while (first + count < db->counts[section] &&
	    oui_get64(row + count * OUI_RECORD_SIZE) == key)
		count++;
	return count;
}

static void
decode_group(const struct oui_db *db, size_t section, size_t first, size_t count,
    struct oui_row *out)
{
	const unsigned char *row = db->rows[section] + first * OUI_RECORD_SIZE;
	size_t i;

	*out = (struct oui_row){
		.key = oui_get64(row), .width = widths[section],
		.type = (enum oui_type)row[12], .assignee_count = count
	};
	memcpy(out->date, db->dates + (size_t)oui_get16(row + 13) * OUI_DATE_SIZE,
	    OUI_DATE_SIZE);
	for (i = 0; i < count; i++, row += OUI_RECORD_SIZE)
		out->assignees[i] = db->names + oui_get32(row + 8);
	if (count == 1)
		out->assignee = out->assignees[0];
}

int
oui_find_prefix(const struct oui_db *db, uint64_t key, unsigned width,
    struct oui_row *out)
{
	size_t section, lo = 0, hi, middle;
	const unsigned char *row;

	for (section = 0; section < 3 && widths[section] != width; section++)
		continue;
	if (out == NULL || section == 3 || key >> width)
		return OUI_INVALID;
	if (db == NULL)
		return OUI_UNAVAILABLE;
	hi = db->counts[section];
	while (lo < hi) {
		middle = lo + (hi - lo) / 2;
		row = db->rows[section] + middle * OUI_RECORD_SIZE;
		if (oui_get64(row) < key)
			lo = middle + 1;
		else
			hi = middle;
	}
	if (lo == db->counts[section] ||
	    oui_get64(db->rows[section] + lo * OUI_RECORD_SIZE) != key)
		return OUI_MISS;
	decode_group(db, section, lo, group_count(db, section, lo), out);
	return OUI_OK;
}

int
oui_lookup(const struct oui_db *db, const struct oui_mac *mac, struct oui_row *out)
{
	size_t section;
	unsigned width;
	int result;

	if (mac == NULL || out == NULL || mac->value >> 48)
		return OUI_INVALID;
	if (db == NULL)
		return OUI_UNAVAILABLE;
	if ((mac->value >> 40) & 3)
		return OUI_MISS;
	for (section = 3; section > 0; section--) {
		width = widths[section - 1];
		result = oui_find_prefix(db, mac->value >> (48 - width), width, out);
		if (result != OUI_MISS)
			return result;
	}
	return OUI_MISS;
}

/* Both strings have already passed the UTF-8 validator. */
static wint_t
fold_next(const unsigned char **cursor, locale_t locale)
{
	const unsigned char *p = *cursor;
	wint_t value = *p++;
	unsigned n;

	if (value < 0x80) {
		*cursor = p;
		return ascii_fold((unsigned char)value);
	}
	if (value < 0xe0) {
		value &= 0x1f;
		n = 1;
	} else if (value < 0xf0) {
		value &= 0x0f;
		n = 2;
	} else {
		value &= 0x07;
		n = 3;
	}
	while (n-- != 0)
		value = (value << 6) | (*p++ & 0x3f);
	*cursor = p;
	return locale == (locale_t)0 ? value : towlower_l(value, locale);
}

static bool
contains_folded(const char *text, const char *needle, locale_t locale)
{
	const unsigned char *start = (const unsigned char *)text, *p, *query;
	const unsigned char *rest = (const unsigned char *)needle;
	wint_t first = fold_next(&rest, locale);

	while (*start != '\0') {
		if (fold_next(&start, locale) != first)
			continue;
		p = start;
		query = rest;
		for (;;) {
			if (*query == '\0')
				return true;
			if (*p == '\0' || fold_next(&p, locale) != fold_next(&query, locale))
				break;
		}
	}
	return false;
}

size_t
oui_search_org(const struct oui_db *db, const char *needle, struct oui_row *out,
    size_t cap)
{
	const unsigned char *row;
	size_t section, first, count, i, matches = 0;

	if (db == NULL || needle == NULL || *needle == '\0' || out == NULL || cap == 0)
		return 0;
	if (!oui_utf8_valid((const unsigned char *)needle, strlen(needle)))
		return 0;
	if (cap > OUI_SEARCH_MAX)
		cap = OUI_SEARCH_MAX;
	for (section = 0; section < 3; section++) {
		for (first = 0; first < db->counts[section]; first += count) {
			count = group_count(db, section, first);
			row = db->rows[section] + first * OUI_RECORD_SIZE;
			for (i = 0; i < count; i++, row += OUI_RECORD_SIZE) {
				if (contains_folded(db->names + oui_get32(row + 8), needle, db->locale))
					break;
			}
			if (i != count) {
				decode_group(db, section, first, count, &out[matches++]);
				if (matches == cap)
					return matches;
			}
		}
	}
	return matches;
}

const char *
oui_type_name(enum oui_type type)
{
	switch (type) {
	case OUI_MAL:
		return "MA-L";
	case OUI_MAM:
		return "MA-M";
	case OUI_MAS:
		return "MA-S";
	case OUI_IAB:
		return "IAB";
	}
	return NULL;
}
