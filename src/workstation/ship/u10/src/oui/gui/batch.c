#include "batch.h"

#include "d32_io.h"
#include "result.h"

#include <sys/wait.h>
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <X11/StringDefs.h>
#include <X11/keysym.h>
#include <Xm/Form.h>
#include <Xm/Label.h>
#include <Xm/List.h>
#include <Xm/Protocols.h>
#include <Xm/PushB.h>
#include <Xm/RowColumn.h>
#include <Xm/SelectioB.h>
#include <Xm/Text.h>
#include <Xm/TextF.h>

#define MAX_LINES 10000u
#define MAX_PASTE (2u * 1024u * 1024u)
#define MAX_CSV (32u * 1024u * 1024u)
#define PAGE_ROWS 100u
#define LOOKUP_SLICE 128u
#define EXPORT_CANCELLED 100
#define RESULT_WIDTH 510

struct batch_row {
	struct oui_mac mac;
	struct oui_row match;
	int result;
	char input[128];
};

struct batch {
	d32ui *ui;
	const struct oui_db *db;
	void (*closed)(void *);
	void *closed_data;
	int export_fd;
	Widget opener, dialog, input, list, status, lookup, previous, next, save;
	Widget export_prompt, progress;
	d32ui_job *export_job;
	char export_name[81];
	bool close_after_export, closing;
	XtWorkProcId work;
	char *text, *cursor;
	struct batch_row *rows;
	size_t total, complete, found, ambiguous, missed, invalid, page;
};

struct csv_buffer {
	char *data;
	size_t used, capacity;
};

static volatile sig_atomic_t export_cancelled;

static void watch_keys(Widget, XtEventHandler, struct batch *);
static void close_batch(Widget, XtPointer, XtPointer);

static void
set_label(Widget widget, const char *text)
{
	XmString label = XmStringCreateLocalized((char *)text);

	XtVaSetValues(widget, XmNlabelString, label, NULL);
	XmStringFree(label);
}

static void
status(struct batch *batch, const char *message)
{
	set_label(batch->status, message);
	d32ui_status(batch->ui, message);
}

static const char *
result_name(int result)
{
	switch (result) {
	case OUI_OK:
		return "found";
	case OUI_MISS:
		return "not_found";
	default:
		return "parse_error";
	}
}

static const char *
flag(bool value)
{
	return value ? "true" : "false";
}

static size_t
display_length(const char *text, size_t limit)
{
	size_t len = strnlen(text, limit + 1);

	if (len <= limit)
		return len;
	len = limit;
	while (((unsigned char)text[len] & 0xc0) == 0x80)
		len--;
	return len;
}

static void
render_page(struct batch *batch)
{
	XmString items[PAGE_ROWS];
	size_t first = batch->page * PAGE_ROWS, i, j, used, length, n = 0;
	const struct batch_row *row;
	const char *name;
	char line[512], mac[18], prefix[10];

	for (i = first; i < batch->complete && n < PAGE_ROWS; i++) {
		row = &batch->rows[i];
		if (row->result == OUI_INVALID) {
			(void)snprintf(line, sizeof(line), "%5zu  [x] Invalid MAC address",
			    i + 1);
		} else {
			oui_format_mac(mac, &row->mac);
			if (row->result == OUI_OK) {
				oui_format_prefix(prefix, &row->match);
				if (row->match.assignee_count > 1) {
					used = (size_t)snprintf(line, sizeof(line),
					    "%5zu  [!] %-17s  %s/%u %s %s  Ambiguous: ",
					    i + 1, mac, prefix, row->match.width,
					    oui_type_name(row->match.type), row->match.date);
					for (j = 0; j < row->match.assignee_count; j++) {
						name = row->match.assignees[j];
						length = display_length(name, 100);
						used += (size_t)snprintf(line + used, sizeof(line) - used,
						    "%s%.*s%s", j == 0 ? "" : " | ", (int)length,
						    name, name[length] == '\0' ? "" : "...");
					}
				} else {
					(void)snprintf(line, sizeof(line),
					    "%5zu  [OK] %-17s  %s/%u %s %s  %.*s",
					    i + 1, mac, prefix, row->match.width,
					    oui_type_name(row->match.type), row->match.date,
					    (int)display_length(row->match.assignee, 120),
					    row->match.assignee);
				}
			} else {
				(void)snprintf(line, sizeof(line),
				    "%5zu  [?]  %-17s  No match  Broadcast=%s Group=%s "
				    "Local=%s%s%s", i + 1, mac, flag(row->mac.broadcast),
				    flag(row->mac.group), flag(row->mac.local),
				    row->mac.slap != NULL ? " SLAP=" : "",
				    row->mac.slap != NULL ? row->mac.slap : "");
			}
		}
		items[n++] = XmStringCreateLocalized(line);
	}
	XmListDeleteAllItems(batch->list);
	if (n != 0)
		XmListAddItems(batch->list, items, (int)n, 0);
	for (i = 0; i < n; i++)
		XmStringFree(items[i]);
	XtSetSensitive(batch->previous, first != 0);
	XtSetSensitive(batch->next, first + n < batch->complete);
}

