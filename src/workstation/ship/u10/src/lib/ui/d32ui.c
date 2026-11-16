#include "d32ui.h"

#include <limits.h>
#include <stdlib.h>
#include <string.h>

#include <X11/Shell.h>
#include <X11/StringDefs.h>
#include <X11/Xatom.h>
#include <X11/Xresource.h>
#include <Xm/Form.h>
#include <Xm/Label.h>
#include <Xm/Protocols.h>
#include <Xm/Separator.h>

static const char system_font[] = "-dt-interface system-medium-r-normal-*-*-*-*-*-*-*-*-*";
static const char user_font[] = "-dt-interface user-medium-r-normal-*-*-*-*-*-*-*-*-*";
static const char mono_font[] = "-misc-fixed-medium-r-normal--13-*-*-*-c-*-iso8859-1";

struct d32ui {
	XtAppContext app;
	Display *display;
	Widget shell, body, status;
	XFontStruct *mono;
	XmFontList mono_list;
	Atom handling_atom, status_atom, utf8_atom;
	enum d32ui_handling handling;
	bool handling_locked;
	XtCallbackProc close;
	XtPointer close_data;
	char text[4096];
};

static bool
font_available(Display *display, const char *pattern)
{
	char **names;
	int count = 0;

	names = XListFonts(display, pattern, 1, &count);
	if (names != NULL)
		XFreeFontNames(names);
	return count > 0;
}

static void
font_defaults(Display *display)
{
	XrmDatabase defaults = NULL, current = XtDatabase(display);

	if (font_available(display, system_font))
		XrmPutStringResource(&defaults, "*fontList", system_font);
	if (font_available(display, user_font)) {
		XrmPutStringResource(&defaults, "*XmText.fontList", user_font);
		XrmPutStringResource(&defaults, "*XmTextField.fontList", user_font);
	}
	if (defaults != NULL) {
		XrmCombineDatabase(defaults, &current, False);
		XrmSetDatabase(display, current);
	}
}

static void
load_mono(d32ui *ui)
{
	String pattern;
	XtResource resource = {
		"d32MonoFont", "D32MonoFont", XtRString, sizeof(pattern), 0,
		XtRString, (XtPointer)mono_font
	};

	XtGetApplicationResources(ui->shell, &pattern, &resource, 1, NULL, 0);
	if (pattern != NULL && *pattern != '\0')
		ui->mono = XLoadQueryFont(ui->display, pattern);
	if (ui->mono == NULL)
		ui->mono = XLoadQueryFont(ui->display, "fixed");
	if (ui->mono != NULL)
		ui->mono_list = XmFontListCreate(ui->mono, XmFONTLIST_DEFAULT_TAG);
}

static void
wm_close(Widget widget, XtPointer data, XtPointer reason)
{
	d32ui *ui = data;

	if (ui->close != NULL)
		ui->close(widget, ui->close_data, reason);
	else
		d32ui_quit(ui);
}

d32ui *
d32ui_init(int *argc, char **argv, const char *app_class)
{
	d32ui *ui;
	Widget frame, separator;
	Atom delete;

	if (argc == NULL || argv == NULL || app_class == NULL || *app_class == '\0')
		return NULL;
	ui = calloc(1, sizeof(*ui));
	if (ui == NULL)
		return NULL;
	XtToolkitInitialize();
	XtSetLanguageProc(NULL, NULL, NULL);
	ui->app = XtCreateApplicationContext();
	ui->display = XtOpenDisplay(ui->app, NULL, NULL, app_class, NULL, 0, argc, argv);
	if (ui->display == NULL) {
		XtDestroyApplicationContext(ui->app);
		free(ui);
		return NULL;
	}
	font_defaults(ui->display);
	ui->shell = XtVaAppCreateShell(NULL, app_class, applicationShellWidgetClass,
	    ui->display, XtNmappedWhenManaged, False, XmNdeleteResponse, XmDO_NOTHING, NULL);
	ui->handling = D32UI_RESTRICTED;
	ui->handling_atom = XInternAtom(ui->display, "_D32_HANDLING", False);
	ui->status_atom = XInternAtom(ui->display, "_D32_STATUS", False);
	ui->utf8_atom = XInternAtom(ui->display, "UTF8_STRING", False);
	delete = XInternAtom(ui->display, "WM_DELETE_WINDOW", False);
	XmAddWMProtocolCallback(ui->shell, delete, wm_close, ui);
	load_mono(ui);
	frame = XtVaCreateManagedWidget("frame", xmFormWidgetClass, ui->shell, NULL);
	ui->status = XtVaCreateManagedWidget("status", xmLabelWidgetClass, frame,
	    XmNalignment, XmALIGNMENT_BEGINNING, XmNrecomputeSize, False,
	    XmNmarginWidth, 8, XmNmarginHeight, 5, XmNtraversalOn, False,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM,
	    XmNbottomAttachment, XmATTACH_FORM, NULL);
	separator = XtVaCreateManagedWidget("statusSeparator", xmSeparatorWidgetClass,
	    frame, XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM,
	    XmNbottomAttachment, XmATTACH_WIDGET, XmNbottomWidget, ui->status, NULL);
	ui->body = XtVaCreateManagedWidget("body", xmFormWidgetClass, frame,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM,
	    XmNtopAttachment, XmATTACH_FORM, XmNbottomAttachment, XmATTACH_WIDGET,
	    XmNbottomWidget, separator, NULL);
	d32ui_status(ui, "[?] Ready");
	d32ui_geometry(ui, 820, 600, 0);
	return ui;
}

