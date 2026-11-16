/* openat.h: the literal Ask/Library URI contract. No decoding or filename construction. */
#ifndef E26_OPENAT_H
#define E26_OPENAT_H
#include <sys/types.h>
#define OPENAT_URI_MAX 662
typedef struct {
	char pack[14], article[513], anchor[129];
} openat_target;
int openat_parse(const char *uri, openat_target *target); /* 0 valid, -1 invalid */
/* Remove the single -open pair before Xt parses the remaining engine options. */
int openat_args(int *argc, char **argv, char uri[OPENAT_URI_MAX + 1]); /* 0 absent, 1 present, -1 invalid */
int openat_reader_allowed(uid_t uid, gid_t primary, const gid_t *groups, int n, gid_t reader);
int openat_private_allowed(void);
#endif
