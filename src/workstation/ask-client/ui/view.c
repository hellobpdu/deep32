#include "view.h"

#include <X11/Xatom.h>
#include <X11/keysym.h>
#include <X11/cursorfont.h>
#include <X11/StringDefs.h>
#include <Xm/DrawingA.h>
#include <Xm/Form.h>
#include <Xm/ScrollBar.h>
#include <Xm/ScrolledW.h>

#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

/* The text size ladder: body, heading 1, heading 2, code and Unicode fallback pixels, all
 * native X11 bitmap sizes (75dpi and 100dpi Helvetica, Courier, Lucida Typewriter and the
 * misc fixed faces). Step 1 is the 14 px transcript and step 2 its wide-window sizes, both as
 * before. From 20 px the code face is Lucida Typewriter at 0.9 x body, as in Library: Courier
 * goes thin at those sizes. Step 3, 150%, is the default on a panel of 140 dpi or more. */
enum { TEXT_STEPS = 6, STEP_14 = 1, STEP_WIDE = 2, ROLES = 8 };
enum { ROLE_BODY, ROLE_BOLD, ROLE_ITALIC, ROLE_BOLD_ITALIC, ROLE_MONO, ROLE_H1, ROLE_H2, ROLE_UNICODE };

typedef struct TextStep { int body, h1, h2, mono, unicode, typewriter; } TextStep;
static const TextStep steps[TEXT_STEPS] = {
	{12, 14, 12, 12, 14, 0},
	{14, 17, 14, 14, 14, 0},
	{17, 20, 17, 17, 14, 0},
	{20, 25, 24, 18, 20, 1},
	{24, 34, 25, 20, 20, 1},
	{34, 34, 34, 26, 20, 1},
};

struct FontResources { String body, bold, italic, boldItalic, mono, heading1, heading2, unicode; };

struct AskView {
	Widget shell, left, scroller, canvas, vertical;
	AskDoc *doc;
	AskLayout layout;
	AskMetrics metrics;
	XFontStruct *face[ASK_FONT_COUNT], *unicode;  /* the step in use */
	XFontStruct *loaded[TEXT_STEPS][ROLES], *configured[ROLES], *fixed;
	struct FontResources r;
	GC gc;
	Cursor hand;
	XtIntervalId redraw;
	Pixel ink, paper, top, bottom, accent, code;
	int scroll, width, height, focused, xoff;
	int panel, choice, step; /* the panel's step, the Text Size choice, the step in use */
	uint32_t turn, ref;
	void *owner;
	void (*open)(void *, uint32_t, uint32_t);
};

/* About 76 characters of English per prose line, at every size (the Library's measure). */
enum { MEASURE_CHARACTERS = 76 };
static const char measure_sample[] = "Turn it on per interface, or for the whole area with the "
    "area command. The key ID and the key must match on both ends.";

#define FONT_RES(name, cap) \
	{ #name "Font", cap "Font", XtRString, sizeof(String), \
	  XtOffsetOf(struct FontResources, name), XtRString, NULL }
static XtResource resources[] = {
	FONT_RES(body, "Body"), FONT_RES(bold, "Bold"),
	FONT_RES(italic, "Italic"), FONT_RES(boldItalic, "BoldItalic"), FONT_RES(mono, "Mono"),
	FONT_RES(heading1, "Heading1"), FONT_RES(heading2, "Heading2"), FONT_RES(unicode, "Unicode")
};
#undef FONT_RES

/* The stock D32Askw faces. A resource naming one of these follows the Text Size ladder;
 * any other per-user font stays as configured at every size. */
#define HELV(weight, slant, size) "-adobe-helvetica-" weight "-" slant "-normal--" size "-*-100-100-p-*-iso8859-1"
static const char *const stock[ROLES] = {
	HELV("medium", "r", "14"), HELV("bold", "r", "14"), HELV("medium", "o", "14"), HELV("bold", "o", "14"),
	"-adobe-courier-medium-r-normal--14-*-100-100-m-*-iso8859-1",
	HELV("bold", "r", "17"), HELV("bold", "r", "14"),
	"-misc-fixed-medium-r-normal--14-*-*-*-*-*-iso10646-1"
};
#undef HELV

static XFontStruct *
load_font(Display *display, const char *patterns)
{
	const char *end;
	char name[512];
	size_t i;
	XFontStruct *font;

	while (patterns != NULL && *patterns) {
		end = patterns;
		while (*end && *end != ',') end++;
		for (i = 0; i < (size_t)(end - patterns) && i < sizeof name - 1; i++)
			name[i] = patterns[i];
		name[i] = '\0';
		if ((font = XLoadQueryFont(display, name)) != NULL) return font;
		patterns = *end ? end + 1 : end;
	}
	return NULL;
}

