#include "d32ui.h"
#include "d32_prefs.h"

#include <stdlib.h>
#include <X11/StringDefs.h>
#include <X11/keysym.h>
#include <Xm/FileSB.h>
#include <Xm/MenuShell.h>
#include <Xm/MessageB.h>
#include <Xm/Protocols.h>
#include <Xm/Text.h>

#ifdef D32UI_WITH_DTHELP
#include <Dt/Help.h>
#include <Dt/HelpDialog.h>
#endif

enum dialog_kind { DISMISS, CHOICE, PROGRESS, PATH };

struct dialog_state {
	Widget dialog, shell, opener;
	enum dialog_kind kind;
	bool closing, in_dismiss, cancel_requested;
	union {
		d32ui_choice_fn choice;
		d32ui_cancel_fn cancel;
		d32ui_path_fn path;
	} callback;
	void *arg;
};

static Widget
dialog_parent(const d32ui *ui, Widget opener)
{
	Widget parent = opener;

	if (!ui)
		return NULL;
	while (parent && (!XtIsShell(parent) || XmIsMenuShell(parent)))
		parent = XtParent(parent);
	return parent ? parent : d32ui_shell(ui);
}

static void
forget_opener(Widget opener, XtPointer closure, XtPointer call)
{
	struct dialog_state *state = closure;

	(void)opener;
	(void)call;
	state->opener = NULL;
}

static void
forget_dialog(Widget dialog, XtPointer closure, XtPointer call)
{
	struct dialog_state *state = closure;

	(void)dialog;
	(void)call;
	state->dialog = NULL;
}

static void
destroy_state(Widget shell, XtPointer closure, XtPointer call)
{
	struct dialog_state *state = closure;

	(void)shell;
	(void)call;
	if (state->opener)
		XtRemoveCallback(state->opener, XtNdestroyCallback, forget_opener,
		    state);
	state->opener = NULL;
	state->shell = NULL;
	if (!state->in_dismiss)
		free(state);
}

static void
dismiss(struct dialog_state *state)
{
	if (state->closing || !state->dialog)
		return;
	state->closing = true;
	state->in_dismiss = true;
	/* Unmanage first so the DialogShell removes its input grab before
	 * returning traversal to a still-live opener. */
	XtUnmanageChild(state->dialog);
	if (state->opener && XtIsManaged(state->opener) &&
	    XtIsRealized(state->opener) && XtIsSensitive(state->opener))
		(void)XmProcessTraversal(state->opener, XmTRAVERSE_CURRENT);
	if (state->shell)
		XtDestroyWidget(state->shell);
	state->in_dismiss = false;
	if (!state->shell)
		free(state);
}

static void
choose(struct dialog_state *state, bool accepted)
{
	d32ui_choice_fn callback = state->callback.choice;
	void *arg = state->arg;

	dismiss(state);
	if (callback)
		callback(accepted, arg);
}

static void
cancel_dialog(Widget widget, XtPointer closure, XtPointer call)
{
	struct dialog_state *state = closure;
	Widget button;

	(void)widget;
	(void)call;
	if (state->closing || !state->dialog)
		return;
	if (state->kind == CHOICE) {
		choose(state, false);
	} else if (state->kind == PROGRESS && state->callback.cancel) {
		if (state->cancel_requested)
			return;
		state->cancel_requested = true;
		button = XmMessageBoxGetChild(state->dialog, XmDIALOG_CANCEL_BUTTON);
		XtSetSensitive(button, False);
		/* Cancellation is asynchronous. The caller closes after reaping. */
		state->callback.cancel(state->arg);
	} else {
		dismiss(state);
	}
}

