#define _POSIX_C_SOURCE 200809L

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sysexits.h>
#include <unistd.h>

#ifndef D32_DTCALC_CATALOG
#define D32_DTCALC_CATALOG "/usr/local/d32/share/data/dtcalc/dtcalc.cat"
#endif

int
main(int argc, char **argv)
{
	char *const args[] = { "/usr/local/dt/bin/dtcalc", NULL };
	const char *locale = getenv("LC_ALL");

	(void)argv;
	if (argc != 1) {
		fputs("usage: d32-dtcalc\n", stderr);
		return EX_USAGE;
	}
	if (locale == NULL || *locale == '\0')
		locale = getenv("LC_MESSAGES");
	if (locale == NULL || *locale == '\0')
		locale = getenv("LANG");
	if (locale == NULL || *locale == '\0')
		locale = "C";
	if ((strcmp(locale, "C") == 0 || strcmp(locale, "POSIX") == 0 ||
	    strcmp(locale, "en_US.UTF-8") == 0) &&
	    setenv("NLSPATH", D32_DTCALC_CATALOG, 1) == -1) {
		perror("d32-dtcalc: NLSPATH");
		return EX_OSERR;
	}
	execv(args[0], args);
	perror("d32-dtcalc: /usr/local/dt/bin/dtcalc");
	return EX_UNAVAILABLE;
}
