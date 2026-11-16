#ifndef LIBRARY_KWAJ_H
#define LIBRARY_KWAJ_H
#include <stddef.h>
/* Returns 0 on success; 1 header, 2 compressed data, 3 allocation/limit,
 * 4 output-size mismatch. Caller owns *output, and frees it with kwaj_free.
 * Output is bounded to 256 MiB. Input is never executed or memory-mapped.
 * KWAJ has no checksum: consumers must validate their decoded file format. */
int kwaj_decode(const unsigned char *input, size_t input_size,
                unsigned char **output, size_t *output_size);
void kwaj_free(void *output);
#endif
