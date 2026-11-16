#define _POSIX_C_SOURCE 200809L
#define _BSD_SOURCE
#include "child.h"
#include "view.h"
#include "session_widgets.h"
#include "ask_sound.h"
#include "sound.h"

#include <X11/Shell.h>
#include <X11/StringDefs.h>
#include <X11/Xatom.h>
#include <X11/Xresource.h>
#include <X11/Xutil.h>
#include <X11/extensions/Xrandr.h>
#include <X11/keysym.h>
#include <Xm/CascadeB.h>
#include <Xm/CutPaste.h>
#include <Xm/Form.h>
#include <Xm/Frame.h>
#include <Xm/Label.h>
#include <Xm/MainW.h>
#include <Xm/MessageB.h>
#include <Xm/PanedW.h>
#include <Xm/Protocols.h>
#include <Xm/PushB.h>
#include <Xm/RowColumn.h>
#include <Xm/Separator.h>
#include <Xm/Text.h>
#include <Xm/TextF.h>
#include <Xm/ToggleB.h>

#include <limits.h>
#include <locale.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

enum { INPUT_LIMIT = 16384, HISTORY_LIMIT = 64, CAP_LIMIT = 128, PASSWORD_LIMIT = 256 };

typedef struct {
	char engine[7], model[65], effort[9], billing[5];
	int preferred;
} CapabilityRow;

typedef struct {
	AskCapabilities choices;
	AskEngine engines[2];
	AskModel models[CAP_LIMIT];
	AskChoice efforts[CAP_LIMIT];
	CapabilityRow rows[CAP_LIMIT];
	size_t count;
} Capabilities;

typedef struct App {
	XtAppContext context;
	Widget shell, input, send, cancel, link, modifiers, login, challenge;
	Widget header, sizes[ASK_TEXT_CHOICES];
	int panel, text_size;
	AskView *view;
	AskChild *child;
	AskReader reader;
	AskDoc *doc;
	AskSessionWidgets *session;
	AskSound *sound;
	Capabilities *caps, *pending_caps;
	unsigned char confirmation[PASSWORD_LIMIT];
	size_t confirmation_length;
	int unlocked, enrolling, running, provider_pending;
	char *history[HISTORY_LIMIT], *draft;
	size_t history_count, history_pos;
	int dead, closing, exit_status;
} App;

static void
label(Widget widget, const char *text)
{
	XmString string = XmStringCreateLocalized((char *)text);
	XtVaSetValues(widget, XmNlabelString, string, NULL);
	XmStringFree(string);
}

/* The transcript header names the session the bridge holds: Research. */
static void
show_session(App *app)
{
	label(app->header, "Research");
}

static void
send_profile(App *app, AskProfile profile)
{
	char wire[128];
	int len;
	if (!app->unlocked || !profile.engine || !profile.model || !profile.strength)
		return;
	len = snprintf(wire, sizeof wire, "D\t%s\t%s\t%s\n", profile.engine, profile.model, profile.strength);
	if (len > 0 && (size_t)len < sizeof wire)
		(void)child_send(app->child, wire, (size_t)len);
}

/* A new engine, model or strength makes the bridge start a fresh session. */
static void
profile_changed(void *owner, AskProfile profile)
{
	App *app = owner;
	send_profile(app, profile);
}

static void
password_submitted(void *owner, const unsigned char *password, size_t length)
{
	static const char digits[] = "0123456789abcdef";
	App *app = owner;
	char wire[2 * PASSWORD_LIMIT + 20];
	size_t i, len;
	if (length < 12 || length > PASSWORD_LIMIT) {
		ask_session_widgets_prompt(app->session, "Password too short", "Continue");
		ask_session_widgets_state(app->session, ASK_SESSION_DENIED);
		return;
	}
	if (app->enrolling && !app->confirmation_length) {
		memcpy(app->confirmation, password, length);
		app->confirmation_length = length;
		ask_session_widgets_prompt(app->session, "Confirm password", "Set password");
		ask_session_widgets_state(app->session, ASK_SESSION_LOCKED);
		return;
	}
	if (app->enrolling) {
		int matches = length == app->confirmation_length && !timingsafe_bcmp(password, app->confirmation, length);
		explicit_bzero(app->confirmation, sizeof app->confirmation);
		app->confirmation_length = 0;
		if (!matches) {
			ask_session_widgets_prompt(app->session, "Try a new password", "Continue");
			ask_session_widgets_state(app->session, ASK_SESSION_DENIED);
			return;
		}
	}
	len = (size_t)snprintf(wire, sizeof wire, "U\t%s\t", app->enrolling ? "enroll" : "unlock");
	for (i = 0; i < length; i++) {
		wire[len++] = digits[password[i] >> 4];
		wire[len++] = digits[password[i] & 15];
	}
	wire[len++] = '\n';
	if (!child_send(app->child, wire, len))
		ask_session_widgets_state(app->session, ASK_SESSION_UNAVAILABLE);
	explicit_bzero(wire, sizeof wire);
}

static const char *
strength_label(const char *effort)
{
	static const char *const id[] = { "low", "medium", "high", "xhigh", "max", "ultra" };
	static const char *const names[] = { "Low", "Medium", "High", "Extra High", "Max", "Ultra" };
	for (size_t i = 0; i < sizeof id / sizeof *id; i++)
		if (!strcmp(effort, id[i])) return names[i];
	return effort;
}

static int
cap_compare(const void *left, const void *right)
{
	const CapabilityRow *a = left, *b = right;
	int order = strcmp(a->engine, b->engine);
	return order ? order : strcmp(a->model, b->model);
}

