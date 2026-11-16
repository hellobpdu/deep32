#ifndef D32_OUI_INTERNAL_H
#define D32_OUI_INTERNAL_H

#include "oui.h"

#define OUI_MAGIC_1 "D32OUI\0\1"
#define OUI_MAGIC_2 "D32OUI\0\2"
#define OUI_HEADER_SIZE 28u
#define OUI_RECORD_SIZE 15u
#define OUI_DATE_SIZE 10u
#define OUI_MAX_IMAGE (32u * 1024u * 1024u)
#define OUI_MAX_SOURCE OUI_MAX_IMAGE
#define OUI_MAX_ASSIGNEE (1024u * 1024u)
#define OUI_SEARCH_MAX 200u

void oui_sha256(const void *, size_t, char[65]);
bool oui_date_valid(const char *);
bool oui_utf8_valid(const unsigned char *, size_t);
bool oui_gen_valid(const char *);
uint16_t oui_get16(const unsigned char *);
uint32_t oui_get32(const unsigned char *);
uint64_t oui_get64(const unsigned char *);
void oui_put16(unsigned char *, uint16_t);
void oui_put32(unsigned char *, uint32_t);
void oui_put64(unsigned char *, uint64_t);

#endif