/* The XLFD field after the given number of dashes, or "". */
static const char *
xlfd_field(const char *name, int dashes)
{
	while (*name && dashes > 0)
		if (*name++ == '-') dashes--;
	return name;
}

/* A face at exactly px pixels from the bitmap directories: a listed name with a real average
 * width (the server lists scalable templates with 0) that loads without RAW_PIXEL_SIZE (the
 * mark of a server-scaled copy). The 100dpi name is preferred, as the 14 px faces always were. */
static XFontStruct *
bitmap_face(Display *display, const char *prefix, int px, const char *spacing, const char *registry)
{
	Atom raw = XInternAtom(display, "RAW_PIXEL_SIZE", False);
	XFontStruct *font = NULL;
	unsigned long value;
	char pattern[160], **names;
	int count = 0;

	snprintf(pattern, sizeof pattern, "-%s-%d-*-*-*-%s-*-%s", prefix, px, spacing, registry);
	if ((names = XListFonts(display, pattern, 32, &count)) == NULL) return NULL;
	for (int pass = 0; pass < 2 && font == NULL; pass++)
		for (int i = 0; i < count && font == NULL; i++) {
			if (!strncmp(xlfd_field(names[i], 12), "0-", 2) ||
			    (pass == 0 && strncmp(xlfd_field(names[i], 9), "100-100-", 8)))
				continue;
			if ((font = XLoadQueryFont(display, names[i])) != NULL && XGetFontProperty(font, raw, &value)) {
				XFreeFont(display, font);
				font = NULL;
			}
		}
	XFreeFontNames(names);
	return font;
}

static XFontStruct *
ladder_face(Display *display, const TextStep *step, int role)
{
	switch (role) {
	case ROLE_BODY: return bitmap_face(display, "adobe-helvetica-medium-r-normal-", step->body, "p", "iso8859-1");
	case ROLE_BOLD: return bitmap_face(display, "adobe-helvetica-bold-r-normal-", step->body, "p", "iso8859-1");
	case ROLE_ITALIC: return bitmap_face(display, "adobe-helvetica-medium-o-normal-", step->body, "p", "iso8859-1");
	case ROLE_BOLD_ITALIC: return bitmap_face(display, "adobe-helvetica-bold-o-normal-", step->body, "p", "iso8859-1");
	case ROLE_MONO: return bitmap_face(display, step->typewriter ? "b&h-lucidatypewriter-medium-r-normal-sans" :
	    "adobe-courier-medium-r-normal-", step->mono, "m", "iso8859-1");
	case ROLE_H1: return bitmap_face(display, "adobe-helvetica-bold-r-normal-", step->h1, "p", "iso8859-1");
	case ROLE_H2: return bitmap_face(display, "adobe-helvetica-bold-r-normal-", step->h2, "p", "iso8859-1");
	default: return bitmap_face(display, "misc-fixed-medium-r-normal-", step->unicode, "c", "iso10646-1");
	}
}

/* One face per role for a step, loaded on first use and kept. A user's own font serves every
 * step. A ladder face that will not load falls back to the 14 px one, then to "fixed". */
static XFontStruct *
role_face(AskView *view, int step, int role)
{
	Display *display = XtDisplay(view->shell);
	static const int fallback[ROLES] = {ROLE_BODY, ROLE_BOLD, ROLE_BODY, ROLE_BOLD, ROLE_MONO, ROLE_BOLD,
	    ROLE_BOLD, ROLE_UNICODE};

	if (view->configured[role] != NULL) return view->configured[role];
	if (view->loaded[step][role] == NULL)
		view->loaded[step][role] = ladder_face(display, &steps[step], role);
	if (view->loaded[step][role] != NULL) return view->loaded[step][role];
	if (step != STEP_14) return role_face(view, STEP_14, role);
	if (fallback[role] != role) return role_face(view, step, fallback[role]);
	return view->fixed;
}

static void
font_metrics(AskView *view)
{
	for (unsigned i = 0; i < ASK_FONT_COUNT; i++) {
		XFontStruct *font = view->face[i];
		view->metrics.ascent[i] = font->ascent;
		view->metrics.descent[i] = font->descent;
		if (view->metrics.ascent[i] < view->unicode->ascent) view->metrics.ascent[i] = view->unicode->ascent;
		if (view->metrics.descent[i] < view->unicode->descent) view->metrics.descent[i] = view->unicode->descent;
	}
}

