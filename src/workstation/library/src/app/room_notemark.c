/* room_notemark.c: Notemarks and the articles read lately (u14 readability spec S8). Tools > Notemark and Ctrl+D
 * (main.keys) add the reader's place to <settings>/NOTEMARK.TXT; every article opened goes to the top of
 * <settings>/RECENT.TXT with its section and how far through it is, refreshed while it is read (every 3 s, and when
 * a screen changes). The Library home on the Main screen (room_main.c) lists both. Tab-separated, one place a line,
 * 8-bit text as the reader draws it:
 *   RECENT.TXT    <unix time> <pack> <article id> <percent> <title> <section>   newest first, the last 50
 *   NOTEMARK.TXT  <unix time> <position URI> <title> <section>                  oldest first (appended), the last 200
 * Only public Library documents are kept, as the reading history and the reading positions are (art_public_position):
 * no private-pack title or place is written to these plain files. */
#include "room_notemark.h"
#include "room_article.h"
#include "article_library.h"
#include "openat.h"
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

static e26_place *rows[2];
static int nrows[2], loaded[2], armed;
static char shown[200];          /* the "Notemark added" line while it stands in the status strip */

static int cap(int list) { return list == PLACES_RECENT ? PLACES_RECENT_MAX : PLACES_NOTEMARK_MAX; }

static void clean(char *s) { for (; *s; s++) if (*s == '\t' || *s == '\r' || *s == '\n') *s = ' '; }

static int path_of(int list, char *out, size_t n, const char *suffix)
{
	const char *dir = state_dir();
	int len;
	if (!dir || !*dir) return -1;
	len = snprintf(out, n, "%s/%s%s", dir, list == PLACES_RECENT ? "RECENT.TXT" : "NOTEMARK.TXT", suffix);
	return len < 0 || (size_t)len >= n ? -1 : 0;
}

static char *field(char **p)
{
	char *s = *p, *tab;
	if (!s) return NULL;
	if ((tab = strchr(s, '\t')) != NULL) { *tab = 0; *p = tab + 1; } else *p = NULL;
	return s;
}

/* r becomes the newest of list (index 0); an older entry for the same place goes, the oldest beyond the cap too */
static void put_first(int list, const e26_place *r)
{
	e26_place *v = rows[list];
	int i, n = nrows[list];
	for (i = 0; i < n; i++)
		if (list == PLACES_RECENT ? !strcmp(v[i].pack, r->pack) && !strcmp(v[i].id, r->id) : !strcmp(v[i].uri, r->uri)) {
			memmove(&v[i], &v[i + 1], (size_t)(n - i - 1) * sizeof *v);
			n--;
			break;
		}
	if (n == cap(list)) n--;
	memmove(&v[1], &v[0], (size_t)n * sizeof *v);
	v[0] = *r;
	nrows[list] = n + 1;
}

/* the user's own regular file, not a link, at most 1 MB (as state.c reads the reading positions) */
static FILE *open_list(const char *path)
{
	struct stat st;
	int fd = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
	FILE *f;
	if (fd < 0) return NULL;
	if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_uid != geteuid() || st.st_nlink != 1 ||
	    st.st_size > 1024L * 1024L || !(f = fdopen(fd, "r"))) { close(fd); return NULL; }
	return f;
}

