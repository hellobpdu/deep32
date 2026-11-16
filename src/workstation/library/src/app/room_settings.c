/* room_settings.c: the Library's modal Options dialog, with a single explicit save. */
#include "library_app.h"
#include "appearance.h"
#include "focus.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int e26_wordproc_known(const char *id);
int e26_wordproc_available(const char *id);
const char *e26_wordproc_default(void);
void e26_wordproc_open(e26_app *a);
void e26_wordproc_register(void);

enum { MENU_SOUND, SHOW_TITLE, TITLE_SOUND, SAVE_VIEW, SAVE_PLACE, HOVER_MENUS, NOPTIONS };
enum { SESSION_MENU_SOUND = 1, SESSION_TITLE_SOUND = 2 };
enum { APPEARANCE_ROWS = 10 };

static const char *const keys[NOPTIONS] = {
	"NoUISound", "ShowTitleScreen", "IntroPlaySounds", "SaveWindowLayout",
	"SetBookmarkOnExit", "FlyoutsOnMousePass"
};
/* SaveWindowLayout and SetBookmarkOnExit are [i], from capture 63's state. */
static const int defaults[NOPTIONS] = { 0, 1, 1, 0, 1, 1 };
static const unsigned long swatches[] = { 8388608, 128, 16711680, 32768, 16711935 };

typedef struct {
	int option[NOPTIONS];
	unsigned long colour;
	const char *editor;
	int editor_chosen;
	size_t theme;
} settings_values;
static settings_values committed, pending;
static unsigned long jump_rgb = 0x000080L;
static size_t preview_theme;

void e26_settings_stage_editor(const char *id)
{
	pending.editor = id;
	pending.editor_chosen = 1;
}

static unsigned long read_colour(void)
{
	const char *value = set_get("JumpColor");
	char *end;
	unsigned long colour;
	if (!value || *value < '0' || *value > '9') return swatches[0];
	errno = 0;
	colour = strtoul(value, &end, 10);
	if (errno || end == value || *end || colour > 0xffffffUL) return swatches[0];
	return colour;
}

static unsigned long rgb(unsigned long colour)
{
	return ((colour & 0xffUL) << 16) | (colour & 0xff00UL) | ((colour >> 16) & 0xffUL);
}

unsigned long e26_jump_rgb(void) { return jump_rgb; }

void e26_settings_session(e26_app *a, int menu_override, int title_override);
void e26_settings_session(e26_app *a, int menu_override, int title_override)
{
	a->settings_overrides = (menu_override ? SESSION_MENU_SOUND : 0u) |
	                       (title_override ? SESSION_TITLE_SOUND : 0u);
	jump_rgb = rgb(read_colour());
}

static scr_screen *current(e26_app *a)
{
	return a->cur && !strcmp(a->cur->name, "settings") ? a->cur : NULL;
}

static void sync_controls(e26_app *a, scr_screen *s)
{
	scr_control *c;
	char id[32];
	int i;
	for (i = 0; i < NOPTIONS; i++) {
		snprintf(id, sizeof id, "settings-option-%d", i);
		if ((c = scr_control_find(s, id)) != NULL)
			c->checked = i == MENU_SOUND ? !pending.option[i] : pending.option[i];
	}
	for (i = 0; i < (int)(sizeof swatches / sizeof swatches[0]); i++) {
		snprintf(id, sizeof id, "settings-colour-%d", i);
		if ((c = scr_control_find(s, id)) != NULL) c->checked = pending.colour == swatches[i];
	}
	if ((c = scr_control_find(s, "settings-theme-current")) != NULL) {
		const char *status = pending.theme != committed.theme ? "Pending" :
		    pending.theme != a->appearance_theme ? "Next launch" : "Current";
		snprintf(c->label, sizeof c->label, "%s\n%s", status, e26_themes[pending.theme].label);
	}
}

static int settings_open(e26_app *a, const e26_actx *x)
{
	scr_screen *s = scr_find(&a->set, "settings");
	int i;
	(void)x;
	if (!s) { nav_notice_text(a, "Settings", "The Settings dialog is unavailable."); return 0; }
	for (i = 0; i < a->nwin; i++) if (a->wstack[i].s == s) return 0;
	/* Keep a slot for a save error or Word Processor notice. */
	if (a->nwin >= E26_WSTACK - 1) return 0;
	for (i = 0; i < NOPTIONS; i++) committed.option[i] = set_get_bool(keys[i], defaults[i]);
	committed.colour = read_colour();
	committed.theme = e26_theme_index(set_get("AppearanceTheme"));
	pending = committed;
	sync_controls(a, s);
	if (nav_push_window(a, "settings", 1)) nav_notice_text(a, "Settings", "Close a floating window and try again.");
	return 0;
}

