/* kind_canvas.c: the canvas kind (W1-CANVAS, plan E8; kind_canvas.h has the model, docs/kinds/CANVAS.md the screen
 * grammar and the verdicts). A canvas draws, in this order: its ground, the current level's layers (only their
 * visible parts are copied, so a masked layer's clip never leaks), then, clipped to the control, the items the
 * view shows (bottom first), the outline of the item under the pointer, and the open pop-up. Items are found
 * through a uniform grid over their base boxes: drawing and hit tests touch only the cells the view or the pointer
 * covers. A burst of pointer motion is drawn once: while more motion is queued a redraw waits for the last event. */
#include "kind_canvas.h"
#include "cursor.h"
#include <X11/keysym.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CV_DRAG_SLOP 3        /* pixels the pointer moves before a press becomes a drag (a click otherwise) */
#define CV_POP_W 220          /* the pop-up's width (narrower when the canvas is) */
#define CV_COORD_MAX 30000    /* X coordinates are 16-bit: drawn coordinates are kept inside this */

typedef struct cv_grid {
	int cell, gx, gy, ox, oy; /* cell size (base units), columns, rows, the grid's base origin */
	int *start, *ids;         /* cell c holds ids[start[c] .. start[c+1]) */
	int *big, nbig;           /* items over too many cells: tested always */
	unsigned *stamp;
	unsigned tick;
	int *out, capout;         /* the last query's result */
} cv_grid;

static cv_view **views;
static int nviews, capviews;
static cv_view *last_view, *drag_view;
static int dirty;             /* a redraw was put off while motion was queued */
static int canvas_kind = -1;
static const kind_ops canvas_ops;

/* ---- geometry */

int cv_lvc(const cv_model *m, int level, int base)
{
	long long s = m && level >= 0 && level < m->nlv ? m->lv[level].scale : 100, v = (long long)base * s;
	return (int)(v >= 0 ? v / 100 : -((-v + 99) / 100));
}

static int to_base(const cv_model *m, int level, int lv)
{
	long long s = m && level >= 0 && level < m->nlv ? m->lv[level].scale : 100, v = (long long)lv * 100;
	if (s <= 0) s = 100;
	return (int)(v >= 0 ? v / s : -((-v + s - 1) / s));
}

int cv_poly_contains(const int *xy, int n, int px, int py)
{
	/* crossing number at the pixel's centre, in doubled coordinates so no vertex or edge passes through it */
	long long tx = 2LL * px + 1, ty = 2LL * py + 1;
	int i, j, in = 0;
	for (i = 0, j = n - 1; i < n; j = i++) {
		long long xi = 2LL * xy[2 * i], yi = 2LL * xy[2 * i + 1], xj = 2LL * xy[2 * j], yj = 2LL * xy[2 * j + 1];
		if ((yi > ty) != (yj > ty)) {
			/* x of the edge at ty, compared without division: (tx - xi) * (yj - yi) < (xj - xi) * (ty - yi) */
			long long lhs = (tx - xi) * (yj - yi), rhs = (xj - xi) * (ty - yi);
			if (yj > yi ? lhs < rhs : lhs > rhs) in = !in;
		}
	}
	return in;
}

static long long cross3(const int *xy, int a, int b, int c)
{
	return (long long)(xy[2 * b] - xy[2 * a]) * (xy[2 * c + 1] - xy[2 * a + 1]) -
	       (long long)(xy[2 * b + 1] - xy[2 * a + 1]) * (xy[2 * c] - xy[2 * a]);
}

static int in_tri(const int *xy, int a, int b, int c, int p, long long sign)
{
	long long d1 = cross3(xy, a, b, p), d2 = cross3(xy, b, c, p), d3 = cross3(xy, c, a, p);
	return sign > 0 ? (d1 >= 0 && d2 >= 0 && d3 >= 0) : (d1 <= 0 && d2 <= 0 && d3 <= 0);
}

int cv_triangulate(const int *xy, int n, int *tri)
{
	int *idx, m = n, nt = 0, i, guard;
	long long area = 0;
	if (n < 3) return 0;
	idx = (int *)malloc((size_t)n * sizeof(int));
	if (!idx) return 0;
	for (i = 0; i < n; i++) {
		int j = (i + 1) % n;
		idx[i] = i;
		area += (long long)xy[2 * i] * xy[2 * j + 1] - (long long)xy[2 * j] * xy[2 * i + 1];
	}
	for (guard = 0; m > 3 && guard < 4 * n * n; guard++) {
		int k, found = 0;
		for (k = 0; k < m && !found; k++) {
			int a = idx[(k + m - 1) % m], b = idx[k], c = idx[(k + 1) % m], q, ok = 1;
			long long cr = cross3(xy, a, b, c);
			if (area == 0 || (area > 0 ? cr <= 0 : cr >= 0)) continue; /* a reflex (or flat) corner is no ear */
			for (q = 0; q < m && ok; q++) {
				int p = idx[q];
				if (p == a || p == b || p == c) continue;
				if (in_tri(xy, a, b, c, p, area)) ok = 0;
			}
			if (!ok) continue;
			tri[3 * nt] = a; tri[3 * nt + 1] = b; tri[3 * nt + 2] = c;
			nt++;
			memmove(idx + k, idx + k + 1, (size_t)(m - k - 1) * sizeof(int));
			m--;
			found = 1;
		}
		if (!found) break; /* not a simple polygon: the rest as a fan */
	}
	for (i = 1; i + 1 < m; i++) { tri[3 * nt] = idx[0]; tri[3 * nt + 1] = idx[i]; tri[3 * nt + 2] = idx[i + 1]; nt++; }
	free(idx);
	return nt;
}

/* ---- the model */

cv_model *cv_model_new(void)
{
	cv_model *m = (cv_model *)calloc(1, sizeof *m);
	if (m) m->ground = -1;
	return m;
}

static void grid_free(cv_model *m)
{
	cv_grid *g = m->grid;
	if (!g) return;
	free(g->start); free(g->ids); free(g->big); free(g->stamp); free(g->out);
	free(g);
	m->grid = NULL;
}

void cv_model_free(cv_model *m)
{
	int i;
	if (!m) return;
	for (i = 0; i < m->nit; i++) {
		cv_item *it = &m->it[i];
		free(it->pts); free(it->tri); free(it->arg); free(it->label); free(it->popup); free(it->jump);
	}
	free(m->it); free(m->lv); free(m->ly);
	grid_free(m);
	free(m);
}

void cv_model_changed(cv_model *m) { if (m) grid_free(m); }

#define GROW(arr, n, cap, type) \
	do { if ((n) == (cap)) { int nc_ = (cap) ? 2 * (cap) : 16; type *na_ = (type *)realloc((arr), (size_t)nc_ * sizeof(type)); \
	     if (!na_) return -1; (arr) = na_; (cap) = nc_; } } while (0)

int cv_add_level(cv_model *m, int scale, int w, int h)
{
	if (!m || scale <= 0 || scale > 100000 || w <= 0 || h <= 0) return -1;
	GROW(m->lv, m->nlv, m->caplv, cv_level);
	m->lv[m->nlv].scale = scale;
	m->lv[m->nlv].w = w;
	m->lv[m->nlv].h = h;
	return m->nlv++;
}

