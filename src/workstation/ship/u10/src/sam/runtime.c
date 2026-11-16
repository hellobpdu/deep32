#include "sam.h"
#include "d32core.h"

#include <sys/stat.h>

#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define DTACTION "/usr/local/dt/bin/dtaction"
#define DTTERM "/usr/local/dt/bin/dtterm"
#define HELP_ROOT "/usr/local/dt/appconfig/help"

static const char *const actions[] = {"look", "sound", "audio", "upd.apply", "upd.rollback"};
static bool installed_self;

const struct sam_operation *
sam_action(enum sam_action_slot slot)
{
	return (unsigned)slot < SAM_ACTION_COUNT ? sam_find(actions[slot]) : NULL;
}

static bool
protected_exec(const char *path)
{
	struct stat st;
	int fd;
	bool executable;

	if (sam_open_protected(path, &fd) != SAM_OK)
		return false;
	executable = fstat(fd, &st) == 0 && (st.st_mode & 0111) != 0;
	(void)close(fd);
	return executable;
}

static bool
installed_delegate(enum sam_action_slot slot)
{
	static const char *const names[] = {"d32-look", "d32sound", "d32mix"};
	static const char *const allowed[] = {"/d32/a/bin/", "/d32/b/bin/", "/usr/local/d32/ship/bin/"};
	char input[128], resolved[PATH_MAX], expected[128];
	size_t i;

	if (!protected_exec(DTACTION))
		return false;
	(void)snprintf(input, sizeof(input), "/d32/active/bin/%s", names[slot]);
	if (realpath(input, resolved) == NULL) {
		if (slot != SAM_LOOK || realpath("/usr/local/d32/bin/d32-look", resolved) == NULL)
			return false;
		if (strcmp(resolved, "/usr/local/d32/bin/d32-look") == 0)
			return protected_exec(resolved);
	}
	for (i = 0; i < sizeof(allowed) / sizeof(allowed[0]); i++) {
		(void)snprintf(expected, sizeof(expected), "%s%s", allowed[i], names[slot]);
		if (strcmp(resolved, expected) == 0)
			return protected_exec(resolved);
	}
	return false;
}

enum sam_revert_release
sam_classify_revert_release(const struct sam_result *marker)
{
	/* The protected release probe formats the validated name and sequence. */
	if (marker->state == SAM_OK && !marker->truncated) {
		if (strcmp(marker->value, "D.32.12u9 (sequence 10)") == 0)
			return SAM_REVERT_U9;
		if (strcmp(marker->value, "D.32.12u10 (sequence 11)") == 0)
			return SAM_REVERT_U10;
	}
	return SAM_REVERT_UNKNOWN;
}

enum sam_revert_release
sam_current_revert_release(void)
{
	struct sam_result marker;

	(void)sam_probe(sam_find("d32.release"), NULL, &marker);
	return sam_classify_revert_release(&marker);
}

const char *
sam_revert_reason(enum sam_revert_release release)
{
	if (release == SAM_REVERT_U10)
		return "Run the signed recovery audit from a root console before reverting U10.";
	if (release == SAM_REVERT_U9)
		return "Run this in a root terminal yourself.";
	return "Installed release could not be verified for terminal handoff.";
}

enum sam_state
sam_action_eligible(const struct sam_operation *op, enum sam_revert_release release)
{
	unsigned i;

	if (op == NULL)
		return SAM_INVALID;
	if (op == sam_action(SAM_APPLY))
		return SAM_PREVIEW;
	if (op == sam_action(SAM_ROLLBACK))
		return release == SAM_REVERT_U9 ? sam_terminal_check(op) : SAM_PREVIEW;
	for (i = SAM_LOOK; i <= SAM_AUDIO; i++)
		if (op == sam_action(i))
			return installed_delegate(i) ? SAM_OK : SAM_UNAVAILABLE;
	return SAM_INVALID;
}

static void
append(struct sam_page_view *view, const char *format, ...)
{
	size_t n = strlen(view->text);
	va_list args;

	va_start(args, format);
	(void)vsnprintf(view->text + n, sizeof(view->text) - n, format, args);
	va_end(args);
}

static void
time_label(time_t value, char out[16])
{
	struct tm local;

	if (value == (time_t)-1 || localtime_r(&value, &local) == NULL ||
	    strftime(out, 16, "%H:%M", &local) == 0)
		(void)snprintf(out, 16, "unknown");
}

