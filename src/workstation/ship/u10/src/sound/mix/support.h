#ifndef D32_MIX_SUPPORT_H
#define D32_MIX_SUPPORT_H

#include "d32core.h"

#include <stdbool.h>
#include <stdint.h>

bool mix_local_display(const char *);
bool mix_local_device(const char *);
int mix_recording_permission(void);
int mix_preferences(void);
void mix_bad_setting(const char *, void *);
uint64_t mix_now(void);
int mix_bell_open(void);
enum d32_error mix_bell_play(int, const char *, pid_t *);
void mix_bell_stop(pid_t *);

#endif
