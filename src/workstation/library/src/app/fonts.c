/* fonts.c: the period font sets and the type size (docs/FONTS.md has the evidence for every face and size), and the
 * k-times faces gfx_text draws with in SHARP mode (W1-SHARP, below), these staying the logical metrics.
 *
 * u14 (readability spec M1-M3, the "Text size" ladder): in the native article viewport the reader's body is
 * (13 x ReaderZoom + 50) / 100 px, ReaderZoom one of 100 125 150 175 200 250 (0 = auto: 150 on a >= 140-dpi panel
 * from XRandR, 125 at 115-139, else 100; E26_DPI overrides the panel's dpi). Bitmap faces snap to their nearest native
 * strike (a tie takes the larger) and a server-scaled bitmap is never taken; outline faces load the size exactly. The
 * other roles derive from the snapped body (section 2.2): small 0.85, mono 0.9 (Lucida Typewriter for the CDE faces,
 * Courier New otherwise), mono small 0.75, headings 1.6 / 1.35 / 1.15 (F_HEAD3). The line pitch is ReaderSpacing's
 * 1.35 / 1.5 / 1.7 of the body, at least the tallest body face plus 2. The 640x480 letterbox keeps zoom 100, the
 * TextStyle size, its period pitch (18/20/24) and the period derived sizes (headings 1.4 and 1.23, Courier New mono),
 * so the 1995 look is unchanged there; only a bitmap reader face snaps to its native strike. */
#include "library_app.h"
#include "appearance.h"
#include <X11/Xatom.h>
#include <X11/extensions/Xrandr.h>
#include <Xm/Xm.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

/* Fonts (docs/FONTS.md has the evidence for every face and size). Three sets, -fonts NAME or *fontSet:
 *   e95     (default) the period design's own faces at its own pixel sizes on a 96-dpi VGA screen: Arial 8 pt = 11 px
 *           for the interface text the program drew (its font factory, GetFont 101/102), Arial 7 pt = 9 px small
 *           (GetFont 100), Times New Roman 18 pt = 24 px for titles (the MediaView TitlePane style), Arial
 *           10/11/14 pt = 13/15/19 px article text by type size S/M/L (the sizes the period program seg100:1984 gives the
 *           article styles; Small, the compiled size, is its default), Courier New 13 px for preformatted RFC text.
 *   win31   as e95, but the chrome in Windows 3.1's raster faces: MS Sans Serif 8 (11 px; the face baked into most of
 *           the period design's button bitmaps), its GDI-simulated bold, and Small Fonts 7 (9 px).
 *   build1  build 1's X core faces (Helvetica, Times Bold, Courier at 100 dpi): the earlier look, unchanged.
 * The period faces are the owner's own (Windows 3.1 disks, the period design disc), installed privately by
 * tools/fonts/install-vm.sh; the X server needs that font path. Per slot the first pattern that loads wins; a
 * missing period face falls back to build 1's face (with a warning), then to "fixed". */
#define E26_HELV(w, px) "-adobe-helvetica-" w "-r-normal--" #px "-*-*-*-p-*-iso8859-1"
#define E26_ARIAL(w, px) "-monotype-arial-" w "-r-normal--" #px "-*-*-*-p-*-iso8859-1"
#define E26_MSWIN(face, w, px, pt) "-ms-" face "-" w "-r-normal--" #px "-" #pt "-96-96-p-*-microsoft-cp1252"
#define E26_TIMES "-adobe-times-bold-r-normal--34-240-100-100-p-*-iso8859-1"
#define E26_COUR "-adobe-courier-medium-r-normal--14-100-100-100-m-*-iso8859-1"
#define E26_TNR24 "-monotype-times new roman-medium-r-normal--24-*-*-*-p-*-iso8859-1"
#define E26_CNEW13 "-monotype-courier new-medium-r-normal--13-*-*-*-m-*-iso8859-1"
#define E26_TNR12 "-monotype-times new roman-medium-r-normal--12-*-*-*-p-*-iso8859-1"

typedef struct {
	const char *name;
	const char *pat[F_NFONTS][3];   /* body slots unused: by type size below */
	const char *body[2];            /* printf patterns (%d = pixel size), medium and bold */
	int body_px[3];                 /* type size S, M, L */
	int body_pitch[3];              /* line pitch by type size; 0 = the font's own height (build 1). e95/win31: Small
	                                   18 [m] (capture 35: Arial 13 px, whose Windows line height is 15, plus 3);
	                                   Medium 20 and Large 24 [i]: Windows' line height of Arial 15 and 19 px (17,
	                                   21) plus the same 3 px (no capture at those sizes) */
	int default_size;               /* 0 S, 1 M, 2 L: the type size when -typesize is not given */
} font_set;