static void
page(Widget widget, XtPointer closure, XtPointer call)
{
	struct batch *batch = closure;

	(void)call;
	if (widget == batch->previous)
		batch->page--;
	else
		batch->page++;
	render_page(batch);
}

static void
input_edited(Widget widget, XtPointer closure, XtPointer call)
{
	struct batch *batch = closure;

	(void)widget;
	(void)call;
	if (batch->complete != 0 && batch->work == 0) {
		XtSetSensitive(batch->save, False);
		status(batch, "[?] Batch input changed. Run lookup again.");
	}
}

static Boolean
lookup_slice(XtPointer closure)
{
	struct batch *batch = closure;
	struct batch_row *row;
	char *end, message[160], ambiguity[40] = "";
	size_t i, len;

	for (i = 0; i < LOOKUP_SLICE && batch->complete < batch->total; i++) {
		row = &batch->rows[batch->complete++];
		end = strchr(batch->cursor, '\n');
		len = end == NULL ? strlen(batch->cursor) :
		    (size_t)(end - batch->cursor);
		/* T11's line reader reserves one byte for NUL before CR stripping. */
		row->result = OUI_INVALID;
		if (len < sizeof(row->input)) {
			if (len != 0 && batch->cursor[len - 1] == '\r')
				len--;
			memcpy(row->input, batch->cursor, len);
			row->input[len] = '\0';
			row->result = oui_parse_mac(row->input, &row->mac);
			if (row->result == OUI_OK)
				row->result = oui_lookup(batch->db, &row->mac, &row->match);
		}
		if (row->result == OUI_OK) {
			batch->found++;
			batch->ambiguous += row->match.assignee_count > 1;
		} else if (row->result == OUI_MISS)
			batch->missed++;
		else
			batch->invalid++;
		if (end != NULL)
			batch->cursor = end + 1;
	}
	if (batch->complete < batch->total)
		return False;
	batch->work = 0;
	XtFree(batch->text);
	batch->text = batch->cursor = NULL;
	XtVaSetValues(batch->input, XmNeditable, True, NULL);
	XtSetSensitive(batch->lookup, True);
	XtSetSensitive(batch->save, batch->export_fd >= 0);
	render_page(batch);
	if (batch->ambiguous != 0)
		(void)snprintf(ambiguity, sizeof(ambiguity), " (%zu ambiguous)", batch->ambiguous);
	(void)snprintf(message, sizeof(message),
	    "[OK] Batch complete: %zu lines, %zu found%s, %zu not found, %zu invalid.",
	    batch->total, batch->found, ambiguity, batch->missed, batch->invalid);
	status(batch, message);
	return True;
}

