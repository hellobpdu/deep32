#include "base_convert.h"
#include "d32netaddr.h"

#include <ctype.h>
#include <string.h>

static unsigned
digit_value(unsigned char c)
{
	if (c >= '0' && c <= '9')
		return c - '0';
	if (c >= 'a' && c <= 'f')
		return c - 'a' + 10;
	if (c >= 'A' && c <= 'F')
		return c - 'A' + 10;
	return 16;
}

static bool
append_digit(d32_count *value, unsigned base, unsigned digit)
{
	d32_count term = *value, total = {.lo = digit};

	for (; base != 0; base >>= 1) {
		if ((base & 1) && (!d32_count_add(total, term, &total) || total.full128))
			return false;
		if (base > 1 && (!d32_count_add(term, term, &term) || term.full128))
			return false;
	}
	*value = total;
	return true;
}

static void
format_bits(d32_count value, unsigned width, char *out)
{
	static const char digits[] = "0123456789ABCDEF";
	char reversed[128];
	size_t n = 0;

	do {
		reversed[n++] = digits[value.lo & ((1U << width) - 1)];
		value.lo = (value.lo >> width) | (value.hi << (64 - width));
		value.hi >>= width;
	} while (value.hi != 0 || value.lo != 0);
	while (n != 0)
		*out++ = reversed[--n];
	*out = '\0';
}

enum d32_base_status
d32_base_convert(const char *text, enum d32_base base,
    struct d32_base_output *out, size_t *column)
{
	const char *p = text, *end = text + strlen(text);
	d32_count value = {0};

	*column = 1;
	if (base != D32_BASE_BINARY && base != D32_BASE_DECIMAL && base != D32_BASE_HEX)
		return D32_BASE_DIGIT;
	while (p < end && isspace((unsigned char)*p))
		p++;
	while (end > p && isspace((unsigned char)end[-1]))
		end--;
	if (end - p >= 2 && *p == '0') {
		if ((base == D32_BASE_BINARY && (p[1] == 'b' || p[1] == 'B')) ||
		    (base == D32_BASE_HEX && (p[1] == 'x' || p[1] == 'X')))
			p += 2;
	}
	*column = (size_t)(p - text) + 1;
	if (p == end)
		return D32_BASE_EMPTY;
	for (; p != end; p++, (*column)++) {
		unsigned digit = digit_value((unsigned char)*p);

		if (digit >= (unsigned)base)
			return D32_BASE_DIGIT;
		if (!append_digit(&value, (unsigned)base, digit))
			return D32_BASE_OVERFLOW;
	}
	format_bits(value, 1, out->binary);
	format_bits(value, 4, out->hex);
	(void)d32_count_format(&value, false, out->decimal, sizeof(out->decimal));
	*column = 0;
	return D32_BASE_OK;
}
