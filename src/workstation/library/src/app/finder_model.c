/* finder_model.c: no X, catalogue SQL, article content or alternative query parser in the UI state. */
#include "finder_model.h"
#include "../common/e26j.h"
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>

#define SAVED_MAX (128u * 1024u)
#define BITMAP_MAX (8u * 1024u * 1024u)

static const char *const media_names[] = {
	"image", "maths", "pdf", "cli", "table", "audio", "video", "interactivity"
};

static const struct { const char *key, *label; } types[FINDER_TYPES] = {
	{ "all", "All types" }, { "rfc", "RFC" }, { "draft", "Internet-Draft" },
	{ "wiki", "Wikipedia" }, { "nist", "NIST" }, { "cisco", "Cisco (all)" },
	{ "synthetic", "Deep32 reference" }, { "unclassified", "Unclassified" },
	{ "cisco-white-paper", "White Papers" }, { "cisco-cvd", "Validated Designs" },
	{ "cisco-configuration-guide", "Configuration Guides" }, { "cisco-other", "Other" }
};

const char *finder_type_key(int type) { return types[type].key; }
const char *finder_type_label(int type) { return types[type].label; }
int finder_type_index(const char *key)
{
	int i;
	for (i = FINDER_RFC; key && i < FINDER_TYPES; i++) if (!strcmp(key, types[i].key)) return i;
	return FINDER_UNCLASSIFIED;
}

static int text_ok(const char *s, size_t cap, int empty)
{
	size_t n = 0;
	if (!s) return 0;
	while (n < cap && s[n]) n++;
	return n < cap && (empty || n) && e26j_utf8(s, n);
}

static int copy_text(char *dst, size_t cap, const jv *v, int empty)
{
	size_t n;
	if (!v || v->type != J_STR || !v->str) return -1;
	n = strlen(v->str);
	if (n >= cap || (!empty && !n) || !e26j_utf8(v->str, n)) return -1;
	memcpy(dst, v->str, n + 1);
	return 0;
}

static int optional_text(char *dst, size_t cap, const jv *v)
{
	if (!v || v->type == J_NULL) { dst[0] = 0; return 0; }
	return copy_text(dst, cap, v, 1);
}

static int optional_bool(const jv *row, const char *key, int *out)
{
	const jv *v = json_get(row, key);
	if (v && v->type != J_BOOL) return -1;
	*out = v && v->b;
	return 0;
}

static int integer(const jv *obj, const char *key, int64_t lo, int64_t hi, int *out)
{
	int64_t n;
	if (e26j_i64(json_get(obj, key), lo, hi, &n)) return -1;
	*out = (int)n;
	return 0;
}

void finder_model_init(finder_model *m)
{
	memset(m, 0, sizeof *m);
	memcpy(m->scope, "all", sizeof "all");
	m->next = -1;
	m->near_distance = 8;
	m->year_min = -100000;
	m->year_max = 2030;
}

void finder_model_clear(finder_model *m)
{
	free(m->bitmap);
	finder_model_init(m);
}

static int valid_filters(const finder_model *m)
{
	size_t i, j;
	if (!text_ok(m->query, sizeof m->query, 1) || !text_ok(m->scope, sizeof m->scope, 0) ||
	    !text_ok(m->place, sizeof m->place, 1) || m->ncategories > FINDER_CATEGORIES || m->media > 255u ||
	    (strcmp(m->scope, "all") && strcmp(m->scope, "current") && strcmp(m->scope, "last")) ||
	    (m->title_mode != 0 && m->title_mode != 1) || m->near_distance < 1 || m->near_distance > 9999 ||
	    (m->catalogue != 0 && m->catalogue != 1) || (m->years != 0 && m->years != 1)) return 0;
	if (m->years && (!m->year_min || !m->year_max || m->year_min < -100000 || m->year_max > 2030 ||
	    m->year_min > m->year_max)) return 0;
	for (i = 0; i < m->ncategories; i++) {
		if (!text_ok(m->categories[i], sizeof m->categories[i], 0)) return 0;
		for (j = 0; j < i; j++) if (!strcmp(m->categories[i], m->categories[j])) return 0;
	}
	return 1;
}

