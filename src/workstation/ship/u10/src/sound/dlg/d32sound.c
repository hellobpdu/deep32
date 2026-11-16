#include "d32ui.h"
#include "sound.h"

#include <Xm/Label.h>
#include <Xm/List.h>
#include <Xm/PushB.h>
#include <Xm/RowColumn.h>
#include <Xm/ToggleB.h>
#include <X11/Shell.h>
#include <errno.h>
#include <signal.h>
#include <sndio.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <sysexits.h>
#include <unistd.h>

struct dialog {
	d32ui *ui;
	Widget profiles[SND_PROFILES], events, preview, banner, apply;
	struct d32_prefs *prefs;
	struct snd_assets assets;
	enum d32_error prefs_error;
	int dir;
	bool audio;
	enum snd_profile profile, saved;
	enum snd_event event;
	pid_t player;
	uint64_t preview_deadline;
	XtIntervalId preview_timer, audio_timer;
};

static const char *const descriptions[SND_PROFILES] = {
	"Silent  -  Visual bell only",
	"Classic  -  X bell only",
	"Workstation  -  X bell, session start and session end",
	"Full  -  All three events wired in this release"
};

static void
label(Widget w, const char *text)
{
	XmString s = XmStringCreateLocalized((char *)text);

	XtVaSetValues(w, XmNlabelString, s, NULL);
	XmStringFree(s);
}

static bool
sample_available(struct dialog *d, enum snd_event e)
{
	int fd = snd_sample_open(&d->assets, e);

	if (fd != -1)
		close(fd);
	return fd != -1;
}

static void
buttons(struct dialog *d)
{
	bool playing = d->player > 0;

	label(d->preview, playing ? "Stop Preview" : "Preview");
	XtSetSensitive(d->preview, playing ||
	    (d->audio && snd_enabled(d->profile, d->event) && sample_available(d, d->event)));
	XtSetSensitive(d->apply, d->prefs_error == D32E_OK && d->profile != d->saved);
}

static void
show_events(struct dialog *d)
{
	XmString items[SND_EVENTS];
	int i;

	for (i = 0; i < SND_EVENTS; i++) {
		const char *state;
		char text[128];

		if (!snd_enabled(d->profile, i))
			state = i == SND_BELL ? "visual cue" : "off";
		else if (!sample_available(d, i))
			state = i == SND_BELL ? "sample unavailable, visual cue" : "sample unavailable";
		else if (!d->audio)
			state = i == SND_BELL ? "audio unavailable, visual cue" : "audio unavailable";
		else
			state = "on";
		snprintf(text, sizeof(text), "%s  -  %s", snd_samples[i].label, state);
		items[i] = XmStringCreateLocalized(text);
	}
	XmListDeleteAllItems(d->events);
	XmListAddItems(d->events, items, SND_EVENTS, 1);
	for (i = 0; i < SND_EVENTS; i++)
		XmStringFree(items[i]);
	XmListSelectPos(d->events, d->event + 1, False);
	buttons(d);
}

static bool
audio_available(void)
{
	struct sio_hdl *handle = sio_open(SIO_DEVANY, SIO_PLAY, 1);

	if (handle != NULL)
		sio_close(handle);
	return handle != NULL;
}

static void
check_audio(XtPointer data, XtIntervalId *id)
{
	struct dialog *d = data;
	bool audio = audio_available();

	(void)id;
	if (audio != d->audio) {
		d->audio = audio;
		label(d->banner, audio ? "Audio output available" :
		    "[!] Audio output unavailable \342\200\224 visual cues only");
		show_events(d);
	}
	d->audio_timer = XtAppAddTimeOut(d32ui_context(d->ui), 2000, check_audio, d);
}

static void
stop_preview(struct dialog *d)
{
	if (d->preview_timer) {
		XtRemoveTimeOut(d->preview_timer);
		d->preview_timer = 0;
	}
	snd_player_stop(&d->player);
	buttons(d);
}

static void
watch_preview(XtPointer data, XtIntervalId *id)
{
	struct dialog *d = data;
	int result;
	pid_t pid;

	(void)id;
	d->preview_timer = 0;
	pid = waitpid(d->player, &result, WNOHANG);
	if (pid > 0) {
		d->player = -1;
		d32ui_status(d->ui, WIFEXITED(result) && WEXITSTATUS(result) == 0 ?
		    "Preview finished" : "Audio output unavailable");
		buttons(d);
	} else if (pid == -1 && errno != EINTR) {
		d->player = -1;
		buttons(d);
	} else if (snd_now() >= d->preview_deadline) {
		stop_preview(d);
		d32ui_status(d->ui, "Preview stopped at its time limit");
	} else
		d->preview_timer = XtAppAddTimeOut(d32ui_context(d->ui), 25, watch_preview, d);
}

