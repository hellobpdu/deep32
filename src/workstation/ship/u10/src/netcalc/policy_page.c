#include "calc.h"

#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <X11/keysym.h>
#include <Xm/Form.h>
#include <Xm/Label.h>
#include <Xm/PushB.h>
#include <Xm/RowColumn.h>
#include <Xm/ScrolledW.h>
#include <Xm/Text.h>
#include <Xm/TextF.h>
#include <Xm/ToggleB.h>

#define RULE_LIMIT 512
#define RULE_TEXT_LIMIT 32768

enum policy_mode { WILDCARD, PREFIX_LIST, MODE_COUNT };

struct policy_page {
	struct calc *calc;
	Widget panel[MODE_COUNT], selector[MODE_COUNT], title[MODE_COUNT];
	Widget output[MODE_COUNT], wild[3], rules, route;
	enum policy_mode mode;
	uint8_t family;
	char status[MODE_COUNT][192];
	bool evaluated[MODE_COUNT];
};

static const char *const prompt[MODE_COUNT] = {
	"Enter a base address, wildcard mask, and candidate. Press Match.",
	"Enter one rule per line and a candidate prefix. Press Evaluate."
};

static void
set_title(Widget widget, const char *text)
{
	XmString label = XmStringCreateLocalized((char *)text);
	XtVaSetValues(widget, XmNlabelString, label, NULL);
	XmStringFree(label);
}

static void
present(struct policy_page *page, enum policy_mode mode, const char *status,
    const char *output, bool evaluated)
{
	(void)snprintf(page->status[mode], sizeof(page->status[mode]), "%s", status);
	XmTextSetString(page->output[mode], (char *)output);
	XmTextSetTopCharacter(page->output[mode], 0);
	page->evaluated[mode] = evaluated;
	if (page->calc->active == CALC_POLICY && page->mode == mode)
		d32ui_status(page->calc->ui, status);
}

static void
update_family(struct policy_page *page)
{
	if (page->family == page->calc->family)
		return;
	page->family = page->calc->family;
	set_title(page->title[WILDCARD], page->family == D32_IPV4 ?
	    "IPv4 wildcard (packet match)" :
	    "IPv6 bit-mask (generic math, outside IOS syntax)");
	set_title(page->title[PREFIX_LIST], page->family == D32_IPV4 ?
	    "IPv4 prefix-list (route match)" : "IPv6 prefix-list (route match)");
	for (int i = 0; i < MODE_COUNT; ++i)
		present(page, (enum policy_mode)i, "[?] Enter policy inputs", prompt[i], false);
}

void
calc_policy_family_changed(struct calc *calc)
{
	if (calc->policy != NULL)
		update_family(calc->policy);
}

void
calc_policy_focus(struct calc *calc)
{
	struct policy_page *page = calc->policy;
	if (page != NULL)
		(void)XmProcessTraversal(page->mode == WILDCARD ? page->wild[0] : page->rules,
		    XmTRAVERSE_CURRENT);
}

static void
policy_error(struct policy_page *page, enum policy_mode mode, const char *field,
    d32_net_status status, const d32_net_error *error)
{
	char text[192];
	const char *message = d32_net_status_name(status);

	if (mode == PREFIX_LIST && error->line != 0 && strcmp(field, "Rule") == 0)
		(void)snprintf(text, sizeof(text), "[x] %s line %zu, column %zu (%s).",
		    field, error->line, error->column, message);
	else
		(void)snprintf(text, sizeof(text), "[x] %s at column %zu (%s).",
		    field, error->column == 0 ? (size_t)1 : error->column, message);
	present(page, mode, text, text, true);
}

static bool
append(char *text, size_t capacity, size_t *length, const char *format, ...)
{
	va_list args;
	int written;

	va_start(args, format);
	written = vsnprintf(text + *length, capacity - *length, format, args);
	va_end(args);
	if (written < 0 || (size_t)written >= capacity - *length)
		return false;
	*length += (size_t)written;
	return true;
}

