/* Pinpointer Cisco browser. Only the active document/part/section pages are resident. */
#include "room_documents.h"
#include "room_finder.h"
#include "document_catalogue.h"
#include "article_library.h"
#include "room_article.h"
#include "edit.h"
#include "focus.h"
#include "select.h"
#include "../common/e26j.h"
#include <X11/keysym.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { DOCS, PARTS, SECTIONS, PANES, STATUS = -1, RESTORE = -2 };
enum { NODE_DOCUMENT, NODE_PART, NODE_CONTENTS_SECTIONS, NODE_SECTION };
enum { PITCH = 32, HISTORY = 128, MAX_VISIBLE = CISCO_SEARCH + 2 * CISCO_PAGE + 1 };
typedef struct { cisco_page *page; int index; cisco_cursor history[HISTORY]; } pane;
typedef struct { int kind, index; const cisco_row *row; } node;
/* u14 (spec M17): on the wide native Pinpointer a guide or a part is two F_RUI lines, 2 x (F_RUI height + 2) + 8 px
 * (48 with Helvetica 17), and a section of its contents one line at the list's pitch (F_RUI height + 6), each level
 * 18 px further in, so that the contents read as a book's; the 640x480 letterbox and the compact Cisco Library window
 * keep every row 32 px and the 12-px steps */
static int node_h(const e26_app *a, const scr_screen *s, const node *v)
{
	const XFontStruct *f = a->font[F_RUI];
	if (!pin_native(a, s)) return PITCH;
	if (v->kind == NODE_SECTION || v->kind == NODE_CONTENTS_SECTIONS) return f->ascent + f->descent + 6;
	return 2 * (f->ascent + f->descent + 2) + 8;
}
static int step(const e26_app *a, const scr_screen *s) { return pin_native(a, s) ? 18 : 12; }
typedef struct { e26_app *a; unsigned serial; int pane, index; } pending;
static struct {
	cisco_status status;
	pane page[PANES];
	int active, subtype, sort, current, topic_is_area, root_open, part_open, sections_open, selected, top;
	char topic[128], query[512], message[192];
	int busy, failed, retry_pane, retry_index;
	uint64_t request;
	unsigned serial, press_serial;
	int press_row;
	int route_pack, pending_open, confirmed_open, results;
	cisco_route opened;
} B;

static int legacy_limits; /* 1: the helper refused a 100-row page (CISCO_PAGE_LEGACY, CISCO_SECTIONS_LEGACY) */

static int front(const e26_app *a, const char *name)
{
	return a->cur && !strcmp(a->cur->name, name);
}

static int finder_front(const e26_app *a)
{
	return B.active && front(a, "pinpointer");
}

static void message(const char *s) { snprintf(B.message, sizeof B.message, "%s", s); }

static void cancel(void)
{
	uint64_t id = B.request;
	++B.serial;
	B.request = 0;
	B.busy = 0;
	if (id) (void)lib_cancel(id);
}

static void clear_from(int which)
{
	int i;
	for (i = which; i < PANES; ++i) {
		free(B.page[i].page);
		B.page[i].page = NULL;
		B.page[i].index = 0;
	}
	if (which <= PARTS) B.root_open = -1;
	B.part_open = -1;
	B.sections_open = 0;
}

static const cisco_row *contents(void)
{
	const cisco_page *p = B.page[DOCS].page;
	return p && B.root_open >= 0 && B.root_open < p->count ? &p->row[B.root_open] : NULL;
}

static const cisco_row *section_owner(void)
{
	const cisco_page *p = B.page[PARTS].page;
	if (B.part_open < 0) return contents();
	return p && B.part_open < p->count ? &p->row[B.part_open] : NULL;
}

static int nrows(const cisco_page *p) { return p ? p->count : 0; }

static void add_sections(node *out, int *n)
{
	const cisco_page *p = B.page[SECTIONS].page;
	int i;
	for (i = 0; i < nrows(p); ++i) out[(*n)++] = (node){ NODE_SECTION, i, &p->row[i] };
}

