/* room_pinpoint.c: build 3 FINDER. Only 64 titles live in the UI process.
 * The Library owns sorting, assignments and query evaluation. A serial rejects
 * superseded replies; typing is coalesced for 40 ms without blocking X. */
#include "room_finder.h"
#include "room_documents.h"
#include "document_catalogue.h"
#include "room_article.h"
#include "article_library.h"
#include "edit.h"
#include "focus.h"
#include "lib_client.h"
#include "select.h"
#include "../common/e26j.h"
#include <X11/Xutil.h>
#include <X11/keysym.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

static finder_model F;
static int doc_type, doc_sort, documents_unsupported, grouped_hits, doc_ready;
static int cisco_type = -1, cisco_sort, cisco_saved_type, cisco_saved_sort;
static int cisco_saved_whole, cisco_saved_cat, cisco_saved_sub;
static char cisco_saved_query[sizeof(((e26_app *)0)->edit)];
static char cisco_saved_categories[FINDER_CATEGORIES][sizeof F.categories[0]];
static size_t cisco_saved_ncategories;
static const char *const sorts[] = { "title", "id", "date" };
static const char *const sort_labels[] = { "Title A-Z", "Identifier", "Date" };
/* u14 (M16): with Type RFC the identifier sort is the RFC number, and says so */
static const char *sort_label(int sort)
{
	return sort == 1 && doc_type == FINDER_RFC && cisco_type < 0 ? "Number" : sort_labels[sort];
}

typedef struct {
	char anchor[129], title[1024];
	int level;
} pin_section;

enum section_failure { SECTION_OK, SECTION_RETRY, SECTION_STALE };
typedef struct {
	char id[sizeof F.row[0].id];
	int row, start, count, total;
	enum section_failure failure;
	int retry_start;
	finder_row document;
	pin_section page[FINDER_PAGE];
} pin_expansion;
static pin_expansion expanded;

/* A single public results page survives leaving Pinpointer for the Reader. */
static struct {
	finder_model model;
	pin_expansion expansion;
	char edit[sizeof(((e26_app *)0)->edit)];
	char *prior;
	char prior_query[FINDER_QUERY], prior_generation[128];
	int valid, top, selected, cat, sub, type, sort, grouped, prior_title, prior_near;
} results;

int pin_doc_mode(void);
int pin_doc_display(const e26_app *, int, char *, size_t, char *, size_t, int *, int *, int *);
int pin_doc_expand(e26_app *, int, int);
uint32_t pin_doc_count(void);
void pin_menu_items(scr_menu *);
int pin_wheel_rows(const e26_app *, const scr_screen *, const scr_control *);
const char *pin_doc_tag(int);

static uint64_t request_id, open_request;
static unsigned open_serial;
static int initialised, debounce, fields_valid = 1;
static int fields_reason;
enum { PIN_BASE_ROWS = 15, PIN_ROW_PITCH = 16 };
static int viewport_rows = PIN_BASE_ROWS;
static int resize_top = -1, resized_top;
static char *last_anchor;
static char last_query[FINDER_QUERY], last_generation[128];
/* a subcategory's article (the Category menu's OSPF: RFC 2328), chosen in the list when its filtered page arrives */
static char choose_id[TAX_TEXT];
static unsigned choose_serial;
static int choose_pages; /* u14 S9: > 0 while the Browse Panel pages through a topic for the open article */
static int last_title, last_near;
static const scr_screen *join_screens[SCR_MAX_SCREENS];
static char joins[SCR_MAX_SCREENS][2][8];
static int join_slot(const scr_screen *s)
{
	int i;
	for (i = 0; i < SCR_MAX_SCREENS; i++) if (join_screens[i] == s || !join_screens[i]) { join_screens[i] = s; return i; }
	return -1;
}
static const char *const media_names[] = { "image", "maths", "pdf", "cli", "table", "audio", "video", "interactivity" };
typedef struct { e26_app *app; unsigned serial; int start, seek, documents; char generation[128]; } pin_pending;

static void fetch(e26_app *a, int start, int seek);
static void fill_viewport(e26_app *a);
static void clamp_viewport(e26_app *a);
static void fetch_sections(e26_app *a, int start);
static void pin_layout(e26_app *a);
static int active_rows(const e26_app *a);
static void doc_status(e26_app *a);

finder_model *finder_st(void) { return &F; }
const finder_row *finder_row_at(int row)
{
	return row >= F.start && (size_t)(row - F.start) < F.nrow ? &F.row[row - F.start] : NULL;
}
static int documents_available(void)
{
	/* Before the first Finder reply, let the helper admit or decline document mode. */
	return !documents_unsupported && (!F.catalogue_id[0] || !strcmp(F.catalogue_id, "lib-e26"));
}
int pin_doc_mode(void) { return cisco_type >= 0 || ((F.ncategories || F.whole_library) && documents_available()); }
uint32_t pin_doc_count(void) { return F.doc_count; }

static void clear_expansion(void)
{
	expanded.id[0] = 0;
	expanded.start = expanded.total = expanded.count = 0;
	expanded.failure = SECTION_OK;
}
static int visible_sections(void)
{
	/* Keep the last complete page together while its missing neighbor is retried. */
	return expanded.failure == SECTION_OK ? expanded.total : expanded.count;
}
static int section_row(int row)
{
	return expanded.id[0] && row > expanded.row && row - expanded.row <= visible_sections();
}
static int document_row(int row)
{
	return expanded.id[0] && row > expanded.row ? row - visible_sections() : row;
}
static const finder_row *document_at(int row)
{
	return expanded.id[0] && row == expanded.row ? &expanded.document : finder_row_at(document_row(row));
}
static const pin_section *section_at(int row)
{
	int index = row - expanded.row - 1;
	if (expanded.failure == SECTION_OK) index -= expanded.start;
	return index >= 0 && index < expanded.count ? &expanded.page[index] : NULL;
}
static const char *doc_query(const e26_app *a, char *buf, size_t n)
{
	if (pin_doc_mode() && a->edit[0]) {
		sel_cp1252_to_utf8(a->edit, strlen(a->edit), buf, n);
		return buf;
	}
	return F.query;
}
static int doc_title_mode(const e26_app *a)
{
	/* The global quick field looks up document names. Words retains body search. */
	return F.title_mode || (F.whole_library && a->edit[0]);
}
static int visible(const e26_app *a)
{
	return a->cur && (!strncmp(a->cur->name, "pinpointer", 10) || !strncmp(a->cur->name, "finder-", 7));
}
static void doc_diag(const e26_app *a)
{
	const char *path = getenv("E26_PINPOINTER_DUMP");
	FILE *f;
	int i, end;
	if (!path || !*path || strcmp(F.catalogue_id, "lib-e26") || !(f = fopen(path, "a"))) return;
	fprintf(f, "P\t%s\n", F.status);
	end = a->wtop + viewport_rows;
	for (i = a->wtop; i < end; i++) {
		char label[3700], id[1024];
		int level, expander, duplicate;
		const pin_section *section = section_row(i) ? section_at(i) : NULL;
		const finder_row *r = section_row(i) ? &expanded.document : document_at(i);
		if (!r || (*r->marking && strcasecmp(r->marking, "PUBLIC")) ||
		    pin_doc_display(a, i, label, sizeof label, id, sizeof id, &level, &expander, &duplicate)) continue;
		fprintf(f, "R\t%d\t%s\t%s\t%s\n", i, r->id, section ? section->anchor : r->anchor, label);
	}
	fclose(f);
}

static void reply(const lib_reply *r, void *ud)
{
	pin_pending *p = (pin_pending *)ud;
	e26_app *a = p->app;
	int64_t start;
	const jv *grouped = json_get(r->result, "grouped");
	int changed = p->generation[0] && r->generation && strcmp(p->generation, r->generation);
	if (p->serial != F.serial) { free(p); return; }
	request_id = 0; F.busy = 0;
	doc_ready = 0;
	if (changed || (!r->ok && r->code && !strcmp(r->code, "E_STALE"))) {
		F.nrow = F.total = F.universe = a->nview = 0;
		F.generation[0] = F.catalogue_id[0] = 0;
		documents_unsupported = 0;
		clear_expansion();
		free(F.bitmap); F.bitmap = NULL;
		free(last_anchor); last_anchor = NULL;
		last_generation[0] = last_query[0] = 0;
		snprintf(F.status, sizeof F.status, "The Library generation changed. Restart Deep32 Encyclopedia before searching again.");
	} else if (!r->ok && p->documents && r->code && !strcmp(r->code, "E_UNSUPPORTED")) {
		int start = p->start, seek = p->seek;
		/* The same serving helper can still answer the existing title-wheel query. */
		documents_unsupported = 1;
		F.nrow = F.total = F.doc_count = a->nview = 0;
		clear_expansion();
		free(p);
		pin_layout(a);
		fetch(a, start, seek);
		return;
	} else if (!r->ok && p->documents && r->code && !strcmp(r->code, "E_TYPES")) {
		doc_type = FINDER_ALL; F.types_unavailable = 1;
		free(p);
		finder_refresh(a);
		return;
	} else if (!r->ok) {
		F.nrow = F.total = a->nview = 0;
		snprintf(F.status, sizeof F.status, "%s: %s", r->code ? r->code : "Library", r->message ? r->message : "The Library is unavailable.");
	} else if (e26j_i64(json_get(r->result, "start"), 0, INT32_MAX, &start) || (!p->seek && start != p->start) ||
	    (p->documents != (json_get(r->result, "doc_count") != NULL)) ||
	    (grouped && (!p->documents || grouped->type != J_BOOL || !grouped->b)) ||
	    finder_model_reply(&F, r->result, r->generation)) {
		F.nrow = F.total = a->nview = 0;
		snprintf(F.status, sizeof F.status, "The Library returned an invalid Finder page.");
	} else {
		grouped_hits = grouped != NULL;
		a->nview = F.total + (unsigned)visible_sections(); a->have_ww = 1;
		if (p->seek) { a->wtop = a->wsel = F.start; resize_top = -1; }
		if (choose_id[0] && p->serial >= choose_serial) {
			size_t i;
			int more;
			for (i = 0; i < F.nrow && strcmp(F.row[i].id, choose_id); i++) ;
			/* u14 (spec S9): the Browse Panel looks for the open article on the topic's following pages too (up to 16
			 * pages of 64) and shows it a third of the way down, its neighbours around it */
			more = i == F.nrow && choose_pages && choose_pages < 16 && pin_doc_mode() &&
			    (uint32_t)F.start + F.nrow < F.total;
			if (i < F.nrow) {
				a->wtop = a->wsel = F.start + (int)i;
				if (choose_pages) a->wtop = a->wsel > viewport_rows / 3 ? a->wsel - viewport_rows / 3 : 0;
			}
			if (a->verbose && !more) fprintf(stderr, "d32library: pinpointer: %s %s\n", choose_id,
			    i < F.nrow ? "chosen in the list" : "not in the filtered list");
			if (more) {
				choose_pages++;
				fetch(a, F.start + (int)F.nrow, 1);
				choose_serial = F.serial;
			} else {
				if (choose_pages && i == F.nrow && F.start) { choose_id[0] = 0; choose_pages = 0; fetch(a, 0, 1); }
				choose_id[0] = 0; choose_pages = 0;
			}
		}
		if (pin_doc_mode()) { doc_ready = 1; doc_status(a); doc_diag(a); }
		else snprintf(F.status, sizeof F.status, "%u article%s; %.100s", F.total, F.total == 1 ? "" : "s", F.catalogue_id);
		/* A drag may have moved while this page was in flight. Fetch its final viewport once, rather than
		 * cancelling and evaluating Words again for every intermediate pointer or key event. */
		fill_viewport(a);
	}
	pin_layout(a);
	if (visible(a) && a->win) nav_redraw(a);
	free(p);
}
static void fetch(e26_app *a, int start, int seek)
{
	char prefix[sizeof a->edit * 3 + 1], words[FINDER_QUERY], *args, *saved_bitmap;
	const char *query = doc_query(a, words, sizeof words);
	int title_mode = doc_title_mode(a);
	pin_pending *p;
	if (start < 0) start = 0;
	/* Keep a margin in both directions so a single upward key does not move and reload a whole page. */
	if (!seek && start > 0) { start -= (FINDER_PAGE - viewport_rows) / 2; if (start < 0) start = 0; }
	F.serial++;
	if (request_id) { (void)lib_cancel(request_id); request_id = 0; }
	F.busy = 0;
	if (seek && !pin_doc_mode()) sel_cp1252_to_utf8(a->edit, strlen(a->edit), prefix, sizeof prefix);
	else prefix[0] = 0;
	/* The Last Topics Found List is the preceding completed list. Prefix edits and paging must keep that
	 * same input set, even though every reply publishes a newly filtered output bitmap in F.bitmap. */
	if (!query[0] || strcmp(F.scope, "last") || !F.bitmap || !F.generation[0]) {
		free(last_anchor); last_anchor = NULL; last_query[0] = last_generation[0] = 0;
	} else if (!last_anchor || strcmp(last_query, query) || last_title != title_mode ||
	    last_near != F.near_distance || strcmp(last_generation, F.generation)) {
		char *copy = (char *)malloc(strlen(F.bitmap) + 1);
		if (!copy) { snprintf(F.status, sizeof F.status, "The previous result list could not be allocated."); return; }
		memcpy(copy, F.bitmap, strlen(F.bitmap) + 1); free(last_anchor); last_anchor = copy;
		snprintf(last_query, sizeof last_query, "%s", query);
		snprintf(last_generation, sizeof last_generation, "%s", F.generation);
		last_title = title_mode; last_near = F.near_distance;
	}
	saved_bitmap = F.bitmap;
	if (last_anchor && query[0] && !strcmp(F.scope, "last")) F.bitmap = last_anchor;
	args = pin_doc_mode() ? finder_model_document_args(&F, prefix, start, a->art_id, query,
	    title_mode, doc_type, sorts[doc_sort]) :
	    finder_model_args(&F, prefix, start, a->art_id);
	F.bitmap = saved_bitmap;
	if (args && seek && (!pin_doc_mode() || query[0])) {
		e26j_buf b;
		e26j_init(&b); e26j_raw(&b, args, strlen(args) - 1);
		if (pin_doc_mode()) {
			e26j_lit(&b, ",\"viewport\":");
			e26j_i(&b, viewport_rows < FINDER_PAGE ? viewport_rows : FINDER_PAGE);
		} else e26j_lit(&b, ",\"seek\":true");
		e26j_lit(&b, "}");
		free(args); args = e26j_take(&b);
	}
	if (!args || !(p = (pin_pending *)malloc(sizeof *p))) {
		free(args); F.busy = 0;
		snprintf(F.status, sizeof F.status, "The request is incomplete or could not be allocated."); return;
	}
	p->app = a; p->serial = F.serial; p->start = start; p->seek = seek; p->documents = pin_doc_mode();
	snprintf(p->generation, sizeof p->generation, "%s", F.generation);
	F.busy = 1;
	snprintf(F.status, sizeof F.status, "Finding...");
	request_id = lib_request(a, "finder.page", args, reply, p);
	free(args);
	if (!request_id) { free(p); F.busy = 0; snprintf(F.status, sizeof F.status, "The Library helper could not be started."); }
}
static int section_text(const jv *row, const char *key, char *out, size_t cap)
{
	const char *s = json_str(row, key);
	size_t n;
	if (!s || (n = strlen(s)) >= cap || !e26j_utf8(s, n)) return -1;
	memcpy(out, s, n + 1);
	return 0;
}