int cv_add_layer(cv_model *m, int level, const char *asset, int x, int y, unsigned flags)
{
	cv_layer *l;
	if (!m || !asset || !asset[0] || strlen(asset) >= SCR_ASSET || level < 0) return -1;
	GROW(m->ly, m->nly, m->caply, cv_layer);
	l = &m->ly[m->nly];
	memset(l, 0, sizeof *l);
	l->level = level;
	snprintf(l->asset, sizeof l->asset, "%s", asset);
	l->x = x;
	l->y = y;
	l->flags = flags;
	return m->nly++;
}

static cv_item *new_item(cv_model *m)
{
	cv_item *it;
	if (m->nit == m->capit) {
		int nc = m->capit ? 2 * m->capit : 64;
		cv_item *na = (cv_item *)realloc(m->it, (size_t)nc * sizeof(cv_item));
		if (!na) return NULL;
		m->it = na;
		m->capit = nc;
	}
	it = &m->it[m->nit++];
	memset(it, 0, sizeof *it);
	it->max_level = INT_MAX;
	it->action = A_NONE;
	it->fill = it->line = it->hot_fill = it->hot_line = it->fg = -1;
	it->font = F_UI;
	it->cursor = -1;
	grid_free(m);
	return it;
}

int cv_add_rect(cv_model *m, int x, int y, int w, int h)
{
	cv_item *it;
	if (!m || w <= 0 || h <= 0 || !(it = new_item(m))) return -1;
	it->shape = CV_RECT;
	it->x = x; it->y = y; it->w = w; it->h = h;
	return m->nit - 1;
}

int cv_add_poly(cv_model *m, const int *xy, int npts)
{
	cv_item *it;
	int i, x0 = INT_MAX, y0 = INT_MAX, x1 = INT_MIN, y1 = INT_MIN;
	if (!m || !xy || npts < 3 || npts > 4096) return -1;
	if (!(it = new_item(m))) return -1;
	it->shape = CV_POLY;
	it->pts = (int *)malloc((size_t)npts * 2 * sizeof(int));
	it->tri = (int *)malloc((size_t)(npts - 2) * 3 * sizeof(int));
	if (!it->pts || !it->tri) { free(it->pts); free(it->tri); m->nit--; return -1; }
	memcpy(it->pts, xy, (size_t)npts * 2 * sizeof(int));
	it->npts = npts;
	it->ntri = cv_triangulate(xy, npts, it->tri);
	for (i = 0; i < npts; i++) {
		if (xy[2 * i] < x0) x0 = xy[2 * i];
		if (xy[2 * i] > x1) x1 = xy[2 * i];
		if (xy[2 * i + 1] < y0) y0 = xy[2 * i + 1];
		if (xy[2 * i + 1] > y1) y1 = xy[2 * i + 1];
	}
	it->x = x0; it->y = y0; it->w = x1 - x0 + 1; it->h = y1 - y0 + 1;
	return m->nit - 1;
}

static int setstr(char **dst, const char *src)
{
	char *d;
	size_t n;
	if (!src) return 0;
	n = strlen(src) + 1;
	d = (char *)malloc(n);
	if (!d) return -1;
	memcpy(d, src, n);
	free(*dst);
	*dst = d;
	return 0;
}

int cv_set_text(cv_model *m, int item, const char *label, const char *popup, const char *jump)
{
	cv_item *it;
	if (!m || item < 0 || item >= m->nit) return -1;
	it = &m->it[item];
	return setstr(&it->label, label) | setstr(&it->popup, popup) | setstr(&it->jump, jump);
}

int cv_set_action(cv_model *m, int item, int action, const char *arg)
{
	if (!m || item < 0 || item >= m->nit) return -1;
	m->it[item].action = action;
	return setstr(&m->it[item].arg, arg ? arg : "");
}

/* a model with no level gets one at 100% the size of its content */
static void ensure_level(cv_model *m)
{
	int i, w = 1, h = 1;
	if (m->nlv) return;
	for (i = 0; i < m->nit; i++) {
		if (m->it[i].x + m->it[i].w > w) w = m->it[i].x + m->it[i].w;
		if (m->it[i].y + m->it[i].h > h) h = m->it[i].y + m->it[i].h;
	}
	for (i = 0; i < m->nly; i++)
		if (!(m->ly[i].flags & (CV_REPEAT_X | CV_REPEAT_Y))) {
			if (m->ly[i].x + 1 > w) w = m->ly[i].x + 1;
			if (m->ly[i].y + 1 > h) h = m->ly[i].y + 1;
		}
	cv_add_level(m, 100, w, h);
}

/* ---- the grid */

static int grid_build(cv_model *m)
{
	cv_grid *g;
	int i, x0 = INT_MAX, y0 = INT_MAX, x1 = INT_MIN, y1 = INT_MIN, span, c, n;
	int *cnt;
	if (m->grid) return 0;
	g = (cv_grid *)calloc(1, sizeof *g);
	if (!g) return -1;
	for (i = 0; i < m->nit; i++) {
		const cv_item *it = &m->it[i];
		if (it->x < x0) x0 = it->x;
		if (it->y < y0) y0 = it->y;
		if (it->x + it->w > x1) x1 = it->x + it->w;
		if (it->y + it->h > y1) y1 = it->y + it->h;
	}
	if (!m->nit) { x0 = y0 = 0; x1 = y1 = 1; }
	span = x1 - x0 > y1 - y0 ? x1 - x0 : y1 - y0;
	g->cell = span / 256 + 1;
	if (g->cell < 32) g->cell = 32;
	g->ox = x0;
	g->oy = y0;
	g->gx = (x1 - x0) / g->cell + 1;
	g->gy = (y1 - y0) / g->cell + 1;
	n = g->gx * g->gy;
	cnt = (int *)calloc((size_t)n + 1, sizeof(int));
	g->start = (int *)calloc((size_t)n + 1, sizeof(int));
	g->stamp = (unsigned *)calloc((size_t)(m->nit ? m->nit : 1), sizeof(unsigned));
	g->big = (int *)malloc((size_t)(m->nit ? m->nit : 1) * sizeof(int));
	if (!cnt || !g->start || !g->stamp || !g->big) { free(cnt); m->grid = g; grid_free(m); return -1; }
	/* two passes: count each cell's items, then place them (in item order, so every cell lists bottom first) */
	for (c = 0; c < 2; c++) {
		if (c == 1) {
			for (i = 0; i < n; i++) g->start[i + 1] = g->start[i] + cnt[i];
			g->ids = (int *)malloc((size_t)(g->start[n] ? g->start[n] : 1) * sizeof(int));
			if (!g->ids) { free(cnt); m->grid = g; grid_free(m); return -1; }
			memset(cnt, 0, (size_t)n * sizeof(int));
			g->nbig = 0;
		}
		for (i = 0; i < m->nit; i++) {
			const cv_item *it = &m->it[i];
			int cx0 = (it->x - g->ox) / g->cell, cy0 = (it->y - g->oy) / g->cell;
			int cx1 = (it->x + it->w - 1 - g->ox) / g->cell, cy1 = (it->y + it->h - 1 - g->oy) / g->cell, cx, cy;
			if ((long)(cx1 - cx0 + 1) * (cy1 - cy0 + 1) > 64) { if (c == 1) g->big[g->nbig++] = i; continue; }
			for (cy = cy0; cy <= cy1; cy++)
				for (cx = cx0; cx <= cx1; cx++) {
					int k = cy * g->gx + cx;
					if (c == 1) g->ids[g->start[k] + cnt[k]] = i;
					cnt[k]++;
				}
		}
	}
	free(cnt);
	m->grid = g;
	return 0;
}

