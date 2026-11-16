/* state.c: the settings store (plan E5, O3), shared checked states, and the reading history (T7).
 *
 * Settings: <dir>/LIBRARY.INI, dir = -settings DIR (*settingsDir), else ~/.d32/e26. Keys keep the period design's own
 * names (ShowTitleScreen, IntroPlaySounds, NoUISound, TextStyle, JumpColor, FlyoutsOnMousePass ...) in a section
 * [Library]; lines it does not know (other sections, comments) are kept as they are when it writes the file. An X
 * resource or option given for the session (-notitle, -typesize M) overrides the stored value for that session and
 * is not written back (app.c). A key is registered (REG_SETTING) with its type and default; the file may hold others.
 * Written atomically (a temporary file renamed over it), mode 0600, the directory made 0700 when missing.
 *
 * Reading history: <dir>/history.tsv, "<unix time>\t<article id>\t<scroll>" per article opened, the last 500 kept;
 * W2-OPENING's "continue where I left off" reads it. */
#include "library_app.h"
#include "openat.h"
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define INI_LINE 512
#define HIST_KEEP 500

typedef struct {
	char *text;           /* the line as read (or as written for a new key), without its newline */
	char *key, *value;    /* a "key=value" line in [Library]; NULL otherwise */
} ini_line;

static char dir[600];
static ini_line *lines;
static int nlines, caplines;

const char *state_dir(void) { return dir; }

static char *dupstr(const char *s)
{
	size_t n = strlen(s) + 1;
	char *d = (char *)malloc(n);
	if (d) memcpy(d, s, n);
	return d;
}

static void trim(char *s)
{
	size_t n = strlen(s), i = 0;
	while (n && (s[n - 1] == ' ' || s[n - 1] == '\t' || s[n - 1] == '\r' || s[n - 1] == '\n')) s[--n] = 0;
	while (s[i] == ' ' || s[i] == '\t') i++;
	if (i) memmove(s, s + i, n - i + 1);
}

static int add_line(const char *text, const char *key, const char *value)
{
	ini_line *l;
	if (nlines == caplines) {
		int cap = caplines ? caplines * 2 : 32;
		ini_line *nl = (ini_line *)realloc(lines, (size_t)cap * sizeof(ini_line));
		if (!nl) return -1;
		lines = nl;
		caplines = cap;
	}
	l = &lines[nlines];
	l->text = dupstr(text);
	l->key = key ? dupstr(key) : NULL;
	l->value = value ? dupstr(value) : NULL;
	if (!l->text || (key && !l->key) || (value && !l->value)) {
		free(l->text); free(l->key); free(l->value);
		return -1;
	}
	nlines++;
	return 0;
}

static void free_values(ini_line *table, int count)
{
	int i;
	for (i = 0; i < count; i++) { free(table[i].text); free(table[i].key); free(table[i].value); }
}

static void clear(void)
{
	free_values(lines, nlines);
	nlines = 0;
}

int state_open(const char *d)
{
	char path[700], buf[INI_LINE];
	FILE *f;
	int in_library = 0;
	clear();
	if (d && d[0]) snprintf(dir, sizeof dir, "%s", d);
	else {
		const char *home = getenv("HOME");
		snprintf(dir, sizeof dir, "%s/.d32/e26", home && home[0] ? home : ".");
	}
	snprintf(path, sizeof path, "%s/LIBRARY.INI", dir);
	if (!(f = fopen(path, "r"))) return errno == ENOENT ? 0 : -1;
	while (fgets(buf, sizeof buf, f)) {
		char line[INI_LINE], *eq;
		size_t n = strlen(buf);
		while (n && (buf[n - 1] == '\n' || buf[n - 1] == '\r')) buf[--n] = 0;
		snprintf(line, sizeof line, "%s", buf);
		trim(line);
		if (line[0] == '[') { in_library = !strcmp(line, "[Library]"); add_line(buf, NULL, NULL); continue; }
		if (in_library && line[0] != ';' && line[0] != '#' && (eq = strchr(line, '='))) {
			*eq = 0;
			trim(line);
			trim(eq + 1);
			if (line[0]) { add_line(buf, line, eq + 1); continue; }
		}
		add_line(buf, NULL, NULL);
	}
	fclose(f);
	return 0;
}

