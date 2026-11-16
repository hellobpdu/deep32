/* d32ww.c: reader for Deep32 word-wheel files. See d32ww.h and WHEEL-FORMAT.md. C99. */
#include "d32ww.h"
#include "d32ww_fold_table.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define JUMP_ENTRIES 257u
#define MAX_PAD 64u

static uint32_t rd32(const unsigned char *p)
{
	return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* ---- CRC-32 (IEEE 802.3, reflected, as zlib) ------------------------------------------------------------------ */
uint32_t d32ww_crc32(const unsigned char *p, size_t n)
{
	static uint32_t table[256];
	static int ready = 0;
	uint32_t c = 0xFFFFFFFFu;
	size_t i;
	if (!ready) {
		uint32_t k, v;
		int b;
		for (k = 0; k < 256; k++) {
			v = k;
			for (b = 0; b < 8; b++)
				v = (v & 1u) ? (0xEDB88320u ^ (v >> 1)) : (v >> 1);
			table[k] = v;
		}
		ready = 1;
	}
	for (i = 0; i < n; i++)
		c = table[(c ^ p[i]) & 0xFFu] ^ (c >> 8);
	return c ^ 0xFFFFFFFFu;
}

/* ---- folding ------------------------------------------------------------------------------------------------ */

/* Decode one UTF-8 sequence. Returns its length (1-4), or 0 when the bytes at s are not valid UTF-8. */
static size_t utf8_decode(const unsigned char *s, size_t n, unsigned long *cp)
{
	unsigned char c = s[0];
	if (c < 0x80) { *cp = c; return 1; }
	if (c >= 0xC2 && c <= 0xDF) {
		if (n < 2 || (s[1] & 0xC0) != 0x80) return 0;
		*cp = ((unsigned long)(c & 0x1F) << 6) | (s[1] & 0x3F);
		return 2;
	}
	if (c >= 0xE0 && c <= 0xEF) {
		unsigned char lo = 0x80, hi = 0xBF;
		if (c == 0xE0) lo = 0xA0;
		if (c == 0xED) hi = 0x9F; /* no surrogates */
		if (n < 3 || s[1] < lo || s[1] > hi || (s[2] & 0xC0) != 0x80) return 0;
		*cp = ((unsigned long)(c & 0x0F) << 12) | ((unsigned long)(s[1] & 0x3F) << 6) | (s[2] & 0x3F);
		return 3;
	}
	if (c >= 0xF0 && c <= 0xF4) {
		unsigned char lo = 0x80, hi = 0xBF;
		if (c == 0xF0) lo = 0x90;
		if (c == 0xF4) hi = 0x8F;
		if (n < 4 || s[1] < lo || s[1] > hi || (s[2] & 0xC0) != 0x80 || (s[3] & 0xC0) != 0x80) return 0;
		*cp = ((unsigned long)(c & 0x07) << 18) | ((unsigned long)(s[1] & 0x3F) << 12) | ((unsigned long)(s[2] & 0x3F) << 6) | (s[3] & 0x3F);
		return 4;
	}
	return 0;
}

static const char *fold_lookup(unsigned long cp)
{
	size_t lo = 0, hi = D32WW_FOLD_N;
	while (lo < hi) {
		size_t mid = lo + (hi - lo) / 2;
		if (d32ww_fold_table[mid].cp < cp) lo = mid + 1;
		else hi = mid;
	}
	return (lo < D32WW_FOLD_N && d32ww_fold_table[lo].cp == cp) ? d32ww_fold_table[lo].to : NULL;
}

static int is_sep(unsigned long cp)
{
	size_t lo = 0, hi = D32WW_SEP_N;
	while (lo < hi) {
		size_t mid = lo + (hi - lo) / 2;
		if (d32ww_sep_table[mid] < cp) lo = mid + 1;
		else hi = mid;
	}
	return lo < D32WW_SEP_N && d32ww_sep_table[lo] == cp;
}

char *d32ww_fold(const char *in, size_t inlen, uint32_t flags, uint32_t pad, int query)
{
	const unsigned char *s = (const unsigned char *)in;
	size_t i = 0, o = 0, start = 0;
	int pending = 0;
	char *a, *res;

	if (inlen > ((size_t)-1) / 2 - 4) return NULL;
	a = malloc(inlen + 2); /* the folded text is never longer than the input (WHEEL-FORMAT.md 3.2) */
	if (!a) return NULL;
	while (i < inlen) {
		unsigned long cp = 0;
		size_t n = utf8_decode(s + i, inlen - i, &cp);
		const char *piece = NULL;
		size_t plen = 0;
		char one = 0;
		if (n == 0) { pending = 1; i++; continue; } /* invalid UTF-8: a separator */
		if (cp < 0x80) {
			if (cp >= 'A' && cp <= 'Z') cp += 0x20;
			if ((cp >= '0' && cp <= '9') || (cp >= 'a' && cp <= 'z')) { one = (char)cp; piece = &one; plen = 1; }
		} else if ((piece = fold_lookup(cp)) != NULL) {
			plen = strlen(piece);
		} else if (!is_sep(cp)) {
			piece = (const char *)(s + i);
			plen = n;
		}
		i += n;
		if (!piece) { pending = 1; continue; }
		if (pending && o > 0) a[o++] = ' ';
		pending = 0;
		memcpy(a + o, piece, plen);
		o += plen;
	}
	if (query && pending && o > 0) a[o++] = ' ';
	a[o] = '\0';

	if (flags & D32WW_F_STRIP_ARTICLES) {
		static const char *const arts[] = { "the ", "an ", "a " };
		size_t k;
		for (k = 0; k < 3; k++) {
			size_t al = strlen(arts[k]);
			if (o > al && memcmp(a, arts[k], al) == 0) { start = al; break; }
		}
	}
	if ((flags & D32WW_F_PAD_DIGITS) && pad > 0) {
		size_t len = o - start, cap, j = start, w = 0;
		if (pad > MAX_PAD) pad = MAX_PAD;
		if (len > (((size_t)-1) - 2) / (pad + 2)) { free(a); return NULL; }
		cap = len * (pad + 2) + 2; /* each digit run grows by at most one space and pad digits */
		res = malloc(cap);
		if (!res) { free(a); return NULL; }
		while (j < o) {
			if (a[j] >= '0' && a[j] <= '9') {
				size_t e = j, t, tl, z;
				if (j > start && a[j - 1] >= 'a' && a[j - 1] <= 'z') res[w++] = ' '; /* "rfc2328" = "rfc 2328" */
				while (e < o && a[e] >= '0' && a[e] <= '9') e++;
				t = j;
				while (t < e - 1 && a[t] == '0') t++; /* drop leading zeros, keep one digit */
				tl = e - t;
				for (z = tl; z < pad; z++) res[w++] = '0';
				memcpy(res + w, a + t, tl);
				w += tl;
				j = e;
			} else {
				res[w++] = a[j++];
			}
		}
		res[w] = '\0';
		free(a);
		return res;
	}
	res = malloc(o - start + 1);
	if (!res) { free(a); return NULL; }
	memcpy(res, a + start, o - start + 1);
	free(a);
	return res;
}

/* ---- open and validate ---------------------------------------------------------------------------------------- */

static int within(size_t len, uint32_t off, uint64_t size)
{
	return off >= D32WW_HEADER_SIZE && (uint64_t)off <= (uint64_t)len && (uint64_t)off + size <= (uint64_t)len;
}

static const char *str_at(const d32ww *w, uint32_t off)
{
	return (const char *)(w->buf + w->strings_off + off);
}

static uint32_t jump_at(const d32ww *w, uint32_t b)
{
	return rd32(w->buf + w->jump_off + 4u * b);
}

static const char *key_at(const d32ww *w, uint32_t i)
{
	return str_at(w, rd32(w->buf + w->entries_off + i * D32WW_ENTRY_SIZE));
}

#define FAIL(...) do { if (err && errlen) snprintf(err, errlen, __VA_ARGS__); goto fail; } while (0)

int d32ww_open_mem(d32ww *w, unsigned char *buf, size_t len, int own, char *err, size_t errlen)
{
	uint32_t ver, hs, fsz, crc, es, i, b, hstr[4];
	const char *prev = NULL;

	memset(w, 0, sizeof *w);
	if (err && errlen) err[0] = '\0';
	if (!buf || len < D32WW_HEADER_SIZE) FAIL("short file (%lu bytes)", (unsigned long)len);
	if (memcmp(buf, "D32WHEEL", 8) != 0) FAIL("%s", "bad magic");
	ver = rd32(buf + 8); hs = rd32(buf + 12); fsz = rd32(buf + 16); crc = rd32(buf + 20);
	w->flags = rd32(buf + 24); w->pad = rd32(buf + 28); w->n_entries = rd32(buf + 32); w->entries_off = rd32(buf + 36);
	es = rd32(buf + 40); w->n_postings = rd32(buf + 44); w->postings_off = rd32(buf + 48); w->strings_off = rd32(buf + 52);
	w->strings_size = rd32(buf + 56); w->jump_off = rd32(buf + 60);
	for (i = 0; i < 4; i++) hstr[i] = rd32(buf + 64 + 4 * i);
	if (ver != D32WW_VERSION) FAIL("unsupported version %lu", (unsigned long)ver);
	if (hs != D32WW_HEADER_SIZE || es != D32WW_ENTRY_SIZE) FAIL("%s", "bad header sizes");
	if ((uint64_t)fsz != (uint64_t)len) FAIL("size mismatch (header %lu, file %lu)", (unsigned long)fsz, (unsigned long)len);
	if (w->pad > MAX_PAD) FAIL("pad width %lu too large", (unsigned long)w->pad);
	if (!within(len, w->entries_off, (uint64_t)w->n_entries * D32WW_ENTRY_SIZE) ||
	    !within(len, w->postings_off, (uint64_t)w->n_postings * 4u) ||
	    !within(len, w->jump_off, (uint64_t)JUMP_ENTRIES * 4u) ||
	    !within(len, w->strings_off, w->strings_size))
		FAIL("%s", "section out of bounds");
	if (w->strings_size < 1 || buf[w->strings_off] != 0 || buf[w->strings_off + w->strings_size - 1] != 0)
		FAIL("%s", "bad string pool");
	if (d32ww_crc32(buf + D32WW_HEADER_SIZE, len - D32WW_HEADER_SIZE) != crc) FAIL("%s", "crc mismatch");
	w->buf = buf;
	w->len = len;
	for (i = 0; i < 4; i++)
		if (hstr[i] >= w->strings_size) FAIL("%s", "header string out of bounds");
	w->name = str_at(w, hstr[0]);
	w->catalogue_id = str_at(w, hstr[1]);
	w->catalogue_sha256 = str_at(w, hstr[2]);
	w->built_at = str_at(w, hstr[3]);
	/* every entry: strings inside the pool, postings inside the array, keys non-empty and in byte order */
	for (i = 0; i < w->n_entries; i++) {
		const unsigned char *e = buf + w->entries_off + i * D32WW_ENTRY_SIZE;
		uint32_t first = rd32(e + 12), count = rd32(e + 16);
		const char *k;
		if (rd32(e) >= w->strings_size || rd32(e + 4) >= w->strings_size || rd32(e + 8) >= w->strings_size)
			FAIL("string offset out of bounds at entry %lu", (unsigned long)i);
		if (count == 0 || (uint64_t)first + count > w->n_postings) FAIL("bad postings at entry %lu", (unsigned long)i);
		k = str_at(w, rd32(e));
		if (k[0] == '\0') FAIL("empty key at entry %lu", (unsigned long)i);
		if (prev && strcmp(prev, k) > 0) FAIL("keys out of order at entry %lu", (unsigned long)i);
		prev = k;
	}
	/* jump table: jump[b] is the first entry whose key's first byte is >= b; jump[256] = n */
	for (b = 0; b < 256; b++) {
		uint32_t j = jump_at(w, b);
		if (j > w->n_entries || (b > 0 && j < jump_at(w, b - 1))) FAIL("jump table not monotonic at %lu", (unsigned long)b);
		if (j < w->n_entries && (unsigned char)key_at(w, j)[0] < b) FAIL("jump table wrong at %lu", (unsigned long)b);
		if (j > 0 && (unsigned char)key_at(w, j - 1)[0] >= b) FAIL("jump table wrong before %lu", (unsigned long)b);
	}
	if (jump_at(w, 256) != w->n_entries) FAIL("%s", "jump sentinel wrong");
	w->owned = own;
	return 0;
fail:
	if (own) free(buf);
	memset(w, 0, sizeof *w);
	return -1;
}

int d32ww_open_file(d32ww *w, const char *path, char *err, size_t errlen)
{
	FILE *f;
	long sz;
	unsigned char *buf;
	memset(w, 0, sizeof *w);
	f = fopen(path, "rb");
	if (!f) { if (err && errlen) snprintf(err, errlen, "cannot open %s", path); return -1; }
	if (fseek(f, 0, SEEK_END) != 0 || (sz = ftell(f)) < 0 || fseek(f, 0, SEEK_SET) != 0) {
		fclose(f);
		if (err && errlen) snprintf(err, errlen, "cannot size %s", path);
		return -1;
	}
	buf = malloc(sz > 0 ? (size_t)sz : 1);
	if (!buf) { fclose(f); if (err && errlen) snprintf(err, errlen, "%s", "out of memory"); return -1; }
	if (sz > 0 && fread(buf, 1, (size_t)sz, f) != (size_t)sz) {
		fclose(f); free(buf);
		if (err && errlen) snprintf(err, errlen, "cannot read %s", path);
		return -1;
	}
	fclose(f);
	return d32ww_open_mem(w, buf, (size_t)sz, 1, err, errlen);
}

void d32ww_close(d32ww *w)
{
	if (w->owned) free(w->buf);
	memset(w, 0, sizeof *w);
}

/* ---- entries and lookup --------------------------------------------------------------------------------------- */

int d32ww_entry_get(const d32ww *w, uint32_t i, d32ww_entry *e)
{
	const unsigned char *p;
	if (i >= w->n_entries) return -1;
	p = w->buf + w->entries_off + i * D32WW_ENTRY_SIZE;
	e->key = str_at(w, rd32(p));
	e->display = str_at(w, rd32(p + 4));
	e->label = str_at(w, rd32(p + 8));
	e->post_first = rd32(p + 12);
	e->post_count = rd32(p + 16);
	e->kind = p[20];
	e->flags = p[21];
	return 0;
}

uint32_t d32ww_posting(const d32ww *w, const d32ww_entry *e, uint32_t k)
{
	if (k >= e->post_count || (uint64_t)e->post_first + k >= w->n_postings) return 0;
	return rd32(w->buf + w->postings_off + 4u * (e->post_first + k));
}

/* bytes of a NUL-terminated key against (q, qlen): <0, 0, >0 as memcmp-then-length (Buffer.compare) */
static int keycmp(const char *k, const unsigned char *q, size_t qlen)
{
	size_t i;
	for (i = 0; i < qlen; i++) {
		unsigned char kc = (unsigned char)k[i];
		if (kc == 0) return -1;
		if (kc != q[i]) return kc < q[i] ? -1 : 1;
	}
	return k[qlen] == '\0' ? 0 : 1;
}

uint32_t d32ww_lower_bound(const d32ww *w, const char *key, size_t keylen)
{
	const unsigned char *q = (const unsigned char *)key;
	uint32_t lo = 0, hi = w->n_entries;
	if (keylen) {
		lo = jump_at(w, q[0]);
		hi = q[0] < 255 ? jump_at(w, (uint32_t)q[0] + 1u) : w->n_entries;
		if (hi < lo) hi = lo;
	}
	while (lo < hi) {
		uint32_t mid = lo + (hi - lo) / 2;
		if (keycmp(key_at(w, mid), q, keylen) < 0) lo = mid + 1;
		else hi = mid;
	}
	return lo;
}

int d32ww_find(const d32ww *w, const char *typed, size_t typedlen, d32ww_range *r, char **folded)
{
	char *f = d32ww_fold(typed, typedlen, w->flags, w->pad, 1);
	size_t fl;
	if (folded) *folded = NULL;
	if (!f) return -1;
	fl = strlen(f);
	r->lo = d32ww_lower_bound(w, f, fl);
	if (fl) {
		/* the end of the prefix range: the first key >= the query followed by 0xFF (no UTF-8 key holds 0xFF) */
		char *u = malloc(fl + 1);
		if (!u) { free(f); return -1; }
		memcpy(u, f, fl);
		u[fl] = (char)0xFF;
		r->hi = d32ww_lower_bound(w, u, fl + 1);
		free(u);
	} else {
		r->hi = w->n_entries;
	}
	r->count = r->hi - r->lo;
	r->nearest = w->n_entries == 0 ? 0 : (r->lo < w->n_entries ? r->lo : w->n_entries - 1);
	if (folded) *folded = f;
	else free(f);
	return 0;
}
