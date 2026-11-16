/* article_fetch.c: generation-pinned, paged article loading through e26-lib, with stale-answer rejection. */
#include "room_article.h"
#include "article_library.h"
#include "room_documents.h"
#include "openat.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
	e26_app *app;
	char *id, *anchor, *generation;
	char *catalogue_generation, *catalogue_id;
	int pack, history_at, scroll, view, search, checked;
	unsigned tried;
	unsigned long epoch;
	uint64_t request;
	ar_wire wire;
} fetch;
static unsigned long epoch;
static fetch *current;

static void release(fetch *f)
{
	if (current == f) current = NULL;
	ar_wire_free(&f->wire); free(f->id); free(f->anchor); free(f->generation);
	free(f->catalogue_generation); free(f->catalogue_id); free(f);
}

void art_fetch_cancel(void)
{
	epoch++;
	if (current) { ar_wire_free(&current->wire); current = NULL; }
	art_st()->busy = 0; art_st()->message[0] = 0;
	/* DATA guarantees one callback, including cancellation/timeouts. Old contexts live until that callback. */
}

static void failure(fetch *f, const char *why)
{
	e26_app *a = f->app;
	art_st()->busy = 0;
	if (f->catalogue_id) cisco_forget_results();
	snprintf(art_st()->message, sizeof art_st()->message, "%s", why);
	if (a->verbose) fprintf(stderr, "d32library: article %s: %s\n", f->id ? f->id : "-", why);
	release(f);
	nav_notice_text(a, "Open article", why);
}

static void ask(fetch *f);

static void received(const lib_reply *r, void *ud)
{
	fetch *f = (fetch *)ud;
	a2_doc d;
	char err[200];
	const char *reader_generation;
	int more, i;
	if (f->epoch != epoch) { release(f); return; }
	if (!r->ok) {
		if (f->search && r->code && !strcmp(r->code, "E_NOT_FOUND")) {
			for (i = 0; i < ar_lib_count(); i++) if (!(f->tried & (1u << i))) {
				f->pack = i; f->checked = 0; f->tried |= 1u << i; free(f->generation); f->generation = NULL; ask(f); return;
			}
		}
		failure(f, r->code && !strcmp(r->code, "E_NOT_FOUND") ? "This article is not in the configured Library packs." :
		        r->message ? r->message : "The Library helper could not read this article.");
		return;
	}
	/* Strict Cisco pages carry the verified origin in the request. The outer
	 * envelope belongs to the default helper, even for Corpus articles. */
	reader_generation = f->catalogue_id ? f->generation : r->generation;
	if (!r->generation || !*r->generation || !reader_generation ||
	    (f->generation && strcmp(f->generation, reader_generation))) {
		failure(f, "The Library changed while this article was loading. Open it again."); return;
	}
	if (!f->generation && !(f->generation = strdup(reader_generation))) { failure(f, "There is not enough memory for this article."); return; }
	more = ar_wire_page(&f->wire, r->result);
	if (more < 0) { failure(f, "The Library returned an incomplete or invalid article page."); return; }
	if (!more) { ask(f); return; }
	memset(&d, 0, sizeof d);
	if (ar_wire_doc(&f->wire, &d, err, sizeof err)) {
		if (f->app->verbose) fprintf(stderr, "d32library: article %s: reader-format validation: %s\n", f->id, err);
		failure(f, "The article failed reader-format validation."); return; }
	if (*ar_lib_name(f->pack) && strcmp(d.id, f->id)) {
		a2_free(&d); failure(f, "The Library returned a different article identifier."); return;
	}
	if (f->catalogue_id) {
		const char *marking = ar_marking(&d);
		if (marking && *marking && strcmp(marking, "PUBLIC")) {
			a2_free(&d); failure(f, "The Cisco article is not public."); return;
		}
		if (*f->anchor) {
			for (i = 0; i < d.nb && (!d.b[i].anchor || strcmp(d.b[i].anchor, f->anchor)); ++i) ;
			if (i == d.nb) { a2_free(&d); failure(f, "The Cisco section is absent from the verified article."); return; }
		}
	}
	if (!strcmp(ar_lib_name(f->pack), "lib-reserved") &&
	    (!openat_private_allowed() || strcmp(d.cls, "corpus") ||
	     strcmp(ar_marking(&d), "RESTRICTED"))) {
		a2_free(&d); failure(f, "This private Library article cannot be opened with its required access and marking."); return;
	}
	ar_lib_note_generation(f->pack, reader_generation);
	if (f->catalogue_id) cisco_confirm_open();
	if (art_install_v2(f->app, &d, f->pack, f->anchor, f->history_at, f->scroll, f->view)) {
		a2_free(&d); failure(f, "There is not enough memory to open this article."); return;
	}
	release(f);
}

static void status_received(const lib_reply *r, void *ud)
{
	fetch *f = (fetch *)ud;
	const char *name = json_str(json_get(r->result, "pack"), "name");
	if (f->epoch != epoch) { release(f); return; }
	if (!r->ok || !name || strcmp(name, ar_lib_name(f->pack)) || !r->generation || !*r->generation) {
		failure(f, "The requested Library pack is unavailable or has a different identity."); return;
	}
	f->generation = strdup(r->generation);
	if (!f->generation) { failure(f, "There is not enough memory for this article."); return; }
	f->checked = 1;
	ask(f);
}

