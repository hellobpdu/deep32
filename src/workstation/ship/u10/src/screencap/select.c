/* Root-coordinate window selection and rubber-band input. */
#define _POSIX_C_SOURCE 200809L

#include <X11/Xatom.h>
#include <X11/Xlib.h>
#include <X11/cursorfont.h>
#include <X11/keysym.h>

#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <time.h>

#include "screencap.h"

#define SELECTION_SECONDS 30
#define STOP_POLL_MS 100
#define CLIENT_TREE_DEPTH 32
#define CLIENT_TREE_NODES 4096

struct rubber_band {
	GC gc;
	XFontStruct *font;
	int screen_width;
	int screen_height;
	int start_x;
	int start_y;
	int end_x;
	int end_y;
	int drawn;
};

static int
has_property(Display *display, Window window, Atom atom, Atom type, int format)
{
	Atom actual_type;
	int actual_format, status;
	unsigned long count, remaining;
	unsigned char *value = NULL;

	if (atom == None)
		return 0;
	status = XGetWindowProperty(display, window, atom, 0, 1, False, type,
	    &actual_type, &actual_format, &count, &remaining, &value);
	if (value != NULL)
		XFree(value);
	if (status != Success)
		return -1;
	return actual_type == type && actual_format == format && count > 0;
}

static enum sc_result
find_client(Display *display, Window window, Atom wm_state, unsigned depth,
    unsigned *budget, Window *client)
{
	XWindowAttributes attr;
	Window root, parent, *children = NULL;
	unsigned count, i;
	int has_state;
	enum sc_result result = SC_OK;

	if (depth > CLIENT_TREE_DEPTH || *budget == 0)
		return SC_LIMIT;
	--*budget;
	if (!XGetWindowAttributes(display, window, &attr))
		return SC_WINDOW_CLOSED;
	if (attr.map_state != IsViewable || attr.class != InputOutput)
		return SC_OK;
	has_state = has_property(display, window, wm_state, wm_state, 32);
	if (has_state < 0)
		return SC_WINDOW_CLOSED;
	if (has_state) {
		if (*client != None)
			return SC_ERROR;
		*client = window;
		return SC_OK;
	}
	if (!XQueryTree(display, window, &root, &parent, &children, &count))
		return SC_WINDOW_CLOSED;
	if (count > *budget) {
		result = SC_LIMIT;
	} else {
		for (i = 0; i < count; i++) {
			result = find_client(display, children[i], wm_state, depth + 1,
			    budget, client);
			if (result != SC_OK)
				break;
		}
	}
	if (children != NULL)
		XFree(children);
	return result;
}

enum sc_result
sc_window_rect(Display *display, Window root, Window target, struct sc_rect *rect)
{
	XWindowAttributes attr;
	Window child;
	int x, y;

	if (target == None || target == root ||
	    !XGetWindowAttributes(display, target, &attr) || attr.root != root ||
	    attr.map_state != IsViewable || attr.class != InputOutput ||
	    !XTranslateCoordinates(display, target, root, 0, 0, &x, &y, &child))
		return SC_WINDOW_CLOSED;
	if (attr.width <= 0 || attr.height <= 0 || attr.border_width < 0)
		return SC_WINDOW_CLOSED;
	/* These attributes are CARD16 and the translated positions are INT16. */
	*rect = (struct sc_rect){x - attr.border_width, y - attr.border_width,
	    (unsigned)attr.width + 2u * (unsigned)attr.border_width,
	    (unsigned)attr.height + 2u * (unsigned)attr.border_width};
	return SC_OK;
}

static enum sc_result
pick_window(Display *display, Window root, Window frame, int include_frame,
    struct sc_rect *rect, Window *target)
{
	Window client = None;
	unsigned budget = CLIENT_TREE_NODES;
	int bare_client;
	enum sc_result result;

	if (frame == None)
		return SC_CANCELLED;
	if (!include_frame) {
		result = find_client(display, frame, XInternAtom(display, "WM_STATE", True),
		    0, &budget, &client);
		if (result != SC_OK)
			return result;
		if (client == None) {
			/* A directly mapped client has no WM_STATE without a WM. */
			bare_client = has_property(display, frame, XA_WM_CLASS,
			    XA_STRING, 8);
			if (bare_client <= 0)
				return bare_client < 0 ? SC_WINDOW_CLOSED : SC_CANCELLED;
			client = frame;
		}
	} else {
		client = frame;
	}
	result = sc_window_rect(display, root, client, rect);
	if (result == SC_OK)
		*target = client;
	return result;
}

static struct sc_rect
drag_rect(int x0, int y0, int x1, int y1)
{
	return (struct sc_rect){x0 < x1 ? x0 : x1, y0 < y1 ? y0 : y1,
	    (unsigned)(x0 < x1 ? x1 - x0 : x0 - x1),
	    (unsigned)(y0 < y1 ? y1 - y0 : y0 - y1)};
}

static void
toggle_band(Display *display, Window root, const struct rubber_band *band)
{
	struct sc_rect rect;
	char dimensions[40];
	int length, text_width, x, y;

	rect = drag_rect(band->start_x, band->start_y, band->end_x, band->end_y);
	XDrawRectangle(display, root, band->gc, rect.x, rect.y, rect.width,
	    rect.height);
	length = snprintf(dimensions, sizeof(dimensions), "%u x %u", rect.width,
	    rect.height);
	if (length < 1 || (size_t)length >= sizeof(dimensions))
		return;
	text_width = XTextWidth(band->font, dimensions, length);
	x = band->end_x + 12;
	y = band->end_y + band->font->ascent + 8;
	if (x + text_width > band->screen_width)
		x = band->end_x - text_width - 12;
	if (y + band->font->descent > band->screen_height)
		y = band->end_y - band->font->descent - 8;
	if (x < 0)
		x = 0;
	if (y < band->font->ascent)
		y = band->font->ascent;
	XDrawString(display, root, band->gc, x, y, dimensions, length);
}