static void
value_line(struct sam_page_view *view, const struct sam_operation *op, enum sam_state state,
    const char *source, time_t read_at, const char *value, bool clipped)
{
	char when[16];

	time_label(read_at, when);
	append(view, "%s  [%s]\nSource %s | read %s | %s\n", op->title, sam_state_name(state),
	    source, when, op->id);
	if (value[0] != '\0')
		append(view, "%s%s", value, value[strlen(value) - 1] == '\n' ? "" : "\n");
	if (clipped)
		append(view, "Additional entries exceed the display limit.\n");
	append(view, "\n");
}

static void
read_operation(const struct sam_operation *op, struct sam_page_view *view)
{
	struct sam_result result;
	(void)sam_probe(op, NULL, &result);
	sam_audit(op, result.state);
	if (op == sam_find("d32.release"))
		view->revert_release = sam_classify_revert_release(&result);
	if (!sam_visible(op, result.state)) {
		append(view, "Updater details require administrator access.\n\n");
		return;
	}
	value_line(view, op, result.state, result.source, result.read_at,
	    result.value, result.truncated);
}

void
sam_read_page(enum sam_category category, const struct sam_prefs *prefs,
    struct sam_page_view *view)
{
	const struct sam_operation *catalog, *op;
	const char *source, *reason;
	char value[192];
	enum sam_state state;
	size_t count, i;

	memset(view, 0, sizeof(*view));
	view->category = category;
	view->read_at = time(NULL);
	for (i = 0; i < SAM_ACTION_COUNT; i++)
		view->actions[i] = SAM_UNAVAILABLE;
	append(view, "%s\n\n", sam_category_name(category));
	catalog = sam_catalog(&count);
	for (i = 0; i < count; i++) {
		op = &catalog[i];
		if (op->class == SAM_READ && (op->category == category ||
		    (category == SAM_DESKTOP && (op == sam_find("d32.release") ||
		    op == sam_find("upd.state")))))
			read_operation(op, view);
	}
	if (category == SAM_DESKTOP) {
		for (i = SAM_LOOK; i <= SAM_AUDIO; i++) {
			op = sam_action(i);
			view->actions[i] = sam_action_eligible(op, view->revert_release);
			value[0] = '\0';
			source = op->source;
			state = sam_desktop_pref(prefs, op->id, value, sizeof(value), &source);
			value_line(view, op, state, source, view->read_at, value, false);
			if (view->actions[i] != SAM_OK)
				append(view, "%s action unavailable.\n\n", op->title);
		}
		op = sam_find("prefs.sam");
		(void)snprintf(value, sizeof(value), "Default page %s\nRefresh %s%s",
		    sam_category_name(prefs->default_page), prefs->refresh_seconds == 30 ? "30 seconds" :
		    prefs->refresh_seconds == 300 ? "5 minutes" : "Off",
		    prefs->newer ? "\n" D32_PREFS_NEWER_STATUS : "");
		value_line(view, op, prefs->dirfd >= 0 ? SAM_OK : SAM_UNAVAILABLE,
		    op->source, view->read_at, value, false);
	} else if (category == SAM_SOFTWARE) {
		for (i = SAM_APPLY; i <= SAM_ROLLBACK; i++) {
			op = sam_action(i);
			state = sam_action_eligible(op, view->revert_release);
			view->actions[i] = state;
			reason = i == SAM_APPLY ? SAM_APPLY_REASON : state == SAM_OK ?
			    "Terminal handoff requires a fresh policy check." :
			    sam_revert_reason(view->revert_release);
			value_line(view, op, SAM_PREVIEW, op->source, view->read_at, reason, false);
		}
	}
}

