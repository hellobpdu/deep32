/* op_pdf.c: e26-render's PDF reading operations (W1-IMAGE, the engine half of plan L2; LIB-PROTOCOL "e26-render
 * named operations"): pdf_open, page_render, page_text, page_outline and pdf_close. A PDF arrives as a read-only
 * descriptor (pdf_open keeps it until pdf_close or the connection's end) and is named afterwards by an opaque handle
 * that lives as long as the connection. Limits: 512 MiB of PDF, 10,000 pages, 16 open documents; a page picture
 * that fits one E26P body. Encrypted documents open only when they need no password (the protocol has no prompt). */
#include "render.h"
#include "mu.h"
#include "../app/registry.h"
#include "../common/helper.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define MAX_PDF (512ull * 1024ull * 1024ull)
#define MAX_PAGES 10000
#define MAX_DOCS 16

#ifdef HAVE_MUPDF
typedef struct {
	int live;
	char id[24];
	FILE *f;
	fz_stream *stm;
	fz_document *doc;
	int pages;
} pdfdoc;

static pdfdoc D[MAX_DOCS];
static unsigned long serial;

static pdfdoc *find(const struct jv *args, char **result, const char **code)
{
	const char *h;
	int i;
	if (ra_str(args, "handle", 64, NULL, &h) != 1) { *result = rs_dup("handle is missing"); *code = "E_ARGUMENT"; return NULL; }
	for (i = 0; i < MAX_DOCS; i++)
		if (D[i].live && !strcmp(D[i].id, h)) return &D[i];
	*result = rs_dup("no open document has this handle");
	*code = "E_NOT_FOUND";
	return NULL;
}

static void close_doc(pdfdoc *d)
{
	fz_context *ctx = mu_ctx();
	fz_drop_document(ctx, d->doc);
	fz_drop_stream(ctx, d->stm);
	if (d->f) fclose(d->f);
	memset(d, 0, sizeof *d);
}

static const char *const OPEN_KEYS[] = { "input_fd", NULL };

static const char *pdf_open_op(const struct jv *args, char **result)
{
	fz_context *ctx = mu_ctx();
	long slot;
	int i, fd;
	struct stat st;
	char head[1024];
	ssize_t n;
	pdfdoc *d = NULL;
	const char *volatile code = NULL;
	if (!ra_only(args, OPEN_KEYS) || ra_int(args, "input_fd", 0, 0, -1, &slot) != 1) { *result = rs_dup("input_fd is 0"); return "E_ARGUMENT"; }
	if (!ctx) { *result = rs_dup("no MuPDF context"); return "E_PACK"; }
	for (i = 0; i < MAX_DOCS && !d; i++)
		if (!D[i].live) d = &D[i];
	if (!d) { *result = rs_dup("too many open documents"); return "E_TOO_LARGE"; }
	fd = render_fd(0);
	if (fstat(fd, &st) || !S_ISREG(st.st_mode)) { *result = rs_dup("the input is not a file"); return "E_ARGUMENT"; }
	if ((unsigned long long)st.st_size > MAX_PDF) { *result = rs_dup("the PDF is larger than 512 MiB"); return "E_TOO_LARGE"; }
	n = pread(fd, head, sizeof head, 0);
	if (n < 5) { *result = rs_dup("the input is not a PDF"); return "E_ARGUMENT"; }
	for (i = 0; i + 5 <= n && memcmp(head + i, "%PDF-", 5); i++) {
	}
	if (i + 5 > n) { *result = rs_dup("the input is not a PDF"); return "E_ARGUMENT"; }
	if (lseek(fd, 0, SEEK_SET) == -1 || !(d->f = fdopen(fd, "rb"))) { *result = rs_dup("the input cannot be read"); return "E_ARGUMENT"; }
	render_take_fd(0);                       /* the FILE owns it now: fclose at pdf_close */
	mu_reset_code();
	fz_try(ctx) {
		d->stm = fz_open_file_ptr_no_close(ctx, d->f);
		d->doc = fz_open_document_with_stream(ctx, "application/pdf", d->stm);
		if (fz_needs_password(ctx, d->doc) && !fz_authenticate_password(ctx, d->doc, "")) code = "E_UNSUPPORTED";
		else if ((d->pages = fz_count_pages(ctx, d->doc)) > MAX_PAGES) code = "E_TOO_LARGE";
	}
	fz_catch(ctx) code = mu_code();
	if (code) {
		*result = rs_dup(!strcmp(code, "E_UNSUPPORTED") ? "the PDF needs a password" : !strcmp(code, "E_TOO_LARGE") ? "the PDF is too large" : "the PDF cannot be read");
		close_doc(d);
		return code;
	}
	{
		char enc[128] = "None", out[200];
		fz_try(ctx) { if (fz_lookup_metadata(ctx, d->doc, FZ_META_ENCRYPTION, enc, sizeof enc) < 0) strcpy(enc, "None"); }
		fz_catch(ctx) strcpy(enc, "None");
		d->live = 1;
		snprintf(d->id, sizeof d->id, "pdf-%lu", ++serial);
		snprintf(out, sizeof out, "{\"handle\":\"%s\",\"pages\":%d,\"encrypted\":%s}", d->id, d->pages, strcmp(enc, "None") ? "true" : "false");
		*result = rs_dup(out);
	}
	return *result ? NULL : "E_PACK";
}