static ini_line *find(const char *key)
{
	int i;
	for (i = 0; i < nlines; i++)
		if (lines[i].key && !strcasecmp(lines[i].key, key)) return &lines[i];
	return NULL;
}

int set_stored(const char *key) { return find(key) != NULL; }

const char *set_get(const char *key)
{
	ini_line *l = find(key);
	int id;
	const e26_setting *s;
	if (l) return l->value;
	if ((id = reg_find(REG_SETTING, key)) >= 0 && (s = (const e26_setting *)reg_impl(REG_SETTING, id))) return s->def;
	return NULL;
}

int set_get_bool(const char *key, int def)
{
	const char *v = set_get(key);
	if (!v || !v[0]) return def;
	if (!strcasecmp(v, "1") || !strcasecmp(v, "yes") || !strcasecmp(v, "true") || !strcasecmp(v, "on")) return 1;
	if (!strcasecmp(v, "0") || !strcasecmp(v, "no") || !strcasecmp(v, "false") || !strcasecmp(v, "off")) return 0;
	return def;
}

int set_get_int(const char *key, int def)
{
	const char *v = set_get(key);
	char *e;
	long x;
	if (!v || !v[0]) return def;
	x = strtol(v, &e, 10);
	return *e ? def : (int)x;
}

int set_put(const char *key, const char *value)
{
	ini_line *l = find(key);
	char text[INI_LINE];
	int i, sect = -1;
	snprintf(text, sizeof text, "%s=%s", key, value);
	if (l) {
		char *t = dupstr(text), *v = dupstr(value);
		if (!t || !v) { free(t); free(v); return -1; }
		free(l->text); free(l->value);
		l->text = t;
		l->value = v;
		return 0;
	}
	/* a new key goes at the end of [Library], which is made when the file has none */
	for (i = 0; i < nlines; i++) {
		char s[INI_LINE];
		snprintf(s, sizeof s, "%s", lines[i].text);
		trim(s);
		if (s[0] == '[') { if (sect >= 0) break; if (!strcmp(s, "[Library]")) sect = i; }
		else if (sect >= 0 && s[0]) sect = i;
	}
	if (sect < 0) {
		if (add_line("[Library]", NULL, NULL)) return -1;
		return add_line(text, key, value);
	}
	if (add_line(text, key, value)) return -1;
	{
		ini_line nl = lines[nlines - 1];
		memmove(&lines[sect + 2], &lines[sect + 1], (size_t)(nlines - 1 - (sect + 1)) * sizeof(ini_line));
		lines[sect + 1] = nl;
	}
	return 0;
}

/* mkdir -p for the settings directory (0700) */
static int mkdirs(const char *path)
{
	char p[600];
	size_t i;
	snprintf(p, sizeof p, "%s", path);
	for (i = 1; p[i]; i++)
		if (p[i] == '/') {
			p[i] = 0;
			if (mkdir(p, 0700) && errno != EEXIST) return -1;
			p[i] = '/';
		}
	return mkdir(p, 0700) && errno != EEXIST ? -1 : 0;
}

int set_save(void)
{
	char path[700], tmp[720];
	FILE *f;
	int i, fd, failed = 0;
	if (!dir[0] || mkdirs(dir)) { fprintf(stderr, "d32library: settings: cannot make %s\n", dir); return -1; }
	snprintf(path, sizeof path, "%s/LIBRARY.INI", dir);
	snprintf(tmp, sizeof tmp, "%s/.LIBRARY.INI.XXXXXX", dir);
	fd = mkstemp(tmp);
	if (fd < 0) goto error;
	if (!(f = fdopen(fd, "w"))) { close(fd); unlink(tmp); goto error; }
	for (i = 0; i < nlines; i++) {
		if (fprintf(f, "%s\n", lines[i].text) < 0) { failed = 1; break; }
	}
	if (ferror(f)) failed = 1;
	if (fclose(f)) failed = 1;
	if (failed || rename(tmp, path)) { unlink(tmp); goto error; }
	return 0;
error:
	fprintf(stderr, "d32library: settings: cannot write %s\n", path);
	return -1;
}

