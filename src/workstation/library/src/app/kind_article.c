/* kind_article.c: kinds article (the laid-out text: layout.h, room_article.c) and list (the Related Articles list, or a
 * quiet note). W1-LAYOUT in wave 1, then W2-ARTICLE. The outline is kind_outline.c.
 *
 * The article draws its display list over white, clipped to the text column (the scroll bar's 16 px excluded): grounds
 * and rules first, then text, fallback glyph boxes and picture boxes. Search hits and the selection show in reverse, as
 * the period design marks search hits (capture 29): the band of the line inverted, the text in the inverse of its colour.
 * Build 1's RFC sample draws exactly as build 2 did (only its text, black, in its order).
 *
 * The pointer: a press starts a selection (a drag extends it, character by character); a click that does not move on
 * a jump follows it (room_article.c art_follow). The keys: arrows, Page Up and Down, Home and End scroll. */
#include "room_article.h"
#include "appearance.h"
#include "lib_client.h"
#include "inline_cache.h"
#include <X11/Xatom.h>
#include <X11/keysym.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* u14 S6: the page, White or Warm (ReaderPaper) */
static long page_rgb(e26_app *a)
{
	return a->reader_paper == E26_PAPER_WARM ? e26_paper_rgb(a->reader_paper, E26_PAPER_GROUND) : C_WHITE;
}

/* u14 S3: the High Contrast theme draws callouts white with black frames and labels */
static int high_contrast(const e26_app *a)
{
	return a->appearance_theme < e26_theme_count && !strcmp(e26_themes[a->appearance_theme].id, "contrast");
}

/* u14 M9, S6: text, links and quiet text by the reading paper (a JumpColor of his own stays his); quiet text
 * #4A4A4A and the reading rules in the theme's rule colour, both above the contrast floor (spec section 6); S3: the
 * callouts' tints and labels (body text on them at least 16.6:1, labels at least 5.5:1) and the CDE etched edges */
static long role_rgb(e26_app *a, int c)
{
	int warm = a->reader_paper == E26_PAPER_WARM;
	switch (c) {
	case LC_NOTE: return high_contrast(a) ? 0xFFFFFF : 0xE8EEF6;
	case LC_CAUTION: return high_contrast(a) ? 0xFFFFFF : 0xF6EFD8;
	case LC_WARNING: return high_contrast(a) ? 0xFFFFFF : 0xF6E0DC;
	case LC_NOTE_LABEL: return high_contrast(a) ? 0x000000 : 0x2F4F7F;
	case LC_CAUTION_LABEL: return high_contrast(a) ? 0x000000 : 0x7A5A10;
	case LC_WARNING_LABEL: return high_contrast(a) ? 0x000000 : 0x8B1A1A;
	case LC_SHADOW: return high_contrast(a) ? 0x000000 : 0x7A7A7A;
	case LC_LIGHT: return high_contrast(a) ? 0x000000 : 0xFFFFFF;
	case LC_TEXT: return warm ? e26_paper_rgb(a->reader_paper, E26_PAPER_TEXT) : C_BLACK;
	case LC_JUMP: return warm && e26_jump_rgb() == 0x000080UL ? e26_paper_rgb(a->reader_paper, E26_PAPER_LINK) : C_JUMP;
	case LC_LISTJUMP: return C_LISTJUMP;
	case LC_QUIET: return e26_paper_rgb(a->reader_paper, E26_PAPER_QUIET);
	case LC_RULE: return e26_theme_rule(a->appearance_theme);
	case LC_BAND: return ui_rgb(a, UI_STONE);
	case LC_BAR: return ui_rgb(a, UI_CAPTION);
	case LC_BARTEXT: return ui_rgb(a, UI_STONE);
	case LC_GROUND: return ui_rgb(a, UI_PAPER);
	case LC_SHADE_YES: return ui_rgb(a, UI_PALE_GREEN);
	case LC_SHADE_NO: return ui_rgb(a, UI_PALE_RED);
	case LC_SHADE_PARTIAL: return ui_rgb(a, UI_PALE_YELLOW);
	case LC_SHADE_HIGHLIGHT: return ui_rgb(a, UI_PALE_BLUE);
	default: return C_BLACK;
	}
}