static int cmp_int(const void *a, const void *b) { int x = *(const int *)a, y = *(const int *)b; return x < y ? -1 : x > y; }

/* the items whose base boxes meet base rectangle [bx0,bx1) x [by0,by1), bottom first */
static int grid_query(cv_model *m, int bx0, int by0, int bx1, int by1, int **out)
{
	cv_grid *g;
	int cx0, cy0, cx1, cy1, cx, cy, n = 0, k;
	if (grid_build(m)) return 0;
	g = m->grid;
	if (g->capout < m->nit) {
		int *no = (int *)realloc(g->out, (size_t)(m->nit ? m->nit : 1) * sizeof(int));
		if (!no) return 0;
		g->out = no;
		g->capout = m->nit;
	}
	if (++g->tick == 0) { memset(g->stamp, 0, (size_t)m->nit * sizeof(unsigned)); g->tick = 1; }
	cx0 = (bx0 - g->ox) / g->cell; cy0 = (by0 - g->oy) / g->cell;
	cx1 = (bx1 - 1 - g->ox) / g->cell; cy1 = (by1 - 1 - g->oy) / g->cell;
	if (bx0 < g->ox) cx0 = 0;
	if (by0 < g->oy) cy0 = 0;
	if (cx1 >= g->gx) cx1 = g->gx - 1;
	if (cy1 >= g->gy) cy1 = g->gy - 1;
	if (bx1 - 1 >= g->ox && by1 - 1 >= g->oy)
		for (cy = cy0; cy <= cy1; cy++)
			for (cx = cx0; cx <= cx1; cx++) {
				int c = cy * g->gx + cx;
				for (k = g->start[c]; k < g->start[c + 1]; k++) {
					int id = g->ids[k];
					if (g->stamp[id] == g->tick) continue;
					g->stamp[id] = g->tick;
					g->out[n++] = id;
				}
			}
	for (k = 0; k < g->nbig; k++) g->out[n++] = g->big[k];
	qsort(g->out, (size_t)n, sizeof(int), cmp_int);
	*out = g->out;
	return n;
}

/* ---- views */

int cv_surface_w(const cv_view *v) { return v->m && v->level < v->m->nlv ? v->m->lv[v->level].w : v->w; }
int cv_surface_h(const cv_view *v) { return v->m && v->level < v->m->nlv ? v->m->lv[v->level].h : v->h; }

static void clamp(cv_view *v)
{
	int sw = cv_surface_w(v), sh = cv_surface_h(v);
	if (v->vx > sw - v->w) v->vx = sw - v->w;
	if (v->vy > sh - v->h) v->vy = sh - v->h;
	if (v->vx < 0) v->vx = 0;
	if (v->vy < 0) v->vy = 0;
}

void cv_pan(cv_view *v, int dx, int dy)
{
	if (!v) return;
	v->vx += dx;
	v->vy += dy;
	clamp(v);
}

void cv_zoom(cv_view *v, int level, int ax, int ay)
{
	int bx, by;
	if (!v || !v->m) return;
	ensure_level(v->m);
	if (level < 0) level = 0;
	if (level >= v->m->nlv) level = v->m->nlv - 1;
	if (level == v->level) return;
	if (ax < 0 || ay < 0) { ax = v->w / 2; ay = v->h / 2; }
	bx = to_base(v->m, v->level, v->vx + ax);
	by = to_base(v->m, v->level, v->vy + ay);
	v->level = level;
	v->vx = cv_lvc(v->m, level, bx) - ax;
	v->vy = cv_lvc(v->m, level, by) - ay;
	v->hot = -1;
	clamp(v);
}

void cv_center(cv_view *v, int bx, int by)
{
	if (!v || !v->m) return;
	ensure_level(v->m);
	v->vx = cv_lvc(v->m, v->level, bx) - v->w / 2;
	v->vy = cv_lvc(v->m, v->level, by) - v->h / 2;
	clamp(v);
}

static int shown_at(const cv_item *it, int level) { return level >= it->min_level && level <= it->max_level; }

/* an item's box on the current level's surface: [x0,x1) x [y0,y1) */
static void item_box(const cv_view *v, const cv_item *it, int *x0, int *y0, int *x1, int *y1)
{
	*x0 = cv_lvc(v->m, v->level, it->x);
	*y0 = cv_lvc(v->m, v->level, it->y);
	*x1 = cv_lvc(v->m, v->level, it->x + it->w);
	*y1 = cv_lvc(v->m, v->level, it->y + it->h);
	if (*x1 <= *x0) *x1 = *x0 + 1;
	if (*y1 <= *y0) *y1 = *y0 + 1;
}

static int item_hit(const cv_view *v, const cv_item *it, int sx, int sy)
{
	int x0, y0, x1, y1, k, buf[64], *xy = buf, in;
	item_box(v, it, &x0, &y0, &x1, &y1);
	if (sx < x0 || sy < y0 || sx >= x1 || sy >= y1) return 0;
	if (it->shape != CV_POLY) return 1;
	if (it->npts > 32 && !(xy = (int *)malloc((size_t)it->npts * 2 * sizeof(int)))) return 0;
	for (k = 0; k < 2 * it->npts; k++) xy[k] = cv_lvc(v->m, v->level, it->pts[k]);
	in = cv_poly_contains(xy, it->npts, sx, sy);
	if (xy != buf) free(xy);
	return in;
}

int cv_item_at(cv_view *v, int x, int y)
{
	int *ids, n, k, sx, sy, bx, by;
	if (!v || !v->m || x < 0 || y < 0 || x >= v->w || y >= v->h) return -1;
	ensure_level(v->m);
	sx = v->vx + x;
	sy = v->vy + y;
	bx = to_base(v->m, v->level, sx);
	by = to_base(v->m, v->level, sy);
	/* the base cell of the point, one unit either side for the rounding between level and base */
	n = grid_query(v->m, bx - 1, by - 1, bx + 2, by + 2, &ids);
	for (k = n - 1; k >= 0; k--) {
		const cv_item *it = &v->m->it[ids[k]];
		if (shown_at(it, v->level) && item_hit(v, it, sx, sy)) return ids[k];
	}
	return -1;
}

int cv_visible(cv_view *v, const int **out)
{
	int *ids, n, k, j = 0;
	if (!v || !v->m) return 0;
	ensure_level(v->m);
	n = grid_query(v->m, to_base(v->m, v->level, v->vx) - 1, to_base(v->m, v->level, v->vy) - 1,
	               to_base(v->m, v->level, v->vx + v->w) + 2, to_base(v->m, v->level, v->vy + v->h) + 2, &ids);
	for (k = 0; k < n; k++) {
		const cv_item *it = &v->m->it[ids[k]];
		int x0, y0, x1, y1;
		if (!shown_at(it, v->level)) continue;
		item_box(v, it, &x0, &y0, &x1, &y1);
		if (x1 <= v->vx || y1 <= v->vy || x0 >= v->vx + v->w || y0 >= v->vy + v->h) continue;
		ids[j++] = ids[k];
	}
	*out = ids;
	return j;
}

