/* article_model.c: room state operations, independent of X and filesystem paths. */
#include "article_model.h"
#include "cite.h"
#include "../common/sha256.h"
#include <ctype.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#define AR_MAX (32u * 1024u * 1024u)

void ar_json(e26j_buf *b, const jv *v)
{
	size_t i;
	if (!v) { b->bad = 1; return; }
	switch (v->type) {
	case J_NULL: e26j_lit(b, "null"); break;
	case J_BOOL: e26j_lit(b, v->b ? "true" : "false"); break;
	case J_NUM: if (v->str) e26j_lit(b, v->str); else b->bad = 1; break;
	case J_STR: e26j_cstr(b, v->str); break;
	case J_ARR: case J_OBJ:
		e26j_lit(b, v->type == J_ARR ? "[" : "{");
		for (i = 0; i < v->n; i++) {
			if (i) e26j_lit(b, ",");
			if (v->type == J_OBJ) { e26j_cstr(b, v->keys[i]); e26j_lit(b, ":"); }
			ar_json(b, v->items[i]);
		}
		e26j_lit(b, v->type == J_ARR ? "]" : "}");
		break;
	default: b->bad = 1; break;
	}
}

int ar_wire_page(ar_wire *w, const jv *page)
{
	const jv *meta = json_get(page, "meta"), *blocks = json_get(page, "blocks"), *next = json_get(page, "next");
	uint64_t start, total, to;
	e26j_buf m;
	size_t i, j;
	const char *hash;
	if (w->complete || !meta || meta->type != J_OBJ || !blocks || blocks->type != J_ARR ||
	    e26j_u64(json_get(page, "start"), &start) || e26j_u64(json_get(page, "total"), &total) ||
	    !total || total > 65536 || start != w->next || start > total || blocks->n > 256 || blocks->n > total - start || !next) return -1;
	to = start + blocks->n;
	if ((next->type == J_NULL && to != total) || (next->type != J_NULL &&
	    (e26j_u64(next, &to) || !blocks->n || to != start + blocks->n || to >= total))) return -1;
	e26j_init(&m);
	ar_json(&m, meta);
	if (m.bad) { e26j_free(&m); return -1; }
	if (!w->begun) {
		hash = json_str(meta, "content_sha256");
		if (start || !hash || strlen(hash) != 64) { e26j_free(&m); return -1; }
		for (i = 0; i < 64; i++) if (!((hash[i] >= '0' && hash[i] <= '9') || (hash[i] >= 'a' && hash[i] <= 'f'))) { e26j_free(&m); return -1; }
		memcpy(w->source_hash, hash, 65);
		w->meta = m;
		w->total = (size_t)total;
		w->begun = 1;
	} else {
		int same = w->total == total && m.n == w->meta.n && !memcmp(m.p, w->meta.p, m.n);
		e26j_free(&m);
		if (!same) return -1;
	}
	for (i = 0; i < blocks->n; i++) {
		const jv *bl = blocks->items[i];
		const char *kind = json_str(bl, "type");
		int fields = 0, known = 0, k;
		for (k = 0; kind && k < A2_NKINDS; k++) if (!strcmp(kind, a2_kind_name(k))) known = 1;
		if (!known || bl->type != J_OBJ) return -1;
		e26j_lit(&w->body, kind); e26j_lit(&w->body, "\t{");
		for (j = 0; j < bl->n; j++) if (strcmp(bl->keys[j], "type")) {
			if (fields++) e26j_lit(&w->body, ",");
			e26j_cstr(&w->body, bl->keys[j]); e26j_lit(&w->body, ":"); ar_json(&w->body, bl->items[j]);
		}
		e26j_lit(&w->body, "}\n");
		if (w->body.bad || w->body.n + w->meta.n + 32 > AR_MAX) return -1;
	}
	w->next = (size_t)(start + blocks->n);
	w->complete = next->type == J_NULL;
	return w->complete;
}