static void
preview(Widget w, XtPointer data, XtPointer call)
{
	struct dialog *d = data;

	(void)w;
	(void)call;
	if (d->player > 0) {
		stop_preview(d);
		d32ui_status(d->ui, "Preview stopped");
		return;
	}
	if (!d->audio || !snd_enabled(d->profile, d->event) ||
	    snd_player_start(snd_sample_open(&d->assets, d->event), &d->player) != D32E_OK) {
		d32ui_status(d->ui, "Preview unavailable");
		return;
	}
	d->preview_deadline = snd_now() + 3000;
	d->preview_timer = XtAppAddTimeOut(d32ui_context(d->ui), 25, watch_preview, d);
	buttons(d);
	d32ui_status(d->ui, "Playing preview");
}

static void
change_profile(Widget w, XtPointer data, XtPointer call)
{
	struct dialog *d = data;
	const XmToggleButtonCallbackStruct *cb = call;
	int i;

	if (!cb->set)
		return;
	for (i = 0; i < SND_PROFILES && w != d->profiles[i]; i++)
		;
	if (i == SND_PROFILES)
		return;
	d->profile = i;
	if (d->player > 0 && !snd_enabled(d->profile, d->event))
		stop_preview(d);
	show_events(d);
}

static void
select_event(Widget w, XtPointer data, XtPointer call)
{
	struct dialog *d = data;
	const XmListCallbackStruct *cb = call;

	(void)w;
	if (cb->item_position > 0 && cb->item_position <= SND_EVENTS)
		d->event = cb->item_position - 1;
	buttons(d);
}

static bool
save(struct dialog *d)
{
	if (d->profile == d->saved)
		return true;
	if (d->prefs_error == D32E_NEWER) {
		d32ui_status(d->ui, D32_PREFS_NEWER_STATUS);
		return false;
	}
	if (d->prefs_error != D32E_OK ||
	    d32_prefs_set(d->prefs, "profile", snd_profile_names[d->profile]) != D32E_OK ||
	    d32_prefs_save(d->prefs) != D32E_OK) {
		d32ui_status(d->ui, "Sound settings could not be saved");
		return false;
	}
	d->saved = d->profile;
	(void)d32_log("d32-sound", "verb=%s caller_uid=%d target=%s result=%s bytes=%d ms=%d",
	    "save", (int)getuid(), "local", "ok", 0, 0);
	d32ui_status(d->ui, snd_signal(d->dir, SIGHUP, 0) == 0 ?
	    "Sound settings saved" : "Sound settings saved for the next session");
	buttons(d);
	return true;
}

static void
apply(Widget w, XtPointer data, XtPointer call)
{
	(void)w;
	(void)call;
	(void)save(data);
}

static void
close_dialog(Widget w, XtPointer data, XtPointer call)
{
	struct dialog *d = data;

	(void)w;
	(void)call;
	stop_preview(d);
	XtRemoveTimeOut(d->audio_timer);
	d32ui_quit(d->ui);
}

static void
okay(Widget w, XtPointer data, XtPointer call)
{
	if (save(data))
		close_dialog(w, data, call);
}

static Widget
push(Widget parent, const char *name, const char *text, XtCallbackProc cb,
    struct dialog *d)
{
	Widget button = XtVaCreateManagedWidget(name, xmPushButtonWidgetClass, parent, NULL);

	label(button, text);
	XtAddCallback(button, XmNactivateCallback, cb, d);
	return button;
}

