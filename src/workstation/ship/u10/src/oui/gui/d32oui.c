#include "batch.h"
#include "d32ui.h"
#include "provision.h"
#include "result.h"

#include <sys/stat.h>
#include <sys/wait.h>

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <langinfo.h>
#include <limits.h>
#include <locale.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <X11/StringDefs.h>
#include <X11/Xlib.h>
#include <Xm/Form.h>
#include <Xm/Label.h>
#include <Xm/PushB.h>
#include <Xm/RowColumn.h>
#include <Xm/Text.h>
#include <Xm/TextF.h>
#include <Xm/ToggleB.h>

#define HELP_ROOT "/usr/local/dt/appconfig/help"
#define HELP_VOLUME HELP_ROOT "/C/D32oui.sdl"
#define ORG_MAX 200u
#define NO_REGISTRY "No vendor registry installed"
#define SETUP_FAILED "Registry setup failed. Select the unchanged signed package and try again."

enum mode { MAC, PREFIX, ORG };

struct app {
	d32ui *ui;
	struct oui_db *db;
	Widget input, output, page, previous, next, batch, setup;
	Widget modes[3];
	XFontSet display_font;
	XmFontList display_fonts;
	enum mode mode;
	struct oui_mac mac;
	struct oui_row rows[ORG_MAX];
	size_t count, selected;
	int export_fd;
	bool help_installed;
	bool batch_open;
	bool sandbox_failed;
	d32ui_job *setup_job;
	char setup_pattern[PATH_MAX];
	char export_dir[PATH_MAX];
	char registry[128];
	char footer[4096];
};

const char oui_notice[] = "Registry assignee of this block \xe2\x80\x94 "
    "not proof of device manufacturer";

static const char help_text[] =
    "OUI Lookup\n\n"
    "Choose MAC address, Registry prefix, or Organization. Type a value and press Return.\n"
    "MAC input accepts colon, hyphen, dotted, and plain hexadecimal notation.\n"
    "Registry prefixes contain 6, 7, or 9 hex digits. Organization search returns up to 200 rows.\n"
    "Previous and Next move through organization results.\n"
    "An ambiguous match lists every published assignee.\n\n"
    "Ctrl+O opens Batch. Paste one MAC per line, up to 10,000.\n"
    "CSV export writes a new file in your private ~/.dt/d32 directory.\n"
    "An existing filename is preserved. Ctrl+F returns to the lookup field.\n\n"
    "If the registry is missing, choose Load Registry and select the unchanged\n"
    "signed OpenBSD mac-vendor-20260913.tgz package from the In-Tray or mounted media.\n\n"
    "Registry assignee of this block \xe2\x80\x94 not proof of device manufacturer.";

void
oui_format_mac(char out[18], const struct oui_mac *mac)
{
	uint64_t n = mac->value;

	(void)snprintf(out, 18, "%02x:%02x:%02x:%02x:%02x:%02x",
	    (unsigned)(n >> 40) & 0xff, (unsigned)(n >> 32) & 0xff,
	    (unsigned)(n >> 24) & 0xff, (unsigned)(n >> 16) & 0xff,
	    (unsigned)(n >> 8) & 0xff, (unsigned)n & 0xff);
}

void
oui_format_prefix(char out[10], const struct oui_row *row)
{
	(void)snprintf(out, 10, "%0*" PRIx64, (int)(row->width / 4), row->key);
}

char *
oui_join_assignees(const struct oui_row *row, const char *separator)
{
	size_t sizes[OUI_ASSIGNEE_MAX], i, separator_size = strlen(separator), total = 1;
	char *text, *next;

	for (i = 0; i < row->assignee_count; i++) {
		sizes[i] = strlen(row->assignees[i]);
		total += sizes[i] + (i == 0 ? 0 : separator_size);
	}
	text = malloc(total);
	if (text == NULL)
		return NULL;
	next = text;
	for (i = 0; i < row->assignee_count; i++) {
		if (i != 0) {
			memcpy(next, separator, separator_size);
			next += separator_size;
		}
		memcpy(next, row->assignees[i], sizes[i]);
		next += sizes[i];
	}
	*next = '\0';
	return text;
}

