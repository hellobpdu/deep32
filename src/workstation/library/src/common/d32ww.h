/*
 * d32ww: reader for Deep32 word-wheel files (.d32ww, format D32WHEEL v1; see WHEEL-FORMAT.md).
 *
 * A word wheel is the Library's .WWT idea: a sorted list of folded keys that the Index scrolls as you type, with the
 * count of matches always known. Deep32 Library's C/Motif engine opens a wheel once, then for every keystroke folds
 * the typed text (d32ww_fold), finds the range (d32ww_find) and draws rows from d32ww_entry_get. Postings are
 * catalogue.sqlite item ids (rowids); the header names the catalogue build the wheel belongs to.
 *
 * C99, no dependencies beyond libc. d32ww_open_* validates everything (sizes, offsets, CRC-32, key order, jump
 * table) before returning, so later calls never read outside the buffer.
 */
#ifndef D32WW_H
#define D32WW_H

#include <stddef.h>
#include <stdint.h>

#define D32WW_VERSION 1u
#define D32WW_HEADER_SIZE 96u
#define D32WW_ENTRY_SIZE 24u

/* header flags */
#define D32WW_F_STRIP_ARTICLES 1u
#define D32WW_F_PAD_DIGITS 2u

/* entry kinds (lib/wheel/format.js KIND) */
enum d32ww_kind {
	D32WW_K_RFC = 1, D32WW_K_BCP = 2, D32WW_K_STD = 3, D32WW_K_FYI = 4, D32WW_K_DRAFT = 5, D32WW_K_RFC_NOT_ISSUED = 6,
	D32WW_K_CONFIG_GUIDE = 16, D32WW_K_COMMAND_REFERENCE = 17, D32WW_K_RELEASE_NOTES = 18, D32WW_K_TECH_NOTE = 19,
	D32WW_K_CISCO_OTHER = 20, D32WW_K_GROUP = 32, D32WW_K_KEYWORD = 33
};

/* entry flags (lib/wheel/format.js FLAG) */
#define D32WW_FL_HELD 1u      /* on your shelf (in the generation's corpus) */
#define D32WW_FL_OBSOLETED 2u /* an RFC that a later RFC obsoletes */
#define D32WW_FL_INACTIVE 4u  /* an Internet-Draft that is not Active; a group that is not active */
#define D32WW_FL_HISTORIC 8u
#define D32WW_FL_NEWER 16u    /* a held Cisco document with a newer release */
#define D32WW_FL_PINNED 32u   /* ... held at a lab pin (17.9, 20.9, 2.3, 3.1) */
#define D32WW_FL_ALIAS 64u    /* a second way in to the same postings (a group's name) */

typedef struct d32ww {
	unsigned char *buf; /* the whole file */
	size_t len;
	int owned;          /* buf is freed by d32ww_close */
	uint32_t flags, pad, n_entries, entries_off, n_postings, postings_off, strings_off, strings_size, jump_off;
	const char *name, *catalogue_id, *catalogue_sha256, *built_at;
} d32ww;

typedef struct d32ww_entry {
	const char *key;     /* folded key (UTF-8, NUL-terminated, inside the buffer) */
	const char *display; /* what the list shows */
	const char *label;   /* secondary text: "RFC 2328", a draft name, "Working group, rtg, active; ..." */
	uint32_t post_first, post_count;
	unsigned kind, flags;
} d32ww_entry;

typedef struct d32ww_range {
	uint32_t lo, hi;  /* matching entries are [lo, hi) */
	uint32_t count;   /* hi - lo: the number the Index shows */
	uint32_t nearest; /* where the list scrolls: lo, or the last entry when lo is past the end; 0 when empty */
} d32ww_range;

/* Open a wheel from memory (buf must stay valid; not freed unless take_ownership) or from a file.
 * Returns 0 on success; -1 with a message in err (if errlen > 0) when the file is not a valid wheel. */
int d32ww_open_mem(d32ww *w, unsigned char *buf, size_t len, int take_ownership, char *err, size_t errlen);
int d32ww_open_file(d32ww *w, const char *path, char *err, size_t errlen);
void d32ww_close(d32ww *w);

/* Fold text the way the wheel's keys were folded (WHEEL-FORMAT.md section 3). query != 0 keeps one trailing space
 * when the text ended in a separator. Returns a malloc'd NUL-terminated string (free it), or NULL when out of memory. */
char *d32ww_fold(const char *in, size_t inlen, uint32_t flags, uint32_t pad, int query);

/* Entry i (0 <= i < n_entries). Returns 0, or -1 when i is out of range. */
int d32ww_entry_get(const d32ww *w, uint32_t i, d32ww_entry *e);
/* Posting k of an entry (0 <= k < post_count): a catalogue item id. Returns 0 when k is out of range. */
uint32_t d32ww_posting(const d32ww *w, const d32ww_entry *e, uint32_t k);

/* First entry whose key is >= key (byte order); n_entries when none. */
uint32_t d32ww_lower_bound(const d32ww *w, const char *key, size_t keylen);
/* Fold typed text with the wheel's own flags and find its range. If folded is not NULL it receives the malloc'd
 * folded query (free it). Returns -1 when out of memory. */
int d32ww_find(const d32ww *w, const char *typed, size_t typedlen, d32ww_range *r, char **folded);

uint32_t d32ww_crc32(const unsigned char *p, size_t n);

#endif
