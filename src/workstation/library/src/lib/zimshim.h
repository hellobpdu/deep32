/* zimshim.h: the C side of e26-lib's two C++ files (W1-DATA). zim.cpp wraps libzim (reader.zim and the
 * encyclopedia layers), fold.cpp wraps ICU (NFKC then full case folding, as the pack builder folds sort keys).
 * Both are compiled only when the Makefile carries docs/rooms/DATA-makefile.diff (E26_LIB_ZIM). */
#ifndef E26_ZIMSHIM_H
#define E26_ZIMSHIM_H
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* an opened ZIM archive, or NULL (err says why) */
void *lib_zim_open(const char *path, char *err, size_t errlen);
void lib_zim_close(void *z);
/* the entry at path (redirects followed): 1 and up to limit bytes from offset (malloc'd; *total is the item's
 * size), 0 when the archive has no such entry, -1 on a read error */
int lib_zim_read(void *z, const char *path, uint64_t offset, size_t limit, unsigned char **data, size_t *len,
                 uint64_t *total);
/* NFKC, then full case folding (Python's unicodedata.normalize("NFKC", s).casefold()): malloc'd UTF-8, or NULL */
char *lib_icu_fold(const char *s);

#ifdef __cplusplus
}
#endif
#endif
