/* room_article.c: the article room (W1-LAYOUT in wave 1, then W2-ARTICLE): loading an article (format 2,
 * article2.h, or build 1's RFC text, article.h), its layout (layout.h) into the text column, its scroll position and
 * the Library's own scroll bar (or the Motif one where a screen has none), jumps, the selection and search hits, the
 * sources "title", "subtitle" and "section", and the actions open-article, scroll-up, scroll-down, section-prev and
 * section-next. Every article opened is announced (EV_ARTICLE_OPEN) and noted in the reading history (state.c).
 *
 * Scenario hooks for the render harness (tools/render/layout.sh): E26_ARTICLE=<id> opens that article when the
 * article screen is entered with none; E26_HITS=<word> marks its occurrences as search hits (as W1-SEARCH will). */
#include "room_article.h"
#include "article_library.h"
#include "article_clipboard.h"
#include "openat.h"
#include "cursor.h"
#include "appearance.h"
#include "select.h"
#include "../common/text8.h"
#include <X11/Xutil.h>
#include <X11/keysym.h>
#include <Xm/ScrollBar.h>
#include <Xm/Xm.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

static art_state ST = { .sel_a = -1, .sel_b = -1, .out_hot = -1, .out_tip = -1, .repeat_ctl = -1, .out_follow = -1 };

/* u14 S7: Find in article (below): the line is open, the current hit, the typed text (cp1252) */
static struct {
	int open, cur;
	char text[64];
} finder;

static int restoring = -1, restore_scroll, restore_view;
int present_article_viewport(const e26_app *a);
void art_viewport_sync(e26_app *a);

typedef struct {
	int block, heading, src, media, offset;
} viewport_anchor;

static viewport_anchor clipped_anchor = { .block = -1 };
static int clipped_scroll, trace_scroll = -1, trace_media = -1;
static void dump(e26_app *a);
static const char *public_trace_path(const e26_app *a);
art_state *art_st(void) { return &ST; }

/* ---------------------------------------------------------------- the faces (gfx.h) */

/* the layout's faces as the program's font slots. The italic, bold italic, small mono, body-relative small and Symbol
 * slots arrive with INTEG change request LAYOUT-CR1 (scr.h, fonts.c); until then those faces fall back (layout.h). */
int art_font_slot(int lf)
{
	if (lf >= LF_NFACES) return F_ART_SIZE + lf - LF_NFACES;
	switch (lf) {
	case LF_BOLD: return F_BODY_BOLD;
	case LF_MONO: return F_FIXED;
	case LF_UI: return F_UI;
	case LF_UI_BOLD: return F_UI_BOLD;
	case LF_HEAD: return F_HEAD;
	case LF_HEAD_ITALIC: return F_HEAD_ITALIC;
	case LF_SUBHEAD: return F_SUBHEAD;
	case LF_SUBHEAD_ITALIC: return F_SUBHEAD_ITALIC;
	case LF_HEAD3: return F_HEAD3;
#ifdef E26_RICH_FACES
	case LF_ITALIC: return F_BODY_ITALIC;
	case LF_BOLD_ITALIC: return F_BODY_BOLD_ITALIC;
	case LF_MONO_SMALL: return F_FIXED_SMALL;
	case LF_SMALL: return F_BODY_SMALL;
	case LF_SYMBOL: return F_SYMBOL;
#else
	case LF_SMALL: return F_SMALL;
#endif
	default: return F_BODY;
	}
}

static int m_width(void *ud, int f, const char *s, int n) { e26_app *a = (e26_app *)ud; return gfx_text_width(&a->g, art_font_slot(f), s, n); }
static int m_ascent(void *ud, int f) { e26_app *a = (e26_app *)ud; return gfx_ascent(&a->g, art_font_slot(f)); }
static int m_descent(void *ud, int f) { e26_app *a = (e26_app *)ud; return gfx_descent(&a->g, art_font_slot(f)); }

static int m_step_face(void *ud, int face, int direction)
{
	int current = art_font_slot(face), next = fonts_article_step(current, direction);
	(void)ud;
	return current == next ? face : LF_NFACES + next - F_ART_SIZE;
}

/* 1 when the face draws a glyph at byte b (code page 1252's 0x80-0x9F on the TrueType faces listed as iso8859-1 have
 * none) */
static int font_has(const XFontStruct *fs, int b)
{
	const XCharStruct *cs;
	if (!fs) return 0;
	if (b < (int)fs->min_char_or_byte2 || b > (int)fs->max_char_or_byte2) return 0;
	if (!fs->per_char) return 1;
	cs = &fs->per_char[b - (int)fs->min_char_or_byte2];
	return cs->width > 0 && (cs->ascent + cs->descent > 0 || cs->rbearing > cs->lbearing);
}

/* u14 M8: the open article's figures at their source size, as IMAGE reports it (art_media_dims); 0: not known yet */
static struct { int w, h; } *media_dims;
static int media_dims_n, dims_timer;
static unsigned long dims_gen;
static double dims_due;

static int m_media_size(void *ud, const char *id, int *w, int *h)
{
	e26_app *a = (e26_app *)ud;
	scr_screen *s;
	scr_control *c;
	int i, k, cap;
	for (i = 0; i < media_dims_n && i < ST.d2.nmedia && strcmp(ST.d2.media[i].id, id); i++) ;
	if (!media_dims || i >= media_dims_n || i >= ST.d2.nmedia || media_dims[i].w <= 0 || media_dims[i].h <= 0) return 0;
	/* one source pixel to one device pixel: the letterbox draws k device pixels to the layout's one */
	k = present_article_viewport(a) || a->g.k < 1 ? 1 : a->g.k;
	*w = (media_dims[i].w + k - 1) / k;
	*h = (media_dims[i].h + k - 1) / k;
	/* never taller than three quarters of the reader's view (the aspect kept) */
	s = scr_find(&a->set, "article");
	c = s ? find_kind(s, K_ARTICLE) : NULL;
	cap = c ? (c->h - 2 * art_banner_inset(a, c)) * 3 / 4 : 0;
	if (cap > 16 && *h > cap) {
		*w = (int)((long)*w * cap / *h);
		if (*w < 1) *w = 1;
		*h = cap;
	}
	return 1;
}

static void dims_relayout(e26_app *a, void *ud)
{
	double now = libc_now_ms();
	dims_timer = 0;
	if ((unsigned long)(uintptr_t)ud != dims_gen || !a->have_art) return;
	if (now < dims_due) {
		dims_timer = 1;
		timer_after(a, (unsigned long)(dims_due - now) + 1, dims_relayout, ud);
		return;
	}
	art_relayout(a);
	if (a->dpy) nav_redraw(a);
}

void art_media_dims(e26_app *a, int media, int sw, int sh)
{
	if (!a->have_art || !ST.is_v2 || media < 0 || media >= ST.d2.nmedia || sw <= 0 || sh <= 0) return;
	if (!media_dims) {
		if (!(media_dims = calloc((size_t)ST.d2.nmedia, sizeof *media_dims))) return;
		media_dims_n = ST.d2.nmedia;
	}
	if (media >= media_dims_n || (media_dims[media].w == sw && media_dims[media].h == sh)) return;
	media_dims[media].w = sw;
	media_dims[media].h = sh;
	/* a new size: lay out again once the sizes stop arriving for 150 ms */
	dims_due = libc_now_ms() + 150;
	if (!dims_timer && a->ctx) {
		dims_timer = 1;
		timer_after(a, 150, dims_relayout, (void *)(uintptr_t)dims_gen);
	}
}

static void metrics(e26_app *a, lay_metrics *M)
{
	int i;
	memset(M, 0, sizeof *M);
	M->width = m_width;
	M->ascent = m_ascent;
	M->descent = m_descent;
	M->media_size = m_media_size;
	M->step_face = m_step_face;
	M->n_size_faces = F_NFONTS - F_ART_SIZE;
	M->open_formula = ST.open_formula;
	M->ud = a;
	M->pitch = a->line > 0 ? a->line : E26_LINE;
	M->have[LF_BODY] = M->have[LF_BOLD] = M->have[LF_MONO] = M->have[LF_UI] = M->have[LF_UI_BOLD] = M->have[LF_SMALL] = 1;
	M->have[LF_HEAD] = a->font[F_HEAD] != NULL;
	M->have[LF_HEAD_ITALIC] = a->font[F_HEAD_ITALIC] != NULL;
	M->have[LF_SUBHEAD] = a->font[F_SUBHEAD] != NULL;
	M->have[LF_SUBHEAD_ITALIC] = a->font[F_SUBHEAD_ITALIC] != NULL;
	M->have[LF_HEAD3] = a->font[F_HEAD3] != NULL;
	/* u14: the em, the body face's pixel size (the unit of the page's spacing, layout.h) */
	{
		Atom size_atom = a->dpy ? XInternAtom(a->dpy, "PIXEL_SIZE", True) : None;
		unsigned long px;
		M->em = a->font[F_BODY] && size_atom != None && XGetFontProperty(a->font[F_BODY], size_atom, &px) && px > 0 ?
		    (int)px : a->font[F_BODY] ? a->font[F_BODY]->ascent + a->font[F_BODY]->descent : 0;
	}
#ifdef E26_RICH_FACES
	M->have[LF_ITALIC] = a->font[F_BODY_ITALIC] != NULL;
	M->have[LF_BOLD_ITALIC] = a->font[F_BODY_BOLD_ITALIC] != NULL;
	M->have[LF_MONO_SMALL] = a->font[F_FIXED_SMALL] != NULL;
	M->have[LF_SYMBOL] = a->font[F_SYMBOL] != NULL && font_has(a->font[F_SYMBOL], 0x61);
#endif
	/* code page 1252's curly quotes, dashes and bullet: only when the body faces draw them */
	M->t8flags = T8_HAVE_CP1252;
	for (i = 0x91; i <= 0x97; i++)
		if (!font_has(a->font[F_BODY], i) || !font_has(a->font[F_BODY_BOLD], i)) M->t8flags = 0;
}

/* ---------------------------------------------------------------- layout */

static int available_text_width(const scr_control *c)
{
	/* Keep a right margin and room for the scroll bar. */
	return c->w - (c->text_x >= 0 ? c->text_x : 10) - 10 - 16;
}

int art_text_origin(const scr_control *c)
{
	int inset = c->text_x >= 0 ? c->text_x : 10;
	int extra = available_text_width(c) - ST.width;

	return inset + (ST.width > 0 && extra > 0 ? extra / 2 : 0);
}

