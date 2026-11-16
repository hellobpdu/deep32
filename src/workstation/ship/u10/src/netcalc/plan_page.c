#include "calc.h"
#include "plan_io.h"

#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef D32_CALC_FRAME_PROOF
#include <time.h>
#include <X11/Xatom.h>
#endif

#include <X11/keysym.h>
#include <Xm/Container.h>
#include <Xm/DrawingA.h>
#include <Xm/FileSB.h>
#include <Xm/Form.h>
#include <Xm/IconG.h>
#include <Xm/Label.h>
#include <Xm/List.h>
#include <Xm/PushB.h>
#include <Xm/RowColumn.h>
#include <Xm/ScrolledW.h>
#include <Xm/TextF.h>

enum edit { ADD, UPDATE, REMOVE, UP, DOWN };
enum paint { FREE, ALLOCATION, PIN, RESERVED, OTHER };

#define PLAN_LEAF_TEXT_CAP (2 * D32_ADDR_TEXT_CAP + CALC_PLAN_NAME_MAX + 8)
#define PLAN_OUTLINE_ROWS 32

struct plan_page {
	struct calc *calc;
	struct calc_plan_model *model, *draft;
	Widget page, name, value, kind, kinds[4], policy, policies[4];
	Widget buttons[5], requests, outline_scroll, outline, tree_root, allocations, allocation_scroll;
	Widget bar, donut, zoom, unzoom, scope_label, earlier, later, allocation_head, free_value, largest;
	Widget leaves[PLAN_OUTLINE_ROWS], picked[1];
	char leaf_labels[PLAN_OUTLINE_ROWS][PLAN_LEAF_TEXT_CAP];
	size_t leaves_count, outline_first, outline_count;
	size_t selected, visible_count, visible[D32_PLAN_MAX_REQUESTS];
	d32_prefix scope;
	bool zoomed, syncing;
	GC gc;
	Pixmap stipple[3];
};

struct pen {
	Display *display;
	Window window;
	GC gc;
	Pixel fg, bg, top, bottom, select;
	unsigned width, height;
};

static const char *const edit_names[] = { "Add", "Update", "Remove", "Up", "Down" };
static const unsigned char patterns[][8] = {
	{0x55, 0xaa, 0x55, 0xaa, 0x55, 0xaa, 0x55, 0xaa},
	{0x11, 0x22, 0x44, 0x88, 0x11, 0x22, 0x44, 0x88},
	{0x81, 0x42, 0x24, 0x18, 0x18, 0x24, 0x42, 0x81}
};

static void render(struct plan_page *);
static void render_outline(struct plan_page *);
static void select_row(struct plan_page *, size_t);

static void
set_label(Widget w, const char *text)
{
	XmString label = XmStringCreateLocalized((char *)text);
	XtVaSetValues(w, XmNlabelString, label, NULL);
	XmStringFree(label);
}

static Widget
label(Widget parent, const char *name, const char *text)
{
	Widget w = XtVaCreateManagedWidget(name, xmLabelWidgetClass, parent,
	    XmNalignment, XmALIGNMENT_BEGINNING, XmNmarginHeight, 0,
	    XmNtraversalOn, False, NULL);
	set_label(w, text);
	return w;
}

static Widget
button(Widget parent, const char *name, const char *text, XtCallbackProc cb,
    struct plan_page *p)
{
	Widget w = XtVaCreateManagedWidget(name, xmPushButtonWidgetClass, parent,
	    XmNmarginHeight, 1, XmNnavigationType, XmTAB_GROUP, NULL);
	set_label(w, text);
	XtAddCallback(w, XmNactivateCallback, cb, p);
	return w;
}

static Widget
input(struct plan_page *p, Widget parent, const char *name, int columns,
    int maximum, bool editable)
{
	Widget w = XtVaCreateManagedWidget(name, xmTextFieldWidgetClass, parent,
	    XmNcolumns, columns, XmNmaxLength, maximum, XmNeditable, editable,
	    XmNmarginHeight, 2, XmNcursorPositionVisible, editable,
	    XmNnavigationType, XmTAB_GROUP, NULL);
	if (d32ui_mono(p->calc->ui) != NULL)
		XtVaSetValues(w, XmNfontList, d32ui_mono(p->calc->ui), NULL);
	return w;
}

static Widget
option(Widget parent, const char *name, const char *heading,
    const char *const labels[4], Widget children[4])
{
	Widget menu = XmCreatePulldownMenu(parent, "choices", NULL, 0);
	Widget widget;
	XmString title = XmStringCreateLocalized((char *)heading);
	Arg args[3];

	for (size_t i = 0; i < 4; i++) {
		children[i] = XtVaCreateManagedWidget("choice", xmPushButtonWidgetClass,
		    menu, NULL);
		set_label(children[i], labels[i]);
	}
	XtSetArg(args[0], XmNsubMenuId, menu);
	XtSetArg(args[1], XmNlabelString, title);
	XtSetArg(args[2], XmNnavigationType, XmTAB_GROUP);
	widget = XmCreateOptionMenu(parent, (char *)name, args, XtNumber(args));
	XmStringFree(title);
	XtManageChild(widget);
	return widget;
}

static Widget
row(Widget parent, const char *name, Widget above)
{
	return XtVaCreateManagedWidget(name, xmRowColumnWidgetClass, parent,
	    XmNorientation, XmHORIZONTAL, XmNpacking, XmPACK_TIGHT,
	    XmNtopAttachment, above ? XmATTACH_WIDGET : XmATTACH_FORM,
	    XmNtopWidget, above, XmNleftAttachment, XmATTACH_FORM,
	    XmNrightAttachment, XmATTACH_FORM, XmNmarginWidth, 0,
	    XmNmarginHeight, 0, XmNspacing, 5, NULL);
}

static Widget
scroll_list(struct plan_page *p, Widget parent, const char *name)
{
	Arg args[4];
	Widget w;
	XtSetArg(args[0], XmNselectionPolicy, XmBROWSE_SELECT);
	XtSetArg(args[1], XmNvisibleItemCount, 3);
	XtSetArg(args[2], XmNlistSizePolicy, XmCONSTANT);
	XtSetArg(args[3], XmNnavigationType, XmTAB_GROUP);
	w = XmCreateScrolledList(parent, (char *)name, args, XtNumber(args));
	if (d32ui_mono(p->calc->ui) != NULL)
		XtVaSetValues(w, XmNfontList, d32ui_mono(p->calc->ui), NULL);
	XtManageChild(w);
	return w;
}

static void
prefix_label(const d32_prefix *prefix, char out[D32_ADDR_TEXT_CAP + 4])
{
	char address[D32_ADDR_TEXT_CAP];
	(void)d32_addr_format(&prefix->addr, address, sizeof(address));
	(void)snprintf(out, D32_ADDR_TEXT_CAP + 4, "%s/%u", address, (unsigned)prefix->len);
}