Widget
d32ui_shell(const d32ui *ui)
{
	return ui->shell;
}

Widget
d32ui_body(const d32ui *ui)
{
	return ui->body;
}

XtAppContext
d32ui_context(const d32ui *ui)
{
	return ui->app;
}

XmFontList
d32ui_mono(const d32ui *ui)
{
	return ui->mono_list;
}

static unsigned
fit(unsigned preferred, unsigned minimum, unsigned available)
{
	unsigned limit = available - (available + 9) / 10;

	if (preferred > limit)
		preferred = limit;
	if (preferred < minimum)
		preferred = minimum;
	return preferred < available ? preferred : available;
}

void
d32ui_geometry(d32ui *ui, unsigned width, unsigned height, unsigned panel)
{
	Screen *screen = XtScreen(ui->shell);
	unsigned w = (unsigned)WidthOfScreen(screen), h = (unsigned)HeightOfScreen(screen);
	unsigned minw, minh;

	if (panel < h)
		h -= panel;
	minw = w < 600 ? w : 600;
	minh = h < 400 ? h : 400;
	XtVaSetValues(ui->shell, XtNwidth, (Dimension)fit(width, minw, w),
	    XtNheight, (Dimension)fit(height, minh, h), XtNminWidth, (int)minw,
	    XtNminHeight, (int)minh, XtNmaxWidth, (int)w, XtNmaxHeight, (int)h, NULL);
}

enum d32_error
d32ui_set_handling(d32ui *ui, enum d32ui_handling handling)
{
	if (handling != D32UI_RESTRICTED && handling != D32UI_PUBLIC)
		return D32E_INVAL;
	if ((ui->handling_locked || XtIsRealized(ui->shell)) && handling != ui->handling)
		return D32E_PERM;
	ui->handling = handling;
	return D32E_OK;
}

static void
tag(d32ui *ui, Widget shell)
{
	const char *handling = ui->handling == D32UI_PUBLIC ? "public" : "restricted";

	XChangeProperty(ui->display, XtWindow(shell), ui->handling_atom, XA_STRING, 8,
	    PropModeReplace, (const unsigned char *)handling, (int)strlen(handling));
	ui->handling_locked = true;
}

void
d32ui_tag_dialog(d32ui *ui, Widget dialog)
{
	Widget shell = dialog;

	while (shell != NULL && !XtIsShell(shell))
		shell = XtParent(shell);
	if (shell == NULL || shell == ui->shell || XtDisplay(shell) != ui->display)
		return;
	if (!XtIsRealized(shell)) {
		XtSetMappedWhenManaged(shell, False);
		XtRealizeWidget(shell);
		tag(ui, shell);
		XtSetMappedWhenManaged(shell, True);
	} else {
		tag(ui, shell);
	}
}

static void
publish_status(d32ui *ui)
{
	if (XtIsRealized(ui->shell))
		XChangeProperty(ui->display, XtWindow(ui->shell), ui->status_atom,
		    ui->utf8_atom, 8, PropModeReplace, (const unsigned char *)ui->text,
		    (int)strlen(ui->text));
}

void
d32ui_status(d32ui *ui, const char *text)
{
	XmString label;
	size_t len;

	if (text == NULL || (len = strnlen(text, sizeof(ui->text))) >= sizeof(ui->text))
		return;
	memcpy(ui->text, text, len + 1);
	label = XmStringCreateLocalized(ui->text);
	XtVaSetValues(ui->status, XmNlabelString, label, NULL);
	XmStringFree(label);
	publish_status(ui);
}

void
d32ui_on_close(d32ui *ui, XtCallbackProc callback, XtPointer data)
{
	ui->close = callback;
	ui->close_data = data;
}

void
d32ui_show(d32ui *ui)
{
	XtRealizeWidget(ui->shell);
	tag(ui, ui->shell);
	publish_status(ui);
	XtMapWidget(ui->shell);
}

void
d32ui_run(d32ui *ui)
{
	XtAppMainLoop(ui->app);
}

void
d32ui_quit(d32ui *ui)
{
	XtAppSetExitFlag(ui->app);
}

void
d32ui_destroy(d32ui *ui)
{
	if (ui == NULL)
		return;
	XtDestroyWidget(ui->shell);
	if (ui->mono_list != NULL)
		XmFontListFree(ui->mono_list);
	if (ui->mono != NULL)
		XFreeFont(ui->display, ui->mono);
	XtCloseDisplay(ui->display);
	XtDestroyApplicationContext(ui->app);
	free(ui);
}