static void section_failed(e26_app *a, int start, enum section_failure failure)
{
	expanded.failure = failure;
	expanded.retry_start = start;
	a->nview = F.total + (unsigned)visible_sections();
	a->wsel = expanded.row;
	if (a->wsel < a->wtop || a->wsel - a->wtop >= viewport_rows) a->wtop = a->wsel;
	resize_top = -1;
	clamp_viewport(a);
	pin_layout(a);
	doc_diag(a);
	if (visible(a) && a->win) nav_redraw(a);
}

static void section_reply(const lib_reply *r, void *ud)
{
	pin_pending *p = ud;
	e26_app *a = p->app;
	pin_section *page = NULL;
	const jv *rows, *next;
	const char *id, *catalogue;
	int64_t total, start, level, offset, last = -1;
	size_t i;
	enum section_failure failure = SECTION_RETRY;
	if (p->serial != F.serial) { free(p); return; }
	request_id = 0; F.busy = 0;
	if (strcmp(F.generation, p->generation) ||
	    (r->generation && strcmp(r->generation, p->generation)) ||
	    (!r->ok && r->code && !strcmp(r->code, "E_STALE"))) {
		failure = SECTION_STALE;
		goto bad;
	}
	if (!r->ok || !r->generation) goto bad;
	id = json_str(r->result, "id");
	catalogue = json_str(r->result, "catalogue_id");
	if (!id || strcmp(id, expanded.id) || !catalogue || strcmp(catalogue, F.catalogue_id) ||
	    e26j_i64(json_get(r->result, "total"), 0, INT32_MAX - F.total, &total) ||
	    e26j_i64(json_get(r->result, "start"), 0, total, &start) || start != p->start ||
	    !(rows = json_get(r->result, "rows")) || rows->type != J_ARR ||
	    rows->n > FINDER_PAGE || rows->n > (size_t)(total - start) ||
	    !(next = json_get(r->result, "next"))) goto bad;
	if ((start + (int64_t)rows->n < total && (!rows->n ||
	    e26j_i64(next, start + (int64_t)rows->n, start + (int64_t)rows->n, &offset))) ||
	    (start + (int64_t)rows->n == total && next->type != J_NULL)) goto bad;
	if (rows->n && !(page = calloc(rows->n, sizeof *page))) goto bad;
	for (i = 0; i < rows->n; i++) {
		const jv *row = rows->items[i];
		pin_section *s = &page[i];
		if (section_text(row, "anchor", s->anchor, sizeof s->anchor) ||
		    section_text(row, "title", s->title, sizeof s->title) ||
		    e26j_i64(json_get(row, "level"), 1, 6, &level) ||
		    e26j_i64(json_get(row, "block_index"), last + 1, INT32_MAX, &last)) goto bad;
		if (!s->anchor[0]) goto bad;
		if (!s->title[0]) snprintf(s->title, sizeof s->title, "%s", s->anchor);
		s->level = (int)level;
	}
	if (rows->n) memcpy(expanded.page, page, rows->n * sizeof *page);
	free(page);
	if (expanded.failure == SECTION_RETRY && start && rows->n) {
		a->wtop = a->wsel = expanded.row + 1 + (int)start;
		resize_top = -1;
	}
	expanded.failure = SECTION_OK;
	expanded.start = (int)start; expanded.count = (int)rows->n; expanded.total = (int)total;
	a->nview = F.total + (unsigned)expanded.total;
	fill_viewport(a); pin_layout(a); doc_diag(a);
	if (visible(a) && a->win) nav_redraw(a);
	free(p);
	return;
bad:
	free(page);
	section_failed(a, p->start, failure);
	free(p);
}

static void fetch_sections(e26_app *a, int start)
{
	e26j_buf b;
	char *args;
	pin_pending *p;
	if (F.busy || !expanded.id[0] || !F.generation[0]) return;
	e26j_init(&b);
	e26j_lit(&b, "{\"id\":"); e26j_cstr(&b, expanded.id);
	e26j_lit(&b, ",\"start\":"); e26j_i(&b, start);
	if (grouped_hits) {
		char words[FINDER_QUERY];
		e26j_lit(&b, ",\"query\":"); e26j_cstr(&b, doc_query(a, words, sizeof words));
		e26j_lit(&b, ",\"title_mode\":"); e26j_lit(&b, doc_title_mode(a) ? "true" : "false");
		e26j_lit(&b, ",\"near\":"); e26j_i(&b, F.near_distance);
	}
	e26j_lit(&b, ",\"limit\":64}"); args = e26j_take(&b);
	p = args ? calloc(1, sizeof *p) : NULL;
	if (p) {
		p->app = a; p->serial = ++F.serial; p->start = start;
		snprintf(p->generation, sizeof p->generation, "%s", F.generation);
		request_id = lib_request(a, "finder.sections", args, section_reply, p);
	}
	free(args);
	if (request_id) { F.busy = 1; pin_layout(a); return; }
	free(p);
	section_failed(a, start, SECTION_RETRY);
}

static void clamp_viewport(e26_app *a)
{
	int max = (int)a->nview - viewport_rows;
	if (max < 0) max = 0;
	if (a->wtop > max) a->wtop = max;
	if (a->wtop < 0) a->wtop = 0;
	if (a->wsel >= (int)a->nview) a->wsel = a->nview ? (int)a->nview - 1 : 0;
	if (a->wsel < 0) a->wsel = 0;
}

static void fill_viewport(e26_app *a)
{
	int last;
	if (cisco_type >= 0) return;
	clamp_viewport(a);
	last = a->wtop + viewport_rows - 1;
	if (last >= (int)a->nview) last = (int)a->nview - 1;
	if (F.busy || debounce || !a->nview) return;
	if (pin_doc_mode()) {
		int row;
		for (row = a->wtop; row <= last; row++) {
			if (section_row(row)) {
				if (!section_at(row)) {
					int start = row - expanded.row - 1 - (FINDER_PAGE - viewport_rows) / 2;
					fetch_sections(a, start > 0 ? start : 0);
					break;
				}
			} else if (!finder_row_at(document_row(row))) {
				fetch(a, document_row(row), 0); break;
			}
		}
	} else if (!finder_row_at(a->wtop) || !finder_row_at(last)) fetch(a, a->wtop, 0);
}
void finder_refresh(e26_app *a)
{
	int rows;
	if (cisco_type >= 0) {
		char words[FINDER_QUERY];
		const char *query = doc_query(a, words, sizeof words);
		const char *topic = !F.whole_library && F.ncategories == 1 ? F.categories[0] : NULL;
		uint64_t old = request_id;
		request_id = 0;
		F.serial++; F.busy = 0;
		if (old) (void)lib_cancel(old);
		cisco_pin_search(a, cisco_type, cisco_sort, topic, a->flt_cat >= 0 && a->flt_sub < 0, query);
		pin_layout(a);
		return;
	}
	cisco_pin_leave();
	grouped_hits = doc_ready = 0;
	clear_expansion();
	if (pin_doc_mode()) a->nview = F.doc_count = 0;
	F.nrow = 0; a->wtop = a->wsel = 0; resize_top = -1;
	pin_layout(a);
	rows = active_rows(a);
	if (rows) viewport_rows = rows;
	fetch(a, 0, 1);
}
int pin_open(e26_app *a)
{
	if (initialised) finder_model_clear(&F); else finder_model_init(&F);
	initialised = 1; a->have_ww = 1; a->nview = 0;
	doc_type = doc_sort = documents_unsupported = grouped_hits = doc_ready = 0;
	cisco_type = -1;
	clear_expansion();
	a->flt_cat = a->flt_sub = a->pan_hot = -1;
	a->wtop = a->wsel = 0; a->edit[0] = 0;
	viewport_rows = PIN_BASE_ROWS; resize_top = -1;
	/* app.c calls this before widgets exist. Room entry starts the helper. */
	return 0;
}
uint32_t pin_total(const e26_app *a) { (void)a; return F.universe; }
int pin_rows(void) { return viewport_rows; }
void finder_categories(e26_app *a, const char *const *ids, size_t n)
{
	size_t i; (void)a;
	if (!F.whole_library) doc_type = FINDER_ALL;
	memset(F.type_counts, 0, sizeof F.type_counts);
	F.ncategories = n > FINDER_CATEGORIES ? FINDER_CATEGORIES : n;
	for (i = 0; i < F.ncategories; i++) snprintf(F.categories[i], sizeof F.categories[i], "%s", ids[i]);
}
void pin_filter(e26_app *a, int cat, int sub)
{
	const char *id;
	choose_id[0] = 0;
	if (!F.whole_library && documents_available() && (cat != a->flt_cat || sub != a->flt_sub)) {
		a->edit[0] = 0; a->edit_sel = 0; F.query[0] = 0;
	}
	a->flt_cat = a->flt_sub = -1; F.ncategories = 0;
	if (a->have_tax && cat >= 0 && cat < a->tax.ncat) {
		a->flt_cat = cat;
		if (sub >= 0 && sub < a->tax.cat[cat].nsub) { a->flt_sub = sub; id = a->tax.cat[cat].sub[sub].id; }
		else id = a->tax.cat[cat].id;
		finder_categories(a, &id, 1);
	}
	finder_refresh(a);
}
void pin_reset(e26_app *a)
{
	char gen[128], cat[128]; unsigned serial = F.serial + 1;
	char past[FINDER_PAST][FINDER_QUERY]; int npast = F.npast;
	char categories[FINDER_CATEGORIES][128];
	size_t ncategories = documents_available() ? F.ncategories : 0;
	memcpy(past, F.past, sizeof past);
	memcpy(categories, F.categories, ncategories * sizeof *categories);
	snprintf(gen, sizeof gen, "%s", F.generation); snprintf(cat, sizeof cat, "%s", F.catalogue_id);
	finder_model_clear(&F); F.serial = serial;
	memcpy(F.past, past, sizeof past); F.npast = npast;
	snprintf(F.generation, sizeof F.generation, "%s", gen); snprintf(F.catalogue_id, sizeof F.catalogue_id, "%s", cat);
	F.ncategories = ncategories;
	memcpy(F.categories, categories, ncategories * sizeof *categories);
	choose_id[0] = 0;
	a->edit[0] = 0; a->edit_sel = 0; a->pan_hot = -1;
	if (!ncategories) a->flt_cat = a->flt_sub = -1;
	doc_type = doc_sort = 0;
	cisco_type = -1;
	fields_valid = 1; finder_refresh(a);
}
static void typed(e26_app *a, void *ud)
{
	(void)ud;
	if (!debounce) return;
	debounce = 0;
	finder_refresh(a);
	if (visible(a)) nav_redraw(a);
}
void pin_type(e26_app *a)
{
	F.serial++; F.nrow = 0; choose_id[0] = 0;
	if (pin_doc_mode()) F.query[0] = 0;
	if (!debounce) { debounce = 1; timer_after(a, 40, typed, NULL); }
}
void pin_clamp(e26_app *a)
{
	int rows = active_rows(a);
	resize_top = -1;
	if (rows) viewport_rows = rows;
	fill_viewport(a);
}
void finder_scroll(e26_app *a, int delta, int rows)
{
	int max;
	int64_t top = (int64_t)a->wtop + delta;
	if (rows < 1) return;
	resize_top = -1;
	viewport_rows = rows > FINDER_PAGE ? FINDER_PAGE : rows;
	max = (int)a->nview - viewport_rows;
	if (max < 0) max = 0;
	a->wtop = top < 0 ? 0 : top > max ? max : (int)top;
	fill_viewport(a);
}
int pin_entry(const e26_app *a, int row, d32ww_entry *e)
{
	const finder_row *r = finder_row_at(row); (void)a;
	if (!r) return -1;
	memset(e, 0, sizeof *e); e->key = e->display = r->title; e->label = r->id;
	return 0;
}
int pin_find_label(const e26_app *a, const char *label)
{
	size_t i; (void)a;
	for (i = 0; i < F.nrow; i++) if (!strcmp(F.row[i].id, label)) return F.start + (int)i;
	return -1;
}
/* Today's reader has this id on disc under one of art_load's names (<id>.art; the scheme's ':' as '-', as the
 * fixtures name wiki-GRE.art; a corpus doc_key's first section, rfc2328-s1.art), so art_load opens it at once. Any
 * other id art_load fetches from the Library asynchronously (it returns 1, accepted), and a fetch that fails raises
 * the reader's own notice: over the stopgap's notice below, where nav_notice_text replaces the window stack, so
 * closing it showed the main screen instead of the Pinpointer. Library titles open through ARTICLE's title.open
 * (CR-F3), which can open them behind the Pinpointer when they arrive. */