/* The display has no year zero. Convert historical BC dates to the protocol's astronomical dates. */
static int astronomical(int historical) { return historical < 0 ? historical + 1 : historical; }

static void filters_out(e26j_buf *b, const finder_model *m, int whole_library)
{
	size_t i;
	int sep = 0;
	e26j_lit(b, "{\"categories\":[");
	for (i = 0; !whole_library && i < m->ncategories; i++) {
		if (i) e26j_lit(b, ",");
		e26j_cstr(b, m->categories[i]);
	}
	e26j_lit(b, "],\"media_kinds\":[");
	for (i = 0; i < sizeof media_names / sizeof media_names[0]; i++) if (m->media & (1u << i)) {
		if (sep++) e26j_lit(b, ",");
		e26j_cstr(b, media_names[i]);
	}
	e26j_lit(b, "]");
	if (m->years) {
		e26j_lit(b, ",\"year_min\":"); e26j_i(b, astronomical(m->year_min));
		e26j_lit(b, ",\"year_max\":"); e26j_i(b, astronomical(m->year_max));
	}
	e26j_lit(b, ",\"places\":[");
	if (m->place[0]) e26j_cstr(b, m->place);
	e26j_lit(b, "]}");
}

static char *model_args(const finder_model *m, const char *prefix, int start, const char *current,
                        const char *query, int title_mode, int type, const char *sort)
{
	e26j_buf b;
	if (!m || start < 0 || !valid_filters(m) || !prefix || strlen(prefix) > 4096 ||
	    !text_ok(query, FINDER_QUERY, 1)) return NULL;
	if (query[0] && !strcmp(m->scope, "last") && (!m->bitmap || !m->generation[0])) return NULL;
	if (query[0] && !strcmp(m->scope, "current") && (!current || !current[0])) return NULL;
	e26j_init(&b);
	e26j_lit(&b, "{\"prefix\":"); e26j_cstr(&b, prefix);
	e26j_lit(&b, ",\"start\":"); e26j_i(&b, start);
	e26j_lit(&b, ",\"limit\":64,\"catalogue\":"); e26j_lit(&b, m->catalogue ? "true" : "false");
	e26j_lit(&b, ",\"filters\":"); filters_out(&b, m, sort && m->whole_library);
	e26j_lit(&b, ",\"query\":"); e26j_cstr(&b, query);
	e26j_lit(&b, ",\"scope\":"); e26j_cstr(&b, m->scope);
	if (query[0] && !strcmp(m->scope, "last")) {
		e26j_lit(&b, ",\"last\":"); e26j_cstr(&b, m->bitmap);
	}
	if (query[0] && !strcmp(m->scope, "current")) {
		e26j_lit(&b, ",\"current\":"); e26j_cstr(&b, current);
	}
	e26j_lit(&b, ",\"title_mode\":"); e26j_lit(&b, title_mode ? "true" : "false");
	e26j_lit(&b, ",\"near\":"); e26j_i(&b, m->near_distance);
	if (sort) {
		e26j_lit(&b, ",\"mode\":\"documents\",\"type\":"); e26j_cstr(&b, finder_type_key(type));
		e26j_lit(&b, ",\"sort\":"); e26j_cstr(&b, sort);
		if (m->whole_library) e26j_lit(&b, ",\"collection\":\"library\"");
	}
	e26j_lit(&b, "}");
	return e26j_take(&b);
}

char *finder_model_args(const finder_model *m, const char *prefix, int start, const char *current)
{
	return m ? model_args(m, prefix, start, current, m->query, m->title_mode, FINDER_ALL, NULL) : NULL;
}