static const font_set font_sets[] = {
	{ "e95", {
		{ E26_ARIAL("medium", 11), E26_HELV("medium", 12), "fixed" },
		{ E26_ARIAL("bold", 11), E26_HELV("bold", 12), "fixed" },
		{ E26_ARIAL("medium", 9), E26_HELV("medium", 10), "fixed" },
		{ E26_TNR24, E26_TIMES, "fixed" },
		{ NULL, NULL, NULL }, { NULL, NULL, NULL },
		{ E26_CNEW13, E26_COUR, "fixed" },
		{ E26_ARIAL("medium", 10), E26_HELV("medium", 10), "fixed" },  /* ui10: the wizard's Close (capture256 20) */
		{ E26_ARIAL("medium", 13), E26_HELV("medium", 14), "fixed" },  /* ui13, ui-bold13: GetFont 104 and 105, Arial 10 pt */
		{ E26_ARIAL("bold", 13), E26_HELV("bold", 14), "fixed" },      /* (the Pinpointer's panels: capture256 10-14, 135) */
		{ E26_TNR12, "-adobe-times-medium-r-normal--12-*-*-*-p-*-iso8859-1", "fixed" } }, /* serif12: GetFont 200, the
		                                                                  Words hints' numbers (capture256 10) */
	  { "-monotype-arial-medium-r-normal--%d-*-*-*-p-*-iso8859-1", "-monotype-arial-bold-r-normal--%d-*-*-*-p-*-iso8859-1" },
	  { 13, 15, 19 }, { E26_LINE, 20, 24 }, 0 },
	{ "win31", {
		{ E26_MSWIN("ms sans serif", "medium", 11, 80), E26_HELV("medium", 12), "fixed" },
		{ E26_MSWIN("ms sans serif", "bold", 11, 80), E26_HELV("bold", 12), "fixed" },
		{ E26_MSWIN("small fonts", "medium", 9, 70), E26_HELV("medium", 10), "fixed" },
		{ E26_TNR24, E26_TIMES, "fixed" },
		{ NULL, NULL, NULL }, { NULL, NULL, NULL },
		{ E26_CNEW13, E26_COUR, "fixed" },
		{ E26_ARIAL("medium", 10), E26_HELV("medium", 10), "fixed" },  /* ui10: the wizard's Close (capture256 20) */
		{ E26_ARIAL("medium", 13), E26_HELV("medium", 14), "fixed" },  /* ui13, ui-bold13: Arial 10 pt, as e95 */
		{ E26_ARIAL("bold", 13), E26_HELV("bold", 14), "fixed" },
		{ E26_TNR12, "-adobe-times-medium-r-normal--12-*-*-*-p-*-iso8859-1", "fixed" } },
	  { "-monotype-arial-medium-r-normal--%d-*-*-*-p-*-iso8859-1", "-monotype-arial-bold-r-normal--%d-*-*-*-p-*-iso8859-1" },
	  { 13, 15, 19 }, { E26_LINE, 20, 24 }, 0 },
	{ "build1", { /* build 1's table, verbatim */
		{ "-adobe-helvetica-medium-r-normal--12-*-*-*-p-*-iso8859-1", "-*-helvetica-medium-r-normal--12-*", "fixed" },
		{ "-adobe-helvetica-bold-r-normal--12-*-*-*-p-*-iso8859-1", "-*-helvetica-bold-r-normal--12-*", "fixed" },
		{ "-adobe-helvetica-medium-r-normal--10-*-*-*-p-*-iso8859-1", "-*-helvetica-medium-r-normal--10-*", "fixed" },
		{ E26_TIMES, "-adobe-times-bold-r-normal--34-*", "-*-times-bold-r-normal--24-*" },
		{ NULL, NULL, NULL }, { NULL, NULL, NULL },
		{ E26_COUR, "-*-courier-medium-r-normal--14-*", "fixed" },
		{ "-adobe-helvetica-medium-r-normal--10-*-*-*-p-*-iso8859-1", "-*-helvetica-medium-r-normal--10-*", "fixed" },
		{ E26_HELV("medium", 14), "-*-helvetica-medium-r-normal--14-*", "fixed" },   /* ui13, ui-bold13 (build 2's fix round) */
		{ E26_HELV("bold", 14), "-*-helvetica-bold-r-normal--14-*", "fixed" },
		{ "-adobe-times-medium-r-normal--12-*-*-*-p-*-iso8859-1", "-*-times-medium-r-normal--12-*", "fixed" } },
	  { "-adobe-helvetica-medium-r-normal--%d-*-100-100-p-*-iso8859-1", "-adobe-helvetica-bold-r-normal--%d-*-100-100-p-*-iso8859-1" },
	  { 14, 17, 20 }, { 0, 0, 0 }, 1 },
};

static String font_set_name;
static XtResource font_resources[] = {
	{ "fontSet", "FontSet", XmRString, sizeof(String), 0, XmRString, (XtPointer)"e95" },
};

enum { READER_ORIGINAL, READER_HELVETICA, READER_TIMES, READER_LUCIDA, READER_TIMES_NEW_ROMAN };
const e26_reader_face fonts_reader_faces[] = {
	[READER_ORIGINAL] = { "original", "Deep32 Encyclopedia Classic" },
	[READER_HELVETICA] = { "helvetica", "Helvetica (CDE Sans)" },
	[READER_TIMES] = { "times", "Times (CDE Serif)" },
	[READER_LUCIDA] = { "lucida", "Lucida (Workstation Sans)" },
	[READER_TIMES_NEW_ROMAN] = { "times-new-roman", "Times New Roman" },
};
const size_t fonts_reader_face_count = XtNumber(fonts_reader_faces);

/* A negative choice follows the theme. The effective face can differ from
 * the choice on an X server that does not provide that font. */
static int reader_face_by_id(const char *id)
{
	size_t i;

	for (i = 0; id && i < fonts_reader_face_count; i++)
		if (!strcmp(id, fonts_reader_faces[i].id)) return (int)i;
	return -1;
}

const char *fonts_reader_theme_name(size_t theme)
{
	int face = reader_face_by_id(e26_theme_reader_font(theme));
	return fonts_reader_faces[face < 0 ? READER_ORIGINAL : face].label;
}

/* These are optional fonts already provided by the X server. Keep the provider
 * and family explicit so an unavailable choice cannot quietly load a different face. */
static const char *const reader_xlfd[] = {
	[READER_HELVETICA] = "-adobe-helvetica-%s-%c-normal--%d-*-*-*-p-*-iso8859-1",
	[READER_TIMES] = "-adobe-times-%s-%c-normal--%d-*-*-*-p-*-iso8859-1",
	[READER_LUCIDA] = "-b&h-lucida-%s-%c-normal-sans-%d-*-*-*-p-*-iso8859-1",
	[READER_TIMES_NEW_ROMAN] = "-monotype-times new roman-%s-%c-normal--%d-*-*-*-p-*-iso8859-1",
};
/* each reader face's foundry and family, for its native sizes (native_sizes) */
static const char *const reader_family[] = {
	[READER_ORIGINAL] = "monotype-arial",
	[READER_HELVETICA] = "adobe-helvetica",
	[READER_TIMES] = "adobe-times",
	[READER_LUCIDA] = "b&h-lucida",
	[READER_TIMES_NEW_ROMAN] = "monotype-times new roman",
};
enum { READER_REGULAR, READER_BOLD, READER_ITALIC, READER_BOLD_ITALIC, READER_SMALL, READER_NROLES };
static const int reader_slots[] = { F_BODY, F_BODY_BOLD, F_BODY_ITALIC, F_BODY_BOLD_ITALIC, F_BODY_SMALL };

/* ---- u14: the Text size ladder, the panel's dpi, native sizes and crisp faces (readability spec M1, M2) */

const int fonts_zoom_ladder[E26_ZOOM_STEPS] = { 100, 125, 150, 175, 200, 250 };

/* x * num / den, rounded half up (sizes are positive) */
static int scaled(int x, int num, int den)
{
	return (2 * x * num + den) / (2 * den);
}

/* the zoom the faces are loaded at: the Text size in the native article viewport, 100 in the letterbox */
static int effective_zoom(const e26_app *a)
{
	return a->font_native && a->reader_zoom > 0 ? a->reader_zoom : 100;
}

static int reader_px(const e26_app *a, int px)
{
	return scaled(px, effective_zoom(a), 100);
}

/* XLFD field k of a font name (1 foundry, 2 family ... 7 PIXEL_SIZE ... 12 AVERAGE_WIDTH), NULL when it has none */
static const char *xlfd_at(const char *name, int k)
{
	const char *p = name;
	while (p && k-- > 0) {
		p = strchr(p, '-');
		if (p) p++;
	}
	return p;
}

