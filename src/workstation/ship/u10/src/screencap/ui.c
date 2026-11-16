#include <sys/socket.h>
#include <sys/stat.h>

#include <X11/Shell.h>
#include <X11/Xutil.h>
#include <Xm/DrawingA.h>
#include <Xm/Form.h>
#include <Xm/Label.h>
#include <Xm/Protocols.h>
#include <Xm/PushB.h>
#include <Xm/RowColumn.h>
#include <Xm/SelectioB.h>
#include <Xm/ToggleB.h>

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "d32ui.h"
#include "output.h"
#include "screencap.h"

struct app {
	d32ui *ui;
	Display *display;
	Atom instance, raise;
	Widget modes[4], delays[4], frame, destination, capture, picker;
	Widget preview, save, discard;
	XImage *thumbnail;
	struct sc_output output;
	struct sc_image image;
	struct sc_policy policy;
	enum sc_mode mode;
	int include_frame, busy, countdown;
	Window overlay;
	GC overlay_gc;
};

static volatile sig_atomic_t stop;
static int x_error;

static void
interrupt(int signo)
{
	(void)signo;
	stop = 1;
}

static int
capture_x_error(Display *display, XErrorEvent *event)
{
	(void)display;
	if (x_error == 0)
		x_error = event->error_code;
	return 0;
}

static void
label(Widget w, const char *text)
{
	XmString value = XmStringCreateLocalized((char *)text);

	XtVaSetValues(w, XmNlabelString, value, NULL);
	XmStringFree(value);
}

static void
status(struct app *a, const char *glyph, const char *text)
{
	char message[PATH_MAX + 160];

	snprintf(message, sizeof(message), "%s %s", glyph, text);
	d32ui_status(a->ui, message);
}

static void
show_main(struct app *a)
{
	a->busy = 0;
	if (!stop) {
		XtMapWidget(d32ui_shell(a->ui));
		(void)XmProcessTraversal(a->capture, XmTRAVERSE_CURRENT);
	}
}

static void
report_capture(struct app *a, enum sc_result result, const char *class_name)
{
	char message[256];

	switch (result) {
	case SC_CANCELLED:
		status(a, "[!]", "Capture cancelled");
		break;
	case SC_POLICY_UNAVAILABLE:
		status(a, "[x]", "Handling policy unavailable");
		break;
	case SC_RESTRICTED:
		if (*class_name == '\0')
			strlcpy(message, "Capture refused: a restricted window is on screen. Close or minimize it and try again.", sizeof(message));
		else
			snprintf(message, sizeof(message), "Capture refused: a restricted window is on screen (%s). Close or minimize it and try again.", class_name);
		status(a, "[x]", message);
		break;
	case SC_UNSUPPORTED:
		status(a, "[x]", "Unsupported display depth");
		break;
	case SC_WINDOW_CLOSED:
		status(a, "[x]", "Window closed during capture");
		break;
	case SC_LIMIT:
		status(a, "[x]", "Capture exceeds size limit");
		break;
	default:
		status(a, "[x]", "Screen capture unavailable");
		break;
	}
}

static Window
pointer_root(Display *dpy, int *x, int *y)
{
	Window actual, child, root;
	int local_x, local_y;
	unsigned keys;

	for (int i = 0; i < ScreenCount(dpy); i++) {
		root = RootWindow(dpy, i);
		if (XQueryPointer(dpy, root, &actual, &child, x, y, &local_x, &local_y, &keys))
			return root;
	}
	return None;
}

static const struct sc_rect *
monitor_at(const struct sc_monitor_list *list, int x, int y)
{
	for (size_t i = 0; i < list->count; i++) {
		const struct sc_rect *r = &list->items[i];
		if (x >= r->x && y >= r->y && (int64_t)x < (int64_t)r->x + r->width &&
		    (int64_t)y < (int64_t)r->y + r->height)
			return r;
	}
	return NULL;
}

