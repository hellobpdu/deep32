/* Public Cisco catalogue IPC values. The outer Library generation is independent. */
#ifndef E26_CISCO_CATALOGUE_H
#define E26_CISCO_CATALOGUE_H
#include "json.h"
#include <stddef.h>
#include <stdint.h>

/* u14 (readability spec M17): a page of the Cisco browser holds 100 guides, parts or sections (was 32). An e26-lib from
 * before u14 refuses more than 50 (pages) or 64 (sections): the client then asks for those once and keeps them. */
#define CISCO_PAGE 100
#define CISCO_PAGE_LEGACY 50
#define CISCO_SECTIONS_LEGACY 64
#define CISCO_SEARCH 200
#define CISCO_GEN 130
#define CISCO_TEXT 4097
#define CISCO_KEY (CISCO_TEXT + 12)

enum cisco_type { CISCO_ALL, CISCO_CONFIG, CISCO_WHITE, CISCO_VALIDATED, CISCO_TECHNOTE, CISCO_OTHER, CISCO_TYPES };
enum cisco_page_kind { CISCO_DOCUMENTS, CISCO_PARTS, CISCO_SECTIONS, CISCO_HITS };

typedef struct {
	char key[CISCO_KEY], generation[CISCO_GEN], article[CISCO_TEXT];
	int pack; /* 0 lib-e26, 1 lib-corpus. Never a logical catalogue identifier. */
} cisco_route;

typedef struct {
	char generation[CISCO_GEN];
	struct { char generation[CISCO_GEN]; int ready; } store[2];
} cisco_status;

typedef struct {
	cisco_route route;
	char doc_key[CISCO_TEXT], title[CISCO_TEXT], product[CISCO_TEXT], release[CISCO_TEXT];
	char anchor[129];
	int type, part, parts, outline, level, block;
} cisco_row;

typedef struct {
	char key[CISCO_KEY], sort_key[CISCO_TEXT];
	int part, block;
} cisco_cursor;

typedef struct {
	int count, refine, block_next;
	cisco_cursor next;
	cisco_row row[];
} cisco_page;

const char *cisco_pack(int pack);
const char *cisco_type_key(int type);
const char *cisco_type_label(int type);
int cisco_type_index(const char *key);
int cisco_status_decode(cisco_status *out, const jv *value);
int cisco_stores_decode(cisco_status *out, const jv *stores);
cisco_page *cisco_page_decode(const jv *value, enum cisco_page_kind kind,
    const cisco_status *status, const cisco_row *parent, int subtype);

#endif
