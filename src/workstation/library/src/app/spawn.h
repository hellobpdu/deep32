/* spawn.h: helper processes (plan E4). d32library starts a helper with fork and exec over one connected AF_UNIX
 * socketpair: the helper's end is its descriptor 3 (E26_FD=3 in its environment), the parent's end is watched through
 * Xt (io.c io_watch), and messages are imsg frames (OpenBSD's imsg, as node/asklink uses it), so a partial read never
 * blocks the interface. The Library helpers carry LIB-PROTOCOL's E26P frames byte for byte inside the imsg payload
 * (src/common/e26p.h); a payload is at most SPAWN_MAX bytes. No X here: the tests drive it directly.
 *
 * SIGCHLD stays ignored (sound.c), so children are reaped by the system; spawn_close sends SIGTERM. */
#ifndef E26_SPAWN_H
#define E26_SPAWN_H
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

#define SPAWN_MAX (8u * 1024u * 1024u + 64u) /* an E26P body (8 MiB) and its 24-byte header, with room to spare */
#define SPAWN_FD 3

struct imsgbuf;

typedef struct {
	pid_t pid;
	int fd;                 /* the parent's end of the socketpair, -1 when closed */
	struct imsgbuf *ib;
	char name[32];
} e26_spawn;

/* fork and exec path with argv (argv[0] the helper's name); 0, or -1 with errno */
int spawn_exec(e26_spawn *sp, const char *path, char *const argv[]);
/* Exec the given absolute path in a detached session, with no app descriptors. */
int spawn_detached(const char *path, char *const argv[]);
/* fork and run child(fd, ud) in the child (a helper built into the caller, and the tests); its return is the exit code */
int spawn_fork(e26_spawn *sp, const char *name, int (*child)(int fd, void *ud), void *ud);
/* queue one message and write what the socket takes now (the rest goes on the next spawn_flush); 0 or -1 */
int spawn_send(e26_spawn *sp, uint32_t type, const void *data, size_t len);
int spawn_flush(e26_spawn *sp);
/* read what is there (call when the descriptor is readable): 1 data, 0 end of file (the helper went), -1 error */
int spawn_read(e26_spawn *sp);
/* the next whole message: 1 (type, len and up to cap bytes in buf), 0 none yet, -1 error or too large for cap */
int spawn_get(e26_spawn *sp, uint32_t *type, void *buf, size_t cap, size_t *len);
void spawn_close(e26_spawn *sp);

/* the helper's side: its socket (E26_FD, else 3), and the same framing over it */
int spawn_child_fd(void);
int spawn_child_open(e26_spawn *sp, int fd);   /* an e26_spawn over an inherited descriptor (pid 0) */

#endif
