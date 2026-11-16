#include "calc.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <Xm/Form.h>
#include <Xm/Label.h>
#include <Xm/Protocols.h>
#include <Xm/PushB.h>
#include <Xm/RowColumn.h>
#include <Xm/Text.h>
#include <Xm/TextF.h>

#define VIEW_ROWS 24
#define SUMMARY_ROWS 128
#define SUMMARY_BYTES 16384

struct text {
	char bytes[4096];
	size_t len;
};

struct parsed {
	d32_prefix prefix;
	d32_range bounds;
	char zone[D32_ZONE_CAP], end_zone[D32_ZONE_CAP];
	bool range;
};

struct address_page {
	struct calc *calc;
	Widget input, other, mac, output, normalize, previous, next;
	bool splitting;
	uint8_t family;
	d32_prefix split_parent;
	d32_addr split_first;
	uint8_t split_length;
};

struct summary_dialog {
	struct address_page *page;
	Widget dialog, opener, input, output, apply, previous, next;
	d32_range spans[SUMMARY_ROWS];
	d32_prefix *cover;
	size_t nspans, ncover, offset;
	d32_prefix supernet;
	d32_count excess;
	bool applied;
};

static void
append(struct text *out, const char *format, ...)
{
	size_t room = sizeof(out->bytes) - out->len;
	va_list args;
	va_start(args, format);
	int used = vsnprintf(out->bytes + out->len, room, format, args);
	va_end(args);
	if (used > 0)
		out->len += (size_t)used < room ? (size_t)used : room - 1;
}

static void
addr_text(const d32_addr *addr, char out[D32_ADDR_TEXT_CAP])
{
	if (d32_addr_format(addr, out, D32_ADDR_TEXT_CAP) != D32_NET_OK)
		(void)snprintf(out, D32_ADDR_TEXT_CAP, "unavailable");
}

static void
count_text(d32_count count, char out[D32_COUNT_TEXT_CAP])
{
	if (d32_count_format(&count, true, out, D32_COUNT_TEXT_CAP) != D32_NET_OK)
		(void)snprintf(out, D32_COUNT_TEXT_CAP, "unavailable");
}

static void
prefix_text(struct text *out, const char *label, const d32_prefix *prefix)
{
	char address[D32_ADDR_TEXT_CAP];
	addr_text(&prefix->addr, address);
	append(out, "%s%s/%u\n", label, address, (unsigned)prefix->len);
}

static void
span_text(struct text *out, const d32_range *span)
{
	char lo[D32_ADDR_TEXT_CAP], hi[D32_ADDR_TEXT_CAP];
	addr_text(&span->lo, lo);
	addr_text(&span->hi, hi);
	append(out, "%s .. %s\n", lo, hi);
}

static void
report(struct address_page *page, const char *field, d32_net_status status,
    const d32_net_error *error)
{
	char message[256];
	if (error != NULL && error->bit >= 0) {
		(void)snprintf(message, sizeof(message), "[x] %s, column %zu: %s (bit %d).",
		    field, error->column, d32_net_status_name(status), error->bit);
	} else if (error != NULL && error->column != 0) {
		(void)snprintf(message, sizeof(message), "[x] %s, column %zu: %s.",
		    field, error->column, d32_net_status_name(status));
	} else {
		(void)snprintf(message, sizeof(message), "[x] %s: %s.", field,
		    d32_net_status_name(status));
	}
	d32ui_status(page->calc->ui, message);
}

static bool
range_separator(const char *input)
{
	const char *percent = strchr(input, '%');
	const char *dash = strchr(input, '-');
	if (strchr(input, '/') != NULL)
		return false;
	return strstr(input, "..") != NULL || strstr(input, " -") != NULL ||
	    strstr(input, "- ") != NULL || (dash != NULL && (percent == NULL || dash < percent));
}

static d32_net_status
parse(const char *input, uint8_t family, struct parsed *out, d32_net_error *error)
{
	struct parsed value = {0};
	d32_net_status status = d32_prefix_parse(input, &value.prefix, value.zone, error);
	if (status == D32_NET_OK) {
		value.bounds.lo = d32_prefix_network(&value.prefix);
		value.bounds.hi = d32_prefix_last(&value.prefix);
	} else if (range_separator(input)) {
		status = d32_range_parse(input, &value.bounds, value.zone, value.end_zone, error);
		value.range = true;
	}
	if (status != D32_NET_OK)
		return status;
	if (value.bounds.lo.fam != family) {
		*error = (d32_net_error){.code = D32_NET_FAMILY, .line = 1, .column = 1, .bit = -1};
		return D32_NET_FAMILY;
	}
	*out = value;
	return D32_NET_OK;
}

