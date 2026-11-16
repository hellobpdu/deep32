#include "d32ui.h"
#include "mix.h"
#include "support.h"

#include <X11/Shell.h>
#include <Xm/Label.h>
#include <Xm/PushB.h>
#include <Xm/RowColumn.h>
#include <Xm/Scale.h>
#include <Xm/ScrolledW.h>
#include <Xm/ToggleB.h>
#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <sysexits.h>
#include <unistd.h>

enum { MAX_FDS = 8, RECONNECT_MS = 2000, BELL_MS = 3000 };

struct app;
struct binding {
	struct app *app;
	enum mix_role group;
	unsigned slot;
	Widget widget, value;
};

struct watch {
	XtInputId id;
	int fd;
	short events;
};

struct app {
	d32ui *ui;
	struct sioctl_hdl *handle;
	struct mix_model model;
	struct d32_prefs *prefs;
	enum d32_error pref_state;
	const char *device_name;
	Widget device, device_row, menu, menu_shell, input, apps, test;
	struct binding volume, mute, input_level, input_mute, controls[MIX_CONTROLS];
	struct watch watches[MAX_FDS];
	XtIntervalId retry, writer, bell_timer;
	int dir, nwatch;
	Dimension initial_width, initial_height;
	bool layout_dirty, values_dirty, have_bell, closing;
	pid_t bell_pid;
	uint64_t bell_until;
};

static void connect_device(struct app *);
static void disconnect_device(struct app *);
static void update(struct app *);
static void schedule_write(struct app *);
static void input_ready(XtPointer, int *, XtInputId *);

static void
set_label(Widget w, const char *text)
{
	XmString label = XmStringCreateLocalized((char *)text);

	XtVaSetValues(w, XmNlabelString, label, NULL);
	XmStringFree(label);
}

static Widget
make_label(Widget parent, const char *name, const char *text)
{
	Widget w = XtVaCreateManagedWidget(name, xmLabelWidgetClass, parent,
	    XmNalignment, XmALIGNMENT_BEGINNING, NULL);

	set_label(w, text);
	return w;
}

static void
visible(Widget w, bool show)
{
	if (show && !XtIsManaged(w))
		XtManageChild(w);
	else if (!show && XtIsManaged(w))
		XtUnmanageChild(w);
}

static void
stop_bell(struct app *a)
{
	if (a->bell_timer) {
		XtRemoveTimeOut(a->bell_timer);
		a->bell_timer = 0;
	}
	mix_bell_stop(&a->bell_pid);
}

static void
show_scale(struct binding *b, struct mix_group g, bool enabled)
{
	char text[32];

	XmScaleSetValue(b->widget, (int)g.percent);
	XtSetSensitive(b->widget, enabled && g.confirmed);
	if (!g.confirmed)
		set_label(b->value, "Unavailable");
	else {
		snprintf(text, sizeof(text), "%u%%%s", g.percent, g.mixed ? " (mixed)" : "");
		set_label(b->value, text);
	}
}

static void
show_mute(struct binding *b, struct mix_group g, bool enabled)
{
	XmToggleButtonSetState(b->widget, g.confirmed && g.muted, False);
	XtSetSensitive(b->widget, enabled && g.confirmed);
	set_label(b->value, !g.confirmed ? "Unavailable" :
	    g.mixed ? "Mixed mute" : g.muted ? "Muted" : "Unmuted");
}

static void
render_test(struct app *a)
{
	bool test = a->have_bell && mix_preview_allowed(&a->model);

	if (!test && a->bell_pid > 0) {
		stop_bell(a);
		d32ui_status(a->ui, "Test sound stopped");
	}
	XtSetSensitive(a->test, test || a->bell_pid > 0);
	set_label(a->test, a->bell_pid > 0 ? "Stop test sound" : "Test sound");
}

