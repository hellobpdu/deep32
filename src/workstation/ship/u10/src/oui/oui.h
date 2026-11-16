#ifndef D32_OUI_H
#define D32_OUI_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum oui_type { OUI_MAL = 1, OUI_MAM, OUI_MAS, OUI_IAB };
enum oui_result { OUI_OK = 0, OUI_MISS = 1, OUI_INVALID = 65, OUI_UNAVAILABLE = 69 };

#define OUI_ASSIGNEE_MAX 3u

struct oui_db;

struct oui_row {
	uint64_t key;
	unsigned width;
	enum oui_type type;
	const char *assignee; /* NULL for ambiguous assignments. */
	size_t assignee_count;
	const char *assignees[OUI_ASSIGNEE_MAX];
	char date[11];
};

struct oui_mac {
	uint64_t value;
	bool broadcast;
	bool group;
	bool local;
	const char *slap;
};

int oui_db_open(struct oui_db **, const char *);
int oui_db_open_default(struct oui_db **);
/* Open the private "oui" directory beneath a checked ~/.dt/d32 fd. */
int oui_db_open_local_at(struct oui_db **, int);
void oui_db_close(struct oui_db *);
const char *oui_db_generation(const struct oui_db *);
const char *oui_db_date(const struct oui_db *);
bool oui_db_fallback(const struct oui_db *);
bool oui_db_stale(const struct oui_db *);

int oui_parse_mac(const char *, struct oui_mac *);
int oui_parse_prefix(const char *, uint64_t *, unsigned *);
/* Assignee strings are borrowed until oui_db_close(). */
int oui_lookup(const struct oui_db *, const struct oui_mac *, struct oui_row *);
int oui_find_prefix(const struct oui_db *, uint64_t, unsigned, struct oui_row *);
size_t oui_search_org(const struct oui_db *, const char *, struct oui_row *, size_t);
const char *oui_type_name(enum oui_type);

#endif
