#ifndef ASK_DOC_H
#define ASK_DOC_H

#include "ask_core.h"

typedef enum ItemKind {
	QUESTION, PARAGRAPH, HEADING, LIST, QUOTE, CODE, PRE, RULE, BLANK,
	REF_TITLE, REFERENCE, STATUS, NOTICE
} ItemKind;

enum { REF_OPEN = 1, REF_FALLBACK = 2 };

typedef struct AskRun {
	char *text;
	size_t length;
	unsigned style;
} AskRun;

typedef struct AskItem {
	ItemKind kind;
	uint64_t id;
	uint32_t turn, ref;
	unsigned level, flags;
	char *marker, *mark;
	AskRun *runs;
	size_t count, capacity, bytes;
} AskItem;

struct AskDoc {
	AskItem *items, pending, preview;
	size_t count, capacity, bytes, turns;
	uint64_t serial;
	uint32_t turn, references;
	char *header[5];
	bool paired, busy, fatal, active, in_block, hidden, truncated;
};

bool ask_grow(void **, size_t *, size_t, size_t);
char *ask_dup(const char *, size_t);

#endif