static int
clip(struct sc_rect *r, const struct sc_rect *bounds)
{
	int64_t right = (int64_t)r->x + r->width;
	int64_t bottom = (int64_t)r->y + r->height;
	int64_t max_x = (int64_t)bounds->x + bounds->width;
	int64_t max_y = (int64_t)bounds->y + bounds->height;

	if (right > max_x)
		right = max_x;
	if (bottom > max_y)
		bottom = max_y;
	if (r->x < bounds->x)
		r->x = bounds->x;
	if (r->y < bounds->y)
		r->y = bounds->y;
	if (right <= r->x || bottom <= r->y)
		return 0;
	r->width = (unsigned)(right - r->x);
	r->height = (unsigned)(bottom - r->y);
	return 1;
}

static enum sc_result
check_target(Display *dpy, Window root, Window target, const struct sc_rect *selected)
{
	struct sc_rect current;
	enum sc_result result;

	if (target == None)
		return SC_OK;
	result = sc_window_rect(dpy, root, target, &current);
	if (result != SC_OK || current.x != selected->x || current.y != selected->y ||
	    current.width != selected->width || current.height != selected->height)
		return SC_WINDOW_CLOSED;
	return SC_OK;
}

static void
discard_image(struct sc_image *image)
{
	free(image->rgb);
	memset(image, 0, sizeof(*image));
}

static enum sc_result
capture_pixels(struct app *a, char *class_name, size_t class_size)
{
	Display *dpy = a->display;
	struct sc_monitor_list monitors;
	struct sc_rect selected = {0}, box = {0};
	const struct sc_rect *monitor;
	Window root, target = None;
	int x, y;
	XErrorHandler handler;
	enum sc_result result = SC_OK;

	root = pointer_root(dpy, &x, &y);
	if (root == None)
		return SC_ERROR;
	XSync(dpy, False);
	x_error = 0;
	handler = XSetErrorHandler(capture_x_error);
	if (a->mode == SC_MODE_WINDOW || a->mode == SC_MODE_RECTANGLE) {
		result = sc_select(dpy, root, a->mode, a->include_frame, &stop,
		    &selected, &target, &x, &y);
		box = selected;
	}
	if (result != SC_OK)
		goto restore;
	if (stop) {
		result = SC_CANCELLED;
		goto restore;
	}
	XGrabServer(dpy);
	result = sc_policy_check(dpy, &a->policy, SC_POLICY_PATH, class_name, class_size);
	if (result != SC_OK)
		goto ungrab;
	result = check_target(dpy, root, target, &selected);
	if (result != SC_OK)
		goto ungrab;
	if (a->mode != SC_MODE_RECTANGLE) {
		result = sc_monitors(dpy, root, &monitors);
		if (result != SC_OK)
			goto ungrab;
		if (a->mode == SC_MODE_ALL) {
			result = sc_bounds(&monitors, &box);
		} else {
			monitor = monitor_at(&monitors, x, y);
			if (monitor == NULL)
				result = SC_ERROR;
			else if (a->mode == SC_MODE_MONITOR)
				box = *monitor;
			else if (!clip(&box, monitor))
				result = SC_WINDOW_CLOSED;
		}
	}
	if (result == SC_OK)
		result = sc_png_fits(box.width, box.height);
	if (result == SC_OK && !stop && x_error == 0) {
		enum sc_result after;
		result = sc_grab(dpy, root, &box, a->mode == SC_MODE_ALL ? &monitors : NULL, &a->image);
		after = sc_policy_check(dpy, &a->policy, SC_POLICY_PATH, class_name, class_size);
		if (after != SC_OK)
			result = after;
		else if (result == SC_OK)
			result = check_target(dpy, root, target, &selected);
	}
ungrab:
	XUngrabServer(dpy);
restore:
	XSync(dpy, False);
	XSetErrorHandler(handler);
	if (stop)
		result = SC_CANCELLED;
	else if (x_error != 0 && result != SC_RESTRICTED && result != SC_POLICY_UNAVAILABLE)
		result = x_error == BadWindow || x_error == BadMatch ? SC_WINDOW_CLOSED : SC_ERROR;
	if (result != SC_OK && a->image.rgb != NULL) {
		explicit_bzero(a->image.rgb, (size_t)a->image.width * a->image.height * 3);
		discard_image(&a->image);
	}
	return result;
}

static unsigned long
pixel_channel(unsigned value, unsigned long mask)
{
	unsigned shift = 0;

	if (mask == 0)
		return 0;
	while ((mask & 1) == 0) {
		mask >>= 1;
		shift++;
	}
	return (unsigned long)(((uint64_t)value * mask + 127) / 255) << shift;
}