static d32_range
bounds(const struct plan_page *p, size_t i)
{
	const d32_plan_request *req = &p->model->requests[i];
	const d32_prefix *allocated = &p->model->result.placements[i].prefix;
	if (req->kind == D32_PLAN_RESERVE)
		return req->reserve;
	return (d32_range){allocated->addr, d32_prefix_last(allocated)};
}

static bool
clip(const struct plan_page *p, size_t i, d32_range *span)
{
	d32_addr end = d32_prefix_last(&p->scope);
	*span = bounds(p, i);
	if (d32_addr_compare(&span->lo, &p->scope.addr) < 0)
		span->lo = p->scope.addr;
	if (d32_addr_compare(&span->hi, &end) > 0)
		span->hi = end;
	return d32_addr_compare(&span->lo, &span->hi) <= 0;
}

/* Multiply an exact 128-bit count by a small scale, then divide by the
 * number of addresses in the current prefix. The fifth word retains carry. */
static unsigned
scale_count(d32_count count, unsigned exponent, unsigned scale, bool round)
{
	uint32_t product[5], words[] = {
		(uint32_t)count.lo, (uint32_t)(count.lo >> 32),
		(uint32_t)count.hi, (uint32_t)(count.hi >> 32)
	};
	uint64_t carry = 0, q;
	unsigned word = exponent / 32, shift = exponent % 32;

	if (count.full128)
		return scale;
	for (size_t i = 0; i < XtNumber(words); i++) {
		uint64_t value = (uint64_t)words[i] * scale + carry;
		product[i] = (uint32_t)value;
		carry = value >> 32;
	}
	product[4] = (uint32_t)carry;
	if (round && exponent != 0) {
		carry = (uint64_t)1 << ((exponent - 1) % 32);
		for (unsigned i = (exponent - 1) / 32; carry != 0 && i < XtNumber(product); i++) {
			carry += product[i];
			product[i] = (uint32_t)carry;
			carry >>= 32;
		}
	}
	q = product[word] >> shift;
	if (shift != 0)
		q |= (uint64_t)product[word + 1] << (32 - shift);
	return (unsigned)q;
}

static unsigned
scope_exponent(const struct plan_page *p)
{
	return (unsigned)d32_addr_width(p->scope.addr.fam) - p->scope.len;
}

static unsigned
position(const struct plan_page *p, const d32_addr *a, unsigned width)
{
	d32_range span = {p->scope.addr, *a};
	d32_count count = d32_range_count(&span);
	(void)d32_count_sub(count, (d32_count){.lo = 1}, &count);
	return scale_count(count, scope_exponent(p), width, false);
}

static enum paint
style(const struct plan_page *p, size_t i)
{
	switch (p->model->rows[i].kind) {
	case D32_PLAN_RESERVE: return RESERVED;
	case D32_PLAN_PIN: return PIN;
	default: return ALLOCATION;
	}
}

static bool
begin_paint(Widget w, struct plan_page *p, struct pen *pen)
{
	Dimension width, height;
	Colormap colormap;
	Pixel foreground, top, bottom;

	if (!XtIsRealized(w))
		return false;
	pen->display = XtDisplay(w);
	pen->window = XtWindow(w);
	XtVaGetValues(w, XmNwidth, &width, XmNheight, &height, XmNcolormap, &colormap,
	    XmNforeground, &pen->fg, XmNbackground, &pen->bg, XmNtopShadowColor, &pen->top,
	    XmNbottomShadowColor, &pen->bottom, NULL);
	pen->width = width < SHRT_MAX ? width : SHRT_MAX;
	pen->height = height < SHRT_MAX ? height : SHRT_MAX;
	if (pen->width < 18 || pen->height < 16)
		return false;
	XmGetColors(XtScreen(w), colormap, pen->bg, &foreground, &top, &bottom, &pen->select);
	if (p->gc == NULL) {
		p->gc = XCreateGC(pen->display, pen->window, 0, NULL);
		if (p->gc == NULL)
			return false;
		for (size_t i = 0; i < XtNumber(patterns); i++)
			p->stipple[i] = XCreateBitmapFromData(pen->display, pen->window,
			    (const char *)patterns[i], 8, 8);
	}
	pen->gc = p->gc;
	XSetFillStyle(pen->display, pen->gc, FillSolid);
	XSetForeground(pen->display, pen->gc, pen->bg);
	XFillRectangle(pen->display, pen->window, pen->gc, 0, 0, pen->width, pen->height);
	return true;
}

static void
brush(struct plan_page *p, const struct pen *pen, enum paint paint)
{
	Pixel foreground = pen->fg;
	Pixmap stipple = None;
	switch (paint) {
	case FREE: foreground = pen->top; break;
	case ALLOCATION: foreground = pen->select; break;
	case PIN: stipple = p->stipple[0]; break;
	case RESERVED: stipple = p->stipple[1]; break;
	case OTHER: stipple = p->stipple[2]; break;
	}
	if (foreground == pen->bg)
		foreground = pen->bottom != pen->bg ? pen->bottom : pen->fg;
	XSetForeground(pen->display, pen->gc, foreground);
	XSetFillStyle(pen->display, pen->gc, stipple == None ? FillSolid : FillOpaqueStippled);
	if (stipple != None) {
		XSetBackground(pen->display, pen->gc, pen->bg);
		XSetStipple(pen->display, pen->gc, stipple);
	}
}

static void
bar_interval(const struct plan_page *p, const d32_range *span, unsigned width,
    unsigned *first, unsigned *last)
{
	d32_addr next, end = d32_prefix_last(&p->scope);
	*first = position(p, &span->lo, width);
	*last = d32_addr_compare(&span->hi, &end) == 0 || !d32_addr_next(&span->hi, &next) ?
	    width : position(p, &next, width);
	if (*last == *first)
		(*last)++;
}

static void
draw_bar(Widget w, XtPointer data, XtPointer call)
{
	struct plan_page *p = data;
	struct pen pen;
	unsigned width, height, left = 4, top = 4, selected_first = 0, selected_last = 0;
	(void)call;
	if (!begin_paint(w, p, &pen))
		return;
	width = pen.width - 2 * left;
	height = pen.height - 2 * top;
	brush(p, &pen, FREE);
	XFillRectangle(pen.display, pen.window, pen.gc, (int)left, (int)top, width, height);
	for (size_t pos = 0; pos < p->visible_count; pos++) {
		size_t i = p->visible[pos];
		d32_range span;
		unsigned first, last;
		if (!clip(p, i, &span))
			continue;
		bar_interval(p, &span, width, &first, &last);
		brush(p, &pen, style(p, i));
		XFillRectangle(pen.display, pen.window, pen.gc, (int)(left + first),
		    (int)top, last - first, height);
		if (i == p->selected) {
			selected_first = first;
			selected_last = last;
		}
	}
	XSetFillStyle(pen.display, pen.gc, FillSolid);
	XSetForeground(pen.display, pen.gc, pen.fg);
	XDrawRectangle(pen.display, pen.window, pen.gc, (int)left - 1, (int)top - 1, width + 1, height + 1);
	if (selected_last != 0) {
		XDrawRectangle(pen.display, pen.window, pen.gc, (int)(left + selected_first),
		    (int)top + 1, selected_last - selected_first - 1, height - 3);
		XDrawLine(pen.display, pen.window, pen.gc, (int)(left + selected_first), 0,
		    (int)(left + selected_first), (int)pen.height - 1);
	}
}