static int flatten(node out[MAX_VISIBLE])
{
	const cisco_page *root = B.page[DOCS].page, *parts = B.page[PARTS].page;
	int i, j, n = 0;
	for (i = 0; i < nrows(root); ++i) {
		const cisco_row *r = &root->row[i];
		out[n++] = (node){ NODE_DOCUMENT, i, r };
		if (i != B.root_open) continue;
		if (r->parts > 1) {
			if (r->outline) out[n++] = (node){ NODE_CONTENTS_SECTIONS, 0, r };
			if (B.sections_open && B.part_open < 0) add_sections(out, &n);
			for (j = 0; j < nrows(parts); ++j) {
				out[n++] = (node){ NODE_PART, j, &parts->row[j] };
				if (B.sections_open && B.part_open == j) add_sections(out, &n);
			}
		} else if (B.sections_open) add_sections(out, &n);
	}
	return n;
}

/* the rows from top that the list's height h shows (one at least) */
static int fitting(const e26_app *a, const scr_screen *s, const node *nodes, int n, int top, int h)
{
	int k, y = 0;
	for (k = top; k < n && y + node_h(a, s, &nodes[k]) <= h; k++) y += node_h(a, s, &nodes[k]);
	return k - top > 0 ? k - top : 1;
}

/* the row at local_y (from the list's top), or -1 */
static int row_at(const e26_app *a, const scr_screen *s, const node *nodes, int n, int h, int local_y)
{
	int k, y = 0;
	if (local_y < 0) return -1;
	for (k = B.top; k < n; k++) {
		int rh = node_h(a, s, &nodes[k]);
		if (y + rh > h) break;
		if (local_y < y + rh) return k;
		y += rh;
	}
	return -1;
}

/* the selected row in view, and no empty space under the last row while earlier rows are out of view */
static void keep_visible(const e26_app *a, const scr_screen *s, const node *nodes, int count, int h)
{
	int k, y = 0;
	if (B.selected >= count) B.selected = count ? count - 1 : 0;
	if (B.selected < 0) B.selected = 0;
	if (B.top > B.selected) B.top = B.selected;
	while (B.top < B.selected && B.top + fitting(a, s, nodes, count, B.top, h) <= B.selected) B.top++;
	for (k = count - 1; k >= 0 && y + node_h(a, s, &nodes[k]) <= h; k--) y += node_h(a, s, &nodes[k]);
	if (B.top > k + 1) B.top = k + 1 > 0 ? k + 1 : 0;
}

static void request(e26_app *a, int which, int index);

static void result_status(int which, const cisco_page *p)
{
	const char *label = which == PARTS ? "parts" : which == SECTIONS ? "sections" : "publications";
	const char *availability = !B.status.store[1].ready ? " | Corpus unavailable" :
	    !B.status.store[0].ready ? " | Base Library unavailable" : "";
	if (which == DOCS && *B.query)
		snprintf(B.message, sizeof B.message, "Showing %d Cisco result%s%s%s", p->count,
		    p->count == 1 ? "" : "s", p->refine ? " | Refine to narrow" : "", availability);
	else snprintf(B.message, sizeof B.message, "%d %s on page %d%s", p->count,
	    label, B.page[which].index + 1, availability);
}

static void failed(int which, int index, const char *why)
{
	B.failed = 1; B.retry_pane = which; B.retry_index = index;
	message(why);
}

static int advances(const cisco_cursor *next, const cisco_cursor *current)
{
	int order = next->part ? next->part - current->part : strcmp(next->sort_key, current->sort_key);
	return order > 0 || (!order && strcmp(next->key, current->key) > 0);
}

static void focus_tree(e26_app *a)
{
	scr_control *c = a->cur ? scr_control_find(a->cur, finder_front(a) ? "list" : "tree") : NULL;
	if (c) focus_set(a, a->cur, (int)(c - a->cur->c), 1);
}

static int article_matches(void)
{
	return B.results && art_st()->full_id && art_st()->pack == B.route_pack &&
	    !strcmp(art_st()->full_id, B.opened.article) &&
	    !strcmp(ar_lib_generation(B.route_pack), B.opened.generation);
}

static int same_stores(const cisco_status *status)
{
	int i;
	for (i = 0; i < 2; ++i) if (status->store[i].ready != B.status.store[i].ready ||
	    strcmp(status->store[i].generation, B.status.store[i].generation)) return 0;
	return 1;
}

static void select_first(int kind)
{
	node nodes[MAX_VISIBLE];
	int n = flatten(nodes), i;
	for (i = 0; i < n; ++i) if (nodes[i].kind == kind) { B.selected = i; break; }
}

