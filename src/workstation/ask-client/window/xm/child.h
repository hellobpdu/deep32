#ifndef ASKW_CHILD_H
#define ASKW_CHILD_H

#include <X11/Intrinsic.h>
#include <stddef.h>

typedef struct AskChild AskChild;
AskChild *child_start(XtAppContext, void *,
    void (*)(void *, const char *, size_t), void (*)(void *, int));
int child_send(AskChild *, const char *, size_t);
void child_close(AskChild *);
void child_terminal(int);

#endif
