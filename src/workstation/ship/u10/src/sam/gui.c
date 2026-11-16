#include "sam.h"
#include "d32_prefs.h"
#include "d32ui.h"

#include <X11/Shell.h>
#include <X11/StringDefs.h>
#include <X11/keysym.h>
#include <Xm/Form.h>
#include <Xm/Label.h>
#include <Xm/List.h>
#include <Xm/Protocols.h>
#include <Xm/PushB.h>
#include <Xm/RowColumn.h>
#include <Xm/Text.h>
#include <Xm/ToggleB.h>

#include <sys/wait.h>

#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sysexits.h>
#include <unistd.h>

#define COUNT(a) (sizeof(a) / sizeof((a)[0]))

enum job_kind { JOB_PAGE, JOB_CHECK, JOB_ACTION };

struct sam_gui;
struct sam_job {
	struct sam_gui *app;
	enum job_kind kind;
	d32ui_job *watch;
	pid_t unwatched;
	size_t size;
	bool overflow;
};

struct sam_button {
	struct sam_gui *app;
	enum sam_action_slot slot;
};

struct sam_gui {
	d32ui *ui;
	const char *self;
	struct sam_prefs prefs;
	enum sam_state pref_state;
	enum sam_category category;
	struct sam_page_view page, incoming;
	struct sam_job reader, command;
	struct sam_button buttons[SAM_ACTION_COUNT];
	Widget list, heading, text, software, desktop, actions[SAM_ACTION_COUNT];
	Widget preview, preview_focus, preview_reason, preview_run;
	Widget preferences, pref_focus, pref_default;
	Widget pref_page[SAM_CATEGORY_COUNT], pref_interval[3];
	enum sam_action_slot preview_slot, command_slot;
	unsigned char fresh_state;
	unsigned long page_epoch, check_epoch;
	int preview_authority;
	XtIntervalId timer;
	bool timer_armed, page_valid, read_again, preview_changed, check_cancelled, leaving;
	bool help_installed;
};

static const char help_volume[] = "/usr/local/dt/appconfig/help/C/D32Sam.sdl";

static const unsigned intervals[] = {0, 30, 300};
static const char *const interval_names[] = {"Off", "30 seconds", "5 minutes"};
static const char *const newer_status = "[!] " D32_PREFS_NEWER_STATUS;

static void request_page(struct sam_gui *);
static void start_page(struct sam_gui *);
static void update_actions(struct sam_gui *);
static void job_done(int, void *);
static void refresh_timer(XtPointer, XtIntervalId *);
static void dismiss_preview(Widget, XtPointer, XtPointer);
static void dismiss_preferences(Widget, XtPointer, XtPointer);

static void
set_label(Widget w, const char *text)
{
	XmString label = XmStringCreateLocalized((char *)text);

	XtVaSetValues(w, XmNlabelString, label, NULL);
	XmStringFree(label);
}

static Widget
button(Widget parent, const char *name, const char *caption, KeySym mnemonic,
    XtCallbackProc callback, XtPointer data)
{
	Widget w = XtVaCreateManagedWidget(name, xmPushButtonWidgetClass, parent,
	    XmNnavigationType, XmTAB_GROUP, XmNmnemonic, mnemonic, NULL);

	set_label(w, caption);
	XtAddCallback(w, XmNactivateCallback, callback, data);
	return w;
}

static void
category_status(struct sam_gui *s, const char *format)
{
	char status[96];

	(void)snprintf(status, sizeof(status), format, sam_category_name(s->category));
	d32ui_status(s->ui, status);
}

static void
focus_widget(Widget w)
{
	if (w != NULL && XtIsManaged(w) && XtIsRealized(w) && XtIsSensitive(w))
		(void)XmProcessTraversal(w, XmTRAVERSE_CURRENT);
}

static Widget
focused(struct sam_gui *s, Widget fallback)
{
	Widget w = XmGetFocusWidget(d32ui_shell(s->ui));

	return w != NULL && XtIsManaged(w) ? w : fallback;
}

static bool
busy(const struct sam_job *job)
{
	return job->watch != NULL || job->unwatched > 0;
}

static bool
authorized(const struct sam_gui *s, enum sam_action_slot slot)
{
	return s->page_valid && s->page.category == sam_action(slot)->category &&
	    s->page.actions[slot] == SAM_OK;
}

