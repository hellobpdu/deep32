#include "sound.h"

#include <assert.h>
#include <stdio.h>

int
main(void)
{
	static const bool matrix[SND_PROFILES][SND_EVENTS] = {
		{ false, false, false }, { true, false, false },
		{ true, true, true }, { true, true, true }
	};
	struct snd_rate rate = { 0 };
	struct snd_rate storm = { 0 };
	unsigned admitted = 0;
	int p, e, i;

	assert(snd_profile_get(NULL) == SND_WORKSTATION);
	for (p = 0; p < SND_PROFILES; p++)
		for (e = 0; e < SND_EVENTS; e++)
			assert(snd_enabled(p, e) == matrix[p][e]);

	for (i = 0; i < 7; i++)
		assert(snd_rate_allow(&rate, 1000));
	assert(!snd_rate_allow(&rate, 1000));
	assert(!snd_rate_allow(&rate, 1142));
	assert(snd_rate_allow(&rate, 1143));
	for (i = 0; i < 200; i++)
		admitted += snd_rate_allow(&storm, 10000 + i * 10);
	assert(admitted == 14);
	assert(snd_rate_allow(&storm, 12000));
	assert(snd_local_display(":0") && snd_local_display("unix:123.0"));
	assert(!snd_local_display(NULL) && !snd_local_display(":") &&
	    !snd_local_display("remote:0") && !snd_local_display("tcp/127.0.0.1:0") &&
	    !snd_local_display(":0.") && !snd_local_display(":0.1.2") &&
	    !snd_local_display(":65536"));
	puts("PASS first-run profile, 12 profile-event combinations, local display, and 14/200 rolling limit");
	return 0;
}
