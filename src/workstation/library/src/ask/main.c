/* d32library-ask: the Ask room's helper (item 41; W2-ASK builds it: the hub relay through the paired link, Codex and
 * Claude engines). d32library starts it as it starts the Library helpers (spawn.h,
 * descriptor 3), with imsg framing. This skeleton reads messages and closes at end of file. */
#include "../app/spawn.h"
#include <stdio.h>

int main(void)
{
	e26_spawn sp;
	if (spawn_child_open(&sp, spawn_child_fd())) return 1;
	for (;;) {
		char buf[256];
		uint32_t type;
		size_t len;
		int r = spawn_read(&sp), g;
		if (r <= 0) break;
		while ((g = spawn_get(&sp, &type, buf, sizeof buf, &len)) == 1) { }
		if (g < 0) break;
	}
	spawn_close(&sp);
	return 0;
}