static void
update_actions(struct sam_gui *s)
{
	enum sam_action_slot slot;
	const char *reason;
	char detail[192];
	bool can_run;

	for (slot = SAM_LOOK; slot <= SAM_AUDIO; slot++)
		XtSetSensitive(s->actions[slot], authorized(s, slot) && !busy(&s->command));
	if (s->preview == NULL)
		return;
	slot = s->preview_slot;
	can_run = slot == SAM_ROLLBACK && authorized(s, slot);
	if (slot == SAM_APPLY) {
		reason = SAM_APPLY_REASON;
	} else if (!s->page_valid && s->category == SAM_SOFTWARE) {
		reason = "Checking current authorization.";
	} else if (can_run) {
		reason = "Authenticate in the terminal to continue.";
	} else {
		reason = sam_revert_reason(s->page.revert_release);
	}
	if (s->page_valid && s->page.category == SAM_SOFTWARE && slot == SAM_ROLLBACK) {
		int authority = s->page.actions[slot];

		if (s->preview_authority >= 0 && s->preview_authority != authority)
			s->preview_changed = true;
		s->preview_authority = authority;
	}
	if (s->preview_changed) {
		(void)snprintf(detail, sizeof(detail),
		    "%s\nAuthorization changed on refresh.", reason);
		reason = detail;
	}
	set_label(s->preview_reason, reason);
	if (can_run) {
		XtSetSensitive(s->preview_run, !busy(&s->command));
		XtManageChild(s->preview_run);
	} else {
		XtUnmanageChild(s->preview_run);
	}
}

static void
arm_refresh(struct sam_gui *s)
{
	if (s->timer_armed) {
		XtRemoveTimeOut(s->timer);
		s->timer_armed = false;
	}
	if (s->prefs.refresh_seconds != 0 && !s->leaving) {
		s->timer = XtAppAddTimeOut(d32ui_context(s->ui),
		    s->prefs.refresh_seconds * 1000UL, refresh_timer, s);
		s->timer_armed = true;
	}
}

static void
refresh_timer(XtPointer arg, XtIntervalId *id)
{
	struct sam_gui *s = arg;

	(void)id;
	s->timer_armed = false;
	if (!busy(&s->reader))
		request_page(s);
	arm_refresh(s);
}

static void
job_data(const unsigned char *data, size_t size, void *arg)
{
	struct sam_job *job = arg;
	struct sam_gui *s = job->app;
	unsigned char *dest;
	size_t capacity;

	if (job->kind == JOB_ACTION)
		return;
	if (job->kind == JOB_PAGE) {
		dest = (unsigned char *)&s->incoming;
		capacity = sizeof(s->incoming);
	} else {
		dest = &s->fresh_state;
		capacity = sizeof(s->fresh_state);
	}
	if (size > capacity - job->size) {
		job->overflow = true;
		return;
	}
	memcpy(dest + job->size, data, size);
	job->size += size;
}

static void
reap_unwatched(XtPointer arg, XtIntervalId *id)
{
	struct sam_job *job = arg;
	pid_t waited;

	(void)id;
	waited = waitpid(job->unwatched, NULL, WNOHANG);
	if (waited == 0 || (waited == -1 && errno == EINTR)) {
		(void)XtAppAddTimeOut(d32ui_context(job->app->ui), 25,
		    reap_unwatched, job);
		return;
	}
	job->unwatched = 0;
	job_done(-1, job);
}

static void
watch(struct sam_job *job, enum job_kind kind, pid_t pid, int fd)
{
	int error;

	job->kind = kind;
	job->size = 0;
	job->overflow = false;
	job->watch = d32ui_job_watch(job->app->ui, pid, fd, job_data, job_done, job);
	if (job->watch != NULL)
		return;
	error = errno;
	(void)close(fd);
	if (error == ECHILD) {
		job_done(-1, job);
		return;
	}
	if (kind != JOB_ACTION)
		(void)kill(pid, SIGTERM);
	job->unwatched = pid;
	(void)XtAppAddTimeOut(d32ui_context(job->app->ui), 25, reap_unwatched, job);
}

static void
start_page(struct sam_gui *s)
{
	pid_t pid;
	int fd;

	s->read_again = false;
	if (sam_spawn_page(s->self, s->category, &pid, &fd) == SAM_OK) {
		watch(&s->reader, JOB_PAGE, pid, fd);
	} else {
		XmTextSetString(s->text, "Page unavailable. Press F5 to retry.");
		category_status(s, "[x] %s unavailable.");
	}
}

