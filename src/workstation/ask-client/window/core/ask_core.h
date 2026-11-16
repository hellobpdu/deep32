#ifndef ASK_CORE_H
#define ASK_CORE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum { ASK_RECORD_MAX = 65536, ASK_STYLE_COUNT = 8 };
enum { ASK_BOLD = 1, ASK_ITALIC = 2, ASK_MONO = 4 };
enum { ASK_H1 = ASK_STYLE_COUNT, ASK_H2, ASK_FONT_COUNT };
typedef enum AskViewSize { ASK_VIEW_DEFAULT, ASK_VIEW_COMPACT, ASK_VIEW_WIDE } AskViewSize;

typedef struct AskEvent {
	char tag;
	char *field[6];
	uint32_t number[6];
} AskEvent;

typedef struct AskReader {
	char line[ASK_RECORD_MAX + 1];
	size_t used, malformed;
	bool discard, version_seen;
} AskReader;

typedef bool (*AskReceive)(void *, const AskEvent *);
void ask_reader_feed(AskReader *, const void *, size_t, AskReceive, void *);
void ask_reader_end(AskReader *);
/* Writes one complete request, or returns zero without a complete record. */
size_t ask_line_request(char *, size_t, const char *, size_t);

typedef struct AskDoc AskDoc;
typedef struct AskHeader {
	const char *link, *state, *engine, *model, *modifiers;
	bool paired;
} AskHeader;

AskDoc *ask_doc_new(void);
void ask_doc_free(AskDoc *);
bool ask_doc_receive(void *, const AskEvent *);
AskHeader ask_doc_header(const AskDoc *);
bool ask_doc_busy(const AskDoc *);
bool ask_doc_fatal(const AskDoc *);
bool ask_doc_notice(AskDoc *, const char *kind, const char *text);
uint32_t ask_doc_last_turn(const AskDoc *);
/* Caller owns the returned NUL-terminated plain text. */
char *ask_copy_turn(const AskDoc *, uint32_t turn);
size_t ask_request_open(const AskDoc *, uint32_t turn, uint32_t n, char *, size_t);

/* scale, measure and pitch are zero for the 14 px transcript, as before. At a larger text
 * size, scale grows the spacing (percent of the 14 px rhythm), measure caps a prose line
 * (pixels, about 76 characters; the column is centred in the pane) and pitch sets the
 * minimum line pitch. Code wells may grow right past the measure, to the pane's edge. */
typedef struct AskMetrics {
	void *context;
	int (*width)(void *, unsigned style, const char *, size_t);
	int ascent[ASK_FONT_COUNT], descent[ASK_FONT_COUNT];
	AskViewSize size;
	int scale, measure, pitch;
} AskMetrics;

typedef enum AskBoxKind {
	ASK_TEXT, ASK_RULE, ASK_QUOTE_BAR, ASK_CODE_RULE
} AskBoxKind;
enum { ASK_ACCENT = 1, ASK_UNDERLINE = 2, ASK_MARKING = 4 };

typedef struct AskBox {
	AskBoxKind kind;
	int x, y, width, height, baseline; /* document coordinates, origin top left */
	unsigned style, flags;
	uint32_t turn, ref; /* ref is zero unless this fragment is an openable hit */
	uint64_t item; /* stable within an answer, used for the scroll anchor */
	const char *text;
	size_t length;
} AskBox;

typedef struct AskCode {
	int x, y, width, height, content_width;
	uint64_t item;
} AskCode;

/* Box and code coordinates are relative to the column; column_x is its left edge in the pane.
 * A code well can reach right of the column, up to the pane's edge. */
typedef struct AskLayout {
	AskBox *boxes;
	AskCode *code;
	size_t count, capacity, code_count, code_capacity;
	int height, wrap_width, line_pitch, column_x;
} AskLayout;

bool ask_layout_build(AskLayout *, const AskDoc *, int pane_width, const AskMetrics *);
void ask_layout_free(AskLayout *);
const AskBox *ask_layout_hit(const AskLayout *, int x, int y);

#endif