static int on_disc(const e26_app *a, const char *id)
{
	char path[1200];
	const char *colon = strchr(id, ':');
	FILE *f;
	int i, n;
	if (!a->article_dir || !id[0] || strchr(id, '/') || strchr(id, '\\') || id[0] == '.') return 0;
	for (i = 0; i < 3; i++) {
		if (i == 0) n = snprintf(path, sizeof path, "%s/%s.art", a->article_dir, id);
		else if (i == 1) n = colon ? snprintf(path, sizeof path, "%s/%.*s-%s.art", a->article_dir, (int)(colon - id), id, colon + 1) : -1;
		else n = snprintf(path, sizeof path, "%s/%s-s1.art", a->article_dir, id);
		if (n > 0 && (size_t)n < sizeof path && (f = fopen(path, "rb")) != NULL) { fclose(f); return 1; }
	}
	return 0;
}
int pin_doc_expand(e26_app *a, int row, int open)
{
	int index = document_row(row);
	const finder_row *r = document_at(row);
	char words[FINDER_QUERY];
	if (!pin_doc_mode() || (doc_query(a, words, sizeof words)[0] && !grouped_hits)) return 0;
	if (!open) {
		if (!expanded.id[0] || (row != expanded.row && !section_row(row))) return 0;
		if (request_id) { F.serial++; (void)lib_cancel(request_id); request_id = 0; F.busy = 0; }
		a->wsel = expanded.row;
		if (a->wtop > a->wsel) a->wtop = a->wsel;
		clear_expansion();
		a->nview = F.total;
		pin_clamp(a);
		pin_layout(a);
		doc_diag(a);
		return 1;
	}
	if (section_row(row) || !r || !r->has_sections || (grouped_hits && !r->anchor[0]) || F.busy) return 0;
	if (expanded.id[0] && row == expanded.row) {
		if (expanded.failure == SECTION_RETRY && !expanded.count) fetch_sections(a, expanded.retry_start);
		else if (visible_sections()) {
			a->wsel = row + 1;
			if (a->wsel >= a->wtop + pin_rows()) a->wtop++;
			pin_clamp(a);
		}
		return 1;
	}
	clear_expansion();
	expanded.document = *r;
	snprintf(expanded.id, sizeof expanded.id, "%s", r->id);
	expanded.row = a->wsel = index;
	a->nview = F.total;
	resize_top = -1;
	if (a->wtop > index) a->wtop = index;
	fetch_sections(a, 0);
	return 1;
}

typedef struct {
	e26_app *app;
	unsigned finder_serial, serial;
	char id[sizeof F.row[0].id], anchor[sizeof F.row[0].anchor], generation[sizeof F.generation];
	char marking[sizeof F.row[0].marking];
} pin_open_pending;

static void doc_unavailable(e26_app *a)
{
	nav_notice_text(a, "Open Library", "This item is unavailable in the current public Library.");
}

int pin_have_results(void) { return results.valid || cisco_have_results(); }
void pin_forget_legacy_results(void) { results.valid = 0; }

void pin_cisco_remember(const e26_app *a)
{
	cisco_saved_type = cisco_type;
	cisco_saved_sort = cisco_sort;
	cisco_saved_whole = F.whole_library;
	cisco_saved_cat = a->flt_cat;
	cisco_saved_sub = a->flt_sub;
	cisco_saved_ncategories = F.ncategories;
	memcpy(cisco_saved_categories, F.categories, sizeof F.categories);
	memcpy(cisco_saved_query, a->edit, sizeof a->edit);
}

void pin_cisco_restored(e26_app *a)
{
	cisco_type = cisco_saved_type;
	cisco_sort = cisco_saved_sort;
	F.whole_library = cisco_saved_whole;
	F.ncategories = cisco_saved_ncategories;
	memcpy(F.categories, cisco_saved_categories, sizeof F.categories);
	a->flt_cat = cisco_saved_cat;
	a->flt_sub = cisco_saved_sub;
	memcpy(a->edit, cisco_saved_query, sizeof a->edit);
	a->edit_sel = 0;
	pin_layout(a);
	nav_go(a, "pinpointer", 0);
}

static void remember_results(const e26_app *a)
{
	char *bitmap = F.bitmap ? strdup(F.bitmap) : NULL;
	char *prior = last_anchor ? strdup(last_anchor) : NULL;
	cisco_forget_results();

	free(results.model.bitmap);
	free(results.prior);
	results.model.bitmap = results.prior = NULL;
	results.valid = 0;
	if ((F.bitmap && !bitmap) || (last_anchor && !prior)) {
		free(bitmap);
		free(prior);
		return;
	}
	results.model = F;
	results.model.bitmap = bitmap;
	results.model.busy = 0;
	results.expansion = expanded;
	results.prior = prior;
	memcpy(results.prior_query, last_query, sizeof last_query);
	memcpy(results.prior_generation, last_generation, sizeof last_generation);
	results.prior_title = last_title;
	results.prior_near = last_near;
	memcpy(results.edit, a->edit, sizeof a->edit);
	results.top = a->wtop;
	results.selected = a->wsel;
	results.cat = a->flt_cat;
	results.sub = a->flt_sub;
	results.type = doc_type;
	results.sort = doc_sort;
	results.grouped = grouped_hits;
	results.valid = 1;
}

static int act_return_results(e26_app *a, const e26_actx *x)
{
	char *bitmap, *prior;
	uint64_t pending;
	unsigned serial;
	scr_control *list;
	(void)x;
	if (cisco_return_results(a)) return 1;

	if (!results.valid || !a->cur || strcmp(a->cur->name, "article")) return 0;
	if (strcmp(F.generation, results.model.generation)) {
		results.valid = 0;
		pin_reset(a);
		nav_go(a, "pinpointer", 0);
		return 1;
	}
	bitmap = results.model.bitmap ? strdup(results.model.bitmap) : NULL;
	prior = results.prior ? strdup(results.prior) : NULL;
	if ((results.model.bitmap && !bitmap) || (results.prior && !prior)) {
		free(bitmap);
		free(prior);
		nav_notice_text(a, "Return to Results", "The saved results could not be opened.");
		return 0;
	}
	serial = ++F.serial;
	pending = request_id;
	request_id = 0;
	if (pending) (void)lib_cancel(pending);
	++open_serial;
	pending = open_request;
	open_request = 0;
	if (pending) (void)lib_cancel(pending);
	free(F.bitmap);
	free(last_anchor);
	F = results.model;
	F.bitmap = bitmap;
	F.serial = serial;
	expanded = results.expansion;
	last_anchor = prior;
	memcpy(last_query, results.prior_query, sizeof last_query);
	memcpy(last_generation, results.prior_generation, sizeof last_generation);
	last_title = results.prior_title;
	last_near = results.prior_near;
	memcpy(a->edit, results.edit, sizeof a->edit);
	a->edit_sel = 0;
	a->wtop = results.top;
	a->wsel = results.selected;
	a->flt_cat = results.cat;
	a->flt_sub = results.sub;
	a->nview = F.total + (unsigned)visible_sections();
	doc_type = results.type;
	doc_sort = results.sort;
	cisco_type = -1;
	cisco_pin_leave();
	grouped_hits = results.grouped;
	doc_ready = fields_valid = 1;
	documents_unsupported = debounce = 0;
	resize_top = -1;
	choose_id[0] = 0;
	nav_go(a, "pinpointer", 0);
	if (a->cur && (list = scr_control_find(a->cur, "list")) != NULL)
		focus_set(a, a->cur, (int)(list - a->cur->c), 1);
	doc_diag(a);
	return 1;
}

static void doc_open_reply(const lib_reply *r, void *ud)
{
	pin_open_pending *p = ud;
	e26_app *a = p->app;
	const jv *pack = json_get(r->result, "pack");
	const char *name = json_str(pack, "name"), *handling = json_str(pack, "handling");
	const char *marking = json_str(pack, "marking"), *known = ar_lib_generation(0);
	const char *library;
	int reader_pack = 0;
	if (p->serial != open_serial) { free(p); return; }
	open_request = 0; open_serial++;
	if (p->finder_serial != F.serial || !visible(a)) { free(p); return; }
	if (!r->ok || !r->generation || strcmp(r->generation, p->generation) ||
	    strcmp(F.generation, p->generation) || strcmp(F.catalogue_id, "lib-e26") ||
	    !name || strcmp(name, "lib-e26") || (handling && strcmp(handling, "PUBLIC")) ||
	    (marking && *marking && strcmp(marking, "PUBLIC")) ||
	    (*known && strcmp(known, p->generation)) ||
	    (p->anchor[0] && !finder_model_public_section(name, p->generation, r->generation, p->marking, p->anchor))) {
		doc_unavailable(a); free(p); return;
	}
	if (strcmp(F.catalogue_id, "lib-e26") || strcmp(F.generation, p->generation)) {
		doc_unavailable(a); free(p); return;
	}
	/* The installed reader channel carries the verified catalogue name. An override shares this helper. */
	library = getenv("E26_LIB_PACK");
	if (!library || !strcmp(library, "/d32/libcontent/e26")) {
		reader_pack = ar_lib_resolve(name);
		known = ar_lib_generation(reader_pack);
		if (reader_pack <= 0 || (*known && strcmp(known, p->generation))) {
			doc_unavailable(a); free(p); return;
		}
	}
	{
		const char *path = getenv("E26_PINPOINTER_DUMP");
		FILE *f = path && *path ? fopen(path, "a") : NULL;
		if (f) { fprintf(f, "O\t%s\t%s\t%d\n", p->id, p->anchor, reader_pack); fclose(f); }
	}
	remember_results(a);
	if (set_get_bool("MinimisePinpointerAfterSelect", 0) && a->cur && a->cur->over[0]) nav_close_overlay(a);
	art_open_pack(a, reader_pack, p->id, p->anchor[0] ? p->anchor : NULL);
	free(p);
}

static void doc_open(e26_app *a, const finder_row *r, const char *anchor)
{
	pin_open_pending *p;
	uint64_t id, previous = open_request;
	unsigned serial = ++open_serial;
	open_request = 0;
	if (previous) (void)lib_cancel(previous);
	if (strcmp(F.catalogue_id, "lib-e26") || !F.generation[0] ||
	    (*r->marking && strcasecmp(r->marking, "PUBLIC")) ||
	    (anchor && !finder_model_public_section(F.catalogue_id, F.generation, F.generation, r->marking, anchor))) {
		doc_unavailable(a); return;
	}
	if (!(p = malloc(sizeof *p))) { doc_unavailable(a); return; }
	p->app = a; p->finder_serial = F.serial; p->serial = serial;
	snprintf(p->id, sizeof p->id, "%s", r->id);
	snprintf(p->anchor, sizeof p->anchor, "%s", anchor ? anchor : "");
	snprintf(p->generation, sizeof p->generation, "%s", F.generation);
	snprintf(p->marking, sizeof p->marking, "%s", r->marking);
	id = lib_request(a, "status", "{}", doc_open_reply, p);
	/* A write failure can deliver the callback synchronously. */
	if (serial != open_serial) return;
	open_request = id;
	if (!id) { free(p); doc_unavailable(a); }
}

