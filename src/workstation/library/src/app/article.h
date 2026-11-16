/* article.h: build 1's article reader. An article file (.art) is a header of "key: value" lines, a line
 * "---", then the source text verbatim. For "format: rfc-text" the body is an RFC excerpt in its published
 * plain-text layout; the reader removes the page furniture (footer and header lines, form feeds), joins
 * paragraphs a page break split, and classifies blocks: numbered section headings, prose paragraphs (reflowed:
 * only line breaks and runs of spaces change, never words), definition terms, and preformatted figures (kept
 * exactly). */
#ifndef E26_ARTICLE_H
#define E26_ARTICLE_H
#include <stddef.h>
#include "../common/rfctext.h"

/* the classifier is src/common/rfctext.c (build 3, W0-F0); its block kinds and blocks are the article's */
enum { B_HEADING = RFC_HEADING, B_PARA = RFC_PARA, B_TERM = RFC_TERM, B_PRE = RFC_PRE };
typedef rfc_block art_block;

#define ART_MAX_META 8

typedef struct {
	char id[64], title[160], subtitle[200], source[300], source_sha256[80], licence[300], format[32];
	char related[ART_MAX_META][200];
	int nrelated;
	art_block *b;
	int nb;
} article;

int article_load(const char *path, article *a, char *err, size_t errlen);
void article_free(article *a);

#endif
