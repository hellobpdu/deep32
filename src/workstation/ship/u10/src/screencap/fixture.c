/* Public synthetic window used by the native T60 Xvfb rehearsal only. */
#define _POSIX_C_SOURCE 200809L

#include <X11/Xatom.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>

#include <errno.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define FIXTURE_WIDTH 360U
#define FIXTURE_HEIGHT 240U
#define STRIPES 32U
#define CORNER 18U

static volatile sig_atomic_t stopped;

static void
stop_fixture(int signal_number)
{
	(void)signal_number;
	stopped = 1;
}

static unsigned long
channel(unsigned long mask, unsigned component)
{
	unsigned long unit;

	if (mask == 0)
		return 0;
	unit = mask & (0UL - mask);
	return (unsigned long)((uint64_t)(mask / unit) * component / 255) * unit;
}

static unsigned long
color(Display *display, int screen, unsigned r, unsigned g, unsigned b)
{
	Visual *visual = DefaultVisual(display, screen);

	if (visual->class != TrueColor)
		return r + g + b > 384 ? WhitePixel(display, screen) : BlackPixel(display, screen);
	return channel(visual->red_mask, r) | channel(visual->green_mask, g) |
	    channel(visual->blue_mask, b);
}

static void
paint(Display *display, int screen, Window window, GC gc)
{
	unsigned i, start, end;
	static const struct {
		int x, y;
		unsigned r, g, b;
	} corners[] = {
		{0, 0, 255, 0, 0},
		{FIXTURE_WIDTH - CORNER, 0, 0, 255, 0},
		{0, FIXTURE_HEIGHT - CORNER, 0, 0, 255},
		{FIXTURE_WIDTH - CORNER, FIXTURE_HEIGHT - CORNER, 255, 255, 0},
	};

	for (i = 0; i < STRIPES; i++) {
		start = i * FIXTURE_WIDTH / STRIPES;
		end = (i + 1) * FIXTURE_WIDTH / STRIPES;
		XSetForeground(display, gc, color(display, screen,
		    16 + 7 * i, screen == 0 ? 58 + 5 * i : 230 - 5 * i, 232 - 5 * i));
		XFillRectangle(display, window, gc, (int)start, 0, end - start, FIXTURE_HEIGHT);
	}
	for (i = 0; i < sizeof(corners) / sizeof(corners[0]); i++) {
		XSetForeground(display, gc, color(display, screen, corners[i].r,
		    corners[i].g, corners[i].b));
		XFillRectangle(display, window, gc, corners[i].x, corners[i].y, CORNER, CORNER);
	}
	XFlush(display);
}

static int
usage(void)
{
	fputs("usage: fixture [-screen 0|1] [-tag public|restricted|missing|malformed] "
	    "[-class NAME]\n", stderr);
	return 64;
}

int
main(int argc, char **argv)
{
	static char name[] = "d32t60Fixture";
	static char default_class[] = "D32T60Fixture";
	const char *tag = "public";
	char *class_name = default_class;
	Display *display;
	Window window;
	Atom handling;
	GC gc;
	XClassHint class_hint;
	XSizeHints size_hint = {0};
	XEvent event;
	struct sigaction action = {0};
	struct pollfd connection;
	unsigned long malformed = 0;
	int i, screen = -1, ready = 0, closed = 0, rc = 0;

	for (i = 1; i < argc; i += 2) {
		if (i + 1 == argc)
			return usage();
		if (strcmp(argv[i], "-screen") == 0) {
			if (strcmp(argv[i + 1], "0") != 0 && strcmp(argv[i + 1], "1") != 0)
				return usage();
			screen = argv[i + 1][0] - '0';
		} else if (strcmp(argv[i], "-tag") == 0) {
			tag = argv[i + 1];
			if (strcmp(tag, "public") != 0 && strcmp(tag, "restricted") != 0 &&
			    strcmp(tag, "missing") != 0 && strcmp(tag, "malformed") != 0)
				return usage();
		} else if (strcmp(argv[i], "-class") == 0) {
			class_name = argv[i + 1];
			if (*class_name == '\0' || strlen(class_name) > 63 ||
			    class_name[strspn(class_name,
			    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-")] != '\0')
				return usage();
		} else {
			return usage();
		}
	}
	action.sa_handler = stop_fixture;
	if (sigemptyset(&action.sa_mask) == -1 || sigaction(SIGTERM, &action, NULL) == -1 ||
	    sigaction(SIGINT, &action, NULL) == -1)
		return 1;
	display = XOpenDisplay(NULL);
	if (display == NULL)
		return 1;
	if (screen == -1)
		screen = DefaultScreen(display);
	if (screen >= ScreenCount(display)) {
		XCloseDisplay(display);
		return usage();
	}
	window = XCreateSimpleWindow(display, RootWindow(display, screen), 72, 52,
	    FIXTURE_WIDTH, FIXTURE_HEIGHT, 0, BlackPixel(display, screen),
	    BlackPixel(display, screen));
	gc = XCreateGC(display, window, 0, NULL);
	if (gc == NULL) {
		XCloseDisplay(display);
		return 1;
	}
	class_hint.res_name = name;
	class_hint.res_class = class_name;
	XSetClassHint(display, window, &class_hint);
	XStoreName(display, window, "T60 Synthetic Pattern");
	size_hint.flags = USPosition | USSize;
	size_hint.x = 72;
	size_hint.y = 52;
	size_hint.width = FIXTURE_WIDTH;
	size_hint.height = FIXTURE_HEIGHT;
	XSetWMNormalHints(display, window, &size_hint);
	handling = XInternAtom(display, "_D32_HANDLING", False);
	if (strcmp(tag, "malformed") == 0) {
		XChangeProperty(display, window, handling, XA_CARDINAL, 32,
		    PropModeReplace, (unsigned char *)&malformed, 1);
	} else if (strcmp(tag, "missing") != 0) {
		XChangeProperty(display, window, handling, XA_STRING, 8,
		    PropModeReplace, (const unsigned char *)tag, (int)strlen(tag));
	}
	XSelectInput(display, window, ExposureMask | StructureNotifyMask);
	XMapWindow(display, window);
	XFlush(display);
	connection.fd = ConnectionNumber(display);
	connection.events = POLLIN;
	while (!stopped && !closed) {
		if (XPending(display) == 0) {
			if (poll(&connection, 1, 100) == -1 && errno != EINTR) {
				rc = 1;
				break;
			}
			continue;
		}
		XNextEvent(display, &event);
		if (event.type == DestroyNotify) {
			closed = 1;
		} else if (event.type == MapNotify ||
		    (event.type == Expose && event.xexpose.count == 0)) {
			paint(display, screen, window, gc);
			if (!ready) {
				XSync(display, False);
				printf("0x%lx\n", window);
				fflush(stdout);
				ready = 1;
			}
		}
	}
	XFreeGC(display, gc);
	if (!closed)
		XDestroyWindow(display, window);
	XCloseDisplay(display);
	return rc;
}