static void receive(const lib_reply *r, void *ud)
{
	pending *p = ud;
	e26_app *a = p->a;
	int which = p->pane, index = p->index;
	if (p->serial != B.serial) { free(p); return; }
	free(p);
	++B.serial; B.request = 0; B.busy = 0;
	if (which == RESTORE ? !front(a, "article") : !finder_front(a) && !front(a, "pinpointer-cisco")) return;
	if (which == RESTORE && !article_matches()) return;
	if (!r->ok && which >= 0 && !legacy_limits && r->code && !strcmp(r->code, "E_ARGUMENT") &&
	    !(which == DOCS && *B.query)) {
		legacy_limits = 1; /* an e26-lib from before u14: its smaller pages */
		request(a, which, index);
		return;
	}
	if (!r->ok) {
		const char *why = r->code && !strcmp(r->code, "E_STALE") ?
		    "Cisco catalogue or reader pack changed. Retry to reload the catalogue." :
		    "The catalogue request failed. Retry to keep your place.";
		if (which == RESTORE) { B.results = 0; nav_notice_text(a, "Cisco Results", why); return; }
		if (r->code && !strcmp(r->code, "E_STALE")) {
			memset(&B.status, 0, sizeof B.status);
			clear_from(DOCS);
			which = STATUS; index = 0;
		}
		failed(which, index, why);
	} else if (which < 0) {
		cisco_status status;
		if (cisco_status_decode(&status, r->result) || (which == RESTORE &&
		    (strcmp(status.generation, B.status.generation) || !same_stores(&status)))) {
			if (which == RESTORE) {
				B.results = 0;
				nav_notice_text(a, "Cisco Results", "The saved catalogue or reader generation changed.");
				return;
			}
			failed(which, index, "The helper returned invalid Cisco catalogue status. Retry.");
		} else {
			B.status = status;
			if (which == RESTORE) {
				B.active = 1;
				pin_cisco_restored(a);
				focus_tree(a); nav_redraw(a);
				return;
			}
			if (!status.store[0].ready && !status.store[1].ready) {
				failed(STATUS, 0, "Cisco publications are unavailable. Retry or select another Type.");
				if (finder_front(a)) pin_cisco_controls(a);
				nav_redraw(a);
				return;
			}
			request(a, DOCS, 0);
			return;
		}
	} else {
		const cisco_row *parent = which == PARTS ? contents() : NULL;
		enum cisco_page_kind kind = which == PARTS ? CISCO_PARTS : which == SECTIONS ? CISCO_SECTIONS :
		    *B.query ? CISCO_HITS : CISCO_DOCUMENTS;
		cisco_status status = B.status;
		const char *generation = json_str(r->result, "catalogue_generation");
		const jv *stores = json_get(r->result, "stores");
		int is_page = kind == CISCO_DOCUMENTS || kind == CISCO_PARTS;
		cisco_page *page = NULL;
		cisco_cursor *at = &B.page[which].history[index];
		if ((!is_page || (generation && stores)) && (!generation || !strcmp(generation, status.generation)) &&
		    (!stores || !cisco_stores_decode(&status, stores)))
			page = cisco_page_decode(r->result, kind, &status, parent, B.subtype);
		if (!page || (index && !page->count) || (*page->next.key && index && !advances(&page->next, at)) ||
		    (page->block_next >= 0 && index && page->block_next <= at->block)) {
			free(page);
			failed(which, index, "The helper returned an invalid Cisco page. Retry.");
		} else {
			int replaced = B.page[which].page != NULL;
			B.status = status;
			if (which != SECTIONS) clear_from(which + 1);
			free(B.page[which].page);
			B.page[which].page = page; B.page[which].index = index;
			if (which == DOCS) B.top = B.selected = 0;
			else if (replaced) select_first(which == PARTS ? NODE_PART : NODE_SECTION);
			result_status(which, page);
		}
	}
	if (finder_front(a)) pin_cisco_controls(a);
	nav_redraw(a);
}

static void pair(e26j_buf *b, const char *key, const char *value)
{
	e26j_lit(b, ","); e26j_cstr(b, key); e26j_lit(b, ":"); e26j_cstr(b, value);
}

