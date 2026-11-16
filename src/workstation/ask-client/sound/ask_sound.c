#include "ask_sound.h"
#include "sound.h"

#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <sha2.h>
#include <signal.h>
#include <sndio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

enum {
	WAV_HEADER = 44, PCM_RATE = 44100, PCM_FRAME = 2, LIFELINE_FD = 3,
	REAP_MS = 20, POLL_MS = 100, RETRY_MS = 10, PREF_MS = 1000,
	STALL_MS = 2000, IO_SECONDS = 2, DRAIN_SECONDS = 3, MAX_AUDIO_FDS = 8,
	FAIL_FRAMES = PCM_RATE * 7 / 10, FADE_FRAMES = PCM_RATE / 8
};

/* The pinned WAVs were mixed quiet for Windows speakers (hum -23 dBFS peak, ding -16).
 * Each cue gets a fixed gain, with clipping, after its hash check: hum x4, ding x2.
 * The error cue is the ding an octave lower and cut to 0.7 s: no new asset. */
struct sample {
	const char *name;
	const char *sha;
	size_t size;
	int gain, lowered;
};

static const struct sample hum = { "/usr/local/share/d32/sounds/ask-think.wav",
    "0b0579bc0bc32cc687c125dd39c1c4f0999570f7343f621035a3c956d0197743", 2822444, 4, 0 }; /* SHA-256 */
static const struct sample ding = { "/usr/local/share/d32/sounds/ask-done.wav",
    "bc4de42a49ebf7fcbfebb5c46cac6e22cb0e5035b2925b01d0e6fe0d266e7714", 97064, 2, 0 }; /* SHA-256 */
static const struct sample fail = { "/usr/local/share/d32/sounds/ask-done.wav",
    "bc4de42a49ebf7fcbfebb5c46cac6e22cb0e5035b2925b01d0e6fe0d266e7714", 97064, 2, 1 }; /* SHA-256 */

struct AskSound {
	XtAppContext app;
	XtIntervalId timer;
	const struct sample *pending;
	pid_t pid;
	int lifeline;
	int thinking;
};

/* Only an explicit Silent or Classic profile mutes Ask. A prefs directory that is missing,
 * not private (0700) or unreadable means no choice was made: the default Workstation
 * profile, which plays the cues. */
static int
sound_enabled(void)
{
	struct d32_prefs *prefs = NULL;
	enum snd_profile profile;
	int dir = snd_prefs_dir();
	if (dir >= 0) {
		(void)d32_prefs_load(dir, "d32sound", 1, NULL, NULL, &prefs);
		close(dir);
	}
	profile = snd_profile_get(prefs);
	d32_prefs_free(prefs);
	return profile == SND_WORKSTATION || profile == SND_FULL;
}

static int
pcm_at(const unsigned char *at)
{
	unsigned value = at[0] | (unsigned)at[1] << 8;
	return value >= 0x8000 ? (int)value - 0x10000 : (int)value;
}

static void
pcm_put(unsigned char *at, long value)
{
	unsigned bits;
	if (value > 32767)
		value = 32767;
	else if (value < -32768)
		value = -32768;
	bits = (unsigned)(value & 0xffff);
	at[0] = (unsigned char)(bits & 0xff);
	at[1] = (unsigned char)(bits >> 8);
}

/* Returns the cue to play, gained (and lowered for the error cue), or NULL. */
static unsigned char *
prepare_cue(const struct sample *sample, unsigned char *wave, size_t *size)
{
	size_t i, frames, source = (sample->size - WAV_HEADER) / PCM_FRAME;
	unsigned char *out;

	*size = sample->size;
	for (i = 0; i < source; i++) {
		unsigned char *at = wave + WAV_HEADER + i * PCM_FRAME;
		pcm_put(at, (long)pcm_at(at) * sample->gain);
	}
	if (!sample->lowered)
		return wave;
	/* Every source frame twice, the second interpolated: the same ding an octave down. */
	frames = FAIL_FRAMES < 2 * source ? FAIL_FRAMES : 2 * source;
	if ((out = malloc(WAV_HEADER + frames * PCM_FRAME)) == NULL) {
		free(wave);
		return NULL;
	}
	memcpy(out, wave, WAV_HEADER);
	for (i = 0; i < frames; i++) {
		const unsigned char *at = wave + WAV_HEADER + (i / 2) * PCM_FRAME;
		long value = pcm_at(at);
		if ((i & 1) && i / 2 + 1 < source)
			value = (value + pcm_at(at + PCM_FRAME)) / 2;
		if (i + FADE_FRAMES > frames)
			value = value * (long)(frames - i) / FADE_FRAMES;
		pcm_put(out + WAV_HEADER + i * PCM_FRAME, value);
	}
	free(wave);
	*size = WAV_HEADER + frames * PCM_FRAME;
	return out;
}

