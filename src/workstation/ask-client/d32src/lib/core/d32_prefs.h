#ifndef D32_PREFS_H
#define D32_PREFS_H

#include "d32_error.h"

enum {
	D32_PREFS_FILE_MAX = 8192,
	D32_PREFS_KEY_MAX = 48,
	D32_PREFS_VALUE_MAX = 512
};

#define D32_PREFS_NEWER_STATUS "Settings from a newer version \342\200\224 not saved"

struct d32_prefs;

/* Receives a valid key, or an empty string if no safe key can be read. */
typedef void (*d32_prefs_log_fn)(const char *, void *);
typedef enum d32_error (*d32_prefs_step_fn)(struct d32_prefs *, unsigned int,
    void *);

/*
 * dirfd is the caller's already-open, user-owned 0700 ~/.dt/d32 directory.
 * Open its path components without following links. The handle duplicates
 * dirfd. stem is the owning app's basename, such as d32calc or sam.
 * The on-disk header for d32calc is '# d32-calc v<N>'.
 *
 * OK includes a missing file (defaults). NEWER returns a defaults-only
 * handle. VERSION returns a readable older handle for explicit migration.
 * All other errors leave *out NULL. A handle belongs to one caller at a
 * time. Independent handles are safe to use from threads and processes.
 * Log callbacks may not reenter this API.
 */
enum d32_error d32_prefs_load(int, const char *, unsigned int,
    d32_prefs_log_fn, void *, struct d32_prefs **);
void d32_prefs_free(struct d32_prefs *);
unsigned int d32_prefs_version(const struct d32_prefs *);

/* The returned value is borrowed until the handle changes or is freed. */
const char *d32_prefs_get(const struct d32_prefs *, const char *, const char *);
enum d32_error d32_prefs_set(struct d32_prefs *, const char *, const char *);
enum d32_error d32_prefs_unset(struct d32_prefs *, const char *);
enum d32_error d32_prefs_save(struct d32_prefs *);

/*
 * On a clean handle, migrate a fresh on-disk older file one version at a
 * time. Each step may get/set/unset on its draft, then must return OK to
 * proceed. Steps must have no external effects and cannot save or migrate.
 * The original is backed up exactly once before publishing the result.
 */
enum d32_error d32_prefs_migrate(struct d32_prefs *, d32_prefs_step_fn, void *);

#endif
