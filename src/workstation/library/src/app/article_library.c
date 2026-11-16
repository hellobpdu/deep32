/* article_library.c: one non-blocking DATA connection per configured pack; no SQL or Library path reads here. */
#include "article_library.h"
#include "library_app.h"
#include <stdlib.h>
#include <string.h>

#define AR_PACKS 16
typedef struct {
	char *directory, *generation;
	const char *name;
	libconn *c;
	int watch, fd;
	char *argv[3]; /* libc_new retains its argument pointers across helper restarts. */
} channel;
static channel packs[AR_PACKS];
static int npacks = 1, initialised, ticking;

static const struct { const char *name, *directory; } installed[] = {
	{ "lib-e26", "/d32/libcontent/e26" },
	{ "lib-corpus", "/d32/libcontent/corpus" },
	{ "lib-reserved", "/d32/libcontent/reserved" }
};

int ar_lib_register_named(const char *name, const char *directory)
{
	int i, k, p;
	if (!name || !directory || !*directory) return -1;
	for (i = 0; i < 3; i++) if (!strcmp(installed[i].name, name)) break;
	if (i == 3) return -1;
	for (k = 1; k < npacks; k++) if (packs[k].name && !strcmp(packs[k].name, name))
		return !strcmp(packs[k].directory, directory) ? k : -1;
	p = ar_lib_add(directory);
	if (p < 0 || (packs[p].name && strcmp(packs[p].name, name))) return -1;
	packs[p].name = installed[i].name;
	return p;
}

int ar_lib_resolve(const char *name)
{
	int i, k;
	if (!name) return -1;
	(void)ar_lib_count();
	for (k = 1; k < npacks; k++) if (packs[k].name && !strcmp(packs[k].name, name)) return k;
	for (i = 0; i < 3; i++) if (!strcmp(installed[i].name, name))
		return ar_lib_register_named(name, installed[i].directory);
	return -1;
}

const char *ar_lib_name(int pack)
{
	return pack >= 0 && pack < npacks && packs[pack].name ? packs[pack].name : "";
}

int ar_lib_add(const char *directory)
{
	channel *p;
	int i;
	if (!directory || !*directory || npacks == AR_PACKS) return -1;
	for (i = 1; i < npacks; i++) if (!strcmp(packs[i].directory, directory)) return i;
	p = &packs[npacks];
	if (!(p->directory = strdup(directory))) return -1;
	p->watch = p->fd = -1;
	return npacks++;
}

int ar_lib_count(void)
{
	if (!initialised) {
		const char *e = getenv("E26_ARTICLE_PACKS");
		char *copy, *p, *next;
		initialised = 1;
		if (e && (copy = strdup(e)) != NULL) {
			for (p = copy; p && *p; p = next) {
				next = strchr(p, ';'); if (next) *next++ = 0;
				if (ar_lib_add(p) < 0) break;
			}
			free(copy);
		}
	}
	return npacks;
}

static void rewatch(e26_app *a, channel *p);
static void tick(e26_app *a, void *ud);

static void arm(e26_app *a)
{
	int i;
	if (ticking || !a->ctx) return;
	for (i = 1; i < npacks; i++) if (packs[i].c && libc_pending(packs[i].c)) {
		ticking = 1; timer_after(a, 10, tick, NULL); break;
	}
}

static void readable(e26_app *a, int fd, void *ud)
{
	channel *p = (channel *)ud;
	(void)fd;
	if (libc_readable(p->c) && p->watch >= 0) { io_unwatch(p->watch); p->watch = p->fd = -1; }
	arm(a);
}

static void rewatch(e26_app *a, channel *p)
{
	int fd;
	if (!libc_pending(p->c)) return;
	fd = libc_fd(p->c);
	if (p->fd == fd && p->watch >= 0) return;
	if (p->watch >= 0) io_unwatch(p->watch);
	p->watch = a->ctx && fd >= 0 ? io_watch(a, fd, readable, p) : -1;
	p->fd = p->watch >= 0 ? fd : -1;
}

static void tick(e26_app *a, void *ud)
{
	int i;
	(void)ud;
	ticking = 0;
	for (i = 1; i < npacks; i++) if (packs[i].c && libc_pending(packs[i].c)) {
		libc_tick(packs[i].c, 0); (void)libc_flush(packs[i].c); rewatch(a, &packs[i]);
	}
	arm(a);
}

uint64_t ar_lib_request(e26_app *a, int pack, const char *op, const char *args, lib_cb cb, void *ud)
{
	channel *p;
	uint64_t id;
	if (pack < 0 || pack >= ar_lib_count()) return 0;
	if (!pack) return lib_request(a, op, args, cb, ud);
	p = &packs[pack];
	p->argv[0] = "-p"; p->argv[1] = p->directory; p->argv[2] = NULL;
	if (!p->c && !(p->c = libc_new(NULL, p->argv))) return 0;
	id = libc_request(p->c, op, args, cb, ud);
	rewatch(a, p); arm(a);
	return id;
}

const char *ar_lib_generation(int pack)
{
	return pack >= 0 && pack < npacks && packs[pack].generation ? packs[pack].generation : "";
}

void ar_lib_note_generation(int pack, const char *generation)
{
	char *s;
	if (pack < 0 || pack >= npacks || !generation || !(s = strdup(generation))) return;
	free(packs[pack].generation); packs[pack].generation = s;
}

void ar_lib_shutdown(void)
{
	int i;
	for (i = 0; i < npacks; i++) {
		if (i && packs[i].watch >= 0) io_unwatch(packs[i].watch);
		libc_free(packs[i].c); free(packs[i].directory); free(packs[i].generation);
	}
	memset(packs, 0, sizeof packs); npacks = 1; initialised = ticking = 0;
}