static void
accept_dialog(Widget widget, XtPointer closure, XtPointer call)
{
	struct dialog_state *state = closure;
	XmFileSelectionBoxCallbackStruct *selection;
	d32ui_path_fn callback;
	void *arg;
	char *path;

	(void)widget;
	if (state->closing || !state->dialog)
		return;
	if (state->kind == CHOICE) {
		choose(state, true);
	} else if (state->kind == PATH) {
		selection = call;
		if (!selection || !selection->value)
			return;
		path = XmStringUnparse(selection->value, NULL, XmCHARSET_TEXT,
		    XmMULTIBYTE_TEXT, NULL, 0, XmOUTPUT_ALL);
		if (!path)
			return;
		if (*path) {
			callback = state->callback.path;
			arg = state->arg;
			dismiss(state);
			if (callback)
				callback(path, arg);
		}
		XtFree(path);
	} else {
		dismiss(state);
	}
}

static void
dialog_keys(Widget widget, XtPointer closure, XEvent *event, Boolean *dispatch)
{
	struct dialog_state *state = closure;
	KeySym key;

	if (event->type != KeyPress)
		return;
	key = XLookupKeysym(&event->xkey, 0);
	if (key == XK_Escape || (state->kind == CHOICE &&
	    (key == XK_Return || key == XK_KP_Enter))) {
		/* Return on a confirmation always chooses its safe default. An
		 * explicitly focused Proceed button can be activated with Space. */
		*dispatch = False;
		cancel_dialog(widget, closure, NULL);
	}
}

static void
watch_keys(Widget widget, struct dialog_state *state)
{
	WidgetList children;
	Cardinal count, i;

	if (!XtIsWidget(widget))
		return;
	XtInsertEventHandler(widget, KeyPressMask, False, dialog_keys, state,
	    XtListHead);
	if (XtIsComposite(widget)) {
		XtVaGetValues(widget, XtNchildren, &children, XtNnumChildren,
		    &count, NULL);
		for (i = 0; i < count; i++)
			watch_keys(children[i], state);
	}
}

static struct dialog_state *
attach(Widget dialog, Widget opener, enum dialog_kind kind)
{
	struct dialog_state *state;
	Widget shell;
	Atom wm_delete;

	if (!dialog)
		return NULL;
	shell = XtParent(dialog);
	state = calloc(1, sizeof(*state));
	if (!state) {
		XtDestroyWidget(shell);
		return NULL;
	}
	state->dialog = dialog;
	state->shell = shell;
	state->opener = opener;
	state->kind = kind;
	if (opener)
		XtAddCallback(opener, XtNdestroyCallback, forget_opener, state);
	XtAddCallback(dialog, XtNdestroyCallback, forget_dialog, state);
	XtAddCallback(shell, XtNdestroyCallback, destroy_state, state);
	XtVaSetValues(shell, XmNdeleteResponse, XmDO_NOTHING, NULL);
	wm_delete = XmInternAtom(XtDisplay(shell), "WM_DELETE_WINDOW", False);
	XmAddWMProtocols(shell, &wm_delete, 1);
	XmAddWMProtocolCallback(shell, wm_delete, cancel_dialog, state);
	return state;
}

static Widget
show(d32ui *ui, struct dialog_state *state)
{
	Widget dialog = state->dialog;

	d32ui_tag_dialog(ui, dialog);
	watch_keys(state->shell, state);
	XtManageChild(dialog);
	return dialog;
}

static void
hide_button(Widget dialog, unsigned char which)
{
	Widget child = XmMessageBoxGetChild(dialog, which);

	if (child)
		XtUnmanageChild(child);
}

static void
message_setup(Widget dialog, const char *title, const char *text,
    unsigned char style)
{
	XmString label = XmStringCreateLocalized((char *)title);
	XmString message = XmStringCreateLocalized((char *)(text ? text : ""));

	XtVaSetValues(dialog, XmNdialogTitle, label, XmNmessageString, message,
	    XmNdialogStyle, style, XmNautoUnmanage, False, NULL);
	XmStringFree(message);
	XmStringFree(label);
	hide_button(dialog, XmDIALOG_HELP_BUTTON);
}

static void
default_button(Widget dialog, unsigned char which)
{
	Widget child = XmMessageBoxGetChild(dialog, which);

	XtVaSetValues(dialog, XmNdefaultButtonType, which, XmNinitialFocus,
	    child, NULL);
}