static void
wild_grid(const d32_addr *base, const d32_mask *mask, const d32_addr *candidate,
    const d32_wild_result *result, char *text, size_t capacity, size_t *length)
{
	static const char *const row_name[] = {
		"Base", "Ignore", "Candidate", "Pattern", "Fail"
	};
	unsigned width = (unsigned)d32_addr_width(base->fam);

	for (unsigned start = 0; start < width; start += 32) {
		char rows[5][36];
		unsigned column = 0;

		for (unsigned bit = start; bit < start + 32; ++bit) {
			unsigned byte = bit / 8, flag = 0x80U >> (bit % 8);
			bool base_bit = (base->b[byte] & flag) != 0;
			bool ignore_bit = (mask->b[byte] & flag) != 0;
			bool candidate_bit = (candidate->b[byte] & flag) != 0;

			if (bit != start && bit % 8 == 0) {
				for (size_t row = 0; row < 5; ++row)
					rows[row][column] = ' ';
				++column;
			}
			rows[0][column] = base_bit ? '1' : '0';
			rows[1][column] = ignore_bit ? '1' : '0';
			rows[2][column] = candidate_bit ? '1' : '0';
			rows[3][column] = result->pattern[bit];
			rows[4][column] = ignore_bit ? '-' : (base_bit == candidate_bit ? '.' : 'x');
			++column;
		}
		(void)append(text, capacity, length, "\nBits %u-%u\n", start, start + 31);
		for (size_t row = 0; row < 5; ++row) {
			rows[row][column] = '\0';
			(void)append(text, capacity, length, "%-10s %s\n", row_name[row], rows[row]);
		}
	}
}

static bool
read_wild(struct policy_page *page, d32_addr *base, d32_mask *mask,
    d32_addr *candidate)
{
	static const char *const field[] = {"Base", "Wildcard mask", "Candidate"};

	for (size_t i = 0; i < 3; ++i) {
		char *text = XmTextFieldGetString(page->wild[i]);
		d32_net_error error = {0};
		d32_net_status status;
		uint8_t family;

		if (i == 1) {
			status = d32_mask_parse(text, mask, &error);
			family = status == D32_NET_OK ? mask->fam : 0;
		} else {
			d32_addr *address = i == 0 ? base : candidate;
			status = d32_addr_parse(text, address, NULL, &error);
			family = status == D32_NET_OK ? address->fam : 0;
		}
		XtFree(text);
		if (status == D32_NET_OK && family != page->family)
			status = D32_NET_FAMILY;
		if (status != D32_NET_OK) {
			policy_error(page, WILDCARD, field[i], status, &error);
			(void)XmProcessTraversal(page->wild[i], XmTRAVERSE_CURRENT);
			return false;
		}
	}
	return true;
}

static void
match_wildcard(struct policy_page *page)
{
	d32_addr base, candidate;
	d32_mask mask;
	d32_wild_result result;
	d32_net_status status;
	char count[D32_COUNT_TEXT_CAP], message[192], output[2048] = "";
	size_t length = 0;

	update_family(page);
	if (!read_wild(page, &base, &mask, &candidate))
		return;
	status = d32_wildcard_match(&base, &mask, &candidate, &result);
	if (status != D32_NET_OK) {
		policy_error(page, WILDCARD, "Match", status, &(d32_net_error){0});
		return;
	}
	(void)d32_count_format(&result.count, true, count, sizeof(count));
	if (result.matched) {
		(void)snprintf(message, sizeof(message), "[OK] Wildcard match yes. %s possible addresses.", count);
		(void)append(output, sizeof(output), &length, "Match yes\n");
	} else {
		(void)snprintf(message, sizeof(message), "[OK] Wildcard match no. First failing bit %d.",
		    result.first_failed_bit);
		(void)append(output, sizeof(output), &length,
		    "Match no\nFirst failing bit %d (bit 0 is the MSB)\n", result.first_failed_bit);
	}
	(void)append(output, sizeof(output), &length, "Possible addresses  %s\nSamples             ", count);
	for (size_t i = 0; i < result.nsamples; ++i) {
		char addr[D32_ADDR_TEXT_CAP];
		(void)d32_addr_format(&result.samples[i], addr, sizeof(addr));
		(void)append(output, sizeof(output), &length, "%s%s", i == 0 ? "" : "\n                    ", addr);
	}
	(void)append(output, sizeof(output), &length, "\n\nMask 1 and pattern ? mean ignore. x marks a failed check.\n");
	wild_grid(&base, &mask, &candidate, &result, output, sizeof(output), &length);
	present(page, WILDCARD, message, output, true);
}