static int slot(const art_state *st, int face)
{
	return art_font_slot(lay_face(&st->M, face));
}

static int font_position(gfx_ctx *g, int font, Atom property, int fallback)
{
	unsigned long value;
	return XGetFontProperty(g->a->font[font], property, &value) ? (int)value : fallback;
}

static void styled_rules(gfx_ctx *g, const lay_item *it, int font, int x, int y, long colour)
{
	if (it->flags & LI_F_UNDERLINE) {
		int dy = font_position(g, font, XA_UNDERLINE_POSITION, 2);
		gfx_fill(g, x, y + dy, it->w, 1, colour);
	}
	if (it->flags & LI_F_STRIKE) {
		int dy = font_position(g, font, XA_STRIKEOUT_ASCENT, (gfx_ascent(g, font) + 1) / 3);
		gfx_fill(g, x, y - dy, it->w, 1, colour);
	}
}

static void glyph_box(gfx_ctx *g, const lay_item *it, int x, int y, long colour)
{
	/* The visible fallback glyph is a hollow box the height of a capital. */
	int h = it->h > 4 ? it->h * 2 / 3 : 8;
	gfx_frame2(g, x + 1, y - h + 1, it->w - 2 > 2 ? it->w - 2 : 3, h, colour, colour);
}

/* the parts of [s, e) ranges (pairs) that fall in an item, as x spans: inverts the line's band and redraws the text in
 * the inverse colour inside each span */
static void reverse_spans(e26_app *a, gfx_ctx *g, const art_state *st, const lay_item *it, int x0, int y0, const int *r, int nr,
                          int cx, int cy, int cw, int ch)
{
	int k;
	for (k = 0; k < nr; k++) {
		int s = r[2 * k], e = r[2 * k + 1], xa, xb, top, bot, lo, hi, font;
		long fg;
		if (s > e) { int t = s; s = e; e = t; }
		if (it->src < 0 || e <= it->src || s >= it->src + (it->srclen ? it->srclen : 1)) continue;
		xa = x0 + lay_item_x(&st->L, &st->M, it, s > it->src ? s : it->src);
		xb = x0 + lay_item_x(&st->L, &st->M, it, e < it->src + it->srclen ? e : it->src + it->srclen);
		if (it->flags & LI_F_FORMULA_LINK) { xa = x0 + it->x; xb = xa + it->w; }
		if (xb <= xa) continue;
		/* the band Library inverts (capture 29: 16 rows at the 18-px pitch of Arial 13 px, from 11 rows above the
		 * baseline to 4 below; the pitch less 2 at the other sizes [i]) */
		font = slot(st, it->face);
		top = y0 + it->y - gfx_ascent(g, font) + 1;
		bot = top + st->M.pitch - 2;
		if (it->flags & LI_F_TYPOGRAPHY) {
			int glyph_bottom = y0 + it->y + gfx_descent(g, font) + 1;
			if (it->flags & LI_F_UNDERLINE) {
				int line_bottom = y0 + it->y + font_position(g, font, XA_UNDERLINE_POSITION, 2) + 1;
				if (line_bottom > glyph_bottom) glyph_bottom = line_bottom;
			}
			top--;
			if (glyph_bottom > bot) bot = glyph_bottom;
		}
		if (top < cy) top = cy;
		if (bot > cy + ch) bot = cy + ch;
		lo = xa > cx ? xa : cx;
		hi = xb < cx + cw ? xb : cx + cw;
		if (hi <= lo || bot <= top) continue;
		gfx_fill(g, lo, top, hi - lo, bot - top, 0xFFFFFF ^ page_rgb(a)); /* the page inverted */
		fg = 0xFFFFFF ^ role_rgb(a, it->colour);
		if ((it->kind == LI_TEXT && it->len) || (it->kind == LI_BOX && (it->flags & LI_F_TYPOGRAPHY))) {
			int x = x0 + it->x, y = y0 + it->y;
			gfx_clip(g, lo, top, hi - lo, bot - top);
			if (it->kind == LI_TEXT) gfx_text(g, x, y, font, fg, st->L.t8 + it->off, it->len);
			else glyph_box(g, it, x, y, fg);
			styled_rules(g, it, font, x, y, fg);
			gfx_clip(g, cx, cy, cw, ch);
		}
	}
}