static void
lookup(Widget widget, XtPointer closure, XtPointer call)
{
	struct batch *batch = closure;
	struct batch_row *rows;
	char *text, *p;
	size_t total = 1;

	(void)widget;
	(void)call;
	if (batch->work != 0 || batch->export_job != NULL)
		return;
	text = XmTextGetString(batch->input);
	if (text == NULL) {
		status(batch, "[x] Could not read the batch input.");
		return;
	}
	if (strlen(text) > MAX_PASTE) {
		XtFree(text);
		status(batch, "[x] Batch input exceeds 2 MiB.");
		return;
	}
	for (p = text; *p != '\0'; p++) {
		if (*p == '\n' && p[1] != '\0' && ++total > MAX_LINES) {
			XtFree(text);
			status(batch, "[x] Batch input exceeds 10,000 lines.");
			return;
		}
	}
	rows = calloc(total, sizeof(*rows));
	if (rows == NULL) {
		XtFree(text);
		status(batch, "[x] Could not allocate the batch results.");
		return;
	}
	free(batch->rows);
	batch->rows = rows;
	batch->text = batch->cursor = text;
	batch->total = total;
	batch->complete = batch->found = batch->missed = batch->invalid = 0;
	batch->ambiguous = 0;
	batch->page = 0;
	render_page(batch);
	XtSetSensitive(batch->save, False);
	XtSetSensitive(batch->lookup, False);
	XtVaSetValues(batch->input, XmNeditable, False, NULL);
	status(batch, "[?] Looking up batch addresses.");
	batch->work = XtAppAddWorkProc(d32ui_context(batch->ui), lookup_slice,
	    batch);
}

static bool
csv_reserve(struct csv_buffer *buffer, size_t count)
{
	size_t capacity;
	char *data;

	if (count > MAX_CSV - buffer->used)
		return false;
	if (buffer->capacity - buffer->used >= count)
		return true;
	capacity = buffer->capacity != 0 ? buffer->capacity : 4096;
	while (capacity - buffer->used < count)
		capacity *= 2;
	data = realloc(buffer->data, capacity);
	if (data == NULL)
		return false;
	buffer->data = data;
	buffer->capacity = capacity;
	return true;
}

static bool
csv_field(struct csv_buffer *buffer, const char *field, char separator)
{
	const char *p;
	size_t len = strlen(field);

	if (len > MAX_CSV)
		return false;
	for (p = field; *p != '\0'; p++)
		if (*p == '"')
			len++;
	if (!csv_reserve(buffer, len + 3))
		return false;
	buffer->data[buffer->used++] = '"';
	for (p = field; *p != '\0'; p++) {
		if (*p == '"')
			buffer->data[buffer->used++] = '"';
		buffer->data[buffer->used++] = *p;
	}
	buffer->data[buffer->used++] = '"';
	buffer->data[buffer->used++] = separator;
	return true;
}

static bool
csv_row(struct csv_buffer *buffer, size_t index, const struct batch_row *row)
{
	const char *fields[14];
	char number[32], input[sizeof(row->input)], mac[18] = "";
	char prefix[10] = "", width[16] = "";
	char *names = NULL;
	size_t i;
	unsigned char c;
	bool valid = row->result != OUI_INVALID, found = row->result == OUI_OK;
	bool ambiguous = found && row->match.assignee_count > 1;

	(void)snprintf(number, sizeof(number), "%zu", index + 1);
	/* Malformed pasted input can include terminal controls or invalid bytes. */
	for (i = 0; row->input[i] != '\0'; i++) {
		c = (unsigned char)row->input[i];
		input[i] = c >= 0x20 && c <= 0x7e ? (char)c : '?';
	}
	input[i] = '\0';
	if (valid)
		oui_format_mac(mac, &row->mac);
	if (found) {
		oui_format_prefix(prefix, &row->match);
		(void)snprintf(width, sizeof(width), "%u", row->match.width);
	}
	if (ambiguous) {
		names = oui_join_assignees(&row->match, "\n");
		if (names == NULL)
			return false;
	}
	fields[0] = number;
	fields[1] = input;
	fields[2] = ambiguous ? "ambiguous" : result_name(row->result);
	fields[3] = mac;
	fields[4] = valid ? flag(row->mac.broadcast) : "";
	fields[5] = valid ? flag(row->mac.group) : "";
	fields[6] = valid ? flag(row->mac.local) : "";
	fields[7] = valid && row->mac.slap != NULL ? row->mac.slap : "";
	fields[8] = prefix;
	fields[9] = width;
	fields[10] = found ? oui_type_name(row->match.type) : "";
	fields[11] = ambiguous ? names : found ? row->match.assignee : "";
	fields[12] = found ? row->match.date : "";
	fields[13] = found ? oui_notice : "";
	for (i = 0; i < 14; i++)
		if (!csv_field(buffer, fields[i], i == 13 ? '\n' : ','))
			break;
	free(names);
	return i == 14;
}

