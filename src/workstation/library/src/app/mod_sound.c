/* mod_sound.c: the interface sounds (W1-SOUND, plan S1 and F15's clicks). screens/sounds.map says which clip plays
 * on which event; this module reads it once, listens on the event bus (a press, a menu dropping, a Category flyout
 * opening, an item picked, a screen entered) and plays the clip on the sound service's click stream (sound.c).
 * NoUISound silences these and nothing else, as in the period design (seg19:03f4). SoundVolume (LIBRARY.INI, 0-100) is
 * Deep32 Library's own key: the volume of both streams.
 *
 * The map's grammar (docs/rooms/SOUND.md has the evidence for every row):
 *   <event> <match> <clip>      the first row that matches decides; "#" starts a comment
 *   events   press | menu-open | flyout | menu-pick | enter
 *   matches  press: "*", "control:<id>", "kind:<kind>", "record" (the control has an original record)
 *            menu-open: "*", "menu:<id>", "flags:<mask>" (the menu's original header has one of the bits), "record"
 *            flyout, menu-pick: "*", "menu:<id>"
 *            enter: a glob over the screen's name
 *   clips    an asset of the store without ".pcm.wav", "@record" (press: the control record's WAVE id, word +32;
 *            0 is none), "-" (silent) */
#include "library_app.h"
#include "sound.h"
#include <ctype.h>
#include <fnmatch.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define SND_MAX_RULES 256
static snd_rule rules[SND_MAX_RULES];
static int nrules;
static int map_state;  /* 0 not read yet, 1 read, -1 missing or bad (silent) */
static int preloaded;

static const char *const evnames[SNDM_NEVENTS] = { "press", "menu-open", "flyout", "menu-pick", "enter" };

const char *snd_map_event_name(int ev) { return ev >= 0 && ev < SNDM_NEVENTS ? evnames[ev] : "?"; }
int snd_map_count(void) { return nrules; }
const snd_rule *snd_map_rule(int i) { return i >= 0 && i < nrules ? &rules[i] : NULL; }

static int asset_ok(const char *s)
{
	const char *p;
	if (!s[0] || s[0] == '/' || strstr(s, "..")) return 0;
	for (p = s; *p; p++)
		if (!isalnum((unsigned char)*p) && !strchr("/_.-", *p)) return 0;
	return 1;
}

static int match_ok(int ev, const char *m)
{
	if (!strcmp(m, "*")) return 1;
	switch (ev) {
	case SNDM_PRESS:
		return !strcmp(m, "record") || (!strncmp(m, "control:", 8) && m[8]) || (!strncmp(m, "kind:", 5) && m[5]);
	case SNDM_MENU_OPEN:
		if (!strncmp(m, "flags:", 6)) {
			char *e;
			unsigned long v = strtoul(m + 6, &e, 0);
			return m[6] && !*e && v > 0 && v <= 0xFFFF;
		}
		return !strcmp(m, "record") || (!strncmp(m, "menu:", 5) && m[5]);
	case SNDM_FLYOUT:
	case SNDM_MENU_PICK:
		return !strncmp(m, "menu:", 5) && m[5];
	case SNDM_ENTER:
		return 1; /* a glob */
	}
	return 0;
}

int snd_map_load(const char *path, char *err, size_t n)
{
	FILE *f = fopen(path, "r");
	char line[512];
	int ln = 0;
	nrules = 0;
	if (!f) { snprintf(err, n, "cannot open %s", path); return -1; }
	while (fgets(line, sizeof line, f)) {
		char *t[4], *p = line, *h;
		int k = 0, ev;
		ln++;
		for (h = line; *h; h++) /* a comment starts at "#" at the line's start or after a blank */
			if (*h == '#' && (h == line || isspace((unsigned char)h[-1]))) { *h = 0; break; }
		while (k < 4 && (t[k] = strtok(k ? NULL : p, " \t\r\n"))) k++;
		if (k == 0) continue;
		if (k != 3) { snprintf(err, n, "%s:%d: needs <event> <match> <clip>", path, ln); fclose(f); return -1; }
		for (ev = 0; ev < SNDM_NEVENTS && strcmp(t[0], evnames[ev]); ev++) {}
		if (ev == SNDM_NEVENTS) { snprintf(err, n, "%s:%d: unknown event %s", path, ln, t[0]); fclose(f); return -1; }
		if (!match_ok(ev, t[1]) || strlen(t[1]) >= sizeof rules[0].match) {
			snprintf(err, n, "%s:%d: %s cannot match %s", path, ln, t[0], t[1]);
			fclose(f);
			return -1;
		}
		if (strcmp(t[2], "-") && !(ev == SNDM_PRESS && !strcmp(t[2], "@record")) && !asset_ok(t[2])) {
			snprintf(err, n, "%s:%d: bad clip %s", path, ln, t[2]);
			fclose(f);
			return -1;
		}
		if (strlen(t[2]) >= sizeof rules[0].clip || nrules == SND_MAX_RULES) {
			snprintf(err, n, "%s:%d: too long, or too many rows", path, ln);
			fclose(f);
			return -1;
		}
		rules[nrules].ev = ev;
		snprintf(rules[nrules].match, sizeof rules[nrules].match, "%s", t[1]);
		snprintf(rules[nrules].clip, sizeof rules[nrules].clip, "%s", t[2]);
		rules[nrules].line = ln;
		nrules++;
	}
	fclose(f);
	return 0;
}

