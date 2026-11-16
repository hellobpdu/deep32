/* room_findwiz.c: measured category art plus FINDER's complete question sequence.
 * Fields live on their pages; Find collects every page before submitting one Library query. */
#include "library_app.h"
#include "room_finder.h"
#include <stdio.h>
#include <string.h>

static const char *const wizard_pages[] = {
	"findwiz", "findwiz-words", "findwiz-areas", "findwiz-areas-2",
	"findwiz-subcategories", "findwiz-media", "findwiz-time", "findwiz-place"
};

const char *finder_wizard_page(size_t step)
{
	return step < sizeof wizard_pages / sizeof wizard_pages[0] ? wizard_pages[step] : NULL;
}

static int page_index(const scr_screen *s)
{
	size_t i;
	if (!s) return -1;
	for (i = 0; i < sizeof wizard_pages / sizeof wizard_pages[0]; i++)
		if (!strcmp(s->name, wizard_pages[i])) return (int)i;
	return -1;
}

static int selected(const char *key)
{
	int *v = state_shared(key);
	return v && *v > 0;
}

/* The shared state holds the ticks even on a page that is not in front. The optional
 * catpanel's selected subcategory replaces its parent area, keeping the request narrow. */
static void categories(e26_app *a)
{
	const char *ids[TAX_MAX_CAT + 1];
	size_t n = 0;
	int i;
	if (a->cur) states_save(a->cur);
	if (a->have_tax && selected("scope-some")) {
		for (i = 0; i < a->tax.ncat; i++) {
			char key[SCR_ID];
			int sub = a->flt_cat == i && a->flt_sub >= 0 && a->flt_sub < a->tax.cat[i].nsub;
			snprintf(key, sizeof key, "cat:%.30s", a->tax.cat[i].id);
			if (sub) ids[n++] = a->tax.cat[i].sub[a->flt_sub].id;
			else if (selected(key) || a->flt_cat == i) ids[n++] = a->tax.cat[i].id;
		}
	}
	finder_categories(a, ids, n);
}

static int find(e26_app *a, const e26_actx *x)
{
	size_t i;
	(void)x;
	finder_begin_fields();
	for (i = 0; i < sizeof wizard_pages / sizeof wizard_pages[0]; i++) {
		scr_screen *s = scr_find(&a->set, wizard_pages[i]);
		if (s) finder_fields(a, s);
	}
	categories(a);
	finder_search(a, NULL);
	/* Validation may open a notice; keep it over the question that needs repair. */
	if (a->cur && !strcmp(a->cur->name, "notice")) return 0;
	nav_go(a, "finder-results", 0);
	return 0;
}

static int next(e26_app *a, const e26_actx *x)
{
	int i = page_index(a->cur);
	(void)x;
	if (i < 0) return 1;
	if ((size_t)(i + 1) == sizeof wizard_pages / sizeof wizard_pages[0]) return find(a, x);
	finder_fields(a, a->cur);
	nav_go(a, wizard_pages[i + 1], 0);
	return 0;
}

static int previous(e26_app *a, const e26_actx *x)
{
	int i = page_index(a->cur);
	(void)x;
	if (i <= 0) return 1;
	finder_fields(a, a->cur);
	nav_go(a, wizard_pages[i - 1], 0);
	return 0;
}

static void note_subcategory(e26_app *a, const e26_event *ev, void *ud)
{
	int *all, *some, i;
	(void)ud;
	if (ev->type != EV_PRESS || !ev->screen || strcmp(ev->screen->name, "findwiz-subcategories") ||
	    ev->ctl < 0 || ev->ctl >= ev->screen->nc || ev->screen->c[ev->ctl].kind != K_CATPANEL) return;
	all = state_shared("scope-all");
	some = state_shared("scope-some");
	if (all) *all = 0;
	if (some) *some = 1;
	for (i = 0; i < a->set.ns; i++)
		if (!strcmp(a->set.s[i].name, "findwiz-areas") || !strcmp(a->set.s[i].name, "findwiz-areas-2"))
			states_load(&a->set.s[i]);
}

void findwiz_category_buttons(e26_app *a)
{
	int i, k;
	for (i = 0; i < a->set.ns; i++)
		for (k = 0; k < a->set.s[i].nc; k++) {
			scr_control *c = &a->set.s[i].c[k];
			char base[SCR_ASSET];
			const char *last;
			if (c->category <= 0) continue;
			if (!a->have_tax || c->category > a->tax.ncat) { c->hidden = 1; continue; }
			snprintf(base, sizeof base, "%s", c->strip);
			/* app.c calls this after loading screens; safe if a later reload calls again. */
			last = strrchr(base, '/');
			if (!last || strcmp(last + 1, a->tax.cat[c->category - 1].id))
				snprintf(c->strip, sizeof c->strip, "%.100s/%.40s", base, a->tax.cat[c->category - 1].id);
			if (!c->state[0]) snprintf(c->state, sizeof c->state, "cat:%.30s", a->tax.cat[c->category - 1].id);
		}
}

void e26_mod_room_findwiz(void);
void e26_mod_room_findwiz(void)
{
	static const e26_action find_ = { find }, next_ = { next }, previous_ = { previous };
	reg_add(REG_ACTION, "wizard.find", &find_, "room_findwiz", 0);
	reg_add(REG_ACTION, "finder.next", &next_, "room_findwiz", 0);
	reg_add(REG_ACTION, "finder.back", &previous_, "room_findwiz", 0);
	ev_subscribe(EV_PRESS, note_subcategory, NULL);
}
