/* kind_chips.c: the Library/source chip, reading buttons and independent floating text/list panes. */
#include "room_article.h"
#include "edit.h"
#include "../common/text8.h"
#include <X11/keysym.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* u14 M13: a pane's text is published as UTF-8 rows (art_panel_publish: each row ends in '\n' and may start with an
 * ART_ROW_* byte) and made 8-bit row by row for the pane's face when first drawn, so the row breaks survive (build 3
 * passed the whole text through t8_to_8bit, which drops every byte below 0x20: one paragraph, every click on the first
 * entry). Each row wraps with a 12-px hanging indent and 4 px between rows; a group heading is bold, a quiet row grey;
 * only a row with an id is a jump. */
#define HANG 12
#define ROW_GAP 4
enum { ROW_PLAIN, ROW_GROUP, ROW_QUIET };

typedef struct {
	char name[SCR_ID];
	char *raw, *text, *mark;
	char **ids;
	int nids, top;
	int *lines, *row, *ytop;
	unsigned char *style;
	int nlines, nrows, width, flags;
} pane;
static pane panes[8];
static int npanes;

static pane *pane_of(const char *name)
{
	int i;
	for (i = 0; i < npanes; i++) if (!strcmp(panes[i].name, name)) return &panes[i];
	if (npanes == 8) return NULL;
	snprintf(panes[npanes].name, sizeof panes[npanes].name, "%s", name);
	return &panes[npanes++];
}

static void unwrap(pane *p)
{
	free(p->lines); free(p->row); free(p->ytop);
	p->lines = p->row = p->ytop = NULL;
	p->nlines = p->width = 0;
}

void art_panel_publish(const char *name)
{
	art_state *st = art_st();
	pane *p = pane_of(name);
	int i;
	if (!p) return;
	free(p->raw); free(p->text); free(p->mark); free(p->style);
	unwrap(p);
	for (i = 0; i < p->nids; i++) free(p->ids[i]);
	free(p->ids); p->raw = p->text = NULL; p->style = NULL; p->ids = NULL; p->nids = p->top = p->nrows = 0;
	p->raw = strdup(st->panel_text ? st->panel_text : "");
	p->mark = strdup(st->is_v2 ? ar_marking(&st->d2) : "");
	if (st->panel_rows && (p->ids = (char **)calloc((size_t)st->panel_rows, sizeof *p->ids)) != NULL) {
		p->nids = st->panel_rows;
		for (i = 0; i < p->nids; i++) if (st->panel_ids[i]) p->ids[i] = strdup(st->panel_ids[i]);
	}
}

/* the raw rows as 8-bit text for faces with flags (T8_*), the row marks taken off into style[] */
static void convert(pane *p, int flags)
{
	size_t len, cap, o = 0, s = 0, e;
	int rows = 0;
	if (!p->raw || (p->text && p->flags == flags)) return;
	free(p->text); free(p->style); p->text = NULL; p->style = NULL; unwrap(p);
	len = strlen(p->raw);
	if (len > 8u * 1024u * 1024u) return;
	for (e = 0; e < len; e++) rows += p->raw[e] == '\n';
	cap = 2 * len + 2;
	p->text = (char *)malloc(cap);
	p->style = (unsigned char *)calloc((size_t)rows + 1, 1);
	if (!p->text || !p->style) { free(p->text); free(p->style); p->text = NULL; p->style = NULL; return; }
	p->nrows = 0;
	while (s <= len && o + 1 < cap) {
		for (e = s; e < len && p->raw[e] != '\n'; e++) ;
		if (s < e && (p->raw[s] == ART_ROW_GROUP || p->raw[s] == ART_ROW_QUIET)) {
			p->style[p->nrows] = p->raw[s] == ART_ROW_GROUP ? ROW_GROUP : ROW_QUIET;
			s++;
		}
		o += t8_to_8bit(p->raw + s, e - s, flags, p->text + o, cap - o);
		if (e == len) break;
		if (o + 1 < cap) p->text[o++] = '\n';
		p->nrows++;
		s = e + 1;
	}
	p->text[o] = '\0';
	p->flags = flags;
}