/* the map from the screens directory, once */
static void preload(e26_app *a);

static int ready(e26_app *a)
{
	if (map_state == 0) {
		char path[1100], err[1300];
		map_state = -1;
		if (!a->screens || !a->screens[0] || !a->store) return 0;
		snprintf(path, sizeof path, "%s/sounds.map", a->screens);
		if (snd_map_load(path, err, sizeof err)) fprintf(stderr, "d32library: sound: %s; the interface stays silent\n", err);
		else map_state = 1;
		snd_set_volume(set_get_int("SoundVolume", 100)); /* before the player starts (the Title's theme follows) */
		if (map_state == 1 && !a->no_ui_sound) {
			/* the player and its clips are ready before the first press (the first screen comes here first) */
			preload(a);
			snd_player_start(a->verbose);
		}
	}
	return map_state == 1;
}

static const char *clip_of(const snd_rule *r, e26_app *a, const scr_control *c, char *buf, size_t n)
{
	if (!strcmp(r->clip, "-")) return NULL;
	if (!strcmp(r->clip, "@record")) {
		rec_control rc;
		if (!c || c->record <= 0 || !a->store || rec_control_load(a->store, c->record, &rc) || rc.id <= 0) return NULL;
		snprintf(buf, n, "ui/wave/%d", rc.id);
		return buf;
	}
	snprintf(buf, n, "%s", r->clip);
	return buf;
}

const char *snd_for_press(e26_app *a, scr_screen *s, int i, char *buf, size_t n, int *rule)
{
	const scr_control *c = &s->c[i];
	const char *kname = reg_name(REG_KIND, c->kind);
	int r;
	*rule = -1;
	for (r = 0; r < nrules; r++) {
		const snd_rule *x = &rules[r];
		if (x->ev != SNDM_PRESS) continue;
		if (strcmp(x->match, "*") && !(!strncmp(x->match, "control:", 8) && !strcmp(x->match + 8, c->id)) &&
		    !(!strncmp(x->match, "kind:", 5) && !strcmp(x->match + 5, kname)) && !(!strcmp(x->match, "record") && c->record > 0))
			continue;
		if (!strcmp(x->clip, "@record")) {
			rec_control rc;
			/* a record that cannot be read leaves the decision to the rows below (the class default) */
			if (!a->store || c->record <= 0 || rec_control_load(a->store, c->record, &rc)) continue;
		}
		*rule = r;
		return clip_of(x, a, c, buf, n);
	}
	return NULL;
}

const char *snd_for_menu(e26_app *a, const char *menu, int flyout, char *buf, size_t n, int *rule)
{
	const scr_menu *m = menu ? scr_menu_find(&a->set, menu) : NULL;
	int r, ev = flyout ? SNDM_FLYOUT : SNDM_MENU_OPEN;
	*rule = -1;
	for (r = 0; r < nrules; r++) {
		const snd_rule *x = &rules[r];
		if (x->ev != ev) continue;
		if (!strcmp(x->match, "*")) {}
		else if (!strncmp(x->match, "menu:", 5)) { if (!menu || strcmp(x->match + 5, menu)) continue; }
		else if (!strcmp(x->match, "record")) { if (!m || m->record <= 0) continue; }
		else if (!strncmp(x->match, "flags:", 6)) { if (!m || m->record <= 0 || !(m->rec_flags & strtoul(x->match + 6, NULL, 0))) continue; }
		else continue;
		*rule = r;
		return clip_of(x, a, NULL, buf, n);
	}
	return NULL;
}

const char *snd_for_pick(e26_app *a, const char *menu, char *buf, size_t n, int *rule)
{
	int r;
	*rule = -1;
	for (r = 0; r < nrules; r++) {
		const snd_rule *x = &rules[r];
		if (x->ev != SNDM_MENU_PICK) continue;
		if (strcmp(x->match, "*") && (strncmp(x->match, "menu:", 5) || !menu || strcmp(x->match + 5, menu))) continue;
		*rule = r;
		return clip_of(x, a, NULL, buf, n);
	}
	return NULL;
}