int cv_edge_dir(const cv_view *v, int x, int y)
{
	int b, l, r, t, d;
	if (!v || v->edge_band <= 0 || x < 0 || y < 0 || x >= v->w || y >= v->h) return -1;
	b = v->edge_band;
	l = x < b; r = x >= v->w - b; t = y < b; d = y >= v->h - b;
	if (t && l) return CUR_PAN_NW;
	if (t && r) return CUR_PAN_NE;
	if (d && l) return CUR_PAN_SW;
	if (d && r) return CUR_PAN_SE;
	if (l) return CUR_PAN_W;
	if (r) return CUR_PAN_E;
	if (t) return CUR_PAN_N;
	if (d) return CUR_PAN_S;
	return -1;
}

static void edge_vector(int dir, int step, int *dx, int *dy)
{
	*dx = *dy = 0;
	if (dir == CUR_PAN_W || dir == CUR_PAN_NW || dir == CUR_PAN_SW) *dx = -step;
	if (dir == CUR_PAN_E || dir == CUR_PAN_NE || dir == CUR_PAN_SE) *dx = step;
	if (dir == CUR_PAN_N || dir == CUR_PAN_NW || dir == CUR_PAN_NE) *dy = -step;
	if (dir == CUR_PAN_S || dir == CUR_PAN_SW || dir == CUR_PAN_SE) *dy = step;
}

/* ---- the screen file's rows */

static long colour_of(e26_app *a, const char *s, int *bad)
{
	long v;
	if (scr_parse_colour(s, &v)) { *bad = 1; return -1; }
	return SCR_IS_UI(v) ? ui_rgb(a, (int)(v - SCR_UI_BASE)) : v;
}

static int font_of(const char *s)
{
	int f;
	for (f = 0; f < F_ART_SIZE; f++)
		if (!strcmp(scr_font_name(f), s)) return f;
	return -1;
}

static int num(const char *s, int *v)
{
	char *e;
	long x = strtol(s, &e, 10);
	if (!*s || *e || x < -1000000000L || x > 1000000000L) return -1;
	*v = (int)x;
	return 0;
}

/* "action <name> <arg>": a registered action, or a named one (a dot) declared here and left on the notice fallback */
static int action_of(const char *name)
{
	int id = reg_find(REG_ACTION, name);
	if (id < 0 && strchr(name, '.')) id = reg_declare(REG_ACTION, name);
	return id;
}

static int item_options(e26_app *a, cv_model *m, int item, char **v, int n, char *err, size_t en)
{
	cv_item *it = &m->it[item];
	int k = 0, bad = 0;
	while (k < n) {
		const char *o = v[k];
		if (!strcmp(o, "show-label")) { it->show_label = 1; k++; continue; }
		if (k + 1 >= n) { snprintf(err, en, "option %s needs a value", o); return -1; }
		if (!strcmp(o, "action")) {
			int id;
			if (k + 2 >= n) { snprintf(err, en, "action needs a name and an argument"); return -1; }
			if ((id = action_of(v[k + 1])) < 0) { snprintf(err, en, "unknown action %s", v[k + 1]); return -1; }
			cv_set_action(m, item, id, v[k + 2]);
			k += 3;
			continue;
		}
		if (!strcmp(o, "popup")) setstr(&it->popup, v[k + 1]);
		else if (!strcmp(o, "jump")) setstr(&it->jump, v[k + 1]);
		else if (!strcmp(o, "fill")) it->fill = colour_of(a, v[k + 1], &bad);
		else if (!strcmp(o, "line")) it->line = colour_of(a, v[k + 1], &bad);
		else if (!strcmp(o, "hot-fill")) it->hot_fill = colour_of(a, v[k + 1], &bad);
		else if (!strcmp(o, "hot-line")) it->hot_line = colour_of(a, v[k + 1], &bad);
		else if (!strcmp(o, "fg")) it->fg = colour_of(a, v[k + 1], &bad);
		else if (!strcmp(o, "font")) { if ((it->font = font_of(v[k + 1])) < 0) { snprintf(err, en, "unknown font %s", v[k + 1]); return -1; } }
		else if (!strcmp(o, "cursor")) { if ((it->cursor = cursor_by_name(v[k + 1])) < 0) { snprintf(err, en, "unknown cursor %s", v[k + 1]); return -1; } }
		else if (!strcmp(o, "levels")) {
			if (k + 2 >= n || num(v[k + 1], &it->min_level) || num(v[k + 2], &it->max_level)) { snprintf(err, en, "levels needs two numbers"); return -1; }
			k++;
		} else { snprintf(err, en, "unknown option %s", o); return -1; }
		if (bad) { snprintf(err, en, "bad colour %s", v[k + 1]); return -1; }
		k += 2;
	}
	return 0;
}

static int parse_row(e26_app *a, cv_model *m, char **v, int n, char *err, size_t en)
{
	int x, y, w, h, i;
	if (n < 1) { snprintf(err, en, "empty row"); return -1; }
	if (!strcmp(v[0], "level")) {
		if (n != 4 || num(v[1], &x) || num(v[2], &w) || num(v[3], &h) || cv_add_level(m, x, w, h) < 0) {
			snprintf(err, en, "level needs a scale (percent), a width and a height");
			return -1;
		}
		return 0;
	}
	if (!strcmp(v[0], "layer")) {
		unsigned fl = 0;
		if (n < 5 || num(v[1], &i) || num(v[3], &x) || num(v[4], &y)) { snprintf(err, en, "layer needs a level, an asset, x and y"); return -1; }
		for (w = 5; w < n; w++) {
			if (!strcmp(v[w], "masked")) fl |= CV_MASKED;
			else if (!strcmp(v[w], "repeat-x")) fl |= CV_REPEAT_X;
			else if (!strcmp(v[w], "repeat-y")) fl |= CV_REPEAT_Y;
			else if (!strcmp(v[w], "repeat")) fl |= CV_REPEAT_X | CV_REPEAT_Y;
			else { snprintf(err, en, "unknown layer flag %s", v[w]); return -1; }
		}
		if (cv_add_layer(m, i, v[2], x, y, fl) < 0) { snprintf(err, en, "bad layer"); return -1; }
		return 0;
	}
	if (!strcmp(v[0], "rect")) {
		if (n < 6 || num(v[1], &x) || num(v[2], &y) || num(v[3], &w) || num(v[4], &h) || (i = cv_add_rect(m, x, y, w, h)) < 0) {
			snprintf(err, en, "rect needs x, y, width, height and a label");
			return -1;
		}
		cv_set_text(m, i, v[5], NULL, NULL);
		return item_options(a, m, i, v + 6, n - 6, err, en);
	}
	if (!strcmp(v[0], "poly")) {
		int pts[2 * 128], np = 0;
		const char *p = n > 1 ? v[1] : "";
		while (*p) {
			char *e;
			long px, py;
			while (*p == ' ') p++;
			if (!*p) break;
			px = strtol(p, &e, 10);
			if (e == p || *e != ',' || np == 128) { np = -1; break; }
			p = e + 1;
			py = strtol(p, &e, 10);
			if (e == p) { np = -1; break; }
			pts[2 * np] = (int)px; pts[2 * np + 1] = (int)py;
			np++;
			p = e;
		}
		if (n < 3 || np < 3 || (i = cv_add_poly(m, pts, np)) < 0) {
			snprintf(err, en, "poly needs \"x,y x,y x,y ...\" (3 to 128 points) and a label");
			return -1;
		}
		cv_set_text(m, i, v[2], NULL, NULL);
		return item_options(a, m, i, v + 3, n - 3, err, en);
	}
	snprintf(err, en, "unknown row %s (level, layer, rect, poly)", v[0]);
	return -1;
}

