#ifndef D32_BASE_CONVERT_H
#define D32_BASE_CONVERT_H

#include <stddef.h>

enum d32_base { D32_BASE_BINARY = 2, D32_BASE_DECIMAL = 10, D32_BASE_HEX = 16 };
enum d32_base_status { D32_BASE_OK, D32_BASE_EMPTY, D32_BASE_DIGIT, D32_BASE_OVERFLOW };

struct d32_base_output {
	char binary[129];
	char decimal[40];
	char hex[33];
};

/* On failure, column is one-based and output remains unchanged. */
enum d32_base_status d32_base_convert(const char *, enum d32_base,
    struct d32_base_output *, size_t *column);

#endif