static void load(int list)
{
	char path[1200], line[2400];
	FILE *f;
	if (loaded[list]) return;
	if (!rows[list] && !(rows[list] = calloc((size_t)cap(list), sizeof *rows[list]))) return;
	if (path_of(list, path, sizeof path, "")) return;   /* no settings directory yet: try again later */
	loaded[list] = 1;
	nrows[list] = 0;
	if (!(f = open_list(path))) return;
	while (fgets(line, sizeof line, f)) {
		char *p = line, *when, *a, *b, *c, *d, *e;
		e26_place r;
		openat_target t;
		line[strcspn(line, "\r\n")] = 0;
		memset(&r, 0, sizeof r);
		when = field(&p); a = field(&p); b = field(&p); c = field(&p); d = field(&p);
		if (!when || !a || !b || !c) continue;
		r.when = strtoll(when, NULL, 10);
		if (list == PLACES_RECENT) {
			e = field(&p);
			if (!d || !e || (strcmp(a, "lib-e26") && strcmp(a, "lib-corpus")) || !*b || strlen(b) >= sizeof r.id) continue;
			snprintf(r.pack, sizeof r.pack, "%s", a);
			snprintf(r.id, sizeof r.id, "%s", b);
			r.percent = atoi(c);
			if (r.percent < 0 || r.percent > 100) r.percent = 0;
			snprintf(r.title, sizeof r.title, "%s", d);
			snprintf(r.section, sizeof r.section, "%s", e);
			if (nrows[list] < cap(list)) rows[list][nrows[list]++] = r; /* the file is newest first */
		} else {
			if (strlen(a) >= sizeof r.uri || openat_parse(a, &t) ||
			    (strcmp(t.pack, "lib-e26") && strcmp(t.pack, "lib-corpus"))) continue;
			snprintf(r.uri, sizeof r.uri, "%s", a);
			snprintf(r.pack, sizeof r.pack, "%s", t.pack);
			snprintf(r.id, sizeof r.id, "%s", t.article);
			snprintf(r.title, sizeof r.title, "%s", b);
			snprintf(r.section, sizeof r.section, "%s", c);
			put_first(list, &r);                                       /* the file is oldest first */
		}
	}
	fclose(f);
}

/* a new file beside the old one (mode 0600, mkstemp), flushed to disc, then renamed over it */
static void save(int list)
{
	char path[1200], temp[1210];
	const e26_place *v = rows[list];
	FILE *f;
	int i, fd, n = nrows[list], bad = 0;
	if (!v || path_of(list, path, sizeof path, "") ||
	    snprintf(temp, sizeof temp, "%s/.%s.XXXXXX", state_dir(), list == PLACES_RECENT ? "RECENT" : "NOTEMARK") >=
	    (int)sizeof temp || (fd = mkstemp(temp)) < 0) return;
	if (!(f = fdopen(fd, "w"))) { close(fd); unlink(temp); return; }
	for (i = 0; i < n; i++) {
		const e26_place *r = list == PLACES_RECENT ? &v[i] : &v[n - 1 - i];
		if (list == PLACES_RECENT)
			bad |= fprintf(f, "%lld\t%s\t%s\t%d\t%s\t%s\n", r->when, r->pack, r->id, r->percent, r->title, r->section) < 0;
		else bad |= fprintf(f, "%lld\t%s\t%s\t%s\n", r->when, r->uri, r->title, r->section) < 0;
	}
	if (fflush(f) || fsync(fd)) bad = 1;
	if (fclose(f) || bad || rename(temp, path)) unlink(temp);
}

int places_count(int list)
{
	if (list != PLACES_RECENT && list != PLACES_NOTEMARKS) return 0;
	load(list);
	return nrows[list];
}
const e26_place *places_get(int list, int i) { return i >= 0 && i < places_count(list) ? &rows[list][i] : NULL; }

/* the open article's place: its title, the heading the view is in, how far through it is; -1 for none or a document
 * these lists do not keep */
static int here(e26_app *a, e26_place *r)
{
	art_state *st = art_st();
	int e, range = a->doc_h - a->view_h;
	if (!a->have_art || !st->is_v2 || !st->full_id || !art_public_position(a) || strlen(st->full_id) >= sizeof r->id ||
	    strpbrk(st->full_id, "\t\r\n"))
		return -1;
	memset(r, 0, sizeof *r);
	snprintf(r->pack, sizeof r->pack, "%s", ar_lib_name(st->pack));
	snprintf(r->id, sizeof r->id, "%s", st->full_id);
	snprintf(r->title, sizeof r->title, "%s", a->art.title[0] ? a->art.title : st->full_id);
	for (e = lay_current_entry(&st->L, a->scroll); e >= 0 && st->L.en[e].kind != LE_HEADING; e--) ;
	snprintf(r->section, sizeof r->section, "%s", e >= 0 && st->L.en[e].text ? st->L.en[e].text : "Beginning");
	clean(r->title); clean(r->section);
	r->percent = range <= 0 ? 0 : a->scroll >= range ? 100 : (int)(100LL * a->scroll / range);
	r->when = (long long)time(NULL);
	return 0;
}

