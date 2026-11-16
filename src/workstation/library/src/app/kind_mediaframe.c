/* kind_mediaframe.c: ARTICLE's picture, caption and media controls over IMAGE and DATA. */
#include "room_article.h"
#include "article_library.h"
#include "article_clipboard.h"
#include "picture.h"
#include "figure_dialog.h"
#include "cursor.h"
#include "../common/sha256.h"
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static unsigned long media_epoch;
static int inline_fetches;
int art_inline_fetches(void) { return inline_fetches; }

static const char *inline_media_id(const char *spec)
{
	static const char prefix[] = ART_INLINE_SCHEME ":";
	const char *id;
	if (strncmp(spec, prefix, sizeof prefix - 1)) return NULL;
	id = strchr(spec + sizeof prefix - 1, ':');
	return id ? id + 1 : NULL;
}

static void media_redraw(e26_app *a, void *ud)
{
	(void)ud;
	if (a->dpy) nav_redraw(a);
	figure_dialog_redraw(a);
}

static char failed[16][512];
static int nfailed;
static int is_failed(const char *spec)
{
	int i;
	for (i = 0; i < nfailed; i++) if (!strcmp(failed[i], spec)) return 1;
	return 0;
}
static void failed_spec(const char *spec)
{
	if (nfailed < 16) snprintf(failed[nfailed++], sizeof failed[0], "%s", spec);
}

const a2_media *art_media_current(void)
{
	art_state *st = art_st();
	return st->is_v2 && st->media_index >= 0 && st->media_index < st->d2.nmedia ? &st->d2.media[st->media_index] : NULL;
}

void art_media_spec(char *buf, size_t n, const char *id)
{
	char hash[65];
	int count;
	const char *g = ar_lib_generation(art_st()->pack);
	sha256_hex(g, strlen(g), hash);
	count = snprintf(buf, n, "article-media:%d:%.16s:%s", art_st()->pack, hash, id);
	if (count < 0 || (size_t)count >= n) buf[0] = 0;
}

void art_media_changed(e26_app *a)
{
	art_state *st = art_st();
	int i;
	media_epoch++;
	figure_dialog_close(a);
	art_inline_clear(a);
	nfailed = 0;
	for (i = 0; i < st->d2.nmedia && st->is_v2; i++) {
		char spec[512]; art_media_spec(spec, sizeof spec, st->d2.media[i].id);
		if (*spec) picture_forget(a, spec);
	}
}

void art_media_pick(e26_app *a, const char *id, int show)
{
	art_state *st = art_st();
	int i;
	for (i = 0; st->is_v2 && i < st->d2.nmedia; i++) if (!strcmp(st->d2.media[i].id, id)) {
		st->media_index = i;
		if ((show || st->view >= 2) && a->dpy) {
			int action = reg_find(REG_ACTION, "article.enlarge");
			e26_actx x = { .ctl = -1 };
			if (action >= 0 && action_built(action)) (void)action_run(a, action, &x);
		}
		return;
	}
	nav_notice_text(a, "Media", "This media item is not in the article's inventory.");
}

typedef struct {
	e26_app *a;
	char spec[512], mime[80];
	char *id, *generation;
	unsigned long epoch;
	int pack, write_fd, read_fd, inline_job;
	size_t offset, total;
} media_job;

static void media_free(media_job *j)
{
	if (j->inline_job) inline_fetches--;
	if (j->write_fd >= 0) close(j->write_fd);
	if (j->read_fd >= 0) close(j->read_fd);
	free(j->id); free(j->generation); free(j);
}

static int media_ask(media_job *j);

static void media_failed(e26_app *a, const char *spec)
{
	if (inline_media_id(spec)) picture_source_failed(a, spec);
	else { failed_spec(spec); picture_forget(a, spec); }
}

static void media_received(const lib_reply *r, void *ud)
{
	media_job *j = (media_job *)ud;
	const jv *available = json_get(r->result, "available"), *next = json_get(r->result, "next"), *total = json_get(r->result, "total");
	const char *b64 = json_str(r->result, "data_b64");
	unsigned char *data;
	size_t n, done = 0;
	uint64_t to, size;
	e26_app *a = j->a;
	int is_inline = j->inline_job;
	if (j->epoch != media_epoch || !picture_waiting(j->spec)) {
		media_free(j);
		if (is_inline && a->ctx) timer_after(a, 0, media_redraw, NULL);
		return;
	}
	if (!r->ok || !r->generation || strcmp(r->generation, j->generation) || !available || available->type != J_BOOL ||
	    !available->b || !b64 || !next || e26j_u64(total, &size) || size > 64u * 1024u * 1024u ||
	    (j->offset && size != j->total)) goto unavailable;
	data = e26j_unb64(b64, strlen(b64), &n);
	if (!data || n > 49152 || j->offset > size || n > size - j->offset) { free(data); goto unavailable; }
	while (done < n) {
		ssize_t wrote = write(j->write_fd, data + done, n - done);
		if (wrote <= 0) { free(data); goto unavailable; }
		done += (size_t)wrote;
	}
	free(data); j->offset += n; j->total = (size_t)size;
	if (next->type != J_NULL) {
		if (!n || e26j_u64(next, &to) || to != j->offset || to >= size) goto unavailable;
		if (media_ask(j)) return;
		goto unavailable;
	}
	if (j->offset != size || !size) goto unavailable;
	close(j->write_fd); j->write_fd = -1;
	picture_feed(j->a, j->spec, j->read_fd, j->mime); j->read_fd = -1;
	media_free(j); return;
unavailable:
	if (!is_inline)
		snprintf(art_st()->message, sizeof art_st()->message, "Media unavailable. Its caption and source credit are retained.");
	media_failed(a, j->spec);
	media_free(j);
	if (a->ctx) timer_after(a, 0, media_redraw, NULL);
}

