/* room_openat.c: bounded local requests enter ARTICLE through its named action. */
#include "room_openat.h"
#include "room_article.h"
#include "openat_ipc.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define OPENAT_QUEUE 16
static openat_ipc *endpoint;
static char queue[OPENAT_QUEUE][OPENAT_URI_MAX + 1];
static int first, count, ready, watching = -1, armed, cleanup_registered;

void openat_shutdown(void)
{
	if (watching >= 0) io_unwatch(watching);
	watching = -1;
	openat_ipc_close(endpoint); endpoint = NULL;
	first = count = ready = 0;
	memset(queue, 0, sizeof queue);
}

int openat_submit(e26_app *a, const char *uri)
{
	openat_target t;
	(void)a;
	if (!uri || (*uri && openat_parse(uri, &t)) || count == OPENAT_QUEUE) return -1;
	memcpy(queue[(first + count) % OPENAT_QUEUE], uri, strlen(uri) + 1);
	count++;
	return 0;
}

void openat_pump(e26_app *a)
{
	e26_actx x;
	char uri[OPENAT_URI_MAX + 1];
	int action;
	if (!ready || !count || art_st()->busy) return;
	memcpy(uri, queue[first], sizeof uri);
	memset(queue[first], 0, sizeof queue[first]);
	first = (first + 1) % OPENAT_QUEUE; count--;
	memset(&x, 0, sizeof x); x.ctl = -1; x.arg = uri; x.label = "Open Library";
	if (*uri) {
		action = reg_find(REG_ACTION, "article.open-at");
		if (action >= 0 && action_built(action)) (void)action_run(a, action, &x);
		else nav_notice_text(a, "Open Library", "The Library reader is unavailable.");
	}
	action = reg_find(REG_ACTION, "wm.raise");
	if (action >= 0 && action_built(action)) (void)action_run(a, action, &x);
}

static int accepted(void *ud, const char *uri) { return openat_submit((e26_app *)ud, uri); }

static void service(e26_app *a)
{
	if (endpoint) openat_ipc_step(endpoint, accepted, a);
	openat_pump(a);
}

static void tick(e26_app *a, void *ud)
{
	(void)ud;
	armed = 0;
	if (!ready) return;
	service(a);
	if (a->ctx && (endpoint || count)) { armed = 1; timer_after(a, 50, tick, NULL); }
}

static void readable(e26_app *a, int fd, void *ud) { (void)fd; (void)ud; service(a); }

int openat_begin(e26_app *a, const char *display, const char *uri)
{
	openat_target t;
	int rc;
	if (endpoint || count || ready || !uri || (*uri && openat_parse(uri, &t))) return -1;
	rc = openat_ipc_claim(&endpoint, display, uri);
	if (rc == OPENAT_IPC_ERROR) { fprintf(stderr, "d32library: the local Library hand-over failed\n"); return -1; }
	if (rc == OPENAT_IPC_FORWARDED) return 1;
	if (!cleanup_registered) {
		if (atexit(openat_shutdown)) { openat_shutdown(); return -1; }
		cleanup_registered = 1;
	}
	if (*uri && openat_submit(a, uri)) { openat_shutdown(); return -1; }
	return 0;
}

void openat_ready(e26_app *a)
{
	ready = 1;
	if (endpoint && watching < 0 && a->ctx) watching = io_watch(a, openat_ipc_fd(endpoint), readable, NULL);
	openat_pump(a);
	if (!armed && a->ctx && (endpoint || count)) { armed = 1; timer_after(a, 50, tick, NULL); }
}

static int request(e26_app *a, const e26_actx *x)
{
	if (openat_submit(a, x->arg)) nav_notice_text(a, "Open Library", "The Library link is invalid or its request queue is full.");
	else openat_pump(a);
	return 0;
}

void e26_mod_room_openat(void);
void e26_mod_room_openat(void)
{
	static const e26_action action = { request };
	reg_add(REG_ACTION, "openat.request", &action, "room_openat", 0);
}