int cv_parse_rows(e26_app *a, cv_model *m, const scr_control *c, char *err, size_t n)
{
	int r, rc = 0;
	char e[200];
	if (err && n) err[0] = 0;
	for (r = 0; r < c->nrows; r++)
		if (parse_row(a, m, c->rows[r].v, c->rows[r].n, e, sizeof e)) {
			if (!rc && err) snprintf(err, n, "control %s, data row %d: %s", c->id, r + 1, e);
			rc = -1;
		}
	ensure_level(m);
	return rc;
}

static cv_view *view_new(const char *screen, const char *ctl)
{
	cv_view *v;
	if (nviews == capviews) {
		int nc = capviews ? 2 * capviews : 8;
		cv_view **nv = (cv_view **)realloc(views, (size_t)nc * sizeof(cv_view *));
		if (!nv) return NULL;
		views = nv;
		capviews = nc;
	}
	v = (cv_view *)calloc(1, sizeof *v);
	if (!v) return NULL;
	snprintf(v->screen, sizeof v->screen, "%s", screen);
	snprintf(v->ctl, sizeof v->ctl, "%s", ctl);
	v->hot = v->popup = v->edge = v->anim = -1;
	v->step = 16;
	v->hot_line = -1;
	v->pop_bg = -1;
	v->pop_jump_y = -1;
	views[nviews++] = v;
	return v;
}

cv_view *cv_find(const char *screen, const char *ctl)
{
	int i;
	for (i = 0; i < nviews; i++)
		if (!strcmp(views[i]->screen, screen) && !strcmp(views[i]->ctl, ctl)) return views[i];
	return NULL;
}

cv_view *cv_last(void) { return last_view; }

/* the control's props: its behaviour, and where the view starts */
static void view_props(e26_app *a, cv_view *v, const scr_control *c)
{
	const char *p;
	int bad = 0, x, y, l;
	if ((p = scr_prop_get(c, "step")) && !num(p, &x) && x > 0) v->step = x;
	if ((p = scr_prop_get(c, "edge-pan")) && !num(p, &x) && x >= 0) v->edge_band = x;
	if ((p = scr_prop_get(c, "click")) && !strcmp(p, "zoom")) v->click_zoom = 1;
	if ((p = scr_prop_get(c, "readout")) && !strcmp(p, "coords")) v->readout_coords = 1;
	if ((p = scr_prop_get(c, "hot-line"))) v->hot_line = colour_of(a, p, &bad);
	if ((p = scr_prop_get(c, "popup-ground"))) v->pop_bg = colour_of(a, p, &bad);
	if (v->own && (p = scr_prop_get(c, "ground"))) v->m->ground = colour_of(a, p, &bad);
	if (bad) fprintf(stderr, "d32library: canvas %s.%s: a bad colour in its props\n", v->screen, v->ctl);
	if ((p = scr_prop_get(c, "start")) && sscanf(p, "%d %d %d", &l, &x, &y) == 3) {
		cv_zoom(v, l, -1, -1);
		cv_center(v, x, y);
	}
}

cv_view *cv_view_of(e26_app *a, scr_screen *s, int ctl)
{
	const scr_control *c = &s->c[ctl];
	cv_view *v = cv_find(s->name, c->id);
	if (!v && !(v = view_new(s->name, c->id))) return NULL;
	v->fx = c->x + (c->absolute ? 0 : s->ox);
	v->fy = c->y + (c->absolute ? 0 : s->oy);
	if (v->w != c->w || v->h != c->h) { v->w = c->w; v->h = c->h; if (v->m) clamp(v); }
	if (!v->m) {
		char err[300];
		v->m = cv_model_new();
		v->own = 1;
		if (v->m && cv_parse_rows(a, v->m, c, err, sizeof err)) fprintf(stderr, "d32library: canvas %s: %s\n", s->name, err);
	}
	if (!v->propped && v->m) { v->propped = 1; view_props(a, v, c); }
	return v;
}

int cv_bind(e26_app *a, const char *screen, const char *ctl, cv_model *m)
{
	cv_view *v = cv_find(screen, ctl);
	scr_screen *s = a ? scr_find(&a->set, screen) : NULL;
	scr_control *c = s ? scr_control_find(s, ctl) : NULL;
	if (!m || (!v && !(v = view_new(screen, ctl)))) return -1;
	if (v->own) cv_model_free(v->m);
	v->m = m;
	v->own = 0;
	v->hot = v->popup = -1;
	ensure_level(m);
	if (v->level >= m->nlv) v->level = m->nlv - 1;
	if (c) {
		v->w = c->w;
		v->h = c->h;
		v->propped = 1;
		view_props(a, v, c);
	}
	clamp(v);
	return 0;
}

/* ---- drawing */

static int clampc(int v) { return v < -CV_COORD_MAX ? -CV_COORD_MAX : v > CV_COORD_MAX ? CV_COORD_MAX : v; }

static void draw_layers(e26_app *a, gfx_ctx *g, cv_view *v)
{
	int i;
	for (i = 0; i < v->m->nly; i++) {
		const cv_layer *l = &v->m->ly[i];
		xasset *x;
		int tx0, ty0, tx1, ty1, tx, ty;
		if (l->level != v->level || !(x = xa_get(a, l->asset)) || x->w <= 0 || x->h <= 0) continue;
		/* the copies of the layer that meet the view: one, or a row, column or grid of them when it repeats */
		tx0 = tx1 = l->x;
		ty0 = ty1 = l->y;
		if (l->flags & CV_REPEAT_X) {
			tx0 = l->x + ((v->vx - l->x) >= 0 ? (v->vx - l->x) / x->w : -((l->x - v->vx + x->w - 1) / x->w)) * x->w;
			tx1 = v->vx + v->w;
		}
		if (l->flags & CV_REPEAT_Y) {
			ty0 = l->y + ((v->vy - l->y) >= 0 ? (v->vy - l->y) / x->h : -((l->y - v->vy + x->h - 1) / x->h)) * x->h;
			ty1 = v->vy + v->h;
		}
		for (ty = ty0; ty <= ty1; ty += x->h) {
			for (tx = tx0; tx <= tx1; tx += x->w) {
				/* the visible part of this copy, copied exactly: no clip is needed */
				int sx0 = tx > v->vx ? tx : v->vx, sy0 = ty > v->vy ? ty : v->vy;
				int sx1 = tx + x->w < v->vx + v->w ? tx + x->w : v->vx + v->w;
				int sy1 = ty + x->h < v->vy + v->h ? ty + x->h : v->vy + v->h;
				if (sx1 > sx0 && sy1 > sy0) {
					if (l->flags & CV_MASKED)
						gfx_blit(g, x, sx0 - tx, sy0 - ty, sx1 - sx0, sy1 - sy0, v->fx + sx0 - v->vx, v->fy + sy0 - v->vy);
					else
						gfx_copy(g, x, sx0 - tx, sy0 - ty, sx1 - sx0, sy1 - sy0, v->fx + sx0 - v->vx, v->fy + sy0 - v->vy);
				}
				if (!(l->flags & CV_REPEAT_X)) break;
			}
			if (!(l->flags & CV_REPEAT_Y)) break;
		}
	}
}

