/* finder_model.h: bounded, X-free FINDER state and private saved requests. */
#ifndef E26_FINDER_MODEL_H
#define E26_FINDER_MODEL_H
#include <stddef.h>
#include <stdint.h>
#include "json.h"

#define FINDER_PAGE 64
#define FINDER_CATEGORIES 256
#define FINDER_QUERY 512
#define FINDER_PAST 16

enum { FINDER_ALL, FINDER_RFC, FINDER_DRAFT, FINDER_WIKI, FINDER_NIST,
       FINDER_CISCO, FINDER_SYNTHETIC, FINDER_UNCLASSIFIED, FINDER_CISCO_WHITE_PAPER,
       FINDER_CISCO_CVD, FINDER_CISCO_CONFIG, FINDER_CISCO_OTHER, FINDER_TYPES };

const char *finder_type_key(int type);
const char *finder_type_label(int type);
int finder_type_index(const char *key);

typedef struct {
	uint32_t wheel_ordinal;
	char id[1024], title[1024], cls[32], pack[128], marking[128], snippet[512];
	int catalogue_only;
	char source_kind[32], published[32], updated[32], revision[2048];
	char anchor[129], section_title[1024], topic_id[128];
	int obsoleted, has_sections, duplicate, document_type;
} finder_row;

typedef struct {
	finder_row row[FINDER_PAGE];
	size_t nrow;
	uint32_t total, universe, doc_count, type_counts[FINDER_TYPES];
	int types_unavailable;
	int start, next; /* finder.page filtered row offsets; row.wheel_ordinal remains global identity */
	char query[FINDER_QUERY], scope[16];
	int title_mode, near_distance;
	char categories[FINDER_CATEGORIES][128];
	size_t ncategories;
	int whole_library; /* transient Look in choice; the selected topic stays intact */
	unsigned media; /* bits: image, maths, pdf, cli, table, audio, video, interactivity */
	int years, year_min, year_max; /* historical numbering: -1 is 1 BC; no zero */
	char place[256];
	int catalogue;
	char generation[128], catalogue_id[128], status[300];
	char *bitmap;
	unsigned serial;
	int busy;
	char past[FINDER_PAST][FINDER_QUERY];
	int npast;
} finder_model;

void finder_model_init(finder_model *m);
void finder_model_clear(finder_model *m);
/* malloc'd finder.page args, or NULL on an invalid state/allocation failure. */
char *finder_model_args(const finder_model *m, const char *prefix, int start, const char *current);
char *finder_model_document_args(const finder_model *m, const char *prefix, int start, const char *current,
                                 const char *query, int title_mode, int type, const char *sort);
/* Atomic page replacement; 0 success, -1 malformed. query/filters/past are kept. */
int finder_model_reply(finder_model *m, const jv *result, const char *generation);
/* True only for an exact current-generation section in the public default pack. */
int finder_model_public_section(const char *catalogue, const char *generation, const char *current,
                                const char *marking, const char *anchor);
void finder_model_remember(finder_model *m);
/* Only query/filters and catalogue identity are stored. No page, snippets, bitmap or history. */
int finder_model_save(const finder_model *m, const char *dir, const char *name, char *err, size_t errlen);
int finder_model_load(finder_model *m, const char *dir, const char *name, char *err, size_t errlen);
/* Parse a signed year or positive year followed by BC/AD, range -100000..2030. */
int finder_year(const char *text, int *year);
#endif
