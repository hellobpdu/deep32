#include "sound.h"

#include <X11/Xatom.h>
#include <X11/XKBlib.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <sysexits.h>
#include <unistd.h>

enum { MAX_VOICES = 2, FLASH_MS = 100, EXIT_MS = 1500, PLAY_MS = 3000 };

struct voice {
	pid_t pid;
	enum snd_event event;
	uint64_t deadline;
};

static struct {
	Display *display;
	Window root, status;
	Atom status_atom, utf8_atom, bell_atom;
	GC flash_gc;
	int xkb_event, dir, lock, sigpipe[2];
	bool bell_saved, published, cleaning;
	unsigned original_bell;
	unsigned long bells, played, dropped, unavailable, visual;
	uint64_t flash_until;
	enum snd_profile profile;
	struct snd_assets assets;
	struct snd_rate rate;
	struct voice voices[MAX_VOICES];
} app = {
	.dir = -1, .lock = -1, .sigpipe = { -1, -1 }, .assets = { .fd = -1 }
};

static volatile sig_atomic_t reload, quitting, logout_requested;
static const char *verb = "start";
static int manager_error;

static int
reported(int code, const char *name, const char *detail)
{
	const char *allowed[] = { detail };

	(void)d32_log_allowed("d32-sndd", allowed, 1,
	    "verb=%s caller_uid=%d target=%s result=%s bytes=%d ms=%d detail=%s",
	    verb, (int)getuid(), "local", name, 0, 0, detail);
	fprintf(stderr, "d32: tool=d32sndd verb=%s result=%s detail=%s\n", verb, name, detail);
	return code;
}

static void
signal_wakeup(int sig)
{
	int saved = errno;
	char wake = 0;

	if (sig == SIGHUP)
		reload = 1;
	else if (sig != SIGCHLD) {
		quitting = 1;
		if (sig == SIGTERM)
			logout_requested = 1;
	}
	(void)write(app.sigpipe[1], &wake, 1);
	errno = saved;
}

static int
signals(void)
{
	struct sigaction sa = { .sa_handler = signal_wakeup };
	static const int sigs[] = { SIGHUP, SIGTERM, SIGINT, SIGQUIT, SIGCHLD };
	size_t i;

	if (pipe2(app.sigpipe, O_NONBLOCK | O_CLOEXEC) == -1)
		return -1;
	sigemptyset(&sa.sa_mask);
	for (i = 0; i < sizeof(sigs) / sizeof(sigs[0]); i++)
		if (sigaction(sigs[i], &sa, NULL) == -1)
			return -1;
	return 0;
}

static void
status(void)
{
	char text[256];

	snprintf(text, sizeof(text), "profile=%s bells=%lu played=%lu dropped=%lu unavailable=%lu visual=%lu",
	    snd_profile_names[app.profile], app.bells, app.played, app.dropped, app.unavailable, app.visual);
	XChangeProperty(app.display, app.status, app.status_atom, app.utf8_atom, 8,
	    PropModeReplace, (const unsigned char *)text, (int)strlen(text));
	XFlush(app.display);
}

static void
flash_toggle(void)
{
	XFillRectangle(app.display, app.root, app.flash_gc, 8, 8, 96, 24);
	XFlush(app.display);
}

static void
flash(uint64_t now)
{
	if (app.flash_until == 0) {
		flash_toggle();
		app.flash_until = now + FLASH_MS;
		app.visual++;
	}
}

static void
cleanup(void)
{
	size_t i;

	app.cleaning = true;
	for (i = 0; i < MAX_VOICES; i++)
		snd_player_stop(&app.voices[i].pid);
	if (app.published)
		snd_pid_clear(app.dir);
	if (app.display != NULL) {
		if (app.flash_until != 0)
			flash_toggle();
		if (app.bell_saved && XkbChangeEnabledControls(app.display, XkbUseCoreKbd,
		    XkbAudibleBellMask, app.original_bell)) {
			XSync(app.display, False);
			XDeleteProperty(app.display, RootWindow(app.display, 0), app.bell_atom);
		}
		XCloseDisplay(app.display);
	}
	snd_assets_close(&app.assets);
}