static void poly_frame(cv_view *v, const cv_item *it, int *xy)
{
	int k;
	for (k = 0; k < it->npts; k++) {
		xy[2 * k] = clampc(v->fx + cv_lvc(v->m, v->level, it->pts[2 * k]) - v->vx);
		xy[2 * k + 1] = clampc(v->fy + cv_lvc(v->m, v->level, it->pts[2 * k + 1]) - v->vy);
	}
}

static void draw_shape(gfx_ctx *g, cv_view *v, const cv_item *it, long fill, long line)
{
	int x0, y0, x1, y1;
	if (fill < 0 && line < 0) return;
	item_box(v, it, &x0, &y0, &x1, &y1);
	x0 = clampc(v->fx + x0 - v->vx); x1 = clampc(v->fx + x1 - v->vx);
	y0 = clampc(v->fy + y0 - v->vy); y1 = clampc(v->fy + y1 - v->vy);
	if (it->shape != CV_POLY) {
		if (fill >= 0) gfx_fill(g, x0, y0, x1 - x0, y1 - y0, fill);
		if (line >= 0) gfx_frame2(g, x0, y0, x1 - x0, y1 - y0, line, line);
		return;
	} else {
		int buf[64], *xy = buf, k;
		if (it->npts > 32 && !(xy = (int *)malloc((size_t)it->npts * 2 * sizeof(int)))) return;
		poly_frame(v, it, xy);
		if (fill >= 0)
			for (k = 0; k < it->ntri; k++) {
				int t[6];
				t[0] = xy[2 * it->tri[3 * k]]; t[1] = xy[2 * it->tri[3 * k] + 1];
				t[2] = xy[2 * it->tri[3 * k + 1]]; t[3] = xy[2 * it->tri[3 * k + 1] + 1];
				t[4] = xy[2 * it->tri[3 * k + 2]]; t[5] = xy[2 * it->tri[3 * k + 2] + 1];
				gfx_poly(g, t, 3, fill);
			}
		if (line >= 0)
			for (k = 0; k < it->npts; k++) {
				int j = (k + 1) % it->npts;
				gfx_line(g, xy[2 * k], xy[2 * k + 1], xy[2 * j], xy[2 * j + 1], line);
			}
		if (xy != buf) free(xy);
	}
}

/* lines gfx_wrap_lines makes of s in width w (the same breaking rule) */
static int wrap_count(gfx_ctx *g, int font, const char *s, int w)
{
	int len = (int)strlen(s), start = 0, lines = 0;
	while (start < len) {
		int end = start, last = -1;
		while (end < len && s[end] != '\n') {
			if (s[end] == ' ') last = end;
			if (gfx_seg_width(g, font, s + start, end - start + 1) > w && last > start) { end = last; break; }
			end++;
		}
		lines++;
		start = end;
		while (start < len && (s[start] == ' ' || s[start] == '\n')) start++;
	}
	return lines;
}

/* the pop-up, anchored under its item (above it when there is no room below), kept inside the canvas: a parchment
 * box with a black frame, the item's name in the bold face, its text, and its jump line in the jump colour */
static void draw_popup(e26_app *a, gfx_ctx *g, cv_view *v)
{
	const cv_item *it = &v->m->it[v->popup];
	int x0, y0, x1, y1, pw, ph, px, py, lh = gfx_font_height(g, F_UI) + 1, lines, by;
	const char *title = it->label ? it->label : "", *text = it->popup ? it->popup : "";
	const char *jump = it->action != A_NONE ? (it->jump && it->jump[0] ? it->jump : "Go to article") : NULL;
	v->pop_w = 0;
	v->pop_jump_y = -1;
	item_box(v, it, &x0, &y0, &x1, &y1);
	if (x1 <= v->vx || y1 <= v->vy || x0 >= v->vx + v->w || y0 >= v->vy + v->h) return; /* its item is out of view */
	x0 += v->fx - v->vx; x1 += v->fx - v->vx; y0 += v->fy - v->vy; y1 += v->fy - v->vy;
	pw = v->w - 8 < CV_POP_W ? v->w - 8 : CV_POP_W;
	if (pw < 40) return;
	lines = text[0] ? wrap_count(g, F_UI, text, pw - 12) : 0;
	ph = 6 + gfx_font_height(g, F_UI_BOLD) + 2 + lines * lh + (jump ? lh + 4 : 0) + 5;
	px = x0;
	if (px + pw > v->fx + v->w - 4) px = v->fx + v->w - 4 - pw;
	if (px < v->fx + 4) px = v->fx + 4;
	py = y1 + 4;
	if (py + ph > v->fy + v->h - 4) py = y0 - 4 - ph;
	if (py < v->fy + 4) py = v->fy + 4;
	gfx_fill(g, px, py, pw, ph, v->pop_bg >= 0 ? v->pop_bg : ui_rgb(a, UI_PAPER));
	gfx_frame2(g, px, py, pw, ph, C_BLACK, C_BLACK);
	by = py + 6 + gfx_ascent(g, F_UI_BOLD);
	{
		int n = (int)strlen(title);
		while (n > 0 && gfx_text_width(g, F_UI_BOLD, title, n) > pw - 12) n--;
		gfx_text(g, px + 6, by, F_UI_BOLD, C_BLACK, title, n);
	}
	by += gfx_descent(g, F_UI_BOLD) + 2 + gfx_ascent(g, F_UI);
	if (lines) {
		gfx_wrap_lines(g, px + 6, by, pw - 12, F_UI, C_BLACK, text, AL_LEFT, lh);
		by += lines * lh;
	}
	if (jump) {
		by += 4;
		gfx_text(g, px + 6, by, F_UI, C_JUMP, jump, -1);
		v->pop_jump_y = by - gfx_ascent(g, F_UI);
	}
	v->pop_x = px; v->pop_y = py; v->pop_w = pw; v->pop_h = ph;
}

static void draw(e26_app *a, gfx_ctx *g, scr_screen *s, int i, int x, int y, int under)
{
	const scr_control *c = &s->c[i];
	cv_view *v = cv_view_of(a, s, i);
	const int *ids;
	int n, k;
	(void)under;
	if (!v || !v->m) return;
	v->fx = x;
	v->fy = y;
	gfx_fill(g, x, y, c->w, c->h, v->m->ground >= 0 ? v->m->ground : c->bg >= 0 ? c->bg : C_BLACK);
	draw_layers(a, g, v);
	gfx_clip(g, x, y, c->w, c->h);
	n = cv_visible(v, &ids);
	for (k = 0; k < n; k++) {
		const cv_item *it = &v->m->it[ids[k]];
		if (v->m->draw_item) {
			int x0, y0, x1, y1;
			item_box(v, it, &x0, &y0, &x1, &y1);
			v->m->draw_item(a, g, v, it, ids[k] == v->hot, clampc(x + x0 - v->vx), clampc(y + y0 - v->vy), x1 - x0, y1 - y0);
			gfx_clip(g, x, y, c->w, c->h); /* a masked blit in the hook resets the clip */
			continue;
		}
		draw_shape(g, v, it, it->fill, it->line);
		if (it->show_label && it->label) {
			int x0, y0, x1, y1;
			item_box(v, it, &x0, &y0, &x1, &y1);
			gfx_text(g, clampc(x + x0 - v->vx + 3), clampc(y + y0 - v->vy + 2 + gfx_ascent(g, it->font)), it->font,
			         it->fg >= 0 ? it->fg : C_BLACK, it->label, -1);
		}
	}
	if (v->hot >= 0 && v->hot < v->m->nit && !v->m->draw_item) {
		const cv_item *it = &v->m->it[v->hot];
		if (shown_at(it, v->level)) draw_shape(g, v, it, it->hot_fill, it->hot_line >= 0 ? it->hot_line : v->hot_line);
	}
	if (v->popup >= 0 && v->popup < v->m->nit) draw_popup(a, g, v);
	gfx_unclip(g);
}