static int option_index(const char *arg, int count)
{
	if (!arg || arg[0] < '0' || arg[0] >= '0' + count || arg[1]) return -1;
	return arg[0] - '0';
}

static int theme_count(void)
{
	return e26_theme_count < APPEARANCE_ROWS ? (int)e26_theme_count : APPEARANCE_ROWS;
}

static scr_control *theme_row(scr_screen *s, int row)
{
	char id[32];
	snprintf(id, sizeof id, "appearance-row-%d", row);
	return scr_control_find(s, id);
}

static void theme_preview(e26_app *a, scr_screen *s)
{
	static const struct { const char *id; int bg, fg; } sample[] = {
		{ "appearance-sample-title", UI_BLACK, UI_WHITE },
		{ "appearance-sample-bar", UI_CAPTION, UI_STONE },
		{ "appearance-sample-page", UI_WHITE, -1 },
		{ "appearance-sample-heading", -1, UI_BLACK },
		{ "appearance-sample-text", -1, UI_BLACK },
		{ "appearance-sample-controls", UI_PAPER, -1 },
		{ "appearance-sample-button", UI_STONE, UI_BLACK },
		{ "appearance-sample-thumb", UI_TAUPE, -1 },
	};
	const e26_theme *theme = &e26_themes[preview_theme];
	rec_palette palette;
	scr_control *c;
	char id[32];
	size_t k;
	int i;

	for (i = 0; i < APPEARANCE_ROWS; i++) {
		if ((c = theme_row(s, i)) == NULL) continue;
		c->hidden = i >= theme_count();
		if (c->hidden) continue;
		c->checked = (size_t)i == preview_theme;
		snprintf(c->label, sizeof c->label, "%s%s", c->checked ? "[*] " : "", e26_themes[i].label);
		snprintf(id, sizeof id, "appearance-chip-%d", i);
		if ((c = scr_control_find(s, id)) != NULL) {
			e26_theme_preview_palette(a, (size_t)i, &palette);
			c->bg = rec_pal(&palette, UI_CAPTION);
		}
	}
	if ((c = scr_control_find(s, "appearance-description")) != NULL) {
		char font_note[64];
		if (a->reader_font_choice < 0 || a->reader_face < 0 || (size_t)a->reader_face >= fonts_reader_face_count)
			snprintf(font_note, sizeof font_note, "Reader follows the theme.");
		else
			snprintf(font_note, sizeof font_note, "%s: %s",
			    a->reader_font_choice == a->reader_face ? "Override" : "Font fallback",
			    fonts_reader_faces[a->reader_face].label);
		snprintf(c->label, sizeof c->label, "%s\nDefault reader: %s\n%s", theme->description,
		    fonts_reader_theme_name(preview_theme), font_note);
	}
	if ((c = scr_control_find(s, "appearance-saved")) != NULL)
		snprintf(c->label, sizeof c->label, "Saved theme: %s", e26_themes[committed.theme].label);
	if ((c = scr_control_find(s, "appearance-use")) != NULL)
		snprintf(c->label, sizeof c->label, "%s", preview_theme == pending.theme ? "Keep" : "Use");
	e26_theme_preview_palette(a, preview_theme, &palette);
	for (k = 0; k < sizeof sample / sizeof sample[0]; k++) {
		if ((c = scr_control_find(s, sample[k].id)) == NULL) continue;
		if (sample[k].bg >= 0) c->bg = rec_pal(&palette, sample[k].bg);
		if (sample[k].fg >= 0) c->fg = rec_pal(&palette, sample[k].fg);
	}
	if (preview_theme && (c = scr_control_find(s, "appearance-sample-title")) != NULL)
		c->bg = theme->title;
}

static scr_screen *appearance(e26_app *a)
{
	return a->cur && !strcmp(a->cur->name, "appearance") ? a->cur : NULL;
}