const char *snd_for_enter(e26_app *a, const char *screen, char *buf, size_t n, int *rule)
{
	int r;
	*rule = -1;
	for (r = 0; r < nrules; r++) {
		const snd_rule *x = &rules[r];
		if (x->ev != SNDM_ENTER || fnmatch(x->match, screen, 0)) continue;
		*rule = r;
		return clip_of(x, a, NULL, buf, n);
	}
	return NULL;
}

/* every clip the map can play, loaded by the player as it starts: the literal ones and the WAVE ids of the records
 * the screens use */
static void preload(e26_app *a)
{
	char path[1200];
	int r, i, k;
	if (preloaded) return;
	preloaded = 1;
	for (r = 0; r < nrules; r++)
		if (strcmp(rules[r].clip, "-") && strcmp(rules[r].clip, "@record")) {
			snprintf(path, sizeof path, "%s/%s.pcm.wav", a->store, rules[r].clip);
			snd_preload(path);
		}
	for (i = 0; i < a->set.ns; i++)
		for (k = 0; k < a->set.s[i].nc; k++) {
			rec_control rc;
			if (a->set.s[i].c[k].record > 0 && !rec_control_load(a->store, a->set.s[i].c[k].record, &rc) && rc.id > 0) {
				snprintf(path, sizeof path, "%s/ui/wave/%d.pcm.wav", a->store, rc.id);
				snd_preload(path);
			}
		}
}

int snd_play_asset(e26_app *a, const char *asset)
{
	char path[1200];
	int idx;
	if (!a->store) return -1;
	preload(a);
	if (snd_player_start(a->verbose)) return -1;
	snprintf(path, sizeof path, "%s/%s.pcm.wav", a->store, asset);
	idx = snd_preload(path);
	return snd_click_index(idx);
}

static void play(e26_app *a, const char *asset, const char *what, const char *who)
{
	if (!asset) return;
	if (a->no_ui_sound) {
		if (a->verbose) fprintf(stderr, "d32library: sound: %s %s: %s suppressed (NoUISound)\n", what, who ? who : "", asset);
		return;
	}
	if (a->verbose) fprintf(stderr, "d32library: sound: %s %s: %s\n", what, who ? who : "", asset);
	snd_play_asset(a, asset);
}

static void on_press(e26_app *a, const e26_event *ev, void *ud)
{
	char buf[80];
	int rule;
	const scr_control *c;
	const kind_ops *k;
	(void)ud;
	if (!ev->screen || ev->ctl < 0 || ev->ctl >= ev->screen->nc || !ready(a)) return;
	c = &ev->screen->c[ev->ctl];
	k = kind_of(c->kind);
	if (c->action == A_NONE && !(k && k->interactive)) return; /* nothing there to press */
	if (!control_enabled(a, ev->screen, ev->ctl)) return;      /* a disabled button is silent (seg18:13b1) */
	play(a, snd_for_press(a, ev->screen, ev->ctl, buf, sizeof buf, &rule), "press", c->id);
}

static void on_menu(e26_app *a, const e26_event *ev, void *ud)
{
	char buf[80];
	int rule;
	(void)ud;
	if (!ready(a)) return;
	/* a Category flyout announces itself as its menu's opening with the area in arg (menus.c open_flyout) */
	play(a, snd_for_menu(a, ev->name, ev->arg != NULL, buf, sizeof buf, &rule), ev->arg ? "flyout" : "menu-open", ev->name);
}

static void on_pick(e26_app *a, const e26_event *ev, void *ud)
{
	char buf[80];
	int rule;
	(void)ud;
	if (!ready(a)) return;
	play(a, snd_for_pick(a, ev->name, buf, sizeof buf, &rule), "menu-pick", ev->name);
}

static void on_enter(e26_app *a, const e26_event *ev, void *ud)
{
	char buf[80];
	int rule;
	(void)ud;
	if (!ev->screen || ev->switched || !ready(a)) return;
	play(a, snd_for_enter(a, ev->screen->name, buf, sizeof buf, &rule), "enter", ev->screen->name);
}

void e26_mod_mod_sound(void);
void e26_mod_mod_sound(void)
{
	static const e26_setting volume = { "SoundVolume", NULL, SET_INT, "100" };
	reg_add(REG_SETTING, volume.key, &volume, "mod_sound", 0);
	ev_subscribe(EV_PRESS, on_press, NULL);
	ev_subscribe(EV_MENU_OPEN, on_menu, NULL);
	ev_subscribe(EV_MENU_PICK, on_pick, NULL);
	ev_subscribe(EV_SCREEN_ENTER, on_enter, NULL);
}
