#include "d32netaddr.h"

#include <stdlib.h>
#include <string.h>

static uint8_t
bit_mask(int bit)
{
	return (uint8_t)(0x80U >> (bit % 8));
}

d32_net_status
d32_wildcard_match(const d32_addr *base, const d32_mask *wildcard,
    const d32_addr *candidate, d32_wild_result *out)
{
	d32_wild_result result = {0};
	d32_addr fixed = {0};
	int free_bit[2] = {-1, -1};
	unsigned nfree = 0;
	int width;

	if (base == NULL || wildcard == NULL || candidate == NULL || out == NULL)
		return D32_NET_ARG;
	width = d32_addr_width(base->fam);
	if (width <= 0 || wildcard->fam != base->fam || candidate->fam != base->fam)
		return D32_NET_FAMILY;

	fixed.fam = base->fam;
	result.first_failed_bit = -1;
	for (int bit = 0; bit < width; ++bit) {
		int byte = bit / 8;
		uint8_t mask = bit_mask(bit);
		if (wildcard->b[byte] & mask) {
			result.pattern[bit] = '?';
			free_bit[1] = free_bit[0];
			free_bit[0] = bit;
			++nfree;
			continue;
		}
		fixed.b[byte] |= base->b[byte] & mask;
		result.pattern[bit] = (base->b[byte] & mask) ? '1' : '0';
		if (result.first_failed_bit == -1 &&
		    ((base->b[byte] ^ candidate->b[byte]) & mask))
			result.first_failed_bit = bit;
	}
	result.matched = result.first_failed_bit == -1;
	result.count = d32_count_pow2(nfree);
	result.nsamples = 1U << (nfree < 2 ? nfree : 2);
	/* Only the two least significant free bits vary in the first four rows. */
	for (size_t i = 0; i < result.nsamples; ++i) {
		result.samples[i] = fixed;
		for (int slot = 0; slot < 2 && free_bit[slot] >= 0; ++slot) {
			int bit = free_bit[slot];
			if (i & (1U << slot))
				result.samples[i].b[bit / 8] |= bit_mask(bit);
		}
	}
	*out = result;
	return D32_NET_OK;
}

typedef struct {
	const char *start;
	size_t len;
} token;

typedef struct {
	d32_rule rule;
	size_t seq_column;
} parsed_rule;

static d32_net_status
rule_error(d32_net_error *err, d32_net_status code, size_t line, size_t column)
{
	if (err != NULL)
		*err = (d32_net_error){.code = code, .line = line,
		    .column = column, .bit = -1};
	return code;
}

static bool
horizontal_space(char c)
{
	return c == ' ' || c == '\t';
}

static bool
token_is(token t, const char *word)
{
	return t.len == strlen(word) && memcmp(t.start, word, t.len) == 0;
}

static bool
number(token t, uint64_t *value)
{
	uint64_t result = 0;

	if (t.len == 0)
		return false;
	for (size_t i = 0; i < t.len; ++i) {
		if (t.start[i] < '0' || t.start[i] > '9')
			return false;
		unsigned digit = (unsigned)(t.start[i] - '0');
		if (result > (UINT64_MAX - digit) / 10)
			return false;
		result = result * 10 + digit;
	}
	*value = result;
	return true;
}

static d32_net_status
rule_prefix(token t, uint8_t fam, d32_rule *rule, const char *line,
    size_t row, d32_net_error *err)
{
	char address[46];
	const char *slash = memchr(t.start, '/', t.len);
	const char *zone = memchr(t.start, '%', t.len);
	d32_net_error addr_error = {0};
	d32_net_status status;
	uint64_t length;
	size_t addrlen, column = (size_t)(t.start - line) + 1;

	if (slash == NULL)
		return rule_error(err, D32_NET_LENGTH, row, column + t.len);
	if (zone != NULL)
		return rule_error(err, D32_NET_ZONE, row, (size_t)(zone - line) + 1);
	addrlen = (size_t)(slash - t.start);
	if (addrlen >= sizeof(address))
		return rule_error(err, D32_NET_ADDRESS, row, column + sizeof(address) - 1);
	memcpy(address, t.start, addrlen);
	address[addrlen] = '\0';
	status = d32_addr_parse(address, &rule->prefix.addr, NULL, &addr_error);
	if (status != D32_NET_OK)
		return rule_error(err, status, row, column + addr_error.column - 1);
	if (rule->prefix.addr.fam != fam)
		return rule_error(err, D32_NET_FAMILY, row, column);
	if (!number((token){slash + 1, t.len - addrlen - 1}, &length) ||
	    length > (uint64_t)d32_addr_width(fam))
		return rule_error(err, D32_NET_LENGTH, row, column + addrlen + 1);
	rule->prefix.len = (uint8_t)length;
	return D32_NET_OK;
}