static void
mark_cancelled(int sig)
{
	(void)sig;
	export_cancelled = 1;
}

static int
export_csv(const struct batch *batch)
{
	static const char header[] =
	    "line,input,status,mac,broadcast,group,local,slap,prefix,width,type,"
	    "assignee,date,notice\n";
	struct csv_buffer buffer = { 0 };
	sigset_t term, pending;
	int result = D32E_LIMIT;
	size_t i;

	if (!csv_reserve(&buffer, sizeof(header) - 1))
		return result;
	memcpy(buffer.data, header, sizeof(header) - 1);
	buffer.used = sizeof(header) - 1;
	for (i = 0; i < batch->complete; i++) {
		if (export_cancelled) {
			result = EXPORT_CANCELLED;
			goto done;
		}
		if (!csv_row(&buffer, i, &batch->rows[i]))
			goto done;
	}
	/* After this boundary, finish atomic publication and cleanup even when
	 * the parent cancels. The child exits without unblocking SIGTERM. */
	sigemptyset(&term);
	sigaddset(&term, SIGTERM);
	if (sigprocmask(SIG_BLOCK, &term, NULL) == -1 ||
	    sigpending(&pending) == -1)
		result = D32E_IO;
	else if (export_cancelled || sigismember(&pending, SIGTERM) == 1)
		result = EXPORT_CANCELLED;
	else
		result = d32_atomic_write(batch->export_fd, batch->export_name,
		    buffer.data, buffer.used, 0600, true);
done:
	free(buffer.data);
	return result;
}

static bool
csv_name(const char *name)
{
	size_t i, len = strlen(name);
	unsigned char c;

	if (len < 5 || len > 80 || strcmp(name + len - 4, ".csv") != 0 ||
	    strstr(name, "..") != NULL)
		return false;
	for (i = 0; i < len; i++) {
		c = (unsigned char)name[i];
		if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
		    (c >= '0' && c <= '9'))
			continue;
		if (i == 0 || (c != '_' && c != '-' && c != '.'))
			return false;
	}
	return true;
}

static void
hide_export(struct batch *batch)
{
	XtUnmanageChild(batch->export_prompt);
	(void)XmProcessTraversal(batch->save, XmTRAVERSE_CURRENT);
}

static void
cancel_worker(void *closure)
{
	struct batch *batch = closure;

	if (batch->export_job != NULL) {
		d32ui_job_cancel(batch->export_job);
		status(batch, "[?] Canceling CSV export.");
	}
}

static void
cancel_export(Widget widget, XtPointer closure, XtPointer call)
{
	struct batch *batch = closure;

	(void)widget;
	(void)call;
	if (batch->export_job != NULL) {
		cancel_worker(batch);
		return;
	}
	hide_export(batch);
	status(batch, "[?] CSV export canceled.");
}

static void
export_done(int wait_status, void *closure)
{
	struct batch *batch = closure;
	d32ui *ui = batch->ui;
	const char *summary;
	char message[128];
	int code, result = D32E_IO;

	batch->export_job = NULL;
	if (batch->dialog == NULL) {
		free(batch);
		return;
	}
	XtSetSensitive(batch->export_prompt, True);
	d32ui_progress_close(batch->progress);
	batch->progress = NULL;
	if (wait_status >= 0 && WIFEXITED(wait_status)) {
		code = WEXITSTATUS(wait_status);
		if (code <= D32E_CREATE || code == EXPORT_CANCELLED)
			result = code;
	} else if (wait_status >= 0 && WIFSIGNALED(wait_status) &&
	    WTERMSIG(wait_status) == SIGTERM)
		result = EXPORT_CANCELLED;
	if (result == D32E_OK) {
		hide_export(batch);
		(void)snprintf(message, sizeof(message), "[OK] Exported %s.",
		    batch->export_name);
		summary = message;
	} else if (result == EXPORT_CANCELLED) {
		hide_export(batch);
		summary = "[?] CSV export canceled.";
	} else {
		summary = result == D32E_EXISTS ?
		    "[x] CSV file already exists." : "[x] CSV export failed.";
		if (!batch->close_after_export)
			(void)d32ui_error(batch->ui, batch->export_prompt,
			    (enum d32_error)result, batch->export_name);
	}
	if (batch->close_after_export) {
		close_batch(NULL, batch, NULL);
		/* The normal close callback restores the footer. Publish the actual
		 * export outcome after it, even if a late cancel still committed. */
		d32ui_status(ui, summary);
	} else
		status(batch, summary);
}