enum { FACE_NATIVE, FACE_OUTLINE, FACE_SCALED };
static const char *const face_kind_name[] = { "native bitmap", "outline", "SCALED bitmap" };

/* what the server gave: an outline face (RASTERIZER_NAME, drawn at its size), a native bitmap strike, or a bitmap the
 * server enlarged or shrank (it carries RAW_PIXEL_SIZE, or its FONT name's AVERAGE_WIDTH is 0): never taken for the
 * reader (spec 2.1) */
static int face_kind(e26_app *a, XFontStruct *f)
{
	Atom ras = XInternAtom(a->dpy, "RASTERIZER_NAME", True), raw = XInternAtom(a->dpy, "RAW_PIXEL_SIZE", True);
	unsigned long v;
	char *name = NULL;
	const char *avg;
	int kind = FACE_NATIVE;
	if (ras != None && XGetFontProperty(f, ras, &v)) return FACE_OUTLINE;
	if (raw != None && XGetFontProperty(f, raw, &v)) return FACE_SCALED;
	if (XGetFontProperty(f, XA_FONT, &v)) name = XGetAtomName(a->dpy, (Atom)v);
	avg = name ? xlfd_at(name, 12) : NULL;
	if (avg && avg[0] == '0' && avg[1] == '-') kind = FACE_SCALED;
	if (name) XFree(name);
	return kind;
}

/* the native pixel sizes of a bitmap family ("adobe-helvetica"), ascending: XListFonts once per family on its medium
 * roman, keeping the names whose PIXEL_SIZE and AVERAGE_WIDTH are not 0 (a 0 is the server's scalable name). An outline
 * family lists none (n 0): every size is exact. */
#define NATIVE_MAX 40
typedef struct {
	char family[48];
	int n, px[NATIVE_MAX];
} native_list;
static native_list natives[16];
static int nnatives;

static const native_list *native_sizes(e26_app *a, const char *family)
{
	native_list *nl;
	char pattern[160], **names;
	int i, count = 0;
	for (i = 0; i < nnatives; i++)
		if (!strcmp(natives[i].family, family)) return &natives[i];
	if (nnatives == (int)(sizeof natives / sizeof natives[0])) nnatives = 0; /* a handful of families: never */
	nl = &natives[nnatives++];
	memset(nl, 0, sizeof *nl);
	snprintf(nl->family, sizeof nl->family, "%s", family);
	snprintf(pattern, sizeof pattern, "-%s-medium-r-normal-*-*-*-*-*-*-*-iso8859-1", family);
	names = XListFonts(a->dpy, pattern, 1000, &count);
	for (i = 0; names && i < count; i++) {
		const char *pxf = xlfd_at(names[i], 7), *avg = xlfd_at(names[i], 12);
		int px = pxf ? atoi(pxf) : 0, k, j;
		if (px <= 0 || !avg || atoi(avg) <= 0) continue;
		for (k = 0; k < nl->n && nl->px[k] < px; k++) { }
		if ((k < nl->n && nl->px[k] == px) || nl->n == NATIVE_MAX) continue;
		for (j = nl->n; j > k; j--) nl->px[j] = nl->px[j - 1];
		nl->px[k] = px;
		nl->n++;
	}
	if (names) XFreeFontNames(names);
	if (a->verbose) {
		fprintf(stderr, "d32library: native sizes %s:", family);
		for (i = 0; i < nl->n; i++) fprintf(stderr, " %d", nl->px[i]);
		fprintf(stderr, "%s\n", nl->n ? "" : " (outline: any size)");
	}
	return nl;
}

static int is_native(const native_list *nl, int px)
{
	int i;
	if (!nl->n) return 1;
	for (i = 0; i < nl->n; i++)
		if (nl->px[i] == px) return 1;
	return 0;
}

/* the native size nearest px, a tie taking the larger; an outline family: px */
static int snap_nearest(const native_list *nl, int px)
{
	int i, best = px, gap = INT_MAX;
	for (i = 0; i < nl->n; i++) {
		int d = nl->px[i] > px ? nl->px[i] - px : px - nl->px[i];
		if (d < gap || (d == gap && nl->px[i] > best)) { gap = d; best = nl->px[i]; }
	}
	return best;
}

/* the largest native size not above px (else the smallest); an outline family: px */
static int snap_floor(const native_list *nl, int px)
{
	int i, best = 0;
	if (!nl->n) return px;
	for (i = 0; i < nl->n; i++)
		if (nl->px[i] <= px) best = nl->px[i];
	return best ? best : nl->px[0];
}

/* a face at exactly px and not server-scaled (pattern holds one %d), else NULL */
static XFontStruct *crisp_font(e26_app *a, const char *pattern, int px)
{
	Atom size_atom = XInternAtom(a->dpy, "PIXEL_SIZE", True);
	char name[200];
	unsigned long actual;
	XFontStruct *f;
	if (px < 6) return NULL;
	snprintf(name, sizeof name, pattern, px);
	if (!(f = XLoadQueryFont(a->dpy, name))) return NULL;
	if ((size_atom == None || (XGetFontProperty(f, size_atom, &actual) && actual == (unsigned long)px)) &&
	    face_kind(a, f) != FACE_SCALED) return f;
	XFreeFont(a->dpy, f);
	return NULL;
}

/* a bitmap family's face snapped (nearest, or floor when floor is set) to its natives, else NULL; family as
 * native_sizes, pattern as crisp_font */
static XFontStruct *snapped_font(e26_app *a, const char *family, const char *pattern, int px, int floor)
{
	const native_list *nl = native_sizes(a, family);
	return crisp_font(a, pattern, floor ? snap_floor(nl, px) : snap_nearest(nl, px));
}

/* the dpi of the panel the window is on: E26_DPI, else XRandR's connected output under the window's centre (the
 * first connected one with a size when none is), its CRTC's pixels over its millimetres; 0 when unknown (Xvfb reports
 * 0 mm). The X server's own dpi is not used: Deep32 forces it to 100 whatever the panel. */