void pin_open_entry(e26_app *a, int row)
{
	const pin_section *section = pin_doc_mode() && section_row(row) ? section_at(row) : NULL;
	const finder_row *r = section ? &expanded.document :
	    section_row(row) ? NULL : document_at(row);
	finder_row selected;
	char catalogue_id[sizeof F.catalogue_id], generation[sizeof F.generation];
	e26_actx x = { .ctl = E26_CTL_FINDER_TITLE };
	int act;
	if (!r) return;
	if (pin_doc_mode()) {
		doc_open(a, r, section ? section->anchor : r->anchor[0] ? r->anchor : NULL);
		return;
	}
	if (r->catalogue_only) { nav_notice_text(a, "Catalogue", "This catalogue entry has no installed article."); return; }
	if (!F.catalogue_id[0]) {
		/* Without a serving catalogue, preserve the local article fallback and its overlay behaviour. */
		int disc = on_disc(a, r->id);
		if (disc && a->cur && a->cur->over[0] && !set_get_bool("MinimisePinpointerAfterSelect", 0)) {
			e26_hist snap;
			nav_snapshot(a, &snap);
			if (!art_load(a, r->id)) {
				a->scroll = 0;
				sel_utf8_to_cp1252(r->title, strlen(r->title), a->edit, sizeof a->edit);
				a->edit_sel = 1; a->wsel = row;
				nav_base_switch_from(a, "article", &snap);
				return;
			}
		} else if (disc) {
			art_open(a, r->id);
			if (a->cur && !strcmp(a->cur->name, "article")) return;
		}
		nav_notice(a, "Article reader");
		return;
	}
	act = reg_find(REG_ACTION, "title.open");
	if (act < 0 || !action_built(act)) {
		nav_notice_text(a, "Open Library", "This Library pack is unavailable.");
		return;
	}
	/* Capture the selected row and serving identity before closing an overlay can dispatch screen events. */
	selected = *r;
	memcpy(catalogue_id, F.catalogue_id, sizeof catalogue_id);
	memcpy(generation, F.generation, sizeof generation);
	x.arg = selected.id; x.label = selected.title; x.marking = selected.marking;
	x.catalogue_id = catalogue_id; x.generation = generation;
	sel_utf8_to_cp1252(selected.title, strlen(selected.title), a->edit, sizeof a->edit);
	a->edit_sel = 1; a->wsel = row;
	if (set_get_bool("MinimisePinpointerAfterSelect", 0) && a->cur && a->cur->over[0]) nav_close_overlay(a);
	if (action_run(a, act, &x)) nav_redraw(a);
}
void pin_open_filtered(e26_app *a, int cat, int sub)
{
	a->edit[0] = 0; a->edit_sel = 0; a->pan_hot = cat;
	if (documents_available()) F.query[0] = 0;
	catpanel_reveal(a, cat); pin_filter(a, cat, sub);
	/* A subcategory whose taxonomy names an article (OSPF: RFC 2328) has that title chosen in the list when the
	 * filtered page arrives, so that Return opens it (build 2, the Category menu's route) */
	if (a->have_tax && cat >= 0 && cat < a->tax.ncat && sub >= 0 && sub < a->tax.cat[cat].nsub && a->tax.cat[cat].sub[sub].article[0]) {
		snprintf(choose_id, sizeof choose_id, "%s", a->tax.cat[cat].sub[sub].article);
		choose_serial = F.serial;
		choose_pages = 0;
	}
	nav_go(a, "pinpointer", 0);
}
static void pasted(e26_app *a, const char *text, size_t n, const sel_prov *prov, void *ud)
{
	(void)prov; (void)ud;
	if (!text || !a->cur || strncmp(a->cur->name, "pinpointer", 10)) return;
	if (n >= sizeof a->edit) n = sizeof a->edit - 1;
	memcpy(a->edit, text, n); a->edit[n] = 0; a->edit_sel = 0; pin_type(a); nav_redraw(a);
}
/* u14 (spec M16): an RFC or an Internet-Draft named by number in the field ("RFC 4271", "rfc-0793", "RFC4271";
 * "draft-ietf-idr-bgp4-26") opens at Return whatever the Type filter is. The Library is asked for the article first
 * (one block): when it holds it, it opens as a list title does; when it does not, the field's words are searched as
 * usual with Type RFC (Internet-Draft for a draft name). */
static int sort_picked;   /* the reader chose a sort this session: Type no longer picks one (M16) */
static uint64_t number_request;
static unsigned number_serial;
typedef struct { e26_app *app; unsigned serial; int type, words; char id[160]; } number_pending;

