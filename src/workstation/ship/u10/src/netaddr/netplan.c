#include "d32netaddr.h"

#include <stdlib.h>
#include <string.h>

/* Free prefixes are grouped by length, ordered by ascending address. */
struct free_queue {
	size_t first;
	size_t last;
};

struct free_block {
	d32_addr addr;
	size_t next;
};

struct demand {
	size_t row;
	uint8_t length;
};

struct plan_work {
	d32_plan_result result;
	d32_range occupied[D32_PLAN_MAX_REQUESTS];
	struct demand pending[D32_PLAN_MAX_REQUESTS];
	size_t occupied_count;
	size_t pending_count;
	struct free_queue by_length[129];
	struct free_block *blocks;
	size_t blocks_used;
	size_t blocks_capacity;
	size_t remaining;
	size_t failed;
};

static bool
charge(struct plan_work *work, size_t amount)
{
	if (amount > work->remaining)
		return false;
	work->remaining -= amount;
	return true;
}

static d32_net_status
child_length(const d32_prefix *parent, int length)
{
	if (length < parent->len || length > d32_addr_width(parent->addr.fam))
		return D32_NET_LENGTH;
	if (parent->addr.fam == D32_IPV6 && length - parent->len > 64)
		return D32_NET_LIMIT;
	return D32_NET_OK;
}

static d32_net_status
host_length(const d32_prefix *parent, const d32_plan_request *request,
    uint8_t *length)
{
	d32_count need = request->hosts;
	d32_host_policy policy = request->policy;
	uint64_t word;
	unsigned int bits = 0;
	int width = d32_addr_width(parent->addr.fam);

	if (need.full128 ? (need.hi != 0 || need.lo != 0) :
	    (need.hi == 0 && need.lo == 0))
		return D32_NET_ARG;
	if (parent->addr.fam == D32_IPV6) {
		if (policy != D32_HOST_RAW)
			return D32_NET_POLICY;
	} else {
		switch (policy) {
		case D32_HOST_SUBNET:
			if (need.full128 || need.hi != 0 || need.lo > UINT32_MAX - 1U)
				return D32_NET_NOFIT;
			need.lo += 2;
			break;
		case D32_HOST_POINT_TO_POINT:
			if (need.full128 || need.hi != 0 || need.lo > 2)
				return D32_NET_ARG;
			need.lo = 2;
			break;
		case D32_HOST_SINGLE:
			if (need.full128 || need.hi != 0 || need.lo != 1)
				return D32_NET_ARG;
			break;
		default:
			return D32_NET_POLICY;
		}
	}
	if (d32_count_compare(need, d32_count_pow2(width - parent->len)) > 0)
		return D32_NET_NOFIT;
	/* ceil(log2(need)) is the bit length of need minus one. */
	if (!d32_count_sub(need, d32_count_pow2(0), &need))
		return D32_NET_ARG;
	word = need.lo;
	if (need.hi != 0) {
		bits = 64;
		word = need.hi;
	}
	while (word != 0) {
		bits++;
		word >>= 1;
	}
	*length = (uint8_t)(width - bits);
	return child_length(parent, *length);
}

static d32_net_status
record_fixed(struct plan_work *work, const d32_range *range,
    const d32_prefix *parent)
{
	size_t at;

	if (range->lo.fam != parent->addr.fam || range->hi.fam != parent->addr.fam)
		return D32_NET_FAMILY;
	if (d32_addr_compare(&range->lo, &range->hi) > 0)
		return D32_NET_RANGE;
	if (!d32_prefix_contains(parent, &range->lo) ||
	    !d32_prefix_contains(parent, &range->hi))
		return D32_NET_OUTSIDE;
	for (at = 0; at < work->occupied_count; at++) {
		const d32_range *previous = &work->occupied[at];

		if (!charge(work, 1))
			return D32_NET_LIMIT;
		if (d32_addr_compare(&range->hi, &previous->lo) < 0)
			break;
		if (d32_addr_compare(&range->lo, &previous->hi) <= 0)
			return D32_NET_OVERLAP;
	}
	if (at < work->occupied_count)
		memmove(&work->occupied[at + 1], &work->occupied[at],
		    (work->occupied_count - at) * sizeof(*work->occupied));
	work->occupied[at] = *range;
	work->occupied_count++;
	return D32_NET_OK;
}

