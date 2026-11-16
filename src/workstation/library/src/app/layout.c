/* layout.c: rich article layout (layout.h). The flow (fl_*) breaks runs of styled text into lines: words split at
 * spaces and at shy runs (a hyphen is drawn at a shy break), a word wider than the line splits between characters,
 * Symbol characters and fallback boxes travel glued to their word. Block layouts place flows, fills and frames and
 * build the plain text in the same order as validate_v2.py's block_text, so every drawn character knows its offset.
 *
 * Period values (AUTHENTICITY section 7): body text Arial 13 px on an 18-px pitch at the Library's default type size
 * (the pitch of the type size in use comes in lay_metrics.pitch), 9 px between paragraphs, 14 px before a level-1
 * heading and 10 before the others, 22 px per indent level, preformatted lines on the mono face's height plus 1 and
 * 8 px after: build 1's measured rules, kept for format 2. The rest ([i]): 2 px between list items, 3-px cell
 * padding, 1-px table rules, a 120-px picture box until IMAGE knows the picture's size.
 *
 * u14 (readability spec sections 2-5): the page's spacing comes from the body's em and its line pitch, so it scales
 * as one piece at every Text size (at the Library's 13 px body on the 18 px pitch it stays within a pixel or two of the
 * period values): half a line between paragraphs, 1.6 em per indent, a quarter line between list items, square
 * bullets, em cell padding, heading space by level, figures at their own size (never enlarged, the column at most),
 * captions and notes in the small body face, footnote markers "[28]" that never grow their line, and the Note,
 * Caution and Warning callouts in CDE etched frames. */
#include "layout.h"
#include "../common/text8.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>

/* u14 spacing tokens (spec section 3): n hundredths of the em, rounded; the paragraph gap; the list item gap */
static int em_px(const lay_metrics *M, int hundredths) { return (LAY_EM(M) * hundredths + 50) / 100; }
static int indent_px(const lay_metrics *M) { return em_px(M, 160); }
static int para_gap(const lay_metrics *M) { return M->pitch / 2; }
static int item_gap(const lay_metrics *M) { return M->pitch / 4; }

int lay_face(const lay_metrics *M, int f)
{
	if (f >= LF_NFACES && f - LF_NFACES < M->n_size_faces) return f;
	if (f < 0 || f >= LF_NFACES) return LF_BODY;
	if (M->have[f]) return f;
	switch (f) {
	case LF_BOLD_ITALIC: return M->have[LF_BOLD] ? LF_BOLD : LF_BODY;
	case LF_HEAD_ITALIC: return M->have[LF_HEAD] ? LF_HEAD : lay_face(M, LF_BOLD_ITALIC);
	case LF_SUBHEAD_ITALIC: return M->have[LF_SUBHEAD] ? LF_SUBHEAD : lay_face(M, LF_BOLD_ITALIC);
	case LF_HEAD: case LF_SUBHEAD: return lay_face(M, LF_BOLD);
	case LF_HEAD3: return lay_face(M, LF_SUBHEAD);
	case LF_MONO_SMALL: return LF_MONO;
	case LF_SMALL: return M->have[LF_UI] ? LF_UI : LF_BODY;
	case LF_UI_BOLD: return M->have[LF_BOLD] ? LF_BOLD : LF_BODY;
	case LF_UI: return LF_BODY;
	default: return LF_BODY;
	}
}

static int fw(const lay_metrics *M, int f, const char *s, int n) { return n > 0 ? M->width(M->ud, lay_face(M, f), s, n) : 0; }
static int fasc(const lay_metrics *M, int f) { return M->ascent(M->ud, lay_face(M, f)); }
static int fdesc(const lay_metrics *M, int f) { return M->descent(M->ud, lay_face(M, f)); }

/* u14: the small body face's line pitch (captions, credits, footnotes, the byline): 0.85 of the body's, never less
 * than the face's own height and 2 */
static int small_pitch(const lay_metrics *M)
{
	int own = fasc(M, LF_SMALL) + fdesc(M, LF_SMALL) + 2, p = (M->pitch * 85 + 50) / 100;
	return p > own ? p : own;
}

/* ---------------------------------------------------------------- storage */

static void *grow(lay_doc *L, void *p, int *cap, int need, size_t size)
{
	void *np;
	int nc;
	if (need <= *cap) return p;
	nc = *cap ? *cap : 64;
	while (nc < need) nc *= 2;
	np = realloc(p, (size_t)nc * size);
	if (!np) { L->oom = 1; return NULL; }
	*cap = nc;
	return np;
}

static lay_item *new_item(lay_doc *L, int kind)
{
	lay_item *n = (lay_item *)grow(L, L->it, &L->cit, L->nit + 1, sizeof(lay_item));
	lay_item *it;
	if (!n) return NULL;
	L->it = n;
	it = &L->it[L->nit++];
	memset(it, 0, sizeof *it);
	it->kind = kind;
	it->src = -1;
	it->link = -1;
	it->line = -1;
	it->media = -1;
	return it;
}

static int t8_add(lay_doc *L, const char *s, int n)
{
	int off = (int)L->nt8;
	if (L->nt8 + (size_t)n + 1 > L->ct8) {
		size_t c = L->ct8 ? L->ct8 : 8192;
		char *ns;
		while (c < L->nt8 + (size_t)n + 1) c *= 2;
		if (!(ns = (char *)realloc(L->t8, c))) { L->oom = 1; return -1; }
		L->t8 = ns;
		L->ct8 = c;
	}
	memcpy(L->t8 + L->nt8, s, (size_t)n);
	L->nt8 += (size_t)n;
	return off;
}

static int pt_add(lay_doc *L, const char *s, size_t n)
{
	int off = (int)L->pt.n;
	if (a2_buf_add(&L->pt, s, n)) L->oom = 1;
	return off;
}
static int pt_adds(lay_doc *L, const char *s) { return pt_add(L, s, strlen(s)); }

static void shape(lay_doc *L, int kind, int x, int y, int w, int h, int colour, int block)
{
	lay_item *it = new_item(L, kind);
	if (!it) return;
	it->x = x; it->y = y; it->w = w; it->h = h;
	it->colour = colour;
	it->block = block;
}

static int add_link(lay_doc *L, int kind, const a2_target *t, const char *name, const char *def)
{
	lay_link *n = (lay_link *)grow(L, L->lk, &L->clk, L->nlk + 1, sizeof(lay_link));
	if (!n) return -1;
	L->lk = n;
	L->lk[L->nlk].kind = kind;
	L->lk[L->nlk].target = t;
	L->lk[L->nlk].name = name;
	L->lk[L->nlk].definition = def;
	return L->nlk++;
}

static void add_anchor(lay_doc *L, int kind, const char *name, int y, int block)
{
	lay_anchor *n = (lay_anchor *)grow(L, L->an, &L->can, L->nan + 1, sizeof(lay_anchor));
	if (!n) return;
	L->an = n;
	L->an[L->nan].kind = kind;
	L->an[L->nan].name = name;
	L->an[L->nan].y = y;
	L->an[L->nan].block = block;
	L->nan++;
}

static void add_entry(lay_doc *L, int kind, int level, int y, int block, const char *text8)
{
	lay_entry *n = (lay_entry *)grow(L, L->en, &L->cen, L->nen + 1, sizeof(lay_entry));
	char *t;
	if (!n) return;
	L->en = n;
	if (!(t = strdup(text8))) { L->oom = 1; return; }
	L->en[L->nen].kind = kind;
	L->en[L->nen].level = level;
	L->en[L->nen].y = y;
	L->en[L->nen].block = block;
	L->en[L->nen].text = t;
	L->en[L->nen].minor = 0;
	L->nen++;
	if (kind == LE_HEADING) L->nheads++;
}

void lay_free(lay_doc *L)
{
	int i;
	for (i = 0; i < L->nen; i++) free(L->en[i].text);
	free(L->it); free(L->ln); free(L->inset); free(L->t8); free(L->pt.s); free(L->lk); free(L->an); free(L->en);
	memset(L, 0, sizeof *L);
}

static void reset(lay_doc *L, int width)
{
	lay_free(L);
	L->width = width;
	a2_buf_add(&L->pt, "", 0);
}