static void
request_page(struct sam_gui *s)
{
	if (s->leaving)
		return;
	s->page_epoch++;
	s->page_valid = false;
	update_actions(s);
	XmTextSetString(s->text, "Reading local sources...");
	category_status(s, "[?] Reading %s.");
	if (busy(&s->reader)) {
		s->read_again = true;
		if (s->reader.watch != NULL)
			d32ui_job_cancel(s->reader.watch);
	} else {
		start_page(s);
	}
}

static void
choose_page(struct sam_gui *s, enum sam_category category)
{
	s->category = category;
	XmListSelectPos(s->list, (int)category + 1, False);
	set_label(s->heading, sam_category_name(category));
	if (category == SAM_SOFTWARE)
		XtManageChild(s->software);
	else
		XtUnmanageChild(s->software);
	if (category == SAM_DESKTOP)
		XtManageChild(s->desktop);
	else
		XtUnmanageChild(s->desktop);
	request_page(s);
}

static void
start_check(struct sam_gui *s, enum sam_action_slot slot)
{
	const struct sam_operation *op = sam_action(slot);
	enum sam_state state;
	pid_t pid;
	int fd;

	if (s->leaving || busy(&s->command) || !authorized(s, slot))
		return;
	s->command_slot = slot;
	s->check_epoch = s->page_epoch;
	s->check_cancelled = false;
	state = sam_spawn_check(s->self, op, &pid, &fd);
	if (state != SAM_OK) {
		if (state == SAM_PREVIEW || state == SAM_UNAVAILABLE || state == SAM_DENIED) {
			s->page.actions[slot] = state;
			update_actions(s);
			d32ui_status(s->ui, "[?] Action unavailable.");
		} else {
			d32ui_status(s->ui, "[x] Could not check current authorization.");
		}
		return;
	}
	d32ui_status(s->ui, "[?] Checking current authorization.");
	watch(&s->command, JOB_CHECK, pid, fd);
	update_actions(s);
}

static void
finish_check(struct sam_gui *s, enum sam_state checked)
{
	enum sam_action_slot slot = s->command_slot;
	const struct sam_operation *op = sam_action(slot);
	pid_t pid;
	int fd;

	if (s->check_cancelled || (slot == SAM_ROLLBACK &&
	    (s->preview == NULL || s->preview_slot != slot)))
		return;
	if (s->check_epoch != s->page_epoch || !authorized(s, slot)) {
		d32ui_status(s->ui, "[!] Page changed during the authorization check.");
		return;
	}
	if (slot == SAM_ROLLBACK && checked == SAM_PREVIEW) {
		request_page(s);
		return;
	}
	if (checked != SAM_OK) {
		s->page.actions[slot] = SAM_DENIED;
		update_actions(s);
		d32ui_status(s->ui, "[!] Current authorization did not permit launch.");
		return;
	}
	if (sam_spawn_action(op, SAM_OK, &pid, &fd) != SAM_OK) {
		d32ui_status(s->ui, "[x] Could not start the requested action.");
		return;
	}
	if (slot == SAM_ROLLBACK)
		dismiss_preview(NULL, s, NULL);
	d32ui_status(s->ui, slot == SAM_ROLLBACK ?
	    "[?] Terminal open. Update state will be read after it closes." :
	    "[?] Desktop action opened.");
	watch(&s->command, JOB_ACTION, pid, fd);
}

static void
job_done(int status, void *arg)
{
	struct sam_job *job = arg;
	struct sam_gui *s = job->app;
	bool ok = status != -1 && WIFEXITED(status) && WEXITSTATUS(status) == 0 &&
	    !job->overflow;

	job->watch = NULL;
	if (s->leaving) {
		if (!busy(&s->reader) && !busy(&s->command))
			d32ui_quit(s->ui);
		return;
	}
	switch (job->kind) {
	case JOB_PAGE:
		if (s->read_again) {
			start_page(s);
			return;
		}
		if (!ok || job->size != sizeof(s->incoming) ||
		    s->incoming.category != s->category ||
		    memchr(s->incoming.text, '\0', sizeof(s->incoming.text)) == NULL) {
			XmTextSetString(s->text, "Page unavailable. Press F5 to retry.");
			category_status(s, "[x] %s unavailable.");
			return;
		}
		s->page = s->incoming;
		s->page_valid = true;
		XmTextSetString(s->text, s->page.text);
		XmTextSetTopCharacter(s->text, 0);
		if (s->prefs.newer)
			d32ui_status(s->ui, newer_status);
		else
			category_status(s, "[OK] %s ready.");
		break;
	case JOB_CHECK:
		finish_check(s, ok && job->size == 1 ? s->fresh_state : SAM_DENIED);
		break;
	case JOB_ACTION:
		if (s->command_slot == SAM_ROLLBACK)
			choose_page(s, SAM_SOFTWARE);
		else
			request_page(s);
		break;
	}
	update_actions(s);
}