static const char *
boolean(bool value)
{
	return value ? "true" : "false";
}

static void
set_label(Widget widget, const char *text)
{
	XmString label = XmStringCreateLocalized((char *)text);

	XtVaSetValues(widget, XmNlabelString, label, NULL);
	XmStringFree(label);
}

static void
load_display_fonts(struct app *app)
{
	Widget shell = d32ui_shell(app->ui);
	Display *display = XtDisplay(shell);
	XmFontListEntry entry;
	char **missing = NULL, *replacement;
	String pattern;
	XtResource resource = {
		"d32MonoFont", "D32MonoFont", XtRString, sizeof(pattern), 0, XtRString,
		(XtPointer)"-misc-fixed-medium-r-normal--13-*-*-*-c-*-iso10646-1"
	};
	int count;

	XtGetApplicationResources(shell, &pattern, &resource, 1, NULL, 0);
	if (pattern == NULL || *pattern == '\0')
		return;
	app->display_font = XCreateFontSet(display, pattern, &missing, &count, &replacement);
	if (missing != NULL)
		XFreeStringList(missing);
	if (app->display_font == NULL)
		return;
	entry = XmFontListEntryCreate(XmFONTLIST_DEFAULT_TAG, XmFONT_IS_FONTSET,
	    app->display_font);
	if (entry != NULL) {
		app->display_fonts = XmFontListAppendEntry(NULL, entry);
		XmFontListEntryFree(&entry);
	}
}

static Widget
button(Widget parent, const char *name, const char *caption, KeySym mnemonic,
    XtCallbackProc callback, struct app *app)
{
	Widget w = XtVaCreateManagedWidget(name, xmPushButtonWidgetClass, parent,
	    XmNmnemonic, mnemonic, NULL);

	set_label(w, caption);
	XtAddCallback(w, XmNactivateCallback, callback, app);
	return w;
}

static void
status(struct app *app, const char *glyph, const char *detail)
{
	int written;

	if (app->db == NULL) {
		(void)snprintf(app->footer, sizeof(app->footer), "%s %s", glyph, detail);
	} else {
		written = snprintf(app->footer, sizeof(app->footer), "%s %s | %s",
		    glyph, app->registry, detail);
		if (written < 0 || written >= (int)sizeof(app->footer))
			(void)snprintf(app->footer, sizeof(app->footer), "%s %s | Result in form",
			    glyph, app->registry);
	}
	d32ui_status(app->ui, app->footer);
}

static void
describe_registry(struct app *app)
{
	(void)snprintf(app->registry, sizeof(app->registry), "Registry dated %s (%s%s%s)",
	    oui_db_date(app->db), oui_db_generation(app->db), oui_db_stale(app->db) ? ", stale" : "",
	    oui_db_fallback(app->db) ? ", previous" : "");
}

static void
navigation(struct app *app)
{
	char page[48];

	(void)snprintf(page, sizeof(page), "Result %zu of %zu",
	    app->count == 0 ? 0 : app->selected + 1, app->count);
	set_label(app->page, page);
	XtSetSensitive(app->previous, app->selected > 0);
	XtSetSensitive(app->next, app->selected + 1 < app->count);
}