static d32_net_status
collect(struct plan_work *work, const d32_prefix *parent,
    const d32_plan_request *requests, size_t count)
{
	d32_net_status status;
	size_t i;

	for (i = 0; i < count; i++) {
		const d32_plan_request *request = &requests[i];
		d32_range range;

		work->failed = i;
		if (!charge(work, 1))
			return D32_NET_LIMIT;
		switch (request->kind) {
		case D32_PLAN_PIN:
			if (request->pin.addr.fam != parent->addr.fam)
				return D32_NET_FAMILY;
			status = child_length(parent, request->pin.len);
			if (status != D32_NET_OK)
				return status;
			if (d32_prefix_has_host_bits(&request->pin))
				return D32_NET_ADDRESS;
			range.lo = d32_prefix_network(&request->pin);
			range.hi = d32_prefix_last(&request->pin);
			work->result.placements[i].present = true;
			work->result.placements[i].prefix = request->pin;
			break;
		case D32_PLAN_RESERVE:
			range = request->reserve;
			break;
		case D32_PLAN_CHILD:
		case D32_PLAN_HOSTS:
			work->pending[work->pending_count++].row = i;
			continue;
		default:
			return D32_NET_ARG;
		}
		status = record_fixed(work, &range, parent);
		if (status != D32_NET_OK)
			return status;
	}
	for (i = 0; i < work->pending_count; i++) {
		struct demand demand = work->pending[i];
		const d32_plan_request *request = &requests[demand.row];
		size_t at = i;

		work->failed = demand.row;
		if (request->kind == D32_PLAN_CHILD) {
			demand.length = request->length;
			status = child_length(parent, demand.length);
		} else
			status = host_length(parent, request, &demand.length);
		if (status != D32_NET_OK)
			return status;
		/* Strict comparison preserves the input order of equal sizes. */
		while (at > 0) {
			if (!charge(work, 1))
				return D32_NET_LIMIT;
			if (work->pending[at - 1].length <= demand.length)
				break;
			work->pending[at] = work->pending[at - 1];
			at--;
		}
		work->pending[at] = demand;
	}
	work->failed = SIZE_MAX;
	return D32_NET_OK;
}

static d32_net_status
enqueue(struct plan_work *work, const d32_prefix *prefix)
{
	struct free_queue *queue = &work->by_length[prefix->len];
	size_t id;

	if (work->blocks_used == work->blocks_capacity) {
		size_t capacity = work->blocks_capacity ? work->blocks_capacity * 2 : 16;
		struct free_block *blocks = realloc(work->blocks,
		    capacity * sizeof(*blocks));

		/* Each insertion is charged, so capacity is bounded by twice the budget. */
		if (blocks == NULL)
			return D32_NET_NOMEM;
		work->blocks = blocks;
		work->blocks_capacity = capacity;
	}
	/* Use one-based IDs so the zero-initialized queues start empty. */
	id = ++work->blocks_used;
	work->blocks[id - 1] = (struct free_block){ prefix->addr, 0 };
	if (queue->last != 0)
		work->blocks[queue->last - 1].next = id;
	else
		queue->first = id;
	queue->last = id;
	return D32_NET_OK;
}

static d32_net_status
add_gap(struct plan_work *work, const d32_range *gap)
{
	d32_prefix blocks[D32_RANGE_COVER_MAX];
	size_t i, count;
	d32_net_status status;

	if (work->remaining == 0)
		return D32_NET_LIMIT;
	status = d32_range_cover(gap, blocks, sizeof(blocks) / sizeof(*blocks), &count);
	if (status != D32_NET_OK)
		return status;
	if (!charge(work, count))
		return D32_NET_LIMIT;
	for (i = 0; i < count; i++) {
		status = enqueue(work, &blocks[i]);
		if (status != D32_NET_OK)
			return status;
	}
	return D32_NET_OK;
}

