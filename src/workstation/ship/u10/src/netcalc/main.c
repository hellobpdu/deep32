#include "base_panel.h"
#include "calc.h"
#include "d32_prefs.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <syslog.h>
#include <unistd.h>

#include <X11/Shell.h>
#include <Xm/CascadeB.h>
#include <Xm/Form.h>
#include <Xm/Label.h>
#include <Xm/Notebook.h>
#include <Xm/PushB.h>
#include <Xm/RowColumn.h>
#include <Xm/Text.h>
#include <Xm/TextF.h>
#include <Xm/ToggleB.h>

#ifndef D32_CALC_DATA_ROOT
#define D32_CALC_DATA_ROOT "/usr/local/d32/share/data"
#endif
#ifndef D32_CALC_HELP_VOLUME
#define D32_CALC_HELP_VOLUME "/usr/local/dt/appconfig/help/en_US.UTF-8/D32Calc.sdl"
#endif

static const struct tab_info {
	const char *label, *name, *focus, *hint;
	KeySym mnemonic;
} tabs[] = {
	[CALC_ADDRESS] = {"Address", "addressTab", "addressInput", "Inspect an address, prefix, mask or range.", 'A'},
	[CALC_PLAN] = {"Plan", "planTab", "planName", "Plan allocations in the context prefix.", 'P'},
	[CALC_POLICY] = {"Policy", "policyTab", "policyWildBase", "Test a wildcard or a prefix-list.", 'L'},
	[CALC_MULTICAST] = {"Multicast", "multicastTab", "multicastGroup", "Convert a multicast or solicited-node address.", 'M'},
	[CALC_REGISTRIES] = {"Registries", "registriesTab", "registryQuery", "Search offline registry and country data.", 'R'},
	[CALC_BASE] = {"Base Converter", "baseTab", "baseInput", "Enter a value and press Convert.", 'B'}
};

static Widget
set_label(Widget widget, const char *title)
{
	XmString label = XmStringCreateLocalized((char *)title);
	XtVaSetValues(widget, XmNlabelString, label, NULL);
	XmStringFree(label);
	return widget;
}

static void
focus_tab(struct calc *calc)
{
	if (calc->active == CALC_POLICY) {
		calc_policy_focus(calc);
		return;
	}
	char path[64];
	snprintf(path, sizeof(path), "*%s", tabs[calc->active].focus);
	Widget focus = XtNameToWidget(calc->pages[calc->active], path);
	if (focus != NULL && XtIsSensitive(focus) && XtIsManaged(focus))
		(void)XmProcessTraversal(focus, XmTRAVERSE_CURRENT);
}

static void
update_tab(struct calc *calc, enum calc_tab tab)
{
	if (tab < CALC_ADDRESS || tab > CALC_TAB_COUNT || calc->active == tab)
		return;
	calc->active = tab;
	d32ui_status(calc->ui, tabs[tab].hint);
	if (tab == CALC_PLAN)
		calc_plan_refresh(calc);
	if (XtIsRealized(d32ui_shell(calc->ui)))
		focus_tab(calc);
}

void
calc_select_tab(struct calc *calc, enum calc_tab tab)
{
	if (tab < CALC_ADDRESS || tab > CALC_TAB_COUNT)
		return;
	XtVaSetValues(calc->notebook, XmNcurrentPageNumber, (int)tab, NULL);
	update_tab(calc, tab);
}

static void
notebook_changed(Widget widget, XtPointer arg, XtPointer call)
{
	const XmNotebookCallbackStruct *change = call;
	(void)widget;
	if (change != NULL)
		update_tab(arg, (enum calc_tab)change->page_number);
}

