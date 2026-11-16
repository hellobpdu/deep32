/* room_notemark.h: Notemarks (Tools > Notemark, Ctrl+D) and the articles read lately, for the Library home on the Main
 * screen (room_main.c). u14 readability spec S8. Public Library documents only, as the reading history and reading
 * positions are (art_public_position). */
#ifndef E26_ROOM_NOTEMARK_H
#define E26_ROOM_NOTEMARK_H
#include "library_app.h"

enum { PLACES_RECENT, PLACES_NOTEMARKS };
#define PLACES_RECENT_MAX 50      /* <settings>/RECENT.TXT */
#define PLACES_NOTEMARK_MAX 200   /* <settings>/NOTEMARK.TXT */

typedef struct {
	char pack[24];                /* lib-e26 or lib-corpus */
	char id[600];                 /* the article id (UTF-8) */
	char uri[680];                /* Notemarks: the library: position (art_position_uri); "" for the recent list */
	char title[200], section[200];/* 8-bit, as the reader draws them */
	int percent;                  /* the recent list: how far through, 0-100 */
	long long when;
} e26_place;

int places_count(int list);                          /* loads the list's file on first use */
const e26_place *places_get(int list, int i);        /* 0 = the newest */
void places_open(e26_app *a, int list, int i);       /* reopens it: the recent article where the reader left it, a
                                                        notemark at its section */
#endif
