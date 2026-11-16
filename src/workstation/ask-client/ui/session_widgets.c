#include "session_widgets.h"

#include <X11/keysym.h>
#include <Xm/Label.h>
#include <Xm/PushB.h>
#include <Xm/RowColumn.h>
#include <Xm/TextF.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { ENGINE, MODEL, STRENGTH, PICK_COUNT, CHOICES_MAX = 64, PASSWORD_MAX = 512 };

typedef struct {
	AskSessionWidgets *owner;
	Widget option, pulldown, placeholder, items[CHOICES_MAX];
	size_t count;
	int kind;
} Picker;

struct AskSessionWidgets {
	Widget root, activity, status, lock, password_label, password, unlock, options, compose;
	const AskCapabilities *capabilities;
	AskSessionState state;
	AskSessionActivity activity_state;
	XtIntervalId activity_timer;
	unsigned frame;
	int timer_pending;
	Picker pick[PICK_COUNT];
	size_t selected[PICK_COUNT];
	void *owner;
	AskProfileChanged changed;
	AskPasswordSubmitted submitted;
	unsigned char secret[PASSWORD_MAX];
	size_t secret_len;
	int clearing;
	Dimension caption_width;
};

static void
erase(void *bytes, size_t length)
{
	volatile unsigned char *at = bytes;
	while (length--) *at++ = 0;
}

static void
caption(Widget widget, const char *text)
{
	XmString label = XmStringCreateLocalized((char *)text);
	XtVaSetValues(widget, XmNlabelString, label, NULL);
	XmStringFree(label);
}

static int ui_font = 14;

void
ask_ui_font(int px)
{
	if (px >= 8 && px <= 64) ui_font = px;
}

int
ask_ui(int pixels)
{
	return (pixels * ui_font + 7) / 14;
}

void
ask_option_style(Widget option)
{
	XtVaSetValues(option, XmNmarginHeight, 1, NULL);
	XtVaSetValues(XmOptionButtonGadget(option), XmNshadowThickness, 2,
	    XmNmarginWidth, ask_ui(6), XmNmarginHeight, ask_ui(2), NULL);
}

void
ask_option_caption(Widget option, Dimension width)
{
	XtVaSetValues(XmOptionLabelGadget(option), XmNrecomputeSize, False, XmNwidth, width,
	    XmNalignment, XmALIGNMENT_BEGINNING, NULL);
}

static void
show(Widget widget, int visible)
{
	if (widget == NULL || !!XtIsManaged(widget) == !!visible) return;
	if (visible) XtManageChild(widget);
	else XtUnmanageChild(widget);
}

static int
active_turn(const AskSessionWidgets *widgets)
{
	return widgets->activity_state == ASK_ACTIVITY_THINKING || widgets->activity_state == ASK_ACTIVITY_RECEIVING;
}

static void
show_activity(AskSessionWidgets *widgets)
{
	static const char *const text[] = {
	    "Connecting", "Locked", "Ready", "Thinking", "Receiving", "Complete", "Request failed"
	};
	static const char frames[] = "|/-\\";
	char line[64];
	if (active_turn(widgets)) {
		snprintf(line, sizeof line, "[%c] %s", frames[widgets->frame % 4], text[widgets->activity_state]);
		caption(widgets->activity, line);
	} else caption(widgets->activity, text[widgets->activity_state]);
}

static void
tick(XtPointer data, XtIntervalId *id)
{
	AskSessionWidgets *widgets = data;
	(void)id;
	widgets->timer_pending = 0;
	if (!active_turn(widgets)) return;
	widgets->frame++;
	show_activity(widgets);
	widgets->activity_timer = XtAppAddTimeOut(XtWidgetToApplicationContext(widgets->root), 175, tick, widgets);
	widgets->timer_pending = 1;
}

void
ask_session_widgets_activity(AskSessionWidgets *widgets, AskSessionActivity activity)
{
	if (activity < ASK_ACTIVITY_CONNECTING || activity > ASK_ACTIVITY_ERROR || activity == widgets->activity_state)
		return;
	widgets->activity_state = activity;
	if (active_turn(widgets)) {
		if (!widgets->timer_pending) {
			widgets->frame = 0;
			widgets->activity_timer = XtAppAddTimeOut(XtWidgetToApplicationContext(widgets->root), 175, tick, widgets);
			widgets->timer_pending = 1;
		}
	} else if (widgets->timer_pending) {
		XtRemoveTimeOut(widgets->activity_timer);
		widgets->timer_pending = 0;
	}
	show_activity(widgets);
}

