#ifndef E26_ROOM_CISCO_H
#define E26_ROOM_CISCO_H
#include "library_app.h"

void cisco_pin_search(e26_app *, int subtype, int sort, const char *topic, int area, const char *query);
void cisco_pin_leave(void);
const char *cisco_pin_status(void);
int cisco_pin_can_page(int step);
int cisco_pin_page(e26_app *, int step);
int cisco_pin_can_retry(void);
int cisco_pin_retry(e26_app *);
int cisco_pin_key(e26_app *, KeySym);
void pin_cisco_restored(e26_app *);
void pin_cisco_remember(const e26_app *);
void pin_cisco_controls(e26_app *);
int cisco_have_results(void);
int cisco_return_results(e26_app *a);
void cisco_forget_results(void);
void cisco_confirm_open(void);
void pin_forget_legacy_results(void);

#endif
