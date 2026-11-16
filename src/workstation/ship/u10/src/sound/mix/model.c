#include "mix.h"

#include <stdio.h>
#include <string.h>

unsigned
mix_percent(unsigned value, unsigned maximum)
{
	return (unsigned)(((uint64_t)value * 100 + maximum / 2) / maximum);
}

static unsigned
raw_value(unsigned percent, unsigned maximum)
{
	return (unsigned)(((uint64_t)percent * maximum + 50) / 100);
}

static bool
bounded_text(const char *text, size_t cap)
{
	size_t i, len = strnlen(text, cap);

	if (len == cap)
		return false;
	for (i = 0; i < len; i++)
		if ((unsigned char)text[i] < 32 || (unsigned char)text[i] > 126)
			return false;
	return true;
}

static enum mix_role
role(const struct sioctl_desc *d)
{
	bool level = d->type == SIOCTL_NUM && d->maxval > 0 && !strcmp(d->func, "level");
	bool mute = d->type == SIOCTL_SW && d->maxval == 1 && !strcmp(d->func, "mute");

	if (level && (!strcmp(d->group, "app") || !strcmp(d->node0.name, "app")))
		return MIX_APP_LEVEL;
	if (d->group[0] != '\0')
		return MIX_NONE;
	if (!strcmp(d->node0.name, "output"))
		return level ? MIX_OUT_LEVEL : mute ? MIX_OUT_MUTE : MIX_NONE;
	if (!strcmp(d->node0.name, "input"))
		return level ? MIX_IN_LEVEL : mute ? MIX_IN_MUTE : MIX_NONE;
	if (d->type == SIOCTL_SEL && d->maxval == 1 && !strcmp(d->node0.name, "server") &&
	    !strcmp(d->func, "device"))
		return MIX_DEVICE;
	return MIX_NONE;
}

static void
invalidate(struct mix_model *m)
{
	unsigned i;

	m->generation++;
	m->ready = false;
	for (i = 0; i < MIX_CONTROLS; i++) {
		m->controls[i].queued = false;
		m->controls[i].inflight = false;
	}
}

void
mix_clear(struct mix_model *m)
{
	invalidate(m);
	memset(m->controls, 0, sizeof(m->controls));
	m->overflow = false;
}

static struct mix_control *
by_addr(struct mix_model *m, unsigned addr)
{
	unsigned i;

	for (i = 0; i < MIX_CONTROLS; i++)
		if (m->controls[i].role != MIX_NONE && m->controls[i].addr == addr)
			return &m->controls[i];
	return NULL;
}

static void
name_control(struct mix_control *c, const struct sioctl_desc *d)
{
	const struct sioctl_node *node = c->role == MIX_DEVICE ? &d->node1 : &d->node0;
	const char *fallback = c->role == MIX_DEVICE ? "Device" : "Application";

	if (d->display[0] != '\0' && bounded_text(d->display, sizeof(d->display))) {
		strlcpy(c->label, d->display, sizeof(c->label));
		return;
	}
	if (node->name[0] != '\0' && bounded_text(node->name, sizeof(node->name)))
		fallback = node->name;
	if (node->unit >= 0)
		snprintf(c->label, sizeof(c->label), "%s %d", fallback, node->unit);
	else
		strlcpy(c->label, fallback, sizeof(c->label));
}

void
mix_describe(struct mix_model *m, const struct sioctl_desc *d, int value)
{
	struct mix_control *c;
	enum mix_role kind = MIX_NONE;
	unsigned i;

	if (d == NULL) {
		m->ready = true;
		return;
	}
	if (m->ready)
		invalidate(m);
	c = by_addr(m, d->addr);
	if (bounded_text(d->group, sizeof(d->group)) &&
	    bounded_text(d->node0.name, sizeof(d->node0.name)) &&
	    bounded_text(d->func, sizeof(d->func)))
		kind = role(d);
	if (kind == MIX_NONE) {
		if (c != NULL)
			memset(c, 0, sizeof(*c));
		return;
	}
	if (c == NULL) {
		for (i = 0; i < MIX_CONTROLS && m->controls[i].role != MIX_NONE; i++)
			;
		if (i == MIX_CONTROLS) {
			m->overflow = true;
			return;
		}
		c = &m->controls[i];
	}
	memset(c, 0, sizeof(*c));
	c->role = kind;
	c->addr = d->addr;
	c->maximum = d->maxval;
	c->confirmed = value >= 0 && (unsigned)value <= d->maxval;
	if (c->confirmed)
		c->value = (unsigned)value;
	if (kind == MIX_DEVICE || kind == MIX_APP_LEVEL)
		name_control(c, d);
}

void
mix_confirm(struct mix_model *m, unsigned addr, unsigned value)
{
	struct mix_control *c = by_addr(m, addr);

	if (c != NULL && value <= c->maximum) {
		c->value = value;
		c->confirmed = true;
		c->inflight = false;
	}
}

