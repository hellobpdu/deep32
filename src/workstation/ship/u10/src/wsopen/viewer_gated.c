#include "ws41.h"

enum d32_error
ws_backend_open(int *dirfd)
{
	*dirfd = -1;
	/* T42 must establish the nested X broker, offline profile and domain
	 * containment before an installed receiver may accept any bytes. */
	return D32E_UNAVAIL;
}

enum d32_error
ws_backend_view(int dirfd, const char *name, const volatile sig_atomic_t *cancelled)
{
	(void)dirfd;
	(void)name;
	(void)cancelled;
	return D32E_UNAVAIL;
}