static void
build(struct dialog *d)
{
	Widget body, radio, row, w;
	Arg args[2];
	int i;

	body = d32ui_body(d->ui);
	row = XtVaCreateManagedWidget("sound", xmRowColumnWidgetClass, body,
	    XmNorientation, XmVERTICAL, XmNpacking, XmPACK_TIGHT, NULL);
	w = XtVaCreateManagedWidget("title", xmLabelWidgetClass, row, XmNalignment,
	    XmALIGNMENT_BEGINNING, NULL);
	label(w, "Sound profile");
	radio = XmCreateRadioBox(row, "profiles", NULL, 0);
	for (i = 0; i < SND_PROFILES; i++) {
		d->profiles[i] = XtVaCreateManagedWidget(snd_profile_names[i],
		    xmToggleButtonWidgetClass, radio, XmNalignment, XmALIGNMENT_BEGINNING, NULL);
		label(d->profiles[i], descriptions[i]);
		XtAddCallback(d->profiles[i], XmNvalueChangedCallback, change_profile, d);
	}
	XmToggleButtonSetState(d->profiles[d->profile], True, False);
	XtManageChild(radio);
	d->banner = XtVaCreateManagedWidget("device", xmLabelWidgetClass, row,
	    XmNalignment, XmALIGNMENT_BEGINNING, NULL);
	label(d->banner, d->audio ? "Audio output available" :
	    "[!] Audio output unavailable \342\200\224 visual cues only");
	XtSetArg(args[0], XmNvisibleItemCount, SND_EVENTS);
	XtSetArg(args[1], XmNselectionPolicy, XmBROWSE_SELECT);
	d->events = XmCreateScrolledList(row, "events", args, 2);
	XtAddCallback(d->events, XmNbrowseSelectionCallback, select_event, d);
	XtManageChild(d->events);
	row = XtVaCreateManagedWidget("buttons", xmRowColumnWidgetClass, row,
	    XmNorientation, XmHORIZONTAL, XmNpacking, XmPACK_TIGHT, NULL);
	d->preview = push(row, "preview", "Preview", preview, d);
	d->apply = push(row, "apply", "Apply", apply, d);
	(void)push(row, "ok", "OK", okay, d);
	(void)push(row, "cancel", "Cancel", close_dialog, d);
	show_events(d);
}

int
main(int argc, char **argv)
{
	struct dialog d = { .dir = -1, .player = -1, .event = SND_BELL };

	umask(077);
	if (!snd_local_display(getenv("DISPLAY")))
		return EX_UNAVAILABLE;
	/* Keep the default audio device local and the UI's X connection under unix. */
	unsetenv("AUDIODEVICE");
	unsetenv("AUDIOPLAYDEVICE");
	/* Xlib loads its locale module during Motif initialization. */
	if (pledge("stdio rpath wpath cpath fattr flock proc exec unix audio getpw prot_exec", NULL) == -1)
		return EX_UNAVAILABLE;
	d.ui = d32ui_init(&argc, argv, "D32Sound");
	if (d.ui == NULL)
		return EX_UNAVAILABLE;
	if (setenv("DISPLAY", DisplayString(XtDisplay(d32ui_shell(d.ui))), 1) == -1) {
		d32ui_destroy(d.ui);
		return EX_UNAVAILABLE;
	}
	d.dir = snd_prefs_dir();
	d.prefs_error = d.dir == -1 ? D32E_PERM :
	    d32_prefs_load(d.dir, "d32sound", 1, snd_prefs_bad, "d32-sound", &d.prefs);
	d.profile = d.saved = snd_profile_get(d.prefs);
	snd_assets_load(&d.assets);
	d.audio = audio_available();
	(void)d32ui_set_handling(d.ui, D32UI_PUBLIC);
	XtVaSetValues(d32ui_shell(d.ui), XtNtitle, "Sound", NULL);
	d32ui_geometry(d.ui, 550, 370, 48);
	build(&d);
	d32ui_on_close(d.ui, close_dialog, &d);
	d32ui_status(d.ui, d.prefs_error == D32E_NEWER ? D32_PREFS_NEWER_STATUS :
	    d.prefs_error != D32E_OK ? "Sound settings are read-only" :
	    d.audio ? "Sound settings" : "Audio output unavailable \342\200\224 visual cues only");
	d.audio_timer = XtAppAddTimeOut(d32ui_context(d.ui), 2000, check_audio, &d);
	d32ui_show(d.ui);
	/* The fixed base aucat child applies its own startup pledge. */
	if (pledge("stdio rpath wpath cpath fattr flock proc exec unix audio getpw", NULL) == -1) {
		d32ui_destroy(d.ui);
		return EX_UNAVAILABLE;
	}
	d32ui_run(d.ui);
	d32ui_destroy(d.ui);
	d32_prefs_free(d.prefs);
	snd_assets_close(&d.assets);
	if (d.dir != -1)
		close(d.dir);
	return EX_OK;
}