static int panel_dpi(e26_app *a)
{
	const char *env = getenv("E26_DPI");
	int ev, er, major = 0, minor = 0, i, cx = 0, cy = 0, dpi = 0, best = 0;
	Window root, child;
	XRRScreenResources *res;
	if (env && *env) {
		char *end;
		long v = strtol(env, &end, 10);
		if (!*end && v >= 50 && v <= 600) return (int)v;
	}
	if (!a->dpy || !XRRQueryExtension(a->dpy, &ev, &er) || !XRRQueryVersion(a->dpy, &major, &minor) ||
	    (major < 1 || (major == 1 && minor < 2))) return 0;
	root = RootWindow(a->dpy, DefaultScreen(a->dpy));
	if (a->win) {
		XWindowAttributes wa;
		if (XGetWindowAttributes(a->dpy, a->win, &wa))
			XTranslateCoordinates(a->dpy, a->win, root, wa.width / 2, wa.height / 2, &cx, &cy, &child);
	}
	res = (major > 1 || minor >= 3) ? XRRGetScreenResourcesCurrent(a->dpy, root) : XRRGetScreenResources(a->dpy, root);
	if (!res) return 0;
	for (i = 0; i < res->noutput; i++) {
		XRROutputInfo *out = XRRGetOutputInfo(a->dpy, res, res->outputs[i]);
		XRRCrtcInfo *crtc;
		if (!out) continue;
		if (out->connection == RR_Connected && out->mm_width > 0 && out->crtc &&
		    (crtc = XRRGetCrtcInfo(a->dpy, res, out->crtc)) != NULL) {
			int turned = (crtc->rotation & (RR_Rotate_90 | RR_Rotate_270)) != 0;
			int px = turned ? (int)crtc->height : (int)crtc->width;
			int d = (int)((px * 254L + out->mm_width * 5L) / (out->mm_width * 10L));
			int under = cx >= crtc->x && cx < crtc->x + (int)crtc->width && cy >= crtc->y && cy < crtc->y + (int)crtc->height;
			if (a->verbose) fprintf(stderr, "d32library: panel %s: %d px over %lu mm, %d dpi%s\n", out->name, px,
			                        out->mm_width, d, under ? " (under the window)" : "");
			if (under && !best) { dpi = d; best = 1; }
			else if (!dpi) dpi = d;
			XRRFreeCrtcInfo(crtc);
		}
		XRRFreeOutputInfo(out);
	}
	XRRFreeScreenResources(res);
	return dpi;
}

int fonts_auto_zoom(const e26_app *a)
{
	return a->panel_dpi >= 140 ? 150 : a->panel_dpi >= 115 ? 125 : 100;
}

int fonts_zoom_step(int zoom)
{
	int i, best = 0;
	for (i = 0; i < E26_ZOOM_STEPS; i++)
		if (abs(fonts_zoom_ladder[i] - zoom) < abs(fonts_zoom_ladder[best] - zoom)) best = i;
	return best;
}

/* ---- the reader's faces */

static XFontStruct *reader_font_at(e26_app *a, int face, int role, int px, Atom size_atom)
{
	const char *weight = role == READER_BOLD || role == READER_BOLD_ITALIC ? "bold" : "medium";
	int italic = role == READER_ITALIC || role == READER_BOLD_ITALIC;
	const char *slants = !italic ? "r" : face == READER_HELVETICA ? "oi" : "io";
	char pattern[120];
	if (px < 8) return NULL;
	for (; *slants; slants++) {
		XFontStruct *font;
		unsigned long actual;
		snprintf(pattern, sizeof pattern, reader_xlfd[face], weight, *slants, px);
		font = XLoadQueryFont(a->dpy, pattern);
		if (!font) continue;
		/* u14 M2: never a server-scaled bitmap (it is blurred or blocky) */
		if (XGetFontProperty(font, size_atom, &actual) && actual == (unsigned long)px && face_kind(a, font) != FACE_SCALED)
			return font;
		XFreeFont(a->dpy, font);
	}
	return NULL;
}

static XFontStruct *load_font(e26_app *a, const char *p0, const char *p1, const char *p2)
{
	const char *p[3] = { p0, p1, p2 };
	int i;
	for (i = 0; i < 3; i++) {
		XFontStruct *f = p[i] ? XLoadQueryFont(a->dpy, p[i]) : NULL;
		if (f) {
			if (a->verbose) fprintf(stderr, "d32library: font %s\n", p[i]);
			if (i > 0 && (strstr(p0, "-monotype-") || strstr(p0, "-ms-")))
				fprintf(stderr, "d32library: period font %s not found, using %s (tools/fonts/install-vm.sh)\n", p0, p[i]);
			return f;
		}
	}
	return XLoadQueryFont(a->dpy, "fixed");
}

/* Neighbours are found by actual font properties so a missing face never steps
 * into a different family, weight, or slant. */
static int article_neighbour[F_NFONTS][2];

/* the article size slots of the last type size (a slot holding the body face itself does not own it) */
static void free_article_sizes(e26_app *a)
{
	int i;
	for (i = F_ART_SIZE; i < F_NFONTS; i++) {
		if (a->font[i] && a->font[i] != a->font[F_BODY] && a->font[i] != a->font[F_BODY_BOLD]) XFreeFont(a->dpy, a->font[i]);
		a->font[i] = NULL;
	}
	memset(article_neighbour, 0, sizeof article_neighbour);
}

/* format 2.2's smaller and bigger text: the reader face's next native strikes below and above the body (two below,
 * one above... as before), or for an outline face the sizes one and two pixels away */
static void reader_extra_sizes(e26_app *a, Atom size_atom)
{
	const native_list *nl = native_sizes(a, reader_family[a->reader_face]);
	int role;
	for (role = READER_REGULAR; role <= READER_BOLD_ITALIC; role++) {
		int slot = F_ART_SIZE + role * 3, direction;
		unsigned long px;
		if (!XGetFontProperty(a->font[reader_slots[role]], size_atom, &px)) continue;
		for (direction = -1; direction <= 1; direction += 2) {
			int distance, wanted = direction < 0 ? 1 : 2;
			for (distance = 1; distance <= 16 && wanted; distance++) {
				int at = (int)px + direction * distance;
				XFontStruct *font;
				if (!is_native(nl, at) || (!nl->n && distance > 8)) continue;
				font = reader_font_at(a, a->reader_face, role, at, size_atom);
				if (font) {
					a->font[slot++] = font;
					wanted--;
				}
			}
		}
	}
}