static void request(e26_app *a, int which, int index)
{
	e26j_buf b;
	char *args;
	const char *op;
	const cisco_row *owner;
	pending *p;
	unsigned serial;
	uint64_t id;
	cancel(); B.failed = 0;
	e26j_init(&b);
	if (which < 0) { op = "finder.cisco.status"; e26j_lit(&b, "{}"); }
	else {
		if (index < 0 || index >= HISTORY || !*B.status.generation) return;
		e26j_lit(&b, "{\"catalogue_generation\":"); e26j_cstr(&b, B.status.generation);
		if (which == SECTIONS) {
			op = "finder.cisco.sections";
			owner = section_owner();
			if (!owner) { free(e26j_take(&b)); return; }
			pair(&b, "catalogue_id", owner->route.key);
			pair(&b, "reader_pack", cisco_pack(owner->route.pack));
			pair(&b, "reader_generation", owner->route.generation);
			pair(&b, "article_id", owner->route.article);
			if (index) { e26j_lit(&b, ",\"cursor\":"); e26j_u(&b, (unsigned)B.page[which].history[index].block); }
		} else if (which == DOCS && *B.query) {
			op = "finder.cisco.search";
			pair(&b, "query", B.query);
		} else {
			op = "finder.cisco.page";
			if (which == PARTS) {
				owner = contents();
				if (!owner) { free(e26j_take(&b)); return; }
				pair(&b, "parent", owner->route.key);
			}
			if (index) {
				const cisco_cursor *c = &B.page[which].history[index];
				e26j_lit(&b, ",\"cursor\":{");
				if (*c->sort_key) { e26j_lit(&b, "\"sort_key\":"); e26j_cstr(&b, c->sort_key); }
				else { e26j_lit(&b, "\"part\":"); e26j_u(&b, (unsigned)c->part); }
				pair(&b, "catalogue_id", c->key); e26j_lit(&b, "}");
			}
		}
		if (which == DOCS && B.subtype) pair(&b, "subtype", cisco_type_key(B.subtype));
		if (which == DOCS && B.active) pair(&b, "sort", B.sort ? "id" : "title");
		if (which == DOCS && B.current) pair(&b, B.topic_is_area ? "area_id" : "topic_id", B.topic);
		e26j_lit(&b, ",\"limit\":"); e26j_u(&b, which == DOCS && *B.query ? CISCO_SEARCH :
		    !legacy_limits ? CISCO_PAGE : which == SECTIONS ? CISCO_SECTIONS_LEGACY : CISCO_PAGE_LEGACY);
		e26j_lit(&b, "}");
	}
	args = e26j_take(&b); p = malloc(sizeof *p);
	if (!args || !p) { free(args); free(p); failed(which, index, "Cisco request allocation failed. Retry."); return; }
	serial = B.serial;
	*p = (pending){ a, serial, which, index };
	B.busy = 1;
	if (which != RESTORE) message(which == SECTIONS ? "Loading sections..." :
	    which == PARTS ? "Loading guide parts..." : "Loading Cisco catalogue...");
	id = lib_request(a, op, args, receive, p);
	free(args);
	if (serial != B.serial) return;
	B.request = id;
	if (!id) { free(p); B.busy = 0; failed(which, index, "The Cisco helper is unavailable. Retry."); }
	if (finder_front(a)) pin_cisco_controls(a);
}

static void refresh(e26_app *a)
{
	clear_from(DOCS); B.top = B.selected = 0;
	request(a, *B.status.generation ? DOCS : STATUS, 0);
}

static int expandable(const node *n)
{
	return n->kind != NODE_SECTION && (n->kind == NODE_CONTENTS_SECTIONS || n->row->outline ||
	    (n->kind == NODE_DOCUMENT && n->row->parts > 1));
}

static int expanded(const node *n)
{
	if (n->kind == NODE_DOCUMENT) return B.root_open == n->index;
	if (n->kind == NODE_SECTION) return 0;
	return B.sections_open && B.part_open == (n->kind == NODE_PART ? n->index : -1);
}

static void change_branch(e26_app *a, const node *n, int open)
{
	if (!expandable(n) || open == expanded(n)) return;
	cancel(); B.failed = 0;
	if (n->kind == NODE_DOCUMENT) {
		int parts = n->row->parts > 1;
		clear_from(PARTS);
		if (open) {
			B.root_open = n->index;
			B.sections_open = !parts;
			request(a, parts ? PARTS : SECTIONS, 0);
		}
	} else {
		clear_from(SECTIONS);
		if (open) {
			B.part_open = n->kind == NODE_PART ? n->index : -1;
			B.sections_open = 1;
			request(a, SECTIONS, 0);
		}
	}
	if (!open && B.page[DOCS].page) result_status(DOCS, B.page[DOCS].page);
}