void
calc_set_context(struct calc *calc, const d32_prefix *context)
{
	char address[D32_ADDR_TEXT_CAP], text[64];
	if (!d32_prefix_valid(context))
		return;
	bool family_changed = calc->family != context->addr.fam;
	calc->context = *context;
	calc->context.addr = d32_prefix_network(context);
	calc->family = context->addr.fam;
	(void)d32_addr_format(&calc->context.addr, address, sizeof(address));
	snprintf(text, sizeof(text), "%s/%u", address, context->len);
	XmToggleButtonSetState(calc->family_choice[0], calc->family == D32_IPV4, False);
	XmToggleButtonSetState(calc->family_choice[1], calc->family == D32_IPV6, False);
	XmTextFieldSetString(calc->context_input, text);
	if (family_changed) {
		calc_address_refresh(calc);
		calc_policy_family_changed(calc);
	}
}

bool
calc_commit_context(struct calc *calc)
{
	d32_prefix context;
	d32_net_error error;
	char zone[D32_ZONE_CAP], status[160];
	char *input = XmTextFieldGetString(calc->context_input);
	if (input == NULL)
		return false;
	d32_net_status result = d32_prefix_parse(input, &context, zone, &error);
	XtFree(input);
	if (result != D32_NET_OK) {
		snprintf(status, sizeof(status), "[x] Context column %zu: %s.",
		    error.column, d32_net_status_name(result));
	} else if (context.addr.fam != calc->family) {
		snprintf(status, sizeof(status), "[x] Context family differs from IPv%u.", calc->family);
	} else if (*zone != '\0') {
		snprintf(status, sizeof(status), "[x] A context prefix cannot carry a zone label.");
	} else if (d32_prefix_has_host_bits(&context)) {
		d32_addr network = d32_prefix_network(&context);
		char text[D32_ADDR_TEXT_CAP];
		(void)d32_addr_format(&network, text, sizeof(text));
		snprintf(status, sizeof(status), "[!] Use the aligned context %s/%u.", text, context.len);
	} else {
		calc_set_context(calc, &context);
		return true;
	}
	d32ui_status(calc->ui, status);
	return false;
}

static void
context_activate(Widget widget, XtPointer arg, XtPointer call)
{
	struct calc *calc = arg;
	(void)widget;
	(void)call;
	if (calc_commit_context(calc)) {
		d32ui_status(calc->ui, "[OK] Context prefix accepted.");
		if (calc->active == CALC_PLAN)
			calc_plan_refresh(calc);
	}
}

static void
family_change(Widget widget, XtPointer arg, XtPointer call)
{
	const XmToggleButtonCallbackStruct *state = call;
	struct calc *calc = arg;
	if (state == NULL || !state->set)
		return;
	d32_prefix context = {.addr.fam = widget == calc->family_choice[0] ? D32_IPV4 : D32_IPV6};
	if (context.addr.fam == calc->family)
		return;
	calc_set_context(calc, &context);
	d32ui_status(calc->ui, "[OK] Family and default context selected.");
	if (calc->active == CALC_PLAN)
		calc_plan_refresh(calc);
}

static void
close_window(Widget widget, XtPointer arg, XtPointer call)
{
	struct calc *calc = arg;
	(void)widget;
	(void)call;
	d32ui_quit(calc->ui);
}

static void
open_plan(Widget widget, XtPointer arg, XtPointer call)
{
	struct calc *calc = arg;
	(void)widget;
	(void)call;
	calc_select_tab(calc, CALC_PLAN);
	calc_plan_open(calc);
}

static void
save_plan(Widget widget, XtPointer arg, XtPointer call)
{
	struct calc *calc = arg;
	(void)widget;
	(void)call;
	calc_select_tab(calc, CALC_PLAN);
	calc_plan_save(calc);
}

static void
tab_step(struct calc *calc, int step)
{
	int tab = ((int)calc->active - 1 + CALC_TAB_COUNT + step) % CALC_TAB_COUNT + 1;
	calc_select_tab(calc, (enum calc_tab)tab);
}

static void
previous_tab(Widget widget, XtPointer arg, XtPointer call)
{
	(void)widget;
	(void)call;
	tab_step(arg, -1);
}

static void
next_tab(Widget widget, XtPointer arg, XtPointer call)
{
	(void)widget;
	(void)call;
	tab_step(arg, 1);
}

static void
find_field(Widget widget, XtPointer arg, XtPointer call)
{
	(void)widget;
	(void)call;
	focus_tab(arg);
}

