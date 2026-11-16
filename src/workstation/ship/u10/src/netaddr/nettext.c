#include "d32netaddr.h"

#include <string.h>

/* Keep positions relative to the original input, including nested fields. */
struct parser {
	const char *text;
	d32_net_error *error;
};

static d32_net_status
diagnostic(const struct parser *parser, d32_net_status code,
    const char *position, int bit)
{
	if (parser->error != NULL) {
		*parser->error = (d32_net_error){
			.code = code,
			.line = position != NULL ? 1 : 0,
			.column = position != NULL ?
			    (size_t)(position - parser->text) + 1 : 0,
			.bit = bit
		};
	}
	return code;
}

static bool
is_space(char c)
{
	return c == ' ' || c == '\t';
}

static void
trim(const char **start, const char **end)
{
	while (*start < *end && is_space(**start))
		++*start;
	while (*start < *end && is_space((*end)[-1]))
		--*end;
}

static int
digit(char c, unsigned radix)
{
	if (c >= '0' && c <= '9')
		return c - '0';
	if (radix == 16 && c >= 'a' && c <= 'f')
		return c - 'a' + 10;
	if (radix == 16 && c >= 'A' && c <= 'F')
		return c - 'A' + 10;
	return -1;
}

static d32_net_status
decimal(const struct parser *parser, const char *start, const char *end,
    unsigned maximum, d32_net_status code, unsigned *result)
{
	unsigned value = 0;

	if (start == end)
		return diagnostic(parser, code, start, -1);
	for (const char *p = start; p < end; ++p) {
		int next = digit(*p, 10);

		if (next < 0)
			return diagnostic(parser, code, p, -1);
		value = value * 10 + (unsigned)next;
		if (value > maximum)
			return diagnostic(parser, code, p, -1);
	}
	*result = value;
	return D32_NET_OK;
}

static d32_net_status
ipv4(const struct parser *parser, const char *start, const char *end,
    uint8_t *bytes, d32_net_status code)
{
	for (unsigned octet = 0; octet < 4; ++octet) {
		const char *dot = memchr(start, '.', (size_t)(end - start));
		const char *stop = dot != NULL ? dot : end;
		unsigned value;
		d32_net_status status;

		if (stop - start > 1 && *start == '0' && digit(start[1], 10) >= 0)
			return diagnostic(parser, D32_NET_LEADING_ZERO, start + 1, -1);
		status = decimal(parser, start, stop, 255, code, &value);
		if (status != D32_NET_OK)
			return status;
		bytes[octet] = (uint8_t)value;
		if ((dot == NULL) != (octet == 3))
			return diagnostic(parser, code, stop, -1);
		if (dot != NULL)
			start = dot + 1;
	}
	return D32_NET_OK;
}

static d32_net_status
ipv6(const struct parser *parser, const char *start, const char *end,
    uint8_t bytes[16])
{
	const char *p = start;
	const char *compression = NULL;
	size_t used = 0, compressed_at = 0;

	if (p < end && *p == ':') {
		if (end - p < 2 || p[1] != ':')
			return diagnostic(parser, D32_NET_ADDRESS, p, -1);
		compression = p;
		p += 2;
	}
	while (p < end) {
		const char *group = p, *dot;
		unsigned value = 0;

		while (p < end && *p != ':')
			++p;
		if (p == group || used == 16)
			return diagnostic(parser, D32_NET_ADDRESS, group, -1);
		dot = memchr(group, '.', (size_t)(p - group));
		if (dot != NULL) {
			d32_net_status status;

			if (p != end || used > 12)
				return diagnostic(parser, D32_NET_ADDRESS, dot, -1);
			status = ipv4(parser, group, end, bytes + used, D32_NET_ADDRESS);
			if (status != D32_NET_OK)
				return status;
			used += 4;
			break;
		}
		for (const char *hex = group; hex < p; ++hex) {
			int next = digit(*hex, 16);

			if (next < 0 || hex - group == 4)
				return diagnostic(parser, D32_NET_ADDRESS, hex, -1);
			value = value * 16 + (unsigned)next;
		}
		bytes[used++] = (uint8_t)(value >> 8);
		bytes[used++] = (uint8_t)value;
		if (p == end)
			break;
		++p;
		if (p == end)
			return diagnostic(parser, D32_NET_ADDRESS, p - 1, -1);
		if (*p == ':') {
			if (compression != NULL)
				return diagnostic(parser, D32_NET_ADDRESS, p - 1, -1);
			compression = p - 1;
			compressed_at = used;
			++p;
		}
	}
	if (compression == NULL) {
		if (used != 16)
			return diagnostic(parser, D32_NET_ADDRESS, end, -1);
	} else {
		size_t missing = 16 - used;

		if (missing == 0)
			return diagnostic(parser, D32_NET_ADDRESS, compression, -1);
		memmove(bytes + compressed_at + missing, bytes + compressed_at,
		    used - compressed_at);
		memset(bytes + compressed_at, 0, missing);
	}
	return D32_NET_OK;
}