static const char *const RENDER_KEYS[] = { "handle", "page", "width", "height", "rotation", NULL };

static const char *page_render(const struct jv *args, char **result)
{
	fz_context *ctx = mu_ctx();
	const char *code = NULL;
	const char *volatile fail = NULL;
	pdfdoc *d;
	long pg, bw, bh, rot;
	fz_page *volatile page = NULL;
	fz_pixmap *volatile pix = NULL;
	unsigned char *volatile rgba = NULL;
	volatile int w = 0, h = 0;
	if (!ra_only(args, RENDER_KEYS) || ra_int(args, "page", 0, MAX_PAGES, -1, &pg) != 1 || ra_int(args, "width", 1, 4096, 0, &bw) != 1 ||
	    ra_int(args, "height", 1, 4096, 0, &bh) != 1 || ra_int(args, "rotation", 0, 270, 0, &rot) < 0 || rot % 90) {
		*result = rs_dup("an argument is missing or malformed");
		return "E_ARGUMENT";
	}
	if (!(d = find(args, result, &code))) return code;
	if (pg >= d->pages) { *result = rs_dup("no such page"); return "E_ARGUMENT"; }
	mu_reset_code();
	fz_try(ctx) {
		fz_rect r;
		float pw, ph, s;
		fz_matrix m;
		page = fz_load_page(ctx, d->doc, (int)pg);
		r = fz_bound_page(ctx, page);
		pw = r.x1 - r.x0; ph = r.y1 - r.y0;
		if (rot == 90 || rot == 270) { float t = pw; pw = ph; ph = t; }
		if (!(pw > 0 && ph > 0)) fail = "E_ARGUMENT";
		else {
			s = (float)bw / pw < (float)bh / ph ? (float)bw / pw : (float)bh / ph;
			if ((size_t)(pw * s + 1) * (size_t)(ph * s + 1) > rs_max_pixels()) fail = "E_TOO_LARGE";
			else {
				m = fz_concat(fz_rotate((float)rot), fz_scale(s, s));
				pix = fz_new_pixmap_from_page(ctx, page, m, fz_device_rgb(ctx), 0);
				w = fz_pixmap_width(ctx, pix); h = fz_pixmap_height(ctx, pix);
				if (w > bw) w = (int)bw;     /* rounding outwards can give a pixel more than the box */
				if (h > bh) h = (int)bh;
				if (!(rgba = (unsigned char *)malloc((size_t)w * h * 4))) fail = "E_TOO_LARGE";
				else mu_to_rgba(fz_pixmap_samples(ctx, pix), fz_pixmap_stride(ctx, pix), fz_pixmap_components(ctx, pix), 0, w, h, rgba);
			}
		}
	}
	fz_always(ctx) {
		fz_drop_pixmap(ctx, pix);
		fz_drop_page(ctx, page);
	}
	fz_catch(ctx) {
		free(rgba);
		*result = rs_dup("the page cannot be drawn");
		return mu_code();
	}
	if (fail) { free(rgba); *result = rs_dup(fail[2] == 'T' ? "the page picture does not fit one response" : "the page has no size"); return fail; }
	*result = rs_rgba(rgba, w, h, &code);
	free(rgba);
	return *result ? NULL : code;
}