static void
render_values(struct app *a)
{
	struct mix_model *m = &a->model;
	const char *name = a->device_name;
	Widget selected = NULL;
	char text[128];
	bool enabled = m->ready && !m->overflow;
	unsigned i;

	show_scale(&a->volume, mix_group(m, MIX_OUT_LEVEL), enabled);
	show_mute(&a->mute, mix_group(m, MIX_OUT_MUTE), enabled);
	show_scale(&a->input_level, mix_group(m, MIX_IN_LEVEL), enabled);
	show_mute(&a->input_mute, mix_group(m, MIX_IN_MUTE), enabled);
	for (i = 0; i < MIX_CONTROLS; i++) {
		struct binding *b = &a->controls[i];
		const struct mix_control *c = &m->controls[i];

		if (b->widget == NULL)
			continue;
		if (c->role == MIX_DEVICE) {
			XtSetSensitive(b->widget, enabled && c->confirmed);
			if (c->confirmed && c->value == 1) {
				name = c->label;
				selected = b->widget;
			}
		} else if (c->role == MIX_APP_LEVEL) {
			struct mix_group g = { .confirmed = c->confirmed };

			if (c->confirmed)
				g.percent = mix_percent(c->value, c->maximum);
			show_scale(b, g, enabled);
		}
	}
	if (selected != NULL && a->menu != NULL)
		XtVaSetValues(a->menu, XmNmenuHistory, selected, NULL);
	if (a->menu != NULL)
		XtSetSensitive(a->menu, enabled && selected != NULL);
	snprintf(text, sizeof(text), "Device: %s",
	    a->handle == NULL ? "No audio device" : m->ready ? name : "Connecting");
	set_label(a->device, text);
	render_test(a);
}

static void
scale_changed(Widget w, XtPointer data, XtPointer call)
{
	struct binding *b = data;
	const XmScaleCallbackStruct *cb = call;
	struct app *a = b->app;
	unsigned value = 0;

	if (b->group == MIX_NONE) {
		const struct mix_control *c = &a->model.controls[b->slot];

		if (c->role == MIX_APP_LEVEL && c->confirmed)
			value = mix_percent(c->value, c->maximum);
		if (cb->value >= 0 && cb->value <= 100)
			mix_queue_control(&a->model, b->slot, (unsigned)cb->value);
	} else {
		value = mix_group(&a->model, b->group).percent;
		if (cb->value >= 0 && cb->value <= 100)
			mix_queue_group(&a->model, b->group, (unsigned)cb->value);
	}
	XmScaleSetValue(w, (int)value);
	render_test(a);
	schedule_write(a);
}

static void
mute_changed(Widget w, XtPointer data, XtPointer call)
{
	struct binding *b = data;
	const XmToggleButtonCallbackStruct *cb = call;
	struct app *a = b->app;
	struct mix_group g = mix_group(&a->model, b->group);

	mix_queue_group(&a->model, b->group, g.mixed ? 1 : (unsigned)!!cb->set);
	XmToggleButtonSetState(w, g.confirmed && g.muted, False);
	render_test(a);
	schedule_write(a);
}

static void
select_device(Widget w, XtPointer data, XtPointer call)
{
	struct binding *b = data;
	struct app *a = b->app;

	(void)w;
	(void)call;
	mix_queue_control(&a->model, b->slot, 1);
	render_values(a);
	schedule_write(a);
}

static Widget
row(Widget parent, const char *name)
{
	return XtVaCreateManagedWidget(name, xmRowColumnWidgetClass, parent,
	    XmNorientation, XmHORIZONTAL, XmNpacking, XmPACK_TIGHT, NULL);
}

static void
make_scale(struct binding *b, struct app *a, Widget parent, enum mix_role group,
    unsigned slot, const char *name, const char *title)
{
	Widget container = row(parent, name);

	*b = (struct binding){ .app = a, .group = group, .slot = slot };
	make_label(container, "controlLabel", title);
	b->widget = XtVaCreateManagedWidget("level", xmScaleWidgetClass, container,
	    XmNorientation, XmHORIZONTAL, XmNminimum, 0, XmNmaximum, 100,
	    XmNscaleWidth, 215, XmNshowValue, False, NULL);
	b->value = make_label(container, "confirmed", "Unavailable");
	XtAddCallback(b->widget, XmNdragCallback, scale_changed, b);
	XtAddCallback(b->widget, XmNvalueChangedCallback, scale_changed, b);
}