static void load_article_sizes(e26_app *a)
{
	static const int sizes[2][2][F_ART_SIZE_COUNT] = {
		{ { 9, 10, 11, 13, 15, 19 }, { 11, 13, 15, 19 } },
		{ { 10, 12, 14, 17, 20 }, { 12, 14, 17, 20 } },
	};
	struct face_key { unsigned long family, weight, slant, px; } key[F_NFONTS] = {{0, 0, 0, 0}};
	Atom size_atom = XInternAtom(a->dpy, "PIXEL_SIZE", True);
	Atom weight_atom = XInternAtom(a->dpy, "WEIGHT_NAME", True);
	Atom slant_atom = XInternAtom(a->dpy, "SLANT", True);
	int bold, i, j;
	if (size_atom == None || weight_atom == None || slant_atom == None) return;
	if (a->reader_face != READER_ORIGINAL) reader_extra_sizes(a, size_atom);
	else for (bold = 0; bold < 2; bold++) {
		XFontStruct *base = a->font[bold ? F_BODY_BOLD : F_BODY];
		unsigned long family, px;
		char *name;
		int set;
		if (!base || !XGetFontProperty(base, XA_FAMILY_NAME, &family) ||
		    !XGetFontProperty(base, size_atom, &px)) continue;
		name = XGetAtomName(a->dpy, (Atom)family);
		if (!name) continue;
		set = !strcasecmp(name, "Arial") ? 0 : !strcasecmp(name, "Helvetica") ? 1 : -1;
		XFree(name);
		if (set < 0) continue;
		for (i = 0; i < F_ART_SIZE_COUNT && sizes[set][bold][i]; i++) {
			int slot = F_ART_SIZE + bold * F_ART_SIZE_COUNT + i;
			int target = reader_px(a, sizes[set][bold][i]);
			if (set) target = snap_nearest(native_sizes(a, "adobe-helvetica"), target);
			if (px == (unsigned long)target) a->font[slot] = base;
			else a->font[slot] = crisp_font(a, font_sets[set ? 2 : 0].body[bold], target);
		}
	}
	for (i = 0; i < F_NFONTS; i++) {
		XFontStruct *font = a->font[i];
		if (font && XGetFontProperty(font, XA_FAMILY_NAME, &key[i].family) &&
		    XGetFontProperty(font, weight_atom, &key[i].weight) &&
		    XGetFontProperty(font, slant_atom, &key[i].slant))
			XGetFontProperty(font, size_atom, &key[i].px);
	}
	for (i = 0; i < F_NFONTS; i++) {
		int nearest[2] = { INT_MAX, INT_MAX };
		if (!key[i].px) continue;
		for (j = F_ART_SIZE; j < F_NFONTS; j++) {
			int delta, larger, distance;
			if (!key[j].px || key[i].family != key[j].family ||
			    key[i].weight != key[j].weight || key[i].slant != key[j].slant) continue;
			delta = (int)key[j].px - (int)key[i].px;
			if (!delta) continue;
			larger = delta > 0;
			distance = larger ? delta : -delta;
			if (distance >= nearest[larger]) continue;
			nearest[larger] = distance;
			article_neighbour[i][larger] = j;
		}
	}
}

int fonts_article_step(int slot, int direction)
{
	int neighbour = article_neighbour[slot][direction > 0];
	return neighbour ? neighbour : slot;
}

/* the faces that follow the type size: the body and its bold (every set), and (LAYOUT-CR1) the article's italic, bold
 * italic, smaller body, smaller mono and Symbol faces from the same period TrueType files (Arial italic and bold
 * italic, Courier New 10 px, Symbol), absent in the build1 set and wherever the X server lacks them (the layout then
 * falls back: layout.h) */
static const font_set *body_set;

static XFontStruct *quiet_font(e26_app *a, const char *p)
{
	XFontStruct *f = XLoadQueryFont(a->dpy, p);
	if (f && a->verbose) fprintf(stderr, "d32library: font %s\n", p);
	return f;
}

/* the body px wanted: the ladder's (13 x zoom + 50) / 100 in the native reader, the TextStyle size in the letterbox */
static int body_target(const e26_app *a, int ts)
{
	return a->font_native ? scaled(body_set->body_px[0], effective_zoom(a), 100) : body_set->body_px[ts];
}

int fonts_zoom_px(e26_app *a, int zoom)
{
	int face = a->reader_face >= 0 && (size_t)a->reader_face < fonts_reader_face_count ? a->reader_face : READER_ORIGINAL;
	int px = scaled(body_set ? body_set->body_px[0] : 13, zoom, 100);
	if (face == READER_ORIGINAL && body_set && !strcmp(body_set->name, "build1")) return snap_nearest(native_sizes(a, "adobe-helvetica"), px);
	return snap_nearest(native_sizes(a, reader_family[face]), px);
}

/* Require matching regular, bold, italic and bold-italic at a common native pixel size, the one nearest the target
 * (a tie takes the larger); an outline face takes the target itself, or a pixel or two away. */
static int reader_load(e26_app *a, int face, int ts, XFontStruct *fonts[READER_NROLES])
{
	Atom size_atom = XInternAtom(a->dpy, "PIXEL_SIZE", True);
	const native_list *nl;
	int target = body_target(a, ts);
	int d, side, role;
	if (size_atom == None) return -1;
	nl = native_sizes(a, reader_family[face]);
	for (d = 0; d <= 40; d++)
		for (side = 0; side < (d ? 2 : 1); side++) {
			int px = side ? target - d : target + d;
			if (px < 8 || !is_native(nl, px) || (!nl->n && d > 2)) continue;
			for (role = READER_REGULAR; role <= READER_BOLD_ITALIC; role++) {
				fonts[role] = reader_font_at(a, face, role, px, size_atom);
				if (!fonts[role]) break;
			}
			if (role == READER_SMALL) {
				/* small: 0.85 of the body (the letterbox: the period three quarters), the largest strike not above */
				int small = a->font_native ? scaled(px, 85, 100) : (px * 3 + 2) / 4;
				fonts[READER_SMALL] = reader_font_at(a, face, READER_REGULAR, snap_floor(nl, small), size_atom);
				if (!fonts[READER_SMALL])
					fonts[READER_SMALL] = reader_font_at(a, face, READER_REGULAR, px, size_atom);
				if (fonts[READER_SMALL]) {
					if (a->verbose) fprintf(stderr, "d32library: reader font %s %d px (wanted %d)\n", fonts_reader_faces[face].id, px, target);
					return 0;
				}
			}
			while (role > 0) XFreeFont(a->dpy, fonts[--role]);
		}
	return -1;
}

/* the reader's interface face (F_RUI, F_RUI_BOLD): round(11 x ui_scale / 100) px, Helvetica (snapped to its natives)
 * for the Helvetica themes and face, else Arial; the chrome's own face when neither loads */
static int rui_shared[2];
static void rui_load(e26_app *a)
{
	const char *theme = e26_theme_reader_font(a->appearance_theme);
	int px = scaled(11, a->ui_scale > 0 ? a->ui_scale : 100, 100), bold;
	int helvetica = a->reader_face == READER_HELVETICA || (theme && !strcmp(theme, "helvetica"));
	for (bold = 0; bold < 2; bold++) {
		int slot = bold ? F_RUI_BOLD : F_RUI;
		XFontStruct *f = NULL;
		if (a->font[slot] && !rui_shared[bold]) XFreeFont(a->dpy, a->font[slot]);
		a->font[slot] = NULL;
		if (!helvetica)
			f = crisp_font(a, bold ? "-monotype-arial-bold-r-normal--%d-*-*-*-p-*-iso8859-1"
			                       : "-monotype-arial-medium-r-normal--%d-*-*-*-p-*-iso8859-1", px);
		if (!f)
			f = snapped_font(a, "adobe-helvetica", bold ? "-adobe-helvetica-bold-r-normal--%d-*-*-*-p-*-iso8859-1"
			                                            : "-adobe-helvetica-medium-r-normal--%d-*-*-*-p-*-iso8859-1", px, 0);
		rui_shared[bold] = !f;
		a->font[slot] = f ? f : a->font[bold ? F_UI_BOLD : F_UI];
	}
}

