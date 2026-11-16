/* The two approved editors and their Settings chooser. */
#include "library_app.h"
#include "focus.h"
#include "spawn.h"
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

/* The Settings module owns the pending selection and its transaction. */
void e26_settings_stage_editor(const char *id);

static const struct editor {
	const char *id;
	const char *label;
	char *const argv[3];
} editors[] = {
	{ "dtpad", "CDE Text Editor", { "/usr/local/dt/bin/dtpad", "-standAlone", NULL } },
	{ "nedit", "NEdit", { "/usr/local/bin/nedit", NULL } }
};

static const struct editor *find_editor(const char *id)
{
	size_t i;
	for (i = 0; id && i < sizeof editors / sizeof editors[0]; i++)
		if (!strcmp(id, editors[i].id)) return &editors[i];
	return NULL;
}

static int available(const struct editor *editor)
{
	struct stat st;
	const char *path;
	if (!editor) return 0;
	path = editor->argv[0];
	return stat(path, &st) == 0 && S_ISREG(st.st_mode) &&
	       (st.st_mode & (S_IXUSR | S_IXGRP | S_IXOTH)) && access(path, X_OK) == 0;
}

static const struct editor *first_available(void)
{
	size_t i;
	for (i = 0; i < sizeof editors / sizeof editors[0]; i++)
		if (available(&editors[i])) return &editors[i];
	return NULL;
}

int e26_wordproc_known(const char *id) { return find_editor(id) != NULL; }
int e26_wordproc_available(const char *id) { return available(find_editor(id)); }

const char *e26_wordproc_default(void)
{
	const struct editor *editor = first_available();
	return editor ? editor->id : NULL;
}

static int tools_launch(e26_app *a, const e26_actx *x)
{
	const struct editor *editor = find_editor(set_get("WordProcessor"));
	(void)x;
	if (!available(editor)) editor = first_available();
	if (!editor) nav_notice(a, "Word Processor");
	else if (spawn_detached(editor->argv[0], editor->argv))
		nav_notice_text(a, "Word Processor", "The word processor could not be started.");
	return 0;
}

void e26_wordproc_open(e26_app *a)
{
	scr_screen *s = scr_find(&a->set, "wordproc");
	int first = -1;
	size_t i;
	for (i = 0; s && i < sizeof editors / sizeof editors[0]; i++) {
		scr_control *row = scr_control_find(s, editors[i].id);
		if (!row) continue;
		row->hidden = !available(&editors[i]);
		snprintf(row->label, sizeof row->label, "%s", editors[i].label);
		if (!row->hidden && first < 0) first = (int)(row - s->c);
	}
	if (first < 0 || nav_push_window(a, "wordproc", 1)) {
		nav_notice(a, "Word Processor");
		return;
	}
	focus_set(a, s, first, 0);
}

static int choose_editor(e26_app *a, const e26_actx *x)
{
	const struct editor *editor = find_editor(x->arg);
	if (!editor || !a->cur || strcmp(a->cur->name, "wordproc")) return 0;
	nav_close_overlay(a);
	if (available(editor)) e26_settings_stage_editor(editor->id);
	else nav_notice(a, "Word Processor");
	return 0;
}

/* APP_SRC includes this file. The generated module list registers room_settings. */
void e26_wordproc_register(void)
{
	static const e26_setting preference = { "WordProcessor", NULL, SET_STRING, "" };
	static const e26_action launch = { tools_launch }, choose = { choose_editor };
	reg_add(REG_SETTING, preference.key, &preference, "wordproc", 0);
	reg_add(REG_ACTION, "room.wordproc", &launch, "wordproc", 0);
	reg_add(REG_ACTION, "wordproc.choose", &choose, "wordproc", 0);
}
