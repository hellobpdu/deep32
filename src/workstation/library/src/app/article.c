/* article.c: the .art reader (article.h): the header, then the body through the RFC plain-text classifier
 * (src/common/rfctext.c). */
#include "article.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void rstrip(char *s)
{
	size_t n = strlen(s);
	while (n && (s[n - 1] == '\n' || s[n - 1] == '\r' || s[n - 1] == ' ' || s[n - 1] == '\t' || s[n - 1] == '\f')) s[--n] = 0;
}

static void meta(article *a, const char *k, const char *v)
{
#define M(name, field) if (!strcmp(k, name)) { snprintf(a->field, sizeof a->field, "%s", v); return; }
	M("id", id) M("title", title) M("subtitle", subtitle) M("source", source) M("source-sha256", source_sha256)
	M("licence", licence) M("format", format)
#undef M
	if (!strcmp(k, "related") && a->nrelated < ART_MAX_META)
		snprintf(a->related[a->nrelated++], sizeof a->related[0], "%s", v);
}

int article_load(const char *path, article *a, char *err, size_t errlen)
{
	FILE *f = fopen(path, "rb");
	char line[2048];
	int body = 0, rc = 0, i, n = 0, cap = 0;
	char **raw = NULL;
	rfc_doc d;
	memset(a, 0, sizeof(*a));
	if (!f) { snprintf(err, errlen, "cannot open %s", path); return -1; }
	while (fgets(line, sizeof line, f)) {
		if (!body) {
			char *c;
			rstrip(line);
			if (!strcmp(line, "---")) { body = 1; continue; }
			if (!line[0] || line[0] == '#') continue;
			c = strchr(line, ':');
			if (!c) { snprintf(err, errlen, "%s: header line without ':'", path); fclose(f); return -1; }
			*c = 0;
			c++;
			while (*c == ' ') c++;
			meta(a, line, c);
			continue;
		}
		if (n == cap) {
			char **nr;
			cap = cap ? cap * 2 : 256;
			nr = (char **)realloc(raw, (size_t)cap * sizeof(char *));
			if (!nr) { rc = -1; break; }
			raw = nr;
		}
		if (!(raw[n] = strdup(line))) { rc = -1; break; }
		n++;
	}
	fclose(f);
	if (rc || !body) { snprintf(err, errlen, "%s: %s", path, rc ? "out of memory" : "no '---' body"); goto out; }
	if (strcmp(a->format, "rfc-text")) { snprintf(err, errlen, "%s: format '%s' not supported in build 1", path, a->format); rc = -1; goto out; }
	if (rfctext_classify(raw, n, &d)) { snprintf(err, errlen, "out of memory"); rc = -1; goto out; }
	a->b = d.b;
	a->nb = d.nb;
out:
	for (i = 0; i < n; i++) free(raw[i]);
	free(raw);
	if (rc) article_free(a);
	return rc;
}

void article_free(article *a)
{
	rfc_doc d;
	d.b = a->b;
	d.nb = a->nb;
	rfctext_free(&d);
	a->b = NULL;
	a->nb = 0;
}