static int spans_overlap(const lay_item *it, const int *ranges, int count)
{
	int k;
	if (it->src < 0 || it->srclen <= 0) return 0;
	for (k = 0; k < count; k++) {
		int lo = ranges[2 * k], hi = ranges[2 * k + 1];
		if (lo > hi) { int swap = lo; lo = hi; hi = swap; }
		if (lo < hi && lo < it->src + it->srclen && hi > it->src) return 1;
	}
	return 0;
}

#define INLINE_LIMIT 2
#define INLINE_BUDGET (32u * 1024u * 1024u)

static inline_pic *pictures;  /* indexed by the open article's unique media ids */
static size_t picture_bytes;
static unsigned long picture_ticket;
static int picture_width, picture_k, picture_dither;
static long picture_ground;

static int inline_spec(const art_state *st, int media, char spec[512])
{
	int n = snprintf(spec, 512, ART_INLINE_SCHEME ":%lu:%s", pictures[media].ticket, st->d2.media[media].id);
	return n > 0 && n < 512;
}

static void discard_inline(e26_app *a, const art_state *st, int media)
{
	inline_pic *p = &pictures[media];
	char spec[512];
	if (inline_spec(st, media, spec)) picture_forget(a, spec);
	picture_bytes -= p->bytes;
	p->bytes = 0;
	p->ticket = 0;
	p->status = PICTURE_IDLE;
}

static void discard_sizes(e26_app *a, const art_state *st)
{
	int i;
	if (pictures) for (i = 0; i < st->d2.nmedia; i++)
		if (pictures[i].ticket) discard_inline(a, st, i);
}

void art_inline_clear(e26_app *a)
{
	discard_sizes(a, art_st());
	free(pictures);
	pictures = NULL;
}

/* "article-inline:<ticket>:<media id>" answered with its source size: the open article's media item, if the ticket is
 * still the one that item was asked for with */
void art_inline_dims(e26_app *a, const char *spec, int sw, int sh)
{
	static const char prefix[] = ART_INLINE_SCHEME ":";
	const art_state *st = art_st();
	unsigned long ticket;
	char *end;
	int i;
	if (!pictures || !a->have_art || strncmp(spec, prefix, sizeof prefix - 1)) return;
	ticket = strtoul(spec + sizeof prefix - 1, &end, 10);
	if (*end != ':') return;
	for (i = 0; i < st->d2.nmedia; i++)
		if (pictures[i].ticket == ticket && !strcmp(st->d2.media[i].id, end + 1)) { art_media_dims(a, i, sw, sh); return; }
}

static int inline_figure(const art_state *st, const lay_item *it)
{
	const a2_media *m;
	if (it->kind != LI_MEDIA || it->media < 0 || it->media >= st->d2.nmedia || it->w < 3 || it->h < 3 ||
	    it->block < 0 || it->block >= st->d2.nb || st->d2.b[it->block].kind != A2_FIGURE) return 0;
	m = &st->d2.media[it->media];
	return !strcmp(m->kind, "image") && !strncmp(m->mime, "image/", 6);
}

