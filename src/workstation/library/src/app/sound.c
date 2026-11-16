/* sound.c: the sound service (W1-SOUND; sound.h has the design). The parent half keeps the clip table and sends
 * requests; the child half (child_main) owns the device: the click stream at SND_RATE with a small buffer, the theme
 * stream at the theme's own rate, the fade and the volume, or the file sink (E26_SOUND_SINK) in their place.
 * Without sndio (SNDIO=0) the child still runs: the sink works, and sndio requests say they cannot play. */
#include "library_app.h"
#include "sound.h"
#include "../common/le.h"
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
#ifdef HAVE_SNDIO
#include <sndio.h>
#endif

/* ---- WAV files and clips */

static int64_t now_ns(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (int64_t)ts.tv_sec * 1000000000 + ts.tv_nsec;
}

int snd_wav_info(const char *path, unsigned *rate, unsigned *ch, long *data_off, long *data_len)
{
	FILE *f = fopen(path, "rb");
	unsigned char h[12], c[8], fmt[16];
	long pos = 12;
	int have_fmt = 0;
	if (!f) return -1;
	if (fread(h, 1, 12, f) != 12 || memcmp(h, "RIFF", 4) || memcmp(h + 8, "WAVE", 4)) { fclose(f); return -1; }
	for (;;) {
		unsigned long len;
		if (fseek(f, pos, SEEK_SET) || fread(c, 1, 8, f) != 8) break;
		len = le32(c + 4);
		if (!memcmp(c, "fmt ", 4)) {
			if (len < 16 || fread(fmt, 1, 16, f) != 16) break;
			/* 16-bit integer PCM only: the store's .pcm.wav files and the shipped theme are */
			if (le16(fmt) != 1 || le16(fmt + 14) != 16 || !le16(fmt + 2) || !le32(fmt + 4)) break;
			*ch = le16(fmt + 2);
			*rate = le32(fmt + 4);
			have_fmt = 1;
		} else if (!memcmp(c, "data", 4) && have_fmt) {
			*data_off = pos + 8;
			*data_len = (long)(len / (2u * *ch)) * 2 * (long)*ch;
			fclose(f);
			return 0;
		}
		pos += 8 + (long)len + (long)(len & 1);
	}
	fclose(f);
	return -1;
}

uint32_t snd_hash(const int16_t *pcm, long n, uint32_t h)
{
	long i;
	for (i = 0; i < n; i++) {
		uint16_t v = (uint16_t)pcm[i];
		h = (h ^ (v & 0xFFu)) * 16777619u;
		h = (h ^ (uint32_t)(v >> 8)) * 16777619u;
	}
	return h;
}

int snd_clip_load(const char *path, snd_clip *out, char *err, size_t n)
{
	unsigned rate = 0, ch = 0, c;
	long off = 0, len = 0, frames, i, outn;
	unsigned char *raw;
	int32_t *mono;
	FILE *f;
	memset(out, 0, sizeof *out);
	if (snd_wav_info(path, &rate, &ch, &off, &len)) { snprintf(err, n, "%s is not a 16-bit PCM WAV", path); return -1; }
	frames = len / (2 * (long)ch);
	if (frames <= 0 || frames > 60L * 48000) { snprintf(err, n, "%s: %ld frames", path, frames); return -1; }
	if (!(f = fopen(path, "rb"))) { snprintf(err, n, "cannot open %s", path); return -1; }
	raw = (unsigned char *)malloc((size_t)len);
	mono = (int32_t *)malloc((size_t)frames * sizeof(int32_t));
	if (!raw || !mono || fseek(f, off, SEEK_SET) || fread(raw, 1, (size_t)len, f) != (size_t)len) {
		snprintf(err, n, "cannot read %s", path);
		free(raw);
		free(mono);
		fclose(f);
		return -1;
	}
	fclose(f);
	for (i = 0; i < frames; i++) {
		int32_t s = 0;
		for (c = 0; c < ch; c++) s += le16s(raw + 2 * ((size_t)i * ch + c));
		mono[i] = s / (int32_t)ch;
	}
	free(raw);
	/* linear resampling to SND_RATE in integer steps (22050 Hz doubles exactly) */
	outn = (long)((int64_t)frames * SND_RATE / rate);
	if (outn < 1) outn = 1;
	out->pcm = (int16_t *)malloc((size_t)outn * sizeof(int16_t));
	if (!out->pcm) { free(mono); snprintf(err, n, "out of memory"); return -1; }
	for (i = 0; i < outn; i++) {
		int64_t num = (int64_t)i * rate;
		long j = (long)(num / SND_RATE);
		int64_t frac = num % SND_RATE;
		int32_t a0 = mono[j < frames ? j : frames - 1], a1 = mono[j + 1 < frames ? j + 1 : frames - 1];
		out->pcm[i] = (int16_t)(a0 + (int32_t)((a1 - a0) * frac / SND_RATE));
	}
	free(mono);
	out->frames = outn;
	out->src_rate = rate;
	out->src_ch = ch;
	out->src_frames = frames;
	return 0;
}