static int
safe_choice(AskChoice choice)
{
	const unsigned char *at;
	const char *idchars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.:/";
	if (!choice.id || !*choice.id || strlen(choice.id) > 128 ||
	    choice.id[strspn(choice.id, idchars)] || !choice.label || !*choice.label || strlen(choice.label) > 128)
		return 0;
	for (at = (const unsigned char *)choice.label; *at; ++at)
		if (*at < 0x20 || *at == 0x7f) return 0;
	return 1;
}

static int
safe_capabilities(const AskCapabilities *caps)
{
	size_t e, m, s, prior;
	if (!caps || !caps->engines || !caps->count || caps->count > CHOICES_MAX ||
	    (caps->preferred != SIZE_MAX && caps->preferred >= caps->count))
		return 0;
	for (e = 0; e < caps->count; ++e) {
		const AskEngine *engine = &caps->engines[e];
		if (!safe_choice(engine->choice) || !engine->models || !engine->count ||
		    engine->count > CHOICES_MAX || (engine->preferred != SIZE_MAX && engine->preferred >= engine->count)) return 0;
		for (prior = 0; prior < e; ++prior)
			if (!strcmp(caps->engines[prior].choice.id, engine->choice.id)) return 0;
		for (m = 0; m < engine->count; ++m) {
			const AskModel *model = &engine->models[m];
			if (!safe_choice(model->choice) || !model->strengths || !model->count ||
			    model->count > CHOICES_MAX || (model->preferred != SIZE_MAX && model->preferred >= model->count)) return 0;
			for (prior = 0; prior < m; ++prior)
				if (!strcmp(engine->models[prior].choice.id, model->choice.id)) return 0;
			for (s = 0; s < model->count; ++s) {
				if (!safe_choice(model->strengths[s])) return 0;
				for (prior = 0; prior < s; ++prior)
					if (!strcmp(model->strengths[prior].id, model->strengths[s].id)) return 0;
			}
		}
	}
	return 1;
}

static const AskEngine *
current_engine(const AskSessionWidgets *widgets)
{
	return widgets->capabilities && widgets->selected[ENGINE] != SIZE_MAX ?
	    &widgets->capabilities->engines[widgets->selected[ENGINE]] : NULL;
}

static const AskModel *
current_model(const AskSessionWidgets *widgets, const AskEngine *engine)
{
	return engine && widgets->selected[MODEL] != SIZE_MAX ? &engine->models[widgets->selected[MODEL]] : NULL;
}

int
ask_session_widgets_profile(const AskSessionWidgets *widgets, AskProfile *profile)
{
	const AskEngine *engine = current_engine(widgets);
	const AskModel *model = current_model(widgets, engine);
	if (!model || widgets->selected[STRENGTH] == SIZE_MAX || !profile) return 0;
	*profile = (AskProfile){engine->choice.id, model->choice.id, model->strengths[widgets->selected[STRENGTH]].id};
	return 1;
}

static void
render(AskSessionWidgets *widgets)
{
	static const char *const status[] = {
	    "Enter the relay password to unlock Ask.", "Checking password…", "Password rejected. Try again.",
	    "Ask authentication is unavailable.", "Waiting for verified engine settings."
	};
	int locked = widgets->state != ASK_SESSION_UNLOCKED;
	int choices = !locked && widgets->capabilities != NULL;
	AskProfile profile;
	int ready = choices && ask_session_widgets_profile(widgets, &profile);
	int enabled = widgets->state == ASK_SESSION_LOCKED || widgets->state == ASK_SESSION_DENIED;

	caption(widgets->status, choices ? "Choose an engine, model and strength." : status[widgets->state]);
	show(widgets->compose, ready);
	show(widgets->status, !ready);
	show(widgets->lock, locked);
	show(widgets->options, choices);
	XtSetSensitive(widgets->password, enabled);
	XtSetSensitive(widgets->unlock, enabled && widgets->secret_len != 0);
}