static void prepare_inline(gfx_ctx *g, const art_state *st, int height, long ground)
{
	e26_app *a = g->a;
	int i, pending = art_inline_fetches() + picture_inline_decodes(), dither;
	char spec[512];
	if (!st->d2.nmedia) return;
	if (!pictures) {
		for (i = 0; i < st->L.nit; i++)
			if (inline_figure(st, &st->L.it[i])) break;
		if (i == st->L.nit || !(pictures = (inline_pic *)calloc((size_t)st->d2.nmedia, sizeof *pictures))) return;
	}
	dither = picture_period_mode();
	if (picture_width != st->L.width || picture_k != g->k || picture_ground != ground || picture_dither != dither) {
		discard_sizes(a, st);
		picture_width = st->L.width; picture_k = g->k; picture_ground = ground; picture_dither = dither;
	}
	for (i = 0; i < st->d2.nmedia; i++) pictures[i].distance = INT_MAX;
	for (i = 0; i < st->L.nit; i++) {
		const lay_item *it = &st->L.it[i];
		inline_pic *p;
		int distance;
		if (!inline_figure(st, it)) continue;
		p = &pictures[it->media];
		distance = inline_distance(it->y, it->h, a->scroll, height);
		if (distance < p->distance) {
			p->distance = distance;
			p->w = it->w - 2; p->h = it->h - 2;
		}
	}
	for (i = 0; i < st->d2.nmedia; i++) {
		inline_pic *p = &pictures[i];
		if (!p->bytes) continue;
		if (inline_spec(st, i, spec)) p->status = picture_box(g, spec, 0, 0, p->w, p->h, ground, PICTURE_CHECK);
		else p->status = PICTURE_FAILED;
		if (p->status == PICTURE_PENDING) {
			/* Forget decodes and stop requesting DATA chunks once an image has scrolled out of range. */
			if (p->distance > height) discard_inline(a, st, i);
		} else if (p->status != PICTURE_READY) {
			picture_bytes -= p->bytes;
			p->bytes = 0;
		}
	}
	while (pending < INLINE_LIMIT && (i = inline_next(pictures, st->d2.nmedia, height)) >= 0) {
		inline_pic *p = &pictures[i];
		int dw, dh, victim;
		size_t bytes;
		if (!p->ticket) p->ticket = ++picture_ticket;
		gfx_device_size(g, p->w, p->h, &dw, &dh);
		if (dw < 1 || dh < 1 || dw > 4096 || dh > 4096 || !inline_spec(st, i, spec)) {
			p->status = PICTURE_FAILED;
			continue;
		}
		bytes = (size_t)dw * dh * 4; /* opaque fitted pixmap */
		while (bytes <= INLINE_BUDGET && picture_bytes > INLINE_BUDGET - bytes &&
		       (victim = inline_victim(pictures, st->d2.nmedia, p->distance)) >= 0)
			discard_inline(a, st, victim);
		if (bytes > INLINE_BUDGET || picture_bytes > INLINE_BUDGET - bytes) break;
		p->status = picture_box(g, spec, 0, 0, p->w, p->h, ground, PICTURE_LOAD);
		if (p->status == PICTURE_IDLE) break; /* IMAGE's shared queue is busy */
		if (p->status == PICTURE_FAILED) continue;
		picture_bytes += bytes;
		p->bytes = bytes;
		pending += p->status == PICTURE_PENDING;
	}
}

static int draw_inline(gfx_ctx *g, const art_state *st, const lay_item *it, int x, int y, long ground)
{
	char spec[512];
	return pictures && inline_figure(st, it) && pictures[it->media].status == PICTURE_READY &&
	       inline_spec(st, it->media, spec) &&
	       picture_box(g, spec, x + 1, y + 1, it->w - 2, it->h - 2, ground, PICTURE_DRAW) == PICTURE_READY;
}

/* u14 M8: a figure's enlarge mark, 11 x 11 inside its frame's bottom-right corner: a CDE etched square (the shadow at
 * its top and left, the light at its bottom and right) on the stone face, a 1-px "+" in the rule colour. A click on the
 * figure opens the viewer (art_media_pick). */
static void enlarge_mark(e26_app *a, gfx_ctx *g, int x, int y, long rule)
{
	gfx_fill(g, x, y, 11, 11, ui_rgb(a, UI_STONE));
	gfx_frame2(g, x, y, 11, 11, high_contrast(a) ? 0x000000 : 0x7A7A7A, high_contrast(a) ? 0x000000 : 0xFFFFFF);
	gfx_fill(g, x + 3, y + 5, 5, 1, rule);
	gfx_fill(g, x + 5, y + 3, 1, 5, rule);
}

static int alt_covered(const lay_item *it, const lay_item *box)
{
	return (it->flags & LI_F_ALT) && box && it->block == box->block && (box->flags & LI_F_DRAWN);
}

static int inset_thumb(const lay_inset *p)
{
	int track = p->w - 2 * LAY_INSET_TRACK;
	int thumb = (int)((long long)track * track / (track + p->range));
	return thumb < 9 ? 9 : thumb;
}

static int inset_travel(const lay_inset *p)
{
	return p->w - 2 * LAY_INSET_TRACK - inset_thumb(p);
}

static int inset_thumb_left(const lay_inset *p)
{
	return LAY_INSET_TRACK + (int)((long long)inset_travel(p) * p->offset / p->range);
}