static void
make_mute(struct binding *b, struct app *a, Widget parent, enum mix_role group)
{
	Widget container = row(parent, "muteRow");

	*b = (struct binding){ .app = a, .group = group };
	b->widget = XtVaCreateManagedWidget("mute", xmToggleButtonWidgetClass, container, NULL);
	set_label(b->widget, "Mute");
	b->value = make_label(container, "confirmed", "Unavailable");
	XtAddCallback(b->widget, XmNvalueChangedCallback, mute_changed, b);
}

static void
render_layout(struct app *a)
{
	struct mix_group in_level = mix_group(&a->model, MIX_IN_LEVEL);
	struct mix_group in_mute = mix_group(&a->model, MIX_IN_MUTE);
	Widget streams = NULL, scroll;
	unsigned i;
	Arg arg;

	if (a->menu != NULL) {
		XtDestroyWidget(a->menu);
		XtDestroyWidget(a->menu_shell);
		a->menu = a->menu_shell = NULL;
	}
	if (a->apps != NULL) {
		XtDestroyWidget(a->apps);
		a->apps = NULL;
	}
	memset(a->controls, 0, sizeof(a->controls));
	visible(a->input, a->model.ready && in_level.present && in_mute.present);
	for (i = 0; a->model.ready && i < MIX_CONTROLS; i++) {
		const struct mix_control *c = &a->model.controls[i];
		struct binding *b = &a->controls[i];
		Widget button;

		if (c->role == MIX_DEVICE) {
			if (a->menu_shell == NULL)
				a->menu_shell = XmCreatePulldownMenu(a->device_row, "deviceChoices", NULL, 0);
			button = XtVaCreateManagedWidget("deviceChoice", xmPushButtonWidgetClass,
			    a->menu_shell, NULL);
			set_label(button, c->label);
			*b = (struct binding){ .app = a, .slot = i, .widget = button };
			XtAddCallback(button, XmNactivateCallback, select_device, b);
		} else if (c->role == MIX_APP_LEVEL) {
			if (streams == NULL) {
				a->apps = XtVaCreateManagedWidget("applications", xmRowColumnWidgetClass,
				    XtParent(a->input), XmNorientation, XmVERTICAL, NULL);
				make_label(a->apps, "applicationsLabel", "Applications");
				scroll = XtVaCreateManagedWidget("streams", xmScrolledWindowWidgetClass,
				    a->apps, XmNscrollingPolicy, XmAUTOMATIC, XmNheight, 84,
				    XmNwidth, 480, NULL);
				streams = XtVaCreateManagedWidget("streamControls", xmRowColumnWidgetClass,
				    scroll, XmNorientation, XmVERTICAL, NULL);
			}
			make_scale(b, a, streams, MIX_NONE, i, "stream", c->label);
		}
	}
	if (a->menu_shell != NULL) {
		XtSetArg(arg, XmNsubMenuId, a->menu_shell);
		a->menu = XmCreateOptionMenu(a->device_row, "deviceMenu", &arg, 1);
		XtManageChild(a->menu);
	}
	d32ui_status(a->ui, a->handle == NULL ? "No audio device" :
	    !a->model.ready ? "Connecting to audio device" :
	    a->model.overflow ? "Audio controls exceed the supported limit" : "Audio controls connected");
}

static void
update(struct app *a)
{
	if (a->layout_dirty) {
		render_layout(a);
		a->layout_dirty = false;
		a->values_dirty = true;
	}
	if (a->values_dirty) {
		render_values(a);
		a->values_dirty = false;
	}
}

static void
description(void *data, struct sioctl_desc *desc, int value)
{
	struct app *a = data;

	mix_describe(&a->model, desc, value);
	a->layout_dirty = true;
}

static void
confirmed(void *data, unsigned address, unsigned value)
{
	struct app *a = data;

	mix_confirm(&a->model, address, value);
	a->values_dirty = true;
}

static void
clear_watches(struct app *a)
{
	int i;

	for (i = 0; i < a->nwatch; i++)
		XtRemoveInput(a->watches[i].id);
	a->nwatch = 0;
}

