/* chrome.h: W1-CHROME's interfaces (build 3, plan 3.2): the window manager side (wm.c), the menus' keyboard and
 * pass-over behaviour (menus.c), the wipes and the period pacing (wipe.c), and the module that wires them to the
 * engine (mod_chrome.c). Nothing here changes a core file: the module installs its X handlers once the shell is
 * realised (the first screen entered) and works through the event bus and the registries (docs/rooms/CHROME.md). */
#ifndef E26_CHROME_H
#define E26_CHROME_H
#include "library_app.h"

/* ---- mod_chrome.c */
/* a CHROME setting: the session's X resource when one is given (-xrm '*wipes: False'), else LIBRARY.INI, else def */
int chrome_flag(e26_app *a, const char *key, const char *res, const char *cls, int def);
/* the room a screen belongs to: its "prop room", else the Main screen for main and article (the period design's one
 * article window), else its own name; a wipe runs only when the room changes */
const char *chrome_room(const scr_screen *s);
/* Go Forward (97): how many steps lie ahead (the bar's Forward is greyed at 0) */
int chrome_forward_depth(void);
/* a key or a press arrived: what is in view now is what Go Back leaves (Go Forward returns to it) */
void chrome_note(e26_app *a);

/* ---- wm.c */
void wm_raise(e26_app *a);                        /* map/deiconify and ask the window manager for focus */
void wm_install(e26_app *a);                      /* once, when the shell is realised: focus, WM_DELETE_WINDOW, keys, icon */
int wm_active(void);                              /* 1 while the window has the focus (or nothing says otherwise) */
void wm_set_active(e26_app *a, int active);       /* the caption turns to entry 12 (inactive-bg) and back; redraws */
void wm_caption_press(e26_app *a, const e26_event *ev); /* EV_PRESS: a drag or a double-click on the caption */
int wm_close(e26_app *a);                         /* Close (Alt+F4): a menu, then a window, the Title card goes to the
                                                   * Main screen, else Library exits; returns as an action does */

/* ---- menus.c */
/* a key while a menu is open (arrows, Home, End, Return, space, a mnemonic): 1 when the menu took it */
int menus_key(e26_app *a, KeySym ks, unsigned state);
/* opens menu id from the keyboard (Alt+E): under the control on the front screen that opens it, or at its "at",
 * with its first item under the keyboard (capture256 140, 143); closes it when it is already open */
void menus_open_by_key(e26_app *a, const char *id);
char menus_mnemonic(const e26_app *a, const scr_menu *m, int item); /* the item's mnemonic (lower case), 0 none */
int menus_pass_opened(void);                      /* 1 while the open menu was opened by passing over its marker */

/* ---- wipe.c */
#define WIPE_STEPS 4                              /* a quick wipe: four frames at 12 a second, a third of a second */
#define WIPE_MS 83
#define PACE_STEPS 15                             /* period pacing: the frame paints top-down in about 1.25 s */
#define PACE_MS 83
int wipe_rows(int step, int nsteps, int h);       /* rows of the new frame shown after step (0..nsteps) */
/* called before the new frame is drawn (EV_SCREEN_ENTER): the window keeps the old frame (or black) while the new
 * one is drawn aside, then shows it top-down in nsteps steps ms apart */
void wipe_begin(e26_app *a, int nsteps, int ms, int from_black);
void wipe_finish(e26_app *a);                     /* the whole new frame at once (a resize, a quit) */
int wipe_running(void);

#endif