static void inset_track(gfx_ctx *g, const lay_inset *p, int x, int y)
{
	e26_app *a = g->a;
	int thumb = inset_thumb(p);
	int height = LAY_INSET_TRACK;
	gfx_fill(g, x + 1, y, p->w - 2, height - 1, ui_rgb(a, UI_STONE));
	gfx_frame2(g, x, y, height, height, C_WHITE, C_BLACK);
	gfx_frame2(g, x + p->w - height, y, height, height, C_WHITE, C_BLACK);
	gfx_text(g, x + 4, y + 11, F_SMALL, C_BLACK, "<", 1);
	gfx_text(g, x + p->w - height + 4, y + 11, F_SMALL, C_BLACK, ">", 1);
	x += inset_thumb_left(p);
	gfx_fill(g, x, y + 1, thumb, height - 2, C_WHITE);
	gfx_frame2(g, x, y + 1, thumb, height - 2, C_WHITE, C_BLACK);
}

static void draw_article(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int cx, int cy, int under)
{
	const scr_control *c = &s->c[i];
	art_state *st = art_st();
	int inset = art_banner_inset(a, c);
	int k, x0 = cx + art_text_origin(c), y0 = cy + inset - a->scroll, cw = c->w - 16, ch = c->h - 2 * inset, pass;
	gfx_fill(g, cx, cy, c->w, c->h, page_rgb(a));
	if (!a->have_art) return;
	cy += inset;
	if (ch < 1) { art_draw_banners(a, g, c, cx, cy - inset); return; }
	gfx_clip(g, cx, cy, cw, ch);
	if (!st->is_v2) {
		/* build 1's RFC text: its lines, black, in order (byte-identical with build 2) */
		for (k = 0; k < st->L.nit; k++) {
			const lay_item *it = &st->L.it[k];
			int y = y0 + it->y;
			if (it->kind != LI_TEXT || y < cy - 30 || y > cy + ch + 30) continue;
			gfx_text(g, x0 + it->x, y, slot(st, it->face), C_BLACK, st->L.t8 + it->off, it->len);
		}
	} else {
		long ground = role_rgb(a, LC_GROUND);
		prepare_inline(g, st, ch, ground);
		for (pass = 0; pass < 2; pass++) {
			const lay_item *box = NULL;
			const lay_inset *clipped = NULL;
			for (k = 0; k < st->L.nit; k++) {
				lay_item *it = &st->L.it[k];
				const lay_inset *inset;
				int draw_x;
				int top = y0 + (it->kind == LI_TEXT || it->kind == LI_BOX ? it->y - 40 : it->y), bot = top + (it->kind == LI_TEXT || it->kind == LI_BOX ? 60 : it->h);
				long col = role_rgb(a, it->colour);
				if (it->kind == LI_MEDIA) {
					box = it;
					if (pass == 0) it->flags &= ~LI_F_DRAWN;
				}
				if (bot < cy || top > cy + ch || alt_covered(it, box)) continue;
				if (pass == 0) {
					if (it->kind == LI_FILL) gfx_fill(g, x0 + it->x, y0 + it->y, it->w, it->h, col);
					else if (it->kind == LI_FORMULA) gfx_fill(g, x0 + it->x, y0 + it->y, it->w, it->h, ground);
					else if (it->kind == LI_MEDIA) {
						gfx_fill(g, x0 + it->x, y0 + it->y, it->w, it->h, ground);
						if (draw_inline(g, st, it, x0 + it->x, y0 + it->y, ground)) it->flags |= LI_F_DRAWN;
						gfx_frame2(g, x0 + it->x, y0 + it->y, it->w, it->h, col, col);
						if (inline_figure(st, it) && it->w >= 40 && it->h >= 40) enlarge_mark(a, g, x0 + it->x + it->w - 13, y0 + it->y + it->h - 13, col);
					}
					continue;
				}
				inset = lay_line_inset(&st->L, it->line);
				draw_x = x0 + it->x;
				if (inset) {
					draw_x -= inset->offset;
					if (draw_x + it->w <= x0 + inset->x + 6 || draw_x >= x0 + inset->x + inset->w - 6) continue;
				}
				if (clipped != inset) {
					if (inset) gfx_clip(g, x0 + inset->x + 6, cy, inset->w - 12, ch);
					else gfx_clip(g, cx, cy, cw, ch);
					clipped = inset;
				}
				switch (it->kind) {
				case LI_TEXT: {
					int x = draw_x, y = y0 + it->y, font = slot(st, it->face);
					gfx_text(g, x, y, font, col, st->L.t8 + it->off, it->len);
					if ((it->flags & LI_F_UNDER) && !(it->flags & LI_F_UNDERLINE))
						gfx_line(g, x, y + 2, x + it->w - 1, y + 2, col);
					styled_rules(g, it, font, x, y, col);
					break;
				}
				case LI_BOX:
					glyph_box(g, it, draw_x, y0 + it->y, col);
					styled_rules(g, it, slot(st, it->face), draw_x, y0 + it->y, col);
					break;
				case LI_FORMULA: case LI_FRAME: gfx_frame2(g, x0 + it->x, y0 + it->y, it->w, it->h, col, col); break;
				case LI_HLINE:
					if (it->flags & LI_F_DASH) {
						int x;
						for (x = 0; x < it->w; x += 4) gfx_fill(g, x0 + it->x + x, y0 + it->y, 2, 1, col);
					} else gfx_fill(g, x0 + it->x, y0 + it->y, it->w, 1, col);
					break;
				default: break;
				}
			}
			if (clipped) gfx_clip(g, cx, cy, cw, ch);
		}
		for (k = 0; k < st->L.ninset; k++) {
			const lay_inset *inset = &st->L.inset[k];
			int top = y0 + inset->y + inset->h - LAY_INSET_TRACK;
			if (inset->range && top + LAY_INSET_TRACK > cy && top < cy + ch)
				inset_track(g, inset, x0 + inset->x, top);
		}
	}
	/* search hits, then the selection, in reverse */
	if (st->nhits || (st->sel_a >= 0 && st->sel_a != st->sel_b)) {
		const lay_item *box = NULL;
		int sel[2];
		sel[0] = st->sel_a;
		sel[1] = st->sel_b;
		for (k = 0; k < st->L.nit; k++) {
			const lay_item *it = &st->L.it[k];
			const lay_inset *inset = lay_line_inset(&st->L, it->line);
			int y = y0 + it->y;
			int sx = x0, clip_x = cx, clip_w = cw;
			if (it->kind == LI_MEDIA) box = it;
			if (it->kind == LI_FORMULA && y + it->h > cy && y < cy + ch &&
			    (spans_overlap(it, st->hits, st->nhits) || spans_overlap(it, sel, 1))) {
				gfx_frame2(g, x0 + it->x, y, it->w, it->h, C_JUMP, C_JUMP);
				gfx_frame2(g, x0 + it->x + 1, y + 1, it->w - 2, it->h - 2, C_JUMP, C_JUMP);
			}
			if ((it->kind != LI_TEXT && it->kind != LI_BOX) || it->src < 0 || y < cy - 30 || y > cy + ch + 30 || alt_covered(it, box)) continue;
			if (inset) {
				sx -= inset->offset;
				clip_x = x0 + inset->x + 6;
				clip_w = inset->w - 12;
				if (sx + it->x + it->w <= clip_x || sx + it->x >= clip_x + clip_w) continue;
			}
			if (st->nhits) reverse_spans(a, g, st, it, sx, y0, st->hits, st->nhits, clip_x, cy, clip_w, ch);
			if (sel[0] >= 0 && sel[0] != sel[1]) reverse_spans(a, g, st, it, sx, y0, sel, 1, clip_x, cy, clip_w, ch);
			if (inset) gfx_clip(g, cx, cy, cw, ch);
		}
	}
	gfx_unclip(g);
	art_draw_banners(a, g, c, cx, cy - inset);
	if (!under) outline_draw_tip(a, g, s);
}