static enum sam_state
spawn(const char *const vector[], pid_t *child, int *reader)
{
	char *argv[12];
	int input[2], output[2];
	enum d32_error result;
	size_t i;

	*child = -1;
	*reader = -1;
	for (i = 0; i < sizeof(argv) / sizeof(argv[0]); i++) {
		argv[i] = (char *)vector[i];
		if (argv[i] == NULL)
			break;
	}
	if (i == sizeof(argv) / sizeof(argv[0]) || i == 0 || argv[0][0] != '/')
		return SAM_INVALID;
	if (pipe2(input, O_CLOEXEC) == -1)
		return SAM_IO;
	(void)close(input[1]);
	if (pipe2(output, O_CLOEXEC) == -1) {
		(void)close(input[0]);
		return SAM_IO;
	}
	result = d32_exec_fixed(argv[0], argv, input[0], output[1], output[1], child);
	(void)close(input[0]);
	(void)close(output[1]);
	if (result != D32E_OK) {
		(void)close(output[0]);
		return result == D32E_PERM ? SAM_DENIED : result == D32E_UNAVAIL ? SAM_UNAVAILABLE : SAM_IO;
	}
	*reader = output[0];
	return SAM_OK;
}

enum sam_state
sam_spawn_page(const char *self, enum sam_category category, pid_t *child, int *reader)
{
	char value[2] = { '0' + (unsigned)category, '\0' };
	const char *const argv[] = {self, "--probe", value, NULL};

	if (self == NULL || (unsigned)category >= SAM_CATEGORY_COUNT)
		return SAM_INVALID;
	return spawn(argv, child, reader);
}

enum sam_state
sam_spawn_check(const char *self, const struct sam_operation *op, pid_t *child, int *reader)
{
	const char *argv[4] = {self, "--check", NULL, NULL};

	*child = -1;
	*reader = -1;
	if (self == NULL || (op != sam_action(SAM_LOOK) && op != sam_action(SAM_SOUND) &&
	    op != sam_action(SAM_AUDIO) && op != sam_action(SAM_ROLLBACK)))
		return SAM_INVALID;
	if (!installed_self)
		return op == sam_action(SAM_ROLLBACK) ? SAM_PREVIEW : SAM_UNAVAILABLE;
	argv[2] = op->id;
	return spawn(argv, child, reader);
}

const char *const *
sam_action_vector(const struct sam_operation *op, enum sam_state verified)
{
	if (verified == SAM_OK && (op == sam_action(SAM_LOOK) || op == sam_action(SAM_SOUND) ||
	    op == sam_action(SAM_AUDIO) || op == sam_action(SAM_ROLLBACK)))
		return op->argv;
	return NULL;
}

enum sam_state
sam_spawn_action(const struct sam_operation *op, enum sam_state verified, pid_t *child, int *reader)
{
	const char *const *argv = sam_action_vector(op, verified);

	*child = -1;
	*reader = -1;
	if (op == sam_action(SAM_APPLY))
		return SAM_PREVIEW;
	if (op != sam_action(SAM_LOOK) && op != sam_action(SAM_SOUND) &&
	    op != sam_action(SAM_AUDIO) && op != sam_action(SAM_ROLLBACK))
		return SAM_INVALID;
	if (!installed_self || argv == NULL)
		return op == sam_action(SAM_ROLLBACK) ? SAM_PREVIEW : SAM_UNAVAILABLE;
	return spawn(argv, child, reader);
}

int
sam_gui_sandbox(const struct sam_prefs *prefs, const char *self)
{
#ifdef __OpenBSD__
	static const struct { const char *path, *permissions; } optional[] = {
		{ DTACTION, "x" }, { DTTERM, "x" }, { HELP_ROOT, "r" }
	};
	size_t i;
	bool writable = prefs->dirfd != -1 && prefs->document != NULL && !prefs->newer;

	installed_self = (strcmp(self, "/d32/a/bin/sam") == 0 || strcmp(self, "/d32/b/bin/sam") == 0 ||
	    strcmp(self, "/usr/local/d32/bin/sam") == 0 ||
	    strcmp(self, "/usr/local/d32/ship/bin/sam") == 0) && protected_exec(self);
	if (unveil(self, "x") == -1)
		return -1;
	for (i = 0; i < sizeof(optional) / sizeof(optional[0]); i++)
		if (unveil(optional[i].path, optional[i].permissions) == -1 && errno != ENOENT)
			return -1;
	if (prefs->dirfd != -1 && unveil(prefs->path, writable ? "rwc" : "r") == -1)
		return -1;
	if (unveil(NULL, NULL) == -1)
		return -1;
	return pledge(writable ? "stdio rpath wpath cpath flock unix proc exec" :
	    "stdio rpath unix proc exec", NULL);
#else
	(void)prefs;
	(void)self;
	return 0;
#endif
}
