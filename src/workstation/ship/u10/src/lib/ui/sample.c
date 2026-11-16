#include "d32ui.h"
#include "d32_io.h"

#include <X11/Shell.h>
#include <X11/StringDefs.h>
#include <Xm/Form.h>
#include <Xm/Label.h>
#include <Xm/List.h>
#include <Xm/MessageB.h>
#include <Xm/PushB.h>
#include <Xm/RowColumn.h>
#include <Xm/TextF.h>

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define COUNT(a) (sizeof(a) / sizeof((a)[0]))
#define SAMPLE_STEPS 40

struct sample {
	d32ui *ui;
	Widget page_label, list, find, run_button, reset_button, progress;
	d32ui_job *job;
	pid_t cleanup_pid;
	size_t page;
	bool cancelled, leaving;
};

static const char *const overview[] = {
	"01  Ready for local work", "02  File selection verifies regular files",
	"03  A short worker reports each synthetic row", "04  Cancel sends SIGTERM",
	"05  A closed worker is reaped", "06  Find selects the first matching row",
	"07  The chart distinguishes rows by pattern", "08  Reset has a safe default",
	"09  Help has a local fallback", "10  Exit waits for an active worker",
	"11  The status is mirrored to the window", "12  Text is synthetic",
	"13  The list scrolls with the viewport", "14  Controls follow reading order",
	"15  Files and dialogs can be opened from the keyboard", "16  Sample queue: Alpha",
	"17  Sample queue: Bravo", "18  Sample queue: Charlie",
	"19  Sample queue: Delta", "20  Sample queue: Echo",
	"21  Sample queue: Foxtrot", "22  Sample queue: Golf",
	"23  Sample queue: Hotel", "24  Sample queue: India",
	"25  Sample queue: Juliet", "26  Sample queue: Kilo",
	"27  Sample queue: Lima", "28  Sample queue: Mike",
	"29  Sample queue: November", "30  Sample queue: Oscar",
	"31  Sample queue: Papa", "32  Sample queue: Quebec",
	"33  Sample queue: Romeo", "34  Sample queue: Sierra",
	"35  Sample queue: Tango", "36  Sample queue: Uniform",
	"37  Sample queue: Victor", "38  Sample queue: Whiskey",
	"39  Sample queue: X-ray", "40  Sample queue: Yankee",
	"41  Sample queue: Zulu", "42  End of sample rows"
};

static const char *const shortcuts[] = {
	"Ctrl+O     Open a file", "Ctrl+Q     Exit", "Ctrl+F     Focus Find",
	"F1         Context help", "F5         Run the worker",
	"Ctrl+Page Up     Previous tab", "Ctrl+Page Down   Next tab",
	"Tab        Move between controls", "Escape     Cancel a dialog",
	"Return     Activate the default button"
};

static void
show_page(struct sample *s, size_t page)
{
	const char *const *rows = page == 0 ? overview : shortcuts;
	size_t n = page == 0 ? COUNT(overview) : COUNT(shortcuts);
	XmString label, item;
	size_t i;

	s->page = page;
	label = XmStringCreateLocalized(page == 0 ? "Overview" : "Shortcuts");
	XtVaSetValues(s->page_label, XmNlabelString, label, NULL);
	XmStringFree(label);
	XmListDeleteAllItems(s->list);
	for (i = 0; i < n; i++) {
		item = XmStringCreateLocalized((char *)rows[i]);
		XmListAddItemUnselected(s->list, item, 0);
		XmStringFree(item);
	}
}

static void
select_overview(Widget w, XtPointer arg, XtPointer event)
{
	struct sample *s = arg;

	(void)w;
	(void)event;
	show_page(s, 0);
	d32ui_status(s->ui, "[OK] Overview selected");
}

static void
select_shortcuts(Widget w, XtPointer arg, XtPointer event)
{
	struct sample *s = arg;

	(void)w;
	(void)event;
	show_page(s, 1);
	d32ui_status(s->ui, "[OK] Shortcuts selected");
}

static void
switch_tab(Widget w, XtPointer arg, XtPointer event)
{
	struct sample *s = arg;

	if (s->page == 0)
		select_shortcuts(w, arg, event);
	else
		select_overview(w, arg, event);
}

static void
find_focus(Widget w, XtPointer arg, XtPointer event)
{
	struct sample *s = arg;

	(void)w;
	(void)event;
	(void)XmProcessTraversal(s->find, XmTRAVERSE_CURRENT);
	d32ui_status(s->ui, "[!] Find in sample rows");
}