static int
x_lost(Display *display)
{
	(void)display;
	app.display = NULL;
	if (app.cleaning)
		_exit(EX_UNAVAILABLE);
	exit(reported(EX_UNAVAILABLE, "unavailable", "display"));
}

static int
save_bell(void)
{
	Atom type;
	int format;
	unsigned long count, extra, baseline = app.original_bell != 0;
	unsigned char *value = NULL;
	Window root = RootWindow(app.display, 0);

	/* setup_x runs after this process owns the per-display lock. The root
	 * property survives a killed client and disappears with the X server. */
	app.bell_atom = XInternAtom(app.display, "_D32_SOUND_BELL_BASELINE", False);
	if (XGetWindowProperty(app.display, root, app.bell_atom, 0, 1, False,
	    XA_CARDINAL, &type, &format, &count, &extra, &value) != Success)
		return -1;
	if (type == XA_CARDINAL && format == 32 && count == 1 && extra == 0 &&
	    value && *(unsigned long *)value <= 1)
		baseline = *(unsigned long *)value;
	if (value)
		XFree(value);
	app.original_bell = baseline ? XkbAudibleBellMask : 0;
	XChangeProperty(app.display, root, app.bell_atom, XA_CARDINAL, 32,
	    PropModeReplace, (unsigned char *)&baseline, 1);
	XSync(app.display, False);
	app.bell_saved = true;
	return 0;
}

static int
setup_x(void)
{
	XkbDescPtr xkb;
	XGCValues gc = { .function = GXinvert, .subwindow_mode = IncludeInferiors };
	int opcode, error, major = XkbMajorVersion, minor = XkbMinorVersion;

	if (!snd_local_display(getenv("DISPLAY")) || (app.display = XOpenDisplay(NULL)) == NULL)
		return -1;
	XSetIOErrorHandler(x_lost);
	if (!XkbQueryExtension(app.display, &opcode, &app.xkb_event, &error, &major, &minor) ||
	    (xkb = XkbAllocKeyboard()) == NULL)
		return -1;
	xkb->device_spec = XkbUseCoreKbd;
	if (XkbGetControls(app.display, XkbControlsEnabledMask, xkb) != Success || xkb->ctrls == NULL) {
		XkbFreeKeyboard(xkb, 0, True);
		return -1;
	}
	app.original_bell = xkb->ctrls->enabled_ctrls & XkbAudibleBellMask;
	XkbFreeKeyboard(xkb, 0, True);
	app.root = DefaultRootWindow(app.display);
	app.status = XCreateSimpleWindow(app.display, app.root, 0, 0, 1, 1, 0, 0, 0);
	XStoreName(app.display, app.status, "d32sndd status");
	app.status_atom = XInternAtom(app.display, "_D32_STATUS", False);
	app.utf8_atom = XInternAtom(app.display, "UTF8_STRING", False);
	app.flash_gc = XCreateGC(app.display, app.root, GCFunction | GCSubwindowMode, &gc);
	if (!XkbSelectEvents(app.display, XkbUseCoreKbd, XkbBellNotifyMask, XkbBellNotifyMask) ||
	    save_bell() == -1 ||
	    !XkbChangeEnabledControls(app.display, XkbUseCoreKbd, XkbAudibleBellMask, 0))
		return -1;
	XSync(app.display, False);
	return 0;
}

static void
reload_profile(void)
{
	struct d32_prefs *prefs = NULL;
	enum d32_error error;
	size_t i;

	error = d32_prefs_load(app.dir, "d32sound", 1, snd_prefs_bad, "d32-sndd", &prefs);
	if (error == D32E_OK || error == D32E_NEWER || error == D32E_VERSION)
		app.profile = snd_profile_get(prefs);
	d32_prefs_free(prefs);
	for (i = 0; i < MAX_VOICES; i++)
		if (app.voices[i].pid > 0 && !snd_enabled(app.profile, app.voices[i].event))
			snd_player_stop(&app.voices[i].pid);
}

static void
play_failed(enum snd_event event, uint64_t now)
{
	app.unavailable++;
	if (event == SND_BELL)
		flash(now);
}