/* the reading measure (u14 M3): ReaderColumn's 62, 75 or 90 average characters of the body face (the pangram's
 * average), clamped to [420, pane - 48] and never the whole pane (Helvetica 13 px measured 438 and fell back to 1728 px,
 * about 280 characters a line). The faces are first made right for the viewport (fonts_viewport): the Text size in the
 * native reader, 100% in the letterbox. */
static int text_width(e26_app *a)
{
	scr_screen *s = scr_find(&a->set, "article");
	scr_control *c = s ? find_kind(s, K_ARTICLE) : NULL;
	static const char sample[] = "The quick brown fox jumps over the lazy dog.";
	static const int chars[3] = { 62, 75, 90 };
	int width, measure, lo = 420, hi, n;

	if (a->dpy) (void)fonts_viewport(a, present_article_viewport(a));
	width = c ? available_text_width(c) : 440;
	if (!present_article_viewport(a) || !a->font[F_BODY]) return width;
	n = chars[a->reader_column >= 0 && a->reader_column < 3 ? a->reader_column : E26_COLUMN_NORMAL];
	measure = gfx_text_width(&a->g, F_BODY, sample, sizeof sample - 1) * n / (int)(sizeof sample - 1);
	hi = width - 48;
	if (hi < lo) return width;   /* a tiny window: the whole pane */
	return measure < lo ? lo : measure > hi ? hi : measure;
}

/* u14: the reader pane's whole text width (never less than the measure): code boxes may use it (layout.h, pane) */
static int pane_width(e26_app *a, int width)
{
	scr_screen *s = scr_find(&a->set, "article");
	scr_control *c = s ? find_kind(s, K_ARTICLE) : NULL;
	int pane = c ? available_text_width(c) : width;
	return pane > width ? pane : width;
}

void art_layout(e26_app *a, int width)
{
	if (!a->have_art || !a->font[F_BODY]) { lay_free(&ST.L); a->doc_h = 0; return; }
	metrics(a, &ST.M);
	ST.M.pane = pane_width(a, width);
	ST.width = width;
	if (ST.is_v2) lay_v2(&ST.L, &ST.M, &ST.d2, width);
	else lay_v1(&ST.L, &ST.M, &a->art, width);
	a->doc_h = ST.L.h;
	/* u14 M11: a format-2 article's outline is a table of contents (exhibits with OutlineExhibits) */
	ST.tree.toc = ST.is_v2;
	ST.tree.exhibits = a->outline_exhibits;
	if (ar_tree_build(&ST.tree, &ST.L, 0)) fprintf(stderr, "d32library: cannot build article outline\n");
	if (ST.L.oom) fprintf(stderr, "d32library: article %s: out of memory while laying out; the page is incomplete\n", a->art_id);
}

static int viewport_geometry_changed(const e26_app *a, const scr_screen *s)
{
	if (!s) return 0;
	if (present_article_viewport(a)) return s->w != a->sc.w || s->h != a->sc.h - 17;
	return s->w > E26_W || s->h > E26_H - 17;
}

static int viewport_block_top(int block)
{
	int i, top = ST.L.h;
	if (block < 0) return -1;
	for (i = 0; i < ST.L.nln; i++)
		if (ST.L.ln[i].block == block && ST.L.ln[i].top < top) top = ST.L.ln[i].top;
	for (i = 0; i < ST.L.nit; i++)
		if (ST.L.it[i].block == block && ST.L.it[i].kind != LI_TEXT && ST.L.it[i].y < top) top = ST.L.it[i].y;
	return top < ST.L.h ? top : -1;
}

/* Anchor the first visible source block, with a byte offset for wrapped text or a media ID index for a picture. */
static viewport_anchor viewport_at(int scroll)
{
	viewport_anchor at = { .block = -1, .heading = -1, .src = -1, .media = -1 };
	int i, j, y = ST.L.h, heading = lay_current_entry(&ST.L, scroll);
	if (heading >= 0) at.heading = ST.L.en[heading].block;
	for (i = 0; i < ST.L.nln; i++) {
		const lay_line *line = &ST.L.ln[i];
		if (line->bot <= scroll || line->block < 0 || line->top >= y) continue;
		at.block = line->block;
		at.src = -1;
		y = line->top;
		for (j = line->first; j < line->first + line->n; j++) {
			if (ST.L.it[j].src >= 0) { at.src = ST.L.it[j].src; break; }
		}
	}
	for (i = 0; i < ST.L.nit; i++) {
		const lay_item *it = &ST.L.it[i];
		/* An inset or table border can surround the first visible source line. Keep that line's byte offset. */
		if (at.src >= 0 && it->block == at.block && it->kind != LI_MEDIA) continue;
		if ((it->kind != LI_MEDIA && it->kind != LI_FRAME && it->kind != LI_FILL && it->kind != LI_FORMULA) ||
		    it->block < 0 || it->h <= 0 || it->y + it->h <= scroll ||
		    it->y > y || (it->y == y && it->kind != LI_MEDIA)) continue;
		at.block = it->block;
		at.src = -1;
		at.media = it->kind == LI_MEDIA ? it->media : -1;
		y = it->y;
	}
	if (at.block >= 0 && at.src < 0 && at.media < 0) y = viewport_block_top(at.block);
	at.offset = scroll - y;
	return at;
}

static int viewport_scroll(const viewport_anchor *at, int old_scroll)
{
	int i, top;
	if (at->src >= 0 && (i = lay_item_at_pos(&ST.L, at->src)) >= 0 &&
	    ST.L.it[i].block == at->block && ST.L.it[i].line >= 0)
		return ST.L.ln[ST.L.it[i].line].top + at->offset;
	if (at->media >= 0) {
		for (i = 0; i < ST.L.nit; i++) {
			const lay_item *it = &ST.L.it[i];
			if (it->kind == LI_MEDIA && it->block == at->block && it->media == at->media)
				return it->y + (at->offset < it->h ? at->offset : it->h - 1);
		}
	}
	top = viewport_block_top(at->block);
	if (top >= 0) return top + at->offset;
	top = viewport_block_top(at->heading);
	return top >= 0 ? top : old_scroll;
}

int art_public_position(const e26_app *a)
{
	const char *pack = ar_lib_name(ST.pack);
	return a->have_art && ST.is_v2 && ST.full_id && !*ar_marking(&ST.d2) &&
	    (!strcmp(pack, "lib-e26") || !strcmp(pack, "lib-corpus"));
}

static int reader_heading(int scroll)
{
	int i = lay_current_entry(&ST.L, scroll);
	while (i >= 0 && ST.L.en[i].kind != LE_HEADING) i--;
	return i;
}

static int reader_position(e26_app *a, e26_reader_pos *pos, int *inset)
{
	viewport_anchor at;
	int entry, block = 0;
	const char *hash = ST.d2.content_sha256;
	if (!art_public_position(a) || ST.L.oom || !hash || strlen(hash) != E26_READER_HASH_HEX) return 0;
	memset(pos, 0, sizeof *pos);
	memcpy(pos->hash, hash, sizeof pos->hash);
	pos->block = pos->text = -1;
	*inset = 0;
	if (!a->scroll) return 1;
	at = viewport_at(a->scroll);
	entry = reader_heading(a->scroll);
	if (entry >= 0) {
		int k = ST.L.en[entry].block;
		const char *section = k >= 0 && k < ST.d2.nb ? ST.d2.b[k].anchor : NULL;
		if (section && strlen(section) < sizeof pos->section) {
			snprintf(pos->section, sizeof pos->section, "%s", section);
			block = k;
		}
	}
	if (at.block < block) return pos->section[0] != '\0';
	pos->block = at.block - block;
	pos->text = at.src;
	*inset = at.offset;
	return 1;
}

static int reader_position_y(const e26_reader_pos *pos, int inset)
{
	int section = 0, fallback = 0, block, i, top;
	if (pos->section[0]) {
		int k = lay_anchor_find(&ST.L, LA_HEADING, pos->section);
		if (k < 0) return 0;
		section = ST.L.an[k].block;
		fallback = ST.L.an[k].y;
	}
	if (!ST.d2.content_sha256 || strcmp(ST.d2.content_sha256, pos->hash) ||
	    section < 0 || section >= ST.d2.nb || pos->block < 0 ||
	    pos->block >= ST.d2.nb - section) return fallback;
	block = section + pos->block;
	if (pos->text >= 0 && (i = lay_item_at_pos(&ST.L, pos->text)) >= 0 &&
	    ST.L.it[i].block == block && ST.L.it[i].line >= 0)
		return ST.L.ln[ST.L.it[i].line].top + inset;
	top = viewport_block_top(block);
	return top < 0 ? fallback : top + inset;
}

void art_history_capture(e26_app *a)
{
	ar_place *place;
	int entry;
	ar_history_save(&ST.history, a->scroll, ST.view);
	if (!a->have_art || !ST.full_id || !ST.history.n) return;
	place = &ST.history.p[ST.history.at];
	if (place->pack != ST.pack || strcmp(place->id, ST.full_id)) return;
	/* u14 M18: every unmarked place is named (the Back List, the history menu); a marked one stays "Article" */
	if (ST.is_v2 && *ar_marking(&ST.d2)) return;
	snprintf(place->title, sizeof place->title, "%.*s", (int)sizeof place->title - 1, a->art.title);
	entry = reader_heading(a->scroll);
	snprintf(place->section, sizeof place->section, "%.*s", (int)sizeof place->section - 1,
	    entry < 0 ? "Beginning" : ST.L.en[entry].text);
	place->position_valid = reader_position(a, &place->position, &place->inset);
}

void art_history_position(e26_app *a, const ar_place *place)
{
	int y = place->position_valid ? reader_position_y(&place->position, place->inset) : place->scroll;
	art_scroll_to(a, y);
}

void art_save_position(e26_app *a)
{
	const scr_screen *base = a->nwin ? a->wstack[0].s : a->cur;
	e26_reader_pos pos;
	int inset;
	if (base && !strcmp(base->name, "article") && *state_dir() &&
	    reader_position(a, &pos, &inset))
		state_reader_save(ar_lib_name(ST.pack), ST.full_id, &pos);
}

/* u14 S12: "Resumed at <section> . Start from top" stays in the status strip until the reader first scrolls
 * (art_scroll_by, the scroll bar, the outline), not five seconds */
static void resume_reader(e26_app *a)
{
	e26_reader_pos pos;
	if (!art_public_position(a) ||
	    !state_reader_load(ar_lib_name(ST.pack), ST.full_id, &pos)) return;
	art_scroll_to(a, reader_position_y(&pos, 0));
	if (!a->scroll || !a->ctx) return;
	ST.resumed_until = 1;
}

