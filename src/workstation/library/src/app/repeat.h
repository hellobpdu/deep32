/* repeat.h: hold-to-repeat (W1-INPUT, plan 3.2 W14). A button held down under the pointer repeats: the first repeat
 * after REPEAT_DELAY_MS, then one every REPEAT_RATE_MS, for as long as the button stays down on it (the pointer leaving
 * it pauses the repeat, as Windows' scroll arrows do). The release then runs the control once more, as every click
 * does. Keys need nothing here: the X server repeats a held key.
 *
 * Which controls repeat: the scroll arrows (actions scroll-up and scroll-down) and any control with "prop repeat yes";
 * "prop repeat no" stops one. A kind or a room with its own stepping (W1-LAYOUT's outline arrows, the media frame's
 * step buttons) calls repeat_hold itself from its press handler. Runs on F0's one-shot timers (timer_after). */
#ifndef E26_REPEAT_H
#define E26_REPEAT_H
#include "library_app.h"

#define REPEAT_DELAY_MS 400   /* [i] Windows 3.1's scroll-bar arrows: about the keyboard delay */
#define REPEAT_RATE_MS 50     /* [i] about twenty steps a second */

/* one step; 0 stops the repeat */
typedef int (*e26_repeat_fn)(e26_app *a, scr_screen *s, int ctl, void *ud);

int repeat_wanted(const scr_control *c);
/* starts repeating fn while control ctl of s is held (a->pressed == ctl, under the pointer, s in front) */
void repeat_hold(e26_app *a, scr_screen *s, int ctl, e26_repeat_fn fn, void *ud);
void repeat_cancel(void);
int repeat_steps(void);   /* the steps the last hold made (the tests and the render log) */
/* the hold test, on its own (the tests) */
int repeat_held(const e26_app *a, const scr_screen *s, int ctl);

#endif
