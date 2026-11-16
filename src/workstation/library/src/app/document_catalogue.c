#include "document_catalogue.h"
#include "../common/e26j.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *const packs[] = { "lib-e26", "lib-corpus" };
static const struct { const char *key, *label; } types[] = {
	{ "All", "All Cisco publications" }, { "ConfigGuide", "Configuration Guides" },
	{ "WhitePaper", "White Papers" }, { "ValidatedDesign", "Validated Designs" },
	{ "TechNote", "Cisco Support / TechNotes" }, { "Other", "Other" }
};

const char *cisco_pack(int pack) { return pack >= 0 && pack < 2 ? packs[pack] : ""; }
const char *cisco_type_key(int type) { return types[type >= 0 && type < CISCO_TYPES ? type : 0].key; }
const char *cisco_type_label(int type) { return types[type >= 0 && type < CISCO_TYPES ? type : 0].label; }
int cisco_type_index(const char *key)
{
	int i;
	for (i = 0; key && i < CISCO_TYPES; ++i) if (!strcmp(key, types[i].key)) return i;
	return -1;
}

static int pack_index(const char *name)
{
	int i;
	for (i = 0; name && i < 2; ++i) if (!strcmp(name, packs[i])) return i;
	return -1;
}

static int is(const jv *value, const char *key, const char *expected)
{
	const char *s = json_str(value, key);
	return s && !strcmp(s, expected);
}

static int copy_text(const jv *v, char *out, size_t cap, int nullable)
{
	size_t n, i;
	if (nullable && v && v->type == J_NULL) { out[0] = 0; return 0; }
	if (!v || v->type != J_STR || !(n = strlen(v->str)) || n >= cap) return -1;
	for (i = 0; i < n; ++i) if ((unsigned char)v->str[i] < 32 || v->str[i] == 127) return -1;
	memcpy(out, v->str, n + 1);
	return 0;
}

static int token(const char *s, const char *allowed)
{
	return *s && strspn(s, allowed) == strlen(s);
}