static d32_net_status
rule_modifiers(const token *fields, size_t nfields, uint8_t fam, bool lenient,
    d32_rule *rule, const char *line, const char *end, size_t row,
    d32_net_error *err)
{
	bool have_ge = false, have_le = false;
	unsigned width = (unsigned)d32_addr_width(fam);

	rule->minimum = rule->maximum = rule->prefix.len;
	for (size_t i = 4; i < nfields; i += 2) {
		bool ge = token_is(fields[i], "ge");
		size_t column = (size_t)(fields[i].start - line) + 1;
		uint64_t length;

		if ((ge && (have_ge || have_le)) ||
		    (!ge && (have_le || !token_is(fields[i], "le"))))
			return rule_error(err, D32_NET_RULE, row, column);
		if (i + 1 == nfields)
			return rule_error(err, D32_NET_LENGTH, row, (size_t)(end - line) + 1);
		column = (size_t)(fields[i + 1].start - line) + 1;
		if (!number(fields[i + 1], &length) || length > width ||
		    length < rule->prefix.len || (ge && !lenient && length == rule->prefix.len))
			return rule_error(err, D32_NET_LENGTH, row, column);
		if (ge) {
			have_ge = true;
			rule->minimum = (uint8_t)length;
			rule->maximum = (uint8_t)width;
		} else {
			have_le = true;
			rule->maximum = (uint8_t)length;
		}
		if (rule->minimum > rule->maximum)
			return rule_error(err, D32_NET_LENGTH, row, column);
	}
	return D32_NET_OK;
}

static d32_net_status
parse_rule(const char *line, const char *end, size_t row, uint8_t fam,
    bool lenient, parsed_rule *out, d32_net_error *err)
{
	token fields[8];
	size_t nfields = 0;
	d32_net_status status;
	parsed_rule result = {0};

	for (const char *p = line; p < end;) {
		if (horizontal_space(*p)) {
			++p;
			continue;
		}
		const char *start = p++;
		while (p < end && !horizontal_space(*p))
			++p;
		if (nfields == sizeof(fields) / sizeof(fields[0]))
			return rule_error(err, D32_NET_RULE, row, (size_t)(start - line) + 1);
		fields[nfields++] = (token){start, (size_t)(p - start)};
	}
	if (nfields < 4 || !token_is(fields[0], "seq"))
		return rule_error(err, D32_NET_RULE, row, 1);
	result.seq_column = (size_t)(fields[1].start - line) + 1;
	if (!number(fields[1], &result.rule.seq))
		return rule_error(err, D32_NET_RULE, row, result.seq_column);
	result.rule.permit = token_is(fields[2], "permit");
	if (!result.rule.permit && !token_is(fields[2], "deny"))
		return rule_error(err, D32_NET_RULE, row, (size_t)(fields[2].start - line) + 1);
	status = rule_prefix(fields[3], fam, &result.rule, line, row, err);
	if (status != D32_NET_OK)
		return status;
	status = rule_modifiers(fields, nfields, fam, lenient, &result.rule,
	    line, end, row, err);
	if (status != D32_NET_OK)
		return status;
	result.rule.line = row;
	*out = result;
	return D32_NET_OK;
}

static const char *
next_line(const char *end)
{
	if (*end == '\r')
		++end;
	if (*end == '\n')
		++end;
	return end;
}

static int
rule_order(const void *left, const void *right)
{
	const d32_rule *a = &((const parsed_rule *)left)->rule;
	const d32_rule *b = &((const parsed_rule *)right)->rule;

	if (a->seq != b->seq)
		return a->seq < b->seq ? -1 : 1;
	return (a->line > b->line) - (a->line < b->line);
}