static d32_net_status
address(const struct parser *parser, const char *start, const char *end,
    d32_addr *result, char *zone, bool allow_zone, d32_net_status code)
{
	const char *percent = memchr(start, '%', (size_t)(end - start));
	const char *stop = percent != NULL ? percent : end;
	d32_addr addr = {0};
	d32_net_status status;

	if (memchr(start, ':', (size_t)(stop - start)) == NULL) {
		addr.fam = D32_IPV4;
		status = ipv4(parser, start, stop, addr.b, code);
	} else {
		addr.fam = D32_IPV6;
		status = ipv6(parser, start, stop, addr.b);
	}
	if (status != D32_NET_OK)
		return status;
	if (percent != NULL) {
		const char *label = percent + 1;

		if (!allow_zone || addr.fam != D32_IPV6)
			return diagnostic(parser, D32_NET_ZONE, percent, -1);
		if (label == end)
			return diagnostic(parser, D32_NET_ZONE, label, -1);
		for (const char *p = label; p < end; ++p) {
			if (p - label == D32_ZONE_CAP - 1 || *p < '!' || *p > '~' ||
			    *p == '%' || *p == '/')
				return diagnostic(parser, D32_NET_ZONE, p, -1);
		}
		if (zone != NULL) {
			memcpy(zone, label, (size_t)(end - label));
			zone[end - label] = '\0';
		}
	} else if (zone != NULL) {
		zone[0] = '\0';
	}
	*result = addr;
	return D32_NET_OK;
}

d32_net_status
d32_addr_parse(const char *text, d32_addr *result, char *zone,
    d32_net_error *error)
{
	const struct parser parser = {text, error};
	const char *start = text, *end;
	d32_net_status status;

	if (text == NULL || result == NULL)
		return diagnostic(&parser, D32_NET_ARG, NULL, -1);
	end = text + strlen(text);
	trim(&start, &end);
	status = address(&parser, start, end, result, zone, true, D32_NET_ADDRESS);
	return status == D32_NET_OK ? diagnostic(&parser, status, NULL, -1) : status;
}

d32_net_status
d32_mask_parse(const char *text, d32_mask *result, d32_net_error *error)
{
	const struct parser parser = {text, error};
	const char *start = text, *end;
	d32_addr addr;
	d32_net_status status;

	if (text == NULL || result == NULL)
		return diagnostic(&parser, D32_NET_ARG, NULL, -1);
	end = text + strlen(text);
	trim(&start, &end);
	status = address(&parser, start, end, &addr, NULL, false, D32_NET_MASK);
	if (status != D32_NET_OK)
		return status;
	result->fam = addr.fam;
	memcpy(result->b, addr.b, sizeof(result->b));
	return diagnostic(&parser, status, NULL, -1);
}

d32_net_status
d32_mask_length(const d32_mask *mask, uint8_t *length, int *bad_bit)
{
	int width;
	uint8_t leading = 0;
	bool saw_zero = false;

	if (bad_bit != NULL)
		*bad_bit = -1;
	if (mask == NULL || length == NULL)
		return D32_NET_ARG;
	width = d32_addr_width(mask->fam);
	if (width == 0)
		return D32_NET_FAMILY;
	for (int i = 0; i < width; ++i) {
		if ((mask->b[i / 8] & (0x80U >> (i % 8))) == 0) {
			saw_zero = true;
			continue;
		}
		if (saw_zero) {
			if (bad_bit != NULL)
				*bad_bit = i + 1;
			return D32_NET_MASK_HOLE;
		}
		++leading;
	}
	*length = leading;
	return D32_NET_OK;
}