static void
show_capabilities(App *app)
{
	Capabilities *caps = app->pending_caps;
	AskProfile profile;
	AskEngine *engine = NULL;
	AskModel *model = NULL;
	size_t model_count = 0;
	app->pending_caps = NULL;
	if (caps && caps->count) {
		qsort(caps->rows, caps->count, sizeof caps->rows[0], cap_compare);
		caps->choices.engines = caps->engines;
		caps->choices.preferred = SIZE_MAX;
		for (size_t i = 0; i < caps->count; i++) {
			CapabilityRow *row = &caps->rows[i];
			if (!engine || strcmp(engine->choice.id, row->engine)) {
				if (caps->choices.count >= 2) break;
				engine = &caps->engines[caps->choices.count++];
				engine->choice = (AskChoice){ row->engine, !strcmp(row->engine, "codex") ? "Codex" : "Claude (Plan)" };
				engine->models = &caps->models[model_count];
				engine->preferred = SIZE_MAX;
				model = NULL;
			}
			if (!model || strcmp(model->choice.id, row->model)) {
				model = &caps->models[model_count++];
				model->choice = (AskChoice){ row->model, row->model };
				model->strengths = &caps->efforts[i];
				model->preferred = SIZE_MAX;
				engine->count++;
			}
			caps->efforts[i] = (AskChoice){ row->effort, strength_label(row->effort) };
			if (row->preferred) {
				if (caps->choices.preferred == SIZE_MAX || !strcmp(row->engine, "codex"))
					caps->choices.preferred = caps->choices.count - 1;
				engine->preferred = engine->count - 1;
				model->preferred = model->count;
			}
			model->count++;
		}
	}
	if (ask_session_widgets_capabilities(app->session, caps && caps->count ? &caps->choices : NULL) &&
	    ask_session_widgets_profile(app->session, &profile))
		send_profile(app, profile);
	free(app->caps);
	app->caps = caps;
}

static void
close_challenge(App *app)
{
	if (app->challenge) {
		XtDestroyWidget(app->challenge);
		app->challenge = NULL;
	}
}

static void
login_cancel(Widget widget, XtPointer owner, XtPointer call)
{
	App *app = owner;
	(void)widget; (void)call;
	close_challenge(app);
	(void)child_send(app->child, "A\tcancel\n", 9);
}

static void
login_begin(Widget widget, XtPointer owner, XtPointer call)
{
	App *app = owner;
	(void)widget; (void)call;
	if (app->unlocked && app->provider_pending)
		(void)child_send(app->child, "A\tbegin\n", 8);
}

static void
show_challenge(App *app, const char *url, const char *code)
{
	char text[256];
	XmString message;
	close_challenge(app);
	snprintf(text, sizeof text, "In your browser, open %s\nEnter this one-time code: %s\nAsk will update when approval completes.", url, code);
	app->challenge = XmCreateInformationDialog(app->shell, "codexApproval", NULL, 0);
	message = XmStringCreateLocalized(text);
	XtVaSetValues(app->challenge, XmNmessageString, message, XmNautoUnmanage, False, NULL);
	XmStringFree(message);
	XtUnmanageChild(XmMessageBoxGetChild(app->challenge, XmDIALOG_OK_BUTTON));
	XtUnmanageChild(XmMessageBoxGetChild(app->challenge, XmDIALOG_HELP_BUTTON));
	XtAddCallback(app->challenge, XmNcancelCallback, login_cancel, app);
	XtManageChild(app->challenge);
	explicit_bzero(text, sizeof text);
}

static void
set_lock(App *app, const AskEvent *event)
{
	const char *lock = event->field[0], *reason = event->field[3];
	AskSessionState state = ASK_SESSION_LOCKED;
	app->unlocked = !strcmp(lock, "unlocked");
	app->enrolling = !strcmp(lock, "unconfigured");
	app->provider_pending = !strcmp(event->field[1], "pending") || !strcmp(event->field[1], "needs_login");
	if (app->unlocked) {
		ask_session_widgets_state(app->session, ASK_SESSION_UNLOCKED);
		if (!app->running) ask_session_widgets_activity(app->session, ASK_ACTIVITY_READY);
		return;
	}
	close_challenge(app);
	/* A lock during a turn (the link dropped) ends it: the error cue, if it was humming. */
	ask_sound_end(app->sound, ASK_SOUND_FAILED);
	app->running = 0;
	explicit_bzero(app->confirmation, sizeof app->confirmation);
	app->confirmation_length = 0;
	(void)ask_session_widgets_capabilities(app->session, NULL);
	free(app->pending_caps);
	app->pending_caps = NULL;
	if (!strcmp(reason, "denied") || !strcmp(reason, "rate_limited"))
		state = ASK_SESSION_DENIED;
	if (!strcmp(lock, "offline") || !strcmp(lock, "unsupported") ||
	    !strcmp(reason, "enrollment_disabled") || !strcmp(reason, "storage_unverified"))
		state = ASK_SESSION_UNAVAILABLE;
	ask_session_widgets_prompt(app->session, app->enrolling ? "Set an Ask password" : "Ask password", app->enrolling ? "Continue" : "Unlock");
	ask_session_widgets_state(app->session, state);
	ask_session_widgets_activity(app->session, ASK_ACTIVITY_LOCKED);
}

