#ifndef D32_CAPTURE_POLICY_H
#define D32_CAPTURE_POLICY_H

#include <net/if.h>
#include <stdbool.h>
#include <stdint.h>
#include <sys/types.h>

enum { CAP_MAX_IFACES = 32, CAP_MAX_FILTERS = 32, CAP_FILTER_ID = 32,
	CAP_EXPRESSION = 512 };

struct cap_interface {
	char name[IF_NAMESIZE];
	uint8_t lladdr[6];
};

struct cap_filter {
	char id[CAP_FILTER_ID + 1];
	char expression[CAP_EXPRESSION + 1];
};

struct cap_policy {
	struct cap_interface ifaces[CAP_MAX_IFACES];
	struct cap_filter filters[CAP_MAX_FILTERS];
	unsigned nifaces, nfilters;
};

enum cap_policy_state { CAP_POLICY_READY, CAP_POLICY_ABSENT, CAP_POLICY_INVALID };
enum cap_gate {
	CAP_ALLOWED, CAP_NO_POLICY, CAP_BAD_POLICY, CAP_NO_BROKER, CAP_NO_VIEWER,
	CAP_NO_AUTHORITY, CAP_NO_LAB_INTERFACE
};

enum cap_policy_state cap_policy_load_at(int, uid_t, struct cap_policy *);
enum cap_policy_state cap_policy_load(struct cap_policy *);
bool cap_helper_present(const char *);
enum cap_gate cap_gate(enum cap_policy_state, bool, bool, bool, bool);
const char *cap_gate_text(enum cap_gate);

#endif
