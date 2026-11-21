/*
 * SPDX-FileCopyrightText: 2026 Jeffrey A. Cummings
 * SPDX-License-Identifier: Apache-2.0
 *
 * Fuzz target netaddr: the network calculator's address library (src/workstation/ship/u10/src/netaddr): parsing of
 * IPv4 and IPv6 addresses with zones, prefixes, masks, ranges and prefix lists, and the arithmetic built on them
 * (subnet split, cover, summary, usable hosts, wildcard match, multicast mapping, the address planner).
 *
 * Byte 0 picks the parser, byte 1 is an option byte, the rest is the text as a C string. Properties: a parsed
 * address formats and parses back to itself; a parsed prefix's network and last address are inside it; split and
 * cover results lie inside their source and never pass the capacity given; every count formats.
 */
#include "fuzz.h"
#include "d32netaddr.h"
#include <string.h>

static void check_addr(const d32_addr *a)
{
	char text[D32_ADDR_TEXT_CAP], zone[D32_ZONE_CAP];
	d32_addr back, next, prev;
	d32_net_error err;
	FUZZ_ASSERT(d32_addr_format(a, text, sizeof text) == D32_NET_OK);
	FUZZ_ASSERT(memchr(text, 0, sizeof text) != NULL);
	FUZZ_ASSERT(d32_addr_parse(text, &back, zone, &err) == D32_NET_OK);
	FUZZ_ASSERT(d32_addr_compare(a, &back) == 0);
	if (d32_addr_next(a, &next)) FUZZ_ASSERT(d32_addr_compare(a, &next) < 0);
	if (d32_addr_prev(a, &prev)) FUZZ_ASSERT(d32_addr_compare(&prev, a) < 0);
}

static void check_count(d32_count c)
{
	char text[D32_COUNT_TEXT_CAP];
	FUZZ_ASSERT(d32_count_format(&c, false, text, sizeof text) == D32_NET_OK);
	FUZZ_ASSERT(d32_count_format(&c, true, text, sizeof text) == D32_NET_OK);
}

static void check_prefix(const d32_prefix *p, uint8_t opt)
{
	d32_addr net, last;
	d32_mask mask, wild;
	d32_range usable;
	d32_prefix parts[64], joined;
	size_t used = 0, i;
	bool present;
	int pol;
	uint8_t len;

	if (!d32_prefix_valid(p)) return;
	net = d32_prefix_network(p);
	last = d32_prefix_last(p);
	FUZZ_ASSERT(d32_prefix_contains(p, &net) && d32_prefix_contains(p, &last));
	FUZZ_ASSERT(d32_addr_compare(&net, &last) <= 0);
	check_addr(&net);
	check_addr(&last);
	if (d32_prefix_masks(p, &mask, &wild) == D32_NET_OK) {
		uint8_t back;
		int bad = 0;
		FUZZ_ASSERT(d32_mask_length(&mask, &back, &bad) == D32_NET_OK && back == p->len);
	}
	for (pol = D32_HOST_RAW; pol <= D32_HOST_SINGLE; pol++)
		if (d32_prefix_usable(p, (d32_host_policy)pol, &usable, &present) == D32_NET_OK && present) {
			FUZZ_ASSERT(d32_addr_compare(&usable.lo, &usable.hi) <= 0);
			check_count(d32_range_count(&usable));
		}
	len = (uint8_t)(p->len + opt % 8);
	if (d32_prefix_split(p, len, parts, sizeof parts / sizeof parts[0], &used) == D32_NET_OK) {
		FUZZ_ASSERT(used <= sizeof parts / sizeof parts[0]);
		for (i = 0; i < used; i++) FUZZ_ASSERT(d32_prefix_contains_prefix(p, &parts[i]));
		if (used == 2) FUZZ_ASSERT(d32_prefix_join(&parts[0], &parts[1], &joined) == D32_NET_OK);
	}
	check_count(d32_count_pow2((unsigned)(d32_addr_width(p->addr.fam) - p->len)));
}