/* ---- page_text: the page's lines, each followed by a newline, and a box per line */

typedef struct { char *s; size_t n, cap; } sbuf;
static int sb_add(sbuf *b, const char *s, size_t n)
{
	if (b->n + n + 1 > b->cap) {
		size_t cap = (b->cap ? b->cap * 2 : 4096);
		char *ns;
		while (cap < b->n + n + 1) cap *= 2;
		if (!(ns = (char *)realloc(b->s, cap))) return -1;
		b->s = ns;
		b->cap = cap;
	}
	memcpy(b->s + b->n, s, n);
	b->n += n;
	b->s[b->n] = 0;
	return 0;
}

typedef struct { size_t start, end; float box[4]; } tspan;
typedef struct { tspan *v; size_t n, cap; } spans;

static int add_char(sbuf *t, int c)
{
	char u[8];
	int n;
	if (c <= 0 || (c >= 0xd800 && c <= 0xdfff) || c > 0x10ffff) c = 0xfffd;   /* never a NUL or a lone surrogate */
	n = fz_runetochar(u, c);
	return sb_add(t, u, (size_t)n);
}

static int walk(fz_stext_block *b, sbuf *t, spans *sp)
{
	for (; b; b = b->next) {
		if (b->type == FZ_STEXT_BLOCK_STRUCT && b->u.s.down) {
			if (walk(b->u.s.down->first_block, t, sp)) return -1;
		} else if (b->type == FZ_STEXT_BLOCK_TEXT) {
			fz_stext_line *l;
			for (l = b->u.t.first_line; l; l = l->next) {
				fz_stext_char *ch;
				size_t start = t->n;
				for (ch = l->first_char; ch; ch = ch->next)
					if (add_char(t, ch->c)) return -1;
				if (sb_add(t, "\n", 1)) return -1;
				if (sp->n == sp->cap) {
					size_t cap = sp->cap ? sp->cap * 2 : 64;
					tspan *nv = (tspan *)realloc(sp->v, cap * sizeof *nv);
					if (!nv) return -1;
					sp->v = nv;
					sp->cap = cap;
				}
				sp->v[sp->n].start = start;
				sp->v[sp->n].end = t->n - 1;
				sp->v[sp->n].box[0] = l->bbox.x0; sp->v[sp->n].box[1] = l->bbox.y0;
				sp->v[sp->n].box[2] = l->bbox.x1; sp->v[sp->n].box[3] = l->bbox.y1;
				sp->n++;
			}
		}
	}
	return 0;
}

static double fin(float v) { return isfinite(v) ? (double)v : 0.0; }

static const char *const TEXT_KEYS[] = { "handle", "page", "start", "limit", NULL };