void art_resume_done(e26_app *a)
{
	if (!ST.resumed_until) return;
	ST.resumed_until = 0;
	art_features_controls(a);
}

void art_relayout(e26_app *a)
{
	viewport_anchor at;
	int old_scroll;
	if (!a->have_art) return;
	old_scroll = a->scroll;
	at = viewport_at(old_scroll);
	clipped_anchor.block = -1;
	art_layout(a, text_width(a));
	a->scroll = viewport_scroll(&at, old_scroll);
	art_place_scrollbar(a);
	if (present_article_viewport(a) && public_trace_path(a)) dump(a);
}

void art_formula_toggle(e26_app *a, int block)
{
	if (!ST.is_v2 || block < 0 || block >= ST.d2.nb || ST.d2.b[block].kind != A2_MATHS) return;
	ST.open_formula = ST.open_formula == block + 1 ? 0 : block + 1;
	art_relayout(a);
}

/* ---------------------------------------------------------------- loading and opening */

static void unload(e26_app *a)
{
	if (!a->have_art) return;
	clipped_anchor.block = -1;
	trace_scroll = trace_media = -1;
	art_media_changed(a);
	free(media_dims);
	media_dims = NULL;
	media_dims_n = 0;
	dims_gen++;
	free(ST.full_id); ST.full_id = NULL;
	ar_tree_free(&ST.tree);
	if (ST.is_v2) { a2_free(&ST.d2); memset(&a->art, 0, sizeof a->art); }
	else article_free(&a->art);
	lay_free(&ST.L);
	free(ST.hits);
	ST.hits = NULL;
	ST.nhits = 0;
	ST.sel_a = ST.sel_b = -1;
	ST.out_top = 0;
	ST.out_follow = -1;
	ST.out_hot = ST.out_tip = -1;
	ST.is_v2 = 0;
	ST.open_formula = 0;
	ST.focused_inset = 0;
	ST.resumed_until = 0;
	finder.open = 0;      /* u14 S7: the typed text stays for F3 in the next article */
	finder.cur = -1;
	a->have_art = 0;
}

static int exists(const char *p)
{
	FILE *f = fopen(p, "rb");
	if (!f) return 0;
	fclose(f);
	return 1;
}

/* the file of an article id: <dir>/<id>.art, the id with its scheme's ':' as '-' ("wiki:GRE" -> wiki-GRE.art, how the
 * fixtures name their files), or a corpus doc_key's first section ("rfc2328" -> rfc2328-s1) */
static int article_path(e26_app *a, const char *want, char *path, size_t n, char *id, size_t idn)
{
	char alt[200];
	const char *colon;
	snprintf(id, idn, "%s", want);
	snprintf(path, n, "%s/%s.art", a->article_dir, want);
	if (exists(path)) return 1;
	if ((colon = strchr(want, ':')) != NULL) {
		snprintf(alt, sizeof alt, "%.*s-%s", (int)(colon - want), want, colon + 1);
		snprintf(path, n, "%s/%s.art", a->article_dir, alt);
		if (exists(path)) return 1;
	}
	snprintf(id, idn, "%s-s1", want);
	snprintf(path, n, "%s/%s-s1.art", a->article_dir, want);
	return exists(path);
}

static const char *public_trace_path(const e26_app *a)
{
	const char *path = getenv("E26_LAYOUT_DUMP");
	return a->have_art && path && *path && (!ST.is_v2 || !*ar_marking(&ST.d2)) ? path : NULL;
}

static void dump_geometry(e26_app *a, FILE *f, scr_screen *s, const scr_control *pane)
{
	e26_scale sc = a->sc;
	viewport_anchor at = viewport_at(a->scroll);
	const scr_control *track = find_kind(s, K_SCROLL);
	int bx = pane->x + (pane->absolute ? 0 : s->ox), by = pane->y + (pane->absolute ? 0 : s->oy);
	int banner = art_banner_inset(a, pane);
	int ix = bx + art_text_origin(pane), iy = by + banner;
	int tx = -1, ty = -1, tw = 0, th = 0;

	/* Installs and view actions can publish a snapshot before the next paint selects the transform. */
	if (sc.w <= 0 || sc.h <= 0) sc = (e26_scale){ .k = 1, .w = E26_W, .h = E26_H };
	if (present_article_viewport(a)) { sc.k = 1; sc.ox = sc.oy = 0; }
	else scale_fit(&sc, sc.w, sc.h);
	scale_to_window(&sc, bx, by, &bx, &by);
	scale_to_window(&sc, ix, iy, &ix, &iy);
	if (a->dpy && track && track->strip[0]) {
		xasset *thumb = xa_get(a, track->strip);
		if (thumb && art_scroll_thumb_at(a, track, track->y + (track->absolute ? 0 : s->oy), &ty, &th)) {
			tw = thumb->w / track->frames;
			tx = track->x + (track->absolute ? 0 : s->ox) + (track->w - tw) / 2;
			scale_to_window(&sc, tx, ty, &tx, &ty);
			tw *= sc.k;
			th *= sc.k;
		}
	}
	fprintf(f, "X\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d"
	        "\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\n",
	        sc.w, sc.h, ST.view, bx, by, pane->w * sc.k, pane->h * sc.k,
	        ix, iy, ST.width * sc.k, (pane->h - 2 * banner) * sc.k,
	        ST.M.pitch * sc.k, a->scroll, at.block, at.heading, tx, ty, tw, th);
	trace_scroll = a->scroll;
}

/* E26_LAYOUT_DUMP=<file>: the display list of each article opened, appended as tab-separated lines (the render
 * scenarios find their jumps in it, and check the layout with the real faces' metrics):
 *   A <id> <width> <height> <lines> <items> <ms>
 *   V <view> <pane-width> <scroll>                                       public view diagnostics
 *   T <x> <y> <w> <face> <colour> <link> <src> <srclen> <8-bit text>      a text item (document coordinates)
 *   S <kind> <x> <y> <w> <h> <colour>                                    a shape (LI_FILL ... LI_MEDIA)
 *   E <kind> <level> <y> <8-bit text>                                    an outline entry
 *   X <client width height> <view> <body rect> <text rect> <pitch> <scroll>
 *     <first block> <heading block> <thumb rect>                        client pixels except document scroll
 *   L <line> <block> <kind> <top> <baseline> <bottom>                    document pixels
 *   J <link> <target kind> <id> <anchor> <RFC number> <section>          link references
 *   F <item> <media> <id> <rect>                                       figure in document pixels
 *   DONE terminates each unmarked snapshot. Standalone X, K (followed link), and M (enlarged media) may follow. */
static void dump(e26_app *a)
{
	const char *p = getenv("E26_LAYOUT_DUMP");
	FILE *f;
	int i, marked = ST.is_v2 && *ar_marking(&ST.d2); /* marked reader text never enters a render log: */
	if (!p || !p[0] || !(f = fopen(p, "a"))) return;            /* its header line only (id, metrics) */
	fprintf(f, "A\t%s\t%d\t%d\t%d\t%d\t%d\n", a->art_id, ST.width, ST.L.h, ST.L.nln, ST.L.nit, ST.L.ms);
	if (!marked) {
		scr_screen *screen = scr_find(&a->set, "article");
		scr_control *pane = screen ? find_kind(screen, K_ARTICLE) : NULL;
		if (pane) {
			fprintf(f, "V\t%d\t%d\t%d\n", ST.view, pane->w, a->scroll);
			dump_geometry(a, f, screen, pane);
		}
	}
	for (i = 0; !marked && i < ST.L.nit; i++) {
		const lay_item *it = &ST.L.it[i];
		if (it->kind == LI_TEXT)
			fprintf(f, "T\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%.*s\n", it->x, it->y, it->w, it->face, it->colour, it->link, it->src, it->srclen,
			        it->len, ST.L.t8 + it->off);
		else fprintf(f, "S\t%d\t%d\t%d\t%d\t%d\t%d\n", it->kind, it->x, it->y, it->w, it->h, it->colour);
	}
	for (i = 0; !marked && i < ST.L.nen; i++) fprintf(f, "E\t%d\t%d\t%d\t%s\n", ST.L.en[i].kind, ST.L.en[i].level, ST.L.en[i].y, ST.L.en[i].text);
	if (!marked) {
		for (i = 0; i < ST.L.nln; i++) {
			const lay_line *line = &ST.L.ln[i];
			int kind = ST.is_v2 && line->block >= 0 && line->block < ST.d2.nb ? ST.d2.b[line->block].kind : -1;
			fprintf(f, "L\t%d\t%d\t%d\t%d\t%d\t%d\n", i, line->block, kind, line->top, line->base, line->bot);
		}
		for (i = 0; i < ST.L.nlk; i++) {
			const a2_target *t = ST.L.lk[i].target;
			fprintf(f, "J\t%d\t%d\t%s\t%s\t%ld\t%s\n", i, t ? t->kind : -1,
			        t && t->id ? t->id : "", t && t->anchor ? t->anchor : "",
			        t ? t->number : 0L, t && t->section ? t->section : "");
		}
		for (i = 0; ST.is_v2 && i < ST.L.nit; i++) {
			const lay_item *it = &ST.L.it[i];
			if (it->kind == LI_MEDIA && it->media >= 0 && it->media < ST.d2.nmedia)
				fprintf(f, "F\t%d\t%d\t%s\t%d\t%d\t%d\t%d\n", i, it->media,
				        ST.d2.media[it->media].id, it->x, it->y, it->w, it->h);
		}
		fputs("DONE\n", f);
	}
	fclose(f);
}