int set_put_save(const char *const *keys, const char *const *values, size_t count)
{
	ini_line *original = lines, *copy = NULL;
	int original_count = nlines, original_capacity = caplines, i;
	size_t j;
	if (!count) return 0;
	if (original_count) {
		copy = (ini_line *)calloc((size_t)original_count, sizeof *copy);
		if (!copy) return -1;
		for (i = 0; i < original_count; i++) {
			const ini_line *src = &original[i];
			ini_line *dst = &copy[i];
			dst->text = dupstr(src->text);
			dst->key = src->key ? dupstr(src->key) : NULL;
			dst->value = src->value ? dupstr(src->value) : NULL;
			if (!dst->text || (src->key && !dst->key) || (src->value && !dst->value)) {
				free_values(copy, i + 1); free(copy);
				return -1;
			}
		}
	}
	lines = copy;
	caplines = original_count;
	for (j = 0; j < count && !set_put(keys[j], values[j]); j++) { }
	if (j == count && !set_save()) {
		free_values(original, original_count); free(original);
		return 0;
	}
	clear(); free(lines);
	lines = original;
	nlines = original_count;
	caplines = original_capacity;
	return -1;
}

void state_builtin_keys(void)
{
	/* the period design's LIBRARY.INI keys the engine reads; the defaults are an installed the period design's (records.h) */
	static const e26_setting keys[] = {
		{ "ShowTitleScreen", "showTitleScreen", SET_BOOL, "1" },
		{ "IntroPlaySounds", "introPlaySounds", SET_BOOL, "1" },
		{ "NoUISound", "noUISound", SET_BOOL, "0" },
		{ "TextStyle", "typeSize", SET_STRING, "" },     /* "Small", "Medium", "Large"; "" = the font set's default */
		{ "ReaderFont", NULL, SET_STRING, "theme" },
		/* u14 readability (fonts.c, room_article.c's Text and Size menus) */
		{ "ReaderZoom", NULL, SET_INT, "0" },             /* 100 125 150 175 200 250; 0 = the panel's default */
		{ "ReaderUiScale", NULL, SET_INT, "0" },          /* the reader's interface face, percent; 0 = auto */
		{ "ReaderSpacing", NULL, SET_STRING, "normal" },  /* compact, normal, relaxed */
		{ "ReaderColumn", NULL, SET_STRING, "normal" },   /* narrow, normal, wide */
		{ "ReaderPaper", NULL, SET_STRING, "white" },     /* white, warm */
		{ "OutlineExhibits", NULL, SET_BOOL, "0" },       /* figures, tables and code in the outline */
		{ "JumpColor", NULL, SET_INT, "8388608" },       /* navy COLORREF for the technical reader */
		{ "FlyoutsOnMousePass", NULL, SET_BOOL, "1" },
	};
	size_t i;
	for (i = 0; i < sizeof keys / sizeof keys[0]; i++) reg_add(REG_SETTING, keys[i].key, &keys[i], "state", 0);
}

/* ---- shared checked states */

static char shkey[256][SCR_ID];
static int shval[256], nsh;

int *state_shared(const char *key)
{
	int i;
	for (i = 0; i < nsh; i++)
		if (!strcmp(shkey[i], key)) return &shval[i];
	if (nsh == 256) return NULL;
	snprintf(shkey[nsh], SCR_ID, "%s", key);
	shval[nsh] = -1;
	return &shval[nsh++];
}

void states_save(scr_screen *s)
{
	int i;
	for (i = 0; s && i < s->nc; i++)
		if (s->c[i].state[0]) { int *v = state_shared(s->c[i].state); if (v) *v = s->c[i].checked; }
}

void states_load(scr_screen *s)
{
	int i;
	for (i = 0; s && i < s->nc; i++)
		if (s->c[i].state[0]) { int *v = state_shared(s->c[i].state); if (v && *v >= 0) s->c[i].checked = *v; }
}