void snd_clip_free(snd_clip *c)
{
	free(c->pcm);
	memset(c, 0, sizeof *c);
}

/* ---- the requests */

enum { M_LOAD = 1, M_CLICK, M_THEME, M_THEME_STOP, M_VOLUME };
typedef struct {
	int32_t op, a, b, pad;
	int64_t t_ns;      /* when the parent asked (CLOCK_MONOTONIC): the press-to-sound latency starts here */
	char s[1024];      /* M_LOAD, M_THEME: a path */
} snd_msg;
#define MSG_HEAD offsetof(snd_msg, s)

/* ---- the child: the device and its two streams */

#define CHUNK 256  /* frames produced at a time */

typedef struct {
	const char *name;    /* "click", "theme" */
	int open, dead;
	int64_t dead_at;
#ifdef HAVE_SNDIO
	struct sio_hdl *h;
#endif
	unsigned rate, ch;
	long buf;            /* the device buffer, frames */
	int pfd, npfd;       /* its descriptors in the poll set */
	int64_t t0;          /* the sink's virtual device: when its frame 0 played */
	long long vwritten;
	/* produced, not yet taken by the device */
	unsigned char out[CHUNK * 2 * 8];
	size_t out_n, out_off;
	/* the click source */
	int clip;            /* -1 none */
	long pos;
	uint32_t hash;
	int64_t stamp;
	long lat_us;
	/* the theme source */
	FILE *f;
	long data_off, data_len, data_pos;
	int loop;
	char path[1024];
	long fade_total, fade_left;
	long long produced;
	long pad;            /* silence still to write before the stream closes */
	int closing;
} stream;

static snd_clip cclips[SND_MAX_CLIPS];
static char cnames[SND_MAX_CLIPS][1024];
static int cvol = 100, cverbose;
static FILE *sink;
static int64_t sink_t0;

static void slog(const char *fmt, ...)
{
	va_list ap;
	if (!sink) return;
	fprintf(sink, "%.3f ", (double)(now_ns() - sink_t0) / 1e9);
	va_start(ap, fmt);
	vfprintf(sink, fmt, ap);
	va_end(ap);
	fputc('\n', sink);
	fflush(sink);
}

static int dev_open(stream *st, unsigned rate, unsigned ch, long buf, const char *what)
{
	st->rate = rate;
	st->ch = ch;
	st->buf = buf;
	st->out_n = st->out_off = 0;
	if (sink) {
		st->open = 1;
		st->t0 = now_ns();
		st->vwritten = 0;
		return 0;
	}
#ifdef HAVE_SNDIO
	{
		struct sio_par par;
		st->h = sio_open(SIO_DEVANY, SIO_PLAY, 1);
		if (!st->h) {
			fprintf(stderr, "d32library: sound: no sndio device for %s; staying silent\n", what);
			st->dead = 1;
			st->dead_at = now_ns();
			return -1;
		}
		sio_initpar(&par);
		par.bits = 16;
		par.sig = 1;
		par.le = SIO_LE_NATIVE;
		par.pchan = ch;
		par.rate = rate;
		par.appbufsz = (unsigned)buf;
		par.xrun = SIO_IGNORE; /* an empty buffer pauses the stream: a click after a quiet spell starts at once */
		if (!sio_setpar(st->h, &par) || !sio_getpar(st->h, &par) || par.bits != 16 || par.le != SIO_LE_NATIVE ||
		    par.pchan != ch || par.rate != rate || !sio_start(st->h)) {
			fprintf(stderr, "d32library: sound: device refused %u Hz %u ch 16-bit for %s\n", rate, ch, what);
			sio_close(st->h);
			st->h = NULL;
			st->dead = 1;
			st->dead_at = now_ns();
			return -1;
		}
		st->buf = (long)par.bufsz;
		st->open = 1;
		return 0;
	}
#else
	fprintf(stderr, "d32library: sound: built without sndio; %s not played\n", what);
	st->dead = 1;
	st->dead_at = now_ns();
	return -1;
#endif
}