static bool
receive_event(void *owner, const AskEvent *event)
{
	App *app = owner;
	switch (event->tag) {
	case 'K': set_lock(app, event); return true;
	case 'M': {
		CapabilityRow *row;
		if (!app->unlocked || strlen(event->field[0]) >= 7 || strlen(event->field[1]) >= 65 ||
		    strlen(event->field[2]) >= 9 || strlen(event->field[3]) >= 5)
			return false;
		if (!app->pending_caps && !(app->pending_caps = calloc(1, sizeof(*app->pending_caps))))
			return false;
		if (app->pending_caps->count >= CAP_LIMIT) return false;
		row = &app->pending_caps->rows[app->pending_caps->count++];
		strlcpy(row->engine, event->field[0], sizeof row->engine);
		strlcpy(row->model, event->field[1], sizeof row->model);
		strlcpy(row->effort, event->field[2], sizeof row->effort);
		strlcpy(row->billing, event->field[3], sizeof row->billing);
		row->preferred = event->number[4] != 0;
		return true;
	}
	case 'J': show_capabilities(app); return true;
	case 'Y': show_challenge(app, event->field[0], event->field[1]); return true;
	case 'Z':
		if (strcmp(event->field[0], "pending")) close_challenge(app);
		if (!strcmp(event->field[0], "ready")) app->provider_pending = 0;
		return true;
	default: break;
	}
	if (!ask_doc_receive(app->doc, event)) return false;
	if (event->tag == 'S' && event->number[0] && !app->closing && app->unlocked) {
		app->running = 1;
		ask_session_widgets_activity(app->session, ASK_ACTIVITY_THINKING);
		ask_sound_thinking(app->sound);
	} else if ((event->tag == 'P' || event->tag == 'R') && app->running) {
		ask_session_widgets_activity(app->session, ASK_ACTIVITY_RECEIVING);
	} else if (event->tag == 'F' || event->tag == 'X') {
		int complete = event->tag == 'F' && !strcmp(event->field[1], "completed");
		app->running = 0;
		ask_sound_end(app->sound, complete ? ASK_SOUND_DONE : ASK_SOUND_FAILED);
		ask_session_widgets_activity(app->session, complete ? ASK_ACTIVITY_COMPLETE : ASK_ACTIVITY_ERROR);
	}
	return true;
}

static int
can_send(const App *app)
{
	AskProfile selected;
	return app->reader.version_seen && !app->dead && !app->closing &&
	    !ask_doc_fatal(app->doc) && app->unlocked && ask_session_widgets_profile(app->session, &selected);
}

static void
sync_ui(App *app)
{
	AskHeader h = ask_doc_header(app->doc);
	int busy = ask_doc_busy(app->doc);
	int ready = can_send(app);
	char status[512];

	if (app->dead)
		snprintf(status, sizeof status, "Ask ended (status %d).", app->exit_status);
	else if (app->closing)
		snprintf(status, sizeof status, "Closing Ask…");
	else if (h.link == NULL || *h.link == '\0')
		snprintf(status, sizeof status, "Connecting to Deep32…");
	else
		snprintf(status, sizeof status, "%s: %s, %s\n%s%s%s", h.link,
		    h.state ? h.state : "", h.paired ? "paired" : "not paired",
		    h.engine ? h.engine : "", h.engine && h.model ? " | " : "",
		    h.model ? h.model : "");
	label(app->link, status);
	label(app->modifiers, h.modifiers && *h.modifiers ? h.modifiers : "Modifiers: none");
	if (app->unlocked && app->provider_pending && !app->closing) XtManageChild(app->login);
	else XtUnmanageChild(app->login);
	XtSetSensitive(app->login, !busy);
	XtSetSensitive(app->send, ready && !busy);
	XtSetSensitive(app->cancel, ready && busy);
	view_refresh(app->view);
}

static void
received(void *owner, const char *bytes, size_t length)
{
	App *app = owner;
	ask_reader_feed(&app->reader, bytes, length, receive_event, app);
	sync_ui(app);
}

static void
child_ended(void *owner, int status)
{
	App *app = owner;
	char text[48];

	ask_reader_end(&app->reader);
	app->dead = 1;
	close_challenge(app);
	ask_sound_end(app->sound, app->closing ? ASK_SOUND_STOP : ASK_SOUND_FAILED);
	ask_session_widgets_state(app->session, ASK_SESSION_UNAVAILABLE);
	ask_session_widgets_activity(app->session, ASK_ACTIVITY_ERROR);
	app->exit_status = status;
	if (app->closing) {
		XtAppSetExitFlag(app->context);
		return;
	}
	snprintf(text, sizeof text, "Ask ended (status %d).", status);
	ask_doc_notice(app->doc, "error", text);
	sync_ui(app);
}

static int
send_line(App *app, const char *text)
{
	char wire[2 * INPUT_LIMIT + 4];
	size_t length;

	if (app->dead || app->closing || !app->reader.version_seen) return 0;
	length = strlen(text);
	if (length == 0 || length > INPUT_LIMIT) {
		if (length > INPUT_LIMIT)
			ask_doc_notice(app->doc, "error", "Ask input is limited to 16 KiB.");
		view_refresh(app->view);
		return 0;
	}
	length = ask_line_request(wire, sizeof wire, text, length);
	if (length != 0 && child_send(app->child, wire, length)) return 1;
	ask_doc_notice(app->doc, "error", "Ask could not send the request.");
	view_refresh(app->view);
	return 0;
}

static void
remember(App *app, const char *text)
{
	char *copy = strdup(text);

	if (copy == NULL) return;
	if (app->history_count == HISTORY_LIMIT) {
		free(app->history[0]);
		memmove(app->history, app->history + 1,
		    (HISTORY_LIMIT - 1) * sizeof app->history[0]);
		app->history_count--;
	}
	app->history[app->history_count++] = copy;
	app->history_pos = app->history_count;
	free(app->draft);
	app->draft = NULL;
}

static void
send_input(App *app)
{
	char *text;

	if (!XtIsSensitive(app->send)) return;
	text = XmTextGetString(app->input);
	if (text != NULL && send_line(app, text)) {
		remember(app, text);
		XmTextSetString(app->input, "");
		XmProcessTraversal(app->input, XmTRAVERSE_CURRENT);
	}
	XtFree(text);
}

static void
interrupt(App *app)
{
	if (XtIsSensitive(app->cancel)) {
		/* Stop the hum at once; the error cue says the turn was abandoned. */
		ask_sound_end(app->sound, ASK_SOUND_FAILED);
		(void)child_send(app->child, "I\n", 2);
	}
}

static void
open_reference(void *owner, uint32_t turn, uint32_t n)
{
	App *app = owner;
	char record[32];
	size_t length = ask_request_open(app->doc, turn, n, record, sizeof record);
	if (length) (void)child_send(app->child, record, length);
}

static void
quit(App *app)
{
	if (app->closing) return;
	if (app->dead || app->child == NULL) {
		XtAppSetExitFlag(app->context);
		return;
	}
	app->closing = 1;
	close_challenge(app);
	ask_sound_end(app->sound, ASK_SOUND_STOP);
	sync_ui(app);
	child_close(app->child);
}