static void
use_step(AskView *view, int step)
{
	XFontStruct *body = role_face(view, step, ROLE_BODY), *bold = role_face(view, step, ROLE_BOLD);
	XFontStruct *italic = role_face(view, step, ROLE_ITALIC), *bold_italic = role_face(view, step, ROLE_BOLD_ITALIC);
	XFontStruct *mono = role_face(view, step, ROLE_MONO);

	for (unsigned i = 0; i < ASK_STYLE_COUNT; i++)
		view->face[i] = i & ASK_MONO ? mono :
		    (i & (ASK_BOLD | ASK_ITALIC)) == (ASK_BOLD | ASK_ITALIC) ? bold_italic :
		    i & ASK_BOLD ? bold : i & ASK_ITALIC ? italic : body;
	view->face[ASK_H1] = role_face(view, step, ROLE_H1);
	view->face[ASK_H2] = role_face(view, step, ROLE_H2);
	view->unicode = role_face(view, step, ROLE_UNICODE);
	view->step = step;
	font_metrics(view);
}

static int
load_fonts(AskView *view)
{
	Display *display = XtDisplay(view->shell);
	String configured[ROLES];

	XtGetApplicationResources(view->shell, &view->r, resources,
	    XtNumber(resources), NULL, 0);
	configured[ROLE_BODY] = view->r.body;
	configured[ROLE_BOLD] = view->r.bold;
	configured[ROLE_ITALIC] = view->r.italic;
	configured[ROLE_BOLD_ITALIC] = view->r.boldItalic;
	configured[ROLE_MONO] = view->r.mono;
	configured[ROLE_H1] = view->r.heading1;
	configured[ROLE_H2] = view->r.heading2;
	configured[ROLE_UNICODE] = view->r.unicode;
	view->fixed = XLoadQueryFont(display, "fixed");
	if (view->fixed == NULL) return 0;
	for (int role = 0; role < ROLES; role++)
		if (configured[role] != NULL && strcasecmp(configured[role], stock[role]))
			view->configured[role] = load_font(display, configured[role]);
	if (role_face(view, STEP_14, ROLE_BODY) == view->fixed || role_face(view, STEP_14, ROLE_BOLD) == view->fixed ||
	    role_face(view, STEP_14, ROLE_MONO) == view->fixed)
		ask_doc_notice(view->doc, "info", "Some transcript fonts are unavailable. Using the fixed font.");
	use_step(view, STEP_14);
	return 1;
}

static unsigned
codepoint(const char **text, size_t *length)
{
	const unsigned char *s = (const unsigned char *)*text;
	unsigned value = *s, count = 1, i;

	if (value >= 0xc2 && value <= 0xf4) {
		count = value < 0xe0 ? 2 : value < 0xf0 ? 3 : 4;
		value &= 0x7f >> count;
		if (count <= *length) {
			for (i = 1; i < count && (s[i] & 0xc0) == 0x80; i++)
				value = value << 6 | (s[i] & 0x3f);
		} else i = 0;
		if (i != count || (count == 3 && value < 0x800) ||
		    (value >= 0xd800 && value <= 0xdfff)) {
			value = 0xfffd;
			count = 1;
		}
	} else if (value >= 0x80) value = 0xfffd;
	*text += count;
	*length -= count;
	return value > 0xffff ? 0xfffd : value;
}

static int
has_char(const XFontStruct *font, unsigned value)
{
	unsigned hi = value >> 8, lo = value & 255, columns;
	const XCharStruct *metrics;

	if (hi < font->min_byte1 || hi > font->max_byte1 ||
	    lo < font->min_char_or_byte2 || lo > font->max_char_or_byte2) return 0;
	if (font->per_char == NULL) return 1;
	columns = font->max_char_or_byte2 - font->min_char_or_byte2 + 1;
	metrics = &font->per_char[(hi - font->min_byte1) * columns +
	    lo - font->min_char_or_byte2];
	return metrics->width || metrics->lbearing || metrics->rbearing ||
	    metrics->ascent || metrics->descent;
}

