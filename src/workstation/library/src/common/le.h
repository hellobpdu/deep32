/* le.h: bounds-free little-endian readers. Callers check lengths first. */
#ifndef E26_LE_H
#define E26_LE_H
#include <stdint.h>

static inline uint16_t le16(const unsigned char *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static inline int16_t le16s(const unsigned char *p) { return (int16_t)le16(p); }
static inline uint32_t le32(const unsigned char *p)
{
	return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static inline int32_t le32s(const unsigned char *p) { return (int32_t)le32(p); }

#endif