static void
find_row(Widget w, XtPointer arg, XtPointer event)
{
	struct sample *s = arg;
	const char *const *rows = s->page == 0 ? overview : shortcuts;
	size_t n = s->page == 0 ? COUNT(overview) : COUNT(shortcuts);
	char *text;
	size_t i;

	(void)w;
	(void)event;
	text = XmTextFieldGetString(s->find);
	if (text == NULL || *text == '\0') {
		d32ui_status(s->ui, "[!] Enter text to find");
		if (text != NULL)
			XtFree(text);
		return;
	}
	for (i = 0; i < n && strstr(rows[i], text) == NULL; i++)
		continue;
	XtFree(text);
	if (i == n) {
		XmListDeselectAllItems(s->list);
		d32ui_status(s->ui, "[?] No matching row");
		return;
	}
	XmListSelectPos(s->list, (int)i + 1, False);
	XmListSetPos(s->list, (int)i + 1);
	d32ui_status(s->ui, "[OK] Matching row selected");
}

static void
file_chosen(const char *path, void *arg)
{
	struct sample *s = arg;
	struct stat st;
	int fd;
	enum d32_error result;

	result = d32_open_input(path, D32_INPUT_FILE, 4 * 1024 * 1024, &fd, &st);
	if (result != D32E_OK) {
		d32ui_status(s->ui, "[x] File refused");
		(void)d32ui_error(s->ui, s->list, result, NULL);
		return;
	}
	(void)close(fd);
	d32ui_status(s->ui, "[OK] File verified");
}

static void
open_file(Widget w, XtPointer arg, XtPointer event)
{
	struct sample *s = arg;

	(void)w;
	(void)event;
	(void)d32ui_file_select(s->ui, s->list, "*", file_chosen, s);
}

static void
show_help(Widget w, XtPointer arg, XtPointer event)
{
	struct sample *s = arg;

	(void)w;
	(void)event;
	(void)d32ui_help(s->ui, s->list, NULL, NULL,
	    "Deep32 UI Sample\n\nF5 runs the synthetic worker. Cancel stops it. "
	    "Ctrl+F moves focus to Find. Use Tab to move between controls. "
	    "In the reset dialog, Return and Escape both keep the current view.");
}

static void
reset_choice(bool confirmed, void *arg)
{
	struct sample *s = arg;

	if (confirmed) {
		show_page(s, 0);
		XmTextFieldSetString(s->find, "");
	}
	d32ui_status(s->ui, confirmed ? "[OK] View reset" : "[OK] Reset cancelled");
}

static void
confirm_reset(Widget w, XtPointer arg, XtPointer event)
{
	struct sample *s = arg;

	(void)event;
	(void)d32ui_confirm(s->ui, w, "Reset the selected tab and Find text?",
	    reset_choice, s);
}

static void
progress_destroyed(Widget w, XtPointer arg, XtPointer event)
{
	struct sample *s = arg;

	(void)event;
	if (s->progress == w)
		s->progress = NULL;
}

static void
close_progress(struct sample *s)
{
	if (s->progress != NULL) {
		Widget progress = s->progress;

		s->progress = NULL;
		d32ui_progress_close(progress);
	}
}

static void
cancel_worker(void *arg)
{
	struct sample *s = arg;

	if (s->job != NULL && !s->cancelled) {
		s->cancelled = true;
		d32ui_status(s->ui, "[!] Stopping worker");
		d32ui_job_cancel(s->job);
	}
}

static void
worker_data(const unsigned char *data, size_t size, void *arg)
{
	struct sample *s = arg;
	char status[80], message[96];
	XmString label;
	unsigned step = data[size - 1];

	(void)snprintf(status, sizeof(status), "[!] Worker running (%u/%u)",
	    step, (unsigned)SAMPLE_STEPS);
	d32ui_status(s->ui, status);
	if (s->progress != NULL) {
		(void)snprintf(message, sizeof(message),
		    "Processing synthetic rows (%02u/%u). Cancel stops the worker.",
		    step, (unsigned)SAMPLE_STEPS);
		label = XmStringCreateLocalized(message);
		XtVaSetValues(s->progress, XmNmessageString, label, NULL);
		XmStringFree(label);
	}
}

static void
worker_done(int status, void *arg)
{
	struct sample *s = arg;

	s->job = NULL;
	XtSetSensitive(s->run_button, True);
	close_progress(s);
	if (s->cancelled)
		d32ui_status(s->ui, "[!] Worker cancelled");
	else if (status != -1 && WIFEXITED(status) && WEXITSTATUS(status) == 0)
		d32ui_status(s->ui, "[OK] Worker complete");
	else
		d32ui_status(s->ui, "[x] Worker failed");
	if (s->leaving)
		d32ui_quit(s->ui);
}