static int
pollfds(struct app *a, struct pollfd fds[MAX_FDS], int events)
{
	int n = sioctl_nfds(a->handle);

	if (n <= 0 || n > MAX_FDS)
		return -1;
	memset(fds, 0, sizeof(*fds) * (size_t)n);
	n = sioctl_pollfd(a->handle, fds, events);
	return n > 0 && n <= MAX_FDS ? n : -1;
}

static void
watch_fds(struct app *a, struct pollfd *fds, int count)
{
	int i;
	XtInputMask mask;

	if (count == a->nwatch) {
		for (i = 0; i < count; i++)
			if (a->watches[i].fd != fds[i].fd || a->watches[i].events != fds[i].events)
				break;
		if (i == count)
			return;
	}
	clear_watches(a);
	for (i = 0; i < count; i++) {
		mask = XtInputExceptMask;
		if (fds[i].events & POLLIN)
			mask |= XtInputReadMask;
		if (fds[i].events & POLLOUT)
			mask |= XtInputWriteMask;
		a->watches[i] = (struct watch){
		    .id = XtAppAddInput(d32ui_context(a->ui), fds[i].fd,
		        (XtPointer)(uintptr_t)mask, input_ready, a),
		    .fd = fds[i].fd, .events = fds[i].events
		};
	}
	a->nwatch = count;
}

static bool
service(struct app *a)
{
	struct pollfd fds[MAX_FDS];
	int n, events = 0, result;

	if (a->handle == NULL)
		return false;
	n = pollfds(a, fds, POLLIN);
	if (n == -1)
		goto down;
	result = poll(fds, (nfds_t)n, 0);
	if (result > 0)
		events = sioctl_revents(a->handle, fds);
	else if (result == -1 && errno != EINTR)
		goto down;
	if ((events & (POLLHUP | POLLERR | POLLNVAL)) || sioctl_eof(a->handle))
		goto down;
	n = pollfds(a, fds, POLLIN);
	if (n == -1)
		goto down;
	watch_fds(a, fds, n);
	update(a);
	return true;
down:
	disconnect_device(a);
	return false;
}

static void
input_ready(XtPointer data, int *fd, XtInputId *id)
{
	struct app *a = data;

	(void)fd;
	(void)id;
	if (service(a))
		schedule_write(a);
}

static void
write_due(XtPointer data, XtIntervalId *id)
{
	struct app *a = data;
	struct mix_request request;
	bool sent;

	(void)id;
	a->writer = 0;
	/* Drain descriptor changes that are already waiting before dispatch. */
	if (!service(a))
		return;
	if (mix_next(&a->model, mix_now(), &request)) {
		sent = sioctl_setval(a->handle, request.addr, request.value) != 0;
		mix_finish(&a->model, &request, sent);
		if (sioctl_eof(a->handle)) {
			disconnect_device(a);
			return;
		}
	}
	schedule_write(a);
}

static void
schedule_write(struct app *a)
{
	if (a->writer == 0 && a->handle != NULL && mix_pending(&a->model))
		a->writer = XtAppAddTimeOut(d32ui_context(a->ui), mix_delay(&a->model, mix_now()),
		    write_due, a);
}

static void
retry_device(XtPointer data, XtIntervalId *id)
{
	struct app *a = data;

	(void)id;
	a->retry = 0;
	connect_device(a);
}

static void
disconnect_device(struct app *a)
{
	clear_watches(a);
	if (a->writer != 0) {
		XtRemoveTimeOut(a->writer);
		a->writer = 0;
	}
	stop_bell(a);
	if (a->handle != NULL) {
		sioctl_close(a->handle);
		a->handle = NULL;
	}
	mix_clear(&a->model);
	if (!a->closing) {
		a->layout_dirty = true;
		update(a);
		if (a->retry == 0)
			a->retry = XtAppAddTimeOut(d32ui_context(a->ui), RECONNECT_MS, retry_device, a);
	}
}