static const char *page_text(const struct jv *args, char **result)
{
	fz_context *ctx = mu_ctx();
	const char *code = NULL;
	pdfdoc *d;
	long pg, start, limit;
	fz_page *volatile page = NULL;
	fz_stext_page *volatile st = NULL;
	sbuf t = { NULL, 0, 0 }, o = { NULL, 0, 0 };
	spans sp = { NULL, 0, 0 };
	volatile int bad = 0;
	size_t end, i;
	char *js;
	if (!ra_only(args, TEXT_KEYS) || ra_int(args, "page", 0, MAX_PAGES, -1, &pg) != 1 || ra_int(args, "start", 0, 0x7fffffffL, 0, &start) < 0 ||
	    ra_int(args, "limit", 1, 65536, 65536, &limit) < 0) {
		*result = rs_dup("an argument is missing or malformed");
		return "E_ARGUMENT";
	}
	if (!(d = find(args, result, &code))) return code;
	if (pg >= d->pages) { *result = rs_dup("no such page"); return "E_ARGUMENT"; }
	mu_reset_code();
	fz_try(ctx) {
		page = fz_load_page(ctx, d->doc, (int)pg);
		st = fz_new_stext_page_from_page(ctx, page, NULL);
		if (walk(st->first_block, &t, &sp)) bad = 1;
	}
	fz_always(ctx) {
		fz_drop_stext_page(ctx, st);
		fz_drop_page(ctx, page);
	}
	fz_catch(ctx) {
		free(t.s); free(sp.v);
		*result = rs_dup("the page's text cannot be read");
		return mu_code();
	}
	if (bad) { free(t.s); free(sp.v); *result = rs_dup("out of memory"); return "E_TOO_LARGE"; }
	if ((size_t)start > t.n || (start < (long)t.n && ((unsigned char)t.s[start] & 0xc0) == 0x80)) {
		free(t.s); free(sp.v);
		*result = rs_dup("start is not the beginning of a character");
		return "E_ARGUMENT";
	}
	end = (size_t)start + (size_t)limit;
	if (end > t.n) end = t.n;
	while (end > (size_t)start && end < t.n && ((unsigned char)t.s[end] & 0xc0) == 0x80) end--;   /* never split a character */
	js = rs_json_str(t.s ? t.s + start : "", end - (size_t)start);
	{
		char head[160];
		snprintf(head, sizeof head, "{\"text\":");
		if (!js || sb_add(&o, head, strlen(head)) || sb_add(&o, js, strlen(js))) bad = 1;
		snprintf(head, sizeof head, ",\"start\":%ld,\"next\":", start);
		if (!bad && sb_add(&o, head, strlen(head))) bad = 1;
		if (end < t.n) snprintf(head, sizeof head, "%lu", (unsigned long)end); else snprintf(head, sizeof head, "null");
		if (!bad && sb_add(&o, head, strlen(head))) bad = 1;
		snprintf(head, sizeof head, ",\"total\":%lu,\"spans\":[", (unsigned long)t.n);
		if (!bad && sb_add(&o, head, strlen(head))) bad = 1;
		for (i = 0; i < sp.n && !bad; i++) {
			char row[200];
			if (sp.v[i].end < (size_t)start || sp.v[i].start >= end) continue;
			snprintf(row, sizeof row, "%s{\"start\":%lu,\"end\":%lu,\"box\":[%.2f,%.2f,%.2f,%.2f]}", o.s[o.n - 1] == '[' ? "" : ",",
			         (unsigned long)sp.v[i].start, (unsigned long)sp.v[i].end, fin(sp.v[i].box[0]), fin(sp.v[i].box[1]),
			         fin(sp.v[i].box[2]), fin(sp.v[i].box[3]));
			if (sb_add(&o, row, strlen(row))) bad = 1;
		}
		if (!bad && sb_add(&o, "]}", 2)) bad = 1;
	}
	free(js); free(t.s); free(sp.v);
	if (bad) { free(o.s); *result = rs_dup("out of memory"); return "E_TOO_LARGE"; }
	*result = o.s;
	return NULL;
}

/* ---- page_outline: the outline flattened in pre-order, a level per entry */

static int flatten(fz_context *ctx, fz_document *doc, fz_outline *ol, int level, sbuf *rows, long *count, long start, long limit)
{
	for (; ol; ol = ol->next) {
		volatile int pg = -1;
		fz_try(ctx) pg = fz_page_number_from_location(ctx, doc, ol->page);
		fz_catch(ctx) pg = -1;
		if (pg >= 0) {                       /* an entry with no page in this document (an external link) is left out */
			if (*count >= start && *count < start + limit) {
				char *title = rs_json_str(ol->title ? ol->title : "", ol->title ? strlen(ol->title) : 0), num[200];
				if (!title) return -1;
				snprintf(num, sizeof num, "%s{\"level\":%d,\"title\":", rows->s[rows->n - 1] == '[' ? "" : ",", level > 32 ? 32 : level);
				if (sb_add(rows, num, strlen(num)) || sb_add(rows, title, strlen(title))) { free(title); return -1; }
				free(title);
				if (isfinite(ol->x) && isfinite(ol->y))
					snprintf(num, sizeof num, ",\"page\":%d,\"box\":[%.2f,%.2f,%.2f,%.2f]}", pg, ol->x, ol->y, ol->x, ol->y);
				else snprintf(num, sizeof num, ",\"page\":%d,\"box\":null}", pg);
				if (sb_add(rows, num, strlen(num))) return -1;
			}
			(*count)++;
		}
		if (ol->down && flatten(ctx, doc, ol->down, level + 1, rows, count, start, limit)) return -1;
	}
	return 0;
}