static void
render(struct app *app, int rc, const char *problem)
{
	const struct oui_row *row = rc == OUI_OK ? &app->rows[app->selected] : NULL;
	char shown_mac[18], prefix[10], flags[160] = "", fields[220] = "";
	const char *message = problem;
	char *visible = NULL, *summary = NULL;

	if (app->setup_job != NULL)
		return;
	if (app->mode == MAC && (rc == OUI_OK || rc == OUI_MISS)) {
		const struct oui_mac *mac = &app->mac;
		const char *slap = mac->slap == NULL ? "" : mac->slap;

		oui_format_mac(shown_mac, mac);
		(void)snprintf(flags, sizeof(flags),
		    "mac=%s broadcast=%s group=%s local=%s slap=%s ", shown_mac,
		    boolean(mac->broadcast), boolean(mac->group), boolean(mac->local), slap);
		(void)snprintf(fields, sizeof(fields),
		    "MAC address    %s\nBroadcast      %s\nGroup          %s\nLocal          %s\nSLAP quadrant  %s\n",
		    shown_mac, boolean(mac->broadcast), boolean(mac->group), boolean(mac->local),
		    mac->slap == NULL ? "(none)" : mac->slap);
	}
	if (row != NULL) {
		oui_format_prefix(prefix, row);
		if (row->assignee_count > 1) {
			char *names = oui_join_assignees(row, "\n               ");
			char *brief = oui_join_assignees(row, " | ");

			if (names != NULL && asprintf(&visible,
			    "%sPrefix         %s/%u\nRegistry type  %s\n"
			    "Ambiguous registry assignment\nAssignees      %s\n"
			    "Source date    %s\n\n%s", fields, prefix, row->width,
			    oui_type_name(row->type), names, row->date, oui_notice) == -1)
				visible = NULL;
			if (brief != NULL && asprintf(&summary,
			    "%sprefix=%s width=%u type=%s ambiguous=true assignees=%s date=%s | %s",
			    flags, prefix, row->width, oui_type_name(row->type), brief,
			    row->date, oui_notice) == -1)
				summary = NULL;
			free(brief);
			free(names);
		} else {
			if (asprintf(&visible,
			    "%sPrefix         %s/%u\nRegistry type  %s\nAssignee       %s\n"
			    "Source date    %s\n\n%s", fields, prefix, row->width,
			    oui_type_name(row->type), row->assignee, row->date, oui_notice) == -1)
				visible = NULL;
			if (asprintf(&summary, "%sprefix=%s width=%u type=%s assignee=%s date=%s | %s",
			    flags, prefix, row->width, oui_type_name(row->type), row->assignee,
			    row->date, oui_notice) == -1)
				summary = NULL;
		}
	} else {
		if (message == NULL)
			message = rc == OUI_MISS ? "No registry match" : "Ready";
		if (app->db == NULL && app->setup != NULL && strcmp(message, NO_REGISTRY) == 0) {
			visible = strdup(NO_REGISTRY ".\n\nChoose Load Registry to select the unchanged "
			    "signed OpenBSD mac-vendor-20260913.tgz package from the In-Tray or mounted media.");
		} else if (asprintf(&visible, "%s%s", fields, message) == -1) {
			visible = NULL;
		}
		if (asprintf(&summary, "%s%s", flags, message) == -1)
			summary = NULL;
	}
	XmTextSetString(app->output, visible == NULL ? "Result unavailable" : visible);
	XmTextSetTopCharacter(app->output, 0);
	status(app, rc == OUI_OK ? "[OK]" : rc == OUI_INVALID || app->db == NULL ? "[x]" : "[?]",
	    summary == NULL ? "Result unavailable" : summary);
	navigation(app);
	free(summary);
	free(visible);
}

static int
lookup_pledge(const struct app *app)
{
#ifdef __OpenBSD__
	return pledge(app->export_fd != -1 ? "stdio rpath wpath cpath flock unix proc" :
	    "stdio rpath unix", NULL);
#else
	(void)app;
	return 0;
#endif
}

static void
setup_message(struct app *app, const char *glyph, const char *message)
{
	XmTextSetString(app->output, (char *)message);
	XmTextSetTopCharacter(app->output, 0);
	status(app, glyph, message);
}

static void
setup_done(int wait_status, void *context)
{
	struct app *app = context;

	app->setup_job = NULL;
	if (wait_status < 0 || !WIFEXITED(wait_status) || WEXITSTATUS(wait_status) != 0 ||
	    oui_db_open_local_at(&app->db, app->export_fd) != OUI_OK) {
		XtSetSensitive(app->setup, True);
		setup_message(app, "[x]", SETUP_FAILED);
		return;
	}
#ifdef __OpenBSD__
	if (oui_provision_unveil(true) == -1 || unveil(NULL, NULL) == -1 ||
	    lookup_pledge(app) == -1) {
		app->sandbox_failed = true;
		d32ui_quit(app->ui);
		return;
	}
#endif
	describe_registry(app);
	XtUnmanageChild(app->setup);
	XtSetSensitive(app->batch, True);
	render(app, OUI_UNAVAILABLE, "Ready");
	XmProcessTraversal(app->input, XmTRAVERSE_CURRENT);
}