void
ask_session_widgets_focus(AskSessionWidgets *widgets)
{
	if (widgets->state == ASK_SESSION_UNLOCKED && widgets->capabilities) {
		Widget engine = widgets->pick[ENGINE].option;
		if (!XmProcessTraversal(XmOptionButtonGadget(engine), XmTRAVERSE_CURRENT))
			(void)XmProcessTraversal(engine, XmTRAVERSE_CURRENT);
	} else if (XtIsSensitive(widgets->password))
		(void)XmProcessTraversal(widgets->password, XmTRAVERSE_CURRENT);
}

void
ask_session_widgets_prompt(AskSessionWidgets *widgets, const char *title, const char *button)
{
	caption(widgets->password_label, title ? title : "Password");
	caption(widgets->unlock, button ? button : "Unlock");
}

static void activate_choice(Widget, XtPointer, XtPointer);

static void
populate(Picker *picker, const AskChoice *choices, size_t count, size_t selected)
{
	Widget items[CHOICES_MAX];
	size_t i;
	for (i = 0; i < count; ++i) {
		/* Room before the option glyph. */
		items[i] = XtVaCreateManagedWidget("choice", xmPushButtonWidgetClass, picker->pulldown,
		    XmNuserData, (XtPointer)(uintptr_t)i, XmNmarginRight, ask_ui(10), NULL);
		caption(items[i], choices[i].label);
		XtAddCallback(items[i], XmNactivateCallback, activate_choice, picker);
	}
	XtVaSetValues(picker->option, XmNmenuHistory, selected < count ? items[selected] : picker->placeholder, NULL);
	for (i = 0; i < picker->count; ++i) XtDestroyWidget(picker->items[i]);
	memcpy(picker->items, items, count * sizeof *items);
	picker->count = count;
	XtSetSensitive(picker->option, count != 0);
}

static void
refill(AskSessionWidgets *widgets, int first)
{
	const AskCapabilities *caps = widgets->capabilities;
	const AskEngine *engine;
	const AskModel *model;
	AskChoice choices[CHOICES_MAX];
	size_t i;
	if (!caps) {
		for (i = 0; i < PICK_COUNT; ++i) populate(&widgets->pick[i], NULL, 0, 0);
		return;
	}
	engine = current_engine(widgets);
	model = current_model(widgets, engine);
	if (first <= ENGINE) {
		for (i = 0; i < caps->count; ++i) choices[i] = caps->engines[i].choice;
		populate(&widgets->pick[ENGINE], choices, caps->count, widgets->selected[ENGINE]);
	}
	if (first <= MODEL) {
		if (engine)
			for (i = 0; i < engine->count; ++i) choices[i] = engine->models[i].choice;
		populate(&widgets->pick[MODEL], choices, engine ? engine->count : 0, widgets->selected[MODEL]);
	}
	populate(&widgets->pick[STRENGTH], model ? model->strengths : NULL, model ? model->count : 0,
	    widgets->selected[STRENGTH]);
}

static void
activate_choice(Widget item, XtPointer data, XtPointer call)
{
	Picker *picker = data;
	AskSessionWidgets *widgets = picker->owner;
	const AskEngine *engine;
	const AskModel *model;
	XtPointer value;
	size_t selected;
	AskProfile profile;
	(void)call;
	if (widgets->state != ASK_SESSION_UNLOCKED || !widgets->capabilities) return;
	XtVaGetValues(item, XmNuserData, &value, NULL);
	selected = (size_t)(uintptr_t)value;
	if (selected >= picker->count || selected == widgets->selected[picker->kind]) return;
	XtVaSetValues(picker->option, XmNmenuHistory, item, NULL);
	widgets->selected[picker->kind] = selected;
	engine = current_engine(widgets);
	if (picker->kind == ENGINE) widgets->selected[MODEL] = engine->preferred;
	model = current_model(widgets, engine);
	if (picker->kind != STRENGTH) {
		widgets->selected[STRENGTH] = model ? model->preferred : SIZE_MAX;
		refill(widgets, picker->kind + 1);
	}
	render(widgets);
	if (widgets->changed && ask_session_widgets_profile(widgets, &profile)) widgets->changed(widgets->owner, profile);
}