static void open_node(e26_app *a, const node *n)
{
	const cisco_row *r = n->kind == NODE_SECTION ? section_owner() : n->row;
	if (n->kind == NODE_CONTENTS_SECTIONS) { change_branch(a, n, !expanded(n)); return; }
	if (!r || B.busy) return;
	B.route_pack = ar_lib_resolve(cisco_pack(r->route.pack));
	if (B.route_pack <= 0) { message("The requested public reader pack is unavailable."); return; }
	B.opened = r->route; B.pending_open = 1; B.confirmed_open = B.results = 0;
	pin_cisco_remember(a);
	art_open_cisco(a, B.route_pack, B.status.generation, r->route.key, r->route.generation,
	    r->route.article, n->kind == NODE_SECTION ? n->row->anchor : NULL);
}

static void article_opened(e26_app *a, const e26_event *ev, void *ud)
{
	(void)a; (void)ud;
	B.results = B.pending_open && B.confirmed_open && ev->name && art_st()->pack == B.route_pack &&
	    !strcmp(ev->name, B.opened.article) && !strcmp(ar_lib_generation(B.route_pack), B.opened.generation);
	B.pending_open = B.confirmed_open = 0;
	if (B.results) pin_forget_legacy_results();
}

int cisco_have_results(void) { return article_matches(); }
void cisco_forget_results(void) { B.pending_open = B.confirmed_open = B.results = 0; }
void cisco_confirm_open(void) { B.confirmed_open = 1; }
int cisco_return_results(e26_app *a)
{
	if (!article_matches() || !front(a, "article")) return 0;
	if (!B.busy) request(a, RESTORE, 0);
	return 1;
}

void cisco_pin_search(e26_app *a, int subtype, int sort, const char *topic, int area, const char *query)
{
	B.active = 1;
	B.subtype = subtype;
	B.sort = sort;
	B.topic_is_area = area;
	B.current = topic && *topic;
	snprintf(B.topic, sizeof B.topic, "%s", B.current ? topic : "");
	snprintf(B.query, sizeof B.query, "%s", query);
	refresh(a);
}

void cisco_pin_leave(void) { if (B.active) { cancel(); B.active = 0; } }
const char *cisco_pin_status(void) { return B.message; }
int cisco_pin_can_retry(void) { return B.failed && !B.busy; }
int cisco_pin_retry(e26_app *a)
{
	if (!cisco_pin_can_retry()) return 0;
	request(a, B.retry_pane, B.retry_index);
	return 1;
}

static int search(e26_app *a, const e26_actx *x)
{
	const char *text = field_text(x && x->s ? x->s : a->cur, "query");
	if (text) sel_cp1252_to_utf8(text, strlen(text), B.query, sizeof B.query);
	refresh(a); return 1;
}

static int subtype(e26_app *a, const e26_actx *x)
{
	int v = cisco_type_index(x->arg);
	if (v >= 0 && v != B.subtype) { B.subtype = v; refresh(a); focus_tree(a); }
	return 1;
}

static int scope(e26_app *a, const e26_actx *x)
{
	int current = !strcmp(x->arg, "current");
	if (current && !*B.topic) { message("Choose a Library topic before using Current topic."); return 1; }
	if (current != B.current) { B.current = current; refresh(a); }
	return 1;
}

static int selected_pane(void)
{
	node nodes[MAX_VISIBLE];
	int n = flatten(nodes);
	if (B.selected < 0 || B.selected >= n) return DOCS;
	if (nodes[B.selected].kind == NODE_SECTION) return SECTIONS;
	return nodes[B.selected].kind == NODE_PART ? PARTS : DOCS;
}

int cisco_pin_can_page(int step)
{
	int which = selected_pane();
	const pane *p = &B.page[which];
	int index = p->index + step;
	if (B.busy || !p->page || index < 0 || index >= HISTORY) return 0;
	return step < 0 || (which == SECTIONS ? p->page->block_next >= 0 : *p->page->next.key);
}