static void dev_close(stream *st)
{
#ifdef HAVE_SNDIO
	if (st->h) {
		sio_flush(st->h); /* only the closing silence is left in it */
		sio_close(st->h);
		st->h = NULL;
	}
#endif
	st->open = 0;
	st->out_n = st->out_off = 0;
}

/* the bytes the device takes now */
static size_t dev_write(stream *st, const unsigned char *p, size_t n)
{
	size_t fb = 2u * st->ch;
	if (sink) {
		int64_t t = now_ns();
		long long played = (long long)((t - st->t0) * (int64_t)st->rate / 1000000000), room;
		if (played > st->vwritten) { /* it ran dry: the virtual device pauses, as sndio does with SIO_IGNORE */
			st->t0 = t - (int64_t)(st->vwritten * 1000000000 / (long long)st->rate);
			played = st->vwritten;
		}
		room = st->buf - (st->vwritten - played);
		if (room <= 0) return 0;
		if ((long long)(n / fb) > room) n = (size_t)room * fb;
		st->vwritten += (long long)(n / fb);
		return n;
	}
#ifdef HAVE_SNDIO
	if (st->h) return sio_write(st->h, p, n);
#endif
	(void)p;
	return n;
}

static int active(const stream *st)
{
	return st->out_n > st->out_off || st->clip >= 0 || st->f || st->pad > 0;
}

static void put_samples(stream *st, const int16_t *s, long nsamples)
{
	long i;
	for (i = 0; i < nsamples; i++) {
		st->out[st->out_n++] = (unsigned char)((uint16_t)s[i] & 0xFF);
		st->out[st->out_n++] = (unsigned char)((uint16_t)s[i] >> 8);
	}
}

static void click_done(stream *st, int cut)
{
	slog("click clip=%s frames=%ld of=%ld hash=%08x lat_us=%ld buf_us=%ld volume=%d cut=%d", cnames[st->clip], st->pos,
	     cclips[st->clip].frames, (unsigned)st->hash, st->lat_us, (long)(st->buf * 1000000 / (long)st->rate), cvol, cut);
	if (cverbose) fprintf(stderr, "d32library: sound: click %s, %ld frames%s\n", cnames[st->clip], st->pos, cut ? " (cut)" : "");
	st->clip = -1;
}

static void theme_close(stream *st, const char *why)
{
	slog("theme %s path=%s frames=%lld fade_frames=%ld", why, st->path, st->produced, st->fade_total);
	if (st->f) fclose(st->f);
	st->f = NULL;
	st->pad = st->buf; /* silence after the last samples, then the stream closes */
	st->closing = 1;
}