static XImage *
make_thumbnail(struct app *a)
{
	Display *dpy = a->display;
	int screen = DefaultScreen(dpy);
	unsigned w = a->image.width, h = a->image.height;
	unsigned max_w = (unsigned)DisplayWidth(dpy, screen) * 3 / 4;
	unsigned max_h = (unsigned)DisplayHeight(dpy, screen) * 3 / 5;
	XImage *image;

	if (max_w > 640)
		max_w = 640;
	if (max_h > 440)
		max_h = 440;
	if (max_w == 0 || max_h == 0 || DefaultVisual(dpy, screen)->class != TrueColor)
		return NULL;
	if (w > max_w) {
		h = h * max_w / w;
		w = max_w;
	}
	if (h > max_h) {
		w = w * max_h / h;
		h = max_h;
	}
	if (w == 0)
		w = 1;
	if (h == 0)
		h = 1;
	image = XCreateImage(dpy, DefaultVisual(dpy, screen), DefaultDepth(dpy, screen),
	    ZPixmap, 0, NULL, w, h, 32, 0);
	if (image == NULL)
		return NULL;
	image->data = calloc(h, (size_t)image->bytes_per_line);
	if (image->data == NULL) {
		XDestroyImage(image);
		return NULL;
	}
	for (unsigned y = 0; y < h; y++) {
		for (unsigned x = 0; x < w; x++) {
			size_t row = (size_t)y * a->image.height / h;
			size_t col = (size_t)x * a->image.width / w;
			const unsigned char *p = a->image.rgb + (row * a->image.width + col) * 3;
			unsigned long pixel = pixel_channel(p[0], image->red_mask) |
			    pixel_channel(p[1], image->green_mask) | pixel_channel(p[2], image->blue_mask);
			XPutPixel(image, x, y, pixel);
		}
	}
	return image;
}

static void
paint_preview(Widget area, XtPointer data, XtPointer call)
{
	struct app *a = data;
	GC gc;

	(void)call;
	if (a->thumbnail == NULL)
		return;
	gc = XCreateGC(a->display, XtWindow(area), 0, NULL);
	if (gc != NULL) {
		XPutImage(a->display, XtWindow(area), gc, a->thumbnail, 0, 0, 0, 0,
		    (unsigned)a->thumbnail->width, (unsigned)a->thumbnail->height);
		XFreeGC(a->display, gc);
	}
}

static void
destroy_preview(struct app *a)
{
	XtDestroyWidget(XtParent(a->preview));
	a->preview = NULL;
	if (a->thumbnail != NULL) {
		XDestroyImage(a->thumbnail);
		a->thumbnail = NULL;
	}
}

static void
discard_preview(Widget widget, XtPointer data, XtPointer call)
{
	struct app *a = data;

	(void)widget;
	(void)call;
	destroy_preview(a);
	discard_image(&a->image);
	status(a, "[!]", "Capture discarded");
	show_main(a);
}

static void
save_preview(Widget widget, XtPointer data, XtPointer call)
{
	struct app *a = data;
	unsigned char *png = NULL;
	size_t length;
	char path[PATH_MAX], message[PATH_MAX + 8];
	enum sc_result result;

	(void)widget;
	(void)call;
	if (stop)
		return;
	destroy_preview(a);
	XSync(a->display, False);
	result = sc_png(&a->image, &png, &length);
	if (result != SC_OK)
		report_capture(a, result, "");
	else if (stop || sc_output_save(&a->output, png, length, path) != D32E_OK)
		status(a, "[x]", "Unable to save capture");
	else {
		snprintf(message, sizeof(message), "Saved %s", path);
		status(a, "[OK]", message);
	}
	free(png);
	discard_image(&a->image);
	show_main(a);
}

static Widget
button(Widget parent, const char *name, const char *text, XtCallbackProc callback, struct app *a)
{
	Widget w = XtVaCreateManagedWidget(name, xmPushButtonWidgetClass, parent, NULL);

	label(w, text);
	XtAddCallback(w, XmNactivateCallback, callback, a);
	return w;
}