d32_net_status
d32_prefix_list_parse(const char *text, uint8_t fam, bool lenient,
    d32_rule *rules, size_t cap, size_t *nrules, d32_net_error *err)
{
	parsed_rule local[8], *work = local;
	size_t n = 0, slots = 0, row = 1;
	d32_net_status status = D32_NET_OK;

	if (text == NULL || (rules == NULL && cap != 0) || nrules == NULL)
		return rule_error(err, D32_NET_ARG, 1, 1);
	if (d32_addr_width(fam) <= 0)
		return rule_error(err, D32_NET_FAMILY, 1, 1);
	/* The number of lines bounds temporary storage regardless of caller cap. */
	for (const char *p = text; *p != '\0' && slots < cap; ++slots)
		p = next_line(p + strcspn(p, "\r\n"));
	if (slots > sizeof(local) / sizeof(local[0])) {
		if (slots > SIZE_MAX / sizeof(*work) ||
		    (work = malloc(slots * sizeof(*work))) == NULL)
			return rule_error(err, D32_NET_NOMEM, 1, 1);
	}

	for (const char *line = text; *line != '\0'; ++row) {
		const char *end = line + strcspn(line, "\r\n");
		const char *start = line;
		while (start < end && horizontal_space(*start))
			++start;
		if (start < end) {
			if (n == cap) {
				status = rule_error(err, D32_NET_CAPACITY, row, 1);
				goto done;
			}
			status = parse_rule(line, end, row, fam, lenient, &work[n], err);
			if (status != D32_NET_OK)
				goto done;
			++n;
		}
		line = next_line(end);
	}
	qsort(work, n, sizeof(*work), rule_order);
	for (size_t i = 1; i < n; ++i) {
		if (work[i - 1].rule.seq == work[i].rule.seq) {
			status = rule_error(err, D32_NET_DUPLICATE, work[i].rule.line,
			    work[i].seq_column);
			goto done;
		}
	}
	for (size_t i = 0; i < n; ++i)
		rules[i] = work[i].rule;
	*nrules = n;
	if (err != NULL)
		*err = (d32_net_error){.code = D32_NET_OK, .bit = -1};
done:
	if (work != local)
		free(work);
	return status;
}

static d32_rule_trace
match_rule(const d32_rule *rule, const d32_prefix *candidate)
{
	d32_rule_trace row = {.seq = rule->seq};

	row.family_ok = candidate->addr.fam == rule->prefix.addr.fam;
	row.contained = d32_prefix_contains_prefix(&rule->prefix, candidate);
	row.length_ok = row.family_ok && candidate->len >= rule->minimum &&
	    candidate->len <= rule->maximum;
	row.matched = row.contained && row.length_ok;
	return row;
}

d32_net_status
d32_prefix_list_eval(const d32_rule *rules, size_t nrules,
    const d32_prefix *candidate, d32_rule_trace *trace, size_t cap,
    d32_rule_decision *out)
{
	d32_rule_decision result = {0};
	int width;

	if ((rules == NULL && nrules != 0) || candidate == NULL ||
	    (trace == NULL && cap != 0) || out == NULL)
		return D32_NET_ARG;
	width = d32_addr_width(candidate->addr.fam);
	if (width <= 0)
		return D32_NET_FAMILY;
	if (candidate->len > width)
		return D32_NET_LENGTH;
	for (size_t i = 0; i < nrules; ++i) {
		width = d32_addr_width(rules[i].prefix.addr.fam);
		if (width <= 0 || rules[i].prefix.len > rules[i].minimum ||
		    rules[i].minimum > rules[i].maximum || rules[i].maximum > width ||
		    (i != 0 && rules[i - 1].seq >= rules[i].seq))
			return D32_NET_RULE;
	}
	for (size_t i = 0; i < nrules; ++i) {
		d32_rule_trace row = match_rule(&rules[i], candidate);
		++result.ntrace;
		if (row.matched) {
			result.matched = true;
			result.permit = rules[i].permit;
			result.seq = row.seq;
			break;
		}
	}
	if (trace != NULL) {
		if (cap < result.ntrace)
			return D32_NET_CAPACITY;
		for (size_t i = 0; i < result.ntrace; ++i)
			trace[i] = match_rule(&rules[i], candidate);
	}
	*out = result;
	return D32_NET_OK;
}

