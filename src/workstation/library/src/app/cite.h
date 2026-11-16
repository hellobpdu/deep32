/* cite.h: Copy with cite (W1-INPUT, plan 3.2; LIBRARY-DESIGN 4.9, CONTENT-EXPANSION 6.5, ENCYCLOPEDIA 8.2). X-free.
 *
 * Every copy ends with a truthful line: it names the source, its rights, and for retrieved evidence the generation
 * and the check. Two classes, never mixed:
 *   corpus   text from the RAG (the hit's own cite and local fields, the generation, the verify_quote result);
 *   library  Deep32 Library text (the IETF pack, the encyclopedia, ...): never evidence, so its last line
 *            always ends "Deep32 Library: not verified evidence."
 * The handling marking is carried, never chosen: when the source has one, it stands above and below the text. A CLI
 * exhibit gets its cite above the commands as IOS comment lines ("!"), so the paste works in configuration mode. */
#ifndef E26_CITE_H
#define E26_CITE_H
#include <stddef.h>

enum { CITE_LIBRARY, CITE_CORPUS };
enum { CITE_RFC, CITE_DRAFT, CITE_WIKI, CITE_CISCO, CITE_NIST, CITE_OTHER };

#define CITE_TRAILER "Deep32 Encyclopedia: not verified evidence."

typedef struct {
	int cls;                /* CITE_LIBRARY or CITE_CORPUS */
	int kind;               /* CITE_RFC ... CITE_OTHER (article format 2: provenance.source_kind) */
	const char *id;         /* the article or document id ("rfc2328", "wiki:Open_Shortest_Path_First") */
	const char *title;      /* "OSPF Version 2"; a Cisco document's title ("IOS XE OSPF command reference") */
	const char *number;     /* RFC number "2328"; a draft's name; NIST's "Special Publication 800-207" */
	const char *authors;    /* "J. Moy" (Library RFCs, NIST) */
	const char *date;       /* "March 1994" (Library RFCs, NIST) */
	const char *section;    /* "Appendix C.3 (HelloInterval)", "Abstract", "Concepts", "\"ip ospf hello-interval\", Command Default" */
	const char *anchor;     /* the RFC Editor URL's fragment ("appendix-C.3", "section-1"); NULL none */
	const char *status;     /* the standing: corpus "Internet Standard, STD 54." (first line); library a line of its own
	                           ("Obsoleted by RFC 2178, itself obsoleted by RFC 2328 (in your RAG).") */
	const char *url;        /* an explicit URL (a DOI, a permanent link); NULL: built for RFCs, drafts and Wikipedia */
	const char *revision;   /* Wikipedia's revision id */
	const char *edited;     /* Wikipedia's last edit, YYYY-MM-DD */
	const char *licence;    /* the rights line as the source states it ("Text CC BY-SA 4.0 (...)", "U.S. Government
	                           work, ...", "Cisco documentation: view-only, personal use.") */
	const char *lines;      /* where in the source file ("lines 26-28 of rfc1583.txt"), Library */
	const char *libcheck;   /* the shelf's byte comparison ("Quote matches the Library text."), Library; NULL none */
	const char *adapted;    /* "Adapted for Deep32 Library (reformatted)." (converted Library text); NULL none */
	/* corpus only */
	const char *cite;       /* the hit's cite ("rfc2328#appendix-c.3") */
	const char *local;      /* the hit's local field ("rfc2328.txt:11125-11126") */
	const char *generation; /* "p9er-20261102" */
	const char *verify;     /* verify_quote's result ("exact"); NULL: not checked */
	/* both */
	const char *handling;   /* the source's marking, as written; NULL or "" none */
	int cli;                /* a CLI exhibit (configuration, show output): the cite above, as "!" lines */
	int block;              /* a whole section or article (copy with nothing selected): no quotation marks */
} cite_src;

/* The text with its cite into out; returns its length, or -1 when out is too small (out then holds ""). The text is
 * cp1252 or UTF-8 (passed through unchanged). Lines end in '\n'; the result ends in one. */
int cite_build(const cite_src *c, const char *text, char *out, size_t n);
/* 1 when text carries the Library trailer (a paste that went through another program first keeps it) */
int cite_has_trailer(const char *text);
/* "library" or "corpus" */
const char *cite_class_name(int cls);

#endif