static void
draw_sector(struct plan_page *p, const struct pen *pen, enum paint paint,
    unsigned diameter, unsigned start, unsigned end)
{
	if (end <= start)
		return;
	brush(p, pen, paint);
	XFillArc(pen->display, pen->window, pen->gc, 5, 4, diameter, diameter,
	    (int)start * 64, (int)(end - start) * 64);
}

static void
draw_donut(Widget w, XtPointer data, XtPointer call)
{
	struct plan_page *p = data;
	struct pen pen;
	unsigned diameter, exponent = scope_exponent(p), degrees = 0;
	d32_count tiny = {0}, drawn = {0}, free = d32_count_pow2(exponent);
	size_t merged = 0;
	char text[96];

	(void)call;
	if (!begin_paint(w, p, &pen))
		return;
	diameter = pen.height - 8;
	if (diameter > pen.width / 3)
		diameter = pen.width / 3;
	for (size_t pos = 0; pos < p->visible_count; pos++) {
		size_t i = p->visible[pos];
		d32_range span;
		d32_count count;
		unsigned next;
		if (!clip(p, i, &span))
			continue;
		count = d32_range_count(&span);
		(void)d32_count_sub(free, count, &free);
		if (scale_count(count, exponent, 360, true) < 2) {
			(void)d32_count_add(tiny, count, &tiny);
			merged++;
			continue;
		}
		(void)d32_count_add(drawn, count, &drawn);
		next = scale_count(drawn, exponent, 360, true);
		draw_sector(p, &pen, style(p, i), diameter, degrees, next);
		degrees = next;
	}
	if (d32_count_compare(free, (d32_count){0}) > 0 && scale_count(free, exponent, 360, true) < 2) {
		(void)d32_count_add(tiny, free, &tiny);
		free = (d32_count){0};
		merged++;
	}
	(void)d32_count_add(drawn, free, &drawn);
	unsigned end = scale_count(drawn, exponent, 360, true);
	draw_sector(p, &pen, FREE, diameter, degrees, end);
	draw_sector(p, &pen, OTHER, diameter, end, 360);
	XSetFillStyle(pen.display, pen.gc, FillSolid);
	XSetForeground(pen.display, pen.gc, pen.bg);
	XFillArc(pen.display, pen.window, pen.gc, 5 + (int)(diameter / 4),
	    4 + (int)(diameter / 4), diameter / 2, diameter / 2, 0, 360 * 64);
	XSetForeground(pen.display, pen.gc, pen.fg);
	XDrawArc(pen.display, pen.window, pen.gc, 5, 4, diameter, diameter, 0, 360 * 64);
	(void)snprintf(text, sizeof(text), "Other (%zu): %u degrees", merged,
	    scale_count(tiny, exponent, 360, true));
	const char *legend = "Allocation / Pin / Reserved / Free";
	XDrawString(pen.display, pen.window, pen.gc, (int)diameter + 12, 16, legend, (int)strlen(legend));
	if (merged != 0)
		XDrawString(pen.display, pen.window, pen.gc, (int)diameter + 12, 32, text, (int)strlen(text));
}

static void
repaint(struct plan_page *p)
{
	if (XtIsRealized(p->bar))
		XClearArea(XtDisplay(p->bar), XtWindow(p->bar), 0, 0, 0, 0, True);
	if (XtIsManaged(p->donut) && XtIsRealized(p->donut))
		XClearArea(XtDisplay(p->donut), XtWindow(p->donut), 0, 0, 0, 0, True);
}

static size_t
view_position(const struct plan_page *p, size_t selected)
{
	for (size_t i = 0; i < p->visible_count; i++)
		if (p->visible[i] == selected)
			return i;
	return SIZE_MAX;
}

static void
select_row(struct plan_page *p, size_t selected)
{
	size_t view;
	int top, count;

	if (p->syncing)
		return;
	p->selected = selected < p->model->count ? selected : SIZE_MAX;
	view = view_position(p, p->selected);
	if (p->zoomed && p->selected != SIZE_MAX && view == SIZE_MAX) {
		p->zoomed = false;
		render(p);
		return;
	}
	p->syncing = true;
	if (view != SIZE_MAX && (view < p->outline_first || view - p->outline_first >= p->outline_count)) {
		p->outline_first = view / PLAN_OUTLINE_ROWS * PLAN_OUTLINE_ROWS;
		render_outline(p);
	}
	XmListDeselectAllItems(p->requests);
	XmListDeselectAllItems(p->allocations);
	if (p->selected != SIZE_MAX) {
		const struct calc_plan_row *row = &p->model->rows[p->selected];
		XmTextFieldSetString(p->name, (char *)row->name);
		XmTextFieldSetString(p->value, (char *)row->value);
		XtVaSetValues(p->kind, XmNmenuHistory, p->kinds[row->kind], NULL);
		XmListSelectPos(p->requests, (int)p->selected + 1, False);
		XtVaGetValues(p->requests, XmNtopItemPosition, &top, XmNvisibleItemCount, &count, NULL);
		if ((int)p->selected + 1 < top || (int)p->selected + 1 >= top + count)
			XmListSetPos(p->requests, (int)p->selected + 1);
	}
	if (view != SIZE_MAX) {
		XmListSelectPos(p->allocations, (int)view + 1, False);
		XtVaGetValues(p->allocations, XmNtopItemPosition, &top, XmNvisibleItemCount, &count, NULL);
		if ((int)view + 1 < top || (int)view + 1 >= top + count)
			XmListSetPos(p->allocations, (int)view + 1);
		XtVaSetValues(p->tree_root, XmNoutlineState, XmEXPANDED, NULL);
		p->picked[0] = p->leaves[view - p->outline_first];
		XtVaSetValues(p->outline, XmNselectedObjects, p->picked, XmNselectedObjectCount, 1, NULL);
		if (XtIsRealized(p->outline_scroll))
			XmScrollVisible(p->outline_scroll, p->picked[0], 0, 0);
	} else
		XtVaSetValues(p->outline, XmNselectedObjects, NULL, XmNselectedObjectCount, 0, NULL);
	XtSetSensitive(p->buttons[ADD], p->model->count < D32_PLAN_MAX_REQUESTS);
	XtSetSensitive(p->buttons[UPDATE], p->selected != SIZE_MAX);
	XtSetSensitive(p->buttons[REMOVE], p->selected != SIZE_MAX);
	XtSetSensitive(p->buttons[UP], p->selected != SIZE_MAX && p->selected != 0);
	XtSetSensitive(p->buttons[DOWN], p->selected != SIZE_MAX && p->selected + 1 < p->model->count);
	XtSetSensitive(p->zoom, p->selected != SIZE_MAX);
	XtSetSensitive(p->unzoom, p->zoomed);
	p->syncing = false;
	repaint(p);
}

