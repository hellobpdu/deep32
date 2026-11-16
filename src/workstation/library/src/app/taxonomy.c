/* taxonomy.c: load the taxonomy JSON (see taxonomy.h). */
#include "taxonomy.h"
#include "json.h"
#include "scr.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void cp(char *d, size_t n, const char *s) { snprintf(d, n, "%s", s ? s : ""); }

/* "label": "text" or ["line 1", "line 2"] (joined with '\n'); falls back to "name" */
static int label_of(const jv *o, char *out, size_t n)
{
	const jv *v = json_get(o, "label");
	size_t i;
	if (!v) v = json_get(o, "name");
	out[0] = 0;
	if (!v) return -1;
	if (v->type == J_STR) { cp(out, n, v->str); return 0; }
	if (v->type != J_ARR || !v->n) return -1;
	for (i = 0; i < v->n; i++) {
		if (v->items[i]->type != J_STR) return -1;
		if (i) strncat(out, "\n", n - strlen(out) - 1);
		strncat(out, v->items[i]->str, n - strlen(out) - 1);
	}
	return 0;
}

/* "#rrggbb", or "ui:<n>" (an entry of the period design's interface colour table, resolved by the application against
 * the store's palette: SCR_UI(n) until then) */
static long hex_colour(const char *s)
{
	long v;
	return scr_parse_colour(s, &v) ? -1 : v;
}

static void colours_of(const jv *o, long *face, long *ink)
{
	const jv *c = json_get(o, "colour");
	if (!c) c = json_get(o, "color");
	*face = *ink = -1;
	if (!c) return;
	if (c->type == J_STR) *face = hex_colour(c->str);
	else if (c->type == J_OBJ) { *face = hex_colour(json_str(c, "face")); *ink = hex_colour(json_str(c, "ink")); }
}

static int num_of(const jv *v, int dflt) { return v && v->type == J_NUM ? (int)v->num : dflt; }

