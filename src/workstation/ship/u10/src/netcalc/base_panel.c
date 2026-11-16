#include "base_convert.h"
#include "base_panel.h"

#include <stdio.h>
#include <stdlib.h>

#include <Xm/Label.h>
#include <Xm/PushB.h>
#include <Xm/RowColumn.h>
#include <Xm/Text.h>
#include <Xm/TextF.h>
#include <Xm/ToggleB.h>

static const struct {
	enum d32_base base;
	const char *label;
} bases[] = {
	{ D32_BASE_BINARY, "Binary" },
	{ D32_BASE_DECIMAL, "Decimal" },
	{ D32_BASE_HEX, "Hexadecimal" }
};

struct base_panel {
	d32ui *ui;
	Widget toggles[3], input, binary, decimal, hex;
};

static void
reset_results(Widget widget, XtPointer data, XtPointer call)
{
	struct base_panel *panel = data;

	(void)widget;
	(void)call;
	XmTextSetString(panel->binary, "");
	XmTextFieldSetString(panel->decimal, "");
	XmTextFieldSetString(panel->hex, "");
	d32ui_status(panel->ui, "Enter a value and press Convert.");
}

static void
change_base(Widget widget, XtPointer data, XtPointer call)
{
	const XmToggleButtonCallbackStruct *toggle = call;

	if (toggle->set)
		reset_results(widget, data, call);
}

static void
convert(Widget widget, XtPointer data, XtPointer call)
{
	struct base_panel *panel = data;
	struct d32_base_output out;
	enum d32_base base = D32_BASE_DECIMAL;
	enum d32_base_status status;
	char message[128], *input;
	size_t column;

	(void)widget;
	(void)call;
	for (size_t i = 0; i < sizeof(bases) / sizeof(bases[0]); i++)
		if (XmToggleButtonGetState(panel->toggles[i]))
			base = bases[i].base;
	input = XmTextFieldGetString(panel->input);
	status = d32_base_convert(input, base, &out, &column);
	XtFree(input);
	switch (status) {
	case D32_BASE_OK:
		XmTextSetString(panel->binary, out.binary);
		XmTextFieldSetString(panel->decimal, out.decimal);
		XmTextFieldSetString(panel->hex, out.hex);
		d32ui_status(panel->ui, "[OK] Converted");
		return;
	case D32_BASE_EMPTY:
		d32ui_status(panel->ui, "Enter a value to convert.");
		return;
	case D32_BASE_DIGIT:
		snprintf(message, sizeof(message), "Invalid base-%d digit at column %zu.",
		    base, column);
		break;
	case D32_BASE_OVERFLOW:
		snprintf(message, sizeof(message), "Value exceeds 128 bits at column %zu.", column);
		break;
	default:
		return;
	}
	d32ui_status(panel->ui, message);
}

static void
free_panel(Widget widget, XtPointer data, XtPointer call)
{
	(void)widget;
	(void)call;
	free(data);
}

static void
label(Widget parent, const char *name, const char *text, Dimension width)
{
	XmString string = XmStringCreateLocalized((char *)text);
	Widget widget = XtVaCreateManagedWidget(name, xmLabelWidgetClass, parent,
	    XmNlabelString, string, XmNalignment, XmALIGNMENT_BEGINNING,
	    XmNtraversalOn, False, NULL);

	if (width != 0)
		XtVaSetValues(widget, XmNwidth, width, XmNrecomputeSize, False, NULL);
	XmStringFree(string);
}

static Widget
field(struct base_panel *panel, Widget parent, const char *name, const char *text,
    Boolean editable, Boolean multiline)
{
	Widget row = XtVaCreateManagedWidget("valueRow", xmRowColumnWidgetClass, parent,
	    XmNorientation, XmHORIZONTAL, XmNpacking, XmPACK_TIGHT, NULL);
	Widget value;

	label(row, "valueLabel", text, 110);
	if (multiline)
		value = XtVaCreateManagedWidget(name, xmTextWidgetClass, row,
		    XmNcolumns, 40, XmNrows, 4, XmNwordWrap, True,
		    XmNeditMode, XmMULTI_LINE_EDIT, XmNeditable, False, NULL);
	else
		value = XtVaCreateManagedWidget(name, xmTextFieldWidgetClass, row,
		    XmNcolumns, 40, XmNmaxLength, 256, XmNeditable, editable, NULL);
	if (d32ui_mono(panel->ui) != NULL)
		XtVaSetValues(value, XmNfontList, d32ui_mono(panel->ui), NULL);
	return value;
}

Widget
d32_base_panel(d32ui *ui, Widget parent)
{
	struct base_panel *panel = calloc(1, sizeof(*panel));
	Widget box, choices, button;

	if (panel == NULL)
		return NULL;
	panel->ui = ui;
	box = XtVaCreateWidget("baseConverter", xmRowColumnWidgetClass, parent,
	    XmNorientation, XmVERTICAL, XmNpacking, XmPACK_TIGHT,
	    XmNmarginWidth, 12, XmNmarginHeight, 12, NULL);
	XtAddCallback(box, XmNdestroyCallback, free_panel, panel);
	label(box, "baseIntro", "Input base (unsigned, up to 128 bits)", 0);
	choices = XmCreateRadioBox(box, "inputBase", NULL, 0);
	XtVaSetValues(choices, XmNorientation, XmHORIZONTAL, XmNpacking, XmPACK_TIGHT, NULL);
	for (size_t i = 0; i < sizeof(bases) / sizeof(bases[0]); i++) {
		panel->toggles[i] = XtVaCreateManagedWidget(bases[i].label,
		    xmToggleButtonWidgetClass, choices, NULL);
		XtAddCallback(panel->toggles[i], XmNvalueChangedCallback, change_base, panel);
		if (bases[i].base == D32_BASE_DECIMAL)
			XmToggleButtonSetState(panel->toggles[i], True, False);
	}
	XtManageChild(choices);
	panel->input = field(panel, box, "baseInput", "Input", True, False);
	button = XtVaCreateManagedWidget("Convert", xmPushButtonWidgetClass, box, NULL);
	panel->binary = field(panel, box, "baseBinary", "Binary", False, True);
	panel->decimal = field(panel, box, "baseDecimal", "Decimal", False, False);
	panel->hex = field(panel, box, "baseHex", "Hexadecimal", False, False);
	XtAddCallback(button, XmNactivateCallback, convert, panel);
	XtAddCallback(panel->input, XmNactivateCallback, convert, panel);
	XtAddCallback(panel->input, XmNvalueChangedCallback, reset_results, panel);
	return box;
}