/* the arrows and page keys scroll the article; Home and End go to its ends */
static void inset_pan(lay_inset *p, long long offset)
{
	p->offset = offset < 0 ? 0 : offset > p->range ? p->range : (int)offset;
}

static int article_keys(e26_app *a, scr_screen *s, int i, KeySym ks)
{
	int step = 0;
	(void)s; (void)i;
	if (!a->have_art) return 0;
	if (ks == XK_Left || ks == XK_Right) {
		art_state *st = art_st();
		int k;
		for (k = 0; k < st->L.ninset; k++) {
			lay_inset *p = &st->L.inset[k];
			if (p->block + 1 != st->focused_inset || !p->range ||
			    p->y >= a->scroll + a->view_h || p->y + p->h <= a->scroll) continue;
			inset_pan(p, p->offset + (ks == XK_Left ? -32 : 32));
			nav_redraw(a);
			return 1;
		}
	}
	if (ks == XK_Home) { art_scroll_to(a, 0); nav_redraw(a); return 1; }
	if (ks == XK_End) { art_scroll_to(a, a->doc_h); nav_redraw(a); return 1; }
	if (ks == XK_Down) step = a->line; else if (ks == XK_Up) step = -a->line;
	else if (ks == XK_Next) step = a->view_h - 2 * a->line; else if (ks == XK_Prior) step = -(a->view_h - 2 * a->line);
	if (step) { art_scroll_by(a, step); nav_redraw(a); }
	return step != 0;
}

