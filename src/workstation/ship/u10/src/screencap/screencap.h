#ifndef D32_SCREENCAP_H
#define D32_SCREENCAP_H

#include <X11/Xlib.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

#define SC_BUFFER_LIMIT ((size_t)256 * 1024 * 1024)
#define SC_DIMENSION_LIMIT 16384U
#define SC_MONITOR_LIMIT 64U
#define SC_POLICY_PATH "/etc/d32/capture-private-classes"

enum sc_mode { SC_MODE_WINDOW, SC_MODE_RECTANGLE, SC_MODE_MONITOR, SC_MODE_ALL };
enum sc_result {
	SC_OK, SC_CANCELLED, SC_POLICY_UNAVAILABLE, SC_RESTRICTED, SC_UNSUPPORTED,
	SC_WINDOW_CLOSED, SC_LIMIT, SC_ERROR
};

struct sc_rect {
	int x, y;
	unsigned width, height;
};

struct sc_monitor_list {
	struct sc_rect items[SC_MONITOR_LIMIT];
	size_t count;
};

struct sc_image {
	unsigned width, height;
	unsigned char *rgb;
};

struct sc_policy {
	pid_t wm_pid;
	uint64_t wm_started_sec;
	uint32_t wm_started_usec;
};

void sc_policy_init(Display *, struct sc_policy *);
enum sc_result sc_policy_check(Display *, const struct sc_policy *,
    const char *, char *, size_t);
enum sc_result sc_monitors(Display *, Window, struct sc_monitor_list *);
enum sc_result sc_bounds(const struct sc_monitor_list *, struct sc_rect *);
enum sc_result sc_grab(Display *, Window, const struct sc_rect *,
    const struct sc_monitor_list *, struct sc_image *);
enum sc_result sc_png(const struct sc_image *, unsigned char **, size_t *);
enum sc_result sc_png_fits(unsigned, unsigned);
enum sc_result sc_select(Display *, Window, enum sc_mode, int,
    const volatile sig_atomic_t *, struct sc_rect *, Window *, int *, int *);
enum sc_result sc_window_rect(Display *, Window, Window, struct sc_rect *);

#endif