static void
refresh(Widget w, XtPointer arg, XtPointer event)
{
	(void)w;
	(void)event;
	request_page(arg);
}

static void
select_category(Widget w, XtPointer arg, XtPointer event)
{
	XmListCallbackStruct *selection = event;

	(void)w;
	if (selection->item_position > 0 && selection->item_position <= SAM_CATEGORY_COUNT)
		choose_page(arg, (enum sam_category)(selection->item_position - 1));
}

static void
previous_category(Widget w, XtPointer arg, XtPointer event)
{
	struct sam_gui *s = arg;

	(void)w;
	(void)event;
	choose_page(s, (s->category + SAM_CATEGORY_COUNT - 1) % SAM_CATEGORY_COUNT);
}

static void
next_category(Widget w, XtPointer arg, XtPointer event)
{
	struct sam_gui *s = arg;

	(void)w;
	(void)event;
	choose_page(s, (s->category + 1) % SAM_CATEGORY_COUNT);
}

static void
show_help(Widget w, XtPointer arg, XtPointer event)
{
	struct sam_gui *s = arg;

	(void)w;
	(void)event;
	(void)d32ui_help(s->ui, focused(s, s->list), s->help_installed ? help_volume : NULL,
	    "_hometopic",
	    "System Administration\n\n"
	    "Choose a category to read its local sources. Press F5 to refresh, "
	    "or use Ctrl+Page Up and Ctrl+Page Down to switch categories. "
	    "Ctrl+S opens SAM Preferences.\n\n"
	    "Software & Updates shows the fixed command arguments. Apply Staged "
	    "runs during the boot window. Revert is offered in a terminal when "
	    "current authorization permits it.");
}

static Widget
new_dialog(struct sam_gui *s, const char *name, const char *caption,
    unsigned char style, XtCallbackProc dismiss)
{
	Widget dialog = XmCreateFormDialog(d32ui_shell(s->ui), (char *)name, NULL, 0);
	Widget shell = XtParent(dialog);
	XmString title = XmStringCreateLocalized((char *)caption);
	Atom wm_delete = XmInternAtom(XtDisplay(shell), "WM_DELETE_WINDOW", False);

	XtVaSetValues(dialog, XmNdialogTitle, title, XmNdialogStyle, style,
	    XmNautoUnmanage, False, NULL);
	XmStringFree(title);
	XtVaSetValues(shell, XmNdeleteResponse, XmDO_NOTHING, NULL);
	XmAddWMProtocolCallback(shell, wm_delete, dismiss, s);
	return dialog;
}