static void
connect_device(struct app *a)
{
	if (!mix_local_device(getenv("AUDIODEVICE"))) {
		a->layout_dirty = true;
		update(a);
		d32ui_status(a->ui, "No audio device: local sndio endpoint required");
		return;
	}
	a->handle = sioctl_open(SIO_DEVANY, SIOCTL_READ | SIOCTL_WRITE, 1);
	if (a->handle == NULL || !sioctl_onval(a->handle, confirmed, a) ||
	    !sioctl_ondesc(a->handle, description, a)) {
		disconnect_device(a);
		return;
	}
	a->layout_dirty = true;
	(void)service(a);
}

static void
watch_bell(XtPointer data, XtIntervalId *id)
{
	struct app *a = data;
	int result;
	pid_t pid;

	(void)id;
	a->bell_timer = 0;
	pid = waitpid(a->bell_pid, &result, WNOHANG);
	if (pid > 0) {
		a->bell_pid = -1;
		d32ui_status(a->ui, WIFEXITED(result) && WEXITSTATUS(result) == 0 ?
		    "Test sound finished" : "Test sound unavailable");
	} else if (pid == -1 && errno != EINTR) {
		a->bell_pid = -1;
		d32ui_status(a->ui, "Test sound unavailable");
	} else if (mix_now() >= a->bell_until) {
		stop_bell(a);
		d32ui_status(a->ui, "Test sound stopped at its time limit");
	} else {
		a->bell_timer = XtAppAddTimeOut(d32ui_context(a->ui), 25, watch_bell, a);
	}
	render_test(a);
}

static void
test_sound(Widget w, XtPointer data, XtPointer call)
{
	struct app *a = data;
	const char *name = getenv("AUDIODEVICE");
	int sample;

	(void)w;
	(void)call;
	if (a->bell_pid > 0) {
		stop_bell(a);
		d32ui_status(a->ui, "Test sound stopped");
	} else if (service(a) && mix_preview_allowed(&a->model) && mix_local_device(name)) {
		sample = mix_bell_open();
		a->have_bell = sample != -1;
		if (mix_bell_play(sample, name && *name ? name : SIO_DEVANY, &a->bell_pid) == D32E_OK) {
			a->bell_until = mix_now() + BELL_MS;
			a->bell_timer = XtAppAddTimeOut(d32ui_context(a->ui), 25, watch_bell, a);
			d32ui_status(a->ui, "Playing test sound");
		} else {
			d32ui_status(a->ui, "Test sound unavailable");
		}
	}
	render_test(a);
}

static unsigned
pref_size(const struct d32_prefs *prefs, const char *name, unsigned fallback)
{
	const char *error, *s;
	long long n;

	if (prefs == NULL)
		return fallback;
	s = d32_prefs_get(prefs, name, "");
	n = strtonum(s, 200, 4096, &error);
	return error == NULL ? (unsigned)n : fallback;
}

static void
save_geometry(struct app *a)
{
	Dimension width, height;
	char w[16], h[16];

	if (a->pref_state != D32E_OK)
		return;
	XtVaGetValues(d32ui_shell(a->ui), XtNwidth, &width, XtNheight, &height, NULL);
	if (width == a->initial_width && height == a->initial_height)
		return;
	snprintf(w, sizeof(w), "%u", (unsigned)width);
	snprintf(h, sizeof(h), "%u", (unsigned)height);
	if (d32_prefs_set(a->prefs, "width", w) == D32E_OK &&
	    d32_prefs_set(a->prefs, "height", h) == D32E_OK)
		(void)d32_prefs_save(a->prefs);
}

static void
close_app(Widget w, XtPointer data, XtPointer call)
{
	struct app *a = data;

	(void)w;
	(void)call;
	a->closing = true;
	if (a->retry != 0)
		XtRemoveTimeOut(a->retry);
	disconnect_device(a);
	save_geometry(a);
	d32ui_quit(a->ui);
}