static int
draw_run(AskView *view, unsigned style, const char *text, size_t length,
    int x, int y, int paint)
{
	Display *display = XtDisplay(view->canvas);
	XFontStruct *font = view->face[style], *last = font, *next;
	XChar2b glyphs[128];
	unsigned code;
	int width = 0, n = 0, span, start;

	while (length != 0 || n != 0) {
		if (length != 0) {
			code = codepoint(&text, &length);
			next = has_char(font, code) ? font : view->unicode;
			if (!has_char(next, code)) code = '?';
		} else {
			code = 0;
			next = last;
		}
		if (n && (next != last || n == (int)XtNumber(glyphs) || !code)) {
			span = XTextWidth16(last, glyphs, n);
			start = x + width;
			if (paint && start >= SHRT_MIN && start <= SHRT_MAX &&
			    start < view->width && start + span > 0) {
				XSetFont(display, view->gc, last->fid);
				XDrawString16(display, XtWindow(view->canvas), view->gc,
				    start, y, glyphs, n);
			}
			width += span;
			n = 0;
		}
		if (code != 0) {
			glyphs[n].byte1 = (unsigned char)(code >> 8);
			glyphs[n++].byte2 = (unsigned char)code;
			last = next;
		}
	}
	return width;
}

static int
text_width(void *context, unsigned style, const char *text, size_t length)
{
	return draw_run(context, style, text, length, 0, 0, 0);
}

static int
maximum_scroll(const AskView *view)
{
	int height = view->layout.height - view->height;
	return height > 0 ? height : 0;
}

static int
page_step(const AskView *view)
{
	int step = view->height - view->layout.line_pitch;
	return step > 0 ? step : 1;
}

/* A code block is an inline recessed well: the code shade with a dark edge above and on
 * the left and a light edge below and on the right, as a CDE text area sits in its panel. */
static void
code_well(AskView *view, Display *display, Window window, const AskCode *code)
{
	int x = code->x + view->xoff, y = code->y - view->scroll;
	int right = x + code->width - 1, bottom = y + code->height - 1;

	if (code->width < 3 || code->height < 3 || bottom < 0 || y >= view->height) return;
	XSetForeground(display, view->gc, view->code);
	XFillRectangle(display, window, view->gc, x, y, (unsigned)code->width, (unsigned)code->height);
	XSetForeground(display, view->gc, view->bottom);
	XDrawLine(display, window, view->gc, x, y, right, y);
	XDrawLine(display, window, view->gc, x, y, x, bottom);
	XSetForeground(display, view->gc, view->top);
	XDrawLine(display, window, view->gc, x + 1, bottom, right, bottom);
	XDrawLine(display, window, view->gc, right, y + 1, right, bottom);
}

static void
paint(AskView *view)
{
	Display *display = XtDisplay(view->canvas);
	Window window = XtWindow(view->canvas);
	const AskBox *box;
	size_t i;
	int x, y;

	if (window == None) return;
	if (view->gc == NULL) view->gc = XCreateGC(display, window, 0, NULL);
	XClearWindow(display, window);
	for (i = 0; i < view->layout.code_count; i++)
		code_well(view, display, window, &view->layout.code[i]);
	for (i = 0; i < view->layout.count; i++) {
		box = &view->layout.boxes[i];
		if (box->y + box->height < view->scroll ||
		    box->y >= view->scroll + view->height) continue;
		y = box->y - view->scroll;
		x = box->x + view->xoff;
		if (box->kind == ASK_CODE_RULE) {
			continue; /* the well's own edges */
		} else if (box->kind == ASK_RULE) {
			/* Etched separator: a shadow line over a highlight line. */
			XSetForeground(display, view->gc, view->bottom);
			XDrawLine(display, window, view->gc, x, y, x + box->width - 1, y);
			XSetForeground(display, view->gc, view->top);
			XDrawLine(display, window, view->gc, x, y + 1, x + box->width - 1, y + 1);
		} else if (box->kind != ASK_TEXT) {
			XSetForeground(display, view->gc, view->bottom);
			XFillRectangle(display, window, view->gc, x, y,
			    (unsigned)box->width, (unsigned)box->height);
		} else {
			XSetForeground(display, view->gc,
			    box->flags & (ASK_ACCENT | ASK_UNDERLINE) ? view->accent : view->ink);
			draw_run(view, box->style, box->text, box->length, x,
			    box->baseline - view->scroll, 1);
			if (box->flags & ASK_UNDERLINE)
				XDrawLine(display, window, view->gc, x, box->baseline - view->scroll + 1,
				    x + box->width - 1, box->baseline - view->scroll + 1);
			if (view->focused && box->ref && box->ref == view->ref && box->turn == view->turn)
				XDrawRectangle(display, window, view->gc, x - 1, y,
				    (unsigned)box->width + 1, (unsigned)box->height);
		}
	}
	if (view->focused && view->width > 4 && view->height > 4) {
		XSetForeground(display, view->gc, view->bottom);
		XSetLineAttributes(display, view->gc, 1, LineOnOffDash, CapButt, JoinMiter);
		XDrawRectangle(display, window, view->gc, 1, 1, (unsigned)view->width - 3, (unsigned)view->height - 3);
		XSetLineAttributes(display, view->gc, 0, LineSolid, CapButt, JoinMiter);
	}
}