char *finder_model_document_args(const finder_model *m, const char *prefix, int start, const char *current,
                                 const char *query, int title_mode, int type, const char *sort)
{
	if (!m || (!m->ncategories && !m->whole_library) || type < FINDER_ALL || type >= FINDER_TYPES || !sort) return NULL;
	return model_args(m, prefix, start, current, query, title_mode, type, sort);
}

int finder_model_reply(finder_model *m, const jv *r, const char *generation)
{
	const jv *rows, *v, *type_counts;
	char gen[128], cat[128], *bitmap = NULL;
	finder_row *page = NULL;
	uint64_t total, universe, ord, doc_count = 0, count;
	uint32_t counts[FINDER_TYPES] = { 0 };
	int documents, type, types_unavailable = 0;
	int start, next = -1;
	size_t i, j, nbits;
	unsigned char *bits = NULL;
	if (!m || !r || r->type != J_OBJ || !generation || !text_ok(generation, sizeof gen, 0)) return -1;
	snprintf(gen, sizeof gen, "%s", generation);
	if (copy_text(cat, sizeof cat, json_get(r, "catalogue_id"), 0) ||
	    e26j_u64(json_get(r, "total"), &total) || total > INT_MAX ||
	    e26j_u64(json_get(r, "universe"), &universe) || universe > UINT32_MAX || total > universe ||
	    integer(r, "start", 0, INT_MAX, &start) || !(rows = json_get(r, "rows")) || rows->type != J_ARR ||
	    rows->n > FINDER_PAGE || rows->n > total) return -1;
	v = json_get(r, "doc_count");
	documents = v != NULL;
	type_counts = json_get(r, "type_counts");
	if (documents) {
		if (e26j_u64(v, &doc_count) || doc_count > INT_MAX || !type_counts || type_counts->type != J_OBJ) return -1;
		if (optional_bool(r, "types_unavailable", &types_unavailable)) return -1;
		for (type = FINDER_RFC; type < FINDER_TYPES; type++) {
			v = json_get(type_counts, finder_type_key(type));
			if (!v) continue;
			if (e26j_u64(v, &count) || count > INT_MAX ||
			    (type < FINDER_CISCO_WHITE_PAPER && counts[FINDER_ALL] > INT_MAX - count)) return -1;
			counts[type] = (uint32_t)count;
			if (type < FINDER_CISCO_WHITE_PAPER) counts[FINDER_ALL] += counts[type];
		}
	}
	v = json_get(r, "next");
	if (!v || (v->type != J_NULL && integer(r, "next", 0, INT_MAX, &next))) return -1;
	/* This composite query pages the filtered list by offset, unlike frozen wheel_page's global cursor. */
	if (rows->n && ((uint64_t)start >= total || rows->n > total - (uint64_t)start)) return -1;
	if ((uint64_t)start + rows->n < total) {
		if (!rows->n || next < 0 || (uint64_t)next != (uint64_t)start + rows->n) return -1;
	} else if (next != -1) return -1;
	v = json_get(r, "bitmap");
	if (!v || v->type != J_STR || !v->str || strlen(v->str) > BITMAP_MAX) return -1;
	bits = e26j_unb64(v->str, strlen(v->str), &nbits);
	if (!bits) return -1;
	free(bits);
	bitmap = (char *)malloc(strlen(v->str) + 1);
	page = (finder_row *)calloc(FINDER_PAGE, sizeof *page);
	if (!bitmap || !page) goto bad;
	memcpy(bitmap, v->str, strlen(v->str) + 1);
	for (i = 0; i < rows->n; i++) {
		const jv *row = rows->items[i], *co;
		finder_row *p = &page[i];
		if (!row || row->type != J_OBJ || e26j_u64(json_get(row, "wheel_ordinal"), &ord) || ord >= universe ||
		    copy_text(p->id, sizeof p->id, json_get(row, "id"), 0) ||
		    copy_text(p->title, sizeof p->title, json_get(row, "title"), 0) ||
		    copy_text(p->cls, sizeof p->cls, json_get(row, "class"), 0) ||
		    copy_text(p->pack, sizeof p->pack, json_get(row, "pack"), 1) ||
		    copy_text(p->marking, sizeof p->marking, json_get(row, "marking"), 1) ||
		    copy_text(p->snippet, sizeof p->snippet, json_get(row, "snippet"), 1)) goto bad;
		if (strcmp(p->cls, "library") && strcmp(p->cls, "corpus") && strcmp(p->cls, "catalogue")) goto bad;
		co = json_get(row, "catalogue_only");
		if (!co || co->type != J_BOOL) goto bad;
		p->catalogue_only = co->b != 0;
		p->wheel_ordinal = (uint32_t)ord;
		v = json_get(row, "document_type");
		if (documents && v) {
			if (v->type != J_STR || (p->document_type = finder_type_index(v->str)) < FINDER_CISCO_WHITE_PAPER)
				goto bad;
		}
		v = json_get(row, "topic_id");
		if (documents && v && copy_text(p->topic_id, sizeof p->topic_id, v, 0)) goto bad;
		if (documents &&
		    (p->catalogue_only ||
		     copy_text(p->source_kind, sizeof p->source_kind, json_get(row, "source_kind"), 0) ||
		     optional_text(p->published, sizeof p->published, json_get(row, "published")) ||
		     optional_text(p->updated, sizeof p->updated, json_get(row, "updated")) ||
		     optional_text(p->revision, sizeof p->revision, json_get(row, "revision")) ||
		     optional_text(p->anchor, sizeof p->anchor, json_get(row, "anchor")) ||
		     optional_text(p->section_title, sizeof p->section_title, json_get(row, "section_title")) ||
		     (p->section_title[0] && !p->anchor[0]) ||
		     optional_bool(row, "obsoleted", &p->obsoleted) || optional_bool(row, "has_sections", &p->has_sections) ||
		     optional_bool(row, "duplicate", &p->duplicate))) goto bad;
		for (j = 0; j < i; j++) if (page[j].wheel_ordinal == p->wheel_ordinal &&
		    (!documents || !strcmp(page[j].anchor, p->anchor))) goto bad;
	}
	free(m->bitmap);
	m->bitmap = bitmap;
	memcpy(m->row, page, FINDER_PAGE * sizeof *page);
	free(page);
	m->nrow = rows->n; m->total = (uint32_t)total; m->universe = (uint32_t)universe;
	m->start = start; m->next = next;
	m->doc_count = (uint32_t)doc_count;
	m->types_unavailable = types_unavailable;
	memcpy(m->type_counts, counts, sizeof counts);
	snprintf(m->generation, sizeof m->generation, "%s", gen); snprintf(m->catalogue_id, sizeof m->catalogue_id, "%s", cat);
	m->status[0] = 0;
	return 0;
bad:
	free(bitmap); free(page);
	return -1;
}

