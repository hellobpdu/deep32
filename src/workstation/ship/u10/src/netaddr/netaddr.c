#include "d32netaddr.h"

#include <limits.h>
#include <stdlib.h>
#include <string.h>

const char *
d32_net_status_name(d32_net_status code)
{
	static const char *const names[] = {
		[D32_NET_OK] = "ok", [D32_NET_ARG] = "invalid argument",
		[D32_NET_FAMILY] = "invalid or mixed address family",
		[D32_NET_ADDRESS] = "invalid address syntax",
		[D32_NET_LEADING_ZERO] = "ambiguous leading zero",
		[D32_NET_LENGTH] = "invalid prefix length", [D32_NET_ZONE] = "invalid zone label",
		[D32_NET_MASK] = "invalid mask", [D32_NET_MASK_HOLE] = "noncontiguous mask",
		[D32_NET_RANGE] = "invalid range", [D32_NET_CAPACITY] = "output capacity exceeded",
		[D32_NET_RULE] = "invalid prefix-list rule", [D32_NET_DUPLICATE] = "duplicate sequence",
		[D32_NET_POLICY] = "invalid host policy", [D32_NET_OVERLAP] = "overlap",
		[D32_NET_OUTSIDE] = "outside parent", [D32_NET_LIMIT] = "work limit exceeded",
		[D32_NET_NOMEM] = "out of memory", [D32_NET_NOFIT] = "no fitting block"
	};

	if ((unsigned)code >= sizeof(names) / sizeof(names[0]))
		return "unknown network error";
	return names[code];
}

int
d32_addr_width(uint8_t fam)
{
	switch (fam) {
	case D32_IPV4: return 32;
	case D32_IPV6: return 128;
	default: return 0;
	}
}

int
d32_addr_compare(const d32_addr *a, const d32_addr *b)
{
	if (a->fam != b->fam)
		return a->fam < b->fam ? -1 : 1;
	int diff = memcmp(a->b, b->b, (size_t)d32_addr_width(a->fam) / 8);
	return (diff > 0) - (diff < 0);
}

static bool
addr_step(const d32_addr *a, d32_addr *out, int step)
{
	if (a == NULL || out == NULL)
		return false;
	int nbytes = d32_addr_width(a->fam) / 8;
	int edge = step > 0 ? UINT8_MAX : 0;
	for (int i = nbytes - 1; i >= 0; --i) {
		if (a->b[i] != edge) {
			*out = *a;
			out->b[i] = (uint8_t)(a->b[i] + step);
			memset(out->b + i + 1, UINT8_MAX - edge, (size_t)(nbytes - i - 1));
			return true;
		}
	}
	return false;
}

bool
d32_addr_next(const d32_addr *a, d32_addr *out)
{
	return addr_step(a, out, 1);
}

bool
d32_addr_prev(const d32_addr *a, d32_addr *out)
{
	return addr_step(a, out, -1);
}

bool
d32_prefix_valid(const d32_prefix *p)
{
	if (p == NULL)
		return false;
	int width = d32_addr_width(p->addr.fam);
	return width != 0 && p->len <= width;
}

static uint8_t
mask_octet(int bits)
{
	if (bits <= 0)
		return 0;
	if (bits >= 8)
		return UINT8_MAX;
	return (uint8_t)(UINT8_MAX << (8 - bits));
}

static d32_addr
prefix_end(const d32_prefix *p, bool last)
{
	d32_addr out = {0};
	if (!d32_prefix_valid(p))
		return out;
	out.fam = p->addr.fam;
	int nbytes = d32_addr_width(out.fam) / 8;
	for (int i = 0; i < nbytes; ++i) {
		uint8_t mask = mask_octet((int)p->len - i * 8);
		out.b[i] = last ? p->addr.b[i] | (uint8_t)~mask : p->addr.b[i] & mask;
	}
	return out;
}

d32_addr
d32_prefix_network(const d32_prefix *p)
{
	return prefix_end(p, false);
}

d32_addr
d32_prefix_last(const d32_prefix *p)
{
	return prefix_end(p, true);
}

bool
d32_prefix_has_host_bits(const d32_prefix *p)
{
	if (!d32_prefix_valid(p))
		return false;
	d32_addr network = d32_prefix_network(p);
	return d32_addr_compare(&network, &p->addr) != 0;
}