static int
show_preview(struct app *a)
{
	Widget column, area, dimensions, buttons, shell;
	Atom close_atom;
	char size[64];

	a->thumbnail = make_thumbnail(a);
	if (a->thumbnail == NULL)
		return 0;
	a->preview = XmCreateFormDialog(d32ui_shell(a->ui), "preview", NULL, 0);
	shell = XtParent(a->preview);
	XtVaSetValues(shell, XtNtitle, "Screen Capture Preview", XmNdeleteResponse, XmDO_NOTHING, NULL);
	column = XtVaCreateManagedWidget("previewColumn", xmRowColumnWidgetClass, a->preview,
	    XmNpacking, XmPACK_TIGHT, XmNtopAttachment, XmATTACH_FORM,
	    XmNbottomAttachment, XmATTACH_FORM, XmNleftAttachment, XmATTACH_FORM,
	    XmNrightAttachment, XmATTACH_FORM, NULL);
	area = XtVaCreateManagedWidget("image", xmDrawingAreaWidgetClass, column,
	    XmNwidth, (Dimension)a->thumbnail->width, XmNheight, (Dimension)a->thumbnail->height,
	    XmNresizePolicy, XmRESIZE_NONE, NULL);
	XtAddCallback(area, XmNexposeCallback, paint_preview, a);
	dimensions = XtVaCreateManagedWidget("dimensions", xmLabelWidgetClass, column, NULL);
	snprintf(size, sizeof(size), "%u x %u pixels", a->image.width, a->image.height);
	label(dimensions, size);
	buttons = XtVaCreateManagedWidget("previewButtons", xmRowColumnWidgetClass, column,
	    XmNorientation, XmHORIZONTAL, NULL);
	a->save = button(buttons, "save", "Save", save_preview, a);
	a->discard = button(buttons, "discard", "Discard", discard_preview, a);
	XtVaSetValues(a->preview, XmNdefaultButton, a->save, XmNcancelButton, a->discard, NULL);
	close_atom = XInternAtom(a->display, "WM_DELETE_WINDOW", False);
	XmAddWMProtocolCallback(shell, close_atom, discard_preview, a);
	d32ui_tag_dialog(a->ui, a->preview);
	XtManageChild(a->preview);
	(void)XmProcessTraversal(a->save, XmTRAVERSE_CURRENT);
	return 1;
}

static void
take_capture(struct app *a)
{
	char restricted[64] = "";
	enum sc_result result;

	if (a->mode == SC_MODE_ALL && ScreenCount(a->display) > 1) {
		status(a, "[x]", "Displays do not share a desktop");
		show_main(a);
		return;
	}
	result = capture_pixels(a, restricted, sizeof(restricted));
	if (result == SC_OK && show_preview(a))
		return;
	if (result == SC_OK)
		result = SC_ERROR;
	discard_image(&a->image);
	report_capture(a, result, restricted);
	show_main(a);
}

static void
hide_overlay(struct app *a)
{
	if (a->overlay != None) {
		XUnmapWindow(a->display, a->overlay);
		XFreeGC(a->display, a->overlay_gc);
		XDestroyWindow(a->display, a->overlay);
		a->overlay = None;
		XSync(a->display, False);
	}
}

static void
countdown_tick(XtPointer data, XtIntervalId *id)
{
	struct app *a = data;
	char text[48];

	(void)id;
	if (stop || a->countdown == 0) {
		hide_overlay(a);
		if (!stop)
			take_capture(a);
		return;
	}
	if (a->overlay != None) {
		XClearWindow(a->display, a->overlay);
		snprintf(text, sizeof(text), "Capture in %d s", a->countdown);
		XDrawString(a->display, a->overlay, a->overlay_gc, 12, 20, text, (int)strlen(text));
		XFlush(a->display);
	}
	a->countdown--;
	XtAppAddTimeOut(d32ui_context(a->ui), 1000, countdown_tick, a);
}