static void
message_callbacks(struct dialog_state *state)
{
	XtAddCallback(state->dialog, XmNokCallback, accept_dialog, state);
	XtAddCallback(state->dialog, XmNcancelCallback, cancel_dialog, state);
}

static const char *
error_text(enum d32_error error)
{
	switch (error) {
	case D32E_NOENT: return "[x] The requested item is missing.";
	case D32E_LINK: return "[x] Links are not accepted here.";
	case D32E_NOTREG: return "[x] Select a regular file.";
	case D32E_TOOBIG: return "[x] The file exceeds the size limit.";
	case D32E_EMPTY: return "[x] The file is empty.";
	case D32E_PERM: return "[x] Permission was denied.";
	case D32E_INVAL: return "[x] A setting or selection is invalid.";
	case D32E_IO: return "[x] The read or write did not complete.";
	case D32E_NOMEM: return "[x] There is not enough memory.";
	case D32E_FORMAT: return "[x] The file format is invalid.";
	case D32E_LIMIT: return "[x] The operation exceeds a supported limit.";
	case D32E_EXISTS: return "[x] An item with that name already exists.";
	case D32E_NEWER: return "[!] " D32_PREFS_NEWER_STATUS;
	case D32E_VERSION: return "[x] This file version is unsupported.";
	case D32E_UNAVAIL: return "[?] This operation is unavailable.";
	case D32E_TEMP: return "[!] The operation failed. Try again.";
	case D32E_CREATE: return "[x] The item could not be created.";
	case D32E_OK: break;
	}
	return "[x] The operation could not be completed.";
}

Widget
d32ui_error(d32ui *ui, Widget opener, enum d32_error error,
    const char *detail_token)
{
	Widget parent = dialog_parent(ui, opener), dialog;
	struct dialog_state *state;

	/* Fixed text only. Tokens and system error strings stay out of the UI. */
	(void)detail_token;
	if (!parent)
		return NULL;
	dialog = XmCreateErrorDialog(parent, "d32Error", NULL, 0);
	state = attach(dialog, opener, DISMISS);
	if (!state)
		return NULL;
	message_setup(dialog, "Error", error_text(error), XmDIALOG_PRIMARY_APPLICATION_MODAL);
	hide_button(dialog, XmDIALOG_CANCEL_BUTTON);
	default_button(dialog, XmDIALOG_OK_BUTTON);
	message_callbacks(state);
	return show(ui, state);
}

Widget
d32ui_confirm(d32ui *ui, Widget opener, const char *question,
    d32ui_choice_fn callback, void *arg)
{
	Widget parent = dialog_parent(ui, opener), dialog;
	struct dialog_state *state;
	XmString label;

	if (!parent || !question || !*question)
		return NULL;
	dialog = XmCreateQuestionDialog(parent, "d32Confirm", NULL, 0);
	state = attach(dialog, opener, CHOICE);
	if (!state)
		return NULL;
	state->callback.choice = callback;
	state->arg = arg;
	message_setup(dialog, "Confirm", question, XmDIALOG_PRIMARY_APPLICATION_MODAL);
	label = XmStringCreateLocalized("Proceed");
	XtVaSetValues(dialog, XmNokLabelString, label, NULL);
	XmStringFree(label);
	default_button(dialog, XmDIALOG_CANCEL_BUTTON);
	message_callbacks(state);
	return show(ui, state);
}

Widget
d32ui_progress(d32ui *ui, Widget opener, const char *message,
    d32ui_cancel_fn callback, void *arg)
{
	Widget parent = dialog_parent(ui, opener), dialog;
	struct dialog_state *state;

	if (!parent)
		return NULL;
	dialog = XmCreateWorkingDialog(parent, "d32Progress", NULL, 0);
	state = attach(dialog, opener, PROGRESS);
	if (!state)
		return NULL;
	state->callback.cancel = callback;
	state->arg = arg;
	message_setup(dialog, "Progress", message ? message : "Working...", XmDIALOG_MODELESS);
	XtVaSetValues(dialog, XmNuserData, state, NULL);
	hide_button(dialog, XmDIALOG_OK_BUTTON);
	default_button(dialog, XmDIALOG_CANCEL_BUTTON);
	message_callbacks(state);
	return show(ui, state);
}

