/* e26-lib: the Library read service (W1-DATA; LIB-PROTOCOL.md, PACK-LAYOUT.md, docs/rooms/DATA.md).
 *
 *   e26-lib [-p packdir] [-e encyclopedia-dir]     serve E26P on descriptor 3 (E26_FD), as d32library starts it
 *   e26-lib -features                              print what this build reads (sqlite, zim, icu) and exit
 *
 * Beside the pack, the corpus pack behind /d32/libcontent/corpus opens when present (E26_LIB_CORPUS overrides,
 * "-" leaves it out; the reserved third slot opens only when E26_LIB_RESERVED names it); id-addressed queries reach them. PACK.json's
 * requires paths are under /d32/libcontent (E26_LIB_ROOT overrides), unveiled read-only.
 *
 * The pack defaults to /d32/libcontent/e26 (resolved once: a later pointer swap does not move a running helper),
 * the encyclopedia to /d32/libcontent/encyclopedia; E26_LIB_PACK and E26_LIB_ENC override both defaults. On OpenBSD
 * the helper unveils the two directories read-only, opens the pack, then pledges "stdio rpath": no writes, no
 * sockets, no exec. Every src/lib/q_<name>.c registers its named queries (lib_modules.gen.c); proto.c answers. */
#include "lib.h"
#include "document_route.h"
#include "../app/registry.h"
#include "../app/spawn.h"
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

void e26_lib_modules_register(void);
#ifdef __OpenBSD__
/* unistd.h declares these only for BSD-visible builds; the Makefile builds with _POSIX_C_SOURCE */
int pledge(const char *, const char *);
int unveil(const char *, const char *);
#endif

int main(int argc, char **argv)
{
	const char *pack = getenv("E26_LIB_PACK"), *enc = getenv("E26_LIB_ENC"), *root = getenv("E26_LIB_ROOT");
	const char *corpus = getenv("E26_LIB_CORPUS"), *reserved = getenv("E26_LIB_RESERVED");
	const char *cisco = getenv("E26_LIB_CISCO_CATALOGUE");
	static char rp[PATH_MAX], re[PATH_MAX], rr[PATH_MAX], rc1[PATH_MAX], rc2[PATH_MAX], cc[PATH_MAX];
	int i, rc, hp, he, hr, h1, h2, hc;
	if (!pack || !*pack) pack = "/d32/libcontent/e26";
	if (!enc || !*enc) enc = "/d32/libcontent/encyclopedia";
	if (!root || !*root) root = "/d32/libcontent";
	/* the corpus packs (PACK-CORPUS section 8): "-" leaves one out */
	if (!corpus || !*corpus) corpus = "/d32/libcontent/corpus";
	if (!reserved || !*reserved) reserved = "-";
	for (i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "-features")) { printf("e26-lib: %s\n", lib_features()); return 0; }
		if (!strcmp(argv[i], "-p") && i + 1 < argc) pack = argv[++i];
		else if (!strcmp(argv[i], "-e") && i + 1 < argc) enc = argv[++i];
		else { fprintf(stderr, "usage: e26-lib [-p packdir] [-e encyclopedia-dir] | -features\n"); return 2; }
	}
	signal(SIGPIPE, SIG_IGN);
	e26_lib_modules_register();
	/* ICU maps its normalisation data from /usr/local/share/icu on first use: fold once before unveil (fullwidth R,
	 * the fi ligature, e with acute) so the folds later need no file */
	free(lib_fold("\xEF\xBC\xB2\xEF\xAC\x81\xC3\xA9"));
	/* both paths resolved once, before any unveil (the first unveil already hides everything not unveiled): the
	 * public pointer /d32/libcontent/e26 becomes its generation directory for the life of this helper */
	hp = realpath(pack, rp) != NULL;
	he = realpath(enc, re) != NULL;
	hr = realpath(root, rr) != NULL;
	h1 = strcmp(corpus, "-") && realpath(corpus, rc1) != NULL;
	h2 = strcmp(reserved, "-") && realpath(reserved, rc2) != NULL;
	hc = realpath(cisco ? cisco : CISCO_CATALOGUE_PATH, cc) != NULL;
	/* the media packs PACK.json requires live under the libcontent root (an ietf directory, encyclopedia ZIMs) */
	if (hr) lib_set_root(rr);
#ifdef __OpenBSD__
	if (hp) unveil(rp, "r");
	if (he) unveil(re, "r");
	if (hr) unveil(rr, "r");
	if (h1) unveil(rc1, "r");
	if (h2) unveil(rc2, "r");
	if (hc && unveil(cc, "r") == -1) hc = 0;
	if (unveil(NULL, NULL) == -1) { perror("e26-lib: unveil"); return 1; }
#endif
	lib_open(hp ? rp : NULL, he ? re : NULL);
	if (h1) lib_open_extra(rc1);
	if (h2) lib_open_extra(rc2);
	lib_cisco_set_path(hc ? cc : NULL, cisco != NULL);
#ifdef __OpenBSD__
	if (pledge("stdio rpath", NULL) == -1) { perror("e26-lib: pledge"); return 1; }
#endif
	rc = lib_serve(spawn_child_fd());
	lib_cisco_set_path(NULL, 0);
	lib_close();
	return rc;
}