static int typed_document(const char *text, char *id, size_t n, int *type)
{
	const char *p = text, *digits, *end;
	size_t len, i;
	while (*p == ' ' || *p == '\t') p++;
	if (!strncasecmp(p, "rfc", 3)) {
		for (p += 3; *p == ' ' || *p == '\t' || *p == '-'; p++) ;
		while (*p == '0' && p[1] >= '0' && p[1] <= '9') p++;
		digits = p;
		len = strspn(p, "0123456789");
		for (end = p + len; *end == ' ' || *end == '\t'; end++) ;
		if (len < 1 || len > 5 || *end) return 0;
		snprintf(id, n, "rfc%.*s", (int)len, digits);
		*type = FINDER_RFC;
		return 1;
	}
	if (!strncasecmp(p, "draft-", 6)) {
		len = strspn(p, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-");
		for (end = p + len; *end == ' ' || *end == '\t'; end++) ;
		if (len <= 6 || len > 140 || *end) return 0;
		snprintf(id, n, "draft:%.*s", (int)len, p);
		for (i = 6; id[i]; i++) if (id[i] >= 'A' && id[i] <= 'Z') id[i] = (char)(id[i] - 'A' + 'a');
		*type = FINDER_DRAFT;
		return 1;
	}
	return 0;
}

static void set_doc_type(int type)
{
	cisco_type = -1;
	doc_type = type;
	if (!sort_picked) doc_sort = type == FINDER_RFC; /* RFCs by number unless the reader chose a sort (M16) */
}

static void search_words(e26_app *a)
{
	/* A deliberate Find starts a new Last request. Paging and type-ahead retain its anchor. */
	free(last_anchor); last_anchor = NULL;
	last_query[0] = last_generation[0] = 0;
	finder_model_remember(&F); a->edit[0] = 0; a->edit_sel = 0; finder_refresh(a);
}

static void number_reply(const lib_reply *r, void *ud)
{
	number_pending *p = ud;
	e26_app *a = p->app;
	if (p->serial != number_serial) { free(p); return; }
	number_request = 0; ++number_serial;
	if (r->ok) {
		if (a->verbose) fprintf(stderr, "d32library: pinpointer: %s opened by number\n", p->id);
		if (visible(a)) {
			remember_results(a);
			if (set_get_bool("MinimisePinpointerAfterSelect", 0) && a->cur && a->cur->over[0]) nav_close_overlay(a);
		}
		art_open_pack(a, -1, p->id, NULL);
	} else {
		if (a->verbose)
			fprintf(stderr, "d32library: pinpointer: %s is not held; searching Type %s\n", p->id, finder_type_label(p->type));
		if (!F.ncategories && documents_available()) F.whole_library = 1;
		set_doc_type(p->type);
		if (p->words) search_words(a); else finder_refresh(a);
		if (visible(a) && a->win) nav_redraw(a);
	}
	free(p);
}

/* 1 when text names an RFC or a draft by number (the request is under way), 0 to go on as before */
static int open_typed_document(e26_app *a, const char *text, int words)
{
	char id[160];
	int type;
	number_pending *p;
	e26j_buf b;
	char *args;
	uint64_t pending = number_request, id_request;
	unsigned serial;
	if (!text || !typed_document(text, id, sizeof id, &type)) return 0;
	number_request = 0;
	++number_serial;
	if (pending) (void)lib_cancel(pending);
	if (!(p = calloc(1, sizeof *p))) return 0;
	p->app = a; p->serial = number_serial; p->type = type; p->words = words;
	snprintf(p->id, sizeof p->id, "%s", id);
	e26j_init(&b);
	e26j_lit(&b, "{\"id\":"); e26j_cstr(&b, id); e26j_lit(&b, ",\"limit\":1}");
	if (!(args = e26j_take(&b))) { free(p); return 0; }
	serial = number_serial;
	id_request = lib_request(a, "article", args, number_reply, p);
	free(args);
	/* a write failure can answer synchronously: number_reply ran (and freed p) */
	if (serial != number_serial) return 1;
	number_request = id_request;
	if (!id_request) { free(p); return 0; }
	return 1;
}

int pin_key(e26_app *a, XKeyEvent *ev, KeySym ks)
{
	char ch[8]; int n, step = 0, fi = focus_get(a, a->cur); size_t len = strlen(a->edit);
	/* F0 offers only the first key_focus kind: route to INPUT's focused field. */
	if (fi >= 0 && fi < a->cur->nc && a->cur->c[fi].kind == reg_find(REG_KIND, EDIT_KIND)) {
		const kind_ops *k = kind_of(a->cur->c[fi].kind);
		return k && k->key_focus ? k->key_focus(a, a->cur, fi, ev, ks) : 0;
	}
	if (((ev->state & ControlMask) && (ks == XK_v || ks == XK_V)) || ((ev->state & ShiftMask) && ks == XK_Insert)) {
		(void)sel_request(a, SEL_CLIPBOARD, pasted, NULL); return 1;
	}
	if (ev->state & (ControlMask | Mod1Mask)) return 0;
	if ((ks == XK_Return || ks == XK_KP_Enter) && open_typed_document(a, a->edit, 0)) { nav_redraw(a); return 1; }
	if (cisco_type >= 0 && cisco_pin_key(a, ks)) { nav_redraw(a); return 1; }
	n = XLookupString(ev, ch, sizeof ch - 1, NULL, NULL);
	if (pin_doc_mode() && (ks == XK_Right || ks == XK_KP_Right || ks == XK_plus || ks == XK_KP_Add)) {
		if (pin_doc_expand(a, a->wsel, 1)) { nav_redraw(a); return 1; }
		if (ks != XK_plus) return 1;
	}
	if (pin_doc_mode() && (ks == XK_Left || ks == XK_KP_Left || ks == XK_minus || ks == XK_KP_Subtract)) {
		if (pin_doc_expand(a, a->wsel, 0)) { nav_redraw(a); return 1; }
		if (ks != XK_minus) return 1;
	}
	if (ks == XK_BackSpace) { if (a->edit_sel) a->edit[0] = 0; else if (len) a->edit[len - 1] = 0; a->edit_sel = 0; pin_type(a); }
	else if (ks == XK_Return || ks == XK_KP_Enter) { pin_open_entry(a, a->wsel); return 1; }
	else if (ks == XK_Up) step = -1;
	else if (ks == XK_Down) step = 1;
	else if (ks == XK_Prior) step = -(pin_rows() - 1);
	else if (ks == XK_Next) step = pin_rows() - 1;
	else if (n == 1 && (unsigned char)ch[0] >= 32 && ch[0] != 127 && (a->edit_sel || len + 1 < sizeof a->edit)) {
		if (a->edit_sel) { a->edit[0] = 0; len = 0; a->edit_sel = 0; }
		a->edit[len] = ch[0]; a->edit[len + 1] = 0; pin_type(a);
	} else return 0;
	if (step) {
		a->wsel += step;
		if (a->wsel < a->wtop) a->wtop = a->wsel;
		if (a->wsel >= a->wtop + pin_rows()) a->wtop = a->wsel - pin_rows() + 1;
		pin_clamp(a);
	}
	nav_redraw(a); return 1;
}
static void utf8_field(const char *t, char *out, size_t n) { sel_cp1252_to_utf8(t, strlen(t), out, n); }
void finder_begin_fields(void) { fields_valid = 1; fields_reason = 0; }
void finder_fields(e26_app *a, const scr_screen *s)
{
	const char *t, *from, *to; char query[FINDER_QUERY], part[FINDER_QUERY]; size_t used = 0, scalars = 0; int i, lo, hi, slot;
	(void)a;
	if (!s) return;
	if ((t = field_text(s, "box1")) != NULL) {
		slot = join_slot(s);
		query[0] = 0;
		for (i = 1; i <= 3; i++) {
			const char *op = slot >= 0 && i > 1 && joins[slot][i - 2][0] ? joins[slot][i - 2] : "and";
			size_t olen = strlen(op) + 2;
			char id[16]; snprintf(id, sizeof id, "box%d", i); t = field_text(s, id);
			if (!t || !t[0]) continue;
			scalars += strlen(t) + (used ? olen : 0);
			if (scalars > 127) { fields_valid = 0; fields_reason = 20101; break; }
			utf8_field(t, part, sizeof part);
			if (used + strlen(part) + (used ? olen : 0u) >= sizeof query) { fields_valid = 0; break; }
			if (used) { query[used++] = ' '; memcpy(query + used, op, strlen(op)); used += strlen(op); query[used++] = ' '; }
			memcpy(query + used, part, strlen(part) + 1); used += strlen(part);
		}
		if (fields_valid) snprintf(F.query, sizeof F.query, "%s", query);
	}
	from = field_text(s, "year-from"); to = field_text(s, "year-to");
	if (from || to) {
		if ((!from || !from[0]) && (!to || !to[0])) F.years = 0;
		else if ((from && from[0] ? finder_year(from, &lo) : (lo = -100000, 0)) ||
		    (to && to[0] ? finder_year(to, &hi) : (hi = 2030, 0)) || lo > hi) fields_valid = 0;
		else { F.years = 1; F.year_min = lo; F.year_max = hi; }
	}
	if ((t = field_text(s, "place-id")) != NULL) utf8_field(t, F.place, sizeof F.place);
	if ((t = field_text(s, "near-value")) != NULL && t[0]) {
		char *end; long value = strtol(t, &end, 10);
		if (*end || value < 1 || value > 9999) fields_valid = 0; else F.near_distance = (int)value;
	}
}
void finder_search(e26_app *a, const scr_screen *s)
{
	/* A wizard may have collected several pages immediately before this call. */
	if (s) finder_begin_fields();
	finder_fields(a, s);
	if (!fields_valid) {
		const char *message = fields_reason && a->have_str ? rec_string(&a->str, fields_reason) : NULL;
		nav_notice_text(a, "Find", message ? message : fields_reason == 20101 ? "The request exceeds the 127-character limit." : "Check the request and enter a valid date range or NEAR value.");
		finder_begin_fields(); return;
	}
	if (s && open_typed_document(a, F.query, 1)) return; /* "RFC 4271" in the Words box (M16) */
	search_words(a);
}
void finder_year_range(e26_app *a, int from, int to)
{
	char text[32]; int i;
	if (!from) from = 1;
	if (!to) to = 1;
	if (from > to) { int t = from; from = to; to = t; }
	F.years = 1; F.year_min = from; F.year_max = to;
	for (i = 0; i < a->set.ns; i++) {
		snprintf(text, sizeof text, "%d", from); (void)field_set_text(&a->set.s[i], "year-from", text);
		snprintf(text, sizeof text, "%d", to); (void)field_set_text(&a->set.s[i], "year-to", text);
	}
	finder_refresh(a);
}
static int act_reset(e26_app *a, const e26_actx *x)
{
	int i; (void)x;
	for (i = 0; i < a->set.ns; i++) {
		scr_screen *s = &a->set.s[i];
		if (strncmp(s->name, "pinpointer", 10) && strncmp(s->name, "findwiz", 7)) continue;
		(void)field_set_text(s, "box1", ""); (void)field_set_text(s, "box2", ""); (void)field_set_text(s, "box3", "");
		(void)field_set_text(s, "year-from", ""); (void)field_set_text(s, "year-to", ""); (void)field_set_text(s, "place-id", "");
		{
			int k;
			for (k = 0; k < s->nc; k++) if (s->c[k].state[0]) {
				int *v = state_shared(s->c[k].state);
				if (v) *v = !strcmp(s->c[k].state, "scope-all");
				s->c[k].checked = !strcmp(s->c[k].state, "scope-all");
			}
		}
	}
	memset(joins, 0, sizeof joins);
	pin_reset(a); return 1;
}
static int act_search(e26_app *a, const e26_actx *x) { finder_search(a, x->s); return 1; }
static int act_op(e26_app *a, const e26_actx *x)
{
	const scr_control *c = x->s && x->ctl >= 0 ? &x->s->c[x->ctl] : NULL;
	const char *op = c ? c->id : x->arg; char id[16]; int n = 1, slot;
	const char *word;
	if (!x->s || !op) return 0;
	word = strstr(op, "near") ? "near" : strstr(op, "not") ? "not" : strstr(op, "or") ? "or" : "and";
	if (strchr(op, '2')) n = 2;
	slot = join_slot(x->s);
	if (slot >= 0) snprintf(joins[slot][n - 1], sizeof joins[slot][n - 1], "%s", word);
	{
		int i;
		for (i = 0; i < x->s->nc; i++) {
			const char *which = scr_prop_get(&x->s->c[i], "join"), *value = scr_prop_get(&x->s->c[i], "operator");
			if (which && value && atoi(which) == n) x->s->c[i].checked = !strcmp(value, word);
		}
	}
	snprintf(id, sizeof id, "box%d", n + 1);
	{ scr_control *f = scr_control_find(x->s, id); if (f) focus_set(a, x->s, (int)(f - x->s->c), 1); }
	return 1;
}
static int media_bit(const char *name)
{
	int i; for (i = 0; i < 8; i++) if (name && !strcmp(name, media_names[i])) return i; return -1;
}
static int act_media(e26_app *a, const e26_actx *x) { int b = media_bit(x->arg); if (b >= 0) { F.media ^= 1u << b; finder_refresh(a); } return 1; }
static int act_scope(e26_app *a, const e26_actx *x)
{
	if (!strcmp(x->arg, "Apply hints")) { finder_search(a, x->s); return 1; }
	if (!strcmp(x->arg, "all") || !strcmp(x->arg, "last") || !strcmp(x->arg, "current")) snprintf(F.scope, sizeof F.scope, "%s", x->arg);
	else if (!strcmp(x->arg, "title")) F.title_mode = !F.title_mode;
	return 1;
}
static int act_time(e26_app *a, const e26_actx *x)
{
	const char *id = !strncmp(x->arg, "from-", 5) ? "year-from" : "year-to";
	const char *t = x->s ? field_text(x->s, id) : NULL; int year; char value[32];
	if ((!strcmp(x->arg, "from-bc") || !strcmp(x->arg, "from-ad") || !strcmp(x->arg, "to-bc") || !strcmp(x->arg, "to-ad")) && t && t[0] && !finder_year(t, &year)) {
		if (year < 0) year = -year;
		if (strstr(x->arg, "-bc")) year = -year;
		snprintf(value, sizeof value, "%d", year); (void)field_set_text(x->s, id, value);
	}
	finder_search(a, x->s); return 1;
}
static int act_place(e26_app *a, const e26_actx *x)
{
	(void)x; nav_notice_text(a, "Protocol places", "Enter an installed protocol-map place identifier. The map browser is coming in a later build."); return 0;
}
static int act_catalogue(e26_app *a, const e26_actx *x) { (void)x; F.catalogue = !F.catalogue; finder_refresh(a); return 1; }
static int act_past(e26_app *a, const e26_actx *x) { (void)x; (void)nav_push_window(a, "finder-past", 0); return 0; }
static int act_hints(e26_app *a, const e26_actx *x) { (void)x; (void)nav_push_window(a, "finder-hints", 0); return 0; }
static int act_lists(e26_app *a, const e26_actx *x) { (void)x; (void)nav_push_window(a, "finder-lists", 0); return 0; }
static int act_delete_past(e26_app *a, const e26_actx *x) { (void)a; (void)x; memset(F.past, 0, sizeof F.past); F.npast = 0; return 1; }
static int list_name(const e26_actx *x, char *out, size_t n)
{
	const char *t = x->s ? field_text(x->s, "list-name") : NULL;
	if (!t || !t[0]) t = "last.eli";
	if (strlen(t) >= n) return -1;
	snprintf(out, n, "%s", t); return 0;
}
static int act_save(e26_app *a, const e26_actx *x)
{
	char name[80], dir[1200], err[256];
	if (F.busy || !F.generation[0]) { nav_notice_text(a, "Save List", "Wait for the Library to identify this catalogue."); return 0; }
	if (list_name(x, name, sizeof name) || snprintf(dir, sizeof dir, "%s/lists", state_dir()) >= (int)sizeof dir ||
	    finder_model_save(&F, dir, name, err, sizeof err)) { nav_notice_text(a, "Save List", "The list could not be saved. Use a simple filename in your lists folder."); return 0; }
	snprintf(F.status, sizeof F.status, "Saved %.80s", name); return 1;
}
static int act_load(e26_app *a, const e26_actx *x)
{
	char name[80], dir[1200], err[256]; finder_model *saved = (finder_model *)malloc(sizeof *saved);
	if (!saved) return 0;
	finder_model_init(saved);
	if (list_name(x, name, sizeof name) || snprintf(dir, sizeof dir, "%s/lists", state_dir()) >= (int)sizeof dir || finder_model_load(saved, dir, name, err, sizeof err)) {
		finder_model_clear(saved); free(saved); nav_notice_text(a, "Open List", "The list could not be opened. Check its name and format."); return 0;
	}
	if (!F.generation[0] || strcmp(saved->generation, F.generation) || strcmp(saved->catalogue_id, F.catalogue_id)) {
		finder_model_clear(saved); free(saved); nav_notice_text(a, "Open List", "This list belongs to another catalogue generation. Run a new search before saving it again."); return 0;
	}
	if ((!strcmp(saved->scope, "last") && !F.bitmap) || (!strcmp(saved->scope, "current") && !a->art_id[0])) {
		finder_model_clear(saved); free(saved);
		nav_notice_text(a, "Open List", "This scoped request needs a current article or an existing Last Topics Found list."); return 0;
	}
	snprintf(F.query, sizeof F.query, "%s", saved->query); snprintf(F.scope, sizeof F.scope, "%s", saved->scope);
	F.title_mode = saved->title_mode; F.near_distance = saved->near_distance;
	F.ncategories = saved->ncategories; memcpy(F.categories, saved->categories, sizeof F.categories);
	F.whole_library = 0; doc_type = FINDER_ALL; doc_sort = 0; cisco_type = -1;
	F.media = saved->media; F.years = saved->years; F.year_min = saved->year_min; F.year_max = saved->year_max;
	snprintf(F.place, sizeof F.place, "%s", saved->place); F.catalogue = saved->catalogue;
	finder_model_clear(saved); free(saved);
	{
		int i; char value[512];
		memset(joins, 0, sizeof joins);
		for (i = 0; i < a->set.ns; i++) {
			scr_screen *s = &a->set.s[i];
			sel_utf8_to_cp1252(F.query, strlen(F.query), value, sizeof value);
			(void)field_set_text(s, "box1", value); (void)field_set_text(s, "box2", ""); (void)field_set_text(s, "box3", "");
			if (F.years) snprintf(value, sizeof value, "%d", F.year_min); else value[0] = 0;
			(void)field_set_text(s, "year-from", value);
			if (F.years) snprintf(value, sizeof value, "%d", F.year_max); else value[0] = 0;
			(void)field_set_text(s, "year-to", value);
			sel_utf8_to_cp1252(F.place, strlen(F.place), value, sizeof value); (void)field_set_text(s, "place-id", value);
			snprintf(value, sizeof value, "%d", F.near_distance); (void)field_set_text(s, "near-value", value);
		}
	}
	finder_begin_fields(); finder_search(a, NULL); return 1;
}
static int act_packs(e26_app *a, const e26_actx *x)
{
	(void)x;
	if (F.ncategories != 1) F.whole_library = 1;
	if (cisco_type < 0) cisco_sort = doc_sort == 1;
	cisco_type = 0; finder_refresh(a);
	if (a->cur && strcmp(a->cur->name, "pinpointer")) nav_go(a, "pinpointer", 0);
	return 1;
}
static int act_timeline(e26_app *a, const e26_actx *x)
{
	(void)x; nav_notice_text(a, "Time scale", "Dragging and typed BC/AD years filter the list. The timeline picture will be updated to match the scale ending at 2030."); return 0;
}
static void doc_topic(const e26_app *a, char *buf, size_t n)
{
	const tax_cat *cat;
	char *p;
	size_t i, used = 0;
	if (F.whole_library) { snprintf(buf, n, "Entire Library"); return; }
	buf[0] = 0;
	if (F.ncategories == 1 && a->flt_cat >= 0 && a->have_tax && a->flt_cat < a->tax.ncat) {
		cat = &a->tax.cat[a->flt_cat];
		if (a->flt_sub >= 0 && a->flt_sub < cat->nsub)
			snprintf(buf, n, "%s > %s", cat->bar, cat->sub[a->flt_sub].label);
		else snprintf(buf, n, "%s", cat->bar);
		for (p = buf; *p; p++) if (*p == '\n' || *p == '\r' || *p == '\t') *p = ' ';
		return;
	}
	for (i = 0; i < F.ncategories && used < n - 1; i++) {
		snprintf(buf + used, n - used, "%s%s", i ? ", " : "", F.categories[i]);
		used += strlen(buf + used);
	}
}

static void doc_status(e26_app *a)
{
	char topic[240], count[32], type[64];
	if (F.types_unavailable) {
		snprintf(F.status, sizeof F.status, "Document types unavailable: Cisco roster mismatch.");
		return;
	}
	doc_topic(a, topic, sizeof topic);
	snprintf(type, sizeof type, "%s%s", doc_type >= FINDER_CISCO_WHITE_PAPER ? "Cisco > " : "",
	    finder_type_label(doc_type));
	if (!F.doc_count) {
		if (doc_type == FINDER_ALL)
			snprintf(F.status, sizeof F.status, "No documents in %.200s.", topic);
		else
			snprintf(F.status, sizeof F.status, "No %s documents in %.200s.", type, topic);
		return;
	}
	group_digits(F.doc_count, count, sizeof count);
	snprintf(F.status, sizeof F.status, "%s documents \xc2\xb7 %.200s \xc2\xb7 %s",
	    count, topic, type);
}

static void doc_label(const finder_row *r, char *out, size_t cap)
{
	static const char *const cisco_badges[] = { "White Paper", "Validated Design", "Configuration Guide", "Other" };
	const char *digits = r->id + 3;
	switch (finder_type_index(r->source_kind)) {
	case FINDER_RFC:
		if (!strncmp(r->id, "rfc", 3) && *digits && digits[strspn(digits, "0123456789")] == 0) {
			snprintf(out, cap, "RFC %s - %s%s", digits, r->title, r->obsoleted ? " (obsoleted)" : "");
			return;
		}
		break;
	case FINDER_DRAFT:
		snprintf(out, cap, "%s - %s", r->title, !strncmp(r->id, "draft:", 6) ? r->id + 6 : r->id);
		return;
	case FINDER_CISCO:
		if (r->document_type >= FINDER_CISCO_WHITE_PAPER) {
			snprintf(out, cap, "Cisco \xc2\xb7 %s - %s",
			    cisco_badges[r->document_type - FINDER_CISCO_WHITE_PAPER], r->title);
			return;
		}
		break;
	}
	snprintf(out, cap, "%s", r->title);
}

int pin_doc_display(const e26_app *a, int row, char *label, size_t n, char *id, size_t idn,
                    int *level, int *expander, int *duplicate)
{
	const finder_row *r;
	const pin_section *section = NULL;
	char title[2120], words[FINDER_QUERY];
	int opened = expanded.id[0] && row == expanded.row &&
	    (expanded.failure == SECTION_OK || expanded.count);
	*level = *expander = *duplicate = 0; id[0] = 0;
	if (section_row(row)) {
		if (!(section = section_at(row))) return -1;
		*level = section->level;
		if (!grouped_hits) {
			snprintf(label, n, "%s", section->title);
			return 0;
		}
		r = &expanded.document;
	} else {
		if (!(r = document_at(row))) return -1;
		*duplicate = r->duplicate;
		snprintf(id, idn, "%s", r->id);
	}
	doc_label(r, title, sizeof title);
	if (section || doc_query(a, words, sizeof words)[0]) {
		const char *heading = section ? section->title :
		    r->section_title[0] ? r->section_title : r->anchor[0] ? r->anchor : "Document top";
		/* u14 (spec M15): the document, then the section the words are in; the scope and the type stay in the status
		 * line, and the short type is the row's quiet tag (pin_doc_tag) */
		snprintf(label, n, "%s \xc2\xbb %s", title, heading);
		if (!section && grouped_hits && r->has_sections && r->anchor[0])
			*expander = opened ? -1 : 1;
	} else {
		snprintf(label, n, "%s%s", title,
		    doc_sort == 2 && !r->published[0] && !r->updated[0] ? " \xc2\xb7 no date" : "");
		if (r->has_sections) *expander = opened ? -1 : 1;
	}
	return 0;
}

/* u14 (spec M15): the short type a search-hit row carries at its right, in quiet grey ("" for none) */
const char *pin_doc_tag(int row)
{
	const finder_row *r = section_row(row) ? (grouped_hits ? &expanded.document : NULL) : document_at(row);
	if (!r) return "";
	switch (finder_type_index(r->source_kind)) {
	case FINDER_RFC: return "RFC";
	case FINDER_DRAFT: return "Draft";
	case FINDER_WIKI: return "Wiki";
	case FINDER_NIST: return "NIST";
	case FINDER_CISCO: return "Cisco";
	case FINDER_SYNTHETIC: return "Deep32";
	}
	return "";
}

static void add_filter(char *buf, size_t n, const char *label, const char *value)
{
	size_t used = strlen(buf);
	snprintf(buf + used, n - used, "%s%s%s", used ? " \xc2\xb7 " : "", label, value);
}

static const char *src_doc_filters(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	char words[FINDER_QUERY], dates[48], raw[1200] = "";
	const char *query = doc_query(a, words, sizeof words);
	int i;
	(void)c;
	if (cisco_type >= 0) {
		char topic[240];
		doc_topic(a, topic, sizeof topic);
		snprintf(raw, sizeof raw, "%.240s | %s | %s", topic, cisco_type_label(cisco_type), sort_labels[cisco_sort]);
		sel_utf8_to_cp1252(raw, strlen(raw), buf, n);
		return buf;
	}
	if (query[0]) add_filter(raw, sizeof raw, doc_title_mode(a) ? "Title: " : "Words: ", query);
	for (i = 0; i < 8; i++) if (F.media & (1u << i)) add_filter(raw, sizeof raw, "Media: ", media_names[i]);
	if (F.years) {
		snprintf(dates, sizeof dates, "%d to %d", F.year_min, F.year_max);
		add_filter(raw, sizeof raw, "Years: ", dates);
	}
	if (F.place[0]) add_filter(raw, sizeof raw, "Place: ", F.place);
	if (strcmp(F.scope, "all")) add_filter(raw, sizeof raw, "Scope: ", F.scope);
	add_filter(raw, sizeof raw, "Sort: ", sort_label(doc_sort));
	sel_utf8_to_cp1252(raw, strlen(raw), buf, n);
	return buf;
}

static const char *src_doc_detail(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	const finder_row *r = section_row(a->wsel) ? &expanded.document : document_at(a->wsel);
	const pin_section *s = section_row(a->wsel) ? section_at(a->wsel) : NULL;
	const char *date;
	char raw[4096] = "";
	(void)c;
	if (r) {
		add_filter(raw, sizeof raw, "", r->id);
		if (r->topic_id[0]) {
			int i, j, matched = 0;
			char topic[256];
			for (i = 0; a->have_tax && i < a->tax.ncat && !matched; i++) {
				const tax_cat *cat = &a->tax.cat[i];
				if (!strcmp(r->topic_id, cat->id)) {
					snprintf(topic, sizeof topic, "%s", cat->bar); matched = 1;
				}
				for (j = 0; j < cat->nsub && !matched; j++)
					if (!strcmp(r->topic_id, cat->sub[j].id)) {
						snprintf(topic, sizeof topic, "%s > %s", cat->bar, cat->sub[j].label);
						matched = 1;
					}
			}
			add_filter(raw, sizeof raw, "Topic: ", matched ? topic : r->topic_id);
		}
		if (r->revision[0]) add_filter(raw, sizeof raw, "", r->revision);
		if (doc_sort == 2) {
			date = r->published[0] ? r->published : r->updated;
			add_filter(raw, sizeof raw, "", date[0] ? date : "no date");
		}
		if (s || r->section_title[0]) add_filter(raw, sizeof raw, "", s ? s->title : r->section_title);
	}
	sel_utf8_to_cp1252(raw, strlen(raw), buf, n);
	return buf;
}

static const char *src_section_error(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	(void)a; (void)c; (void)buf; (void)n;
	return expanded.failure == SECTION_STALE ? "The Library changed. Restart Deep32 Encyclopedia to reload sections." :
	    "Couldn't load these sections.";
}

void pin_cisco_controls(e26_app *a)
{
	scr_screen *s = scr_find(&a->set, "pinpointer");
	scr_control *c;
	if (!s) return;
	if ((c = scr_control_find(s, "cisco-prev")) != NULL) c->disabled = !cisco_pin_can_page(-1);
	if ((c = scr_control_find(s, "cisco-next")) != NULL) c->disabled = !cisco_pin_can_page(1);
	if ((c = scr_control_find(s, "cisco-retry")) != NULL) c->disabled = !cisco_pin_can_retry();
}

int pin_native(const e26_app *a, const scr_screen *s)
{
	return s && !strcmp(s->name, "pinpointer") && a->font[F_RUI] && present_client_viewport(a);
}
int pin_row_font(const e26_app *a, const scr_screen *s) { return pin_native(a, s) ? F_RUI : F_UI; }
const char *pin_ellipsis(const e26_app *a, int font)
{
	const XFontStruct *f = font >= 0 && font < F_NFONTS ? a->font[font] : NULL;
	const XCharStruct *c;
	if (!f || f->min_byte1 || f->max_byte1 || f->min_char_or_byte2 > 0x85 || f->max_char_or_byte2 < 0x85) return "...";
	if (!f->per_char) return "\x85";
	c = &f->per_char[0x85 - f->min_char_or_byte2];
	return c->width || c->ascent || c->descent ? "\x85" : "...";
}
int pin_row_pitch(const e26_app *a, const scr_screen *s)
{
	const XFontStruct *f = pin_native(a, s) ? a->font[F_RUI] : NULL;
	return f ? f->ascent + f->descent + 6 : PIN_ROW_PITCH;
}

/* u14 (spec M14): over a native viewport the Pinpointer is clamp(0.72 x the screen, 925, 1400) px wide (1382 at
 * 1920) and reads in F_RUI: list rows of pin_row_pitch(), a 24-px field, and the status and filter lines in F_RUI on
 * bands 6 px taller. The 640x480 letterbox keeps the 1995 window exactly. */
static int pin_outer_width(int screen_w)
{
	int w = (screen_w * 72 + 50) / 100;
	if (w < 925) w = 925;
	if (w > 1400) w = 1400;
	if (w > screen_w - 20) w = screen_w - 20;
	return w;
}

static void pin_layout(e26_app *a)
{
	enum { RIGHT = 1, WIDTH = 2, HEIGHT = 4, BOTTOM = 8 };
	/* native only: moves (dy), growths (dh) and the F_RUI lines (font) on top of the compact geometry; the header band
	 * and the field row grow 6 px each above the list, the two lines under it 6 px each */
	static const struct { const char *id; int dy, dh, rui; } native[] = {
		{ "header-bar", 0, 6, 0 }, { "header", 0, 6, 1 }, { "reset", 2, 0, 0 },
		{ "field", 6, 6, 0 }, { "spell", 9, 0, 0 }, { "doc-type", 6, 6, 0 }, { "doc-sort", 6, 6, 0 },
		{ "doc-scope", 6, 6, 0 }, { "list", 12, -24, 0 }, { "zero-status", 12, 6, 1 },
		{ "count", -12, 5, 0 }, { "wizard", -10, 0, 0 }, { "doc-filters", -12, 6, 1 },
		{ "doc-detail", -6, 6, 1 }, { "section-error", -6, 6, 1 }, { "section-retry", -6, 6, 0 },
		{ "cisco-prev", -6, 6, 0 }, { "cisco-next", -6, 6, 0 }, { "cisco-retry", -6, 6, 0 },
		{ "zero-library", -6, 6, 0 }, { "zero-all", -6, 6, 0 }
	};
	static const struct { const char *id; int dx, dw, dh, dy, flags; } controls[] = {
		{ "win-frame", 0, 367, 16, 0, WIDTH | HEIGHT },
		{ "win-ground", 0, 367, 16, 0, WIDTH | HEIGHT },
		{ "cap", 0, 367, 0, 0, WIDTH }, { "close", 367, 0, 0, 0, RIGHT },
		{ "close-bar", 367, 0, 0, 0, RIGHT }, { "header-bar", 0, 367, 0, 0, WIDTH },
		{ "header", 0, 367, 0, 0, WIDTH }, { "reset", 367, 0, 0, 0, RIGHT },
		{ "field", 0, 73, 0, 0, WIDTH }, { "spell", 73, 0, 0, 0, RIGHT },
		{ "list", 0, 367, 0, 0, WIDTH | HEIGHT }, { "count", 0, 0, 0, 0, WIDTH | BOTTOM },
		{ "wizard", 367, 0, 0, 0, RIGHT | BOTTOM },
		{ "f-words", 367, 0, 0, 0, RIGHT }, { "f-categ", 367, 0, 0, 0, RIGHT },
		{ "f-media", 367, 0, 0, 0, RIGHT }, { "f-time", 367, 0, 0, 0, RIGHT },
		{ "f-place", 367, 0, 0, 0, RIGHT }, { "doc-type", 367, 0, 0, 0, RIGHT },
		{ "doc-scope", 249, 0, 0, 0, RIGHT },
		{ "doc-sort", 367, 0, 0, 0, RIGHT }, { "doc-filters", 0, 367, 0, 0, WIDTH | BOTTOM },
		{ "cisco-prev", 367, 0, 0, 16, RIGHT | BOTTOM },
		{ "cisco-next", 367, 0, 0, 16, RIGHT | BOTTOM },
		{ "cisco-retry", 367, 0, 0, 16, RIGHT | BOTTOM },
		{ "doc-detail", 0, 367, 0, 16, WIDTH | BOTTOM },
		{ "section-error", 0, 367, 0, 16, WIDTH | BOTTOM },
		{ "section-retry", 367, 0, 0, 16, RIGHT | BOTTOM },
		{ "zero-status", 0, 367, 0, 0, WIDTH },
		{ "zero-library", 0, 0, 0, 16, BOTTOM }, { "zero-all", 196, 0, 0, 16, BOTTOM }
	};
	static const char *const extra[] = { "doc-type", "doc-sort", "doc-filters", "doc-scope" };
	static struct { int x, y, w, h, font, text_y; } compact[sizeof controls / sizeof controls[0]];
	static const scr_screen *captured;
	static int compact_w, compact_h;
	scr_screen *s = scr_find(&a->set, "pinpointer");
	scr_control *c;
	size_t i, k;
	int cisco = cisco_type >= 0, wide, pitch;
	int docs = pin_doc_mode(), empty = docs && !cisco && doc_ready && !F.doc_count, w, h, dx, dy;
	int failed = docs && !cisco && expanded.id[0] && expanded.failure != SECTION_OK;
	if (!s) return;
	if (captured != s) {
		captured = s; compact_w = s->w; compact_h = s->h;
		for (i = 0; i < sizeof controls / sizeof controls[0]; i++)
			if ((c = scr_control_find(s, controls[i].id)) != NULL) {
				compact[i].x = c->x; compact[i].y = c->y;
				compact[i].w = c->w; compact[i].h = c->h;
				compact[i].font = c->font; compact[i].text_y = c->text_y;
			}
	}
	present_frame_bounds(a, &w, &h);
	wide = pin_native(a, s);
	pitch = pin_row_pitch(a, s);
	if (wide) {
		dx = pin_outer_width(w) - (compact_w + 367 * docs);
		if (dx < 0) dx = 0;
	} else {
		dx = w - E26_W;
		if (dx > 320) dx = 320;
	}
	dy = (h - E26_H) / pitch;
	if (dy > FINDER_PAGE - PIN_BASE_ROWS) dy = FINDER_PAGE - PIN_BASE_ROWS;
	dy *= pitch;
	s->w = compact_w + 367 * docs + dx;
	s->h = compact_h + 16 * docs + dy;
	for (i = 0; i < sizeof controls / sizeof controls[0]; i++)
		if ((c = scr_control_find(s, controls[i].id)) != NULL) {
			int flags = controls[i].flags;
			c->x = compact[i].x + controls[i].dx * docs + (flags & RIGHT ? dx : 0);
			c->y = compact[i].y + controls[i].dy * docs + (flags & BOTTOM ? dy : 0);
			c->w = compact[i].w + controls[i].dw * docs + (flags & WIDTH ? dx : 0);
			c->h = compact[i].h + controls[i].dh * docs + (flags & HEIGHT ? dy : 0);
			c->font = compact[i].font; c->text_y = compact[i].text_y;
			for (k = 0; wide && k < sizeof native / sizeof native[0]; k++) {
				const XFontStruct *f = a->font[F_RUI];
				if (strcmp(native[k].id, controls[i].id)) continue;
				c->y += native[k].dy; c->h += native[k].dh;
				if (native[k].rui) { c->font = F_RUI; c->text_y = (c->h + f->ascent - f->descent) / 2; }
			}
		}
	for (i = 0; i < sizeof extra / sizeof extra[0]; i++)
		if ((c = scr_control_find(s, extra[i])) != NULL) c->hidden = !docs;
	if ((c = scr_control_find(s, "list")) != NULL)
		c->kind = reg_find(REG_KIND, cisco ? "cisco-tree" : "wheel");
	if ((c = scr_control_find(s, "cisco-shortcut")) != NULL) c->hidden = !documents_available() && !cisco;
	if ((c = scr_control_find(s, "cisco-prev")) != NULL) c->hidden = !cisco;
	if ((c = scr_control_find(s, "cisco-next")) != NULL) c->hidden = !cisco;
	if ((c = scr_control_find(s, "cisco-retry")) != NULL) c->hidden = !cisco;
	if ((c = scr_control_find(s, "f-words")) != NULL) c->hidden = cisco;
	if ((c = scr_control_find(s, "f-media")) != NULL) c->hidden = cisco;
	if ((c = scr_control_find(s, "f-time")) != NULL) c->hidden = cisco;
	if ((c = scr_control_find(s, "f-place")) != NULL) c->hidden = cisco;
	if ((c = scr_control_find(s, "browse-scope")) != NULL)
		c->hidden = docs || !documents_available();
	if ((c = scr_control_find(s, "doc-detail")) != NULL) c->hidden = !docs || cisco || empty || failed;
	if ((c = scr_control_find(s, "section-error")) != NULL) c->hidden = !failed;
	if ((c = scr_control_find(s, "section-retry")) != NULL) {
		c->hidden = !failed || expanded.failure != SECTION_RETRY;
		c->disabled = F.busy;
		if (c->hidden && focus_get(a, s) == (int)(c - s->c)) {
			const scr_control *list = scr_control_find(s, "list");
			if (list) focus_set(a, s, (int)(list - s->c), 1);
		}
	}
	if ((c = scr_control_find(s, "doc-type")) != NULL) c->disabled = 0;
	if ((c = scr_control_find(s, "zero-status")) != NULL) c->hidden = !empty;
	if ((c = scr_control_find(s, "zero-library")) != NULL) c->hidden = !empty || F.whole_library;
	if ((c = scr_control_find(s, "zero-all")) != NULL) c->hidden = !empty || doc_type == FINDER_ALL;
	if ((c = scr_control_find(s, "count")) != NULL) c->hidden = docs;
	if (cisco) pin_cisco_controls(a);
}

/* A nonmodal wheel stays in the scene while the base has focus. */
static int screen_rows(const e26_app *a, scr_screen *s)
{
	if (!s) return 0;
	if (!strcmp(s->name, "finder-results")) return finder_results_rows(a, s);
	if (!strncmp(s->name, "pinpointer", 10)) return pin_wheel_rows(a, s, scr_control_find(s, "list"));
	return 0;
}

static int active_rows(const e26_app *a)
{
	int i, rows;
	if (!a->nwin) return screen_rows(a, a->cur);
	for (i = a->nwin - 1; i >= 0; i--)
		if (!a->wstack[i].hidden && (rows = screen_rows(a, a->wstack[i].s)) != 0) return rows;
	return 0;
}

static void resize_viewport(e26_app *a)
{
	int rows = active_rows(a);
	int selected_visible = a->wsel >= a->wtop && a->wsel < a->wtop + viewport_rows;
	if (!rows || rows == viewport_rows) return;
	if (resize_top < 0 || resized_top != a->wtop) resize_top = a->wtop;
	viewport_rows = rows;
	a->wtop = resize_top;
	if (selected_visible && a->wsel >= a->wtop + rows) a->wtop = a->wsel - rows + 1;
	fill_viewport(a);
	resized_top = a->wtop;
	if (resize_top == a->wtop) resize_top = -1;
}

void pin_menu_items(scr_menu *m)
{
	static const int types[] = { FINDER_ALL, FINDER_RFC, FINDER_CISCO,
	    FINDER_CISCO_WHITE_PAPER, FINDER_CISCO_CVD, FINDER_CISCO_CONFIG, FINDER_CISCO_OTHER, FINDER_TYPES,
	    FINDER_DRAFT, FINDER_WIKI, FINDER_NIST, FINDER_SYNTHETIC, FINDER_UNCLASSIFIED };
	int i, sort = !strcmp(m->id, "pin-doc-sort"), look = !strcmp(m->id, "pin-doc-scope");
	m->nit = 0;
	if (!strcmp(m->id, "cisco-subtypes")) {
		/* the Library reply's count for each Cisco type where it carries one (S14; TechNotes has none) */
		static const int counted[CISCO_TYPES] = { FINDER_CISCO, FINDER_CISCO_CONFIG, FINDER_CISCO_WHITE_PAPER,
		    FINDER_CISCO_CVD, FINDER_TYPES, FINDER_CISCO_OTHER };
		for (i = 0; i < CISCO_TYPES; ++i) {
			scr_menu_item *item = &m->it[m->nit++];
			char digits[32] = "";
			memset(item, 0, sizeof *item);
			item->action = reg_find(REG_ACTION, "cisco.type");
			snprintf(item->arg, sizeof item->arg, "%s", cisco_type_key(i));
			if (counted[i] < FINDER_TYPES && F.type_counts[counted[i]])
				group_digits(F.type_counts[counted[i]], digits, sizeof digits);
			snprintf(item->label, sizeof item->label, "%s%s%s%s", cisco_type_label(i), *digits ? " (" : "", digits,
			    *digits ? ")" : "");
		}
		return;
	}
	if (look && documents_available()) {
		for (i = 0; i < 2; i++) {
			scr_menu_item *item = &m->it[m->nit++];
			memset(item, 0, sizeof *item);
			item->action = reg_find(REG_ACTION, "pin.look");
			snprintf(item->arg, sizeof item->arg, "%s", i ? "library" : "topic");
			snprintf(item->label, sizeof item->label, "%s%s", i == F.whole_library ? "[*] " : "    ",
			    i ? "Entire Library" : "Current topic");
		}
		return;
	}
	if (!pin_doc_mode()) return;
	for (i = 0; i < (sort ? (cisco_type >= 0 ? 2 : 3) : (int)(sizeof types / sizeof types[0])); i++) {
		scr_menu_item *item;
		int choice = sort ? i : types[i];
		int cisco = !sort && (choice == FINDER_CISCO || choice >= FINDER_CISCO_WHITE_PAPER);
		if (!sort && F.types_unavailable && choice != FINDER_ALL && !cisco) continue;
		item = &m->it[m->nit++];
		memset(item, 0, sizeof *item);
		item->action = reg_find(REG_ACTION, sort ? "pin.sort" : "pin.type");
		snprintf(item->arg, sizeof item->arg, "%d", choice);
		if (sort) sel_utf8_to_cp1252(sort_label(i), strlen(sort_label(i)), item->label, sizeof item->label);
		else if (cisco) {
			/* u14 (spec S14): the count the Library's reply carries for a Cisco type, where it carries one; the Cisco
			 * browser also lists the RAG corpus's guides, which these Library counts leave out, so a type the reply
			 * does not count (TechNotes, or none in this scope) shows no number rather than "(0)" */
			char count[40] = "";
			if (choice < FINDER_TYPES && F.type_counts[choice]) {
				char digits[32];
				group_digits(F.type_counts[choice], digits, sizeof digits);
				snprintf(count, sizeof count, " (%s)", digits);
			}
			snprintf(item->label, sizeof item->label, "%s%s%s", choice == FINDER_CISCO ? "" : "  ",
			    choice == FINDER_TYPES ? "Support / TechNotes" : finder_type_label(choice), count);
		} else {
			char count[32];
			group_digits(F.type_counts[choice], count, sizeof count);
			snprintf(item->label, sizeof item->label, "%s%s (%s)",
			    choice >= FINDER_CISCO_WHITE_PAPER ? "  " : "", finder_type_label(choice), count);
		}
	}
}

static void remember_doc_selection(const e26_app *a)
{
	const finder_row *r = section_row(a->wsel) ? &expanded.document : document_at(a->wsel);
	choose_id[0] = 0;
	if (r && pin_doc_mode()) {
		snprintf(choose_id, sizeof choose_id, "%s", r->id);
		choose_serial = F.serial + 1;
		choose_pages = 0;
	}
}

static int act_section_retry(e26_app *a, const e26_actx *x)
{
	(void)x;
	if (!pin_doc_mode() || expanded.failure != SECTION_RETRY || F.busy || debounce) return 0;
	fetch_sections(a, expanded.retry_start);
	return 1;
}

static int act_doc_type(e26_app *a, const e26_actx *x)
{
	char *end;
	long type;
	int cisco = -1;
	if (!x->arg || !*x->arg) return 0;
	type = strtol(x->arg, &end, 10);
	if (*end || type < 0 || type > FINDER_TYPES) return 0;
	switch (type) {
	case FINDER_CISCO: cisco = cisco_type_index("All"); break;
	case FINDER_CISCO_WHITE_PAPER: cisco = cisco_type_index("WhitePaper"); break;
	case FINDER_CISCO_CVD: cisco = cisco_type_index("ValidatedDesign"); break;
	case FINDER_CISCO_CONFIG: cisco = cisco_type_index("ConfigGuide"); break;
	case FINDER_CISCO_OTHER: cisco = cisco_type_index("Other"); break;
	case FINDER_TYPES: cisco = cisco_type_index("TechNote"); break;
	}
	if (cisco >= 0) {
		if (cisco == cisco_type) return 0;
		if (F.ncategories != 1) F.whole_library = 1;
		if (cisco_type < 0) cisco_sort = doc_sort == 1;
		cisco_type = cisco;
		finder_refresh(a);
		return 1;
	}
	if ((cisco_type < 0 && (!pin_doc_mode() || type == doc_type)) ||
	    (F.types_unavailable && type != FINDER_ALL)) return 0;
	remember_doc_selection(a);
	set_doc_type((int)type); finder_refresh(a); return 1;
}

/* u14 (spec S8): "Browse the Library" on the Library home: the Pinpointer on the Entire Library with one Type and a
 * sort; a Cisco type opens the Cisco browser (its guides as books, M17) */
void pin_browse_type(e26_app *a, int type, int sort)
{
	int cisco = -1;
	switch (type) {
	case FINDER_CISCO: cisco = cisco_type_index("All"); break;
	case FINDER_CISCO_WHITE_PAPER: cisco = cisco_type_index("WhitePaper"); break;
	case FINDER_CISCO_CVD: cisco = cisco_type_index("ValidatedDesign"); break;
	case FINDER_CISCO_CONFIG: cisco = cisco_type_index("ConfigGuide"); break;
	case FINDER_CISCO_OTHER: cisco = cisco_type_index("Other"); break;
	case FINDER_TYPES: cisco = cisco_type_index("TechNote"); break;
	}
	if (type < FINDER_ALL || type > FINDER_TYPES) return;
	choose_id[0] = 0;
	a->edit[0] = 0; a->edit_sel = 0; F.query[0] = 0;
	F.whole_library = 1;
	if (cisco >= 0) {
		cisco_type = cisco;
		cisco_sort = sort == 1;
	} else {
		cisco_type = -1;
		doc_type = type;
		doc_sort = sort >= 0 && sort <= 2 ? sort : 0;
	}
	if (a->verbose)
		fprintf(stderr, "d32library: pinpointer: browse the Library: %s, sort %d\n",
		    type == FINDER_TYPES ? "Cisco TechNotes" : finder_type_label(type), sort);
	finder_refresh(a);
	nav_go(a, "pinpointer", 0);
}

/* u14 (spec S9): Tools > Browse Panel, the period design's "articles next to this one": the Pinpointer on the open article's
 * topic with the article chosen in the list; with no article, or one outside the taxonomy, the Pinpointer's root */
static int act_browse(e26_app *a, const e26_actx *x)
{
	const art_state *st = art_st();
	int i, j, cat = -1, sub = -1;
	(void)x;
	if (a->have_art && st->is_v2 && st->full_id && a->have_tax && st->d2.primary_category && *st->d2.primary_category)
		for (i = 0; i < a->tax.ncat && cat < 0; i++) {
			if (!strcmp(a->tax.cat[i].id, st->d2.primary_category)) cat = i;
			for (j = 0; j < a->tax.cat[i].nsub && cat < 0; j++)
				if (!strcmp(a->tax.cat[i].sub[j].id, st->d2.primary_category)) { cat = i; sub = j; }
		}
	set_doc_type(FINDER_ALL); /* all types, by title unless the reader chose a sort */
	F.whole_library = 0;
	if (cat < 0) {
		if (a->verbose) fprintf(stderr, "d32library: browse panel: no topic for the open article; the Pinpointer's root\n");
		pin_filter(a, -1, -1);
		nav_go(a, "pinpointer", 0);
		return 0;
	}
	pin_open_filtered(a, cat, sub);
	snprintf(choose_id, sizeof choose_id, "%s", st->full_id);
	choose_serial = F.serial;
	choose_pages = 1;
	if (a->verbose)
		fprintf(stderr, "d32library: browse panel: %s in %s\n", st->full_id,
		    sub >= 0 ? a->tax.cat[cat].sub[sub].id : a->tax.cat[cat].id);
	return 0;
}

static int act_doc_look(e26_app *a, const e26_actx *x)
{
	int whole;
	if (!documents_available() || !x->arg) return 0;
	if (!strcmp(x->arg, "library")) whole = 1;
	else if (!strcmp(x->arg, "topic")) whole = 0;
	else return 0;
	if (!whole && cisco_type >= 0 && F.ncategories != 1) return 0;
	if (whole == F.whole_library) return 0;
	remember_doc_selection(a);
	F.whole_library = whole; finder_refresh(a); return 1;
}

static const char *src_doc_look(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	(void)a; (void)c; (void)buf; (void)n;
	return F.whole_library ? "Scope: Entire Library" : "Scope: Current topic";
}

static const char *src_doc_type(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	(void)a; (void)c; (void)buf; (void)n;
	return cisco_type >= 0 ? "Type: Cisco" : F.types_unavailable ? "All types" : "Type";
}

static int act_doc_sort(e26_app *a, const e26_actx *x)
{
	int sort = x->arg && *x->arg >= '0' && *x->arg <= '2' && !x->arg[1] ? *x->arg - '0' : -1;
	if (cisco_type >= 0) {
		if (sort < 0 || sort > 1 || sort == cisco_sort) return 0;
		cisco_sort = sort; finder_refresh(a); return 1;
	}
	if (sort < 0) return 0;
	sort_picked = 1; /* the reader chose: Type no longer changes the sort this session (M16) */
	if (sort == doc_sort) return 0;
	doc_sort = sort; finder_refresh(a); return 1;
}

static const char *src_status(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	(void)a; (void)c;
	if (cisco_type >= 0) snprintf(buf, n, "%s", cisco_pin_status());
	else if (pin_doc_mode()) sel_utf8_to_cp1252(F.status, strlen(F.status), buf, n);
	else snprintf(buf, n, "%s", F.status);
	return buf;
}
static const char *src_doc_zero(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	char words[FINDER_QUERY], topic[240], raw[768];
	const char *query = doc_query(a, words, sizeof words);
	(void)c;
	if (!query[0]) return src_status(a, c, buf, n);
	doc_topic(a, topic, sizeof topic);
	snprintf(raw, sizeof raw, "No matches for \"%s\" in %.190s.", query, topic);
	sel_utf8_to_cp1252(raw, strlen(raw), buf, n);
	return buf;
}
static const char *src_header(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	(void)c;
	if (cisco_type >= 0 && a->cur && !strcmp(a->cur->name, "pinpointer")) {
		snprintf(buf, n, "%s", cisco_pin_status());
	} else if (pin_doc_mode() && a->cur && !strcmp(a->cur->name, "pinpointer")) {
		sel_utf8_to_cp1252(F.status, strlen(F.status), buf, n);
	} else if (F.ncategories == 1 && a->flt_cat >= 0 && a->have_tax && a->flt_cat < a->tax.ncat) {
		const tax_cat *cat = &a->tax.cat[a->flt_cat];
		if (a->flt_sub >= 0 && a->flt_sub < cat->nsub) snprintf(buf, n, "%s > %s", cat->label, cat->sub[a->flt_sub].label);
		else snprintf(buf, n, "%s", cat->label);
	} else if (!F.ncategories && !F.query[0] && !F.media && !F.years && !F.place[0]) snprintf(buf, n, "%s", F.catalogue ? "Catalogue" : "All Articles");
	else snprintf(buf, n, "%u Article%s", a->nview, a->nview == 1 ? "" : "s");
	{ char *p; for (p = buf; *p; p++) if (*p == '\n') *p = ' '; }
	return buf;
}
static int src_category(e26_app *a, const scr_control *c) { (void)a; (void)c; return F.ncategories != 0; }
static int src_filter(e26_app *a, const scr_control *c)
{
	const char *p = scr_prop_get(c, "filter"); (void)a;
	return p && ((!strcmp(p, "words") && F.query[0]) || (!strcmp(p, "media") && F.media) || (!strcmp(p, "time") && F.years) || (!strcmp(p, "place") && F.place[0]));
}
static int src_media(e26_app *a, const scr_control *c) { int b = media_bit(scr_prop_get(c, "media")); (void)a; return b >= 0 && (F.media >> b & 1); }
static int src_scope(e26_app *a, const scr_control *c) { const char *p = scr_prop_get(c, "scope"); (void)a; return p && (!strcmp(p, "title") ? F.title_mode : !strcmp(F.scope, p)); }
static int src_catalogue(e26_app *a, const scr_control *c) { (void)a; (void)c; return F.catalogue; }
static int src_era(e26_app *a, const scr_control *c)
{
	const char *ep = scr_prop_get(c, "endpoint"), *era = scr_prop_get(c, "era"); int year = ep && !strcmp(ep, "from") ? F.year_min : F.year_max;
	(void)a; return F.years && era && (!strcmp(era, "bc") ? year < 0 : year > 0);
}
static void on_enter(e26_app *a, const e26_event *ev, void *ud)
{
	scr_screen *s = ev->screen;
	(void)ud;
	if (!s) return;
	pin_layout(a);
	resize_viewport(a);
	if (!ev->switched && screen_rows(a, s)) {
		if (cisco_type < 0 && !F.generation[0] && !F.busy) fetch(a, 0, 1);
		fill_viewport(a);
	}
}
static void on_resize(e26_app *a, const e26_event *ev, void *ud)
{
	(void)ev; (void)ud;
	pin_layout(a);
	resize_viewport(a);
}
static void on_back(e26_app *a, const e26_event *ev, void *ud)
{
	(void)ud;
	if (ev->hist && (ev->hist->flt_cat != a->flt_cat || ev->hist->flt_sub != a->flt_sub)) pin_filter(a, ev->hist->flt_cat, ev->hist->flt_sub);
}
void e26_mod_room_pinpoint(void);
void e26_mod_room_pinpoint(void)
{
	static const e26_action reset = { act_reset }, search = { act_search }, op = { act_op }, media = { act_media }, scope = { act_scope },
	    time = { act_time }, place = { act_place }, catalogue = { act_catalogue }, past = { act_past }, hints = { act_hints },
	    del = { act_delete_past }, save = { act_save }, load = { act_load }, packs = { act_packs }, timeline = { act_timeline }, lists = { act_lists };
	static const source_ops header = { src_header, NULL, NULL }, category = { NULL, src_category, NULL }, status = { src_status, NULL, NULL },
	    filter = { NULL, src_filter, NULL }, med = { NULL, src_media, NULL }, sc = { NULL, src_scope, NULL }, ca = { NULL, src_catalogue, NULL }, era = { NULL, src_era, NULL };
	static const e26_action type = { act_doc_type }, sort = { act_doc_sort }, look = { act_doc_look };
	static const e26_action retry = { act_section_retry };
	static const source_ops filters = { src_doc_filters, NULL, NULL }, detail = { src_doc_detail, NULL, NULL },
	    look_text = { src_doc_look, NULL, NULL }, type_text = { src_doc_type, NULL, NULL },
	    zero_text = { src_doc_zero, NULL, NULL }, section_error = { src_section_error, NULL, NULL };
	static const e26_setting minimise = { "MinimisePinpointerAfterSelect", NULL, SET_BOOL, "0" };
	reg_add(REG_ACTION, "pin.type", &type, "room_pinpoint", 0);
	reg_add(REG_ACTION, "pin.sort", &sort, "room_pinpoint", 0);
	reg_add(REG_ACTION, "pin.look", &look, "room_pinpoint", 0);
	reg_add(REG_ACTION, "pin.section-retry", &retry, "room_pinpoint", 0);
	{
		static const e26_action return_results = { act_return_results };
		reg_add(REG_ACTION, "pin.return-results", &return_results, "room_pinpoint", 0);
		static const e26_action browse = { act_browse };
		reg_add(REG_ACTION, "room.browse", &browse, "room_pinpoint", 0); /* Tools > Browse Panel (u14 S9) */
	}
	reg_add(REG_SOURCE, "pin-doc-look", &look_text, "room_pinpoint", 0);
	reg_add(REG_SOURCE, "pin-doc-type", &type_text, "room_pinpoint", 0);
	reg_add(REG_SOURCE, "pin-doc-zero", &zero_text, "room_pinpoint", 0);
	reg_add(REG_SOURCE, "pin-doc-filters", &filters, "room_pinpoint", 0);
	reg_add(REG_SOURCE, "pin-doc-detail", &detail, "room_pinpoint", 0);
	reg_add(REG_SOURCE, "pin-section-error", &section_error, "room_pinpoint", 0);
	reg_add(REG_ACTION, "reset", &reset, "room_pinpoint", 0);
	reg_add(REG_ACTION, "finder.search", &search, "room_pinpoint", 0);
	reg_add(REG_ACTION, "find.words", &search, "room_pinpoint", 0);
	reg_add(REG_ACTION, "find.word-op", &op, "room_pinpoint", 0);
	reg_add(REG_ACTION, "find.media", &media, "room_pinpoint", 0);
	reg_add(REG_ACTION, "finder.media", &media, "room_pinpoint", 0);
	reg_add(REG_ACTION, "finder.scope", &scope, "room_pinpoint", 0);
	reg_add(REG_ACTION, "find.time", &time, "room_pinpoint", 0);
	reg_add(REG_ACTION, "finder.time", &time, "room_pinpoint", 0);
	reg_add(REG_ACTION, "find.place", &place, "room_pinpoint", 0);
	reg_add(REG_ACTION, "finder.place", &place, "room_pinpoint", 0);
	reg_add(REG_ACTION, "finder.catalogue", &catalogue, "room_pinpoint", 0);
	reg_add(REG_ACTION, "finder.past", &past, "room_pinpoint", 0);
	reg_add(REG_ACTION, "finder.hints", &hints, "room_pinpoint", 0);
	reg_add(REG_ACTION, "finder.lists", &lists, "room_pinpoint", 0);
	reg_add(REG_ACTION, "finder.delete-past", &del, "room_pinpoint", 0);
	reg_add(REG_ACTION, "finder.save", &save, "room_pinpoint", 0);
	reg_add(REG_ACTION, "finder.open-list", &load, "room_pinpoint", 0);
	reg_add(REG_ACTION, "finder.multiple-packs", &packs, "room_pinpoint", 0);
	reg_add(REG_ACTION, "finder.timeline", &timeline, "room_pinpoint", 0);
	reg_add(REG_SOURCE, "pin-header", &header, "room_pinpoint", 0);
	reg_add(REG_SOURCE, "pin-category", &category, "room_pinpoint", 0);
	reg_add(REG_SOURCE, "finder-status", &status, "room_pinpoint", 0);
	reg_add(REG_SOURCE, "finder-filter", &filter, "room_pinpoint", 0);
	reg_add(REG_SOURCE, "finder-media", &med, "room_pinpoint", 0);
	reg_add(REG_SOURCE, "finder-scope", &sc, "room_pinpoint", 0);
	reg_add(REG_SOURCE, "finder-catalogue", &ca, "room_pinpoint", 0);
	reg_add(REG_SOURCE, "finder-era", &era, "room_pinpoint", 0);
	reg_add(REG_SETTING, minimise.key, &minimise, "room_pinpoint", 0);
	ev_subscribe(EV_SCREEN_ENTER, on_enter, NULL); ev_subscribe(EV_RESIZE, on_resize, NULL);
	ev_subscribe(EV_HISTORY_BACK, on_back, NULL);
}