static void
send_cb(Widget widget, XtPointer data, XtPointer call)
{
	(void)widget;
	(void)call;
	send_input(data);
}

static void
cancel_cb(Widget widget, XtPointer data, XtPointer call)
{
	(void)widget;
	(void)call;
	interrupt(data);
}

static void
close_cb(Widget widget, XtPointer data, XtPointer call)
{
	(void)widget;
	(void)call;
	quit(data);
}

static int
recall(App *app, int direction)
{
	XmTextPosition cursor = XmTextGetInsertionPosition(app->input), ignored;
	char *text;
	size_t next;

	if (!app->history_count ||
	    (direction < 0 && app->history_pos == 0) ||
	    (direction > 0 && app->history_pos == app->history_count)) return 0;
	if (direction < 0 && cursor &&
	    XmTextFindString(app->input, cursor - 1, "\n", XmTEXT_BACKWARD, &ignored)) return 0;
	if (direction > 0 &&
	    XmTextFindString(app->input, cursor, "\n", XmTEXT_FORWARD, &ignored)) return 0;
	if (app->history_pos == app->history_count) {
		text = XmTextGetString(app->input);
		if (text == NULL) return 0;
		free(app->draft);
		app->draft = strdup(text);
		XtFree(text);
	}
	next = direction < 0 ? app->history_pos - 1 : app->history_pos + 1;
	app->history_pos = next;
	text = next == app->history_count ? app->draft : app->history[next];
	XmTextSetString(app->input, text == NULL ? "" : text);
	XmTextSetInsertionPosition(app->input, XmTextGetLastPosition(app->input));
	return 1;
}

static void
input_key(Widget widget, XtPointer data, XEvent *event, Boolean *dispatch)
{
	App *app = data;
	unsigned state = event->xkey.state;
	KeySym key = XLookupKeysym(&event->xkey, 0);

	(void)widget;
	if ((key == XK_Return || key == XK_KP_Enter) &&
	    !(state & (ShiftMask | ControlMask | Mod1Mask))) send_input(app);
	else if (key == XK_Escape) interrupt(app);
	else if (key == XK_l && state & ControlMask) XmTextSetString(app->input, "");
	else if (key == XK_Page_Up && state & ControlMask) view_page(app->view, -1);
	else if (key == XK_Page_Down && state & ControlMask) view_page(app->view, 1);
	else if (!(state & (ShiftMask | ControlMask | Mod1Mask)) &&
	    (key == XK_Up || key == XK_Down)) {
		if (!recall(app, key == XK_Up ? -1 : 1)) return;
	} else return;
	*dispatch = False;
}

static void
copy_answer(App *app)
{
	uint32_t turn = view_selected_turn(app->view);
	Display *display = XtDisplay(app->shell);
	Window window = XtWindow(app->shell);
	XTextProperty compound;
	XmString title;
	char *text;
	long id;

	if (turn == 0) turn = ask_doc_last_turn(app->doc);
	text = ask_copy_turn(app->doc, turn);
	if (text == NULL && turn != ask_doc_last_turn(app->doc))
		text = ask_copy_turn(app->doc, ask_doc_last_turn(app->doc));
	if (text == NULL) return;
	title = XmStringCreateLocalized("Ask answer");
	if (XmClipboardStartCopy(display, window, title, XtLastTimestampProcessed(display),
	    NULL, NULL, &id) == ClipboardSuccess) {
		XmClipboardRegisterFormat(display, "UTF8_STRING", 8);
		XmClipboardCopy(display, window, id, "UTF8_STRING", text,
		    (unsigned long)strlen(text), 0, NULL);
		if (Xutf8TextListToTextProperty(display, &text, 1, XCompoundTextStyle,
		    &compound) == Success) {
			XmClipboardCopy(display, window, id, "COMPOUND_TEXT", compound.value,
			    compound.nitems, 0, NULL);
			XFree(compound.value);
		}
		XmClipboardEndCopy(display, window, id);
	} else {
		ask_doc_notice(app->doc, "info", "The clipboard is busy. Try Copy Answer again.");
		view_refresh(app->view);
	}
	XmStringFree(title);
	free(text);
}

static void
menu_cb(Widget widget, XtPointer data, XtPointer call)
{
	App *app = data;
	const char *name = XtName(widget);
	Time time = XtLastTimestampProcessed(XtDisplay(widget));
	(void)call;
	if (!strcmp(name, "new")) {
		(void)send_line(app, "/new");
	}
	else if (!strcmp(name, "ledger")) send_line(app, "/ledger");
	else if (!strcmp(name, "terminal")) child_terminal(1);
	else if (!strcmp(name, "close")) quit(app);
	else if (!strcmp(name, "cut")) XmTextCut(app->input, time);
	else if (!strcmp(name, "copy")) XmTextCopy(app->input, time);
	else if (!strcmp(name, "paste")) XmTextPaste(app->input);
	else if (!strcmp(name, "answer")) copy_answer(app);
	else if (!strcmp(name, "commands")) send_line(app, "/help");
	else if (!strcmp(name, "modifiers")) send_line(app, "/mods");
}

static Widget
menu_group(Widget menu, const char *name, const char *caption, KeySym key)
{
	Widget group = XmCreatePulldownMenu(menu, (char *)name, NULL, 0);
	Widget entry = XtVaCreateManagedWidget(name, xmCascadeButtonWidgetClass,
	    menu, XmNsubMenuId, group, XmNmnemonic, key, NULL);
	label(entry, caption);
	return group;
}

static void
menu_item(App *app, Widget group, const char *name, const char *caption, KeySym key)
{
	Widget item = XtVaCreateManagedWidget(name, xmPushButtonWidgetClass,
	    group, XmNmnemonic, key, NULL);
	label(item, caption);
	if (!strcmp(name, "close")) {
		XmString accel = XmStringCreateLocalized("Ctrl+Q");
		XtVaSetValues(item, XmNaccelerator, "Ctrl<Key>q",
		    XmNacceleratorText, accel, NULL);
		XmStringFree(accel);
	}
	XtAddCallback(item, XmNactivateCallback, menu_cb, app);
}