static void ask(fetch *f)
{
	e26j_buf b;
	char *args;
	uint64_t request;
	if (!strcmp(ar_lib_name(f->pack), "lib-reserved") && !openat_private_allowed()) {
		failure(f, "Access to this private Library pack is unavailable."); return;
	}
	if (f->catalogue_id) {
		e26j_init(&b);
		e26j_lit(&b, "{\"catalogue_generation\":"); e26j_cstr(&b, f->catalogue_generation);
		e26j_lit(&b, ",\"catalogue_id\":"); e26j_cstr(&b, f->catalogue_id);
		e26j_lit(&b, ",\"reader_pack\":"); e26j_cstr(&b, ar_lib_name(f->pack));
		e26j_lit(&b, ",\"reader_generation\":"); e26j_cstr(&b, f->generation);
		e26j_lit(&b, ",\"article_id\":"); e26j_cstr(&b, f->id);
		e26j_lit(&b, ",\"start\":"); e26j_u(&b, f->wire.next); e26j_lit(&b, ",\"limit\":128}");
		args = e26j_take(&b);
		if (!args) { failure(f, "There is not enough memory for this article."); return; }
		request = lib_request(f->app, "finder.cisco.article", args, received, f);
		free(args);
		if (!request && current == f) failure(f, "The helper could not start this Cisco article request.");
		return;
	}
	if (!f->checked && *ar_lib_name(f->pack)) {
		request = ar_lib_request(f->app, f->pack, "status", "{}", status_received, f);
		if (!request && current == f) failure(f, "The Library helper could not check the requested pack.");
		return;
	}
	e26j_init(&b); e26j_lit(&b, "{\"id\":"); e26j_cstr(&b, f->id);
	e26j_lit(&b, ",\"start\":"); e26j_u(&b, f->wire.next); e26j_lit(&b, ",\"limit\":128}");
	args = e26j_take(&b);
	if (!args) { failure(f, "There is not enough memory for this article."); return; }
	request = ar_lib_request(f->app, f->pack, "article", args, received, f);
	free(args);
	if (!request && current == f) failure(f, "The Library helper could not start this article request.");
}

static void start(e26_app *a, int pack, const char *id, const char *anchor, int history_at, int scroll, int view,
    const char *catalogue_generation, const char *catalogue_id, const char *reader_generation)
{
	fetch *f;
	art_fetch_cancel();
	if (pack >= ar_lib_count() || pack < -1) { nav_notice_text(a, "Open article", "This Library pack is unavailable."); return; }
	if (pack >= 0 && !strcmp(ar_lib_name(pack), "lib-reserved") && !openat_private_allowed()) {
		nav_notice_text(a, "Open article", "Access to this private Library pack is unavailable."); return;
	}
	if (!id || !*id || strlen(id) > 4096) { nav_notice_text(a, "Open article", "The article identifier is empty or too long."); return; }
	f = (fetch *)calloc(1, sizeof *f);
	if (!f) { nav_notice_text(a, "Open article", "There is not enough memory for this article."); return; }
	f->app = a;
	f->id = strdup(id); f->anchor = strdup(anchor ? anchor : "");
	if (!f->id || !f->anchor) { failure(f, "There is not enough memory for this article."); return; }
	if (catalogue_id) {
		f->catalogue_id = strdup(catalogue_id);
		f->catalogue_generation = strdup(catalogue_generation);
		f->generation = strdup(reader_generation);
		if (!f->catalogue_id || !f->catalogue_generation || !f->generation) {
			cisco_forget_results(); failure(f, "There is not enough memory for this article."); return;
		}
	}
	f->app = a; f->epoch = epoch; f->history_at = history_at; f->scroll = scroll; f->view = view;
	f->search = pack < 0;
	f->pack = pack < 0 ? art_st()->pack : pack;
	if (f->pack < 0 || f->pack >= ar_lib_count()) f->pack = 0;
	f->tried = 1u << f->pack;
	current = f; art_st()->busy = 1;
	snprintf(art_st()->message, sizeof art_st()->message, "Opening article...");
	ask(f);
}

void art_fetch_start(e26_app *a, int pack, const char *id, const char *anchor, int history_at, int scroll, int view)
{
	start(a, pack, id, anchor, history_at, scroll, view, NULL, NULL, NULL);
}

void art_open_cisco(e26_app *a, int pack, const char *catalogue_generation, const char *catalogue_id,
    const char *reader_generation, const char *id, const char *anchor)
{
	const char *name = ar_lib_name(pack);
	if (strcmp(name, "lib-e26") && strcmp(name, "lib-corpus")) {
		cisco_forget_results();
		nav_notice_text(a, "Open Cisco article", "The requested public Library pack is unavailable.");
		return;
	}
	/* A previously opened article with the same ID can belong to an older
	 * generation. Always use the strict helper path here. */
	start(a, pack, id, anchor, -1, 0, art_st()->is_v2 ? art_st()->view :
	    set_get_int("MainWindowView", ART_VIEW_DEFAULT), catalogue_generation, catalogue_id, reader_generation);
}
