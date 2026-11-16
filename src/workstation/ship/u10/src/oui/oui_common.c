#include "oui_internal.h"

#include <sys/types.h>

#include <sha2.h>
#include <string.h>

void
oui_sha256(const void *data, size_t len, char hex[65])
{
	(void)SHA256Data(data, len, hex);
}

static bool
ascii_alnum(unsigned char c)
{
	return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
	    (c >= '0' && c <= '9');
}

bool
oui_gen_valid(const char *s)
{
	size_t i;

	if (!s || !ascii_alnum((unsigned char)s[0]))
		return false;
	for (i = 1; s[i] != '\0'; i++) {
		if (i >= 64 || (!ascii_alnum((unsigned char)s[i]) &&
		    s[i] != '-' && s[i] != '_' && s[i] != '.'))
			return false;
		if (s[i] == '.' && s[i - 1] == '.')
			return false;
	}
	return true;
}

bool
oui_date_valid(const char *s)
{
	static const unsigned days[] = { 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31 };
	unsigned parts[3] = { 0 }, part = 0, maxday, i;

	if (!s || strlen(s) != OUI_DATE_SIZE)
		return false;
	for (i = 0; i < OUI_DATE_SIZE; i++) {
		if (i == 4 || i == 7) {
			if (s[i] != '-')
				return false;
			part++;
		} else {
			if (s[i] < '0' || s[i] > '9')
				return false;
			parts[part] = parts[part] * 10 + (unsigned)(s[i] - '0');
		}
	}
	if (parts[0] == 0 || parts[1] == 0 || parts[1] > 12)
		return false;
	maxday = days[parts[1] - 1];
	if (parts[1] == 2 && parts[0] % 4 == 0 &&
	    (parts[0] % 100 != 0 || parts[0] % 400 == 0))
		maxday++;
	return parts[2] != 0 && parts[2] <= maxday;
}

bool
oui_utf8_valid(const unsigned char *s, size_t len)
{
	uint32_t value, min;
	unsigned n;

	while (len != 0) {
		value = *s++;
		len--;
		if (value < 0x80) {
			if (value < 0x20 || value == 0x7f)
				return false;
			continue;
		}
		if (value >= 0xc2 && value <= 0xdf) {
			value &= 0x1f;
			n = 1;
			min = 0x80;
		} else if (value >= 0xe0 && value <= 0xef) {
			value &= 0x0f;
			n = 2;
			min = 0x800;
		} else if (value >= 0xf0 && value <= 0xf4) {
			value &= 0x07;
			n = 3;
			min = 0x10000;
		} else {
			return false;
		}
		if (len < n)
			return false;
		len -= n;
		while (n-- != 0) {
			if ((*s & 0xc0) != 0x80)
				return false;
			value = (value << 6) | (*s++ & 0x3f);
		}
		if (value < min || value > 0x10ffff || value <= 0x9f ||
		    (value >= 0xd800 && value <= 0xdfff))
			return false;
	}
	return true;
}

uint16_t
oui_get16(const unsigned char *p)
{
	return (uint16_t)((uint16_t)p[0] << 8 | p[1]);
}

uint32_t
oui_get32(const unsigned char *p)
{
	return (uint32_t)oui_get16(p) << 16 | oui_get16(p + 2);
}

uint64_t
oui_get64(const unsigned char *p)
{
	return (uint64_t)oui_get32(p) << 32 | oui_get32(p + 4);
}

void
oui_put16(unsigned char *p, uint16_t value)
{
	p[0] = (unsigned char)(value >> 8);
	p[1] = (unsigned char)value;
}

void
oui_put32(unsigned char *p, uint32_t value)
{
	oui_put16(p, (uint16_t)(value >> 16));
	oui_put16(p + 2, (uint16_t)value);
}

void
oui_put64(unsigned char *p, uint64_t value)
{
	oui_put32(p, (uint32_t)(value >> 32));
	oui_put32(p + 4, (uint32_t)value);
}
