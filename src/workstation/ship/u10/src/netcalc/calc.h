#ifndef D32_CALC_H
#define D32_CALC_H

#include "d32netaddr.h"
#include "d32ui.h"
#include <limits.h>

enum calc_tab { CALC_ADDRESS = 1, CALC_PLAN, CALC_POLICY, CALC_MULTICAST,
	CALC_REGISTRIES, CALC_BASE, CALC_TAB_COUNT = CALC_BASE };

struct calc {
	d32ui *ui;
	Widget notebook, context_input, family_choice[2];
	Widget pages[CALC_TAB_COUNT + 1];
	enum calc_tab active;
	uint8_t family;
	d32_prefix context;
	d32_host_policy host_policy;
	bool donut;
	int plan_fd;
	char prefs_path[PATH_MAX], plan_path[PATH_MAX];
	struct d32_prefs *prefs;
	struct address_page *address;
	struct plan_page *plan;
	struct policy_page *policy;
	struct registry_pages *registry;
	struct registry_data *datasets;
};

Widget calc_address_page(struct calc *, Widget);
void calc_address_refresh(struct calc *);
Widget calc_plan_page(struct calc *, Widget);
void calc_plan_refresh(struct calc *);
void calc_plan_open(struct calc *);
void calc_plan_save(struct calc *);
void calc_plan_donut(struct calc *);
Widget calc_policy_page(struct calc *, Widget);
void calc_policy_refresh(struct calc *);
void calc_policy_family_changed(struct calc *);
void calc_policy_focus(struct calc *);
Widget calc_multicast_page(struct calc *, Widget);
Widget calc_registries_page(struct calc *, Widget);
void calc_registry_start(struct calc *, const char *);
void calc_registry_stop(struct calc *);
void calc_multicast_refresh(struct calc *);
void calc_registries_refresh(struct calc *);

/* Context is committed on Return or before an action that consumes it. */
bool calc_commit_context(struct calc *);
void calc_set_context(struct calc *, const d32_prefix *);
void calc_select_tab(struct calc *, enum calc_tab);

#endif
