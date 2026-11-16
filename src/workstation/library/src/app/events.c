/* events.c: the UI event bus (plan E4). Modules subscribe to what the engine does (a press, a menu opening or picked,
 * a screen entered, an article opened, the window resized, Go Back restoring an entry) without the engine knowing
 * them: the click sound (W1-SOUND), the reading history (state.c), the article's scroll bar (room_article.c).
 * Subscribers run in the order they subscribed, synchronously, inside the publisher. */
#include "library_app.h"
#include <stdlib.h>

typedef struct {
	int type, live;
	e26_event_fn fn;
	void *ud;
} sub;

static sub *subs;
static int nsubs, capsubs;

int ev_subscribe(int type, e26_event_fn fn, void *ud)
{
	if (type < 0 || type >= EV_NTYPES || !fn) return -1;
	if (nsubs == capsubs) {
		int cap = capsubs ? capsubs * 2 : 16;
		sub *ns = (sub *)realloc(subs, (size_t)cap * sizeof(sub));
		if (!ns) return -1;
		subs = ns;
		capsubs = cap;
	}
	subs[nsubs].type = type;
	subs[nsubs].live = 1;
	subs[nsubs].fn = fn;
	subs[nsubs].ud = ud;
	return nsubs++;
}

void ev_unsubscribe(int id)
{
	if (id >= 0 && id < nsubs) subs[id].live = 0;
}

void ev_publish(e26_app *a, const e26_event *ev)
{
	int i, n = nsubs; /* subscribers added while publishing hear the next event */
	for (i = 0; i < n; i++)
		if (subs[i].live && subs[i].type == ev->type) subs[i].fn(a, ev, subs[i].ud);
}

const char *ev_name(int type)
{
	static const char *const names[EV_NTYPES] = { "press", "menu-open", "menu-pick", "screen-enter", "article-open", "resize",
	                                               "history-back" };
	return type >= 0 && type < EV_NTYPES ? names[type] : "?";
}