static void
export_child(const struct batch *batch, int start_fd, int x_fd)
{
	struct sigaction action = { 0 };
	sigset_t term;
	char start;
	ssize_t count;

	(void)close(x_fd);
	action.sa_handler = mark_cancelled;
	action.sa_flags = SA_RESTART;
	sigemptyset(&action.sa_mask);
	sigemptyset(&term);
	sigaddset(&term, SIGTERM);
	if (sigaction(SIGTERM, &action, NULL) == -1 ||
	    sigprocmask(SIG_UNBLOCK, &term, NULL) == -1)
		_exit(D32E_IO);
	do {
		count = read(start_fd, &start, sizeof(start));
	} while (count == -1 && errno == EINTR && !export_cancelled);
	(void)close(start_fd);
	if (count != 1 || export_cancelled)
		_exit(EXPORT_CANCELLED);
	(void)umask(077);
#ifdef __OpenBSD__
	if (pledge("stdio rpath wpath cpath", NULL) == -1)
		_exit(D32E_IO);
#endif
	_exit(export_csv(batch));
}

static bool
start_export(struct batch *batch, const char *name)
{
	int watched[2], start[2], x_fd = ConnectionNumber(XtDisplay(batch->dialog));
	ssize_t sent;
	pid_t child;

	memcpy(batch->export_name, name, strlen(name) + 1);
	batch->progress = d32ui_progress(batch->ui, batch->export_prompt,
	    "Exporting batch CSV...", cancel_worker, batch);
	if (batch->progress == NULL)
		return false;
	if (pipe(watched) == -1)
		goto fail;
	if (pipe(start) == -1) {
		(void)close(watched[0]);
		(void)close(watched[1]);
		goto fail;
	}
	child = fork();
	if (child == 0) {
		(void)close(watched[0]);
		(void)close(start[1]);
		export_child(batch, start[0], x_fd);
		_exit(D32E_IO);
	}
	(void)close(watched[1]);
	if (child > 0)
		batch->export_job = d32ui_job_watch(batch->ui, child, watched[0],
		    NULL, export_done, batch);
	if (batch->export_job == NULL) {
		/* EOF at the gate prevents publication before the watcher exists. */
		(void)close(start[1]);
		(void)close(start[0]);
		(void)close(watched[0]);
		if (child > 0)
			while (waitpid(child, NULL, 0) == -1 && errno == EINTR)
				;
		goto fail;
	}
	XtSetSensitive(batch->export_prompt, False);
	status(batch, "[?] Exporting batch CSV.");
	/* Retain our read end through this one byte, so a child that exited
	 * early cannot signal SIGPIPE in the GUI. */
	do {
		sent = write(start[1], "1", 1);
	} while (sent == -1 && errno == EINTR);
	(void)close(start[1]);
	(void)close(start[0]);
	if (sent != 1)
		d32ui_job_cancel(batch->export_job);
	return true;
fail:
	d32ui_progress_close(batch->progress);
	batch->progress = NULL;
	return false;
}

static void
save_export(Widget widget, XtPointer closure, XtPointer call)
{
	struct batch *batch = closure;
	char *name;

	(void)widget;
	(void)call;
	if (batch->export_job != NULL)
		return;
	name = XmTextFieldGetString(XmSelectionBoxGetChild(batch->export_prompt,
	    XmDIALOG_TEXT));
	if (name == NULL)
		return;
	if (!csv_name(name)) {
		status(batch, "[x] Enter a simple filename ending in .csv.");
		(void)d32ui_error(batch->ui, batch->export_prompt, D32E_INVAL, NULL);
	} else if (!start_export(batch, name)) {
		status(batch, "[x] CSV export failed.");
		(void)d32ui_error(batch->ui, batch->export_prompt, D32E_IO, name);
	}
	XtFree(name);
}

