#include "oui_internal.h"
#include "d32_io.h"
#include "d32_json.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define MAX_LINES 10000u
#define MAX_BATCH (2u * 1024u * 1024u)

static const char notice[] = "Registry assignee of this block \xe2\x80\x94 "
    "not proof of device manufacturer";

enum mode { MAC, PREFIX, ORG, BATCH };

struct output {
	bool json;
	bool first;
	int result;
};

static enum d32_error
write_json(void *stream, const char *p, size_t n)
{
	return fwrite(p, 1, n, stream) == n ? D32E_OK : D32E_IO;
}

static void
quote(const char *s)
{
	enum d32_error error = d32_json_quote(s, strlen(s), write_json, stdout);

	if (error == D32E_FORMAT || error == D32E_LIMIT)
		fputs("\"\\ufffd\"", stdout);
}

static const char *
json_bool(bool value)
{
	return value ? "true" : "false";
}

static void
begin_output(const struct output *out, const struct oui_db *db)
{
	if (!out->json) {
		if (db != NULL)
			printf("Registry dated %s (%s%s%s)\n", oui_db_date(db),
			    oui_db_generation(db), oui_db_stale(db) ? ", stale" : "",
			    oui_db_fallback(db) ? ", previous" : "");
		return;
	}
	fputs("{\"dataset\":", stdout);
	if (db == NULL) {
		fputs("null", stdout);
	} else {
		fputs("{\"generation\":", stdout);
		quote(oui_db_generation(db));
		fputs(",\"source_date\":", stdout);
		quote(oui_db_date(db));
		printf(",\"fallback\":%s,\"stale\":%s}",
		    json_bool(oui_db_fallback(db)), json_bool(oui_db_stale(db)));
	}
	fputs(",\"results\":[", stdout);
}

static void
emit(struct output *out, const char *input, size_t line, int result,
    const struct oui_mac *mac, const struct oui_row *row)
{
	const char *status = result == OUI_OK ? "found" : result == OUI_MISS ?
	    "not_found" : "parse_error";
	char normalized[18], prefix[10];
	size_t i;

	if (result == OUI_INVALID || (result == OUI_OK && out->result == OUI_MISS))
		out->result = result;
	if (mac != NULL) {
		uint64_t n = mac->value;
		(void)snprintf(normalized, sizeof(normalized),
		    "%02x:%02x:%02x:%02x:%02x:%02x", (unsigned)(n >> 40) & 0xff,
		    (unsigned)(n >> 32) & 0xff, (unsigned)(n >> 24) & 0xff,
		    (unsigned)(n >> 16) & 0xff, (unsigned)(n >> 8) & 0xff,
		    (unsigned)n & 0xff);
	}
	if (row != NULL)
		(void)snprintf(prefix, sizeof(prefix), "%0*" PRIx64,
		    (int)(row->width / 4), row->key);

	if (out->json) {
		if (!out->first)
			putchar(',');
		out->first = false;
		fputs("{\"input\":", stdout);
		quote(input);
		printf(",\"status\":\"%s\"", status);
		if (line != 0)
			printf(",\"line\":%zu", line);
		if (mac != NULL) {
			printf(",\"mac\":\"%s\",\"broadcast\":%s,\"group\":%s,"
			    "\"local\":%s,\"slap\":", normalized, json_bool(mac->broadcast),
			    json_bool(mac->group), json_bool(mac->local));
			if (mac->slap != NULL)
				quote(mac->slap);
			else
				fputs("null", stdout);
		}
		if (row != NULL) {
			printf(",\"prefix\":\"%s\",\"width\":%u,\"type\":\"%s\","
			    "\"date\":\"%s\",\"assignee\":", prefix, row->width,
			    oui_type_name(row->type), row->date);
			if (row->assignee_count == 1) {
				quote(row->assignee);
			} else {
				fputs("null,\"ambiguous\":true,\"assignees\":[", stdout);
				for (i = 0; i < row->assignee_count; i++) {
					if (i != 0)
						putchar(',');
					quote(row->assignees[i]);
				}
				putchar(']');
			}
			fputs(",\"notice\":", stdout);
			quote(notice);
		}
		putchar('}');
		return;
	}
	if (line != 0)
		printf("line %zu\t", line);
	if (mac != NULL) {
		printf("%s\tbroadcast=%s group=%s local=%s", normalized,
		    json_bool(mac->broadcast), json_bool(mac->group), json_bool(mac->local));
		if (mac->slap != NULL)
			printf(" slap=%s", mac->slap);
		putchar('\t');
	}
	if (row != NULL) {
		printf("%s/%u\t%s\t", prefix, row->width, oui_type_name(row->type));
		if (row->assignee_count > 1)
			fputs("Ambiguous registry assignment: ", stdout);
		for (i = 0; i < row->assignee_count; i++)
			printf("%s%s", i == 0 ? "" : " | ", row->assignees[i]);
		printf("\t%s\t%s\n", row->date, notice);
	} else
		puts(status);
}

