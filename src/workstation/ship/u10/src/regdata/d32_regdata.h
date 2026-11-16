#ifndef D32_REGDATA_H
#define D32_REGDATA_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum d32_regdata_dataset {
	D32_REGDATA_IANA = 1,
	D32_REGDATA_MCAST,
	D32_REGDATA_NRO,
	D32_REGDATA_GEO
};

enum d32_regdata_kind {
	D32_REGDATA_INTERVAL,
	D32_REGDATA_SCOPE,
	D32_REGDATA_RELATIVE,
	D32_REGDATA_GROUP_ID
};

#define D32_REGDATA_NO_SCOPE 255
#define D32_REGDATA_MAX_ROWS 2000000U

struct d32_regdata;

/* Addresses and numeric IDs are inclusive, big-endian and zero extended. */
struct d32_regdata_row {
	uint8_t family, kind, flags, scope;
	const uint8_t *first, *last;
	const char *name, *registry, *status, *country, *date, *reference, *detail;
};

/*
 * dataset_fd is a trusted, installed dataset directory, borrowed for the call.
 * On success the caller owns *out. A missing, corrupt or unsupported CURRENT
 * is tried against PREVIOUS. An unavailable dataset leaves *out NULL and errno
 * set. All borrowed row pointers stay valid until the immutable table is freed.
 */
int d32_regdata_load_at(int, enum d32_regdata_dataset, struct d32_regdata **);
void d32_regdata_free(struct d32_regdata *);
size_t d32_regdata_count(const struct d32_regdata *);
bool d32_regdata_get(const struct d32_regdata *, size_t, struct d32_regdata_row *);
const char *d32_regdata_gen(const struct d32_regdata *);
const char *d32_regdata_source_date(const struct d32_regdata *);
bool d32_regdata_is_previous(const struct d32_regdata *);

#endif
