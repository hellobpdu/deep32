#ifndef D32_OUI_GUI_RESULT_H
#define D32_OUI_GUI_RESULT_H

#include "oui.h"

void oui_format_mac(char[18], const struct oui_mac *);
void oui_format_prefix(char[10], const struct oui_row *);
char *oui_join_assignees(const struct oui_row *, const char *);
extern const char oui_notice[];

#endif