static void
export_keys(Widget widget, XtPointer closure, XEvent *event, Boolean *dispatch)
{
	KeySym key;

	if (event->type != KeyPress)
		return;
	key = XLookupKeysym(&event->xkey, 0);
	if (key != XK_Escape && key != XK_Return && key != XK_KP_Enter)
		return;
	*dispatch = False;
	if (key == XK_Escape)
		cancel_export(widget, closure, NULL);
	else
		save_export(widget, closure, NULL);
}

static void
wm_close_callback(Widget shell, XtCallbackProc callback, XtPointer closure)
{
	Atom protocol = XmInternAtom(XtDisplay(shell), "WM_DELETE_WINDOW", False);

	XtVaSetValues(shell, XmNdeleteResponse, XmDO_NOTHING, NULL);
	XmAddWMProtocols(shell, &protocol, 1);
	XmAddWMProtocolCallback(shell, protocol, callback, closure);
}

static void
open_export(Widget widget, XtPointer closure, XtPointer call)
{
	struct batch *batch = closure;
	Widget text, shell;
	XmString value, title, label;

	(void)widget;
	(void)call;
	if (batch->export_job != NULL || !XtIsSensitive(batch->save))
		return;
	if (batch->export_prompt == NULL) {
		batch->export_prompt = XmCreatePromptDialog(batch->dialog,
		    "batchSave", NULL, 0);
		shell = XtParent(batch->export_prompt);
		title = XmStringCreateLocalized("Export Batch CSV");
		label = XmStringCreateLocalized("CSV filename in ~/.dt/d32");
		XtVaSetValues(batch->export_prompt, XmNdialogTitle, title,
		    XmNselectionLabelString, label, XmNautoUnmanage, False,
		    XmNdialogStyle, XmDIALOG_PRIMARY_APPLICATION_MODAL, NULL);
		XmStringFree(title);
		XmStringFree(label);
		XtUnmanageChild(XmSelectionBoxGetChild(batch->export_prompt,
		    XmDIALOG_HELP_BUTTON));
		XtAddCallback(batch->export_prompt, XmNokCallback, save_export, batch);
		XtAddCallback(batch->export_prompt, XmNcancelCallback, cancel_export,
		    batch);
		wm_close_callback(shell, cancel_export, batch);
		d32ui_tag_dialog(batch->ui, batch->export_prompt);
		watch_keys(batch->export_prompt, export_keys, batch);
	}
	value = XmStringCreateLocalized("oui-batch.csv");
	XtVaSetValues(batch->export_prompt, XmNtextString, value, NULL);
	XmStringFree(value);
	XtManageChild(batch->export_prompt);
	text = XmSelectionBoxGetChild(batch->export_prompt, XmDIALOG_TEXT);
	(void)XmProcessTraversal(text, XmTRAVERSE_CURRENT);
	XmTextFieldSetSelection(text, 0, (XmTextPosition)strlen("oui-batch.csv"),
	    XtLastTimestampProcessed(XtDisplay(text)));
	status(batch, "[?] Export Batch CSV.");
}

static void
close_batch(Widget widget, XtPointer closure, XtPointer call)
{
	struct batch *batch = closure;

	(void)widget;
	(void)call;
	if (batch->closing)
		return;
	if (batch->export_job != NULL) {
		batch->close_after_export = true;
		cancel_worker(batch);
		return;
	}
	batch->closing = true;
	XtUnmanageChild(batch->dialog);
	if (batch->opener != NULL && XtIsManaged(batch->opener) &&
	    XtIsSensitive(batch->opener))
		(void)XmProcessTraversal(batch->opener, XmTRAVERSE_CURRENT);
	if (batch->closed != NULL)
		batch->closed(batch->closed_data);
	XtDestroyWidget(XtParent(batch->dialog));
}