int cisco_pin_page(e26_app *a, int step)
{
	int which = selected_pane();
	pane *p = &B.page[which];
	int index = p->index + step;
	if (!cisco_pin_can_page(step)) return 0;
	if (step > 0) {
		if (which == SECTIONS) {
			if (p->page->block_next < 0) return 1;
			p->history[index].block = p->page->block_next;
		} else {
			if (!*p->page->next.key) return 1;
			p->history[index] = p->page->next;
		}
	}
	request(a, which, index); return 1;
}

static int previous(e26_app *a, const e26_actx *x) { (void)x; return cisco_pin_page(a, -1); }
static int next(e26_app *a, const e26_actx *x) { (void)x; return cisco_pin_page(a, 1); }
static int retry(e26_app *a, const e26_actx *x) { (void)x; if (B.failed && !B.busy) request(a, B.retry_pane, B.retry_index); return 1; }
static int close_room(e26_app *a, const e26_actx *x)
{
	(void)x;
	cancel();
	if (B.pending_open) { art_fetch_cancel(); cisco_forget_results(); }
	nav_close_overlay(a); return 0;
}

static void screen_entered(e26_app *a, const e26_event *ev, void *ud)
{
	static int was_visible;
	int visible = finder_front(a) || front(a, "pinpointer-cisco");
	(void)ev; (void)ud;
	if (was_visible && !visible) {
		cancel();
		if (B.pending_open) { art_fetch_cancel(); cisco_forget_results(); }
	}
	was_visible = visible;
}

static const char *status_label(e26_app *a, const scr_control *c, char *out, size_t n)
{ (void)a; (void)c; (void)out; (void)n; return B.message; }
static const char *type_label(e26_app *a, const scr_control *c, char *out, size_t n)
{ (void)a; (void)c; (void)out; (void)n; return cisco_type_label(B.subtype); }
static int scope_checked(e26_app *a, const scr_control *c)
{ const char *s = scr_prop_get(c, "scope"); (void)a; return s && ((!strcmp(s, "current")) == B.current); }

static void text(gfx_ctx *g, int x, int y, int width, int font, long ink, const char *s)
{
	char out[CISCO_TEXT];
	int lo = 0, hi;
	sel_utf8_to_cp1252(s, strlen(s), out, sizeof out);
	hi = (int)strlen(out);
	while (lo < hi) {
		int mid = lo + (hi - lo + 1) / 2;
		if (gfx_text_width(g, font, out, mid) <= width) lo = mid;
		else hi = mid - 1;
	}
	if (lo) gfx_text(g, x, y, font, ink, out, lo);
}

static int indent(const node *n)
{
	if (n->kind == NODE_DOCUMENT) return 0;
	if (n->kind == NODE_SECTION) {
		const cisco_row *parent = contents();
		return (parent && parent->parts > 1 ? 2 : 1) + (n->row->level > 4 ? 3 : n->row->level - 1);
	}
	return 1;
}

/* u14 (spec M17): a guide's title without its trailing " (chapters: ...)" note, which moves to the meta line */
static void split_title(const char *title, char *head, size_t hn, char *note, size_t nn)
{
	const char *at = NULL, *q;
	size_t len;
	for (q = title; (q = strstr(q, " (chapter")) != NULL; q++)
		if (!strncmp(q, " (chapter: ", 11) || !strncmp(q, " (chapters: ", 12)) at = q;
	note[0] = 0;
	if (!at) { snprintf(head, hn, "%s", title); return; }
	snprintf(head, hn, "%.*s", (int)(at - title), title);
	snprintf(note, nn, "%s", at + 2);
	len = strlen(note);
	if (len && note[len - 1] == ')') note[len - 1] = 0;
}

/* Documents and parts: the title, then a quiet meta line "<type> · <product> · <release>[ · N parts][ · chapters:
 * ...]" (the +/- shows whether it is open). Sections and "Contents sections": the title alone on one line centred
 * in the row, each level of the book's contents further in. */