static int
range_order(const void *a, const void *b)
{
	return d32_addr_compare(&((const d32_range *)a)->lo, &((const d32_range *)b)->lo);
}

static void
largest_in_gap(const d32_range *gap, bool *present, d32_prefix *largest)
{
	d32_prefix cover[D32_RANGE_COVER_MAX];
	size_t n;
	if (d32_range_cover(gap, cover, XtNumber(cover), &n) != D32_NET_OK)
		return;
	for (size_t i = 0; i < n; i++) {
		if (!*present || cover[i].len < largest->len) {
			*largest = cover[i];
			*present = true;
		}
	}
}

static void
scoped_capacity(const struct plan_page *p, d32_count *unused, bool *present,
    d32_prefix *largest)
{
	d32_range used[D32_PLAN_MAX_REQUESTS];
	d32_range gap = {p->scope.addr, d32_prefix_last(&p->scope)};
	d32_addr end = gap.hi;
	if (!p->zoomed) {
		*unused = p->model->result.free_count;
		*present = p->model->result.has_largest;
		*largest = p->model->result.largest;
		return;
	}
	*unused = d32_count_pow2(scope_exponent(p));
	*present = false;
	for (size_t i = 0; i < p->visible_count; i++) {
		(void)clip(p, p->visible[i], &used[i]);
		(void)d32_count_sub(*unused, d32_range_count(&used[i]), unused);
	}
	qsort(used, p->visible_count, sizeof(*used), range_order);
	for (size_t i = 0; i < p->visible_count; i++) {
		if (d32_addr_compare(&gap.lo, &used[i].lo) < 0) {
			(void)d32_addr_prev(&used[i].lo, &gap.hi);
			largest_in_gap(&gap, present, largest);
		}
		if (d32_addr_compare(&end, &used[i].hi) == 0)
			return;
		(void)d32_addr_next(&used[i].hi, &gap.lo);
	}
	gap.hi = end;
	largest_in_gap(&gap, present, largest);
}

static void
format_span(const struct plan_page *p, size_t i, const d32_range *span,
    char out[2 * D32_ADDR_TEXT_CAP + 4])
{
	if (p->model->rows[i].kind == D32_PLAN_RESERVE) {
		char lo[D32_ADDR_TEXT_CAP], hi[D32_ADDR_TEXT_CAP];
		(void)d32_addr_format(&span->lo, lo, sizeof(lo));
		(void)d32_addr_format(&span->hi, hi, sizeof(hi));
		(void)snprintf(out, 2 * D32_ADDR_TEXT_CAP + 4, "%s .. %s", lo, hi);
	} else
		prefix_label(&p->model->result.placements[i].prefix, out);
}

/* The full lists and bar keep every row. Motif lays out only one page of
 * reusable outline gadgets, even when an import replaces all 512 rows. */
static void
render_outline(struct plan_page *p)
{
	Widget newly_managed[PLAN_OUTLINE_ROWS];
	Cardinal nnew = 0;
	char leaf[PLAN_LEAF_TEXT_CAP], span_text[2 * D32_ADDR_TEXT_CAP + 4];
	char prefix[D32_ADDR_TEXT_CAP + 4], title[96];
	bool paged = p->visible_count > PLAN_OUTLINE_ROWS;

	if (p->outline_first >= p->visible_count)
		p->outline_first = 0;
	p->outline_count = p->visible_count - p->outline_first;
	if (p->outline_count > PLAN_OUTLINE_ROWS)
		p->outline_count = PLAN_OUTLINE_ROWS;
	XtVaSetValues(p->outline, XmNselectedObjects, NULL, XmNselectedObjectCount, 0, NULL);
	for (size_t slot = 0; slot < p->outline_count; slot++) {
		size_t i = p->visible[p->outline_first + slot];
		d32_range span;
		(void)clip(p, i, &span);
		format_span(p, i, &span, span_text);
		(void)snprintf(leaf, sizeof(leaf), "%s  %s", span_text, p->model->rows[i].name);
		if (slot == p->leaves_count) {
			p->leaves[slot] = XtVaCreateWidget("planBranch", xmIconGadgetClass, p->outline,
			    XmNentryParent, p->tree_root, XmNviewType, XmSMALL_ICON, NULL);
			p->leaves_count++;
		}
		if (strcmp(leaf, p->leaf_labels[slot]) != 0) {
			set_label(p->leaves[slot], leaf);
			(void)snprintf(p->leaf_labels[slot], sizeof(p->leaf_labels[slot]), "%s", leaf);
		}
		if (!XtIsManaged(p->leaves[slot]))
			newly_managed[nnew++] = p->leaves[slot];
	}
	if (p->leaves_count > p->outline_count)
		XtUnmanageChildren(p->leaves + p->outline_count, (Cardinal)(p->leaves_count - p->outline_count));
	if (nnew != 0)
		XtManageChildren(newly_managed, nnew);
	if (paged) {
		XtManageChild(p->later);
		XtManageChild(p->earlier);
		XtVaSetValues(p->scope_label, XmNrightAttachment, XmATTACH_WIDGET,
		    XmNrightWidget, p->earlier, XmNrightOffset, 3, NULL);
	} else {
		XtVaSetValues(p->scope_label, XmNrightAttachment, XmATTACH_FORM, XmNrightOffset, 0, NULL);
		XtUnmanageChild(p->earlier);
		XtUnmanageChild(p->later);
	}
	XtSetSensitive(p->earlier, p->outline_first != 0);
	XtSetSensitive(p->later, p->outline_first + p->outline_count < p->visible_count);
	prefix_label(&p->scope, prefix);
	set_label(p->tree_root, prefix);
	if (paged)
		(void)snprintf(title, sizeof(title), "%zu-%zu/%zu  %s", p->outline_first + 1,
		    p->outline_first + p->outline_count, p->visible_count, prefix);
	else
		(void)snprintf(title, sizeof(title), "%s %s", p->zoomed ? "Zoom" : "Scope", prefix);
	set_label(p->scope_label, title);
}