static bool
read_entry(struct address_page *page, Widget widget, const char *field,
    struct parsed *out)
{
	d32_net_error error;
	char *input = XmTextFieldGetString(widget);
	d32_net_status status = parse(input, page->calc->family, out, &error);
	XtFree(input);
	if (status == D32_NET_OK)
		return true;
	report(page, field, status, &error);
	return false;
}

static void
show_output(Widget widget, struct text *out)
{
	XmTextSetString(widget, out->bytes);
	XmTextShowPosition(widget, 0);
}

static void
clear_split(struct address_page *page)
{
	page->splitting = false;
	XtSetSensitive(page->previous, False);
	XtSetSensitive(page->next, False);
}

static void
host_bits(struct address_page *page, const d32_prefix *prefix)
{
	char network[D32_ADDR_TEXT_CAP], message[160];
	d32_addr base = d32_prefix_network(prefix);
	addr_text(&base, network);
	(void)snprintf(message, sizeof(message),
	    "[!] Host bits set \xe2\x80\x94 Normalize to %s/%u?", network, (unsigned)prefix->len);
	d32ui_status(page->calc->ui, message);
}

static void
binary_text(struct text *out, const d32_prefix *prefix)
{
	int width = d32_addr_width(prefix->addr.fam);
	for (int bit = 0; bit < width; ++bit) {
		if (bit % 32 == 0)
			append(out, "Binary %d        ", bit / 32 + 1);
		else if (bit % 8 == 0)
			append(out, " ");
		if (bit == prefix->len)
			append(out, "|");
		append(out, "%c", prefix->addr.b[bit / 8] & (0x80U >> (bit % 8)) ? '1' : '0');
		if (bit + 1 == width && prefix->len == width)
			append(out, "|");
		if ((bit + 1) % 32 == 0)
			append(out, "\n");
	}
}

