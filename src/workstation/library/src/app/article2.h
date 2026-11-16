/* article2.h: the article format 2 reader (docs/ARTICLE-FORMAT-2.md; build 3, W1-LAYOUT). A strict parser of the
 * "e26art 2" record file into a typed model the layout walks. It follows the normative reference parser
 * (tools/validate_v2.py) check for check and answers with its error codes (E_JSON, E_FIELD, E_TABLE ...), so a file
 * the home builder would reject is rejected here too. Two of its checks need Unicode tables this reader does not
 * carry: sort_key = NFKC(title).casefold() and the Unicode \w word count are enforced when the title or the body is
 * ASCII and otherwise left to the signed builder, which is the trust boundary (the content hash still binds every
 * byte). Strings stay UTF-8 exactly as the file has them (text8.h maps them to the faces at layout time).
 * X-free: the tests and the helpers link it. */
#ifndef E26_ARTICLE2_H
#define E26_ARTICLE2_H
#include <stddef.h>

/* block kinds (the record commands) */
enum a2_kind { A2_TITLE, A2_SUBTITLE, A2_BYLINE, A2_HEADING, A2_PARAGRAPH, A2_LIST, A2_DEFINITIONS, A2_PRE, A2_CODE,
	A2_NOTE, A2_CAUTION, A2_WARNING, A2_QUOTE, A2_TABLE, A2_FIGURE, A2_MATHS, A2_FACTBOX, A2_INFOBOX, A2_QUICK_FACTS,
	A2_REFERENCES, A2_SEE_ALSO, A2_TIMELINE_YEAR, A2_FOOTNOTE, A2_ATTRIBUTION, A2_NKINDS };
/* inline run types */
enum a2_run_type { R_TEXT, R_BOLD, R_ITALIC, R_MONO, R_SUB, R_SUP, R_SMALLCAPS, R_UNDERLINE, R_STRIKE, R_SMALL, R_BIG,
	R_KEYWORD, R_LINK, R_FOOTREF, R_GLOSSARY, R_SHY };
/* link target kinds */
enum a2_target_kind { T_ARTICLE, T_ANCHOR, T_RFC, T_URL, T_MEDIA };
enum { REF_NORMATIVE, REF_INFORMATIVE, REF_FURTHER };
enum { COL_LEFT, COL_CENTRE, COL_RIGHT };
enum a2_shade { SHADE_NONE, SHADE_YES, SHADE_NO, SHADE_PARTIAL, SHADE_NEUTRAL, SHADE_HEADER, SHADE_HIGHLIGHT };

typedef struct {
	int kind;             /* T_* */
	const char *id;       /* T_ARTICLE: the article id; T_MEDIA: the media id */
	const char *anchor;   /* T_ARTICLE (optional, NULL) and T_ANCHOR */
	long number;          /* T_RFC */
	const char *section;  /* T_RFC (optional, NULL) */
	const char *url;      /* T_URL */
} a2_target;

typedef struct a2_run a2_run;
typedef struct {
	a2_run *r;
	int n;
} a2_runs;

struct a2_run {
	int t;                /* R_* */
	const char *text;     /* R_TEXT the string ("" allowed); R_KEYWORD the keyword; R_FOOTREF the footnote id;
	                         R_GLOSSARY the term */
	const char *definition; /* R_GLOSSARY */
	a2_runs kids;         /* styles: their runs; R_LINK and R_GLOSSARY: the label */
	a2_target target;     /* R_LINK */
};

typedef struct a2_list a2_list;
typedef struct {
	a2_runs runs;
	a2_list *kids;
	int nkids;
} a2_item;
struct a2_list {
	int ordered;
	long start;
	a2_item *items;
	int nitems;
};

typedef struct {
	a2_runs runs;
	int rowspan, colspan;
	enum a2_shade shade;
	int row, col;         /* its place in the occupancy grid (computed) */
} a2_cell;

