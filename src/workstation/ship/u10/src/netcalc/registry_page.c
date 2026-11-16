#include "registry_data.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include <Xm/Form.h>
#include <Xm/Label.h>
#include <Xm/PushB.h>
#include <Xm/RowColumn.h>
#include <Xm/Text.h>
#include <Xm/TextF.h>

struct registry_pages {
	Widget reg_header, reg_query, reg_output;
	Widget mcast_header, group, mac, limit, other, mcast_output;
	Widget group_button, reverse_button, solicited_button, collision_button;
	Widget last_operation;
};

struct output {
	char text[65536];
	size_t length;
};

static void
append(struct output *out, const char *format, ...)
{
	va_list args;
	const size_t limit = sizeof(out->text) - 48;
	if (out->length >= limit)
		return;
	va_start(args, format);
	int written = vsnprintf(out->text + out->length, limit - out->length,
	    format, args);
	va_end(args);
	if (written < 0)
		return;
	if ((size_t)written >= limit - out->length) {
		static const char clipped[] = "\n[!] Result text limit reached.\n";
		memcpy(out->text + out->length, clipped, sizeof(clipped));
		out->length = limit;
	} else
		out->length += (size_t)written;
}

static void
set_label(Widget widget, const char *text)
{
	XmString value = XmStringCreateLocalized((char *)text);
	XtVaSetValues(widget, XmNlabelString, value, NULL);
	XmStringFree(value);
}

static Widget
label(Widget parent, const char *name, const char *text)
{
	Widget widget = XtVaCreateManagedWidget(name, xmLabelWidgetClass, parent,
	    XmNalignment, XmALIGNMENT_BEGINNING, XmNtraversalOn, False, NULL);
	set_label(widget, text);
	return widget;
}

static Widget
field(struct calc *calc, Widget parent, const char *name, const char *title,
    short columns)
{
	label(parent, "fieldLabel", title);
	Widget input = XtVaCreateManagedWidget(name, xmTextFieldWidgetClass, parent,
	    XmNcolumns, columns, XmNmaxLength, 260, XmNresizeWidth, False, NULL);
	if (d32ui_mono(calc->ui) != NULL)
		XtVaSetValues(input, XmNfontList, d32ui_mono(calc->ui), NULL);
	return input;
}

static Widget
button(Widget parent, const char *name, const char *title, KeySym mnemonic,
    XtCallbackProc callback, struct calc *calc)
{
	Widget widget = XtVaCreateManagedWidget(name, xmPushButtonWidgetClass,
	    parent, XmNmnemonic, mnemonic, NULL);
	set_label(widget, title);
	XtAddCallback(widget, XmNactivateCallback, callback, calc);
	return widget;
}

static Widget
top_box(Widget page)
{
	return XtVaCreateManagedWidget("controls", xmRowColumnWidgetClass, page,
	    XmNorientation, XmVERTICAL, XmNpacking, XmPACK_TIGHT,
	    XmNtopAttachment, XmATTACH_FORM, XmNleftAttachment, XmATTACH_FORM,
	    XmNrightAttachment, XmATTACH_FORM, NULL);
}

static Widget
row_box(Widget parent)
{
	return XtVaCreateManagedWidget("inputRow", xmRowColumnWidgetClass, parent,
	    XmNorientation, XmHORIZONTAL, XmNpacking, XmPACK_TIGHT, NULL);
}

static Widget
results(struct calc *calc, Widget page, Widget top, const char *name)
{
	Arg args[8];
	Cardinal n = 0;
	XtSetArg(args[n], XmNeditMode, XmMULTI_LINE_EDIT); ++n;
	XtSetArg(args[n], XmNeditable, False); ++n;
	XtSetArg(args[n], XmNcursorPositionVisible, False); ++n;
	XtSetArg(args[n], XmNrows, 6); ++n;
	XtSetArg(args[n], XmNcolumns, 36); ++n;
	XtSetArg(args[n], XmNwordWrap, False); ++n;
	Widget text = XmCreateScrolledText(page, (char *)name, args, n);
	XtVaSetValues(XtParent(text), XmNtopAttachment, XmATTACH_WIDGET,
	    XmNtopWidget, top, XmNbottomAttachment, XmATTACH_FORM,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM, NULL);
	if (d32ui_mono(calc->ui) != NULL)
		XtVaSetValues(text, XmNfontList, d32ui_mono(calc->ui), NULL);
	XtManageChild(text);
	return text;
}