d32_net_status
d32_multicast_mac(const d32_addr *group, uint8_t mac[6])
{
	uint8_t result[6];

	if (group == NULL || mac == NULL)
		return D32_NET_ARG;
	switch (group->fam) {
	case D32_IPV4:
		if ((group->b[0] & 0xf0) != 0xe0)
			return D32_NET_RANGE;
		result[0] = 0x01;
		result[1] = 0x00;
		result[2] = 0x5e;
		result[3] = group->b[1] & 0x7f;
		result[4] = group->b[2];
		result[5] = group->b[3];
		break;
	case D32_IPV6:
		if (group->b[0] != 0xff)
			return D32_NET_RANGE;
		result[0] = result[1] = 0x33;
		memcpy(result + 2, group->b + 12, 4);
		break;
	default:
		return D32_NET_FAMILY;
	}
	memcpy(mac, result, sizeof(result));
	return D32_NET_OK;
}

d32_net_status
d32_ipv4_groups_for_mac(const uint8_t mac[6], const d32_prefix *narrow,
    d32_addr groups[32], size_t *ngroups)
{
	d32_addr group = {.fam = D32_IPV4};
	size_t n = 0;

	if (mac == NULL || groups == NULL || ngroups == NULL)
		return D32_NET_ARG;
	if (mac[0] != 0x01 || mac[1] != 0x00 || mac[2] != 0x5e || (mac[3] & 0x80))
		return D32_NET_RANGE;
	if (narrow != NULL && narrow->addr.fam != D32_IPV4)
		return D32_NET_FAMILY;
	if (narrow != NULL && narrow->len > 32)
		return D32_NET_LENGTH;
	group.b[2] = mac[4];
	group.b[3] = mac[5];
	uint8_t low = mac[3];
	for (unsigned high = 0; high < 32; ++high) {
		group.b[0] = (uint8_t)(0xe0 | (high >> 1));
		group.b[1] = (uint8_t)(low | ((high & 1) << 7));
		if (narrow == NULL || d32_prefix_contains(narrow, &group))
			groups[n++] = group;
	}
	*ngroups = n;
	return D32_NET_OK;
}

d32_net_status
d32_solicited_node(const d32_addr *unicast, d32_addr *group, uint8_t mac[6])
{
	d32_addr result = {.fam = D32_IPV6,
	    .b = {0xff, 0x02, [11] = 0x01, [12] = 0xff}};

	if (unicast == NULL || group == NULL || mac == NULL)
		return D32_NET_ARG;
	if (unicast->fam != D32_IPV6)
		return D32_NET_FAMILY;
	if (unicast->b[0] == 0xff)
		return D32_NET_RANGE;
	memcpy(result.b + 13, unicast->b + 13, 3);
	*group = result;
	return d32_multicast_mac(&result, mac);
}

d32_net_status
d32_multicast_collision(const d32_addr *a, const d32_addr *b, bool solicited,
    bool *collision)
{
	d32_addr group;
	uint8_t first[6], second[6];
	d32_net_status status;

	if (collision == NULL)
		return D32_NET_ARG;
	status = solicited ? d32_solicited_node(a, &group, first) :
	    d32_multicast_mac(a, first);
	if (status != D32_NET_OK)
		return status;
	status = solicited ? d32_solicited_node(b, &group, second) :
	    d32_multicast_mac(b, second);
	if (status != D32_NET_OK)
		return status;
	*collision = d32_addr_compare(a, b) != 0 &&
	    memcmp(first, second, sizeof(first)) == 0;
	return D32_NET_OK;
}

void
d32_eui64(const uint8_t mac[6], uint8_t iid[8])
{
	uint8_t result[8] = {mac[0] ^ 0x02, mac[1], mac[2], 0xff, 0xfe,
	    mac[3], mac[4], mac[5]};
	memcpy(iid, result, sizeof(result));
}

d32_net_status
d32_eui64_mac(const uint8_t iid[8], uint8_t mac[6])
{
	if (iid == NULL || mac == NULL)
		return D32_NET_ARG;
	if (iid[3] != 0xff || iid[4] != 0xfe)
		return D32_NET_RANGE;
	uint8_t result[6] = {iid[0] ^ 0x02, iid[1], iid[2], iid[5], iid[6], iid[7]};
	memcpy(mac, result, sizeof(result));
	return D32_NET_OK;
}