static void
start_countdown(XtPointer data, XtIntervalId *id)
{
	struct app *a = data;
	XSetWindowAttributes window = {0};
	XGCValues values = {0};
	Window root;
	int x, y;

	(void)id;
	if (stop)
		return;
	if (a->countdown > 0 && (root = pointer_root(a->display, &x, &y)) != None) {
		window.override_redirect = True;
		XtVaGetValues(a->capture, XmNforeground, &values.foreground,
		    XmNbackground, &window.background_pixel, NULL);
		a->overlay = XCreateWindow(a->display, root, 8, 8, 140, 30, 0,
		    CopyFromParent, InputOutput, CopyFromParent, CWOverrideRedirect | CWBackPixel, &window);
		a->overlay_gc = XCreateGC(a->display, a->overlay, GCForeground, &values);
		XMapRaised(a->display, a->overlay);
	}
	countdown_tick(a, NULL);
}

static void
begin_capture(Widget w, XtPointer data, XtPointer call)
{
	static const int seconds[] = {0, 3, 5, 10};
	struct app *a = data;

	(void)w;
	(void)call;
	if (a->busy || stop)
		return;
	for (size_t i = 0; i < XtNumber(a->modes); i++)
		if (XmToggleButtonGetState(a->modes[i]))
			a->mode = (enum sc_mode)i;
	for (size_t i = 0; i < XtNumber(a->delays); i++)
		if (XmToggleButtonGetState(a->delays[i]))
			a->countdown = seconds[i];
	a->include_frame = XmToggleButtonGetState(a->frame);
	a->busy = 1;
	XtUnmapWidget(d32ui_shell(a->ui));
	XSync(a->display, False);
	XtAppAddTimeOut(d32ui_context(a->ui), 150, start_countdown, a);
}

static void
pick_cancel(Widget w, XtPointer data, XtPointer call)
{
	struct app *a = data;

	(void)w;
	(void)call;
	XtUnmanageChild(a->picker);
}

static void
pick_destination(Widget w, XtPointer data, XtPointer call)
{
	struct app *a = data;
	XmSelectionBoxCallbackStruct *picked = call;
	char *path = NULL;

	(void)w;
	if (picked != NULL)
		(void)XmStringGetLtoR(picked->value, XmFONTLIST_DEFAULT_TAG, &path);
	if (path != NULL && sc_output_choose(&a->output, path) == D32E_OK) {
		label(a->destination, a->output.path);
		XtUnmanageChild(a->picker);
		status(a, "[OK]", "Destination selected");
	} else {
		XtUnmanageChild(a->picker);
		status(a, "[x]", "Choose a private directory inside ~/Captures");
	}
	if (path != NULL)
		XtFree(path);
}

static void
make_picker(struct app *a)
{
	XmString prompt = XmStringCreateLocalized("Directory under ~/Captures");
	a->picker = XmCreatePromptDialog(d32ui_shell(a->ui), "destinationPicker", NULL, 0);
	XtVaSetValues(XtParent(a->picker), XtNtitle, "Screen Capture Destination", NULL);
	XtVaSetValues(a->picker, XmNautoUnmanage, False, XmNselectionLabelString, prompt, NULL);
	XmStringFree(prompt);
	XtUnmanageChild(XmSelectionBoxGetChild(a->picker, XmDIALOG_HELP_BUTTON));
	XtAddCallback(a->picker, XmNokCallback, pick_destination, a);
	XtAddCallback(a->picker, XmNcancelCallback, pick_cancel, a);
	d32ui_tag_dialog(a->ui, a->picker);
}

static void
change_destination(Widget w, XtPointer data, XtPointer call)
{
	struct app *a = data;
	XmString dir;
	(void)w;
	(void)call;
	dir = XmStringCreateLocalized("");
	XtVaSetValues(a->picker, XmNtextString, dir, NULL);
	XmStringFree(dir);
	XtManageChild(a->picker);
	(void)XmProcessTraversal(XmSelectionBoxGetChild(a->picker, XmDIALOG_TEXT), XmTRAVERSE_CURRENT);
}

static void
mode_changed(Widget w, XtPointer data, XtPointer call)
{
	struct app *a = data;

	(void)w;
	(void)call;
	XtSetSensitive(a->frame, XmToggleButtonGetState(a->modes[SC_MODE_WINDOW]));
}

static void
close_app(Widget w, XtPointer data, XtPointer call)
{
	struct app *a = data;

	(void)w;
	(void)call;
	if (!a->busy)
		d32ui_quit(a->ui);
}