static void
set_scroll(AskView *view, int scroll)
{
	int maximum = maximum_scroll(view);
	view->scroll = scroll < 0 ? 0 : scroll > maximum ? maximum : scroll;
	XtVaSetValues(view->vertical, XmNmaximum, maximum + view->height,
	    XmNsliderSize, view->height, XmNvalue, view->scroll,
	    XmNincrement, view->layout.line_pitch, XmNpageIncrement, page_step(view), NULL);
	paint(view);
}

static void
scroll_cb(Widget widget, XtPointer data, XtPointer call)
{
	AskView *view = data;
	XmScrollBarCallbackStruct *event = call;
	(void)widget;
	set_scroll(view, event->value);
}

static const AskBox *
first_visible(const AskView *view)
{
	size_t i;
	for (i = 0; i < view->layout.count; i++)
		if (view->layout.boxes[i].kind == ASK_TEXT &&
		    view->layout.boxes[i].y + view->layout.boxes[i].height > view->scroll)
			return &view->layout.boxes[i];
	return NULL;
}

static int
item_bounds(const AskLayout *layout, uint64_t item, int *top, int *end)
{
	size_t i;
	const AskBox *box;
	int found = 0;

	for (i = 0; i < layout->count; i++) {
		box = &layout->boxes[i];
		if (box->item != item) continue;
		if (!found || box->y < *top) *top = box->y;
		if (!found || box->y + box->height > *end) *end = box->y + box->height;
		found = 1;
	}
	return found;
}

static void
dump(const AskView *view)
{
	const char *name = getenv("D32ASKW_DUMP");
	const AskBox *box = first_visible(view);
	Dimension w, h, left, right;
	Position x, y, shell_x, shell_y;
	FILE *file;
	size_t i;
	unsigned faces[] = {0, ASK_BOLD, ASK_ITALIC, ASK_MONO};

	if (name == NULL || *name == '\0' || !XtIsRealized(view->canvas)) return;
	if ((file = fopen(name, "a")) == NULL) return;
	XtVaGetValues(view->shell, XmNwidth, &w, XmNheight, &h, NULL);
	XtVaGetValues(view->left, XmNwidth, &left, NULL);
	XtVaGetValues(view->scroller, XmNwidth, &right, NULL);
	XtTranslateCoords(view->canvas, 0, 0, &x, &y);
	XtTranslateCoords(view->shell, 0, 0, &shell_x, &shell_y);
	fprintf(file, "SNAP client=%ux%u panes=%u,%u wrap=%d pitch=",
	    (unsigned)w, (unsigned)h, (unsigned)left, (unsigned)right, view->layout.wrap_width);
	for (i = 0; i < XtNumber(faces); i++)
		fprintf(file, "%s%d", i ? "," : "",
		    view->metrics.ascent[faces[i]] + view->metrics.descent[faces[i]]);
	fprintf(file, " total=%d scroll=%d first=%llu busy=%d turn=%u", view->layout.height,
	    view->scroll, (unsigned long long)(box == NULL ? 0 : box->item),
	    ask_doc_busy(view->doc), ask_doc_last_turn(view->doc));
	fprintf(file, " text=%d step=%d body=%d measure=%d column_x=%d line=%d\n", view->choice, view->step,
	    steps[view->step].body, view->metrics.measure, view->layout.column_x, view->layout.line_pitch);
	for (i = 0; i < XtNumber(faces) + 2; i++) {
		XFontStruct *font = i < XtNumber(faces) ? view->face[faces[i]] : i == XtNumber(faces) ?
		    view->face[ASK_H1] : view->face[ASK_H2];
		unsigned long value;
		char *face = XGetFontProperty(font, XA_FONT, &value) ? XGetAtomName(XtDisplay(view->canvas), value) : NULL;
		fprintf(file, "FACE %s\n", face ? face : "?");
		if (face) XFree(face);
	}
	for (i = 0; i < view->layout.count; i++) {
		box = &view->layout.boxes[i];
		if (!box->ref && !(box->flags & ASK_MARKING)) continue;
		fprintf(file, "REF turn=%u n=%u marking=%d x=%d y=%d w=%d h=%d\n",
		    box->turn, box->ref, (box->flags & ASK_MARKING) != 0,
		    x - shell_x + box->x + view->xoff, y - shell_y + box->y - view->scroll,
		    box->width, box->height);
	}
	fputs("END\n", file);
	fclose(file);
}

