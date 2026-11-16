/* e26p.h: the E26P frame of LIB-PROTOCOL.md ("Transport, bytes and versions"): a 24-byte header (ASCII "E26P",
 * uint16 major 1, uint16 minor 0, uint32 body length 1..8,388,608, uint64 request id >= 1, uint32 kind 1 request,
 * 2 response, 4 cancellation; network byte order) and a UTF-8 JSON body. W0-F0 gives the framing and a blocking
 * reader and writer for the helpers' side; W1-DATA adds the UI side's buffered, non-blocking client and the body
 * validation the contract asks for (UTF-8, duplicate keys, nesting). */
#ifndef E26_E26P_H
#define E26_E26P_H
#include <stddef.h>
#include <stdint.h>

#define E26P_HEADER 24
#define E26P_MAX_BODY 8388608u
enum { E26P_REQUEST = 1, E26P_RESPONSE = 2, E26P_CANCEL = 4 };

typedef struct {
	uint16_t major, minor;
	uint32_t length;
	uint64_t id;
	uint32_t kind;
} e26p_header;

/* the 24 header bytes for h */
void e26p_pack(const e26p_header *h, unsigned char out[E26P_HEADER]);
/* parses and checks 24 bytes: 0, or -1 (wrong magic, version, length, id or kind) */
int e26p_unpack(const unsigned char in[E26P_HEADER], e26p_header *h);
/* blocking, for a helper on its socket: one whole frame (body malloc'd, NUL-terminated for the JSON reader);
 * 1 a frame, 0 end of file before a header, -1 a malformed or short frame (the caller closes the connection) */
int e26p_read(int fd, e26p_header *h, char **body);
/* one frame: 0, or -1 */
int e26p_write(int fd, uint64_t id, uint32_t kind, const char *body, size_t len);

#endif
