/* article_clipboard.h: exact UTF-8 selections for v2, over INPUT's provenance contract. */
#ifndef E26_ARTICLE_CLIPBOARD_H
#define E26_ARTICLE_CLIPBOARD_H
#include "select.h"
/* Copies text immediately, owns CLIPBOARD and PRIMARY; 0 or -1. X-free when a is NULL. */
int ar_clipboard(struct e26_app *a, const char *text, const sel_prov *provenance);
const char *ar_clipboard_text(void);
#endif