static void draw(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int x, int y, int under)
{
	const scr_control *c = &s->c[i];
	node nodes[MAX_VISIBLE];
	int k, n = flatten(nodes), native = pin_native(a, s), row_y = y;
	int head_font = native ? F_RUI : F_UI_BOLD, line_font = native ? F_RUI : F_UI, meta_font = native ? F_RUI : F_UI10;
	int mark_font = native ? F_RUI_BOLD : F_UI_BOLD, gap = native ? 18 : 14;
	int asc = gfx_ascent(g, line_font), desc = gfx_descent(g, line_font), lh = asc + desc + 2;
	long quiet = native ? PIN_QUIET : C_BLACK;
	(void)under;
	keep_visible(a, s, nodes, n, c->h);
	gfx_fill(g, x, y, c->w, c->h, C_WHITE);
	gfx_clip(g, x, y, c->w, c->h);
	for (k = B.top; k < n && row_y + node_h(a, s, &nodes[k]) <= y + c->h; row_y += node_h(a, s, &nodes[k]), ++k) {
		const node *v = &nodes[k];
		const cisco_row *r = v->row;
		int dx = 6 + step(a, s) * indent(v), rh = node_h(a, s, v), one = v->kind == NODE_SECTION ||
		    v->kind == NODE_CONTENTS_SECTIONS;
		int first = one ? row_y + (rh + asc - desc) / 2 : native ? row_y + 4 + asc : row_y + 13;
		char title[CISCO_TEXT], note[CISCO_TEXT], meta[CISCO_TEXT * 3 + 160];
		if (k == B.selected) gfx_fill(g, x + 1, row_y, c->w - 2, rh - 1, ui_rgb(a, UI_STONE));
		if (expandable(v)) gfx_text(g, x + dx, first, mark_font, C_BLACK, expanded(v) ? "-" : "+", 1);
		if (one) {
			text(g, x + dx + gap, first, c->w - dx - gap - 6, line_font, C_BLACK,
			    v->kind == NODE_CONTENTS_SECTIONS ? "Contents sections" : r->title);
			continue;
		}
		split_title(r->title, title, sizeof title, note, sizeof note);
		snprintf(meta, sizeof meta, "%s", cisco_type_label(r->type));
		if (*r->product) snprintf(meta + strlen(meta), sizeof meta - strlen(meta), " \xc2\xb7 %s", r->product);
		if (*r->release) snprintf(meta + strlen(meta), sizeof meta - strlen(meta), " \xc2\xb7 %s", r->release);
		if (v->kind == NODE_DOCUMENT && r->parts > 1)
			snprintf(meta + strlen(meta), sizeof meta - strlen(meta), " \xc2\xb7 %d parts", r->parts);
		if (*note) snprintf(meta + strlen(meta), sizeof meta - strlen(meta), " \xc2\xb7 %s", note);
		text(g, x + dx + gap, first, c->w - dx - gap - 6, head_font, C_BLACK, title);
		text(g, x + dx + gap, native ? first + lh : row_y + 26, c->w - dx - gap - 6, meta_font, quiet, meta);
	}
	gfx_unclip(g);
}

static int press(e26_app *a, scr_screen *s, int i, int x, int y)
{
	node nodes[MAX_VISIBLE];
	int n = flatten(nodes), row = row_at(a, s, nodes, n, s->c[i].h, y - s->oy - s->c[i].y);
	(void)x;
	B.press_row = -1;
	if (!B.busy && row >= 0) {
		B.press_serial = B.serial; B.press_row = B.selected = row; a->pressed = i;
	}
	if (finder_front(a)) pin_cisco_controls(a);
	nav_redraw(a); return 1;
}

static int release_node(e26_app *a, scr_screen *s, int i, int x, int y)
{
	node nodes[MAX_VISIBLE];
	int n = flatten(nodes), row = row_at(a, s, nodes, n, s->c[i].h, y - s->oy - s->c[i].y);
	int pressed = B.press_row;
	B.press_row = -1;
	if (!B.busy && B.press_serial == B.serial && row >= 0 && row == B.selected && row == pressed) {
		node *v = &nodes[row];
		if (x - s->ox - s->c[i].x < (pin_native(a, s) ? 26 : 20) + step(a, s) * indent(v) && expandable(v))
			change_branch(a, v, !expanded(v));
		else open_node(a, v);
	}
	nav_redraw(a);
	return 1;
}