static int generation(const jv *v, char out[CISCO_GEN])
{
	return copy_text(v, out, CISCO_GEN, 0) ||
	    !token(out, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-");
}

static int number(const jv *v, int min, int max, int nullable, int *out)
{
	int64_t n;
	if (nullable && v && v->type == J_NULL) { *out = 0; return 0; }
	if (e26j_i64(v, min, max, &n)) return -1;
	*out = (int)n;
	return 0;
}

int cisco_stores_decode(cisco_status *out, const jv *stores)
{
	cisco_status status = *out;
	int seen = 0;
	size_t i;
	if (!stores || stores->type != J_ARR || stores->n != 2) return -1;
	for (i = 0; i < stores->n; ++i) {
		const jv *s = stores->items[i];
		int pack = pack_index(json_str(s, "reader_pack"));
		char expected[CISCO_GEN];
		const char *state = json_str(s, "state"), *installed = json_str(s, "installed_generation");
		if (pack < 0 || (seen & (1 << pack)) || !state ||
		    generation(json_get(s, "reader_generation"), expected) ||
		    (*status.store[pack].generation && strcmp(status.store[pack].generation, expected))) return -1;
		memcpy(status.store[pack].generation, expected, strlen(expected) + 1);
		seen |= 1 << pack;
		status.store[pack].ready = 0;
		if (!strcmp(state, "ready")) {
			if (!installed || strcmp(installed, status.store[pack].generation)) return -1;
			status.store[pack].ready = 1;
		} else if (strcmp(state, "stale") && strcmp(state, "missing")) return -1;
	}
	*out = status;
	return 0;
}

int cisco_status_decode(cisco_status *out, const jv *value)
{
	cisco_status status = {0};
	if (!is(value, "catalogue_id", "cisco-public") ||
	    generation(json_get(value, "catalogue_generation"), status.generation) ||
	    cisco_stores_decode(&status, json_get(value, "stores"))) return -1;
	*out = status;
	return 0;
}

static int decode_document(cisco_row *row, const jv *v, const cisco_status *status, const cisco_row *parent, int subtype)
{
	char key[CISCO_KEY], parts_of[CISCO_TEXT];
	const jv *outline = json_get(v, "has_outline"), *opt_in = json_get(v, "opt_in");
	const jv *handling = json_get(v, "handling");
	row->route.pack = pack_index(json_str(v, "reader_pack"));
	row->type = cisco_type_index(json_str(v, "subtype"));
	if (row->route.pack < 0 || !status->store[row->route.pack].ready ||
	    !is(v, "doc_type", "cisco") || (handling && !is(v, "handling", "PUBLIC")) ||
	    (opt_in && (opt_in->type != J_BOOL || opt_in->b)) ||
	    row->type <= CISCO_ALL || (subtype && row->type != subtype) ||
	    !outline || (outline->type != J_NULL && outline->type != J_BOOL) ||
	    generation(json_get(v, "reader_generation"), row->route.generation) ||
	    strcmp(row->route.generation, status->store[row->route.pack].generation) ||
	    copy_text(json_get(v, "catalogue_id"), row->route.key, sizeof row->route.key, 0) ||
	    copy_text(json_get(v, "article_id"), row->route.article, sizeof row->route.article, 0) ||
	    !token(row->route.article, "abcdefghijklmnopqrstuvwxyz0123456789._:-") ||
	    strspn(row->route.article, "abcdefghijklmnopqrstuvwxyz0123456789") == 0 ||
	    copy_text(json_get(v, "doc_key"), row->doc_key, sizeof row->doc_key, 0) ||
	    copy_text(json_get(v, "parts_of"), parts_of, sizeof parts_of, 1) ||
	    copy_text(json_get(v, "title"), row->title, sizeof row->title, 0) ||
	    copy_text(json_get(v, "product"), row->product, sizeof row->product, 1) ||
	    copy_text(json_get(v, "release"), row->release, sizeof row->release, 1) ||
	    number(json_get(v, "part"), 0, 4096, 1, &row->part) ||
	    number(json_get(v, "parts"), 1, 4096, 1, &row->parts)) return -1;
	snprintf(key, sizeof key, "%s:%s", cisco_pack(row->route.pack), row->route.article);
	if (strcmp(key, row->route.key) ||
	    (row->type == CISCO_TECHNOTE && !is(v, "publisher_label", "Cisco Support / TechNotes"))) return -1;
	if (parent ? (!*parts_of || strcmp(parts_of, parent->doc_key) ||
	    row->route.pack != parent->route.pack || strcmp(row->route.generation, parent->route.generation) ||
	    !row->part || row->part > parent->parts) : *parts_of) return -1;
	row->outline = outline->type == J_NULL || outline->b;
	return 0;
}

cisco_page *cisco_page_decode(const jv *value, enum cisco_page_kind kind,
    const cisco_status *status, const cisco_row *parent, int subtype)
{
	const jv *rows = json_get(value, "rows"), *next = json_get(value, "next");
	cisco_page *page;
	size_t i, j;
	if (!rows || rows->type != J_ARR || (kind == CISCO_PARTS && !parent) ||
	    rows->n > (kind == CISCO_HITS ? CISCO_SEARCH : CISCO_PAGE)) return NULL;
	page = calloc(1, sizeof *page + rows->n * sizeof *page->row);
	if (!page) return NULL;
	page->count = (int)rows->n;
	page->block_next = -1;
	if (!next && (kind == CISCO_DOCUMENTS || kind == CISCO_PARTS)) next = json_get(value, "next_cursor");
	if (kind == CISCO_HITS) {
		const jv *refine = json_get(value, "refine");
		int count;
		if (!refine || refine->type != J_BOOL ||
		    number(json_get(value, "count"), 0, CISCO_SEARCH, 0, &count) || count != page->count) goto bad;
		page->refine = refine->b;
	} else if (!next || (!page->count && next->type != J_NULL)) goto bad;
	else if (kind == CISCO_SECTIONS) {
		if (next->type != J_NULL && number(next, 0, INT32_MAX, 0, &page->block_next)) goto bad;
	} else if (next->type != J_NULL) {
		if (next->type != J_OBJ || copy_text(json_get(next, "catalogue_id"),
		    page->next.key, sizeof page->next.key, 0)) goto bad;
		if (kind == CISCO_PARTS && json_get(next, "part")) {
			if (number(json_get(next, "part"), 1, 4096, 0, &page->next.part)) goto bad;
		} else if (copy_text(json_get(next, "sort_key"), page->next.sort_key,
		    sizeof page->next.sort_key, 0)) goto bad;
	}
	for (i = 0; i < rows->n; ++i) {
		cisco_row *row = &page->row[i];
		const jv *v = rows->items[i];
		if (kind == CISCO_SECTIONS) {
			if (copy_text(json_get(v, "anchor"), row->anchor, sizeof row->anchor, 0) ||
			    !token(row->anchor, "abcdefghijklmnopqrstuvwxyz0123456789._-") ||
			    copy_text(json_get(v, "title"), row->title, sizeof row->title, 0) ||
			    number(json_get(v, "level"), 1, 16, 0, &row->level) ||
			    number(json_get(v, "block_index"), 0, INT32_MAX, 0, &row->block) ||
			    (i && row->block <= page->row[i - 1].block)) goto bad;
		} else {
			if (decode_document(row, v, status, kind == CISCO_PARTS ? parent : NULL,
			    kind == CISCO_PARTS ? 0 : subtype)) goto bad;
			for (j = 0; j < i; ++j) if (!strcmp(page->row[j].route.key, row->route.key)) goto bad;
		}
	}
	if (*page->next.key && (strcmp(page->next.key, page->row[rows->n - 1].route.key) ||
	    (page->next.part && page->next.part != page->row[rows->n - 1].part))) goto bad;
	if (*page->next.key && kind == CISCO_PARTS) page->next.part = page->row[rows->n - 1].part;
	if (page->block_next >= 0 && page->block_next != page->row[rows->n - 1].block) goto bad;
	return page;
bad:
	free(page);
	return NULL;
}