void art_viewport_sync(e26_app *a)
{
	scr_screen *s = scr_find(&a->set, "article");
	scr_screen *base = a->nwin ? a->wstack[0].s : a->cur;
	const char *path;
	int media, refonted;
	FILE *f;

	if (!s) return;
	if (clipped_anchor.block >= 0 && a->scroll != clipped_scroll) clipped_anchor.block = -1;
	/* u14: entering or leaving the native reader reloads the faces (the Text size there, 100% in the letterbox) */
	refonted = a->dpy && fonts_viewport(a, present_article_viewport(a));
	if (refonted || viewport_geometry_changed(a, s)) {
		viewport_anchor at = clipped_anchor.block >= 0 ? clipped_anchor : viewport_at(a->scroll);
		int wanted = a->scroll, width;
		art_view_apply(a);
		width = text_width(a);
		if (a->have_art && (refonted || width != ST.width || pane_width(a, width) != ST.M.pane)) {
			art_layout(a, width);
			wanted = viewport_scroll(&at, wanted);
		} else if (clipped_anchor.block >= 0) wanted = viewport_scroll(&at, wanted);
		a->scroll = wanted;
		art_place_scrollbar(a);
		clipped_anchor.block = -1;
		if (a->scroll < wanted) {
			clipped_anchor = at;
			clipped_scroll = a->scroll;
		}
		if (a->have_art && base == s && public_trace_path(a)) dump(a);
	}
	if (base != s || !(path = public_trace_path(a))) return;
	media = ST.is_v2 && a->cur && !strcmp(a->cur->name, "article-enlarge") ? ST.media_index : -1;
	if (trace_scroll == a->scroll && trace_media == media) return;
	if (!(f = fopen(path, "a"))) return;
	if (trace_scroll != a->scroll) {
		scr_control *pane = find_kind(s, K_ARTICLE);
		if (pane) dump_geometry(a, f, s, pane);
	}
	if (media != trace_media && media >= 0 && media < ST.d2.nmedia)
		fprintf(f, "M\t%s\n", ST.d2.media[media].id);
	trace_media = media;
	fclose(f);
}

int art_load(e26_app *a, const char *want)
{
	char path[1200], err[300], code[32], id[80];
	FILE *f;
	char head[9];
	size_t got;
	e26_event ev;
	a2_doc d;
	article old;
	/* ids never name a path (ARTICLE-FORMAT-2: no '/' in any id scheme the reader opens from files) */
	if (!want || !want[0] || strchr(want, '/') || strchr(want, '\\') || want[0] == '.') return -1;
	article_path(a, want, path, sizeof path, id, sizeof id);
	if (a->have_art && !strcmp(a->art_id, id)) return 0;
	if (!(f = fopen(path, "rb"))) {
		if (!a->ctx) return -1; /* no event loop (the unit tests): nothing could answer */
		art_fetch_start(a, -1, want, NULL, -1, 0, set_get_int("MainWindowView", ART_VIEW_DEFAULT));
		return 1; /* accepted asynchronously; old content remains in view */
	}
	got = fread(head, 1, sizeof head, f);
	fclose(f);
	if (a2_is_v2(head, got)) {
		memset(&d, 0, sizeof d);
		if (a2_load(path, &d, code, sizeof code, err, sizeof err)) { fprintf(stderr, "d32library: article validation failed: %s\n", code); return -1; }
		if (art_install_v2(a, &d, -1, NULL, restoring, restoring >= 0 ? restore_scroll : 0,
		                   restoring >= 0 ? restore_view : set_get_int("MainWindowView", ART_VIEW_DEFAULT))) { a2_free(&d); return -1; }
		return 0;
	}
	memset(&old, 0, sizeof old);
	if (article_load(path, &old, err, sizeof err)) { fprintf(stderr, "d32library: article validation failed\n"); return -1; }
	art_history_capture(a);
	art_save_position(a);
	unload(a);
	a->art = old;
	ST.full_id = strdup(id);
	ST.pack = -1;
	a->have_art = 1;
	snprintf(a->art_id, sizeof a->art_id, "%s", id);
	a->scroll = 0;
	art_layout(a, text_width(a));
	if (restoring >= 0) ST.history.at = restoring;
	art_features_opened(a, restoring < 0);
	if (a->verbose)
		fprintf(stderr, "d32library: article %s: format %d, %d blocks, %d lines, %d items, %d outline entries, %d px, laid out in %d ms\n",
		        id, ST.is_v2 ? 2 : 1, ST.is_v2 ? ST.d2.nb : a->art.nb, ST.L.nln, ST.L.nit, ST.L.nen, a->doc_h, ST.L.ms);
	dump(a);
	memset(&ev, 0, sizeof ev);
	ev.type = EV_ARTICLE_OPEN;
	ev.name = a->art_id;
	ev.ctl = -1;
	ev_publish(a, &ev);
	return 0;
}

int art_restore_local(e26_app *a, const ar_place *place, int at)
{
	char *id = strdup(place->id);
	int result;
	if (!id) return -1;
	restoring = at; restore_scroll = place->scroll; restore_view = place->view;
	result = art_load(a, id);
	restoring = -1;
	if (!result) { art_view_set(a, restore_view, 0); art_scroll_to(a, restore_scroll); }
	free(id); return result;
}

void art_open(e26_app *a, const char *id)
{
	e26_hist snap;
	nav_snapshot(a, &snap); /* Go Back returns to what was in view before this article */
	if (art_load(a, id)) return;
	a->scroll = 0; /* an article always opens at its top */
	nav_go_from(a, "article", 1, &snap);
}

/* Exact stored heading, including its selection in the canonical text. A missing
 * heading always falls back to the beginning, including an already-open article. */
static void jump_requested(e26_app *a, const char *anchor)
{
	int k = anchor && *anchor ? lay_anchor_find(&ST.L, LA_HEADING, anchor) : -1, i;
	ST.sel_a = ST.sel_b = -1;
	ST.message[0] = 0;
	art_scroll_to(a, k < 0 ? 0 : ST.L.an[k].y);
	if (anchor && *anchor && k < 0) {
		snprintf(ST.message, sizeof ST.message, "The requested section is unavailable. The document is open at its beginning.");
		return;
	}
	if (k < 0) return;
	for (i = 0; i < ST.L.nit; i++) {
		const lay_item *it = &ST.L.it[i];
		if (it->block != ST.L.an[k].block || it->src < 0 || it->srclen <= 0) continue;
		if (ST.sel_a < 0 || it->src < ST.sel_a) ST.sel_a = it->src;
		if (it->src + it->srclen > ST.sel_b) ST.sel_b = it->src + it->srclen;
	}
}

int art_install_v2(e26_app *a, a2_doc *d, int pack, const char *anchor, int history_at, int scroll, int view)
{
	e26_hist snap;
	e26_event ev;
	char *id = strdup(d->id);
	const char *override = getenv("E26_ARTICLE_VIEW");
	lay_metrics m;
	if (!id) return -1;
	nav_snapshot(a, &snap);
	art_history_capture(a);
	art_save_position(a);
	unload(a);
	ST.d2 = *d; memset(d, 0, sizeof *d);
	ST.full_id = id; ST.is_v2 = 1; ST.pack = pack; ST.busy = 0; ST.media_index = 0;
	ST.message[0] = 0;
	a->have_art = 1;
	snprintf(a->art_id, sizeof a->art_id, "%s", id);
	snprintf(a->art.id, sizeof a->art.id, "%s", id);
	metrics(a, &m);
	t8_to_8bit(ST.d2.title, strlen(ST.d2.title), m.t8flags, a->art.title, sizeof a->art.title);
	a->scroll = 0;
	ST.view = view >= 0 && view < 4 ? view : ART_VIEW_DEFAULT;
	if (override && override[0] >= '0' && override[0] <= '3' && !override[1]) ST.view = override[0] - '0';
	art_view_apply(a);
	art_layout(a, text_width(a));
	if (ar_tree_build(&ST.tree, &ST.L, 1)) snprintf(ST.message, sizeof ST.message, "The outline could not be allocated.");
	if (history_at >= 0 && history_at < ST.history.n) ST.history.at = history_at;
	else if (ar_history_push(&ST.history, id, pack, scroll, ST.view)) snprintf(ST.message, sizeof ST.message, "History is full: not enough memory.");
	art_features_opened(a, 0);
	memset(&ev, 0, sizeof ev); ev.type = EV_ARTICLE_OPEN; ev.name = id; ev.ctl = -1; ev_publish(a, &ev);
	/* The initial local render fixture is installed during SCREEN_ENTER. Do not recursively enter that screen. */
	if (a->cur && (strcmp(a->cur->name, "article") || pack >= 0)) nav_go_from(a, "article", history_at < 0, &snap);
	art_scroll_to(a, scroll);
	if (anchor && *anchor) jump_requested(a, anchor);
	else if (history_at >= 0 && history_at < ST.history.n)
		art_history_position(a, &ST.history.p[history_at]);
	else resume_reader(a);
	art_history_capture(a);
	art_features_controls(a);
	dump(a);
	if (a->dpy) nav_redraw(a);
	return 0;
}

void art_open_pack(e26_app *a, int pack, const char *id, const char *anchor)
{
	if (ST.is_v2 && ST.full_id && !strcmp(ST.full_id, id) && (pack < 0 || pack == ST.pack)) {
		e26_hist snap;
		int reenter = !a->cur || strcmp(a->cur->name, "article") || a->nwin > 1;
		nav_snapshot(a, &snap);
		art_history_capture(a);
		if ((anchor && *anchor) || !art_public_position(a)) {
			ST.resumed_until = 0;
			jump_requested(a, anchor);
			if (ar_history_push(&ST.history, id, ST.pack, a->scroll, ST.view)) return;
		} else {
			ST.sel_a = ST.sel_b = -1;
			ST.message[0] = 0;
		}
		if (reenter) {
			nav_go_from(a, "article", 1, &snap);
			if ((!anchor || !*anchor) && art_public_position(a)) resume_reader(a);
		}
		art_history_capture(a);
		art_features_controls(a);
		if (a->dpy) nav_redraw(a);
		return;
	}
	art_fetch_start(a, pack, id, anchor, -1, 0, ST.is_v2 ? ST.view : set_get_int("MainWindowView", ART_VIEW_DEFAULT));
}

/* ---------------------------------------------------------------- scrolling */

int art_current_heading(e26_app *a)
{
	int e = lay_current_entry(&ST.L, a->scroll), i, k = -1;
	if (e < 0) return -1;
	for (i = 0; i <= e; i++) if (ST.L.en[i].kind == LE_HEADING) k++;
	return k;
}

/* the thumb's top (frame coordinates) and height for a scroll bar control; 0 when the article fits its pane */
int art_scroll_thumb_at(e26_app *a, const scr_control *c, int y, int *ty, int *th)
{
	int range = a->doc_h - a->view_h, h = 14;
	xasset *t = c->strip[0] ? xa_get(a, c->strip) : NULL;
	if (!a->have_art || range <= 0) return 0;
	if (t) h = t->h;
	/* u14 S16: in the native reader the thumb is as long as the view is of the article (32 px at least) */
	if (art_scroll_native(a)) {
		h = (int)((long long)c->h * a->view_h / (a->doc_h > 0 ? a->doc_h : 1));
		if (h < 32) h = 32;
		if (h > c->h) h = c->h;
	}
	*th = h;
	*ty = y + (int)((long)(c->h - h) * a->scroll / range);
	return 1;
}