static void
play(enum snd_event event, uint64_t now, unsigned limit)
{
	struct voice *voice = NULL;
	size_t i;

	if (!snd_enabled(app.profile, event)) {
		if (event == SND_BELL)
			flash(now);
		return;
	}
	for (i = 0; i < MAX_VOICES; i++)
		if (app.voices[i].pid <= 0) {
			voice = &app.voices[i];
			break;
		}
	if (voice == NULL) {
		app.dropped++;
		return;
	}
	if (snd_player_start(snd_sample_open(&app.assets, event), &voice->pid) != D32E_OK) {
		play_failed(event, now);
		return;
	}
	voice->event = event;
	voice->deadline = now + limit;
	app.played++;
}

static void
reap(uint64_t now)
{
	int result;
	pid_t pid;
	size_t i;

	for (i = 0; i < MAX_VOICES; i++) {
		struct voice *v = &app.voices[i];
		if (v->pid <= 0)
			continue;
		pid = waitpid(v->pid, &result, WNOHANG);
		if (pid > 0) {
			v->pid = -1;
			if (!WIFEXITED(result) || WEXITSTATUS(result) != 0)
				play_failed(v->event, now);
		} else if (pid == -1 && errno == ECHILD) {
			v->pid = -1;
		} else if (now >= v->deadline) {
			snd_player_stop(&v->pid);
			play_failed(v->event, now);
		}
	}
}

static int
next_delay(uint64_t now)
{
	uint64_t next = app.flash_until;
	size_t i;

	for (i = 0; i < MAX_VOICES; i++)
		if (app.voices[i].pid > 0 && (next == 0 || app.voices[i].deadline < next))
			next = app.voices[i].deadline;
	return next == 0 ? -1 : next <= now ? 0 : (int)(next - now);
}

static void
bells(uint64_t now)
{
	XkbEvent event;
	unsigned i;

	for (i = 0; i < 64 && !quitting && XPending(app.display); i++) {
		XNextEvent(app.display, &event.core);
		if (event.type != app.xkb_event || event.any.xkb_type != XkbBellNotify ||
		    event.any.send_event || event.bell.event_only)
			continue;
		app.bells++;
		if (snd_rate_allow(&app.rate, now))
			play(SND_BELL, now, PLAY_MS);
		else
			app.dropped++;
	}
}

static void
session_loop(void)
{
	struct pollfd fds[2] = {
		{ .fd = ConnectionNumber(app.display), .events = POLLIN },
		{ .fd = app.sigpipe[0], .events = POLLIN }
	};
	uint64_t now;
	char pending[64];

	while (!quitting) {
		while (read(app.sigpipe[0], pending, sizeof(pending)) > 0)
			;
		if (reload) {
			reload = 0;
			reload_profile();
		}
		now = snd_now();
		if (app.flash_until && now >= app.flash_until) {
			flash_toggle();
			app.flash_until = 0;
		}
		reap(now);
		bells(now);
		status();
		if (quitting || XPending(app.display))
			continue;
		if (poll(fds, 2, next_delay(now)) == -1 && errno != EINTR)
			break;
	}
}

static void
session_end(void)
{
	struct pollfd fd = { .fd = app.sigpipe[0], .events = POLLIN };
	char pending[64];
	size_t i;

	for (i = 0; i < MAX_VOICES; i++)
		snd_player_stop(&app.voices[i].pid);
	play(SND_LOGOUT, snd_now(), EXIT_MS);
	while (app.voices[0].pid > 0) {
		(void)poll(&fd, 1, next_delay(snd_now()));
		while (read(fd.fd, pending, sizeof(pending)) > 0)
			;
		reap(snd_now());
	}
}

static int
manager_xerror(Display *display, XErrorEvent *error)
{
	(void)display;
	(void)error;
	manager_error = 1;
	return 0;
}

static Window
manager_window(Display *display, Atom info)
{
	Atom type;
	int format;
	unsigned long count, extra;
	unsigned char *value = NULL;
	Window window = None;

	/* CDE's two CARD32 fields are flags and the session manager window. */
	if (XGetWindowProperty(display, RootWindow(display, 0), info, 0, 2, False,
	    info, &type, &format, &count, &extra, &value) == Success &&
	    type == info && format == 32 && count == 2 && extra == 0 && value)
		window = ((unsigned long *)value)[1];
	if (value)
		XFree(value);
	return window;
}

