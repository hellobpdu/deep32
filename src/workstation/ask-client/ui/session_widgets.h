#ifndef D32_ASK_SESSION_WIDGETS_H
#define D32_ASK_SESSION_WIDGETS_H

#include <X11/Intrinsic.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
	const char *id, *label;
} AskChoice;

typedef struct {
	AskChoice choice;
	const AskChoice *strengths;
	size_t count, preferred;
} AskModel;

typedef struct {
	AskChoice choice;
	const AskModel *models;
	size_t count, preferred;
} AskEngine;

typedef struct {
	const AskEngine *engines;
	size_t count, preferred;
} AskCapabilities;

typedef struct {
	const char *engine, *model, *strength;
} AskProfile;

typedef enum {
	ASK_SESSION_LOCKED, ASK_SESSION_CHECKING, ASK_SESSION_DENIED,
	ASK_SESSION_UNAVAILABLE, ASK_SESSION_UNLOCKED
} AskSessionState;

typedef enum {
	ASK_ACTIVITY_CONNECTING, ASK_ACTIVITY_LOCKED, ASK_ACTIVITY_READY, ASK_ACTIVITY_THINKING,
	ASK_ACTIVITY_RECEIVING, ASK_ACTIVITY_COMPLETE, ASK_ACTIVITY_ERROR
} AskSessionActivity;

typedef struct AskSessionWidgets AskSessionWidgets;
typedef void (*AskProfileChanged)(void *, AskProfile);
/* Password bytes are borrowed only during this synchronous callback. */
typedef void (*AskPasswordSubmitted)(void *, const unsigned char *, size_t);

/* The caller attaches the returned container and groups its entire composer under compose.
 * The container's Xt destroy callback releases its controller. */
AskSessionWidgets *ask_session_widgets_create(Widget parent, Widget compose, void *,
    AskProfileChanged, AskPasswordSubmitted);
Widget ask_session_widgets_container(const AskSessionWidgets *);
void ask_session_widgets_focus(AskSessionWidgets *);
void ask_session_widgets_prompt(AskSessionWidgets *, const char *title, const char *button);
void ask_session_widgets_state(AskSessionWidgets *, AskSessionState);
void ask_session_widgets_activity(AskSessionWidgets *, AskSessionActivity);
/* Capabilities are borrowed and must stay immutable until replaced or destroyed.
 * Passing NULL revokes them. An invalid snapshot clears them and returns 0.
 * A preferred index of SIZE_MAX leaves that selector unselected. */
int ask_session_widgets_capabilities(AskSessionWidgets *, const AskCapabilities *);
/* Returns 0 with no selection. Returned IDs share the capability snapshot lifetime. */
int ask_session_widgets_profile(const AskSessionWidgets *, AskProfile *);
/* Selects this engine with its preferred model and strength, reporting a change through the
 * profile callback. Returns 0 while locked or when the relay does not offer the engine. */
int ask_session_widgets_use_engine(AskSessionWidgets *, const char *engine);
/* Selects this strength for the model shown and shows it in the Strength menu, reporting a
 * change through the profile callback. Returns 0, leaving the selection as it was, while
 * locked, with no model shown, or when that model does not offer the strength. */
int ask_session_widgets_use_strength(AskSessionWidgets *, const char *strength);
/* Widens the shared caption column of the Engine, Model and Strength menus to at least
 * this many pixels, so other groups can line up with it. Returns the column width. */
Dimension ask_session_widgets_align(AskSessionWidgets *, Dimension minimum);
/* CDE styling shared by every option menu in the window. */
void ask_option_style(Widget option);
void ask_option_caption(Widget option, Dimension width);
/* Control geometry follows the control font: at 14 px a pixel is a pixel, at 17 px it is
 * 17/14 of one. Set the font size once, before any widget is made. */
void ask_ui_font(int px);
int ask_ui(int pixels);

#endif
