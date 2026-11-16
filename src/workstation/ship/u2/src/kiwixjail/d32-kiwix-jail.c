/*
 * libd32kiwixjail.so: unveil(2) and pledge(2) for the package's kiwix-serve, which has neither (U28, update 2).
 *
 * kiwix-serve cannot be confined from outside by an exec wrapper alone: unveil does not survive execve, and pledge's
 * execpromises would have to include ld.so's needs for the whole start-up. So the confinement goes INSIDE the process,
 * through a preloaded library: ld.so maps it with the executable's own libraries, and its constructor runs before
 * kiwix-serve's main() has opened a ZIM or a socket. How it gets there: rc.subr runs every daemon through
 * "su -fl -c <class>", and since OpenBSD 7.x picks the login class named after the service when
 * /etc/login.conf.d/<service> defines one. /etc/login.conf.d/kiwix_serve (schg) sets
 * LD_PRELOAD=/usr/local/d32/lib/libd32kiwixjail.so for that class only. The package's rc.d script, its binary and
 * rc.conf.local stay as they are, so pkg_check stays clean and pkg_add -u cannot undo the confinement.
 *
 * The constructor:
 *   1. refuses to run in anything but kiwix-serve, and only as a non-root user (fail closed, before any work);
 *   2. removes LD_PRELOAD from the environment (nothing inherits it);
 *   3. unveils, read-only, the encyclopedia directory (D32_KIWIX_ZIMDIR, default /d32/libcontent/encyclopedia)
 *      and the read-only data kiwix-serve, libkiwix and ICU read after start (ICU's data, time zones), and locks
 *      unveil;
 *   4. pledges "stdio rpath inet proc": proc only because the package's rc.d starts "kiwix-serve -d", which forks
 *      once to detach. A pthread_atfork(3) child handler drops proc at that fork, so the serving daemon runs with
 *      "stdio rpath inet" and nothing else: no exec, no writes, no new files, no UNIX sockets, no further forks.
 * Any failure ends the process with status 78 and a syslog line (daemon.err); success logs one daemon.info line.
 * The network side is unchanged: --address=127.0.0.1 in kiwix_serve_flags, and pf's no-network user block for
 * _kiwix-serve (update 1, U28) keeps it off every network.
 *
 * Build (the build VM): cc -O2 -Wall -Wextra -Werror -fPIC -shared -o libd32kiwixjail.so d32-kiwix-jail.c -lpthread
 */
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <syslog.h>
#include <unistd.h>

#define ZIMDIR_DEFAULT	"/d32/libcontent/encyclopedia"
#define EXIT_JAIL	78

static const char *const ro_paths[] = {
	"/usr/local/share/icu",		/* ICU's common data file and its transliterator resources (search, suggest) */
	"/usr/share/zoneinfo",		/* localtime(3) after start */
	"/etc/localtime",
	NULL
};

static void
jail_fail(const char *what)
{
	int e = errno;

	syslog(LOG_DAEMON | LOG_ERR, "d32-kiwix-jail: %s: %s; kiwix-serve not started", what, strerror(e));
	dprintf(STDERR_FILENO, "d32-kiwix-jail: %s: %s\n", what, strerror(e));
	_exit(EXIT_JAIL);
}

static void
jail_child(void)
{
	/* the -d fork: the daemon that serves keeps no proc */
	if (pledge("stdio rpath inet", NULL) == -1)
		jail_fail("pledge after fork");
}

__attribute__((constructor))
static void
jail_init(void)
{
	const char *zimdir, *p;
	int i, n = 0;

	p = getprogname();
	if (p == NULL || strcmp(p, "kiwix-serve") != 0) {
		errno = EPERM;
		jail_fail("loaded into a program other than kiwix-serve");
	}
	if (getuid() == 0 || geteuid() == 0) {
		errno = EPERM;
		jail_fail("refusing to serve as root");
	}
	unsetenv("LD_PRELOAD");

	zimdir = getenv("D32_KIWIX_ZIMDIR");
	if (zimdir == NULL || *zimdir != '/')
		zimdir = ZIMDIR_DEFAULT;
	if (unveil(zimdir, "r") == -1)
		jail_fail("unveil of the ZIM directory");
	n++;
	for (i = 0; ro_paths[i] != NULL; i++) {
		if (unveil(ro_paths[i], "r") == -1 && errno != ENOENT)
			jail_fail("unveil of read-only data");
		n++;
	}
	if (unveil(NULL, NULL) == -1)
		jail_fail("unveil lock");
	if (pthread_atfork(NULL, NULL, jail_child) != 0) {
		errno = EAGAIN;
		jail_fail("pthread_atfork");
	}
	if (pledge("stdio rpath inet proc", NULL) == -1)
		jail_fail("pledge");
	syslog(LOG_DAEMON | LOG_INFO, "d32-kiwix-jail: unveil %d paths read-only (%s), locked; "
	    "pledge \"stdio rpath inet proc\", \"stdio rpath inet\" after the detach fork", n, zimdir);
}