/* Arguments are same-family addresses. */
static unsigned
common_length(const d32_addr *a, const d32_addr *b)
{
	int nbytes = d32_addr_width(a->fam) / 8;
	unsigned bits = 0;
	for (int i = 0; i < nbytes; ++i, bits += 8) {
		unsigned diff = a->b[i] ^ b->b[i];
		if (diff != 0) {
			for (unsigned bit = 0x80; (diff & bit) == 0; bit >>= 1)
				++bits;
			break;
		}
	}
	return bits;
}

bool
d32_prefix_contains(const d32_prefix *p, const d32_addr *a)
{
	return d32_prefix_valid(p) && a != NULL && p->addr.fam == a->fam &&
	    common_length(&p->addr, a) >= p->len;
}

bool
d32_prefix_contains_prefix(const d32_prefix *outer, const d32_prefix *inner)
{
	return d32_prefix_valid(outer) && d32_prefix_valid(inner) &&
	    outer->len <= inner->len && d32_prefix_contains(outer, &inner->addr);
}

d32_net_status
d32_prefix_masks(const d32_prefix *p, d32_mask *mask, d32_mask *wildcard)
{
	if (!d32_prefix_valid(p) || (mask == NULL && wildcard == NULL) ||
	    (mask != NULL && mask == wildcard))
		return D32_NET_ARG;
	d32_mask m = {.fam = p->addr.fam}, w = {.fam = p->addr.fam};
	int nbytes = d32_addr_width(p->addr.fam) / 8;
	for (int i = 0; i < nbytes; ++i) {
		m.b[i] = mask_octet((int)p->len - i * 8);
		w.b[i] = (uint8_t)~m.b[i];
	}
	if (mask != NULL)
		*mask = m;
	if (wildcard != NULL)
		*wildcard = w;
	return D32_NET_OK;
}

d32_net_status
d32_prefix_usable(const d32_prefix *p, d32_host_policy policy, d32_range *out, bool *present)
{
	if (!d32_prefix_valid(p) || out == NULL || present == NULL)
		return D32_NET_ARG;
	if (policy != D32_HOST_RAW && p->addr.fam != D32_IPV4)
		return D32_NET_POLICY;
	d32_range range = {d32_prefix_network(p), d32_prefix_last(p)};
	switch (policy) {
	case D32_HOST_RAW:
		break;
	case D32_HOST_SUBNET:
		if (p->len >= 31) {
			*present = false;
			return D32_NET_OK;
		}
		d32_addr_next(&range.lo, &range.lo);
		d32_addr_prev(&range.hi, &range.hi);
		break;
	case D32_HOST_POINT_TO_POINT:
		if (p->len != 31)
			return D32_NET_POLICY;
		break;
	case D32_HOST_SINGLE:
		if (p->len != 32)
			return D32_NET_POLICY;
		break;
	default:
		return D32_NET_POLICY;
	}
	*out = range;
	*present = true;
	return D32_NET_OK;
}

d32_net_status
d32_prefix_split(const d32_prefix *p, uint8_t length, d32_prefix *out, size_t cap, size_t *used)
{
	if (!d32_prefix_valid(p) || used == NULL || (out == NULL && cap != 0))
		return D32_NET_ARG;
	if (length < p->len || length > d32_addr_width(p->addr.fam))
		return D32_NET_LENGTH;
	unsigned delta = length - p->len;
	if (delta >= sizeof(size_t) * CHAR_BIT) {
		*used = SIZE_MAX;
		return D32_NET_CAPACITY;
	}
	*used = (size_t)1 << delta;
	if (*used > cap)
		return D32_NET_CAPACITY;
	d32_prefix child = {d32_prefix_network(p), length};
	for (size_t i = 0; i < *used; ++i) {
		out[i] = child;
		if (i != *used - 1) {
			d32_addr end = d32_prefix_last(&child);
			d32_addr_next(&end, &child.addr);
		}
	}
	return D32_NET_OK;
}