void
d32ui_progress_close(Widget dialog)
{
	struct dialog_state *state = NULL;

	/* The caller passes the still-live Widget from d32ui_progress. */
	if (dialog && XmIsMessageBox(dialog))
		XtVaGetValues(dialog, XmNuserData, &state, NULL);
	if (state && state->kind == PROGRESS && state->dialog == dialog)
		dismiss(state);
}

Widget
d32ui_file_select(d32ui *ui, Widget opener, const char *pattern,
    d32ui_path_fn callback, void *arg)
{
	Widget parent = dialog_parent(ui, opener), dialog, help;
	struct dialog_state *state;
	XmString title, filter;

	if (!parent || !callback)
		return NULL;
	dialog = XmCreateFileSelectionDialog(parent, "d32FileSelect", NULL, 0);
	state = attach(dialog, opener, PATH);
	if (!state)
		return NULL;
	state->callback.path = callback;
	state->arg = arg;
	title = XmStringCreateLocalized("Open File");
	filter = XmStringCreateLocalized((char *)(pattern && *pattern ? pattern : "*"));
	XtVaSetValues(dialog, XmNdialogTitle, title, XmNpattern, filter,
	    XmNpathMode, XmPATH_MODE_FULL, XmNautoUnmanage, False,
	    XmNdialogStyle, XmDIALOG_PRIMARY_APPLICATION_MODAL, NULL);
	XmStringFree(filter);
	XmStringFree(title);
	help = XmFileSelectionBoxGetChild(dialog, XmDIALOG_HELP_BUTTON);
	if (help)
		XtUnmanageChild(help);
	message_callbacks(state);
	return show(ui, state);
}

Widget
d32ui_help(d32ui *ui, Widget opener, const char *volume, const char *location,
    const char *fallback)
{
	Widget parent = dialog_parent(ui, opener), dialog, text;
	struct dialog_state *state;

	if (!parent)
		return NULL;
#ifdef D32UI_WITH_DTHELP
	if (volume && *volume && location && *location) {
		Arg args[3];

		XtSetArg(args[0], DtNhelpType, DtHELP_TYPE_TOPIC);
		XtSetArg(args[1], DtNhelpVolume, volume);
		XtSetArg(args[2], DtNlocationId, location);
		dialog = DtCreateHelpDialog(parent, "d32Help", args, XtNumber(args));
		if (dialog) {
			state = attach(dialog, opener, DISMISS);
			if (!state)
				return NULL;
			XtAddCallback(dialog, DtNcloseCallback, cancel_dialog, state);
			return show(ui, state);
		}
	}
#else
	(void)volume;
	(void)location;
#endif
	dialog = XmCreateInformationDialog(parent, "d32HelpText", NULL, 0);
	state = attach(dialog, opener, DISMISS);
	if (!state)
		return NULL;
	message_setup(dialog, "Help", NULL, XmDIALOG_MODELESS);
	hide_button(dialog, XmDIALOG_CANCEL_BUTTON);
	hide_button(dialog, XmDIALOG_SYMBOL_LABEL);
	hide_button(dialog, XmDIALOG_MESSAGE_LABEL);
	text = XmCreateScrolledText(dialog, "helpText", NULL, 0);
	XtVaSetValues(text, XmNeditMode, XmMULTI_LINE_EDIT, XmNeditable, False,
	    XmNcursorPositionVisible, False, XmNwordWrap, True,
	    XmNscrollHorizontal, False, XmNrows, 12, XmNcolumns, 60, NULL);
	XmTextSetString(text, (char *)(fallback ? fallback : "Help is unavailable."));
	XtManageChild(text);
	default_button(dialog, XmDIALOG_OK_BUTTON);
	message_callbacks(state);
	return show(ui, state);
}