/* the pane's face has code page 1252's quotes and dashes (as room_article.c metrics() asks of the body faces) */
static int face_flags(const e26_app *a, int font)
{
	const XFontStruct *f = a->font[font];
	int i;
	if (!f || f->min_char_or_byte2 > 0x91 || f->max_char_or_byte2 < 0x97) return 0;
	for (i = 0x91; i <= 0x97; i++) if (f->per_char && f->per_char[i - (int)f->min_char_or_byte2].width <= 0) return 0;
	return T8_HAVE_CP1252;
}

static int measure(void *ud, const char *s, int n) { return gfx_text_width((gfx_ctx *)ud, F_UI, s, n); }

static int line_h(gfx_ctx *g) { return gfx_font_height(g, F_UI) + 3; }

static void wrap(pane *p, gfx_ctx *g, int width)
{
	size_t len;
	int i, k, cap, row = 0, scanned = 0, h = line_h(g);
	if (!p->text || (p->width == width && p->lines)) return;
	len = strlen(p->text);
	if (len > 16u * 1024u * 1024u) return;
	cap = (int)(len + 1);
	unwrap(p);
	p->lines = (int *)malloc((size_t)cap * sizeof(int)); p->row = (int *)malloc((size_t)cap * sizeof(int));
	p->ytop = (int *)malloc((size_t)cap * sizeof(int));
	if (!p->lines || !p->row || !p->ytop) { unwrap(p); return; }
	/* every line at the hanging width: the first line of a row starts HANG px left of the rest */
	p->nlines = edit_wrap(p->text, (int)len, width - HANG, measure, g, p->lines, cap); p->width = width;
	for (i = 0; i < p->nlines; i++) {
		for (k = scanned; k < p->lines[i]; k++) if (p->text[k] == '\n') row++;
		scanned = p->lines[i]; p->row[i] = row;
		p->ytop[i] = i * h + row * ROW_GAP;
	}
}

static int first_of_row(const pane *p, int k) { return k == 0 || p->row[k - 1] != p->row[k]; }

/* the lines in view from top in height px */
static int lines_in(const pane *p, int top, int height, int h)
{
	int k = top;
	while (k < p->nlines && p->ytop[k] - p->ytop[top] + h <= height) k++;
	return k - top;
}

static void clamp(pane *p, int height, int h)
{
	int last = p->nlines - 1, top;
	if (p->top < 0) p->top = 0;
	if (last < 0) { p->top = 0; return; }
	/* the highest top that still fills the pane */
	for (top = last; top > 0 && p->ytop[last] - p->ytop[top - 1] + h <= height; top--) ;
	if (p->top > top) p->top = top;
}

static void draw_list(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int x, int y, int under)
{
	const scr_control *c = &s->c[i];
	pane *p = pane_of(s->name);
	int k, h = line_h(g), inset = p && p->mark ? art_marking_inset(a, c, p->mark) : 0, height = c->h - 2 * inset, n;
	(void)under;
	gfx_fill(g, x, y, c->w, c->h, C_PAPER);
	if (!p || !p->raw) return;
	art_marking_draw(a, g, c, x, y, p->mark ? p->mark : "");
	y += inset;
	if (height < h) return;
	convert(p, face_flags(a, F_UI));
	if (!p->text) return;
	wrap(p, g, c->w - 26);
	if (!p->lines) return;
	clamp(p, height, h);
	n = lines_in(p, p->top, height, h);
	gfx_clip(g, x, y, c->w, height);
	for (k = p->top; k < p->top + n; k++) {
		int start = p->lines[k], end = k + 1 < p->nlines ? p->lines[k + 1] : (int)strlen(p->text), r = p->row[k];
		int style = r < p->nrows + 1 && p->style ? p->style[r] : ROW_PLAIN;
		int font = style == ROW_GROUP && a->font[F_UI_BOLD] ? F_UI_BOLD : F_UI;
		long colour = r < p->nids && p->ids[r] ? C_LISTJUMP : style == ROW_QUIET ? ui_rgb(a, UI_CAPTION) : C_BLACK;
		while (end > start && (p->text[end - 1] == '\n' || p->text[end - 1] == '\r')) end--;
		gfx_text(g, x + 6 + (first_of_row(p, k) ? 0 : HANG), y + gfx_ascent(g, F_UI) + p->ytop[k] - p->ytop[p->top], font,
		         colour, p->text + start, end - start);
	}
	if (p->top > 0 || p->top + n < p->nlines) gfx_text(g, x + c->w - 17, y + height - 5, F_UI, C_BLACK, "^v", -1);
	gfx_unclip(g);
}