int tax_load(const char *path, taxonomy *t, char *err, size_t errlen)
{
	FILE *f = fopen(path, "rb");
	char *buf;
	long len;
	jv *root;
	const jv *cats, *geo;
	size_t i, k;
	int c;
	memset(t, 0, sizeof(*t));
	if (!f) { snprintf(err, errlen, "cannot open %s", path); return -1; }
	if (fseek(f, 0, SEEK_END) || (len = ftell(f)) < 0 || len > 4 * 1024 * 1024 || fseek(f, 0, SEEK_SET)) {
		fclose(f); snprintf(err, errlen, "%s: unreadable or too large", path); return -1;
	}
	buf = (char *)malloc((size_t)len + 1);
	if (!buf || fread(buf, 1, (size_t)len, f) != (size_t)len) { free(buf); fclose(f); snprintf(err, errlen, "read error"); return -1; }
	fclose(f);
	root = json_parse(buf, (size_t)len, err, errlen);
	free(buf);
	if (!root) return -1;
	cp(t->source, sizeof t->source, path);
	cp(t->status, sizeof t->status, json_str(root, "status"));
	cp(t->schema, sizeof t->schema, json_str(root, "schema"));
	t->placeholder = !strcmp(t->status, "PLACEHOLDER");
	geo = json_get(json_get(root, "geometry"), "category_menu");
	t->cols = num_of(json_get(geo, "columns"), 1);
	t->rows = num_of(json_get(geo, "rows"), 0);
	cats = json_get(root, "categories");
	if (!cats || cats->type != J_ARR || !cats->n) { json_free(root); snprintf(err, errlen, "%s: no \"categories\" array", path); return -1; }
	for (i = 0; i < cats->n && t->ncat < TAX_MAX_CAT; i++) {
		const jv *cj = cats->items[i];
		const jv *subs, *ex, *menu;
		const char *wm, *bar;
		tax_cat *tc = &t->cat[t->ncat];
		if (cj->type != J_OBJ || label_of(cj, tc->label, sizeof tc->label)) continue;
		cp(tc->id, sizeof tc->id, json_str(cj, "id"));
		bar = json_str(cj, "bar_label");
		if (bar) cp(tc->bar, sizeof tc->bar, bar);
		else { char *nl; cp(tc->bar, sizeof tc->bar, tc->label); while ((nl = strchr(tc->bar, '\n'))) *nl = ' '; }
		wm = json_str(cj, "watermark");
		if (wm && strchr(wm, '/') && !strchr(wm, ' ')) cp(tc->watermark, sizeof tc->watermark, wm);
		colours_of(cj, &tc->colour, &tc->ink);
		tc->face_entry = tc->ink_entry = -1;
		tc->chrome_face = tc->chrome_ink = -1;
		/* "draws_on": ["ietf-areas: INT (IP, ND, addressing), WIT (transport)", ...]: the IETF area codes are the
		 * words in capitals of 2-4 letters before any bracket (the Pinpointer's Category filter uses them) */
		{
			const jv *d = json_get(cj, "draws_on");
			size_t q;
			if (d && d->type == J_ARR)
				for (q = 0; q < d->n; q++) {
					const char *ds = d->items[q]->type == J_STR ? d->items[q]->str : NULL, *p;
					int depth = 0;
					if (!ds || strncmp(ds, "ietf-areas:", 11)) continue;
					for (p = ds + 11; *p; p++) {
						size_t L = 0;
						if (*p == '(') depth++;
						else if (*p == ')') depth--;
						if (depth || *p < 'A' || *p > 'Z' || (p > ds && p[-1] >= 'A' && p[-1] <= 'Z')) continue;
						while (p[L] >= 'A' && p[L] <= 'Z') L++;
						if (L >= 2 && L <= 4 && tc->nareas < 8) {
							size_t z;
							for (z = 0; z < L; z++) tc->areas[tc->nareas][z] = (char)(p[z] - 'A' + 'a');
							tc->areas[tc->nareas][L] = 0;
							tc->nareas++;
						}
					}
				}
		}
		menu = json_get(cj, "menu");
		tc->col = tc->row = -1;
		if (menu && menu->type == J_ARR && menu->n == 2) { tc->col = num_of(menu->items[0], 0) - 1; tc->row = num_of(menu->items[1], 0) - 1; }
		subs = json_get(cj, "subcategories");
		if (!subs) subs = json_get(cj, "children");
		if (subs && subs->type == J_ARR)
			for (k = 0; k < subs->n && tc->nsub < TAX_MAX_SUB; k++) {
				const jv *s = subs->items[k], *arts;
				tax_sub *ts = &tc->sub[tc->nsub];
				if (s->type != J_OBJ || label_of(s, ts->label, sizeof ts->label)) continue;
				cp(ts->id, sizeof ts->id, json_str(s, "id"));
				arts = json_get(s, "articles");
				if (arts && arts->type == J_ARR && arts->n && arts->items[0]->type == J_STR) cp(ts->article, sizeof ts->article, arts->items[0]->str);
				tc->nsub++;
			}
		/* corpus examples name a document for a subcategory: the first one becomes its article link */
		ex = json_get(cj, "examples");
		if (ex && ex->type == J_ARR)
			for (k = 0; k < ex->n; k++) {
				const jv *e = ex->items[k];
				const char *kind = json_str(e, "kind"), *dk = json_str(e, "doc_key"), *sub = json_str(e, "sub");
				int s;
				if (!kind || strcmp(kind, "corpus") || !dk || !sub) continue;
				for (s = 0; s < tc->nsub; s++)
					if (!strcmp(tc->sub[s].id, sub) && !tc->sub[s].article[0]) cp(tc->sub[s].article, sizeof tc->sub[s].article, dk);
			}
		t->ncat++;
	}
	json_free(root);
	if (!t->ncat) { snprintf(err, errlen, "%s: no usable categories", path); return -1; }
	if (t->cols < 1) t->cols = 1;
	if (t->rows < 1) t->rows = (t->ncat + t->cols - 1) / t->cols;
	/* grid positions: as given ("menu": [column, row]), else column-major in file order */
	for (c = 0; c < t->ncat; c++)
		if (t->cat[c].col < 0 || t->cat[c].row < 0 || t->cat[c].col >= t->cols || t->cat[c].row >= t->rows) {
			t->cat[c].col = c / t->rows;
			t->cat[c].row = c % t->rows;
		}
	return 0;
}