static void
render_trace(struct policy_page *page, const d32_prefix *candidate,
    const d32_rule *rules, const d32_rule_trace *trace,
    const d32_rule_decision *decision)
{
	char addr[D32_ADDR_TEXT_CAP], status[192];
	size_t length = 0, capacity = decision->ntrace * 160 + 320;
	char *text = malloc(capacity);
	int seq_width = 3;
	bool ok;

	if (text == NULL) {
		present(page, PREFIX_LIST, "[x] Trace unavailable (out of memory).", "", true);
		return;
	}
	if (decision->matched)
		(void)snprintf(status, sizeof(status), "[OK] Prefix-list %s at sequence %" PRIu64 ".",
		    decision->permit ? "permit" : "deny", decision->seq);
	else
		(void)snprintf(status, sizeof(status), "[OK] Prefix-list implicit deny.");
	if (decision->ntrace != 0) {
		int digits = snprintf(NULL, 0, "%" PRIu64, trace[decision->ntrace - 1].seq);
		if (digits > seq_width)
			seq_width = digits;
	}
	ok = append(text, capacity, &length, "%s\n%-*s %-6s %-9s %-12s %-7s %s\n",
	    status + 5, seq_width, "Seq", "Family", "Contained", "Length range", "Result", "Rule");
	for (size_t i = 0; ok && i < decision->ntrace; ++i) {
		const d32_rule *rule = &rules[i];
		const d32_rule_trace *row = &trace[i];
		char bounds[16];
		const char *action = rule->permit ? "permit" : "deny";

		(void)d32_addr_format(&rule->prefix.addr, addr, sizeof(addr));
		(void)snprintf(bounds, sizeof(bounds), "%u-%u %s", (unsigned)rule->minimum,
		    (unsigned)rule->maximum, row->length_ok ? "yes" : "no");
		ok = append(text, capacity, &length,
		    "%-*" PRIu64 " %-6s %-9s %-12s %-7s %s %s/%u\n", seq_width, row->seq,
		    row->family_ok ? "yes" : "no", row->contained ? "yes" : "no", bounds,
		    row->matched ? action : "skip", action, addr, (unsigned)rule->prefix.len);
	}
	if (ok && !decision->matched)
		ok = append(text, capacity, &length, "\nimplicit deny\n");
	(void)d32_addr_format(&candidate->addr, addr, sizeof(addr));
	if (ok)
		ok = append(text, capacity, &length, "\nCandidate %s/%u\n", addr, (unsigned)candidate->len);
	if (ok)
		present(page, PREFIX_LIST, status, text, true);
	else
		present(page, PREFIX_LIST, "[x] Trace exceeded its display limit.", "", true);
	free(text);
}

static void
evaluate_list(struct policy_page *page)
{
	d32_rule rules[RULE_LIMIT];
	d32_rule_trace trace[RULE_LIMIT];
	d32_rule_decision decision;
	d32_prefix candidate;
	d32_net_error error = {0};
	d32_net_status status;
	size_t count;
	char *text;

	update_family(page);
	text = XmTextGetString(page->rules);
	if (text != NULL && strlen(text) > RULE_TEXT_LIMIT) {
		XtFree(text);
		present(page, PREFIX_LIST, "[x] Rule text exceeds 32 KiB.", "", true);
		return;
	}
	status = d32_prefix_list_parse(text, page->family, false, rules, RULE_LIMIT, &count, &error);
	XtFree(text);
	if (status != D32_NET_OK) {
		policy_error(page, PREFIX_LIST, "Rule", status, &error);
		(void)XmProcessTraversal(page->rules, XmTRAVERSE_CURRENT);
		return;
	}
	text = XmTextFieldGetString(page->route);
	status = d32_prefix_parse(text, &candidate, NULL, &error);
	XtFree(text);
	if (status != D32_NET_OK) {
		policy_error(page, PREFIX_LIST, "Candidate prefix", status, &error);
		(void)XmProcessTraversal(page->route, XmTRAVERSE_CURRENT);
		return;
	}
	status = d32_prefix_list_eval(rules, count, &candidate, trace, RULE_LIMIT, &decision);
	if (status == D32_NET_OK)
		render_trace(page, &candidate, rules, trace, &decision);
	else
		policy_error(page, PREFIX_LIST, "Evaluation", status, &(d32_net_error){0});
}

