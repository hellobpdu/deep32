#include "d32_regdata.h"

#include <errno.h>
#include <fcntl.h>
#include <sha2.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define HEADER_SIZE 24
#define ROW_SIZE 64
#define GEO_ROW_SIZE 36
#define MAX_POOL (8U * 1024 * 1024)
#define MAX_DATA (HEADER_SIZE + D32_REGDATA_MAX_ROWS * ROW_SIZE + MAX_POOL)
#define GEN_SIZE 26
#define DATE_SIZE 11
#define HEX_SIZE (SHA256_DIGEST_LENGTH * 2)

enum manifest_key {
	KEY_DATASET, KEY_GEN, KEY_URL, KEY_DATE, KEY_SOURCE_SHA,
	KEY_LICENSE, KEY_REV, KEY_SCHEMA, KEY_RECORDS, KEY_OUTPUT_SHA, KEY_COUNT
};

static const char *const manifest_keys[] = {
	"dataset", "gen", "source_url", "source_date", "source_sha256",
	"license", "converter_rev", "schema", "records", "output_sha256"
};

struct d32_regdata {
	uint8_t *bytes;
	const uint8_t *strings;
	uint32_t count;
	char gen[GEN_SIZE], date[DATE_SIZE];
	bool previous;
};

static uint32_t
u32(const uint8_t *b)
{
	return (uint32_t)b[0] << 24 | (uint32_t)b[1] << 16 |
	    (uint32_t)b[2] << 8 | b[3];
}

static int
read_at(int dir, const char *name, size_t cap, uint8_t **out, size_t *len)
{
	struct stat st;
	uint8_t *buf = NULL, extra;
	size_t size, used = 0;
	ssize_t n;
	int saved;
	int fd = openat(dir, name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);

	if (fd == -1)
		return -1;
	if (fstat(fd, &st) == -1)
		goto fail;
	if (!S_ISREG(st.st_mode) || st.st_size <= 0 || (uintmax_t)st.st_size > cap) {
		errno = EINVAL;
		goto fail;
	}
	size = (size_t)st.st_size;
	buf = malloc(size + 1);
	if (buf == NULL)
		goto fail;
	while (used < size) {
		n = read(fd, buf + used, size - used);
		if (n == -1 && errno == EINTR)
			continue;
		if (n <= 0) {
			if (n == 0)
				errno = EINVAL;
			goto fail;
		}
		used += (size_t)n;
	}
	do {
		n = read(fd, &extra, 1);
	} while (n == -1 && errno == EINTR);
	if (n != 0) {
		if (n > 0)
			errno = EINVAL;
		goto fail;
	}
	buf[size] = '\0';
	close(fd);
	*out = buf;
	*len = size;
	return 0;
fail:
	saved = errno;
	free(buf);
	close(fd);
	errno = saved;
	return -1;
}

static bool
digits(const char *p, size_t n)
{
	for (size_t i = 0; i < n; i++)
		if (p[i] < '0' || p[i] > '9')
			return false;
	return true;
}

static bool
hex(const char *p, size_t n)
{
	if (strlen(p) != n)
		return false;
	for (size_t i = 0; i < n; i++)
		if (!digits(p + i, 1) && (p[i] < 'a' || p[i] > 'f'))
			return false;
	return true;
}