static int
wait_manager(Display *display, Atom info, Window previous)
{
	uint64_t deadline = snd_now() + 15000;
	Window window;
	XEvent event;

	do {
		window = manager_window(display, info);
		if (window != None && window != previous)
			break;
		(void)poll(NULL, 0, 50);
	} while (snd_now() < deadline);
	if (window == None || window == previous)
		return EX_UNAVAILABLE;
	/* A manager that already disappeared after publishing is also done. */
	XSelectInput(display, window, StructureNotifyMask);
	XSync(display, False);
	if (!manager_error)
		do {
			XNextEvent(display, &event);
		} while (event.type != DestroyNotify || event.xdestroywindow.window != window);
	return EX_OK;
}

static int
session_manager(const char *previous)
{
	Display *display;
	Atom info;
	const char *error = NULL;
	Window window = None;
	int code = EX_OK;

	if (previous)
		window = (Window)strtonum(previous, 0, UINT32_MAX, &error);
	if (error)
		return EX_USAGE;
	if (!snd_local_display(getenv("DISPLAY")) ||
	    pledge("stdio rpath unix getpw", NULL) == -1 ||
	    (display = XOpenDisplay(NULL)) == NULL)
		return EX_UNAVAILABLE;
	XSetErrorHandler(manager_xerror);
	info = XInternAtom(display, "_DT_SM_WINDOW_INFO", False);
	if (previous)
		code = wait_manager(display, info, window);
	else
		printf("%lu\n", (unsigned long)manager_window(display, info));
	XCloseDisplay(display);
	return code;
}

int
main(int argc, char **argv)
{
	const char *pid_error = NULL;
	pid_t expected = 0;
	int i, fd;

	if (argc == 2 && strcmp(argv[1], "--session-window") == 0)
		return session_manager(NULL);
	if (argc == 3 && strcmp(argv[1], "--session-wait") == 0)
		return session_manager(argv[2]);
	if (argc > 3 || (argc > 1 && strcmp(argv[1], "--session-end") != 0))
		return reported(EX_USAGE, "usage", "arguments");
	if (argc == 3)
		expected = (pid_t)strtonum(argv[2], 2, INT_MAX, &pid_error);
	if (pid_error != NULL)
		return reported(EX_USAGE, "usage", "session-pid");
	if (argc > 1)
		verb = "stop";
	umask(077);
	if (!snd_local_display(getenv("DISPLAY")))
		return reported(EX_UNAVAILABLE, "unavailable", "local-display");
	/* The fixed base aucat child applies its own startup pledge. */
	if (pledge("stdio rpath wpath cpath fattr flock proc exec unix audio getpw", NULL) == -1)
		return reported(EX_UNAVAILABLE, "unavailable", "sandbox");
	app.dir = snd_prefs_dir();
	if (app.dir == -1)
		return reported(EX_NOPERM, "noperm", "settings-directory");
	if (argc > 1)
		return snd_signal(app.dir, SIGTERM, expected) == 0 ?
		    reported(EX_OK, "ok", "session-ended") :
		    reported(EX_UNAVAILABLE, "unavailable", "session-stop-incomplete");
	app.lock = snd_lock(app.dir);
	if (app.lock == -1) {
		if (errno == EAGAIN || errno == EACCES)
			return reported(EX_OK, "ok", "already-running");
		return reported(EX_NOPERM, "noperm", "session-lock");
	}
	if (atexit(cleanup) != 0 || signals() == -1)
		return reported(EX_UNAVAILABLE, "unavailable", "signal-setup");
	if (snd_pid_write(app.dir) == -1)
		return reported(EX_CANTCREAT, "cantcreat", "session-pid");
	app.published = true;
	if (setup_x() == -1)
		return reported(EX_UNAVAILABLE, "unavailable", "local-xkb");
	app.profile = SND_WORKSTATION;
	reload_profile();
	snd_assets_load(&app.assets);
	for (i = 0; i < SND_EVENTS; i++) {
		fd = snd_sample_open(&app.assets, i);
		if (fd != -1)
			close(fd);
	}
	if (!quitting)
		play(SND_LOGIN, snd_now(), PLAY_MS);
	session_loop();
	verb = "stop";
	if (logout_requested)
		session_end();
	return reported(EX_OK, "ok", "session-ended");
}