static void
render(struct plan_page *p)
{
	XmString rows[D32_PLAN_MAX_REQUESTS], allocations[D32_PLAN_MAX_REQUESTS];
	size_t count = p->model->count;
	char row_text[CALC_PLAN_NAME_MAX + CALC_PLAN_VALUE_MAX + 90];
	char range_text[2 * D32_ADDR_TEXT_CAP + 4];
	char amount[D32_COUNT_TEXT_CAP], largest[D32_ADDR_TEXT_CAP + 4];
	d32_prefix largest_prefix;
	d32_count unused;
	bool has_largest;

	if (!p->zoomed)
		p->scope = p->model->parent;
	p->visible_count = 0;
	for (size_t i = 0; i < count; i++) {
		const struct calc_plan_row *row = &p->model->rows[i];
		d32_range span;
		char capacity[D32_COUNT_TEXT_CAP];
		(void)snprintf(row_text, sizeof(row_text), "%s  %s  %s", row->name,
		    calc_plan_kind_labels[row->kind], row->value);
		rows[i] = XmStringCreateLocalized(row_text);
		if (!clip(p, i, &span))
			continue;
		format_span(p, i, &span, range_text);
		d32_count addresses = d32_range_count(&span);
		(void)d32_count_format(&addresses, true, capacity, sizeof(capacity));
		size_t pos = p->visible_count++;
		p->visible[pos] = i;
		(void)snprintf(row_text, sizeof(row_text), "%s  %s  %s", row->name, range_text, capacity);
		allocations[pos] = XmStringCreateLocalized(row_text);
	}
	p->syncing = true;
	size_t selected_view = view_position(p, p->selected);
	if (selected_view != SIZE_MAX)
		p->outline_first = selected_view / PLAN_OUTLINE_ROWS * PLAN_OUTLINE_ROWS;
	render_outline(p);
	XmListDeleteAllItems(p->requests);
	XmListDeleteAllItems(p->allocations);
	if (count != 0)
		XmListAddItemsUnselected(p->requests, rows, (int)count, 1);
	if (p->visible_count != 0)
		XmListAddItemsUnselected(p->allocations, allocations, (int)p->visible_count, 1);
	for (size_t i = 0; i < count; i++)
		XmStringFree(rows[i]);
	for (size_t i = 0; i < p->visible_count; i++)
		XmStringFree(allocations[i]);
	p->syncing = false;
	scoped_capacity(p, &unused, &has_largest, &largest_prefix);
	(void)d32_count_format(&unused, true, amount, sizeof(amount));
	if (has_largest)
		prefix_label(&largest_prefix, largest);
	XmTextFieldSetString(p->free_value, amount);
	XmTextFieldSetString(p->largest, has_largest ? largest : "None");
	select_row(p, p->selected);
}

static void
list_selected(Widget widget, XtPointer data, XtPointer call)
{
	struct plan_page *p = data;
	const XmListCallbackStruct *event = call;
	if (p->syncing || event->item_position < 1)
		return;
	size_t i = (size_t)event->item_position - 1;
	select_row(p, widget == p->requests ? i : i < p->visible_count ? p->visible[i] : SIZE_MAX);
}

static void
tree_selected(Widget widget, XtPointer data, XtPointer call)
{
	struct plan_page *p = data;
	const XmContainerSelectCallbackStruct *event = call;
	(void)widget;
	if (p->syncing || event->selected_item_count != 1)
		return;
	for (size_t i = 0; i < p->outline_count; i++) {
		if (event->selected_items[0] == p->leaves[i]) {
			select_row(p, p->visible[p->outline_first + i]);
			return;
		}
	}
	select_row(p, SIZE_MAX);
}

static void
outline_turn(Widget widget, XtPointer data, XtPointer call)
{
	struct plan_page *p = data;
	size_t position = p->outline_first;
	(void)call;
	if (widget == p->earlier) {
		if (position == 0)
			return;
		position -= PLAN_OUTLINE_ROWS;
	} else {
		position += PLAN_OUTLINE_ROWS;
		if (position >= p->visible_count)
			return;
	}
	select_row(p, p->visible[position]);
}

static void
bar_input(Widget widget, XtPointer data, XEvent *event, Boolean *dispatch)
{
	struct plan_page *p = data;
	(void)dispatch;
	if (event->type == ButtonPress && event->xbutton.button == Button1) {
		Dimension widget_width;
		int x = event->xbutton.x - 4;
		XtVaGetValues(widget, XmNwidth, &widget_width, NULL);
		if (x < 0 || widget_width <= 8 || x >= widget_width - 8)
			return;
		for (size_t k = p->visible_count; k > 0; k--) {
			size_t i = p->visible[k - 1];
			d32_range span;
			unsigned first, last;
			if (!clip(p, i, &span))
				continue;
			bar_interval(p, &span, widget_width - 8, &first, &last);
			if ((unsigned)x >= first && (unsigned)x < last) {
				select_row(p, i);
				break;
			}
		}
		(void)XmProcessTraversal(widget, XmTRAVERSE_CURRENT);
	} else if (event->type == KeyPress && p->visible_count != 0) {
		KeySym key = XLookupKeysym(&event->xkey, 0);
		size_t i = view_position(p, p->selected);
		if (key == XK_Left && i > 0)
			i = i == SIZE_MAX ? p->visible_count - 1 : i - 1;
		else if (key == XK_Right && (i == SIZE_MAX || i + 1 < p->visible_count))
			i = i == SIZE_MAX ? 0 : i + 1;
		else if (key == XK_Home)
			i = 0;
		else if (key == XK_End)
			i = p->visible_count - 1;
		else
			return;
		select_row(p, p->visible[i]);
		*dispatch = False;
	}
}

static void
zoom(Widget widget, XtPointer data, XtPointer call)
{
	struct plan_page *p = data;
	d32_count extra;
	(void)call;
	if (widget == p->unzoom)
		p->zoomed = false;
	else if (p->selected != SIZE_MAX) {
		d32_range range = bounds(p, p->selected);
		if (d32_range_supernet(&range, &p->scope, &extra) != D32_NET_OK)
			return;
		p->zoomed = true;
	}
	render(p);
	d32ui_status(p->calc->ui, p->zoomed ? "[OK] Showing selected branch" : "[OK] Showing entire plan");
}

static void
report_failure(struct plan_page *p, const struct calc_plan_error *error)
{
	char detail[96], message[220];
	const char *why = d32_net_status_name(error->status);
	if (error->column != 0)
		(void)snprintf(detail, sizeof(detail), "%s at %s column %zu", why,
		    error->name ? "name" : "value", error->column);
	else
		(void)snprintf(detail, sizeof(detail), "%s", why);
	if (error->row != SIZE_MAX)
		(void)snprintf(message, sizeof(message), "[x] Request %zu: %s. Previous plan kept.",
		    error->row + 1, detail);
	else
		(void)snprintf(message, sizeof(message), "[x] %s. Previous plan kept.", detail);
	d32ui_status(p->calc->ui, message);
}