static const char *const OUTLINE_KEYS[] = { "handle", "start", "limit", NULL };

static const char *page_outline(const struct jv *args, char **result)
{
	fz_context *ctx = mu_ctx();
	const char *code = NULL;
	pdfdoc *d;
	long start, limit, count = 0;
	fz_outline *volatile ol = NULL;
	sbuf rows = { NULL, 0, 0 };
	char tail[120];
	int bad;
	if (!ra_only(args, OUTLINE_KEYS) || ra_int(args, "start", 0, 0x7fffffffL, 0, &start) < 0 || ra_int(args, "limit", 1, 256, 32, &limit) < 0) {
		*result = rs_dup("an argument is missing or malformed");
		return "E_ARGUMENT";
	}
	if (!(d = find(args, result, &code))) return code;
	mu_reset_code();
	fz_try(ctx) ol = fz_load_outline(ctx, d->doc);
	fz_catch(ctx) ol = NULL;                 /* a damaged outline reads as none */
	bad = sb_add(&rows, "{\"rows\":[", 9);
	if (!bad) bad = flatten(ctx, d->doc, ol, 1, &rows, &count, start, limit);
	fz_drop_outline(ctx, ol);
	if (!bad) {
		if (start + limit < count) snprintf(tail, sizeof tail, "],\"start\":%ld,\"next\":%ld,\"total\":%ld}", start, start + limit, count);
		else snprintf(tail, sizeof tail, "],\"start\":%ld,\"next\":null,\"total\":%ld}", start, count);
		bad = sb_add(&rows, tail, strlen(tail));
	}
	if (bad) { free(rows.s); *result = rs_dup("out of memory"); return "E_TOO_LARGE"; }
	*result = rows.s;
	return NULL;
}

static const char *const CLOSE_KEYS[] = { "handle", NULL };

static const char *pdf_close_op(const struct jv *args, char **result)
{
	const char *code = NULL;
	pdfdoc *d;
	if (!ra_only(args, CLOSE_KEYS)) { *result = rs_dup("pdf_close takes a handle"); return "E_ARGUMENT"; }
	if (!(d = find(args, result, &code))) return code;
	close_doc(d);
	*result = rs_dup("{\"closed\":true}");
	return *result ? NULL : "E_PACK";
}
#else
static const char *pdf_open_op(const struct jv *args, char **result) { (void)args; *result = rs_dup("this build has no PDF reader"); return "E_UNSUPPORTED"; }
#define page_render pdf_open_op
#define page_text pdf_open_op
#define page_outline pdf_open_op
#define pdf_close_op pdf_open_op
#endif

void e26_op_pdf(void);
void e26_op_pdf(void)
{
	static const e26_helper_op open = { pdf_open_op }, render = { page_render }, text = { page_text },
	                           outline = { page_outline }, close_ = { pdf_close_op };
	reg_add(REG_RENDEROP, "pdf_open", &open, "op_pdf", RENDER_FDS(1));
	reg_add(REG_RENDEROP, "page_render", &render, "op_pdf", 0);
	reg_add(REG_RENDEROP, "page_text", &text, "op_pdf", 0);
	reg_add(REG_RENDEROP, "page_outline", &outline, "op_pdf", 0);
	reg_add(REG_RENDEROP, "pdf_close", &close_, "op_pdf", 0);
}
