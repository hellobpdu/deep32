#ifndef D32_SAM_CATALOG_H
#define D32_SAM_CATALOG_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <time.h>

enum sam_class { SAM_READ, SAM_PREF, SAM_DELEGATE, SAM_TERMINAL };
enum sam_category { SAM_SUMMARY, SAM_SOFTWARE, SAM_STORAGE, SAM_SERVICES,
	SAM_BOOT, SAM_DESKTOP, SAM_CATEGORY_COUNT };
enum sam_state { SAM_OK, SAM_UNAVAILABLE, SAM_DENIED, SAM_INVALID, SAM_IO,
	SAM_PREVIEW };

#define SAM_VALUE_SIZE 16384u
#define SAM_RELEASE_SIZE 65u

/* A root other than / is a filesystem fixture. It never enables commands. */
struct sam_context { const char *root; };

struct sam_result {
	enum sam_state state;
	time_t read_at;
	const char *source;
	bool truncated;
	char value[SAM_VALUE_SIZE];
};

struct sam_operation {
	const char *id, *title;
	enum sam_class class;
	enum sam_category category;
	const char *authority;
	const char *const *argv;
	const char *unit, *source;
	enum sam_state (*probe)(const struct sam_context *, struct sam_result *);
};

const struct sam_operation *sam_catalog(size_t *);
const struct sam_operation *sam_find(const char *);
const char *sam_category_name(enum sam_category);
const char *sam_state_name(enum sam_state);
enum sam_state sam_probe(const struct sam_operation *, const struct sam_context *,
    struct sam_result *);
bool sam_visible(const struct sam_operation *, enum sam_state);
enum sam_state sam_command_preview(const struct sam_operation *, struct sam_result *);
enum sam_state sam_terminal_check(const struct sam_operation *);
void sam_audit(const struct sam_operation *, enum sam_state);

/* Bounded source parsers used by the probes and by synthetic tests. */
enum sam_state sam_release_parse(const void *, size_t, char[SAM_RELEASE_SIZE], uint64_t *);
enum sam_state sam_policy_answer(int, const void *, size_t);
enum sam_state sam_ready_answer(int, const void *, size_t, struct sam_result *);

/* Read-only helpers shared by the catalogue source files. */
enum sam_state sam_open_read(const struct sam_context *, const char *, bool, int *);
enum sam_state sam_open_protected(const char *, int *);
enum sam_state sam_error_from_errno(int);
bool sam_append(struct sam_result *, const char *, ...) __attribute__((__format__(__printf__, 2, 3)));
enum sam_state sam_system_summary(const struct sam_context *, struct sam_result *);
enum sam_state sam_disk_free(const struct sam_context *, struct sam_result *);
enum sam_state sam_package_list(const struct sam_context *, struct sam_result *);
enum sam_state sam_boot_ready(const struct sam_context *, struct sam_result *);

#endif