static void log_faces(e26_app *a)
{
	static const struct { int slot; const char *role; } roles[] = {
		{ F_BODY, "body" }, { F_BODY_BOLD, "bold" }, { F_BODY_ITALIC, "italic" }, { F_BODY_BOLD_ITALIC, "bold italic" },
		{ F_BODY_SMALL, "small" }, { F_FIXED, "mono" }, { F_FIXED_SMALL, "mono small" }, { F_HEAD, "H1" },
		{ F_SUBHEAD, "H2" }, { F_HEAD3, "H3" }, { F_RUI, "reader UI" }, { F_RUI_BOLD, "reader UI bold" },
	};
	size_t i;
	for (i = 0; i < sizeof roles / sizeof roles[0]; i++) {
		XFontStruct *f = a->font[roles[i].slot];
		unsigned long v;
		char *name = f && XGetFontProperty(f, XA_FONT, &v) ? XGetAtomName(a->dpy, (Atom)v) : NULL;
		fprintf(stderr, "d32library: face %s: %s (%s)\n", roles[i].role, name ? name : f ? "(no name)" : "(none)",
		        f ? face_kind_name[face_kind(a, f)] : "-");
		if (name) XFree(name);
	}
}

static int font_px(e26_app *a, XFontStruct *f, int fallback)
{
	Atom size_atom = XInternAtom(a->dpy, "PIXEL_SIZE", True);
	unsigned long v;
	return f && size_atom != None && XGetFontProperty(f, size_atom, &v) && v > 0 ? (int)v : fallback;
}

static void body_load(e26_app *a, int ts, XFontStruct *const *reader)
{
	const font_set *fs = body_set;
	char b1[120], b2[120], p[160];
	int build1 = !strcmp(fs->name, "build1"), i, px = body_target(a, ts), period = !a->font_native;
	int cde = reader && (a->reader_face == READER_HELVETICA || a->reader_face == READER_TIMES || a->reader_face == READER_LUCIDA);
	static const int small_px[3] = { 10, 11, 14 }; /* the letterbox's Arial small: about three quarters of 13, 15, 19 */
	static const int slots[] = { F_BODY, F_BODY_BOLD, F_BODY_ITALIC, F_BODY_BOLD_ITALIC, F_BODY_SMALL, F_FIXED, F_FIXED_SMALL,
	    F_SYMBOL, F_HEAD, F_HEAD_ITALIC, F_SUBHEAD, F_SUBHEAD_ITALIC, F_HEAD3 };
	free_article_sizes(a);
	for (i = 0; i < (int)(sizeof slots / sizeof slots[0]); i++)
		if (a->font[slots[i]]) {
			if (slots[i] != F_BODY_SMALL || a->font[F_BODY_SMALL] != a->font[F_SMALL]) XFreeFont(a->dpy, a->font[slots[i]]);
			a->font[slots[i]] = NULL;
		}
	if (reader) {
		for (i = 0; i < READER_NROLES; i++) a->font[reader_slots[i]] = reader[i];
	} else {
		const native_list *helv = native_sizes(a, "adobe-helvetica");
		int hpx = snap_nearest(helv, px);
		if (build1) px = hpx;
		snprintf(b1, sizeof b1, fs->body[0], px);
		snprintf(b2, sizeof b2, fs->body[1], px);
		a->font[F_BODY] = crisp_font(a, fs->body[0], px);
		a->font[F_BODY_BOLD] = crisp_font(a, fs->body[1], px);
		if (!a->font[F_BODY]) a->font[F_BODY] = crisp_font(a, "-adobe-helvetica-medium-r-normal--%d-*-*-*-p-*-iso8859-1", hpx);
		if (!a->font[F_BODY_BOLD]) a->font[F_BODY_BOLD] = crisp_font(a, "-adobe-helvetica-bold-r-normal--%d-*-*-*-p-*-iso8859-1", hpx);
		if (!a->font[F_BODY]) a->font[F_BODY] = load_font(a, b1, !build1 ? "-*-helvetica-medium-r-normal--14-*" : "-*-helvetica-medium-r-normal--17-*", "fixed");
		if (!a->font[F_BODY_BOLD]) a->font[F_BODY_BOLD] = load_font(a, b2, !build1 ? "-*-helvetica-bold-r-normal--14-*" : "-*-helvetica-bold-r-normal--17-*", "fixed");
	}
	px = font_px(a, a->font[F_BODY], px);   /* the roles derive from the body as loaded (snapped) */
	if (!build1) {
		/* headings: Times New Roman Bold at 1.6, 1.35 and 1.15 of the body (Times Bold's nearest strikes with the Times
		 * reader face, or wherever Times New Roman is missing); the letterbox keeps the period 1.4 and 1.23 */
		static const struct { int slot, num, period; char slant; } heads[] = {
			{ F_HEAD, 160, 140, 'r' }, { F_HEAD_ITALIC, 160, 140, 'i' }, { F_SUBHEAD, 135, 123, 'r' },
			{ F_SUBHEAD_ITALIC, 135, 123, 'i' }, { F_HEAD3, 115, 123, 'r' },
		};
		for (i = 0; i < (int)(sizeof heads / sizeof heads[0]); i++) {
			int head_px = scaled(px, period ? heads[i].period : heads[i].num, 100);
			XFontStruct *f = NULL;
			if (!(reader && a->reader_face == READER_TIMES)) {
				snprintf(p, sizeof p, "-monotype-times new roman-bold-%c-normal--%%d-*-*-*-p-*-iso8859-1", heads[i].slant);
				f = crisp_font(a, p, head_px);
			}
			if (!f) {
				snprintf(p, sizeof p, "-adobe-times-bold-%c-normal--%%d-*-*-*-p-*-iso8859-1", heads[i].slant);
				f = snapped_font(a, "adobe-times", p, head_px, 0);
			}
			a->font[heads[i].slot] = f;
		}
		if (!reader) {
			a->font[F_BODY_ITALIC] = crisp_font(a, "-monotype-arial-medium-i-normal--%d-*-*-*-p-*-iso8859-1", px);
			a->font[F_BODY_BOLD_ITALIC] = crisp_font(a, "-monotype-arial-bold-i-normal--%d-*-*-*-p-*-iso8859-1", px);
			a->font[F_BODY_SMALL] = crisp_font(a, "-monotype-arial-medium-r-normal--%d-*-*-*-p-*-iso8859-1",
			                                   period ? small_px[ts] : scaled(px, 85, 100));
		}
		/* mono 0.9 and mono small 0.75 of the body: Lucida Typewriter's largest strike not above it for the CDE faces,
		 * Courier New (exact) for Classic and Times New Roman; each falls back to the other. The letterbox keeps the
		 * set's period mono (Courier New 13 px) and Courier New 10 px. */
		if (period) a->font[F_FIXED_SMALL] = crisp_font(a, "-monotype-courier new-medium-r-normal--%d-*-*-*-m-*-iso8859-1", 10);
		else for (i = 0; i < 2; i++) {
			int slot = i ? F_FIXED_SMALL : F_FIXED, mono_px = scaled(px, i ? 75 : 90, 100);
			XFontStruct *f = NULL;
			if (cde) f = snapped_font(a, "b&h-lucidatypewriter", "-b&h-lucidatypewriter-medium-r-normal-sans-%d-*-*-*-m-*-iso8859-1", mono_px, 1);
			if (!f) f = crisp_font(a, "-monotype-courier new-medium-r-normal--%d-*-*-*-m-*-iso8859-1", mono_px);
			if (!f && !cde) f = snapped_font(a, "b&h-lucidatypewriter", "-b&h-lucidatypewriter-medium-r-normal-sans-%d-*-*-*-m-*-iso8859-1", mono_px, 1);
			if (!f) f = snapped_font(a, "adobe-courier", "-adobe-courier-medium-r-normal--%d-*-*-*-m-*-iso8859-1", mono_px, 1);
			a->font[slot] = f;
		}
		snprintf(p, sizeof p, "-misc-symbol-medium-r-normal--%d-*-*-*-p-*-microsoft-symbol", px);
		a->font[F_SYMBOL] = quiet_font(a, p);
	}
	if (!a->font[F_FIXED]) {
		if (effective_zoom(a) == 100)
			a->font[F_FIXED] = load_font(a, fs->pat[F_FIXED][0], fs->pat[F_FIXED][1], fs->pat[F_FIXED][2]);
		else {
			snprintf(p, sizeof p, "-adobe-courier-medium-r-normal--%d-*-*-*-m-*-iso8859-1",
			         snap_floor(native_sizes(a, "adobe-courier"), reader_px(a, 14)));
			a->font[F_FIXED] = load_font(a, p, fs->pat[F_FIXED][0], "fixed");
		}
	}
	if (!a->font[F_BODY_SMALL]) a->font[F_BODY_SMALL] = a->font[F_SMALL];
	a->reader_size = ts;
	if (a->font_native) {
		/* M3: ReaderSpacing's 1.35, 1.5 or 1.7 of the body, at least the tallest body face plus 2 */
		static const int spacing[3] = { 135, 150, 170 };
		int s = a->reader_spacing >= 0 && a->reader_spacing < 3 ? a->reader_spacing : E26_SPACING_NORMAL;
		a->line = scaled(px, spacing[s], 100);
		for (i = 0; i < 4; i++) {
			XFontStruct *f = a->font[reader_slots[i]];
			if (f && a->line < f->ascent + f->descent + 2) a->line = f->ascent + f->descent + 2;
		}
	} else {
		/* the letterbox: the period pitch of the type size (the period design's 18/20/24) */
		a->line = fs->body_pitch[ts] > 0 ? fs->body_pitch[ts] : a->font[F_BODY]->ascent + a->font[F_BODY]->descent;
		if (reader) for (i = READER_REGULAR; i <= READER_BOLD_ITALIC; i++) {
			int pitch = reader[i]->ascent + reader[i]->descent + 2;
			if (a->line < pitch) a->line = pitch;
		}
	}
	rui_load(a);
	if (a->verbose) {
		fprintf(stderr, "d32library: type size %c, body %d px, line pitch %d, zoom %d%% (%s; Text size %d%%%s, panel %d dpi)\n",
		        "SML"[ts], px, a->line, effective_zoom(a), a->font_native ? "native reader" : "letterbox", a->reader_zoom,
		        a->reader_zoom_auto ? " auto" : "", a->panel_dpi);
		log_faces(a);
	}
	load_article_sizes(a);
}

