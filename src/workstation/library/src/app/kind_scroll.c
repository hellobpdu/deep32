/* kind_scroll.c: kind scrollbar: the period design's own article scroll bar (1995 captures 29-35): the track in its colour,
 * the thumb a frame of the thumb strip; nothing when the article fits (capture 05 shows no track for a short
 * article). A press above or below the thumb pages; the thumb drags. */
#include "library_app.h"
#include "room_article.h"

static void draw(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int x, int y, int under)
{
	const scr_control *c = &s->c[i];
	int ty, th;
	(void)under;
	if (!art_scroll_thumb_at(a, c, y, &ty, &th)) return; /* y: this control's own drawn origin */
	gfx_fill(g, x, y, c->w, c->h, c->bg >= 0 ? c->bg : C_WHITE);
	if (art_scroll_native(a)) { art_scroll_track_draw(a, g, c, x, y, ty, th); return; } /* u14 S16 */
	if (c->strip[0]) {
		xasset *t = xa_get(a, c->strip);
		if (t) {
			int fw = t->w / c->frames, fr = frame_for(c, S_NORMAL);
			if (fr >= c->frames) fr = 0;
			gfx_blit(g, t, fr * fw, 0, fw, t->h, x + (c->w - fw) / 2, ty);
		}
	}
}

static void drag(e26_app *a, int x, int y)
{
	scr_control *sc = find_kind(a->cur, K_SCROLL);
	int range = a->doc_h - a->view_h, ty, th;
	(void)x;
	if (sc && range > 0 && art_scroll_thumb(a, sc, &ty, &th) && sc->h > th) {
		art_resume_done(a);
		a->scroll = (int)((long)(y - a->drag_off - (sc->y + a->cur->oy)) * range / (sc->h - th));
		art_place_scrollbar(a);
		nav_redraw(a);
	}
}

static int press(e26_app *a, scr_screen *s, int i, int x, int y);

static const kind_ops ops = { draw, 1, press, NULL, NULL, NULL, drag, NULL, NULL };

static int press(e26_app *a, scr_screen *s, int i, int x, int y)
{
	int ty, th;
	(void)x;
	if (art_scroll_thumb(a, &s->c[i], &ty, &th)) {
		art_resume_done(a);
		if (y < ty) a->scroll -= a->view_h - 2 * a->line;
		else if (y >= ty + th) a->scroll += a->view_h - 2 * a->line;
		else { a->drag_ops = &ops; a->drag_off = y - ty; }
		art_place_scrollbar(a);
	}
	nav_redraw(a);
	return 1;
}

void e26_mod_kind_scroll(void);
void e26_mod_kind_scroll(void)
{
	reg_add(REG_KIND, "scrollbar", &ops, "kind_scroll", 0);
}