/* ---- the pointer */

/* 1 while more pointer motion is queued (a burst is drawn once, at its last event) */
static int motion_queued(e26_app *a)
{
	XEvent e;
	if (!a->dpy || XEventsQueued(a->dpy, QueuedAfterReading) == 0) return 0;
	XPeekEvent(a->dpy, &e);
	return e.type == MotionNotify;
}

static int in_popup(const cv_view *v, int x, int y)
{
	return v->popup >= 0 && v->pop_w > 0 && x >= v->pop_x && y >= v->pop_y && x < v->pop_x + v->pop_w && y < v->pop_y + v->pop_h;
}

static int on_jump(e26_app *a, const cv_view *v, int x, int y)
{
	return in_popup(v, x, y) && v->pop_jump_y >= 0 && y >= v->pop_jump_y && y < v->pop_jump_y + gfx_font_height(&a->g, F_UI) + 1;
}

static void readout(e26_app *a, cv_view *v, int x, int y)
{
	char buf[160];
	const char *t = NULL;
	int lx = x - v->fx, ly = y - v->fy;
	int bx = to_base(v->m, v->level, v->vx + lx), by = to_base(v->m, v->level, v->vy + ly);
	if (v->m->readout) t = v->m->readout(a, v, bx, by, v->hot, buf, sizeof buf);
	else if (v->hot >= 0 && v->m->it[v->hot].label) t = v->m->it[v->hot].label;
	else if (v->readout_coords && lx >= 0 && ly >= 0 && lx < v->w && ly < v->h) { snprintf(buf, sizeof buf, "%d, %d", bx, by); t = buf; }
	snprintf(v->readout, sizeof v->readout, "%s", t ? t : "");
}

static int hover(e26_app *a, scr_screen *s, int i, int x, int y)
{
	int k, changed = 0, hot;
	cv_view *v = NULL;
	char was[160];
	if (i >= 0 && (v = cv_view_of(a, s, i)) && v->m) {
		last_view = v;
		hot = in_popup(v, x, y) ? v->popup : cv_item_at(v, x - v->fx, y - v->fy);
		if (hot != v->hot) { v->hot = hot; changed = 1; }
		snprintf(was, sizeof was, "%s", v->readout);
		readout(a, v, x, y);
		if (strcmp(was, v->readout)) changed = 1;
	}
	for (k = 0; k < nviews; k++) /* every other canvas forgets its item under the pointer */
		if (views[k] != v && (views[k]->hot >= 0 || views[k]->readout[0])) {
			views[k]->hot = -1;
			views[k]->readout[0] = 0;
			changed = 1;
		}
	if (changed || dirty) {
		if (motion_queued(a)) { dirty = 1; return 0; }
		dirty = 0;
		return 1;
	}
	return 0;
}

static int edge_tick(e26_app *a, void *ud)
{
	cv_view *v = (cv_view *)ud;
	int dx, dy;
	if (a->drag_ops != &canvas_ops || drag_view != v || v->edge < 0) { v->anim = -1; return 0; }
	edge_vector(v->edge, v->step, &dx, &dy);
	cv_pan(v, dx, dy);
	nav_redraw(a);
	return 1;
}

static int press(e26_app *a, scr_screen *s, int i, int x, int y)
{
	cv_view *v = cv_view_of(a, s, i);
	if (!v || !v->m) return 0;
	last_view = drag_view = v;
	v->px = x; v->py = y; v->pvx = v->vx; v->pvy = v->vy;
	v->moved = 0;
	v->dragging = 1;
	v->edge = -1;
	v->edge_gesture = 0;
	a->drag_ops = &canvas_ops; /* the motion until the release is this canvas's (input.c) */
	a->drag_ctl = i;
	a->pressed = i;            /* so that the release comes back here */
	a->hot = i;
	/* the 97 Atlas's edge band: a press there pans that way, and keeps panning while the button is held */
	if (!in_popup(v, x, y) && cv_item_at(v, x - v->fx, y - v->fy) < 0 && (v->edge = cv_edge_dir(v, x - v->fx, y - v->fy)) >= 0) {
		int dx, dy;
		v->edge_gesture = 1;
		edge_vector(v->edge, v->step, &dx, &dy);
		cv_pan(v, dx, dy);
		if (v->anim < 0) v->anim = timer_animate(a, edge_tick, v);
		nav_redraw(a);
	}
	return 1;
}

static void drag(e26_app *a, int x, int y)
{
	cv_view *v = drag_view;
	int dx, dy;
	if (!v) return;
	if (v->edge_gesture) { /* an edge pan follows the pointer along the band, stops off it, and resumes on it */
		v->edge = cv_edge_dir(v, x - v->fx, y - v->fy);
		if (v->edge >= 0 && v->anim < 0) v->anim = timer_animate(a, edge_tick, v);
		return;
	}
	dx = x - v->px;
	dy = y - v->py;
	if (!v->moved && abs(dx) + abs(dy) < CV_DRAG_SLOP) return;
	if (!v->moved) { v->moved = 1; cursor_hold(CUR_PAN); }
	v->vx = v->pvx - dx;
	v->vy = v->pvy - dy;
	clamp(v);
	if (motion_queued(a)) { dirty = 1; return; }
	dirty = 0;
	nav_redraw(a);
}

static int run_item(e26_app *a, scr_screen *s, int i, const cv_item *it)
{
	e26_actx x;
	memset(&x, 0, sizeof x);
	x.s = s;
	x.ctl = i;
	x.arg = it->arg ? it->arg : "";
	x.label = it->label ? it->label : "";
	return action_run(a, it->action, &x);
}

static int release(e26_app *a, scr_screen *s, int i, int x, int y)
{
	cv_view *v = cv_view_of(a, s, i);
	int item;
	if (!v || !v->m) return 0;
	drag_view = NULL;
	v->dragging = 0;
	v->edge = -1;
	if (v->moved) { cursor_hold(-1); return 0; }
	if (v->edge_gesture) { v->edge_gesture = 0; return 0; }
	if (v->popup >= 0) {
		if (in_popup(v, x, y)) {
			if (on_jump(a, v, x, y) && v->m->it[v->popup].action != A_NONE) {
				const cv_item *it = &v->m->it[v->popup];
				v->popup = -1;
				if (run_item(a, s, i, it)) nav_redraw(a);
				return 1;
			}
			return 0;
		}
		v->popup = -1; /* a click elsewhere closes it, and goes on to what it hit */
	}
	item = cv_item_at(v, x - v->fx, y - v->fy);
	if (item >= 0) {
		const cv_item *it = &v->m->it[item];
		int bx = to_base(v->m, v->level, v->vx + x - v->fx), by = to_base(v->m, v->level, v->vy + y - v->fy);
		if (v->m->click && v->m->click(a, v, item, bx, by)) return 0;
		if (it->popup) { v->popup = item; return 0; }
		if (it->action != A_NONE) { if (run_item(a, s, i, it)) nav_redraw(a); return 1; }
		return 0;
	}
	if (v->click_zoom && v->level + 1 < v->m->nlv) cv_zoom(v, v->level + 1, x - v->fx, y - v->fy);
	return 0;
}