/* the next chunk into st->out (called when the device has taken everything produced before) */
static void produce(stream *st)
{
	int16_t s[CHUNK * 8];
	long n, i;
	st->out_n = st->out_off = 0;
	if (st->clip >= 0) {
		const snd_clip *c = &cclips[st->clip];
		if (st->pos == 0) st->lat_us = (long)((now_ns() - st->stamp) / 1000); /* its first samples go now */
		n = c->frames - st->pos;
		if (n > CHUNK) n = CHUNK;
		for (i = 0; i < n; i++) s[i] = (int16_t)((int32_t)c->pcm[st->pos + i] * cvol / 100);
		st->hash = snd_hash(s, n, st->hash);
		st->pos += n;
		put_samples(st, s, n);
		if (st->pos >= c->frames) click_done(st, 0);
		return;
	}
	if (st->f) {
		unsigned char raw[CHUNK * 2 * 8];
		long want = CHUNK, got, left = (st->data_len - st->data_pos) / (2 * (long)st->ch);
		if (st->fade_total && want > st->fade_left) want = st->fade_left;
		if (want > left) want = left;
		got = want > 0 ? (long)fread(raw, 2u * st->ch, (size_t)want, st->f) : 0;
		for (i = 0; i < got * (long)st->ch; i++) {
			int64_t v = (int64_t)le16s(raw + 2 * i) * cvol / 100;
			if (st->fade_total) v = v * (st->fade_left - i / (long)st->ch) / st->fade_total;
			s[i] = (int16_t)v;
		}
		st->data_pos += got * 2 * (long)st->ch;
		st->produced += got;
		if (st->fade_total) st->fade_left -= got;
		put_samples(st, s, got * (long)st->ch);
		if (st->fade_total && st->fade_left <= 0) { theme_close(st, "stop"); return; }
		if (st->data_pos >= st->data_len || got < want || got == 0) {
			if (st->loop && st->data_len > 0 && !st->fade_total) {
				st->data_pos = 0;
				if (fseek(st->f, st->data_off, SEEK_SET)) theme_close(st, "error");
			} else
				theme_close(st, "end");
		}
		return;
	}
	if (st->pad > 0) {
		n = st->pad > CHUNK ? CHUNK : st->pad;
		memset(s, 0, sizeof s);
		put_samples(st, s, n * (long)st->ch);
		st->pad -= n;
	}
}

/* write what the device takes; produce more while it takes everything */
static void feed(stream *st)
{
	int rounds = 0;
	while (st->open && rounds++ < 64) {
		size_t w;
		if (st->out_off >= st->out_n) {
			if (!active(st)) break;
			produce(st);
			if (st->out_n == 0) break;
		}
		w = dev_write(st, st->out + st->out_off, st->out_n - st->out_off);
		st->out_off += w;
		if (st->out_off < st->out_n) break; /* the device is full */
	}
	if (st->open && st->closing && !active(st)) {
		dev_close(st);
		st->closing = 0;
	}
}

static void click_start(stream *ck, int idx, int64_t stamp)
{
	if (idx < 0 || idx >= SND_MAX_CLIPS || !cclips[idx].pcm) return;
	if (!ck->open) {
		/* a device that failed is tried again after five seconds (the sink never fails) */
		if (ck->dead && now_ns() - ck->dead_at < 5000000000LL) return;
		ck->dead = 0;
		if (dev_open(ck, SND_RATE, 1, SND_CLICK_BUF, "the interface clicks")) return;
	}
	if (ck->clip >= 0) click_done(ck, 1); /* sndPlaySound stops the sound playing */
	ck->clip = idx;
	ck->pos = 0;
	ck->hash = SND_HASH0;
	ck->stamp = stamp;
	ck->lat_us = -1;
}

static void theme_start(stream *th, const char *path, int loop)
{
	unsigned rate, ch;
	long off, len;
	if (th->open) { /* a new theme replaces the old one at once */
		if (th->f) fclose(th->f);
		th->f = NULL;
		th->pad = 0;
		th->closing = 0;
		dev_close(th);
	}
	if (snd_wav_info(path, &rate, &ch, &off, &len) || ch > 8) {
		fprintf(stderr, "d32library: sound: %s is not 16-bit PCM\n", path);
		return;
	}
	snprintf(th->path, sizeof th->path, "%s", path);
	th->dead = 0;
	if (dev_open(th, rate, ch, (long)rate * SND_THEME_BUF_MS / 1000, path)) return;
	if (!(th->f = fopen(path, "rb")) || fseek(th->f, off, SEEK_SET)) {
		fprintf(stderr, "d32library: sound: cannot open %s\n", path);
		if (th->f) fclose(th->f);
		th->f = NULL;
		dev_close(th);
		return;
	}
	th->data_off = off;
	th->data_len = len;
	th->data_pos = 0;
	th->loop = loop;
	th->fade_total = th->fade_left = 0;
	th->produced = 0;
	th->pad = 0;
	th->closing = 0;
	if (cverbose) fprintf(stderr, "d32library: sound: playing %s (%u Hz, %u ch) via %s\n", path, rate, ch, sink ? "the sink" : "sndio");
	slog("theme start path=%s rate=%u ch=%u loop=%d", path, rate, ch, loop);
}