static int media_ask(media_job *j)
{
	e26j_buf b;
	char *args;
	uint64_t request;
	e26j_init(&b); e26j_lit(&b, "{\"id\":"); e26j_cstr(&b, j->id);
	e26j_lit(&b, ",\"offset\":"); e26j_u(&b, j->offset); e26j_lit(&b, ",\"limit\":49152}"); args = e26j_take(&b);
	request = args ? ar_lib_request(j->a, j->pack, "media", args, media_received, j) : 0;
	free(args);
	return request != 0;
}

static int media_open(e26_app *a, const char *spec, int *fd, char *mime, size_t n)
{
	art_state *st = art_st();
	const a2_media *m = NULL;
	const char *inline_id = inline_media_id(spec);
	int i;
	char expected[512], path[1600];
	media_job *j;
	if (!inline_id && is_failed(spec)) return -1;
	for (i = 0; st->is_v2 && i < st->d2.nmedia; i++) {
		if (inline_id) {
			if (strcmp(inline_id, st->d2.media[i].id)) continue;
		} else {
			art_media_spec(expected, sizeof expected, st->d2.media[i].id);
			if (!*expected || strcmp(expected, spec)) continue;
		}
		m = &st->d2.media[i];
		break;
	}
	if (!m || strncmp(m->mime, "image/", 6)) {
		if (!inline_id) failed_spec(spec);
		return -1;
	}
	snprintf(mime, n, "%s", m->mime);
	if (st->pack < 0) {
		if (strncmp(m->uri, "fixture:media/", 14) || strstr(m->uri, "..") || strchr(m->uri, '\\')) {
			if (!inline_id) failed_spec(spec);
			return -1;
		}
		if (snprintf(path, sizeof path, "%s/../media/%s", a->article_dir, m->uri + 14) >= (int)sizeof path) return -1;
		*fd = open(path, O_RDONLY | O_CLOEXEC);
		if (*fd < 0 && !inline_id) failed_spec(spec);
		return *fd >= 0 ? 1 : -1;
	}
	j = (media_job *)calloc(1, sizeof *j); if (!j) return -1;
	j->write_fd = j->read_fd = -1;
	j->a = a; j->pack = st->pack; j->epoch = media_epoch;
	j->id = strdup(m->id); j->generation = strdup(ar_lib_generation(st->pack));
	snprintf(j->spec, sizeof j->spec, "%s", spec); snprintf(j->mime, sizeof j->mime, "%s", m->mime);
	/* Reopen read-only before unlinking. Only that descriptor goes to IMAGE; the spool has no pathname after this. */
	snprintf(path, sizeof path, "/tmp/e26-article.XXXXXXXX");
	j->write_fd = mkstemp(path);
	if (j->write_fd >= 0) { j->read_fd = open(path, O_RDONLY | O_CLOEXEC); (void)unlink(path); }
	if (!j->id || !j->generation || j->write_fd < 0 || j->read_fd < 0) {
		if (!inline_id) failed_spec(spec);
		media_free(j);
		return -1;
	}
	(void)fcntl(j->write_fd, F_SETFD, FD_CLOEXEC);
	j->inline_job = inline_id != NULL;
	inline_fetches += j->inline_job;
	if (media_ask(j)) return 0;
	if (!inline_id) failed_spec(spec);
	media_free(j);
	return -1;
}

static const char *media_source(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	const a2_media *m = art_media_current();
	(void)a; (void)c;
	if (!m) return "";
	art_media_spec(buf, n, m->id); return buf;
}

/* u14 M6 in the figure window and the Caption pane: a converter's placeholder ("Image", "Image on page 12") or an
 * alternative text that only repeats the caption says nothing, as in the article */