int finder_model_public_section(const char *catalogue, const char *generation, const char *current,
                                const char *marking, const char *anchor)
{
	static const char chars[] = "abcdefghijklmnopqrstuvwxyz0123456789._-";
	return catalogue && !strcmp(catalogue, "lib-e26") && generation && *generation && current &&
	    !strcmp(generation, current) && (!marking || !*marking || !strcasecmp(marking, "PUBLIC")) &&
	    anchor && *anchor >= 'a' && *anchor <= 'z' && strlen(anchor) <= 128 &&
	    strspn(anchor, chars) == strlen(anchor);
}

void finder_model_remember(finder_model *m)
{
	int i, found;
	if (!m || !text_ok(m->query, sizeof m->query, 0) || m->npast < 0 || m->npast > FINDER_PAST) return;
	for (found = 0; found < m->npast; found++) if (!strcmp(m->past[found], m->query)) break;
	if (found == m->npast && m->npast < FINDER_PAST) m->npast++;
	if (found >= FINDER_PAST) found = FINDER_PAST - 1;
	for (i = found; i > 0; i--) memcpy(m->past[i], m->past[i - 1], FINDER_QUERY);
	memcpy(m->past[0], m->query, FINDER_QUERY);
}

int finder_year(const char *text, int *year)
{
	char *end;
	const char *suffix;
	long n;
	int era = 0;
	if (!text || !year) return -1;
	while (*text == ' ' || *text == '\t') text++;
	if (!*text || (*text != '-' && *text != '+' && !isdigit((unsigned char)*text))) return -1;
	errno = 0;
	n = strtol(text, &end, 10);
	if (errno || end == text || n < -100000 || n > 100000 || !n) return -1;
	while (*end == ' ' || *end == '\t') end++;
	suffix = end;
	if (!strncasecmp(suffix, "BC", 2)) { era = -1; end += 2; }
	else if (!strncasecmp(suffix, "AD", 2)) { era = 1; end += 2; }
	else if (*end) return -1;
	while (*end == ' ' || *end == '\t') end++;
	if (*end || (era && (n < 1 || *text == '-' || *text == '+'))) return -1;
	if (era < 0) n = -n;
	if (n > 2030) return -1;
	*year = (int)n;
	return 0;
}