static int list_wheel(e26_app *a, scr_screen *s, int i, int dir)
{
	pane *p = pane_of(s->name);
	(void)a; (void)i;
	if (p) p->top += 3 * dir;
	return 1;
}

static int list_keys(e26_app *a, scr_screen *s, int i, KeySym ks)
{
	pane *p = pane_of(s->name);
	int rows = s->c[i].h / (gfx_font_height(&a->g, F_UI) + 3);
	if (!p) return 0;
	if (ks == XK_Home) p->top = 0;
	else if (ks == XK_End) p->top = p->nlines;
	else if (ks == XK_Next) p->top += rows;
	else if (ks == XK_Prior) p->top -= rows;
	else if (ks == XK_Up) p->top--;
	else if (ks == XK_Down) p->top++;
	else return 0;
	if (a->dpy) nav_redraw(a);
	return 1;
}

/* the entry under y: the row of the line whose band holds it (the gap between rows belongs to none) */
static int list_release(e26_app *a, scr_screen *s, int i, int x, int y)
{
	pane *p = pane_of(s->name);
	int inset = p && p->mark ? art_marking_inset(a, &s->c[i], p->mark) : 0, h = line_h(&a->g), k;
	int dy = y - s->oy - s->c[i].y - inset;
	(void)x;
	if (!p || !p->lines || dy < 0) return 0;
	for (k = p->top; k < p->nlines; k++) {
		int top = p->ytop[k] - p->ytop[p->top];
		if (top > dy) break;
		if (dy < top + h && p->row[k] < p->nids && p->ids[p->row[k]]) {
			const char *id = p->ids[p->row[k]];
			char *copy, *anchor;
			if (!strncmp(id, "@place:", 7)) return art_place_open(a, atoi(id + 7));
			if (!(copy = strdup(id))) return 0;
			anchor = strchr(copy, '#'); if (anchor) *anchor++ = 0;
			art_open_pack(a, -1, copy, anchor); free(copy); return 1;
		}
	}
	return 0;
}

/* u14 M10: the native reader draws its chip and reading buttons in the reader's interface face */
static int ui_face(e26_app *a) { return a->font[F_RUI] && present_article_viewport(a) ? F_RUI : F_UI; }

static void chip(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int x, int y, int under)
{
	art_state *st = art_st();
	const scr_control *c = &s->c[i];
	int corpus = st->is_v2 && !strcmp(st->d2.cls, "corpus"), f = ui_face(a), small = f == F_RUI ? F_RUI : F_SMALL;
	int b1 = f == F_RUI ? y + 4 + gfx_ascent(g, f) : y + 12, b2 = f == F_RUI ? b1 + gfx_font_height(g, f) : y + 25;
	/* PACK-CORPUS 8: a corpus article shows the RAG chip with its generation, a Library article the Library chip */
	const char *label = corpus ? "RAG" : "Library";
	int updated = st->is_v2 && st->d2.updated && !corpus;
	(void)under;
	gfx_fill(g, x, y, c->w, c->h, C_PAPER); gfx_frame2(g, x, y, c->w, c->h, ui_rgb(a, UI_TAUPE), C_BLACK);
	gfx_clip(g, x, y, c->w - 2, c->h);
	gfx_text(g, x + 4, b1, f, C_BLACK, label, -1);
	if (updated) gfx_text(g, x + 4, b2, small, C_LISTJUMP, "Updated", -1);
	if (corpus && st->d2.corpus_generation) gfx_text(g, x + 4, b2, small, C_BLACK, st->d2.corpus_generation, -1);
	gfx_unclip(g);
}