static struct registry_pages *
pages(struct calc *calc)
{
	if (calc->registry == NULL)
		calc->registry = calloc(1, sizeof(*calc->registry));
	return calc->registry;
}

static const char *
snapshot(struct calc *calc, enum d32_regdata_dataset dataset, char text[160])
{
	struct calc_reg_view view;
	if (calc_registry_loading(calc))
		return "Snapshot --  gen --  [?] Loading\342\200\246";
	if (!calc_registry_view(calc, dataset, &view))
		return "Snapshot --  gen --  [x] IANA source unavailable";
	snprintf(text, 160, "Snapshot %s  gen %s  %s", view.date, view.generation,
	    view.previous ? "[stale] Verified previous generation" : "[OK]");
	return text;
}

static d32_addr
row_address(uint8_t family, const uint8_t *bytes)
{
	d32_addr addr = {.fam = family};
	size_t width = (size_t)d32_addr_width(family) / 8;
	memcpy(addr.b, bytes + 16 - width, width);
	return addr;
}

static void
display_span(struct output *out, const d32_range *range)
{
	char first[D32_ADDR_TEXT_CAP], last[D32_ADDR_TEXT_CAP];
	(void)d32_addr_format(&range->lo, first, sizeof(first));
	(void)d32_addr_format(&range->hi, last, sizeof(last));
	append(out, "%s", first);
	if (d32_addr_compare(&range->lo, &range->hi) != 0)
		append(out, " .. %s", last);
}

static void
display_iana(struct output *out, const d32_range *span,
    const struct d32_regdata_row *row)
{
	display_span(out, span);
	if (row == NULL) {
		append(out, " | IPv%u | -- | (no record \342\200\224 unknown) | -- | -- | --\n",
		    span->lo.fam);
		return;
	}
	append(out, " | IPv%u | %s | %s | %s | %s | %s\n  %s%s%s\n",
	    row->family, *row->registry ? row->registry : "IANA", row->status,
	    *row->date ? row->date : "--", *row->country ? row->country : "--",
	    *row->reference ? row->reference : "IANA special-purpose", row->name,
	    *row->detail ? "  " : "", row->detail);
}

static bool
includes_text(const char *haystack, const char *needle)
{
	size_t n = strlen(needle);
	if (n == 0)
		return true;
	for (const char *p = haystack; *p != '\0'; ++p)
		if (strncasecmp(p, needle, n) == 0)
			return true;
	return false;
}