int ar_wire_doc(ar_wire *w, a2_doc *d, char *err, size_t n)
{
	char hash[65], code[32], *p;
	e26j_buf file;
	jv *meta;
	size_t i;
	int rc;
	if (!w->complete) { snprintf(err, n, "The article is incomplete."); return -1; }
	meta = e26j_parse(w->meta.p, w->meta.n, err, n);
	if (!meta) return -1;
	sha256_hex(w->body.p, w->body.n, hash);
	for (i = 0; i < meta->n; i++) if (!strcmp(meta->keys[i], "content_sha256")) memcpy(meta->items[i]->str, hash, 65);
	e26j_init(&file);
	e26j_lit(&file, "e26art 2\nmeta\t"); ar_json(&file, meta); e26j_lit(&file, "\n");
	e26j_raw(&file, w->body.p, w->body.n);
	json_free(meta);
	if (file.bad || !(p = e26j_take(&file))) { e26j_free(&file); snprintf(err, n, "There is not enough memory for this article."); return -1; }
	/* DATA verified the source-byte hash before serving it. Wire JSON has different whitespace and escaping:
	 * validate the reconstructed record hash, then restore the original identity for provenance/history. */
	rc = a2_parse(p, strlen(p), d, code, sizeof code, err, n);
	free(p);
	if (!rc) memcpy((char *)d->content_sha256, w->source_hash, 65);
	return rc;
}

void ar_wire_free(ar_wire *w)
{
	e26j_free(&w->meta); e26j_free(&w->body); memset(w, 0, sizeof *w);
}

/* u14 M11: in table-of-contents mode the outline lists the headings to level 4 that are not minor (Procedure, Step n:
 * layout.c flags them), and figures, tables and code only when t->exhibits is set (OutlineExhibits) */
static int toc_hides(const ar_tree *t, const lay_entry *e)
{
	if (!t->toc) return 0;
	if (e->kind != LE_HEADING) return !t->exhibits;
	return e->level > 4 || e->minor;
}

int ar_tree_build(ar_tree *t, const lay_doc *L, int reset)
{
	int i, hidden = 0;
	if (L->nen > t->capacity) {
		int *v = (int *)malloc((size_t)L->nen * sizeof *v);
		unsigned char *c = (unsigned char *)calloc((size_t)L->nen, 1);
		if (!v || !c) { free(v); free(c); return -1; }
		if (!reset && t->capacity) memcpy(c, t->closed, (size_t)t->capacity);
		free(t->visible); free(t->closed); t->visible = v; t->closed = c; t->capacity = L->nen;
	}
	if (reset && t->capacity) memset(t->closed, 0, (size_t)t->capacity);
	t->n = 0;
	for (i = 0; i < L->nen; i++) {
		const lay_entry *e = &L->en[i];
		if (hidden && e->kind == LE_HEADING && e->level <= hidden) hidden = 0;
		if (hidden || toc_hides(t, e)) continue;
		t->visible[t->n++] = i;
		if (t->closed[i] && e->kind == LE_HEADING) hidden = e->level;
	}
	if (!t->n && L->nen && t->toc && !t->exhibits) {
		/* u14: a document with no heading to list shows its figures, tables and code rather than an empty pane */
		int r;
		t->exhibits = 1;
		r = ar_tree_build(t, L, 0);
		t->exhibits = 0;
		return r;
	}
	return 0;
}

int ar_tree_heading_row(const ar_tree *t, const lay_doc *L, int entry)
{
	int row = ar_tree_row(t, entry);
	while (row >= 0 && L->en[t->visible[row]].kind != LE_HEADING) row--;
	return row;
}

int ar_tree_toggle(ar_tree *t, const lay_doc *L, int entry)
{
	if (entry < 0 || entry >= L->nen || entry >= t->capacity || L->en[entry].kind != LE_HEADING) return 0;
	t->closed[entry] = !t->closed[entry];
	return ar_tree_build(t, L, 0) ? -1 : 1;
}

int ar_tree_row(const ar_tree *t, int entry)
{
	int i, prev = -1;
	for (i = 0; i < t->n; i++) { if (t->visible[i] > entry) break; prev = i; }
	return prev;
}

void ar_tree_free(ar_tree *t) { free(t->visible); free(t->closed); memset(t, 0, sizeof *t); }

int ar_page_number(const char *s)
{
	long v = 0;
	if (!s) return 0;
	if (!strncmp(s, "page-", 5) || !strncmp(s, "page.", 5)) s += 5;
	else if (!strncmp(s, "page", 4)) s += 4;
	else if (*s == 'p') s++;
	else return 0;
	if (!isdigit((unsigned char)*s)) return 0;
	for (; isdigit((unsigned char)*s); s++) { v = v * 10 + *s - '0'; if (v > 1000000) return 0; }
	return !*s ? (int)v : 0;
}

