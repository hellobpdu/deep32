#ifndef D32_SOUND_H
#define D32_SOUND_H

#include <stdbool.h>
#include <stdint.h>
#include <sys/stat.h>
#include <sys/types.h>

#include "d32core.h"

enum snd_event { SND_BELL, SND_LOGIN, SND_LOGOUT, SND_EVENTS };
enum snd_profile { SND_SILENT, SND_CLASSIC, SND_WORKSTATION, SND_FULL, SND_PROFILES };

struct snd_sample {
	const char *id;
	const char *label;
	const char *sha256;
	unsigned profiles;
};

struct snd_assets {
	int fd;
	bool listed[SND_EVENTS], verified[SND_EVENTS];
	struct stat last[SND_EVENTS];
};

struct snd_rate {
	uint64_t last, recent[14];
	unsigned units, first, count;
	bool initialized;
};

extern const struct snd_sample snd_samples[SND_EVENTS];
extern const char *const snd_profile_names[SND_PROFILES];
extern const char *const snd_profile_labels[SND_PROFILES];

int snd_prefs_dir(void);
void snd_prefs_bad(const char *, void *);
enum snd_profile snd_profile_get(const struct d32_prefs *);
bool snd_enabled(enum snd_profile, enum snd_event);
bool snd_local_display(const char *);

void snd_assets_load(struct snd_assets *);
void snd_assets_close(struct snd_assets *);
int snd_sample_open(struct snd_assets *, enum snd_event);
enum d32_error snd_player_start(int, pid_t *);
void snd_player_stop(pid_t *);
uint64_t snd_now(void);
bool snd_rate_allow(struct snd_rate *, uint64_t);

int snd_lock(int);
int snd_pid_write(int);
void snd_pid_clear(int);
int snd_signal(int, int, pid_t);

#endif
