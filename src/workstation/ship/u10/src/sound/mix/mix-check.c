#include "mix.h"
#include "support.h"

#include <assert.h>
#include <stdio.h>

int
main(void)
{
	struct mix_model m = { 0 };
	struct mix_request req;
	struct mix_group group;
	struct sioctl_desc left = { .addr = 11, .type = SIOCTL_NUM, .maxval = 127,
	    .func = "level", .node0 = { .name = "output", .unit = 0 } };
	struct sioctl_desc right = { .addr = 12, .type = SIOCTL_NUM, .maxval = 255,
	    .func = "level", .node0 = { .name = "output", .unit = 1 } };
	struct sioctl_desc mute = { .addr = 20, .type = SIOCTL_SW, .maxval = 1,
	    .func = "mute", .node0 = { .name = "output", .unit = -1 } };
	struct sioctl_desc device = { .addr = 30, .type = SIOCTL_SEL, .maxval = 1,
	    .func = "device", .node0 = { .name = "server", .unit = -1 },
	    .node1 = { .name = "snd", .unit = 0 }, .display = "Synthetic device" };
	struct sioctl_desc stream = { .addr = 40, .type = SIOCTL_NUM, .maxval = 100,
	    .func = "level", .group = "app", .node0 = { .name = "fixture", .unit = -1 } };
	unsigned i;

	mix_describe(&m, &left, 64);
	mix_describe(&m, &right, 128);
	mix_describe(&m, &mute, 0);
	assert(!mix_queue_group(&m, MIX_OUT_LEVEL, 40));
	mix_describe(&m, NULL, 0);
	assert(mix_preview_allowed(&m));
	assert(mix_queue_group(&m, MIX_OUT_LEVEL, 80));
	assert(mix_queue_group(&m, MIX_OUT_LEVEL, 40));
	assert(mix_next(&m, 100, &req) && req.addr == 11 && req.value == 51);
	mix_finish(&m, &req, true);
	assert(mix_group(&m, MIX_OUT_LEVEL).percent == 50);
	mix_confirm(&m, 11, 51);
	group = mix_group(&m, MIX_OUT_LEVEL);
	assert(group.percent == 45 && group.mixed);
	assert(!mix_next(&m, 149, &req));
	assert(mix_next(&m, 150, &req) && req.addr == 12 && req.value == 102);
	mix_finish(&m, &req, false);
	assert(mix_pending(&m));
	/* A new descriptor transaction invalidates both queued and sent state. */
	mix_describe(&m, &device, 0);
	assert(!mix_pending(&m) && !mix_preview_allowed(&m));
	mix_describe(&m, &stream, 60);
	mix_describe(&m, NULL, 0);
	mix_finish(&m, &req, true);
	assert(!mix_next(&m, 200, &req));
	assert(mix_queue_group(&m, MIX_OUT_MUTE, 1) && !mix_preview_allowed(&m));
	assert(mix_next(&m, 200, &req) && req.addr == 20 && req.value == 1);
	mix_finish(&m, &req, true);
	assert(!mix_preview_allowed(&m));
	mix_confirm(&m, 20, 1);
	assert(!mix_preview_allowed(&m));
	mix_confirm(&m, 20, 0);
	assert(mix_preview_allowed(&m));
	for (i = 0; i < MIX_CONTROLS && m.controls[i].role != MIX_DEVICE; i++)
		;
	assert(i < MIX_CONTROLS && mix_queue_control(&m, i, 1));
	assert(!mix_preview_allowed(&m));
	mix_clear(&m);
	assert(!mix_pending(&m) && !mix_preview_allowed(&m));
	assert(mix_local_device(NULL) && mix_local_device("snd,4/loop") &&
	    mix_local_device("rsnd/0") && !mix_local_device("snd@remote/default") &&
	    !mix_local_device("snd/../default"));
	puts("PASS confirmation, coalescing, 50ms pace, generation invalidation, mute/selection and local endpoints");
	return 0;
}