static int key(e26_app *a, scr_screen *s, int i, KeySym ks)
{
	node nodes[MAX_VISIBLE];
	int n = flatten(nodes), slots, k;
	keep_visible(a, s, nodes, n, s->c[i].h);
	slots = fitting(a, s, nodes, n, B.top, s->c[i].h);
	if (ks == XK_Up) --B.selected;
	else if (ks == XK_Down) ++B.selected;
	else if (ks == XK_Prior) B.selected -= slots;
	else if (ks == XK_Next) B.selected += slots;
	else if (ks == XK_Home) B.selected = 0;
	else if (ks == XK_End) B.selected = n - 1;
	else if (!n) return 0;
	else if (ks == XK_Return || ks == XK_KP_Enter) open_node(a, &nodes[B.selected]);
	else if (ks == XK_Right || ks == XK_KP_Right || ks == XK_plus || ks == XK_KP_Add)
		change_branch(a, &nodes[B.selected], 1);
	else if (ks == XK_Left || ks == XK_KP_Left || ks == XK_minus || ks == XK_KP_Subtract) {
		if (expanded(&nodes[B.selected])) change_branch(a, &nodes[B.selected], 0);
		else for (k = B.selected - 1; k >= 0; --k) {
			if (nodes[k].kind != NODE_SECTION && indent(&nodes[k]) < indent(&nodes[B.selected])) {
				B.selected = k; break;
			}
		}
	} else return 0;
	keep_visible(a, s, nodes, flatten(nodes), s->c[i].h);
	if (finder_front(a)) pin_cisco_controls(a);
	return 1;
}

int cisco_pin_key(e26_app *a, KeySym ks)
{
	scr_control *list;
	if (!finder_front(a) || !(list = scr_control_find(a->cur, "list"))) return 0;
	return key(a, a->cur, (int)(list - a->cur->c), ks);
}

static int open_selected(e26_app *a, const e26_actx *x)
{
	if (!x || !x->s || x->s != a->cur || x->ctl < 0 || x->ctl >= x->s->nc ||
	    strcmp(x->s->c[x->ctl].id, "tree")) return 0;
	return key(a, x->s, x->ctl, XK_Return);
}

static int key_focus(e26_app *a, scr_screen *s, int i, XKeyEvent *event, KeySym ks)
{
	if (event->state & (ControlMask | Mod1Mask) || focus_get(a, s) != i || !key(a, s, i, ks)) return 0;
	nav_redraw(a);
	return 1;
}

static int key_late(e26_app *a, scr_screen *s, int i, KeySym ks)
{
	/* The Pinpointer's search field is the first key_focus kind. Arrow keys
	 * fall through it to the late handler when the tree owns focus. */
	if (focus_get(a, s) != i || !key(a, s, i, ks)) return 0;
	nav_redraw(a);
	return 1;
}
static int wheel(e26_app *a, scr_screen *s, int i, int dir)
{ return key(a, s, i, dir < 0 ? XK_Up : XK_Down); }

void e26_mod_room_cisco(void);
void e26_mod_room_cisco(void)
{
	static const kind_ops tree = { draw, 1, press, release_node, NULL, wheel, NULL, key_focus, key_late };
	static const e26_action q = { search }, t = { subtype }, sc = { scope }, prev = { previous },
	    nx = { next }, again = { retry }, back = { close_room }, open = { open_selected };
	static const source_ops st = { status_label, NULL, NULL }, ty = { type_label, NULL, NULL },
	    current = { NULL, scope_checked, NULL };
	reg_add(REG_KIND, "cisco-tree", &tree, "room_cisco", 0);
	reg_add(REG_ACTION, "cisco.search", &q, "room_cisco", 0);
	reg_add(REG_ACTION, "cisco.type", &t, "room_cisco", 0);
	reg_add(REG_ACTION, "cisco.scope", &sc, "room_cisco", 0);
	reg_add(REG_ACTION, "cisco.previous", &prev, "room_cisco", 0);
	reg_add(REG_ACTION, "cisco.next", &nx, "room_cisco", 0);
	reg_add(REG_ACTION, "cisco.retry", &again, "room_cisco", 0);
	reg_add(REG_ACTION, "cisco.close", &back, "room_cisco", 0);
	reg_add(REG_ACTION, "cisco.open", &open, "room_cisco", 0);
	reg_add(REG_SOURCE, "cisco-status", &st, "room_cisco", 0);
	reg_add(REG_SOURCE, "cisco-type", &ty, "room_cisco", 0);
	reg_add(REG_SOURCE, "cisco-scope", &current, "room_cisco", 0);
	(void)ev_subscribe(EV_ARTICLE_OPEN, article_opened, NULL);
	(void)ev_subscribe(EV_SCREEN_ENTER, screen_entered, NULL);
}