int art_scroll_native(const e26_app *a) { return ST.is_v2 && present_article_viewport(a); }

/* u14 S16: the native reader's track: a tick (6 x 1 px, the theme's rule colour) at each level-1 heading, then the
 * proportional thumb as a Motif bevel (white top-left, the CDE shadow #7A7A7A bottom-right, three grip lines) */
void art_scroll_track_draw(e26_app *a, gfx_ctx *g, const scr_control *c, int x, int y, int ty, int th)
{
	long rule = e26_theme_rule(a->appearance_theme), face = ui_rgb(a, UI_STONE), shadow = 0x7A7A7AL;
	int i, w = c->w - 4, tx = x + 2, mid = ty + th / 2;
	for (i = 0; i < ST.L.nen && a->doc_h > 0; i++)
		if (ST.L.en[i].kind == LE_HEADING && ST.L.en[i].level == 1)
			gfx_fill(g, x + (c->w - 6) / 2, y + (int)((long long)(c->h - 1) * ST.L.en[i].y / a->doc_h), 6, 1, rule);
	gfx_fill(g, tx, ty, w, th, face);
	gfx_frame2(g, tx, ty, w, th, C_WHITE, shadow);
	for (i = -3; i <= 3 && th >= 16; i += 3) {
		gfx_fill(g, tx + 3, mid + i, w - 6, 1, shadow);
		gfx_fill(g, tx + 3, mid + i + 1, w - 6, 1, C_WHITE);
	}
}

/* the same for a control of the front screen (a->cur); drawing code passes the control's own drawn y instead, because
 * a scroll bar on a screen UNDER an overlay (the article behind the Pinpointer) must not take the overlay's origin */
int art_scroll_thumb(e26_app *a, const scr_control *c, int *ty, int *th)
{
	return art_scroll_thumb_at(a, c, c->y + (c->absolute ? 0 : a->cur->oy), ty, th);
}

/* the arrows of the scroll column show only when the article does not fit, and the section buttons are greyed then
 * (1995 capture 05: a short article shows the section buttons alone, both in their disabled frame, entry-13 glyphs;
 * AUTHENTICITY 7.11) */
static void arrows_for(e26_app *a)
{
	scr_screen *s = scr_find(&a->set, "article");
	scr_control *up, *dn, *sp, *sn;
	int fits;
	if (!s) return;
	fits = !a->have_art || a->doc_h <= a->view_h;
	if ((up = scr_control_find(s, "sb-up")) != NULL) up->hidden = fits;
	if ((dn = scr_control_find(s, "sb-down")) != NULL) dn->hidden = fits;
	if ((sp = scr_control_find(s, "sec-prev")) != NULL) sp->disabled = fits;
	if ((sn = scr_control_find(s, "sec-next")) != NULL) sn->disabled = fits;
}

void art_place_scrollbar(e26_app *a)
{
	scr_screen *s = a->cur;
	scr_control *c = s ? find_kind(s, K_ARTICLE) : NULL;
	if (!c && s && s->over[0]) { s = scr_find(&a->set, "article"); c = s ? find_kind(s, K_ARTICLE) : NULL; }
	if (c && a->have_art) {
		int height = c->h - 2 * art_banner_inset(a, c);
		int max;
		if (height < 1) height = 1;
		max = a->doc_h > height ? a->doc_h : height;
		a->view_h = height;
		if (a->scroll > max - height) a->scroll = max - height;
		if (a->scroll < 0) a->scroll = 0;
		arrows_for(a);
		art_features_controls(a);
		if (!a->sb) return; /* no widgets (the tests run the screens headless) */
		if (find_kind(s, K_SCROLL)) { XtUnmanageChild(a->sb); return; } /* the screen draws the Library's own */
		{
			/* the Motif bar is a child window over the frame: in a maximised window it stands where the frame's
			 * enlarged column is, k times its size (its values stay in document pixels) */
			int wx, wy;
			scale_to_window(&a->sc, c->x + a->cur->ox + c->w - 16, c->y + a->cur->oy, &wx, &wy);
			XtVaSetValues(a->sb, XmNx, wx, XmNy, wy, XmNwidth, 16 * a->sc.k, XmNheight, c->h * a->sc.k,
			              XmNminimum, 0, XmNmaximum, max, XmNsliderSize, c->h, XmNvalue, a->scroll,
			              XmNincrement, 19, XmNpageIncrement, c->h - 38, NULL);
		}
		XtManageChild(a->sb);
	} else if (a->sb) XtUnmanageChild(a->sb);
}

void art_scroll_by(e26_app *a, int dy)
{
	clipped_anchor.block = -1;
	ST.resumed_until = 0;   /* u14 S12: the reader scrolled: the resume note goes */
	a->scroll += dy;
	art_place_scrollbar(a);
}

void art_scroll_to(e26_app *a, int y)
{
	clipped_anchor.block = -1;
	a->scroll = y;
	art_place_scrollbar(a);
}

void art_scroll_cb(Widget w, XtPointer cd, XtPointer call)
{
	XmScrollBarCallbackStruct *s = (XmScrollBarCallbackStruct *)call;
	e26_app *a = (e26_app *)cd;
	(void)w;
	clipped_anchor.block = -1;
	art_resume_done(a);
	a->scroll = s->value;
	nav_redraw(a);
}

/* ---------------------------------------------------------------- jumps, hits, the selection */

int art_link_at(e26_app *a, const scr_control *c, int cx, int cy, int x, int y)
{
	lay_hit h;
	if (!a->have_art) return -1;
	if (x < cx || x >= cx + c->w - 16 || y < cy + art_banner_inset(a, c) || y >= cy + c->h - art_banner_inset(a, c)) return -1;
	lay_hit_test(&ST.L, &ST.M, x - (cx + art_text_origin(c)), y - cy - art_banner_inset(a, c) + a->scroll, 1, &h);
	return h.link;
}

char *art_word_at(e26_app *a, const scr_control *c, int cx, int cy, int x, int y)
{
	lay_hit h;
	int s, e;
	if (!a->have_art) return NULL;
	if (x < cx || x >= cx + c->w - 16 || y < cy + art_banner_inset(a, c) || y >= cy + c->h - art_banner_inset(a, c)) return NULL;
	lay_hit_test(&ST.L, &ST.M, x - (cx + art_text_origin(c)), y - cy - art_banner_inset(a, c) + a->scroll, 1, &h);
	if (h.pos < 0 || !lay_word_at(&ST.L, h.pos, &s, &e)) return NULL;
	return lay_text_range(&ST.L, s, e);
}

char *art_selection(e26_app *a)
{
	if (!a->have_art || ST.sel_a < 0 || ST.sel_a == ST.sel_b) return NULL;
	return lay_text_range(&ST.L, ST.sel_a, ST.sel_b);
}

static void scroll_to_anchor(e26_app *a, int kind, const char *name)
{
	int k = lay_anchor_find(&ST.L, kind, name);
	if (k >= 0) {
		art_history_capture(a);
		if (ST.full_id) (void)ar_history_push(&ST.history, ST.full_id, ST.pack, ST.L.an[k].y, ST.view);
		art_scroll_to(a, ST.L.an[k].y);
	}
}

void art_set_hits(e26_app *a, const int *pairs, int npairs)
{
	free(ST.hits);
	ST.hits = NULL;
	ST.nhits = 0;
	if (npairs > 0 && (ST.hits = (int *)malloc((size_t)npairs * 2 * sizeof(int))) != NULL) {
		int i;
		memcpy(ST.hits, pairs, (size_t)npairs * 2 * sizeof(int));
		ST.nhits = npairs;
		i = lay_item_at_pos(&ST.L, pairs[0]);
		if (i >= 0) {
			const lay_item *it = &ST.L.it[i];
			int top = it->line >= 0 ? ST.L.ln[it->line].top : it->y;
			art_scroll_to(a, top - 2 * (a->line > 0 ? a->line : E26_LINE));
		}
	}
}

/* every occurrence of a word (ASCII letters compared without case) as hits: the scenario hook E26_HITS */
static void hits_for_word(e26_app *a, const char *w)
{
	size_t n = strlen(w), i;
	int *p = NULL, np = 0, cap = 0;
	const char *t = ST.L.pt.s;
	if (!n || !t) return;
	for (i = 0; i + n <= ST.L.pt.n; i++) {
		size_t k;
		for (k = 0; k < n; k++) {
			char x = t[i + k], y = w[k];
			if (x >= 'A' && x <= 'Z') x = (char)(x - 'A' + 'a');
			if (y >= 'A' && y <= 'Z') y = (char)(y - 'A' + 'a');
			if (x != y) break;
		}
		if (k < n) continue;
		if (np == cap) {
			int *q;
			cap = cap ? cap * 2 : 32;
			if (!(q = (int *)realloc(p, (size_t)cap * 2 * sizeof(int)))) break;
			p = q;
		}
		p[2 * np] = (int)i;
		p[2 * np + 1] = (int)(i + n);
		np++;
		i += n - 1;
	}
	art_set_hits(a, p, np);
	free(p);
}

/* ---------------------------------------------------------------- u14 S7: Find in article
 * Ctrl+F opens a find line in the section bar ("Find: ospf_   3 of 17"); typing searches the article's plain text
 * (ASCII letters without case) and marks every occurrence as the search hits (reverse, as the period design marks them);
 * Enter or F3 goes to the next, Shift+Enter or Shift+F3 to the previous, each scrolled to a third of the view; Escape
 * closes the line and clears the marks. The arrows and page keys keep scrolling while it is open. */
static void find_show(e26_app *a)
{
	int i;
	if (finder.cur < 0 || finder.cur >= ST.nhits) return;
	i = lay_item_at_pos(&ST.L, ST.hits[2 * finder.cur]);
	if (i >= 0) {
		const lay_item *it = &ST.L.it[i];
		art_scroll_to(a, (it->line >= 0 ? ST.L.ln[it->line].top : it->y) - a->view_h / 3);
	}
}

/* every occurrence of the typed text, the current one the first at or below the top of the view */
static void find_search(e26_app *a)
{
	char w[200];
	int keep = a->scroll, i;
	finder.cur = -1;
	sel_cp1252_to_utf8(finder.text, strlen(finder.text), w, sizeof w);
	if (!w[0]) { art_set_hits(a, NULL, 0); return; }
	hits_for_word(a, w);
	art_scroll_to(a, keep);
	if (!ST.nhits) return;
	finder.cur = 0;
	for (i = 0; i < ST.nhits; i++) {
		int k = lay_item_at_pos(&ST.L, ST.hits[2 * i]);
		if (k >= 0 && ST.L.it[k].y >= keep) { finder.cur = i; break; }
	}
	find_show(a);
}

