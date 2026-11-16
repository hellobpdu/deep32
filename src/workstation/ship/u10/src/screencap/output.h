#ifndef D32_SCREENCAP_OUTPUT_H
#define D32_SCREENCAP_OUTPUT_H

#include <limits.h>
#include <stddef.h>

#include "d32_error.h"

struct sc_output {
	int home_fd, lock_fd;
	char base[PATH_MAX];
	char path[PATH_MAX];
};

enum d32_error sc_output_init(struct sc_output *, const char *home);
void sc_output_close(struct sc_output *);
enum d32_error sc_output_choose(struct sc_output *, const char *path);
enum d32_error sc_output_save(const struct sc_output *, const void *, size_t,
    char saved[PATH_MAX]);

#endif