static void
copy_selection(Widget widget, XtPointer arg, XtPointer call)
{
	struct calc *calc = arg;
	(void)call;
	Widget focus = XmGetFocusWidget(d32ui_shell(calc->ui));
	Time time = XtLastTimestampProcessed(XtDisplay(widget));
	if (focus != NULL && XmIsText(focus))
		(void)XmTextCopy(focus, time);
	else if (focus != NULL && XmIsTextField(focus))
		(void)XmTextFieldCopy(focus, time);
}

static void
refresh(Widget widget, XtPointer arg, XtPointer call)
{
	struct calc *calc = arg;
	(void)widget;
	(void)call;
	switch (calc->active) {
	case CALC_ADDRESS: calc_address_refresh(calc); break;
	case CALC_PLAN: if (calc_commit_context(calc)) calc_plan_refresh(calc); break;
	case CALC_POLICY: calc_policy_refresh(calc); break;
	case CALC_MULTICAST: calc_multicast_refresh(calc); break;
	case CALC_REGISTRIES: calc_registries_refresh(calc); break;
	case CALC_BASE: {
		Widget input = XtNameToWidget(calc->pages[CALC_BASE], "*baseInput");
		if (input != NULL)
			XtCallCallbacks(input, XmNactivateCallback, NULL);
		break;
	}
	}
}

static void
help(Widget widget, XtPointer arg, XtPointer call)
{
	struct calc *calc = arg;
	(void)widget;
	(void)call;
	Widget opener = XmGetFocusWidget(d32ui_shell(calc->ui));
	const char *volume = access(D32_CALC_HELP_VOLUME, R_OK) == 0 ? D32_CALC_HELP_VOLUME : NULL;
	(void)d32ui_help(calc->ui, opener, volume, "_hometopic",
	    "Network Calculator\n\n"
	    "Use Ctrl+Page Up or Ctrl+Page Down to change tabs. Ctrl+F focuses the first input.\n"
	    "Press Return in a single-line input to run its operation. F5 reruns the current view.\n\n"
	    "Address inspects prefixes and ranges. Normalize applies the proposed network address.\n"
	    "Plan builds allocations from the context prefix. Ctrl+O opens and Ctrl+S exports a plan\n"
	    "in the private ~/.dt/d32/plans folder.\n"
	    "Policy tests packet wildcards and route prefix-lists. Multicast maps groups and MACs.\n"
	    "Registries shows IANA special-purpose records and offline DB-IP country estimates.\n"
	    "An estimate requires one country across the whole address or prefix. NRO data remains held.\n"
	    "Base Converter accepts unsigned binary, decimal and hexadecimal values up to 128 bits.");
}

static void
toggle_donut(Widget widget, XtPointer arg, XtPointer call)
{
	struct calc *calc = arg;
	const XmToggleButtonCallbackStruct *toggle = call;
	(void)widget;
	if (toggle != NULL)
		calc->donut = toggle->set;
	calc_select_tab(calc, CALC_PLAN);
	calc_plan_donut(calc);
}

static Widget
make_menu(struct calc *calc, Widget body)
{
	const struct d32ui_menu_item items[] = {
		{D32UI_OPEN, open_plan, calc}, {D32UI_SAVE, save_plan, calc},
		{D32UI_EXIT, close_window, calc}, {D32UI_COPY, copy_selection, calc},
		{D32UI_FIND, find_field, calc}, {D32UI_REFRESH, refresh, calc},
		{D32UI_PREV_TAB, previous_tab, calc}, {D32UI_NEXT_TAB, next_tab, calc},
		{D32UI_HELP, help, calc}
	};
	Widget bar = d32ui_menu(calc->ui, body, items, sizeof(items) / sizeof(items[0]));
	if (bar == NULL)
		return NULL;
	XtVaSetValues(bar, XmNtopAttachment, XmATTACH_FORM, XmNleftAttachment, XmATTACH_FORM,
	    XmNrightAttachment, XmATTACH_FORM, NULL);
	Widget cascade = XtNameToWidget(bar, "view"), view = NULL;
	if (cascade != NULL && XmIsCascadeButton(cascade))
		XtVaGetValues(cascade, XmNsubMenuId, &view, NULL);
	if (view != NULL) {
		Widget donut = set_label(XtVaCreateManagedWidget("planDonut", xmToggleButtonWidgetClass,
		    view, XmNmnemonic, (KeySym)'D', XmNset, calc->donut, NULL), "Plan Donut");
		XtAddCallback(donut, XmNvalueChangedCallback, toggle_donut, calc);
	}
	return bar;
}