struct mix_group
mix_group(const struct mix_model *m, enum mix_role role)
{
	struct mix_group g = { .confirmed = true };
	unsigned i, n = 0, sum = 0, first = 0;

	for (i = 0; i < MIX_CONTROLS; i++) {
		const struct mix_control *c = &m->controls[i];
		unsigned pct;

		if (c->role != role)
			continue;
		g.present = true;
		if (!c->confirmed) {
			g.confirmed = false;
			continue;
		}
		pct = mix_percent(c->value, c->maximum);
		if (n == 0)
			first = pct;
		else if (pct != first)
			g.mixed = true;
		sum += pct;
		n++;
		if (c->value != 0)
			g.muted = true;
	}
	if (n > 0)
		g.percent = (sum + n / 2) / n;
	g.confirmed = g.present && g.confirmed;
	return g;
}

static void
queue(struct mix_model *m, struct mix_control *c, unsigned raw)
{
	c->generation = m->generation;
	c->desired = raw;
	c->queued = c->value != raw || (c->inflight && c->sent != raw);
}

bool
mix_queue_group(struct mix_model *m, enum mix_role role, unsigned value)
{
	struct mix_group g = mix_group(m, role);
	unsigned i;

	if (!m->ready || m->overflow || !g.confirmed ||
	    (role != MIX_OUT_LEVEL && role != MIX_OUT_MUTE &&
	    role != MIX_IN_LEVEL && role != MIX_IN_MUTE))
		return false;
	if ((role == MIX_OUT_MUTE || role == MIX_IN_MUTE) ? value > 1 : value > 100)
		return false;
	for (i = 0; i < MIX_CONTROLS; i++) {
		struct mix_control *c = &m->controls[i];

		if (c->role == role)
			queue(m, c, role == MIX_OUT_MUTE || role == MIX_IN_MUTE ?
			    value : raw_value(value, c->maximum));
	}
	return true;
}

bool
mix_queue_control(struct mix_model *m, unsigned slot, unsigned value)
{
	struct mix_control *c;
	unsigned i;

	if (!m->ready || m->overflow || slot >= MIX_CONTROLS)
		return false;
	c = &m->controls[slot];
	if (!c->confirmed)
		return false;
	if (c->role == MIX_DEVICE && value == 1) {
		for (i = 0; i < MIX_CONTROLS; i++)
			if (m->controls[i].role == MIX_DEVICE)
				m->controls[i].queued = false;
		queue(m, c, 1);
		return true;
	}
	if (c->role == MIX_APP_LEVEL && value <= 100) {
		queue(m, c, raw_value(value, c->maximum));
		return true;
	}
	return false;
}

bool
mix_pending(const struct mix_model *m)
{
	unsigned i;

	if (m->ready && !m->overflow)
		for (i = 0; i < MIX_CONTROLS; i++)
			if (m->controls[i].queued && m->controls[i].generation == m->generation)
				return true;
	return false;
}

unsigned
mix_delay(const struct mix_model *m, uint64_t now)
{
	uint64_t elapsed = now - m->last_attempt;

	return m->attempted && elapsed < MIX_WRITE_MS ? MIX_WRITE_MS - (unsigned)elapsed : 0;
}

bool
mix_next(struct mix_model *m, uint64_t now, struct mix_request *request)
{
	unsigned i;

	if (mix_delay(m, now) != 0 || !mix_pending(m))
		return false;
	for (i = 0; i < MIX_CONTROLS; i++) {
		unsigned slot = (m->cursor + i) % MIX_CONTROLS;
		const struct mix_control *c = &m->controls[slot];

		if (!c->queued || c->generation != m->generation)
			continue;
		*request = (struct mix_request){ slot, c->addr, c->desired, m->generation };
		m->cursor = (slot + 1) % MIX_CONTROLS;
		m->last_attempt = now;
		m->attempted = true;
		return true;
	}
	return false;
}

void
mix_finish(struct mix_model *m, const struct mix_request *r, bool accepted)
{
	struct mix_control *c = &m->controls[r->slot];

	if (accepted && m->ready && r->generation == m->generation && c->addr == r->addr) {
		c->queued = c->desired != r->value;
		c->sent = r->value;
		c->inflight = c->value != r->value;
	}
}

bool
mix_preview_allowed(const struct mix_model *m)
{
	struct mix_group level = mix_group(m, MIX_OUT_LEVEL), mute = mix_group(m, MIX_OUT_MUTE);
	unsigned i;

	if (!m->ready || m->overflow || !level.confirmed || level.percent == 0 ||
	    !mute.confirmed || mute.muted)
		return false;
	for (i = 0; i < MIX_CONTROLS; i++) {
		const struct mix_control *c = &m->controls[i];

		if ((c->role == MIX_DEVICE && (c->queued || c->inflight)) ||
		    (c->role == MIX_OUT_MUTE && ((c->queued && c->desired) ||
		    (c->inflight && c->sent))) ||
		    (c->role == MIX_OUT_LEVEL && ((c->queued && c->desired == 0) ||
		    (c->inflight && c->sent == 0))))
			return false;
	}
	return true;
}