static void
run_wild(Widget widget, XtPointer data, XtPointer call)
{
	(void)widget;
	(void)call;
	match_wildcard(data);
}

static void
run_list(Widget widget, XtPointer data, XtPointer call)
{
	(void)widget;
	(void)call;
	evaluate_list(data);
}

static void
select_mode(Widget widget, XtPointer data, XtPointer call)
{
	struct policy_page *page = data;
	const XmToggleButtonCallbackStruct *choice = call;
	enum policy_mode mode = widget == page->selector[WILDCARD] ? WILDCARD : PREFIX_LIST;

	if (!choice->set || page->mode == mode)
		return;
	XtUnmanageChild(page->panel[page->mode]);
	page->mode = mode;
	update_family(page);
	XtManageChild(page->panel[mode]);
	d32ui_status(page->calc->ui, page->status[mode]);
	calc_policy_focus(page->calc);
}

static void
input_changed(Widget widget, XtPointer data, XtPointer call)
{
	struct policy_page *page = data;
	enum policy_mode mode = widget == page->rules || widget == page->route ? PREFIX_LIST : WILDCARD;

	(void)call;
	if (page->evaluated[mode])
		present(page, mode, "[?] Inputs changed. Run the policy check.", prompt[mode], false);
}

static void
traverse(Widget widget, XEvent *event, String *params, Cardinal *count)
{
	(void)event;
	(void)XmProcessTraversal(widget, *count != 0 && strcmp(params[0], "previous") == 0 ?
	    XmTRAVERSE_PREV_TAB_GROUP : XmTRAVERSE_NEXT_TAB_GROUP);
}

static void
mode_shortcut(Widget widget, XEvent *event, String *params, Cardinal *count)
{
	(void)event;
	if (*count != 1)
		return;
	for (Widget ancestor = widget; ancestor != NULL; ancestor = XtParent(ancestor)) {
		if (strcmp(XtName(ancestor), "policyPage") == 0) {
			struct policy_page *page = NULL;
			XtVaGetValues(ancestor, XmNuserData, &page, NULL);
			if (page != NULL)
				XmToggleButtonSetState(page->selector[strcmp(params[0], "wildcard") == 0 ?
				    WILDCARD : PREFIX_LIST], True, True);
			return;
		}
	}
}

static Widget
form_label(Widget parent, const char *name, const char *text, Widget above)
{
	Widget label = XtVaCreateManagedWidget(name, xmLabelWidgetClass, parent,
	    XmNalignment, XmALIGNMENT_BEGINNING, XmNtraversalOn, False,
	    XmNtopAttachment, above == NULL ? XmATTACH_FORM : XmATTACH_WIDGET,
	    XmNtopWidget, above, XmNleftAttachment, XmATTACH_FORM, NULL);
	set_title(label, text);
	return label;
}