static Widget
make_context(struct calc *calc, Widget body, Widget above)
{
	Widget top = XtVaCreateManagedWidget("contextBar", xmRowColumnWidgetClass, body,
	    XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, above, XmNleftAttachment,
	    XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, XmNorientation, XmHORIZONTAL,
	    XmNpacking, XmPACK_TIGHT, NULL);
	Widget radio = XmCreateRadioBox(top, "addressFamily", NULL, 0);
	XtVaSetValues(radio, XmNorientation, XmHORIZONTAL, XmNpacking, XmPACK_TIGHT, NULL);
	calc->family_choice[0] = set_label(XtVaCreateManagedWidget("familyIPv4",
	    xmToggleButtonWidgetClass, radio, XmNmnemonic, (KeySym)'4', NULL), "IPv4");
	calc->family_choice[1] = set_label(XtVaCreateManagedWidget("familyIPv6",
	    xmToggleButtonWidgetClass, radio, XmNmnemonic, (KeySym)'6', NULL), "IPv6");
	for (size_t i = 0; i < 2; ++i)
		XtAddCallback(calc->family_choice[i], XmNvalueChangedCallback, family_change, calc);
	XtManageChild(radio);
	(void)set_label(XtVaCreateManagedWidget("contextLabel", xmLabelWidgetClass, top,
	    XmNtraversalOn, False, NULL), "Context prefix");
	calc->context_input = XtVaCreateManagedWidget("contextPrefix", xmTextFieldWidgetClass,
	    top, XmNcolumns, 27, XmNmaxLength, 160, XmNresizeWidth, False, NULL);
	if (d32ui_mono(calc->ui) != NULL)
		XtVaSetValues(calc->context_input, XmNfontList, d32ui_mono(calc->ui), NULL);
	XtAddCallback(calc->context_input, XmNactivateCallback, context_activate, calc);
	calc_set_context(calc, &calc->context);
	return top;
}

static bool
make_pages(struct calc *calc, Widget parent, Widget above)
{
	calc->notebook = XtVaCreateManagedWidget("calculator", xmNotebookWidgetClass, parent,
	    XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, above,
	    XmNbottomAttachment, XmATTACH_FORM, XmNleftAttachment, XmATTACH_FORM,
	    XmNrightAttachment, XmATTACH_FORM, XmNfirstPageNumber, CALC_ADDRESS,
	    XmNlastPageNumber, CALC_TAB_COUNT, XmNbackPageNumber, 0, NULL);
	Widget notebook = calc->notebook;
	calc->pages[CALC_ADDRESS] = calc_address_page(calc, notebook);
	calc->pages[CALC_PLAN] = calc_plan_page(calc, notebook);
	calc->pages[CALC_POLICY] = calc_policy_page(calc, notebook);
	calc->pages[CALC_MULTICAST] = calc_multicast_page(calc, notebook);
	calc->pages[CALC_REGISTRIES] = calc_registries_page(calc, notebook);
	calc->pages[CALC_BASE] = d32_base_panel(calc->ui, notebook);
	for (int i = CALC_ADDRESS; i <= CALC_TAB_COUNT; ++i) {
		if (calc->pages[i] == NULL)
			return false;
		XtVaSetValues(calc->pages[i], XmNnotebookChildType, XmPAGE, XmNpageNumber, i, NULL);
		(void)set_label(XtVaCreateManagedWidget(tabs[i].name, xmPushButtonWidgetClass,
		    notebook, XmNnotebookChildType, XmMAJOR_TAB, XmNpageNumber, i,
		    XmNmnemonic, tabs[i].mnemonic, NULL), tabs[i].label);
		XtManageChild(calc->pages[i]);
	}
	XtAddCallback(notebook, XmNpageChangedCallback, notebook_changed, calc);
	return true;
}