d32_net_status
d32_prefix_join(const d32_prefix *a, const d32_prefix *b, d32_prefix *out)
{
	if (!d32_prefix_valid(a) || !d32_prefix_valid(b) || out == NULL)
		return D32_NET_ARG;
	if (a->addr.fam != b->addr.fam)
		return D32_NET_FAMILY;
	if (a->len == 0 || a->len != b->len || common_length(&a->addr, &b->addr) != a->len - 1U)
		return D32_NET_RANGE;
	d32_prefix parent = {a->addr, (uint8_t)(a->len - 1)};
	parent.addr = d32_prefix_network(&parent);
	*out = parent;
	return D32_NET_OK;
}

d32_count
d32_count_pow2(unsigned power)
{
	if (power < 64)
		return (d32_count){.lo = UINT64_C(1) << power};
	if (power < 128)
		return (d32_count){.hi = UINT64_C(1) << (power - 64)};
	return (d32_count){.full128 = power == 128};
}

static bool
count_valid(d32_count n)
{
	return !n.full128 || (n.hi == 0 && n.lo == 0);
}

int
d32_count_compare(d32_count a, d32_count b)
{
	if (a.full128 != b.full128)
		return a.full128 ? 1 : -1;
	if (a.hi != b.hi)
		return a.hi < b.hi ? -1 : 1;
	return (a.lo > b.lo) - (a.lo < b.lo);
}

bool
d32_count_add(d32_count a, d32_count b, d32_count *out)
{
	if (out == NULL || !count_valid(a) || !count_valid(b))
		return false;
	if (a.full128 || b.full128) {
		if ((a.full128 && b.full128) || a.hi != 0 || a.lo != 0 || b.hi != 0 || b.lo != 0)
			return false;
		*out = d32_count_pow2(128);
		return true;
	}
	d32_count sum = {.hi = a.hi + b.hi, .lo = a.lo + b.lo};
	bool overflow = sum.hi < a.hi;
	if (sum.lo < a.lo && ++sum.hi == 0)
		overflow = true;
	if (overflow && (sum.hi != 0 || sum.lo != 0))
		return false;
	sum.full128 = overflow;
	*out = sum;
	return true;
}

bool
d32_count_sub(d32_count a, d32_count b, d32_count *out)
{
	if (out == NULL || !count_valid(a) || !count_valid(b) || d32_count_compare(a, b) < 0)
		return false;
	if (b.full128) {
		*out = (d32_count){0};
		return true;
	}
	*out = (d32_count){.hi = a.hi - b.hi - (a.lo < b.lo), .lo = a.lo - b.lo,
	    .full128 = a.full128 && b.hi == 0 && b.lo == 0};
	return true;
}

static d32_net_status
range_status(const d32_range *r)
{
	if (r == NULL)
		return D32_NET_ARG;
	if (d32_addr_width(r->lo.fam) == 0 || r->lo.fam != r->hi.fam)
		return D32_NET_FAMILY;
	return d32_addr_compare(&r->lo, &r->hi) <= 0 ? D32_NET_OK : D32_NET_RANGE;
}

static d32_count
addr_value(const d32_addr *a)
{
	d32_count n = {0};
	int nbytes = d32_addr_width(a->fam) / 8;
	for (int i = 0; i < nbytes; ++i) {
		n.hi = (n.hi << 8) | (n.lo >> 56);
		n.lo = (n.lo << 8) | a->b[i];
	}
	return n;
}

d32_count
d32_range_count(const d32_range *r)
{
	d32_count n = {0};
	if (range_status(r) == D32_NET_OK) {
		d32_count_sub(addr_value(&r->hi), addr_value(&r->lo), &n);
		d32_count_add(n, d32_count_pow2(0), &n);
	}
	return n;
}

static unsigned
alignment_length(const d32_addr *a)
{
	int nbytes = d32_addr_width(a->fam) / 8;
	for (int i = nbytes - 1; i >= 0; --i) {
		unsigned byte = a->b[i];
		if (byte != 0) {
			unsigned length = (unsigned)(i + 1) * 8;
			for (; (byte & 1) == 0; byte >>= 1)
				--length;
			return length;
		}
	}
	return 0;
}

