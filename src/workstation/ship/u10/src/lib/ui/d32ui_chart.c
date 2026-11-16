#include "d32ui.h"

#include <Xm/DrawingA.h>

#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define CHART_MAX_BARS 64
#define CHART_MAX_LABEL 128
#define CHART_MARGIN 6
#define CHART_GAP 6

struct chart_row {
	XmString label, number;
	unsigned value;
};

struct chart {
	struct chart_row rows[CHART_MAX_BARS];
	size_t count;
	unsigned maximum, row_height, label_width, number_width;
	XmString empty, clipped;
	XmFontList font;
	GC gc;
	Pixmap stipple[3];
};

/* X bitmap data is low bit first. Solid fill needs no bitmap. */
static const unsigned char patterns[3][8] = {
	{0x55, 0xaa, 0x55, 0xaa, 0x55, 0xaa, 0x55, 0xaa},
	{0x11, 0x22, 0x44, 0x88, 0x11, 0x22, 0x44, 0x88},
	{0x81, 0x42, 0x24, 0x18, 0x18, 0x24, 0x42, 0x81}
};

static unsigned
minimum(unsigned a, unsigned b)
{
	return a < b ? a : b;
}

static int
label_valid(const char *label)
{
	size_t i;

	if (label == NULL)
		return 0;
	for (i = 0; i <= CHART_MAX_LABEL; i++) {
		unsigned char c = (unsigned char)label[i];

		if (c == '\0')
			return 1;
		if (c < 0x20 || c == 0x7f)
			return 0;
	}
	return 0;
}

static void
chart_free(struct chart *chart)
{
	size_t i;

	for (i = 0; i < chart->count; i++) {
		if (chart->rows[i].label != NULL)
			XmStringFree(chart->rows[i].label);
		if (chart->rows[i].number != NULL)
			XmStringFree(chart->rows[i].number);
	}
	if (chart->empty != NULL)
		XmStringFree(chart->empty);
	if (chart->clipped != NULL)
		XmStringFree(chart->clipped);
	if (chart->font != NULL)
		XmFontListFree(chart->font);
	free(chart);
}

static void
destroy_chart(Widget widget, XtPointer data, XtPointer call)
{
	struct chart *chart = data;
	Display *display = XtDisplay(widget);
	size_t i;

	(void)call;
	for (i = 0; i < XtNumber(chart->stipple); i++)
		if (chart->stipple[i] != None)
			XFreePixmap(display, chart->stipple[i]);
	if (chart->gc != NULL)
		XFreeGC(display, chart->gc);
	chart_free(chart);
}

static int
chart_gc(Widget widget, struct chart *chart)
{
	Display *display = XtDisplay(widget);
	Window window = XtWindow(widget);
	size_t i;

	if (chart->gc != NULL)
		return 1;
	chart->gc = XCreateGC(display, window, 0, NULL);
	if (chart->gc == NULL)
		return 0;
	for (i = 0; i < XtNumber(chart->stipple); i++)
		chart->stipple[i] = XCreateBitmapFromData(display, window,
		    (const char *)patterns[i], 8, 8);
	return 1;
}

static void
text_at(Widget widget, struct chart *chart, XmString text, unsigned x, unsigned y,
    unsigned width, unsigned height, unsigned char alignment)
{
	XRectangle clip;

	if (width == 0)
		return;
	clip.x = (short)x;
	clip.y = (short)y;
	clip.width = (unsigned short)width;
	clip.height = (unsigned short)height;
	XmStringDraw(XtDisplay(widget), XtWindow(widget), chart->font, text,
	    chart->gc, (Position)x, (Position)y, (Dimension)width, alignment,
	    XmSTRING_DIRECTION_L_TO_R, &clip);
	XSetClipMask(XtDisplay(widget), chart->gc, None);
}

static void
paint_bar(Widget widget, struct chart *chart, size_t row, unsigned x, unsigned y,
    unsigned width, unsigned height, const Pixel ink[4], const Pixel paper[4],
    Pixel foreground)
{
	Display *display = XtDisplay(widget);
	Window window = XtWindow(widget);
	GC gc = chart->gc;
	size_t style = row % 4;
	unsigned fill = chart->maximum != 0 ? (unsigned)((uint64_t)(width - 2) *
	    chart->rows[row].value / chart->maximum) : 0;

	if (fill != 0) {
		XSetForeground(display, gc, ink[style]);
		if (style != 0 && chart->stipple[style - 1] != None) {
			XSetStipple(display, gc, chart->stipple[style - 1]);
			XSetTSOrigin(display, gc, (int)x, (int)y);
			XSetBackground(display, gc, paper[style]);
			XSetFillStyle(display, gc, FillOpaqueStippled);
		}
		XFillRectangle(display, window, gc, (int)x + 1, (int)y + 1,
		    fill, height - 2);
		XSetFillStyle(display, gc, FillSolid);
	}
	XSetForeground(display, gc, foreground);
	XDrawRectangle(display, window, gc, (int)x, (int)y, width - 1, height - 1);
}