static unsigned char *
read_sample(const struct sample *sample)
{
	struct stat st;
	char hash[SHA256_DIGEST_STRING_LENGTH];
	unsigned char *bytes = NULL;
	int fd = open(sample->name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
	if (fd < 0)
		return NULL;
	if (fstat(fd, &st) < 0 || !S_ISREG(st.st_mode) || st.st_uid != 0 ||
	    (st.st_mode & 0022) || st.st_size != (off_t)sample->size)
		goto out;
	bytes = malloc(sample->size);
	if (bytes && (pread(fd, bytes, sample->size, 0) != (ssize_t)sample->size ||
	    !SHA256Data(bytes, sample->size, hash) || strcmp(hash, sample->sha))) {
		free(bytes);
		bytes = NULL;
	}
out:
	close(fd);
	return bytes;
}

static int64_t
monotonic_ms(void)
{
	struct timespec now;
	if (clock_gettime(CLOCK_MONOTONIC, &now) == -1)
		return -1;
	return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

/* Return success only after submitting the entire one-shot cue. */
static int
submit_pcm(struct sio_hdl *device, const unsigned char *wave, size_t size, int loop)
{
	struct pollfd fds[1 + MAX_AUDIO_FDS] = {{LIFELINE_FD, POLLIN, 0}};
	int nfds = sio_nfds(device);
	int64_t now = monotonic_ms(), progress = now, pref = now;
	size_t pos = WAV_HEADER;

	if (nfds <= 0 || nfds > MAX_AUDIO_FDS)
		return 0;
	while (now >= 0 && now - progress < STALL_MS && !sio_eof(device)) {
		int n, ready, events;
		size_t written, remaining;
		const size_t chunk = PCM_RATE / 25 * PCM_FRAME;

		if (now - pref >= PREF_MS) {
			int enabled;
			alarm(IO_SECONDS);
			enabled = sound_enabled();
			alarm(0);
			if (!enabled)
				return 0;
			pref = now;
		}
		n = sio_pollfd(device, fds + 1, POLLOUT);
		if (n <= 0 || n > nfds)
			return 0;
		ready = poll(fds, (nfds_t)n + 1, POLL_MS);
		if (ready == -1 && errno == EINTR) {
			now = monotonic_ms();
			continue;
		}
		if (ready < 0 || fds[0].revents)
			return 0;
		now = monotonic_ms();
		if (now < 0 || now - progress >= STALL_MS)
			return 0;
		if (ready == 0)
			continue;
		events = sio_revents(device, fds + 1);
		for (int i = 1; i <= n; i++)
			if (fds[i].revents & (POLLERR | POLLHUP | POLLNVAL))
				return 0;
		if (events & POLLHUP)
			return 0;
		if (!(events & POLLOUT))
			continue;

		remaining = size - pos;
		written = sio_write(device, wave + pos, remaining < chunk ? remaining : chunk);
		if (written == 0) {
			/* A device that stays writable without accepting data must not spin. */
			ready = poll(fds, 1, RETRY_MS);
			if (ready > 0 || (ready == -1 && errno != EINTR))
				return 0;
			now = monotonic_ms();
			continue;
		}
		progress = now;
		pos += written;
		if (pos == size) {
			if (!loop)
				return 1;
			pos = WAV_HEADER;
		}
	}
	return 0;
}

static void
play_worker(const struct sample *sample, int lifeline)
{
	struct rlimit no_core = {0, 0};
	struct pollfd parent = {LIFELINE_FD, POLLIN, 0};
	sigset_t unblock;
	struct sio_hdl *device;
	struct sio_par par;
	unsigned char *wave;
	size_t size;
	int complete;

	if (lifeline != LIFELINE_FD) {
		if (dup2(lifeline, LIFELINE_FD) == -1)
			_exit(1);
		close(lifeline);
	}
	closefrom(LIFELINE_FD + 1);
	/* The forked worker inherits the Ask process's address space. */
	if (setrlimit(RLIMIT_CORE, &no_core) == -1 || signal(SIGALRM, SIG_DFL) == SIG_ERR)
		_exit(1);
	sigemptyset(&unblock);
	sigaddset(&unblock, SIGALRM);
	if (sigprocmask(SIG_UNBLOCK, &unblock, NULL) == -1)
		_exit(1);
	/* Setup and teardown may block inside sndio even for a nonblocking stream. */
	alarm(IO_SECONDS);
	if (!sound_enabled() || (wave = read_sample(sample)) == NULL ||
	    (wave = prepare_cue(sample, wave, &size)) == NULL || poll(&parent, 1, 0) != 0)
		_exit(0);
	device = sio_open("snd/default", SIO_PLAY, 1);
	if (!device)
		_exit(0);
	sio_initpar(&par);
	par.bits = 16; par.bps = PCM_FRAME; par.sig = 1; par.le = 1;
	par.pchan = 1; par.rate = PCM_RATE; par.appbufsz = PCM_RATE / 10;
	if (!sio_setpar(device, &par) || !sio_getpar(device, &par) ||
	    par.bits != 16 || par.bps != PCM_FRAME || par.sig != 1 || par.le != 1 ||
	    par.pchan != 1 || par.rate != PCM_RATE || !sio_start(device)) {
		sio_close(device);
		_exit(0);
	}
	alarm(0);
	complete = submit_pcm(device, wave, size, sample == &hum);
	alarm(complete ? DRAIN_SECONDS : IO_SECONDS);
	if (complete)
		(void)sio_stop(device);
	else if (!sio_eof(device))
		(void)sio_flush(device);
	sio_close(device);
	_exit(0);
}

static void
start_worker(AskSound *sound, const struct sample *sample)
{
	int pipefd[2];
	pid_t pid;

	if (pipe2(pipefd, O_CLOEXEC) == -1)
		return;
	pid = fork();
	if (pid == 0) {
		close(pipefd[1]);
		play_worker(sample, pipefd[0]);
		_exit(1);
	}
	close(pipefd[0]);
	if (pid == -1) {
		close(pipefd[1]);
		return;
	}
	sound->pid = pid;
	sound->lifeline = pipefd[1];
}

static void
close_lifeline(AskSound *sound)
{
	if (sound->lifeline != -1) {
		close(sound->lifeline);
		sound->lifeline = -1;
	}
}

/* Xt serializes this check and signal with the bridge's SIGCHLD callback. */
static int
worker_alive(AskSound *sound)
{
	pid_t result;
	if (!sound->pid)
		return 0;
	do {
		result = waitpid(sound->pid, NULL, WNOHANG);
	} while (result == -1 && errno == EINTR);
	if (result == sound->pid || (result == -1 && errno == ECHILD)) {
		sound->pid = 0;
		close_lifeline(sound);
	}
	return result == 0;
}

static void
interrupt_worker(AskSound *sound)
{
	/* sndiod discards this stream's queued data when its socket disconnects. */
	if (worker_alive(sound))
		(void)kill(sound->pid, SIGKILL);
	close_lifeline(sound);
}

static void
advance(XtPointer data, XtIntervalId *id)
{
	AskSound *sound = data;
	(void)id;
	sound->timer = 0;
	(void)worker_alive(sound);
	if (!sound->pid && sound->pending) {
		const struct sample *sample = sound->pending;
		sound->pending = NULL;
		start_worker(sound, sample);
	}
	if (sound->pid)
		sound->timer = XtAppAddTimeOut(sound->app, REAP_MS, advance, sound);
}

static void
replace_cue(AskSound *sound, const struct sample *sample)
{
	sound->pending = sample;
	interrupt_worker(sound);
	if (!sound->timer)
		advance(sound, NULL);
}

AskSound *
ask_sound_create(XtAppContext app)
{
	AskSound *sound = calloc(1, sizeof(*sound));
	if (sound) {
		sound->app = app;
		sound->lifeline = -1;
	}
	return sound;
}

void
ask_sound_thinking(AskSound *sound)
{
	if (!sound || sound->thinking)
		return;
	sound->thinking = 1;
	replace_cue(sound, &hum);
}

void
ask_sound_end(AskSound *sound, int outcome)
{
	if (!sound || (outcome != ASK_SOUND_STOP && !sound->thinking))
		return;
	sound->thinking = 0;
	replace_cue(sound, outcome == ASK_SOUND_DONE ? &ding : outcome == ASK_SOUND_FAILED ? &fail : NULL);
}

void
ask_sound_free(AskSound *sound)
{
	if (sound) {
		if (sound->timer)
			XtRemoveTimeOut(sound->timer);
		interrupt_worker(sound);
		(void)worker_alive(sound);
		free(sound);
	}
}
