/* io.c: input sources (plan E4). A helper's socket (spawn.c) or any other descriptor is watched through Xt's event
 * loop, so the interface never blocks on it: fn runs when the descriptor is readable. */
#include "library_app.h"
#include <stdlib.h>

typedef struct {
	e26_app *a;
	int fd, live;
	e26_io_fn fn;
	void *ud;
	XtInputId xid;
} watch;

static watch *w;
static int nw, capw;

static void input_cb(XtPointer cd, int *fd, XtInputId *id)
{
	watch *x = &w[(size_t)cd];
	(void)id;
	if (x->live) x->fn(x->a, *fd, x->ud);
}

int io_watch(e26_app *a, int fd, e26_io_fn fn, void *ud)
{
	int id;
	if (fd < 0 || !fn || !a || !a->ctx) return -1;
	if (nw == capw) {
		int cap = capw ? capw * 2 : 8;
		watch *nwp = (watch *)realloc(w, (size_t)cap * sizeof(watch));
		if (!nwp) return -1;
		w = nwp;
		capw = cap;
	}
	id = nw++;
	w[id].a = a;
	w[id].fd = fd;
	w[id].fn = fn;
	w[id].ud = ud;
	w[id].live = 1;
	w[id].xid = XtAppAddInput(a->ctx, fd, (XtPointer)XtInputReadMask, input_cb, (XtPointer)(size_t)id);
	return id;
}

void io_unwatch(int id)
{
	if (id < 0 || id >= nw || !w[id].live) return;
	XtRemoveInput(w[id].xid);
	w[id].live = 0;
}