static Widget
policy_field(struct policy_page *page, Widget pane, Widget above,
    const char *name, const char *label)
{
	Widget value = XtVaCreateManagedWidget(name, xmTextFieldWidgetClass, pane,
	    XmNcolumns, 24, XmNmaxLength, 128, XmNnavigationType, XmTAB_GROUP,
	    XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, above, XmNtopOffset, 3,
	    XmNleftAttachment, XmATTACH_FORM, XmNleftOffset, 110,
	    XmNrightAttachment, XmATTACH_FORM, NULL);
	Widget caption = form_label(pane, "policyFieldLabel", label, NULL);

	XtVaSetValues(caption, XmNtopAttachment, XmATTACH_OPPOSITE_WIDGET, XmNtopWidget, value,
	    XmNbottomAttachment, XmATTACH_OPPOSITE_WIDGET, XmNbottomWidget, value, NULL);
	if (d32ui_mono(page->calc->ui) != NULL)
		XtVaSetValues(value, XmNfontList, d32ui_mono(page->calc->ui), NULL);
	XtAddCallback(value, XmNvalueChangedCallback, input_changed, page);
	return value;
}

static Widget
policy_text(struct policy_page *page, Widget pane, Widget above, const char *name,
    bool editable, XtTranslations keys)
{
	Widget text = XmCreateScrolledText(pane, (char *)name, NULL, 0);
	Widget frame = XtParent(text);

	XtVaSetValues(text, XmNeditMode, XmMULTI_LINE_EDIT, XmNeditable, editable,
	    XmNcursorPositionVisible, editable, XmNrows, 3,
	    XmNcolumns, 24, XmNwordWrap, False, XmNnavigationType, XmTAB_GROUP,
	    XmNscrollHorizontal, True, XmNscrollVertical, True, NULL);
	XtVaSetValues(frame, XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, above,
	    XmNtopOffset, 4, XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM,
	    XmNbottomAttachment, editable ? XmATTACH_NONE : XmATTACH_FORM, NULL);
	if (d32ui_mono(page->calc->ui) != NULL)
		XtVaSetValues(text, XmNfontList, d32ui_mono(page->calc->ui), NULL);
	XtOverrideTranslations(text, keys);
	XtManageChild(text);
	return text;
}

static Widget
policy_button(Widget parent, Widget above, const char *name, const char *label,
    KeySym mnemonic, XtCallbackProc action, struct policy_page *page)
{
	Widget button = XtVaCreateManagedWidget(name, xmPushButtonWidgetClass, parent,
	    XmNmnemonic, mnemonic, XmNnavigationType, XmTAB_GROUP,
	    XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, above, XmNtopOffset, 3,
	    XmNleftAttachment, XmATTACH_FORM, NULL);
	set_title(button, label);
	XtAddCallback(button, XmNactivateCallback, action, page);
	return button;
}

static void
destroy_policy(Widget widget, XtPointer data, XtPointer call)
{
	struct policy_page *page = data;

	(void)widget;
	(void)call;
	page->calc->policy = NULL;
	free(page);
}