static void find_step(e26_app *a, int dir)
{
	if (finder.text[0] && finder.cur < 0) find_search(a);
	if (!ST.nhits || finder.cur < 0) return;
	finder.cur = (finder.cur + dir + ST.nhits) % ST.nhits;
	find_show(a);
}

int art_find_key(e26_app *a, XKeyEvent *ev, KeySym ks)
{
	char typed[8];
	int n, shift = (ev->state & ShiftMask) != 0;
	size_t len = strlen(finder.text);
	if (!finder.open || !a->have_art) return 0;
	if (ks == XK_Escape) { finder.open = 0; finder.cur = -1; art_set_hits(a, NULL, 0); nav_redraw(a); return 1; }
	if (ev->state & (ControlMask | Mod1Mask)) return 0;
	if (ks == XK_Return || ks == XK_KP_Enter || ks == XK_F3) { find_step(a, shift ? -1 : 1); nav_redraw(a); return 1; }
	if (ks == XK_BackSpace) {
		if (len) { finder.text[len - 1] = '\0'; find_search(a); }
		nav_redraw(a);
		return 1;
	}
	n = XLookupString(ev, typed, sizeof typed - 1, NULL, NULL);
	if (n == 1 && (unsigned char)typed[0] >= 0x20 && typed[0] != 0x7F) {
		if (len + 1 < sizeof finder.text) { finder.text[len] = typed[0]; finder.text[len + 1] = '\0'; find_search(a); }
		nav_redraw(a);
		return 1;
	}
	return 0;   /* the scroll keys and the rest: the reader's */
}

const char *art_find_line(char *buf, size_t n)
{
	if (!finder.open) return NULL;
	if (!finder.text[0]) snprintf(buf, n, "Find: _    (Enter: next, Shift+Enter: previous, Esc: close)");
	else if (!ST.nhits) snprintf(buf, n, "Find: %s_    not found", finder.text);
	else snprintf(buf, n, "Find: %s_    %d of %d", finder.text, finder.cur + 1, ST.nhits);
	return buf;
}

static int act_find(e26_app *a, const e26_actx *x)
{
	(void)x;
	if (!a->have_art) return 0;
	finder.open = 1;
	if (finder.text[0]) find_search(a);
	return 1;
}

static int act_find_next(e26_app *a, const e26_actx *x) { (void)x; if (!finder.text[0]) return act_find(a, x); find_step(a, 1); return 1; }
static int act_find_prev(e26_app *a, const e26_actx *x) { (void)x; if (!finder.text[0]) return act_find(a, x); find_step(a, -1); return 1; }

static void open_target_article(e26_app *a, const char *id, const char *anchor)
{
	char path[1200], rid[80];
	if (ST.pack < 0 && article_path(a, id, path, sizeof path, rid, sizeof rid)) {
		char *saved_anchor = strdup(anchor ? anchor : "");
		int changed = strcmp(rid, a->art_id);
		if (!saved_anchor) return;
		if (changed) art_open(a, id);
		if (*saved_anchor) {
			int k = lay_anchor_find(&ST.L, LA_HEADING, saved_anchor);
			if (changed && k >= 0) art_scroll_to(a, ST.L.an[k].y);
			else if (!changed) scroll_to_anchor(a, LA_HEADING, saved_anchor);
		}
		free(saved_anchor);
		if (a->dpy) nav_redraw(a);
	} else art_open_pack(a, -1, id, anchor);
}

void art_follow(e26_app *a, int link)
{
	const lay_link *lk;
	const char *path;
	FILE *f;
	if (!a->have_art || link < 0 || link >= ST.L.nlk) return;
	if ((path = public_trace_path(a)) && (f = fopen(path, "a"))) {
		fprintf(f, "K\t%d\n", link);
		fclose(f);
	}
	lk = &ST.L.lk[link];
	if (lk->kind == LK_FOOTNOTE) { scroll_to_anchor(a, LA_FOOTNOTE, lk->name); nav_redraw(a); return; }
	if (lk->kind == LK_GLOSSARY) {
		art_dictionary(a, lk->name);
		return;
	}
	switch (lk->target->kind) {
	case T_ANCHOR: scroll_to_anchor(a, LA_HEADING, lk->target->anchor); nav_redraw(a); break;
	case T_MEDIA: {
		const char *id = lk->target->id;
		int i;
		for (i = 0; i < ST.d2.nb; i++) {
			const a2_block *b = &ST.d2.b[i];
			if (b->kind != A2_MATHS || strcmp(b->media, id)) continue;
			/* The target string belongs to the article and survives relayout. */
			if (ST.open_formula != i + 1) art_formula_toggle(a, i);
			scroll_to_anchor(a, LA_MEDIA, id);
			break;
		}
		if (i == ST.d2.nb) art_media_pick(a, id, 0);
		nav_redraw(a);
		break;
	}
	case T_ARTICLE: open_target_article(a, lk->target->id, lk->target->anchor); break;
	case T_RFC: {
		char id[32], anchor[48];
		snprintf(id, sizeof id, "rfc%ld", lk->target->number);
		anchor[0] = 0;
		if (lk->target->section) {
			/* ARTICLE-FORMAT-2: section "3.1" is anchor "s3.1", Appendix A "sa" */
			size_t i;
			snprintf(anchor, sizeof anchor, "s%s", lk->target->section);
			for (i = 0; anchor[i]; i++) if (anchor[i] >= 'A' && anchor[i] <= 'Z') anchor[i] = (char)(anchor[i] - 'A' + 'a');
		}
		open_target_article(a, id, anchor);
		break;
	}
	case T_URL: (void)ar_clipboard(a, lk->target->url, NULL); break;
	default: break;
	}
}

/* ---------------------------------------------------------------- actions, sources, events */

static int act_open(e26_app *a, const e26_actx *x)
{
	const char *id = x->arg && x->arg[0] ? x->arg : NULL;
	if (!id && a->have_tax && a->cur_sub >= 0 && a->tax.cat[a->cur_cat].sub[a->cur_sub].article[0]) id = a->tax.cat[a->cur_cat].sub[a->cur_sub].article;
	art_open(a, id ? id : "rfc2328-s1");
	return 0;
}

static int act_scroll_up(e26_app *a, const e26_actx *x) { (void)x; art_scroll_by(a, -a->line); return 1; }
static int act_scroll_down(e26_app *a, const e26_actx *x) { (void)x; art_scroll_by(a, a->line); return 1; }

/* the two double-arrow buttons beside the section bar: the previous or next section's start */
static int section(e26_app *a, int dir)
{
	int cur = art_current_heading(a), to = cur + dir, e;
	if (ST.L.nheads && to >= 0 && to < ST.L.nheads && (e = lay_heading_entry(&ST.L, to)) >= 0) { a->scroll = ST.L.en[e].y; art_place_scrollbar(a); }
	else if (to < 0) { a->scroll = 0; art_place_scrollbar(a); }
	return 1;
}
static int act_section_prev(e26_app *a, const e26_actx *x) { (void)x; return section(a, -1); }
static int act_section_next(e26_app *a, const e26_actx *x) { (void)x; return section(a, 1); }

/* a menu item of the Size or Text menu: "[*] " before the label of the choice in effect */
static scr_menu_item *menu_item(scr_menu *m, int action, const char *arg, int on, const char *label)
{
	scr_menu_item *item;
	if (m->nit >= SCR_MAX_MENU_ITEMS) return NULL;
	item = &m->it[m->nit++];
	memset(item, 0, sizeof *item);
	item->kind = action < 0 ? MI_RULE : MI_TEXT;
	if (action < 0) return item;
	item->action = action;
	snprintf(item->arg, sizeof item->arg, "%s", arg);
	snprintf(item->label, sizeof item->label, "%s%s", on ? "[*] " : "    ", label);
	return item;
}

/* the menu as wide as its widest label (the record's width at least) */
static void menu_fit(const e26_app *a, scr_menu *m, int least)
{
	int i, w = least;
	for (i = 0; a->font[F_UI] && i < m->nit; i++)
		if (m->it[i].kind == MI_TEXT) {
			int lw = XTextWidth(a->font[F_UI], m->it[i].label, (int)strlen(m->it[i].label)) + 2 * 10 + 8;
			if (lw > w) w = lw;
		}
	m->width = w;
}

/* u14 M1: the Size menu, the Text size ladder: "[*] 150%   20 px" (the reader face's body at that step, snapped to
 * its native strike), then "Panel default (150%)" (Ctrl+0) */
void art_zoom_items(const e26_app *a, scr_menu *m)
{
	const int action = reg_find(REG_ACTION, "article.zoom");
	char arg[16], label[64];
	int i;

	m->nit = 0;
	for (i = 0; i < E26_ZOOM_STEPS; i++) {
		int zoom = fonts_zoom_ladder[i];
		snprintf(arg, sizeof arg, "%d", zoom);
		snprintf(label, sizeof label, "%d%%   %d px", zoom, fonts_zoom_px((e26_app *)a, zoom));
		menu_item(m, action, arg, a->reader_zoom == zoom, label);
	}
	menu_item(m, -1, "", 0, "");
	snprintf(label, sizeof label, "Panel default (%d%%)", fonts_auto_zoom(a));
	menu_item(m, action, "auto", a->reader_zoom_auto, label);
	menu_fit(a, m, 112);
}

/* u14 M3, S6: the Text menu: the reader faces, a rule, Line spacing, a rule, Column, a rule, Paper, a rule, and the
 * outline's exhibits toggle */
