/* openat.c: ASCII validation shared by argv, the local endpoint and ARTICLE. */
#include "openat.h"
#include <grp.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int alnum_ascii(unsigned char c)
{
	return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9');
}

int openat_parse(const char *uri, openat_target *target)
{
	openat_target t;
	const char *p, *slash, *hash;
	size_t n, k, pn, an;
	if (!uri || !target) return -1;
	n = strnlen(uri, OPENAT_URI_MAX + 1);
	if (n > OPENAT_URI_MAX || n < 10 || strncmp(uri, "library:", 8)) return -1;
	p = uri + 8; slash = strchr(p, '/');
	if (!slash || (pn = (size_t)(slash - p)) >= sizeof t.pack) return -1;
	memset(&t, 0, sizeof t); memcpy(t.pack, p, pn);
	if (strcmp(t.pack, "lib-e26") && strcmp(t.pack, "lib-corpus") && strcmp(t.pack, "lib-reserved")) return -1;
	p = slash + 1; hash = strchr(p, '#'); an = hash ? (size_t)(hash - p) : strlen(p);
	if (!an || an > 512 || !alnum_ascii((unsigned char)p[0])) return -1;
	for (k = 0; k < an; k++) if (!alnum_ascii((unsigned char)p[k]) && !strchr(":._()-", p[k])) return -1;
	memcpy(t.article, p, an);
	if (hash) {
		p = hash + 1; an = strlen(p);
		if (!an || an > 128 || p[0] < 'a' || p[0] > 'z') return -1;
		for (k = 0; k < an; k++) if (!((p[k] >= 'a' && p[k] <= 'z') || (p[k] >= '0' && p[k] <= '9')) && !strchr("._-", p[k])) return -1;
		memcpy(t.anchor, p, an);
	}
	*target = t;
	return 0;
}

int openat_args(int *argc, char **argv, char uri[OPENAT_URI_MAX + 1])
{
	int i, at = -1;
	openat_target t;
	if (!argc || !argv || !uri) return -1;
	uri[0] = 0;
	for (i = 1; i < *argc; i++) {
		if (!strncmp(argv[i], "-open=", 6)) return -1;
		if (strcmp(argv[i], "-open")) continue;
		if (at >= 0 || i + 1 >= *argc || openat_parse(argv[i + 1], &t)) return -1;
		at = i++;
	}
	if (at < 0) return 0;
	memcpy(uri, argv[at + 1], strlen(argv[at + 1]) + 1);
	for (i = at; i + 2 <= *argc; i++) argv[i] = argv[i + 2];
	*argc -= 2;
	return 1;
}

int openat_reader_allowed(uid_t uid, gid_t primary, const gid_t *groups, int n, gid_t reader)
{
	int i;
	(void)uid; /* No uid, including zero, bypasses the reader-group rule. */
	if (n < 0 || (n && !groups)) return 0;
	if (primary == reader) return 1;
	for (i = 0; i < n; i++) if (groups[i] == reader) return 1;
	return 0;
}

int openat_private_allowed(void)
{
	struct group *g = getgrnam("_d32users");
	gid_t reader, *groups = NULL;
	int n, allowed;
	if (!g) return 0;
	reader = g->gr_gid;
	n = getgroups(0, NULL);
	if (n < 0 || (n && !(groups = (gid_t *)calloc((size_t)n, sizeof *groups)))) return 0;
	if (n && getgroups(n, groups) != n) { free(groups); return 0; }
	allowed = openat_reader_allowed(geteuid(), getegid(), groups, n, reader);
	free(groups);
	return allowed;
}