typedef struct {
	const char *key;      /* fact rows */
	a2_runs runs;
} a2_fact;

typedef struct {
	const char *id;       /* references only */
	int kind;             /* REF_*; references only */
	a2_target target;
	a2_runs label;
} a2_ref;

typedef struct {
	a2_runs term, def;
} a2_def;

typedef struct {
	int kind;             /* A2_* */
	int line;             /* the record's line in the file */
	a2_runs runs;         /* subtitle, byline, heading, paragraph, note, caution, warning, quote, timeline_year,
	                         footnote; figure: the caption */
	int level;            /* heading 1-6 */
	const char *anchor;   /* heading anchor; footnote id */
	const char *text;     /* title, pre, code */
	const char *language, *prompt;          /* code */
	int *var;             /* code: 2 x nvar scalar offsets (start, end) */
	int nvar;
	const char *media, *credit, *alt;       /* figure (media, credit); maths (media, alt) */
	const char *title;    /* factbox, infobox, quick_facts */
	a2_fact *facts;
	int nfacts;
	a2_list list;         /* list */
	a2_def *defs;         /* definitions */
	int ndefs;
	int ncols, header_rows, nrows;          /* table */
	int *align;           /* COL_* per column */
	a2_cell *cells;
	int ncells;
	a2_ref *refs;         /* references, see_also */
	int nrefs;
	long year;            /* timeline_year */
	/* attribution */
	const char *licence, *source_url, *retrieved, *revision;
	const char **authors;
	int nauthors;
} a2_block;

typedef struct {
	const char *id, *kind, *uri, *mime, *alt, *credit;
} a2_media;

typedef struct {
	/* meta */
	const char *id, *cls, *title, *sort_key, *primary_category, *published, *updated, *content_sha256;
	const char **categories, **aliases, **redirects, **places, **obsoletes, **obsoleted_by, **updates, **updated_by,
		**errata;
	int ncategories, naliases, nredirects, nplaces, nobsoletes, nobsoleted_by, nupdates, nupdated_by, nerrata;
	long *years;
	int nyears;
	long word_count;
	a2_media *media;
	int nmedia;
	/* provenance */
	const char *source_kind, *source_url, *licence, *retrieved, *source_sha256, *revision, *pack;
	const char **authors;
	int nauthors;
	/* corpus provenance (class "corpus" only; NULL otherwise) */
	const char *corpus_doc_id, *corpus_generation, *corpus_section, *corpus_cite, *corpus_handling;
	/* the blocks: the title first, the attribution last */
	a2_block *b;
	int nb;
	void *arena;          /* every allocation of the document */
} a2_doc;

/* parses an article held in buf (n bytes). 0, or -1 with the reference parser's code ("E_TABLE") in code and a
 * located message ("line 7: span outside table") in err */
int a2_parse(const char *buf, size_t n, a2_doc *d, char *code, size_t codelen, char *err, size_t errlen);
/* reads and parses a file */
int a2_load(const char *path, a2_doc *d, char *code, size_t codelen, char *err, size_t errlen);
void a2_free(a2_doc *d);
/* 1 when buf starts with the format-2 magic line */
int a2_is_v2(const char *buf, size_t n);
const char *a2_kind_name(int kind);
const a2_media *a2_media_find(const a2_doc *d, const char *id);
/* the canonical plain text (ARTICLE-FORMAT-2 "plain_text"): malloc'd, NUL-terminated; *len its bytes */
char *a2_plain_text(const a2_doc *d, size_t *len);
/* one block's text in that body (block_text), appended to a growing buffer: 0, or -1 out of memory */
typedef struct {
	char *s;
	size_t n, cap;
} a2_buf;
int a2_buf_add(a2_buf *b, const char *s, size_t n);
int a2_block_text(const a2_block *bl, a2_buf *out);
int a2_runs_text(const a2_runs *r, a2_buf *out);

#endif