void art_font_items(const e26_app *a, scr_menu *m)
{
	const int action = reg_find(REG_ACTION, "article.font"), reading = reg_find(REG_ACTION, "article.reading");
	static const char *const spacing[] = { "Lines: Compact", "Lines: Normal", "Lines: Relaxed" };
	static const char *const column[] = { "Column: Narrow", "Column: Normal", "Column: Wide" };
	static const char *const paper[] = { "Paper: White", "Paper: Warm" };
	const char *theme_mark = a->reader_font_choice < 0 ? "[*] " : "    ";
	scr_menu_item *theme = &m->it[0];
	char label[sizeof theme->label], arg[40];
	size_t i;

	memset(theme, 0, sizeof *theme);
	theme->kind = MI_TEXT;
	theme->action = action;
	snprintf(theme->arg, sizeof theme->arg, "theme");
	snprintf(theme->label, sizeof theme->label, "%sUse Theme Font", theme_mark);
	if (a->font[F_UI]) {
		const char *name = fonts_reader_theme_name(a->appearance_theme);
		if (a->reader_font_choice < 0 && a->reader_face >= 0 && (size_t)a->reader_face < fonts_reader_face_count)
			name = fonts_reader_faces[a->reader_face].label;
		if (!strcmp(name, fonts_reader_faces[0].label)) name = "Classic";
		const char *detail = strstr(name, " (");
		int name_len = (int)(detail ? (size_t)(detail - name) : strlen(name));
		int len = snprintf(label, sizeof label, "%sUse Theme Font (%.*s)", theme_mark, name_len, name);
		if (len > 0 && (size_t)len < sizeof label)
			snprintf(theme->label, sizeof theme->label, "%s", label);
	}
	m->nit = 1;
	for (i = 0; i < fonts_reader_face_count && m->nit < SCR_MAX_MENU_ITEMS; i++) {
		scr_menu_item *item = &m->it[m->nit++];
		memset(item, 0, sizeof *item);
		item->kind = MI_TEXT;
		item->action = action;
		snprintf(item->arg, sizeof item->arg, "%s", fonts_reader_faces[i].id);
		snprintf(item->label, sizeof item->label, "%s%s",
		    a->reader_font_choice >= 0 && a->reader_face == (int)i ? "[*] " : "    ",
		    fonts_reader_faces[i].label);
	}
	menu_item(m, -1, "", 0, "");
	for (i = 0; i < 3; i++) {
		snprintf(arg, sizeof arg, "spacing=%s", fonts_spacing_names[i]);
		menu_item(m, reading, arg, a->reader_spacing == (int)i, spacing[i]);
	}
	menu_item(m, -1, "", 0, "");
	for (i = 0; i < 3; i++) {
		snprintf(arg, sizeof arg, "column=%s", fonts_column_names[i]);
		menu_item(m, reading, arg, a->reader_column == (int)i, column[i]);
	}
	menu_item(m, -1, "", 0, "");
	for (i = 0; i < 2; i++) {
		snprintf(arg, sizeof arg, "paper=%s", fonts_paper_names[i]);
		menu_item(m, reading, arg, a->reader_paper == (int)i, paper[i]);
	}
	menu_item(m, -1, "", 0, "");
	menu_item(m, reading, a->outline_exhibits ? "exhibits=0" : "exhibits=1", a->outline_exhibits,
	    "Figures, tables and code in outline");
	menu_fit(a, m, 178);
}

static int act_reader_font(e26_app *a, const e26_actx *x)
{
	const char *const keys[] = { "ReaderFont" };
	const char *values[1];
	int previous = a->reader_font_choice, choice = -1;
	size_t i;

	if (!ST.is_v2 || !x->arg) return 0;
	if (strcmp(x->arg, "theme")) {
		for (i = 0; i < fonts_reader_face_count; i++)
			if (!strcmp(x->arg, fonts_reader_faces[i].id)) break;
		if (i == fonts_reader_face_count) return 0;
		choice = (int)i;
	}
	if (choice == previous && (choice < 0 || a->reader_face == choice)) return 0;
	if (fonts_reader_select(a, choice)) {
		nav_notice_text(a, "Article font", "That font is unavailable at the current size on this display.");
		return 0;
	}
	values[0] = choice < 0 ? "theme" : fonts_reader_faces[choice].id;
	if (set_put_save(keys, values, 1)) {
		a->reader_font_choice = previous;
		fonts_type_size(a, a->reader_size);
		art_relayout(a);
		nav_notice_text(a, "Article font", "The font preference could not be saved.");
		return 0;
	}
	art_relayout(a);
	return 1;
}

/* u14 M1: the Text size in effect, saved as ReaderZoom ("0" = the panel's default); the faces reloaded and the
 * article laid out again in place. Nothing changes when it cannot be saved. */
static int zoom_to(e26_app *a, int zoom, int automatic)
{
	const char *const keys[] = { "ReaderZoom" };
	char value[16];
	const char *values[] = { value };
	if (zoom == a->reader_zoom && automatic == a->reader_zoom_auto) return 0;
	snprintf(value, sizeof value, "%d", automatic ? 0 : zoom);
	if (set_put_save(keys, values, 1)) {
		nav_notice_text(a, "Text size", "The text size could not be saved.");
		return 0;
	}
	a->reader_zoom = zoom;
	a->reader_zoom_auto = automatic;
	fonts_type_size(a, a->reader_size);
	art_relayout(a);
	return 1;
}

/* article.zoom: in, out (a ladder step), reset or auto (the panel's default, Ctrl+0), or a ladder value */
static int act_zoom(e26_app *a, const e26_actx *x)
{
	const char *arg = x->arg;
	int step = fonts_zoom_step(a->reader_zoom), zoom;
	char *end;

	if (!ST.is_v2 || !arg) return 0;
	if (!strcmp(arg, "reset") || !strcmp(arg, "auto")) return zoom_to(a, fonts_auto_zoom(a), 1);
	if (!strcmp(arg, "in")) return step + 1 < E26_ZOOM_STEPS ? zoom_to(a, fonts_zoom_ladder[step + 1], 0) : 0;
	if (!strcmp(arg, "out")) return step > 0 ? zoom_to(a, fonts_zoom_ladder[step - 1], 0) : 0;
	zoom = (int)strtol(arg, &end, 10);
	if (*end || zoom != fonts_zoom_ladder[fonts_zoom_step(zoom)]) return 0;
	return zoom_to(a, zoom, 0);
}

/* u14 M3, S6: article.reading <key>=<value>: spacing (ReaderSpacing), column (ReaderColumn), paper (ReaderPaper) or
 * exhibits (OutlineExhibits 0/1), saved, then the article laid out again in place (paper only redraws) */
static int act_reading(e26_app *a, const e26_actx *x)
{
	static const struct { const char *name, *key; const char *const *values; int n; } kinds[] = {
		{ "spacing", "ReaderSpacing", fonts_spacing_names, 3 },
		{ "column", "ReaderColumn", fonts_column_names, 3 },
		{ "paper", "ReaderPaper", fonts_paper_names, 2 },
	};
	static const char *const exhibits[] = { "0", "1" };
	const char *eq = x->arg ? strchr(x->arg, '=') : NULL, *keys[1], *values[1];
	int *field = NULL, value = -1, i;
	size_t len;

	if (!eq) return 0;
	len = (size_t)(eq - x->arg);
	for (i = 0; i < (int)(sizeof kinds / sizeof kinds[0]); i++) {
		int k;
		if (strlen(kinds[i].name) != len || strncmp(x->arg, kinds[i].name, len)) continue;
		for (k = 0; k < kinds[i].n; k++)
			if (!strcmp(eq + 1, kinds[i].values[k])) value = k;
		if (value < 0) return 0;
		field = i == 0 ? &a->reader_spacing : i == 1 ? &a->reader_column : &a->reader_paper;
		keys[0] = kinds[i].key;
		values[0] = kinds[i].values[value];
	}
	if (!field && len == 8 && !strncmp(x->arg, "exhibits", 8) && (!strcmp(eq + 1, "0") || !strcmp(eq + 1, "1"))) {
		value = eq[1] == '1';
		field = &a->outline_exhibits;
		keys[0] = "OutlineExhibits";
		values[0] = exhibits[value];
	}
	if (!field) return 0;
	if (*field == value) return 0;
	if (set_put_save(keys, values, 1)) {
		nav_notice_text(a, "Text", "The reading preference could not be saved.");
		return 0;
	}
	*field = value;
	if (field == &a->reader_paper) return 1;
	if (field == &a->reader_spacing) fonts_type_size(a, a->reader_size);
	art_relayout(a);
	return 1;
}

#ifdef E26_FONTS_TYPESIZE
/* the article's text menu (12075): Small, Medium, Large (the period design's TextStyle), laid out again in place. u14 M1:
 * they are Text size steps (100/125/150%, or 150/175/200% on a >= 140-dpi panel), saved as ReaderZoom too; TextStyle
 * still sizes the 640x480 letterbox. */
static int textsize(e26_app *a, int ts)
{
	static const char *const sizes[] = { "Small", "Medium", "Large" };
	const int zoom = fonts_zoom_ladder[(a->panel_dpi >= 140 ? 2 : 0) + ts];
	char value[16];
	const char *const keys[] = { "TextStyle", "ReaderZoom" };
	const char *values[] = { sizes[ts], value };

	snprintf(value, sizeof value, "%d", zoom);
	if (set_put_save(keys, values, 2)) {
		nav_notice_text(a, "Text size", "The text size could not be saved.");
		return 0;
	}
	a->reader_zoom = zoom;
	a->reader_zoom_auto = 0;
	fonts_type_size(a, ts);
	art_relayout(a);
	return 1;
}
static int act_text_small(e26_app *a, const e26_actx *x) { (void)x; return textsize(a, 0); }
static int act_text_medium(e26_app *a, const e26_actx *x) { (void)x; return textsize(a, 1); }
static int act_text_large(e26_app *a, const e26_actx *x) { (void)x; return textsize(a, 2); }
#else
static int act_text_pending(e26_app *a, const e26_actx *x)
{
	(void)x;
	nav_notice_text(a, "Text sizes", "Live text-size changes need the LAYOUT font patch. The -typesize option works when starting Deep32 Encyclopedia.");
	return 0;
}
#endif

static int act_openat(e26_app *a, const e26_actx *x)
{
	openat_target t;
	int pack;
	if (openat_parse(x->arg, &t)) { nav_notice_text(a, "Open Library", "The Library link is invalid."); return 0; }
	if (!strcmp(t.pack, "lib-reserved") && !openat_private_allowed()) {
		nav_notice_text(a, "Open Library", "Access to this private Library pack is unavailable."); return 0;
	}
	pack = ar_lib_resolve(t.pack);
	if (pack < 0) { nav_notice_text(a, "Open Library", "This Library pack is unavailable."); return 0; }
	if (!strcmp(t.pack, "lib-reserved") && ST.is_v2 && ST.pack == pack &&
	    (strcmp(ST.d2.cls, "corpus") || strcmp(ar_marking(&ST.d2), "RESTRICTED"))) {
		nav_notice_text(a, "Open Library", "This private Library article has no usable handling marking."); return 0;
	}
	/* A named URI never searches other packs or the local legacy article directory. */
	art_open_pack(a, pack, t.article, t.anchor);
	return 0;
}