/* ---- reading history */

void state_note_article(const char *id, int scroll)
{
	char path[700], line[256];
	FILE *f;
	long size;
	if (!dir[0] || !id || !id[0] || mkdirs(dir)) return;
	snprintf(path, sizeof path, "%s/history.tsv", dir);
	if (!(f = fopen(path, "a"))) return;
	fprintf(f, "%lld\t%s\t%d\n", (long long)time(NULL), id, scroll);
	size = ftell(f);
	fclose(f);
	if (size > 120L * HIST_KEEP) {
		/* keep the last HIST_KEEP lines */
		char **keep = (char **)calloc(HIST_KEEP, sizeof(char *));
		int n = 0, i;
		if (!keep || !(f = fopen(path, "r"))) { free(keep); return; }
		while (fgets(line, sizeof line, f)) {
			free(keep[n % HIST_KEEP]);
			keep[n % HIST_KEEP] = dupstr(line);
			n++;
		}
		fclose(f);
		if ((f = fopen(path, "w"))) {
			for (i = n > HIST_KEEP ? n - HIST_KEEP : 0; i < n; i++) if (keep[i % HIST_KEEP]) fputs(keep[i % HIST_KEEP], f);
			fclose(f);
		}
		for (i = 0; i < HIST_KEEP; i++) free(keep[i]);
		free(keep);
	}
}

/* Bounded, newest-first positions. Each row is a public URI, article
 * content digest, section-relative block and canonical text byte offset. */
#define READER_HEADER "e26-reader-positions\t1\n"
#define READER_BYTES (HIST_KEEP * (OPENAT_URI_MAX + 96) + sizeof(READER_HEADER))

static int reader_public(const char *pack)
{
	return pack && (!strcmp(pack, "lib-e26") || !strcmp(pack, "lib-corpus"));
}

static int reader_uri(char *uri, size_t n, const char *pack, const char *id, const char *section)
{
	openat_target target;
	int len;
	if (!reader_public(pack) || !id) return -1;
	len = snprintf(uri, n, "library:%s/%s%s%s", pack, id, *section ? "#" : "", section);
	return len < 0 || (size_t)len >= n || openat_parse(uri, &target) ||
	    strcmp(target.article, id) || strcmp(target.anchor, section) ? -1 : 0;
}

static int reader_fd(const char *path, int flags)
{
	struct stat st;
	int fd = open(path, flags | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0600);
	if (fd < 0) return -1;
	if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_uid != geteuid() ||
	    st.st_nlink != 1 || (st.st_mode & 077) || st.st_size > (off_t)READER_BYTES) {
		close(fd); errno = EINVAL; return -1;
	}
	return fd;
}

static FILE *reader_open(const char *path)
{
	char header[sizeof READER_HEADER];
	int fd = reader_fd(path, O_RDONLY);
	FILE *f;
	if (fd < 0) return NULL;
	f = fdopen(fd, "r");
	if (!f) { close(fd); errno = EINVAL; return NULL; }
	if (!fgets(header, sizeof header, f) || strcmp(header, READER_HEADER)) {
		fclose(f); errno = EINVAL; return NULL;
	}
	return f;
}

static int reader_hash(const char *s)
{
	return strnlen(s, E26_READER_HASH_HEX + 1) == E26_READER_HASH_HEX &&
	    strspn(s, "0123456789abcdef") == E26_READER_HASH_HEX;
}

static int reader_number(const char *s, int *out)
{
	char *end;
	long value;
	errno = 0;
	value = strtol(s, &end, 10);
	if (s == end || *end || errno || value < -1 || value > INT_MAX) return -1;
	*out = (int)value;
	return 0;
}