static int y_for_pos(const lay_doc *L, int pos)
{
	int i;
	for (i = 0; i < L->nit; i++) if (L->it[i].src >= pos && L->it[i].line >= 0) return L->ln[L->it[i].line].top;
	return L->h;
}

int ar_page_y(const lay_doc *L, const a2_doc *d, int page)
{
	int i, count = 1, explicit_pages = 0;
	size_t p;
	(void)d;
	if (page < 1) return -1;
	for (i = 0; i < L->nan; i++) {
		int number = L->an[i].kind == LA_HEADING ? ar_page_number(L->an[i].name) : 0;
		if (number == page) return L->an[i].y;
		if (number) explicit_pages = 1;
	}
	if (explicit_pages) return -1;
	/* The first retained physical page is 1. Explicit source page anchors take priority over this numbering. */
	if (page == 1) return 0;
	for (p = 0; p < L->pt.n; p++) if (L->pt.s[p] == '\f' && ++count == page) return y_for_pos(L, (int)p + 1);
	return -1;
}

int ar_current_page(const lay_doc *L, const a2_doc *d, int y)
{
	int i, page = 0, first = 0, best = -1, count = 1;
	size_t p;
	(void)d;
	for (i = 0; i < L->nan; i++) {
		int n = L->an[i].kind == LA_HEADING ? ar_page_number(L->an[i].name) : 0;
		if (n && !first) first = n;
		if (n && L->an[i].y <= y + 6 && L->an[i].y >= best) { page = n; best = L->an[i].y; }
	}
	if (page) return page;
	if (first) return first;
	for (p = 0; p < L->pt.n; p++) if (L->pt.s[p] == '\f' && y_for_pos(L, (int)p + 1) <= y + 6) count++;
	return count;
}

/* 1 when the source has pages: explicit page anchors, or retained form feeds */
int ar_has_pages(const lay_doc *L)
{
	int i;
	for (i = 0; i < L->nan; i++) if (L->an[i].kind == LA_HEADING && ar_page_number(L->an[i].name)) return 1;
	return L->pt.s && memchr(L->pt.s, '\f', L->pt.n) != NULL;
}

int ar_page_reference(const char *s, size_t len, size_t pos)
{
	size_t i = pos > 24 ? pos - 24 : 0, stop = pos + 24 < len ? pos + 24 : len;
	for (; i < stop; i++) {
		size_t j = i;
		int v = 0;
		if (i && isalnum((unsigned char)s[i - 1])) continue;
		if (j + 4 <= len && tolower((unsigned char)s[j]) == 'p' && tolower((unsigned char)s[j + 1]) == 'a' &&
		    tolower((unsigned char)s[j + 2]) == 'g' && tolower((unsigned char)s[j + 3]) == 'e') j += 4;
		else if (j + 2 <= len && tolower((unsigned char)s[j]) == 'p' && s[j + 1] == '.') j += 2;
		else continue;
		while (j < len && s[j] == ' ') j++;
		if (j == len || !isdigit((unsigned char)s[j])) continue;
		for (; j < len && isdigit((unsigned char)s[j]); j++) { v = v * 10 + s[j] - '0'; if (v > 1000000) break; }
		if (v <= 1000000 && pos >= i && pos < j && (j == len || !isalnum((unsigned char)s[j]))) return v;
	}
	return 0;
}

/* the source's marking, exactly as written; lib-corpus labels every public document "PUBLIC", which is the corpus'
 * handling, not a marking the source carries: no banner for it */
const char *ar_marking(const a2_doc *d)
{
	if (!d || !d->corpus_handling || !d->corpus_handling[0] || !strcasecmp(d->corpus_handling, "PUBLIC")) return "";
	return d->corpus_handling;
}

/* u14 S17: the selection with a one-line citation, "-- Title, Ssection, URL (retrieved date)" (UTF-8), then the corpus
 * cite or the Library trailer; the marking above and below as ever. The licence is ar_copy_attributed's. */