static int chip_release(e26_app *a, scr_screen *s, int i, int x, int y)
{
	e26_actx ctx;
	(void)s; (void)i; (void)x; (void)y;
	memset(&ctx, 0, sizeof ctx); ctx.ctl = -1;
	(void)action_run(a, reg_find(REG_ACTION, "article.attribution"), &ctx); return 1;
}

static const char *const commands[] = { "article.outline", "article.go-page", "article.further", "article.moreinfo" };
static const char *const labels[] = { "Outline window", "Go to page...", "Further Reading", "More Information" };

/* the four buttons: a column (the left column), or a row when the control is wide (S15: the Text view's status strip) */
static void cell(const scr_control *c, int k, int *cx, int *cy, int *cw, int *ch)
{
	int across = c->w > 4 * c->h;
	*cw = across ? c->w / 4 : c->w;
	*ch = across ? c->h : c->h / 4;
	*cx = across ? k * *cw : 0;
	*cy = across ? 0 : k * *ch;
}

static void buttons(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int x, int y, int under)
{
	int k, f = ui_face(a);
	const scr_control *c = &s->c[i];
	(void)under;
	for (k = 0; k < 4; k++) {
		int bx, by, bw, bh;
		cell(c, k, &bx, &by, &bw, &bh);
		if (bw < 4 || bh < 4) continue;
		gfx_fill(g, x + bx, y + by, bw, bh, C_PAPER);
		gfx_frame2(g, x + bx, y + by, bw, bh, ui_rgb(a, UI_STONE), C_BLACK);
		gfx_clip(g, x + bx, y + by, bw - 2, bh);
		gfx_text(g, x + bx + 5, f == F_RUI ? y + by + (bh + gfx_ascent(g, f) - gfx_descent(g, f)) / 2 : y + by + 12, f, C_BLACK,
		         labels[k], -1);
		gfx_unclip(g);
	}
}

static int buttons_release(e26_app *a, scr_screen *s, int i, int x, int y)
{
	const scr_control *c = &s->c[i];
	int dx = x - s->ox - c->x, dy = y - s->oy - c->y, k, row = -1;
	e26_actx ctx;
	for (k = 0; k < 4; k++) {
		int bx, by, bw, bh;
		cell(c, k, &bx, &by, &bw, &bh);
		if (dx >= bx && dx < bx + bw && dy >= by && dy < by + bh) row = k;
	}
	if (row < 0) return 0;
	memset(&ctx, 0, sizeof ctx); ctx.ctl = -1;
	(void)action_run(a, reg_find(REG_ACTION, commands[row]), &ctx); return 1;
}

void e26_mod_kind_chips(void);
void e26_mod_kind_chips(void)
{
	static const kind_ops chips = { chip, 1, NULL, chip_release, NULL, NULL, NULL, NULL, NULL };
	static const kind_ops reading = { buttons, 1, NULL, buttons_release, NULL, NULL, NULL, NULL, NULL };
	static const kind_ops list = { draw_list, 1, NULL, list_release, NULL, list_wheel, NULL, NULL, list_keys };
	reg_add(REG_KIND, "article-chips", &chips, "kind_chips", 0);
	reg_add(REG_KIND, "article-buttons", &reading, "kind_chips", 0);
	reg_add(REG_KIND, "article-list", &list, "kind_chips", 0);
}