Widget
calc_policy_page(struct calc *calc, Widget parent)
{
	static XtActionsRec actions[] = {
		{"d32-policy-traverse", traverse}, {"d32-policy-mode", mode_shortcut}
	};
	static const char *const names[] = {"policyWildcard", "policyPrefixList"};
	static const char *const labels[] = {"Wildcard", "Prefix-list"};
	static const KeySym mnemonic[] = {XK_w, XK_l};
	struct policy_page *page = calloc(1, sizeof(*page));
	Widget box, choice, wild, list, grammar, button;
	XtTranslations keys, modes;

	if (page == NULL)
		return NULL;
	page->calc = calc;
	calc->policy = page;
	box = XtVaCreateWidget("policyPage", xmFormWidgetClass, parent, XmNuserData, page, NULL);
	XtAddCallback(box, XmNdestroyCallback, destroy_policy, page);
	XtAppAddActions(d32ui_context(calc->ui), actions, XtNumber(actions));
	keys = XtParseTranslationTable("#override\nShift<Key>Tab: d32-policy-traverse(previous)\n"
	    "~Shift<Key>Tab: d32-policy-traverse(next)");
	modes = XtParseTranslationTable("#override\nAlt<Key>l: d32-policy-mode(prefix)\n"
	    "Alt<Key>w: d32-policy-mode(wildcard)");
	choice = XmCreateRadioBox(box, "policyMode", NULL, 0);
	XtVaSetValues(choice, XmNorientation, XmHORIZONTAL, XmNpacking, XmPACK_TIGHT,
	    XmNtopAttachment, XmATTACH_FORM, XmNleftAttachment, XmATTACH_FORM,
	    XmNnavigationType, XmTAB_GROUP, NULL);
	for (int i = 0; i < MODE_COUNT; ++i) {
		page->selector[i] = XtVaCreateManagedWidget(names[i], xmToggleButtonWidgetClass,
		    choice, XmNmnemonic, mnemonic[i], XmNnavigationType, XmTAB_GROUP, NULL);
		set_title(page->selector[i], labels[i]);
		XtAddCallback(page->selector[i], XmNvalueChangedCallback, select_mode, page);
		page->panel[i] = XtVaCreateWidget(i == WILDCARD ? "wildcardPanel" : "prefixListPanel",
		    xmFormWidgetClass, box, XmNtopAttachment, XmATTACH_WIDGET, XmNtopWidget, choice,
		    XmNbottomAttachment, XmATTACH_FORM, XmNbottomOffset, 5,
		    XmNleftAttachment, XmATTACH_FORM, XmNleftOffset, 5,
		    XmNrightAttachment, XmATTACH_FORM, XmNrightOffset, 5, NULL);
		page->title[i] = form_label(page->panel[i], "policyTitle", "", NULL);
	}
	XtManageChild(choice);
	XmToggleButtonSetState(page->selector[WILDCARD], True, False);
	wild = page->panel[WILDCARD];
	page->wild[0] = policy_field(page, wild, page->title[WILDCARD], "policyWildBase", "Base address");
	page->wild[1] = policy_field(page, wild, page->wild[0], "policyWildMask", "Wildcard mask");
	page->wild[2] = policy_field(page, wild, page->wild[1], "policyWildCandidate", "Candidate");
	button = policy_button(wild, page->wild[2], "policyWildMatch", "Match", XK_m, run_wild, page);
	page->output[WILDCARD] = policy_text(page, wild, button, "policyWildOutput", false, keys);
	XtAddCallback(page->wild[2], XmNactivateCallback, run_wild, page);
	list = page->panel[PREFIX_LIST];
	grammar = form_label(list, "policyGrammar", "seq N permit|deny P/len [ge X] [le Y]", page->title[PREFIX_LIST]);
	page->rules = policy_text(page, list, grammar, "policyRules", true, keys);
	XtVaSetValues(page->rules, XmNmaxLength, RULE_TEXT_LIMIT, NULL);
	XtAddCallback(page->rules, XmNvalueChangedCallback, input_changed, page);
	page->route = policy_field(page, list, XtParent(page->rules), "policyRoute", "Candidate prefix");
	button = policy_button(list, page->route, "policyEvaluate", "Evaluate", XK_v, run_list, page);
	page->output[PREFIX_LIST] = policy_text(page, list, button, "policyTrace", false, keys);
	XtAddCallback(page->route, XmNactivateCallback, run_list, page);
	for (size_t i = 0; i < XtNumber(page->wild); ++i)
		XtOverrideTranslations(page->wild[i], modes);
	for (int i = 0; i < MODE_COUNT; ++i)
		XtOverrideTranslations(page->output[i], modes);
	XtOverrideTranslations(page->rules, modes);
	XtOverrideTranslations(page->route, modes);
	XtManageChild(wild);
	update_family(page);
	return box;
}

static bool
field_populated(Widget field)
{
	char *text = XmTextFieldGetString(field);
	bool populated = text != NULL && *text != '\0';

	XtFree(text);
	return populated;
}

void
calc_policy_refresh(struct calc *calc)
{
	struct policy_page *page = calc->policy;

	if (page == NULL)
		return;
	update_family(page);
	if (page->mode == WILDCARD && field_populated(page->wild[0]) &&
	    field_populated(page->wild[1]) && field_populated(page->wild[2]))
		match_wildcard(page);
	else if (page->mode == PREFIX_LIST && field_populated(page->route))
		evaluate_list(page);
	else
		d32ui_status(calc->ui, page->status[page->mode]);
}
