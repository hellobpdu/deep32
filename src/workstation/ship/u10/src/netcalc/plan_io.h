#ifndef D32_CALC_PLAN_IO_H
#define D32_CALC_PLAN_IO_H

#include "d32_error.h"
#include "d32netaddr.h"

#define CALC_PLAN_NAME_MAX 48
#define CALC_PLAN_VALUE_MAX 160

struct calc_plan_row {
	d32_plan_kind kind;
	char name[CALC_PLAN_NAME_MAX + 1], value[CALC_PLAN_VALUE_MAX + 1];
};

struct calc_plan_model {
	d32_prefix parent;
	d32_host_policy policy;
	size_t count;
	struct calc_plan_row rows[D32_PLAN_MAX_REQUESTS];
	d32_plan_request requests[D32_PLAN_MAX_REQUESTS];
	d32_plan_result result;
};

struct calc_plan_error {
	d32_net_status status;
	size_t row, column;
	bool name;
};

extern const char *const calc_plan_kind_labels[4];
extern const char *const calc_plan_policy_labels[4];

d32_net_status calc_plan_compute(struct calc_plan_model *, struct calc_plan_error *);
/* Read publishes a complete replay only on success. */
enum d32_error calc_plan_read(const char *, struct calc_plan_model *, struct calc_plan_error *);
enum d32_error calc_plan_write(const char *, const struct calc_plan_model *);
enum d32_error calc_plan_write_at(int, const char *, const struct calc_plan_model *);

#endif
