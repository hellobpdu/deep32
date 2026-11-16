/* room_openat.h: startup seam and serial Library requests; core calls are an INTEG request. */
#ifndef E26_ROOM_OPENAT_H
#define E26_ROOM_OPENAT_H
#include "library_app.h"
#include "openat.h"
/* After a display opens, before assets: 0 continue, 1 handed over, -1 failed. */
int openat_begin(e26_app *a, const char *display, const char *uri);
/* After the first nav_go, with fonts ready. No Title setting is saved. */
void openat_ready(e26_app *a);
int openat_submit(e26_app *a, const char *uri); /* 0 accepted, -1 invalid/full */
void openat_pump(e26_app *a); /* one queued request, only after the previous fetch completes */
void openat_shutdown(void);
#endif
