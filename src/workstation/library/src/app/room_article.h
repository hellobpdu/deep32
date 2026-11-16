/* room_article.h: the article room's state for the kinds that draw it (kind_article.c, kind_outline.c) and for the
 * packages that build on it (W2-ARTICLE, W1-INPUT, W2-FINDER, W2-TOOLS). Build 3, W1-LAYOUT; W2-ARTICLE owns it in
 * wave 2a. One article is open at a time: build 1's RFC text (e26_app.art) or format 2 (art_state.d2), laid out
 * into art_state.L. */
#ifndef E26_ROOM_ARTICLE_H
#define E26_ROOM_ARTICLE_H
#define ART_VIEW_DEFAULT 3
#include "library_app.h"
#include "layout.h"
#include "article_model.h"

typedef struct {
	lay_doc L;            /* the open article, laid out */
	lay_metrics M;        /* the faces it was laid out with (gfx.h) */
	a2_doc d2;            /* the format-2 article, when is_v2 */
	int is_v2;
	int width;            /* the text column it was laid out for */
	/* selection and search hits: plain-text byte ranges (layout.h) */
	int sel_a, sel_b;     /* the selection's anchor and end, -1 none */
	int selecting;        /* a drag is extending it */
	int press_x, press_y; /* where the press went down (a click on a jump moves less than 3 px) */
	int *hits;            /* search hits: pairs of [start, end) offsets in the plain text, shown in reverse */
	int nhits;
	/* the outline (kind_outline.c) */
	int out_top;          /* the first entry in view */
	int out_follow;       /* the article scroll the outline last followed (99 touch: it keeps the current entry in view) */
	int out_hot;          /* the entry under the pointer, -1 */
	int out_tip;          /* the entry whose full text shows in the tip box (it is cut off in the pane), -1 */
	int out_tip_x, out_tip_y; /* the tip's place (frame coordinates) */
	int repeat_ctl;       /* the arrow held down (hold-to-repeat), -1 */
	scr_screen *repeat_s;
	int repeat_dir;
	/* ARTICLE room additions. Identifiers here retain their complete UTF-8 bytes. */
	char *full_id;
	int pack, view, media_index, busy;
	int open_formula;
	int focused_inset;    /* one-based article block, independent of relayout */
	double resumed_until;
	ar_tree tree;
	ar_history history;
	char *panel_text;
	char **panel_ids;
	int panel_rows, panel_top;
	char message[200];
} art_state;

art_state *art_st(void);
int art_font_slot(int face);  /* the gfx slot for a resolved layout face */
int art_text_origin(const scr_control *c); /* article-local x shared by paint and hit-testing */
/* re-lays out the open article (a new type size or column width), keeping the first line in view */
void art_relayout(e26_app *a);
void art_formula_toggle(e26_app *a, int block);
/* the jump at a frame point over the article control (c at cx, cy), -1; *link_out the layout's link */
int art_link_at(e26_app *a, const scr_control *c, int cx, int cy, int x, int y);
/* follows a jump of the open article: a heading, footnote or media anchor here, another article by id or RFC number;
 * URL targets and media are W2-ARTICLE's (Copy URL, the media frame) and show the notice box until then */
void art_follow(e26_app *a, int link);
/* search hits for the open article (pairs of plain-text offsets, copied); the view scrolls to the first */
void art_set_hits(e26_app *a, const int *pairs, int npairs);
/* the selection as UTF-8 (malloc'd), NULL when none */
char *art_selection(e26_app *a);
/* the word under a frame point over the article control, as UTF-8 (malloc'd), NULL none (the Dictionary hook) */
char *art_word_at(e26_app *a, const scr_control *c, int cx, int cy, int x, int y);
/* kind_outline.c: the tip of a cut-off outline entry (the article kind draws it last, over the text) */
void outline_draw_tip(e26_app *a, gfx_ctx *g, scr_screen *s);
/* the scroll position that shows document y at the top of the text, for the scroll keys */
void art_scroll_to(e26_app *a, int y);
/* Accepted Library request: the current article stays readable until the whole new one validates. */
void art_open_pack(e26_app *a, int pack, const char *id, const char *anchor);
void art_open_cisco(e26_app *a, int pack, const char *catalogue_generation, const char *catalogue_id,
    const char *reader_generation, const char *id, const char *anchor);