static void
inspect_prefix(struct address_page *page, const struct parsed *value, struct text *out)
{
	const d32_prefix *p = &value->prefix;
	d32_prefix network = {value->bounds.lo, p->len};
	d32_mask mask, wildcard;
	d32_range usable;
	bool present;
	char addr[D32_ADDR_TEXT_CAP], last[D32_ADDR_TEXT_CAP];
	char mask_string[D32_ADDR_TEXT_CAP], wild_string[D32_ADDR_TEXT_CAP];
	char size[D32_COUNT_TEXT_CAP];
	d32_addr mask_addr = {.fam = p->addr.fam}, wild_addr = {.fam = p->addr.fam};
	d32_host_policy policy = p->addr.fam == D32_IPV4 ? page->calc->host_policy : D32_HOST_RAW;

	prefix_text(out, "Canonical       ", p);
	if (value->zone[0] != '\0')
		append(out, "Zone            %s (display only)\n", value->zone);
	prefix_text(out, "Network         ", &network);
	(void)d32_prefix_masks(p, &mask, &wildcard);
	memcpy(mask_addr.b, mask.b, sizeof(mask.b));
	memcpy(wild_addr.b, wildcard.b, sizeof(wildcard.b));
	addr_text(&mask_addr, mask_string);
	addr_text(&wild_addr, wild_string);
	append(out, "Mask            %s\nWildcard        %s\n", mask_string, wild_string);
	addr_text(&value->bounds.hi, last);
	append(out, "%-16s%s\n", p->addr.fam == D32_IPV4 ? "Broadcast/last" : "Last address", last);
	if (d32_prefix_usable(p, policy, &usable, &present) != D32_NET_OK) {
		append(out, "Usable          Host policy does not apply to this prefix\n");
	} else if (!present) {
		append(out, "Usable          None under the selected host policy\n");
	} else {
		addr_text(&usable.lo, addr);
		addr_text(&usable.hi, last);
		append(out, "First usable    %s\nLast usable     %s\n", addr, last);
	}
	count_text(d32_range_count(&value->bounds), size);
	append(out, "Address count   %s\n", size);
	binary_text(out, p);
	if (p->addr.fam == D32_IPV6) {
		uint8_t mac[6];
		const uint8_t *iid = p->addr.b + 8;
		append(out, "Interface ID    %02x%02x:%02x%02x:%02x%02x:%02x%02x\n",
		    iid[0], iid[1], iid[2], iid[3], iid[4], iid[5], iid[6], iid[7]);
		if (d32_eui64_mac(iid, mac) == D32_NET_OK) {
			append(out, "EUI-64 candidate MAC  %02x:%02x:%02x:%02x:%02x:%02x\n",
			    mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
		}
	}
}

static void
inspect_range(const struct parsed *value, struct text *out)
{
	d32_prefix covering;
	d32_count excess;
	char size[D32_COUNT_TEXT_CAP];
	append(out, "Inclusive range\n");
	span_text(out, &value->bounds);
	if (value->zone[0] != '\0' || value->end_zone[0] != '\0')
		append(out, "Endpoint zones  %s / %s\n", value->zone[0] ? value->zone : "none",
		    value->end_zone[0] ? value->end_zone : "none");
	count_text(d32_range_count(&value->bounds), size);
	append(out, "Address count   %s\n", size);
	if (d32_range_supernet(&value->bounds, &covering, &excess) == D32_NET_OK) {
		prefix_text(out, "Covering prefix ", &covering);
		count_text(excess, size);
		append(out, "Covering prefix adds %s addresses\n", size);
	}
}

static void
inspect(Widget widget, XtPointer data, XtPointer call)
{
	struct address_page *page = data;
	struct parsed value;
	struct text out = {0};
	(void)widget;
	(void)call;
	clear_split(page);
	XtSetSensitive(page->normalize, False);
	if (!read_entry(page, page->input, "Address", &value))
		return;
	if (value.range) {
		inspect_range(&value, &out);
		d32ui_status(page->calc->ui, "[OK] Inclusive range");
	} else {
		inspect_prefix(page, &value, &out);
		if (d32_prefix_has_host_bits(&value.prefix)) {
			XtSetSensitive(page->normalize, True);
			host_bits(page, &value.prefix);
		} else {
			d32ui_status(page->calc->ui, "[OK] Address calculated");
		}
	}
	show_output(page->output, &out);
}

static void
changed(Widget widget, XtPointer data, XtPointer call)
{
	struct address_page *page = data;
	(void)widget;
	(void)call;
	clear_split(page);
	XtSetSensitive(page->normalize, False);
	d32ui_status(page->calc->ui, "[?] Input changed. Press Inspect to calculate.");
}

static void
normalize(Widget widget, XtPointer data, XtPointer call)
{
	struct address_page *page = data;
	struct parsed value;
	char addr[D32_ADDR_TEXT_CAP], input[D32_ADDR_TEXT_CAP + D32_ZONE_CAP + 6];
	(void)widget;
	(void)call;
	if (!read_entry(page, page->input, "Address", &value) || value.range)
		return;
	addr_text(&value.bounds.lo, addr);
	(void)snprintf(input, sizeof(input), "%s%s%s/%u", addr,
	    value.zone[0] ? "%" : "", value.zone, (unsigned)value.prefix.len);
	XmTextFieldSetString(page->input, input);
	inspect(NULL, page, NULL);
	d32ui_status(page->calc->ui, "[OK] Address normalized");
}

static void
compare(Widget widget, XtPointer data, XtPointer call)
{
	struct address_page *page = data;
	struct parsed first, second;
	struct text out = {0};
	(void)widget;
	(void)call;
	if (!read_entry(page, page->input, "Address", &first) ||
	    !read_entry(page, page->other, "Second value", &second))
		return;
	const d32_addr *a = first.range ? &first.bounds.lo : &first.prefix.addr;
	const d32_addr *b = second.range ? &second.bounds.lo : &second.prefix.addr;
	int order = d32_addr_compare(a, b);
	bool contains = d32_addr_compare(&first.bounds.lo, &second.bounds.lo) <= 0 &&
	    d32_addr_compare(&first.bounds.hi, &second.bounds.hi) >= 0;
	append(&out, "First bounds    ");
	span_text(&out, &first.bounds);
	append(&out, "Second bounds   ");
	span_text(&out, &second.bounds);
	append(&out, "First contains second: %s\nAddress comparison: first %s second\n",
	    contains ? "yes" : "no", order < 0 ? "<" : order > 0 ? ">" : "=");
	clear_split(page);
	show_output(page->output, &out);
	d32ui_status(page->calc->ui, contains ? "[OK] Contains: yes" : "[OK] Contains: no");
}

static bool
read_prefix(struct address_page *page, Widget widget, const char *field, d32_prefix *out)
{
	struct parsed value;
	if (!read_entry(page, widget, field, &value))
		return false;
	if (value.range) {
		report(page, field, D32_NET_LENGTH, NULL);
		return false;
	}
	if (d32_prefix_has_host_bits(&value.prefix)) {
		if (widget == page->input) {
			XtSetSensitive(page->normalize, True);
			host_bits(page, &value.prefix);
		} else {
			d32ui_status(page->calc->ui, "[!] Enter the normalized second prefix.");
		}
		return false;
	}
	*out = value.prefix;
	return true;
}

static void
join(Widget widget, XtPointer data, XtPointer call)
{
	struct address_page *page = data;
	d32_prefix first, second, result;
	struct text out = {0};
	(void)widget;
	(void)call;
	if (!read_prefix(page, page->input, "First prefix", &first) ||
	    !read_prefix(page, page->other, "Second prefix", &second))
		return;
	if (d32_prefix_join(&first, &second, &result) != D32_NET_OK) {
		d32ui_status(page->calc->ui, "[x] Join requires two distinct sibling prefixes.");
		return;
	}
	clear_split(page);
	prefix_text(&out, "Joined prefix   ", &result);
	show_output(page->output, &out);
	d32ui_status(page->calc->ui, "[OK] Joined sibling prefixes");
}

static void
show_split(struct address_page *page)
{
	struct text out = {0};
	d32_addr parent_last = d32_prefix_last(&page->split_parent);
	d32_addr first = page->split_first;
	d32_addr end = first;
	char size[D32_COUNT_TEXT_CAP];
	count_text(d32_count_pow2(page->split_length - page->split_parent.len), size);
	append(&out, "%s child prefixes of length /%u\n\n", size, (unsigned)page->split_length);
	for (unsigned i = 0; i < VIEW_ROWS; ++i) {
		d32_prefix child = {first, page->split_length};
		prefix_text(&out, "", &child);
		end = d32_prefix_last(&child);
		if (d32_addr_compare(&end, &parent_last) == 0 || !d32_addr_next(&end, &first))
			break;
	}
	XtSetSensitive(page->previous,
	    d32_addr_compare(&page->split_first, &page->split_parent.addr) != 0);
	XtSetSensitive(page->next, d32_addr_compare(&end, &parent_last) != 0);
	show_output(page->output, &out);
	d32ui_status(page->calc->ui, "[OK] Split into aligned child prefixes");
}

static void
split(Widget widget, XtPointer data, XtPointer call)
{
	struct address_page *page = data;
	d32_prefix prefix;
	(void)widget;
	(void)call;
	if (!read_prefix(page, page->input, "Parent prefix", &prefix))
		return;
	char *input = XmTextFieldGetString(page->other);
	const char *digits = input + (input[0] == '/');
	unsigned length = 0;
	size_t bad = 0;
	for (const char *p = digits; *p != '\0'; ++p) {
		if (*p < '0' || *p > '9' || length * 10 + (unsigned)(*p - '0') > 128) {
			bad = (size_t)(p - input) + 1;
			break;
		}
		length = length * 10 + (unsigned)(*p - '0');
	}
	if (*digits == '\0' || length < prefix.len || length > (unsigned)d32_addr_width(prefix.addr.fam))
		bad = bad ? bad : (size_t)(digits - input) + 1;
	XtFree(input);
	if (bad != 0) {
		d32_net_error error = {.column = bad, .bit = -1};
		report(page, "Child length", D32_NET_LENGTH, &error);
		return;
	}
	page->splitting = true;
	page->split_parent = prefix;
	page->split_first = prefix.addr;
	page->split_length = (uint8_t)length;
	show_split(page);
}

static void
move_split(Widget widget, XtPointer data, XtPointer call)
{
	struct address_page *page = data;
	(void)call;
	if (!page->splitting)
		return;
	bool forward = widget == page->next;
	d32_addr limit = forward ? d32_prefix_last(&page->split_parent) : page->split_parent.addr;
	for (unsigned i = 0; i < VIEW_ROWS; ++i) {
		d32_prefix child = {page->split_first, page->split_length};
		d32_addr boundary = forward ? d32_prefix_last(&child) : child.addr;
		if (d32_addr_compare(&boundary, &limit) == 0)
			break;
		if (forward) {
			(void)d32_addr_next(&boundary, &page->split_first);
		} else {
			(void)d32_addr_prev(&boundary, &child.addr);
			page->split_first = d32_prefix_network(&child);
		}
	}
	show_split(page);
}

static int
hex_digit(char digit)
{
	if (digit >= '0' && digit <= '9')
		return digit - '0';
	if (digit >= 'a' && digit <= 'f')
		return digit - 'a' + 10;
	if (digit >= 'A' && digit <= 'F')
		return digit - 'A' + 10;
	return -1;
}

static void
eui64(Widget widget, XtPointer data, XtPointer call)
{
	struct address_page *page = data;
	struct text out = {0};
	uint8_t mac[6], iid[8];
	(void)widget;
	(void)call;
	char *input = XmTextFieldGetString(page->mac);
	size_t size = strlen(input), column = 0;
	for (size_t i = 0; i < sizeof(mac); ++i) {
		size_t offset = 3 * i;
		for (size_t digit = 0; digit < 2; ++digit) {
			if (offset + digit >= size || hex_digit(input[offset + digit]) < 0) {
				column = offset + digit + 1;
				break;
			}
		}
		if (column != 0)
			break;
		mac[i] = (uint8_t)(16 * hex_digit(input[offset]) + hex_digit(input[offset + 1]));
		if (i + 1 < sizeof(mac) && (offset + 2 >= size || input[offset + 2] != ':')) {
			column = offset + 3;
			break;
		}
	}
	if (column == 0 && size > 17)
		column = 18;
	XtFree(input);
	if (column != 0) {
		char message[120];
		(void)snprintf(message, sizeof(message), "[x] MAC column %zu: use six colon-separated hex octets.", column);
		d32ui_status(page->calc->ui, message);
		return;
	}
	d32_eui64(mac, iid);
	append(&out, "Modified EUI-64 interface ID\n%02x%02x:%02x%02x:%02x%02x:%02x%02x\n",
	    iid[0], iid[1], iid[2], iid[3], iid[4], iid[5], iid[6], iid[7]);
	clear_split(page);
	show_output(page->output, &out);
	d32ui_status(page->calc->ui, "[OK] Modified EUI-64 calculated");
}

static Widget
row(Widget parent, const char *name)
{
	return XtVaCreateManagedWidget(name, xmRowColumnWidgetClass, parent,
	    XmNorientation, XmHORIZONTAL, XmNpacking, XmPACK_TIGHT,
	    XmNmarginWidth, 0, XmNmarginHeight, 1, XmNspacing, 4, NULL);
}

static void
label(Widget parent, const char *title)
{
	XmString text = XmStringCreateLocalized((char *)title);
	(void)XtVaCreateManagedWidget("label", xmLabelWidgetClass, parent,
	    XmNlabelString, text, XmNtraversalOn, False, NULL);
	XmStringFree(text);
}

static Widget
button(Widget parent, const char *name, const char *title, char key,
    XtCallbackProc callback, XtPointer data)
{
	XmString text = XmStringCreateLocalized((char *)title);
	Widget widget = XtVaCreateManagedWidget(name, xmPushButtonWidgetClass, parent,
	    XmNlabelString, text, XmNmnemonic, (KeySym)key,
	    XmNnavigationType, XmTAB_GROUP, NULL);
	XmStringFree(text);
	XtAddCallback(widget, XmNactivateCallback, callback, data);
	return widget;
}

static Widget
entry(struct address_page *page, Widget parent, const char *name, short columns,
    int limit)
{
	Widget field = XtVaCreateManagedWidget(name, xmTextFieldWidgetClass, parent,
	    XmNcolumns, columns, XmNmaxLength, limit, XmNnavigationType, XmTAB_GROUP, NULL);
	if (d32ui_mono(page->calc->ui) != NULL)
		XtVaSetValues(field, XmNfontList, d32ui_mono(page->calc->ui), NULL);
	return field;
}

static Widget
editor(struct address_page *page, Widget parent, const char *name, short rows,
    bool editable, int limit)
{
	Widget field = XmCreateScrolledText(parent, (char *)name, NULL, 0);
	XtVaSetValues(field, XmNrows, rows, XmNcolumns, 38,
	    XmNeditMode, XmMULTI_LINE_EDIT, XmNeditable, editable ? True : False,
	    XmNwordWrap, False, XmNmaxLength, limit, XmNnavigationType, XmTAB_GROUP, NULL);
	if (d32ui_mono(page->calc->ui) != NULL)
		XtVaSetValues(field, XmNfontList, d32ui_mono(page->calc->ui), NULL);
	XtManageChild(field);
	return field;
}

static void
summary_view(struct summary_dialog *state)
{
	struct text out = {0};
	size_t count = state->applied ? state->ncover : state->nspans;
	size_t stop = state->offset + VIEW_ROWS;
	if (stop > count)
		stop = count;
	if (state->applied) {
		char excess[D32_COUNT_TEXT_CAP];
		count_text(state->excess, excess);
		prefix_text(&out, "Covering supernet  ", &state->supernet);
		append(&out, "Covering supernet adds %s addresses\n\n", excess);
	}
	append(&out, "%s rows %zu-%zu of %zu\n\n", state->applied ? "Exact CIDR cover" : "Staged",
	    state->offset + 1, stop, count);
	for (size_t i = state->offset; i < stop; ++i) {
		if (state->applied)
			prefix_text(&out, "", &state->cover[i]);
		else
			span_text(&out, &state->spans[i]);
	}
	XtSetSensitive(state->previous, state->offset != 0);
	XtSetSensitive(state->next, stop < count);
	show_output(state->output, &out);
}

static void
summary_clear(struct summary_dialog *state)
{
	free(state->cover);
	state->cover = NULL;
	state->nspans = state->ncover = state->offset = 0;
	state->applied = false;
	XtSetSensitive(state->apply, False);
	XtSetSensitive(state->previous, False);
	XtSetSensitive(state->next, False);
}

static void
summary_changed(Widget widget, XtPointer data, XtPointer call)
{
	struct summary_dialog *state = data;
	(void)widget;
	(void)call;
	summary_clear(state);
	XmTextSetString(state->output, "Input changed. Press Stage to preview.");
}

static void
summary_error(struct summary_dialog *state, const char *reason, size_t line,
    size_t column, int bit)
{
	struct text out = {0};
	if (line != 0)
		append(&out, "[x] Line %zu, column %zu: %s", line, column, reason);
	else
		append(&out, "[x] %s", reason);
	if (bit >= 0)
		append(&out, " (bit %d)", bit);
	show_output(state->output, &out);
	d32ui_status(state->page->calc->ui, out.bytes);
}

static void
summary_stage(Widget widget, XtPointer data, XtPointer call)
{
	struct summary_dialog *state = data;
	char *input = XmTextGetString(state->input), *line = input;
	size_t line_number = 0, count = 0;
	(void)widget;
	(void)call;
	summary_clear(state);
	if (strlen(input) > SUMMARY_BYTES) {
		summary_error(state, "Paste exceeds 16 KiB", 0, 0, -1);
		goto done;
	}
	while (line != NULL) {
		char *next = strchr(line, '\n');
		if (next != NULL)
			*next++ = '\0';
		size_t end = strlen(line);
		if (end != 0 && line[end - 1] == '\r')
			line[end - 1] = '\0';
		++line_number;
		if (line[strspn(line, " \t")] != '\0') {
			struct parsed item;
			d32_net_error error;
			if (count == SUMMARY_ROWS) {
				summary_error(state, "Paste exceeds 128 input rows", line_number, 1, -1);
				goto done;
			}
			d32_net_status status = parse(line, state->page->calc->family, &item, &error);
			if (status != D32_NET_OK) {
				summary_error(state, d32_net_status_name(status), line_number, error.column, error.bit);
				goto done;
			}
			state->spans[count++] = item.bounds;
		}
		line = next;
	}
	if (count == 0) {
		summary_error(state, "Paste at least one address, prefix or range", 0, 0, -1);
		goto done;
	}
	state->nspans = count;
	XtSetSensitive(state->apply, True);
	summary_view(state);
	d32ui_status(state->page->calc->ui, "[OK] Summary staged. Review the bounds before applying.");
done:
	XtFree(input);
}

static void
summary_apply(Widget widget, XtPointer data, XtPointer call)
{
	struct summary_dialog *state = data;
	(void)widget;
	(void)call;
	if (state->nspans == 0 || state->applied)
		return;
	size_t cap = state->nspans * D32_RANGE_COVER_MAX, count;
	d32_prefix *cover = calloc(cap, sizeof(*cover));
	if (cover == NULL) {
		summary_error(state, d32_net_status_name(D32_NET_NOMEM), 0, 0, -1);
		return;
	}
	d32_net_status status = d32_range_summary(state->spans, state->nspans, cover, cap, &count);
	if (status != D32_NET_OK) {
		free(cover);
		summary_error(state, d32_net_status_name(status), 0, 0, -1);
		return;
	}
	d32_count total = {0}, ignore;
	d32_range span = {cover[0].addr, d32_prefix_last(&cover[count - 1])};
	int width = d32_addr_width(span.lo.fam);
	for (size_t i = 0; i < count; ++i)
		(void)d32_count_add(total, d32_count_pow2((unsigned)width - cover[i].len), &total);
	(void)d32_range_supernet(&span, &state->supernet, &ignore);
	(void)d32_count_sub(d32_count_pow2((unsigned)width - state->supernet.len), total, &state->excess);
	state->cover = cover;
	state->ncover = count;
	state->offset = 0;
	state->applied = true;
	XtSetSensitive(state->apply, False);
	summary_view(state);
	d32ui_status(state->page->calc->ui, "[OK] Exact CIDR summary calculated");
}

static void
summary_move(Widget widget, XtPointer data, XtPointer call)
{
	struct summary_dialog *state = data;
	size_t count = state->applied ? state->ncover : state->nspans;
	(void)call;
	if (widget == state->next && state->offset + VIEW_ROWS < count)
		state->offset += VIEW_ROWS;
	else if (widget == state->previous && state->offset >= VIEW_ROWS)
		state->offset -= VIEW_ROWS;
	else
		return;
	summary_view(state);
}

static void
summary_close(Widget widget, XtPointer data, XtPointer call)
{
	struct summary_dialog *state = data;
	Widget opener = state->opener;
	Widget shell = XtParent(state->dialog);
	(void)widget;
	(void)call;
	XtUnmanageChild(state->dialog);
	if (XtIsManaged(opener) && XtIsRealized(opener) && XtIsSensitive(opener))
		(void)XmProcessTraversal(opener, XmTRAVERSE_CURRENT);
	XtDestroyWidget(shell);
}

static void
summary_destroy(Widget widget, XtPointer data, XtPointer call)
{
	struct summary_dialog *state = data;
	(void)widget;
	(void)call;
	free(state->cover);
	free(state);
}

static void
summarize(Widget widget, XtPointer data, XtPointer call)
{
	struct address_page *page = data;
	struct summary_dialog *state = calloc(1, sizeof(*state));
	(void)call;
	if (state == NULL) {
		report(page, "Summarize", D32_NET_NOMEM, NULL);
		return;
	}
	state->page = page;
	state->opener = widget;
	state->dialog = XmCreateFormDialog(d32ui_shell(page->calc->ui), "addressSummary", NULL, 0);
	XmString title = XmStringCreateLocalized("Summarize addresses");
	XtVaSetValues(state->dialog, XmNdialogTitle, title,
	    XmNdialogStyle, XmDIALOG_PRIMARY_APPLICATION_MODAL, NULL);
	XmStringFree(title);
	Widget shell = XtParent(state->dialog);
	XtVaSetValues(shell, XmNdeleteResponse, XmDO_NOTHING, NULL);
	Atom close = XmInternAtom(XtDisplay(shell), "WM_DELETE_WINDOW", False);
	XmAddWMProtocolCallback(shell, close, summary_close, state);
	XtAddCallback(state->dialog, XmNdestroyCallback, summary_destroy, state);
	Widget box = XtVaCreateManagedWidget("summaryFields", xmRowColumnWidgetClass,
	    state->dialog, XmNorientation, XmVERTICAL, XmNpacking, XmPACK_TIGHT,
	    XmNtopAttachment, XmATTACH_FORM, XmNbottomAttachment, XmATTACH_FORM,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
	label(box, "Paste one address, prefix or range per line.");
	state->input = editor(page, box, "summaryInput", 4, true, SUMMARY_BYTES);
	Widget buttons = row(box, "summaryActions");
	Widget stage = button(buttons, "summaryStage", "Stage", 'S', summary_stage, state);
	state->apply = button(buttons, "summaryApply", "Apply", 'A', summary_apply, state);
	state->previous = button(buttons, "summaryPrevious", "Previous", 'P', summary_move, state);
	state->next = button(buttons, "summaryNext", "Next", 'N', summary_move, state);
	Widget dismiss = button(buttons, "summaryClose", "Close", 'C', summary_close, state);
	state->output = editor(page, box, "summaryOutput", 7, false, sizeof(((struct text *)0)->bytes));
	XtVaSetValues(state->dialog, XmNdefaultButton, stage, XmNcancelButton, dismiss, NULL);
	XtAddCallback(state->input, XmNvalueChangedCallback, summary_changed, state);
	summary_clear(state);
	d32ui_tag_dialog(page->calc->ui, state->dialog);
	XtManageChild(state->dialog);
	(void)XmProcessTraversal(state->input, XmTRAVERSE_CURRENT);
}

static void
destroy_page(Widget widget, XtPointer data, XtPointer call)
{
	struct address_page *page = data;
	(void)widget;
	(void)call;
	page->calc->address = NULL;
	free(page);
}

Widget
calc_address_page(struct calc *calc, Widget notebook)
{
	struct address_page *page = calloc(1, sizeof(*page));
	if (page == NULL)
		return NULL;
	page->calc = calc;
	page->family = calc->family;
	calc->address = page;
	Widget form = XtVaCreateWidget("addressPage", xmFormWidgetClass, notebook,
	    XmNmarginWidth, 6, XmNmarginHeight, 4, NULL);
	XtAddCallback(form, XmNdestroyCallback, destroy_page, page);
	Widget top = XtVaCreateManagedWidget("addressControls", xmRowColumnWidgetClass, form,
	    XmNorientation, XmVERTICAL, XmNpacking, XmPACK_TIGHT, XmNspacing, 2,
	    XmNmarginWidth, 0, XmNmarginHeight, 0, XmNtopAttachment, XmATTACH_FORM,
	    XmNleftAttachment, XmATTACH_FORM, NULL);
	Widget fields = row(top, "addressEntry");
	label(fields, "Address input");
	page->input = entry(page, fields, "addressInput", 20, 256);
	(void)button(fields, "addressInspect", "Inspect", 'I', inspect, page);
	page->normalize = button(fields, "addressNormalize", "Normalize", 'N', normalize, page);
	XtSetSensitive(page->normalize, False);
	XtAddCallback(page->input, XmNactivateCallback, inspect, page);
	XtAddCallback(page->input, XmNvalueChangedCallback, changed, page);
	fields = row(top, "addressOtherRow");
	label(fields, "Second / length");
	page->other = entry(page, fields, "addressOther", 15, 256);
	(void)button(fields, "addressCompare", "Compare", 'C', compare, page);
	(void)button(fields, "addressJoin", "Join", 'J', join, page);
	(void)button(fields, "addressSplit", "Split", 'S', split, page);
	XtAddCallback(page->other, XmNactivateCallback, compare, page);
	fields = row(top, "addressTools");
	label(fields, "MAC");
	page->mac = entry(page, fields, "addressMac", 17, 32);
	(void)button(fields, "addressEui64", "EUI-64", 'E', eui64, page);
	(void)button(fields, "addressSummarize", "Summarize", 'U', summarize, page);
	page->previous = button(fields, "addressPrevious", "Previous", 'P', move_split, page);
	page->next = button(fields, "addressNext", "Next", 'X', move_split, page);
	XtAddCallback(page->mac, XmNactivateCallback, eui64, page);
	page->output = editor(page, form, "addressOutput", 5, false, sizeof(((struct text *)0)->bytes));
	XtVaSetValues(XtParent(page->output), XmNtopAttachment, XmATTACH_WIDGET,
	    XmNtopWidget, top, XmNbottomAttachment, XmATTACH_FORM,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
	clear_split(page);
	XmTextSetString(page->output, "Enter an address, prefix or inclusive range.\n"
	    "Examples: 192.0.2.129/24, 2001:db8::/48, 192.0.2.1 .. 192.0.2.14");
	return form;
}

void
calc_address_refresh(struct calc *calc)
{
	struct address_page *page = calc->address;
	if (page == NULL)
		return;
	if (page->family != calc->family) {
		page->family = calc->family;
		clear_split(page);
		XtSetSensitive(page->normalize, False);
		XmTextSetString(page->output, calc->family == D32_IPV4 ?
		    "Enter an IPv4 address, prefix or range." : "Enter an IPv6 address, prefix or range.");
		return;
	}
	char *input = XmTextFieldGetString(page->input);
	bool present = *input != '\0';
	XtFree(input);
	if (present)
		inspect(NULL, page, NULL);
	else
		d32ui_status(calc->ui, "[?] Enter an address and press Inspect.");
}