/* 1 row, 0 EOF, -1 invalid. A clean reader never takes a partial row. */
static int reader_row(FILE *f, char **line, size_t *cap, openat_target *target,
    e26_reader_pos *pos)
{
	char *field[4], *sep;
	int i;
	size_t anchor_len;
	ssize_t n = getline(line, cap, f);
	if (n < 0) return feof(f) && !ferror(f) ? 0 : -1;
	if (n < 1 || (*line)[n - 1] != '\n' || memchr(*line, '\0', (size_t)n)) return -1;
	(*line)[n - 1] = '\0'; field[0] = *line;
	for (i = 0; i < 3; i++) {
		if (!(sep = strchr(field[i], '\t'))) return -1;
		*sep = '\0'; field[i + 1] = sep + 1;
	}
	if (openat_parse(field[0], target) || !reader_public(target->pack) ||
	    !reader_hash(field[1]) || reader_number(field[2], &pos->block) || reader_number(field[3], &pos->text))
		return -1;
	anchor_len = strnlen(target->anchor, sizeof target->anchor);
	if (anchor_len >= sizeof target->anchor || anchor_len >= sizeof pos->section) return -1;
	memcpy(pos->section, target->anchor, anchor_len + 1);
	memcpy(pos->hash, field[1], sizeof pos->hash);
	return 1;
}

int state_reader_load(const char *pack, const char *id, e26_reader_pos *pos)
{
	char path[700], *line = NULL;
	size_t cap = 0;
	openat_target target;
	e26_reader_pos candidate;
	FILE *f;
	int rc = 0, n;
	if (!dir[0] || !reader_public(pack) || !id || !pos) return 0;
	snprintf(path, sizeof path, "%s/reader-positions.tsv", dir);
	if (!(f = reader_open(path))) return 0;
	while ((n = reader_row(f, &line, &cap, &target, &candidate)) > 0) {
		if (!strcmp(pack, target.pack) && !strcmp(id, target.article)) {
			*pos = candidate; rc = 1; break;
		}
	}
	free(line);
	fclose(f);
	return rc;
}

void state_reader_save(const char *pack, const char *id, const e26_reader_pos *pos)
{
	char path[700], temp[720], lockpath[700], uri[OPENAT_URI_MAX + 1], *line = NULL;
	size_t cap = 0;
	openat_target target;
	e26_reader_pos old;
	struct flock guard = { .l_type = F_WRLCK, .l_whence = SEEK_SET };
	int lock, fd, ok, input_error = 0, keep = 1;
	FILE *in, *out;
	if (!dir[0] || !pos || !reader_hash(pos->hash) || pos->block < -1 || pos->text < -1 ||
	    strnlen(pos->section, sizeof pos->section) == sizeof pos->section ||
	    reader_uri(uri, sizeof uri, pack, id, pos->section) || mkdirs(dir)) return;
	snprintf(path, sizeof path, "%s/reader-positions.tsv", dir);
	snprintf(temp, sizeof temp, "%s/.reader-positions.XXXXXX", dir);
	snprintf(lockpath, sizeof lockpath, "%s/.reader-positions.lock", dir);
	if ((lock = reader_fd(lockpath, O_RDWR | O_CREAT)) < 0) return;
	if (fcntl(lock, F_SETLKW, &guard)) { close(lock); return; }
	in = reader_open(path);
	if (!in && errno != ENOENT) { close(lock); return; }
	fd = mkstemp(temp);
	out = fd < 0 ? NULL : fdopen(fd, "w");
	if (!out) {
		if (fd >= 0) { close(fd); unlink(temp); }
		if (in) fclose(in);
		close(lock);
		return;
	}
	ok = fprintf(out, READER_HEADER "%s\t%s\t%d\t%d\n", uri, pos->hash, pos->block, pos->text) > 0;
	while (ok && in && reader_row(in, &line, &cap, &target, &old) > 0) {
		if (keep >= HIST_KEEP || (!strcmp(pack, target.pack) && !strcmp(id, target.article))) continue;
		ok = fprintf(out, "%s\t%s\t%d\t%d\n", line, old.hash, old.block, old.text) > 0;
		keep++;
	}
	if (in) {
		input_error = ferror(in);
		if (fclose(in)) input_error = 1;
	}
	free(line);
	/* A corrupt tail is replaced by the verified prefix and newest reading position. */
	if (input_error || fflush(out) || fsync(fd)) ok = 0;
	if (fclose(out)) ok = 0;
	if (!ok || rename(temp, path)) unlink(temp);
	close(lock);
}