/* The step a Text Size choice gives in a shell this wide. Small, Normal, Large and Larger are
 * one below, at, one above and two above the panel's own step. Auto on a 100-dpi panel stays
 * as it was: 14 px, and 17 px with the wide spacing in a window 1440 pixels or wider. */
static int
choice_step(const AskView *view, int choice, int shell_width)
{
	static const int offset[ASK_TEXT_CHOICES] = {0, -1, 0, 1, 2};
	int step;

	if (choice < 0 || choice >= ASK_TEXT_CHOICES) choice = ASK_TEXT_AUTO;
	if (choice == ASK_TEXT_AUTO && view->panel == STEP_14)
		return shell_width >= 1440 ? STEP_WIDE : STEP_14;
	step = view->panel + offset[choice];
	return step < 0 ? 0 : step >= TEXT_STEPS ? TEXT_STEPS - 1 : step;
}

static void
redraw(XtPointer data, XtIntervalId *id)
{
	AskView *view = data;
	const AskBox *old = first_visible(view);
	uint64_t anchor = old == NULL ? 0 : old->item;
	int at_bottom = view->scroll == maximum_scroll(view);
	int target = view->scroll;
	int top = 0, end = 0, offset = 0, step;
	Dimension width, height, shell_width;

	(void)id;
	view->redraw = 0;
	if (anchor && item_bounds(&view->layout, anchor, &top, &end))
		offset = view->scroll - top;
	XtVaGetValues(view->canvas, XmNwidth, &width, XmNheight, &height, NULL);
	XtVaGetValues(view->shell, XmNwidth, &shell_width, NULL);
	view->width = width > 0 ? width : 1;
	view->height = height > 0 ? height : 1;
	step = choice_step(view, view->choice, shell_width);
	if (step != view->step) use_step(view, step);
	if (view->choice == ASK_TEXT_AUTO && view->panel == STEP_14) {
		/* The 14 px transcript: its own spacing tiers and the faces' own line pitch. */
		view->metrics.size = shell_width < 760 ? ASK_VIEW_COMPACT :
		    shell_width >= 1440 ? ASK_VIEW_WIDE : ASK_VIEW_DEFAULT;
		view->metrics.scale = view->metrics.pitch = 0;
	} else {
		/* Any other size keeps the 14 px rhythm in proportion: spacing and an 18/14 pitch. */
		int body = steps[step].body;
		view->metrics.scale = body * 100 / 14;
		view->metrics.size = shell_width * 100 < 760 * view->metrics.scale ? ASK_VIEW_COMPACT : ASK_VIEW_DEFAULT;
		view->metrics.pitch = (body * 18 + 7) / 14;
	}
	view->metrics.measure = MEASURE_CHARACTERS * text_width(view, 0, measure_sample, sizeof measure_sample - 1) /
	    (int)(sizeof measure_sample - 1);
	/* The column is centred in a wider pane, like a page on the desk. */
	if (!ask_layout_build(&view->layout, view->doc, view->width, &view->metrics)) return;
	view->xoff = view->layout.column_x;
	if (at_bottom) target = maximum_scroll(view);
	else if (anchor && item_bounds(&view->layout, anchor, &top, &end))
		target = top + (offset < end - top ? offset : end - top - 1);
	set_scroll(view, target);
	dump(view);
}

void
view_refresh(AskView *view)
{
	if (!view->redraw)
		view->redraw = XtAppAddTimeOut(XtWidgetToApplicationContext(view->canvas),
		    30, redraw, view);
}

void
view_page(AskView *view, int direction)
{
	set_scroll(view, view->scroll + direction * page_step(view));
	dump(view);
}

static void
focus_ref(AskView *view, int direction)
{
	const AskBox *box, *chosen = NULL;
	size_t i, start = view->layout.count;

	for (i = 0; i < view->layout.count; i++) {
		box = &view->layout.boxes[i];
		if (box->ref && box->turn == view->turn && box->ref == view->ref) {
			start = i;
			break;
		}
	}
	for (i = 0; i < view->layout.count; i++) {
		size_t index = direction > 0 ? start + 1 + i :
		    start + view->layout.count - 1 - i;
		if (start == view->layout.count)
			index = direction > 0 ? i : view->layout.count - 1 - i;
		box = &view->layout.boxes[index % view->layout.count];
		if (box->ref && (box->turn != view->turn || box->ref != view->ref)) {
			chosen = box;
			break;
		}
	}
	if (chosen == NULL) return;
	view->turn = chosen->turn;
	view->ref = chosen->ref;
	if (chosen->y < view->scroll) set_scroll(view, chosen->y);
	else if (chosen->y + chosen->height > view->scroll + view->height)
		set_scroll(view, chosen->y + chosen->height - view->height);
	else paint(view);
}

