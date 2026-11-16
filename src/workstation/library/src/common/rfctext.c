/* rfctext.c: the RFC plain-text classifier (rfctext.h), moved out of the article reader for the home-side converter
 * (W0-F0; H1 owns it from wave 1). Byte-for-byte the build-2 rules. */
#include "rfctext.h"
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
	char **l;
	int n, cap;
	char *brk; /* brk[i] = 1 when a page break was removed just before line i */
} lines;

static int add(lines *ls, const char *s, int pagebreak)
{
	if (ls->n == ls->cap) {
		int cap = ls->cap ? ls->cap * 2 : 256;
		char **nl = (char **)realloc(ls->l, (size_t)cap * sizeof(char *));
		char *nb = (char *)realloc(ls->brk, (size_t)cap);
		if (!nl || !nb) { free(nl ? nl : ls->l); free(nb ? nb : ls->brk); ls->l = NULL; ls->brk = NULL; return -1; }
		ls->l = nl; ls->brk = nb; ls->cap = cap;
	}
	ls->l[ls->n] = strdup(s);
	if (!ls->l[ls->n]) return -1;
	ls->brk[ls->n] = (char)pagebreak;
	ls->n++;
	return 0;
}

static int blank(const char *s) { while (*s) { if (!isspace((unsigned char)*s)) return 0; s++; } return 1; }
static int indent(const char *s) { int i = 0; while (s[i] == ' ') i++; return i; }

static void rstrip(char *s)
{
	size_t n = strlen(s);
	while (n && (s[n - 1] == '\n' || s[n - 1] == '\r' || s[n - 1] == ' ' || s[n - 1] == '\t' || s[n - 1] == '\f')) s[--n] = 0;
}

/* "Moy   Standards Track   [Page 6]" */
static int is_footer(const char *s)
{
	size_t n = strlen(s);
	const char *p;
	if (n < 8 || s[n - 1] != ']' || indent(s) > 0) return 0;
	p = strstr(s, "[Page ");
	return p && p > s && strspn(p + 6, "0123456789") == (size_t)(n - 1 - (size_t)(p + 6 - s));
}

/* "RFC 2328                     OSPF Version 2                   April 1998" */
static int is_header(const char *s)
{
	size_t n = strlen(s);
	return !strncmp(s, "RFC ", 4) && isdigit((unsigned char)s[4]) && n > 10 &&
	       isdigit((unsigned char)s[n - 1]) && isdigit((unsigned char)s[n - 4]) && strstr(s, "   ");
}

/* "1.1.  Protocol overview" -> number "1.1", returns depth, or 0 */
static int heading(const char *s, char *num, size_t numlen, const char **title)
{
	const char *p = s + indent(s);
	size_t k = 0;
	int depth = 0;
	if (!isdigit((unsigned char)*p)) return 0;
	while (isdigit((unsigned char)*p) || *p == '.') {
		if (*p == '.') depth++;
		if (k + 1 < numlen) num[k++] = *p;
		p++;
	}
	num[k] = 0;
	if (k && num[k - 1] == '.') num[--k] = 0;
	if (!depth || *p != ' ' || num[0] == 0) return 0;
	while (*p == ' ') p++;
	if (!isupper((unsigned char)*p)) return 0;
	if (strlen(p) > 70) return 0;
	*title = p;
	return depth;
}

static char *join_reflow(char **l, int from, int to)
{
	size_t cap = 1, i;
	char *o, *d;
	int k;
	for (k = from; k < to; k++) cap += strlen(l[k]) + 1;
	o = (char *)malloc(cap);
	if (!o) return NULL;
	d = o;
	for (k = from; k < to; k++) {
		const char *s = l[k] + indent(l[k]);
		if (d != o) *d++ = ' ';
		for (i = 0; s[i]; i++) {
			if (s[i] == ' ' && d != o && d[-1] == ' ') continue; /* runs of spaces collapse: whitespace only */
			*d++ = s[i];
		}
	}
	*d = 0;
	return o;
}

static char *join_pre(char **l, int from, int to)
{
	size_t cap = 1;
	int k, base = 1000;
	char *o, *d;
	for (k = from; k < to; k++) { cap += strlen(l[k]) + 1; if (!blank(l[k]) && indent(l[k]) < base) base = indent(l[k]); }
	if (base == 1000) base = 0;
	o = (char *)malloc(cap);
	if (!o) return NULL;
	d = o;
	for (k = from; k < to; k++) {
		const char *s = l[k];
		if ((int)strlen(s) >= base) s += base; else s = "";
		if (k > from) *d++ = '\n';
		memcpy(d, s, strlen(s));
		d += strlen(s);
	}
	*d = 0;
	return o;
}