static d32_net_status
build_free(struct plan_work *work, const d32_prefix *parent)
{
	d32_range gap = { .lo = parent->addr };
	d32_addr last = d32_prefix_last(parent);
	size_t i;
	d32_net_status status;

	for (i = 0; i < work->occupied_count; i++) {
		const d32_range *fixed = &work->occupied[i];

		if (!d32_count_sub(work->result.free_count, d32_range_count(fixed),
		    &work->result.free_count))
			return D32_NET_ARG;
		if (d32_addr_compare(&gap.lo, &fixed->lo) < 0) {
			if (!d32_addr_prev(&fixed->lo, &gap.hi))
				return D32_NET_ARG;
			status = add_gap(work, &gap);
			if (status != D32_NET_OK)
				return status;
		}
		if (d32_addr_compare(&fixed->hi, &last) == 0)
			return D32_NET_OK;
		if (!d32_addr_next(&fixed->hi, &gap.lo))
			return D32_NET_ARG;
	}
	gap.hi = last;
	return add_gap(work, &gap);
}

static d32_net_status
place(struct plan_work *work, const struct demand *demand,
    const d32_prefix *parent)
{
	struct free_queue *queue;
	const struct free_block *block;
	d32_prefix placed;
	int length;

	for (length = demand->length; length >= parent->len; length--) {
		if (!charge(work, 1))
			return D32_NET_LIMIT;
		if (work->by_length[length].first != 0)
			break;
	}
	if (length < parent->len)
		return D32_NET_NOFIT;
	queue = &work->by_length[length];
	block = &work->blocks[queue->first - 1];
	placed = (d32_prefix){ block->addr, (uint8_t)length };
	queue->first = block->next;
	if (queue->first == 0)
		queue->last = 0;
	while (placed.len < demand->length) {
		d32_prefix right = placed;
		d32_net_status status;

		if (!charge(work, 1))
			return D32_NET_LIMIT;
		right.addr.b[placed.len / 8] |= (uint8_t)(0x80U >> (placed.len % 8));
		placed.len++;
		right.len = placed.len;
		/* The best-fit search proved every intermediate queue was empty. */
		status = enqueue(work, &right);
		if (status != D32_NET_OK)
			return status;
	}
	work->result.placements[demand->row] = (d32_plan_placement){ true, placed };
	if (!d32_count_sub(work->result.free_count,
	    d32_count_pow2(d32_addr_width(parent->addr.fam) - placed.len),
	    &work->result.free_count))
		return D32_NET_ARG;
	return D32_NET_OK;
}

/* A budget unit covers a request, ordering comparison, free CIDR, bucket
 * probe or split. Width and request count bound the work within each unit. */
d32_net_status
d32_plan(const d32_prefix *parent, const d32_plan_request *requests,
    size_t count, size_t operation_limit, d32_plan_result *result, size_t *failed)
{
	struct plan_work work = { .remaining = operation_limit, .failed = SIZE_MAX };
	d32_net_status status;
	size_t i;
	int width, length;

	if (failed != NULL)
		*failed = SIZE_MAX;
	if (parent == NULL || result == NULL || (count != 0 && requests == NULL) ||
	    operation_limit > D32_PLAN_MAX_OPERATIONS)
		return D32_NET_ARG;
	if (!d32_prefix_valid(parent))
		return D32_NET_LENGTH;
	if (d32_prefix_has_host_bits(parent))
		return D32_NET_ADDRESS;
	if (count > D32_PLAN_MAX_REQUESTS) {
		if (failed != NULL)
			*failed = D32_PLAN_MAX_REQUESTS;
		return D32_NET_LIMIT;
	}
	width = d32_addr_width(parent->addr.fam);
	work.result.count = count;
	work.result.free_count = d32_count_pow2(width - parent->len);
	status = collect(&work, parent, requests, count);
	if (status != D32_NET_OK)
		goto done;
	status = build_free(&work, parent);
	if (status != D32_NET_OK)
		goto done;
	for (i = 0; i < work.pending_count; i++) {
		work.failed = work.pending[i].row;
		status = place(&work, &work.pending[i], parent);
		if (status != D32_NET_OK)
			goto done;
	}
	for (length = parent->len; length <= width; length++) {
		size_t first = work.by_length[length].first;

		if (first != 0) {
			work.result.has_largest = true;
			work.result.largest = (d32_prefix){ work.blocks[first - 1].addr,
			    (uint8_t)length };
			break;
		}
	}
	work.failed = SIZE_MAX;
	*result = work.result;
done:
	if (failed != NULL)
		*failed = work.failed;
	free(work.blocks);
	return status;
}
