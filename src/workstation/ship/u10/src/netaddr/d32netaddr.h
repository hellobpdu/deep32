#ifndef D32NETADDR_H
#define D32NETADDR_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define D32_IPV4 4
#define D32_IPV6 6
#define D32_ZONE_CAP 64
#define D32_ADDR_TEXT_CAP 40
#define D32_COUNT_TEXT_CAP 52
#define D32_RANGE_COVER_MAX 254
#define D32_PLAN_MAX_REQUESTS 512
#define D32_PLAN_MAX_OPERATIONS 2000000

/* Bytes are MSB first. IPv4 uses the first four bytes. */
typedef struct { uint8_t fam, b[16]; } d32_addr;
typedef struct { d32_addr addr; uint8_t len; } d32_prefix;
typedef struct { d32_addr lo, hi; } d32_range;
typedef struct { uint8_t fam, b[16]; } d32_mask;
/* full128 denotes exactly 2^128 and requires both limbs to be zero. */
typedef struct { uint64_t hi, lo; bool full128; } d32_count;

typedef enum {
	D32_NET_OK, D32_NET_ARG, D32_NET_FAMILY, D32_NET_ADDRESS,
	D32_NET_LEADING_ZERO, D32_NET_LENGTH, D32_NET_ZONE, D32_NET_MASK,
	D32_NET_MASK_HOLE, D32_NET_RANGE, D32_NET_CAPACITY, D32_NET_RULE,
	D32_NET_DUPLICATE, D32_NET_POLICY, D32_NET_OVERLAP, D32_NET_OUTSIDE,
	D32_NET_LIMIT, D32_NET_NOMEM, D32_NET_NOFIT
} d32_net_status;

typedef struct {
	d32_net_status code;
	size_t line, column; /* One-based, zero when unavailable. */
	int bit;             /* One-based mask bit, or -1. */
} d32_net_error;

typedef enum {
	D32_HOST_RAW, D32_HOST_SUBNET, D32_HOST_POINT_TO_POINT, D32_HOST_SINGLE
} d32_host_policy;

const char *d32_net_status_name(d32_net_status);
d32_net_status d32_addr_parse(const char *, d32_addr *, char *, d32_net_error *);
d32_net_status d32_prefix_parse(const char *, d32_prefix *, char *, d32_net_error *);
d32_net_status d32_range_parse(const char *, d32_range *, char *, char *, d32_net_error *);
d32_net_status d32_mask_parse(const char *, d32_mask *, d32_net_error *);
d32_net_status d32_mask_length(const d32_mask *, uint8_t *, int *);
d32_net_status d32_addr_format(const d32_addr *, char *, size_t);
d32_net_status d32_count_format(const d32_count *, bool, char *, size_t);

int d32_addr_width(uint8_t);
int d32_addr_compare(const d32_addr *, const d32_addr *);
bool d32_addr_next(const d32_addr *, d32_addr *);
bool d32_addr_prev(const d32_addr *, d32_addr *);
bool d32_prefix_valid(const d32_prefix *);
bool d32_prefix_has_host_bits(const d32_prefix *);
d32_addr d32_prefix_network(const d32_prefix *);
d32_addr d32_prefix_last(const d32_prefix *);
bool d32_prefix_contains(const d32_prefix *, const d32_addr *);
bool d32_prefix_contains_prefix(const d32_prefix *, const d32_prefix *);
d32_net_status d32_prefix_masks(const d32_prefix *, d32_mask *, d32_mask *);
d32_net_status d32_prefix_usable(const d32_prefix *, d32_host_policy, d32_range *, bool *);
d32_net_status d32_prefix_split(const d32_prefix *, uint8_t, d32_prefix *, size_t, size_t *);
d32_net_status d32_prefix_join(const d32_prefix *, const d32_prefix *, d32_prefix *);

d32_count d32_count_pow2(unsigned);
int d32_count_compare(d32_count, d32_count);
bool d32_count_add(d32_count, d32_count, d32_count *);
bool d32_count_sub(d32_count, d32_count, d32_count *);
d32_count d32_range_count(const d32_range *);
d32_net_status d32_range_cover(const d32_range *, d32_prefix *, size_t, size_t *);
d32_net_status d32_range_supernet(const d32_range *, d32_prefix *, d32_count *);
d32_net_status d32_range_summary(const d32_range *, size_t, d32_prefix *, size_t, size_t *);

typedef struct {
	bool matched;
	int first_failed_bit; /* Zero-based, or -1 on a match. */
	d32_count count;
	char pattern[129];
	size_t nsamples;
	d32_addr samples[4];
} d32_wild_result;

typedef struct {
	uint64_t seq;
	bool permit;
	d32_prefix prefix;
	uint8_t minimum, maximum;
	size_t line;
} d32_rule;

typedef struct {
	uint64_t seq;
	bool family_ok, contained, length_ok, matched;
} d32_rule_trace;

typedef struct {
	bool permit, matched;
	uint64_t seq;
	size_t ntrace;
} d32_rule_decision;

d32_net_status d32_wildcard_match(const d32_addr *, const d32_mask *, const d32_addr *, d32_wild_result *);
d32_net_status d32_prefix_list_parse(const char *, uint8_t, bool, d32_rule *, size_t, size_t *, d32_net_error *);
d32_net_status d32_prefix_list_eval(const d32_rule *, size_t, const d32_prefix *, d32_rule_trace *, size_t, d32_rule_decision *);
d32_net_status d32_multicast_mac(const d32_addr *, uint8_t mac[6]);
d32_net_status d32_ipv4_groups_for_mac(const uint8_t mac[6], const d32_prefix *, d32_addr groups[32], size_t *);
d32_net_status d32_solicited_node(const d32_addr *, d32_addr *, uint8_t mac[6]);
d32_net_status d32_multicast_collision(const d32_addr *, const d32_addr *, bool, bool *);
void d32_eui64(const uint8_t mac[6], uint8_t iid[8]);
d32_net_status d32_eui64_mac(const uint8_t iid[8], uint8_t mac[6]);

typedef enum { D32_PLAN_CHILD, D32_PLAN_HOSTS, D32_PLAN_RESERVE, D32_PLAN_PIN } d32_plan_kind;
typedef struct {
	d32_plan_kind kind;
	uint8_t length;
	d32_count hosts;
	d32_host_policy policy;
	d32_range reserve;
	d32_prefix pin;
} d32_plan_request;

typedef struct { bool present; d32_prefix prefix; } d32_plan_placement;
typedef struct {
	d32_plan_placement placements[D32_PLAN_MAX_REQUESTS];
	size_t count;
	d32_count free_count;
	bool has_largest;
	d32_prefix largest;
} d32_plan_result;

d32_net_status d32_plan(const d32_prefix *, const d32_plan_request *, size_t, size_t, d32_plan_result *, size_t *);

#endif