static Bool
selection_event(Display *display, XEvent *event, XPointer argument)
{
	const Window *root = (const Window *)argument;

	(void)display;
	if (event->xany.window != *root || event->xany.send_event)
		return False;
	return event->type == KeyPress || event->type == ButtonPress ||
	    event->type == ButtonRelease || event->type == MotionNotify;
}

enum sc_result
sc_select(Display *display, Window root, enum sc_mode mode, int include_frame,
    const volatile sig_atomic_t *stop, struct sc_rect *rect, Window *target,
    int *picked_x, int *picked_y)
{
	struct rubber_band band = {0};
	XWindowAttributes attr;
	XGCValues values;
	struct timespec now, deadline;
	struct pollfd connection;
	Cursor cursor = None;
	XEvent event;
	int have_pointer = 0, have_keyboard = 0, dragging = 0, polled;
	enum sc_result result = SC_ERROR;

	*rect = (struct sc_rect){0};
	*target = None;
	*picked_x = *picked_y = 0;
	if (mode != SC_MODE_WINDOW && mode != SC_MODE_RECTANGLE)
		return SC_ERROR;
	if (*stop)
		return SC_CANCELLED;
	if (clock_gettime(CLOCK_MONOTONIC, &deadline) == -1 ||
	    !XGetWindowAttributes(display, root, &attr))
		return SC_ERROR;
	deadline.tv_sec += SELECTION_SECONDS;
	if (mode == SC_MODE_RECTANGLE) {
		band.font = XLoadQueryFont(display, "fixed");
		if (band.font == NULL)
			goto done;
		band.screen_width = attr.width;
		band.screen_height = attr.height;
		values.function = GXxor;
		values.foreground = BlackPixelOfScreen(attr.screen) ^
		    WhitePixelOfScreen(attr.screen);
		values.subwindow_mode = IncludeInferiors;
		values.font = band.font->fid;
		band.gc = XCreateGC(display, root,
		    GCFunction | GCForeground | GCSubwindowMode | GCFont, &values);
		if (band.gc == NULL)
			goto done;
	}
	cursor = XCreateFontCursor(display, XC_crosshair);
	if (cursor == None)
		goto done;
	if (XGrabPointer(display, root, False,
	    ButtonPressMask | ButtonReleaseMask | PointerMotionMask,
	    GrabModeAsync, GrabModeAsync, root, cursor, CurrentTime) != GrabSuccess)
		goto done;
	have_pointer = 1;
	if (XGrabKeyboard(display, root, False, GrabModeAsync, GrabModeAsync,
	    CurrentTime) != GrabSuccess)
		goto done;
	have_keyboard = 1;
	connection.fd = ConnectionNumber(display);
	connection.events = POLLIN;
	for (;;) {
		if (clock_gettime(CLOCK_MONOTONIC, &now) == -1)
			break;
		if (*stop || now.tv_sec > deadline.tv_sec ||
		    (now.tv_sec == deadline.tv_sec && now.tv_nsec >= deadline.tv_nsec)) {
			result = SC_CANCELLED;
			break;
		}
		if (!XCheckIfEvent(display, &event, selection_event, (XPointer)&root)) {
			polled = poll(&connection, 1, STOP_POLL_MS);
			if ((polled == -1 && errno != EINTR) ||
			    (polled > 0 && (connection.revents & (POLLERR | POLLHUP | POLLNVAL))))
				break;
			continue;
		}
		if (event.type == KeyPress && XLookupKeysym(&event.xkey, 0) == XK_Escape) {
			result = SC_CANCELLED;
			break;
		}
		if (event.type == ButtonPress && event.xbutton.button == Button1 &&
		    event.xbutton.same_screen && !dragging) {
			dragging = 1;
			if (mode == SC_MODE_RECTANGLE) {
				band.start_x = band.end_x = event.xbutton.x_root;
				band.start_y = band.end_y = event.xbutton.y_root;
				toggle_band(display, root, &band);
				band.drawn = 1;
			}
		}
		if (event.type == MotionNotify && event.xmotion.same_screen && band.drawn) {
			toggle_band(display, root, &band);
			band.end_x = event.xmotion.x_root;
			band.end_y = event.xmotion.y_root;
			toggle_band(display, root, &band);
		}
		if (event.type == ButtonRelease && event.xbutton.button == Button1 && dragging) {
			if (!event.xbutton.same_screen) {
				result = SC_CANCELLED;
				break;
			}
			*picked_x = event.xbutton.x_root;
			*picked_y = event.xbutton.y_root;
			if (mode == SC_MODE_WINDOW) {
				result = pick_window(display, root, event.xbutton.subwindow,
				    include_frame, rect, target);
			} else {
				*rect = drag_rect(band.start_x, band.start_y, *picked_x, *picked_y);
				result = rect->width && rect->height ? SC_OK : SC_CANCELLED;
			}
			break;
		}
	}

done:
	if (band.drawn)
		toggle_band(display, root, &band);
	if (have_keyboard)
		XUngrabKeyboard(display, CurrentTime);
	if (have_pointer)
		XUngrabPointer(display, CurrentTime);
	if (cursor != None)
		XFreeCursor(display, cursor);
	if (band.gc != NULL)
		XFreeGC(display, band.gc);
	if (band.font != NULL)
		XFreeFont(display, band.font);
	XSync(display, False);
	return *stop ? SC_CANCELLED : result;
}