void art_fetch_start(e26_app *a, int pack, const char *id, const char *anchor, int history_at, int scroll, int view);
void art_fetch_cancel(void);
int art_install_v2(e26_app *a, a2_doc *d, int pack, const char *anchor, int history_at, int scroll, int view);
void art_view_set(e26_app *a, int view, int save);
int art_position_uri(e26_app *a, char *out, size_t n);
int art_public_position(const e26_app *a);
void art_save_position(e26_app *a);
void art_history_capture(e26_app *a);
void art_history_position(e26_app *a, const ar_place *place);
void art_history_items(e26_app *a, scr_menu *m);
void art_view_apply(e26_app *a);
int art_page_jump(e26_app *a, int page);
int art_page_click(e26_app *a, int pos);
int art_banner_inset(e26_app *a, const scr_control *c);
void art_draw_banners(e26_app *a, gfx_ctx *g, const scr_control *c, int x, int y);
void art_dictionary(e26_app *a, const char *term);
void art_media_pick(e26_app *a, const char *id, int show);
void art_media_changed(e26_app *a);
#define ART_INLINE_SCHEME "article-inline"
void art_inline_clear(e26_app *a);
int art_inline_fetches(void);   /* pending DATA transfers, including an old article's last chunk */
/* u14 M8: IMAGE's source size of an inline picture (kind_picture.c, from its fit-down answer; 0 x 0 for a drawing):
 * kind_article.c matches the spec to the open article's media (its ticket), room_article.c keeps it per media item
 * and lays the article out again (debounced, anchored by the viewport) when a figure's size becomes known */
void art_inline_dims(e26_app *a, const char *spec, int sw, int sh);
void art_media_dims(e26_app *a, int media, int sw, int sh);
const a2_media *art_media_current(void);
void art_media_spec(char *buf, size_t n, const char *id);
int art_copy_action(e26_app *a, const e26_actx *x);
void art_features_register(void);
void art_features_opened(e26_app *a, int remember);
void art_features_controls(e26_app *a);
int art_restore_local(e26_app *a, const ar_place *place, int at);
void art_panel_publish(const char *name);
/* u14 M13: a list pane's row (panel_text, rows end in '\n', UTF-8) may start with one of these bytes; a row is a jump
 * when panel_ids has an id for it ("@place:<n>" is a place of the history: art_place_open) */
#define ART_ROW_GROUP '\001'  /* a group heading: bold */
#define ART_ROW_QUIET '\002'  /* quiet grey (not in the Library) */
void art_humanise_id(const char *id, char *out, size_t n); /* "rfc1092" -> "RFC 1092" (an id people can read) */
int art_place_open(e26_app *a, int at);  /* u14 M18: goes to place at of the history (the Back List), 1 done */
void art_status_note(e26_app *a, const char *text); /* a short note in the status strip for four seconds */
void art_resume_done(e26_app *a);        /* u14 S12: the reader scrolled: the "Resumed at" note goes */
int art_scroll_native(const e26_app *a); /* u14 S16: the native reader's proportional thumb and heading ticks */
void art_scroll_track_draw(e26_app *a, gfx_ctx *g, const scr_control *c, int x, int y, int ty, int th);
/* u14 S7: Find in article: the keys while its line is open (1 = taken), and the section bar's line */
int art_find_key(e26_app *a, XKeyEvent *ev, KeySym ks);
const char *art_find_line(char *buf, size_t n);
int art_marking_inset(e26_app *a, const scr_control *c, const char *mark);
void art_marking_draw(e26_app *a, gfx_ctx *g, const scr_control *c, int x, int y, const char *mark);

#endif