static long now_ms(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &ts); /* the work itself: a busy build VM preempts wall time */
	return (long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* ---------------------------------------------------------------- the flow */

typedef struct {
	int face, colour, link, flags, shift;
} style;

enum { P_WORD, P_SPACE, P_SHY, P_BOX };

typedef struct {
	style st;
	int kind;
	int off, len;         /* drawn bytes */
	int src, srclen;
	int w;
	int glue;             /* no break before it */
} piece;

typedef struct {
	lay_doc *L;
	const lay_metrics *M;
	int x0, w, align, block;
	int y;                /* the next line's top */
	int pitch;            /* the line pitch of the flow's base face */
	int base_face;
	piece *p;
	int np, cp;
	int cur;              /* the pending pieces' width */
	int lines;            /* lines emitted */
	int indent1;          /* extra indent of the first line only (list markers stand in it) */
	int inset;            /* one-based inset index */
	int size_steps[8], n_size_steps;  /* the size changes on the active inline path */
} flow;

static int sized_face(const flow *f, int face)
{
	int i;
	if (f->M->step_face)
		for (i = 0; i < f->n_size_steps; i++)
			face = f->M->step_face(f->M->ud, lay_face(f->M, face), f->size_steps[i]);
	return face;
}

static void fl_begin(flow *f, lay_doc *L, const lay_metrics *M, int x0, int w, int align, int block, int y, int base_face, int pitch)
{
	memset(f, 0, sizeof *f);
	f->L = L; f->M = M;
	f->x0 = x0; f->w = w > 8 ? w : 8; f->align = align; f->block = block;
	f->y = y;
	f->base_face = base_face;
	f->pitch = pitch;
}

static int line_x0(const flow *f) { return f->x0 + (f->lines ? 0 : f->indent1); }
static int line_w(const flow *f) { return f->w - (f->lines ? 0 : f->indent1); }

/* the pending pieces [0, n) become a line (trailing spaces dropped); a shy break adds its hyphen */
static void fl_emit(flow *f, int n, int hyphen)
{
	lay_doc *L = f->L;
	const lay_metrics *M = f->M;
	int i, asc = fasc(M, f->base_face), desc = fdesc(M, f->base_face), lw = 0, x, top = f->y, base, bot, adv, first;
	int half = (f->pitch - asc - desc) / 2, basea = asc, based = desc;
	lay_line *ln;
	if (half < 0) half = 0;
	while (n > 0 && (f->p[n - 1].kind == P_SPACE || f->p[n - 1].kind == P_SHY) && !hyphen) n--;
	for (i = 0; i < n; i++) {
		const piece *q = &f->p[i];
		int a = fasc(M, q->st.face) + q->st.shift, d = fdesc(M, q->st.face) - q->st.shift;
		if (q->kind == P_SHY) continue;
		/* u14 M7: a run that reaches at most half the leading past the base face (a clamped superscript or subscript, an
		 * italic or bold face a pixel taller) never grows the line: every body line keeps the one pitch */
		if (a > basea && a <= basea + half) a = basea;
		if (d > based && d <= based + half) d = based;
		if (a > asc) asc = a;
		if (d > desc) desc = d;
		lw += q->w;
	}
	if (hyphen) lw += fw(M, f->p[n - 1].st.face, "-", 1);
	x = line_x0(f);
	if (f->align == 1) x += (line_w(f) - lw) / 2;
	else if (f->align == 2) x += line_w(f) - lw;
	base = top + asc;
	bot = base + desc;
	adv = f->pitch;
	if (bot - top + (f->pitch - fasc(M, f->base_face) - fdesc(M, f->base_face)) > adv)
		adv = bot - top + (f->pitch - fasc(M, f->base_face) - fdesc(M, f->base_face));
	first = L->nit;
	for (i = 0; i < n; i++) {
		const piece *q = &f->p[i];
		lay_item *it, *prev = L->nit > first ? &L->it[L->nit - 1] : NULL;
		if (q->kind == P_SHY) {
			if (hyphen && i == n - 1) {
				int off = t8_add(L, "-", 1);
				if ((it = new_item(L, LI_TEXT)) && off >= 0) {
					it->x = x; it->y = base - q->st.shift; it->w = fw(M, q->st.face, "-", 1);
					it->face = q->st.face; it->colour = q->st.colour; it->off = off; it->len = 1;
					it->link = q->st.link; it->block = f->block;
					it->flags = q->st.flags & LI_F_TYPOGRAPHY;
					x += it->w;
				}
			}
			continue;
		}
		/* a word or space joins the previous item when nothing about it differs */
		if (prev && prev->kind == LI_TEXT && q->kind != P_BOX && prev->face == q->st.face && prev->colour == q->st.colour &&
		    (!f->inset || prev->len < 64) &&
		    prev->link == q->st.link && prev->flags == q->st.flags && prev->y == base - q->st.shift &&
		    prev->off + prev->len == q->off && prev->x + prev->w == x &&
		    ((prev->src < 0 && q->src < 0) || (prev->src >= 0 && q->src == prev->src + prev->srclen))) {
			prev->len += q->len;
			prev->w += q->w;
			if (q->src >= 0) prev->srclen += q->srclen;
			x += q->w;
			continue;
		}
		if (!(it = new_item(L, q->kind == P_BOX ? LI_BOX : LI_TEXT))) return;
		it->x = x;
		it->y = base - q->st.shift;
		it->w = q->w;
		it->h = fasc(M, q->st.face) + fdesc(M, q->st.face);
		it->face = q->st.face;
		it->colour = q->st.colour;
		it->off = q->off;
		it->len = q->len;
		it->src = q->src;
		it->srclen = q->src >= 0 ? q->srclen : 0;
		it->link = q->st.link;
		it->flags = q->st.flags;
		it->block = f->block;
		x += q->w;
	}
	ln = (lay_line *)grow(L, L->ln, &L->cln, L->nln + 1, sizeof(lay_line));
	if (ln) {
		L->ln = ln;
		ln = &L->ln[L->nln];
		ln->top = top;
		ln->base = base;
		ln->bot = top + adv;
		ln->x0 = line_x0(f);
		ln->x1 = x;
		ln->first = first;
		ln->n = L->nit - first;
		ln->block = f->block;
		ln->inset = f->inset;
		for (i = first; i < L->nit; i++) L->it[i].line = L->nln;
		L->nln++;
	}
	f->y = top + adv;
	f->lines++;
}

/* drops the first n pending pieces, keeping the rest for the next line */
static void fl_shift(flow *f, int n)
{
	int i;
	if (n > f->np) n = f->np;
	memmove(f->p, f->p + n, (size_t)(f->np - n) * sizeof(piece));
	f->np -= n;
	while (f->np && f->p[0].kind == P_SPACE) { memmove(f->p, f->p + 1, (size_t)(f->np - 1) * sizeof(piece)); f->np--; }
	f->cur = 0;
	for (i = 0; i < f->np; i++) if (f->p[i].kind != P_SHY) f->cur += f->p[i].w;
}

static void fl_append(flow *f, const piece *q)
{
	piece *n = (piece *)grow(f->L, f->p, &f->cp, f->np + 1, sizeof(piece));
	if (!n) return;
	f->p = n;
	f->p[f->np++] = *q;
	if (q->kind != P_SHY) f->cur += q->w;
}

/* splits a word piece wider than the line between characters: the first part fills the room left */
static int fl_split(flow *f, piece *q, int room, piece *rest)
{
	int k, wk = 0;
	const char *s = f->L->t8 + q->off;
	for (k = 1; k < q->len; k++) {
		int w = fw(f->M, q->st.face, s, k + 1);
		if (w > room) break;
		wk = w;
	}
	if (k >= q->len) return 0;
	if (wk == 0) wk = fw(f->M, q->st.face, s, k);
	*rest = *q;
	rest->off = q->off + k;
	rest->len = q->len - k;
	rest->w = fw(f->M, q->st.face, s + k, q->len - k);
	rest->glue = 1;
	q->len = k;
	q->w = wk;
	/* the plain-text span follows the drawn bytes only when they are one byte per character (ASCII words); otherwise
	 * the whole span stays with the first part */
	if (q->src >= 0 && q->srclen == q->len + rest->len) { rest->src = q->src + k; rest->srclen = q->srclen - k; q->srclen = k; }
	else if (q->src >= 0) { rest->src = -1; rest->srclen = 0; }
	return 1;
}

static void fl_push(flow *f, piece q)
{
	for (;;) {
		int bp = -1, i;
		if (q.kind == P_SPACE && f->np == 0) return; /* no space at the start of a line */
		if (q.kind == P_SPACE || q.kind == P_SHY || f->cur + q.w <= line_w(f)) { fl_append(f, &q); return; }
		/* overflow: the last break opportunity before this word */
		for (i = f->np - 1; i >= 0; i--)
			if (f->p[i].kind == P_SPACE || f->p[i].kind == P_SHY) { bp = i; break; }
		if (bp > 0) {
			int shy = f->p[bp].kind == P_SHY;
			fl_emit(f, shy ? bp + 1 : bp, shy);
			fl_shift(f, bp + 1);
			continue;
		}
		if (f->np > 0 && !q.glue) { fl_emit(f, f->np, 0); fl_shift(f, f->np); continue; }
		/* one word wider than the line: split it */
		{
			piece rest;
			int room = line_w(f) - f->cur;
			if (room < 8 && f->np > 0) { fl_emit(f, f->np, 0); fl_shift(f, f->np); continue; }
			if (fl_split(f, &q, room, &rest)) {
				fl_append(f, &q);
				fl_emit(f, f->np, 0);
				fl_shift(f, f->np);
				q = rest;
				continue;
			}
			fl_append(f, &q);
			return;
		}
	}
}

/* a run of text: UTF-8 s (n bytes) shown in style st; src: its offset in the plain text (-1: a decoration). Words are
 * split at spaces; each scalar goes to the text face (t8_map), the Symbol face, or a fallback box. */
static void fl_text(flow *f, const char *s, size_t n, int src, style st)
{
	const lay_metrics *M = f->M;
	lay_doc *L = f->L;
	size_t i = 0;
	piece cur;
	int have = 0, glue_next = f->np > 0 && f->p[f->np - 1].kind != P_SPACE && f->p[f->np - 1].kind != P_SHY;
	int flags = M->t8flags | (M->have[LF_SYMBOL] ? T8_HAVE_SYMBOL : 0), smallcaps = st.flags & 0x100;
	int cap_face = smallcaps ? sized_face(f, LF_SMALL) : LF_SMALL;
	st.flags &= ~0x100;
	st.face = sized_face(f, st.face);
	if (f->n_size_steps) st.flags |= LI_F_RESIZED;
	memset(&cur, 0, sizeof cur);
	while (i < n) {
		uint32_t cp;
		char out[T8_MAXOUT];
		int k = t8_decode(s + i, n - i, &cp), len, kind, face = st.face;
		size_t at = i;
		i += (size_t)k;
		if (cp == ' ') {
			piece sp;
			if (have) { fl_push(f, cur); have = 0; }
			memset(&sp, 0, sizeof sp);
			sp.st = st;
			sp.kind = P_SPACE;
			sp.off = t8_add(L, " ", 1);
			sp.len = 1;
			sp.src = src >= 0 ? src + (int)at : -1;
			sp.srclen = 1;
			sp.w = fw(M, st.face, " ", 1);
			fl_push(f, sp);
			glue_next = 0;
			continue;
		}
		kind = t8_map(cp, flags, out, &len);
		/* small capitals: lower-case letters as capitals in the small face */
		if (kind == T8_LATIN1 && smallcaps && len == 1) {
			unsigned char ch = (unsigned char)out[0];
			if ((ch >= 'a' && ch <= 'z') || (ch >= 0xE0 && ch <= 0xFE && ch != 0xF7)) { out[0] = (char)(ch - 0x20); face = cap_face; }
		}
		if (kind == T8_SYMBOL) face = sized_face(f, LF_SYMBOL);
		if (kind != T8_LATIN1 || !have || cur.st.face != face) {
			if (have) { fl_push(f, cur); glue_next = 1; have = 0; }
		}
		if (kind == T8_NONE) {
			piece bx;
			memset(&bx, 0, sizeof bx);
			bx.st = st;
			bx.kind = P_BOX;
			bx.off = (int)L->nt8;
			bx.len = 0;
			bx.src = src >= 0 ? src + (int)at : -1;
			bx.srclen = k;
			bx.w = fw(M, st.face, "n", 1) + 1;
			bx.glue = glue_next;
			fl_push(f, bx);
			glue_next = 1;
			continue;
		}
		if (!len) {
			/* a character that draws nothing (a combining mark, a zero-width space): its span joins the word */
			if (have && cur.src >= 0) cur.srclen += k;
			continue;
		}
		if (!have) {
			memset(&cur, 0, sizeof cur);
			cur.st = st;
			cur.st.face = face;
			if (kind == T8_SYMBOL) cur.st.flags |= LI_F_SYMBOL;
			cur.kind = P_WORD;
			cur.off = (int)L->nt8;
			cur.src = src >= 0 ? src + (int)at : -1;
			cur.glue = glue_next;
			have = 1;
		}
		t8_add(L, out, len);
		cur.len += len;
		if (cur.src >= 0) cur.srclen += k;
		cur.w = fw(M, cur.st.face, L->t8 + cur.off, cur.len);
		if (kind == T8_SYMBOL) { fl_push(f, cur); have = 0; glue_next = 1; }
	}
	if (have) fl_push(f, cur);
}

/* a discretionary break (a shy run) */
static void fl_shy(flow *f, style st)
{
	piece p;
	memset(&p, 0, sizeof p);
	p.st = st;
	p.st.face = sized_face(f, st.face);
	p.kind = P_SHY;
	p.src = -1;
	p.glue = 1;
	fl_push(f, p);
}

/* ends the flow: the pending line out; returns the y under the last line */
static int fl_end(flow *f)
{
	if (f->np) fl_emit(f, f->np, 0);
	free(f->p);
	f->p = NULL;
	f->np = f->cp = 0;
	return f->y;
}

static void fl_break(flow *f) { if (f->np) { fl_emit(f, f->np, 0); fl_shift(f, f->np); } }

/* ---------------------------------------------------------------- runs */

#define ST_BOLD 1
#define ST_ITALIC 2
#define ST_MONO 4
#define ST_SMALL 8
#define ST_UNDERLINE 16
#define ST_STRIKE 32

static int face_for(int sty, int base)
{
	/* u14 M6: small text (captions, credits, footnotes, the byline) is the one small face: never bold */
	if (base == LF_SMALL) return sty & ST_MONO ? LF_MONO_SMALL : LF_SMALL;
	if (sty & ST_MONO) return LF_MONO;
	if (sty & ST_SMALL) return LF_SMALL;
	if (base == LF_HEAD) return sty & ST_ITALIC ? LF_HEAD_ITALIC : LF_HEAD;
	if (base == LF_SUBHEAD) return sty & ST_ITALIC ? LF_SUBHEAD_ITALIC : LF_SUBHEAD;
	if (base == LF_HEAD3) return LF_HEAD3;
	if ((sty & ST_BOLD) && (sty & ST_ITALIC)) return LF_BOLD_ITALIC;
	if (sty & ST_BOLD) return base == LF_UI ? LF_UI_BOLD : LF_BOLD;
	if (sty & ST_ITALIC) return LF_ITALIC;
	return base;
}

typedef struct {
	flow *f;
	int base;             /* the block's base face */
	int colour;
} runctx;

/* u14 M7: footnote ids with no number of their own ("fn-author"), numbered in the order they first appear; the table
 * lives for one lay_v2 (its names point into the article) */
static struct { const char **name; int n, cap; } fn_seq;

/* "[28]" for the footnote id cite_note-daniels-28 (the digits after its last '-'), else its place in fn_seq */
static void footnote_label(const char *id, char *out, size_t n)
{
	const char *tail = strrchr(id, '-'), *p;
	int i;
	tail = tail ? tail + 1 : id;
	for (p = tail; *p >= '0' && *p <= '9'; p++) ;
	if (p > tail && !*p && p - tail <= 6) { snprintf(out, n, "[%s]", tail); return; }
	for (i = 0; i < fn_seq.n && strcmp(fn_seq.name[i], id); i++) ;
	if (i == fn_seq.n) {
		if (fn_seq.n == fn_seq.cap) {
			int cap = fn_seq.cap ? 2 * fn_seq.cap : 16;
			const char **nn = (const char **)realloc((void *)fn_seq.name, (size_t)cap * sizeof *nn);
			if (!nn) { snprintf(out, n, "[*]"); return; }
			fn_seq.name = nn;
			fn_seq.cap = cap;
		}
		fn_seq.name[fn_seq.n++] = id;
	}
	snprintf(out, n, "[%d]", i + 1);
}

/* u14 M7: a raised (lowered) run sits at most half the leading above the body's ascent (below its descent), so it
 * never grows the line (fl_emit lets it ride in the leading) */
static int shift_clamp(const flow *f, int face, int shift)
{
	const lay_metrics *M = f->M;
	int a = fasc(M, f->base_face), d = fdesc(M, f->base_face), half = (f->pitch - a - d) / 2, lim;
	if (half < 0) half = 0;
	if (shift > 0) {
		lim = a + half - fasc(M, face);
		if (shift > lim) shift = lim > 0 ? lim : 0;
	} else if (shift < 0) {
		lim = d + half - fdesc(M, face);
		if (-shift > lim) shift = lim > 0 ? -lim : 0;
	}
	return shift;
}

/* lays out runs and adds their plain text, in validate_v2.py inline_text's order */
static void lay_runs(runctx *rc, const a2_runs *r, int sty, int link, int shift, int smallcaps, int colour)
{
	lay_doc *L = rc->f->L;
	const lay_metrics *M = rc->f->M;
	int i;
	for (i = 0; i < r->n; i++) {
		const a2_run *x = &r->r[i];
		style st;
		st.face = face_for(sty, rc->base);
		st.colour = colour;
		st.link = link;
		st.flags = smallcaps ? 0x100 : 0;
		if (sty & ST_UNDERLINE) st.flags |= LI_F_UNDERLINE;
		if ((st.face == LF_ITALIC || st.face == LF_BOLD_ITALIC || st.face == LF_BOLD) && !M->have[st.face])
			st.flags |= LI_F_UNDERLINE;
		if (sty & ST_STRIKE) st.flags |= LI_F_STRIKE;
		st.shift = shift;
		switch (x->t) {
		case R_TEXT: {
			size_t n = strlen(x->text);
			int src = pt_add(L, x->text, n);
			fl_text(rc->f, x->text, n, src, st);
			break;
		}
		case R_SHY: fl_shy(rc->f, st); break;
		case R_KEYWORD: {
			int src = pt_adds(L, x->text);
			/* Converter-added RFC keywords retain their capitalization; authored bold is a separate run. */
			fl_text(rc->f, x->text, strlen(x->text), src, st);
			break;
		}
		case R_FOOTREF: {
			/* u14 M7: the marker shows the note's number, "[28]", raised a third of the em in the small face; its
			 * plain text stays the whole "[cite_note-...]" (one generated label over the full span) */
			char t[40];
			piece p;
			int lk = add_link(L, LK_FOOTNOTE, NULL, x->text, NULL);
			footnote_label(x->text, t, sizeof t);
			memset(&p, 0, sizeof p);
			st.face = sized_face(rc->f, LF_SMALL);
			st.shift = shift_clamp(rc->f, st.face, shift + em_px(M, 33));
			st.colour = LC_JUMP;
			st.link = lk;
			st.flags = (st.flags & ~0x100) | LI_F_FORMULA_LINK;
			if (rc->f->n_size_steps) st.flags |= LI_F_RESIZED;
			p.st = st;
			p.kind = P_WORD;
			p.off = t8_add(L, t, (int)strlen(t));
			p.len = (int)strlen(t);
			p.src = pt_adds(L, "[");
			pt_adds(L, x->text);
			pt_adds(L, "]");
			p.srclen = (int)strlen(x->text) + 2;
			p.w = fw(M, st.face, t, p.len);
			p.glue = rc->f->np > 0 && rc->f->p[rc->f->np - 1].kind != P_SPACE && rc->f->p[rc->f->np - 1].kind != P_SHY;
			if (p.off >= 0) fl_push(rc->f, p);
			break;
		}
		case R_LINK: {
			static const char math_prefix[] = "{\\displaystyle";
			static const char tag[] = "[Formula]";
			const char *raw = x->target.kind == T_MEDIA && x->kids.n == 1 &&
			    x->kids.r[0].t == R_TEXT ? x->kids.r[0].text : NULL;
			int jump = add_link(L, LK_TARGET, &x->target, NULL, NULL);
			size_t n = raw ? strlen(raw) : 0;
			if (n > sizeof math_prefix && raw[n - 1] == '}' &&
			    !strncmp(raw, math_prefix, sizeof math_prefix - 1) && raw[sizeof math_prefix - 1] == ' ') {
				piece p = {0};
				st.colour = LC_JUMP;
				st.link = jump;
				st.flags |= LI_F_FORMULA_LINK;
				st.face = sized_face(rc->f, st.face);
				p.st = st;
				p.kind = P_WORD;
				p.off = t8_add(L, tag, sizeof tag - 1);
				p.len = sizeof tag - 1;
				p.src = pt_add(L, raw, n);
				p.srclen = (int)n;
				p.w = fw(M, st.face, tag, p.len);
				if (p.off >= 0) fl_push(rc->f, p);
			} else lay_runs(rc, &x->kids, sty, jump, shift, smallcaps, LC_JUMP);
			break;
		}
		case R_GLOSSARY: lay_runs(rc, &x->kids, sty, add_link(L, LK_GLOSSARY, NULL, x->text, x->definition), shift, smallcaps, LC_JUMP); break;
		case R_BOLD: lay_runs(rc, &x->kids, sty | ST_BOLD, link, shift, smallcaps, colour); break;
		case R_ITALIC: lay_runs(rc, &x->kids, sty | ST_ITALIC, link, shift, smallcaps, colour); break;
		case R_MONO: lay_runs(rc, &x->kids, sty | ST_MONO, link, shift, smallcaps, colour); break;
		/* u14 M5/M7: a third of the em up, 0.15 em down, clamped so the line never grows */
		case R_SUB: lay_runs(rc, &x->kids, sty | ST_SMALL, link, shift_clamp(rc->f, sized_face(rc->f, LF_SMALL), shift - em_px(M, 15)), smallcaps, colour); break;
		case R_SUP: lay_runs(rc, &x->kids, sty | ST_SMALL, link, shift_clamp(rc->f, sized_face(rc->f, LF_SMALL), shift + em_px(M, 33)), smallcaps, colour); break;
		case R_SMALLCAPS: lay_runs(rc, &x->kids, sty, link, shift, 1, colour); break;
		case R_UNDERLINE: lay_runs(rc, &x->kids, sty | ST_UNDERLINE, link, shift, smallcaps, colour); break;
		case R_STRIKE: lay_runs(rc, &x->kids, sty | ST_STRIKE, link, shift, smallcaps, colour); break;
		case R_SMALL: case R_BIG:
			/* The parser caps inline nesting at eight. Apply steps in outer-to-inner order. */
			rc->f->size_steps[rc->f->n_size_steps++] = x->t == R_SMALL ? -1 : 1;
			lay_runs(rc, &x->kids, sty, link, shift, smallcaps, colour);
			rc->f->n_size_steps--;
			break;
		default: break;
		}
	}
}

/* a paragraph-like block: runs in one flow from x0, width w, on the line pitch given; a label (a decoration) before
 * them in its own colour; returns the y under it */
static int para_at(lay_doc *L, const lay_metrics *M, const a2_runs *r, int x0, int w, int y, int block, int base, int sty,
                   int colour, const char *label, int label_colour, int align, int pitch)
{
	flow f;
	runctx rc;
	fl_begin(&f, L, M, x0, w, align, block, y, base, pitch);
	rc.f = &f;
	rc.base = base;
	rc.colour = colour;
	if (label) {
		style st = { face_for(ST_BOLD, base), label_colour, -1, 0, 0 };
		fl_text(&f, label, strlen(label), -1, st);
	}
	lay_runs(&rc, r, sty, -1, 0, 0, colour);
	return fl_end(&f);
}

static int para(lay_doc *L, const lay_metrics *M, const a2_runs *r, int x0, int w, int y, int block, int base, int sty, int colour,
                const char *label, int align)
{
	return para_at(L, M, r, x0, w, y, block, base, sty, colour, label, colour, align, M->pitch);
}

/* u14: the plain text of runs or a block the layout leaves undrawn (lay_hidden): offsets stay the reader's */
static void pt_runs(lay_doc *L, const a2_runs *r) { if (a2_runs_text(r, &L->pt)) L->oom = 1; }

/* plain text of runs as 8-bit interface text (outline entries) */
static void runs_8bit(const lay_metrics *M, const a2_runs *r, char *out, size_t n)
{
	a2_buf b;
	memset(&b, 0, sizeof b);
	if (a2_runs_text(r, &b) || !b.s) { out[0] = 0; free(b.s); return; }
	t8_to_8bit(b.s, b.n, M->t8flags, out, n);
	free(b.s);
}

/* ---------------------------------------------------------------- preformatted text and code */

static int cli_prompt(const char *text)
{
	size_t start = strspn(text, " \t");
	size_t n = strspn(text + start, "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:/-");
	if (!n || !strchr("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789", text[start])) return 0;
	text += start + n;
	if (*text == '(') {
		if (strncmp(text, "(config", 7)) return 0;
		text += 7;
		text += strspn(text, "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./-");
		if (*text++ != ')') return 0;
	}
	return *text == '#' || *text == '>';
}

/* Fold a physical line visually. Source spans, trailing spaces and tab stops
 * stay attached to their original bytes, including at continuation points. */
static int fold_cli_line(flow *f, const char *text, int src, int x, int w, int lh, int striped)
{
	piece *pieces = f->p;
	int count = f->np, left = f->x0, at = 0, widest = 0;
	while (at < count) {
		int indent = at && w >= 48 ? 8 : 0;
		int room = x + w - 6 - left - indent;
		int end = at, used = 0, last_space = at, space_width = 0;
		while (end < count && (end == at || used + pieces[end].w <= room)) {
			const piece *q = &pieces[end++];
			char ch = text[q->src - src];
			used += q->w;
			if (ch == ' ' || ch == '\t') { last_space = end; space_width = used; }
		}
		if (end < count && last_space > at && space_width >= room / 2) {
			end = last_space;
			used = space_width;
		}
		if (at && striped) shape(f->L, LI_FILL, x + 1, f->y, w - 2, lh, LC_GROUND, f->block);
		if (at && w >= 32) shape(f->L, LI_HLINE, x + 3, f->y + lh / 2, 4, 1, LC_QUIET, f->block);
		f->x0 = left + indent;
		f->p = pieces + at;
		fl_emit(f, end - at, 0);
		if (used + f->x0 - (x + 6) > widest) widest = used + f->x0 - (x + 6);
		at = end;
	}
	free(pieces);
	return widest;
}

/* the bytes of a blank character at t[q] (space, tab, carriage return, no-break space), 0 for anything else */
static size_t code_blank(const char *t, size_t q, size_t n)
{
	if (t[q] == ' ' || t[q] == '\t' || t[q] == '\r') return 1;
	return q + 1 < n && (unsigned char)t[q] == 0xC2 && (unsigned char)t[q + 1] == 0xA0 ? 2 : 0;
}

/* u14: blank first and last lines take no row; their bytes stay in the copy text. [*start, *end) narrows to the
 * lines from the first to the last one holding anything else. */
static void code_trim(const char *t, size_t *start, size_t *end)
{
	size_t p = *start, n = *end, q, k;
	for (;;) {
		for (q = p; q < n && (k = code_blank(t, q, n)); q += k) ;
		if (q >= n) { p = n; break; }
		if (t[q] != '\n') break;
		p = q + 1;
	}
	while (n > p) {
		size_t s = n;
		while (s > p && t[s - 1] != '\n') s--;
		for (q = s; q < n && (k = code_blank(t, q, n)); q += k) ;
		if (q < n) break;
		n = s > p ? s - 1 : p;
	}
	*start = p;
	*end = n;
}

/* the first line holding more than blanks, from its first non-blank character (NULL: none) */
static const char *code_first_line(const char *t, size_t *len)
{
	size_t s = 0, e = strlen(t), k, q;
	code_trim(t, &s, &e);
	if (s >= e) return NULL;
	for (q = s; q < e && (k = code_blank(t, q, e)); q += k) ;
	*len = strcspn(t + q, "\n");
	if (*len > e - q) *len = e - q;
	return t + q;
}

/* the widest line in character cells: one per scalar, tabs to the next multiple of 8 */
static int code_cols(const char *t)
{
	int col = 0, widest = 0;
	for (; *t; t++) {
		if (*t == '\n' || *t == '\f') { col = 0; continue; }
		if (*t == '\t') col = (col / 8 + 1) * 8;
		else if (((unsigned char)*t & 0xC0) != 0x80) col++;
		if (col > widest) widest = col;
	}
	return widest;
}

/* u14: one box (one frame, one horizontal inset) holds a run of consecutive code and preformatted blocks: Cisco's
 * configurations arrive one line per block. The stripes continue from block to block; each block keeps its own
 * plain-text spans, so copy, search and selection are unchanged. */
typedef struct {
	int x0, w, top, inset, stripe, widest, cli, rows, lh, gutter;
	int blank;            /* a blank block came after the last row: one blank row before the next row, if any */
} code_box;

static int box_open(lay_doc *L, const lay_metrics *M, code_box *bx, int x0, int w, int y, int cli)
{
	lay_inset *insets = grow(L, L->inset, &L->cinset, L->ninset + 1, sizeof *insets);
	memset(bx, 0, sizeof *bx);
	bx->x0 = x0; bx->w = w; bx->top = y; bx->cli = cli;
	bx->lh = fasc(M, LF_MONO) + fdesc(M, LF_MONO) + 1;
	bx->gutter = cli && w >= 32 ? 6 : 0;
	if (insets) { L->inset = insets; bx->inset = ++L->ninset; }
	return y + 3;
}

static int box_close(lay_doc *L, code_box *bx, int y, int block)
{
	int range;
	if (!bx->rows) y += bx->lh; /* nothing but blank lines: the box keeps one empty row */
	y += 3;
	range = bx->widest > bx->w - 12 ? bx->widest - (bx->w - 12) : 0;
	if (range) y += LAY_INSET_TRACK;
	if (bx->inset) L->inset[bx->inset - 1] = (lay_inset){ bx->x0, bx->top, bx->w, y - bx->top, range, 0, block };
	shape(L, LI_FRAME, bx->x0, bx->top, bx->w, y - bx->top, LC_RULE, block);
	return y;
}

/* One block's source lines into the open box, keeping lines and tab stops intact. Cisco CLI folds a line wider than
 * the box at a space where it can; other preformatted material (fixed-width diagrams, tables) pans in the inset. */
static int box_rows(lay_doc *L, const lay_metrics *M, code_box *bx, const char *text, int src, int y, int block,
                    const int *var, int nvar)
{
	int face = LF_MONO, lh = bx->lh, col, x0 = bx->x0, w = bx->w, gutter = bx->gutter;
	size_t p = 0, n = strlen(text), k;
	long scal = 0;
	code_trim(text, &p, &n);
	/* A block of nothing but blank lines is the source's blank line between two stanzas of a configuration split
	 * one line per block: one blank row, and only between rows (never a box's first or last). */
	if (p >= n) { bx->blank = bx->rows > 0; return y; }
	if (bx->blank) {
		/* u14 (readability spec section 5): only Cisco CLI boxes are striped; RFC text and diagrams are not */
		if ((bx->stripe++ & 1) && bx->cli) shape(L, LI_FILL, x0 + 1, y, w - 2, lh, LC_GROUND, block);
		y += lh;
		bx->rows++;
		bx->blank = 0;
	}
	for (k = 0; k < p; k++) if (((unsigned char)text[k] & 0xC0) != 0x80) scal++; /* the skipped lines' scalars */
	while (p < n) {
		flow f;
		style st = { face, LC_TEXT, -1, 0, 0 };
		int vi = 0, line_width, striped;
		if (text[p] == '\f') {
			int before = L->nit;
			shape(L, LI_HLINE, x0, y + lh / 2, w, 1, LC_RULE, block);
			if (L->nit > before) L->it[before].flags |= LI_F_DASH;
			y += lh;
			p++;
			scal++;
			bx->rows++;
			continue;
		}
		striped = (bx->stripe++ & 1) && bx->cli;
		if (striped) shape(L, LI_FILL, x0 + 1, y, w - 2, lh, LC_GROUND, block);
		if (gutter && cli_prompt(text + p)) shape(L, LI_FILL, x0 + 4, y + 2, 2, lh - 3, LC_QUIET, block);
		fl_begin(&f, L, M, x0 + 6 + gutter, 1 << 29, 0, block, y, face, lh);
		f.inset = bx->inset;
		col = 0;
		while (p < n && text[p] != '\n' && text[p] != '\f') {
			uint32_t cp;
			int k = t8_decode(text + p, n - p, &cp), under = 0;
			for (vi = 0; vi < nvar; vi++) if (scal >= var[2 * vi] && scal < var[2 * vi + 1]) under = 1;
			st.flags = under ? LI_F_UNDER : 0;
			st.face = face;
			if (cp == '\t') {
				int to = (col / 8 + 1) * 8;
				piece sp;
				char spaces[8] = { ' ', ' ', ' ', ' ', ' ', ' ', ' ', ' ' };
				memset(&sp, 0, sizeof sp);
				sp.st = st;
				sp.st.flags |= LI_F_TAB;
				sp.kind = P_WORD;
				sp.off = t8_add(L, spaces, to - col);
				sp.len = to - col;
				sp.src = src + (int)p;
				sp.srclen = 1;
				sp.w = fw(M, face, spaces, to - col);
				sp.glue = 1;
				fl_push(&f, sp);
				col = to;
			} else {
				/* Glued pieces keep the original source offsets, spaces, and tab stops. */
				char out[T8_MAXOUT];
				int len, kind = t8_map(cp, M->t8flags | (M->have[LF_SYMBOL] ? T8_HAVE_SYMBOL : 0), out, &len);
				piece c;
				memset(&c, 0, sizeof c);
				c.st = st;
				c.glue = 1;
				c.src = src + (int)p;
				c.srclen = k;
				if (kind == T8_NONE) { c.kind = P_BOX; c.off = (int)L->nt8; c.w = fw(M, face, "n", 1); }
				else {
					if (kind == T8_SYMBOL) { c.st.face = LF_SYMBOL; c.st.flags |= LI_F_SYMBOL; }
					c.kind = P_WORD;
					c.off = t8_add(L, out, len);
					c.len = len;
					c.w = fw(M, c.st.face, out, len);
				}
				fl_append(&f, &c);
				col++;
			}
			p += (size_t)k;
			scal++;
		}
		line_width = f.cur + gutter;
		if (bx->cli && f.np && line_width > w - 12) line_width = fold_cli_line(&f, text, src, x0, w, lh, striped);
		else if (f.np) fl_end(&f);
		else { free(f.p); f.y += lh; }
		if (line_width > bx->widest) bx->widest = line_width;
		y = f.y;
		bx->rows++;
		if (p < n && text[p] == '\n') { p++; scal++; }
	}
	return y;
}

/* a box of its own (a disclosed formula's source) */
static int pre_block(lay_doc *L, const lay_metrics *M, const char *text, int src, int x0, int w, int y, int block,
                     const int *var, int nvar, int cli)
{
	code_box bx;
	y = box_open(L, M, &bx, x0, w, y, cli);
	y = box_rows(L, M, &bx, text, src, y, block, var, nvar);
	return box_close(L, &bx, y, block);
}

static int code_block(const a2_block *b) { return b->kind == A2_CODE || b->kind == A2_PRE; }
static int code_language(const a2_block *b, const char *l) { return b->language && !strcmp(b->language, l); }

/* 1: Cisco CLI (a line wider than the box folds); 0: other code and fixed-width diagrams (the box pans) */
static int code_mode(const a2_block *b, int cisco)
{
	size_t n;
	const char *first;
	if (b->kind == A2_CODE) return cisco || code_language(b, "ios") || code_language(b, "cli");
	first = code_first_line(b->text, &n);
	return cisco && first && cli_prompt(first);
}

/* Cisco's own documents: provenance cisco, or an https URL on cisco.com, a subdomain of it, or DevNet's pubhub */
static int cisco_source(const a2_doc *d)
{
	const char *h;
	size_t n;
	if (d->source_kind && !strcmp(d->source_kind, "cisco")) return 1;
	if (!d->source_url || strncmp(d->source_url, "https://", 8)) return 0;
	h = d->source_url + 8;
	n = strcspn(h, "/:?#");
	return (n == 9 && !strncmp(h, "cisco.com", 9)) || (n > 10 && !strncmp(h + n - 10, ".cisco.com", 10)) ||
	       (n == 22 && !strncmp(h, "pubhub.devnetcloud.com", 22));
}

/* ---------------------------------------------------------------- lists */

static int list_block(lay_doc *L, const lay_metrics *M, const a2_list *l, int depth, int x0, int w, int y, int block, int *first)
{
	int i, j, d;
	for (i = 0; i < l->nitems; i++) {
		char num[24];
		int msrc, ind = indent_px(M), ytop;
		flow f;
		runctx rc;
		style st = { LF_BODY, LC_TEXT, -1, 0, 0 };
		if (!*first) pt_adds(L, "\n");
		*first = 0;
		for (d = 0; d < depth; d++) pt_adds(L, "  ");
		if (l->ordered) snprintf(num, sizeof num, "%ld. ", l->start + i);
		else snprintf(num, sizeof num, "* ");
		msrc = pt_adds(L, num);
		ytop = y;
		fl_begin(&f, L, M, x0 + ind, w - ind, 0, block, y, LF_BODY, M->pitch);
		rc.f = &f;
		rc.base = LF_BODY;
		rc.colour = LC_TEXT;
		lay_runs(&rc, &l->items[i].runs, 0, -1, 0, 0, LC_TEXT);
		y = fl_end(&f);
		/* the marker in the indent: "3." right-aligned before the text, or a bullet */
		if (L->nln) {
			const lay_line *ln = &L->ln[L->nln - 1];
			int base = ytop + fasc(M, LF_BODY);
			(void)ln;
			if (l->ordered) {
				lay_item *it;
				int len = (int)strlen(num) - 1, off = t8_add(L, num, len);
				if ((it = new_item(L, LI_TEXT)) && off >= 0) {
					it->w = fw(M, LF_BODY, num, len);
					it->x = x0 + ind - em_px(M, 40) - it->w;
					it->y = base;
					it->face = LF_BODY;
					it->colour = LC_TEXT;
					it->off = off;
					it->len = len;
					it->src = msrc;
					it->srclen = len;
					it->block = block;
				}
			} else {
				/* u14 M5: square bullets: filled 0.3 em, hollow at the second level, a smaller filled one at the
				 * third; centred 0.35 em above the baseline, 0.6 em before the text */
				int lvl = depth % 3, size = em_px(M, lvl == 2 ? 22 : 30), cy = base - em_px(M, 35);
				if (size < 3) size = 3;
				shape(L, lvl == 1 ? LI_FRAME : LI_FILL, x0 + ind - em_px(M, 60) - size, cy - size / 2, size, size, LC_TEXT, block);
			}
		}
		(void)st;
		y += item_gap(M);
		for (j = 0; j < l->items[i].nkids; j++) y = list_block(L, M, &l->items[i].kids[j], depth + 1, x0 + ind, w - ind, y, block, first);
	}
	return y;
}

/* ---------------------------------------------------------------- tables */

typedef struct {
	int min, max;         /* the widest word, the whole text on one line */
} colw;

/* u14 S2: a cell's widths measured in the faces its runs are drawn in (walked as lay_runs walks them): bold and mono
 * cells are measured bold and mono, footnote markers as their "[n]" */
typedef struct {
	const lay_metrics *M;
	int word, line, mn;
} cellw;

static void cw_text(cellw *c, const char *s, size_t n, int face)
{
	size_t i = 0;
	char out[T8_MAXOUT];
	while (i < n) {
		uint32_t cp;
		int k = t8_decode(s + i, n - i, &cp), len = 0;
		if (cp == ' ') {
			if (c->word > c->mn) c->mn = c->word;
			c->line += c->word + fw(c->M, face, " ", 1);
			c->word = 0;
		} else {
			if (t8_map(cp, c->M->t8flags, out, &len) == T8_NONE || !len) { out[0] = 'n'; len = 1; }
			c->word += fw(c->M, face, out, len);
		}
		i += (size_t)k;
	}
}

static void cw_runs(cellw *c, const a2_runs *r, int sty, int base)
{
	int i;
	for (i = 0; i < r->n; i++) {
		const a2_run *x = &r->r[i];
		switch (x->t) {
		case R_TEXT: case R_KEYWORD: cw_text(c, x->text, strlen(x->text), face_for(sty, base)); break;
		case R_FOOTREF: {
			char t[40];
			footnote_label(x->text, t, sizeof t);
			cw_text(c, t, strlen(t), LF_SMALL);
			break;
		}
		case R_BOLD: cw_runs(c, &x->kids, sty | ST_BOLD, base); break;
		case R_ITALIC: cw_runs(c, &x->kids, sty | ST_ITALIC, base); break;
		case R_MONO: cw_runs(c, &x->kids, sty | ST_MONO, base); break;
		case R_SUB: case R_SUP: cw_runs(c, &x->kids, sty | ST_SMALL, base); break;
		case R_SHY: break;
		default: cw_runs(c, &x->kids, sty, base); break;
		}
	}
}

static void runs_widths(const lay_metrics *M, const a2_runs *r, int bold, int *mn, int *mx)
{
	cellw c;
	int cap = em_px(M, 1230);
	memset(&c, 0, sizeof c);
	c.M = M;
	cw_runs(&c, r, bold ? ST_BOLD : 0, LF_BODY);
	if (c.word > c.mn) c.mn = c.word;
	*mx = c.line + c.word;
	*mn = c.mn > cap ? cap : c.mn; /* a long token (a URL) may break inside: 160 px at the 13 px body */
}

static int cell_colour(enum a2_shade shade)
{
	switch (shade) {
	case SHADE_YES: return LC_SHADE_YES;
	case SHADE_NO: return LC_SHADE_NO;
	case SHADE_PARTIAL: return LC_SHADE_PARTIAL;
	case SHADE_HIGHLIGHT: return LC_SHADE_HIGHLIGHT;
	case SHADE_NEUTRAL: case SHADE_HEADER: return LC_BAND;
	default: return -1;
	}
}

/* A table: columns from their cells' widest words and whole lines (measured in their own faces), at the reading
 * measure when they fit it; u14 S2: a table that does not fit grows into the reader pane up to 1.6 times the measure
 * (placed the way code boxes are: on the column's left edge while the pane has room on the right, else moved left)
 * before it falls back to the row-by-row view. Cell padding 0.35 em by 0.5 em, 1-px rules, the header tinted, and
 * every other row tinted in tables longer than 8 rows. */
static int table_block(lay_doc *L, const lay_metrics *M, const a2_block *b, int x0, int w, int pane, int y, int block)
{
	int padh = em_px(M, 50), padv = em_px(M, 35), lead = M->pitch - fasc(M, LF_BODY) - fdesc(M, LF_BODY);
	int c, r, i, total_min = 0, total_max = 0, *cw, *cx, *rh, *ry, chrome = 1 + b->ncols * (2 * padh + 1);
	int avail = w - chrome, budget = (w * 16 + 5) / 10, avail_max, target;
	colw *col;
	struct cellpos { int first_item, last_item, first_line, last_line, h; } *cp;
	if (b->ncols <= 0 || b->nrows <= 0) return y;
	if (lead < 0) lead = 0;
	if (budget > pane) budget = pane;
	if (budget < w) budget = w;
	avail_max = budget - chrome;
	col = (colw *)calloc((size_t)b->ncols, sizeof(colw));
	cw = (int *)calloc((size_t)b->ncols + 1, sizeof(int));
	cx = (int *)calloc((size_t)b->ncols + 1, sizeof(int));
	rh = (int *)calloc((size_t)b->nrows + 1, sizeof(int));
	ry = (int *)calloc((size_t)b->nrows + 1, sizeof(int));
	cp = (struct cellpos *)calloc((size_t)b->ncells + 1, sizeof(*cp));
	if (!col || !cw || !cx || !rh || !ry || !cp) { L->oom = 1; goto out; }
	/* column widths: single-column cells first, then spans widen their columns evenly */
	for (i = 0; i < b->ncells; i++) {
		const a2_cell *ce = &b->cells[i];
		int mn, mx;
		runs_widths(M, &ce->runs, ce->row < b->header_rows, &mn, &mx);
		if (ce->colspan == 1) {
			if (mn > col[ce->col].min) col[ce->col].min = mn;
			if (mx > col[ce->col].max) col[ce->col].max = mx;
		}
	}
	for (i = 0; i < b->ncells; i++) {
		const a2_cell *ce = &b->cells[i];
		int mn, mx, have_min = 0, have_max = 0;
		if (ce->colspan == 1) continue;
		runs_widths(M, &ce->runs, ce->row < b->header_rows, &mn, &mx);
		for (c = ce->col; c < ce->col + ce->colspan; c++) { have_min += col[c].min; have_max += col[c].max; }
		for (c = ce->col; c < ce->col + ce->colspan; c++) {
			if (mn > have_min) col[c].min += (mn - have_min) / ce->colspan + 1;
			if (mx > have_max) col[c].max += (mx - have_max) / ce->colspan + 1;
		}
	}
	for (c = 0; c < b->ncols; c++) {
		if (col[c].max < col[c].min) col[c].max = col[c].min;
		if (col[c].min < 8) col[c].min = 8;
		if (col[c].max < 8) col[c].max = 8;
		total_min += col[c].min;
		total_max += col[c].max;
	}
	if (total_min > avail_max) {
		/* the narrow fallback (ARTICLE-FORMAT-2): each row as labelled cell text */
		int hdr = b->header_rows > 0;
		int rr = -1;
		for (i = 0; i < b->ncells; i++) {
			const a2_cell *ce = &b->cells[i];
			char label[200];
			label[0] = 0;
			if (ce->row != rr) {
				while (rr < ce->row) { if (rr >= 0) pt_adds(L, "\n"); rr++; }
				if (i) y += item_gap(M);
			} else pt_adds(L, " | ");
			if (hdr && ce->row >= b->header_rows) {
				int k;
				for (k = 0; k < b->ncells; k++)
					if (b->cells[k].row == 0 && b->cells[k].col <= ce->col && ce->col < b->cells[k].col + b->cells[k].colspan) {
						runs_8bit(M, &b->cells[k].runs, label, sizeof label - 2);
						strcat(label, ": ");
						break;
					}
			}
			{
				int indent = ce->col ? indent_px(M) : 0, top = y, colour = cell_colour(ce->shade);
				y = para(L, M, &ce->runs, x0 + indent, w - indent, y, block, LF_BODY,
				         ce->row < b->header_rows ? ST_BOLD : 0, LC_TEXT, label[0] ? label : NULL, 0);
				if (colour >= 0 && w > indent && y > top)
					shape(L, LI_FILL, x0 + indent, top, w - indent, y - top, colour, block);
			}
		}
		while (rr < b->nrows - 1) { pt_adds(L, "\n"); rr++; }
		{
			/* u14 M6: the notice in the small body face, quiet */
			style st = { LF_SMALL, LC_QUIET, -1, 0, 0 };
			flow f;
			const char *m = "(The table is shown row by row; Open original shows it whole.)";
			fl_begin(&f, L, M, x0, w, 0, block, y + 2, LF_SMALL, small_pitch(M));
			fl_text(&f, m, strlen(m), -1, st);
			y = fl_end(&f);
		}
		goto out;
	}
	target = total_max <= avail ? total_max : total_max < avail_max ? total_max : avail_max;
	{
		/* short columns (their whole text within 4 em of their widest word: "Step 1") get their whole text first; the
		 * rest share what is left by how much more they would take */
		int fixed = 0, flex_min = 0, flex_max = 0, slack = em_px(M, 400), share;
		for (c = 0; c < b->ncols; c++) {
			if (col[c].max - col[c].min <= slack) fixed += col[c].max;
			else { flex_min += col[c].min; flex_max += col[c].max; }
		}
		if (total_max <= target || fixed + flex_min > target || flex_max <= flex_min) {
			fixed = 0; flex_min = total_min; flex_max = total_max; slack = -1;
		}
		share = target - fixed - flex_min;
		for (c = 0; c < b->ncols; c++) {
			if (total_max <= target || col[c].max - col[c].min <= slack) cw[c] = col[c].max;
			else cw[c] = col[c].min + (int)((long)share * (col[c].max - col[c].min) / (flex_max - flex_min > 0 ? flex_max - flex_min : 1));
		}
	}
	{
		/* wider than the measure: on its left edge while the pane has room on the right, else moved left */
		int tw = chrome, left = (pane - w) / 2;
		for (c = 0; c < b->ncols; c++) tw += cw[c];
		if (tw > w && tw > pane - left) x0 += pane - left - tw;
	}
	cx[0] = x0;
	for (c = 0; c < b->ncols; c++) cx[c + 1] = cx[c] + cw[c] + 2 * padh + 1;
	/* the cells at y 0, measured; rows take their tallest single-row cell, spans stretch their last row */
	{
		int rr = -1;
		for (i = 0; i < b->ncells; i++) {
			const a2_cell *ce = &b->cells[i];
			int x = cx[ce->col] + 1 + padh, wdt = cx[ce->col + ce->colspan] - cx[ce->col] - 1 - 2 * padh, yy;
			if (ce->row != rr) {
				while (rr < ce->row) { if (rr >= 0) pt_adds(L, "\n"); rr++; }
			} else pt_adds(L, " | ");
			cp[i].first_item = L->nit;
			cp[i].first_line = L->nln;
			yy = para(L, M, &ce->runs, x, wdt, 0, block, LF_BODY, ce->row < b->header_rows ? ST_BOLD : 0, LC_TEXT, NULL,
			          b->align[ce->col]);
			cp[i].last_item = L->nit;
			cp[i].last_line = L->nln;
			cp[i].h = yy + 2 * padv - lead;
			if (ce->rowspan == 1 && cp[i].h > rh[ce->row]) rh[ce->row] = cp[i].h;
		}
		while (rr < b->nrows - 1) { pt_adds(L, "\n"); rr++; }
	}
	for (r = 0; r < b->nrows; r++) if (rh[r] < M->pitch) rh[r] = M->pitch;
	for (i = 0; i < b->ncells; i++) {
		const a2_cell *ce = &b->cells[i];
		int have = 0;
		if (ce->rowspan == 1) continue;
		for (r = ce->row; r < ce->row + ce->rowspan; r++) have += rh[r] + 1;
		if (cp[i].h > have - 1) rh[ce->row + ce->rowspan - 1] += cp[i].h - (have - 1);
	}
	ry[0] = y;
	for (r = 0; r < b->nrows; r++) ry[r + 1] = ry[r] + rh[r] + 1;
	/* every other body row tinted in a long table (drawn under the cells' own shades and the rules) */
	if (b->nrows > 8)
		for (r = b->header_rows + 1; r < b->nrows; r += 2)
			shape(L, LI_FILL, cx[0] + 1, ry[r] + 1, cx[b->ncols] - cx[0] - 1, ry[r + 1] - ry[r] - 1, LC_GROUND, block);
	/* move each cell's items and lines into place; header grounds and the rules */
	for (i = 0; i < b->ncells; i++) {
		const a2_cell *ce = &b->cells[i];
		int k, dy = ry[ce->row] + 1 + padv, colour = cell_colour(ce->shade);
		for (k = cp[i].first_item; k < cp[i].last_item; k++) L->it[k].y += dy;
		for (k = cp[i].first_line; k < cp[i].last_line; k++) { L->ln[k].top += dy; L->ln[k].base += dy; L->ln[k].bot += dy; }
		if (colour < 0 && ce->row < b->header_rows) colour = LC_BAND;
		if (colour >= 0)
			shape(L, LI_FILL, cx[ce->col] + 1, ry[ce->row] + 1, cx[ce->col + ce->colspan] - cx[ce->col] - 1,
			      ry[ce->row + ce->rowspan] - ry[ce->row] - 1, colour, block);
		/* each cell's right and bottom rules */
		shape(L, LI_FILL, cx[ce->col + ce->colspan], ry[ce->row], 1, ry[ce->row + ce->rowspan] - ry[ce->row] + 1, LC_RULE, block);
		shape(L, LI_FILL, cx[ce->col], ry[ce->row + ce->rowspan], cx[ce->col + ce->colspan] - cx[ce->col] + 1, 1, LC_RULE, block);
	}
	shape(L, LI_FILL, x0, y, cx[b->ncols] - x0 + 1, 1, LC_RULE, block);
	shape(L, LI_FILL, x0, y, 1, ry[b->nrows] - y + 1, LC_RULE, block);
	y = ry[b->nrows] + 1;
out:
	free(col); free(cw); free(cx); free(rh); free(ry); free(cp);
	return y;
}

/* ---------------------------------------------------------------- boxes */

static int media_index(const a2_doc *d, const char *id)
{
	int i;
	for (i = 0; i < d->nmedia; i++) if (!strcmp(d->media[i].id, id)) return i;
	return -1;
}

static int wiki_source(const a2_doc *d)
{
	return (d->source_kind && !strcmp(d->source_kind, "wiki")) || (d->id && !strncmp(d->id, "wiki:", 5));
}

/* u14 M8: a figure's box (its 1-px frame included) in a column w px wide. A raster picture shows at its own size, one
 * source pixel to one device pixel (media_size), never enlarged, scaled down (area-averaged by IMAGE) to the column at
 * most; a drawing (SVG, no pixel size) is 18 em wide at most and 0.6 as tall. Until IMAGE has reported a picture's
 * size: Wikipedia's 250 x 180 thumbnail box, else the column at 16:9 (Cisco, NIST and RFC artwork fills it). */
static void figure_size(const lay_metrics *M, const a2_doc *d, const char *id, int w, int *bw, int *bh)
{
	int mi = media_index(d, id), nw = 0, nh = 0;
	const char *mime = mi >= 0 ? d->media[mi].mime : NULL;
	if (w < 8) w = 8;
	if (mime && !strcmp(mime, "image/svg+xml")) {
		nw = em_px(M, 1800);
		*bw = nw < w ? nw : w;
		*bh = (*bw * 3 + 2) / 5;
	} else if (M->media_size && M->media_size(M->ud, id, &nw, &nh) && nw > 0 && nh > 0) {
		if (nw + 2 > w) { nh = (int)((long)nh * (w - 2) / nw); nw = w - 2; }
		*bw = nw + 2;
		*bh = (nh > 0 ? nh : 1) + 2;
	} else if (wiki_source(d)) {
		*bw = w < 250 ? w : 250;
		*bh = 180;
	} else {
		*bw = w;
		*bh = (w * 9 + 8) / 16;
	}
}

/* a picture's box mw x mh at x; its alternative text inside (the small body face) until IMAGE draws the picture */
static int media_box(lay_doc *L, const lay_metrics *M, const a2_doc *d, const char *id, int x, int mw, int mh, int y, int block,
                     const char *inside, int inside_src)
{
	int mi = media_index(d, id), i;
	lay_item *it;
	if (!(it = new_item(L, LI_MEDIA))) return y;
	it->x = x; it->y = y; it->w = mw; it->h = mh;
	it->colour = LC_RULE;
	it->media = mi;
	it->block = block;
	add_anchor(L, LA_MEDIA, id, y, block);
	{
		/* the stand-in text, centred in the box */
		flow f;
		style st = { inside_src >= 0 ? LF_MONO : LF_SMALL, LC_QUIET, -1, LI_F_ALT, 0 };
		int first = L->nit, firstln = L->nln, hh, dy;
		fl_begin(&f, L, M, x + 4, mw - 8, 1, block, 0, st.face, fasc(M, st.face) + fdesc(M, st.face) + 1);
		fl_text(&f, inside, strlen(inside), inside_src, st);
		hh = fl_end(&f);
		if (inside_src >= 0 && hh + 8 > mh) { mh = hh + 8; L->it[first - 1].h = mh; }
		dy = y + (mh - hh) / 2;
		for (i = first; i < L->nit; i++) L->it[i].y += dy;
		for (i = firstln; i < L->nln; i++) { L->ln[i].top += dy; L->ln[i].base += dy; L->ln[i].bot += dy; }
	}
	return y + mh;
}

static int formula_button(lay_doc *L, const lay_metrics *M, const char *label,
    int x, int y, int width, int block, int media, int src, int srclen)
{
	style text = { LF_MONO_SMALL, LC_JUMP, -1, 0, 0 };
	flow f;
	int pitch = fasc(M, text.face) + fdesc(M, text.face) + 2;
	int button = fw(M, text.face, label, (int)strlen(label)) + 12;
	int item = L->nit;
	if (button > width) button = width;
	shape(L, LI_FORMULA, x, y, button, pitch + 4, LC_RULE, block);
	if (L->nit > item) {
		L->it[item].media = media;
		L->it[item].src = src;
		L->it[item].srclen = srclen;
	}
	fl_begin(&f, L, M, x + 6, button - 12, 0, block, y + 2, text.face, pitch);
	fl_text(&f, label, strlen(label), -1, text);
	(void)fl_end(&f);
	return y + pitch + 4;
}

/* a fact box: a caption bar with its title, then its rows (key in bold, the value after it), framed */
static int fact_block(lay_doc *L, const lay_metrics *M, const a2_block *b, int x0, int w, int y, int block)
{
	int i, top = y, bar = M->pitch + 2;
	flow f;
	style st = { LF_BOLD, LC_BARTEXT, -1, 0, 0 };
	int src = pt_adds(L, b->title);
	pt_adds(L, "\n");
	shape(L, LI_FILL, x0, y, w, bar, LC_BAR, block);
	fl_begin(&f, L, M, x0 + 6, w - 12, 0, block, y + 1, LF_BOLD, M->pitch);
	fl_text(&f, b->title, strlen(b->title), src, st);
	y = fl_end(&f) + 3;
	if (y < top + bar + 3) y = top + bar + 3;
	for (i = 0; i < b->nfacts; i++) {
		runctx rc;
		style ks = { LF_BOLD, LC_TEXT, -1, 0, 0 };
		int ksrc;
		if (i) pt_adds(L, "\n");
		ksrc = pt_adds(L, b->facts[i].key);
		pt_adds(L, ": ");
		fl_begin(&f, L, M, x0 + 6, w - 12, 0, block, y, LF_BODY, M->pitch);
		fl_text(&f, b->facts[i].key, strlen(b->facts[i].key), ksrc, ks);
		{
			style sep = { LF_BOLD, LC_TEXT, -1, 0, 0 };
			fl_text(&f, ": ", 2, ksrc + (int)strlen(b->facts[i].key), sep);
		}
		rc.f = &f;
		rc.base = LF_BODY;
		rc.colour = LC_TEXT;
		lay_runs(&rc, &b->facts[i].runs, 0, -1, 0, 0, LC_TEXT);
		y = fl_end(&f) + 1;
	}
	y += 3;
	shape(L, LI_FRAME, x0, top, w, y - top, LC_RULE, block);
	return y;
}

/* ---------------------------------------------------------------- u14: what is left undrawn, callouts, figures */

static void trim_text(const char **s, size_t *n)
{
	while (*n && (**s == ' ' || **s == '\t' || **s == '\n' || **s == '\r')) { (*s)++; (*n)--; }
	while (*n >= 2 && (unsigned char)(*s)[0] == 0xC2 && (unsigned char)(*s)[1] == 0xA0) { *s += 2; *n -= 2; }
	while (*n && strchr(" \t\n\r", (*s)[*n - 1])) (*n)--;
	while (*n >= 2 && (unsigned char)(*s)[*n - 2] == 0xC2 && (unsigned char)(*s)[*n - 1] == 0xA0) *n -= 2;
}

/* the trimmed text s (n bytes) equals the trimmed string t */
static int same_text(const char *s, size_t n, const char *t)
{
	size_t m = t ? strlen(t) : 0;
	if (!t) return 0;
	trim_text(&t, &m);
	return m == n && !memcmp(s, t, n);
}

static int starts_with(const char *s, size_t n, const char *p) { size_t m = strlen(p); return n >= m && !memcmp(s, p, m); }

static int holds(const char *s, size_t n, const char *p)
{
	size_t m = strlen(p), i;
	for (i = 0; i + m <= n; i++) if (!memcmp(s + i, p, m)) return 1;
	return 0;
}

/* "Image on page 12": a PDF conversion's placeholder caption */
static int image_on_page(const char *s, size_t n)
{
	size_t i = 14;
	if (!starts_with(s, n, "Image on page ") || n == i) return 0;
	for (; i < n; i++) if (s[i] < '0' || s[i] > '9') return 0;
	return 1;
}

static int alert_table_repeat(const a2_doc *d, int block);

int lay_hidden(const a2_doc *d, int block)
{
	const a2_block *b;
	a2_buf t;
	const char *s;
	size_t n;
	int hide = 0;
	if (!d || block < 0 || block >= d->nb) return 0;
	b = &d->b[block];
	if (b->kind == A2_TABLE) return alert_table_repeat(d, block) ? LAY_HIDE_BLOCK : 0;
	if (b->kind != A2_PARAGRAPH && b->kind != A2_NOTE && b->kind != A2_FOOTNOTE && b->kind != A2_FIGURE) return 0;
	if (b->kind == A2_PARAGRAPH && block >= 4 && !cisco_source(d)) return 0;
	memset(&t, 0, sizeof t);
	if (a2_runs_text(&b->runs, &t)) { free(t.s); return 0; }
	s = t.s ? t.s : "";
	n = t.s ? t.n : 0;
	trim_text(&s, &n);
	switch (b->kind) {
	case A2_PARAGRAPH:
		/* S5: the title repeated as a paragraph near the top; a Cisco PDF's running page footer */
		if (block < 4 && same_text(s, n, d->title)) hide = LAY_HIDE_BLOCK;
		else if (n < 140 && starts_with(s, n, "\xC2\xA9") && holds(s, n, "Cisco and/or its affiliates") && cisco_source(d))
			hide = LAY_HIDE_BLOCK;
		break;
	case A2_NOTE:
		if (starts_with(s, n, "Source image (no alternative text supplied)")) hide = LAY_HIDE_BLOCK;
		break;
	case A2_FOOTNOTE:
		if (starts_with(s, n, "See reference ref-")) hide = LAY_HIDE_BLOCK;
		break;
	default: {
		const a2_media *md = b->media ? a2_media_find(d, b->media) : NULL;
		if (!n || same_text(s, n, "Image") || image_on_page(s, n) || (md && md->alt && same_text(s, n, md->alt)))
			hide = LAY_HIDE_CAPTION;
		break;
	}
	}
	free(t.s);
	return hide;
}

/* u14 M4: a Cisco heading at level 3 or deeper: 1 for an "Example:" label (drawn run-in, no outline entry), 2 for one
 * of the guides' recurring task headings (drawn as an H4, a minor outline entry), else 0 */
static int cisco_heading(const a2_block *b)
{
	static const char *const minor[] = { "Procedure", "Before you begin", "What to do next", "Related Topics" };
	a2_buf t;
	const char *s;
	size_t n, k;
	int r = 0;
	memset(&t, 0, sizeof t);
	if (a2_runs_text(&b->runs, &t) || !t.s) { free(t.s); return 0; }
	s = t.s;
	n = t.n;
	trim_text(&s, &n);
	if (n && s[n - 1] == ':') n--;
	if ((n == 7 && !strncasecmp(s, "Example", 7)) || (n == 8 && !strncasecmp(s, "Examples", 8))) r = 1;
	for (k = 0; !r && k < sizeof minor / sizeof minor[0]; k++)
		if (n == strlen(minor[k]) && !strncasecmp(s, minor[k], n)) r = 2;
	if (!r && n > 5 && !strncmp(s, "Step ", 5)) {
		for (k = 5; k < n && s[k] >= '0' && s[k] <= '9'; k++) ;
		if (k > 5 && (k == n || (k + 1 == n && s[k] == '.'))) r = 2;
	}
	free(t.s);
	return r;
}

/* the bytes of blanks (space, tab, no-break space) at the start of s */
static size_t blank_len(const char *s)
{
	size_t n = 0;
	for (;;) {
		if (s[n] == ' ' || s[n] == '\t') n++;
		else if ((unsigned char)s[n] == 0xC2 && (unsigned char)s[n + 1] == 0xA0) n += 2;
		else return n;
	}
}

/* the alert words a Cisco callout may open with (its own label): 0 Note, 1 Caution, 2 Warning, then the ones drawn
 * as notes; -1 when s does not start with one (followed by a colon, a blank or the end); *n its bytes, colon included */
static int alert_word(const char *s, size_t *n)
{
	static const char *const words[] = { "Note", "Caution", "Warning", "Tip", "Timesaver", "Important" };
	size_t k, m;
	for (k = 0; k < sizeof words / sizeof words[0]; k++) {
		m = strlen(words[k]);
		if (strncasecmp(s, words[k], m)) continue;
		if (s[m] == ':') m++;
		if (s[m] && !blank_len(s + m) && s[m] != '\n') continue;
		*n = m;
		return (int)k;
	}
	return -1;
}

/* the next word (a run of non-blanks) of s from *i */
static int next_word(const char *s, size_t n, size_t *i, const char **w, size_t *wl)
{
	size_t k;
	while (*i < n && ((k = blank_len(s + *i)) || s[*i] == '\n' || s[*i] == '\r')) *i += k ? k : 1;
	if (*i >= n) return 0;
	*w = s + *i;
	while (*i < n && !blank_len(s + *i) && s[*i] != '\n' && s[*i] != '\r') (*i)++;
	*wl = (size_t)(s + *i - *w);
	return 1;
}

/* the same words in the same order, whatever blanks lie between them */
static int same_words(const char *a, size_t an, const char *b, size_t bn)
{
	size_t i = 0, j = 0, al = 0, bl = 0;
	const char *aw = NULL, *bw = NULL;
	for (;;) {
		int ha = next_word(a, an, &i, &aw, &al), hb = next_word(b, bn, &j, &bw, &bl);
		if (!ha || !hb) return ha == hb;
		if (al != bl || memcmp(aw, bw, al)) return 0;
	}
}

/* Wikipedia's hatnotes, which the converter writes as notes */
static const char *const hatnotes[] = { "Main article", "See also:", "Further information", "Further reading:", NULL };

/* the runs' trimmed text starts with one of the prefixes (a NULL-ended list) */
static int text_opens(const a2_runs *r, const char *const *prefixes)
{
	a2_buf t;
	const char *s;
	size_t n;
	int k, hit = 0;
	memset(&t, 0, sizeof t);
	if (a2_runs_text(r, &t) || !t.s) { free(t.s); return 0; }
	s = t.s;
	n = t.n;
	trim_text(&s, &n);
	for (k = 0; prefixes[k] && !hit; k++) hit = starts_with(s, n, prefixes[k]);
	free(t.s);
	return hit;
}

/* the runs' trimmed text is the string, letter case aside */
static int runs_say(const a2_runs *r, const char *say)
{
	a2_buf t;
	const char *s;
	size_t n;
	int hit;
	memset(&t, 0, sizeof t);
	if (a2_runs_text(r, &t) || !t.s) { free(t.s); return 0; }
	s = t.s;
	n = t.n;
	trim_text(&s, &n);
	hit = n == strlen(say) && !strncasecmp(s, say, n);
	free(t.s);
	return hit;
}

/* u14: 1 when the document has no normative reference (a Wikipedia article's notes are "informative", its further
 * reading "further"): the normative/informative split means nothing there, so a kind heading under the section's own
 * heading ("Notes", "References") only repeats it */
static int refs_no_normative(const a2_doc *d)
{
	int i, k;
	for (i = 0; i < d->nb; i++)
		if (d->b[i].kind == A2_REFERENCES)
			for (k = 0; k < d->b[i].nrefs; k++)
				if (d->b[i].refs[k].kind == REF_NORMATIVE) return 0;
	return 1;
}

/* u14 S5: a one-row table of an alert word and a text that only repeats the callout just before it (the Cisco
 * converter emits both) */
static int alert_table_repeat(const a2_doc *d, int block)
{
	const a2_block *b = &d->b[block], *p = block > 0 ? &d->b[block - 1] : NULL;
	a2_buf c0, c1, pt;
	const char *s;
	size_t n, w;
	int r = 0;
	if (b->nrows != 1 || b->ncols != 2 || b->ncells != 2 || !p ||
	    (p->kind != A2_NOTE && p->kind != A2_CAUTION && p->kind != A2_WARNING)) return 0;
	memset(&c0, 0, sizeof c0); memset(&c1, 0, sizeof c1); memset(&pt, 0, sizeof pt);
	if (!a2_runs_text(&b->cells[0].runs, &c0) && !a2_runs_text(&b->cells[1].runs, &c1) && !a2_runs_text(&p->runs, &pt) &&
	    c0.s && c1.s && pt.s) {
		s = c0.s;
		n = c0.n;
		trim_text(&s, &n);
		if (alert_word(s, &w) >= 0 && (w == n || (w + 1 == n && s[w] == ':'))) {
			s = pt.s + blank_len(pt.s);
			if (alert_word(s, &w) >= 0) s += w;
			r = same_words(s, pt.n - (size_t)(s - pt.s), c1.s, c1.n);
		}
	}
	free(c0.s); free(c1.s); free(pt.s);
	return r;
}

/* u14 S3: a Note, Caution or Warning as a CDE callout: a tint in a 2-px etched frame (shadow then light at the top
 * and left, light then shadow at the bottom and right), padded 0.5 em by 0.75 em; its label bold in its own left
 * column, the text hanging after an en space. A callout whose text opens with an alert word of its own (Cisco's
 * "Note", "Caution", "Timesaver" ...) shows that word as its label, and Caution and Warning take their own tints. */
static int callout(lay_doc *L, const lay_metrics *M, const a2_block *b, int x0, int w, int y, int block)
{
	static const char *const names[] = { "Note", "Caution", "Warning" };
	int k = b->kind == A2_CAUTION ? 1 : b->kind == A2_WARNING ? 2 : 0, ink, word = -1, bold0 = 0;
	int padv = em_px(M, 50), padh = em_px(M, 75), en = em_px(M, 50), top = y, fill = L->nit, h, bottom, inner;
	int lead = M->pitch - fasc(M, LF_BODY) - fdesc(M, LF_BODY), src0 = -1;
	const a2_run *r0;
	const char *t0 = NULL, *label;
	size_t wlen = 0, at = 0, len;
	a2_runs rest = b->runs;
	flow f;
	runctx rc;
	piece p;
	int skip = 0;
	/* blank text runs before the first word (the converter opens with "") */
	while (skip < b->runs.n && b->runs.r[skip].t == R_TEXT && !b->runs.r[skip].text[blank_len(b->runs.r[skip].text)]) skip++;
	r0 = skip < b->runs.n ? &b->runs.r[skip] : NULL;
	if (lead < 0) lead = 0;
	if (r0 && r0->t == R_TEXT) t0 = r0->text;
	else if (r0 && r0->t == R_BOLD && r0->kids.n == 1 && r0->kids.r[0].t == R_TEXT) { t0 = r0->kids.r[0].text; bold0 = 1; }
	if (t0) {
		at = blank_len(t0);
		word = alert_word(t0 + at, &wlen);
		if (word == 1 || word == 2) k = word;
	}
	ink = LC_NOTE_LABEL + k;
	inner = w - 4 - 2 * padh;
	shape(L, LI_FILL, x0 + 2, top + 2, w - 4, 1, LC_NOTE + k, block);
	fl_begin(&f, L, M, x0 + 2 + padh, inner, 0, block, top + 2 + padv, LF_BODY, M->pitch);
	rc.f = &f;
	rc.base = LF_BODY;
	rc.colour = LC_TEXT;
	/* the label: the text's own alert word (its bytes keep their plain-text place), else the kind's name */
	memset(&p, 0, sizeof p);
	p.st.face = LF_BOLD;
	p.st.colour = ink;
	p.st.link = -1;
	p.kind = P_WORD;
	if (word >= 0) {
		int s;
		for (s = 0; s < skip; s++) pt_adds(L, b->runs.r[s].text);
		src0 = pt_adds(L, t0);
		label = t0 + at;
		len = wlen;
		p.src = src0 + (int)at;
		p.srclen = (int)wlen;
	} else {
		label = names[k];
		len = strlen(label);
		p.src = -1;
	}
	p.off = t8_add(L, label, (int)len);
	p.len = (int)len;
	p.w = fw(M, LF_BOLD, label, (int)len) + en;
	if (p.w < inner / 2) { f.x0 += p.w; f.w -= p.w; f.indent1 = -p.w; }
	if (p.off >= 0) fl_push(&f, p);
	if (word >= 0) {
		/* the rest of the first run after the word and its blanks, then the other runs (their leading blanks too) */
		style st = { bold0 ? LF_BOLD : LF_BODY, LC_TEXT, -1, 0, 0 };
		size_t n = strlen(t0), q = at + wlen;
		q += blank_len(t0 + q);
		if (q < n) fl_text(&f, t0 + q, n - q, src0 + (int)q, st);
		rest.r = b->runs.r + skip + 1;
		rest.n = b->runs.n - skip - 1;
		if (q >= n && rest.n && rest.r[0].t == R_TEXT) {
			const char *t1 = rest.r[0].text;
			size_t m = strlen(t1), s1 = blank_len(t1);
			int src1 = pt_adds(L, t1);
			style ts = { LF_BODY, LC_TEXT, -1, 0, 0 };
			if (s1 < m) fl_text(&f, t1 + s1, m - s1, src1 + (int)s1, ts);
			rest.r++;
			rest.n--;
		}
	}
	lay_runs(&rc, &rest, 0, -1, 0, 0, LC_TEXT);
	bottom = fl_end(&f) - lead + padv + 2;
	h = bottom - top;
	if (fill < L->nit) L->it[fill].h = h - 4;
	shape(L, LI_FILL, x0, top, w, 1, LC_SHADOW, block);
	shape(L, LI_FILL, x0, top, 1, h, LC_SHADOW, block);
	shape(L, LI_FILL, x0, top + h - 1, w, 1, LC_LIGHT, block);
	shape(L, LI_FILL, x0 + w - 1, top, 1, h, LC_LIGHT, block);
	shape(L, LI_FILL, x0 + 1, top + 1, w - 2, 1, LC_LIGHT, block);
	shape(L, LI_FILL, x0 + 1, top + 1, 1, h - 2, LC_LIGHT, block);
	shape(L, LI_FILL, x0 + 1, top + h - 2, w - 2, 1, LC_SHADOW, block);
	shape(L, LI_FILL, x0 + w - 2, top + 1, 1, h - 2, LC_SHADOW, block);
	return bottom;
}

/* u14 M6, M8: one figure: its picture box (bw x bh at x), the caption 0.4 em under it in the small body face (regular,
 * never bold; left out when lay_hidden says so) and the credit, small and quiet, on its own line; both cw wide from
 * cx. Returns the y under it. */
static int figure(lay_doc *L, const lay_metrics *M, const a2_doc *d, const a2_block *b, int block, int x, int bw, int bh,
                  int cx, int cw, int y, int hidden)
{
	const a2_media *md = a2_media_find(d, b->media);
	int src;
	y = media_box(L, M, d, b->media, x, bw, bh, y, block, md ? md->alt : b->media, -1);
	if (!hidden || *b->credit) y += em_px(M, 40);
	if (hidden) pt_runs(L, &b->runs);
	else y = para_at(L, M, &b->runs, cx, cw, y, block, LF_SMALL, 0, LC_TEXT, NULL, LC_TEXT, 0, small_pitch(M));
	pt_adds(L, "\n");
	src = pt_adds(L, b->credit);
	if (*b->credit) {
		flow f;
		style st = { LF_SMALL, LC_QUIET, -1, 0, 0 };
		fl_begin(&f, L, M, cx, cw, 0, block, y, LF_SMALL, small_pitch(M));
		fl_text(&f, b->credit, strlen(b->credit), src, st);
		y = fl_end(&f);
	}
	return y;
}

/* ---------------------------------------------------------------- format 2 */

void lay_v2(lay_doc *L, const lay_metrics *M, const a2_doc *d, int width)
{
	int i, y = 0, W = width, drawn = 0, heading_level = 1, pend = 0, ref_last = -1;
	int cisco = cisco_source(d), pane = M->pane > W ? M->pane : W, run_first = -1, run_last = -1;
	int fig_last = -1, fig_x = 0, fig_top = 0, fig_bottom = 0, fig_gallery = 0, fig_first = 0, fig_row_end = -1, gutter = em_px(M, 100);
	code_box box;
	long t0 = now_ms();
	reset(L, width);
	L->v2 = 1;
	fn_seq.n = 0;
	memset(&box, 0, sizeof box);
	for (i = 0; i < d->nb; i++) {
		const a2_block *b = &d->b[i];
		int gap = para_gap(M), lead = pend, hidden = lay_hidden(d, i);
		if (i) pt_adds(L, "\n\n");
		if (hidden == LAY_HIDE_BLOCK) {
			/* u14: left undrawn (lay_hidden), its plain text kept; the space before it carries over */
			if (a2_block_text(b, &L->pt)) L->oom = 1;
			continue;
		}
		if (b->kind != A2_REFERENCES) ref_last = -1;
		if (b->kind != A2_HEADING && b->kind != A2_TITLE) y += lead;
		switch (b->kind) {
		case A2_TITLE:
			pt_adds(L, b->text); /* the title band shows it */
			gap = 0;
			break;
		case A2_HEADING: {
			/* u14 M4: H1 Times New Roman Bold 1.6 x the body, H2 1.35, H3 1.15 (F_HEAD3), H4-H6 the body bold;
			 * space above by level (1.5, 1.2, 1.0, 0.8 lines) when anything is drawn above; H1's double rule, H2's
			 * hairline; Cisco's "Example:" labels run in, its recurring task headings are minor H4s */
			char t[400];
			int kind = cisco && b->level >= 3 ? cisco_heading(b) : 0, lvl = kind == 2 ? 4 : b->level, top, face, hp, above;
			if (kind == 1) {
				y += lead > para_gap(M) ? lead : para_gap(M);
				add_anchor(L, LA_HEADING, b->anchor, y - 4, i);
				y = para(L, M, &b->runs, 0, W, y, i, LF_BODY, ST_ITALIC, LC_QUIET, NULL, 0);
				gap = item_gap(M);
				break;
			}
			face = lvl <= 1 ? LF_HEAD : lvl == 2 ? LF_SUBHEAD : lvl == 3 ? LF_HEAD3 : LF_BODY;
			above = lvl <= 1 ? 3 * M->pitch / 2 : lvl == 2 ? (6 * M->pitch + 2) / 5 : lvl == 3 ? M->pitch : (4 * M->pitch + 2) / 5;
			y += drawn && above > lead ? above : lead;
			top = y - 4;
			add_anchor(L, LA_HEADING, b->anchor, top, i);
			runs_8bit(M, &b->runs, t, sizeof t);
			add_entry(L, LE_HEADING, b->level, top, i, t);
			if (kind == 2 && L->nen) L->en[L->nen - 1].minor = 1;
			heading_level = b->level;
			/* the heading's own pitch: room for its face's descent above the rule */
			hp = fasc(M, face_for(ST_BOLD, face)) + fdesc(M, face_for(ST_BOLD, face)) + 2;
			if (hp < M->pitch) hp = M->pitch;
			y = para_at(L, M, &b->runs, 0, W, y, i, face, ST_BOLD, LC_TEXT, NULL, LC_TEXT, 0, hp);
			if (lvl <= 1) {
				shape(L, LI_HLINE, 0, y, W, 1, LC_RULE, i);
				shape(L, LI_HLINE, 0, y + 1, W, 1, LC_BAND, i);
				y += 2 + (35 * M->pitch + 50) / 100;
			} else if (lvl == 2) {
				shape(L, LI_HLINE, 0, y + 1, W, 1, LC_RULE, i);
				y += 2 + (35 * M->pitch + 50) / 100;
			} else y += (M->pitch + 2) / 4;
			gap = 0;
			break;
		}
		case A2_SUBTITLE: y = para(L, M, &b->runs, 0, W, y, i, LF_BODY, ST_ITALIC, LC_TEXT, NULL, 0); break;
		case A2_BYLINE: y = para_at(L, M, &b->runs, 0, W, y, i, LF_SMALL, ST_ITALIC, LC_QUIET, NULL, LC_QUIET, 0, small_pitch(M)); break;
		case A2_PARAGRAPH: y = para(L, M, &b->runs, 0, W, y, i, LF_BODY, 0, LC_TEXT, NULL, 0); break;
		case A2_NOTE: case A2_CAUTION: case A2_WARNING:
			/* a Wikipedia hatnote ("Main article: ...") is an indented italic line, not a callout */
			if (b->kind == A2_NOTE && text_opens(&b->runs, hatnotes))
				y = para(L, M, &b->runs, indent_px(M), W - indent_px(M), y, i, LF_BODY, ST_ITALIC, LC_QUIET, NULL, 0);
			else y = callout(L, M, b, 0, W, y, i);
			break;
		case A2_QUOTE: y = para(L, M, &b->runs, indent_px(M), W - 2 * indent_px(M), y, i, LF_BODY, ST_ITALIC, LC_TEXT, NULL, 0); break;
		case A2_LIST: {
			int first = 1;
			y = list_block(L, M, &b->list, 0, 0, W, y, i, &first);
			gap = para_gap(M) - item_gap(M);
			break;
		}
		case A2_DEFINITIONS: {
			int k;
			for (k = 0; k < b->ndefs; k++) {
				if (k) { pt_adds(L, "\n"); y += item_gap(M); }
				y = para(L, M, &b->defs[k].term, 0, W, y, i, LF_BODY, ST_BOLD, LC_TEXT, NULL, 0) + 1;
				pt_adds(L, ": ");
				y = para(L, M, &b->defs[k].def, indent_px(M), W - indent_px(M), y, i, LF_BODY, 0, LC_TEXT, NULL, 0);
			}
			break;
		}
		case A2_PRE: case A2_CODE: {
			int src;
			if (i > run_last) {
				/* The run's first block opens its box: as wide as its widest line needs, from the reading measure
				 * up to the pane; on the measure's left edge while the pane has room, else moved left. One
				 * outline entry for the box (when it holds code), named by its first line. */
				int k, cols = 0, mode = code_mode(b, cisco), bw, left = (pane - W) / 2;
				const a2_block *code = NULL;
				const char *name = NULL;
				size_t nlen = 0;
				for (run_last = i; run_last + 1 < d->nb && code_block(&d->b[run_last + 1]) &&
				     code_mode(&d->b[run_last + 1], cisco) == mode; run_last++) ;
				for (k = i; k <= run_last; k++) {
					int c = code_cols(d->b[k].text);
					if (c > cols) cols = c;
					if (!name) name = code_first_line(d->b[k].text, &nlen);
					if (!code && d->b[k].kind == A2_CODE) code = &d->b[k];
				}
				bw = cols * fw(M, LF_MONO, "0", 1) + 12 + (mode ? 6 : 0);
				bw = bw < W ? W : bw > pane ? pane : bw;
				if (code) {
					char t[200], t8[200];
					int cli = code_language(code, "ios") || code_language(code, "cli") || (cisco && name && cli_prompt(name));
					snprintf(t, sizeof t, "%s: %.*s", cli ? "CLI" : "Code", (int)(nlen > 120 ? 120 : nlen), name ? name : "");
					t8_to_8bit(t, strlen(t), M->t8flags, t8, sizeof t8);
					add_entry(L, LE_CODE, heading_level + 1, y - 4, i, t8);
				}
				run_first = i;
				y = box_open(L, M, &box, bw <= pane - left ? 0 : pane - left - bw, bw, y, mode);
			}
			src = pt_adds(L, b->text);
			y = box_rows(L, M, &box, b->text, src, y, i, b->kind == A2_CODE ? b->var : NULL, b->kind == A2_CODE ? b->nvar : 0);
			if (i == run_last) y = box_close(L, &box, y, run_first) + 8;
			gap = 0;
			break;
		}
		case A2_TABLE: {
			char t[200], cell[160];
			cell[0] = 0;
			if (b->ncells) runs_8bit(M, &b->cells[0].runs, cell, sizeof cell);
			snprintf(t, sizeof t, "Table: %s", cell);
			add_entry(L, LE_TABLE, heading_level + 1, y - 4, i, t);
			add_anchor(L, LA_MEDIA, "", y, i);
			y = table_block(L, M, b, 0, W, pane, y, i);
			break;
		}
		case A2_FIGURE: {
			/* u14 M8: centred at its own size; S11: a run of two or more consecutive figures each at most 0.48 of the
			 * column wide packs side by side, a gutter of one em, the captions under each, rows as tall as the
			 * tallest */
			char t[300];
			int least = em_px(M, 1800) > 240 ? em_px(M, 1800) : 240, bw, bh, cw = W < least ? W : least, x, cx;
			int cmin = (W - gutter) / 2 < least ? (W - gutter) / 2 : least;
			figure_size(M, d, b->media, W, &bw, &bh);
			if (cw < bw) cw = bw;
			if (i > fig_last) {
				int k = i, kw, kh;
				while (k + 1 < d->nb && d->b[k + 1].kind == A2_FIGURE) {
					figure_size(M, d, d->b[k + 1].media, W, &kw, &kh);
					if (kw > W * 48 / 100) break;
					k++;
				}
				fig_last = bw <= W * 48 / 100 && k > i ? k : i;
				fig_gallery = fig_last > i;
				fig_first = i;
				fig_row_end = i - 1;
				fig_top = fig_bottom = y;
			}
			if (fig_gallery) {
				/* a gallery cell: at least 240 px (half the column at most) for its caption; the picture and the
				 * caption at the cell's left; each row as many cells as fit, centred in the column */
				cw = bw > cmin ? bw : cmin;
				if (i > fig_row_end) {
					int k, kw, kh, rw = cw;
					if (i > fig_first) fig_top = fig_bottom + para_gap(M);
					for (k = i + 1; k <= fig_last; k++) {
						figure_size(M, d, d->b[k].media, W, &kw, &kh);
						if (kw < cmin) kw = cmin;
						if (rw + gutter + kw > W) break;
						rw += gutter + kw;
					}
					fig_row_end = k - 1;
					fig_x = (W - rw) / 2;
				}
				x = cx = fig_x;
				fig_x += cw + gutter;
				y = fig_top;
			} else {
				x = (W - bw) / 2;
				cx = x + cw <= W ? x : W - cw;
				if (cx < 0) cx = 0;
			}
			if (hidden != LAY_HIDE_CAPTION) {
				runs_8bit(M, &b->runs, t, sizeof t);
				add_entry(L, LE_FIGURE, heading_level + 1, y - 4, i, t);
			}
			y = figure(L, M, d, b, i, x, bw, bh, cx, cw, y, hidden);
			if (y > fig_bottom) fig_bottom = y;
			if (i < fig_last) { y = fig_top; gap = 0; }
			else y = fig_bottom;
			break;
		}
		case A2_MATHS: {
			int open = M->open_formula == i + 1;
			int src = pt_adds(L, b->alt);
			int media = media_index(d, b->media);
			add_anchor(L, LA_MEDIA, b->media, y, i);
			y = formula_button(L, M, open ? "[Formula] -" : "[Formula] +", 0, y, W, i, -1,
			    open ? -1 : src, open ? 0 : (int)strlen(b->alt));
			if (open) {
				y = pre_block(L, M, b->alt, src, 0, W, y + 4, i, NULL, 0, 0);
				if (media >= 0) y = formula_button(L, M, "View artwork", 0, y + 4, W, i, media, -1, 0);
			}
			break;
		}
		case A2_FACTBOX: case A2_INFOBOX: case A2_QUICK_FACTS:
			y = fact_block(L, M, b, 0, W, y, i);
			break;
		case A2_REFERENCES: case A2_SEE_ALSO: {
			int k;
			/* u14 M7: one heading per kind across consecutive reference blocks (an RFC's references come one per
			 * block); each label in the text colour, the jump on its leading "[tag]" when it has one */
			for (k = 0; k < b->nrefs; k++) {
				const a2_ref *rf = &b->refs[k];
				const char *head = NULL;
				flow f;
				runctx rc;
				int lk;
				if (b->kind == A2_REFERENCES && rf->kind != ref_last) {
					head = rf->kind == REF_NORMATIVE ? "Normative references" : rf->kind == REF_INFORMATIVE ? "Informative references" : "Further reading";
					ref_last = rf->kind;
				} else if (b->kind == A2_SEE_ALSO && k == 0) head = "See also";
				/* not under a heading that already says it ("Normative References", "See also"), nor under any
				 * heading in a document with no normative reference; blocks left undrawn in between (Wikipedia's
				 * "See reference" footnotes) do not count */
				if (head) {
					int above = i - 1;
					while (above > 0 && (lay_hidden(d, above) & LAY_HIDE_BLOCK)) above--;
					if (above > 0 && d->b[above].kind == A2_HEADING &&
					    (runs_say(&d->b[above].runs, head) || (b->kind == A2_REFERENCES && refs_no_normative(d))))
						head = NULL;
				}
				if (head) {
					style hs = { LF_BOLD, LC_TEXT, -1, 0, 0 };
					fl_begin(&f, L, M, 0, W, 0, i, y + (k ? item_gap(M) : 0), LF_BOLD, M->pitch);
					fl_text(&f, head, strlen(head), -1, hs);
					y = fl_end(&f);
				} else if (k) y += item_gap(M);
				if (k) pt_adds(L, "\n");
				fl_begin(&f, L, M, indent_px(M), W - indent_px(M), 0, i, y, LF_BODY, M->pitch);
				rc.f = &f;
				rc.base = LF_BODY;
				rc.colour = LC_TEXT;
				if (b->kind == A2_REFERENCES) add_anchor(L, LA_FOOTNOTE, rf->id, y, i);
				lk = add_link(L, LK_TARGET, &rf->target, NULL, NULL);
				if (b->kind == A2_SEE_ALSO) lay_runs(&rc, &rf->label, ST_ITALIC, lk, 0, 0, LC_JUMP);
				else {
					const a2_run *r0 = rf->label.n ? &rf->label.r[0] : NULL;
					const char *close = r0 && r0->t == R_TEXT && r0->text[0] == '[' ? strchr(r0->text, ']') : NULL;
					if (close && close - r0->text < 40) {
						size_t n = strlen(r0->text), tag = (size_t)(close - r0->text) + 1;
						int src = pt_add(L, r0->text, n);
						a2_runs rest = { rf->label.r + 1, rf->label.n - 1 };
						style js = { LF_BODY, LC_JUMP, lk, 0, 0 }, ts = { LF_BODY, LC_TEXT, -1, 0, 0 };
						fl_text(&f, r0->text, tag, src, js);
						fl_text(&f, r0->text + tag, n - tag, src + (int)tag, ts);
						lay_runs(&rc, &rest, 0, -1, 0, 0, LC_TEXT);
					} else lay_runs(&rc, &rf->label, 0, lk, 0, 0, LC_TEXT);
				}
				y = fl_end(&f);
			}
			break;
		}
		case A2_TIMELINE_YEAR: {
			char yr[32];
			if (b->year <= 0) snprintf(yr, sizeof yr, "%ld BC  ", 1 - b->year);
			else snprintf(yr, sizeof yr, "%ld  ", b->year);
			y = para(L, M, &b->runs, 0, W, y, i, LF_BODY, 0, LC_TEXT, yr, 0);
			break;
		}
		case A2_FOOTNOTE: {
			/* u14 M7: the small body face in the text colour; only its "[28]" label is a jump colour */
			char t[48];
			add_anchor(L, LA_FOOTNOTE, b->anchor, y, i);
			footnote_label(b->anchor, t, sizeof t - 1);
			strcat(t, " ");
			y = para_at(L, M, &b->runs, 0, W, y, i, LF_SMALL, 0, LC_TEXT, t, LC_JUMP, 0, small_pitch(M));
			break;
		}
		case A2_ATTRIBUTION: {
			flow f;
			style st = { LF_SMALL, LC_QUIET, -1, 0, 0 };
			int k, lh = small_pitch(M);
			char rt[40];
			shape(L, LI_HLINE, 0, y + 2, W, 1, LC_RULE, i);
			y += 2 + em_px(M, 50);
			fl_begin(&f, L, M, 0, W, 0, i, y, LF_SMALL, lh);
			for (k = 0; k < b->nauthors; k++) {
				int src = k ? pt_adds(L, "; ") : -1;
				if (k) fl_text(&f, "; ", 2, src, st);
				src = pt_adds(L, b->authors[k]);
				fl_text(&f, b->authors[k], strlen(b->authors[k]), src, st);
			}
			fl_break(&f);
			pt_adds(L, "\n");
			fl_text(&f, b->licence, strlen(b->licence), pt_adds(L, b->licence), st);
			fl_break(&f);
			pt_adds(L, "\n");
			fl_text(&f, b->source_url, strlen(b->source_url), pt_adds(L, b->source_url), st);
			fl_break(&f);
			snprintf(rt, sizeof rt, "Retrieved %s", b->retrieved);
			{
				int src = pt_adds(L, "\nRetrieved ") + 1;
				pt_adds(L, b->retrieved);
				fl_text(&f, rt, strlen(rt), src, st);
			}
			y = fl_end(&f);
			break;
		}
		default: break;
		}
		if (b->kind != A2_TITLE) { pend = gap > 0 ? gap : 0; drawn = 1; }
	}
	/* u14 M7: a footnote left out as "See reference ref-x." answers its markers at reference ref-x */
	for (i = 0; i < d->nb; i++) {
		const a2_block *b = &d->b[i];
		const char *s;
		char ref[160];
		int k;
		if (b->kind != A2_FOOTNOTE || !b->anchor || lay_anchor_find(L, LA_FOOTNOTE, b->anchor) >= 0 ||
		    !b->runs.n || b->runs.r[0].t != R_TEXT || strncmp(b->runs.r[0].text, "See reference ", 14)) continue;
		s = b->runs.r[0].text + 14;
		snprintf(ref, sizeof ref, "%.*s", (int)strcspn(s, " \t."), s);
		if ((k = lay_anchor_find(L, LA_FOOTNOTE, ref)) >= 0) add_anchor(L, LA_FOOTNOTE, b->anchor, L->an[k].y, L->an[k].block);
	}
	free((void *)fn_seq.name);
	memset(&fn_seq, 0, sizeof fn_seq);
	L->h = y + 20;
	L->ms = (int)(now_ms() - t0);
}

/* ---------------------------------------------------------------- build 1's RFC text, exactly as build 2 laid it out */

static void v1_op(lay_doc *L, const lay_metrics *M, int x, int y, int face, const char *s, int n, int block)
{
	lay_item *it;
	int off = t8_add(L, s, n), src, w = fw(M, face, s, n);
	lay_line *ln;
	if (L->pt.n) pt_adds(L, "\n");
	src = pt_add(L, s, (size_t)n);
	if (off < 0 || !(it = new_item(L, LI_TEXT))) return;
	it->x = x;
	it->y = y;
	it->face = face;
	it->colour = LC_TEXT;
	it->off = off;
	it->len = n;
	it->src = src;
	it->srclen = n;
	it->block = block;
	it->w = w;
	if (!(ln = (lay_line *)grow(L, L->ln, &L->cln, L->nln + 1, sizeof(lay_line)))) return;
	L->ln = ln;
	ln = &L->ln[L->nln];
	ln->top = y - fasc(M, face);
	ln->base = y;
	ln->bot = y + fdesc(M, face) + 3;
	ln->x0 = x;
	ln->x1 = x + w;
	ln->first = L->nit - 1;
	ln->n = 1;
	ln->block = block;
	ln->inset = 0;
	it->line = L->nln++;
}

void lay_v1(lay_doc *L, const lay_metrics *M, const article *a, int width)
{
	int i, y = 0;
	long t0 = now_ms();
	reset(L, width);
	for (i = 0; i < a->nb; i++) {
		const art_block *b = &a->b[i];
		if (b->kind == B_HEADING) {
			char h[260], e[260];
			int f = LF_BOLD;
			y += i ? (b->level == 1 ? 14 : 10) : 0;
			snprintf(e, sizeof e, "%s %s", b->number, b->text);
			add_entry(L, LE_HEADING, b->level, y - 4, i, e);
			snprintf(h, sizeof h, "%s  %s", b->number, b->text);
			y += fasc(M, f);
			v1_op(L, M, 0, y, f, h, (int)strlen(h), i);
			y += fdesc(M, f) + 6;
		} else if (b->kind == B_PARA || b->kind == B_TERM) {
			int f = b->kind == B_TERM ? LF_BOLD : LF_BODY, ind = b->kind == B_PARA ? b->level * 22 : 0;
			int w = width - ind, len = (int)strlen(b->text), start = 0, lh = M->pitch > 0 ? M->pitch : fasc(M, f) + fdesc(M, f);
			while (start < len) {
				int end = start, last = -1;
				while (end < len) {
					if (b->text[end] == ' ') last = end;
					if (fw(M, f, b->text + start, end - start + 1) > w && last > start) { end = last; break; }
					end++;
				}
				y += fasc(M, f);
				v1_op(L, M, ind, y, f, b->text + start, end - start, i);
				y += lh - fasc(M, f);
				start = end;
				while (start < len && b->text[start] == ' ') start++;
			}
			y += b->kind == B_TERM ? 1 : 9;
		} else {
			const char *s = b->text, *nl;
			int f = LF_MONO, ind = b->level * 22, lh = fasc(M, f) + fdesc(M, f) + 1;
			for (;;) {
				int n;
				nl = strchr(s, '\n');
				n = nl ? (int)(nl - s) : (int)strlen(s);
				y += fasc(M, f);
				v1_op(L, M, ind, y, f, s, n, i);
				y += lh - fasc(M, f);
				if (!nl) break;
				s = nl + 1;
			}
			y += 8;
		}
	}
	L->h = y + 20;
	L->ms = (int)(now_ms() - t0);
}

/* ---------------------------------------------------------------- hit-testing, selection, lookups */

/* the drawn width of the item's first k characters of plain text (format 2 maps each character to its bytes again) */
static int prefix_w(const lay_doc *L, const lay_metrics *M, const lay_item *it, int upto)
{
	int bytes = 0, p = it->src;
	if (it->kind != LI_TEXT || it->src < 0) return 0;
	if (it->flags & LI_F_FORMULA_LINK) return upto >= it->src + it->srclen ? it->w : 0;
	if (it->flags & LI_F_TAB) return upto > it->src ? it->w : 0;
	if (!L->v2 || (it->flags & LI_F_SYMBOL)) {
		if (it->flags & LI_F_SYMBOL) {
			/* one byte per character on the Symbol face */
			while (p < upto && p < it->src + it->srclen) { uint32_t cp; p += t8_decode(L->pt.s + p, (size_t)(L->pt.n - (size_t)p), &cp); bytes++; }
		} else bytes = upto - it->src;
	} else {
		int flags = M->t8flags | (M->have[LF_SYMBOL] ? T8_HAVE_SYMBOL : 0);
		while (p < upto && p < it->src + it->srclen) {
			uint32_t cp;
			char out[T8_MAXOUT];
			int len, k = t8_decode(L->pt.s + p, L->pt.n - (size_t)p, &cp);
			if (cp == '\t') len = 1; /* (pre tabs are their own pieces) */
			else if (t8_map(cp, flags, out, &len) == T8_NONE) len = 0;
			bytes += len;
			p += k;
		}
	}
	if (bytes > it->len) bytes = it->len;
	return fw(M, it->face, L->t8 + it->off, bytes);
}

int lay_item_x(const lay_doc *L, const lay_metrics *M, const lay_item *it, int pos)
{
	if (it->kind == LI_BOX) return pos > it->src ? it->x + it->w : it->x;
	return it->x + prefix_w(L, M, it, pos);
}

/* the character boundary in item it nearest x */
static int pos_in_item(const lay_doc *L, const lay_metrics *M, const lay_item *it, int x)
{
	int p = it->src, best = it->src, bestd = 1 << 30;
	if (it->src < 0) return -1;
	if (it->flags & LI_F_FORMULA_LINK) return x - it->x > it->w / 2 ? it->src + it->srclen : it->src;
	while (p <= it->src + it->srclen) {
		int px = lay_item_x(L, M, it, p), d = px > x ? px - x : x - px;
		uint32_t cp;
		if (d < bestd) { bestd = d; best = p; }
		if (p >= it->src + it->srclen) break;
		p += t8_decode(L->pt.s + p, L->pt.n - (size_t)p, &cp);
	}
	return best;
}

const lay_inset *lay_line_inset(const lay_doc *L, int line)
{
	return line >= 0 && line < L->nln && L->ln[line].inset ? &L->inset[L->ln[line].inset - 1] : NULL;
}

void lay_hit_test(const lay_doc *L, const lay_metrics *M, int x, int y, int exact, lay_hit *h)
{
	int i, best = -1, bestd = 1 << 30;
	h->item = h->line = h->pos = h->link = -1;
	for (i = 0; i < L->nln; i++) {
		const lay_line *ln = &L->ln[i];
		const lay_inset *p = lay_line_inset(L, i);
		int d, left = ln->x0, right = ln->x1;
		if (p) {
			left = p->x + 6;
			right -= p->offset;
			if (right < left) right = left;
			if (right > p->x + p->w - 6) right = p->x + p->w - 6;
		}
		if (y >= ln->top && y < ln->bot && x >= left - 2 && x < right + 2) { best = i; bestd = 0; break; }
		if (exact) continue;
		d = y < ln->top ? ln->top - y : y >= ln->bot ? y - ln->bot + 1 : 0;
		d = d * 4 + (x < left ? left - x : x > right ? x - right : 0);
		if (d < bestd) { bestd = d; best = i; }
	}
	if (best < 0) return;
	h->line = best;
	{
		const lay_line *ln = &L->ln[best];
		const lay_inset *p = lay_line_inset(L, best);
		int k, nearest = -1, nd = 1 << 30;
		if (p) {
			int left = p->x + 6, right = p->x + p->w - 6;
			if (exact && (x < left || x >= right)) return;
			if (x < left) x = left;
			if (x >= right) x = right - 1;
			x += p->offset;
		}
		for (k = ln->first; k < ln->first + ln->n; k++) {
			const lay_item *it = &L->it[k];
			int d;
			if (it->kind != LI_TEXT && it->kind != LI_BOX) continue;
			if (x >= it->x && x < it->x + it->w) { nearest = k; nd = 0; break; }
			d = x < it->x ? it->x - x : x - (it->x + it->w) + 1;
			if (it->src >= 0 && d < nd) { nd = d; nearest = k; }
		}
		if (nearest < 0) return;
		if (nd == 0) {
			h->item = nearest;
			h->link = L->it[nearest].link;
		} else if (exact) return;
		if (L->it[nearest].src >= 0) h->pos = pos_in_item(L, M, &L->it[nearest], x);
	}
}

int lay_word_at(const lay_doc *L, int pos, int *s, int *e)
{
	int a = pos, b = pos;
	uint32_t cp;
	if (pos < 0 || (size_t)pos > L->pt.n) return 0;
	/* forwards over word characters */
	while ((size_t)b < L->pt.n) {
		int k = t8_decode(L->pt.s + b, L->pt.n - (size_t)b, &cp);
		if (!t8_is_word(cp)) break;
		b += k;
	}
	/* backwards: step to each character's first byte */
	while (a > 0) {
		int q = a - 1;
		while (q > 0 && ((unsigned char)L->pt.s[q] & 0xC0) == 0x80) q--;
		t8_decode(L->pt.s + q, L->pt.n - (size_t)q, &cp);
		if (!t8_is_word(cp)) break;
		a = q;
	}
	if (a == b) return 0;
	*s = a;
	*e = b;
	return 1;
}

int lay_item_at_pos(const lay_doc *L, int pos)
{
	int i;
	for (i = 0; i < L->nit; i++) {
		const lay_item *it = &L->it[i];
		if ((it->kind == LI_TEXT || it->kind == LI_BOX || it->kind == LI_FORMULA) &&
		    it->src >= 0 && pos >= it->src && pos < it->src + (it->srclen ? it->srclen : 1)) return i;
	}
	return -1;
}

int lay_anchor_find(const lay_doc *L, int kind, const char *name)
{
	int i;
	for (i = 0; i < L->nan; i++) if (L->an[i].kind == kind && !strcmp(L->an[i].name, name)) return i;
	return -1;
}

int lay_current_entry(const lay_doc *L, int scroll)
{
	int i, cur = -1, first = -1;
	for (i = 0; i < L->nen; i++) {
		if (L->en[i].kind != LE_HEADING) continue;
		if (first < 0) first = i;
		if (L->en[i].y <= scroll + 6) cur = i;
	}
	return cur < 0 ? first : cur;
}

int lay_heading_entry(const lay_doc *L, int n)
{
	int i, k = -1;
	for (i = 0; i < L->nen; i++)
		if (L->en[i].kind == LE_HEADING && ++k == n) return i;
	return -1;
}

char *lay_text_range(const lay_doc *L, int s, int e)
{
	char *t;
	if (s > e) { int x = s; s = e; e = x; }
	if (s < 0) s = 0;
	if ((size_t)e > L->pt.n) e = (int)L->pt.n;
	if (!(t = (char *)malloc((size_t)(e - s) + 1))) return NULL;
	memcpy(t, L->pt.s + s, (size_t)(e - s));
	t[e - s] = 0;
	return t;
}
