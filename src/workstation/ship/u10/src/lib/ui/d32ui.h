#ifndef D32UI_H
#define D32UI_H

#include <stdbool.h>
#include <stddef.h>
#include <sys/types.h>

#include <Xm/Xm.h>

#include "d32_error.h"

typedef struct d32ui d32ui;
typedef struct d32ui_job d32ui_job;

enum d32ui_handling { D32UI_RESTRICTED, D32UI_PUBLIC };

d32ui *d32ui_init(int *, char **, const char *app_class);
Widget d32ui_shell(const d32ui *);
Widget d32ui_body(const d32ui *);
XtAppContext d32ui_context(const d32ui *);
XmFontList d32ui_mono(const d32ui *);

/* Pass the app's preferred size and T00's front-panel height in pixels. */
void d32ui_geometry(d32ui *, unsigned width, unsigned height, unsigned front_panel_px);
/* The default is restricted. Choose public before realizing any shell. */
enum d32_error d32ui_set_handling(d32ui *, enum d32ui_handling);
/* Realize a still-unmapped dialog shell and tag it before showing it. */
void d32ui_tag_dialog(d32ui *, Widget);
/* A valid UTF-8 status of at most 4095 bytes. State labels include a glyph. */
void d32ui_status(d32ui *, const char *);
void d32ui_on_close(d32ui *, XtCallbackProc, XtPointer);
void d32ui_show(d32ui *);
void d32ui_run(d32ui *);
void d32ui_quit(d32ui *);
void d32ui_destroy(d32ui *);

enum d32ui_action {
	D32UI_OPEN, D32UI_SAVE, D32UI_EXIT, D32UI_COPY, D32UI_FIND,
	D32UI_REFRESH, D32UI_PREV_TAB, D32UI_NEXT_TAB, D32UI_HELP
};

struct d32ui_menu_item {
	enum d32ui_action action;
	XtCallbackProc callback;
	XtPointer data;
};

struct d32ui_bar {
	const char *label;
	unsigned value;
};

Widget d32ui_menu(d32ui *, Widget, const struct d32ui_menu_item *, size_t);
Widget d32ui_chart(d32ui *, Widget, const struct d32ui_bar *, size_t);

typedef void (*d32ui_choice_fn)(bool, void *);
typedef void (*d32ui_cancel_fn)(void *);
typedef void (*d32ui_path_fn)(const char *, void *);

Widget d32ui_error(d32ui *, Widget opener, enum d32_error, const char *detail_token);
Widget d32ui_confirm(d32ui *, Widget opener, const char *, d32ui_choice_fn, void *);
/* Progress owns its XmNuserData. Close after the cancellation is reaped. */
Widget d32ui_progress(d32ui *, Widget opener, const char *, d32ui_cancel_fn, void *);
void d32ui_progress_close(Widget);
/* The callback borrows a pathname. The caller revalidates it before use. */
Widget d32ui_file_select(d32ui *, Widget opener, const char *pattern, d32ui_path_fn, void *);
Widget d32ui_help(d32ui *, Widget opener, const char *volume, const char *location, const char *fallback);

typedef void (*d32ui_job_data_fn)(const unsigned char *, size_t, void *);
typedef void (*d32ui_job_done_fn)(int wait_status, void *);

/*
 * Watch a direct child. On success the job owns fd and reaps pid; the caller
 * retains both on failure. The data callback borrows bytes until it returns
 * and must return promptly. Done receives waitpid status, or -1 on failure.
 * Keep the app context alive through done, when the job handle expires.
 */
d32ui_job *d32ui_job_watch(d32ui *, pid_t, int, d32ui_job_data_fn, d32ui_job_done_fn, void *);
void d32ui_job_cancel(d32ui_job *);

#endif
