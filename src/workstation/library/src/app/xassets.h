/* xassets.h: the asset cache beyond library_app.h's three calls (W1-IMAGE, plan E6). xassets.c keeps store bitmaps and
 * decoded pictures as X pixmaps in the e26_app's slots, least recently used first out: a slot is given up when the
 * cache passes its byte budget (PictureCacheMB in LIBRARY.INI, default 64 MiB) or runs out of slots, so any number
 * of pictures (a thousand thumbnails) cycle through it. The XA_KEEP most recently used entries are never given up,
 * so a caller holding a few xasset pointers from one drawing pass keeps them. An evicted store bitmap is read
 * again on its next use, pixel for pixel the same. */
#ifndef E26_XASSETS_H
#define E26_XASSETS_H
#include <stddef.h>

struct e26_app;
struct e26_xasset;

#define XA_DEFAULT_MB 64
#define XA_KEEP 48

/* a cached entry by name, without reading the store; NULL when not held */
struct e26_xasset *xa_find(struct e26_app *a, const char *name);
/* a w x h RGBA picture uploaded under name (replacing one of that name); NULL when it cannot be held. The pixels are
 * copied and not kept (xa_sample answers -1 for it); transparent pixels get a mask as store bitmaps do. */
struct e26_xasset *xa_put_rgba(struct e26_app *a, const char *name, const unsigned char *rgba, int w, int h);
void xa_forget(struct e26_app *a, const char *name);             /* frees the entry of that name, if held */
void xa_forget_prefix(struct e26_app *a, const char *prefix);    /* frees every entry whose name starts so */
void xa_set_budget(size_t bytes);                                /* 0: PictureCacheMB, else XA_DEFAULT_MB MiB */
size_t xa_budget(void);
size_t xa_used(void);                                            /* bytes held (server pixmaps and kept pixels) */
int xa_live(void);                                               /* entries held */
unsigned long xa_evictions(void);                                /* entries given up so far */

#endif