static bool
date_matches_gen(const char *date, const char *gen)
{
	const int days[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
	int year = 0, month, day, max_day;

	if (strlen(date) != DATE_SIZE - 1 || date[4] != '-' || date[7] != '-' ||
	    memcmp(date, gen, 4) != 0 || memcmp(date + 5, gen + 4, 2) != 0 ||
	    memcmp(date + 8, gen + 6, 2) != 0)
		return false;
	for (size_t i = 0; i < 4; i++)
		year = year * 10 + gen[i] - '0';
	month = (gen[4] - '0') * 10 + gen[5] - '0';
	day = (gen[6] - '0') * 10 + gen[7] - '0';
	if (year == 0 || month < 1 || month > 12 || day < 1)
		return false;
	max_day = days[month - 1];
	if (month == 2 && year % 4 == 0 && (year % 100 != 0 || year % 400 == 0))
		max_day++;
	return day <= max_day;
}

static int
read_gen(int dir, const char *file, char gen[GEN_SIZE])
{
	size_t len;
	uint8_t *data;
	int result = -1;

	if (read_at(dir, file, GEN_SIZE, &data, &len) == -1)
		return -1;
	if (len == GEN_SIZE && data[len - 1] == '\n') {
		data[len - 1] = '\0';
		if (digits((char *)data, 8) && data[8] == '-' && hex((char *)data + 9, 16)) {
			memcpy(gen, data, GEN_SIZE);
			result = 0;
		}
	}
	free(data);
	if (result == -1)
		errno = EINVAL;
	return result;
}

static bool
split_manifest(char *data, size_t len, char *values[KEY_COUNT])
{
	char *line = data, *newline, *eq;
	size_t i;

	if (memchr(data, '\0', len) != NULL)
		return false;
	while (*line != '\0') {
		newline = strchr(line, '\n');
		eq = strchr(line, '=');
		if (newline == NULL || eq == NULL || eq > newline || eq + 1 == newline)
			return false;
		*eq = *newline = '\0';
		for (i = 0; i < KEY_COUNT; i++)
			if (strcmp(line, manifest_keys[i]) == 0)
				break;
		if (i == KEY_COUNT || values[i] != NULL)
			return false;
		for (char *p = eq + 1; *p != '\0'; p++)
			if ((unsigned char)*p < 32 || (unsigned char)*p > 126)
				return false;
		values[i] = eq + 1;
		line = newline + 1;
	}
	for (i = 0; i < KEY_COUNT; i++)
		if (values[i] == NULL)
			return false;
	return true;
}

static bool
checksum_matches(const uint8_t *bytes, size_t size, const char *expected)
{
	SHA2_CTX ctx;
	uint8_t digest[SHA256_DIGEST_LENGTH];
	const char alphabet[] = "0123456789abcdef";

	SHA256Init(&ctx);
	SHA256Update(&ctx, bytes, size);
	SHA256Final(digest, &ctx);
	for (size_t i = 0; i < sizeof(digest); i++)
		if (expected[2 * i] != alphabet[digest[i] >> 4] ||
		    expected[2 * i + 1] != alphabet[digest[i] & 15])
			return false;
	return true;
}

/* Verify the serialized strings once, independently of the record count. */
static bool
valid_pool(const uint8_t *p, uint32_t size)
{
	size_t len = 0;

	if (size == 0 || p[0] != 0 || p[size - 1] != 0)
		return false;
	for (uint32_t i = 0; i < size;) {
		uint8_t lead = p[i++];
		unsigned trail;
		uint8_t lo = 0x80, hi = 0xbf;

		if (lead == 0) {
			len = 0;
			continue;
		}
		if (lead >= 32 && lead < 127) {
			if (++len > 1024)
				return false;
			continue;
		}
		if (lead >= 0xc2 && lead <= 0xdf) {
			trail = 1;
		} else if (lead >= 0xe0 && lead <= 0xef) {
			trail = 2;
			if (lead == 0xe0)
				lo = 0xa0;
			if (lead == 0xed)
				hi = 0x9f;
		} else if (lead >= 0xf0 && lead <= 0xf4) {
			trail = 3;
			if (lead == 0xf0)
				lo = 0x90;
			if (lead == 0xf4)
				hi = 0x8f;
		} else
			return false;
		len += 1 + trail;
		if (len > 1024 || trail > size - i || p[i] < lo || p[i] > hi)
			return false;
		while (trail-- != 0) {
			uint8_t continuation = p[i++];

			if (continuation < 0x80 || continuation > 0xbf)
				return false;
		}
	}
	return true;
}

static bool
zero_prefix(const uint8_t *p, size_t size)
{
	while (size-- != 0)
		if (*p++ != 0)
			return false;
	return true;
}

static bool
valid_range(const uint8_t *r, enum d32_regdata_dataset dataset)
{
	const uint8_t *first = r + 4, *last = r + 20;

	if ((r[0] != 4 && r[0] != 6) || memcmp(first, last, 16) > 0)
		return false;
	if (r[0] == 4 && (!zero_prefix(first, 12) || !zero_prefix(last, 12)))
		return false;
	if (r[1] == D32_REGDATA_INTERVAL && dataset == D32_REGDATA_MCAST) {
		if (r[0] == 6)
			return first[0] == 0xff && last[0] == 0xff &&
			    first[1] == last[1] && r[2] == first[1] >> 4 &&
			    r[3] == (first[1] & 15);
		return first[12] >> 4 == 14 && last[12] >> 4 == 14 &&
		    r[2] == 0 && r[3] == D32_REGDATA_NO_SCOPE;
	}
	if (r[2] != 0 || r[3] != D32_REGDATA_NO_SCOPE)
		return false;
	if (r[1] == D32_REGDATA_INTERVAL)
		return true;
	if (dataset != D32_REGDATA_MCAST || !zero_prefix(last, 12))
		return false;
	switch (r[1]) {
	case D32_REGDATA_SCOPE:
		return r[0] == 6 && u32(last + 12) <= 15;
	case D32_REGDATA_RELATIVE:
		return r[0] == 4 && u32(last + 12) <= 255;
	case D32_REGDATA_GROUP_ID:
		return r[0] == 6;
	default:
		return false;
	}
}

static bool
valid_geo_data(const struct d32_regdata *data, size_t size, uint32_t pool)
{
	const uint8_t *prior = NULL;

	if (pool != 0 || size != HEADER_SIZE + (size_t)data->count * GEO_ROW_SIZE)
		return false;
	for (uint32_t i = 0; i < data->count; ++i) {
		const uint8_t *r = data->bytes + HEADER_SIZE + (size_t)i * GEO_ROW_SIZE;
		bool unknown = r[1] == 0 && r[2] == 0;
		bool code = r[1] >= 'A' && r[1] <= 'Z' && r[2] >= 'A' && r[2] <= 'Z' &&
		    !(r[1] == 'Z' && r[2] == 'Z');

		if ((r[0] != 4 && r[0] != 6) || r[3] != 0 || (!unknown && !code) ||
		    memcmp(r + 4, r + 20, 16) > 0)
			return false;
		if (r[0] == 4 && (!zero_prefix(r + 4, 12) || !zero_prefix(r + 20, 12)))
			return false;
		if (prior != NULL && (prior[0] > r[0] ||
		    (prior[0] == r[0] && memcmp(prior + 20, r + 4, 16) >= 0)))
			return false;
		prior = r;
	}
	return true;
}

static bool
valid_data(struct d32_regdata *data, size_t size, enum d32_regdata_dataset set)
{
	const uint8_t *b = data->bytes, *prior = NULL;
	uint32_t count, pool, offset;

	if (size < HEADER_SIZE || memcmp(b, "D32REG1\0", 8) != 0 ||
	    u32(b + 8) != 1 || u32(b + 12) != (uint32_t)set)
		return false;
	count = u32(b + 16);
	pool = u32(b + 20);
	if (count != data->count)
		return false;
	if (set == D32_REGDATA_GEO)
		return valid_geo_data(data, size, pool);
	if (pool > MAX_POOL ||
	    size != HEADER_SIZE + (size_t)count * ROW_SIZE + pool)
		return false;
	data->strings = b + HEADER_SIZE + (size_t)count * ROW_SIZE;
	if (!valid_pool(data->strings, pool))
		return false;
	for (uint32_t i = 0; i < count; i++) {
		const uint8_t *r = b + HEADER_SIZE + (size_t)i * ROW_SIZE;
		int group;

		if (!valid_range(r, set))
			return false;
		if (prior != NULL) {
			group = memcmp(prior, r, 2);
			if (group > 0 || (group == 0 && memcmp(prior + 4, r + 4, 32) > 0))
				return false;
			if (group == 0 && set == D32_REGDATA_NRO &&
			    memcmp(prior + 20, r + 4, 16) >= 0)
				return false;
		}
		for (size_t col = 36; col < ROW_SIZE; col += 4) {
			offset = u32(r + col);
			if (offset >= pool || (offset != 0 && data->strings[offset - 1] != 0))
				return false;
		}
		prior = r;
	}
	return true;
}

static int
load_one(int dir, enum d32_regdata_dataset set, bool previous,
    struct d32_regdata **out)
{
	const char *const dataset[] = { "", "iana", "mcast", "nro", "geo" };
	const char *range_error;
	char *v[KEY_COUNT] = { NULL };
	struct d32_regdata *data;
	uint8_t *manifest = NULL;
	size_t size, capacity;
	int genfd = -1, saved;

	data = calloc(1, sizeof(*data));
	if (data == NULL)
		return -1;
	if (read_gen(dir, previous ? "PREVIOUS" : "CURRENT", data->gen) == -1)
		goto fail;
	genfd = openat(dir, data->gen, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_DIRECTORY);
	if (genfd == -1 || read_at(genfd, "MANIFEST", 4096, &manifest, &size) == -1)
		goto fail;
	if (!split_manifest((char *)manifest, size, v) ||
	    strcmp(v[KEY_DATASET], dataset[set]) != 0 ||
	    strcmp(v[KEY_GEN], data->gen) != 0 || strcmp(v[KEY_SCHEMA], "1") != 0 ||
	    !date_matches_gen(v[KEY_DATE], data->gen) ||
	    !hex(v[KEY_SOURCE_SHA], HEX_SIZE) || !hex(v[KEY_OUTPUT_SHA], HEX_SIZE) ||
	    memcmp(v[KEY_SOURCE_SHA], data->gen + 9, 16) != 0 ||
	    (set == D32_REGDATA_GEO && strcmp(v[KEY_LICENSE], "CC-BY-4.0") != 0))
		goto invalid;
	data->count = (uint32_t)strtonum(v[KEY_RECORDS], 1, D32_REGDATA_MAX_ROWS, &range_error);
	if (range_error != NULL)
		goto invalid;
	capacity = set == D32_REGDATA_GEO ? HEADER_SIZE + (size_t)data->count * GEO_ROW_SIZE : MAX_DATA;
	if (read_at(genfd, "registry.d32r", capacity, &data->bytes, &size) == -1)
		goto fail;
	if (!checksum_matches(data->bytes, size, v[KEY_OUTPUT_SHA]) ||
	    !valid_data(data, size, set))
		goto invalid;
	memcpy(data->date, v[KEY_DATE], DATE_SIZE);
	data->previous = previous;
	free(manifest);
	close(genfd);
	*out = data;
	return 0;
invalid:
	errno = EINVAL;
fail:
	saved = errno;
	free(manifest);
	if (genfd != -1)
		close(genfd);
	d32_regdata_free(data);
	errno = saved;
	return -1;
}

int
d32_regdata_load_at(int dir, enum d32_regdata_dataset dataset,
    struct d32_regdata **out)
{
	int primary;

	if (out != NULL)
		*out = NULL;
	if (out == NULL || dataset < D32_REGDATA_IANA || dataset > D32_REGDATA_GEO) {
		errno = EINVAL;
		return -1;
	}
	if (load_one(dir, dataset, false, out) == 0)
		return 0;
	primary = errno;
	if (load_one(dir, dataset, true, out) == 0)
		return 0;
	if (errno == ENOENT)
		errno = primary;
	return -1;
}

void
d32_regdata_free(struct d32_regdata *data)
{
	if (data != NULL) {
		free(data->bytes);
		free(data);
	}
}

size_t
d32_regdata_count(const struct d32_regdata *data)
{
	return data->count;
}

bool
d32_regdata_get(const struct d32_regdata *data, size_t index,
    struct d32_regdata_row *row)
{
	const uint8_t *r;

	if (index >= data->count || row == NULL)
		return false;
	if (u32(data->bytes + 12) == D32_REGDATA_GEO) {
		r = data->bytes + HEADER_SIZE + index * GEO_ROW_SIZE;
		*row = (struct d32_regdata_row){r[0], D32_REGDATA_INTERVAL, 0,
		    D32_REGDATA_NO_SCOPE, r + 4, r + 20, "", "",
		    r[1] == 0 ? "unknown" : "estimated", (const char *)r + 1,
		    "", "", ""};
		return true;
	}
	r = data->bytes + HEADER_SIZE + index * ROW_SIZE;
	*row = (struct d32_regdata_row){
		r[0], r[1], r[2], r[3], r + 4, r + 20,
		(const char *)data->strings + u32(r + 36),
		(const char *)data->strings + u32(r + 40),
		(const char *)data->strings + u32(r + 44),
		(const char *)data->strings + u32(r + 48),
		(const char *)data->strings + u32(r + 52),
		(const char *)data->strings + u32(r + 56),
		(const char *)data->strings + u32(r + 60)
	};
	return true;
}

const char *
d32_regdata_gen(const struct d32_regdata *data)
{
	return data->gen;
}

const char *
d32_regdata_source_date(const struct d32_regdata *data)
{
	return data->date;
}

bool
d32_regdata_is_previous(const struct d32_regdata *data)
{
	return data->previous;
}
