/* actions.c: the built-in actions (registry.h names them at build 2's A_* numbers) and action_run, which falls back
 * to the "coming in a later build" box for a named action no module builds yet (plan 3.1: every build-2 notice is a
 * named action or room id with the notice as fallback). A room adds its own with reg_add(REG_ACTION, ...). */
#include "library_app.h"
#include "room_article.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int action_built(int id)
{
	const e26_action *h = (const e26_action *)reg_impl(REG_ACTION, id);
	return h && h->fn;
}

int action_run(e26_app *a, int id, const e26_actx *x)
{
	const e26_action *h = (const e26_action *)reg_impl(REG_ACTION, id);
	if (h && h->fn) return h->fn(a, x);
	if (scr_action_is_notice(id) || !x->s) {
		/* a named action no module builds yet (its argument is the room's title), "notice" itself, or a menu item with
		 * nothing to do: today's box, word for word */
		nav_notice(a, x->arg && x->arg[0] ? x->arg : x->label && x->label[0] ? x->label : "This");
		return 0;
	}
	return 1;
}

static scr_control *ctl(const e26_actx *x) { return x->s && x->ctl >= 0 ? &x->s->c[x->ctl] : NULL; }

static int a_goto(e26_app *a, const e26_actx *x) { nav_go(a, x->arg, 1); return 0; }

static int a_close(e26_app *a, const e26_actx *x)
{
	scr_screen *s = x->s ? x->s : a->cur;
	if (s && s->over[0]) nav_close_overlay(a);
	return 0;
}

static int a_notice(e26_app *a, const e26_actx *x) { nav_notice(a, x->arg && x->arg[0] ? x->arg : x->label); return 0; }

static int a_back(e26_app *a, const e26_actx *x) { (void)x; nav_back(a); return 0; }

void e26_quit(e26_app *a)
{
	/* state.c reads 512-byte INI lines. Leave space for the key, LF and NUL. */
	char position[512 - sizeof "LastPosition="];
	if (set_get_bool("SetBookmarkOnExit", 1)) art_save_position(a);
	if (*state_dir() && set_get_bool("SetBookmarkOnExit", 1) && art_public_position(a) &&
	    !art_position_uri(a, position, sizeof position)) {
		const char *keys[] = { "LastPosition" }, *values[] = { position };
		(void)set_put_save(keys, values, 1);
	}
	snd_stop(a->snd_pid);
	exit(0);
}

static int a_quit(e26_app *a, const e26_actx *x) { (void)x; e26_quit(a); return 0; }

static int a_iconify(e26_app *a, const e26_actx *x)
{
	XIconifyWindow(a->dpy, XtWindow(a->top), DefaultScreen(a->dpy)); /* the caption's minimise button */
	if (ctl(x)) a->hot = a->pressed = -1;
	return 1;
}

static int a_maximize(e26_app *a, const e26_actx *x)
{
	/* the caption's middle button: the window fills its monitor, or is 640x480 again (wm.c) */
	if (ctl(x)) a->hot = a->pressed = -1;
	wm_toggle_maximize(a);
	return 1;
}

static int a_toggle(e26_app *a, const e26_actx *x)
{
	scr_control *c = ctl(x);
	const char *key;
	(void)a;
	if (!c) return 1;
	c->checked = !c->checked;
	states_save(x->s);
	/* a box bound to a setting ("prop setting ShowTitleScreen") stores its state (plan O3) */
	if ((key = scr_prop_get(c, "setting"))) {
		set_put(key, c->checked ? "1" : "0");
		set_save();
	}
	return 1;
}

static int a_select(e26_app *a, const e26_actx *x)
{
	scr_control *c = ctl(x);
	int k;
	(void)a;
	if (!c) return 1;
	for (k = 0; k < x->s->nc; k++)
		if (!strcmp(x->s->c[k].group, c->group) && x->s->c[k].kind == K_RADIO) x->s->c[k].checked = 0;
	c->checked = 1;
	states_save(x->s);
	return 1;
}

static int a_menu(e26_app *a, const e26_actx *x)
{
	scr_control *c = ctl(x);
	if (c) menus_toggle(a, x->arg, c->x + (c->absolute ? 0 : x->s->ox), c->y + (c->absolute ? 0 : x->s->oy) + c->h + 1);
	else menus_toggle(a, x->arg, 0, 0);
	return 1;
}

void actions_register(void)
{
	static const e26_action goto_ = { a_goto }, close_ = { a_close }, notice_ = { a_notice }, back_ = { a_back }, quit_ = { a_quit },
	                        iconify_ = { a_iconify }, maximize_ = { a_maximize }, toggle_ = { a_toggle }, select_ = { a_select },
	                        menu_ = { a_menu };
	reg_add(REG_ACTION, "goto", &goto_, "actions", 0);
	reg_add(REG_ACTION, "close", &close_, "actions", 0);
	reg_add(REG_ACTION, "notice", &notice_, "actions", 0);
	reg_add(REG_ACTION, "back", &back_, "actions", 0);
	reg_add(REG_ACTION, "quit", &quit_, "actions", 0);
	reg_add(REG_ACTION, "iconify", &iconify_, "actions", 0);
	reg_add(REG_ACTION, "maximize", &maximize_, "actions", 0);
	reg_add(REG_ACTION, "toggle", &toggle_, "actions", 0);
	reg_add(REG_ACTION, "select", &select_, "actions", 0);
	reg_add(REG_ACTION, "menu", &menu_, "actions", 0);
	/* open-article, scroll-up, scroll-down, section-prev and section-next: room_article.c; reset: room_pinpoint.c */
}