static void theme_fade(stream *th, int ms)
{
	if (!th->f || th->fade_total) return;
	th->fade_total = th->fade_left = ms > 0 ? (long)th->rate * ms / 1000 : 1;
}

static void close_fds(int keep)
{
	long max = sysconf(_SC_OPEN_MAX), fd;
	if (max < 0 || max > 4096) max = 4096;
	for (fd = 3; fd < max; fd++)
		if (fd != keep) close((int)fd);
}

static int child_main(int fd, int verbose)
{
	stream ck, th;
	stream *st[2];
	const char *sp = getenv("E26_SOUND_SINK");
	int eof = 0, i;
	int64_t eof_at = 0;
	signal(SIGPIPE, SIG_IGN);
	signal(SIGTERM, SIG_DFL);
	signal(SIGINT, SIG_IGN); /* a ^C at the terminal stops d32library; the end of the socket then stops this */
	close_fds(fd);           /* the X connection and every helper's socket stay with d32library */
	cverbose = verbose;
	if (sp && sp[0]) {
		sink = fopen(sp, "a");
		if (!sink) fprintf(stderr, "d32library: sound: cannot open the sink %s\n", sp);
	}
	sink_t0 = now_ns();
	memset(&ck, 0, sizeof ck);
	memset(&th, 0, sizeof th);
	ck.name = "click";
	th.name = "theme";
	ck.clip = th.clip = -1;
	st[0] = &ck;
	st[1] = &th;
	slog("start pid=%ld click_rate=%d click_buf=%d theme_buf_ms=%d fade_ms=%d", (long)getpid(), SND_RATE, SND_CLICK_BUF,
	     SND_THEME_BUF_MS, SND_FADE_MS);
	for (;;) {
		struct pollfd pfd[1 + 2 * 8];
		int nfd = 1, timeout = -1;
		pfd[0].fd = eof ? -1 : fd;
		pfd[0].events = POLLIN;
		pfd[0].revents = 0;
		for (i = 0; i < 2; i++) {
			st[i]->npfd = 0;
			if (!st[i]->open || !active(st[i])) continue;
			if (sink) { timeout = 3; continue; }
#ifdef HAVE_SNDIO
			st[i]->pfd = nfd;
			st[i]->npfd = sio_pollfd(st[i]->h, &pfd[nfd], POLLOUT);
			nfd += st[i]->npfd;
#endif
		}
		if (eof) {
			if (!active(&ck) && !active(&th)) break;
			if (now_ns() - eof_at > 3000000000LL) break; /* never outlive d32library by more than 3 s */
			if (timeout < 0 || timeout > 50) timeout = 50;
		}
		if (poll(pfd, (nfds_t)nfd, timeout) < 0 && errno != EINTR) break;
		if (!eof && (pfd[0].revents & (POLLIN | POLLHUP | POLLERR))) {
			for (;;) {
				snd_msg m;
				ssize_t r;
				memset(&m, 0, sizeof m);
				r = recv(fd, &m, sizeof m, 0);
				if (r == 0 || (r < 0 && errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK)) {
					eof = 1;
					eof_at = now_ns();
					theme_fade(&th, SND_FADE_MS);
					slog("parent gone");
					break;
				}
				if (r < 0) break;
				if ((size_t)r < MSG_HEAD) continue;
				m.s[sizeof m.s - 1] = 0;
				switch (m.op) {
				case M_LOAD:
					if (m.a >= 0 && m.a < SND_MAX_CLIPS) {
						char err[1200];
						snd_clip_free(&cclips[m.a]);
						snprintf(cnames[m.a], sizeof cnames[m.a], "%s", m.s);
						if (snd_clip_load(m.s, &cclips[m.a], err, sizeof err)) fprintf(stderr, "d32library: sound: %s\n", err);
						else slog("load clip=%s frames=%ld src_rate=%u src_frames=%ld", m.s, cclips[m.a].frames, cclips[m.a].src_rate,
						          cclips[m.a].src_frames);
					}
					break;
				case M_CLICK:
					click_start(&ck, m.a, m.t_ns);
					break;
				case M_THEME:
					theme_start(&th, m.s, m.a);
					break;
				case M_THEME_STOP:
					theme_fade(&th, m.a);
					break;
				case M_VOLUME:
					cvol = m.a < 0 ? 0 : m.a > 100 ? 100 : m.a;
					slog("volume %d", cvol);
					break;
				}
			}
		}
		for (i = 0; i < 2; i++) {
			if (!st[i]->open) continue;
#ifdef HAVE_SNDIO
			if (!sink) {
				int rev;
				if (st[i]->npfd <= 0) continue;
				rev = sio_revents(st[i]->h, &pfd[st[i]->pfd]);
				if (rev & POLLHUP) {
					fprintf(stderr, "d32library: sound: the %s stream was lost\n", st[i]->name);
					if (st[i]->clip >= 0) click_done(st[i], 1);
					if (st[i]->f) theme_close(st[i], "lost");
					st[i]->pad = 0;
					dev_close(st[i]);
					continue;
				}
				if (!(rev & POLLOUT)) continue;
			}
#endif
			feed(st[i]);
		}
	}
	if (ck.clip >= 0) click_done(&ck, 1);
	if (th.f) theme_close(&th, "stop");
	dev_close(&ck);
	dev_close(&th);
	slog("exit");
	if (sink) fclose(sink);
	return 0;
}