static void
batch_keys(Widget widget, XtPointer closure, XEvent *event, Boolean *dispatch)
{
	struct batch *batch = closure;
	KeySym key;
	bool control;

	if (event->type != KeyPress)
		return;
	key = XLookupKeysym(&event->xkey, 0);
	control = (event->xkey.state & ControlMask) != 0;
	if (key == XK_Escape) {
		*dispatch = False;
		close_batch(widget, closure, NULL);
	} else if (control && key == XK_s) {
		*dispatch = False;
		open_export(widget, closure, NULL);
	} else if ((key == XK_Return || key == XK_KP_Enter) &&
	    (control || widget != batch->input)) {
		*dispatch = False;
		if (!control && XtIsSubclass(widget, xmPushButtonWidgetClass)) {
			if (XtIsSensitive(widget))
				XtCallCallbacks(widget, XmNactivateCallback, NULL);
		} else {
			lookup(widget, closure, NULL);
		}
	}
}

static void
watch_keys(Widget widget, XtEventHandler handler, struct batch *batch)
{
	WidgetList children;
	Cardinal count, i;

	if (!XtIsWidget(widget))
		return;
	XtInsertEventHandler(widget, KeyPressMask, False, handler, batch,
	    XtListHead);
	if (XtIsComposite(widget)) {
		XtVaGetValues(widget, XtNchildren, &children, XtNnumChildren, &count,
		    NULL);
		for (i = 0; i < count; i++)
			watch_keys(children[i], handler, batch);
	}
}

static void
forget_opener(Widget widget, XtPointer closure, XtPointer call)
{
	struct batch *batch = closure;

	(void)widget;
	(void)call;
	batch->opener = NULL;
}

static void
destroy_batch(Widget widget, XtPointer closure, XtPointer call)
{
	struct batch *batch = closure;

	(void)widget;
	(void)call;
	if (batch->opener != NULL)
		XtRemoveCallback(batch->opener, XtNdestroyCallback, forget_opener,
		    batch);
	if (batch->work != 0)
		XtRemoveWorkProc(batch->work);
	if (batch->text != NULL)
		XtFree(batch->text);
	free(batch->rows);
	if (batch->export_job != NULL) {
		/* The watcher still owns a reference until its done callback. */
		batch->dialog = NULL;
		d32ui_job_cancel(batch->export_job);
		return;
	}
	free(batch);
}

static Widget
button(Widget parent, const char *name, const char *label, XtCallbackProc cb,
    struct batch *batch)
{
	Widget w = XtVaCreateManagedWidget(name, xmPushButtonWidgetClass,
	    parent, XmNnavigationType, XmTAB_GROUP, NULL);

	set_label(w, label);
	XtAddCallback(w, XmNactivateCallback, cb, batch);
	return w;
}

