#ifndef OUI_GUI_PROVISION_H
#define OUI_GUI_PROVISION_H

#include <sys/types.h>

#include <limits.h>
#include <stdbool.h>

#include "d32_error.h"

/* Discover trusted tools and a local chooser pattern before applying unveil. */
bool oui_provision_available(char pattern[PATH_MAX]);
/* Apply or revoke exactly the setup grants before locking unveil. */
int oui_provision_unveil(bool remove);
/* On success the caller owns the child and its combined output pipe. */
enum d32_error oui_provision_start(const char *path, pid_t *pid, int *reader);

#endif