static d32_net_status
dotted_length(const struct parser *parser, const char *start, const char *end,
    uint8_t *length)
{
	d32_mask mask = {.fam = D32_IPV4};
	int bad_bit;
	d32_net_status status = ipv4(parser, start, end, mask.b, D32_NET_MASK);

	if (status != D32_NET_OK)
		return status;
	status = d32_mask_length(&mask, length, &bad_bit);
	if (status == D32_NET_MASK_HOLE) {
		const char *octet = start;

		for (int i = 0; i < (bad_bit - 1) / 8; ++i)
			octet = (const char *)memchr(octet, '.', (size_t)(end - octet)) + 1;
		return diagnostic(parser, status, octet, bad_bit);
	}
	return status;
}

d32_net_status
d32_prefix_parse(const char *text, d32_prefix *result, char *zone,
    d32_net_error *error)
{
	const struct parser parser = {text, error};
	const char *start = text, *end, *delimiter, *part;
	char parsed_zone[D32_ZONE_CAP];
	d32_prefix prefix = {0};
	d32_net_status status;
	unsigned length;
	bool slash;

	if (text == NULL || result == NULL)
		return diagnostic(&parser, D32_NET_ARG, NULL, -1);
	end = text + strlen(text);
	trim(&start, &end);
	for (delimiter = start; delimiter < end; ++delimiter) {
		if (*delimiter == '/' || is_space(*delimiter))
			break;
	}
	status = address(&parser, start, delimiter, &prefix.addr, parsed_zone,
	    true, D32_NET_ADDRESS);
	if (status != D32_NET_OK)
		return status;
	prefix.len = (uint8_t)d32_addr_width(prefix.addr.fam);
	if (delimiter != end) {
		slash = *delimiter == '/';
		part = delimiter + 1;
		if (!slash)
			trim(&part, &end);
		if (!slash || (prefix.addr.fam == D32_IPV4 &&
		    memchr(part, '.', (size_t)(end - part)) != NULL)) {
			if (prefix.addr.fam != D32_IPV4)
				return diagnostic(&parser, D32_NET_MASK, part, -1);
			status = dotted_length(&parser, part, end, &prefix.len);
		} else {
			status = decimal(&parser, part, end, prefix.len, D32_NET_LENGTH,
			    &length);
			if (status == D32_NET_OK)
				prefix.len = (uint8_t)length;
		}
		if (status != D32_NET_OK)
			return status;
	}
	if (zone != NULL)
		memcpy(zone, parsed_zone, strlen(parsed_zone) + 1);
	*result = prefix;
	return diagnostic(&parser, D32_NET_OK, NULL, -1);
}

d32_net_status
d32_range_parse(const char *text, d32_range *result, char *lo_zone,
    char *hi_zone, d32_net_error *error)
{
	const struct parser parser = {text, error};
	const char *start = text, *end, *first_end, *last_start, *separator = NULL;
	char first_zone[D32_ZONE_CAP], last_zone[D32_ZONE_CAP];
	d32_range range = {0};
	d32_net_status status;
	bool scoped = false;
	size_t sep_len = 1;

	if (text == NULL || result == NULL)
		return diagnostic(&parser, D32_NET_ARG, NULL, -1);
	end = text + strlen(text);
	trim(&start, &end);
	for (const char *p = start; p < end; ++p) {
		if (*p == '%')
			scoped = true;
		bool dots = end - p >= 2 && p[0] == '.' && p[1] == '.';
		if (!dots && *p != '-')
			continue;
		sep_len = dots ? 2 : 1;
		if (!scoped || (p > start && is_space(p[-1])) ||
		    ((size_t)(end - p) > sep_len && is_space(p[sep_len]))) {
			separator = p;
			break;
		}
	}
	if (separator == NULL)
		return diagnostic(&parser, D32_NET_RANGE, end, -1);
	first_end = separator;
	last_start = separator + sep_len;
	trim(&start, &first_end);
	trim(&last_start, &end);
	status = address(&parser, start, first_end, &range.lo, first_zone, true,
	    D32_NET_ADDRESS);
	if (status != D32_NET_OK)
		return status;
	status = address(&parser, last_start, end, &range.hi, last_zone, true,
	    D32_NET_ADDRESS);
	if (status != D32_NET_OK)
		return status;
	if (range.lo.fam != range.hi.fam)
		return diagnostic(&parser, D32_NET_FAMILY, last_start, -1);
	if (d32_addr_compare(&range.lo, &range.hi) > 0)
		return diagnostic(&parser, D32_NET_RANGE, last_start, -1);
	if (lo_zone != NULL)
		memcpy(lo_zone, first_zone, strlen(first_zone) + 1);
	if (hi_zone != NULL)
		memcpy(hi_zone, last_zone, strlen(last_zone) + 1);
	*result = range;
	return diagnostic(&parser, D32_NET_OK, NULL, -1);
}

