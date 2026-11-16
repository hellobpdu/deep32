#include "d32ui.h"

#include <Xm/CascadeB.h>
#include <Xm/PushB.h>
#include <Xm/RowColumn.h>
#include <X11/keysym.h>

enum menu_group { MENU_FILE, MENU_EDIT, MENU_VIEW, MENU_HELP, MENU_COUNT };

struct menu_group_spec {
	const char *name, *label;
	KeySym mnemonic;
};

struct menu_action_spec {
	enum menu_group group;
	const char *name, *label, *accelerator, *accelerator_text;
	KeySym mnemonic;
};

static const struct menu_group_spec groups[MENU_COUNT] = {
	[MENU_FILE] = {"file", "File", XK_F},
	[MENU_EDIT] = {"edit", "Edit", XK_E},
	[MENU_VIEW] = {"view", "View", XK_V},
	[MENU_HELP] = {"help", "Help", XK_H}
};

static const struct menu_action_spec actions[] = {
	[D32UI_OPEN] = {MENU_FILE, "open", "Open", "Ctrl<Key>o", "Ctrl+O", XK_O},
	[D32UI_SAVE] = {MENU_FILE, "save", "Save", "Ctrl<Key>s", "Ctrl+S", XK_S},
	[D32UI_EXIT] = {MENU_FILE, "exit", "Exit", "Ctrl<Key>q", "Ctrl+Q", XK_x},
	[D32UI_COPY] = {MENU_EDIT, "copy", "Copy", "Ctrl<Key>c", "Ctrl+C", XK_C},
	[D32UI_FIND] = {MENU_EDIT, "find", "Find", "Ctrl<Key>f", "Ctrl+F", XK_F},
	[D32UI_REFRESH] = {MENU_VIEW, "refresh", "Refresh / Rerun", "<Key>F5", "F5", XK_R},
	[D32UI_PREV_TAB] = {MENU_VIEW, "previousTab", "Previous Tab", "Ctrl<Key>Prior",
	    "Ctrl+Page Up", XK_P},
	[D32UI_NEXT_TAB] = {MENU_VIEW, "nextTab", "Next Tab", "Ctrl<Key>Next",
	    "Ctrl+Page Down", XK_N},
	[D32UI_HELP] = {MENU_HELP, "contextHelp", "Context Help", "<Key>F1", "F1", XK_H}
};

#define ACTION_COUNT (sizeof(actions) / sizeof(actions[0]))

static Widget
create_item(Widget menu, const struct menu_action_spec *spec,
    const struct d32ui_menu_item *item)
{
	XmString label, accelerator_text;
	Widget button = NULL;
	Arg args[5];
	Cardinal n = 0;

	label = XmStringCreateLocalized((char *)spec->label);
	accelerator_text = XmStringCreateLocalized((char *)spec->accelerator_text);
	if (label != NULL && accelerator_text != NULL) {
		XtSetArg(args[n], XmNlabelString, label); n++;
		XtSetArg(args[n], XmNmnemonic, spec->mnemonic); n++;
		XtSetArg(args[n], XmNaccelerator, spec->accelerator); n++;
		XtSetArg(args[n], XmNacceleratorText, accelerator_text); n++;
		XtSetArg(args[n], XmNsensitive, item->callback != NULL); n++;
		button = XmCreatePushButton(menu, (char *)spec->name, args, n);
		if (button != NULL) {
			if (item->callback != NULL)
				XtAddCallback(button, XmNactivateCallback, item->callback, item->data);
			XtManageChild(button);
		}
	}
	if (label != NULL)
		XmStringFree(label);
	if (accelerator_text != NULL)
		XmStringFree(accelerator_text);
	return button;
}

static Widget
create_group(Widget bar, enum menu_group group,
    const struct d32ui_menu_item *const ordered[ACTION_COUNT])
{
	const struct menu_group_spec *spec = &groups[group];
	Widget menu, cascade;
	XmString label;
	Arg args[4];
	Cardinal n = 0;
	size_t i, count = 0;

	menu = XmCreatePulldownMenu(bar, (char *)spec->name, NULL, 0);
	if (menu == NULL)
		return NULL;
	for (i = 0; i < ACTION_COUNT; i++) {
		if (ordered[i] == NULL || actions[i].group != group)
			continue;
		if (create_item(menu, &actions[i], ordered[i]) == NULL)
			return NULL;
		count++;
	}
	label = XmStringCreateLocalized((char *)spec->label);
	if (label == NULL)
		return NULL;
	XtSetArg(args[n], XmNlabelString, label); n++;
	XtSetArg(args[n], XmNmnemonic, spec->mnemonic); n++;
	XtSetArg(args[n], XmNsubMenuId, menu); n++;
	XtSetArg(args[n], XmNsensitive, count != 0); n++;
	cascade = XmCreateCascadeButton(bar, (char *)spec->name, args, n);
	XmStringFree(label);
	if (cascade != NULL)
		XtManageChild(cascade);
	return cascade;
}

Widget
d32ui_menu(d32ui *ui, Widget parent, const struct d32ui_menu_item *items, size_t n)
{
	const struct d32ui_menu_item *ordered[ACTION_COUNT] = {NULL};
	Widget bar, cascade;
	size_t i;
	enum menu_group group;

	if (ui == NULL || parent == NULL || (items == NULL && n != 0) ||
	    n > ACTION_COUNT)
		return NULL;
	for (i = 0; i < n; i++) {
		size_t action = (size_t)items[i].action;

		if (action >= ACTION_COUNT || actions[action].label == NULL ||
		    ordered[action] != NULL)
			return NULL;
		ordered[action] = &items[i];
	}
	bar = XmCreateMenuBar(parent, "menuBar", NULL, 0);
	if (bar == NULL)
		return NULL;
	for (group = MENU_FILE; group < MENU_COUNT; group++) {
		cascade = create_group(bar, group, ordered);
		if (cascade == NULL) {
			XtDestroyWidget(bar);
			return NULL;
		}
		if (group == MENU_HELP)
			XtVaSetValues(bar, XmNmenuHelpWidget, cascade, NULL);
	}
	/* Motif handles F1 in this menu bar's shell. Dialogs retain their own Help. */
	XtManageChild(bar);
	return bar;
}