bool
oui_batch_show(d32ui *ui, Widget opener, const struct oui_db *db,
    int export_dirfd, XmFontList display_fonts,
    void (*closed)(void *), void *closed_data)
{
	struct batch *batch;
	Widget shell, body, intro, controls, notice;
	XmFontList mono;
	XmString title;
	Arg args[4];
	Cardinal n = 0;

	if (db == NULL) {
		d32ui_status(ui, "[x] No vendor registry installed");
		return false;
	}
	batch = calloc(1, sizeof(*batch));
	if (batch == NULL) {
		d32ui_status(ui, "[x] Could not open the batch dialog.");
		return false;
	}
	batch->ui = ui;
	batch->db = db;
	batch->opener = opener;
	batch->export_fd = export_dirfd;
	batch->closed = closed;
	batch->closed_data = closed_data;
	batch->dialog = XmCreateFormDialog(d32ui_shell(ui), "batchDialog", NULL, 0);
	if (batch->dialog == NULL) {
		free(batch);
		d32ui_status(ui, "[x] Could not open the batch dialog.");
		return false;
	}
	shell = XtParent(batch->dialog);
	title = XmStringCreateLocalized("Batch OUI Lookup");
	XtVaSetValues(batch->dialog, XmNdialogTitle, title, XmNautoUnmanage,
	    False, XmNdialogStyle, XmDIALOG_PRIMARY_APPLICATION_MODAL, NULL);
	XmStringFree(title);
	XtAddCallback(shell, XtNdestroyCallback, destroy_batch, batch);
	if (opener != NULL)
		XtAddCallback(opener, XtNdestroyCallback, forget_opener, batch);
	wm_close_callback(shell, close_batch, batch);
	body = XtVaCreateManagedWidget("batchBody", xmRowColumnWidgetClass,
	    batch->dialog, XmNorientation, XmVERTICAL, XmNpacking, XmPACK_TIGHT,
	    XmNmarginWidth, 12, XmNmarginHeight, 8,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM,
	    XmNtopAttachment, XmATTACH_FORM, XmNbottomAttachment, XmATTACH_FORM,
	    NULL);
	intro = XtVaCreateManagedWidget("batchIntro", xmLabelWidgetClass, body,
	    XmNalignment, XmALIGNMENT_BEGINNING, NULL);
	set_label(intro, "Paste one MAC per line (up to 10,000). "
	    "Ctrl+Return runs lookup.");
	batch->input = XmCreateScrolledText(body, "batchInput", NULL, 0);
	XtVaSetValues(batch->input, XmNeditMode, XmMULTI_LINE_EDIT, XmNrows, 5,
	    XmNcolumns, 56, XmNmaxLength, (int)MAX_PASTE, XmNwordWrap, False,
	    NULL);
	XtAddCallback(batch->input, XmNvalueChangedCallback, input_edited, batch);
	XtManageChild(batch->input);
	controls = XtVaCreateManagedWidget("batchControls", xmRowColumnWidgetClass,
	    body, XmNorientation, XmHORIZONTAL, XmNpacking, XmPACK_TIGHT, NULL);
	batch->lookup = button(controls, "batchLookup", "Look Up", lookup, batch);
	batch->previous = button(controls, "batchPrevious", "Previous", page, batch);
	batch->next = button(controls, "batchNext", "Next", page, batch);
	batch->save = button(controls, "batchExport", "Export CSV...", open_export,
	    batch);
	(void)button(controls, "batchClose", "Close", close_batch, batch);
	XtSetArg(args[n], XmNvisibleItemCount, 8); n++;
	XtSetArg(args[n], XmNlistSizePolicy, XmCONSTANT); n++;
	XtSetArg(args[n], XmNscrollBarDisplayPolicy, XmAS_NEEDED); n++;
	XtSetArg(args[n], XmNwidth, RESULT_WIDTH); n++;
	batch->list = XmCreateScrolledList(body, "batchResults", args, n);
	XtVaSetValues(XtParent(batch->list), XmNwidth, RESULT_WIDTH, NULL);
	mono = d32ui_mono(ui);
	if (mono != NULL)
		XtVaSetValues(batch->input, XmNfontList, mono, NULL);
	if (display_fonts != NULL || mono != NULL)
		XtVaSetValues(batch->list, XmNfontList, display_fonts != NULL ? display_fonts : mono, NULL);
	XtManageChild(batch->list);
	notice = XtVaCreateManagedWidget("batchNotice", xmLabelWidgetClass, body,
	    XmNalignment, XmALIGNMENT_BEGINNING, XmNrecomputeSize, False,
	    XmNwidth, RESULT_WIDTH, NULL);
	if (display_fonts != NULL)
		XtVaSetValues(notice, XmNfontList, display_fonts, NULL);
	set_label(notice, oui_notice);
	batch->status = XtVaCreateManagedWidget("batchStatus", xmLabelWidgetClass,
	    body, XmNalignment, XmALIGNMENT_BEGINNING, XmNrecomputeSize, False,
	    XmNwidth, RESULT_WIDTH, NULL);
	set_label(batch->status, "Paste addresses to look them up.");
	XtSetSensitive(batch->previous, False);
	XtSetSensitive(batch->next, False);
	XtSetSensitive(batch->save, False);
	XtVaSetValues(batch->dialog, XmNinitialFocus, batch->input, NULL);
	d32ui_tag_dialog(ui, batch->dialog);
	watch_keys(batch->dialog, batch_keys, batch);
	XtManageChild(batch->dialog);
	(void)XmProcessTraversal(batch->input, XmTRAVERSE_CURRENT);
	status(batch, "[?] Batch open. Paste addresses.");
	return true;
}