static char *
print_uint(char *output, unsigned value, unsigned radix)
{
	static const char digits[] = "0123456789abcdef";
	char reversed[4];
	size_t count = 0;

	/* Callers supply a decimal octet or a hexadecimal 16-bit group. */
	do {
		reversed[count++] = digits[value % radix];
		value /= radix;
	} while (value != 0);
	while (count != 0)
		*output++ = reversed[--count];
	return output;
}

static d32_net_status
formatted(char *output, size_t capacity, const char *text, size_t length)
{
	if (output == NULL)
		return D32_NET_ARG;
	if (capacity <= length)
		return D32_NET_CAPACITY;
	memcpy(output, text, length);
	output[length] = '\0';
	return D32_NET_OK;
}

d32_net_status
d32_addr_format(const d32_addr *addr, char *output, size_t capacity)
{
	char text[D32_ADDR_TEXT_CAP], *end = text;
	unsigned groups[8];
	unsigned run = 0, best = 1, first = 0;

	if (addr == NULL)
		return D32_NET_ARG;
	if (addr->fam == D32_IPV4) {
		for (unsigned i = 0; i < 4; ++i) {
			if (i != 0)
				*end++ = '.';
			end = print_uint(end, addr->b[i], 10);
		}
		return formatted(output, capacity, text, (size_t)(end - text));
	}
	if (addr->fam != D32_IPV6)
		return D32_NET_FAMILY;
	for (unsigned i = 0; i < 8; ++i) {
		groups[i] = (unsigned)addr->b[i * 2] * 256 + addr->b[i * 2 + 1];
		run = groups[i] == 0 ? run + 1 : 0;
		if (run > best) {
			best = run;
			first = i + 1 - run;
		}
	}
	for (unsigned i = 0; i < 8;) {
		if (best > 1 && i == first) {
			*end++ = ':';
			*end++ = ':';
			i += best;
			continue;
		}
		if (end != text && end[-1] != ':')
			*end++ = ':';
		end = print_uint(end, groups[i], 16);
		++i;
	}
	return formatted(output, capacity, text, (size_t)(end - text));
}

d32_net_status
d32_count_format(const d32_count *count, bool grouped, char *output,
    size_t capacity)
{
	uint32_t words[4];
	char reversed[39], text[D32_COUNT_TEXT_CAP], *end = text;
	size_t digits = 0;
	bool more;

	if (count == NULL)
		return D32_NET_ARG;
	if (count->full128) {
		if (count->hi != 0 || count->lo != 0)
			return D32_NET_ARG;
		return formatted(output, capacity, "2^128", 5);
	}
	words[0] = (uint32_t)(count->hi >> 32);
	words[1] = (uint32_t)count->hi;
	words[2] = (uint32_t)(count->lo >> 32);
	words[3] = (uint32_t)count->lo;
	do {
		uint64_t remainder = 0;

		more = false;
		for (unsigned i = 0; i < 4; ++i) {
			uint64_t dividend = (remainder << 32) | words[i];

			words[i] = (uint32_t)(dividend / 10);
			remainder = dividend % 10;
			more |= words[i] != 0;
		}
		reversed[digits++] = (char)('0' + remainder);
	} while (more);
	while (digits != 0) {
		*end++ = reversed[--digits];
		if (grouped && digits != 0 && digits % 3 == 0)
			*end++ = ',';
	}
	return formatted(output, capacity, text, (size_t)(end - text));
}