static void
setup_selected(const char *path, void *context)
{
	struct app *app = context;
	pid_t child;
	int reader;

	if (app->setup_job != NULL || app->db != NULL)
		return;
	if (oui_provision_start(path, &child, &reader) != D32E_OK) {
		setup_message(app, "[x]", SETUP_FAILED);
		return;
	}
	app->setup_job = d32ui_job_watch(app->ui, child, reader, NULL, setup_done, app);
	if (app->setup_job == NULL) {
		(void)close(reader);
		(void)kill(child, SIGTERM);
		while (waitpid(child, NULL, 0) == -1 && errno == EINTR)
			continue;
		setup_message(app, "[x]", SETUP_FAILED);
		return;
	}
	XtSetSensitive(app->setup, False);
	setup_message(app, "[?]", "Verifying the signed registry package...");
}

static void
select_package(Widget w, XtPointer context, XtPointer call)
{
	struct app *app = context;

	(void)call;
	if (app->setup_job == NULL && app->db == NULL)
		(void)d32ui_file_select(app->ui, w, app->setup_pattern, setup_selected, app);
}

static void
run_lookup(Widget w, XtPointer context, XtPointer call)
{
	struct app *app = context;
	char *text;
	uint64_t key;
	unsigned width;
	int rc = OUI_INVALID;
	const char *problem = NULL;

	(void)w;
	(void)call;
	app->count = 0;
	app->selected = 0;
	if (app->db == NULL) {
		render(app, OUI_UNAVAILABLE, NO_REGISTRY);
		return;
	}
	text = XmTextFieldGetString(app->input);
	if (text == NULL) {
		render(app, OUI_INVALID, "Could not read input");
		return;
	}
	switch (app->mode) {
	case MAC:
		if (oui_parse_mac(text, &app->mac) == OUI_OK)
			rc = oui_lookup(app->db, &app->mac, app->rows);
		else
			problem = "Invalid MAC address";
		break;
	case PREFIX:
		if (oui_parse_prefix(text, &key, &width) == OUI_OK)
			rc = oui_find_prefix(app->db, key, width, app->rows);
		else
			problem = "Enter 6, 7, or 9 hexadecimal digits";
		break;
	case ORG:
		if (*text == '\0') {
			problem = "Enter an organization name";
			break;
		}
		app->count = oui_search_org(app->db, text, app->rows, ORG_MAX);
		rc = app->count == 0 ? OUI_MISS : OUI_OK;
		break;
	}
	XtFree(text);
	if (rc == OUI_OK && app->mode != ORG)
		app->count = 1;
	render(app, rc, problem);
}

static void
change_mode(Widget w, XtPointer context, XtPointer call)
{
	struct app *app = context;
	XmToggleButtonCallbackStruct *event = call;
	XtPointer value;

	if (!event->set)
		return;
	XtVaGetValues(w, XmNuserData, &value, NULL);
	app->mode = (enum mode)(uintptr_t)value;
	app->count = 0;
	app->selected = 0;
	XmTextFieldSetString(app->input, "");
	render(app, OUI_UNAVAILABLE, app->db == NULL ? NO_REGISTRY : "Ready");
	XmProcessTraversal(app->input, XmTRAVERSE_CURRENT);
}

static void
cycle_mode(Widget w, XtPointer context, XtPointer call)
{
	struct app *app = context;
	unsigned step = strcmp(XtName(w), "previousTab") == 0 ? 2 : 1;

	(void)call;
	XmToggleButtonSetState(app->modes[(app->mode + step) % 3], True, True);
}

static void
move_result(Widget w, XtPointer context, XtPointer call)
{
	struct app *app = context;

	(void)call;
	if (w == app->previous && app->selected > 0)
		app->selected--;
	else if (w == app->next && app->selected + 1 < app->count)
		app->selected++;
	else
		return;
	render(app, OUI_OK, NULL);
}

