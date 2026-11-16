/* dib.h: Windows DIB decoder (BITMAPCOREHEADER and BITMAPINFOHEADER; 1/4/8/16/24/32 bpp; BI_RGB, BI_RLE8,
 * BI_RLE4, BI_BITFIELDS), with or without the 14-byte BITMAPFILEHEADER. Strict: dimensions, palette and RLE
 * streams are bounds-checked; nothing is guessed. Palette indices the palette does not define are kept as
 * transparent pixels (alpha 0) and counted, never given an invented colour. */
#ifndef E26_DIB_H
#define E26_DIB_H
#include <stddef.h>
#include "../common/image.h"

typedef struct {
	int width, height, bpp, compression, header_size, palette_entries;
	int file_header;        /* 1 when a "BM" header was present */
	long undefined_pixels;  /* pixels whose palette index the palette does not define */
	int rle4_padding;       /* odd-width RLE4 rows that encode the unused final nibble (seen on the disc) */
	int rle_no_eob;         /* RLE stream ended without the end-of-bitmap escape (accepted, reported) */
} dib_info;

/* icon: 1 for ICON/CURSOR resources (height covers XOR + AND masks; the AND mask becomes alpha).
 * Returns 0 on success; on error a reason in err and no image. */
int dib_decode(const unsigned char *p, size_t n, int icon, e26_image *out, dib_info *info, char *err, size_t errlen);

/* Build 3: the same decode as the screen showed it through a 256-colour system palette: for an 8-bit DIB with a
 * full 256-entry colour table, entries 0-9 and 246-255 take the system's static colours (dib_sys_static) in place
 * of the table's. Any other DIB decodes exactly as dib_decode. */
int dib_decode_sys(const unsigned char *p, size_t n, int icon, e26_image *out, dib_info *info, char *err, size_t errlen);
extern const unsigned char dib_sys_static[20][3]; /* R, G, B of static entry k (system index dib_sys_index(k)) */
int dib_sys_index(int k);                         /* 0-9 -> 0-9, 10-19 -> 246-255 */

#endif