static bool
publish(struct plan_page *p, size_t selected)
{
	struct calc_plan_model *old = p->model;
	struct calc_plan_error error;
	char message[96];
	if (calc_plan_compute(p->draft, &error) != D32_NET_OK) {
		report_failure(p, &error);
		return false;
	}
	p->model = p->draft;
	p->draft = old;
	p->selected = selected;
	p->zoomed = false;
	render(p);
	(void)snprintf(message, sizeof(message), "[OK] Plan computed. %zu request%s.", p->model->count,
	    p->model->count == 1 ? "" : "s");
	d32ui_status(p->calc->ui, message);
	return true;
}

static d32_host_policy
context_policy(const struct calc *calc)
{
	if (calc->family == D32_IPV6)
		return D32_HOST_RAW;
	return calc->host_policy == D32_HOST_RAW ? D32_HOST_SUBNET : calc->host_policy;
}

static void
policy_menu(struct plan_page *p)
{
	bool ipv6 = p->calc->family == D32_IPV6;
	for (size_t i = 0; i < XtNumber(p->policies); i++)
		XtSetSensitive(p->policies[i], (i == D32_HOST_RAW) == ipv6);
	XtVaSetValues(p->policy, XmNmenuHistory, p->policies[context_policy(p->calc)], NULL);
}

static void
policy_changed(Widget widget, XtPointer data, XtPointer call)
{
	struct plan_page *p = data;
	size_t i;
	(void)call;
	for (i = 0; i < XtNumber(p->policies) && p->policies[i] != widget; i++)
		;
	if (i == XtNumber(p->policies))
		return;
	if (!calc_commit_context(p->calc)) {
		policy_menu(p);
		return;
	}
	*p->draft = *p->model;
	p->draft->parent = p->calc->context;
	p->draft->policy = (d32_host_policy)i;
	if (publish(p, p->selected) && i != D32_HOST_RAW)
		p->calc->host_policy = (d32_host_policy)i;
	policy_menu(p);
}

static bool
editor_row(struct plan_page *p, struct calc_plan_row *row)
{
	Widget choice;
	size_t i;
	char *name = XmTextFieldGetString(p->name), *value = XmTextFieldGetString(p->value);
	XtVaGetValues(p->kind, XmNmenuHistory, &choice, NULL);
	for (i = 0; i < XtNumber(p->kinds) && p->kinds[i] != choice; i++)
		;
	bool valid = name != NULL && value != NULL && strlen(name) <= CALC_PLAN_NAME_MAX &&
	    strlen(value) <= CALC_PLAN_VALUE_MAX && i < XtNumber(p->kinds);
	if (valid) {
		row->kind = (d32_plan_kind)i;
		(void)snprintf(row->name, sizeof(row->name), "%s", name);
		(void)snprintf(row->value, sizeof(row->value), "%s", value);
	}
	XtFree(name);
	XtFree(value);
	return valid;
}

static void
edit_row(Widget widget, XtPointer data, XtPointer call)
{
	struct plan_page *p = data;
	struct calc_plan_model *next = p->draft;
	size_t selected = p->selected, action;
	(void)call;
	for (action = 0; action < XtNumber(p->buttons) && p->buttons[action] != widget; action++)
		;
	if (widget == p->value)
		action = selected == SIZE_MAX ? ADD : UPDATE;
	if (action >= XtNumber(p->buttons) || !calc_commit_context(p->calc))
		return;
	*next = *p->model;
	next->parent = p->calc->context;
	next->policy = context_policy(p->calc);
	if (action == ADD) {
		if (next->count == D32_PLAN_MAX_REQUESTS) {
			d32ui_status(p->calc->ui, "[x] A plan can contain at most 512 requests.");
			return;
		}
		selected = next->count++;
	}
	if (selected >= next->count)
		return;
	if (action == ADD || action == UPDATE) {
		if (!editor_row(p, &next->rows[selected])) {
			d32ui_status(p->calc->ui, "[x] Invalid request editor fields. Previous plan kept.");
			return;
		}
	} else if (action == REMOVE) {
		next->count--;
		if (selected < next->count)
			memmove(&next->rows[selected], &next->rows[selected + 1],
			    (next->count - selected) * sizeof(next->rows[0]));
		else
			selected = next->count == 0 ? SIZE_MAX : next->count - 1;
	} else {
		size_t neighbor = action == UP ? selected - 1 : selected + 1;
		if (neighbor >= next->count)
			return;
		struct calc_plan_row swap = next->rows[selected];
		next->rows[selected] = next->rows[neighbor];
		next->rows[neighbor] = swap;
		selected = neighbor;
	}
	(void)publish(p, selected);
}

static void
name_activate(Widget widget, XtPointer data, XtPointer call)
{
	struct plan_page *p = data;
	(void)widget;
	(void)call;
	(void)XmProcessTraversal(p->value, XmTRAVERSE_CURRENT);
}

static const char *
plan_leaf(const struct calc *calc, const char *path)
{
	size_t length = strlen(calc->plan_path);
	if (calc->plan_fd == -1 || strncmp(path, calc->plan_path, length) != 0 ||
	    path[length] != '/' || path[length + 1] == '\0' || strchr(path + length + 1, '/') != NULL)
		return NULL;
	return path + length + 1;
}

static void
import_plan(const char *path, void *data)
{
	struct plan_page *p = data;
	struct calc_plan_error error;
	enum d32_error rc;
	struct calc_plan_model *old;
	if (plan_leaf(p->calc, path) == NULL) {
		d32ui_status(p->calc->ui, "[x] Choose a file in the private ~/.dt/d32/plans folder.");
		return;
	}
#ifdef D32_CALC_FRAME_PROOF
	struct timespec begin, end;
	bool measured = clock_gettime(CLOCK_MONOTONIC, &begin) == 0;
#endif
	memset(p->draft, 0, sizeof(*p->draft));
	rc = calc_plan_read(path, p->draft, &error);
	if (rc != D32E_OK) {
		if (error.status != D32_NET_OK)
			report_failure(p, &error);
		else {
			d32ui_status(p->calc->ui, "[x] Plan import failed. Previous plan kept.");
			(void)d32ui_error(p->calc->ui, p->page, rc, "plan-import");
		}
		return;
	}
	old = p->model;
	p->model = p->draft;
	p->draft = old;
	p->selected = p->model->count == 0 ? SIZE_MAX : 0;
	p->zoomed = false;
	if (p->model->policy != D32_HOST_RAW)
		p->calc->host_policy = p->model->policy;
	calc_set_context(p->calc, &p->model->parent);
	calc_select_tab(p->calc, CALC_PLAN);
	policy_menu(p);
	render(p);
#ifdef D32_CALC_FRAME_PROOF
	XmUpdateDisplay(p->page);
	Display *display = XtDisplay(p->page);
	XSync(display, False);
	if (measured && clock_gettime(CLOCK_MONOTONIC, &end) == 0) {
		long long usec = (long long)(end.tv_sec - begin.tv_sec) * 1000000 +
		    (end.tv_nsec - begin.tv_nsec) / 1000;
		char text[48];
		int n = snprintf(text, sizeof(text), "%zu %lld", p->model->count, usec);
		Atom atom = XInternAtom(display, "_D32_PLAN_FRAME_US", False);
		if (n > 0 && (size_t)n < sizeof(text))
			XChangeProperty(display, XtWindow(d32ui_shell(p->calc->ui)), atom,
			    XA_STRING, 8, PropModeReplace, (unsigned char *)text, n);
	}
#endif
	d32ui_status(p->calc->ui, "[OK] Plan imported");
}