/* ---- the parent */

static int psock = -1, pverbose, pstarts, theme_on, pvolume = 100;
static pid_t pchild = -1;
static char pclips[SND_MAX_CLIPS][1024];
static int npclips;

static int psend(int op, int a, const char *s)
{
	snd_msg m;
	size_t len;
	if (psock < 0) return -1;
	memset(&m, 0, sizeof m);
	m.op = op;
	m.a = a;
	m.t_ns = now_ns();
	if (s) snprintf(m.s, sizeof m.s, "%s", s);
	len = MSG_HEAD + strlen(m.s) + 1;
	if (send(psock, &m, len, MSG_NOSIGNAL) == (ssize_t)len) return 0;
	if (errno == EAGAIN || errno == EWOULDBLOCK || errno == ENOBUFS) return -1; /* busy: this request is dropped */
	/* the child is gone: forget it; the next request starts another (three in a session at most) */
	close(psock);
	psock = -1;
	pchild = -1;
	theme_on = 0;
	return -1;
}

int snd_player_start(int verbose)
{
	int sv[2], i, sz = 65536;
	pid_t pid;
	if (verbose) pverbose = 1;
	if (psock >= 0) return 0;
	if (pstarts >= 3) return -1;
	pstarts++;
	if (socketpair(AF_UNIX, SOCK_SEQPACKET, 0, sv) == -1) {
		fprintf(stderr, "d32library: sound: socketpair: %s; staying silent\n", strerror(errno));
		return -1;
	}
	signal(SIGCHLD, SIG_IGN); /* children are reaped by the system (spawn.h) */
	fflush(stdout);
	fflush(stderr);
	pid = fork();
	if (pid < 0) {
		close(sv[0]);
		close(sv[1]);
		return -1;
	}
	if (pid == 0) {
		close(sv[0]);
		fcntl(sv[1], F_SETFL, fcntl(sv[1], F_GETFL) | O_NONBLOCK);
		_exit(child_main(sv[1], pverbose));
	}
	close(sv[1]);
	fcntl(sv[0], F_SETFL, fcntl(sv[0], F_GETFL) | O_NONBLOCK);
	fcntl(sv[0], F_SETFD, FD_CLOEXEC); /* helpers started later never hold the player's socket */
	setsockopt(sv[0], SOL_SOCKET, SO_SNDBUF, &sz, sizeof sz);
	psock = sv[0];
	pchild = pid;
	if (pverbose) fprintf(stderr, "d32library: sound: player %ld started%s\n", (long)pid, getenv("E26_SOUND_SINK") ? " (sink)" : "");
	for (i = 0; i < npclips; i++) psend(M_LOAD, i, pclips[i]);
	if (pvolume != 100) psend(M_VOLUME, pvolume, NULL);
	return 0;
}

pid_t snd_player_pid(void) { return psock >= 0 ? pchild : -1; }

int snd_clip_index(const char *path)
{
	int i;
	for (i = 0; i < npclips; i++)
		if (!strcmp(pclips[i], path)) return i;
	return -1;
}