static int push(rfc_doc *a, int kind, int level, char *text, const char *num)
{
	rfc_block *nb;
	if (!text) return -1;
	nb = (rfc_block *)realloc(a->b, (size_t)(a->nb + 1) * sizeof(rfc_block));
	if (!nb) { free(text); return -1; }
	a->b = nb;
	memset(&a->b[a->nb], 0, sizeof(rfc_block));
	a->b[a->nb].kind = kind;
	a->b[a->nb].level = level;
	a->b[a->nb].text = text;
	if (num) snprintf(a->b[a->nb].number, sizeof a->b[a->nb].number, "%s", num);
	a->nb++;
	return 0;
}

static int classify(rfc_doc *a, lines *ls)
{
	int i = 0;
	while (i < ls->n) {
		int j, same = 1, ind0, h;
		char num[16];
		const char *title;
		if (blank(ls->l[i])) { i++; continue; }
		/* a block runs to the next blank line; a page break inside it was already joined */
		for (j = i + 1; j < ls->n && !blank(ls->l[j]); j++) ;
		ind0 = indent(ls->l[i]);
		if (j == i + 1 && (h = heading(ls->l[i], num, sizeof num, &title))) {
			if (push(a, RFC_HEADING, h, strdup(title), num)) return -1;
			i = j;
			continue;
		}
		for (h = i + 1; h < j; h++) if (indent(ls->l[h]) != ind0) same = 0;
		if (same) {
			if (push(a, RFC_PARA, ind0 > 8 ? 2 : ind0 > 4 ? 1 : 0, join_reflow(ls->l, i, j), NULL)) return -1;
		} else if (j > i + 1 && indent(ls->l[i + 1]) > ind0 && strlen(ls->l[i]) - (size_t)ind0 <= 40 &&
		           ls->l[i][strlen(ls->l[i]) - 1] != '.') {
			int k, rest_same = 1;
			for (k = i + 2; k < j; k++) if (indent(ls->l[k]) != indent(ls->l[i + 1])) rest_same = 0;
			if (push(a, RFC_TERM, 0, strdup(ls->l[i] + ind0), NULL)) return -1;
			if (rest_same) { if (push(a, RFC_PARA, 1, join_reflow(ls->l, i + 1, j), NULL)) return -1; }
			else if (push(a, RFC_PRE, 1, join_pre(ls->l, i + 1, j), NULL)) return -1;
		} else {
			if (push(a, RFC_PRE, 0, join_pre(ls->l, i, j), NULL)) return -1;
		}
		i = j;
	}
	return 0;
}

int rfctext_classify(char **raw_lines, int n, rfc_doc *out)
{
	lines raw = { 0 }, ls = { 0 };
	int i, rc = 0, pending_break = 0;
	memset(out, 0, sizeof(*out));
	for (i = 0; i < n; i++) {
		char *line = raw_lines[i];
		if (strchr(line, '\f')) pending_break = 1;
		rstrip(line);
		if (is_footer(line) || is_header(line)) { pending_break = 1; continue; }
		if (add(&raw, line, pending_break)) { rc = -1; goto out; }
		pending_break = 0;
	}
	/* Page breaks: drop the blank run around a removed footer/header; keep one blank line unless the text
	 * before the break does not end a sentence and the text after continues at the same indent (a split
	 * paragraph), in which case join. */
	for (i = 0; i < raw.n; i++) {
		if (raw.brk[i]) {
			int k = ls.n, nxt = i, prev_end;
			while (k > 0 && blank(ls.l[k - 1])) { free(ls.l[--k]); ls.n = k; }
			while (nxt < raw.n && blank(raw.l[nxt])) nxt++;
			if (nxt < raw.n && ls.n > 0) {
				const char *p = ls.l[ls.n - 1];
				char last = p[strlen(p) - 1];
				prev_end = last == '.' || last == ':' || last == ')' ;
				if (prev_end || indent(p) != indent(raw.l[nxt]) || heading(raw.l[nxt], (char[16]){ 0 }, 16, &(const char *){ 0 }))
					if (add(&ls, "", 0)) { rc = -1; goto out; }
			}
			i = nxt - 1;
			continue;
		}
		if (add(&ls, raw.l[i], 0)) { rc = -1; goto out; }
	}
	if (classify(out, &ls)) rc = -1;
out:
	for (i = 0; i < raw.n; i++) free(raw.l[i]);
	free(raw.l); free(raw.brk);
	for (i = 0; i < ls.n; i++) free(ls.l[i]);
	free(ls.l); free(ls.brk);
	if (rc) rfctext_free(out);
	return rc;
}

void rfctext_free(rfc_doc *d)
{
	int i;
	for (i = 0; i < d->nb; i++) free(d->b[i].text);
	free(d->b);
	d->b = NULL;
	d->nb = 0;
}
