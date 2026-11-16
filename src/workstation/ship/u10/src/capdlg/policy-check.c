#include "policy.h"

#include <assert.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static void
put(int dir, const char *name, const char *bytes, size_t size, mode_t mode)
{
	int fd;

	(void)unlinkat(dir, name, 0);
	fd = openat(dir, name, O_WRONLY | O_CREAT | O_EXCL, 0600);
	assert(fd != -1 && write(fd, bytes, size) == (ssize_t)size);
	assert(fchmod(fd, mode) == 0 && close(fd) == 0);
}

#define PUT(d, n, s, m) put(d, n, s, sizeof(s) - 1, m)

int
main(void)
{
	static const char iface[] = "iface=vether0 lladdr=02:00:00:00:00:01 class=lab\n";
	static const char filters[] = "# synthetic test\nbench-dns=udp port 53\n";
	static const char nul[] = "bench-dns=udp\0 port 53\n";
	char name[] = "cap-policy.XXXXXXXX", too_long[600];
	struct cap_policy p;
	uid_t uid = getuid();
	int dir;

	assert(mkdtemp(name) != NULL);
	dir = open(name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
	assert(dir != -1);
	assert(cap_policy_load_at(dir, uid, &p) == CAP_POLICY_ABSENT);
	PUT(dir, "capture.conf", "# comment only\r\n", 0444);
	assert(cap_policy_load_at(dir, uid, &p) == CAP_POLICY_ABSENT && p.nifaces == 0);
	PUT(dir, "capture.conf", iface, 0444);
	assert(cap_policy_load_at(dir, uid, &p) == CAP_POLICY_ABSENT && p.nifaces == 0);
	PUT(dir, "capture-filters", filters, 0444);
	assert(cap_policy_load_at(dir, uid, &p) == CAP_POLICY_READY);
	assert(p.nifaces == 1 && p.nfilters == 1 && !strcmp(p.ifaces[0].name, "vether0"));
	assert(p.ifaces[0].lladdr[0] == 2 && p.ifaces[0].lladdr[5] == 1);
	assert(!strcmp(p.filters[0].id, "bench-dns") &&
	    !strcmp(p.filters[0].expression, "udp port 53"));

	PUT(dir, "capture-filters", "", 0444);
	assert(cap_policy_load_at(dir, uid, &p) == CAP_POLICY_READY && p.nfilters == 0);
	PUT(dir, "capture-filters", filters, 0644);
	assert(cap_policy_load_at(dir, uid, &p) == CAP_POLICY_INVALID && p.nifaces == 0);
	PUT(dir, "capture-filters", filters, 0444);
	assert(cap_policy_load_at(dir, uid == 0 ? 1 : 0, &p) == CAP_POLICY_INVALID);
	PUT(dir, "capture.conf", "iface=vether0 lladdr=02:00:00:00:00:01 class=work\n", 0444);
	assert(cap_policy_load_at(dir, uid, &p) == CAP_POLICY_INVALID && p.nifaces == 0);
	PUT(dir, "capture.conf", "iface=vether0 lladdr=01:00:00:00:00:01 class=lab\n", 0444);
	assert(cap_policy_load_at(dir, uid, &p) == CAP_POLICY_INVALID);
	PUT(dir, "capture.conf", "iface=-s lladdr=02:00:00:00:00:01 class=lab\n", 0444);
	assert(cap_policy_load_at(dir, uid, &p) == CAP_POLICY_INVALID);
	PUT(dir, "capture.conf",
	    "iface=vether0 lladdr=02:00:00:00:00:01 class=lab\n"
	    "iface=vether0 lladdr=02:00:00:00:00:02 class=lab\n", 0444);
	assert(cap_policy_load_at(dir, uid, &p) == CAP_POLICY_INVALID && p.nifaces == 0);
	PUT(dir, "capture.conf", iface, 0444);
	PUT(dir, "capture-filters", "none=ip\n", 0444);
	assert(cap_policy_load_at(dir, uid, &p) == CAP_POLICY_INVALID);
	PUT(dir, "capture-filters", "bench-dns=udp\nbench-dns=tcp\n", 0444);
	assert(cap_policy_load_at(dir, uid, &p) == CAP_POLICY_INVALID && p.nfilters == 0);
	PUT(dir, "capture-filters", nul, 0444);
	assert(cap_policy_load_at(dir, uid, &p) == CAP_POLICY_INVALID);
	memset(too_long, 'x', sizeof(too_long));
	put(dir, "capture-filters", too_long, sizeof(too_long), 0444);
	assert(cap_policy_load_at(dir, uid, &p) == CAP_POLICY_INVALID);
	assert(unlinkat(dir, "capture-filters", 0) == 0);
	assert(symlinkat("capture.conf", dir, "capture-filters") == 0);
	assert(cap_policy_load_at(dir, uid, &p) == CAP_POLICY_INVALID);
	assert(unlinkat(dir, "capture-filters", 0) == 0);
	assert(mkfifoat(dir, "capture-filters", 0444) == 0);
	assert(cap_policy_load_at(dir, uid, &p) == CAP_POLICY_INVALID);

	assert(cap_gate(CAP_POLICY_ABSENT, true, true, true, true) == CAP_NO_POLICY);
	assert(cap_gate(CAP_POLICY_INVALID, true, true, true, true) == CAP_BAD_POLICY);
	assert(cap_gate(CAP_POLICY_READY, false, true, true, true) == CAP_NO_BROKER);
	assert(cap_gate(CAP_POLICY_READY, true, false, true, true) == CAP_NO_VIEWER);
	assert(cap_gate(CAP_POLICY_READY, true, true, false, true) == CAP_NO_AUTHORITY);
	assert(cap_gate(CAP_POLICY_READY, true, true, true, false) == CAP_NO_LAB_INTERFACE);
	assert(cap_gate(CAP_POLICY_READY, true, true, true, true) == CAP_ALLOWED);

	assert(unlinkat(dir, "capture-filters", 0) == 0);
	assert(unlinkat(dir, "capture.conf", 0) == 0);
	assert(close(dir) == 0 && rmdir(name) == 0);
	puts("PASS bounded policy grammar, ownership, modes, duplicates, symlink/FIFO refusals and all seven gate states");
	return 0;
}