static void
build(struct app *a, int recording)
{
	Widget column, controls, buttons, close;

	column = XtVaCreateManagedWidget("audioControls", xmRowColumnWidgetClass, d32ui_body(a->ui),
	    XmNorientation, XmVERTICAL, XmNpacking, XmPACK_TIGHT, XmNresizeWidth, False,
	    XmNleftAttachment, XmATTACH_FORM, XmNrightAttachment, XmATTACH_FORM,
	    XmNtopAttachment, XmATTACH_FORM, NULL);
	a->device_row = row(column, "deviceRow");
	a->device = make_label(a->device_row, "device", "Device: No audio device");
	controls = XtVaCreateManagedWidget("output", xmRowColumnWidgetClass, column,
	    XmNorientation, XmVERTICAL, NULL);
	make_scale(&a->volume, a, controls, MIX_OUT_LEVEL, 0, "outputLevel", "Output level");
	make_mute(&a->mute, a, controls, MIX_OUT_MUTE);
	make_label(column, "recordingPermission", recording < 0 ? "Recording permission: Unavailable" :
	    recording ? "Recording permission: Allowed" : "Recording permission: Off");
	a->input = XtVaCreateWidget("input", xmRowColumnWidgetClass, column,
	    XmNorientation, XmVERTICAL, NULL);
	make_scale(&a->input_level, a, a->input, MIX_IN_LEVEL, 0, "inputLevel",
	    "Input level (does not grant recording permission)");
	make_mute(&a->input_mute, a, a->input, MIX_IN_MUTE);
	buttons = row(column, "buttons");
	a->test = XtVaCreateManagedWidget("testSound", xmPushButtonWidgetClass, buttons, NULL);
	set_label(a->test, "Test sound");
	XtAddCallback(a->test, XmNactivateCallback, test_sound, a);
	close = XtVaCreateManagedWidget("close", xmPushButtonWidgetClass, buttons, NULL);
	set_label(close, "Close");
	XtAddCallback(close, XmNactivateCallback, close_app, a);
	if (!a->have_bell)
		make_label(column, "bellUnavailable", "Test sound unavailable: bell sample missing or invalid");
	if (a->pref_state == D32E_NEWER)
		make_label(column, "newerSettings", D32_PREFS_NEWER_STATUS);
	render_values(a);
}

int
main(int argc, char **argv)
{
	struct app a = { .dir = -1, .bell_pid = -1 };
	const char *device = getenv("AUDIODEVICE");
	int sample, recording;

	if (!mix_local_display(getenv("DISPLAY")))
		return EX_UNAVAILABLE;
	umask(077);
	recording = mix_recording_permission();
	/* Xlib's locale module is dynamically loaded during Motif initialization. */
	if (pledge("stdio rpath wpath cpath fattr flock proc exec unix audio getpw prot_exec", NULL) == -1)
		return EX_UNAVAILABLE;
	a.ui = d32ui_init(&argc, argv, "D32Mix");
	if (a.ui == NULL)
		return EX_UNAVAILABLE;
	a.dir = mix_preferences();
	a.pref_state = a.dir == -1 ? D32E_PERM :
	    d32_prefs_load(a.dir, "d32mix", 1, mix_bad_setting, NULL, &a.prefs);
	a.device_name = mix_local_device(device) && device && *device && strcmp(device, "default") ?
	    device : "Default audio device";
	sample = mix_bell_open();
	a.have_bell = sample != -1;
	if (sample != -1)
		close(sample);
	(void)d32ui_set_handling(a.ui, D32UI_PUBLIC);
	XtVaSetValues(d32ui_shell(a.ui), XtNtitle, "Audio Control", NULL);
	d32ui_geometry(a.ui, pref_size(a.prefs, "width", 640), pref_size(a.prefs, "height", 420), 48);
	build(&a, recording);
	d32ui_on_close(a.ui, close_app, &a);
	d32ui_show(a.ui);
	XtVaGetValues(d32ui_shell(a.ui), XtNwidth, &a.initial_width,
	    XtNheight, &a.initial_height, NULL);
	/* The fixed stock aucat process applies its own pledge. */
	if (pledge("stdio rpath wpath cpath fattr flock proc exec unix audio getpw", NULL) == -1) {
		d32ui_destroy(a.ui);
		return EX_UNAVAILABLE;
	}
	connect_device(&a);
	d32ui_run(a.ui);
	d32ui_destroy(a.ui);
	d32_prefs_free(a.prefs);
	if (a.dir != -1)
		close(a.dir);
	return EX_OK;
}