static void caption_trim(const char **s, size_t *n)
{
	while (*n && (**s == ' ' || **s == '\t' || **s == '\n' || **s == '\r')) { (*s)++; (*n)--; }
	while (*n && ((*s)[*n - 1] == ' ' || (*s)[*n - 1] == '\t' || (*s)[*n - 1] == '\n' || (*s)[*n - 1] == '\r')) (*n)--;
}

static int caption_placeholder(const char *s, size_t n)
{
	size_t i = 14;
	caption_trim(&s, &n);
	if (!n || (n == 5 && !strncmp(s, "Image", 5))) return 1;
	if (n <= 14 || strncmp(s, "Image on page ", 14)) return 0;
	while (i < n && s[i] >= '0' && s[i] <= '9') i++;
	return i == n;
}

static int caption_same(const char *a, size_t an, const char *b, size_t bn)
{
	caption_trim(&a, &an);
	caption_trim(&b, &bn);
	return an == bn && !memcmp(a, b, an);
}

static char *media_caption(const a2_media *m)
{
	e26j_buf text;
	art_state *st = art_st();
	const char *alt = m->alt ? m->alt : "", *credit = m->credit ? m->credit : "";
	a2_buf b;
	int i, lines = 0;
	e26j_init(&text);
	memset(&b, 0, sizeof b);
	for (i = 0; i < st->d2.nb; i++) if (st->d2.b[i].kind == A2_FIGURE && !strcmp(st->d2.b[i].media, m->id)) {
		if (a2_runs_text(&st->d2.b[i].runs, &b)) { free(b.s); memset(&b, 0, sizeof b); }
		break;
	}
	if (b.s && !caption_placeholder(b.s, b.n)) { e26j_raw(&text, b.s, b.n); lines++; }
	if (!caption_placeholder(alt, strlen(alt)) && !(lines && caption_same(b.s, b.n, alt, strlen(alt)))) {
		if (lines++) e26j_lit(&text, "\n");
		e26j_lit(&text, alt);
	}
	free(b.s);
	if (credit[0]) {
		if (lines++) e26j_lit(&text, "\n");
		e26j_lit(&text, "Credit: ");
		e26j_lit(&text, credit);
	}
	return e26j_take(&text);
}

static int enlarge(e26_app *a, const e26_actx *x)
{
	const a2_media *m = art_media_current();
	const char *path = getenv("E26_LAYOUT_DUMP");
	char spec[512], *text;
	FILE *f;
	(void)x;
	if (!m) { nav_notice_text(a, "Enlarge", "This article has no media item."); return 0; }
	art_media_spec(spec, sizeof spec, m->id);
	text = media_caption(m);
	if (figure_dialog_open(a, spec, text))
		nav_notice_text(a, "Enlarge", "The figure window could not be opened.");
	else if (path && *path && a->have_art && !*ar_marking(&art_st()->d2) && (f = fopen(path, "a")) != NULL) {
		fprintf(f, "M\t%s\n", m->id);
		fclose(f);
	}
	free(text);
	return 0;
}

static int caption(e26_app *a, const e26_actx *x)
{
	const a2_media *m = art_media_current();
	art_state *st = art_st();
	(void)x;
	if (!m) return 1;
	free(st->panel_text); st->panel_text = media_caption(m); st->panel_top = 0;
	/* Caption is an independent pane and has no navigable article rows. */
	{
		int row;
		for (row = 0; row < st->panel_rows; row++) free(st->panel_ids[row]);
		free(st->panel_ids); st->panel_ids = NULL; st->panel_rows = 0;
	}
	art_panel_publish("article-caption");
	(void)nav_push_window(a, "article-caption", 0); return 0;
}

static int original(e26_app *a, const e26_actx *x)
{
	int id = reg_find(REG_ACTION, "reader.open-media");
	if (id >= 0 && action_built(id)) return action_run(a, id, x);
	nav_notice_text(a, "Open original", "The original viewer needs the Reader room. Caption and Enlarge are available here."); return 0;
}

static int media_menu(e26_app *a, const e26_actx *x)
{
	(void)x; (void)nav_push_window(a, "article-media-menu", 0); return 0;
}
static int copy_media(e26_app *a, const e26_actx *x)
{
	(void)x;
	nav_notice_text(a, "Copy Image and Caption", "Copying an image with its caption needs INPUT's binary image selection targets. Text copy with its source citation is available from the article.");
	return 0;
}
static int factbox(e26_app *a, const e26_actx *x)
{
	(void)x;
	nav_notice_text(a, "Fact Box", "Fact Box placement in the media frame needs the Exhibits room. Its text remains readable in the article.");
	return 0;
}
static int prev(e26_app *a, const e26_actx *x) { (void)a; (void)x; if (art_st()->media_index > 0) art_st()->media_index--; return 1; }
static int next(e26_app *a, const e26_actx *x) { (void)a; (void)x; if (art_st()->media_index + 1 < art_st()->d2.nmedia) art_st()->media_index++; return 1; }