static void
find_input(Widget w, XtPointer context, XtPointer call)
{
	struct app *app = context;

	(void)w;
	(void)call;
	XmProcessTraversal(app->input, XmTRAVERSE_CURRENT);
	XmTextFieldSetSelection(app->input, 0, XmTextFieldGetLastPosition(app->input),
	    XtLastTimestampProcessed(XtDisplay(app->input)));
}

static void
copy_result(Widget w, XtPointer context, XtPointer call)
{
	struct app *app = context;
	Time time = XtLastTimestampProcessed(XtDisplay(app->output));
	XmTextPosition first, last;

	(void)w;
	(void)call;
	if (!XmTextGetSelectionPosition(app->output, &first, &last))
		XmTextSetSelection(app->output, 0, XmTextGetLastPosition(app->output), time);
	(void)XmTextCopy(app->output, time);
}

static void
show_help(Widget w, XtPointer context, XtPointer call)
{
	struct app *app = context;

	(void)w;
	(void)call;
	(void)d32ui_help(app->ui, app->input, app->help_installed ? HELP_VOLUME : NULL,
	    "_hometopic", help_text);
}

static void
restore_footer(void *context)
{
	struct app *app = context;

	app->batch_open = false;
	d32ui_status(app->ui, app->footer);
}

static void
show_batch(Widget w, XtPointer context, XtPointer call)
{
	struct app *app = context;
	Widget opener = app->batch;

	(void)call;
	if (app->db == NULL) {
		if (app->setup != NULL)
			select_package(app->setup, app, NULL);
		else if (app->setup_job == NULL)
			status(app, "[x]", NO_REGISTRY);
		return;
	}
	if (app->batch_open)
		return;
	if (w != app->batch)
		opener = app->input;
	app->batch_open = oui_batch_show(app->ui, opener, app->db, app->export_fd,
	    app->display_fonts, restore_footer, app);
}

static void
quit(Widget w, XtPointer context, XtPointer call)
{
	struct app *app = context;

	(void)w;
	(void)call;
	if (!app->batch_open && app->setup_job == NULL)
		d32ui_quit(app->ui);
}

