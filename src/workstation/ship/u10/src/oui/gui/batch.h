#ifndef D32_OUI_BATCH_H
#define D32_OUI_BATCH_H

#include "d32ui.h"
#include "oui.h"

/* The registry and directory descriptor outlive the dialog. */
bool oui_batch_show(d32ui *, Widget, const struct oui_db *, int,
    XmFontList, void (*)(void *), void *);

#endif