/* the article control of the front screen and its frame origin */
static scr_control *front_article(e26_app *a, int *cx, int *cy)
{
	scr_control *c = find_kind(a->cur, K_ARTICLE);
	if (!c) return NULL;
	*cx = c->x + (c->absolute ? 0 : a->cur->ox);
	*cy = c->y + (c->absolute ? 0 : a->cur->oy);
	return c;
}

static int inside_text(e26_app *a, const scr_control *c, int cx, int cy, int x, int y)
{
	int inset = art_banner_inset(a, c);
	return x >= cx && x < cx + c->w - 16 && y >= cy + inset && y < cy + c->h - inset;
}

static int pos_at(e26_app *a, const scr_control *c, int cx, int cy, int x, int y)
{
	art_state *st = art_st();
	lay_hit h;
	if (!inside_text(a, c, cx, cy, x, y)) return -1;
	lay_hit_test(&st->L, &st->M, x - (cx + art_text_origin(c)), y - cy - art_banner_inset(a, c) + a->scroll, 0, &h);
	return h.pos;
}

static const kind_ops article_ops;
static int inset_drag_x, inset_drag_offset;

static void article_drag(e26_app *a, int x, int y)
{
	art_state *st = art_st();
	int cx, cy, p, k;
	scr_control *c = front_article(a, &cx, &cy);
	if (!c) return;
	if (!st->selecting) {
		for (k = 0; k < st->L.ninset; k++) {
			lay_inset *inset = &st->L.inset[k];
			int travel;
			if (inset->block + 1 != st->focused_inset || !inset->range) continue;
			travel = inset_travel(inset);
			if (travel > 0) {
				inset_pan(inset, inset_drag_offset + (long long)(x - inset_drag_x) * inset->range / travel);
				nav_redraw(a);
			}
			break;
		}
		return;
	}
	p = pos_at(a, c, cx, cy, x, y);
	if (p >= 0 && p != st->sel_b) { st->sel_b = p; nav_redraw(a); }
}

static int article_press(e26_app *a, scr_screen *s, int i, int x, int y)
{
	art_state *st = art_st();
	const scr_control *c = &s->c[i];
	int cx = c->x + (c->absolute ? 0 : s->ox), cy = c->y + (c->absolute ? 0 : s->oy);
	int k, dx = x - cx - art_text_origin(c), dy = y - cy - art_banner_inset(a, c) + a->scroll;
	if (!a->have_art) return 0;
	if (!inside_text(a, c, cx, cy, x, y)) return 1;
	st->focused_inset = 0;
	for (k = 0; k < st->L.ninset; k++) {
		lay_inset *p = &st->L.inset[k];
		int local_x = dx - p->x, thumb;
		if (local_x < 0 || local_x >= p->w || dy < p->y || dy >= p->y + p->h) continue;
		st->focused_inset = p->block + 1;
		if (!p->range || dy < p->y + p->h - LAY_INSET_TRACK) break;
		thumb = inset_thumb_left(p);
		if (local_x < LAY_INSET_TRACK) inset_pan(p, p->offset - 32);
		else if (local_x >= p->w - LAY_INSET_TRACK) inset_pan(p, p->offset + 32);
		else if (local_x < thumb) inset_pan(p, p->offset - (p->w - 12));
		else if (local_x >= thumb + inset_thumb(p)) inset_pan(p, p->offset + (p->w - 12));
		else {
			st->selecting = 0;
			inset_drag_x = x;
			inset_drag_offset = p->offset;
			a->drag_ops = &article_ops;
		}
		nav_redraw(a);
		return 1;
	}
	st->sel_a = st->sel_b = pos_at(a, c, cx, cy, x, y);
	st->selecting = 1;
	st->press_x = x;
	st->press_y = y;
	a->drag_ops = &article_ops;
	return 0; /* input.c marks it pressed and redraws (the old selection goes) */
}