static int theme_open(e26_app *a, const e26_actx *x)
{
	scr_screen *s = scr_find(&a->set, "appearance");
	scr_control *row;
	(void)x;
	if (!current(a) || !s || !theme_count() || a->nwin >= E26_WSTACK - 1) return 0;
	preview_theme = pending.theme < (size_t)theme_count() ? pending.theme : 0;
	theme_preview(a, s);
	if (nav_push_window(a, "appearance", 1)) return 0;
	if ((row = theme_row(s, (int)preview_theme)) != NULL)
		focus_set(a, s, (int)(row - s->c), 1);
	return 1;
}

static int theme_select(e26_app *a, const e26_actx *x)
{
	scr_screen *s = appearance(a);
	int i = option_index(x->arg, theme_count());
	if (!s || i < 0) return 0;
	preview_theme = (size_t)i;
	theme_preview(a, s);
	return 1;
}

static int theme_step(e26_app *a, int direction)
{
	scr_screen *s = appearance(a);
	scr_control *row;
	int n = theme_count();
	if (!s || !n) return 0;
	preview_theme = (size_t)(((int)preview_theme + direction + n) % n);
	theme_preview(a, s);
	if ((row = theme_row(s, (int)preview_theme)) != NULL)
		focus_set(a, s, (int)(row - s->c), 1);
	return 1;
}

static int theme_prev(e26_app *a, const e26_actx *x) { (void)x; return theme_step(a, -1); }
static int theme_next(e26_app *a, const e26_actx *x) { (void)x; return theme_step(a, 1); }

static int theme_use(e26_app *a, const e26_actx *x)
{
	scr_screen *s = scr_find(&a->set, "settings");
	scr_control *ok;
	(void)x;
	if (!appearance(a) || !s) return 0;
	pending.theme = preview_theme;
	sync_controls(a, s);
	nav_close_overlay(a);
	if (current(a) == s && (ok = scr_control_find(s, "settings-ok")) != NULL) {
		focus_set(a, s, (int)(ok - s->c), 1);
		return 1;
	}
	return 0;
}

static int toggle(e26_app *a, const e26_actx *x)
{
	scr_screen *s = current(a);
	int i = option_index(x->arg, NOPTIONS);
	if (!s || i < 0) return 0;
	pending.option[i] = !pending.option[i];
	sync_controls(a, s);
	return 1;
}

static int colour(e26_app *a, const e26_actx *x)
{
	scr_screen *s = current(a);
	int i = option_index(x->arg, (int)(sizeof swatches / sizeof swatches[0]));
	if (!s || i < 0) return 0;
	pending.colour = swatches[i];
	sync_controls(a, s);
	return 1;
}

static int step_colour(e26_app *a, int direction)
{
	scr_screen *s = current(a);
	scr_control *proxy;
	int i, count = (int)(sizeof swatches / sizeof swatches[0]);
	if (!s || !(proxy = scr_control_find(s, "settings-colour-0")) ||
	    focus_get(a, s) != proxy - s->c) return 0;
	for (i = 0; i < count && pending.colour != swatches[i]; i++) { }
	if (i == count) i = direction > 0 ? 0 : count - 1;
	else i = (i + count + direction) % count;
	pending.colour = swatches[i];
	sync_controls(a, s);
	return 1;
}

static int previous_colour(e26_app *a, const e26_actx *x) { (void)x; return step_colour(a, -1); }
static int next_colour(e26_app *a, const e26_actx *x) { (void)x; return step_colour(a, 1); }

static int activate(e26_app *a, const e26_actx *x)
{
	scr_screen *s = current(a);
	scr_control *group = s ? scr_control_find(s, "settings-colour-0") : NULL;
	if (!s || (group && focus_get(a, s) == group - s->c)) return 0;
	return action_run(a, reg_find(REG_ACTION, "focus.activate"), x);
}

static int reset(e26_app *a, const e26_actx *x)
{
	scr_screen *s = current(a);
	(void)x;
	if (!s) return 0;
	memcpy(pending.option, defaults, sizeof defaults);
	pending.colour = swatches[0];
	pending.editor = e26_wordproc_default();
	pending.editor_chosen = 0;
	pending.theme = 0;
	sync_controls(a, s);
	return 1;
}