int
ask_session_widgets_use_engine(AskSessionWidgets *widgets, const char *id)
{
	const AskCapabilities *caps = widgets->capabilities;
	const AskEngine *engine;
	const AskModel *model;
	AskProfile profile;
	size_t e;
	if (widgets->state != ASK_SESSION_UNLOCKED || !caps || !id) return 0;
	for (e = 0; e < caps->count && strcmp(caps->engines[e].choice.id, id); ++e) {}
	if (e == caps->count) return 0;
	if (widgets->selected[ENGINE] == e && ask_session_widgets_profile(widgets, &profile)) return 1;
	engine = &caps->engines[e];
	widgets->selected[ENGINE] = e;
	widgets->selected[MODEL] = engine->preferred != SIZE_MAX ? engine->preferred : 0;
	model = current_model(widgets, engine);
	widgets->selected[STRENGTH] = model->preferred != SIZE_MAX ? model->preferred : 0;
	refill(widgets, ENGINE);
	render(widgets);
	if (!ask_session_widgets_profile(widgets, &profile)) return 0;
	if (widgets->changed) widgets->changed(widgets->owner, profile);
	return 1;
}

int
ask_session_widgets_use_strength(AskSessionWidgets *widgets, const char *id)
{
	const AskModel *model = current_model(widgets, current_engine(widgets));
	AskProfile profile;
	size_t s;
	if (widgets->state != ASK_SESSION_UNLOCKED || !model || !id) return 0;
	for (s = 0; s < model->count && strcmp(model->strengths[s].id, id); ++s) {}
	if (s == model->count) return 0;
	if (widgets->selected[STRENGTH] == s) return 1;
	widgets->selected[STRENGTH] = s;
	refill(widgets, STRENGTH);
	render(widgets);
	if (widgets->changed && ask_session_widgets_profile(widgets, &profile)) widgets->changed(widgets->owner, profile);
	return 1;
}

Dimension
ask_session_widgets_align(AskSessionWidgets *widgets, Dimension minimum)
{
	int i;
	if (minimum > widgets->caption_width) {
		widgets->caption_width = minimum;
		for (i = 0; i < PICK_COUNT; ++i)
			ask_option_caption(widgets->pick[i].option, widgets->caption_width);
	}
	return widgets->caption_width;
}

int
ask_session_widgets_capabilities(AskSessionWidgets *widgets, const AskCapabilities *caps)
{
	const AskEngine *previous_engine = current_engine(widgets);
	const AskModel *previous_model = current_model(widgets, previous_engine);
	AskProfile old = {
		previous_engine ? previous_engine->choice.id : NULL,
		previous_model ? previous_model->choice.id : NULL,
		previous_model && widgets->selected[STRENGTH] != SIZE_MAX ?
		    previous_model->strengths[widgets->selected[STRENGTH]].id : NULL
	};
	const AskEngine *engine;
	const AskModel *model;
	size_t e, m, s;
	int valid = safe_capabilities(caps);
	int was_ready = widgets->capabilities != NULL && widgets->state == ASK_SESSION_UNLOCKED;
	widgets->capabilities = valid ? caps : NULL;
	if (valid) {
		e = caps->preferred;
		if (old.engine)
			for (size_t i = 0; i < caps->count; ++i)
				if (!strcmp(caps->engines[i].choice.id, old.engine)) { e = i; break; }
		engine = e != SIZE_MAX ? &caps->engines[e] : NULL;
		m = engine ? engine->preferred : SIZE_MAX;
		if (engine && old.model && !strcmp(engine->choice.id, old.engine))
			for (size_t i = 0; i < engine->count; ++i)
				if (!strcmp(engine->models[i].choice.id, old.model)) { m = i; break; }
		model = m != SIZE_MAX ? &engine->models[m] : NULL;
		s = model ? model->preferred : SIZE_MAX;
		if (model && old.strength && !strcmp(engine->choice.id, old.engine) && !strcmp(model->choice.id, old.model))
			for (size_t i = 0; i < model->count; ++i)
				if (!strcmp(model->strengths[i].id, old.strength)) { s = i; break; }
		widgets->selected[ENGINE] = e;
		widgets->selected[MODEL] = m;
		widgets->selected[STRENGTH] = s;
	} else {
		widgets->selected[ENGINE] = widgets->selected[MODEL] = widgets->selected[STRENGTH] = SIZE_MAX;
	}
	if (valid) refill(widgets, ENGINE);
	render(widgets);
	if (!valid) refill(widgets, ENGINE);
	if (valid && widgets->state == ASK_SESSION_UNLOCKED && !was_ready) ask_session_widgets_focus(widgets);
	return valid;
}

