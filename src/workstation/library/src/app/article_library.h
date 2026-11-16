/* article_library.h: several trusted packs through DATA's existing per-connection interface. */
#ifndef E26_ARTICLE_LIBRARY_H
#define E26_ARTICLE_LIBRARY_H
#include "lib_client.h"
struct e26_app;
/* Pack 0 is DATA's default connection. E26_ARTICLE_PACKS adds semicolon-separated trusted pack directories.
 * No article id, media URI or document text is ever interpreted as a directory or helper option. */
int ar_lib_count(void);
int ar_lib_add(const char *directory);
/* Trusted registry configuration only. URI bytes never supply directory names. */
int ar_lib_register_named(const char *name, const char *directory);
int ar_lib_resolve(const char *name);
const char *ar_lib_name(int pack);
uint64_t ar_lib_request(struct e26_app *a, int pack, const char *op, const char *args, lib_cb cb, void *ud);
const char *ar_lib_generation(int pack);
void ar_lib_note_generation(int pack, const char *generation);
void ar_lib_shutdown(void);
#endif