static void
create_widgets(struct app *app)
{
	static const char *const labels[] = {"MAC address", "Registry prefix", "Organization"};
	static const char *const names[] = {"mac", "prefix", "organization"};
	const struct d32ui_menu_item actions[] = {
		{ D32UI_OPEN, show_batch, app }, { D32UI_EXIT, quit, app },
		{ D32UI_COPY, copy_result, app }, { D32UI_FIND, find_input, app },
		{ D32UI_REFRESH, run_lookup, app }, { D32UI_HELP, show_help, app },
		{ D32UI_PREV_TAB, cycle_mode, app }, { D32UI_NEXT_TAB, cycle_mode, app },
	};
	Widget parent = d32ui_body(app->ui), menu, modes, choice, input, commands, scroll;
	XmFontList mono = d32ui_mono(app->ui);
	Arg text_args[5];
	size_t i;

	menu = d32ui_menu(app->ui, parent, actions, sizeof(actions) / sizeof(actions[0]));
	XtVaSetValues(menu, XmNtopAttachment, XmATTACH_FORM, XmNleftAttachment, XmATTACH_FORM,
	    XmNrightAttachment, XmATTACH_FORM, NULL);
	modes = XtVaCreateManagedWidget("modes", xmRowColumnWidgetClass, parent,
	    XmNorientation, XmHORIZONTAL, XmNradioBehavior, True, XmNradioAlwaysOne, True,
	    XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, menu,
	    XmNleftAttachment, XmATTACH_FORM, XmNleftOffset, 8, NULL);
	for (i = 0; i < sizeof(labels) / sizeof(labels[0]); i++) {
		choice = XtVaCreateManagedWidget(names[i], xmToggleButtonWidgetClass, modes,
		    XmNuserData, (XtPointer)(uintptr_t)i, XmNset, i == 0,
		    XmNnavigationType, XmTAB_GROUP, NULL);
		app->modes[i] = choice;
		set_label(choice, labels[i]);
		XtAddCallback(choice, XmNvalueChangedCallback, change_mode, app);
	}
	input = XtVaCreateManagedWidget("entry", xmFormWidgetClass, parent,
	    XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, modes,
	    XmNleftAttachment, XmATTACH_FORM, XmNleftOffset, 8,
	    XmNrightAttachment, XmATTACH_FORM, XmNrightOffset, 8, NULL);
	choice = button(input, "lookup", "Lookup", 'L', run_lookup, app);
	XtVaSetValues(choice, XmNtopAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
	app->input = XtVaCreateManagedWidget("query", xmTextFieldWidgetClass, input,
	    XmNcolumns, 35, XmNmaxLength, 4096, XmNleftAttachment, XmATTACH_FORM,
	    XmNtopAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_WIDGET,
	    XmNrightWidget, choice, XmNrightOffset, 6, NULL);
	if (mono != NULL)
		XtVaSetValues(app->input, XmNfontList, mono, NULL);
	XtAddCallback(app->input, XmNactivateCallback, run_lookup, app);

	commands = XtVaCreateManagedWidget("commands", xmRowColumnWidgetClass, parent,
	    XmNorientation, XmHORIZONTAL, XmNbottomAttachment, XmATTACH_FORM,
	    XmNbottomOffset, 6, XmNleftAttachment, XmATTACH_FORM, XmNleftOffset, 8, NULL);
	app->batch = button(commands, "batch", "Batch...", 'B', show_batch, app);
	app->previous = button(commands, "previous", "Previous", 'P', move_result, app);
	app->next = button(commands, "next", "Next", 'N', move_result, app);
	app->page = XtVaCreateManagedWidget("page", xmLabelWidgetClass, commands, NULL);
	XtSetSensitive(app->batch, app->db != NULL);
	if (app->db == NULL && app->export_fd != -1 && app->setup_pattern[0] != '\0')
		app->setup = button(commands, "provisionRegistry", "Load Registry...", 'G',
		    select_package, app);

	XtSetArg(text_args[0], XmNeditable, False);
	XtSetArg(text_args[1], XmNcursorPositionVisible, False);
	XtSetArg(text_args[2], XmNeditMode, XmMULTI_LINE_EDIT);
	XtSetArg(text_args[3], XmNwordWrap, True);
	XtSetArg(text_args[4], XmNscrollHorizontal, False);
	app->output = XmCreateScrolledText(parent, "result", text_args, XtNumber(text_args));
	scroll = XtParent(app->output);
	XtVaSetValues(scroll, XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, input,
	    XmNtopOffset, 8, XmNbottomAttachment, XmATTACH_WIDGET, XmNbottomWidget, commands,
	    XmNbottomOffset, 5, XmNleftAttachment, XmATTACH_FORM, XmNleftOffset, 8,
	    XmNrightAttachment, XmATTACH_FORM, XmNrightOffset, 8, NULL);
	if (app->display_fonts != NULL || mono != NULL)
		XtVaSetValues(app->output, XmNfontList,
		    app->display_fonts != NULL ? app->display_fonts : mono, NULL);
	XtManageChild(app->output);
}

static int
export_directory(char path[PATH_MAX])
{
	const char *home = getenv("HOME");
	const int flags = O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC;
	struct stat info;
	int fd, dt, exports, n;

	if (home == NULL || home[0] != '/')
		return -1;
	n = snprintf(path, PATH_MAX, "%s/.dt/d32", home);
	if (n < 0 || n >= PATH_MAX)
		return -1;
	fd = open(home, flags);
	if (fd == -1)
		return -1;
	if (fstat(fd, &info) == -1 || info.st_uid != geteuid() || (info.st_mode & 022) != 0 ||
	    (mkdirat(fd, ".dt", 0700) == -1 && errno != EEXIST)) {
		(void)close(fd);
		return -1;
	}
	dt = openat(fd, ".dt", flags);
	(void)close(fd);
	if (dt == -1)
		return -1;
	if (fstat(dt, &info) == -1 || info.st_uid != geteuid() || (info.st_mode & 022) != 0 ||
	    (mkdirat(dt, "d32", 0700) == -1 && errno != EEXIST)) {
		(void)close(dt);
		return -1;
	}
	exports = openat(dt, "d32", flags);
	(void)close(dt);
	if (exports == -1)
		return -1;
	if (fstat(exports, &info) == -1 || info.st_uid != geteuid() || (info.st_mode & 0777) != 0700) {
		(void)close(exports);
		return -1;
	}
	return exports;
}

static int
sandbox(struct app *app)
{
#ifdef __OpenBSD__
	if (app->export_fd != -1 && unveil(app->export_dir, "rwc") == -1) {
		(void)close(app->export_fd);
		app->export_fd = -1;
		if (app->setup != NULL)
			return -1;
	}
	if (unveil(HELP_ROOT, "r") == -1 && errno != ENOENT)
		return -1;
	if (app->setup != NULL) {
		if (oui_provision_unveil(false) == -1)
			return -1;
		return pledge("stdio rpath wpath cpath flock unix proc exec unveil", NULL);
	}
	if (unveil(NULL, NULL) == -1)
		return -1;
#endif
	return lookup_pledge(app);
}

static bool
local_display(const char *display)
{
	size_t digits;

	if (display == NULL)
		return false;
	if (strncmp(display, "unix/", 5) == 0)
		display += 5;
	else if (strncmp(display, "unix:", 5) == 0)
		display += 4;
	if (*display++ != ':')
		return false;
	digits = strspn(display, "0123456789");
	if (digits == 0)
		return false;
	display += digits;
	if (*display == '.') {
		display++;
		digits = strspn(display, "0123456789");
		if (digits == 0)
			return false;
		display += digits;
	}
	return *display == '\0';
}

static void
prefer_utf8(void)
{
	if (setlocale(LC_CTYPE, "") != NULL && strcmp(nl_langinfo(CODESET), "UTF-8") == 0)
		return;
	/* Registry text is UTF-8 even when the desktop starts in the C locale. */
	if (setenv("LC_CTYPE", "en_US.UTF-8", 1) == 0)
		(void)unsetenv("LC_ALL");
}

int
main(int argc, char **argv)
{
	struct app app = { .export_fd = -1 };
	int rc = 0;

	if (argc != 1) {
		fputs("usage: d32oui\n", stderr);
		return 65;
	}
	if (!local_display(getenv("DISPLAY"))) {
		fputs("d32oui: a local CDE display is required\n", stderr);
		return 69;
	}
	prefer_utf8();
	app.ui = d32ui_init(&argc, argv, "D32oui");
	if (app.ui == NULL) {
		fputs("d32oui: cannot open the CDE display\n", stderr);
		return 69;
	}
	(void)d32ui_set_handling(app.ui, D32UI_PUBLIC);
	d32ui_on_close(app.ui, quit, &app);
	load_display_fonts(&app);
	XtVaSetValues(d32ui_shell(app.ui), XtNtitle, "OUI Lookup", NULL);
	d32ui_geometry(app.ui, 760, 530, 48);
	app.help_installed = access(HELP_VOLUME, R_OK) == 0;
	if (oui_db_open_default(&app.db) == OUI_OK) {
		describe_registry(&app);
		app.export_fd = export_directory(app.export_dir);
	} else if (getenv("D32_OUI_ROOT") == NULL && oui_provision_available(app.setup_pattern)) {
		app.export_fd = export_directory(app.export_dir);
	}
	create_widgets(&app);
	render(&app, OUI_UNAVAILABLE, app.db == NULL ? NO_REGISTRY : "Ready");
	XtRealizeWidget(d32ui_shell(app.ui));
	if (sandbox(&app) == -1) {
		perror("d32oui: sandbox");
		rc = 69;
	} else {
		d32ui_show(app.ui);
		XmProcessTraversal(app.input, XmTRAVERSE_CURRENT);
		d32ui_run(app.ui);
		if (app.sandbox_failed)
			rc = 69;
	}
	if (app.display_fonts != NULL) {
		XtVaSetValues(app.output, XmNfontList, d32ui_mono(app.ui), NULL);
		XmFontListFree(app.display_fonts);
	}
	if (app.display_font != NULL)
		XFreeFontSet(XtDisplay(d32ui_shell(app.ui)), app.display_font);
	d32ui_destroy(app.ui);
	if (app.export_fd != -1)
		(void)close(app.export_fd);
	oui_db_close(app.db);
	return rc;
}
