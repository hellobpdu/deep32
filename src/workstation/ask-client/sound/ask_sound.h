#ifndef ASKW_SOUND_H
#define ASKW_SOUND_H

#include <X11/Intrinsic.h>

typedef struct AskSound AskSound;

/* How a turn ended: silence, the done ding, or the error cue (the ding an octave down). */
enum { ASK_SOUND_STOP, ASK_SOUND_DONE, ASK_SOUND_FAILED };

/* Owned by the single Xt event thread. A DONE or FAILED end consumes one busy cue. */
AskSound *ask_sound_create(XtAppContext);
void ask_sound_thinking(AskSound *);
void ask_sound_end(AskSound *, int outcome);
/* Called during application teardown, after the event loop has returned. */
void ask_sound_free(AskSound *);

#endif