/* View > Text Size is kept in the Deep32 prefs, ~/.dt/d32/d32askw.conf: text_size=auto|small|
 * normal|large|larger. A missing or unreadable file means Auto. */
static const char *const text_size_names[ASK_TEXT_CHOICES] = { "auto", "small", "normal", "large", "larger" };

static int
text_size_load(void)
{
	struct d32_prefs *prefs = NULL;
	int dir = snd_prefs_dir(), choice = ASK_TEXT_AUTO;
	if (dir >= 0) {
		(void)d32_prefs_load(dir, "d32askw", 1, NULL, NULL, &prefs);
		close(dir);
	}
	if (prefs != NULL) {
		const char *name = d32_prefs_get(prefs, "text_size", "auto");
		for (int i = 0; i < ASK_TEXT_CHOICES; i++)
			if (!strcmp(name, text_size_names[i])) choice = i;
	}
	d32_prefs_free(prefs);
	return choice;
}

static int
text_size_save(int choice)
{
	struct d32_prefs *prefs = NULL;
	int dir = snd_prefs_dir(), saved = 0;
	if (dir < 0) return 0;
	if (d32_prefs_load(dir, "d32askw", 1, NULL, NULL, &prefs) == D32E_OK && prefs != NULL)
		saved = d32_prefs_set(prefs, "text_size", text_size_names[choice]) == D32E_OK &&
		    d32_prefs_save(prefs) == D32E_OK;
	close(dir);
	d32_prefs_free(prefs);
	return saved;
}

static void
text_size_cb(Widget widget, XtPointer data, XtPointer call)
{
	App *app = data;
	XmToggleButtonCallbackStruct *toggle = call;
	XtPointer value;
	int choice;

	if (!toggle->set) return;
	XtVaGetValues(widget, XmNuserData, &value, NULL);
	choice = (int)(intptr_t)value;
	if (choice == app->text_size) return;
	app->text_size = choice;
	view_text_size(app->view, choice);
	if (!text_size_save(choice)) {
		ask_doc_notice(app->doc, "info", "Ask could not save the text size. It applies until Ask closes.");
		view_refresh(app->view);
	}
}

/* As the Text Size menu opens, each entry shows the body size it gives in this window. */
static void
text_size_show(Widget widget, XtPointer data, XtPointer call)
{
	App *app = data;
	char px[16];
	(void)widget;
	(void)call;
	for (int i = 0; i < ASK_TEXT_CHOICES; i++) {
		XmString text;
		snprintf(px, sizeof px, "%d px", view_text_px(app->view, i));
		text = XmStringCreateLocalized(px);
		XtVaSetValues(app->sizes[i], XmNacceleratorText, text, NULL);
		XmStringFree(text);
		XmToggleButtonSetState(app->sizes[i], i == app->text_size, False);
	}
}

static void
text_size_menu(App *app, Widget view)
{
	static const char *const captions[ASK_TEXT_CHOICES] = { "Auto", "Small", "Normal", "Large", "Larger" };
	static const KeySym keys[ASK_TEXT_CHOICES] = { XK_A, XK_S, XK_N, XK_L, XK_r };
	Widget sizes = XmCreatePulldownMenu(view, "textSizes", NULL, 0);
	Widget entry = XtVaCreateManagedWidget("textSize", xmCascadeButtonWidgetClass, view,
	    XmNsubMenuId, sizes, XmNmnemonic, XK_T, NULL);

	label(entry, "Text Size");
	XtVaSetValues(sizes, XmNradioBehavior, True, XmNradioAlwaysOne, True, NULL);
	XtAddCallback(entry, XmNcascadingCallback, text_size_show, app);
	for (int i = 0; i < ASK_TEXT_CHOICES; i++) {
		app->sizes[i] = XtVaCreateManagedWidget(text_size_names[i], xmToggleButtonWidgetClass, sizes,
		    XmNindicatorType, XmONE_OF_MANY_DIAMOND, XmNvisibleWhenOff, True,
		    XmNset, i == app->text_size ? XmSET : XmUNSET, XmNmnemonic, keys[i],
		    XmNuserData, (XtPointer)(intptr_t)i, NULL);
		label(app->sizes[i], captions[i]);
		XtAddCallback(app->sizes[i], XmNvalueChangedCallback, text_size_cb, app);
		if (i == ASK_TEXT_AUTO)
			XtVaCreateManagedWidget("autoRule", xmSeparatorWidgetClass, sizes, NULL);
	}
}

static Widget
menus(App *app, Widget parent)
{
	Widget menu = XmCreateMenuBar(parent, "menu", NULL, 0);
	Widget file = menu_group(menu, "file", "File", XK_F);
	Widget edit = menu_group(menu, "edit", "Edit", XK_E);
	Widget view = menu_group(menu, "view", "View", XK_V);
	Widget help = menu_group(menu, "help", "Help", XK_H);

	XtVaSetValues(menu, XmNshadowThickness, 2, XmNmarginHeight, ask_ui(2), NULL);
	text_size_menu(app, view);
	menu_item(app, file, "new", "New Session", XK_N);
	menu_item(app, file, "ledger", "Ledger", XK_L);
	menu_item(app, file, "terminal", "Open Terminal Ask", XK_T);
	menu_item(app, file, "close", "Close", XK_C);
	menu_item(app, edit, "cut", "Cut", XK_t);
	menu_item(app, edit, "copy", "Copy", XK_C);
	menu_item(app, edit, "paste", "Paste", XK_P);
	menu_item(app, edit, "answer", "Copy Answer", XK_A);
	menu_item(app, help, "commands", "Commands", XK_C);
	menu_item(app, help, "modifiers", "Modifiers", XK_M);
	XtVaSetValues(menu, XmNmenuHelpWidget, XtNameToWidget(menu, "help"), NULL);
	XtManageChild(menu);
	return menu;
}

