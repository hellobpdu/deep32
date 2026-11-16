#include "d32ui.h"
#include "policy.h"

#include <X11/Shell.h>
#include <Xm/Label.h>
#include <Xm/PushB.h>
#include <Xm/RowColumn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sysexits.h>
#include <unistd.h>

static const char unavailable[] = "Lab Capture is not configured on this system";

static bool
local_display(const char *display)
{
	size_t n;

	if (display == NULL)
		return false;
	if (strncmp(display, "unix:", 5) == 0)
		display += 4;
	if (*display++ != ':')
		return false;
	n = strspn(display, "0123456789");
	if (n == 0 || n > 5)
		return false;
	display += n;
	if (*display == '\0')
		return true;
	return *display == '.' && display[1] != '\0' &&
	    display[1 + strspn(display + 1, "0123456789")] == '\0';
}

static int
exit_status(int code, const char *result, const char *detail)
{
	fprintf(stderr, "d32: tool=d32capdlg verb=open result=%s detail=%s\n", result, detail);
	return code;
}

static void
label(Widget w, const char *text)
{
	XmString s = XmStringCreateLocalized((char *)text);

	XtVaSetValues(w, XmNlabelString, s, NULL);
	XmStringFree(s);
}

static void
help(Widget w, XtPointer data, XtPointer call)
{
	static const char text[] =
	    "Lab Capture needs a reviewed Lab policy and\n"
	    "a matching interface identity.\n\n"
	    "The approved capture and viewer helpers need\n"
	    "verified non-interactive authorization.";

	(void)call;
	(void)d32ui_help(data, w, NULL, NULL, text);
}

static void
close_dialog(Widget w, XtPointer data, XtPointer call)
{
	(void)w;
	(void)call;
	d32ui_quit(data);
}

static void
build(d32ui *ui)
{
	Widget column, buttons, text, button;

	column = XtVaCreateManagedWidget("captureStatus", xmRowColumnWidgetClass, d32ui_body(ui),
	    XmNorientation, XmVERTICAL, XmNpacking, XmPACK_TIGHT,
	    XmNmarginWidth, 18, XmNmarginHeight, 18,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM,
	    XmNtopAttachment, XmATTACH_FORM, NULL);
	text = XtVaCreateManagedWidget("unavailable", xmLabelWidgetClass, column,
	    XmNalignment, XmALIGNMENT_BEGINNING, XmNmarginHeight, 9, NULL);
	label(text, unavailable);
	buttons = XtVaCreateManagedWidget("actions", xmRowColumnWidgetClass, column,
	    XmNorientation, XmHORIZONTAL, XmNpacking, XmPACK_TIGHT, NULL);
	button = XtVaCreateManagedWidget("help", xmPushButtonWidgetClass, buttons, NULL);
	label(button, "Help");
	XtAddCallback(button, XmNactivateCallback, help, ui);
	button = XtVaCreateManagedWidget("close", xmPushButtonWidgetClass, buttons, NULL);
	label(button, "Close");
	XtAddCallback(button, XmNactivateCallback, close_dialog, ui);
	d32ui_on_close(ui, close_dialog, ui);
}

int
main(int argc, char **argv)
{
	struct cap_policy policy;
	enum cap_policy_state result;
	enum cap_gate gate;
	d32ui *ui;
	bool broker = false, viewer = false;

	if (argc != 1)
		return exit_status(EX_USAGE, "usage", "arguments");
	if (!local_display(getenv("DISPLAY")) ||
	    pledge("stdio rpath unix getpw prot_exec", NULL) == -1)
		return exit_status(EX_UNAVAILABLE, "unavailable", "local-display");
	result = cap_policy_load(&policy);
	if (result == CAP_POLICY_READY) {
		broker = cap_helper_present("d32-capture");
		viewer = cap_helper_present("wireshark-run");
	}
	/* The accepted U9 inventory provides neither a live transport contract
	 * nor a verified non-interactive rule or matching Lab interface. */
	gate = cap_gate(result, broker, viewer, false, false);
	ui = d32ui_init(&argc, argv, "D32Capture");
	if (ui == NULL)
		return exit_status(EX_UNAVAILABLE, "unavailable", "local-display");
	(void)d32ui_set_handling(ui, D32UI_PUBLIC);
	XtVaSetValues(d32ui_shell(ui), XtNtitle, "Lab Capture", NULL);
	d32ui_geometry(ui, 600, 400, 48);
	build(ui);
	d32ui_status(ui, cap_gate_text(gate));
	d32ui_show(ui);
	if (pledge("stdio rpath unix getpw", NULL) == -1) {
		d32ui_destroy(ui);
		return exit_status(EX_UNAVAILABLE, "unavailable", "sandbox");
	}
	d32ui_run(ui);
	d32ui_destroy(ui);
	return exit_status(EX_OK, "ok", "closed");
}
