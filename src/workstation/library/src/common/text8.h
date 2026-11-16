/* text8.h: Unicode text on the Library's 8-bit faces (build 3, W1-LAYOUT; plan W13). Article format 2 carries UTF-8;
 * Deep32 Library draws with the period faces, whose glyphs are addressed by one byte: Latin-1 on the Arial, Times and
 * Courier New TrueType faces as the X server lists them (iso8859-1), Windows code page 1252 on the baked Windows 3.1
 * faces (microsoft-cp1252), and the Adobe Symbol encoding on the Symbol face. This maps each scalar to the best of
 * these, in this order:
 *   1. ASCII and Latin-1 as themselves;
 *   2. the 27 characters code page 1252 adds at 0x80-0x9F (curly quotes, dashes, the bullet, the euro sign ...), as
 *      their cp1252 byte when the face has that glyph, otherwise a Latin-1 spelling ("--" for an em dash);
 *   3. Greek and the mathematical signs on the Symbol face, when there is one;
 *   4. a transliteration (Latin Extended-A to its base letters, typographic spaces and hyphens, ligatures, arrows
 *      and relations spelt in ASCII when the Symbol face is absent);
 *   5. otherwise nothing: the caller draws the visible fallback glyph (a hollow box, as Windows drew a missing
 *      glyph), so no character ever disappears silently.
 * Combining marks (U+0300-U+036F) and the zero-width characters map to no bytes on purpose: the base letter shows.
 * X-free and allocation-free; the tests drive it with tables. */
#ifndef E26_TEXT8_H
#define E26_TEXT8_H
#include <stddef.h>
#include <stdint.h>

enum { T8_LATIN1 = 0, T8_SYMBOL = 1, T8_NONE = 2 };
/* what the faces in use can show (t8_map's flags) */
#define T8_HAVE_CP1252 1      /* the face has glyphs at 0x80-0x9F (the baked cp1252 faces) */
#define T8_HAVE_SYMBOL 2      /* a Symbol face is available */
#define T8_MAXOUT 8           /* the longest spelling t8_map writes */

/* one scalar from UTF-8 at s (n bytes available): the bytes consumed (1-4), *cp the scalar, or U+FFFD and 1 for a
 * malformed, overlong or surrogate sequence */
int t8_decode(const char *s, size_t n, uint32_t *cp);
/* the scalar as UTF-8 into out (4 bytes): its length */
int t8_encode(uint32_t cp, char *out);
/* the byte code page 1252 gives the scalar, or -1 */
int t8_cp1252(uint32_t cp);
/* the byte the Adobe Symbol encoding gives the scalar (Greek, mathematics, arrows), or -1 */
int t8_symbol(uint32_t cp);
/* a spelling of the scalar in ASCII or Latin-1, or NULL; "" for the characters that draw nothing */
const char *t8_translit(uint32_t cp);
/* the scalar for the faces in flags: writes up to T8_MAXOUT bytes to out, sets *len and returns T8_LATIN1 (bytes for
 * the text face), T8_SYMBOL (one byte for the Symbol face) or T8_NONE (draw the fallback glyph) */
int t8_map(uint32_t cp, int flags, char *out, int *len);
/* a whole UTF-8 string to 8-bit bytes for one face (titles, outline entries): as t8_map, with '?' where no glyph
 * exists and Symbol characters spelt as t8_translit gives them; NUL-terminates; returns the length */
size_t t8_to_8bit(const char *in, size_t n, int flags, char *out, size_t outn);
/* 1 when the scalar is part of a word (letters, digits, underscore; word under the pointer, search terms) */
int t8_is_word(uint32_t cp);

#endif
