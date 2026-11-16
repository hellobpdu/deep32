/* cite.c: Copy with cite (W1-INPUT; cite.h). The shapes are LIBRARY-DESIGN 4.9's (corpus quotes, CLI exhibits, the
 * marking above and below) and CONTENT-EXPANSION 6.5 / ENCYCLOPEDIA 8.2's (Library RFCs, NIST, Wikipedia). One
 * difference from those hand-set examples: a line is never wrapped inside a field (a clipboard has no page width), so
 * an example's continuation line is part of the line before it here. X-free. */
#include "cite.h"
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

typedef struct {
	char *p;
	size_t n, k;
	int over;
} buf;

static void put(buf *b, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void put(buf *b, const char *fmt, ...)
{
	va_list ap;
	int r;
	if (b->over) return;
	va_start(ap, fmt);
	r = vsnprintf(b->p + b->k, b->n - b->k, fmt, ap);
	va_end(ap);
	if (r < 0 || (size_t)r >= b->n - b->k) { b->over = 1; return; }
	b->k += (size_t)r;
}

static int has(const char *s) { return s && s[0]; }

/* the text itself: as written for a whole section, else in quotation marks; it ends with a newline either way */
static void put_text(buf *b, const cite_src *c, const char *text, const char *prefix)
{
	size_t len = text ? strlen(text) : 0;
	int nl = len && text[len - 1] == '\n';
	if (c->cli || c->block) put(b, "%s%s", text ? text : "", nl || !len ? "" : "\n");
	else put(b, "%s\"%.*s\"\n", prefix, (int)(len - (size_t)nl), text ? text : "");
}

/* a Wikipedia title as its URL writes it: spaces as underscores, the characters a query string needs escaped */
static void put_wiki_title(buf *b, const char *t)
{
	for (; *t; t++) {
		unsigned char ch = (unsigned char)*t;
		if (ch == ' ') put(b, "_");
		else if (ch < 0x21 || ch >= 0x7f || strchr("&?#%+\"<>", ch)) put(b, "%%%02X", ch);
		else put(b, "%c", ch);
	}
}

static void put_url(buf *b, const cite_src *c)
{
	if (has(c->url)) { put(b, "%s", c->url); return; }
	switch (c->kind) {
	case CITE_RFC:
		put(b, "https://www.rfc-editor.org/rfc/rfc%s", has(c->number) ? c->number : "");
		if (has(c->anchor)) put(b, "#%s", c->anchor);
		break;
	case CITE_DRAFT: put(b, "https://datatracker.ietf.org/doc/%s/", has(c->number) ? c->number : ""); break;
	case CITE_WIKI:
		put(b, "https://en.wikipedia.org/w/index.php?title=");
		put_wiki_title(b, has(c->title) ? c->title : "");
		if (has(c->revision)) put(b, "&oldid=%s", c->revision);
		break;
	default: break;
	}
}

static int url_known(const cite_src *c)
{
	return has(c->url) || c->kind == CITE_RFC || c->kind == CITE_DRAFT || c->kind == CITE_WIKI;
}

/* ", a, b, c" for the fields that are present */
static void put_list(buf *b, const char *a1, const char *a2, const char *a3)
{
	if (has(a1)) put(b, ", %s", a1);
	if (has(a2)) put(b, ", %s", a2);
	if (has(a3)) put(b, ", %s", a3);
}

/* the cite's first line without its "-- " (corpus) */
static void corpus_head(buf *b, const cite_src *c)
{
	switch (c->kind) {
	case CITE_RFC:
		put(b, "RFC %s, \"%s\"", has(c->number) ? c->number : "?", has(c->title) ? c->title : "");
		if (has(c->section)) put(b, ", %s", c->section);
		put(b, ".");
		if (has(c->status)) put(b, " %s", c->status);
		break;
	case CITE_CISCO:
		put(b, "Cisco, %s", has(c->title) ? c->title : "");
		if (has(c->section)) put(b, ", %s", c->section);
		put(b, ".");
		break;
	default:
		put(b, "%s", has(c->title) ? c->title : "");
		if (has(c->section)) put(b, ", %s", c->section);
		break;
	}
}

static void corpus_ref(buf *b, const cite_src *c)
{
	put(b, "[%s; %s]", has(c->cite) ? c->cite : "", has(c->local) ? c->local : "");
}

static void corpus_check(buf *b, const cite_src *c)
{
	put(b, "Deep32 Encyclopedia, generation %s; ", has(c->generation) ? c->generation : "unknown");
	if (has(c->verify)) put(b, "verified against the source: %s.", c->verify);
	else put(b, "not checked against the source.");
}

/* the cite's first line without its "-- " (Library) */
static void library_head(buf *b, const cite_src *c)
{
	const char *t = has(c->title) ? c->title : "";
	switch (c->kind) {
	case CITE_RFC:
		put(b, "\"%s,\" RFC %s", t, has(c->number) ? c->number : "?");
		put_list(b, c->authors, c->date, c->section);
		if (has(c->lines)) put(b, " (%s)", c->lines);
		put(b, ".");
		break;
	case CITE_DRAFT:
		put(b, "\"%s,\" Internet-Draft %s", t, has(c->number) ? c->number : "?");
		put_list(b, c->authors, c->date, c->section);
		put(b, ".");
		break;
	case CITE_WIKI:
		put(b, "Wikipedia, \"%s\"", t);
		if (has(c->section)) put(b, ", section \"%s\"", c->section);
		if (has(c->revision)) {
			put(b, "; revision %s", c->revision);
			if (has(c->edited)) put(b, " (last edited %s)", c->edited);
		}
		put(b, ".");
		break;
	case CITE_NIST:
		put(b, "\"%s,\" NIST %s", t, has(c->number) ? c->number : "");
		put_list(b, c->authors, c->date, c->section);
		put(b, ".");
		if (has(c->url)) put(b, " %s", c->url);
		break;
	case CITE_CISCO:
		put(b, "Cisco, %s", t);
		if (has(c->section)) put(b, ", %s", c->section);
		put(b, ".");
		break;
	default:
		put(b, "\"%s,\"", t);
		if (has(c->number)) put(b, " %s", c->number);
		put_list(b, c->authors, c->date, c->section);
		put(b, ".");
		if (has(c->url)) put(b, " %s", c->url);
		break;
	}
}

static const char *default_licence(const cite_src *c)
{
	if (has(c->licence)) return c->licence;
	if (c->kind == CITE_WIKI) return "Text CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/); authors: see the page history.";
	if (c->kind == CITE_NIST) return "U.S. Government work, not subject to copyright in the United States (17 U.S.C. 105).";
	return NULL;
}

static void library_body(buf *b, const cite_src *c)
{
	const char *lic = default_licence(c);
	/* the URL line: RFCs and drafts carry their rights on it (CONTENT-EXPANSION 6.5); NIST and the others put the URL
	 * on the first line */
	if (has(c->status)) put(b, "   %s\n", c->status);
	if (c->kind == CITE_RFC || c->kind == CITE_DRAFT) {
		put(b, "   ");
		put_url(b, c);
		if (lic) put(b, " . %s", lic);
		put(b, "\n");
	} else {
		if (c->kind == CITE_WIKI || c->kind == CITE_CISCO) {
			if (url_known(c)) { put(b, "   "); put_url(b, c); put(b, "\n"); }
		}
		if (lic) put(b, "   %s\n", lic);
	}
	put(b, "   ");
	if (has(c->libcheck)) put(b, "%s ", c->libcheck);
	if (has(c->adapted)) put(b, "%s ", c->adapted);
	put(b, "%s\n", CITE_TRAILER);
}

int cite_build(const cite_src *c, const char *text, char *out, size_t n)
{
	buf b;
	int mark = has(c->handling);
	if (!out || !n) return -1;
	b.p = out;
	b.n = n;
	b.k = 0;
	b.over = 0;
	out[0] = 0;
	if (c->cli) {
		/* IOS ignores "!" lines in configuration mode: the cite stands above the commands (LIBRARY-DESIGN 4.9) */
		if (mark) put(&b, "! %s\n", c->handling);
		if (c->cls == CITE_CORPUS) {
			put(&b, "! %s . Deep32 Encyclopedia, %s . %s%s\n", has(c->cite) ? c->cite : has(c->id) ? c->id : "",
			    has(c->generation) ? c->generation : "unknown", has(c->verify) ? "verified " : "not checked",
			    has(c->verify) ? c->verify : "");
		} else {
			put(&b, "! ");
			library_head(&b, c);
			put(&b, "\n! %s\n", CITE_TRAILER);
		}
		put_text(&b, c, text, "");
		if (mark) put(&b, "! %s\n", c->handling);
	} else {
		if (mark) put(&b, "%s\n", c->handling);
		put_text(&b, c, text, "");
		if (c->block) put(&b, "\n");
		put(&b, "-- ");
		if (c->cls == CITE_CORPUS) {
			corpus_head(&b, c);
			if (c->kind == CITE_RFC) {
				put(&b, "\n   ");
				put_url(&b, c);
				put(&b, "   ");
				corpus_ref(&b, c);
				put(&b, "\n   ");
			} else if (c->kind == CITE_CISCO) {
				put(&b, "\n   ");
				corpus_ref(&b, c);
				put(&b, "\n   ");
			} else {
				put(&b, "   ");
				corpus_ref(&b, c);
				put(&b, "\n   ");
			}
			if (has(c->licence)) put(&b, "%s ", c->licence);
			corpus_check(&b, c);
			put(&b, "\n");
		} else {
			library_head(&b, c);
			put(&b, "\n");
			library_body(&b, c);
		}
		if (mark) put(&b, "%s\n", c->handling);
	}
	if (b.over) { out[0] = 0; return -1; }
	return (int)b.k;
}

int cite_has_trailer(const char *text)
{
	static const char CITE_TRAILER_V1[] = "Deep32 Library, not " "RAG: not verified evidence.";
	static const char CITE_TRAILER_PREVIOUS[] = "Deep32 Library: not verified evidence.";
	return text && (strstr(text, CITE_TRAILER) != NULL
	                || strstr(text, CITE_TRAILER_PREVIOUS) != NULL
	                || strstr(text, CITE_TRAILER_V1) != NULL);
}

const char *cite_class_name(int cls) { return cls == CITE_CORPUS ? "corpus" : "library"; }