static int article_release(e26_app *a, scr_screen *s, int i, int x, int y)
{
	art_state *st = art_st();
	const scr_control *c = &s->c[i];
	int cx = c->x + (c->absolute ? 0 : s->ox), cy = c->y + (c->absolute ? 0 : s->oy), dx = x - st->press_x, dy = y - st->press_y;
	st->selecting = 0;
	if (!inside_text(a, c, cx, cy, x, y)) return 0;
	if (dx * dx + dy * dy < 9) {
		int link = art_link_at(a, c, cx, cy, x, y);
		int pos = pos_at(a, c, cx, cy, x, y);
		static double last_click;
		static int last_x, last_y;
		double now = libc_now_ms();
		int twice = now - last_click < 400 && abs(last_x - x) < 4 && abs(last_y - y) < 4;
		last_click = now; last_x = x; last_y = y;
		st->sel_a = st->sel_b = -1;
		if (link >= 0) { art_follow(a, link); return 1; }
		{
			int k, dxp = x - cx - art_text_origin(c), dyp = y - cy - art_banner_inset(a, c) + a->scroll;
			for (k = 0; st->is_v2 && k < st->L.nit; k++) {
				const lay_item *it = &st->L.it[k];
				if (dxp < it->x || dxp >= it->x + it->w || dyp < it->y || dyp >= it->y + it->h) continue;
				if (it->kind == LI_FORMULA) {
					if (it->media >= 0 && it->media < st->d2.nmedia) art_media_pick(a, st->d2.media[it->media].id, 1);
					else art_formula_toggle(a, it->block);
					nav_redraw(a); return 1;
				}
				if (it->kind == LI_MEDIA && it->media >= 0 && it->media < st->d2.nmedia) {
					art_media_pick(a, st->d2.media[it->media].id, 0); nav_redraw(a); return 1;
				}
			}
		}
		if (art_page_click(a, pos)) return 1;
		if (twice) {
			char *word = art_word_at(a, c, cx, cy, x, y);
			last_click = 0;
			if (word) { art_dictionary(a, word); free(word); return 1; }
		}
	}
	return 0;
}

static const kind_ops article_ops = { draw_article, 1, article_press, article_release, NULL, NULL, article_drag, NULL, article_keys };

static void draw_list(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int cx, int cy, int under)
{
	const scr_control *c = &s->c[i];
	int k, y = cy + 4;
	(void)under;
	gfx_clip(g, cx, cy, c->w, c->h);
	if (!strcmp(c->source, "related") && a->have_art) {
		for (k = 0; k < a->art.nrelated; k++) {
			char t[200];
			char *bar;
			snprintf(t, sizeof t, "%s", a->art.related[k]);
			bar = strchr(t, '|');
			if (bar) *bar = 0;
			gfx_text(g, cx + 6, y + gfx_ascent(g, F_UI), F_UI, C_LISTJUMP, t, -1); /* entry 19 (capture256 32) */
			y += gfx_font_height(g, F_UI) + 3;
		}
	} else if (c->label[0]) {
		gfx_wrap_text(g, cx + 6, y, c->w - 12, F_UI, C_QUIET, c->label, AL_LEFT);
	}
	gfx_unclip(g);
}

void e26_mod_kind_article(void);
void e26_mod_kind_article(void)
{
	static const kind_ops list = { draw_list, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL };
	reg_add(REG_KIND, "article", &article_ops, "kind_article", 0);
	reg_add(REG_KIND, "list", &list, "kind_article", 0);
}