static void
clear_password(AskSessionWidgets *widgets)
{
	erase(widgets->secret, sizeof widgets->secret);
	widgets->secret_len = 0;
	widgets->clearing = 1;
	XmTextFieldSetString(widgets->password, "");
	widgets->clearing = 0;
}

void
ask_session_widgets_state(AskSessionWidgets *widgets, AskSessionState state)
{
	int changed = state != widgets->state;
	if (state < ASK_SESSION_LOCKED || state > ASK_SESSION_UNLOCKED) return;
	widgets->state = state;
	if (changed) clear_password(widgets);
	render(widgets);
	if (changed) ask_session_widgets_focus(widgets);
}

static void
mask_edit(Widget widget, XtPointer data, XtPointer call)
{
	AskSessionWidgets *widgets = data;
	XmTextVerifyCallbackStruct *edit = call;
	const XmTextBlock text = edit->text;
	size_t first, last, count, length, i;
	(void)widget;
	if (widgets->clearing || !edit->doit) return;
	edit->doit = False;
	if ((widgets->state != ASK_SESSION_LOCKED && widgets->state != ASK_SESSION_DENIED) ||
	    edit->startPos < 0 || edit->endPos < edit->startPos || (size_t)edit->endPos > widgets->secret_len ||
	    (text && (text->length < 0 || (text->length && (!text->ptr || text->format != XmFMT_8_BIT))))) return;
	first = (size_t)edit->startPos;
	last = (size_t)edit->endPos;
	count = text ? (size_t)text->length : 0;
	length = widgets->secret_len - (last - first) + count;
	if (length > sizeof widgets->secret) return;
	for (i = 0; i < count; ++i)
		if ((unsigned char)text->ptr[i] < 0x20 || (unsigned char)text->ptr[i] == 0x7f) return;
	memmove(widgets->secret + first + count, widgets->secret + last, widgets->secret_len - last);
	if (count) {
		memcpy(widgets->secret + first, text->ptr, count);
		memset(text->ptr, '*', count);
	}
	erase(widgets->secret + length, sizeof widgets->secret - length);
	widgets->secret_len = length;
	XtSetSensitive(widgets->unlock, length != 0);
	edit->doit = True;
}

static void
submit_password(Widget widget, XtPointer data, XtPointer call)
{
	AskSessionWidgets *widgets = data;
	unsigned char secret[PASSWORD_MAX];
	size_t length = widgets->secret_len;
	AskPasswordSubmitted submitted = widgets->submitted;
	void *owner = widgets->owner;
	(void)widget;
	(void)call;
	if (!submitted || !length || (widgets->state != ASK_SESSION_LOCKED && widgets->state != ASK_SESSION_DENIED)) return;
	memcpy(secret, widgets->secret, length);
	ask_session_widgets_state(widgets, ASK_SESSION_CHECKING);
	submitted(owner, secret, length);
	erase(secret, sizeof secret);
}

static void
destroy(Widget widget, XtPointer data, XtPointer call)
{
	AskSessionWidgets *widgets = data;
	(void)widget;
	(void)call;
	if (widgets->timer_pending) XtRemoveTimeOut(widgets->activity_timer);
	erase(widgets->secret, sizeof widgets->secret);
	free(widgets);
}

static void
open_with_enter(Widget widget, XtPointer data, XEvent *event, Boolean *dispatch)
{
	KeySym key;
	(void)data;
	(void)dispatch;
	if (event->type != KeyPress || (event->xkey.state & (ControlMask | Mod1Mask))) return;
	key = XLookupKeysym(&event->xkey, 0);
	if (key == XK_Return || key == XK_KP_Enter) {
		KeyCode space = XKeysymToKeycode(XtDisplay(widget), XK_space);
		if (space) event->xkey.keycode = space;
	}
}