static Widget
dialog_contents(Widget dialog)
{
	return XtVaCreateManagedWidget("contents", xmRowColumnWidgetClass, dialog,
	    XmNorientation, XmVERTICAL, XmNpacking, XmPACK_TIGHT,
	    XmNmarginWidth, 10, XmNmarginHeight, 10, XmNspacing, 8,
	    XmNtopAttachment, XmATTACH_FORM, XmNbottomAttachment, XmATTACH_FORM,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
}

static Widget
dialog_buttons(Widget parent)
{
	return XtVaCreateManagedWidget("buttons", xmRowColumnWidgetClass, parent,
	    XmNorientation, XmHORIZONTAL, XmNpacking, XmPACK_TIGHT, XmNspacing, 8, NULL);
}

static void
dismiss_preview(Widget w, XtPointer arg, XtPointer event)
{
	struct sam_gui *s = arg;
	Widget dialog = s->preview;

	(void)w;
	(void)event;
	if (dialog == NULL)
		return;
	s->preview = NULL;
	XtUnmanageChild(dialog);
	focus_widget(s->preview_focus);
	XtDestroyWidget(XtParent(dialog));
	if (s->command.kind == JOB_CHECK && s->command_slot == SAM_ROLLBACK) {
		s->check_cancelled = true;
		if (s->command.watch != NULL)
			d32ui_job_cancel(s->command.watch);
	}
}

static void
run_preview(Widget w, XtPointer arg, XtPointer event)
{
	struct sam_gui *s = arg;

	(void)w;
	(void)event;
	if (s->preview != NULL && s->preview_slot == SAM_ROLLBACK)
		start_check(s, SAM_ROLLBACK);
}

static void
dialog_keys(Widget w, XtPointer arg, XEvent *event, Boolean *dispatch)
{
	struct sam_gui *s = arg;
	Widget shell = w;
	KeySym key;

	if (event->type != KeyPress)
		return;
	while (!XtIsShell(shell))
		shell = XtParent(shell);
	key = XLookupKeysym(&event->xkey, 0);
	if (s->preview != NULL && shell == XtParent(s->preview)) {
		if (key == XK_F5)
			request_page(s);
		else if ((key == XK_Return || key == XK_KP_Enter) &&
		    XtIsSubclass(w, xmPushButtonWidgetClass) && XtIsSensitive(w))
			XtCallCallbacks(w, XmNactivateCallback, NULL);
		else if (key == XK_Escape || key == XK_Return || key == XK_KP_Enter)
			dismiss_preview(w, s, NULL);
		else
			return;
	} else if (s->preferences != NULL && shell == XtParent(s->preferences)) {
		if (key == XK_Escape)
			dismiss_preferences(w, s, NULL);
		else if (key == XK_Return || key == XK_KP_Enter)
			XtCallCallbacks(XtIsSubclass(w, xmPushButtonWidgetClass) && XtIsSensitive(w) ?
			    w : s->pref_default, XmNactivateCallback, NULL);
		else
			return;
	} else {
		return;
	}
	*dispatch = False;
}

static void
watch_keys(Widget w, XtEventHandler handler, struct sam_gui *s)
{
	WidgetList children;
	Cardinal i, count;

	if (!XtIsWidget(w))
		return;
	XtInsertEventHandler(w, KeyPressMask, False, handler, s, XtListHead);
	if (XtIsComposite(w)) {
		XtVaGetValues(w, XtNchildren, &children, XtNnumChildren, &count, NULL);
		for (i = 0; i < count; i++)
			watch_keys(children[i], handler, s);
	}
}

static void
show_preview(struct sam_gui *s, enum sam_action_slot slot)
{
	const struct sam_operation *op = sam_action(slot);
	struct sam_result result;
	Widget content, argv_text, controls, close_button;
	char status[96];

	if (s->preview != NULL) {
		if (s->preview_slot == slot) {
			XRaiseWindow(XtDisplay(s->preview), XtWindow(XtParent(s->preview)));
			return;
		}
		dismiss_preview(NULL, s, NULL);
	}
	if (sam_command_preview(op, &result) != SAM_PREVIEW) {
		d32ui_status(s->ui, "[x] Command Preview unavailable.");
		return;
	}
	s->preview_focus = focused(s, s->actions[slot]);
	s->preview_slot = slot;
	s->preview_authority = -1;
	s->preview_changed = false;
	s->preview = new_dialog(s, "commandPreview", "Command Preview",
	    XmDIALOG_MODELESS, dismiss_preview);
	content = dialog_contents(s->preview);
	set_label(XtVaCreateManagedWidget("operation", xmLabelWidgetClass, content,
	    XmNalignment, XmALIGNMENT_BEGINNING, NULL), op->title);
	argv_text = XtVaCreateManagedWidget("argv", xmTextWidgetClass, content,
	    XmNeditMode, XmMULTI_LINE_EDIT, XmNeditable, False,
	    XmNcursorPositionVisible, False, XmNcolumns, 42, XmNrows, 7,
	    XmNfontList, d32ui_mono(s->ui), XmNnavigationType, XmTAB_GROUP, NULL);
	XmTextSetString(argv_text, result.value);
	s->preview_reason = XtVaCreateManagedWidget("authorization", xmLabelWidgetClass,
	    content, XmNalignment, XmALIGNMENT_BEGINNING, NULL);
	controls = dialog_buttons(content);
	s->preview_run = button(controls, "runInTerminal", "Run in Terminal", XK_R,
	    run_preview, s);
	close_button = button(controls, "closePreview", "Close", XK_C, dismiss_preview, s);
	XtVaSetValues(s->preview, XmNdefaultButton, close_button,
	    XmNcancelButton, close_button, XmNinitialFocus, close_button, NULL);
	update_actions(s);
	d32ui_tag_dialog(s->ui, s->preview);
	watch_keys(XtParent(s->preview), dialog_keys, s);
	XtManageChild(s->preview);
	focus_widget(close_button);
	(void)snprintf(status, sizeof(status), "[?] Command Preview: %s.", op->id);
	d32ui_status(s->ui, status);
}

static void
activate_action(Widget w, XtPointer arg, XtPointer event)
{
	struct sam_button *b = arg;

	(void)w;
	(void)event;
	if (b->slot >= SAM_APPLY)
		show_preview(b->app, b->slot);
	else
		start_check(b->app, b->slot);
}

static void
main_keys(Widget w, XtPointer arg, XEvent *event, Boolean *dispatch)
{
	struct sam_gui *s = arg;
	KeySym key;

	(void)w;
	if (event->type != KeyPress || s->category != SAM_SOFTWARE ||
	    (event->xkey.state & (Mod1Mask | ControlMask)) != Mod1Mask)
		return;
	key = XLookupKeysym(&event->xkey, 0);
	if (key == XK_a || key == XK_r) {
		*dispatch = False;
		show_preview(s, key == XK_a ? SAM_APPLY : SAM_ROLLBACK);
	}
}

static void
dismiss_preferences(Widget w, XtPointer arg, XtPointer event)
{
	struct sam_gui *s = arg;
	Widget dialog = s->preferences;

	(void)event;
	if (dialog == NULL)
		return;
	s->preferences = NULL;
	XtUnmanageChild(dialog);
	focus_widget(s->pref_focus);
	XtDestroyWidget(XtParent(dialog));
	if (w != NULL)
		d32ui_status(s->ui, "[?] SAM preferences cancelled.");
}

static void
save_preferences(Widget w, XtPointer arg, XtPointer event)
{
	struct sam_gui *s = arg;
	enum sam_category page;
	size_t interval;
	XtPointer close_after = NULL;

	(void)event;
	XtVaGetValues(w, XmNuserData, &close_after, NULL);
	for (page = SAM_SUMMARY; page < SAM_CATEGORY_COUNT; page++)
		if (XmToggleButtonGetState(s->pref_page[page]))
			break;
	for (interval = 0; interval < COUNT(intervals); interval++)
		if (XmToggleButtonGetState(s->pref_interval[interval]))
			break;
	if (page == SAM_CATEGORY_COUNT || interval == COUNT(intervals)) {
		d32ui_status(s->ui, "[x] Select a page and refresh interval.");
		return;
	}
	if (s->prefs.newer || sam_prefs_save(&s->prefs, page, intervals[interval]) != SAM_OK) {
		d32ui_status(s->ui, s->prefs.newer ? newer_status :
		    "[x] Could not save SAM preferences.");
		return;
	}
	arm_refresh(s);
	d32ui_status(s->ui, "[OK] SAM preferences saved.");
	if (close_after != NULL)
		dismiss_preferences(NULL, s, NULL);
}

static Widget
choice_group(Widget parent, const char *name, const char *title)
{
	Widget box = XtVaCreateManagedWidget(name, xmRowColumnWidgetClass, parent,
	    XmNorientation, XmVERTICAL, XmNpacking, XmPACK_TIGHT, NULL);

	set_label(XtVaCreateManagedWidget("prompt", xmLabelWidgetClass, box,
	    XmNalignment, XmALIGNMENT_BEGINNING, NULL), title);
	box = XmCreateRadioBox(box, "choices", NULL, 0);
	XtVaSetValues(box, XmNradioAlwaysOne, True, NULL);
	XtManageChild(box);
	return box;
}

static Widget
choice(Widget box, const char *caption, bool selected)
{
	Widget w = XtVaCreateManagedWidget("choice", xmToggleButtonWidgetClass, box,
	    XmNnavigationType, XmTAB_GROUP, XmNset, (Boolean)selected, NULL);

	set_label(w, caption);
	return w;
}

static void
show_preferences(Widget w, XtPointer arg, XtPointer event)
{
	struct sam_gui *s = arg;
	Widget content, choices, pages, interval, controls, ok, apply, cancel;
	enum sam_category page;
	size_t i;
	bool writable = s->pref_state == SAM_OK && !s->prefs.newer;

	(void)w;
	(void)event;
	if (s->preferences != NULL)
		return;
	s->pref_focus = focused(s, s->list);
	s->preferences = new_dialog(s, "samPreferences", "SAM Preferences",
	    XmDIALOG_PRIMARY_APPLICATION_MODAL, dismiss_preferences);
	content = dialog_contents(s->preferences);
	choices = dialog_buttons(content);
	pages = choice_group(choices, "defaultPage", "Default page");
	interval = choice_group(choices, "refreshInterval", "Refresh interval");
	for (page = SAM_SUMMARY; page < SAM_CATEGORY_COUNT; page++)
		s->pref_page[page] = choice(pages, sam_category_name(page),
		    page == s->prefs.default_page);
	for (i = 0; i < COUNT(intervals); i++)
		s->pref_interval[i] = choice(interval, interval_names[i],
		    intervals[i] == s->prefs.refresh_seconds);
	controls = dialog_buttons(content);
	ok = button(controls, "ok", "OK", XK_O, save_preferences, s);
	XtVaSetValues(ok, XmNuserData, (XtPointer)1, NULL);
	apply = button(controls, "apply", "Apply", XK_A, save_preferences, s);
	cancel = button(controls, "cancel", "Cancel", XK_C, dismiss_preferences, s);
	XtSetSensitive(pages, writable);
	XtSetSensitive(interval, writable);
	XtSetSensitive(ok, writable);
	XtSetSensitive(apply, writable);
	s->pref_default = writable ? ok : cancel;
	XtVaSetValues(s->preferences, XmNdefaultButton, s->pref_default,
	    XmNcancelButton, cancel, XmNinitialFocus,
	    writable ? s->pref_page[s->prefs.default_page] : cancel, NULL);
	d32ui_tag_dialog(s->ui, s->preferences);
	watch_keys(XtParent(s->preferences), dialog_keys, s);
	XtManageChild(s->preferences);
	focus_widget(writable ? s->pref_page[s->prefs.default_page] : cancel);
	d32ui_status(s->ui, s->prefs.newer ? newer_status : "[?] SAM preferences.");
}

static void
quit(Widget w, XtPointer arg, XtPointer event)
{
	struct sam_gui *s = arg;

	(void)w;
	(void)event;
	s->leaving = true;
	arm_refresh(s);
	dismiss_preview(NULL, s, NULL);
	dismiss_preferences(NULL, s, NULL);
	if (s->reader.watch != NULL)
		d32ui_job_cancel(s->reader.watch);
	if (s->command.kind == JOB_CHECK && s->command.watch != NULL)
		d32ui_job_cancel(s->command.watch);
	if (!busy(&s->reader) && !busy(&s->command))
		d32ui_quit(s->ui);
	else
		d32ui_status(s->ui, "[?] Waiting for the active operation to close.");
}

static bool
make_window(struct sam_gui *s)
{
	const struct d32ui_menu_item items[] = {
		{D32UI_SAVE, show_preferences, s}, {D32UI_EXIT, quit, s},
		{D32UI_REFRESH, refresh, s}, {D32UI_PREV_TAB, previous_category, s},
		{D32UI_NEXT_TAB, next_category, s}, {D32UI_HELP, show_help, s}
	};
	static const char *const names[] = {"openLook", "openSound", "openAudio",
	    "applyStaged", "revert"};
	static const char *const captions[] = {"Open Look", "Open Sound", "Open Audio Control",
	    "Apply Staged", "Revert"};
	Arg text_args[6];
	static const KeySym mnemonics[] = {XK_L, XK_S, XK_U, XK_A, XK_R};
	Widget body = d32ui_body(s->ui), menu, list_frame, pane, frame, controls, prefs;
	XmString categories[SAM_CATEGORY_COUNT];
	enum sam_category page;
	enum sam_action_slot slot;
	Arg args[5];
	Cardinal n = 0;

	menu = d32ui_menu(s->ui, body, items, COUNT(items));
	if (menu == NULL)
		return false;
	XtVaSetValues(menu, XmNtopAttachment, XmATTACH_FORM,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
	for (page = SAM_SUMMARY; page < SAM_CATEGORY_COUNT; page++)
		categories[page] = XmStringCreateLocalized((char *)sam_category_name(page));
	XtSetArg(args[n], XmNitems, categories); n++;
	XtSetArg(args[n], XmNitemCount, SAM_CATEGORY_COUNT); n++;
	XtSetArg(args[n], XmNvisibleItemCount, SAM_CATEGORY_COUNT); n++;
	XtSetArg(args[n], XmNselectionPolicy, XmBROWSE_SELECT); n++;
	XtSetArg(args[n], XmNnavigationType, XmTAB_GROUP); n++;
	s->list = XmCreateScrolledList(body, "categories", args, n);
	for (page = SAM_SUMMARY; page < SAM_CATEGORY_COUNT; page++)
		XmStringFree(categories[page]);
	list_frame = XtParent(s->list);
	XtVaSetValues(list_frame, XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, menu,
	    XmNtopOffset, 8, XmNleftAttachment, XmATTACH_FORM, XmNleftOffset, 8,
	    XmNbottomAttachment, XmATTACH_FORM, XmNbottomOffset, 8,
	    XtNwidth, (Dimension)164, NULL);
	XtAddCallback(s->list, XmNbrowseSelectionCallback, select_category, s);
	XtManageChild(s->list);
	pane = XtVaCreateManagedWidget("page", xmFormWidgetClass, body,
	    XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, menu, XmNtopOffset, 8,
	    XmNleftAttachment, XmATTACH_WIDGET, XmNleftWidget, list_frame, XmNleftOffset, 8,
	    XmNrightAttachment, XmATTACH_FORM, XmNrightOffset, 8,
	    XmNbottomAttachment, XmATTACH_FORM, XmNbottomOffset, 8, NULL);
	s->heading = XtVaCreateManagedWidget("pageHeading", xmLabelWidgetClass, pane,
	    XmNalignment, XmALIGNMENT_BEGINNING, XmNtraversalOn, False,
	    XmNtopAttachment, XmATTACH_FORM, XmNleftAttachment, XmATTACH_FORM, NULL);
	controls = XtVaCreateManagedWidget("pageControls", xmFormWidgetClass, pane,
	    XmNheight, (Dimension)38, XmNbottomAttachment, XmATTACH_FORM,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
	s->software = dialog_buttons(controls);
	s->desktop = dialog_buttons(controls);
	XtVaSetValues(s->software, XmNleftAttachment, XmATTACH_FORM,
	    XmNbottomAttachment, XmATTACH_FORM, NULL);
	XtVaSetValues(s->desktop, XmNleftAttachment, XmATTACH_FORM,
	    XmNbottomAttachment, XmATTACH_FORM, NULL);
	for (slot = SAM_LOOK; slot < SAM_ACTION_COUNT; slot++) {
		s->buttons[slot].app = s;
		s->buttons[slot].slot = slot;
		s->actions[slot] = button(slot < SAM_APPLY ? s->desktop : s->software,
		    names[slot], captions[slot], mnemonics[slot], activate_action, &s->buttons[slot]);
	}
	prefs = button(controls, "preferences", "Preferences", XK_P, show_preferences, s);
	XtVaSetValues(prefs, XmNrightAttachment, XmATTACH_FORM,
	    XmNbottomAttachment, XmATTACH_FORM, NULL);
	XtSetArg(text_args[0], XmNeditMode, XmMULTI_LINE_EDIT);
	XtSetArg(text_args[1], XmNeditable, False);
	XtSetArg(text_args[2], XmNcursorPositionVisible, False);
	XtSetArg(text_args[3], XmNwordWrap, True);
	XtSetArg(text_args[4], XmNscrollHorizontal, False);
	XtSetArg(text_args[5], XmNnavigationType, XmTAB_GROUP);
	s->text = XmCreateScrolledText(pane, "pageText", text_args, XtNumber(text_args));
	frame = XtParent(s->text);
	XtVaSetValues(frame, XmNtopAttachment, XmATTACH_WIDGET,
	    XmNtopWidget, s->heading, XmNtopOffset, 4,
	    XmNbottomAttachment, XmATTACH_WIDGET, XmNbottomWidget, controls,
	    XmNbottomOffset, 4, XmNleftAttachment, XmATTACH_FORM,
	    XmNrightAttachment, XmATTACH_FORM, NULL);
	XtManageChild(s->text);
	watch_keys(body, main_keys, s);
	return true;
}

int
sam_gui(int argc, char **argv, bool settings, const char *self)
{
	struct sam_gui s = {.self = self, .prefs = {.dirfd = -1}};
	Widget shell;
	int result = EX_UNAVAILABLE;

	s.pref_state = sam_prefs_open(&s.prefs, true);
	s.help_installed = access(help_volume, R_OK) == 0;
	s.ui = d32ui_init(&argc, argv, "D32Sam");
	if (s.ui == NULL)
		goto done;
	if (d32ui_set_handling(s.ui, D32UI_PUBLIC) != D32E_OK || !make_window(&s))
		goto done;
	s.reader.app = s.command.app = &s;
	shell = d32ui_shell(s.ui);
	XtVaSetValues(shell, XtNtitle, settings ? "Deep32 Settings" : "System Administration",
	    NULL);
	d32ui_geometry(s.ui, 760, 520, 48);
	d32ui_on_close(s.ui, quit, &s);
	XtRealizeWidget(shell);
	if (sam_gui_sandbox(&s.prefs, self) == -1)
		goto done;
	d32ui_show(s.ui);
	choose_page(&s, settings ? SAM_DESKTOP : s.prefs.default_page);
	focus_widget(s.list);
	arm_refresh(&s);
	d32ui_run(s.ui);
	result = EX_OK;

done:
	d32ui_destroy(s.ui);
	sam_prefs_close(&s.prefs);
	return result;
}