static size_t
range_blocks(const d32_range *r, d32_prefix *out)
{
	d32_addr start = r->lo;
	size_t used = 0;
	for (;;) {
		d32_prefix p = {start, (uint8_t)alignment_length(&start)};
		d32_addr end = d32_prefix_last(&p);
		if (d32_addr_compare(&end, &r->hi) > 0) {
			/* The highest differing bit bounds the aligned block. */
			p.len = (uint8_t)common_length(&start, &r->hi);
			end = d32_prefix_last(&p);
			if (d32_addr_compare(&end, &r->hi) > 0) {
				++p.len;
				end = d32_prefix_last(&p);
			}
		}
		if (out != NULL)
			out[used] = p;
		++used;
		if (d32_addr_compare(&end, &r->hi) == 0)
			return used;
		d32_addr_next(&end, &start);
	}
}

d32_net_status
d32_range_cover(const d32_range *r, d32_prefix *out, size_t cap, size_t *used)
{
	if (used == NULL || (out == NULL && cap != 0))
		return D32_NET_ARG;
	d32_net_status status = range_status(r);
	if (status != D32_NET_OK)
		return status;
	/* The maximum is known, so a full-size caller buffer needs only one pass. */
	if (cap >= D32_RANGE_COVER_MAX) {
		*used = range_blocks(r, out);
		return D32_NET_OK;
	}
	*used = range_blocks(r, NULL);
	if (*used > cap)
		return D32_NET_CAPACITY;
	range_blocks(r, out);
	return D32_NET_OK;
}

d32_net_status
d32_range_supernet(const d32_range *r, d32_prefix *out, d32_count *excess)
{
	if (out == NULL || excess == NULL)
		return D32_NET_ARG;
	d32_net_status status = range_status(r);
	if (status != D32_NET_OK)
		return status;
	d32_prefix p = {r->lo, (uint8_t)common_length(&r->lo, &r->hi)};
	p.addr = d32_prefix_network(&p);
	d32_count size = d32_count_pow2((unsigned)d32_addr_width(p.addr.fam) - p.len);
	d32_count_sub(size, d32_range_count(r), excess);
	*out = p;
	return D32_NET_OK;
}

static int
range_order(const void *left, const void *right)
{
	const d32_range *a = left, *b = right;
	return d32_addr_compare(&a->lo, &b->lo);
}

d32_net_status
d32_range_summary(const d32_range *ranges, size_t count, d32_prefix *out, size_t cap, size_t *used)
{
	if (used == NULL || (ranges == NULL && count != 0) || (out == NULL && cap != 0))
		return D32_NET_ARG;
	if (count == 0) {
		*used = 0;
		return D32_NET_OK;
	}
	if (count > SIZE_MAX / sizeof(*ranges) || count > SIZE_MAX / D32_RANGE_COVER_MAX)
		return D32_NET_LIMIT;
	for (size_t i = 0; i < count; ++i) {
		d32_net_status status = range_status(&ranges[i]);
		if (status != D32_NET_OK)
			return status;
		if (ranges[i].lo.fam != ranges[0].lo.fam)
			return D32_NET_FAMILY;
	}
	d32_range *sorted = malloc(count * sizeof(*sorted));
	if (sorted == NULL)
		return D32_NET_NOMEM;
	memcpy(sorted, ranges, count * sizeof(*sorted));
	qsort(sorted, count, sizeof(*sorted), range_order);
	size_t merged = 1;
	for (size_t i = 1; i < count; ++i) {
		d32_range *last = &sorted[merged - 1];
		d32_addr next;
		if (d32_addr_compare(&sorted[i].lo, &last->hi) <= 0 ||
		    (d32_addr_next(&last->hi, &next) && d32_addr_compare(&sorted[i].lo, &next) == 0)) {
			if (d32_addr_compare(&sorted[i].hi, &last->hi) > 0)
				last->hi = sorted[i].hi;
		} else {
			sorted[merged++] = sorted[i];
		}
	}
	*used = 0;
	for (size_t i = 0; i < merged; ++i)
		*used += range_blocks(&sorted[i], NULL);
	if (*used > cap) {
		free(sorted);
		return D32_NET_CAPACITY;
	}
	size_t offset = 0;
	for (size_t i = 0; i < merged; ++i)
		offset += range_blocks(&sorted[i], out + offset);
	free(sorted);
	return D32_NET_OK;
}
