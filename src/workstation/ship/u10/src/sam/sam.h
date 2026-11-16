#ifndef D32_SAM_H
#define D32_SAM_H

#include "catalog.h"

#include <limits.h>
#include <sys/types.h>

struct d32_prefs;

struct sam_prefs {
	int dirfd;
	struct d32_prefs *document;
	enum sam_category default_page;
	unsigned refresh_seconds;
	bool newer;
	char path[PATH_MAX];
};

enum sam_action_slot { SAM_LOOK, SAM_SOUND, SAM_AUDIO, SAM_APPLY, SAM_ROLLBACK,
	SAM_ACTION_COUNT };
enum sam_revert_release { SAM_REVERT_UNKNOWN, SAM_REVERT_U9, SAM_REVERT_U10 };

#define SAM_PAGE_TEXT 65536u
#define SAM_APPLY_REASON "Runs during the boot window."

struct sam_page_view {
	enum sam_category category;
	time_t read_at;
	enum sam_revert_release revert_release;
	unsigned char actions[SAM_ACTION_COUNT];
	char text[SAM_PAGE_TEXT];
};

enum sam_state sam_prefs_open(struct sam_prefs *, bool create);
void sam_prefs_close(struct sam_prefs *);
enum sam_state sam_prefs_save(struct sam_prefs *, enum sam_category, unsigned);
enum sam_state sam_desktop_pref(const struct sam_prefs *, const char *, char *, size_t,
    const char **source);

const struct sam_operation *sam_action(enum sam_action_slot);
const char *const *sam_action_vector(const struct sam_operation *, enum sam_state);
enum sam_revert_release sam_classify_revert_release(const struct sam_result *);
enum sam_revert_release sam_current_revert_release(void);
const char *sam_revert_reason(enum sam_revert_release);
enum sam_state sam_action_eligible(const struct sam_operation *, enum sam_revert_release);
void sam_read_page(enum sam_category, const struct sam_prefs *, struct sam_page_view *);
enum sam_state sam_spawn_page(const char *, enum sam_category, pid_t *, int *);
enum sam_state sam_spawn_check(const char *, const struct sam_operation *, pid_t *, int *);
enum sam_state sam_spawn_action(const struct sam_operation *, enum sam_state, pid_t *, int *);
int sam_gui_sandbox(const struct sam_prefs *, const char *);
int sam_gui(int, char **, bool settings, const char *self);

#endif