static void
canvas_event(Widget widget, XtPointer data, XEvent *event, Boolean *dispatch)
{
	AskView *view = data;
	const AskBox *hit;
	KeySym key;
	int i;

	if (event->type == Expose) {
		if (event->xexpose.count == 0) paint(view);
	} else if (event->type == FocusIn || event->type == FocusOut) {
		/* The pointer resting over the pane is not keyboard focus. */
		if (event->xfocus.detail == NotifyPointer) return;
		view->focused = event->type == FocusIn;
		paint(view);
	} else if (event->type == MotionNotify) {
		hit = ask_layout_hit(&view->layout, event->xmotion.x - view->xoff,
		    event->xmotion.y + view->scroll);
		if (hit != NULL) XDefineCursor(XtDisplay(widget), XtWindow(widget), view->hand);
		else XUndefineCursor(XtDisplay(widget), XtWindow(widget));
	} else if (event->type == ButtonPress) {
		if (event->xbutton.button == Button4 || event->xbutton.button == Button5) {
			i = event->xbutton.button == Button4 ? -3 : 3;
			set_scroll(view, view->scroll + i * view->layout.line_pitch);
			dump(view);
		} else if (event->xbutton.button == Button1) {
			XmProcessTraversal(widget, XmTRAVERSE_CURRENT);
			i = event->xbutton.y + view->scroll;
			hit = ask_layout_hit(&view->layout, event->xbutton.x - view->xoff, i);
			if (hit != NULL) {
				view->turn = hit->turn;
				view->ref = hit->ref;
				view->open(view->owner, hit->turn, hit->ref);
				paint(view);
			} else {
				for (size_t j = 0; j < view->layout.count; j++) {
					hit = &view->layout.boxes[j];
					if (hit->turn && i >= hit->y && i < hit->y + hit->height) {
						view->turn = hit->turn;
						view->ref = 0;
						paint(view);
						break;
					}
				}
			}
		}
	} else if (event->type == KeyPress) {
		key = XLookupKeysym(&event->xkey, 0);
		switch (key) {
		case XK_Up: set_scroll(view, view->scroll - view->layout.line_pitch); break;
		case XK_Down: set_scroll(view, view->scroll + view->layout.line_pitch); break;
		case XK_Page_Up: view_page(view, -1); break;
		case XK_Page_Down: view_page(view, 1); break;
		case XK_Home: set_scroll(view, 0); break;
		case XK_End: set_scroll(view, maximum_scroll(view)); break;
		case XK_Tab:
			if (view->layout.count == 0) return;
			focus_ref(view, event->xkey.state & ShiftMask ? -1 : 1);
			if (!view->ref) return;
			break;
		case XK_Return: case XK_space:
			if (view->ref) view->open(view->owner, view->turn, view->ref);
			break;
		default: return;
		}
		*dispatch = False;
	}
}

static void
resize_cb(Widget widget, XtPointer data, XtPointer call)
{
	(void)widget;
	(void)call;
	view_refresh(data);
}

static int
brightness(const XColor *color)
{
	return 299 * color->red + 587 * color->green + 114 * color->blue;
}

static XColor
mix(XColor color, XColor toward, unsigned divisor)
{
	color.red = (unsigned short)((color.red * (divisor - 1) + toward.red) / divisor);
	color.green = (unsigned short)((color.green * (divisor - 1) + toward.green) / divisor);
	color.blue = (unsigned short)((color.blue * (divisor - 1) + toward.blue) / divisor);
	color.flags = DoRed | DoGreen | DoBlue;
	return color;
}