static void
query_mac(struct output *out, const struct oui_db *db, const char *input, size_t line)
{
	struct oui_mac mac;
	struct oui_row row;
	int result;

	result = oui_parse_mac(input, &mac);
	if (result != OUI_OK) {
		emit(out, input, line, result, NULL, NULL);
		return;
	}
	result = oui_lookup(db, &mac, &row);
	emit(out, input, line, result, &mac, result == OUI_OK ? &row : NULL);
}

static void
query_prefix(struct output *out, const struct oui_db *db, const char *input)
{
	struct oui_row row;
	uint64_t key;
	unsigned width;
	int result;

	result = oui_parse_prefix(input, &key, &width);
	if (result == OUI_OK)
		result = oui_find_prefix(db, key, width, &row);
	emit(out, input, 0, result, NULL, result == OUI_OK ? &row : NULL);
}

static void
query_org(struct output *out, const struct oui_db *db, const char *input)
{
	struct oui_row rows[OUI_SEARCH_MAX];
	size_t i, n, len = strlen(input);

	if (len == 0 || !oui_utf8_valid((const unsigned char *)input, len)) {
		emit(out, input, 0, OUI_INVALID, NULL, NULL);
		return;
	}
	n = oui_search_org(db, input, rows, OUI_SEARCH_MAX);
	if (n == 0)
		emit(out, input, 0, OUI_MISS, NULL, NULL);
	for (i = 0; i < n; i++)
		emit(out, input, 0, OUI_OK, NULL, &rows[i]);
}

static void
query_batch(struct output *out, const struct oui_db *db, int fd)
{
	struct d32_reader reader;
	char input[128];
	size_t len, line = 0;
	bool eof;

	d32_reader_init(&reader, fd, MAX_BATCH);
	for (;;) {
		if (d32_read_line(&reader, input, sizeof(input), &len, &eof) != D32E_OK) {
			emit(out, "", line + 1, OUI_INVALID, NULL, NULL);
			return;
		}
		if (eof)
			break;
		if (++line > MAX_LINES) {
			emit(out, "", line, OUI_INVALID, NULL, NULL);
			return;
		}
		if (len > 0 && input[len - 1] == '\r')
			input[len - 1] = '\0';
		query_mac(out, db, input, line);
	}
	if (line == 0)
		emit(out, "", 1, OUI_INVALID, NULL, NULL);
}

static int
usage(void)
{
	fputs("usage: d32ouiq [-j] MAC ... | -p PREFIX | -o NAME | -f FILE\n", stderr);
	return OUI_INVALID;
}

int
main(int argc, char **argv)
{
	struct output out = { .first = true, .result = OUI_MISS };
	struct oui_db *db = NULL;
	struct stat info;
	const char *arg = NULL;
	enum mode mode = MAC;
	int ch, fd = -1, result;

#ifdef __OpenBSD__
	if (pledge("stdio rpath", NULL) == -1) {
		perror("pledge");
		return OUI_UNAVAILABLE;
	}
#endif
	while ((ch = getopt(argc, argv, "jp:o:f:")) != -1) {
		if (ch == 'j') {
			out.json = true;
			continue;
		}
		if (mode != MAC)
			return usage();
		switch (ch) {
		case 'p': mode = PREFIX; break;
		case 'o': mode = ORG; break;
		case 'f': mode = BATCH; break;
		default: return usage();
		}
		arg = optarg;
	}
	if ((mode == MAC && optind == argc) || (mode != MAC && optind != argc))
		return usage();
	if (mode == BATCH && d32_open_input(arg, D32_INPUT_FILE, MAX_BATCH, &fd,
	    &info) != D32E_OK) {
		begin_output(&out, NULL);
		emit(&out, arg, 1, OUI_INVALID, NULL, NULL);
		if (out.json)
			puts("]}");
		return OUI_INVALID;
	}
	result = oui_db_open_default(&db);
#ifdef __OpenBSD__
	if (pledge("stdio", NULL) == -1) {
		perror("pledge");
		oui_db_close(db);
		if (fd != -1)
			(void)close(fd);
		return OUI_UNAVAILABLE;
	}
#endif
	begin_output(&out, db);
	if (result != OUI_OK) {
		out.result = OUI_UNAVAILABLE;
		if (!out.json)
			fputs("No vendor registry installed\n", stderr);
	} else {
		switch (mode) {
		case MAC:
			for (; optind < argc; optind++)
				query_mac(&out, db, argv[optind], 0);
			break;
		case PREFIX: query_prefix(&out, db, arg); break;
		case ORG: query_org(&out, db, arg); break;
		case BATCH: query_batch(&out, db, fd); break;
		}
	}
	if (out.json)
		puts("]}");
	if (fd != -1)
		(void)close(fd);
	oui_db_close(db);
	if (fflush(stdout) == EOF || ferror(stdout))
		return OUI_UNAVAILABLE;
	return out.result;
}