static void
export_plan(const char *path, void *data)
{
	struct plan_page *p = data;
	const char *name = plan_leaf(p->calc, path);
	if (name == NULL) {
		d32ui_status(p->calc->ui, "[x] Choose a file in the private ~/.dt/d32/plans folder.");
		return;
	}
	enum d32_error rc = calc_plan_write_at(p->calc->plan_fd, name, p->model);
	if (rc != D32E_OK) {
		d32ui_status(p->calc->ui, "[x] Plan export failed");
		(void)d32ui_error(p->calc->ui, p->page, rc, "plan-export");
	} else
		d32ui_status(p->calc->ui, "[OK] Plan exported");
}

static void
plan_picker(struct plan_page *p, Widget dialog, const char *title)
{
	if (dialog == NULL)
		return;
	XmString label = XmStringCreateLocalized((char *)title);
	XtVaSetValues(dialog, XmNdialogTitle, label, XmNmustMatch, False, NULL);
	XmStringFree(label);
	Widget selection = XmFileSelectionBoxGetChild(dialog, XmDIALOG_TEXT);
	if (selection != NULL && XmIsTextField(selection)) {
		XmTextFieldSetString(selection, "");
		(void)XmProcessTraversal(selection, XmTRAVERSE_CURRENT);
	}
	d32ui_status(p->calc->ui, "[?] Choose a plan file.");
}

void
calc_plan_open(struct calc *calc)
{
	struct plan_page *p = calc->plan;
	if (p == NULL)
		return;
	if (calc->plan_fd == -1) {
		d32ui_status(calc->ui, "[x] The private ~/.dt/d32/plans folder is unavailable.");
		return;
	}
	calc_select_tab(calc, CALC_PLAN);
	Widget dialog = d32ui_file_select(calc->ui, p->name, "*.json", import_plan, p);
	plan_picker(p, dialog, "Import Plan");
}

void
calc_plan_save(struct calc *calc)
{
	struct plan_page *p = calc->plan;
	if (p == NULL)
		return;
	if (calc->plan_fd == -1) {
		d32ui_status(calc->ui, "[x] The private ~/.dt/d32/plans folder is unavailable.");
		return;
	}
	calc_select_tab(calc, CALC_PLAN);
	Widget dialog = d32ui_file_select(calc->ui, p->name, "*.json", export_plan, p);
	plan_picker(p, dialog, "Export Plan to a New File");
}

static void
show_donut(struct plan_page *p)
{
	XtVaSetValues(p->outline_scroll, XmNbottomPosition, p->calc->donut ? 29 : 42, NULL);
	if (p->calc->donut)
		XtManageChild(p->donut);
	XtVaSetValues(p->allocation_head, XmNtopWidget,
	    p->calc->donut ? p->donut : p->bar, NULL);
	if (!p->calc->donut)
		XtUnmanageChild(p->donut);
	repaint(p);
}

void
calc_plan_donut(struct calc *calc)
{
	struct plan_page *p = calc->plan;
	if (p == NULL)
		return;
	show_donut(p);
	d32ui_status(calc->ui, calc->donut ? "[OK] Plan donut shown" : "[OK] Plan donut hidden");
}

void
calc_plan_refresh(struct calc *calc)
{
	struct plan_page *p = calc->plan;
	d32_host_policy policy;
	if (p == NULL)
		return;
	policy = context_policy(calc);
	policy_menu(p);
	if (calc->active != CALC_PLAN)
		return;
	if (p->model->policy == policy && p->model->parent.len == calc->context.len &&
	    d32_addr_compare(&p->model->parent.addr, &calc->context.addr) == 0)
		return;
	*p->draft = *p->model;
	p->draft->parent = calc->context;
	p->draft->policy = policy;
	(void)publish(p, p->selected);
}

static void
destroy_page(Widget widget, XtPointer data, XtPointer call)
{
	struct plan_page *p = data;
	(void)call;
	for (size_t i = 0; i < XtNumber(p->stipple); i++)
		if (p->stipple[i] != None)
			XFreePixmap(XtDisplay(widget), p->stipple[i]);
	if (p->gc != NULL)
		XFreeGC(XtDisplay(widget), p->gc);
	free(p->model);
	free(p->draft);
	p->calc->plan = NULL;
	free(p);
}

