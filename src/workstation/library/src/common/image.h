/* image.h: RGBA images and the asset store's pixmap files.
 * The store writes Netpbm files: P6 (PPM, 8-bit RGB) when every pixel is opaque, P7 (PAM, RGB_ALPHA) when a
 * bitmap uses palette indices its palette does not define (those pixels stay transparent, never invented).
 * Netpbm tools (pamtopng, pnmtopng) read both, so a private asset can be viewed without Deep32 Library. */
#ifndef E26_IMAGE_H
#define E26_IMAGE_H
#include <stddef.h>
#include <stdint.h>

typedef struct {
	int w, h;
	unsigned char *rgba; /* w*h*4, top-down rows, R G B A */
} e26_image;

int e26_image_alloc(e26_image *im, int w, int h); /* 0 ok, -1 limit/alloc */
void e26_image_free(e26_image *im);
int e26_image_opaque(const e26_image *im);
/* SHA-256 over the RGBA buffer (top-down, 4 bytes per pixel): the store's pixel identity. */
void e26_image_sha256(const e26_image *im, char hex[65]);
/* Write P6 when opaque, else P7 RGB_ALPHA. Returns 0 ok. */
int e26_image_write_pnm(const e26_image *im, const char *path);
/* Read P6 or P7 (RGB, RGB_ALPHA). Returns 0 ok, and a short reason in err. */
int e26_image_read_pnm(const char *path, e26_image *im, char *err, size_t errlen);

#define E26_IMAGE_MAX_PIXELS (40000000L)

#endif