static int act_title_open(e26_app *a, const e26_actx *x)
{
	const char *generation;
	int pack, private_pack, restricted;
	if (x->ctl != E26_CTL_FINDER_TITLE || !x->catalogue_id || !*x->catalogue_id ||
	    (pack = ar_lib_resolve(x->catalogue_id)) < 0) {
		nav_notice_text(a, "Open Library", "This Library pack is unavailable."); return 0;
	}
	generation = ar_lib_generation(pack);
	if (!x->generation || !*x->generation || (*generation && strcmp(generation, x->generation))) {
		nav_notice_text(a, "Open Library", "The Library changed; search again."); return 0;
	}
	private_pack = !strcmp(x->catalogue_id, "lib-reserved");
	/* As in the reader, absent handling and PUBLIC are unmarked. */
	restricted = x->marking && *x->marking && strcasecmp(x->marking, "PUBLIC");
	if ((!private_pack && restricted) || (private_pack && !openat_private_allowed())) {
		nav_notice_text(a, "Open Library", "Access to this private Library pack is unavailable."); return 0;
	}
	if (private_pack && (!x->marking || strcmp(x->marking, "RESTRICTED") ||
	    (ST.is_v2 && ST.pack == pack &&
	     (strcmp(ST.d2.cls, "corpus") || strcmp(ar_marking(&ST.d2), "RESTRICTED"))))) {
		nav_notice_text(a, "Open Library", "This private Library article has no usable handling marking."); return 0;
	}
	art_open_pack(a, pack, x->arg, NULL);
	return 0;
}

static const char *src_title(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	(void)c;
	if (!a->have_art) return NULL;
	snprintf(buf, n, "%s", a->art.title);
	return buf;
}

static const char *src_subtitle(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	(void)c;
	if (!a->have_art) return NULL;
	snprintf(buf, n, "%s", a->art.subtitle);
	return buf;
}

static const char *src_section(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	int k, bi, hk = -1;
	(void)c;
	if (!a->have_art) return NULL;
	if (art_find_line(buf, n)) return buf;   /* u14 S7: the find line takes the section bar while it is open */
	if (ST.is_v2) {
		int e = lay_current_entry(&ST.L, a->scroll);
		if (e < 0) return NULL;
		snprintf(buf, n, "%s", ST.L.en[e].text);
		return buf;
	}
	k = art_current_heading(a);
	for (bi = 0; k >= 0 && bi < a->art.nb; bi++)
		if (a->art.b[bi].kind == B_HEADING && ++hk == k) {
			snprintf(buf, n, "%s  %s", a->art.b[bi].number, a->art.b[bi].text);
			return buf;
		}
	return NULL;
}

/* the article screen with no article yet shows the subcategory's, else RFC 2328's first section; every screen
 * entered places (or hides) the scroll bar */
static void on_enter(e26_app *a, const e26_event *ev, void *ud)
{
	(void)ud;
	if (ev->screen && !strncmp(ev->screen->name, "article-", 8)) {
		scr_control *cap = scr_control_find(ev->screen, "window-cap");
		const char *title = scr_screen_prop(ev->screen, "window-title");
		if (cap && title) snprintf(cap->label, sizeof cap->label, "%s", title);
	}
	if (!ev->switched && ev->screen && !strcmp(ev->screen->name, "article") && !a->have_art && !ST.busy) {
		const char *id = getenv("E26_ARTICLE"), *hits = getenv("E26_HITS");
		if (!id || !id[0])
			id = a->have_tax && a->cur_sub >= 0 && a->tax.cat[a->cur_cat].sub[a->cur_sub].article[0] ? a->tax.cat[a->cur_cat].sub[a->cur_sub].article : "rfc2328-s1";
		if (!art_load(a, id) && hits && hits[0]) hits_for_word(a, hits);
	}
	if (viewport_geometry_changed(a, scr_find(&a->set, "article"))) return;
	if (ST.is_v2) art_view_apply(a);
	art_features_controls(a);
	art_place_scrollbar(a);
}

static void on_resize(e26_app *a, const e26_event *ev, void *ud)
{
	(void)ev;
	(void)ud;
	if (!viewport_geometry_changed(a, scr_find(&a->set, "article"))) art_place_scrollbar(a);
}

static void on_open(e26_app *a, const e26_event *ev, void *ud)
{
	(void)ud;
	if (!ST.is_v2 || art_public_position(a)) state_note_article(ev->name, a->scroll);
}

/* hold-to-repeat on the scroll column's arrows (the help: they repeat while held; AUTHENTICITY 7.17): a press on
 * sb-up or sb-down starts the timer; the release runs the button's own step as before */
static void repeat_tick(e26_app *a, void *ud)
{
	(void)ud;
	if (ST.repeat_ctl < 0 || a->cur != ST.repeat_s || a->pressed != ST.repeat_ctl) { ST.repeat_ctl = -1; return; }
	art_scroll_by(a, ST.repeat_dir * a->line);
	nav_redraw(a);
	timer_after(a, 60, repeat_tick, NULL);
}

static void on_press(e26_app *a, const e26_event *ev, void *ud)
{
	const scr_control *c;
	(void)ud;
	if (!ev->screen || ev->ctl < 0 || strcmp(ev->screen->name, "article") || !a->have_art) return;
	c = &ev->screen->c[ev->ctl];
	if (strcmp(c->id, "sb-up") && strcmp(c->id, "sb-down")) return;
	ST.repeat_ctl = ev->ctl;
	ST.repeat_s = ev->screen;
	ST.repeat_dir = strcmp(c->id, "sb-up") ? 1 : -1;
	timer_after(a, 350, repeat_tick, NULL);
}

static const e26_setting follow_key = { "OutlineFollowsReading", NULL, SET_BOOL, "1" };

static int article_cursor(e26_app *a, scr_screen *s, int i, int x, int y)
{
	const scr_control *c = &s->c[i];
	int cx = c->x + (c->absolute ? 0 : s->ox), cy = c->y + (c->absolute ? 0 : s->oy), k;
	lay_hit hit;
	if (!a->have_art) return CUR_ARROW;
	if (y < cy + art_banner_inset(a, c) || y >= cy + c->h - art_banner_inset(a, c)) return CUR_ARROW;
	if (art_link_at(a, c, cx, cy, x, y) >= 0) return CUR_HAND;
	lay_hit_test(&ST.L, &ST.M, x - cx - art_text_origin(c), y - cy - art_banner_inset(a, c) + a->scroll, 1, &hit);
	if (hit.pos >= 0 && ar_has_pages(&ST.L) && ar_page_reference(ST.L.pt.s, ST.L.pt.n, (size_t)hit.pos)) return CUR_HAND;
	for (k = 0; k < ST.L.nit; k++) {
		const lay_item *it = &ST.L.it[k];
		int dx = x - cx - art_text_origin(c), dy = y - cy - art_banner_inset(a, c) + a->scroll;
		if (dx >= it->x && dx < it->x + it->w && dy >= it->y && dy < it->y + it->h) {
			if (it->kind == LI_MEDIA) return CUR_MAGNIFY;
			if (it->kind == LI_FORMULA) return CUR_HAND;
		}
	}
	return CUR_ARROW;
}

void e26_mod_room_article(void);
void e26_mod_room_article(void)
{
	static const e26_action open = { act_open }, up = { act_scroll_up }, down = { act_scroll_down }, prev = { act_section_prev },
	                        next = { act_section_next }, openat_ = { act_openat }, title_open = { act_title_open };
	static const source_ops title = { src_title, NULL, NULL }, subtitle = { src_subtitle, NULL, NULL }, sect = { src_section, NULL, NULL };
	reg_add(REG_ACTION, "article.open-at", &openat_, "room_article", 0);
	reg_add(REG_ACTION, "title.open", &title_open, "room_article", 0);
	reg_add(REG_ACTION, "open-article", &open, "room_article", 0);
	reg_add(REG_ACTION, "scroll-up", &up, "room_article", 0);
	reg_add(REG_ACTION, "scroll-down", &down, "room_article", 0);
	reg_add(REG_ACTION, "section-prev", &prev, "room_article", 0);
	reg_add(REG_ACTION, "section-next", &next, "room_article", 0);
	{
		static const e26_action zoom = { act_zoom }, font = { act_reader_font }, reading = { act_reading };
		static const e26_action find = { act_find }, find_next = { act_find_next }, find_prev = { act_find_prev };
		reg_add(REG_ACTION, "article.find", &find, "room_article", 0);
		reg_add(REG_ACTION, "article.find-next", &find_next, "room_article", 0);
		reg_add(REG_ACTION, "article.find-prev", &find_prev, "room_article", 0);
		reg_add(REG_ACTION, "article.zoom", &zoom, "room_article", 0);
		reg_add(REG_ACTION, "article.font", &font, "room_article", 0);
		reg_add(REG_ACTION, "article.reading", &reading, "room_article", 0);
	}
#ifdef E26_FONTS_TYPESIZE
	{
		static const e26_action ts_s = { act_text_small }, ts_m = { act_text_medium }, ts_l = { act_text_large };
		reg_add(REG_ACTION, "article.text-small", &ts_s, "room_article", 0);
		reg_add(REG_ACTION, "article.text-medium", &ts_m, "room_article", 0);
		reg_add(REG_ACTION, "article.text-large", &ts_l, "room_article", 0);
	}
#else
	{
		static const e26_action ts = { act_text_pending };
		reg_add(REG_ACTION, "article.text-small", &ts, "room_article", 0);
		reg_add(REG_ACTION, "article.text-medium", &ts, "room_article", 0);
		reg_add(REG_ACTION, "article.text-large", &ts, "room_article", 0);
	}
#endif
	reg_add(REG_SOURCE, "title", &title, "room_article", 0);
	reg_add(REG_SOURCE, "subtitle", &subtitle, "room_article", 0);
	reg_add(REG_SOURCE, "section", &sect, "room_article", 0);
	reg_add(REG_SETTING, follow_key.key, &follow_key, "room_article", 0);
	ev_subscribe(EV_SCREEN_ENTER, on_enter, NULL);
	ev_subscribe(EV_RESIZE, on_resize, NULL);
	ev_subscribe(EV_ARTICLE_OPEN, on_open, NULL);
	ev_subscribe(EV_PRESS, on_press, NULL);
	art_features_register();
	cursor_probe(K_ARTICLE, article_cursor);
}