static int reader_apply(e26_app *a, int choice, int ts, int fallback)
{
	XFontStruct *reader[READER_NROLES];
	int face = choice;

	if (face < 0) {
		face = reader_face_by_id(e26_theme_reader_font(a->appearance_theme));
		if (face < 0) face = READER_ORIGINAL;
	}
	if (face != READER_ORIGINAL && reader_load(a, face, ts, reader)) {
		if (!fallback) return -1;
		face = READER_ORIGINAL;
	}
	a->reader_font_choice = choice;
	a->reader_face = face;
	body_load(a, ts, face == READER_ORIGINAL ? NULL : reader);
	return 0;
}

void fonts_type_size(e26_app *a, int ts)
{
	if (!body_set || ts < 0 || ts > 2) return;
	(void)reader_apply(a, a->reader_font_choice, ts, 1);
}

int fonts_reader_select(e26_app *a, int choice)
{
	if (!body_set || choice < -1 || (choice >= 0 && (size_t)choice >= fonts_reader_face_count)) return -1;
	return reader_apply(a, choice, a->reader_size, choice == -1);
}

int fonts_viewport(e26_app *a, int native)
{
	native = native != 0;
	if (!body_set || a->font_native == native) return 0;
	a->font_native = native;
	fonts_type_size(a, a->reader_size);
	return 1;
}

static int choice_index(const char *key, const char *const *names, int n, int def)
{
	const char *v = set_get(key);
	int i;
	for (i = 0; v && i < n; i++)
		if (!strcasecmp(v, names[i])) return i;
	return def;
}

const char *const fonts_spacing_names[3] = { "compact", "normal", "relaxed" };
const char *const fonts_column_names[3] = { "narrow", "normal", "wide" };
const char *const fonts_paper_names[2] = { "white", "warm" };

/* ReaderZoom (0 = auto) and the one-time TextStyle migration: with no ReaderZoom stored, TextStyle Medium or Large is
 * one or two steps above the panel's default, and is written as ReaderZoom so it happens once */
