/* rfctext.h: the RFC plain-text classifier (W0-F0 moved it here from the article reader; W0-H1 owns it from wave 1,
 * for the home-side conversion to article format 2). Given an RFC excerpt in its published plain-text layout, it
 * removes the page furniture (footer and header lines, form feeds), joins paragraphs a page break split, and
 * classifies blocks: numbered section headings, prose paragraphs (reflowed: only line breaks and runs of spaces
 * change, never words), definition terms, and preformatted figures (kept exactly). */
#ifndef E26_RFCTEXT_H
#define E26_RFCTEXT_H
#include <stddef.h>

enum { RFC_HEADING, RFC_PARA, RFC_TERM, RFC_PRE };

typedef struct {
	int kind;
	int level;        /* heading depth (1 = "1.", 2 = "1.1."), or paragraph indent level */
	char *text;       /* PRE: lines joined with '\n' */
	char number[16];  /* heading number "1.1" */
} rfc_block;

typedef struct {
	rfc_block *b;
	int nb;
} rfc_doc;

/* raw_lines: the text's lines as read (with their newlines and form feeds; they are trimmed in place). 0, or -1 out
 * of memory. */
int rfctext_classify(char **raw_lines, int n, rfc_doc *out);
void rfctext_free(rfc_doc *d);

#endif