int snd_preload(const char *path)
{
	int i = snd_clip_index(path);
	if (i >= 0) return i;
	if (npclips == SND_MAX_CLIPS || strlen(path) >= sizeof pclips[0]) return -1;
	i = npclips++;
	snprintf(pclips[i], sizeof pclips[i], "%s", path);
	if (psock >= 0) psend(M_LOAD, i, path);
	return i;
}

int snd_click_index(int index)
{
	if (index < 0 || index >= npclips) return -1;
	if (snd_player_start(0)) return -1;
	return psend(M_CLICK, index, NULL);
}

int snd_theme_play(const char *path, int loop)
{
	unsigned rate, ch;
	long off, len;
	if (snd_wav_info(path, &rate, &ch, &off, &len)) {
		fprintf(stderr, "d32library: sound: %s is not 16-bit PCM\n", path);
		return -1;
	}
	if (snd_player_start(0)) return -1;
	if (psend(M_THEME, loop ? 1 : 0, path)) return -1;
	theme_on = 1;
	return 0;
}

void snd_theme_stop(int fade_ms)
{
	if (!theme_on) return;
	theme_on = 0;
	psend(M_THEME_STOP, fade_ms, NULL);
}

int snd_theme_playing(void) { return theme_on; }

void snd_set_volume(int volume)
{
	pvolume = volume < 0 ? 0 : volume > 100 ? 100 : volume;
	if (psock >= 0) psend(M_VOLUME, pvolume, NULL);
}

int snd_volume(void) { return pvolume; }

void snd_player_quit(void)
{
	if (psock >= 0) close(psock);
	psock = -1;
	pchild = -1;
	theme_on = 0;
}

/* ---- build 2's interface, kept: the theme through the service */

pid_t snd_play(const char *path, int loop, int verbose)
{
	if (snd_player_start(verbose)) return -1;
	if (snd_theme_play(path, loop)) return -1;
	return snd_player_pid();
}

/* the theme stops with its fade (e26_quit calls this before it exits: the child fades it out on its own) */
void snd_stop(pid_t pid)
{
	if (pid > 0 && pid == snd_player_pid()) snd_theme_stop(SND_FADE_MS);
}

/* the front screen's sound (nav.c calls it as a screen comes to the front): the screen file's "sound" on the theme
 * stream, or for the Title screen *titleTheme (another asset of the store, "hilite/aquatic", or an absolute 16-bit PCM
 * .wav path; "" keeps the screen's own, the period design's theme, and so does a theme file that is not there). A screen
 * without a sound stops the theme with its fade. IntroPlaySounds off silences it; NoUISound does not: in the period design
 * NoUISound silences the interface sounds only (seg19:03f4 reads [95Options] NoUISound before each one). */
void snd_screen(e26_app *a)
{
	char path[1200];
	FILE *probe;
	if (!a->cur || !a->cur->sound[0]) {
		snd_stop(a->snd_pid);
		a->snd_pid = -1;
		return;
	}
	if (!a->intro_sounds || !a->store) {
		snd_stop(a->snd_pid);
		a->snd_pid = -1;
		if (a->verbose) fprintf(stderr, "d32library: sound: %s suppressed (IntroPlaySounds off)\n", a->cur->sound);
		return;
	}
	if (a->snd_pid > 0 && a->snd_pid == snd_player_pid() && snd_theme_playing()) return; /* the theme plays on */
	if (a->title_theme && a->title_theme[0] && !strcmp(a->cur->name, "title")) {
		if (a->title_theme[0] == '/') snprintf(path, sizeof path, "%s", a->title_theme);
		else snprintf(path, sizeof path, "%s/%s.pcm.wav", a->store, a->title_theme);
		if ((probe = fopen(path, "rb"))) {
			fclose(probe);
			a->snd_pid = snd_play(path, a->cur->sound_loop, a->verbose);
			return;
		}
		if (a->verbose) fprintf(stderr, "d32library: sound: titleTheme %s not found; playing the screen's own theme\n", path);
	}
	snprintf(path, sizeof path, "%s/%s.pcm.wav", a->store, a->cur->sound);
	a->snd_pid = snd_play(path, a->cur->sound_loop, a->verbose);
}
