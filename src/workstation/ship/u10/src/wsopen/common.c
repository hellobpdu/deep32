#include "ws41.h"

#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <sysexits.h>
#include <syslog.h>
#include <time.h>
#include <unistd.h>

uint64_t
ws_now_ms(void)
{
	struct timespec now;
	if (clock_gettime(CLOCK_MONOTONIC, &now) == -1)
		return 0;
	return (uint64_t)now.tv_sec * 1000 + (uint64_t)now.tv_nsec / 1000000;
}

size_t
ws_header_min(const unsigned char bytes[4])
{
	static const unsigned char pcap[][4] = {
		{0xa1, 0xb2, 0xc3, 0xd4}, {0xd4, 0xc3, 0xb2, 0xa1},
		{0xa1, 0xb2, 0x3c, 0x4d}, {0x4d, 0x3c, 0xb2, 0xa1}
	};
	static const unsigned char pcapng[4] = {0x0a, 0x0d, 0x0d, 0x0a};
	for (size_t i = 0; i < sizeof(pcap) / sizeof(pcap[0]); ++i)
		if (memcmp(bytes, pcap[i], 4) == 0)
			return 24;
	return memcmp(bytes, pcapng, 4) == 0 ? 28 : 0;
}

static enum d32_error
read_at(int fd, unsigned char *out, size_t count, off_t offset)
{
	while (count != 0) {
		ssize_t n = pread(fd, out, count, offset);
		if (n == -1 && errno == EINTR)
			continue;
		if (n <= 0)
			return n == 0 ? D32E_FORMAT : D32E_IO;
		out += n;
		count -= (size_t)n;
		offset += n;
	}
	return D32E_OK;
}

static uint32_t
word(const unsigned char bytes[4], bool little)
{
	uint32_t n = 0;
	for (size_t i = 0; i < 4; ++i)
		n = (n << 8) | bytes[little ? 3 - i : i];
	return n;
}

enum d32_error
ws_validate_fd(int fd, off_t size)
{
	unsigned char head[28], tail[4];
	if (size < 4)
		return D32E_FORMAT;
	if ((uintmax_t)size > WS_MAX_BYTES)
		return D32E_TOOBIG;
	enum d32_error rc = read_at(fd, head, 4, 0);
	if (rc != D32E_OK)
		return rc;
	size_t need = ws_header_min(head);
	if (need == 0 || (uintmax_t)size < need)
		return D32E_FORMAT;
	rc = read_at(fd, head + 4, need - 4, 4);
	if (rc != D32E_OK || need == 24)
		return rc;
	/* The pcapng SHB's byte-order marker determines its own length encoding. */
	uint32_t bom = word(head + 8, false);
	if (bom != UINT32_C(0x1a2b3c4d) && bom != UINT32_C(0x4d3c2b1a))
		return D32E_FORMAT;
	bool little = bom == UINT32_C(0x4d3c2b1a);
	uint32_t length = word(head + 4, little);
	if (length < need || length % 4 != 0 || length > (uintmax_t)size)
		return D32E_FORMAT;
	rc = read_at(fd, tail, sizeof(tail), (off_t)length - 4);
	if (rc != D32E_OK)
		return rc;
	return word(tail, little) == length ? D32E_OK : D32E_FORMAT;
}

enum d32_error
ws_error_from_exit(int status)
{
	switch (status) {
	case EX_OK: return D32E_OK;
	case EX_USAGE: return D32E_INVAL;
	case EX_DATAERR: return D32E_FORMAT;
	case EX_NOINPUT: return D32E_NOENT;
	case EX_UNAVAILABLE: return D32E_UNAVAIL;
	case EX_CANTCREAT: return D32E_CREATE;
	case EX_TEMPFAIL: return D32E_TEMP;
	case EX_NOPERM:
	case 1: return D32E_PERM; /* doas reports authorization failures as 1. */
	default: return D32E_IO;
	}
}

static const char *
result_name(int status)
{
	switch (status) {
	case EX_OK: return "ok";
	case EX_USAGE: return "usage";
	case EX_DATAERR: return "dataerr";
	case EX_NOINPUT: return "noinput";
	case EX_UNAVAILABLE: return "unavailable";
	case EX_CANTCREAT: return "cantcreat";
	case EX_TEMPFAIL: return "tempfail";
	case EX_NOPERM: return "noperm";
	default: return "ioerr";
	}
}

int
ws_finish(const char *tool, const char *verb, enum d32_error error,
    const char *detail, size_t bytes, uint64_t start_ms)
{
	int status = d32_sysexit(error);
	const char *result = result_name(status);
	/* Every string argument comes from the caller's fixed operation tokens. */
	(void)fprintf(stderr, "d32: tool=%s verb=%s result=%s detail=%s\n",
	    tool, verb, result, detail);
#ifndef WS41_TEST_NO_SYSLOG
	uint64_t now = ws_now_ms();
	uint64_t elapsed = start_ms != 0 && now >= start_ms ? now - start_ms : 0;
	const char *tag = strcmp(tool, "wireshark-run") == 0 ? "d32-wireshark-run" : "d32-wsopen";
	openlog(tag, LOG_PID, LOG_DAEMON);
	syslog(LOG_DAEMON | LOG_INFO,
	    "verb=%s caller_uid=%ju target=file result=%s bytes=%zu ms=%" PRIu64,
	    verb, (uintmax_t)getuid(), result, bytes, elapsed);
	closelog();
#else
	(void)bytes;
	(void)start_ms;
#endif
	return status;
}