static void
make_views(struct plan_page *p, Widget above)
{
	Widget left, right, heading, split, w;
	Arg args[4];

	left = XtVaCreateManagedWidget("requestColumn", xmFormWidgetClass, p->page,
	    XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, above, XmNbottomAttachment, XmATTACH_FORM,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_POSITION, XmNrightPosition, 39, NULL);
	w = label(left, "requestHeading", "Requests");
	XtVaSetValues(w, XmNtopAttachment, XmATTACH_FORM, XmNleftAttachment, XmATTACH_FORM, NULL);
	p->requests = scroll_list(p, left, "planRequests");
	XtVaSetValues(XtParent(p->requests), XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, w,
	    XmNbottomAttachment, XmATTACH_FORM, XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
	right = XtVaCreateManagedWidget("resultColumn", xmFormWidgetClass, p->page,
	    XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, above, XmNbottomAttachment, XmATTACH_FORM,
	    XmNleftAttachment, XmATTACH_POSITION, XmNleftPosition, 40, XmNrightAttachment, XmATTACH_FORM, NULL);
	heading = XtVaCreateManagedWidget("planOutlineHeading", xmFormWidgetClass, right,
	    XmNtopAttachment, XmATTACH_FORM, XmNleftAttachment, XmATTACH_FORM,
	    XmNrightAttachment, XmATTACH_FORM, NULL);
	p->scope_label = label(heading, "planScope", "Scope");
	XtVaSetValues(p->scope_label, XmNrecomputeSize, False, XmNtopAttachment, XmATTACH_FORM,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
	p->earlier = button(heading, "planOutlineEarlier", "Earlier", outline_turn, p);
	p->later = button(heading, "planOutlineLater", "Later", outline_turn, p);
	XtVaSetValues(p->earlier, XmNtopAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_WIDGET,
	    XmNrightWidget, p->later, XmNrightOffset, 3, NULL);
	XtVaSetValues(p->later, XmNtopAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
	XtUnmanageChild(p->earlier);
	XtUnmanageChild(p->later);
	split = XtVaCreateManagedWidget("planViews", xmFormWidgetClass, right,
	    XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, heading,
	    XmNbottomAttachment, XmATTACH_FORM, XmNleftAttachment, XmATTACH_FORM,
	    XmNrightAttachment, XmATTACH_FORM, NULL);
	XtSetArg(args[0], XmNscrollingPolicy, XmAUTOMATIC);
	XtSetArg(args[1], XmNscrollBarDisplayPolicy, XmAS_NEEDED);
	p->outline_scroll = XmCreateScrolledWindow(split, "outlineScroll", args, 2);
	XtVaSetValues(p->outline_scroll, XmNtopAttachment, XmATTACH_FORM,
	    XmNbottomAttachment, XmATTACH_POSITION, XmNbottomPosition, 42,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
	XtSetArg(args[0], XmNlayoutType, XmOUTLINE);
	XtSetArg(args[1], XmNselectionPolicy, XmSINGLE_SELECT);
	XtSetArg(args[2], XmNnavigationType, XmTAB_GROUP);
	XtSetArg(args[3], XmNentryViewType, XmSMALL_ICON);
	p->outline = XmCreateContainer(p->outline_scroll, "planOutline", args, XtNumber(args));
	XtManageChild(p->outline);
	XtManageChild(p->outline_scroll);
	p->tree_root = XtVaCreateManagedWidget("planRoot", xmIconGadgetClass, p->outline,
	    XmNoutlineState, XmEXPANDED, XmNviewType, XmSMALL_ICON, NULL);
	p->bar = XtVaCreateManagedWidget("planRange", xmDrawingAreaWidgetClass, split,
	    XmNheight, 19, XmNresizePolicy, XmRESIZE_NONE, XmNnavigationType, XmTAB_GROUP,
	    XmNtraversalOn, True, XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, p->outline_scroll,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
	p->donut = XtVaCreateWidget("planDonut", xmDrawingAreaWidgetClass, split,
	    XmNheight, 38, XmNresizePolicy, XmRESIZE_NONE, XmNtraversalOn, False,
	    XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, p->bar,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
	p->allocation_head = label(split, "allocationHeading", "Allocations (name / block / count)");
	XtVaSetValues(p->allocation_head, XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, p->bar,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
	p->allocations = scroll_list(p, split, "planAllocations");
	p->allocation_scroll = XtParent(p->allocations);
	XtVaSetValues(p->allocation_scroll, XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, p->allocation_head,
	    XmNbottomAttachment, XmATTACH_FORM, XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
	XtAddCallback(p->requests, XmNbrowseSelectionCallback, list_selected, p);
	XtAddCallback(p->allocations, XmNbrowseSelectionCallback, list_selected, p);
	XtAddCallback(p->outline, XmNselectionCallback, tree_selected, p);
	XtAddCallback(p->bar, XmNexposeCallback, draw_bar, p);
	XtAddCallback(p->bar, XmNresizeCallback, draw_bar, p);
	XtAddCallback(p->donut, XmNexposeCallback, draw_donut, p);
	XtAddCallback(p->donut, XmNresizeCallback, draw_donut, p);
	XtAddEventHandler(p->bar, ButtonPressMask | KeyPressMask, False, bar_input, p);
}

Widget
calc_plan_page(struct calc *calc, Widget notebook)
{
	static const char *const ids[] = { "planAdd", "planUpdate", "planRemove", "planUp", "planDown" };
	struct plan_page *p = calloc(1, sizeof(*p));
	struct calc_plan_error error;
	Widget head, edit, controls, capacity;

	if (p == NULL)
		return NULL;
	p->model = calloc(1, sizeof(*p->model));
	p->draft = calloc(1, sizeof(*p->draft));
	if (p->model == NULL || p->draft == NULL) {
		free(p->model);
		free(p->draft);
		free(p);
		return NULL;
	}
	p->calc = calc;
	p->selected = SIZE_MAX;
	p->model->parent = calc->context;
	p->model->policy = context_policy(calc);
	if (calc_plan_compute(p->model, &error) != D32_NET_OK) {
		free(p->model);
		free(p->draft);
		free(p);
		return NULL;
	}
	p->scope = p->model->parent;
	p->page = XtVaCreateWidget("planPage", xmFormWidgetClass, notebook,
	    XmNmarginWidth, 6, XmNmarginHeight, 5, NULL);
	calc->plan = p;
	XtAddCallback(p->page, XmNdestroyCallback, destroy_page, p);
	head = row(p->page, "planPolicyRow", NULL);
	p->policy = option(head, "planHostPolicy", "Host policy", calc_plan_policy_labels, p->policies);
	for (size_t i = 0; i < XtNumber(p->policies); i++)
		XtAddCallback(p->policies[i], XmNactivateCallback, policy_changed, p);
	policy_menu(p);
	edit = row(p->page, "planEditor", head);
	(void)label(edit, "planNameLabel", "Name");
	p->name = input(p, edit, "planName", 9, CALC_PLAN_NAME_MAX, true);
	p->kind = option(edit, "planKind", "Kind", calc_plan_kind_labels, p->kinds);
	XtVaSetValues(p->kind, XmNmenuHistory, p->kinds[D32_PLAN_HOSTS], NULL);
	(void)label(edit, "planValueLabel", "Value");
	p->value = input(p, edit, "planValue", 11, CALC_PLAN_VALUE_MAX, true);
	XtAddCallback(p->name, XmNactivateCallback, name_activate, p);
	XtAddCallback(p->value, XmNactivateCallback, edit_row, p);
	controls = row(p->page, "planEdits", edit);
	for (size_t i = 0; i < XtNumber(p->buttons); i++)
		p->buttons[i] = button(controls, ids[i], edit_names[i], edit_row, p);
	p->zoom = button(controls, "planZoom", "Zoom branch", zoom, p);
	p->unzoom = button(controls, "planResetZoom", "Full plan", zoom, p);
	capacity = row(p->page, "planCapacity", controls);
	(void)label(capacity, "planFreeLabel", "Free addresses");
	p->free_value = input(p, capacity, "planFree", 14, D32_COUNT_TEXT_CAP, false);
	(void)label(capacity, "planLargestLabel", "Largest block");
	p->largest = input(p, capacity, "planLargest", 16, D32_ADDR_TEXT_CAP + 4, false);
	make_views(p, capacity);
	show_donut(p);
	render(p);
	return p->page;
}