static void check_range(const d32_range *r)
{
	d32_prefix out[D32_RANGE_COVER_MAX], super;
	d32_count excess;
	size_t used = 0, i;
	check_count(d32_range_count(r));
	if (d32_range_cover(r, out, D32_RANGE_COVER_MAX, &used) == D32_NET_OK) {
		FUZZ_ASSERT(used >= 1 && used <= D32_RANGE_COVER_MAX);
		for (i = 0; i < used; i++) {
			d32_addr lo = d32_prefix_network(&out[i]), hi = d32_prefix_last(&out[i]);
			FUZZ_ASSERT(d32_addr_compare(&r->lo, &lo) <= 0 && d32_addr_compare(&hi, &r->hi) <= 0);
		}
	}
	if (d32_range_supernet(r, &super, &excess) == D32_NET_OK) {
		FUZZ_ASSERT(d32_prefix_contains(&super, &r->lo) && d32_prefix_contains(&super, &r->hi));
		check_count(excess);
	}
	(void)d32_range_summary(r, 1, out, D32_RANGE_COVER_MAX, &used);
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
	char *text;
	uint8_t mode, opt;
	d32_net_error err;
	char zone[D32_ZONE_CAP], zone2[D32_ZONE_CAP];

	if (size < 2) return 0;
	mode = data[0] % 7;
	opt = data[1];
	text = fuzz_cstr(data + 2, size - 2);
	switch (mode) {
	case 0: {
		d32_addr a;
		if (d32_addr_parse(text, &a, zone, &err) == D32_NET_OK) {
			uint8_t mac[6];
			d32_addr group;
			bool collide;
			FUZZ_ASSERT(memchr(zone, 0, sizeof zone) != NULL);
			check_addr(&a);
			(void)d32_multicast_mac(&a, mac);
			if (d32_solicited_node(&a, &group, mac) == D32_NET_OK) check_addr(&group);
			(void)d32_multicast_collision(&a, &a, opt & 1, &collide);
		}
		break;
	}
	case 1: {
		d32_prefix p;
		if (d32_prefix_parse(text, &p, zone, &err) == D32_NET_OK) check_prefix(&p, opt);
		break;
	}
	case 2: {
		d32_range r;
		if (d32_range_parse(text, &r, zone, zone2, &err) == D32_NET_OK) {
			FUZZ_ASSERT(d32_addr_compare(&r.lo, &r.hi) <= 0);
			check_range(&r);
		}
		break;
	}
	case 3: {
		d32_mask m;
		if (d32_mask_parse(text, &m, &err) == D32_NET_OK) {
			uint8_t len;
			int bad;
			d32_addr base = { m.fam, { 0 } }, probe = { m.fam, { 0 } };
			d32_wild_result wr;
			(void)d32_mask_length(&m, &len, &bad);
			probe.b[0] = opt;
			if (d32_wildcard_match(&base, &m, &probe, &wr) == D32_NET_OK) {
				FUZZ_ASSERT(wr.nsamples <= 4);
				FUZZ_ASSERT(memchr(wr.pattern, 0, sizeof wr.pattern) != NULL);
				check_count(wr.count);
			}
		}
		break;
	}
	case 4: {
		d32_rule rules[32];
		d32_rule_trace trace[32];
		size_t nrules = 0;
		uint8_t fam = (opt & 1) ? D32_IPV6 : D32_IPV4;
		if (d32_prefix_list_parse(text, fam, (opt & 2) != 0, rules, 32, &nrules, &err) == D32_NET_OK) {
			d32_prefix cand = { { fam, { 0 } }, (uint8_t)(opt % 33) };
			d32_rule_decision dec;
			size_t i;
			FUZZ_ASSERT(nrules <= 32);
			for (i = 0; i < nrules; i++) FUZZ_ASSERT(rules[i].minimum <= rules[i].maximum);
			if (nrules) cand = rules[opt % nrules].prefix;
			if (d32_prefix_list_eval(rules, nrules, &cand, trace, 32, &dec) == D32_NET_OK)
				FUZZ_ASSERT(dec.ntrace <= nrules);
		}
		break;
	}
	case 5: {
		/* the planner: the first line is the parent prefix, then one request per line:
		 * "c LEN", "h HOSTS POLICY", "r RANGE" or "p PREFIX" */
		d32_prefix parent;
		d32_plan_request req[16];
		static d32_plan_result res;
		size_t count = 0, failed;
		char *line = text, *nl;
		nl = strchr(line, '\n');
		if (nl) *nl = 0;
		if (d32_prefix_parse(line, &parent, zone, &err) != D32_NET_OK) break;
		while (nl && count < 16) {
			d32_plan_request *q = &req[count];
			line = nl + 1;
			nl = strchr(line, '\n');
			if (nl) *nl = 0;
			memset(q, 0, sizeof *q);
			if (line[0] == 'c') { q->kind = D32_PLAN_CHILD; q->length = (uint8_t)atoi(line + 1); }
			else if (line[0] == 'h') {
				char *e;
				q->kind = D32_PLAN_HOSTS;
				q->hosts.lo = strtoull(line + 1, &e, 10);
				q->policy = (d32_host_policy)(strtoul(e, NULL, 10) % 4);
			} else if (line[0] == 'r') {
				q->kind = D32_PLAN_RESERVE;
				if (d32_range_parse(line + 1, &q->reserve, zone, zone2, &err) != D32_NET_OK) continue;
			} else if (line[0] == 'p') {
				q->kind = D32_PLAN_PIN;
				if (d32_prefix_parse(line + 1, &q->pin, zone, &err) != D32_NET_OK) continue;
			} else continue;
			count++;
		}
		if (d32_plan(&parent, req, count, 20000, &res, &failed) == D32_NET_OK) {
			size_t i;
			FUZZ_ASSERT(res.count == count);
			for (i = 0; i < count; i++)
				if (res.placements[i].present)
					FUZZ_ASSERT(d32_prefix_contains_prefix(&parent, &res.placements[i].prefix));
			check_count(res.free_count);
		}
		break;
	}
	case 6: {
		/* EUI-64 and the IPv4 multicast groups of a MAC address taken from the option byte and the text */
		uint8_t mac[6], iid[8], back[6];
		d32_addr groups[32];
		size_t ng = 0, i;
		d32_prefix narrow;
		for (i = 0; i < 6; i++) mac[i] = i < size - 2 ? data[2 + i] : opt;
		d32_eui64(mac, iid);
		if (d32_eui64_mac(iid, back) == D32_NET_OK) FUZZ_ASSERT(memcmp(mac, back, 6) == 0);
		mac[0] = 0x01; mac[1] = 0x00; mac[2] = 0x5e;
		if (d32_prefix_parse(size > 8 ? text + 6 : "224.0.0.0/4", &narrow, zone, &err) != D32_NET_OK)
			narrow = (d32_prefix){ { D32_IPV4, { 224 } }, 4 };
		if (d32_ipv4_groups_for_mac(mac, &narrow, groups, &ng) == D32_NET_OK) {
			FUZZ_ASSERT(ng <= 32);
			for (i = 0; i < ng; i++) check_addr(&groups[i]);
		}
		break;
	}
	}
	free(text);
	return 0;
}