static int settings_ok(e26_app *a, const e26_actx *x)
{
	const char *changed_keys[NOPTIONS + 3], *changed_values[NOPTIONS + 3];
	char stored_colour[16];
	size_t n = 0;
	int i;
	(void)x;
	if (!current(a)) return 0;
	for (i = 0; i < NOPTIONS; i++) if (pending.option[i] != committed.option[i]) {
		changed_keys[n] = keys[i];
		changed_values[n++] = pending.option[i] ? "1" : "0";
	}
	if (pending.colour != committed.colour) {
		snprintf(stored_colour, sizeof stored_colour, "%lu", pending.colour);
		changed_keys[n] = "JumpColor";
		changed_values[n++] = stored_colour;
	}
	if (pending.theme != committed.theme) {
		changed_keys[n] = "AppearanceTheme";
		changed_values[n++] = e26_themes[pending.theme].id;
	}
	if (pending.editor) {
		const char *stored = set_get("WordProcessor");
		if (!e26_wordproc_available(pending.editor)) {
			nav_notice(a, "Word Processor");
			return 0;
		}
		/* Reset keeps an implicit fallback implicit. Choose is an explicit preference. */
		if ((pending.editor_chosen || e26_wordproc_known(stored)) &&
		    (!stored || strcmp(stored, pending.editor))) {
			changed_keys[n] = "WordProcessor";
			changed_values[n++] = pending.editor;
		}
	}
	if (n && set_put_save(changed_keys, changed_values, n)) {
		nav_notice_text(a, "Settings", "The settings could not be saved.");
		return 0;
	}
	if (pending.option[MENU_SOUND] != committed.option[MENU_SOUND] &&
	    !(a->settings_overrides & SESSION_MENU_SOUND))
		a->no_ui_sound = pending.option[MENU_SOUND] ? True : False;
	if (pending.option[TITLE_SOUND] != committed.option[TITLE_SOUND] &&
	    !(a->settings_overrides & SESSION_TITLE_SOUND))
		a->intro_sounds = pending.option[TITLE_SOUND] ? True : False;
	if (pending.option[SHOW_TITLE] != committed.option[SHOW_TITLE]) {
		scr_screen *title = scr_find(&a->set, "title");
		scr_control *checkbox = title ? scr_control_find(title, "showtitle") : NULL;
		if (checkbox) checkbox->checked = pending.option[SHOW_TITLE];
	}
	if (pending.colour != committed.colour) jump_rgb = rgb(pending.colour);
	nav_close_overlay(a);
	return 0;
}

static int settings_close(e26_app *a, const e26_actx *x)
{
	(void)x;
	if (current(a)) nav_close_overlay(a);
	return 0;
}

static int settings_enter(e26_app *a, const e26_actx *x)
{
	scr_screen *s = current(a);
	scr_control *theme = s ? scr_control_find(s, "settings-theme") : NULL;
	if (theme && focus_get(a, s) == theme - s->c) return theme_open(a, x);
	return settings_ok(a, x);
}

static int word_processor(e26_app *a, const e26_actx *x)
{
	(void)x;
	if (current(a)) e26_wordproc_open(a);
	return 0;
}

void e26_mod_room_settings(void);
void e26_mod_room_settings(void)
{
	static const e26_setting save_view = { "SaveWindowLayout", NULL, SET_BOOL, "0" };
	static const e26_setting save_place = { "SetBookmarkOnExit", NULL, SET_BOOL, "1" };
	static const e26_setting theme = { "AppearanceTheme", NULL, SET_STRING, "classic" };
	static const struct { const char *name; e26_action action; } actions[] = {
		{ "room.settings", { settings_open } }, { "settings.toggle", { toggle } },
		{ "settings.colour", { colour } }, { "settings.colour-prev", { previous_colour } },
		{ "settings.colour-next", { next_colour } }, { "settings.ok", { settings_ok } },
		{ "settings.enter", { settings_enter } },
		{ "settings.close", { settings_close } }, { "settings.reset", { reset } },
		{ "settings.activate", { activate } },
		{ "settings.word-processor", { word_processor } },
		{ "settings.theme-open", { theme_open } },
		{ "settings.theme-select", { theme_select } },
		{ "settings.theme-prev", { theme_prev } },
		{ "settings.theme-next", { theme_next } },
		{ "settings.theme-use", { theme_use } }
	};
	size_t i;
	reg_add(REG_SETTING, save_view.key, &save_view, "room_settings", 0);
	reg_add(REG_SETTING, save_place.key, &save_place, "room_settings", 0);
	reg_add(REG_SETTING, theme.key, &theme, "room_settings", 0);
	e26_wordproc_register();
	for (i = 0; i < sizeof actions / sizeof actions[0]; i++)
		reg_add(REG_ACTION, actions[i].name, &actions[i].action, "room_settings", 0);
}