static void
reap_failed_watch(XtPointer arg, XtIntervalId *id)
{
	struct sample *s = arg;
	pid_t waited;

	(void)id;
	waited = waitpid(s->cleanup_pid, NULL, WNOHANG);
	if (waited == 0 || (waited == -1 && errno == EINTR)) {
		(void)XtAppAddTimeOut(d32ui_context(s->ui), 25, reap_failed_watch, s);
		return;
	}
	s->cleanup_pid = 0;
	XtSetSensitive(s->run_button, True);
	if (s->leaving)
		d32ui_quit(s->ui);
	else
		(void)XmProcessTraversal(s->run_button, XmTRAVERSE_CURRENT);
}

static void
run_child(int fd)
{
	const struct timespec interval = {0, 150000000};
	struct timespec delay;
	unsigned char step;
	ssize_t n;

	(void)signal(SIGTERM, SIG_DFL);
	for (step = 1; step <= SAMPLE_STEPS; step++) {
		delay = interval;
		while (nanosleep(&delay, &delay) == -1 && errno == EINTR)
			continue;
		do {
			n = write(fd, &step, 1);
		} while (n == -1 && errno == EINTR);
		if (n != 1)
			_exit(1);
	}
	(void)close(fd);
	_exit(0);
}

static void
start_worker(Widget w, XtPointer arg, XtPointer event)
{
	struct sample *s = arg;
	pid_t pid;
	int fds[2], xfd;

	(void)w;
	(void)event;
	if (s->job != NULL || s->cleanup_pid > 0 || s->leaving)
		return;
	s->progress = d32ui_progress(s->ui, s->run_button,
	    "Processing synthetic rows (00/40). Cancel stops the worker.", cancel_worker, s);
	if (s->progress == NULL) {
		d32ui_status(s->ui, "[x] Progress dialog unavailable");
		return;
	}
	XtAddCallback(s->progress, XtNdestroyCallback, progress_destroyed, s);
	if (pipe2(fds, O_CLOEXEC) == -1)
		goto fail;
	xfd = ConnectionNumber(XtDisplay(d32ui_shell(s->ui)));
	pid = fork();
	if (pid == 0) {
		(void)close(fds[0]);
		(void)close(xfd);
		run_child(fds[1]);
	}
	(void)close(fds[1]);
	if (pid == -1) {
		(void)close(fds[0]);
		goto fail;
	}
	s->job = d32ui_job_watch(s->ui, pid, fds[0], worker_data, worker_done, s);
	if (s->job == NULL) {
		if (errno != ECHILD) {
			(void)kill(pid, SIGTERM);
			s->cleanup_pid = pid;
			XtSetSensitive(s->run_button, False);
			(void)XtAppAddTimeOut(d32ui_context(s->ui), 25, reap_failed_watch, s);
		}
		(void)close(fds[0]);
		goto fail;
	}
	s->cancelled = false;
	XtSetSensitive(s->run_button, False);
	d32ui_status(s->ui, "[!] Worker running (0/40)");
	return;

fail:
	close_progress(s);
	d32ui_status(s->ui, "[x] Could not start worker");
}

static void
quit_sample(Widget w, XtPointer arg, XtPointer event)
{
	struct sample *s = arg;

	(void)w;
	(void)event;
	s->leaving = true;
	if (s->job != NULL)
		cancel_worker(s);
	else if (s->cleanup_pid == 0)
		d32ui_quit(s->ui);
}

static Widget
button(Widget parent, const char *name, const char *caption,
    XtCallbackProc activate, struct sample *s)
{
	XmString label = XmStringCreateLocalized((char *)caption);
	Widget w;

	w = XtVaCreateManagedWidget(name, xmPushButtonWidgetClass, parent,
	    XmNlabelString, label, XmNnavigationType, XmTAB_GROUP, NULL);
	XmStringFree(label);
	XtAddCallback(w, XmNactivateCallback, activate, s);
	return w;
}