static int fail(char *err, size_t len, const char *message)
{
	if (err && len) snprintf(err, len, "%s", message);
	return -1;
}

static int safe_name(const char *name)
{
	size_t i, n;
	if (!name || !(n = strlen(name)) || n > 80 || name[0] == '.') return 0;
	for (i = 0; i < n; i++) if (!((name[i] >= 'A' && name[i] <= 'Z') || (name[i] >= 'a' && name[i] <= 'z') ||
	    (name[i] >= '0' && name[i] <= '9') || name[i] == '-' || name[i] == '_' || name[i] == '.')) return 0;
	return strstr(name, "..") == NULL;
}

/* Walk from a directory descriptor, refusing symlinks in every component (including the settings directory).
 * Only the final directory is required to be private; /tmp and HOME's ancestors need not be mode 0700. */
static int open_dir(const char *dir, int create)
{
	char path[4096], *p, *slash;
	int fd, child, components = 0;
	struct stat st;
	if (!dir || !dir[0] || strlen(dir) >= sizeof path) return -1;
	snprintf(path, sizeof path, "%s", dir);
	fd = open(path[0] == '/' ? "/" : ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	if (fd < 0) return -1;
	p = path;
	while (*p == '/') p++;
	while (*p) {
		slash = strchr(p, '/');
		if (slash) *slash = 0;
		if (!strcmp(p, "..")) goto bad;
		if (strcmp(p, ".")) {
			components++;
			child = openat(fd, p, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
			if (child < 0 && create && errno == ENOENT) {
				if (mkdirat(fd, p, 0700) && errno != EEXIST) goto bad;
				child = openat(fd, p, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
			}
			if (child < 0) goto bad;
			close(fd); fd = child;
		}
		if (!slash) break;
		p = slash + 1;
		while (*p == '/') p++;
	}
	if (!components || fstat(fd, &st) || st.st_uid != getuid() || (create && fchmod(fd, 0700)) ||
	    (!create && (st.st_mode & 077) != 0)) goto bad;
	return fd;
bad:
	close(fd);
	return -1;
}

static int regular_file(int dirfd, const char *name, int absent)
{
	struct stat st;
	if (fstatat(dirfd, name, &st, AT_SYMLINK_NOFOLLOW)) return absent && errno == ENOENT;
	return S_ISREG(st.st_mode) && st.st_uid == getuid() && st.st_nlink == 1 && !(st.st_mode & 077);
}

static char *saved_text(const finder_model *m)
{
	e26j_buf b;
	if (!valid_filters(m) || !text_ok(m->generation, sizeof m->generation, 0) ||
	    !text_ok(m->catalogue_id, sizeof m->catalogue_id, 0)) return NULL;
	e26j_init(&b);
	e26j_lit(&b, "{\"version\":1,\"catalogue_id\":"); e26j_cstr(&b, m->catalogue_id);
	e26j_lit(&b, ",\"generation\":"); e26j_cstr(&b, m->generation);
	e26j_lit(&b, ",\"query\":"); e26j_cstr(&b, m->query);
	e26j_lit(&b, ",\"scope\":"); e26j_cstr(&b, m->scope);
	e26j_lit(&b, ",\"title_mode\":"); e26j_lit(&b, m->title_mode ? "true" : "false");
	e26j_lit(&b, ",\"near\":"); e26j_i(&b, m->near_distance);
	e26j_lit(&b, ",\"catalogue\":"); e26j_lit(&b, m->catalogue ? "true" : "false");
	e26j_lit(&b, ",\"filters\":"); filters_out(&b, m, 0);
	e26j_lit(&b, "}\n");
	return e26j_take(&b);
}

int finder_model_save(const finder_model *m, const char *dir, const char *name, char *err, size_t errlen)
{
	static unsigned counter;
	char tmp[128], *text;
	int d, fd = -1, tries, ok = 0;
	size_t n, pos = 0;
	if (!m || !safe_name(name)) return fail(err, errlen, "Choose a simple list name.");
	text = saved_text(m);
	if (!text) return fail(err, errlen, "The request cannot be saved.");
	n = strlen(text);
	if (n > SAVED_MAX || (d = open_dir(dir, 1)) < 0) { free(text); return fail(err, errlen, "The Lists directory is unavailable."); }
	if (!regular_file(d, name, 1)) goto done;
	for (tries = 0; tries < 100 && fd < 0; tries++) {
		snprintf(tmp, sizeof tmp, ".finder.%ld.%u", (long)getpid(), ++counter);
		fd = openat(d, tmp, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
		if (fd < 0 && errno != EEXIST) break;
	}
	if (fd < 0) goto done;
	if (fchmod(fd, 0600)) goto cleanup;
	while (pos < n) {
		ssize_t wrote = write(fd, text + pos, n - pos);
		if (wrote < 0 && errno == EINTR) continue;
		if (wrote <= 0) goto cleanup;
		pos += (size_t)wrote;
	}
	if (fsync(fd)) goto cleanup;
	if (close(fd)) { fd = -1; goto cleanup; }
	fd = -1;
	if (!regular_file(d, name, 1) || renameat(d, tmp, d, name)) goto cleanup;
	ok = 1;
	/* rename has committed. A directory fsync is best effort where the file system supports it. */
	(void)fsync(d);
cleanup:
	if (fd >= 0) close(fd);
	if (!ok) unlinkat(d, tmp, 0);
done:
	close(d); free(text);
	if (!ok) return fail(err, errlen, "The list could not be written safely.");
	if (err && errlen) err[0] = 0;
	return 0;
}

static int boolean(const jv *obj, const char *key, int *out)
{
	const jv *v = json_get(obj, key);
	if (!v || v->type != J_BOOL) return -1;
	*out = v->b != 0;
	return 0;
}

static int keys_known(const jv *obj, const char *const *known)
{
	size_t i, j;
	if (!obj || obj->type != J_OBJ) return 0;
	for (i = 0; i < obj->n; i++) {
		for (j = 0; known[j] && strcmp(known[j], obj->keys[i]); j++) { }
		if (!known[j]) return 0;
	}
	return 1;
}

static int saved_read(finder_model *m, const jv *v)
{
	static const char *const root_keys[] = {
		"version", "catalogue_id", "generation", "query", "scope", "title_mode", "near", "catalogue", "filters", NULL
	};
	static const char *const filter_keys[] = { "categories", "media_kinds", "year_min", "year_max", "places", NULL };
	const jv *filters, *a, *s;
	int version, y;
	size_t i, j;
	if (!keys_known(v, root_keys) || integer(v, "version", 1, 1, &version) ||
	    copy_text(m->generation, sizeof m->generation, json_get(v, "generation"), 0) ||
	    copy_text(m->catalogue_id, sizeof m->catalogue_id, json_get(v, "catalogue_id"), 0) ||
	    copy_text(m->query, sizeof m->query, json_get(v, "query"), 1) ||
	    copy_text(m->scope, sizeof m->scope, json_get(v, "scope"), 0) ||
	    boolean(v, "title_mode", &m->title_mode) || boolean(v, "catalogue", &m->catalogue) ||
	    integer(v, "near", 1, 9999, &m->near_distance)) return -1;
	filters = json_get(v, "filters");
	if (!keys_known(filters, filter_keys)) return -1;
	a = json_get(filters, "categories");
	if (!a || a->type != J_ARR || a->n > FINDER_CATEGORIES) return -1;
	m->ncategories = a->n;
	for (i = 0; i < a->n; i++) if (copy_text(m->categories[i], sizeof m->categories[i], a->items[i], 0)) return -1;
	a = json_get(filters, "media_kinds");
	if (!a || a->type != J_ARR || a->n > 8) return -1;
	for (i = 0; i < a->n; i++) {
		s = a->items[i];
		if (!s || s->type != J_STR) return -1;
		for (j = 0; j < 8; j++) if (!strcmp(s->str, media_names[j])) break;
		if (j == 8 || (m->media & (1u << j))) return -1;
		m->media |= 1u << j;
	}
	if (json_get(filters, "year_min") || json_get(filters, "year_max")) {
		m->years = 1;
		if (integer(filters, "year_min", -99999, 2030, &y)) return -1;
		m->year_min = y <= 0 ? y - 1 : y;
		if (integer(filters, "year_max", -99999, 2030, &y)) return -1;
		m->year_max = y <= 0 ? y - 1 : y;
	}
	a = json_get(filters, "places");
	if (!a || a->type != J_ARR || a->n > 1) return -1;
	if (a->n && copy_text(m->place, sizeof m->place, a->items[0], 0)) return -1;
	return valid_filters(m) ? 0 : -1;
}

int finder_model_load(finder_model *m, const char *dir, const char *name, char *err, size_t errlen)
{
	int d, fd = -1, ok = 0;
	struct stat st;
	char *text = NULL;
	size_t pos = 0, n;
	jv *v = NULL;
	finder_model *loaded = NULL;
	if (!m || !safe_name(name)) return fail(err, errlen, "Choose a simple list name.");
	d = open_dir(dir, 0);
	if (d < 0) return fail(err, errlen, "The Lists directory is unavailable.");
	fd = openat(d, name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
	if (fd < 0 || fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_uid != getuid() || st.st_nlink != 1 ||
	    (st.st_mode & 077) || st.st_size <= 0 || st.st_size > (off_t)SAVED_MAX) goto done;
	n = (size_t)st.st_size;
	text = (char *)malloc(n + 1);
	loaded = (finder_model *)malloc(sizeof *loaded);
	if (!text || !loaded) goto done;
	while (pos < n) {
		ssize_t got = read(fd, text + pos, n - pos);
		if (got < 0 && errno == EINTR) continue;
		if (got <= 0) goto done;
		pos += (size_t)got;
	}
	/* Refuse a concurrent append rather than silently parsing only the initial stat size. */
	for (;;) {
		char extra;
		ssize_t got = read(fd, &extra, 1);
		if (got < 0 && errno == EINTR) continue;
		if (got != 0) goto done;
		break;
	}
	text[n] = 0;
	v = e26j_parse(text, n, NULL, 0);
	finder_model_init(loaded);
	if (saved_read(loaded, v)) goto done;
	/* Identity is compared with the live Library before reuse by the room. No bitmap is persisted. */
	free(m->bitmap);
	*m = *loaded;
	ok = 1;
done:
	if (fd >= 0) close(fd);
	close(d); free(text); free(loaded); json_free(v);
	if (!ok) return fail(err, errlen, "The saved list is unavailable or invalid.");
	if (err && errlen) err[0] = 0;
	return 0;
}