AskView *
view_create(Widget pane, Widget shell, Widget left, AskDoc *doc, void *owner,
    void (*open)(void *, uint32_t, uint32_t), int panel, int choice)
{
	AskView *view = calloc(1, sizeof *view);
	Colormap map;
	Display *display;
	Pixel unused, select;
	XColor shade[3], paper, code, accent, neutral = {0};

	if (view == NULL) return NULL;
	view->shell = shell;
	view->left = left;
	view->doc = doc;
	view->owner = owner;
	view->open = open;
	view->height = 1;
	view->panel = panel == ASK_PANEL_125 ? STEP_WIDE : panel == ASK_PANEL_150 ? STEP_WIDE + 1 : STEP_14;
	view->choice = choice >= 0 && choice < ASK_TEXT_CHOICES ? choice : ASK_TEXT_AUTO;
	view->metrics.context = view;
	view->metrics.width = text_width;
	if (!load_fonts(view)) { free(view); return NULL; }
	view->scroller = XtVaCreateWidget("transcript", xmScrolledWindowWidgetClass,
	    pane, XmNscrollingPolicy, XmAPPLICATION_DEFINED, XmNscrollBarPlacement, XmBOTTOM_RIGHT,
	    XmNwidth, 620, XmNshadowThickness, 2, NULL);
	if (XmIsForm(pane))
		XtVaSetValues(view->scroller, XmNtopAttachment, XmATTACH_FORM,
		    XmNbottomAttachment, XmATTACH_FORM, XmNleftAttachment, XmATTACH_FORM,
		    XmNrightAttachment, XmATTACH_FORM, NULL);
	else
		XtVaSetValues(view->scroller, XmNpaneMinimum, 360, XmNpaneMaximum, SHRT_MAX, NULL);
	XtManageChild(view->scroller);
	view->vertical = XtVaCreateManagedWidget("transcriptScroll", xmScrollBarWidgetClass,
	    view->scroller, XmNorientation, XmVERTICAL, NULL);
	view->canvas = XtVaCreateManagedWidget("answer", xmDrawingAreaWidgetClass,
	    view->scroller, XmNtraversalOn, True, XmNwidth, 600, XmNheight, 550, NULL);
	XmScrolledWindowSetAreas(view->scroller, NULL, view->vertical, view->canvas);
	display = XtDisplay(view->canvas);
	XtVaGetValues(view->canvas, XmNbackground, &view->paper, XmNforeground, &view->ink,
	    XmNtopShadowColor, &view->top, XmNbottomShadowColor, &view->bottom,
	    XmNcolormap, &map, NULL);
	XmGetColors(XtScreen(view->canvas), map, view->paper, &unused, &unused, &unused, &select);
	shade[0].pixel = view->paper;
	shade[1].pixel = view->bottom;
	shade[2].pixel = view->ink;
	XQueryColors(display, map, shade, XtNumber(shade));
	/* Every colour derives from the CDE palette background. The paper is a quarter of the way
	 * toward white (black on a dark palette) and gets its own shadows for rules and wells. */
	if (brightness(&shade[0]) > brightness(&shade[2])) neutral.red = neutral.green = neutral.blue = 65535;
	paper = mix(shade[0], neutral, 4);
	if (XAllocColor(display, map, &paper)) {
		view->paper = paper.pixel;
		XtVaSetValues(view->canvas, XmNbackground, view->paper, NULL);
		XmGetColors(XtScreen(view->canvas), map, view->paper, &unused, &view->top, &view->bottom, &unused);
	} else paper = shade[0];
	code = mix(paper, shade[2], 8);
	view->code = XAllocColor(display, map, &code) ? code.pixel : select;
	accent = mix(shade[2], shade[1], 4);
	view->accent = abs(brightness(&paper) - brightness(&accent)) >= 26214000 &&
	    XAllocColor(display, map, &accent) ? accent.pixel : view->ink;
	view->hand = XCreateFontCursor(XtDisplay(view->canvas), XC_hand2);
	XtAddCallback(view->canvas, XmNresizeCallback, resize_cb, view);
	XtAddEventHandler(view->canvas, ExposureMask | FocusChangeMask | ButtonPressMask |
	    PointerMotionMask | KeyPressMask, False, canvas_event, view);
	XtAddCallback(view->vertical, XmNvalueChangedCallback, scroll_cb, view);
	XtAddCallback(view->vertical, XmNdragCallback, scroll_cb, view);
	return view;
}

Widget
view_widget(const AskView *view)
{
	return view->scroller;
}

uint32_t
view_selected_turn(const AskView *view)
{
	return view->turn;
}

void
view_text_size(AskView *view, int choice)
{
	if (choice < 0 || choice >= ASK_TEXT_CHOICES || choice == view->choice) return;
	view->choice = choice;
	view_refresh(view);
}

int
view_text_px(const AskView *view, int choice)
{
	Dimension shell_width;
	XtVaGetValues(view->shell, XmNwidth, &shell_width, NULL);
	return steps[choice_step(view, choice, shell_width)].body;
}