static int wheel(e26_app *a, scr_screen *s, int i, int dir)
{
	cv_view *v = cv_view_of(a, s, i);
	(void)a;
	if (!v || !v->m) return 0;
	/* down the surface when it is taller than the view, else along it (the Timeline's wall) */
	if (cv_surface_h(v) > v->h) cv_pan(v, 0, 3 * dir * v->step);
	else cv_pan(v, 3 * dir * v->step, 0);
	return 1;
}

static void open_hot(e26_app *a, scr_screen *s, int i, cv_view *v)
{
	int item = v->popup >= 0 ? v->popup : v->hot;
	const cv_item *it;
	if (item < 0) return;
	it = &v->m->it[item];
	if (v->popup < 0 && it->popup) { v->popup = item; return; }
	v->popup = -1;
	if (it->action != A_NONE) run_item(a, s, i, it);
}

static int keys(e26_app *a, scr_screen *s, int i, KeySym ks)
{
	cv_view *v = cv_view_of(a, s, i);
	int tall;
	if (!v || !v->m) return 0;
	tall = cv_surface_h(v) > v->h;
	switch (ks) {
	case XK_Left: cv_pan(v, -v->step, 0); break;
	case XK_Right: cv_pan(v, v->step, 0); break;
	case XK_Up: cv_pan(v, 0, -v->step); break;
	case XK_Down: cv_pan(v, 0, v->step); break;
	case XK_Prior: if (tall) cv_pan(v, 0, -(v->h - v->step)); else cv_pan(v, -(v->w - v->step), 0); break;
	case XK_Next: if (tall) cv_pan(v, 0, v->h - v->step); else cv_pan(v, v->w - v->step, 0); break;
	case XK_Home: v->vx = 0; if (tall) v->vy = 0; clamp(v); break;
	case XK_End: v->vx = cv_surface_w(v); if (tall) v->vy = cv_surface_h(v); clamp(v); break;
	case XK_plus: case XK_equal: case XK_KP_Add: cv_zoom(v, v->level + 1, -1, -1); break;
	case XK_minus: case XK_KP_Subtract: cv_zoom(v, v->level - 1, -1, -1); break;
	case XK_Escape: if (v->popup < 0) return 0; v->popup = -1; break;
	case XK_Return: open_hot(a, s, i, v); break;
	default: return 0;
	}
	nav_redraw(a);
	cursor_refresh(a);
	return 1;
}

/* ---- the cursor over a canvas */

static int cursor_probe_canvas(e26_app *a, scr_screen *s, int i, int x, int y)
{
	cv_view *v = cv_view_of(a, s, i);
	int item, e;
	if (!v || !v->m) return -1;
	if (a->drag_ops == &canvas_ops && drag_view == v && v->moved) return CUR_PAN; /* (a release elsewhere ends it) */
	if (in_popup(v, x, y)) return on_jump(a, v, x, y) && v->m->it[v->popup].action != A_NONE ? CUR_HAND : CUR_ARROW;
	if ((item = cv_item_at(v, x - v->fx, y - v->fy)) >= 0) {
		const cv_item *it = &v->m->it[item];
		if (it->cursor >= 0) return it->cursor;
		return it->action != A_NONE || it->popup || v->m->click ? CUR_HAND : -1;
	}
	if ((e = cv_edge_dir(v, x - v->fx, y - v->fy)) >= 0) return e;
	if (v->click_zoom && v->level + 1 < v->m->nlv) return CUR_MAGNIFY;
	return -1;
}

/* ---- actions and the readout source */

/* the canvas an action works on: the first on the action's screen, else on the screen in front, else under it */
static cv_view *target(e26_app *a, const e26_actx *x)
{
	scr_screen *cand[3];
	int n, k;
	cand[0] = x->s; cand[1] = a->cur; cand[2] = a->base;
	for (n = 0; n < 3; n++) {
		scr_screen *s = cand[n];
		if (!s) continue;
		for (k = 0; k < s->nc; k++)
			if (s->c[k].kind == canvas_kind) return cv_view_of(a, s, k);
	}
	return NULL;
}

static int act_zoom_in(e26_app *a, const e26_actx *x) { cv_view *v = target(a, x); if (v) cv_zoom(v, v->level + 1, -1, -1); return 1; }
static int act_zoom_out(e26_app *a, const e26_actx *x) { cv_view *v = target(a, x); if (v) cv_zoom(v, v->level - 1, -1, -1); return 1; }

/* canvas.pan <left|right|up|down|page-left|page-right|page-up|page-down|home|end> */
static int act_pan(e26_app *a, const e26_actx *x)
{
	cv_view *v = target(a, x);
	const char *d = x->arg ? x->arg : "";
	if (!v) return 1;
	if (!strcmp(d, "left")) cv_pan(v, -v->step, 0);
	else if (!strcmp(d, "right")) cv_pan(v, v->step, 0);
	else if (!strcmp(d, "up")) cv_pan(v, 0, -v->step);
	else if (!strcmp(d, "down")) cv_pan(v, 0, v->step);
	else if (!strcmp(d, "page-left")) cv_pan(v, -(v->w - v->step), 0);
	else if (!strcmp(d, "page-right")) cv_pan(v, v->w - v->step, 0);
	else if (!strcmp(d, "page-up")) cv_pan(v, 0, -(v->h - v->step));
	else if (!strcmp(d, "page-down")) cv_pan(v, 0, v->h - v->step);
	else if (!strcmp(d, "home")) { v->vx = v->vy = 0; clamp(v); }
	else if (!strcmp(d, "end")) { v->vx = cv_surface_w(v); v->vy = cv_surface_h(v); clamp(v); }
	return 1;
}

/* "source canvas-readout" (and "prop canvas <control>" to name one): the pointer readout; the label when empty */
static const char *readout_text(e26_app *a, const scr_control *c, char *buf, size_t n)
{
	const char *want = scr_prop_get(c, "canvas");
	cv_view *v = NULL;
	int k;
	for (k = 0; want && k < nviews && !v; k++)
		if (!strcmp(views[k]->ctl, want) && ((a->cur && !strcmp(views[k]->screen, a->cur->name)) ||
		                                     (a->base && !strcmp(views[k]->screen, a->base->name)))) v = views[k];
	if (!v) v = last_view;
	if (!v || !v->readout[0]) return NULL;
	snprintf(buf, n, "%s", v->readout);
	return buf;
}

static const kind_ops canvas_ops = { draw, 1, press, release, hover, wheel, drag, NULL, keys };

void e26_mod_kind_canvas(void);
void e26_mod_kind_canvas(void)
{
	static const e26_action zin = { act_zoom_in }, zout = { act_zoom_out }, pan = { act_pan };
	static const source_ops ro = { readout_text, NULL, NULL };
	canvas_kind = reg_add(REG_KIND, "canvas", &canvas_ops, "kind_canvas", 0);
	reg_add(REG_ACTION, "canvas.zoom-in", &zin, "kind_canvas", 0);
	reg_add(REG_ACTION, "canvas.zoom-out", &zout, "kind_canvas", 0);
	reg_add(REG_ACTION, "canvas.pan", &pan, "kind_canvas", 0);
	reg_add(REG_SOURCE, "canvas-readout", &ro, "kind_canvas", 0);
	cursor_probe(canvas_kind, cursor_probe_canvas);
	cursor_register();
}