/* A titled, etched group box: the CDE dialog section. */
static Widget
group(Widget parent, const char *name, const char *title)
{
	Widget frame = XtVaCreateManagedWidget(name, xmFrameWidgetClass, parent,
	    XmNshadowType, XmSHADOW_ETCHED_IN, XmNshadowThickness, 2,
	    XmNmarginWidth, ask_ui(8), XmNmarginHeight, ask_ui(6), NULL);
	Widget caption = XtVaCreateManagedWidget("wellTitle", xmLabelWidgetClass, frame,
	    XmNchildType, XmFRAME_TITLE_CHILD, XmNchildHorizontalAlignment, XmALIGNMENT_BEGINNING,
	    XmNchildHorizontalSpacing, ask_ui(6), XmNmarginWidth, ask_ui(3), XmNmarginHeight, 0, NULL);
	label(caption, title);
	return frame;
}

/* ring: an action-row button that keeps room for the CDE default-button ring. */
static Widget
push(Widget parent, const char *name, const char *caption, XtCallbackProc activate, App *app, int ring)
{
	Widget button = XtVaCreateManagedWidget(name, xmPushButtonWidgetClass, parent,
	    XmNshadowThickness, 2, XmNmarginWidth, ask_ui(10), XmNmarginHeight, ask_ui(3),
	    XmNdefaultButtonShadowThickness, ring ? 1 : 0, NULL);
	label(button, caption);
	XtAddCallback(button, XmNactivateCallback, activate, app);
	return button;
}

