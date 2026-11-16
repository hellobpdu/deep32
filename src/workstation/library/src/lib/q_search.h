/* q_search.h: the e26-lib "search" named query (W1-SEARCH; q_search.c, docs/rooms/SEARCH.md).
 *
 * The seam with the Library service (W1-DATA): once e26-lib has opened a generation (PACK-LAYOUT.md: search.sqlite and
 * catalogue.sqlite, mode=ro&immutable=1) it hands both connections and N, the number of canonical articles (the
 * article ordinal universe, so every bitmap is ceil(N/8) bytes), to q_search_attach. Before that, and after
 * q_search_attach(NULL, NULL, 0), the query answers E_PACK. The connections stay the service's: search never closes
 * them. */
#ifndef E26_Q_SEARCH_H
#define E26_Q_SEARCH_H

struct sqlite3;
struct jv;

void q_search_attach(struct sqlite3 *search_db, struct sqlite3 *catalogue_db, unsigned ndocs);

/* the registered operation itself (helper.h's e26_helper_op.run), for in-process tests */
const char *q_search_run(const struct jv *args, char **result);

#endif