AskSessionWidgets *
ask_session_widgets_create(Widget parent, Widget compose, void *owner, AskProfileChanged changed,
    AskPasswordSubmitted submitted)
{
	static const char *const names[] = {"engine", "model", "strength"};
	static const char *const captions[] = {"Engine", "Model", "Strength"};
	static const char *const placeholders[] = {"Choose engine", "Choose model", "Choose strength"};
	AskSessionWidgets *widgets = calloc(1, sizeof *widgets);
	int i;
	if (!widgets) return NULL;
	widgets->owner = owner;
	widgets->changed = changed;
	widgets->submitted = submitted;
	widgets->compose = compose;
	widgets->root = XtVaCreateWidget("sessionControls", xmRowColumnWidgetClass, parent,
	    XmNorientation, XmVERTICAL, XmNpacking, XmPACK_TIGHT, XmNspacing, ask_ui(4),
	    XmNmarginWidth, 0, XmNmarginHeight, ask_ui(2), NULL);
	widgets->activity = XtVaCreateManagedWidget("activity", xmLabelWidgetClass, widgets->root,
	    XmNalignment, XmALIGNMENT_BEGINNING, XmNrecomputeSize, False, XmNwidth, ask_ui(230), NULL);
	show_activity(widgets);
	widgets->status = XtVaCreateManagedWidget("sessionStatus", xmLabelWidgetClass, widgets->root,
	    XmNalignment, XmALIGNMENT_BEGINNING, NULL);
	widgets->lock = XtVaCreateManagedWidget("unlockControls", xmRowColumnWidgetClass, widgets->root,
	    XmNorientation, XmHORIZONTAL, XmNpacking, XmPACK_TIGHT, XmNspacing, ask_ui(5), NULL);
	widgets->password_label = XtVaCreateManagedWidget("passwordLabel", xmLabelWidgetClass, widgets->lock, NULL);
	widgets->password = XtVaCreateManagedWidget("password", xmTextFieldWidgetClass, widgets->lock,
	    XmNmaxLength, PASSWORD_MAX, XmNcolumns, 15, XmNnavigationType, XmTAB_GROUP, NULL);
	widgets->unlock = XtVaCreateManagedWidget("unlock", xmPushButtonWidgetClass, widgets->lock,
	    XmNshadowThickness, 2, XmNmarginWidth, ask_ui(8), NULL);
	ask_session_widgets_prompt(widgets, NULL, NULL);
	XtAddCallback(widgets->password, XmNmodifyVerifyCallback, mask_edit, widgets);
	XtAddCallback(widgets->password, XmNactivateCallback, submit_password, widgets);
	XtAddCallback(widgets->unlock, XmNactivateCallback, submit_password, widgets);
	widgets->options = XtVaCreateWidget("profileControls", xmRowColumnWidgetClass, widgets->root,
	    XmNorientation, XmVERTICAL, XmNpacking, XmPACK_TIGHT, XmNspacing, ask_ui(2),
	    XmNmarginWidth, 0, XmNmarginHeight, 0, NULL);
	for (i = 0; i < PICK_COUNT; ++i) {
		Picker *picker = &widgets->pick[i];
		Arg args[3];
		Dimension width;
		XmString caption_text = XmStringCreateLocalized((char *)captions[i]);
		picker->kind = i;
		picker->owner = widgets;
		picker->pulldown = XmCreatePulldownMenu(widgets->options, "choices", NULL, 0);
		picker->placeholder = XtVaCreateManagedWidget("choose", xmPushButtonWidgetClass, picker->pulldown, NULL);
		caption(picker->placeholder, placeholders[i]);
		XtSetSensitive(picker->placeholder, False);
		XtSetArg(args[0], XmNsubMenuId, picker->pulldown);
		XtSetArg(args[1], XmNlabelString, caption_text);
		XtSetArg(args[2], XmNnavigationType, XmTAB_GROUP);
		picker->option = XmCreateOptionMenu(widgets->options, (char *)names[i], args, 3);
		ask_option_style(picker->option);
		XtVaGetValues(XmOptionLabelGadget(picker->option), XmNwidth, &width, NULL);
		if (width > widgets->caption_width) widgets->caption_width = width;
		XtAddEventHandler(picker->option, KeyPressMask, False, open_with_enter, NULL);
		XtSetSensitive(picker->option, False);
		XtManageChild(picker->option);
		XmStringFree(caption_text);
	}
	/* One caption column, so the three menu buttons line up as in a CDE dialog. */
	widgets->caption_width += ask_ui(8);
	for (i = 0; i < PICK_COUNT; ++i)
		ask_option_caption(widgets->pick[i].option, widgets->caption_width);
	if (!submitted) widgets->state = ASK_SESSION_UNAVAILABLE;
	render(widgets);
	XtAddCallback(widgets->root, XmNdestroyCallback, destroy, widgets);
	XtManageChild(widgets->root);
	return widgets;
}

Widget
ask_session_widgets_container(const AskSessionWidgets *widgets)
{
	return widgets->root;
}