static void zoom_init(e26_app *a)
{
	int stored = set_stored("ReaderZoom") ? set_get_int("ReaderZoom", 0) : -1, ui;
	a->panel_dpi = panel_dpi(a);
	a->reader_zoom = fonts_auto_zoom(a);
	a->reader_zoom_auto = 1;
	if (stored > 0) {
		a->reader_zoom = fonts_zoom_ladder[fonts_zoom_step(stored)];
		a->reader_zoom_auto = 0;
	} else if (stored < 0 && set_stored("TextStyle")) {
		const char *v = set_get("TextStyle");
		int steps = v && (v[0] == 'm' || v[0] == 'M') ? 1 : v && (v[0] == 'l' || v[0] == 'L') ? 2 : 0;
		if (steps) {
			int at = fonts_zoom_step(a->reader_zoom) + steps;
			char value[16];
			const char *const keys[] = { "ReaderZoom" };
			const char *values[] = { value };
			a->reader_zoom = fonts_zoom_ladder[at < E26_ZOOM_STEPS ? at : E26_ZOOM_STEPS - 1];
			a->reader_zoom_auto = 0;
			snprintf(value, sizeof value, "%d", a->reader_zoom);
			if (set_put_save(keys, values, 1) && a->verbose) fprintf(stderr, "d32library: ReaderZoom not saved\n");
		}
	}
	ui = set_get_int("ReaderUiScale", 0);
	a->ui_scale = ui >= 100 && ui <= 300 ? ui : a->panel_dpi >= 140 ? 150 : 100;
	a->reader_spacing = choice_index("ReaderSpacing", fonts_spacing_names, 3, E26_SPACING_NORMAL);
	a->reader_column = choice_index("ReaderColumn", fonts_column_names, 3, E26_COLUMN_NORMAL);
	a->reader_paper = choice_index("ReaderPaper", fonts_paper_names, 2, E26_PAPER_WHITE);
	a->outline_exhibits = set_get_bool("OutlineExhibits", 0);
	if (a->verbose)
		fprintf(stderr, "d32library: Text size %d%%%s (panel %d dpi), reader UI %d%%, spacing %s, column %s, paper %s\n",
		        a->reader_zoom, a->reader_zoom_auto ? " (auto)" : "", a->panel_dpi, a->ui_scale,
		        fonts_spacing_names[a->reader_spacing], fonts_column_names[a->reader_column], fonts_paper_names[a->reader_paper]);
}

void fonts_load(e26_app *a)
{
	const font_set *fs = &font_sets[0];
	int i, ts;
	XtGetApplicationResources(a->top, &font_set_name, font_resources, XtNumber(font_resources), NULL, 0);
	for (i = 0; i < (int)XtNumber(font_sets); i++)
		if (font_set_name && !strcmp(font_set_name, font_sets[i].name)) fs = &font_sets[i];
	if (font_set_name && strcmp(font_set_name, fs->name))
		fprintf(stderr, "d32library: no font set %s (e95, win31, build1); using e95\n", font_set_name);
	if (a->verbose) fprintf(stderr, "d32library: font set %s\n", fs->name);
	/* the type size: -typesize, else the set's default (e95 and win31: Small, the period design's own default; build1:
	 * Medium, as build 1 drew). the period program seg100:0bbc reads TextStyle from LIBRARY.INI: "m..." is 1, "l..." is
	 * 2, anything else (and no key, as on the 1995 capture lane) leaves 0 and the articles at their compiled size.
	 * u14: it sizes the 640x480 letterbox only; the native reader takes the Text size (zoom_init). */
	switch (a->type_size ? a->type_size[0] : 0) {
	case 'S': case 's': ts = 0; break;
	case 'M': case 'm': ts = 1; break;
	case 'L': case 'l': ts = 2; break;
	default: ts = fs->default_size;
	}
	for (i = 0; i < F_NFONTS; i++)
		if (i != F_FIXED && fs->pat[i][0]) a->font[i] = load_font(a, fs->pat[i][0], fs->pat[i][1], fs->pat[i][2]);
	body_set = fs;
	zoom_init(a);   /* first: its one-time migration may rewrite the settings (set_get's strings with them) */
	a->font_native = 0;
	a->reader_font_choice = reader_face_by_id(set_get("ReaderFont"));
	fonts_type_size(a, ts);
}

/* ---- SHARP mode (W1-SHARP, item 63): each slot's face at k times its pixel size.
 * The slot's 1x font names itself (its FONT property, the XLFD the server matched); the same name with the pixel size
 * times k asks the server for the same face, rasterised again from its outline at k times the size (the private
 * TrueType faces are scalable on the font path of the build VM and of the node: /build/private/fonts/ttf and
 * /usr/local/share/fonts/d32private/ttf, fonts.scale). Only a face the server rasterises from an outline is taken:
 * one with a RASTERIZER_NAME property and the pixel size asked for. A raster face (MS Sans Serif, Small Fonts,
 * build 1's 100-dpi Helvetica, "fixed") would come back as the server's own enlargement of its bitmap; it is
 * refused, and gfx.c doubles its 1x glyphs nearest instead, as the 1995 bitmaps are. The 1x faces stay the logical
 * metrics at every k: widths, ascents and line pitches never change with k. */

static struct {
	Font fid1;          /* the 1x face this was made for */
	int k;
	XFontStruct *f;     /* NULL: no outline face at k (doubled) */
} kface[F_NFONTS];

static int outline_face(e26_app *a, XFontStruct *f, int px)
{
	Atom ras = XInternAtom(a->dpy, "RASTERIZER_NAME", True), pxa = XInternAtom(a->dpy, "PIXEL_SIZE", True);
	unsigned long v;
	if (ras == None || !XGetFontProperty(f, ras, &v)) return 0;
	if (pxa != None && XGetFontProperty(f, pxa, &v) && (int)v != px) return 0;
	return 1;
}

XFontStruct *fonts_at_k(e26_app *a, int slot, int k)
{
	XFontStruct *f1, *fk = NULL;
	unsigned long v;
	char *name = NULL, kname[400];
	int px = 0;
	if (slot < 0 || slot >= F_NFONTS || !(f1 = a->font[slot]) || k < 2) return NULL;
	if (kface[slot].fid1 == f1->fid && kface[slot].k == k) return kface[slot].f;
	if (kface[slot].f) XFreeFont(a->dpy, kface[slot].f);
	kface[slot].fid1 = f1->fid;
	kface[slot].k = k;
	kface[slot].f = NULL;
	if (XGetFontProperty(f1, XA_FONT, &v) && (name = XGetAtomName(a->dpy, (Atom)v)) &&
	    !scale_xlfd_k(name, k, kname, sizeof kname, &px)) {
		fk = XLoadQueryFont(a->dpy, kname);
		if (fk && !outline_face(a, fk, px * k)) {
			XFreeFont(a->dpy, fk);
			fk = NULL;
		}
	}
	if (a->verbose)
		fprintf(stderr, "d32library: sharp: font slot %d at x%d: %s%s\n", slot, k, fk ? kname : name ? name : "(no name)",
		        fk ? " (outline)" : " (no outline at k: its 1x glyphs doubled)");
	if (name) XFree(name);
	kface[slot].f = fk;
	return fk;
}