static void draw(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int x, int y, int under)
{
	const scr_control *c = &s->c[i];
	const a2_media *m = art_media_current();
	const kind_ops *pic = kind_of(reg_find(REG_KIND, "picture"));
	scr_control box = *c;
	scr_screen frame = *s;
	char t[120], spec[512];
	int k, bh = 18;
	gfx_fill(g, x, y, c->w, c->h, C_PAPER);
	if (!m) {
		gfx_wrap_text(g, x + 12, y + 14, c->w - 24, F_UI, C_QUIET, "No media in this article.", AL_LEFT); return;
	}
	gfx_frame2(g, x, y, c->w, c->h, ui_rgb(a, UI_TAUPE), ui_rgb(a, UI_STONE));
	box.h = c->h - 3 * bh; box.w = c->w - 8; box.x = box.y = 0; box.bg = C_PAPER;
	snprintf(box.source, sizeof box.source, "article-media");
	frame.c = &box; frame.nc = 1;
	if (pic && pic->draw && !strncmp(m->mime, "image/", 6)) pic->draw(a, g, &frame, 0, x + 4, y + 4, under);
	else gfx_wrap_text(g, x + 8, y + 18, c->w - 16, F_UI, C_QUIET, m->alt, AL_LEFT);
	art_media_spec(spec, sizeof spec, m->id);
	if (is_failed(spec)) gfx_wrap_text(g, x + 8, y + 18, c->w - 16, F_UI, C_QUIET, m->alt, AL_LEFT);
	snprintf(t, sizeof t, "%d of %d", art_st()->media_index + 1, art_st()->d2.nmedia);
	gfx_text(g, x + 8, y + c->h - 2 * bh - 3, F_SMALL, C_BLACK, t, -1);
	for (k = 0; k < 3; k++) {
		int bx = x + k * c->w / 3, bw = c->w / 3;
		gfx_frame2(g, bx, y + c->h - 2 * bh, bw, bh, ui_rgb(a, UI_STONE), C_BLACK);
		gfx_text(g, bx + 5, y + c->h - 2 * bh + 12, F_UI, C_BLACK, k == 0 ? "Caption" : k == 1 ? "Open" : "Expand", -1);
	}
	gfx_text(g, x + 8, y + c->h - 5, F_UI, C_BLACK, "< Previous", -1);
	gfx_text(g, x + c->w - 48, y + c->h - 5, F_UI, C_BLACK, "Next >", -1);
}

static int release_frame(e26_app *a, scr_screen *s, int i, int x, int y)
{
	const scr_control *c = &s->c[i];
	int dx = x - c->x - s->ox, dy = y - c->y - s->oy;
	e26_actx ctx;
	memset(&ctx, 0, sizeof ctx); ctx.ctl = -1;
	if (dy < c->h - 36) { enlarge(a, &ctx); return 1; }
	if (dy >= c->h - 18) return dx < c->w / 2 ? prev(a, &ctx) : next(a, &ctx);
	if (dx < c->w / 3) return caption(a, &ctx) ? 0 : 1;
	if (dx < 2 * c->w / 3) return original(a, &ctx) ? 0 : 1;
	return enlarge(a, &ctx) ? 0 : 1;
}

void e26_mod_kind_mediaframe(void);
void e26_mod_kind_mediaframe(void)
{
	static const kind_ops ops = { draw, 1, NULL, release_frame, NULL, NULL, NULL, NULL, NULL };
	static const source_ops source = { media_source, NULL, NULL };
	static const e26_action cap = { caption }, big = { enlarge }, orig = { original }, menu = { media_menu }, pre = { prev }, nex = { next };
	static const e26_action copy = { copy_media }, facts = { factbox };
	reg_add(REG_KIND, "mediaframe", &ops, "kind_mediaframe", 0);
	reg_add(REG_SOURCE, "article-media", &source, "kind_mediaframe", 0);
	reg_add(REG_ACTION, "article.caption", &cap, "kind_mediaframe", 0);
	reg_add(REG_ACTION, "article.enlarge", &big, "kind_mediaframe", 0);
	reg_add(REG_ACTION, "article.media-open", &orig, "kind_mediaframe", 0);
	reg_add(REG_ACTION, "article.media-menu", &menu, "kind_mediaframe", 0);
	reg_add(REG_ACTION, "article.media-prev", &pre, "kind_mediaframe", 0);
	reg_add(REG_ACTION, "article.media-next", &nex, "kind_mediaframe", 0);
	reg_add(REG_ACTION, "article.copy-media", &copy, "kind_mediaframe", 0);
	reg_add(REG_ACTION, "article.factbox", &facts, "kind_mediaframe", 0);
	(void)picture_provider("article-media", media_open);
	(void)picture_provider(ART_INLINE_SCHEME, media_open);
}
