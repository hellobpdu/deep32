/* wav.h: RIFF WAVE reader that decodes PCM 8/16-bit, WAVE ADPCM (format 0x0002) and IMA ADPCM (format 0x11)
 * to signed 16-bit PCM, so the application only ever plays plain PCM. */
#ifndef E26_WAV_H
#define E26_WAV_H
#include <stddef.h>
#include <stdint.h>

typedef struct {
	int format;      /* source format tag */
	int channels;
	long rate;
	int bits;        /* source bits per sample */
	long frames;     /* decoded frames */
	int16_t *pcm;    /* interleaved, frames*channels */
} wav_pcm;

int wav_decode(const unsigned char *p, size_t n, wav_pcm *out, char *err, size_t errlen);
void wav_free(wav_pcm *w);
/* Writes a canonical 16-bit PCM WAV. 0 ok. */
int wav_write_pcm16(const wav_pcm *w, const char *path);

#endif