static Widget
input_pane(App *app, Widget pane)
{
	Widget form, frame, session, rule, stack, question, hint, buttons, scroll, compose;
	Arg args[5];
	Cardinal n = 0;

	form = XtVaCreateManagedWidget("inputPane", xmFormWidgetClass, pane,
	    XmNwidth, ask_ui(340), XmNpaneMinimum, ask_ui(260), XmNskipAdjust, True,
	    XmNmarginWidth, ask_ui(8), XmNmarginHeight, ask_ui(8), NULL);
	frame = group(form, "linkFrame", "Relay Connection");
	XtVaSetValues(frame, XmNtopAttachment, XmATTACH_FORM, XmNleftAttachment, XmATTACH_FORM,
	    XmNrightAttachment, XmATTACH_FORM, NULL);
	stack = XtVaCreateManagedWidget("linkStack", xmRowColumnWidgetClass, frame,
	    XmNorientation, XmVERTICAL, XmNmarginWidth, 0, XmNmarginHeight, ask_ui(2), XmNspacing, ask_ui(2), NULL);
	app->link = XtVaCreateManagedWidget("link", xmLabelWidgetClass, stack,
	    XmNalignment, XmALIGNMENT_BEGINNING, XmNrecomputeSize, False,
	    XmNheight, ask_ui(40), NULL);
	app->modifiers = XtVaCreateManagedWidget("modifiers", xmLabelWidgetClass, stack,
	    XmNalignment, XmALIGNMENT_BEGINNING, XmNrecomputeSize, False, NULL);
	app->login = XtVaCreateWidget("codexLogin", xmPushButtonWidgetClass, stack,
	    XmNshadowThickness, 2, XmNmarginWidth, ask_ui(10), NULL);
	label(app->login, "Sign in to Codex");
	XtAddCallback(app->login, XmNactivateCallback, login_begin, app);
	session = group(form, "sessionFrame", "Session");
	XtVaSetValues(session, XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, frame, XmNtopOffset, ask_ui(10),
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
	compose = XtVaCreateWidget("compose", xmFormWidgetClass, form,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM,
	    XmNbottomAttachment, XmATTACH_FORM, NULL);
	app->session = ask_session_widgets_create(session, compose, app, profile_changed, password_submitted);
	if (!app->session) return NULL;
	rule = XtVaCreateManagedWidget("composeRule", xmSeparatorWidgetClass, form,
	    XmNseparatorType, XmSHADOW_ETCHED_IN, XmNshadowThickness, 2,
	    XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, session, XmNtopOffset, ask_ui(10),
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
	XtVaSetValues(compose, XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, rule,
	    XmNtopOffset, ask_ui(8), NULL);
	question = XtVaCreateManagedWidget("questionLabel", xmLabelWidgetClass, compose,
	    XmNtopAttachment, XmATTACH_FORM,
	    XmNleftAttachment, XmATTACH_FORM, NULL);
	label(question, "Question or command");
	hint = XtVaCreateManagedWidget("hint", xmLabelWidgetClass, compose,
	    XmNbottomAttachment, XmATTACH_FORM, XmNleftAttachment, XmATTACH_FORM,
	    XmNrightAttachment, XmATTACH_FORM, XmNalignment, XmALIGNMENT_BEGINNING,
	    XmNrecomputeSize, False, NULL);
	label(hint, "Type /help for commands, /mods for modifiers.");
	buttons = XtVaCreateManagedWidget("buttons", xmRowColumnWidgetClass, compose,
	    XmNbottomAttachment, XmATTACH_WIDGET, XmNbottomWidget, hint, XmNbottomOffset, ask_ui(6),
	    XmNleftAttachment, XmATTACH_FORM, XmNorientation, XmHORIZONTAL,
	    XmNpacking, XmPACK_TIGHT, XmNspacing, ask_ui(8), XmNmarginWidth, 0, NULL);
	app->send = push(buttons, "send", "Send", send_cb, app, 1);
	app->cancel = push(buttons, "cancel", "Cancel", cancel_cb, app, 1);
	XtVaSetValues(app->send, XmNshowAsDefault, 1, NULL); /* Return sends */
	XtSetSensitive(app->send, False);
	XtSetSensitive(app->cancel, False);
	XtSetArg(args[n], XmNeditMode, XmMULTI_LINE_EDIT); n++;
	XtSetArg(args[n], XmNwordWrap, True); n++;
	XtSetArg(args[n], XmNscrollHorizontal, False); n++;
	XtSetArg(args[n], XmNmaxLength, INPUT_LIMIT); n++;
	XtSetArg(args[n], XmNrows, 12); n++;
	app->input = XmCreateScrolledText(compose, "question", args, n);
	scroll = XtParent(app->input);
	XtVaSetValues(scroll, XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, question,
	    XmNtopOffset, ask_ui(5), XmNbottomAttachment, XmATTACH_WIDGET, XmNbottomWidget, buttons,
	    XmNbottomOffset, ask_ui(8), XmNleftAttachment, XmATTACH_FORM,
	    XmNrightAttachment, XmATTACH_FORM, NULL);
	XtAddEventHandler(app->input, KeyPressMask, False, input_key, app);
	XtManageChild(app->input);
	return form;
}

/* The right pane: a titled Transcript group, the session header over the answer view. */
static int
transcript_pane(App *app, Widget pane, Widget left)
{
	Widget right, frame, body, rule;

	right = XtVaCreateManagedWidget("rightPane", xmFormWidgetClass, pane,
	    XmNwidth, ask_ui(620), XmNpaneMinimum, ask_ui(360), XmNpaneMaximum, SHRT_MAX,
	    XmNmarginWidth, ask_ui(8), XmNmarginHeight, ask_ui(8), NULL);
	frame = group(right, "transcriptFrame", "Transcript");
	XtVaSetValues(frame, XmNtopAttachment, XmATTACH_FORM, XmNbottomAttachment, XmATTACH_FORM,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM,
	    XmNmarginWidth, ask_ui(6), XmNmarginHeight, ask_ui(6), NULL);
	body = XtVaCreateManagedWidget("transcriptBody", xmFormWidgetClass, frame,
	    XmNmarginWidth, 0, XmNmarginHeight, 0, NULL);
	app->header = XtVaCreateManagedWidget("sessionHeader", xmLabelWidgetClass, body,
	    XmNtopAttachment, XmATTACH_FORM, XmNleftAttachment, XmATTACH_FORM,
	    XmNrightAttachment, XmATTACH_FORM, XmNalignment, XmALIGNMENT_BEGINNING,
	    XmNmarginHeight, ask_ui(2), XmNrecomputeSize, False, NULL);
	show_session(app);
	rule = XtVaCreateManagedWidget("headerRule", xmSeparatorWidgetClass, body,
	    XmNseparatorType, XmSHADOW_ETCHED_IN, XmNshadowThickness, 2,
	    XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, app->header, XmNtopOffset, ask_ui(3),
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
	app->view = view_create(body, app->shell, left, app->doc, app, open_reference, app->panel, app->text_size);
	if (app->view == NULL) return 0;
	XtVaSetValues(view_widget(app->view), XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, rule,
	    XmNtopOffset, ask_ui(6), NULL);
	return 1;
}

/* The panel's dpi: D32ASK_DPI, else XRandR's connected output under the pointer (where the
 * window will open; the first connected one with a size when none is), its CRTC's pixels over
 * its millimetres; 0 when unknown (Xvfb reports 0 mm). The X server's own dpi is not used:
 * Deep32 forces it to 100 whatever the panel. the Library's panel_dpi() rule. */
static int
panel_dpi(Display *display)
{
	const char *env = getenv("D32ASK_DPI");
	Window root = DefaultRootWindow(display), root_return, child;
	int event, error, major = 0, minor = 0, x = -1, y = -1, ignore, dpi = 0, under = 0;
	unsigned mask;
	XRRScreenResources *res;

	if (env != NULL && *env) {
		char *end;
		long value = strtol(env, &end, 10);
		if (!*end && value >= 50 && value <= 600) return (int)value;
	}
	if (!XRRQueryExtension(display, &event, &error) || !XRRQueryVersion(display, &major, &minor) ||
	    major < 1 || (major == 1 && minor < 2))
		return 0;
	if (!XQueryPointer(display, root, &root_return, &child, &x, &y, &ignore, &ignore, &mask)) x = y = -1;
	res = major > 1 || minor >= 3 ? XRRGetScreenResourcesCurrent(display, root) : XRRGetScreenResources(display, root);
	if (res == NULL) return 0;
	for (int i = 0; i < res->noutput && !under; i++) {
		XRROutputInfo *out = XRRGetOutputInfo(display, res, res->outputs[i]);
		XRRCrtcInfo *crtc;
		if (out == NULL) continue;
		if (out->connection == RR_Connected && out->mm_width > 0 && out->crtc &&
		    (crtc = XRRGetCrtcInfo(display, res, out->crtc)) != NULL) {
			long px = crtc->rotation & (RR_Rotate_90 | RR_Rotate_270) ? (long)crtc->height : (long)crtc->width;
			int d = (int)((px * 254 + (long)out->mm_width * 5) / ((long)out->mm_width * 10));
			if (x >= crtc->x && x < crtc->x + (int)crtc->width && y >= crtc->y && y < crtc->y + (int)crtc->height) {
				dpi = d;
				under = 1;
			} else if (!dpi) dpi = d;
			XRRFreeCrtcInfo(crtc);
		}
		XRRFreeOutputInfo(out);
	}
	XRRFreeScreenResources(res);
	return dpi;
}

/* Controls at 17 px: the stock D32Askw control faces become their 17 px 100dpi natives. A
 * user's own fontList is left alone. Runs before any widget reads its resources. */
static void
chrome_fonts(Display *display)
{
#define FACE(family, weight, spacing, size) \
	"-adobe-" family "-" weight "-r-normal--" size "-*-100-100-" spacing "-*-iso8859-1"
	static const struct { const char *spec, *name, *class, *stock, *large; } list[] = {
		{ "*fontList", "d32-askw.fontList", "D32Askw.FontList",
		  FACE("helvetica", "medium", "p", "14"), FACE("helvetica", "medium", "p", "17") },
		{ "*wellTitle.fontList", "d32-askw.wellTitle.fontList", "D32Askw.XmLabel.FontList",
		  FACE("helvetica", "bold", "p", "14"), FACE("helvetica", "bold", "p", "17") },
		{ "*sessionHeader.fontList", "d32-askw.sessionHeader.fontList", "D32Askw.XmLabel.FontList",
		  FACE("helvetica", "bold", "p", "14"), FACE("helvetica", "bold", "p", "17") },
		{ "*XmText.fontList", "d32-askw.question.fontList", "D32Askw.XmText.FontList",
		  FACE("courier", "medium", "m", "14"), FACE("courier", "medium", "m", "17") },
		{ "*XmTextField.fontList", "d32-askw.questionNumber.fontList", "D32Askw.XmTextField.FontList",
		  FACE("courier", "medium", "m", "14"), FACE("courier", "medium", "m", "17") },
	};
#undef FACE
	XrmDatabase db = XtDatabase(display);

	for (size_t i = 0; i < XtNumber(list); i++) {
		char *type = NULL;
		XrmValue value = { 0, NULL };
		if (!XrmGetResource(db, list[i].name, list[i].class, &type, &value) || value.addr == NULL ||
		    !strcasecmp((const char *)value.addr, list[i].stock))
			XrmPutStringResource(&db, list[i].spec, list[i].large);
	}
	XrmSetDatabase(display, db);
}

int
main(int argc, char **argv)
{
	static String fallback[] = {
	    "*XmText.marginWidth: 7", "*XmText.marginHeight: 6",
	    "*XmPushButton.marginWidth: 8", NULL
	};
	App app = {0};
	Display *display;
	Widget main_window, pane, left, bar;
	Atom wm_delete;
	XrmDatabase resource_db;
	int dpi, percent, width, height;

	setlocale(LC_ALL, "");
	XtToolkitInitialize();
	XtSetLanguageProc(NULL, NULL, NULL);
	app.context = XtCreateApplicationContext();
	XtAppSetFallbackResources(app.context, fallback);
	display = XtOpenDisplay(app.context, NULL, "d32-askw", "D32Askw", NULL, 0, &argc, argv);
	if (display == NULL) {
		child_terminal(0);
		fputs("Ask could not open the display or start Terminal Ask.\n", stderr);
		return 1;
	}
	if (argc != 1) {
		fputs("Usage: d32-askw [standard Xt options]\n", stderr);
		return 2;
	}
	/* Use the packaged defaults even on X servers with another Xt search path. */
	resource_db = XtDatabase(display);
	if (XrmCombineFileDatabase("/etc/X11/app-defaults/D32Askw", &resource_db, False))
		XrmSetDatabase(display, resource_db);
	/* The panel sets the scale, as in Library: 150% from 140 dpi (the laptop is 158), 125%
	 * from 115, else 100%. At 150% the controls step to 17 px and the window opens larger. */
	dpi = panel_dpi(display);
	app.panel = dpi >= 140 ? ASK_PANEL_150 : dpi >= 115 ? ASK_PANEL_125 : ASK_PANEL_100;
	percent = app.panel == ASK_PANEL_150 ? 150 : app.panel == ASK_PANEL_125 ? 125 : 100;
	if (app.panel == ASK_PANEL_150) {
		ask_ui_font(17);
		chrome_fonts(display);
	}
	width = 960 * percent / 100;
	height = 640 * percent / 100;
	if (percent > 100) {
		if (width > WidthOfScreen(DefaultScreenOfDisplay(display)) * 9 / 10)
			width = WidthOfScreen(DefaultScreenOfDisplay(display)) * 9 / 10;
		if (height > HeightOfScreen(DefaultScreenOfDisplay(display)) * 22 / 25)
			height = HeightOfScreen(DefaultScreenOfDisplay(display)) * 22 / 25;
	}
	app.text_size = text_size_load();
	app.doc = ask_doc_new();
	if (app.doc == NULL) return 1;
	app.shell = XtVaAppCreateShell("d32-askw", "D32Askw",
	    applicationShellWidgetClass, display, XtNtitle, "Ask Deep32",
	    XtNiconName, "Ask", XtNwidth, width, XtNheight, height,
	    XtNminWidth, ask_ui(625), XtNminHeight, ask_ui(340), XmNdeleteResponse, XmDO_NOTHING,
	    /* A zero base size keeps -geometry WxH exact (else the minimum size is added). */
	    XtNbaseWidth, 0, XtNbaseHeight, 0, NULL);
	wm_delete = XmInternAtom(display, "WM_DELETE_WINDOW", False);
	XmAddWMProtocolCallback(app.shell, wm_delete, close_cb, &app);
	main_window = XtVaCreateManagedWidget("main", xmMainWindowWidgetClass, app.shell,
	    XmNshowSeparator, True, NULL);
	bar = menus(&app, main_window);
	pane = XtVaCreateManagedWidget("panes", xmPanedWindowWidgetClass, main_window,
	    XmNorientation, XmHORIZONTAL, XmNmarginWidth, ask_ui(2), XmNmarginHeight, ask_ui(2),
	    XmNspacing, ask_ui(8), XmNsashWidth, ask_ui(10), XmNsashHeight, ask_ui(16), XmNsashShadowThickness, 2, NULL);
	left = input_pane(&app, pane);
	if (left == NULL) return 1;
	if (!transcript_pane(&app, pane, left)) return 1;
	XmMainWindowSetAreas(main_window, bar, NULL, NULL, NULL, pane);
	XtRealizeWidget(app.shell);
	ask_session_widgets_focus(app.session);
	app.sound = ask_sound_create(app.context);
	app.child = child_start(app.context, &app, received, child_ended);
	if (app.child == NULL) child_ended(&app, 1);
	else sync_ui(&app);
	XtAppMainLoop(app.context);
	ask_sound_free(app.sound);
	XtDestroyWidget(app.shell);
	free(app.caps);
	free(app.pending_caps);
	explicit_bzero(app.confirmation, sizeof app.confirmation);
	ask_doc_free(app.doc);
	return 0;
}
