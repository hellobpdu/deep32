/* repeat.c: hold-to-repeat (W1-INPUT; repeat.h). Whether the button is still down, and over what, is asked of the X
 * server at each step (XQueryPointer), so it works for a kind that handles its own presses as well as for a plain
 * button; without a display (the tests) a->pressed and a->hot answer. */
#include "repeat.h"
#include <stdint.h>
#include <string.h>

static struct {
	unsigned gen;          /* each hold has its own number: a timer of an older hold does nothing */
	scr_screen *s;
	int ctl, steps, live;
	e26_repeat_fn fn;
	void *ud;
} r;

int repeat_wanted(const scr_control *c)
{
	const char *p = scr_prop_get(c, "repeat");
	if (p) return !strcmp(p, "yes") || !strcmp(p, "1") || !strcmp(p, "on");
	return c->action == A_SCROLL_UP || c->action == A_SCROLL_DOWN;
}

int repeat_held(const e26_app *a, const scr_screen *s, int ctl)
{
	return a->cur == s && a->pressed == ctl && a->hot == ctl;
}

/* 1 and the state when the server could be asked */
static int pointer(e26_app *a, int *down, int *over)
{
	Window root, child;
	int rx, ry, wx, wy, x, y;
	unsigned mask;
	if (!a->dpy || !a->win || !XQueryPointer(a->dpy, a->win, &root, &child, &rx, &ry, &wx, &wy, &mask)) return 0;
	*down = (mask & Button1Mask) != 0;
	scale_to_frame(&a->sc, wx, wy, &x, &y);
	*over = hit_control(a, x, y);
	return 1;
}

static void tick(e26_app *a, void *ud)
{
	unsigned gen = (unsigned)(uintptr_t)ud;
	int down, over, held;
	if (gen != r.gen || !r.live) return;
	if (a->cur != r.s) { r.live = 0; return; }                        /* the screen went */
	if (pointer(a, &down, &over)) {
		if (!down) { r.live = 0; return; }                            /* released */
		held = over == r.ctl;
	} else {
		if (a->pressed != r.ctl) { r.live = 0; return; }
		held = repeat_held(a, r.s, r.ctl);
	}
	if (held) {
		r.steps++;
		if (!r.fn(a, r.s, r.ctl, r.ud)) { r.live = 0; return; }
	}
	/* still down (beside it: paused until the pointer comes back) */
	timer_after(a, REPEAT_RATE_MS, tick, (void *)(uintptr_t)gen);
}

void repeat_hold(e26_app *a, scr_screen *s, int ctl, e26_repeat_fn fn, void *ud)
{
	r.gen++;
	r.s = s;
	r.ctl = ctl;
	r.fn = fn;
	r.ud = ud;
	r.steps = 0;
	r.live = 1;
	if (a && a->ctx) timer_after(a, REPEAT_DELAY_MS, tick, (void *)(uintptr_t)r.gen);
}

void repeat_cancel(void)
{
	r.gen++;
	r.live = 0;
}

int repeat_steps(void) { return r.steps; }