struct geometry { unsigned width, height; };

static int
dir_open(int parent, const char *name, bool create)
{
	if (create && mkdirat(parent, name, 0700) == -1 && errno != EEXIST)
		return -1;
	return openat(parent, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
}

static int
prefs_dir(void)
{
	const char *home = getenv("HOME");
	if (home == NULL || *home != '/' || home[1] == '\0')
		return -1;
	int fd = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	if (fd == -1)
		return -1;
	for (const char *p = home + 1; *p != '\0';) {
		size_t length = strcspn(p, "/");
		char part[NAME_MAX + 1];
		if (length == 0 || length >= sizeof(part))
			goto fail;
		memcpy(part, p, length);
		part[length] = '\0';
		if (strcmp(part, ".") == 0 || strcmp(part, "..") == 0)
			goto fail;
		int next = dir_open(fd, part, false);
		close(fd);
		if (next == -1)
			return -1;
		fd = next;
		p += length;
		if (*p == '/')
			++p;
	}
	struct stat sb;
	if (fstat(fd, &sb) == -1 || sb.st_uid != geteuid())
		goto fail;
	int dot = dir_open(fd, ".dt", true);
	close(fd);
	if (dot == -1)
		return -1;
	fd = dir_open(dot, "d32", true);
	close(dot);
	if (fd == -1)
		return -1;
	if (fstat(fd, &sb) == -1 || sb.st_uid != geteuid() || (sb.st_mode & 0777) != 0700)
		goto fail;
	return fd;
fail:
	close(fd);
	return -1;
}

static void
bad_pref(const char *key, void *unused)
{
	(void)unused;
	syslog(LOG_DAEMON | LOG_INFO, "d32calc: ignored invalid preference key=%s", key);
}

static unsigned
pref_number(const struct d32_prefs *prefs, const char *key, unsigned fallback, unsigned max)
{
	const char *text = d32_prefs_get(prefs, key, "");
	if (*text == '\0' || text[strspn(text, "0123456789")] != '\0')
		return fallback;
	errno = 0;
	unsigned long value = strtoul(text, NULL, 10);
	return errno == 0 && value <= max ? (unsigned)value : fallback;
}

static bool
load_settings(struct calc *calc, struct geometry *geometry, enum calc_tab *tab)
{
	int fd = prefs_dir();
	if (fd == -1)
		return false;
	char path[PATH_MAX];
	int length = snprintf(path, sizeof(path), "%s/.dt/d32", getenv("HOME"));
	if (length > 0 && (size_t)length < sizeof(path) && realpath(path, calc->prefs_path) != NULL) {
		struct stat st;
		int plan = dir_open(fd, "plans", true);
		length = snprintf(calc->plan_path, sizeof(calc->plan_path), "%s/plans", calc->prefs_path);
		if (plan != -1 && length > 0 && (size_t)length < sizeof(calc->plan_path) &&
		    fstat(plan, &st) == 0 && st.st_uid == geteuid() &&
		    (st.st_mode & 0777) == 0700 && fchdir(plan) == 0)
			calc->plan_fd = plan;
		else if (plan != -1)
			close(plan);
	} else
		calc->prefs_path[0] = '\0';
	enum d32_error status = d32_prefs_load(fd, "d32calc", 1, bad_pref, NULL, &calc->prefs);
	close(fd);
	if (status != D32E_OK) {
		d32_prefs_free(calc->prefs);
		calc->prefs = NULL;
		return status == D32E_NEWER;
	}
	calc->family = pref_number(calc->prefs, "family", 4, 6) == 6 ? D32_IPV6 : D32_IPV4;
	calc->context.addr.fam = calc->family;
	calc->host_policy = (d32_host_policy)pref_number(calc->prefs, "host_policy", D32_HOST_SUBNET, D32_HOST_SINGLE);
	calc->donut = pref_number(calc->prefs, "donut", 0, 1) != 0;
	unsigned page = pref_number(calc->prefs, "tab", CALC_ADDRESS, CALC_TAB_COUNT);
	*tab = page != 0 ? (enum calc_tab)page : CALC_ADDRESS;
	geometry->width = pref_number(calc->prefs, "pane.width", 920, UINT16_MAX);
	geometry->height = pref_number(calc->prefs, "pane.height", 620, UINT16_MAX);
	return false;
}

static void
set_pref(struct d32_prefs *prefs, const char *key, unsigned value)
{
	char text[16];
	snprintf(text, sizeof(text), "%u", value);
	(void)d32_prefs_set(prefs, key, text);
}

static void
save_settings(struct calc *calc, const struct calc *initial, struct geometry geometry)
{
	if (calc->prefs == NULL)
		return;
	Dimension width, height;
	XtVaGetValues(d32ui_shell(calc->ui), XmNwidth, &width, XmNheight, &height, NULL);
	if (calc->active != initial->active)
		set_pref(calc->prefs, "tab", calc->active);
	if (calc->family != initial->family)
		set_pref(calc->prefs, "family", calc->family);
	if (calc->host_policy != initial->host_policy)
		set_pref(calc->prefs, "host_policy", calc->host_policy);
	if (calc->donut != initial->donut)
		set_pref(calc->prefs, "donut", calc->donut);
	if (width != geometry.width)
		set_pref(calc->prefs, "pane.width", width);
	if (height != geometry.height)
		set_pref(calc->prefs, "pane.height", height);
	(void)d32_prefs_save(calc->prefs);
}

int
main(int argc, char **argv)
{
	struct calc calc = {.family = D32_IPV4, .context.addr.fam = D32_IPV4,
	    .host_policy = D32_HOST_SUBNET, .plan_fd = -1};
	struct geometry geometry = {920, 620};
	enum calc_tab tab = CALC_ADDRESS;
	int result = 1;
	(void)umask(077);
	calc.ui = d32ui_init(&argc, argv, "D32Calc");
	if (calc.ui == NULL)
		return 1;
	if (d32ui_set_handling(calc.ui, D32UI_PUBLIC) != D32E_OK)
		goto done;
	bool prefs_newer = load_settings(&calc, &geometry, &tab);
	XtVaSetValues(d32ui_shell(calc.ui), XtNtitle, "Network Calculator", NULL);
	Widget body = d32ui_body(calc.ui);
	Widget menu = make_menu(&calc, body);
	if (menu == NULL || !make_pages(&calc, body, make_context(&calc, body, menu)))
		goto done;
	calc_select_tab(&calc, tab);
	struct calc initial = calc;
	d32ui_geometry(calc.ui, geometry.width, geometry.height, 0);
	d32ui_on_close(calc.ui, close_window, &calc);
	if (prefs_newer)
		d32ui_status(calc.ui, D32_PREFS_NEWER_STATUS);
	calc_registry_start(&calc, D32_CALC_DATA_ROOT);
	d32ui_show(calc.ui);
	focus_tab(&calc);
	Dimension width, height;
	XtVaGetValues(d32ui_shell(calc.ui), XmNwidth, &width, XmNheight, &height, NULL);
	geometry = (struct geometry){width, height};
#ifdef __OpenBSD__
	if ((calc.prefs_path[0] != '\0' && unveil(calc.prefs_path, "rwc") == -1) ||
	    (unveil("/usr/local/dt/appconfig/help", "r") == -1 && errno != ENOENT) ||
	    unveil(NULL, NULL) == -1 || pledge("stdio rpath wpath cpath flock", NULL) == -1)
		goto done;
#endif
	d32ui_run(calc.ui);
	save_settings(&calc, &initial, geometry);
	result = 0;
done:
	calc_registry_stop(&calc);
	if (calc.plan_fd != -1)
		close(calc.plan_fd);
	d32_prefs_free(calc.prefs);
	d32ui_destroy(calc.ui);
	free(calc.registry);
	return result;
}
