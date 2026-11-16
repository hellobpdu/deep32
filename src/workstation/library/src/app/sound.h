/* sound.h: the sound service (W1-SOUND, plan S1, S2, F15's clicks).
 *
 * One long-lived player child, forked on first use, plays every sound of Deep32 Library through sndio on two streams:
 *   - the click stream: the interface clips, preloaded into memory as mono 16-bit at SND_RATE, played with a small
 *     buffer (SND_CLICK_BUF frames); a new click cuts the one playing, as Windows' sndPlaySound did;
 *   - the theme stream: the Title screen's theme, streamed from its file at its own rate, with a fade on stop.
 * The parent talks to it over an AF_UNIX SOCK_SEQPACKET pair and never waits: a request the socket cannot take
 * is dropped. When the parent goes (Close, Alt+F4, a crash) the child sees end of file, fades the theme, lets
 * the click finish and exits.
 *
 * E26_SOUND_SINK=<file>: the child plays into a virtual device paced in real time instead of sndio and appends one
 * line per sound to <file> (clip, frames written, an FNV-1a hash of the samples, the press-to-first-write latency),
 * so the tests can check the expected clip and sample count for each press on a machine with no audio.
 *
 * Which clip plays on which event is screens/sounds.map (mod_sound.c, docs/rooms/SOUND.md); the scan that
 * recovered it from the period program is tools/scan/. */
#ifndef E26_SOUND_H
#define E26_SOUND_H
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>
#include "library_app.h"

#define SND_RATE 44100        /* the click stream's rate: every clip is resampled to it when loaded */
#define SND_CLICK_BUF 2048    /* the click stream's buffer: 46 ms at SND_RATE (a 10 ms scheduler tick never empties it) */
#define SND_THEME_BUF_MS 100  /* the theme stream's buffer */
#define SND_FADE_MS 250       /* the theme's fade on stop (update 1's decision, plan O2) */
#define SND_MAX_CLIPS 64
#define SND_HASH0 2166136261u

typedef struct {
	int16_t *pcm;   /* mono, SND_RATE */
	long frames;
	unsigned src_rate, src_ch;
	long src_frames;
} snd_clip;

/* a 16-bit PCM WAV (the store's .pcm.wav, or any canonical one) as a clip: channels mixed to mono, resampled to
 * SND_RATE (linear); 0, or -1 with a message in err */
int snd_clip_load(const char *path, snd_clip *out, char *err, size_t n);
void snd_clip_free(snd_clip *c);
/* FNV-1a over the samples as little-endian bytes; start with SND_HASH0 */
uint32_t snd_hash(const int16_t *pcm, long n, uint32_t h);
/* a 16-bit PCM WAV's format and the place of its samples; 0 or -1 */
int snd_wav_info(const char *path, unsigned *rate, unsigned *ch, long *data_off, long *data_len);

/* the player (parent side). Every call starts the child when it is not running; verbose as d32library -v. */
int snd_player_start(int verbose);                 /* 0, or -1 (no child: Library stays silent) */
pid_t snd_player_pid(void);                        /* -1 when not running */
int snd_preload(const char *path);                 /* a clip by file path: its index (the child loads it), or -1 */
int snd_clip_index(const char *path);              /* an index already preloaded, else -1 */
int snd_click_index(int index);                    /* play a preloaded clip on the click stream; 0 or -1 */
int snd_theme_play(const char *path, int loop);    /* the theme stream; 0 or -1 */
void snd_theme_stop(int fade_ms);
int snd_theme_playing(void);
void snd_set_volume(int volume);                   /* 0-100, both streams (SoundVolume) */
int snd_volume(void);
void snd_player_quit(void);                        /* close the socket: the child fades, finishes and exits */

/* mod_sound.c: the event-to-clip map (screens/sounds.map) */
enum { SNDM_PRESS, SNDM_MENU_OPEN, SNDM_FLYOUT, SNDM_MENU_PICK, SNDM_ENTER, SNDM_NEVENTS };
typedef struct {
	int ev;
	char match[64];  /* "*", "control:<id>", "kind:<name>", "record", "menu:<id>", "flags:<mask>", or a screen glob */
	char clip[64];   /* "-" (silent), "@record" (the control record's WAVE id), or a store asset without .pcm.wav */
	int line;
} snd_rule;
int snd_map_load(const char *path, char *err, size_t n);   /* 0, or -1 with the line in err */
int snd_map_count(void);
const snd_rule *snd_map_rule(int i);
const char *snd_map_event_name(int ev);



/* what a press, a menu opening, a flyout, a pick or a screen entered plays: the store asset ("ui/wave/32767") in
 * buf, or NULL when it is silent; *rule gets the deciding rule's index (or -1 when no rule matched) */
const char *snd_for_press(e26_app *a, scr_screen *s, int i, char *buf, size_t n, int *rule);
const char *snd_for_menu(e26_app *a, const char *menu, int flyout, char *buf, size_t n, int *rule);
const char *snd_for_pick(e26_app *a, const char *menu, char *buf, size_t n, int *rule);
const char *snd_for_enter(e26_app *a, const char *screen, char *buf, size_t n, int *rule);
/* play an asset of the store on the click stream (NoUISound and the map's state checked by the caller) */
int snd_play_asset(e26_app *a, const char *asset);

#endif
