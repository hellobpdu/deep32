#ifndef ASKW_VIEW_H
#define ASKW_VIEW_H

#include <X11/Intrinsic.h>
#include "ask_core.h"

/* The panel's own text scale, from its dpi: 100% below 115, 125% to 139, 150% from 140. */
enum { ASK_PANEL_100, ASK_PANEL_125, ASK_PANEL_150 };
/* View > Text Size. Auto is the panel's size; the others step from it. */
enum { ASK_TEXT_AUTO, ASK_TEXT_SMALL, ASK_TEXT_NORMAL, ASK_TEXT_LARGE, ASK_TEXT_LARGER, ASK_TEXT_CHOICES };

typedef struct AskView AskView;
AskView *view_create(Widget, Widget, Widget, AskDoc *, void *,
    void (*)(void *, uint32_t, uint32_t), int panel, int choice);
Widget view_widget(const AskView *);
void view_refresh(AskView *);
void view_page(AskView *, int);
uint32_t view_selected_turn(const AskView *);
/* Lays the transcript out again at this Text Size, keeping the first visible line in view. */
void view_text_size(AskView *, int choice);
/* The body size in pixels this choice gives in the window as it is now. */
int view_text_px(const AskView *, int choice);

#endif
