#ifndef D32_CALC_REGISTRY_DATA_H
#define D32_CALC_REGISTRY_DATA_H

#include "calc.h"
#include "d32_regdata.h"

struct calc_reg_row {
	struct d32_regdata_row value;
	uint8_t first[16], last[16];
};

struct calc_reg_view {
	const struct calc_reg_row *rows;
	size_t count;
	const char *generation, *date;
	bool previous;
};

bool calc_registry_loading(const struct calc *);
bool calc_registry_view(const struct calc *, enum d32_regdata_dataset,
    struct calc_reg_view *);
bool calc_geo_snapshot(const struct calc *, struct calc_reg_view *);

enum calc_geo_result {
	CALC_GEO_UNKNOWN, CALC_GEO_MIXED, CALC_GEO_COUNTRY, CALC_GEO_SPECIAL
};
enum calc_geo_result calc_geo_country(const struct calc *, const d32_range *, char[3]);

#endif
