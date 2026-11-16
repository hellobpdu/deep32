/* select.h: the X selections (W1-INPUT, plan 3.2; LIBRARY-DESIGN 4.9, CONTENT-EXPANSION 5.3 rule 6).
 *
 * Deep32 Library owns CLIPBOARD (Copy) and PRIMARY (a selection made in a field, and Copy with cite) and answers the
 * targets TARGETS, UTF8_STRING, STRING (ISO 8859-1), TEXT and, when the text came from an Deep32 Library surface that
 * knows its source, D32_PROVENANCE: three lines "class <library|corpus>", "key <item key>", "version <version>"
 * after a first line "D32_PROVENANCE 1". No visible line is ever added to a plain copy (it would land in dtterm and
 * router consoles); the provenance travels only in that target, which Deep32 Library's own fields (and the Ask, Codex
 * Console fields) ask for first. Pasting asks for D32_PROVENANCE, then UTF8_STRING, then STRING.
 *
 * Text inside Deep32 Library is cp1252 (the period fonts); the conversions below are the only place it meets UTF-8. */
#ifndef E26_SELECT_H
#define E26_SELECT_H
#include <stddef.h>

struct e26_app;

enum { SEL_PRIMARY, SEL_CLIPBOARD };

typedef struct {
	char cls[16];       /* "library" or "corpus" */
	char key[96];       /* the item key ("rfc2328-s1", "wiki:Open_Shortest_Path_First", a corpus cite) */
	char version[96];   /* the item's version (its source SHA-256, a revision id, a generation) */
} sel_prov;

/* "D32_PROVENANCE 1\nclass ...\nkey ...\nversion ...\n"; the length, or -1 */
int sel_prov_format(const sel_prov *p, char *out, size_t n);
/* 0 when in is such a record (fields with spaces or newlines are refused) */
int sel_prov_parse(const char *in, size_t len, sel_prov *p);

/* conversions; each returns the bytes written (out is NUL-terminated, cap includes the NUL) */
size_t sel_cp1252_to_utf8(const char *in, size_t n, char *out, size_t cap);
size_t sel_utf8_to_cp1252(const char *in, size_t n, char *out, size_t cap);   /* no cp1252 glyph: '?' */
size_t sel_cp1252_to_latin1(const char *in, size_t n, char *out, size_t cap); /* the STRING target */

/* takes the selection with text (cp1252, n bytes) and its provenance (NULL: none); 0, or -1 (and a message) */
int sel_own(struct e26_app *a, int which, const char *text, size_t n, const sel_prov *prov);
int sel_owned(int which);
const char *sel_text(int which, size_t *n);    /* what Deep32 Library holds (UTF-8), NULL when it does not own it */
/* asks the owner of the selection for its text; fn gets it as cp1252 (n bytes, NUL-terminated) with the provenance
 * when the owner gave one, or text NULL when there was nothing to paste */
typedef void (*sel_paste_fn)(struct e26_app *a, const char *text, size_t n, const sel_prov *prov, void *ud);
int sel_request(struct e26_app *a, int which, sel_paste_fn fn, void *ud);

#endif