static bool
make_sample(struct sample *s)
{
	const struct d32ui_menu_item items[] = {
		{D32UI_OPEN, open_file, s}, {D32UI_EXIT, quit_sample, s},
		{D32UI_FIND, find_focus, s}, {D32UI_REFRESH, start_worker, s},
		{D32UI_PREV_TAB, switch_tab, s}, {D32UI_NEXT_TAB, switch_tab, s},
		{D32UI_HELP, show_help, s}
	};
	static const struct d32ui_bar bars[] = {
		{"Queued", 25}, {"Reading", 50}, {"Verified", 75}, {"Ready", 100}
	};
	Widget body = d32ui_body(s->ui), menu, chart, controls, list_frame;
	Arg args[6];
	Cardinal n = 0;

	menu = d32ui_menu(s->ui, body, items, COUNT(items));
	chart = d32ui_chart(s->ui, body, bars, COUNT(bars));
	if (menu == NULL || chart == NULL)
		return false;
	XtVaSetValues(menu, XmNtopAttachment, XmATTACH_FORM,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
	XtVaSetValues(chart, XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, menu,
	    XmNtopOffset, 8, XmNleftAttachment, XmATTACH_FORM, XmNleftOffset, 8,
	    XmNrightAttachment, XmATTACH_FORM, XmNrightOffset, 8, NULL);
	controls = XtVaCreateManagedWidget("controls", xmRowColumnWidgetClass, body,
	    XmNorientation, XmHORIZONTAL, XmNpacking, XmPACK_TIGHT, XmNspacing, 4,
	    XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, chart, XmNtopOffset, 6,
	    XmNleftAttachment, XmATTACH_FORM, XmNleftOffset, 8,
	    XmNrightAttachment, XmATTACH_FORM, XmNrightOffset, 8, NULL);
	(void)button(controls, "overview", "Overview", select_overview, s);
	(void)button(controls, "shortcuts", "Shortcuts", select_shortcuts, s);
	s->run_button = button(controls, "runWorker", "Run Worker", start_worker, s);
	s->reset_button = button(controls, "resetView", "Reset View", confirm_reset, s);
	s->find = XtVaCreateManagedWidget("findText", xmTextFieldWidgetClass, controls,
	    XmNcolumns, 12, XmNmaxLength, 128, XmNnavigationType, XmTAB_GROUP, NULL);
	XtAddCallback(s->find, XmNactivateCallback, find_row, s);
	(void)button(controls, "findRow", "Find", find_row, s);
	s->page_label = XtVaCreateManagedWidget("pageLabel", xmLabelWidgetClass, body,
	    XmNalignment, XmALIGNMENT_BEGINNING, XmNtraversalOn, False,
	    XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, controls, XmNtopOffset, 4,
	    XmNleftAttachment, XmATTACH_FORM, XmNleftOffset, 8, NULL);
	XtSetArg(args[n], XmNvisibleItemCount, 5); n++;
	XtSetArg(args[n], XmNselectionPolicy, XmBROWSE_SELECT); n++;
	XtSetArg(args[n], XmNnavigationType, XmTAB_GROUP); n++;
	if (d32ui_mono(s->ui) != NULL) {
		XtSetArg(args[n], XmNfontList, d32ui_mono(s->ui)); n++;
	}
	s->list = XmCreateScrolledList(body, "sampleRows", args, n);
	list_frame = XtParent(s->list);
	XtVaSetValues(list_frame, XmNtopAttachment, XmATTACH_WIDGET,
	    XmNtopWidget, s->page_label, XmNleftAttachment, XmATTACH_FORM, XmNleftOffset, 8,
	    XmNrightAttachment, XmATTACH_FORM, XmNrightOffset, 8,
	    XmNbottomAttachment, XmATTACH_FORM, XmNbottomOffset, 8, NULL);
	show_page(s, 0);
	XtManageChild(s->list);
	return true;
}

int
main(int argc, char **argv)
{
	struct sample s = {0};
	enum d32ui_handling handling = D32UI_PUBLIC;
	int read_arg, write_arg = 1;

	for (read_arg = 1; read_arg < argc; read_arg++) {
		if (strcmp(argv[read_arg], "-restricted") == 0)
			handling = D32UI_RESTRICTED;
		else
			argv[write_arg++] = argv[read_arg];
	}
	argv[write_arg] = NULL;
	argc = write_arg;
	s.ui = d32ui_init(&argc, argv, "D32UISample");
	if (s.ui == NULL)
		return 1;
	if (d32ui_set_handling(s.ui, handling) != D32E_OK || !make_sample(&s)) {
		d32ui_destroy(s.ui);
		return 1;
	}
	XtVaSetValues(d32ui_shell(s.ui), XtNtitle, "Deep32 UI Sample", NULL);
	/* The demo reserves a synthetic front panel until T00 is measured. */
	d32ui_geometry(s.ui, 1440, 900, 48);
	d32ui_on_close(s.ui, quit_sample, &s);
	d32ui_status(s.ui, "[OK] Ready");
	d32ui_show(s.ui);
	(void)XmProcessTraversal(s.run_button, XmTRAVERSE_CURRENT);
	d32ui_run(s.ui);
	d32ui_destroy(s.ui);
	return 0;
}
