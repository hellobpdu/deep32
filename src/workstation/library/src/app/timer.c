/* timer.c: one-shot timers, and the animation tick (plan E4): a 33-ms Xt timeout that runs only while at least one
 * animator is registered, so an idle Deep32 Library wakes for nothing. An animator returns 1 to keep going and 0 when it
 * is done; it draws what it changes itself (nav_redraw). The tick's bookkeeping (timer_tick_once) needs no X, so the
 * tests drive it directly. */
#include "library_app.h"
#include <stdlib.h>

typedef struct {
	e26_tick_fn fn;
	void *ud;
	int live;
} animator;

static animator *anim;
static int nanim, capanim, armed;

typedef struct {
	e26_app *a;
	e26_timer_fn fn;
	void *ud;
} oneshot;

static void oneshot_cb(XtPointer cd, XtIntervalId *id)
{
	oneshot *o = (oneshot *)cd;
	(void)id;
	o->fn(o->a, o->ud);
	free(o);
}

void timer_after(e26_app *a, unsigned long ms, e26_timer_fn fn, void *ud)
{
	oneshot *o = (oneshot *)malloc(sizeof *o);
	if (!o) return;
	o->a = a;
	o->fn = fn;
	o->ud = ud;
	XtAppAddTimeOut(a->ctx, ms ? ms : 1, oneshot_cb, (XtPointer)o);
}

int timer_ticking(void)
{
	int i, n = 0;
	for (i = 0; i < nanim; i++) n += anim[i].live;
	return n;
}

int timer_tick_once(e26_app *a)
{
	int i, n = nanim;
	for (i = 0; i < n; i++)
		if (anim[i].live && !anim[i].fn(a, anim[i].ud)) anim[i].live = 0;
	while (nanim > 0 && !anim[nanim - 1].live) nanim--; /* ids of finished animators at the end are reused */
	return timer_ticking();
}

static void tick_cb(XtPointer cd, XtIntervalId *id)
{
	e26_app *a = (e26_app *)cd;
	(void)id;
	armed = 0;
	if (timer_tick_once(a) > 0 && a->ctx) {
		armed = 1;
		XtAppAddTimeOut(a->ctx, E26_TICK_MS, tick_cb, (XtPointer)a);
	}
}

int timer_animate(e26_app *a, e26_tick_fn fn, void *ud)
{
	int id;
	if (!fn) return -1;
	if (nanim == capanim) {
		int cap = capanim ? capanim * 2 : 8;
		animator *na = (animator *)realloc(anim, (size_t)cap * sizeof(animator));
		if (!na) return -1;
		anim = na;
		capanim = cap;
	}
	id = nanim++;
	anim[id].fn = fn;
	anim[id].ud = ud;
	anim[id].live = 1;
	if (!armed && a && a->ctx) {
		armed = 1;
		XtAppAddTimeOut(a->ctx, E26_TICK_MS, tick_cb, (XtPointer)a);
	}
	return id;
}

void timer_stop(int id)
{
	if (id >= 0 && id < nanim) anim[id].live = 0;
}