static Widget
row(Widget parent, const char *name, const char *caption)
{
	Widget group = XtVaCreateManagedWidget(name, xmRowColumnWidgetClass, parent,
	    XmNorientation, XmHORIZONTAL, XmNpacking, XmPACK_TIGHT, NULL);
	Widget title = XtVaCreateManagedWidget("caption", xmLabelWidgetClass, group, NULL);

	label(title, caption);
	return group;
}

static void
radios(Widget parent, const char *name, const char *const names[], Widget values[], size_t n)
{
	Widget box = XmCreateRadioBox(parent, (char *)name, NULL, 0);

	XtVaSetValues(box, XmNorientation, XmHORIZONTAL, XmNpacking, XmPACK_TIGHT, NULL);
	for (size_t i = 0; i < n; i++) {
		values[i] = XtVaCreateManagedWidget("option", xmToggleButtonWidgetClass, box, XmNset, i == 0, NULL);
		label(values[i], names[i]);
	}
	XtManageChild(box);
}

static void
make_form(struct app *a)
{
	static const char *const modes[] = {"Window", "Rectangle", "Current Monitor", "All Displays"};
	static const char *const delays[] = {"0 s", "3 s", "5 s", "10 s"};
	Widget form, column, choices, buttons;

	form = XtVaCreateManagedWidget("captureForm", xmFormWidgetClass, d32ui_body(a->ui),
	    XmNtopAttachment, XmATTACH_FORM, XmNbottomAttachment, XmATTACH_FORM,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
	column = XtVaCreateManagedWidget("options", xmRowColumnWidgetClass, form,
	    XmNtopAttachment, XmATTACH_FORM, XmNleftAttachment, XmATTACH_FORM,
	    XmNrightAttachment, XmATTACH_FORM, XmNmarginWidth, 16, XmNmarginHeight, 16,
	    XmNspacing, 8, NULL);
	radios(row(column, "mode", "Mode"), "modes", modes, a->modes, XtNumber(modes));
	radios(row(column, "delay", "Delay"), "delays", delays, a->delays, XtNumber(delays));
	a->frame = XtVaCreateManagedWidget("includeFrame", xmToggleButtonWidgetClass, column, XmNset, True, NULL);
	label(a->frame, "Include frame");
	for (size_t i = 0; i < XtNumber(a->modes); i++)
		XtAddCallback(a->modes[i], XmNvalueChangedCallback, mode_changed, a);
	choices = row(column, "destination", "Destination");
	a->destination = XtVaCreateManagedWidget("path", xmLabelWidgetClass, choices, NULL);
	label(a->destination, a->output.path);
	(void)button(choices, "change", "Change...", change_destination, a);
	buttons = row(column, "buttons", "");
	a->capture = button(buttons, "capture", "Capture", begin_capture, a);
	(void)button(buttons, "close", "Close", close_app, a);
	XtVaSetValues(form, XmNdefaultButton, a->capture, NULL);
	XtVaSetValues(d32ui_shell(a->ui), XtNtitle, "Screen Capture", NULL);
	d32ui_geometry(a->ui, 620, 420, 0);
	d32ui_on_close(a->ui, close_app, a);
}

static void
raise_event(Widget w, XtPointer data, XEvent *event, Boolean *dispatch)
{
	struct app *a = data;

	(void)dispatch;
	if (event->type != ClientMessage || event->xclient.message_type != a->raise)
		return;
	if (a->preview != NULL)
		XMapRaised(a->display, XtWindow(XtParent(a->preview)));
	else if (!a->busy)
		XMapRaised(a->display, XtWindow(w));
}

static void
raise_existing(struct app *a)
{
	Window window = XGetSelectionOwner(a->display, a->instance);
	XEvent event = {0};
	XErrorHandler handler;

	if (window == None)
		return;
	event.xclient.type = ClientMessage;
	event.xclient.window = window;
	event.xclient.message_type = a->raise;
	event.xclient.format = 32;
	handler = XSetErrorHandler(capture_x_error);
	XSendEvent(a->display, window, False, NoEventMask, &event);
	XSync(a->display, False);
	XSetErrorHandler(handler);
}

static void
check_stop(XtPointer data, XtIntervalId *id)
{
	struct app *a = data;

	(void)id;
	if (stop) {
		hide_overlay(a);
		d32ui_quit(a->ui);
	} else {
		XtAppAddTimeOut(d32ui_context(a->ui), 100, check_stop, a);
	}
}

static int
local_display(const char *display)
{
	size_t len;

	if (display == NULL)
		return 0;
	if (strncmp(display, "unix:", 5) == 0)
		display += 4;
	if (*display++ != ':')
		return 0;
	len = strspn(display, "0123456789");
	if (len == 0)
		return 0;
	display += len;
	if (*display == '.') {
		display++;
		len = strspn(display, "0123456789");
		return len > 0 && display[len] == '\0';
	}
	return *display == '\0';
}

static int
sandbox(struct app *a)
{
	struct stat st;
	int rc;

	rc = fstatat(a->output.home_fd, "Captures", &st, AT_SYMLINK_NOFOLLOW);
	if (rc == 0 && S_ISDIR(st.st_mode)) {
		if (unveil(a->output.base, "rwc") == -1)
			return 0;
	}
	rc = lstat(SC_POLICY_PATH, &st);
	if (((rc == 0 && S_ISREG(st.st_mode)) || (rc == -1 && errno == ENOENT)) &&
	    unveil(SC_POLICY_PATH, "r") == -1 && errno != ENOENT)
		return 0;
	return unveil(NULL, NULL) == 0 && pledge("stdio rpath wpath cpath flock unix ps", NULL) == 0;
}

int
main(int argc, char **argv)
{
	struct app a = {0};
	struct sigaction signals = {0};
	struct sockaddr_storage peer;
	socklen_t peer_size = sizeof(peer);
	enum d32_error lock;
	int result = 1;

	if (argc != 1 || getuid() == 0 || !local_display(getenv("DISPLAY"))) {
		fputs("d32-screencap: an unprivileged local X display is required\n", stderr);
		return 1;
	}
	umask(077);
	tzset();
	lock = sc_output_init(&a.output, getenv("HOME"));
	if (lock != D32E_OK && lock != D32E_EXISTS) {
		fputs("d32-screencap: private instance state unavailable\n", stderr);
		goto done;
	}
	a.ui = d32ui_init(&argc, argv, "D32ScreenCapture");
	if (a.ui == NULL)
		goto done;
	a.display = XtDisplay(d32ui_shell(a.ui));
	if (getpeername(ConnectionNumber(a.display), (struct sockaddr *)&peer, &peer_size) == -1 ||
	    peer.ss_family != AF_UNIX)
		goto done;
	a.instance = XInternAtom(a.display, "_D32_SCREENCAP_INSTANCE", False);
	a.raise = XInternAtom(a.display, "_D32_SCREENCAP_RAISE", False);
	if (lock == D32E_EXISTS) {
		raise_existing(&a);
		result = 0;
		goto done;
	}
	signals.sa_handler = interrupt;
	sigemptyset(&signals.sa_mask);
	if (sigaction(SIGTERM, &signals, NULL) == -1 || sigaction(SIGINT, &signals, NULL) == -1 ||
	    d32ui_set_handling(a.ui, D32UI_PUBLIC) != D32E_OK)
		goto done;
	make_form(&a);
	status(&a, "[OK]", "Ready");
	d32ui_show(a.ui);
	/* Realize Motif's text input before restricting the filesystem view. */
	make_picker(&a);
	XtAddEventHandler(d32ui_shell(a.ui), NoEventMask, True, raise_event, &a);
	XSetSelectionOwner(a.display, a.instance, XtWindow(d32ui_shell(a.ui)), CurrentTime);
	sc_policy_init(a.display, &a.policy);
	if (!sandbox(&a)) {
		fputs("d32-screencap: capture sandbox unavailable\n", stderr);
		goto done;
	}
	XtAppAddTimeOut(d32ui_context(a.ui), 100, check_stop, &a);
	d32ui_run(a.ui);
	result = 0;
done:
	if (a.preview != NULL)
		destroy_preview(&a);
	discard_image(&a.image);
	d32ui_destroy(a.ui);
	sc_output_close(&a.output);
	return result;
}
