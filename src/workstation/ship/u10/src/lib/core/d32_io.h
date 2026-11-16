#ifndef D32_IO_H
#define D32_IO_H

#include <stdbool.h>
#include <stddef.h>
#include <sys/stat.h>
#include <sys/types.h>

#include "d32_error.h"

enum d32_input_kind {
	D32_INPUT_FILE,
	D32_INPUT_PCAP
};

/* On success, the caller owns the descriptor and reads from offset zero. */
enum d32_error d32_open_input(const char *, enum d32_input_kind, size_t,
    int *, struct stat *);

/* The caller owns dirfd and must control its directory entries.
 * Exact modes other than the creation mode after umask need fchmod.
 * A failed cleanup after a successful no-clobber link leaves the target intact.
 */
enum d32_error d32_atomic_write(int, const char *, const void *, size_t,
    mode_t, bool);

/* Use the reader exclusively while it owns buffered bytes. It borrows fd. */
struct d32_reader {
	int fd;
	size_t remaining;
	size_t next;
	size_t used;
	bool eof;
	enum d32_error error;
	unsigned char buffer[4096];
};

void d32_reader_init(struct d32_reader *, int, size_t);

/* Capacity includes NUL. LF is removed, CR is retained, and NUL is invalid.
 * Both readers set eof only on clean EOF before an item. Stream errors stick.
 */
enum d32_error d32_read_line(struct d32_reader *, char *, size_t, size_t *,
    bool *);

/* Read exactly len bytes. A partial record at EOF is D32E_FORMAT. */
enum d32_error d32_read_record(struct d32_reader *, void *, size_t, bool *);

#endif