char *ar_copy_at(const a2_doc *d, const char *section, const char *text, size_t len)
{
	e26j_buf b;
	const char *mark = ar_marking(d);
	e26j_init(&b);
	if (*mark) { e26j_lit(&b, mark); e26j_lit(&b, "\n"); }
	e26j_raw(&b, text, len); e26j_lit(&b, "\n\n");
	e26j_lit(&b, "\xe2\x80\x94 "); e26j_lit(&b, d->title);
	if (section && *section && strcmp(section, d->title)) { e26j_lit(&b, ", \xc2\xa7"); e26j_lit(&b, section); }
	if (d->source_url && *d->source_url) { e26j_lit(&b, ", "); e26j_lit(&b, d->source_url); }
	if (d->retrieved && *d->retrieved) { e26j_lit(&b, " (retrieved "); e26j_lit(&b, d->retrieved); e26j_lit(&b, ")"); }
	e26j_lit(&b, "\n");
	if (!strcmp(d->cls, "corpus")) {
		e26j_lit(&b, d->corpus_cite); e26j_lit(&b, "; generation "); e26j_lit(&b, d->corpus_generation);
		e26j_lit(&b, "\nReader copy; not checked against the source in this session.\n");
	} else { e26j_lit(&b, CITE_TRAILER); e26j_lit(&b, "\n"); }
	if (*mark) { e26j_lit(&b, mark); e26j_lit(&b, "\n"); }
	return e26j_take(&b);
}

char *ar_copy(const a2_doc *d, const char *text, size_t len) { return ar_copy_at(d, NULL, text, len); }

char *ar_copy_attributed(const a2_doc *d, const char *text, size_t len)
{
	e26j_buf b;
	int i;
	const char *mark = ar_marking(d);
	e26j_init(&b);
	if (*mark) { e26j_lit(&b, mark); e26j_lit(&b, "\n"); }
	e26j_raw(&b, text, len); e26j_lit(&b, "\n\n");
	e26j_lit(&b, d->title); e26j_lit(&b, "\n");
	for (i = 0; i < d->nauthors; i++) { if (i) e26j_lit(&b, "; "); e26j_lit(&b, d->authors[i]); }
	e26j_lit(&b, "\n"); e26j_lit(&b, d->source_url); e26j_lit(&b, "\n"); e26j_lit(&b, d->licence);
	e26j_lit(&b, "\nRetrieved "); e26j_lit(&b, d->retrieved); e26j_lit(&b, "; revision ");
	e26j_lit(&b, d->revision && *d->revision ? d->revision : "unspecified");
	if (!strcmp(d->cls, "corpus")) {
		e26j_lit(&b, "\n"); e26j_lit(&b, d->corpus_cite); e26j_lit(&b, "; generation "); e26j_lit(&b, d->corpus_generation);
		e26j_lit(&b, "\nReader copy; not checked against the source in this session.\n");
	} else { e26j_lit(&b, "\n"); e26j_lit(&b, CITE_TRAILER); e26j_lit(&b, "\n"); }
	if (*mark) { e26j_lit(&b, mark); e26j_lit(&b, "\n"); }
	return e26j_take(&b);
}

void ar_history_save(ar_history *h, int scroll, int view)
{
	if (h->n) { h->p[h->at].scroll = scroll; h->p[h->at].view = view; }
}

int ar_history_push(ar_history *h, const char *id, int pack, int scroll, int view)
{
	ar_place p;
	int i;
	memset(&p, 0, sizeof p);
	if (!(p.id = strdup(id))) return -1;
	p.pack = pack; p.scroll = scroll; p.view = view;
	for (i = h->n ? h->at + 1 : 0; i < h->n; i++) free(h->p[i].id);
	h->n = h->n ? h->at + 1 : 0;
	if (h->n == AR_HISTORY) {
		free(h->p[0].id); memmove(h->p, h->p + 1, (AR_HISTORY - 1) * sizeof p); h->n--;
	}
	h->p[h->n++] = p; h->at = h->n - 1;
	return 0;
}

const ar_place *ar_history_step(const ar_history *h, int dir)
{
	int to = h->at + dir;
	return h->n && to >= 0 && to < h->n ? &h->p[to] : NULL;
}

void ar_history_free(ar_history *h)
{
	int i;
	for (i = 0; i < h->n; i++) free(h->p[i].id);
	memset(h, 0, sizeof *h);
}