static void note_recent(e26_app *a)
{
	e26_place r;
	const e26_place *top;
	if (here(a, &r)) return;
	load(PLACES_RECENT);
	if (!rows[PLACES_RECENT]) return;
	top = nrows[PLACES_RECENT] ? &rows[PLACES_RECENT][0] : NULL;
	if (top && !strcmp(top->pack, r.pack) && !strcmp(top->id, r.id) && top->percent == r.percent &&
	    !strcmp(top->section, r.section) && !strcmp(top->title, r.title)) return;
	put_first(PLACES_RECENT, &r);
	save(PLACES_RECENT);
}

static int reading(const e26_app *a)
{
	const scr_screen *base = a->nwin ? a->wstack[0].s : a->cur;
	return base && !strcmp(base->name, "article");
}

static void tick(e26_app *a, void *ud)
{
	(void)ud;
	armed = 0;
	if (!reading(a)) return;
	note_recent(a);
	armed = 1;
	timer_after(a, 3000, tick, NULL);
}

static void arm(e26_app *a)
{
	if (armed || !a->ctx || !reading(a)) return;
	armed = 1;
	timer_after(a, 3000, tick, NULL);
}

static void opened(e26_app *a, const e26_event *ev, void *ud)
{
	(void)ev; (void)ud;
	note_recent(a);
	arm(a);
}

static void entered(e26_app *a, const e26_event *ev, void *ud)
{
	(void)ev; (void)ud;
	note_recent(a);   /* leaving the article for the Main screen keeps where the reader was */
	arm(a);
}

static void unshow(e26_app *a, void *ud)
{
	art_state *st = art_st();
	(void)ud;
	if (!shown[0] || strcmp(st->message, shown)) { shown[0] = 0; return; }
	st->message[0] = shown[0] = 0;
	if (a->dpy) nav_redraw(a);
}

/* Tools > Notemark, Ctrl+D: the reader's place (its section) joins the notemarks */
static int act_notemark(e26_app *a, const e26_actx *x)
{
	art_state *st = art_st();
	e26_place r;
	char uri[OPENAT_URI_MAX + 1];
	(void)x;
	if (!reading(a) || !a->have_art || !st->is_v2) {
		nav_notice_text(a, "Notemark", "Open an article, then choose Notemark (Ctrl+D) to mark your place. Your notemarks "
		    "are listed on the Main screen.");
		return 0;
	}
	if (here(a, &r) || art_position_uri(a, uri, sizeof uri) || strlen(uri) >= sizeof r.uri) {
		nav_notice_text(a, "Notemark", "Notemarks are kept for public Library documents only.");
		return 0;
	}
	snprintf(r.uri, sizeof r.uri, "%s", uri);
	load(PLACES_NOTEMARKS);
	if (!rows[PLACES_NOTEMARKS]) return 0;
	put_first(PLACES_NOTEMARKS, &r);
	save(PLACES_NOTEMARKS);
	if (a->verbose) fprintf(stderr, "d32library: notemark %s (%d kept)\n", uri, nrows[PLACES_NOTEMARKS]);
	snprintf(st->message, sizeof st->message, "Notemark added: %.150s", r.section);
	snprintf(shown, sizeof shown, "%s", st->message);
	if (a->ctx) timer_after(a, 3000, unshow, NULL);
	return 1;
}

void places_open(e26_app *a, int list, int i)
{
	const e26_place *r = places_get(list, i);
	int action, pack;
	e26_actx x;
	if (!r) return;
	if (list == PLACES_NOTEMARKS) {
		action = reg_find(REG_ACTION, "openat.request");
		if (action < 0 || !action_built(action)) {
			nav_notice_text(a, "Notemark", "The Library reader is unavailable.");
			return;
		}
		memset(&x, 0, sizeof x);
		x.ctl = -1; x.arg = r->uri; x.label = "Notemark";
		(void)action_run(a, action, &x);
		return;
	}
	/* the reader resumes where it was left (its reading position), as any article opened without a section does */
	pack = ar_lib_resolve(r->pack);
	if (pack < 0) { nav_notice_text(a, "Continue reading", "This Library pack is unavailable."); return; }
	art_open_pack(a, pack, r->id, NULL);
}

void e26_mod_room_notemark(void);
void e26_mod_room_notemark(void)
{
	static const e26_action notemark = { act_notemark };
	reg_add(REG_ACTION, "room.notemark", &notemark, "room_notemark", 0);
	(void)ev_subscribe(EV_ARTICLE_OPEN, opened, NULL);
	(void)ev_subscribe(EV_SCREEN_ENTER, entered, NULL);
}