static void
draw_chart(Widget widget, XtPointer data, XtPointer call)
{
	struct chart *chart = data;
	Display *display;
	Window window;
	Dimension widget_width, widget_height;
	Colormap colormap;
	Pixel foreground, background, top, bottom, select;
	Pixel derived_fg, derived_top, derived_bottom, ink[4], paper[4];
	unsigned width, height, available, label, number, track, fit, y, bar_height;
	size_t i, visible;
	int clipped;

	(void)call;
	if (!XtIsRealized(widget) || !chart_gc(widget, chart))
		return;
	display = XtDisplay(widget);
	window = XtWindow(widget);
	XtVaGetValues(widget, XmNwidth, &widget_width, XmNheight, &widget_height,
	    XmNcolormap, &colormap, XmNforeground, &foreground,
	    XmNbackground, &background, XmNtopShadowColor, &top,
	    XmNbottomShadowColor, &bottom, NULL);
	width = minimum(widget_width, SHRT_MAX);
	height = minimum(widget_height, SHRT_MAX);
	XSetFillStyle(display, chart->gc, FillSolid);
	XSetForeground(display, chart->gc, background);
	XFillRectangle(display, window, chart->gc, 0, 0, width, height);
	if (width <= 2 * (CHART_MARGIN + CHART_GAP) + 3 ||
	    height < 2 * CHART_MARGIN + chart->row_height)
		return;
	available = width - 2 * CHART_MARGIN;
	fit = (height - 2 * CHART_MARGIN) / chart->row_height;
	XSetForeground(display, chart->gc, foreground);
	if (chart->count == 0) {
		text_at(widget, chart, chart->empty, CHART_MARGIN, CHART_MARGIN,
		    available, chart->row_height, XmALIGNMENT_BEGINNING);
		return;
	}

	/* Select is derived by Motif from the same inherited palette and colormap. */
	XmGetColors(XtScreen(widget), colormap, background, &derived_fg, &derived_top,
	    &derived_bottom, &select);
	paper[0] = paper[1] = paper[2] = paper[3] = background;
	ink[0] = select != background ? select : foreground;
	ink[1] = top != background ? top : foreground;
	ink[2] = bottom != background ? bottom : foreground;
	ink[3] = foreground != select ? foreground : bottom;
	paper[3] = select != ink[3] ? select : background;

	track = available - 2 * CHART_GAP;
	label = minimum(chart->label_width, track / 3);
	number = minimum(chart->number_width, track / 4);
	track -= label + number;
	if (track < 3)
		return;
	bar_height = chart->row_height - 6;
	clipped = chart->count > fit;
	visible = clipped ? fit - 1 : chart->count;
	for (i = 0, y = CHART_MARGIN; i < visible; i++, y += chart->row_height) {
		text_at(widget, chart, chart->rows[i].label, CHART_MARGIN, y,
		    label, chart->row_height, XmALIGNMENT_BEGINNING);
		paint_bar(widget, chart, i, CHART_MARGIN + label + CHART_GAP,
		    y + 3, track, bar_height, ink, paper, foreground);
		text_at(widget, chart, chart->rows[i].number,
		    width - CHART_MARGIN - number, y, number, chart->row_height,
		    XmALIGNMENT_END);
	}
	if (clipped)
		text_at(widget, chart, chart->clipped, CHART_MARGIN, y,
		    available, chart->row_height, XmALIGNMENT_BEGINNING);
}

static int
make_rows(struct chart *chart, const struct d32ui_bar *bars)
{
	Dimension width, height;
	char number[sizeof(unsigned) * CHAR_BIT + 1];
	unsigned text_height = 0;
	size_t i;

	XmStringExtent(chart->font, chart->clipped, &width, &height);
	text_height = height;
	for (i = 0; i < chart->count; i++) {
		struct chart_row *row = &chart->rows[i];

		row->value = bars[i].value;
		if (row->value > chart->maximum)
			chart->maximum = row->value;
		(void)snprintf(number, sizeof(number), "%u", row->value);
		row->label = XmStringCreateLocalized((char *)bars[i].label);
		row->number = XmStringCreateLocalized(number);
		if (row->label == NULL || row->number == NULL)
			return 0;
		XmStringExtent(chart->font, row->label, &width, &height);
		if (width > chart->label_width)
			chart->label_width = width;
		if (height > text_height)
			text_height = height;
		XmStringExtent(chart->font, row->number, &width, &height);
		if (width > chart->number_width)
			chart->number_width = width;
		if (height > text_height)
			text_height = height;
	}
	chart->row_height = text_height < 10 ? 16 : text_height + 6;
	return 1;
}

Widget
d32ui_chart(d32ui *ui, Widget parent, const struct d32ui_bar *bars, size_t count)
{
	struct chart *chart;
	XmFontList font;
	Widget widget;
	Arg args[3];
	Cardinal n = 0;
	size_t i;

	if (ui == NULL || parent == NULL || (bars == NULL && count != 0) ||
	    count > CHART_MAX_BARS)
		return NULL;
	for (i = 0; i < count; i++)
		if (!label_valid(bars[i].label))
			return NULL;
	font = d32ui_mono(ui);
	if (font == NULL || (chart = calloc(1, sizeof(*chart))) == NULL)
		return NULL;
	chart->count = count;
	chart->font = XmFontListCopy(font);
	chart->empty = XmStringCreateLocalized("No data");
	chart->clipped = XmStringCreateLocalized("Resize to show more bars");
	if (chart->font == NULL || chart->empty == NULL || chart->clipped == NULL ||
	    !make_rows(chart, bars)) {
		chart_free(chart);
		return NULL;
	}

	XtSetArg(args[n], XmNwidth, 480); n++;
	XtSetArg(args[n], XmNheight, minimum(SHRT_MAX,
	    (unsigned)(count != 0 ? count : 1) * chart->row_height +
	    2 * CHART_MARGIN)); n++;
	XtSetArg(args[n], XmNtraversalOn, False); n++;
	widget = XmCreateDrawingArea(parent, "chart", args, n);
	if (widget == NULL) {
		chart_free(chart);
		return NULL;
	}
	XtAddCallback(widget, XmNdestroyCallback, destroy_chart, chart);
	XtAddCallback(widget, XmNexposeCallback, draw_chart, chart);
	XtAddCallback(widget, XmNresizeCallback, draw_chart, chart);
	XtManageChild(widget);
	return widget;
}
