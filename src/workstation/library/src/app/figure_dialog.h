#ifndef E26_FIGURE_DIALOG_H
#define E26_FIGURE_DIALOG_H

struct e26_app;

int figure_dialog_open(struct e26_app *, const char *spec, const char *caption);
void figure_dialog_close(struct e26_app *);
void figure_dialog_redraw(struct e26_app *);

#endif
