/* picture.h: pictures in Deep32 Library's frame (W1-IMAGE: plan R3, W10, and the engine half of A10's media frame).
 *
 * A control of kind "picture" shows one picture fitted into its rectangle on the Library's ground (its bg, else its
 * screen's ground), the way the 1995 media frame and Gallery show theirs:
 *   control figure
 *       kind picture
 *       rect 330 120 300 200
 *       prop image media:fig-0042     the picture (or "source <name>": a room's source gives it at run time)
 *       prop mime image/webp          optional: the declared type (else from the name's extension)
 *       prop fit fit                  fit (whole picture, padded with the ground; the default) or fill (cropped)
 *       prop filter bilinear          enlarging: bilinear (the default, photographs) or nearest (diagrams, pixel art)
 *       prop dither 1                 always the period's 256 colours (otherwise the Colors256 setting decides)
 *   end
 * A picture spec is "<scheme>:<rest>":
 *   store:<asset>   a bitmap of the private asset store (decoded in the interface: the store's own Netpbm files)
 *   file:<path>     a file Deep32 Library may read (an original in the Library, a fixture): opened read-only here and
 *                   handed to e26-render, which decodes it (WebP, PNG, JPEG, GIF, SVG)
 *   <other>:...     a scheme a room provides (picture_provider): ARTICLE and GALLERY add "media:" over e26-lib
 * A spec with no scheme is a store asset. Each picture is decoded for its box's device pixels (gfx_device_size: the
 * box itself at k = 1, k times it in SHARP mode, so a picture is never doubled) and drawn 1:1 (gfx_picture_draw).
 * Pictures are decoded once per spec, device size and options and kept in the asset
 * cache (xassets.h) as they are drawn; until a picture arrives its box shows the ground only, and a picture that
 * cannot be shown leaves the ground and says why on stderr (with -v). No image parser runs in the interface
 * process: every byte of a WebP, PNG, JPEG, GIF or SVG goes to e26-render. */
#ifndef E26_PICTURE_H
#define E26_PICTURE_H
#include <stddef.h>

struct e26_app;
struct gfx_ctx;

enum picture_status { PICTURE_IDLE, PICTURE_PENDING, PICTURE_READY, PICTURE_FAILED };
enum picture_box_mode { PICTURE_CHECK, PICTURE_DRAW, PICTURE_LOAD };
/* Fit one picture to an existing box without painting its ground. CHECK and DRAW never start a request. */
enum picture_status picture_box(struct gfx_ctx *g, const char *spec, int x, int y, int w, int h, long ground,
                                enum picture_box_mode mode);
int picture_waiting(const char *spec);    /* the provider still owns the fetch, not IMAGE */
int picture_inline_decodes(void);          /* sent decodes, including results no longer wanted */
void picture_source_failed(struct e26_app *a, const char *spec);

/* a scheme's opener: 1 with *fd (read-only, taken by the caller) and mime filled; 0 when the descriptor will come
 * later through picture_feed; -1 when the picture cannot be had */
typedef int (*picture_open_fn)(struct e26_app *a, const char *spec, int *fd, char *mime, size_t mimelen);
int picture_provider(const char *scheme, picture_open_fn fn);   /* "media" -> fn; 0, or -1 when the table is full */
/* the descriptor for a spec whose opener answered 0 (taken); every box waiting for that spec is decoded from it */
void picture_feed(struct e26_app *a, const char *spec, int fd, const char *mime);
/* forgets every decoded size of a spec (its source changed) */
void picture_forget(struct e26_app *a, const char *spec);
/* Start a fresh Enlarge view; late answers from an earlier view are ignored. */
void picture_view_reset(void);
void picture_view_draw(struct gfx_ctx *g, const char *spec, int w, int h);
int picture_view_change(const char *command);
/* u14 S4: the picture is smaller than the viewer's canvas (Fit shows it 1:1; "Fit (enlarge)" is offered) */
int picture_view_small(void);
const char *picture_view_info(char *buf, size_t n);
/* the period mode (W10): 1 when Colors256 is on for this session */
int picture_period_mode(void);
/* the MIME type of a name by its extension (.webp .png .jpg .jpeg .gif .svg), or NULL */
const char *picture_mime_of(const char *name);

#endif