static bool
registry_filter(const char *text)
{
	const char *const rir[] = {"afrinic", "apnic", "arin", "lacnic", "ripe", "ripe ncc", "nro"};
	for (size_t i = 0; i < sizeof(rir) / sizeof(rir[0]); ++i)
		if (strcasecmp(rir[i], text) == 0)
			return true;
	return strlen(text) == 2 && strspn(text,
	    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ") == 2;
}

static bool
looks_like_address(const char *text)
{
	return strpbrk(text, ":./%") != NULL || (*text >= '0' && *text <= '9');
}

static void
query_spans(struct output *out, const struct calc_reg_view *view,
    const d32_range *query)
{
	d32_addr cursor = query->lo;
	bool covered_to_end = false;
	for (size_t i = 0; i < view->count; ++i) {
		const struct d32_regdata_row *row = &view->rows[i].value;
		if (row->family != query->lo.fam || row->kind != D32_REGDATA_INTERVAL)
			continue;
		d32_range span = {row_address(row->family, row->first),
		    row_address(row->family, row->last)};
		if (d32_addr_compare(&span.hi, &query->lo) < 0 ||
		    d32_addr_compare(&span.lo, &query->hi) > 0)
			continue;
		if (d32_addr_compare(&span.lo, &query->lo) < 0)
			span.lo = query->lo;
		if (d32_addr_compare(&span.hi, &query->hi) > 0)
			span.hi = query->hi;
		if (!covered_to_end && d32_addr_compare(&cursor, &span.lo) < 0) {
			d32_range gap = {.lo = cursor};
			(void)d32_addr_prev(&span.lo, &gap.hi);
			display_iana(out, &gap, NULL);
		}
		display_iana(out, &span, row);
		if (d32_addr_compare(&span.hi, &query->hi) == 0)
			covered_to_end = true;
		else if (!covered_to_end && d32_addr_compare(&span.hi, &cursor) >= 0)
			(void)d32_addr_next(&span.hi, &cursor);
	}
	if (!covered_to_end) {
		d32_range gap = {cursor, query->hi};
		display_iana(out, &gap, NULL);
	}
}

static const char *
display_geo(struct output *out, const struct calc *calc, const d32_range *span,
    char country_status[48])
{
	struct calc_reg_view view;
	char country[3];
	const char *status = "[?] DB-IP country estimate unknown.";

	append(out, "\nDB-IP COUNTRY ESTIMATE\n");
	if (!calc_geo_snapshot(calc, &view)) {
		status = "[?] DB-IP country estimate unavailable.";
		append(out, "%s\n", status);
		return status;
	}
	switch (calc_geo_country(calc, span, country)) {
	case CALC_GEO_COUNTRY:
		(void)snprintf(country_status, 48, "[OK] DB-IP estimated country: %s.", country);
		status = country_status;
		break;
	case CALC_GEO_MIXED:
		status = "[?] DB-IP prefix has mixed or incomplete coverage.";
		break;
	case CALC_GEO_SPECIAL:
		status = "[?] Query includes special-purpose or non-global addresses.";
		break;
	case CALC_GEO_UNKNOWN:
		break;
	}
	append(out, "%s\nSnapshot %s  gen %s\n", status, view.date, view.generation);
	if (view.previous)
		append(out, "[stale] Verified previous generation\n");
	append(out, "IP Geolocation by DB-IP (https://db-ip.com)\n"
	    "CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)\n");
	return status;
}

static void
diagnose(struct calc *calc, d32_net_status code, const d32_net_error *error)
{
	char message[160];
	snprintf(message, sizeof(message), "[x] %s at column %zu.",
	    d32_net_status_name(code), error->column != 0 ? error->column : 1);
	d32ui_status(calc->ui, message);
}

static void
reg_search(Widget widget, XtPointer arg, XtPointer call)
{
	struct calc *calc = arg;
	struct registry_pages *p = calc->registry;
	struct calc_reg_view view = {0};
	struct output out = {0};
	char country_status[48];
	char *query = XmTextFieldGetString(p->reg_query);
	(void)widget;
	(void)call;
	if (query == NULL)
		return;
	if (registry_filter(query)) {
		XmTextSetString(p->reg_output,
		    "[x] NRO delegation source unavailable pending redistribution approval.\n"
		    "Registry and country-code searches require that source.");
		d32ui_status(calc->ui, "[x] NRO delegation source unavailable.");
		goto done;
	}
	if (calc_registry_loading(calc)) {
		d32ui_status(calc->ui, "[?] Loading registry data.");
		goto done;
	}
	bool iana_ready = calc_registry_view(calc, D32_REGDATA_IANA, &view);
	const char *status = iana_ready ? "[OK] IANA registry search complete."
	    : "[x] IANA special-purpose source unavailable.";
	append(&out, "IANA SPECIAL-PURPOSE REGISTRIES\n");
	if (iana_ready)
		append(&out, "Range | Family | Registry | Status | Date | Country | Source\n\n");
	else
		append(&out, "%s\n", status);
	if (looks_like_address(query)) {
		d32_prefix prefix;
		d32_net_error error;
		d32_range span;
		d32_net_status code = d32_prefix_parse(query, &prefix, NULL, &error);
		if (code != D32_NET_OK) {
			diagnose(calc, code, &error);
			goto done;
		}
		span = (d32_range){d32_prefix_network(&prefix), d32_prefix_last(&prefix)};
		if (iana_ready)
			query_spans(&out, &view, &span);
		status = display_geo(&out, calc, &span, country_status);
	} else if (iana_ready) {
		size_t found = 0;
		for (size_t i = 0; i < view.count; ++i) {
			const struct d32_regdata_row *row = &view.rows[i].value;
			if (row->kind != D32_REGDATA_INTERVAL ||
			    (!includes_text(row->name, query) && !includes_text(row->registry, query)))
				continue;
			d32_range span = {row_address(row->family, row->first),
			    row_address(row->family, row->last)};
			display_iana(&out, &span, row);
			++found;
		}
		if (found == 0)
			append(&out, "(no record \342\200\224 unknown)\n");
	}
	XmTextSetString(p->reg_output, out.text);
	d32ui_status(calc->ui, status);
done:
	XtFree(query);
}

Widget
calc_registries_page(struct calc *calc, Widget parent)
{
	struct registry_pages *p = pages(calc);
	if (p == NULL)
		return NULL;
	Widget page = XmCreateForm(parent, "registriesPage", NULL, 0);
	Widget top = top_box(page);
	p->reg_header = label(top, "registrySnapshot", "Snapshot --  gen --  [?] Loading\342\200\246");
	label(top, "nroUnavailable", "[x] NRO data unavailable pending redistribution approval");
	label(top, "registryCountry", "NRO country describes registration. DB-IP country is an estimate.");
	Widget row = row_box(top);
	p->reg_query = field(calc, row, "registryQuery", "Query", 27);
	button(row, "registrySearch", "Search", 'S', reg_search, calc);
	XtAddCallback(p->reg_query, XmNactivateCallback, reg_search, calc);
	p->reg_output = results(calc, page, top, "registryResults");
	XmTextSetString(p->reg_output, "[?] Loading registry data.");
	return page;
}

void
calc_registries_refresh(struct calc *calc)
{
	struct registry_pages *p = calc->registry;
	if (p == NULL || p->reg_header == NULL)
		return;
	char text[160];
	set_label(p->reg_header, snapshot(calc, D32_REGDATA_IANA, text));
	bool loading = calc_registry_loading(calc);
	XtSetSensitive(p->reg_query, !loading);
	char *query = XmTextFieldGetString(p->reg_query);
	if (!loading && query != NULL && *query != '\0')
		reg_search(NULL, calc, NULL);
	else if (!loading)
		XmTextSetString(p->reg_output, "Search an address, prefix or special-purpose name.\n"
		    "Address and prefix searches include the DB-IP country estimate.\n"
		    "Registry and country-code searches require the gated NRO source.");
	XtFree(query);
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

static size_t
parse_mac(const char *input, uint8_t mac[6])
{
	if (strlen(input) != 17)
		return strlen(input) < 17 ? strlen(input) + 1 : 18;
	char sep = input[2];
	if (sep != ':' && sep != '-')
		return 3;
	for (size_t i = 0; i < 6; ++i) {
		int hi = hex_digit(input[i * 3]), lo = hex_digit(input[i * 3 + 1]);
		if (hi < 0 || lo < 0)
			return i * 3 + (hi < 0 ? 1 : 2);
		if (i != 5 && input[i * 3 + 2] != sep)
			return i * 3 + 3;
		mac[i] = (uint8_t)(hi * 16 + lo);
	}
	return 0;
}

static void
display_mac(struct output *out, const uint8_t mac[6])
{
	append(out, "%02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2],
	    mac[3], mac[4], mac[5]);
}

static void
mcast_details(struct calc *calc, struct output *out, const d32_addr *group)
{
	struct calc_reg_view view;
	if (!calc_registry_view(calc, D32_REGDATA_MCAST, &view)) {
		append(out, "\n%s\n", calc_registry_loading(calc) ? "[?] Loading IANA multicast registry."
		    : "[x] IANA multicast source unavailable.");
		return;
	}
	const struct d32_regdata_row *scope = NULL;
	const uint8_t scope_id = group->b[1] & 15;
	if (group->fam == D32_IPV6) {
		for (size_t i = 0; i < view.count; ++i) {
			const struct d32_regdata_row *row = &view.rows[i].value;
			if (row->kind == D32_REGDATA_SCOPE && row->scope == scope_id) {
				scope = row;
				break;
			}
		}
		append(out, "\nIPv6 flags 0x%x | Scope 0x%x %s%s%s\n",
		    group->b[1] >> 4, scope_id, scope != NULL ? scope->name : "(unknown)",
		    scope != NULL && *scope->status ? " | " : "", scope != NULL ? scope->status : "");
	}
	append(out, "\nIANA MULTICAST REGISTRY\n");
	size_t found = 0;
	for (size_t i = 0; i < view.count; ++i) {
		const struct d32_regdata_row *row = &view.rows[i].value;
		if (row->family != group->fam || row->kind != D32_REGDATA_INTERVAL)
			continue;
		d32_range span = {row_address(row->family, row->first),
		    row_address(row->family, row->last)};
		if (d32_addr_compare(group, &span.lo) < 0 || d32_addr_compare(group, &span.hi) > 0)
			continue;
		display_span(out, &span);
		append(out, " | %s | %s\n  %s%s%s\n", row->name, row->status,
		    row->detail, *row->reference ? "  " : "", row->reference);
		++found;
	}
	if (found == 0)
		append(out, "(no record \342\200\224 unknown)\n");
}

static bool
read_addr(struct calc *calc, Widget field_widget, d32_addr *out)
{
	char *input = XmTextFieldGetString(field_widget);
	d32_net_error error;
	if (input == NULL)
		return false;
	d32_net_status status = d32_addr_parse(input, out, NULL, &error);
	XtFree(input);
	if (status != D32_NET_OK) {
		diagnose(calc, status, &error);
		return false;
	}
	return true;
}

static void
mcast_operation(Widget widget, XtPointer arg, XtPointer call)
{
	struct calc *calc = arg;
	struct registry_pages *p = calc->registry;
	struct output out = {0};
	d32_addr first, second;
	d32_net_status status;
	uint8_t mac[6];
	char addr[D32_ADDR_TEXT_CAP];
	(void)call;
	if (widget == p->mac || widget == p->reverse_button || widget == p->limit) {
		char *input = XmTextFieldGetString(p->mac);
		char *limit = XmTextFieldGetString(p->limit);
		d32_prefix narrow, *narrow_ptr = NULL;
		d32_net_error error = {.column = 1};
		d32_addr candidates[32];
		size_t count;
		if (input == NULL || limit == NULL) {
			XtFree(input);
			XtFree(limit);
			return;
		}
		size_t bad = parse_mac(input, mac);
		XtFree(input);
		if (bad != 0) {
			XtFree(limit);
			char message[100];
			snprintf(message, sizeof(message), "[x] Invalid Ethernet address at column %zu.", bad);
			d32ui_status(calc->ui, message);
			return;
		}
		if (*limit != '\0') {
			status = d32_prefix_parse(limit, &narrow, NULL, &error);
			if (status != D32_NET_OK || narrow.addr.fam != D32_IPV4) {
				XtFree(limit);
				diagnose(calc, status == D32_NET_OK ? D32_NET_FAMILY : status, &error);
				return;
			}
			narrow_ptr = &narrow;
		}
		XtFree(limit);
		status = d32_ipv4_groups_for_mac(mac, narrow_ptr, candidates, &count);
		if (status != D32_NET_OK)
			goto bad_value;
		append(&out, "%zu compatible IPv4 groups\n\n", count);
		for (size_t i = 0; i < count; ++i) {
			(void)d32_addr_format(&candidates[i], addr, sizeof(addr));
			append(&out, "%s\n", addr);
		}
	} else {
		if (!read_addr(calc, p->group, &first))
			return;
		if (widget == p->other || widget == p->collision_button) {
			bool collision;
			if (!read_addr(calc, p->other, &second))
				return;
			bool solicited = first.fam == D32_IPV6 && second.fam == D32_IPV6 &&
			    first.b[0] != 0xff && second.b[0] != 0xff;
			status = d32_multicast_collision(&first, &second, solicited, &collision);
			if (status != D32_NET_OK)
				goto bad_value;
			append(&out, "%s Ethernet collision: %s\n", solicited ? "Solicited-node" : "Multicast",
			    collision ? "yes" : "no");
		} else {
			if (widget == p->solicited_button) {
				status = d32_solicited_node(&first, &second, mac);
				if (status == D32_NET_OK)
					first = second;
			} else
				status = d32_multicast_mac(&first, mac);
			if (status != D32_NET_OK)
				goto bad_value;
			(void)d32_addr_format(&first, addr, sizeof(addr));
			append(&out, "%s %s\nEthernet ", widget == p->solicited_button ? "Solicited-node" : "Group", addr);
			display_mac(&out, mac);
			append(&out, "\n");
			mcast_details(calc, &out, &first);
		}
	}
	XmTextSetString(p->mcast_output, out.text);
	p->last_operation = widget;
	if (calc->active == CALC_MULTICAST)
		d32ui_status(calc->ui, "[OK] Multicast calculation complete.");
	return;
bad_value:
	d32ui_status(calc->ui, status == D32_NET_FAMILY ? "[x] Address family does not fit this operation."
	    : "[x] Address or MAC is outside the range for this operation.");
}

Widget
calc_multicast_page(struct calc *calc, Widget parent)
{
	struct registry_pages *p = pages(calc);
	if (p == NULL)
		return NULL;
	Widget page = XmCreateForm(parent, "multicastPage", NULL, 0);
	Widget top = top_box(page);
	p->mcast_header = label(top, "multicastSnapshot", "Snapshot --  gen --  [?] Loading\342\200\246");
	Widget row = row_box(top);
	p->group = field(calc, row, "multicastGroup", "Address", 22);
	p->group_button = button(row, "multicastMap", "Group to MAC", 'G', mcast_operation, calc);
	p->solicited_button = button(row, "multicastSolicited", "Solicited", 'S', mcast_operation, calc);
	row = row_box(top);
	p->other = field(calc, row, "multicastOther", "Second address", 22);
	p->collision_button = button(row, "multicastCollision", "Check collision", 'C', mcast_operation, calc);
	row = row_box(top);
	p->mac = field(calc, row, "multicastMac", "MAC", 20);
	p->reverse_button = button(row, "multicastReverse", "IPv4 groups", 'I', mcast_operation, calc);
	row = row_box(top);
	p->limit = field(calc, row, "multicastNarrow", "Optional IPv4 prefix", 22);
	XtAddCallback(p->group, XmNactivateCallback, mcast_operation, calc);
	XtAddCallback(p->other, XmNactivateCallback, mcast_operation, calc);
	XtAddCallback(p->mac, XmNactivateCallback, mcast_operation, calc);
	XtAddCallback(p->limit, XmNactivateCallback, mcast_operation, calc);
	p->mcast_output = results(calc, page, top, "multicastResults");
	XmTextSetString(p->mcast_output, "Enter a group, unicast IPv6 address or Ethernet MAC.");
	return page;
}

void
calc_multicast_refresh(struct calc *calc)
{
	struct registry_pages *p = calc->registry;
	if (p == NULL || p->mcast_header == NULL)
		return;
	char text[160];
	set_label(p->mcast_header, snapshot(calc, D32_REGDATA_MCAST, text));
	if (p->last_operation != NULL)
		mcast_operation(p->last_operation, calc, NULL);
}
