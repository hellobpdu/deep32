#ifndef D32_MIX_H
#define D32_MIX_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <sndio.h>

enum { MIX_CONTROLS = 128, MIX_WRITE_MS = 50 };
enum mix_role {
	MIX_NONE, MIX_OUT_LEVEL, MIX_OUT_MUTE, MIX_IN_LEVEL, MIX_IN_MUTE,
	MIX_DEVICE, MIX_APP_LEVEL
};

struct mix_control {
	enum mix_role role;
	unsigned addr, maximum, value, desired, sent;
	uint64_t generation;
	bool confirmed, queued, inflight;
	char label[SIOCTL_DISPLAYMAX + SIOCTL_NAMEMAX + 16];
};

struct mix_model {
	struct mix_control controls[MIX_CONTROLS];
	uint64_t generation, last_attempt;
	unsigned cursor;
	bool ready, attempted, overflow;
};

struct mix_request {
	unsigned slot, addr, value;
	uint64_t generation;
};

struct mix_group {
	unsigned percent;
	bool present, confirmed, mixed, muted;
};

void mix_clear(struct mix_model *);
void mix_describe(struct mix_model *, const struct sioctl_desc *, int);
void mix_confirm(struct mix_model *, unsigned, unsigned);
struct mix_group mix_group(const struct mix_model *, enum mix_role);
bool mix_queue_group(struct mix_model *, enum mix_role, unsigned);
bool mix_queue_control(struct mix_model *, unsigned, unsigned);
bool mix_pending(const struct mix_model *);
unsigned mix_delay(const struct mix_model *, uint64_t);
bool mix_next(struct mix_model *, uint64_t, struct mix_request *);
void mix_finish(struct mix_model *, const struct mix_request *, bool);
bool mix_preview_allowed(const struct mix_model *);
unsigned mix_percent(unsigned, unsigned);

#endif
